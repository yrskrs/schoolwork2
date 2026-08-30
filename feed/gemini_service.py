"""
Сервісний модуль для взаємодії з Google Gemini API.
Забезпечує автоматичний аналіз та попереднє оцінювання робіт учнів за критеріями НУШ.
Підтримує читання файлів без розширення, Word/PDF документів, коду, електронних таблиць,
презентацій, зображень, архівів та інтернет-посилань.
"""

import os
import math
import json
import base64
import mimetypes
import urllib.request
import urllib.error
from urllib.parse import urlparse
from html.parser import HTMLParser
import re
import time
import zipfile
import tarfile
import xml.etree.ElementTree as ET
from django.utils import timezone
from .models import AISettings, Submission, DEFAULT_NUS_SYSTEM_PROMPT, AICriteriaPreset
from .duplicate_detector import check_submission_duplicates, get_normalized_file_content

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def get_ai_settings():
    """Повертає глобальні налаштування ШІ."""
    return AISettings.get_solo()


def _http_post_json(url, payload_dict, timeout=30):
    """
    Виконує HTTP POST запит із JSON тілом через вбудований urllib.
    Повертає (status_code: int, response_data: dict | None, response_text: str).
    """
    json_bytes = json.dumps(payload_dict).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=json_bytes,
        headers={
            'Content-Type': 'application/json; charset=utf-8',
            'Accept': 'application/json'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body_bytes = resp.read()
            body_text = body_bytes.decode('utf-8', errors='replace')
            try:
                data = json.loads(body_text)
                return status, data, body_text
            except json.JSONDecodeError:
                return status, None, body_text
    except urllib.error.HTTPError as e:
        status = e.code
        err_bytes = e.read()
        err_text = err_bytes.decode('utf-8', errors='replace')
        try:
            err_data = json.loads(err_text)
            return status, err_data, err_text
        except json.JSONDecodeError:
            return status, None, err_text
    except urllib.error.URLError as e:
        raise Exception(f"Мережева помилка підключення: {e.reason}")


def clean_model_name(name):
    """Очищує та нормалізує назву моделі Gemini."""
    if not name:
        return 'gemini-2.5-flash'
    name = name.strip()
    if name.startswith('models/'):
        name = name[7:]
    if name == 'gemini-2.0-flash':
        return 'gemini-2.5-flash'
    return name


def test_gemini_connection(api_key=None, model_name=None):
    """
    Перевіряє коректність API ключа та доступність вибраної моделі Google Gemini.
    Повертає (success: bool, message: str, model_used: str).
    """
    settings = get_ai_settings()
    key = api_key.strip() if api_key else (settings.api_key or '').strip()
    model = clean_model_name(model_name or settings.model_name)

    if not key:
        return False, "Google Gemini API Key не вказано в налаштуваннях.", model

    endpoint = f"{GEMINI_API_BASE_URL}/{model}:generateContent?key={key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Тест з'єднання. Напиши коротку відповідь: 'З'єднання зі SchoolNet AI успішне!'"}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 60
        }
    }

    try:
        status_code, data, text = _http_post_json(endpoint, payload, timeout=12)
        if status_code == 200 and data:
            reply_text = ""
            try:
                reply_text = data['candidates'][0]['content']['parts'][0]['text'].strip()
            except (KeyError, IndexError):
                reply_text = "З'єднання встановлено."
            return True, f"Успішно підключено! Відповідь моделі: {reply_text}", model
        elif status_code == 400:
            err = (data or {}).get('error', {}).get('message', text)
            return False, f"Помилка API (400): {err}", model
        elif status_code == 403:
            return False, "Помилка автентифікації (403): Недійсний API Key або відсутній доступ до моделі.", model
        elif status_code == 429:
            return False, "Перевищено ліміт запитів (429 Rate Limit). Спробуйте через кілька хвилин.", model
        else:
            return False, f"Помилка сервера Google ({status_code}): {text[:200]}", model
    except Exception as e:
        return False, f"Помилка підключення: {str(e)}", model


# ═══════════════════════════════════════════════════════════════════════════════
# УТИЛІТИ ДЛЯ ЧИТАННЯ ТА ВИДОБУВАННЯ КОНТЕНТУ З ФАЙЛІВ ТА ІНТЕРНЕТ-ПОСИЛАНЬ
# ═══════════════════════════════════════════════════════════════════════════════

class CleanHTMLTextExtractor(HTMLParser):
    """Видобуває структурований чистий текст із HTML, відкидаючи скрипти, стилі та службові теги."""
    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.skip_tags = {'script', 'style', 'noscript', 'svg', 'head', 'meta', 'link', 'style'}
        self.current_tag_stack = []
        self.page_title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        self.current_tag_stack.append(t)
        if t == 'title':
            self._in_title = True
        if t in {'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'blockquote', 'article', 'section'}:
            self.text_parts.append('\n')

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == 'title':
            self._in_title = False
        if self.current_tag_stack and self.current_tag_stack[-1] == t:
            self.current_tag_stack.pop()
        elif t in self.current_tag_stack:
            self.current_tag_stack.remove(t)
        if t in {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'blockquote', 'article', 'section'}:
            self.text_parts.append('\n')

    def handle_data(self, data):
        if self._in_title:
            self.page_title += data.strip() + " "
        if any(tag in self.skip_tags for tag in self.current_tag_stack):
            return
        cleaned = data.strip()
        if cleaned:
            self.text_parts.append(data)

    def get_clean_text(self):
        full = ''.join(self.text_parts)
        # Очищення від надлишкових порожніх рядків
        lines = [line.strip() for line in full.splitlines() if line.strip()]
        return '\n'.join(lines)


def is_text_file(file_path, sample_size=16384):
    """
    Визначає, чи є файл текстовим (навіть якщо файл не має розширення).
    Перевіряє відсутність нульових байтів та коректність декодування в текст.
    """
    if not os.path.exists(file_path):
        return False
    try:
        file_size = os.path.getsize(file_path)
        if file_size == 0:
            return True

        with open(file_path, 'rb') as f:
            sample = f.read(sample_size)

        if not sample:
            return True

        # Наявність байта 0x00 зазвичай вказує на бінарний файл
        if b'\x00' in sample:
            return False

        # Спроба декодування в поширені текстові кодування
        for enc in ['utf-8', 'utf-8-sig', 'cp1251', 'windows-1251', 'latin-1']:
            try:
                decoded = sample.decode(enc)
                # Перевіряємо відсоток друкованих або пробільних символів
                printable_count = sum(1 for c in decoded if c.isprintable() or c in '\n\r\t ')
                if printable_count / max(len(decoded), 1) > 0.85:
                    return True
            except (UnicodeDecodeError, UnicodeError):
                continue

        return False
    except Exception:
        return False


def read_text_file(file_path, max_chars=60000):
    """
    Надійно зчитує вміст текстового файлу з автоматичним підбором кодування (UTF-8, CP1251 тощо).
    """
    for enc in ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5', 'latin-1']:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.read(max_chars)
                return content
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            break

    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read(max_chars)
    except Exception as e:
        return f"[Помилка читання файлу: {str(e)}]"


def extract_text_from_doc(file_path, max_chars=50000):
    """
    Видобуває текстовий вміст зі старого бінарного або RTF формату Word (.doc).
    """
    try:
        with open(file_path, 'rb') as f:
            data = f.read()

        # 1. Перевірка RTF формату
        if data.startswith(b'{\\rtf'):
            text = re.sub(r'\\\w+\s?', ' ', data.decode('latin-1', errors='ignore'))
            text = re.sub(r'[{}]', '', text)
            clean_lines = [l.strip() for l in text.splitlines() if l.strip()]
            return '\n'.join(clean_lines)[:max_chars]

        # 2. Видобування послідовностей UTF-16LE (OLE Compound Document)
        utf16_matches = re.findall(b'(?:[\x20-\x7e\x0a\x0d\x09\x04\x00-\xff]\x00){4,}', data)
        text_chunks = []
        for m in utf16_matches:
            try:
                decoded = m.decode('utf-16le').strip()
                if len(decoded) > 3 and not decoded.startswith(('Normal', 'Default', 'Heading', 'Table', 'Title', 'Font', 'Times', 'Arial', 'Calibri')):
                    text_chunks.append(decoded)
            except Exception:
                pass

        if text_chunks:
            return '\n'.join(text_chunks)[:max_chars]

        # 3. Видобування послідовностей ASCII / CP1251
        ascii_matches = re.findall(b'[\x20-\x7e\t\n\r\xc0-\xff]{6,}', data)
        ascii_chunks = []
        for m in ascii_matches:
            for enc in ['utf-8', 'cp1251', 'latin-1']:
                try:
                    s = m.decode(enc).strip()
                    if s and len(s) > 5:
                        ascii_chunks.append(s)
                        break
                except Exception:
                    pass

        if ascii_chunks:
            return '\n'.join(ascii_chunks)[:max_chars]

        return "[Документ .doc не містить розпізнаваного тексту або має складний бінарний формат]"
    except Exception as e:
        return f"[Помилка читання .doc файлу: {str(e)}]"


def extract_text_from_pdf(file_path, max_pages=40, max_chars=60000):
    """
    Видобуває текст із PDF файлу за допомогою бібліотеки pypdf.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(file_path)
        num_pages = len(reader.pages)
        pages_to_read = min(num_pages, max_pages)

        extracted_text = []
        for i in range(pages_to_read):
            page_text = reader.pages[i].extract_text() or ""
            if page_text.strip():
                extracted_text.append(f"--- Сторінка {i+1} ---\n{page_text.strip()}")

        full_text = "\n\n".join(extracted_text)
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + f"\n\n[... показано перші {max_chars} символів з PDF]"

        return full_text if full_text.strip() else None
    except Exception:
        return None


def extract_text_from_opendocument(file_path, max_chars=50000):
    """
    Видобуває текст з файлів OpenDocument (.odt, .ods, .odp) через стандартний zipfile та XML.
    """
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            if 'content.xml' not in zf.namelist():
                return None
            xml_bytes = zf.read('content.xml')
            root = ET.fromstring(xml_bytes)
            texts = []
            for elem in root.iter():
                if elem.tag.endswith(('}p', '}h', '}span', '}a', '}table-cell')):
                    if elem.text and elem.text.strip():
                        texts.append(elem.text.strip())
            return '\n'.join(texts)[:max_chars]
    except Exception:
        return None


def extract_text_from_excel(file_path, max_rows=50, max_cols=20):
    """
    Видобуває дані та таблиці з файлу Excel (.xlsx).
    """
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)
        sheet_summaries = []

        for sheetname in wb.sheetnames[:5]:
            sheet = wb[sheetname]
            sheet_lines = [f"📊 Аркуш: {sheetname}"]
            rows_count = 0

            for row in sheet.iter_rows(values_only=True):
                rows_count += 1
                if rows_count > max_rows:
                    sheet_lines.append(f"[... ще рядки]")
                    break
                row_vals = [str(v) if v is not None else "" for v in row[:max_cols]]
                if any(v.strip() for v in row_vals):
                    sheet_lines.append(" | ".join(row_vals))

            sheet_summaries.append("\n".join(sheet_lines))

        wb.close()
        return "\n\n".join(sheet_summaries)
    except Exception as e:
        return f"[Помилка читання Excel таблиці: {str(e)}]"


def extract_text_from_powerpoint(file_path, max_slides=30):
    """
    Видобуває текст слайдів із презентації PowerPoint (.pptx).
    """
    try:
        from pptx import Presentation
        prs = Presentation(file_path)
        slides_text = []

        for idx, slide in enumerate(prs.slides[:max_slides], 1):
            slide_lines = [f"📽️ Слайд {idx}:"]
            for shape in slide.shapes:
                if hasattr(shape, "text") and shape.text.strip():
                    slide_lines.append(shape.text.strip())
            if len(slide_lines) > 1:
                slides_text.append("\n".join(slide_lines))

        return "\n\n".join(slides_text) if slides_text else "[Презентація не містить тексту або порожня]"
    except Exception as e:
        return f"[Помилка читання презентації .pptx: {str(e)}]"


def extract_text_from_archive(file_path, ext, max_files=8, max_file_chars=12000):
    """
    Інспектує структуру архіву (.zip, .tar, .gz) та зчитує ключові програмні/текстові файли зсередини.
    """
    try:
        file_tree = []
        extracted_files_content = []

        if ext == '.zip':
            with zipfile.ZipFile(file_path, 'r') as zf:
                infolist = zf.infolist()
                for info in infolist[:40]:
                    file_tree.append(f"• {info.filename} ({info.file_size / 1024:.1f} КБ)")

                # Шукаємо ключові файли з кодом або текстом для аналізу ШІ
                code_exts = {'.py', '.html', '.htm', '.css', '.js', '.ts', '.json', '.md', '.txt', '.c', '.cpp', '.h', '.java', '.pas', '.sql', '.sh'}
                read_count = 0

                for info in infolist:
                    if info.is_dir() or read_count >= max_files:
                        continue
                    fname = info.filename
                    f_ext = os.path.splitext(fname)[1].lower()

                    if f_ext in code_exts or not f_ext:
                        if info.file_size < 500 * 1024:  # до 500 КБ
                            try:
                                with zf.open(info) as subfile:
                                    raw_bytes = subfile.read(max_file_chars)
                                    # Перевірка чи текст
                                    if b'\x00' not in raw_bytes[:1024]:
                                        for enc in ['utf-8', 'cp1251', 'latin-1']:
                                            try:
                                                text = raw_bytes.decode(enc)
                                                extracted_files_content.append(f"📄 Файл з архіву: {fname}\n```\n{text}\n```")
                                                read_count += 1
                                                break
                                            except UnicodeDecodeError:
                                                continue
                            except Exception:
                                pass

        elif ext in ('.tar', '.gz', '.tgz'):
            with tarfile.open(file_path, 'r:*') as tf:
                members = tf.getmembers()
                for m in members[:40]:
                    file_tree.append(f"• {m.name} ({m.size / 1024:.1f} КБ)")

                code_exts = {'.py', '.html', '.htm', '.css', '.js', '.ts', '.json', '.md', '.txt', '.c', '.cpp', '.h', '.java', '.pas', '.sql', '.sh'}
                read_count = 0

                for m in members:
                    if m.isdir() or read_count >= max_files:
                        continue
                    fname = m.name
                    f_ext = os.path.splitext(fname)[1].lower()

                    if f_ext in code_exts or not f_ext:
                        if m.size < 500 * 1024:
                            try:
                                f_obj = tf.extractfile(m)
                                if f_obj:
                                    raw_bytes = f_obj.read(max_file_chars)
                                    if b'\x00' not in raw_bytes[:1024]:
                                        for enc in ['utf-8', 'cp1251', 'latin-1']:
                                            try:
                                                text = raw_bytes.decode(enc)
                                                extracted_files_content.append(f"📄 Файл з архіву: {fname}\n```\n{text}\n```")
                                                read_count += 1
                                                break
                                            except UnicodeDecodeError:
                                                continue
                            except Exception:
                                pass

        summary_parts = [f"📦 Вміст архіву ({len(file_tree)} файлів/папок):\n" + "\n".join(file_tree[:25])]
        if len(file_tree) > 25:
            summary_parts.append(f"... та ще {len(file_tree) - 25} елементів.")

        if extracted_files_content:
            summary_parts.append("\n🔍 Прочитаний вміст ключових файлів з архіву:")
            summary_parts.extend(extracted_files_content)

        return "\n\n".join(summary_parts)

    except Exception as e:
        return f"[Прикріплено архів ({ext}), помилка читання структури: {str(e)}]"


def fetch_url_content(url, timeout=10, max_chars=20000):
    """
    Завантажує вміст інтернет-посилання (веб-сторінки, YouTube oEmbed, GitHub)
    та повертає очищений текстовий контент для аналізу ШІ.
    """
    if not url:
        return None, None, "Посилання порожнє"

    url = url.strip()
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ('http', 'https'):
        return None, None, f"Непідтримуваний протокол URL: {url}"

    # 1. Спеціальна обробка YouTube
    if 'youtube.com' in parsed.netloc or 'youtu.be' in parsed.netloc:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
            req = urllib.request.Request(oembed_url, headers={'User-Agent': 'SchoolNet-AI/2.0'})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                title = data.get('title', 'YouTube Video')
                author = data.get('author_name', '')
                text_info = f"🎬 Відео YouTube: «{title}» (Автор/Канал: {author})\nПосилання: {url}"
                return title, text_info, None
        except Exception:
            return "YouTube відео", f"🎬 Посилання на відео YouTube: {url}", None

    # 2. Спеціальна обробка посилань на файли GitHub (blob -> raw)
    fetch_url = url
    if 'github.com' in parsed.netloc and '/blob/' in parsed.path:
        fetch_url = url.replace('github.com', 'raw.githubusercontent.com').replace('/blob/', '/')

    # 3. Загальне завантаження веб-сторінки
    try:
        req = urllib.request.Request(
            fetch_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.8',
                'Accept-Language': 'uk-UA,uk;q=0.9,en;q=0.8'
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get('Content-Type', '').lower()
            raw_bytes = resp.read(500 * 1024)  # до 500 КБ

            # Визначаємо кодування
            charset = 'utf-8'
            if 'charset=' in content_type:
                charset = content_type.split('charset=')[-1].split(';')[0].strip()

            try:
                html_or_text = raw_bytes.decode(charset, errors='replace')
            except Exception:
                html_or_text = raw_bytes.decode('utf-8', errors='replace')

            # Якщо це чистий текст або JSON/код
            if 'text/plain' in content_type or 'application/json' in content_type:
                clean_text = html_or_text[:max_chars]
                return "Текстовий веб-ресурс", clean_text, None

            # Парсимо HTML
            parser = CleanHTMLTextExtractor()
            try:
                parser.feed(html_or_text)
                title = parser.page_title.strip() or "Веб-сторінка"
                clean_text = parser.get_clean_text()[:max_chars]
                if clean_text:
                    formatted_content = f"🌐 Вміст веб-сторінки («{title}» | {url}):\n{clean_text}"
                    return title, formatted_content, None
                else:
                    return title, f"🌐 Веб-сторінка: {url} (Сторінка відкрилась, але не містить видимого статичного тексту).", None
            except Exception as pe:
                return "Веб-сторінка", f"🌐 Веб-сторінка: {url} (Помилка аналізу HTML: {str(pe)})", None

    except urllib.error.HTTPError as e:
        return None, None, f"HTTP {e.code} при відкритті {url}"
    except urllib.error.URLError as e:
        return None, None, f"Помилка мережі при відкритті {url}: {e.reason}"
    except Exception as e:
        return None, None, f"Помилка завантаження {url}: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# ОСНОВНА ФУНКЦІЯ ВИДОБУВАННЯ ВМІСТУ ЗДАЧІ РОБОТИ УЧНЯ
# ═══════════════════════════════════════════════════════════════════════════════

def extract_submission_content(submission):
    """
    Видобуває текст та медіа-вміст із зданої учнем роботи:
    - Файли без розширення (детекція та відкриття як текст)
    - Word документи (.docx, .doc, .odt, .rtf)
    - PDF документи (multimodal base64 inlineData + локальний текст)
    - Всі інші текстові формати та вихідний код (.py, .js, .ts, .html, .css, .json, .csv, .cpp, .java, .sql, .sh тощо)
    - Електронні таблиці (.xlsx, .ods, .csv) та презентації (.pptx, .odp)
    - Зображення (мультимодальний зір Gemini)
    - Інтернет-посилання (завантаження та парсинг веб-сторінок, YouTube, GitHub)
    - Архіви (.zip, .tar, .gz) із видобуванням структури та вмісту вихідного коду всередині

    Повертає (text_parts: list[str], inline_media: list[dict], error: str|None).
    """
    text_parts = []
    inline_media = []

    # 1. Текстовий коментар учня
    if submission.comment_student:
        text_parts.append(f"Коментар/відповідь учня:\n{submission.comment_student.strip()}")
        # Перевіряємо, чи учень не додав посилання у коментарі
        url_match = re.search(r'https?://[^\s<>"\']+', submission.comment_student)
        if url_match and not submission.link:
            u = url_match.group(0)
            u_title, u_content, u_err = fetch_url_content(u)
            if u_content:
                text_parts.append(f"Вміст веб-сторінки за посиланням з коментаря:\n{u_content}")

    # 2. Посилання на роботу (якщо є)
    if submission.link:
        link_url = submission.link.strip()
        text_parts.append(f"Посилання на виконаний проєкт/роботу:\n{link_url}")
        u_title, u_content, u_err = fetch_url_content(link_url)
        if u_content:
            text_parts.append(f"Автоматично завантажений вміст сторінки ({link_url}):\n{u_content}")
        elif u_err:
            text_parts.append(f"[Примітка щодо посилання {link_url}: не вдалося завантажити вміст сторінки ({u_err})]")

    # 3. Прикріплені файли (один або декілька)
    submission_files = list(submission.files.all()) if hasattr(submission, 'files') and submission.files.exists() else ([submission] if submission.file else [])

    for sf in submission_files:
        f_obj = getattr(sf, 'file', None)
        if not f_obj or not hasattr(f_obj, 'path') or not os.path.exists(f_obj.path):
            continue
        file_path = f_obj.path
        filename = getattr(sf, 'original_name', '') or os.path.basename(file_path)
        ext = getattr(sf, 'get_extension', lambda: os.path.splitext(file_path)[1].lower())() or os.path.splitext(file_path)[1].lower()
        file_size_kb = os.path.getsize(file_path) / 1024

        # ── А. ФАЙЛИ БЕЗ РОЗШИРЕННЯ (Default to text file detection) ──────────
        if not ext:
            if is_text_file(file_path):
                content = read_text_file(file_path)
                text_parts.append(f"Вміст прикріпленого текстового файлу без розширення ({filename}):\n```\n{content}\n```")
            else:
                # Перевіряємо чи це зображення без розширення
                try:
                    from PIL import Image
                    with Image.open(file_path) as img:
                        fmt = (img.format or 'JPEG').lower()
                        mime = f"image/{fmt}"
                    with open(file_path, 'rb') as img_f:
                        b64_data = base64.b64encode(img_f.read()).decode('utf-8')
                        inline_media.append({
                            "mime_type": mime,
                            "data": b64_data
                        })
                        text_parts.append(f"[Прикріплено фотозображення ({filename}, {file_size_kb:.1f} КБ)]")
                except Exception:
                    text_parts.append(f"[Прикріплено бінарний файл без розширення ({filename}, {file_size_kb:.1f} КБ)]")

        # ── Б. ТЕКСТОВІ ФАЙЛИ ТА ВИХІДНИЙ КОД ──────────────────────────────────
        elif ext in [
            '.txt', '.text', '.log', '.md', '.markdown', '.rst', '.csv', '.tsv',
            '.json', '.json5', '.jsonl', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env',
            '.xml', '.html', '.htm', '.xhtml', '.css', '.scss', '.sass', '.less',
            '.js', '.mjs', '.cjs', '.jsx', '.ts', '.tsx', '.vue', '.svelte',
            '.py', '.pyw', '.ipynb',
            '.c', '.cpp', '.cxx', '.cc', '.h', '.hpp', '.cs',
            '.java', '.kt', '.kts', '.scala', '.groovy',
            '.pas', '.pp', '.dpr', '.inc',
            '.php', '.phtml', '.rb', '.go', '.rs', '.swift', '.lua',
            '.sql', '.sh', '.bash', '.zsh', '.bat', '.cmd', '.ps1',
            '.tex', '.properties', '.gradle'
        ]:
            code_text = read_text_file(file_path)
            lang_tag = ext.replace('.', '')
            text_parts.append(f"Вміст прикріпленого файлу ({filename}, {file_size_kb:.1f} КБ):\n```{lang_tag}\n{code_text}\n```")

        # ── В. PDF ДОКУМЕНТИ ─────────────────────────────────────────────────
        elif ext == '.pdf':
            pdf_read_success = False
            # 1. Мультимодальна передача в Gemini (Gemini natively parses PDF files!)
            try:
                # Якщо розмір PDF до 18 МБ — передаємо як inlineData
                if os.path.getsize(file_path) <= 18 * 1024 * 1024:
                    with open(file_path, 'rb') as pdf_f:
                        pdf_bytes = pdf_f.read()
                        b64_data = base64.b64encode(pdf_bytes).decode('utf-8')
                        inline_media.append({
                            "mime_type": "application/pdf",
                            "data": b64_data
                        })
                        pdf_read_success = True
            except Exception as e:
                text_parts.append(f"[Помилка читання байтів PDF: {e}]")

            # 2. Локальне видобування тексту через pypdf для текстового контексту
            local_pdf_text = extract_text_from_pdf(file_path)
            if local_pdf_text:
                text_parts.append(f"Текстовий вміст прикріпленого PDF документа ({filename}, {file_size_kb:.1f} КБ):\n{local_pdf_text}")
                pdf_read_success = True
            else:
                text_parts.append(f"[Прикріплено PDF документ ({filename}, {file_size_kb:.1f} КБ) — передано на візуальний мультимодальний аналіз ШІ]")

            if not pdf_read_success:
                text_parts.append(f"[Не вдалося обробити PDF документ {filename}]")

        # ── Г. WORD ДОКУМЕНТИ ТА ОФІСНІ ТЕКСТИ (.docx, .doc, .odt, .rtf) ──────
        elif ext == '.docx':
            try:
                import docx
                doc = docx.Document(file_path)
                doc_paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                for table in doc.tables:
                    for row in table.rows:
                        row_cells = [cell.text.strip() for cell in row.cells]
                        if any(row_cells):
                            doc_paragraphs.append(" | ".join(row_cells))
                full_doc_text = "\n".join(doc_paragraphs[:400])
                if full_doc_text.strip():
                    text_parts.append(f"Вміст документа Word ({filename}, {file_size_kb:.1f} КБ):\n{full_doc_text}")
                else:
                    text_parts.append(f"Документ Word ({filename}) порожній або містить лише графічні елементи.")
            except Exception as e:
                # Fallback to mammoth or XML
                try:
                    import mammoth
                    with open(file_path, "rb") as docx_file:
                        result = mammoth.extract_raw_text(docx_file)
                        if result.value.strip():
                            text_parts.append(f"Вміст документа Word ({filename}):\n{result.value.strip()[:50000]}")
                        else:
                            text_parts.append(f"[Не вдалося прочитати .docx документ: {e}]")
                except Exception:
                    text_parts.append(f"[Не вдалося прочитати .docx документ: {e}]")

        elif ext == '.doc':
            doc_text = extract_text_from_doc(file_path)
            text_parts.append(f"Вміст документа Word (.doc) ({filename}, {file_size_kb:.1f} КБ):\n{doc_text}")

        elif ext == '.odt':
            odt_text = extract_text_from_opendocument(file_path)
            if odt_text:
                text_parts.append(f"Вміст документа OpenDocument (.odt) ({filename}):\n{odt_text}")
            else:
                text_parts.append(f"[Документ .odt {filename} порожній або не вдалося прочитати]")

        elif ext == '.rtf':
            rtf_text = extract_text_from_doc(file_path)
            text_parts.append(f"Вміст RTF документа ({filename}):\n{rtf_text}")

        # ── Д. ЕЛЕКТРОННІ ТАБЛИЦІ (.xlsx, .xls, .ods) ─────────────────────────
        elif ext in ['.xlsx', '.xls']:
            excel_text = extract_text_from_excel(file_path)
            text_parts.append(f"Вміст таблиці Excel ({filename}, {file_size_kb:.1f} КБ):\n{excel_text}")

        elif ext == '.ods':
            ods_text = extract_text_from_opendocument(file_path)
            text_parts.append(f"Вміст таблиці OpenDocument (.ods) ({filename}):\n{ods_text or '[Порожня таблиця]'}")

        # ── Е. ПРЕЗЕНТАЦІЇ (.pptx, .ppt, .odp) ─────────────────────────────────
        elif ext in ['.pptx', '.ppt']:
            pptx_text = extract_text_from_powerpoint(file_path)
            text_parts.append(f"Вміст презентації PowerPoint ({filename}, {file_size_kb:.1f} КБ):\n{pptx_text}")

        elif ext == '.odp':
            odp_text = extract_text_from_opendocument(file_path)
            text_parts.append(f"Вміст презентації OpenDocument (.odp) ({filename}):\n{odp_text or '[Порожня презентація]'}")

        # ── Є. ЗОБРАЖЕННЯ (ФОТО ЗОШИТІВ, СКРІНШОТИ, СХЕМИ) ───────────────────
        elif ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif', '.svg', '.heic', '.heif']:
            try:
                mime_type = mimetypes.guess_type(file_path)[0] or 'image/jpeg'
                if ext == '.webp':
                    mime_type = 'image/webp'
                elif ext == '.png':
                    mime_type = 'image/png'
                elif ext in ['.jpg', '.jpeg']:
                    mime_type = 'image/jpeg'

                with open(file_path, 'rb') as img_f:
                    img_bytes = img_f.read()
                    b64_data = base64.b64encode(img_bytes).decode('utf-8')
                    inline_media.append({
                        "mime_type": mime_type,
                        "data": b64_data
                    })
                    text_parts.append(f"[Прикріплено фотозображення зошита/роботи: {filename} ({file_size_kb:.1f} КБ)]")
            except Exception as e:
                text_parts.append(f"[Помилка обробки зображення: {e}]")

        # ── Ж. АРХІВИ (.zip, .tar, .gz, .tgz, .rar, .7z) ───────────────────────
        elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.tgz']:
            archive_summary = extract_text_from_archive(file_path, ext)
            text_parts.append(f"Вміст прикріпленого архіву ({filename}, {file_size_kb:.1f} КБ):\n{archive_summary}")

        # ── З. SCRATCH 3 ПРОЄКТИ (.sb3) ───────────────────────────────────────
        elif ext == '.sb3':
            try:
                from .scratch_utils import parse_scratch_sb3
                _, scratch_summary, scratch_err = parse_scratch_sb3(file_path)
                if scratch_summary:
                    text_parts.append(f"Розбір структури та коду Scratch 3 проєкту ({filename}, {file_size_kb:.1f} КБ):\n{scratch_summary}")
                elif scratch_err:
                    text_parts.append(f"[Помилка читання Scratch проєкту {filename}: {scratch_err}]")
            except Exception as e:
                text_parts.append(f"[Не вдалося розібрати Scratch проєкт {filename}: {e}]")

        # ── И. BBC MICRO:BIT ПРОЄКТИ (.hex) ───────────────────────────────────
        elif ext == '.hex':
            try:
                from .microbit_utils import parse_microbit_hex
                _, hex_summary, hex_err = parse_microbit_hex(file_path)
                if hex_summary:
                    text_parts.append(f"Вміст та вихідний код BBC micro:bit проєкту ({filename}, {file_size_kb:.1f} КБ):\n{hex_summary}")
                elif hex_err:
                    text_parts.append(f"[Помилка читання micro:bit файлу {filename}: {hex_err}]")
            except Exception as e:
                text_parts.append(f"[Не вдалося розібрати micro:bit проєкт {filename}: {e}]")

        # ── І. MICROSOFT ACCESS БАЗИ ДАНИХ (.mdb / .accdb) ──────────────────
        elif ext in ['.mdb', '.accdb']:
            try:
                from .access_utils import extract_access_text_for_ai
                access_text = extract_access_text_for_ai(file_path)
                if access_text:
                    text_parts.append(f"Вміст бази даних Microsoft Access ({filename}, {file_size_kb:.1f} КБ):\n{access_text}")
                else:
                    text_parts.append(f"[Прикріплено базу даних Microsoft Access ({filename}, {file_size_kb:.1f} КБ) — не вдалося вилучити вміст]")
            except Exception as e:
                text_parts.append(f"[Помилка читання Microsoft Access файлу {filename}: {e}]")

        # ── Ї. ІНШІ / НЕВІДОМІ РОЗШИРЕННЯ ─────────────────────────────────────
        else:
            # Спроба прочитати як текстовий файл
            if is_text_file(file_path):
                content = read_text_file(file_path)
                text_parts.append(f"Вміст прикріпленого файлу ({filename}, {file_size_kb:.1f} КБ):\n```\n{content}\n```")
            else:
                text_parts.append(f"[Прикріплено файл формату {ext} ({filename}, {file_size_kb:.1f} КБ)]")

    if not text_parts and not inline_media:
        return text_parts, inline_media, "Учень не додав жодного тексту, посилання чи придатного файлу для перевірки."

    return text_parts, inline_media, None


def extract_json_from_text(text):
    """
    Надійно видобуває JSON-об'єкт із будь-якого тексту чи markdown-блоку відповіді Gemini.
    """
    if not text:
        return None
    text = text.strip()

    # 1. Пряма спроба парсингу
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2. Пошук markdown блоку ```json ... ``` або ``` ... ```
    code_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_match:
        try:
            return json.loads(code_match.group(1).strip())
        except Exception:
            pass

    # 3. Пошук першої '{' та останньої '}'
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace:last_brace + 1].strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass

    return None


def evaluate_submission_with_gemini(submission, custom_prompt=None, ai_settings=None, preset_id=None, criteria_preset=None, selected_gr_codes=None):
    """
    Виконує педагогічний аналіз та попереднє оцінювання роботи учня за допомогою Google Gemini.
    Підтримує чергу пріоритетів моделей, вибір шаблону критеріїв та вибір конкретних ГР (прапорцями).
    Результати записуються безпосередньо у submission (ai_suggested_grade, ai_feedback, ai_status тощо).
    """
    settings = ai_settings or get_ai_settings()
    api_key = (settings.api_key or '').strip()

    if not api_key:
        error_msg = "Google Gemini API Key не налаштовано в системі. Вкажіть ключ у Налаштуваннях ШІ."
        submission.ai_status = 'failed'
        submission.ai_error_reason = error_msg
        submission.save(update_fields=['ai_status', 'ai_error_reason'])
        return {'status': 'failed', 'error': error_msg}

    # Визначаємо шаблон критеріїв оцінювання
    selected_preset = criteria_preset
    if not selected_preset and preset_id:
        try:
            selected_preset = AICriteriaPreset.objects.filter(id=preset_id).first()
        except Exception:
            selected_preset = None

    if not selected_preset:
        try:
            AICriteriaPreset.ensure_default_presets()
            selected_preset = AICriteriaPreset.objects.filter(is_default=True).first() or AICriteriaPreset.objects.first()
        except Exception:
            selected_preset = None

    # Визначаємо, чи обрана класична / традиційна система оцінювання
    is_traditional = False
    if selected_preset and selected_preset.evaluation_type == 'traditional':
        is_traditional = True
    elif custom_prompt and ('класичн' in custom_prompt.lower() or 'традиційн' in custom_prompt.lower()) and 'груп' not in custom_prompt.lower():
        is_traditional = True

    # Визначаємо перелік груп результатів для оцінювання (з урахуванням вибору вчителя)
    all_preset_grs = selected_preset.get_gr_list() if (selected_preset and not is_traditional) else []
    active_grs = []
    if not is_traditional:
        if selected_gr_codes and all_preset_grs:
            selected_set = {str(c).strip().lower() for c in selected_gr_codes if str(c).strip()}
            for gr in all_preset_grs:
                gr_code = str(gr.get('code', '')).strip().lower()
                gr_name = str(gr.get('name', '')).strip().lower()
                if gr_code in selected_set or any(s in gr_code or s in gr_name for s in selected_set):
                    active_grs.append(gr)
        elif all_preset_grs:
            active_grs = all_preset_grs

    # Видобуваємо вміст роботи
    text_parts, inline_media, extract_error = extract_submission_content(submission)

    if extract_error and not text_parts and not inline_media:
        submission.ai_status = 'unsupported'
        submission.ai_error_reason = extract_error
        submission.save(update_fields=['ai_status', 'ai_error_reason'])
        return {'status': 'unsupported', 'error': extract_error}

    # Формуємо контекст завдання
    assignment = submission.assignment
    subject_name = assignment.subject.name if assignment and assignment.subject else "Загальний предмет"
    class_name = submission.class_group.name if submission.class_group else "Шкільний клас"
    assignment_title = assignment.title if assignment else "Самостійна робота"
    assignment_desc = assignment.description if assignment else "Вимоги до виконання роботи."
    preset_name_display = selected_preset.name if selected_preset else "Критерії НУШ"

    # Збираємо запит
    prompt_lines = [
        f"ПРЕДМЕТ: {subject_name}",
        f"КЛАС: {class_name}",
        f"НАЗВА ТА ТЕМА ЗАВДАННЯ: {assignment_title}",
        f"УМОВА ТА ВИМОГИ ВЧИТЕЛЯ:\n{assignment_desc}\n",
        f"ОБРАНІ КРИТЕРІЇ ПЕРЕВІРКИ: {preset_name_display}",
    ]

    # Витягуємо вміст прикріплених вчителем файлів до завдання (щоб ШІ знав повну умову завдання)
    if assignment and assignment.files.exists():
        teacher_files_content = []
        for af in assignment.files.all():
            if af.file and os.path.exists(af.file.path):
                af_name = af.original_name or os.path.basename(af.file.name)
                af_text = get_normalized_file_content(af.file.path, af.file.name)
                if af_text:
                    teacher_files_content.append(f"• Файл завдання вчителя «{af_name}»:\n{af_text[:12000]}")
                else:
                    teacher_files_content.append(f"• Прикріплений вчителем файл «{af_name}» ({af.get_extension()})")
        if teacher_files_content:
            prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
            prompt_lines.append("ПОВНА УМОВА ТА НАВЧАЛЬНІ МАТЕРІАЛИ З ПРИКРІПЛЕНИХ ВЧИТЕЛЕМ ФАЙЛІВ:")
            prompt_lines.extend(teacher_files_content)
            prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    if active_grs and (not selected_preset or selected_preset.evaluation_type in ['nus_gr', 'nus', 'custom']):
        prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
        prompt_lines.append("ОБРАНІ ГРУПИ РЕЗУЛЬТАТІВ (ГР) ДЛЯ ОЦІНЮВАННЯ В ЦІЙ РОБОТІ:")
        for gr in active_grs:
            prompt_lines.append(f"• {gr.get('code', 'ГР')}: {gr.get('name', '')}")
        prompt_lines.append(
            "ВАЖЛИВО щодо оцінювання:\n"
            "- Оціни роботу ВИКЛЮЧНО за цими обраними групами результатів!\n"
            "- У полі 'gr_results' поверни JSON масив ТІЛЬКИ для цих обраних груп з цілими оцінками (1-12 балів).\n"
            "- Усі оцінки повинні бути ВИКЛЮЧНО цілими числами (без десятих чи дробових значень!).\n"
            "- Загальна оцінка 'suggested_grade' повинна бути середнім арифметичним балом (1-12) серед оцінених груп результатів, обов'язково заокругленим на користь/перевагу учня до більшого цілого числа!"
        )
        prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    if assignment and assignment.link_url:
        prompt_lines.append(f"Корисне посилання вчителя до уроку: {assignment.link_url}")

    # ── ПЕРЕВІРКА НА ДУБЛІКАТ ТА ПЛАГІАТ ──────────────────────────────────────
    dup_info = check_submission_duplicates(submission)
    if dup_info['is_duplicate_teacher']:
        prompt_lines.append(
            "\n🚨 КРИТИЧНЕ ЗАУВАЖЕННЯ СИСТЕМИ АНТИПЛАГІАТУ ТА ПЕРЕВІРКИ:\n"
            f"Встановлено 100% збіг: прикріплений учнем файл є точною копією вихідного файлу завдання вчителя («{dup_info['teacher_file_name']}»)! "
            "Учень НЕ виконував завдання, а просто повторно прикріпив сам файл завдання або умову від вчителя.\n"
            "КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:\n"
            "- Встанови 'suggested_grade' як 'Доопрацювати' (або 1-2 бали, якщо вимагається число).\n"
            "- У 'summary' та 'feedback_comment' прямо напиши учню: «Здано оригінальний файл завдання вчителя без виконання розв'язку. Робота не зарахована. Необхідно самостійно виконати завдання та надіслати свій результат на доопрацювання.»\n"
            "- У 'weaknesses' обов'язково зазнач: «Здано вихідний файл завдання замість виконаної роботи».\n"
        )
    elif dup_info['is_duplicate_student']:
        prompt_lines.append(
            "\n🚨 КРИТИЧНЕ ЗАУВАЖЕННЯ СИСТЕМИ АНТИПЛАГІАТУ:\n"
            f"Встановлено 100% збіг: вміст прикріпленого файлу повністю ідентичний файлу, який раніше здав інший учень ({dup_info['duplicate_student_name']})!\n"
            "КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:\n"
            "- Врахуй факт плагіату/списування чужої роботи.\n"
            "- Знизь оцінку (або признач 'Доопрацювати') та чітко зафіксуй факт плагіату у 'feedback_comment', 'summary' та 'weaknesses'.\n"
        )

    # ── КОНТЕКСТ ПЕРЕЗДАЧІ ТА РОБОТИ НАД ПОМИЛКАМИ ───────────────────────────
    if submission.is_resubmission or submission.resubmission_attempt > 1:
        prev = submission.previous_submission
        prev_info = []
        if prev:
            prev_time = prev.submitted_at.strftime('%d.%m.%Y %H:%M') if prev.submitted_at else 'раніше'
            prev_info.append(f"• Дата та час попередньої здачі: {prev_time}")
            if prev.student_ai_grade:
                prev_info.append(f"• Чернова оцінка ШІ за першу спробу: {prev.student_ai_grade} ({prev.student_ai_summary or ''})")
                if prev.student_ai_feedback:
                    prev_info.append(f"• Зауваження ШІ першої спроби: {prev.student_ai_feedback[:400]}")
            if prev.grade:
                prev_info.append(f"• Попередня оцінка вчителя: {prev.grade}")
            if prev.teacher_comment:
                prev_info.append(f"• Попередній коментар вчителя: {prev.teacher_comment}")
            if prev.comment_student:
                prev_info.append(f"• Коментар учня до попередньої спроби: {prev.comment_student}")

            p_files = [getattr(pf, 'original_name', '') for pf in prev.get_files()]
            if p_files:
                prev_info.append(f"• Файли попередньої спроби: {', '.join(p_files)}")

        prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
        prompt_lines.append(f"🔄 КОНТЕКСТ ПЕРЕЗДАЧІ (СПРОБА #{submission.resubmission_attempt} — РОБОТА НАД ПОМИЛКАМИ):")
        prompt_lines.append("Учень повторно здав роботу з метою виправлення помилок та покращення результату.")
        if prev_info:
            prompt_lines.extend(prev_info)
        prompt_lines.append(
            "\nПЕДАГОГІЧНІ ВКАЗІВКИ ДЛЯ ШІ ЩОДО РОБОТИ НАД ПОМИЛКАМИ:\n"
            "- Врахуй, що це РОБОТА НАД ПОМИЛКАМИ (учень опрацював зауваження та перездав завдання).\n"
            "- Проаналізуй, які виправлення та прогрес зробив учень у новій версії роботи.\n"
            "- У блоках 'strengths' та 'feedback_comment' обов'язково відзнач старанність та успішне виправлення помилок.\n"
            "- Оціни поточний розв'язок за фактичною якістю нового виконання."
        )
        prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    # ── ПЕРЕВІРКА НА ВИКОРИСТАННЯ ШТУЧНОГО ІНТЕЛЕКТУ (AI Content Detection) ──
    is_ai_allowed = bool(assignment and assignment.allow_ai_usage)
    prompt_lines.append("═══════════════════════════════════════════════════════════════════")
    prompt_lines.append("🤖 ПЕРЕВІРКА НА ВИКОРИСТАННЯ ШТУЧНОГО ІНТЕЛЕКТУ (AI DETECTOR):")
    prompt_lines.append(
        "Обов'язково проаналізуй поданий текст, розв'язок або код учня на предмет ознак автоматичної генерації штучним інтелектом (ChatGPT, Gemini, Claude тощо):\n"
        "1. Ознаки ШІ в тексті: характерна неприродна шаблонність відповідей мовних моделей, надмірно формальний академічний стиль для шкільного віку, однакові за розміром абзаци, характерні вступні та заключні фрази («У підсумку можна сказати...», «Цей твір розкриває...»).\n"
        "2. Ознаки ШІ в коді: шаблонні автогенеровані коментарі до кожного очевидного рядка, назви функцій/змінних у стилі ШІ-генераторів, використання конструкцій чи бібліотек, що не вивчаються у шкільній програмі.\n"
        f"3. ПОЛІТИКА ВЧИТЕЛЯ ЩОДО ШІ: {'ДОЗВОЛЕНО використання ШІ учнями' if is_ai_allowed else 'ЗАБОРОНЕНО використання ШІ учнями (вимагається самостійна робота)'}.\n"
        "- Якщо ШІ заборонено вчителем і виявлено ознаки генерації: зафіксуй це у 'weaknesses' та 'feedback_comment', а також врахуй при оцінюванні.\n"
        "- Якщо ШІ дозволено вчителем і виявлено ознаки генерації: оціни доречність та якість застосування ШІ.\n"
        "ОБОВ'ЯЗКОВО поверни в JSON поля:\n"
        "- 'ai_generated_detected': true (якщо є ознаки ШІ) або false\n"
        "- 'ai_generated_confidence': 'none' | 'low' | 'medium' | 'high'\n"
        "- 'ai_generated_details': короткий висновок українською мовою з поясненням виявлених ознак або null."
    )
    prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    prompt_lines.append(f"\nДАНІ УЧНЯ: {submission.get_student_full_name()} ({class_name})")
    prompt_lines.append("ВИКОНАНА РОБОТА УЧНЯ ДЛЯ ОЦІНЮВАННЯ:")
    prompt_lines.extend(text_parts)
    if is_traditional:
        prompt_lines.append(f"\nПроаналізуй роботу за класичною (традиційною) 12-бальною системою ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), summary, strengths (масив), weaknesses (масив), feedback_comment, ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Поле 'gr_results' поверни порожнім масивом [] або null, оскільки групи результатів НЕ використовуються в класичній системі.")
    else:
        prompt_lines.append(f"\nПроаналізуй роботу згідно з обраними критеріями ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), summary, strengths (масив), weaknesses (масив), feedback_comment, gr_results (масив об'єктів з code, name, grade, level, comment), ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Усі оцінки обов'язково мають бути цілими числами (без десятих часток), заокругленими на користь учня.")

    if custom_prompt:
        system_instruction = custom_prompt.strip()
    elif selected_preset:
        system_instruction = selected_preset.get_full_prompt().strip()
    else:
        system_instruction = (settings.system_prompt or DEFAULT_NUS_SYSTEM_PROMPT).strip()

    # Формування payload для Gemini API
    request_parts = [{"text": "\n".join(prompt_lines)}]

    for media in inline_media:
        request_parts.append({
            "inlineData": {
                "mimeType": media['mime_type'],
                "data": media['data']
            }
        })

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": request_parts
            }
        ],
        "systemInstruction": {
            "parts": [{"text": system_instruction}]
        },
        "generationConfig": {
            "temperature": float(settings.temperature or 0.2),
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024
        }
    }

    # Отримуємо ланцюжок моделей з пріоритетами
    models_chain = settings.get_active_fallback_chain() if hasattr(settings, 'get_active_fallback_chain') else [settings.model_name]
    models_to_try = [clean_model_name(m) for m in models_chain if m]
    if not models_to_try:
        models_to_try = [clean_model_name(settings.model_name or 'gemini-2.5-flash')]

    attempted_errors = []

    for model_idx, model_name in enumerate(models_to_try):
        endpoint = f"{GEMINI_API_BASE_URL}/{model_name}:generateContent?key={api_key}"
        fallback_happened = (model_idx > 0)
        max_retries = 1 if len(models_to_try) > 1 else 2

        for attempt in range(max_retries + 1):
            try:
                status_code, data, text = _http_post_json(endpoint, payload, timeout=35)

                if status_code == 429 and attempt < max_retries:
                    time.sleep(2)
                    continue

                if status_code != 200 or not data:
                    err_data = data or {}
                    err_msg = err_data.get('error', {}).get('message', f"HTTP {status_code}: {text[:200]}")
                    if status_code == 429:
                        err_msg = f"Перевищено ліміт запитів для {model_name} (429 Rate Limit)."
                    elif status_code == 403:
                        err_msg = f"Недійсний Google Gemini API Key для {model_name} (403)."

                    attempted_errors.append(f"[{model_name}]: {err_msg}")
                    break  # Переходимо до наступної пріоритетної моделі

                raw_text = data['candidates'][0]['content']['parts'][0]['text'].strip()
                result_json = extract_json_from_text(raw_text)

                if result_json:
                    suggested_grade = str(result_json.get('suggested_grade', '')).strip()
                    if suggested_grade.lower() in ['none', 'null', '']:
                        suggested_grade = 'Доопрацювати' if 'доопрацю' in raw_text.lower() else '7'

                    level = str(result_json.get('level', '')).strip()
                    format_warning = str(result_json.get('format_warning') or '').strip()
                    if format_warning.lower() in ['none', 'null', 'false', 'ok', 'none.', 'null.']:
                        format_warning = ''

                    feedback_comment = str(result_json.get('feedback_comment', '')).strip()
                    summary = str(result_json.get('summary', '')).strip()
                    strengths = result_json.get('strengths', [])
                    weaknesses = result_json.get('weaknesses', [])
                    raw_gr_results = result_json.get('gr_results', [])

                    if not isinstance(weaknesses, list):
                        weaknesses = [str(weaknesses)] if weaknesses else []
                    if not isinstance(strengths, list):
                        strengths = [str(strengths)] if strengths else []

                    # Обробка та валідація результатів за групами результатів (ГР НУШ)
                    clean_gr_results = []
                    numeric_gr_grades = []
                    if not is_traditional and isinstance(raw_gr_results, list):
                        for idx, item in enumerate(raw_gr_results, 1):
                            if isinstance(item, dict):
                                code = str(item.get('code', '')).strip() or f"ГР {idx}"
                                name = str(item.get('name', '')).strip() or f"Група результатів {idx}"
                                grade = str(item.get('grade', '')).strip().replace(',', '.')
                                gr_level = str(item.get('level', '')).strip()
                                comment = str(item.get('comment', '')).strip()

                                # Якщо вчитель обрав конкретні ГР, фільтруємо зайві
                                if selected_gr_codes:
                                    selected_set = {str(c).strip().lower() for c in selected_gr_codes if str(c).strip()}
                                    code_lower = code.lower()
                                    name_lower = name.lower()
                                    if not (code_lower in selected_set or any(s in code_lower or s in name_lower for s in selected_set)):
                                        continue

                                # Переконуємось, що бал ГР є цілим числом
                                try:
                                    g_num = float(grade)
                                    g_int = int(min(12, max(1, math.ceil(g_num))))
                                    grade = str(g_int)
                                    numeric_gr_grades.append(float(g_int))
                                except (ValueError, TypeError):
                                    pass

                                clean_gr_results.append({
                                    'code': code,
                                    'name': name,
                                    'grade': grade,
                                    'level': gr_level,
                                    'comment': comment
                                })

                    # Обчислюємо середній бал за оціненими групами результатів (заокруглення на перевагу учню, тільки ціле число)
                    avg_gr_grade = None
                    if not is_traditional and numeric_gr_grades:
                        avg_val = sum(numeric_gr_grades) / len(numeric_gr_grades)
                        avg_gr_grade = int(min(12, max(1, math.ceil(avg_val))))
                        # Якщо оцінювалось декілька ГР одночасно — середня оцінка стає рекомендованою
                        if suggested_grade != 'Доопрацювати':
                            suggested_grade = str(avg_gr_grade)
                    elif suggested_grade and suggested_grade != 'Доопрацювати':
                        try:
                            s_num = float(str(suggested_grade).replace(',', '.'))
                            suggested_grade = str(int(min(12, max(1, math.ceil(s_num)))))
                        except (ValueError, TypeError):
                            pass

                    # Захист: якщо ШІ помилково помістив змістовне зауваження (не про технічний тип/розширення файлу)
                    # у поле format_warning, переносимо його до списку зауважень (weaknesses)
                    if format_warning:
                        fw_lower = format_warning.lower()
                        technical_keywords = [
                            'розширен', 'формат', 'розширення', '.py', '.doc', '.docx', '.pdf',
                            '.xls', '.xlsx', '.cpp', '.html', '.js', '.txt', '.png', '.jpg',
                            '.zip', 'розширенням', 'контейнер', 'тип файл', 'типу файл',
                            'не має розширення', 'без розширення', 'некоректне розширення'
                        ]
                        is_technical = any(k in fw_lower for k in technical_keywords)
                        has_content_keywords = any(k in fw_lower for k in ['замість', 'людин', 'не та тема', 'не той об\'єкт', 'не відповідає темі', 'інший малюнок', 'інше фото'])

                        if not is_technical or (has_content_keywords and not any(ext in fw_lower for ext in ['.py', '.doc', '.xlsx', '.txt', 'розширен'])):
                            if format_warning not in weaknesses:
                                weaknesses.append(format_warning)
                            format_warning = ''

                    if not format_warning and submission.file and os.path.exists(submission.file.path):
                        f_ext = os.path.splitext(submission.file.path)[1].lower()
                        if not f_ext:
                            format_warning = "Файл прикріплено без розширення (для належної здачі файл потрібно зберігати з відповідним розширенням, наприклад .py для коду)."

                    full_feedback_parts = []
                    if format_warning:
                        full_feedback_parts.append(f"⚠️ **Зауваження до формату файлу (вплинуло на оцінку):**\n{format_warning}")
                    if summary:
                        full_feedback_parts.append(f"📌 **Висновок:** {summary}")

                    if clean_gr_results and not is_traditional:
                        gr_lines = []
                        for gr in clean_gr_results:
                            g_val = gr.get('grade') or '—'
                            l_val = f" ({gr.get('level')})" if gr.get('level') else ""
                            c_val = f": {gr.get('comment')}" if gr.get('comment') else ""
                            gr_lines.append(f"• **{gr.get('code')}: {gr.get('name')}** → **{g_val} б.**{l_val}{c_val}")
                        if avg_gr_grade is not None and len(clean_gr_results) > 1:
                            gr_lines.append(f"\n📊 **Середній бал за ГР (Оцінка по ГР):** **{avg_gr_grade} б.**")
                        full_feedback_parts.append("📊 **Оцінювання за групами результатів (ГР НУШ):**\n" + "\n".join(gr_lines))

                    if strengths and isinstance(strengths, list) and len(strengths) > 0:
                        full_feedback_parts.append("✅ **Сильні сторони:**\n" + "\n".join(f"• {s}" for s in strengths))
                    if weaknesses and isinstance(weaknesses, list) and len(weaknesses) > 0:
                        full_feedback_parts.append("💡 **Зауваження та неточності:**\n" + "\n".join(f"• {w}" for w in weaknesses))
                    if feedback_comment:
                        full_feedback_parts.append(f"💬 **Рекомендація учню:**\n{feedback_comment}")

                    combined_feedback = "\n\n".join(full_feedback_parts) if full_feedback_parts else feedback_comment

                    # Формуємо чистий відгук для публічних коментарів учневі (БЕЗ оцінок ГР)
                    student_feedback_parts = []
                    if format_warning:
                        student_feedback_parts.append(f"⚠️ **Зауваження до формату:** {format_warning}")
                    if summary:
                        student_feedback_parts.append(f"📌 {summary}")
                    if strengths and isinstance(strengths, list) and len(strengths) > 0:
                        student_feedback_parts.append("✅ **Сильні сторони:**\n" + "\n".join(f"• {s}" for s in strengths))
                    if weaknesses and isinstance(weaknesses, list) and len(weaknesses) > 0:
                        student_feedback_parts.append("💡 **Зауваження:**\n" + "\n".join(f"• {w}" for w in weaknesses))
                    if feedback_comment:
                        student_feedback_parts.append(f"💬 {feedback_comment}")
                    clean_student_feedback = "\n\n".join(student_feedback_parts) if student_feedback_parts else feedback_comment

                    # Виявлення використання ШІ у роботі
                    raw_ai_detected = result_json.get('ai_generated_detected')
                    ai_generated_detected = bool(raw_ai_detected and raw_ai_detected not in ['false', 'False', 0, '0', 'none', 'null'])
                    ai_generated_confidence = str(result_json.get('ai_generated_confidence') or 'none').lower().strip()
                    if ai_generated_confidence not in ['none', 'low', 'medium', 'high']:
                        ai_generated_confidence = 'medium' if ai_generated_detected else 'none'
                    if not ai_generated_detected and ai_generated_confidence != 'none':
                        ai_generated_confidence = 'none'

                    ai_generated_details = str(result_json.get('ai_generated_details') or '').strip()
                    if ai_generated_details.lower() in ['none', 'null', 'false', 'ok', 'none.', 'null.']:
                        ai_generated_details = ''

                    submission.ai_suggested_grade = suggested_grade
                    submission.ai_score_level = level
                    submission.ai_feedback = combined_feedback
                    submission.ai_gr_results = json.dumps(clean_gr_results, ensure_ascii=False) if (clean_gr_results and not is_traditional) else ''
                    submission.ai_generated_detected = ai_generated_detected
                    submission.ai_generated_confidence = ai_generated_confidence
                    submission.ai_generated_details = ai_generated_details
                    submission.ai_model_used = model_name
                    submission.ai_status = 'success'
                    submission.ai_error_reason = ''
                    submission.ai_reviewed_at = timezone.now()
                    submission.save(update_fields=[
                        'ai_suggested_grade', 'ai_score_level', 'ai_feedback', 'ai_gr_results',
                        'ai_generated_detected', 'ai_generated_confidence', 'ai_generated_details',
                        'ai_model_used', 'ai_status', 'ai_error_reason', 'ai_reviewed_at'
                    ])

                    return {
                        'status': 'success',
                        'suggested_grade': suggested_grade,
                        'level': level,
                        'format_warning': format_warning,
                        'is_traditional': is_traditional,
                        'gr_results': [] if is_traditional else clean_gr_results,
                        'gr_avg': None if is_traditional else avg_gr_grade,
                        'ai_generated_detected': ai_generated_detected,
                        'ai_generated_confidence': ai_generated_confidence,
                        'ai_generated_details': ai_generated_details,
                        'feedback': combined_feedback,
                        'clean_feedback': clean_student_feedback,
                        'raw_json': result_json,
                        'model_used': model_name,
                        'fallback_activated': fallback_happened
                    }
                else:
                    grade_match = re.search(r'((?:1[0-2]|[1-9])|Доопрацювати)', raw_text, re.IGNORECASE)
                    suggested_grade = grade_match.group(1) if grade_match else 'Доопрацювати'

                    submission.ai_suggested_grade = suggested_grade
                    submission.ai_feedback = raw_text
                    submission.ai_model_used = model_name
                    submission.ai_status = 'success'
                    submission.ai_error_reason = ''
                    submission.ai_reviewed_at = timezone.now()
                    submission.save(update_fields=[
                        'ai_suggested_grade', 'ai_feedback', 'ai_model_used', 'ai_status', 'ai_error_reason', 'ai_reviewed_at'
                    ])
                    return {'status': 'success', 'feedback': raw_text, 'suggested_grade': suggested_grade, 'model_used': model_name}

            except Exception as e:
                attempted_errors.append(f"[{model_name} виняток]: {str(e)}")
                break

    # Якщо всі моделі в черзі зазнали невдачі
    all_err_msg = " | ".join(attempted_errors) if attempted_errors else "Не вдалося отримати відповідь від жодної з налаштованих моделей ШІ."
    submission.ai_status = 'failed'
    submission.ai_error_reason = all_err_msg
    submission.save(update_fields=['ai_status', 'ai_error_reason'])
    return {'status': 'failed', 'error': all_err_msg}
