"""
Модуль для повного синтаксичного розбору, вилучення вихідного коду та семантичного аналізу файлів BBC micro:bit (.hex).
Підтримує всі відомі формати прошивок micro:bit:
1. Microsoft MakeCode Universal HEX (v1 та v2) з вбудованими LZMA-стисненими сховищами проєктів (main.ts, main.py, main.blocks, pxt.json)
2. Microsoft MakeCode PXT з нестисненим JSON (main.ts / main.py)
3. MicroPython з офіційним бінарним заголовком 'MP' та адресними розділами Flash (0x3E000, 0x70000, 0x78000)
4. Необроблені текстові скрипти та евристичний аналіз коду у Flash пам'яті
5. Семантичний аналіз апаратних компонентів micro:bit для ШІ Gemini
"""

import os
import re
import json
import html
import urllib.parse

try:
    import lzma
except ImportError:
    lzma = None


def _extract_intel_hex_payload(file_path):
    """
    Розбирає Intel HEX файл та витягує суцільний масив байтів payload.
    Обробляє:
    - Record Type 00: стандартні дані
    - Record Type 0D (13): дані Universal Hex для micro:bit v2
    - Record Type 0C (12): метадані MakeCode
    - Record Type 0E (14): вбудовані файли вихідного коду MakeCode (PXT Embedded Source)
    - Record Type 04 / 02: адресні зміщення
    Повертає: (all_bytes: bytearray, type14_bytes: bytearray, memory_map: dict, total_lines: int)
    """
    memory_map = {}
    all_sequential_bytes = bytearray()
    type14_bytes = bytearray()
    total_lines = 0
    current_upper_address = 0
    current_segment_address = 0

    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            total_lines += 1
            line = line.strip()
            if not line.startswith(':') or len(line) < 11:
                continue

            try:
                byte_count = int(line[1:3], 16)
                offset = int(line[3:7], 16)
                rec_type = int(line[7:9], 16)
                data_hex = line[9:9 + byte_count * 2]

                if rec_type in (0, 13, 0x0D, 12, 0x0C, 14, 0x0E):
                    data_bytes = bytes.fromhex(data_hex)
                    all_sequential_bytes.extend(data_bytes)

                    if rec_type in (14, 0x0E):
                        type14_bytes.extend(data_bytes)

                    addr = current_upper_address + current_segment_address + offset
                    for i, b in enumerate(data_bytes):
                        memory_map[addr + i] = b

                elif rec_type == 4:  # Extended Linear Address Record
                    current_upper_address = int(data_hex, 16) << 16
                    current_segment_address = 0

                elif rec_type == 2:  # Extended Segment Address Record
                    current_segment_address = int(data_hex, 16) << 4
                    current_upper_address = 0

            except Exception:
                continue

    return all_sequential_bytes, type14_bytes, memory_map, total_lines


def _try_extract_makecode_lzma(type14_bytes, all_sequential_bytes):
    """
    Шукає та декомпресує LZMA-стиснене сховище проєкту Microsoft MakeCode (PXT).
    MakeCode записує метадані {"compression":"LZMA", ...} та потік LZMA зі стандартним заголовком 0x5D.
    """
    if not lzma:
        return None, None, None, {}

    for buf in (type14_bytes, all_sequential_bytes):
        if not buf:
            continue

        # Шукаємо заголовок LZMA за сигнатурою 0x5D 0x00 0x00
        search_pos = 0
        while True:
            idx = buf.find(b'\x5d\x00\x00', search_pos)
            if idx == -1:
                break

            lzma_stream = buf[idx:]
            try:
                decompressor = lzma.LZMADecompressor()
                decompressed = decompressor.decompress(lzma_stream)
                if decompressed and len(decompressed) > 10:
                    decomp_text = decompressed.decode('utf-8', errors='ignore')

                    # Парсимо склеєні JSON об'єкти (MakeCode може мати два підряд: метадані та файли)
                    decoder = json.JSONDecoder()
                    pos = 0
                    project_files = {}

                    while pos < len(decomp_text):
                        sub_text = decomp_text[pos:].lstrip()
                        if not sub_text:
                            break
                        try:
                            obj, end_idx = decoder.raw_decode(sub_text)
                            if isinstance(obj, dict):
                                project_files.update(obj)
                            skipped = len(decomp_text[pos:]) - len(sub_text)
                            pos += skipped + end_idx
                        except Exception:
                            break

                    main_py = project_files.get('main.py')
                    main_ts = project_files.get('main.ts')
                    main_blocks = project_files.get('main.blocks')

                    if main_py and len(main_py.strip()) > 0:
                        return main_py.strip(), 'python', 'MakeCode Python (LZMA PXT)', project_files
                    elif main_ts and len(main_ts.strip()) > 0:
                        return main_ts.strip(), 'typescript', 'MakeCode TypeScript / Blocks (LZMA PXT)', project_files
                    elif main_blocks:
                        return main_blocks.strip(), 'xml', 'MakeCode Blocks (LZMA XML)', project_files

            except Exception:
                pass

            search_pos = idx + 1

    return None, None, None, {}


def _try_extract_makecode_json(all_bytes):
    """
    Витягує нестиснений вихідний код проєкту MakeCode PXT з JSON.
    """
    if not all_bytes:
        return None, None, None, {}

    markers = [b'"main.ts"', b'"main.py"', b'"pxt.json"', b'pxt-microbit', b'"main.blocks"']
    found_idx = -1
    for m in markers:
        idx = all_bytes.find(m)
        if idx != -1:
            found_idx = idx
            break

    if found_idx == -1:
        return None, None, None, {}

    search_start = max(0, found_idx - 4096)
    start_brace = -1
    for i in range(found_idx, search_start - 1, -1):
        if all_bytes[i] == ord(b'{'):
            start_brace = i
            break

    if start_brace != -1:
        depth = 0
        in_string = False
        escape = False
        end_brace = -1

        for i in range(start_brace, min(len(all_bytes), start_brace + 500000)):
            b = all_bytes[i]
            c = chr(b) if b < 128 else ''

            if escape:
                escape = False
                continue

            if c == '\\':
                escape = True
                continue

            if c == '"':
                in_string = not in_string
                continue

            if not in_string:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end_brace = i + 1
                        break

        if end_brace != -1:
            json_slice = all_bytes[start_brace:end_brace]
            try:
                json_str = json_slice.decode('utf-8', errors='ignore')
                parsed = json.loads(json_str)
                if isinstance(parsed, dict):
                    main_py = parsed.get('main.py')
                    main_ts = parsed.get('main.ts')
                    if main_py and len(main_py.strip()) > 0:
                        return main_py.strip(), 'python', 'MakeCode Python (PXT JSON)', parsed
                    elif main_ts and len(main_ts.strip()) > 0:
                        return main_ts.strip(), 'typescript', 'MakeCode TypeScript (PXT JSON)', parsed
            except Exception:
                pass

    py_code = _extract_json_string_value(all_bytes, b'"main.py"')
    if py_code:
        return py_code, 'python', 'MakeCode Python (PXT Raw)', {}

    ts_code = _extract_json_string_value(all_bytes, b'"main.ts"')
    if ts_code:
        return ts_code, 'typescript', 'MakeCode TypeScript (PXT Raw)', {}

    return None, None, None, {}


def _extract_json_string_value(data_bytes, key_bytes):
    """Витягує значення строкового поля JSON."""
    pos = data_bytes.find(key_bytes)
    if pos == -1:
        return None

    colon_pos = data_bytes.find(b':', pos + len(key_bytes))
    if colon_pos == -1 or colon_pos - pos > 30:
        return None

    quote_start = data_bytes.find(b'"', colon_pos + 1)
    if quote_start == -1 or quote_start - colon_pos > 20:
        return None

    i = quote_start + 1
    escape = False
    quote_end = -1
    while i < len(data_bytes):
        b = data_bytes[i]
        c = chr(b) if b < 128 else ''
        if escape:
            escape = False
            i += 1
            continue
        if c == '\\':
            escape = True
            i += 1
            continue
        if c == '"':
            quote_end = i
            break
        i += 1

    if quote_end != -1:
        raw_str_bytes = data_bytes[quote_start + 1 : quote_end]
        try:
            json_wrapped = b'"' + raw_str_bytes + b'"'
            return json.loads(json_wrapped.decode('utf-8', errors='ignore'))
        except Exception:
            try:
                return raw_str_bytes.decode('utf-8', errors='ignore').replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
            except Exception:
                pass

    return None


def _try_extract_micropython_header(all_bytes, memory_map):
    """
    Шукає офіційний заголовок MicroPython для micro:bit:
    Сигнатура: 0x4D 0x50 ('MP') + uint16 (little-endian) довжина скрипта + UTF-8 текст.
    """
    search_pos = 0
    while True:
        pos = all_bytes.find(b'MP', search_pos)
        if pos == -1 or pos + 4 > len(all_bytes):
            break

        length = all_bytes[pos + 2] | (all_bytes[pos + 3] << 8)
        if 5 <= length <= 65535 and pos + 4 + length <= len(all_bytes):
            script_bytes = all_bytes[pos + 4 : pos + 4 + length]
            try:
                decoded = script_bytes.decode('utf-8')
                if any(k in decoded for k in ['import', 'from microbit', 'display', 'basic', 'button', 'pin', 'while', 'for', 'sleep', 'def', '#', '=']):
                    return decoded.strip(), 'python', 'MicroPython (офіційний MP заголовок)'
            except Exception:
                pass

        search_pos = pos + 2

    # Пошук за відомими адресами MicroPython у Flash пам'яті
    known_script_addresses = [0x3E000, 0x70000, 0x78000, 0x3C000, 0x38000]
    for base_addr in known_script_addresses:
        if base_addr in memory_map and base_addr + 1 in memory_map:
            if memory_map[base_addr] == 0x4D and memory_map[base_addr + 1] == 0x50:
                length = memory_map.get(base_addr + 2, 0) | (memory_map.get(base_addr + 3, 0) << 8)
                if 5 <= length <= 65535:
                    script_bytes = bytearray()
                    for offset in range(length):
                        script_bytes.append(memory_map.get(base_addr + 4 + offset, 0))
                    try:
                        decoded = script_bytes.decode('utf-8')
                        return decoded.strip(), 'python', f'MicroPython (Flash адреса 0x{base_addr:X})'
                    except Exception:
                        pass

    return None, None, None


def _try_extract_generic_text_code(all_bytes):
    """
    Шукає зв'язні текстові фрагменти коду Python або TypeScript у бінарному масиві.
    """
    text_chunks = []
    cur_chunk = bytearray()

    for b in all_bytes:
        if 32 <= b <= 126 or b in (9, 10, 13) or b >= 160:
            cur_chunk.append(b)
        else:
            if len(cur_chunk) >= 25:
                try:
                    decoded = cur_chunk.decode('utf-8', errors='ignore').strip()
                    keywords = ['from microbit import', 'display.show', 'display.scroll', 'basic.show', 'input.on_button', 'input.onButtonPressed', 'music.play', 'pin0', 'pin1', 'while True', 'def ', 'function ']
                    if any(k in decoded for k in keywords):
                        text_chunks.append(decoded)
                except Exception:
                    pass
            cur_chunk = bytearray()

    if text_chunks:
        full_code = "\n\n".join(text_chunks)
        lang = 'typescript' if ('function' in full_code or 'let ' in full_code or 'input.onButtonPressed' in full_code) else 'python'
        return full_code, lang, 'Текстовий аналіз Flash пам\'яті'

    return None, None, None


def _analyze_microbit_features(code_text, language):
    """
    Аналізує вилучений вихідний код та формує структурований семантичний звіт
    про всі використані апаратні можливості micro:bit для ШІ Gemini.
    """
    features = []

    # 1. Дисплей 5x5 LED
    if re.search(r'(?:display\.show|display\.scroll|display\.set_pixel|display\.clear|basic\.show_string|basic\.show_icon|basic\.show_number|basic\.show_leds|basic\.showString|basic\.showIcon|basic\.showNumber|basic\.showLeds|basic\.clearScreen|basic_show_icon)', code_text, re.IGNORECASE):
        icons = re.findall(r'(?:IconNames\.|Image\.)([A-Z_a-z]+)', code_text)
        strings = re.findall(r'(?:show_string|showString|scroll)\s*\(\s*["\']([^"\']+)["\']', code_text)
        feat_desc = "🖥️ Світлодіодний дисплей 5x5 (LED)"
        extra = []
        if icons: extra.append(f"іконки: {', '.join(set(icons[:5]))}")
        if strings: extra.append(f"текст: «{', '.join(set(strings[:3]))}»")
        if extra: feat_desc += f" [{'; '.join(extra)}]"
        features.append(feat_desc)

    # 2. Кнопки A та B, сенсорний логотип
    buttons_used = []
    if re.search(r'(?:button_a|Button\.A)', code_text): buttons_used.append("Кнопка A")
    if re.search(r'(?:button_b|Button\.B)', code_text): buttons_used.append("Кнопка B")
    if re.search(r'(?:pin_logo|TouchPin\.Logo|onLogoEvent)', code_text): buttons_used.append("Сенсорний логотип (V2)")
    if buttons_used:
        features.append(f"🔘 Введення з кнопок: {', '.join(buttons_used)}")

    # 3. Акселерометр та жести (рух, струс, нахил)
    if re.search(r'(?:accelerometer|Gesture|onGesture|get_gestures|is_gesture)', code_text, re.IGNORECASE):
        gestures = re.findall(r'(?:Gesture\.|was_gesture\(\s*["\'])([A-Za-z0-9_]+)', code_text)
        g_str = f" (жести: {', '.join(set(gestures))})" if gestures else ""
        features.append(f"🧭 Акселерометр / Датчик руху та положення{g_str}")

    # 4. Компас / Магнітометр
    if re.search(r'(?:compass|input\.compassHeading|compass\.heading)', code_text, re.IGNORECASE):
        features.append("🧭 Електронний компас (магнітометр)")

    # 5. Термометр
    if re.search(r'(?:temperature|input\.temperature)', code_text, re.IGNORECASE):
        features.append("🌡️ Датчик температури процесора")

    # 6. Датчик освітленості
    if re.search(r'(?:display\.read_light_level|input\.lightLevel)', code_text, re.IGNORECASE):
        features.append("☀️ Датчик рівня освітленості")

    # 7. Звук та мікрофон
    if re.search(r'(?:music\.|soundExpression|input\.soundLevel|onSound|SoundExpression)', code_text, re.IGNORECASE):
        features.append("🔊 Звукові сигнали, мелодії або мікрофон")

    # 8. Радіо / Бездротовий зв'язок
    if re.search(r'(?:radio\.|radio\.send|radio\.receive|radio\.set_group|radio\.onReceived)', code_text, re.IGNORECASE):
        features.append("📻 Бездротовий радіомодуль (обмін даними)")

    # 9. Зовнішні контакти GPIO (Pins 0, 1, 2, 3...)
    pins_found = re.findall(r'(?:pin[0-9]+|DigitalPin\.P[0-9]+|AnalogPin\.P[0-9]+)', code_text, re.IGNORECASE)
    if pins_found:
        features.append(f"🔌 Зовнішні контакти GPIO: {', '.join(sorted(set(pins_found)))}")

    # 10. Алгоритмічні структури (цикли, розгалуження, функції)
    algo_parts = []
    if re.search(r'(?:while\s+True|basic\.forever|forever\s*\(|device_forever)', code_text):
        algo_parts.append("Нескінченний цикл (завжди)")
    if re.search(r'(?:for\s+[a-zA-Z_]|for\s*\()', code_text):
        algo_parts.append("Цикл з лічильником (for)")
    if re.search(r'(?:if\s+|if\s*\()', code_text):
        algo_parts.append("Умовні розгалуження (if/else)")
    if re.search(r'(?:def\s+[a-zA-Z_]|function\s+[a-zA-Z_])', code_text):
        algo_parts.append("Користувацькі функції")
    if re.search(r'(?:sleep\s*\(|basic\.pause\s*\()', code_text):
        algo_parts.append("Таймери та затримки виконання (sleep/pause)")

    if algo_parts:
        features.append(f"⚙️ Алгоритмічні конструкції: {', '.join(algo_parts)}")

    return features


def parse_microbit_hex(file_path, raw_file_url=None):
    """
    Повний розбір файлу BBC micro:bit (.hex).
    Витягує вихідний код (MakeCode LZMA, MakeCode PXT JSON, MicroPython),
    проводить семантичний аналіз та формує детальний звіт для вчителя та ШІ Gemini.
    """
    if not file_path or not os.path.exists(file_path):
        return "", "", "Файл .hex не знайдено на диску."

    try:
        all_bytes, type14_bytes, memory_map, total_lines = _extract_intel_hex_payload(file_path)
    except Exception as e:
        return "", "", f"Помилка розбору Intel HEX: {str(e)}"

    if total_lines == 0:
        return "", "", "Файл .hex порожній."

    extracted_code = None
    source_language = 'python'
    method_name = 'Невизначено'
    extra_project_meta = {}

    # ── КРОК 1: Спроба вилучення MakeCode LZMA (найпоширеніший формат MakeCode Universal HEX) ──
    code, lang, method, meta = _try_extract_makecode_lzma(type14_bytes, all_bytes)
    if code:
        extracted_code = code
        source_language = lang
        method_name = method
        extra_project_meta = meta

    # ── КРОК 2: Спроба вилучення MicroPython (офіційний бінарний заголовок MP) ──
    if not extracted_code:
        code, lang, method = _try_extract_micropython_header(all_bytes, memory_map)
        if code:
            extracted_code = code
            source_language = lang
            method_name = method

    # ── КРОК 3: Спроба вилучення нестисненого MakeCode PXT JSON ────────────────
    if not extracted_code:
        code, lang, method, meta = _try_extract_makecode_json(all_bytes)
        if code:
            extracted_code = code
            source_language = lang
            method_name = method
            extra_project_meta = meta

    # ── КРОК 4: Загальний евристичний пошук коду у пам'яті ─────────────────────
    if not extracted_code:
        code, lang, method = _try_extract_generic_text_code(all_bytes)
        if code:
            extracted_code = code
            source_language = lang
            method_name = method

    # ── КРОК 5: Семантичний аналіз коду для Gemini AI ─────────────────────────
    firmware_size_kb = len(all_bytes) / 1024
    features_detected = _analyze_microbit_features(extracted_code or "", source_language)

    # Якщо є метадані проєкту MakeCode (назва проєкту)
    project_name = extra_project_meta.get('name') or ''
    pxt_json_str = extra_project_meta.get('pxt.json')
    if not project_name and pxt_json_str and isinstance(pxt_json_str, str):
        try:
            p_data = json.loads(pxt_json_str)
            project_name = p_data.get('name', '')
        except Exception:
            pass

    text_lines = [
        "=== BBC MICRO:BIT ПРОЄКТ (.hex) ===",
        f"Технічна статистика прошивки:",
        f"- Загальна кількість рядків Intel HEX: {total_lines}",
        f"- Розмір Flash пам'яті: {firmware_size_kb:.1f} КБ ({len(all_bytes)} байт)",
        f"- Середовище / Метод вилучення: {method_name}",
        f"- Назва проєкту: «{project_name}»" if project_name else "- Назва проєкту: Стандартний проєкт micro:bit",
        f"- Мова вихідного коду: {source_language.upper()}",
    ]

    if features_detected:
        text_lines.append("\n--- ВИЯВЛЕНІ АПАРАТНІ КОМПОНЕНТИ ТА АЛГОРИТМИ ---")
        for feat in features_detected:
            text_lines.append(f"• {feat}")

    if extracted_code:
        text_lines.append(f"\n--- ПОВНИЙ ВИХІДНИЙ КОД ПРОЄКТУ ({source_language.upper()}) ---")
        text_lines.append(extracted_code)
    else:
        text_lines.append("\n[УВАГА: Вихідний код скрипта не знайдено у стандартних розділах Flash пам'яті прошивки]")

    text_summary = "\n".join(text_lines)

    # ── Формування інтерактивного HTML для перегляду ───────────────────────────
    html_parts = []
    html_parts.append('<div class="microbit-project-viewer" style="font-family:inherit;color:var(--color-text-primary);padding:4px 0;">')

    # Заголовок
    html_parts.append(
        '<div style="background:linear-gradient(135deg,rgba(16,185,129,0.12),rgba(5,150,105,0.08));border:1.5px solid rgba(16,185,129,0.4);border-radius:var(--radius-lg);padding:18px 22px;margin-bottom:20px;">'
        '<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px;margin-bottom:14px;">'
        '<div style="display:flex;align-items:center;gap:10px;">'
        '<span style="font-size:32px;">📟</span>'
        '<div>'
        f'<h3 style="margin:0;font-size:17px;font-weight:800;color:var(--color-text-primary);">BBC micro:bit {f"«{html.escape(project_name)}»" if project_name else "Проєкт"} (.hex)</h3>'
        f'<div style="font-size:12px;color:var(--color-text-muted);margin-top:2px;">Формат: <strong>{method_name}</strong> (Flash: {firmware_size_kb:.1f} КБ)</div>'
        '</div>'
        '</div>'
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">'
    )

    if extracted_code:
        html_parts.append(
            '<button type="button" class="btn btn-primary btn-sm" onclick="copyMicrobitExtractedCode(this)" style="font-size:12px;font-weight:800;background:#059669;border-color:#059669;color:#fff;">'
            '📋 Скопіювати код'
            '</button>'
        )

    html_parts.append(
        '<a href="https://makecode.microbit.org/" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11.5px;font-weight:700;background:var(--color-surface);">'
        '⚡ MakeCode Редактор ↗'
        '</a>'
        '<a href="https://python.microbit.org/v/3" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm" style="font-size:11.5px;font-weight:700;background:var(--color-surface);">'
        '🐍 Python Редактор ↗'
        '</a>'
        '</div>'
        '</div>'
    )

    # Метрики
    html_parts.append(
        '<div style="display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:10px;margin-bottom:16px;">'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">📦 Flash пам\'ять</div>'
        f'<div style="font-size:18px;font-weight:800;color:var(--color-primary);margin-top:2px;">{firmware_size_kb:.1f} КБ</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">📝 Рядків HEX</div>'
        f'<div style="font-size:18px;font-weight:800;color:#10b981;margin-top:2px;">{total_lines}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">🛠️ Мова коду</div>'
        f'<div style="font-size:15px;font-weight:800;color:#f59e0b;margin-top:4px;">{source_language.upper()}</div>'
        f'</div>'
        f'<div style="background:var(--color-surface);padding:10px 14px;border-radius:8px;border:1px solid var(--color-border);text-align:center;">'
        f'<div style="font-size:11px;color:var(--color-text-muted);font-weight:700;text-transform:uppercase;">🧩 Компонентів</div>'
        f'<div style="font-size:18px;font-weight:800;color:#8b5cf6;margin-top:2px;">{len(features_detected)}</div>'
        f'</div>'
        '</div>'
        '</div>'
    )

    # Виявлені компоненти
    if features_detected:
        html_parts.append(
            '<div style="background:var(--color-surface);border:1px solid var(--color-border);border-radius:var(--radius-md);padding:14px 18px;margin-bottom:16px;">'
            '<div style="font-size:12px;font-weight:800;color:var(--color-text-muted);text-transform:uppercase;margin-bottom:10px;">🔍 Виявлені компоненти та алгоритми micro:bit:</div>'
            '<div style="display:flex;flex-direction:column;gap:6px;">'
        )
        for feat in features_detected:
            html_parts.append(f'<div style="font-size:13px;color:var(--color-text-primary);display:flex;align-items:center;gap:6px;">• {html.escape(feat)}</div>')
        html_parts.append('</div></div>')

    # Вилучений вихідний код
    if extracted_code:
        html_parts.append(
            '<div style="background:#1e1e2e;border:1px solid rgba(255,255,255,0.1);border-radius:var(--radius-lg);overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,0.15);margin-bottom:20px;">'
            '<div style="display:flex;justify-content:space-between;align-items:center;padding:10px 18px;background:rgba(255,255,255,0.05);border-bottom:1px solid rgba(255,255,255,0.1);">'
            f'<div style="font-size:13px;font-weight:700;color:#93c5fd;font-family:\'JetBrains Mono\',monospace;display:flex;align-items:center;gap:6px;">'
            f'<span>💻</span> <span>Вилучений вихідний код ({source_language.upper()}):</span>'
            f'</div>'
            '<span style="font-size:11px;color:#94a3b8;">✨ Повний синтаксичний лістинг</span>'
            '</div>'
            f'<pre style="margin:0;padding:20px;font-family:\'JetBrains Mono\',Consolas,monospace;font-size:13.5px;line-height:1.65;color:#f8fafc;overflow-x:auto;white-space:pre-wrap;word-break:break-word;" id="microbit-extracted-code-pre"><code class="language-{source_language}">{html.escape(extracted_code)}</code></pre>'
            '</div>'
        )
    else:
        html_parts.append(
            '<div class="alert alert-warning" style="margin-bottom:16px;">'
            '⚠️ Не вдалося вилучити текстовий вихідний код зі стандартних Flash-розділів цього .hex файлу.'
            '</div>'
        )

    # JS функція копіювання
    html_parts.append(
        '<script>'
        'function copyMicrobitExtractedCode(btn) {'
        '  var pre = document.getElementById("microbit-extracted-code-pre");'
        '  if (!pre) return;'
        '  var text = pre.innerText || pre.textContent;'
        '  navigator.clipboard.writeText(text).then(function() {'
        '    var old = btn.innerHTML;'
        '    btn.innerHTML = "✓ Скопійовано!";'
        '    btn.style.background = "#10b981";'
        '    setTimeout(function() { btn.innerHTML = old; btn.style.background = "#059669"; }, 2000);'
        '  });'
        '}'
        '</script>'
    )

    html_parts.append('</div>')  # /.microbit-project-viewer

    return "".join(html_parts), text_summary, None
