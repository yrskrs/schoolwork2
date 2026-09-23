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
import subprocess
import tempfile
import glob
import shutil
from django.utils import timezone
from .models import AISettings, Submission, DEFAULT_NUS_SYSTEM_PROMPT, AICriteriaPreset
from .duplicate_detector import check_submission_duplicates, get_normalized_file_content

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"
GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_MODELS_BY_PROVIDER = {
    'gemini': ['gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.1-flash-lite', 'gemini-3-flash-preview', 'gemini-3.1-pro-preview'],
    'openai': ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'o3-mini'],
    'deepseek': ['deepseek-chat', 'deepseek-reasoner'],
    'groq': ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
    'openrouter': ['google/gemini-2.5-flash', 'deepseek/deepseek-chat', 'openai/gpt-4o-mini', 'anthropic/claude-3.5-sonnet'],
    'custom': ['llama3.2', 'mistral', 'qwen2.5'],
}


def get_default_model_for_provider(provider):
    """Повертає рекомендовану модель за замовчуванням для обраного провайдера."""
    prov = (provider or 'gemini').lower().strip()
    models = DEFAULT_MODELS_BY_PROVIDER.get(prov, [])
    return models[0] if models else 'gemini-3.8-flash'


def get_ai_settings():
    """Повертає глобальні налаштування ШІ."""
    return AISettings.get_solo()


def _http_post_json(url, payload_dict, headers=None, timeout=30):
    """
    Виконує HTTP POST запит із JSON тілом через вбудований urllib з підтримкою кастомних заголовків.
    Повертає (status_code: int, response_data: dict | None, response_text: str).
    """
    json_bytes = json.dumps(payload_dict).encode('utf-8')
    req_headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json'
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(
        url,
        data=json_bytes,
        headers=req_headers,
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


def clean_model_name(name, provider='gemini'):
    """Очищує та нормалізує назву моделі."""
    if not name:
        return get_default_model_for_provider(provider)
    name = name.strip()
    if (provider or 'gemini').lower() == 'gemini':
        if name.startswith('models/'):
            name = name[7:]
        # Автоматичне перенаправлення застарілих / вимкнених Google моделей на актуальні
        legacy_flash = [
            'gemini-1.5-flash', 'gemini-1.5-flash-latest', 'gemini-1.5-flash-8b',
            'gemini-1.5-pro', 'gemini-1.5-pro-latest',
            'gemini-2.0-flash', 'gemini-2.0-flash-exp', 'gemini-2.0-flash-001',
            'gemini-2.5-flash', 'gemini-2.0-pro', 'gemini-2.0-pro-exp-02-05',
            # Моделі з проблемами квоти/доступу — перенаправляємо на актуальну
            'gemini-3.5-flash', 'gemini-3.6-flash', 'gemini-flash-latest',
        ]
        if name in legacy_flash:
            return 'gemini-3.8-flash'
        if name in ['gemini-2.5-pro', 'gemini-pro-latest']:
            return 'gemini-3.1-pro-preview'
        if name in ['gemini-2.0-flash-lite', 'gemini-2.5-flash-lite', 'gemini-3.1-flash-lite-preview']:
            return 'gemini-3.1-flash-lite'
    return name


def get_provider_endpoint(provider, model_name=None, api_key=None, custom_url=None):
    """
    Повертає (url: str, headers: dict, actual_model: str) для обраного ШІ-провайдера.
    """
    headers = {}
    provider = (provider or 'gemini').lower().strip()
    model = clean_model_name(model_name or get_default_model_for_provider(provider), provider=provider)

    if provider == 'gemini':
        url = f"{GEMINI_API_BASE_URL}/{model}:generateContent?key={api_key}"
        return url, headers, model

    # OpenAI-сумісні провайдери
    if api_key:
        headers['Authorization'] = f"Bearer {api_key.strip()}"

    if provider == 'openai':
        url = f"{OPENAI_API_BASE_URL}/chat/completions"
    elif provider == 'deepseek':
        url = f"{DEEPSEEK_API_BASE_URL}/chat/completions"
    elif provider == 'groq':
        url = f"{GROQ_API_BASE_URL}/chat/completions"
    elif provider == 'openrouter':
        url = f"{OPENROUTER_API_BASE_URL}/chat/completions"
        headers['HTTP-Referer'] = 'https://schoolnet.local'
        headers['X-Title'] = 'SchoolNet Education AI'
    elif provider == 'custom':
        base = (custom_url or 'http://localhost:11434/v1').strip().rstrip('/')
        if not base.endswith('/chat/completions'):
            url = f"{base}/chat/completions"
        else:
            url = base
    else:
        url = f"{GEMINI_API_BASE_URL}/{model}:generateContent?key={api_key}"

    return url, headers, model


def call_ai_api(prompt_text, system_prompt="", inline_media=None, provider="gemini", api_key="", model_name="", custom_url="", temperature=0.2, max_output_tokens=3500, timeout=35, json_mode=False, thinking_budget=None):
    """
    Універсальна функція для звернення до будь-якого ШІ-провайдера
    (Google Gemini, OpenAI, DeepSeek, Groq, OpenRouter, Custom/Ollama).
    Повертає (status_code: int, response_text: str | None, error_message: str | None, raw_data: dict | None).
    """
    provider = (provider or 'gemini').lower().strip()
    url, headers, model = get_provider_endpoint(provider, model_name=model_name, api_key=api_key, custom_url=custom_url)

    if provider == 'gemini':
        parts = []
        if prompt_text:
            parts.append({"text": prompt_text})
        if inline_media:
            for item in inline_media:
                parts.append({
                    "inlineData": {
                        "mimeType": item["mime_type"],
                        "data": item["data"]
                    }
                })

        generation_config = {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens
        }
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts
                }
            ],
            "generationConfig": generation_config
        }
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        try:
            status_code, data, text = _http_post_json(url, payload, headers=headers, timeout=timeout)

            # Якщо модель не підтримує thinkingConfig (400), повторюємо без нього
            if status_code == 400 and thinking_budget is not None and ('thinkingConfig' in text or 'thinking' in text):
                del payload["generationConfig"]["thinkingConfig"]
                status_code, data, text = _http_post_json(url, payload, headers=headers, timeout=timeout)

            if status_code == 200 and data:
                candidates = data.get('candidates', [])
                if candidates:
                    cand = candidates[0]
                    c_parts = cand.get('content', {}).get('parts', [])
                    if c_parts:
                        # Фільтруємо частини роздумів ШІ (thinking)
                        text_parts = [p.get('text', '') for p in c_parts if not p.get('thought')]
                        if not text_parts:
                            text_parts = [p.get('text', '') for p in c_parts]
                        raw_reply = "\n".join([t for t in text_parts if t]).strip()
                        if raw_reply:
                            return status_code, raw_reply, None, data

                    finish_reason = cand.get('finishReason', '')
                    if finish_reason == 'MAX_TOKENS':
                        return status_code, "", "Модель досягла ліміту токенів (MAX_TOKENS) під час формування відповіді. Збільште ліміт вихідних токенів.", data
                    elif finish_reason == 'SAFETY':
                        return status_code, "", "Відповідь заблоковано фільтром безпеки Gemini (SAFETY)", data
                return status_code, "", "Порожня відповідь від Gemini", data
            else:
                err_data = data or {}
                err_msg = err_data.get('error', {}).get('message', text[:300])
                return status_code, None, err_msg, data
        except Exception as e:
            return 0, None, str(e), None

    else:
        # OpenAI Chat Completions формат
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        user_content = []
        if prompt_text:
            user_content.append({"type": "text", "text": prompt_text})

        if inline_media:
            for item in inline_media:
                user_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{item['mime_type']};base64,{item['data']}"
                    }
                })

        # Якщо немає зображень, передаємо простий рядок для максимальної сумісності
        if not inline_media:
            messages.append({"role": "user", "content": prompt_text})
        else:
            messages.append({"role": "user", "content": user_content})

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }

        # Спроба з response_format, якщо ввімкнено json_mode
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            status_code, data, text = _http_post_json(url, payload, headers=headers, timeout=timeout)

            # Якщо endpoint не підтримує response_format (400), пробуємо повторити без нього
            if status_code == 400 and json_mode and ('response_format' in text or 'json_object' in text):
                del payload["response_format"]
                status_code, data, text = _http_post_json(url, payload, headers=headers, timeout=timeout)

            if status_code == 200 and data:
                choices = data.get('choices', [])
                if choices:
                    msg = choices[0].get('message', {})
                    raw_reply = msg.get('content', '') or msg.get('reasoning_content', '')
                    raw_reply = raw_reply.strip()
                    return status_code, raw_reply, None, data
                return status_code, "", "Порожня відповідь від моделі", data
            else:
                err_data = data or {}
                err_msg = err_data.get('error', {}).get('message', text[:300])
                return status_code, None, err_msg, data
        except Exception as e:
            return 0, None, str(e), None


def test_ai_connection(provider=None, api_key=None, model_name=None, custom_url=None):
    """
    Перевіряє коректність API ключа та доступність вибраної моделі для вказаного або активного провайдера.
    Повертає (success: bool, message: str, model_used: str, provider_used: str).
    """
    settings = get_ai_settings()
    act_provider, act_key, act_model, act_url, _ = settings.get_active_config()

    prov = (provider or act_provider or 'gemini').lower().strip()
    key = api_key.strip() if api_key is not None else act_key
    raw_model = (model_name or act_model or get_default_model_for_provider(prov)).strip()
    model = clean_model_name(raw_model, provider=prov)
    url = custom_url.strip() if custom_url is not None else act_url

    if prov != 'custom' and not key:
        return False, f"API Key для {prov.title()} не вказано.", model, prov

    test_prompt = "Тест з'єднання. Напиши коротку відповідь: 'З'єднання зі SchoolNet AI успішне!'"
    status_code, reply_text, err_msg, _ = call_ai_api(
        prompt_text=test_prompt,
        provider=prov,
        api_key=key,
        model_name=model,
        custom_url=url,
        temperature=0.1,
        max_output_tokens=1000,
        timeout=15,
        thinking_budget=0
    )

    if status_code == 200 and reply_text:
        return True, f"Успішно підключено! Відповідь моделі ({model}): {reply_text}", model, prov
    elif status_code in (401, 403):
        return False, f"Помилка автентифікації ({status_code}): Недійсний API Key або відсутній доступ до моделі {model}.", model, prov
    elif status_code == 429:
        return False, f"Перевищено ліміт запитів (429 Rate Limit) для {model}. Рекомендується використати резервний API.", model, prov
    elif status_code == 404:
        return False, f"Модель {model} не знайдена в API ({status_code}): {err_msg}", model, prov
    elif status_code == 400:
        return False, f"Помилка запиту API (400): {err_msg}", model, prov
    else:
        msg = err_msg or f"HTTP {status_code}"
        return False, f"Помилка підключення до {prov.title()} ({model}): {msg}", model, prov


def test_gemini_connection(api_key=None, model_name=None):
    """
    Перевіряє коректність API ключа та доступність вибраної моделі Google Gemini.
    (Збережено для зворотної сумісності).
    """
    success, message, model_used, _ = test_ai_connection(provider='gemini', api_key=api_key, model_name=model_name)
    return success, message, model_used


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


def extract_images_from_docx(file_path, max_images=4, max_bytes_per_img=8 * 1024 * 1024):
    """
    Видобуває вбудовані зображення (скриншоти, фотографії розв'язків тощо)
    із документа Word (.docx), які зберігаються у zip-папці word/media/.
    Повертає список словників: [{'name': filename, 'mime_type': mime, 'data': base64_str, 'size_kb': float}].
    """
    extracted = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            media_files = [f for f in z.namelist() if f.startswith('word/media/')]
            img_exts = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.gif': 'image/gif'
            }
            media_files.sort()
            for mf in media_files:
                ext = os.path.splitext(mf)[1].lower()
                if ext in img_exts:
                    info = z.getinfo(mf)
                    if 0 < info.file_size <= max_bytes_per_img:
                        data = z.read(mf)
                        b64 = base64.b64encode(data).decode('utf-8')
                        extracted.append({
                            'name': os.path.basename(mf),
                            'mime_type': img_exts[ext],
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                        if len(extracted) >= max_images:
                            break
    except Exception:
        pass
    return extracted


def extract_images_from_odt(file_path, max_images=3, max_bytes_per_img=8 * 1024 * 1024):
    """
    Видобуває вбудовані зображення з файлу OpenDocument (.odt), що зберігаються в папці Pictures/.
    """
    extracted = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            media_files = [f for f in z.namelist() if f.startswith('Pictures/')]
            img_exts = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.gif': 'image/gif'
            }
            media_files.sort()
            for mf in media_files:
                ext = os.path.splitext(mf)[1].lower()
                if ext in img_exts:
                    info = z.getinfo(mf)
                    if 0 < info.file_size <= max_bytes_per_img:
                        data = z.read(mf)
                        b64 = base64.b64encode(data).decode('utf-8')
                        extracted.append({
                            'name': os.path.basename(mf),
                            'mime_type': img_exts[ext],
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                        if len(extracted) >= max_images:
                            break
    except Exception:
        pass
    return extracted


def extract_images_from_pptx(file_path, max_images=6, max_bytes_per_img=8 * 1024 * 1024):
    """
    Видобуває вбудовані зображення (слайди, схеми, фотографії, графіки)
    із презентації PowerPoint (.pptx), які зберігаються у zip-папці ppt/media/.
    Повертає список словників: [{'name': filename, 'mime_type': mime, 'data': base64_str, 'size_kb': float}].
    """
    extracted = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            media_files = [f for f in z.namelist() if f.startswith('ppt/media/')]
            img_exts = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.gif': 'image/gif',
                '.svg': 'image/svg+xml',
            }
            media_files.sort()
            for mf in media_files:
                ext = os.path.splitext(mf)[1].lower()
                if ext in img_exts:
                    info = z.getinfo(mf)
                    if 0 < info.file_size <= max_bytes_per_img:
                        data = z.read(mf)
                        b64 = base64.b64encode(data).decode('utf-8')
                        extracted.append({
                            'name': os.path.basename(mf),
                            'mime_type': img_exts[ext],
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                        if len(extracted) >= max_images:
                            break
    except Exception:
        pass
    return extracted


def col_num_to_letter(col_idx):
    """Конвертує 0-індексований номер стовпця Excel у літерне позначення (0 -> A, 1 -> B, 26 -> AA тощо)."""
    letters = ''
    col_idx += 1
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def parse_excel_charts(file_path):
    """
    Аналізує структуру OpenXML книги Excel (.xlsx, .xlsm) та витягує повні метадані
    про всі створені учнем вбудовані діаграми, графіки та візуалізації.
    Повертає список словників з детальним описом кожної діаграми.
    """
    charts_info = []
    if not file_path or not os.path.exists(file_path):
        return charts_info

    try:
        if not zipfile.is_zipfile(file_path):
            return charts_info

        with zipfile.ZipFile(file_path, 'r') as z:
            names = set(z.namelist())

            # 1. Збираємо мапу аркушів (sheet file -> sheet name)
            sheet_name_map = {}
            if 'xl/workbook.xml' in names and 'xl/_rels/workbook.xml.rels' in names:
                try:
                    wb_root = ET.fromstring(z.read('xl/workbook.xml'))
                    rels_root = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))

                    wb_ns = {
                        'w': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                        'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
                    }
                    pkg_ns = {'p': 'http://schemas.openxmlformats.org/package/2006/relationships'}

                    rid_to_target = {}
                    for rel in rels_root.findall('./p:Relationship', pkg_ns):
                        rid = rel.get('Id')
                        target = rel.get('Target', '').lstrip('/')
                        if not target.startswith('xl/'):
                            target = 'xl/' + target
                        rid_to_target[rid] = target

                    for sheet in wb_root.findall('.//w:sheet', wb_ns):
                        s_name = sheet.get('name')
                        rid = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                        if rid and rid in rid_to_target:
                            sheet_name_map[rid_to_target[rid]] = s_name
                except Exception:
                    pass

            # 2. Мапа зв'язків аркушів з кресленнями (drawing file -> sheet_name)
            drawing_to_sheet = {}
            chart_locations = {}

            for name in names:
                if name.startswith('xl/worksheets/_rels/') and name.endswith('.xml.rels'):
                    base_xml = name.replace('xl/worksheets/_rels/', 'xl/worksheets/').replace('.rels', '')
                    sheet_name = sheet_name_map.get(base_xml, 'Аркуш')
                    try:
                        rels_root = ET.fromstring(z.read(name))
                        for rel in rels_root.findall('.//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
                            target = rel.get('Target', '').lstrip('/')
                            if 'drawings/' in target:
                                if not target.startswith('xl/'):
                                    target = 'xl/' + target.split('xl/')[-1] if 'xl/' in target else 'xl/drawings/' + target.split('/')[-1]
                                drawing_to_sheet[target] = sheet_name
                    except Exception:
                        pass

            # 3. Аналізуємо зв'язки креслень з діаграмами (xl/drawings/_rels/)
            drawing_rid_to_chart = {}
            for name in names:
                if name.startswith('xl/drawings/_rels/') and name.endswith('.xml.rels'):
                    dr_xml = name.replace('xl/drawings/_rels/', 'xl/drawings/').replace('.rels', '')
                    sheet_name = drawing_to_sheet.get(dr_xml, 'Таблиця')
                    try:
                        rels_root = ET.fromstring(z.read(name))
                        for rel in rels_root.findall('.//{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'):
                            target = rel.get('Target', '').lstrip('/')
                            rid = rel.get('Id')
                            if 'charts/' in target:
                                chart_target = 'xl/charts/' + target.split('/')[-1]
                                drawing_rid_to_chart[(dr_xml, rid)] = (chart_target, sheet_name)
                    except Exception:
                        pass

            # 4. Аналізуємо координати комірок (anchor) у файлах креслень
            for name in names:
                if name.startswith('xl/drawings/drawing') and name.endswith('.xml'):
                    sheet_name = drawing_to_sheet.get(name, 'Таблиця')
                    try:
                        dr_root = ET.fromstring(z.read(name))
                        for anchor_elem in dr_root.findall('./*'):
                            col_elem = anchor_elem.find('.//{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}col')
                            row_elem = anchor_elem.find('.//{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}row')
                            chart_elem = anchor_elem.find('.//{http://schemas.openxmlformats.org/drawingml/2006/chart}chart')

                            anchor_str = ''
                            if col_elem is not None and row_elem is not None and col_elem.text and row_elem.text:
                                try:
                                    c_idx = int(col_elem.text)
                                    r_idx = int(row_elem.text) + 1
                                    anchor_str = f'{col_num_to_letter(c_idx)}{r_idx}'
                                except Exception:
                                    pass

                            if chart_elem is not None:
                                rid = chart_elem.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                                if rid and (name, rid) in drawing_rid_to_chart:
                                    ch_path, s_name = drawing_rid_to_chart[(name, rid)]
                                    chart_locations[ch_path] = {'sheet_name': s_name, 'anchor': anchor_str}
                    except Exception:
                        pass

            # 5. Парсимо самі файли діаграм xl/charts/chart*.xml
            chart_files = sorted([f for f in names if f.startswith('xl/charts/chart') and f.endswith('.xml')])
            if not chart_files:
                return charts_info

            chart_type_map = {
                'barChart': 'Стовпчаста / лінійчата діаграма (Bar/Column chart)',
                'bar3DChart': 'Об\'ємна стовпчаста діаграма (3D Bar/Column chart)',
                'lineChart': 'Лінійний графік (Line chart)',
                'line3DChart': 'Об\'ємний графік (3D Line chart)',
                'pieChart': 'Кругова секторна діаграма (Pie chart)',
                'pie3DChart': 'Об\'ємна кругова діаграма (3D Pie chart)',
                'doughnutChart': 'Кільцева діаграма (Doughnut chart)',
                'areaChart': 'Діаграма з областями (Area chart)',
                'area3DChart': 'Об\'ємна діаграма з областями (3D Area chart)',
                'scatterChart': 'Точкова діаграма / графік розсіювання (Scatter plot)',
                'radarChart': 'Пелюсткова / радіальна діаграма (Radar chart)',
                'bubbleChart': 'Бульбашкова діаграма (Bubble chart)',
                'stockChart': 'Біржова діаграма (Stock chart)',
                'surfaceChart': 'Поверхнева діаграма (Surface chart)',
                'surface3DChart': 'Об\'ємна поверхнева діаграма (3D Surface chart)'
            }

            ns = {
                'c': 'http://schemas.openxmlformats.org/drawingml/2006/chart',
                'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'
            }

            for idx, cf in enumerate(chart_files, 1):
                try:
                    xml_data = z.read(cf)
                    root = ET.fromstring(xml_data)
                except Exception:
                    continue

                # Заголовок діаграми
                title = ''
                title_elem = root.find('.//c:chart/c:title', ns)
                if title_elem is not None:
                    texts = [t.text for t in title_elem.findall('.//a:t', ns) if t.text]
                    if texts:
                        title = ''.join(texts).strip()
                    else:
                        v_elem = title_elem.find('.//c:v', ns)
                        if v_elem is not None and v_elem.text:
                            title = v_elem.text.strip()
                        else:
                            f_elem = title_elem.find('.//c:f', ns)
                            if f_elem is not None and f_elem.text:
                                title = f'Посилання: {f_elem.text.strip()}'

                # PlotArea
                plot_area = root.find('.//c:chart/c:plotArea', ns)
                found_types = []
                series_info = []
                axis_titles = []

                if plot_area is not None:
                    for child in plot_area:
                        tag_name = child.tag.split('}')[-1]
                        if tag_name in chart_type_map:
                            desc = chart_type_map[tag_name]
                            if tag_name == 'barChart':
                                bar_dir = child.find('./c:barDir', ns)
                                if bar_dir is not None:
                                    val = bar_dir.get('val')
                                    if val == 'col':
                                        desc = 'Вертикальна стовпчаста діаграма / гістограма (Column chart)'
                                    elif val == 'bar':
                                        desc = 'Горизонтальна лінійчата діаграма (Bar chart)'
                            found_types.append(desc)

                            # Серії (ряди даних)
                            for ser in child.findall('./c:ser', ns):
                                s_name = ''
                                tx = ser.find('./c:tx', ns)
                                if tx is not None:
                                    s_texts = [t.text for t in tx.findall('.//a:t', ns) if t.text]
                                    if s_texts:
                                        s_name = ''.join(s_texts).strip()
                                    else:
                                        v = tx.find('.//c:v', ns)
                                        if v is not None and v.text:
                                            s_name = v.text.strip()
                                        else:
                                            f = tx.find('.//c:f', ns)
                                            if f is not None and f.text:
                                                s_name = f.text.strip()

                                cat_ref = ''
                                cat_f = ser.find('.//c:cat//c:f', ns)
                                if cat_f is not None and cat_f.text:
                                    cat_ref = cat_f.text.strip()

                                val_ref = ''
                                val_f = ser.find('.//c:val//c:f', ns)
                                if val_f is not None and val_f.text:
                                    val_ref = val_f.text.strip()

                                s_parts = []
                                if s_name:
                                    s_parts.append(f'Серія: \"{s_name}\"')
                                if cat_ref:
                                    s_parts.append(f'Категорії (X): {cat_ref}')
                                if val_ref:
                                    s_parts.append(f'Значення (Y): {val_ref}')
                                if s_parts:
                                    series_info.append(' | '.join(s_parts))

                    # Осі
                    for ax_tag in ['./c:catAx', './c:valAx', './c:dateAx', './c:serAx']:
                        for ax in plot_area.findall(ax_tag, ns):
                            ax_t = ax.find('./c:title', ns)
                            if ax_t is not None:
                                ax_texts = [t.text for t in ax_t.findall('.//a:t', ns) if t.text]
                                if ax_texts:
                                    ax_str = ''.join(ax_texts).strip()
                                    if ax_str and ax_str not in axis_titles:
                                        axis_titles.append(ax_str)

                # Легенда
                has_legend = root.find('.//c:chart/c:legend', ns) is not None
                legend_pos_str = ''
                if has_legend:
                    leg_elem = root.find('.//c:chart/c:legend/c:legendPos', ns)
                    leg_val = leg_elem.get('val') if leg_elem is not None else 'r'
                    pos_dict = {'r': 'праворуч', 'l': 'ліворуч', 't': 'вгорі', 'b': 'знизу', 'tr': 'вгорі праворуч'}
                    legend_pos_str = pos_dict.get(leg_val, 'налаштована')

                loc = chart_locations.get(cf, {})
                s_name = loc.get('sheet_name') or 'Таблиця'
                anchor = loc.get('anchor')

                chart_type_str = ', '.join(dict.fromkeys(found_types)) if found_types else 'Вбудована діаграма'

                charts_info.append({
                    'index': idx,
                    'file': cf,
                    'sheet_name': s_name,
                    'anchor': anchor,
                    'type': chart_type_str,
                    'title': title or '(без назви)',
                    'axis_titles': axis_titles,
                    'series': series_info,
                    'has_legend': has_legend,
                    'legend_position': legend_pos_str
                })
    except Exception:
        pass

    return charts_info


def format_excel_charts_summary(charts_info):
    """
    Форматує структурований інформаційний опис виявлених у файлі діаграм для промпту ШІ.
    """
    if not charts_info:
        return ""

    lines = [
        "════════════════════════════════════════════════════════════════════",
        f"📊 ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ФАЙЛІ EXCEL ({len(charts_info)} шт.):",
        "ШІ повинен обов'язково врахувати наявність та параметри цих діаграм при оцінюванні!"
    ]
    for c in charts_info:
        loc_str = f"на аркуші «{c['sheet_name']}»"
        if c.get('anchor'):
            loc_str += f" (розташована біля клітинки {c['anchor']})"
        lines.append(f"• Діаграма #{c['index']} {loc_str}:")
        lines.append(f"  - Тип діаграми: {c['type']}")
        lines.append(f"  - Назва (заголовок): {c['title']}")
        if c.get('axis_titles'):
            lines.append(f"  - Підписи осей: {', '.join(c['axis_titles'])}")
        if c.get('has_legend'):
            leg_info = f"наявна (позиція: {c.get('legend_position', 'налаштована')})"
            lines.append(f"  - Легенда: {leg_info}")
        if c.get('series'):
            lines.append("  - Ряди та діапазони даних:")
            for s in c['series']:
                lines.append(f"    * {s}")
    lines.append("════════════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def render_spreadsheet_to_images(file_path, max_pages=4):
    """
    Рендерить аркуші електронної таблиці (.xlsx, .xls, .ods) у візуальні PNG зображення
    через headless LibreOffice та pdftoppm, щоб передати їх у мультимодальний зір ШІ Gemini.
    """
    rendered = []
    if not file_path or not os.path.exists(file_path):
        return rendered

    lo_bin = 'libreoffice' if shutil.which('libreoffice') else ('soffice' if shutil.which('soffice') else None)
    ppm_bin = shutil.which('pdftoppm')

    if not lo_bin or not ppm_bin:
        return rendered

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            pdf_cmd = [
                lo_bin,
                '--headless',
                f'-env:UserInstallation=file://{tmp_dir}/lo_profile',
                '--convert-to', 'pdf',
                '--outdir', tmp_dir,
                file_path
            ]
            res = subprocess.run(pdf_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
            if res.returncode != 0:
                return rendered

            pdf_files = glob.glob(os.path.join(tmp_dir, '*.pdf'))
            if not pdf_files:
                return rendered
            pdf_file = pdf_files[0]

            page_prefix = os.path.join(tmp_dir, 'sheet_page')
            ppm_cmd = [
                ppm_bin,
                '-png',
                '-r', '150',
                pdf_file,
                page_prefix
            ]
            subprocess.run(ppm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)

            png_files = sorted(glob.glob(os.path.join(tmp_dir, 'sheet_page-*.png')))
            for idx, pf in enumerate(png_files[:max_pages]):
                try:
                    with open(pf, 'rb') as f:
                        data = f.read()
                    if 0 < len(data) <= 8 * 1024 * 1024:
                        b64 = base64.b64encode(data).decode('utf-8')
                        rendered.append({
                            'name': f'excel_chart_page_{idx+1}.png',
                            'mime_type': 'image/png',
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                except Exception:
                    pass
        except Exception:
            pass

    return rendered


def extract_images_from_xlsx(file_path, max_images=4, max_bytes_per_img=8 * 1024 * 1024):
    """
    Видобуває графічні зображення, діаграми та сторінки з таблиці Excel (.xlsx, .xls, .ods):
    1. Растрові зображення, додані користувачем (зберігаються в xl/media/).
    2. Візуальний рендеринг сторінок з вбудованими діаграмами та графіками через LibreOffice + pdftoppm,
       щоб ШІ Gemini міг безпосередньо оцінити вигляд, кольори, підписи та оформлення діаграм.
    """
    extracted = []
    has_charts = False

    # 1. Перевіряємо вбудовані картинки та наявність діаграм у .xlsx
    try:
        if zipfile.is_zipfile(file_path):
            with zipfile.ZipFile(file_path, 'r') as z:
                all_names = z.namelist()
                chart_files = [f for f in all_names if f.startswith('xl/charts/chart') and f.endswith('.xml')]
                if chart_files:
                    has_charts = True

                media_files = [f for f in all_names if f.startswith('xl/media/')]
                img_exts = {
                    '.png': 'image/png',
                    '.jpg': 'image/jpeg',
                    '.jpeg': 'image/jpeg',
                    '.webp': 'image/webp',
                    '.bmp': 'image/bmp',
                    '.gif': 'image/gif',
                }
                media_files.sort()
                for mf in media_files:
                    ext = os.path.splitext(mf)[1].lower()
                    if ext in img_exts:
                        info = z.getinfo(mf)
                        if 0 < info.file_size <= max_bytes_per_img:
                            data = z.read(mf)
                            b64 = base64.b64encode(data).decode('utf-8')
                            extracted.append({
                                'name': os.path.basename(mf),
                                'mime_type': img_exts[ext],
                                'data': b64,
                                'size_kb': len(data) / 1024
                            })
                            if len(extracted) >= max_images:
                                break
    except Exception:
        pass

    # 2. Якщо є вбудовані діаграми (або це файл .xls/.ods, або в xl/media/ нічого не знайдено),
    # рендеримо сторінки таблиці у високій якості, щоб ШІ міг побачити діаграми на власні очі!
    remaining_slots = max_images - len(extracted)
    if remaining_slots > 0:
        ext = os.path.splitext(file_path)[1].lower()
        if has_charts or ext in ['.xls', '.ods'] or not extracted:
            rendered_pages = render_spreadsheet_to_images(file_path, max_pages=remaining_slots)
            for r_img in rendered_pages:
                extracted.append(r_img)
                if len(extracted) >= max_images:
                    break

    return extracted


def extract_images_from_zip(file_path, max_images=6, max_bytes_per_img=8 * 1024 * 1024):
    """
    Видобуває графічні файли (фотографії зошитів, скриншоти тощо) з zip-архіву учня або вчителя.
    """
    extracted = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            img_exts = {
                '.png': 'image/png',
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.webp': 'image/webp',
                '.bmp': 'image/bmp',
                '.gif': 'image/gif',
            }
            names = [f for f in z.namelist() if not f.startswith('__MACOSX') and not os.path.basename(f).startswith('.')]
            names.sort()
            for mf in names:
                ext = os.path.splitext(mf)[1].lower()
                if ext in img_exts:
                    info = z.getinfo(mf)
                    if 0 < info.file_size <= max_bytes_per_img:
                        data = z.read(mf)
                        b64 = base64.b64encode(data).decode('utf-8')
                        extracted.append({
                            'name': os.path.basename(mf),
                            'mime_type': img_exts[ext],
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                        if len(extracted) >= max_images:
                            break
    except Exception:
        pass
    return extracted


def extract_text_from_excel(file_path, max_rows=50, max_cols=20):
    """
    Видобуває дані, таблиці та метадані діаграм із файлу Excel (.xlsx, .xls).
    """
    sheet_summaries = []
    charts_summary = ""

    ext = os.path.splitext(file_path)[1].lower() if file_path else ""

    # 1. Витягуємо метадані діаграм для .xlsx
    if ext == '.xlsx' or zipfile.is_zipfile(file_path):
        try:
            charts_info = parse_excel_charts(file_path)
            if charts_info:
                charts_summary = format_excel_charts_summary(charts_info)
        except Exception:
            pass

    # 2. Витягуємо дані таблиць
    # 2.1. Якщо це застарілий .xls, спершу читаємо напряму через xlrd
    if ext == '.xls':
        try:
            import xlrd
            wb = xlrd.open_workbook(file_path)
            for sheetname in wb.sheet_names()[:5]:
                sheet = wb.sheet_by_name(sheetname)
                sheet_lines = [f"📊 Аркуш: {sheetname}"]
                max_r = min(sheet.nrows, max_rows)
                max_c = min(sheet.ncols, max_cols)
                for r_idx in range(max_r):
                    row_vals = []
                    for c_idx in range(max_c):
                        cell = sheet.cell(r_idx, c_idx)
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            try:
                                dt = xlrd.xldate_as_datetime(cell.value, wb.datemode)
                                val = dt.strftime('%d.%m.%Y')
                            except Exception:
                                val = str(cell.value)
                        elif cell.ctype == xlrd.XL_CELL_NUMBER:
                            val = str(int(cell.value)) if cell.value.is_integer() else str(cell.value)
                        elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                            val = ""
                        else:
                            val = str(cell.value).strip() if cell.value is not None else ""
                        row_vals.append(val)
                    if any(v.strip() for v in row_vals):
                        sheet_lines.append(" | ".join(row_vals))
                if sheet.nrows > max_rows:
                    sheet_lines.append(f"[... ще рядки]")
                if len(sheet_lines) > 1:
                    sheet_summaries.append("\n".join(sheet_lines))
        except Exception:
            pass

    # 2.2. Якщо це .xlsx (або якщо xlrd не зміг прочитати), читаємо через openpyxl
    if not sheet_summaries:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)

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
        except Exception as e:
            # Резервне читання для застарілих .xls або пошкоджених файлів через LibreOffice
            lo_bin = 'libreoffice' if shutil.which('libreoffice') else ('soffice' if shutil.which('soffice') else None)
            if lo_bin and ext in ['.xls', '.xlsx']:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    try:
                        res = subprocess.run([lo_bin, '--headless', f'-env:UserInstallation=file://{tmp_dir}/lo_profile', '--convert-to', 'xlsx', '--outdir', tmp_dir, file_path],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
                        if res.returncode == 0:
                            gen_files = glob.glob(os.path.join(tmp_dir, '*.xlsx'))
                            if gen_files:
                                return extract_text_from_excel(gen_files[0], max_rows=max_rows, max_cols=max_cols)
                    except Exception:
                        pass
            sheet_summaries.append(f"[Помилка читання Excel таблиці: {str(e)}]")

    res_parts = []
    if sheet_summaries:
        res_parts.append("\n\n".join(sheet_summaries))
    if charts_summary:
        res_parts.append(charts_summary)

    return "\n\n".join(res_parts) if res_parts else "[Порожня електронна таблиця]"


def extract_text_from_binary_presentation(file_path, max_chars=40000):
    """
    Резервне видобування тексту зі застарілих бінарних файлів PowerPoint (.ppt)
    шляхом пошуку юнікод-рядків (UTF-16LE) та кириличних послідовностей (CP1251/UTF-8).
    """
    try:
        with open(file_path, 'rb') as f:
            data = f.read(5 * 1024 * 1024)

        text_chunks = []
        # 1. Пошук UTF-16LE рядків
        utf16_matches = re.findall(b'(?:[\x20-\x7e\t\n\r\x00-\xff]\x00){4,}', data)
        for m in utf16_matches:
            try:
                decoded = m.decode('utf-16le').strip()
                if len(decoded) > 3 and any(c.isalnum() for c in decoded):
                    if not any(sub in decoded for sub in ['Current User', 'PowerPoint Document', 'SummaryInformation', 'DocumentSummaryInformation']):
                        text_chunks.append(decoded)
            except Exception:
                pass

        # 2. Пошук ASCII / CP1251 рядків
        ascii_matches = re.findall(b'[\x20-\x7e\t\n\r\xc0-\xff]{6,}', data)
        for m in ascii_matches:
            for enc in ['utf-8', 'cp1251', 'latin-1']:
                try:
                    s = m.decode(enc).strip()
                    if s and len(s) > 5 and any(c.isalnum() for c in s):
                        if s not in text_chunks:
                            text_chunks.append(s)
                        break
                except Exception:
                    pass

        if text_chunks:
            return "Текст презентації (видобуто з бінарного формату .ppt):\n" + "\n".join(text_chunks)[:max_chars]
        return "[Документ .ppt не містить розпізнаваного тексту]"
    except Exception as e:
        return f"[Помилка читання .ppt файлу: {str(e)}]"


def extract_text_from_powerpoint(file_path, max_slides=40):
    """
    Видобуває детальну структуру, слайди, текст, ієрархію списків, таблиці та нотатки
    із презентацій PowerPoint (.pptx та .ppt).
    """
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except Exception:
        MSO_SHAPE_TYPE = None

    try:
        prs = Presentation(file_path)
        slides_text = []
        total_slides = len(prs.slides)

        def _process_shape(shape):
            lines = []
            img_c = 0
            tbl_c = 0

            # Рекурсивна обробка груп фігур
            if hasattr(shape, "shapes"):
                for sub_sh in shape.shapes:
                    sub_lines, sub_img, sub_tbl = _process_shape(sub_sh)
                    lines.extend(sub_lines)
                    img_c += sub_img
                    tbl_c += sub_tbl
                return lines, img_c, tbl_c

            # Таблиця
            if hasattr(shape, "has_table") and shape.has_table:
                tbl_c += 1
                lines.append("[Таблиця на слайді:]")
                for row in shape.table.rows:
                    row_cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    if any(row_cells):
                        lines.append(" | ".join(row_cells))
                return lines, img_c, tbl_c

            # Текстовий блок
            if hasattr(shape, "has_text_frame") and shape.has_text_frame:
                for p in shape.text_frame.paragraphs:
                    pt = p.text.strip()
                    if pt:
                        level = getattr(p, 'level', 0) or 0
                        indent = "  " * level
                        bullet = "• " if level > 0 else ""
                        lines.append(f"{indent}{bullet}{pt}")
            elif hasattr(shape, "text") and shape.text and shape.text.strip():
                lines.append(shape.text.strip())

            # Зображення / медіа
            if hasattr(shape, "shape_type"):
                st = shape.shape_type
                if MSO_SHAPE_TYPE and st in [MSO_SHAPE_TYPE.PICTURE, 13]:
                    img_c += 1
                elif hasattr(shape, "image"):
                    img_c += 1

            return lines, img_c, tbl_c

        # УВАГА: не використовувати зріз prs.slides[:max_slides], бо python-pptx викидає
        # AttributeError: 'list' object has no attribute 'rId'!
        for idx, slide in enumerate(prs.slides, 1):
            if idx > max_slides:
                break

            slide_header = f"📽️ Слайд {idx}/{total_slides}"
            title_text = ""
            try:
                if slide.shapes.title and slide.shapes.title.text.strip():
                    title_text = slide.shapes.title.text.strip().replace('\n', ' ')
            except Exception:
                pass

            if title_text:
                slide_header += f": «{title_text}»"
            else:
                slide_header += ":"

            slide_lines = [slide_header]
            slide_imgs = 0
            slide_tbls = 0

            for shape in slide.shapes:
                try:
                    if slide.shapes.title and shape == slide.shapes.title:
                        continue
                except Exception:
                    pass

                sh_lines, sh_img, sh_tbl = _process_shape(shape)
                slide_lines.extend(sh_lines)
                slide_imgs += sh_img
                slide_tbls += sh_tbl

            visual_indicators = []
            if slide_imgs > 0:
                visual_indicators.append(f"{slide_imgs} ілюстрацій/зображень")
            if slide_tbls > 0:
                visual_indicators.append(f"{slide_tbls} таблиць")
            if visual_indicators:
                slide_lines.append(f"  [Візуальне оформлення слайда: {', '.join(visual_indicators)}]")

            try:
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                    if notes:
                        slide_lines.append(f"  [Нотатки доповідача: {notes}]")
            except Exception:
                pass

            if len(slide_lines) > 1 or visual_indicators:
                slides_text.append("\n".join(slide_lines))
            elif title_text:
                slides_text.append(slide_header)

        overview = f"Всього слайдів у презентації: {total_slides}."
        if total_slides > max_slides:
            overview += f" (Опрацьовано перші {max_slides} слайдів)."

        return overview + "\n\n" + "\n\n".join(slides_text) if slides_text else "[Презентація не містить тексту або порожня]"
    except Exception as e:
        ext_lower = os.path.splitext(file_path)[1].lower()
        if ext_lower == '.ppt' or 'not a zip' in str(e).lower() or 'PackageNotFoundError' in str(e):
            return extract_text_from_binary_presentation(file_path)
        return f"[Помилка читання презентації: {str(e)}]"


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


def _optimize_image_for_ai(file_path_or_bytes, max_dim=1600, quality=85):
    """
    Оптимізує та масштабує фотозображення перед кодуванням у base64 для передачі до ШІ API.
    Зменшує 8-15 МБ фотографії з камер телефонів до 150-350 КБ без втрати читабельності рукописного тексту,
    зберігаючи вихідний MIME-тип (PNG для PNG, JPEG для JPEG).
    """
    try:
        from PIL import Image, ImageOps
        import io
        if isinstance(file_path_or_bytes, (bytes, bytearray)):
            orig_bytes = bytes(file_path_or_bytes)
            img = Image.open(io.BytesIO(orig_bytes))
        else:
            with open(file_path_or_bytes, 'rb') as f:
                orig_bytes = f.read()
            img = Image.open(io.BytesIO(orig_bytes))

        orig_fmt = (img.format or 'JPEG').upper()
        mime_type = 'image/png' if orig_fmt == 'PNG' else 'image/jpeg'

        w, h = img.size
        # Якщо розмір файлу менше 1.5 МБ і роздільна здатність у нормі — повертаємо як є без перетиснення
        if len(orig_bytes) <= 1536 * 1024 and w <= max_dim and h <= max_dim:
            return orig_bytes, mime_type

        try:
            img = ImageOps.exif_transpose(img)
        except Exception:
            pass

        if w > max_dim or h > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

        out = io.BytesIO()
        if orig_fmt == 'PNG':
            img.save(out, format='PNG', optimize=True)
            return out.getvalue(), 'image/png'
        else:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            img.save(out, format='JPEG', quality=quality, optimize=True)
            return out.getvalue(), 'image/jpeg'
    except Exception:
        fallback_mime = 'image/png' if (isinstance(file_path_or_bytes, str) and file_path_or_bytes.lower().endswith('.png')) else 'image/jpeg'
        if isinstance(file_path_or_bytes, (bytes, bytearray)):
            return bytes(file_path_or_bytes), fallback_mime
        try:
            with open(file_path_or_bytes, 'rb') as f:
                return f.read(), fallback_mime
        except Exception:
            return None, fallback_mime


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

    # Автоматично розпізнаємо та прив'язуємо співавторів із коментаря учня (якщо є)
    if submission.comment_student:
        try:
            from .student_matcher import auto_bind_coauthors_from_comment
            auto_bind_coauthors_from_comment(submission)
        except Exception:
            pass

    # 1. Текстовий коментар учня (обов'язково читається ШІ)
    if submission.comment_student:
        text_parts.append(f"Коментар/пояснення учня до роботи:\n«{submission.comment_student.strip()}»")
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
                    text_parts.append(f"Документ Word ({filename}) містить графічні елементи або текст відсутній.")
            except Exception as e:
                # Fallback to mammoth or XML
                try:
                    import mammoth
                    with open(file_path, "rb") as docx_file:
                        result = mammoth.extract_raw_text(docx_file)
                        if result.value.strip():
                            text_parts.append(f"Вміст документа Word ({filename}):\n{result.value.strip()[:50000]}")
                        else:
                            text_parts.append(f"[Не вдалося прочитати текст .docx: {e}]")
                except Exception:
                    text_parts.append(f"[Не вдалося прочитати текст .docx: {e}]")

            # 📸 Видобуваємо вбудовані зображення (скриншоти/фото), щоб передати на аналіз Gemini Vision
            docx_images = extract_images_from_docx(file_path)
            for d_img in docx_images:
                inline_media.append({
                    "mime_type": d_img['mime_type'],
                    "data": d_img['data']
                })
                text_parts.append(f"[У документі Word ({filename}) виявлено вбудоване зображення/скриншот: {d_img['name']} ({d_img['size_kb']:.1f} КБ) — передано на візуальний мультимодальний аналіз ШІ]")

        elif ext == '.doc':
            doc_text = extract_text_from_doc(file_path)
            text_parts.append(f"Вміст документа Word (.doc) ({filename}, {file_size_kb:.1f} КБ):\n{doc_text}")

        elif ext == '.odt':
            odt_text = extract_text_from_opendocument(file_path)
            if odt_text:
                text_parts.append(f"Вміст документа OpenDocument (.odt) ({filename}):\n{odt_text}")
            else:
                text_parts.append(f"[Документ .odt {filename} містить лише графічні елементи або порожній]")

            odt_images = extract_images_from_odt(file_path)
            for o_img in odt_images:
                inline_media.append({
                    "mime_type": o_img['mime_type'],
                    "data": o_img['data']
                })
                text_parts.append(f"[У документі OpenDocument ({filename}) виявлено вбудоване зображення: {o_img['name']} ({o_img['size_kb']:.1f} КБ) — передано на візуальний мультимодальний аналіз ШІ]")

        elif ext == '.rtf':
            rtf_text = extract_text_from_doc(file_path)
            text_parts.append(f"Вміст RTF документа ({filename}):\n{rtf_text}")

        # ── Д. ЕЛЕКТРОННІ ТАБЛИЦІ (.xlsx, .xls, .ods) ─────────────────────────
        elif ext in ['.xlsx', '.xls']:
            excel_text = extract_text_from_excel(file_path)
            text_parts.append(f"Вміст таблиці Excel ({filename}, {file_size_kb:.1f} КБ):\n{excel_text}")
            xlsx_imgs = extract_images_from_xlsx(file_path)
            for x_img in xlsx_imgs:
                inline_media.append({
                    "mime_type": x_img['mime_type'],
                    "data": x_img['data']
                })
                text_parts.append(f"[У таблиці Excel ({filename}) виявлено діаграму/сторінку з графіком: {x_img['name']} ({x_img['size_kb']:.1f} КБ) — передано на візуальний мультимодальний аналіз ШІ]")

        elif ext == '.ods':
            ods_text = extract_text_from_opendocument(file_path)
            text_parts.append(f"Вміст таблиці OpenDocument (.ods) ({filename}):\n{ods_text or '[Порожня таблиця]'}")
            ods_imgs = extract_images_from_xlsx(file_path)
            for o_img in ods_imgs:
                inline_media.append({
                    "mime_type": o_img['mime_type'],
                    "data": o_img['data']
                })
                text_parts.append(f"[У таблиці OpenDocument ({filename}) виявлено сторінку з графіком: {o_img['name']} ({o_img['size_kb']:.1f} КБ) — передано на візуальний мультимодальний аналіз ШІ]")

        # ── Е. ПРЕЗЕНТАЦІЇ (.pptx, .ppt, .odp) ─────────────────────────────────
        elif ext in ['.pptx', '.ppt']:
            pptx_text = extract_text_from_powerpoint(file_path)
            text_parts.append(f"Вміст презентації PowerPoint ({filename}, {file_size_kb:.1f} КБ):\n{pptx_text}")
            if ext == '.pptx':
                pptx_imgs = extract_images_from_pptx(file_path)
                for p_img in pptx_imgs:
                    inline_media.append({
                        "mime_type": p_img['mime_type'],
                        "data": p_img['data']
                    })
                    text_parts.append(f"[У презентації PowerPoint ({filename}) виявлено вбудоване зображення/слайд: {p_img['name']} ({p_img['size_kb']:.1f} КБ) — передано на візуальний аналіз ШІ]")

        elif ext == '.odp':
            odp_text = extract_text_from_opendocument(file_path)
            text_parts.append(f"Вміст презентації OpenDocument (.odp) ({filename}):\n{odp_text or '[Порожня презентація]'}")
            odp_imgs = extract_images_from_odt(file_path)
            for o_img in odp_imgs:
                inline_media.append({
                    "mime_type": o_img['mime_type'],
                    "data": o_img['data']
                })
                text_parts.append(f"[У презентації OpenDocument ({filename}) виявлено вбудоване зображення: {o_img['name']} ({o_img['size_kb']:.1f} КБ) — передано на візуальний аналіз ШІ]")

        # ── Є. ЗОБРАЖЕННЯ (ФОТО ЗОШИТІВ, СКРІНШОТИ, СХЕМИ) ───────────────────
        elif ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.tiff', '.tif', '.svg', '.heic', '.heif']:
            try:
                b_data, mime_type = _optimize_image_for_ai(file_path)
                if b_data:
                    b64_data = base64.b64encode(b_data).decode('utf-8')
                    inline_media.append({
                        "mime_type": mime_type,
                        "data": b64_data
                    })
                    text_parts.append(f"[Прикріплено фотозображення зошита/роботи: {filename} ({file_size_kb:.1f} КБ, оптимізовано для ШІ)]")
            except Exception as e:
                text_parts.append(f"[Помилка обробки зображення: {e}]")

        # ── Ж. АРХІВИ (.zip, .tar, .gz, .tgz, .rar, .7z) ───────────────────────
        elif ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.tgz']:
            archive_summary = extract_text_from_archive(file_path, ext)
            text_parts.append(f"Вміст прикріпленого архіву ({filename}, {file_size_kb:.1f} КБ):\n{archive_summary}")
            if ext == '.zip':
                zip_imgs = extract_images_from_zip(file_path)
                for z_img in zip_imgs:
                    inline_media.append({
                        "mime_type": z_img['mime_type'],
                        "data": z_img['data']
                    })
                    text_parts.append(f"[В архіві ({filename}) знайдено зображення/фото розв'язку: {z_img['name']} ({z_img['size_kb']:.1f} КБ) — передано на візуальний аналіз ШІ]")

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
    Підтримує виправлення невалідних escape-послідовностей (наприклад \'), незакритих лапок/дужок
    через обрив токенів, та автоматичний regex-парсинг окремих полів при пошкодженому JSON.
    """
    if not text:
        return None
    text = text.strip()

    # Допоміжна функція нормалізації тексту перед JSON-парсингом
    def _sanitize_json_str(s):
        # Виправлення невалідно екранованих одинарних лапок \' -> '
        s = re.sub(r"(?<!\\)\\'", "'", s)
        # Виправлення типових висячих ком перед закриваючими дужками
        s = re.sub(r',\s*([\]}])', r'\1', s)
        return s

    # 1. Пряма спроба парсингу нормалізованого тексту
    sanitized = _sanitize_json_str(text)
    try:
        return json.loads(sanitized, strict=False)
    except Exception:
        pass

    # 2. Пошук markdown блоку ```json ... ``` або ``` ... ```
    code_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if code_match:
        cand = _sanitize_json_str(code_match.group(1).strip())
        try:
            return json.loads(cand, strict=False)
        except Exception:
            pass

    # 3. Пошук першої '{' та останньої '}'
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = _sanitize_json_str(text[first_brace:last_brace + 1].strip())
        try:
            return json.loads(candidate, strict=False)
        except Exception:
            pass

    # 4. Спроба відновлення обірваного/обрізаного JSON (наприклад через ліміт токенів)
    if first_brace != -1:
        cand = text[first_brace:]
        for r_pos in range(len(cand), max(0, len(cand) - 800), -1):
            sub = cand[:r_pos].rstrip()
            if sub.endswith(','):
                sub = sub[:-1].rstrip()
            open_braces = sub.count('{') - sub.count('}')
            open_brackets = sub.count('[') - sub.count(']')
            if open_braces > 0 or open_brackets > 0:
                closing = (']' * max(0, open_brackets)) + ('}' * max(0, open_braces))
                attempt = _sanitize_json_str(sub + closing)
                try:
                    res = json.loads(attempt, strict=False)
                    if isinstance(res, dict) and ('suggested_grade' in res or 'feedback_comment' in res or 'summary' in res):
                        return res
                except Exception:
                    continue

    # 5. Резервне пряме видобування полів через Regex (якщо JSON синтаксично пошкоджений)
    if first_brace != -1 or '"suggested_grade"' in text or '"feedback_comment"' in text:
        recovered = {}
        g_m = re.search(r'"suggested_grade"\s*:\s*["\']?([^"\',\s}]+)', text)
        if g_m: recovered['suggested_grade'] = g_m.group(1).strip()

        l_m = re.search(r'"level"\s*:\s*"([^"]+)"', text)
        if l_m: recovered['level'] = l_m.group(1).strip()

        fw_m = re.search(r'"format_warning"\s*:\s*(?:"((?:[^"\\]|\\.)*)"|null|None)', text)
        if fw_m and fw_m.group(1): recovered['format_warning'] = fw_m.group(1).strip()

        s_m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if s_m:
            try:
                recovered['summary'] = json.loads('"' + s_m.group(1) + '"')
            except Exception:
                recovered['summary'] = s_m.group(1).replace(r'\"', '"').replace(r'\n', '\n')

        fc_m = re.search(r'"feedback_comment"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
        if fc_m:
            try:
                recovered['feedback_comment'] = json.loads('"' + fc_m.group(1) + '"')
            except Exception:
                recovered['feedback_comment'] = fc_m.group(1).replace(r'\"', '"').replace(r'\n', '\n')

        str_m = re.search(r'"strengths"\s*:\s*\[([\s\S]*?)\]', text)
        if str_m:
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', str_m.group(1))
            recovered['strengths'] = [it.replace(r'\"', '"').replace(r'\n', '\n') for it in items]

        weak_m = re.search(r'"weaknesses"\s*:\s*\[([\s\S]*?)\]', text)
        if weak_m:
            items = re.findall(r'"((?:[^"\\]|\\.)*)"', weak_m.group(1))
            recovered['weaknesses'] = [it.replace(r'\"', '"').replace(r'\n', '\n') for it in items]

        if recovered.get('suggested_grade') or recovered.get('feedback_comment') or recovered.get('summary'):
            return recovered

    return None


def evaluate_submission_with_gemini(submission, custom_prompt=None, ai_settings=None, preset_id=None, criteria_preset=None, selected_gr_codes=None):
    """
    Виконує педагогічний аналіз та попереднє оцінювання роботи учня за допомогою Google Gemini.
    Підтримує чергу пріоритетів моделей, вибір шаблону критеріїв та вибір конкретних ГР (прапорцями).
    Результати записуються безпосередньо у submission (ai_suggested_grade, ai_feedback, ai_status тощо).
    """
    settings = ai_settings or get_ai_settings()
    act_provider, act_key, act_model, act_url, is_backup_active = settings.get_active_config()

    if not act_key and act_provider != 'custom':
        # Якщо в активному провайдері немає ключа, але є резервний — використовуємо резервний
        if settings.has_backup_configured():
            b_prov, b_key, b_model, b_url = settings.get_backup_config()
            if b_key or b_prov == 'custom':
                act_provider, act_key, act_model, act_url = b_prov, b_key, b_model, b_url
                is_backup_active = not is_backup_active
        if not act_key and act_provider != 'custom':
            error_msg = f"API Key для {act_provider.title()} не налаштовано в системі. Вкажіть ключ у Налаштуваннях ШІ."
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
        f"УМОВА ТА ВИМОГИ ВЧИТЕЛЯ (ЗАВДАННЯ ДО ВИКОНАННЯ):\n{assignment_desc}\n",
        f"ОБРАНІ КРИТЕРІЇ ПЕРЕВІРКИ: {preset_name_display}",
    ]

    # ── ІНДИВІДУАЛЬНІ КРИТЕРІЇ ОЦІНЮВАННЯ ВЧИТЕЛЯ ДЛЯ ЦЬОГО ЗАВДАННЯ ─────────
    if assignment and assignment.custom_criteria and assignment.custom_criteria.strip():
        prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
        prompt_lines.append("📋 ІНДИВІДУАЛЬНІ КРИТЕРІЇ ОЦІНЮВАННЯ ВЧИТЕЛЯ ДЛЯ ЦЬОГО ЗАВДАННЯ:")
        prompt_lines.append("Вчитель встановив спеціальні індивідуальні критерії оцінювання саме для цього завдання:")
        prompt_lines.append(assignment.custom_criteria.strip())
        prompt_lines.append(
            "\n⚠️ ВАЖЛИВА ВИМОГА ДЛЯ ШІ ЩОДО ІНДИВІДУАЛЬНИХ КРИТЕРІЇВ:\n"
            "- Оцінюй роботу суворо з урахуванням цих індивідуальних критеріїв вчителя!\n"
            "- Якщо індивідуальні критерії вчителя визначають конкретну розбаловку, вимоги до структури відповіді чи особливі шкали — вони мають НАЙВИЩИЙ ПРІОРИТЕТ над загальними шаблонами!\n"
            "- У 'feedback_comment' та 'summary' чітко зістав відповідь учня із цими індивідуальними критеріями вчителя."
        )
        prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    # ── КРИТИЧНО: ОБСЯГ ЗАВДАННЯ ВЧИТЕЛЯ ТА ПРІОРИТЕТ УМОВИ (Scope of Work) ───
    prompt_lines.append(
        "🎯 КРИТИЧНЕ ПРАВИЛО: ОБСЯГ РОБОТИ ТА ДЖЕРЕЛО ЗАВДАННЯ (SCOPE OF WORK):\n"
        "1. Поле «УМОВА ТА ВИМОГИ ВЧИТЕЛЯ (ЗАВДАННЯ ДО ВИКОНАННЯ)» задає мету, контекст та конкретні вказівки вчителя.\n"
        "2. ПРИКРІПЛЕНІ ВЧИТЕЛЕМ ФАЙЛИ (презентації .pptx/.ppt/.odp/PDF, документи, зображення, фото вправ/підручника) є ПОВНОЦІННИМ ДЖЕРЕЛОМ ЗАВДАННЯ ТА НАВЧАЛЬНОГО КОНТЕКСТУ. Вчителі дуже часто розміщують формулювання завдань безпосередньо на слайдах презентацій (особливо на останніх/фінальних слайдах під заголовками «Домашнє завдання», «Практична робота», «Завдання до уроку», «Вправи», «Питання для самоперевірки» тощо) або у прикріплених PDF/зображеннях.\n"
        "3. ЯКЩО ВЧИТЕЛЬ У ПОЛІ «ЗАВДАННЯ ДО ВИКОНАННЯ» ВКАЗАВ ЗРОБИТИ ЛИШЕ ПЕВНЕ КОНКРЕТНЕ ЗАВДАННЯ (наприклад: «виконати тільки завдання 2 зі слайду 6», «зробити вправу 3», «розв'язати номер 4», «виконати лише одне завдання...» тощо):\n"
        "   - ТИ ЗОБОВ'ЯЗАНИЙ ОЦІНЮВАТИ ВИКЛЮЧНО ТЕ КОНКРЕТНЕ ЗАВДАННЯ/ВПРАВУ, ЯКЕ ЗАДАВ ВЧИТЕЛЬ!\n"
        "   - СУВОРО ТА КАТЕГОРИЧНО ЗАБОРОНЕНО знижувати оцінку, занижувати рівень досягнень або писати у «weaknesses» чи «feedback_comment», що робота неповна або що «учень не виконав інші завдання з презентації/файлу». Решта завдань з файлу вважаються незаданими!\n"
        "   - Якщо учень якісно та правильно виконав вказане вчителем завдання (наприклад, тільки 1 вправу з 5 наявних у матеріалах), робота вважається ВИКОНАНОЮ НА 100% У ПОВНОМУ ОБСЯЗІ і заслуговує на найвищий бал (10-12 балів відповідно до якості виконання).\n"
        "4. ЯКЩО ОПИС ВЧИТЕЛЯ КОРОТКИЙ АБО ЗАГАЛЬНИЙ (наприклад: «опрацювати презентацію», «виконати завдання», «домашнє завдання у файлі», або просто вказано назву теми уроку):\n"
        "   - ШІ ЗОБОВ'ЯЗАНИЙ уважно проаналізувати всі слайди презентації, сторінки PDF, документи та зображення вчителя, знайти сформульовані практичні завдання/запитання/вправи та оцінити виконання учнем саме цих завдань з матеріалів вчителя!\n"
        "5. БАГАТОЗАДАЧНІ УМОВИ ТА ПРАВИЛА ВИБОРУ ЗАВДАНЬ («виконати будь-яке завдання на вибір», «одне на вибір» тощо):\n"
        "   - Якщо вчитель дозволив учням обрати будь-яке завдання з файлу умови чи слайдів презентації:\n"
        "     * КРОК 1: ШІ спочатку уважно перевіряє, чи зазначив учень, яке саме завдання він виконував: у полі «ВАЖЛИВИЙ КОМЕНТАР / ПОЯСНЕННЯ УЧНЯ» (наприклад: «виконував завдання 2», «робив вправу 3», «завдання №1»), у назві прикріпленого файлу або у тексті відповіді.\n"
        "     * КРОК 2 (якщо учень зазначив обране завдання): ШІ оцінює саме це завдання за повними критеріями без жодного зниження оцінки за вибір.\n"
        "     * КРОК 3 (якщо учень НЕ зазначив, яке саме завдання він виконував):\n"
        "       - ШІ повинен самостійно проаналізувати зміст зданої роботи, зіставити його із завданнями з файлу умови чи слайдів презентації та автоматично визначити найбільш імовірне завдання.\n"
        "       - ⚠️ ОБОВ'ЯЗКОВО у полях 'weaknesses', 'feedback_comment' та 'summary' чітко зазначити: «Зверніть увагу: ви не вказали, яке саме завдання з умови на вибір ви виконували (визначено як Завдання X). Відсутність зазначення обраного завдання вплинула на оцінку (знижено бал за дотримання вимог оформлення).»\n"
        "       - ⚠️ ЗНИЗИТИ оцінку на 1-2 бали через недотримання вимоги зазначити обране завдання.\n"
        "6. КОЛИ ШІ НЕ ЗРОЗУМІВ, ЯКЕ ЗАВДАННЯ ВИКОНАНО АБО РОБОТА НЕ ВІДПОВІДАЄ ЖОДНОМУ ЗАВДАННЮ:\n"
        "   - ⚠️ НАЙВАЖЛИВІШЕ ПРАВИЛО: Перед тим як зробити висновок, що завдання незрозуміле, ШІ ЗОБОВ'ЯЗАНИЙ перевірити ВСІ слайди презентацій, усі сторінки PDF та зображення від вчителя! Якщо робота учня відповідає завданням, питанням чи вправам з будь-якого слайду чи файлу вчителя — завдання ПОВНІСТЮ ЗРОЗУМІЛЕ І ЗНАЙДЕНЕ!\n"
        "   - КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 'suggested_grade': 'Доопрацювати', 'unclear_task': true або писати «Не зрозуміло, яке саме завдання виконане», якщо учень виконав завдання, знайдене у презентації, PDF або матеріалах вчителя!\n"
        "   - Тільки якщо зміст роботи ДІЙСНО не підходить під ЖОДНЕ завдання з тексту опису, ЖОДНОГО слайду презентації та ЖОДНОГО файлу вчителя (наприклад, здано стороннє фото чи зовсім сторонній текст):\n"
        "     * Встанови 'suggested_grade': 'Доопрацювати', 'level': 'Початковий', 'unclear_task': true.\n"
        "     * У полі 'format_warning' ОБОВ'ЯЗКОВО поверни: «Не зрозуміло, яке саме завдання виконане. Будь ласка, вкажіть номер завдання в коментарі або перевірте прикріплений файл.»\n"
        "     * У полях 'summary' та 'feedback_comment' розгорнуто поясни учневі, що за зданими матеріалами не вдалося визначити, яке завдання розв'язувалося, і роботу повернуто на доопрацювання.\n"
        "7. ВИСНОВКИ В КОМЕНТАРІ УЧНЯ ДО ЗДАЧІ:\n"
        "   - Учні мають можливість залишити висновки по своїй роботі в полі коментаря (якщо самого висновку немає у файлі або учень забув його туди дописати).\n"
        "   - Якщо учень зазначив висновки або підсумки в коментарі до здачі роботи — ШІ ЗОБОВ'ЯЗАНИЙ повністю зарахувати їх як наявний та повноцінний висновок до завдання при оцінюванні!\n"
    )

    # ── КРИТИЧНО: ТОЧНЕ РОЗУМІННЯ СУТІ ЗАВДАННЯ, ЗМІСТОВА ВІДПОВІДНІСТЬ ТА ПОВНОТА ──
    prompt_lines.append(
        "🎯 КРИТИЧНЕ ПРАВИЛО: ТОЧНЕ РОЗУМІННЯ СУТІ ЗАВДАННЯ, ЗМІСТОВА ВІДПОВІДНІСТЬ ТА ПОВНОТА:\n"
        "1. АНАЛІЗ ФОРМАТУ ТА ВИМОГ ЗАВДАННЯ:\n"
        "   - Уважно проаналізуй поле «УМОВА ТА ВИМОГИ ВЧИТЕЛЯ (ЗАВДАННЯ ДО ВИКОНАННЯ)». З'ясуй, ЩО САМЕ вимагається від учнів:\n"
        "     * Створити структурований список/перелік дат із подіями;\n"
        "     * Написати твір-роздум або есе певної структури;\n"
        "     * Розв'язати блок завдань/задач із записом умови, формул, обчислень і відповіді;\n"
        "     * Скласти комп'ютерну програму, електронну таблицю, схему чи презентацію за заданими вимогами тощо.\n"
        "   - Оцінюй відповідність зданої роботи САМЕ ЦІЙ ФОРМІ ТА ЗМІСТУ, а не випадковим ключовим словам чи побіжним фразам!\n\n"
        "2. СУВОРІ КРИТЕРІЇ ДЛЯ ВИСОКИХ БАЛІВ (10-12 БАЛІВ):\n"
        "   - Оцінки 10, 11 або 12 балів (Високий рівень) призначаються ВИКЛЮЧНО тоді, коли завдання виконано ПОВНІСТЮ, СТРУКТУРОВАНО, ЗМІСТОВНО ТА САМОСТІЙНО відповідно до поставлених вимог вчителя!\n"
        "   - КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 10-12 балів за окремі вирвані фрази, фрагментарні начерки чи випадкові згадки слів/дат!\n"
        "   - НАПРИКЛАД: якщо вимагалося створити перелік/список дат, а учень здав картинку та лише одне коротке речення з датами — це ФРАГМЕНТАРНА спроба! Ставити 10-12 балів за таку роботу КАТЕГОРИЧНО ЗАБОРОНЕНО.\n"
        "   - Роботи, де завдання виконано лише частково або поверхово (окремі речення замість повного списку/твору, картинка без належного розкриття теми чи розв'язку), оцінюються в межах СЕРЕДНЬОГО РІВНЯ (4-6 балів) або ПОЧАТКОВОГО РІВНЯ (1-3 бали / «Доопрацювати»).\n\n"
        "3. ВІДСУТНІСТЬ НЕОБХІДНОГО МАТЕРІАЛУ АБО НЕВІДПОВІДНІСТЬ ТЕМІ:\n"
        "   - Якщо у зданій роботі немає того навчального матеріалу, який вимагався за темою (наприклад, здано лише картинку з кількома словами без виконання розв'язку чи розкриття теми, сторонній контент тощо), оцінюй роботу об'єктивно й критично.\n"
        "   - Якщо зміст роботи не відповідає темі або суті завдання — призначай статус 'Доопрацювати' (або 1-3 бали, якщо потрібна цифрова оцінка).\n\n"
        "4. ОБОВ'ЯЗКОВИЙ ЗВОРОТНИЙ ЗВ'ЯЗОК ПРИ ОЦІНЦІ МЕНШЕ 10 БАЛІВ (1-9 або 'Доопрацювати'):\n"
        "   - Якщо рекомендована оцінка менше 10 балів (або 'Доопрацювати'):\n"
        "     * Окрім позитивних сторін («strengths»), ТИ ЗОБОВ'ЯЗАНИЙ У РОЗДІЛАХ «weaknesses» ТА «feedback_comment» ЧІТКО Й КОНСТРУКТИВНО ОПИСАТИ В ЗАГАЛЬНОМУ («але в загальному»), що саме не так і чого не вистачає в роботі для повного виконання завдання!\n"
        "     * Зістав вимогу завдання із фактично зданим результатом: наприклад, узагальнено поясни учню: «Завдання вимагало скласти детальний хронологічний перелік дат із подіями, проте в роботі наведено лише одне речення та ілюстрацію. Для отримання вищого балу необхідно виконати роботу в повному обсязі — скласти повноцінний список ключових дат із назвами подій.»\n"
        "     * Коментар має бути тактовним, узагальненим («в загальному»), без надмірної прискіпливості до дрібниць, але щоб учень чітко зрозумів причину оцінки та напрямок покращення."
    )

    # ── ОЦІНЮВАННЯ ПРЕЗЕНТАЦІЙ (.pptx, .ppt, .odp) ──────────────────────────
    prompt_lines.append(
        "📽️ ВКАЗІВКИ ДЛЯ ПЕРЕВІРКИ ПРЕЗЕНТАЦІЙ (якщо робота є презентацією):\n"
        "- Оцінюй презентацію комплексно: змістовну глибину розкриття теми, логічну структуру (титульний слайд, вступ, основні тези, висновки), лаконічність формулювання думок на слайдах (тези замість перевантаження суцільним текстом).\n"
        "- Враховуй візуальне наповнення (наявність ілюстрацій, схем, таблиць, зафіксованих у структурі слайдів).\n"
        "- У 'strengths' та 'weaknesses' відзначай як відповідність темі, так і якість оформлення презентації."
    )

    # ── ОЦІНЮВАННЯ ЕЛЕКТРОННИХ ТАБЛИЦЬ ТА ДІАГРАМ/ГРАФІКІВ (.xlsx, .xls, .ods) ──
    prompt_lines.append(
        "📊 ВКАЗІВКИ ДЛЯ ПЕРЕВІРКИ ЕЛЕКТРОННИХ ТАБЛИЦЬ ТА ДІАГРАМ (Excel / Calc):\n"
        "- Уважно перевіряй наявність побудованих діаграм, графіків, гістограм та візуалізацій (якщо в завданні вимагалося створити діаграму/графік)!\n"
        "- Звертай особливу увагу на структурований блок «ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ» у текстовому описі таблиці, а також на прикріплені візуальні зображення сторінок таблиці з графіками (передані у мультимодальному контексті).\n"
        "- Якщо учень побудував діаграму: оцінюй правильність вибору типу діаграми (стовпчаста/гістограма, кругова, графік тощо), наявність назви (заголовка), підписів осей, легенди та коректність діапазонів даних (рядів та категорій).\n"
        "- КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати, що діаграма відсутня, якщо вона зафіксована у структурі таблиці або на переданих зображеннях сторінок!\n"
        "- Оцінюй також коректність розрахунків, використання формул (якщо вимагалося) та структуру таблиці."
    )

    # Витягуємо вміст прикріплених вчителем файлів до завдання (щоб ШІ знав повну умову завдання)
    if assignment and assignment.files.exists():
        from django.conf import settings as django_settings
        primary_task_content = []
        teacher_files_content = []
        for af in assignment.files.all():
            if af.file and os.path.exists(af.file.path):
                af_name = af.original_name or os.path.basename(af.file.name)
                af_ext = af.get_extension()
                is_ai_task = getattr(af, 'is_task_source_for_ai', False)

                # 1. Спочатку витягуємо детальний структурований текст файлу (слайди, заголовки, таблиці, параграфи)
                af_text = ""
                if af_ext in ['.pptx', '.ppt']:
                    try:
                        af_text = extract_text_from_powerpoint(af.file.path)
                    except Exception:
                        pass
                elif af_ext == '.odp':
                    try:
                        af_text = extract_text_from_opendocument(af.file.path)
                    except Exception:
                        pass

                if not af_text:
                    try:
                        af_text = get_normalized_file_content(af.file.path, af.file.name)
                    except Exception:
                        pass

                # 2. Додаємо візуальні медіа до inline_media для Gemini Vision (тільки коли це дійсно необхідно):
                has_visual_attached = False

                # Зображення (фото вправ з підручника, зошита, графічні схеми) — оптимізуємо розмір
                if af_ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']:
                    try:
                        b_data, mime_t = _optimize_image_for_ai(af.file.path)
                        if b_data:
                            inline_media.append({
                                "mime_type": mime_t,
                                "data": base64.b64encode(b_data).decode('utf-8')
                            })
                            has_visual_attached = True
                    except Exception:
                        pass

                # Прямий PDF документ: передаємо у Vision, якщо розмір до 4 МБ або якщо текст не вдалося видобути
                elif af_ext == '.pdf':
                    try:
                        pdf_size = os.path.getsize(af.file.path)
                        if (not af_text or len(af_text.strip()) < 60 or pdf_size <= 4 * 1024 * 1024) and pdf_size <= 10 * 1024 * 1024:
                            with open(af.file.path, 'rb') as f_pdf:
                                inline_media.append({
                                    "mime_type": "application/pdf",
                                    "data": base64.b64encode(f_pdf.read()).decode('utf-8')
                                })
                            has_visual_attached = True
                    except Exception:
                        pass

                # Презентація (.pptx, .ppt, .odp) або Office документ:
                # ОПТИМІЗАЦІЯ ШВИДКОДІЇ: якщо структурований текст слайдів/документа успішно видобуто,
                # ШІ миттєво оцінює роботу за текстом без передачі важкого багатомегабайтного PDF прев'ю!
                # Передаємо згенероване PDF прев'ю ТІЛЬКИ якщо текст відсутній (чисто графічні слайди чи скани).
                elif af_ext in ['.pptx', '.ppt', '.odp', '.docx', '.xlsx', '.xls', '.ods']:
                    preview_pdf_path = None
                    cand1 = os.path.join(django_settings.MEDIA_ROOT, 'previews', f"{af.id}.pdf")
                    cand2 = os.path.join(django_settings.MEDIA_ROOT, 'previews', str(af.id), 'presentation.pdf')
                    if os.path.exists(cand1):
                        preview_pdf_path = cand1
                    elif os.path.exists(cand2):
                        preview_pdf_path = cand2
                    elif not af_text or len(af_text.strip()) < 60:
                        # Тільки якщо тексту немає, запускаємо важку конвертацію LibreOffice на льоту:
                        try:
                            from .views import get_pdf_preview_url
                            get_pdf_preview_url(af)
                            if os.path.exists(cand1):
                                preview_pdf_path = cand1
                        except Exception:
                            pass

                    if preview_pdf_path and os.path.exists(preview_pdf_path) and os.path.getsize(preview_pdf_path) <= 6 * 1024 * 1024:
                        try:
                            with open(preview_pdf_path, 'rb') as f_prev:
                                inline_media.append({
                                    "mime_type": "application/pdf",
                                    "data": base64.b64encode(f_prev.read()).decode('utf-8')
                                })
                            has_visual_attached = True
                        except Exception:
                            pass

                visual_status = " [візуальний вміст/PDF передано на безпосередній зоровий аналіз ШІ]" if has_visual_attached else ""

                if is_ai_task:
                    header = f"🎯 ГОЛОВНИЙ ФАЙЛ З УМОВОЮ ЗАВДАННЯ ВІД ВЧИТЕЛЯ «{af_name}»{visual_status} (вчитель зазначив цей файл як першоджерело умови завдання):"
                    if af_text:
                        primary_task_content.append(f"{header}\n{af_text[:16000]}")
                    else:
                        primary_task_content.append(f"{header} ({af_ext})")
                else:
                    if af_ext in ['.pptx', '.ppt', '.odp']:
                        kind = "Презентація до уроку"
                    elif af_ext == '.pdf':
                        kind = "PDF документ/матеріал"
                    elif af_ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']:
                        kind = "Зображення завдання"
                    else:
                        kind = "Матеріал"

                    if af_text:
                        teacher_files_content.append(f"• {kind} вчителя «{af_name}» ({af_ext}){visual_status}:\n{af_text[:16000]}")
                    else:
                        teacher_files_content.append(f"• Прикріплений вчителем файл «{af_name}» ({af_ext}){visual_status}")

        if primary_task_content:
            prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
            prompt_lines.append("🎯 ОСНОВНИЙ ФАЙЛ З УМОВОЮ ЗАВДАННЯ ДЛЯ ШІ (ВКАЗАНО ВЧИТЕЛЕМ):")
            prompt_lines.append("Вчитель окремо позначив цей файл як першоджерело умови завдання!")
            prompt_lines.append("ШІ повинен оцінювати роботу учня на підставі конкретних завдань і вимог саме із цього файлу:")
            prompt_lines.extend(primary_task_content)
            prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

        if teacher_files_content:
            prompt_lines.append("\n═══════════════════════════════════════════════════════════════════")
            prompt_lines.append("МАТЕРІАЛИ ДО УРОКУ / ДОВІДКОВІ ФАЙЛИ ВЧИТЕЛЯ (ПРЕЗЕНТАЦІЇ, PDF, ЗОБРАЖЕННЯ, ДОКУМЕНТИ):")
            if not primary_task_content:
                prompt_lines.append(
                    "⚠️ КРИТИЧНО ДЛЯ ШІ: ПОШУК ЗАВДАННЯ В ЦИХ МАТЕРІАЛАХ:\n"
                    "1. Вчитель прикріпив матеріали (презентацію .pptx/.pdf, документ або зображення). Якщо текстовий опис містить коротку чи загальну вказівку (наприклад: «Опрацювати презентацію», «Виконати завдання», «Домашнє завдання на слайді» або просто тему уроку) — САМЕ В ЦИХ ФАЙЛАХ РОЗТАШОВАНО ЗАВДАННЯ ДЛЯ УЧНЯ!\n"
                    "2. ДЕ ШУКАТИ ЗАВДАННЯ:\n"
                    "   * У презентаціях (.pptx, .ppt, .odp, PDF): уважно перевір ФІНАЛЬНІ/ОСТАННІ СЛАЙДИ під заголовками «Домашнє завдання», «Практична робота», «Завдання до уроку», «Вправи для закріплення», «Питання для самоперевірки», або практичні завдання на окремих слайдах;\n"
                    "   * У PDF та документах: знайди блок вправ, практичних робіт або запитань;\n"
                    "   * На зображеннях: розглянь завдання з фото підручника чи схеми.\n"
                    "3. КОНТЕКСТ УРОКУ: слайди та сторінки містять теорію, терміни, правила та приклади уроку. Враховуй цей навчальний контекст при оцінці правильності та повноти відповіді учня.\n"
                    "4. ЗАБОРОНА ПОМИЛКОВОГО «ДОПРАЦЮВАННЯ»:\n"
                    "   * Якщо відповідь учня відповідає завданням, запитанням або вправам зі слайдів презентації, PDF чи зображення вчителя — завдання ПОВНІСТЮ ЗРОЗУМІЛЕ ТА ВИЗНАЧЕНЕ!\n"
                    "   * СУВОРО ЗАБОРОНЕНО ставити 'unclear_task: true' або повертати роботу на 'Доопрацювати' через «незрозумілість завдання», якщо воно виконане за цими матеріалами вчителя!\n"
                    "5. Якщо у файлі міститься кілька завдань, а вчитель вимагав виконати лише конкретне — оцінюй виключно задане, а решта завдань з файлу вважаються незаданими."
                )
            else:
                prompt_lines.append("⚠️ УВАГА ДЛЯ ШІ: Нижче наведено додаткові матеріали уроку (презентація, роздатковий матеріал, підручник). Враховуй їхній зміст та контекст при оцінюванні роботи учня. Якщо вчитель задав конкретне завдання, решта завдань з файлу вважаються незаданими.")
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

    # ── ПЕРЕВІРКА НА ВИКОРИСТАННЯ ШТУЧНОГО ІНТЕЛЕКТУ, ЗОБРАЖЕНЬ ТА ОБХОДУ ДЕТЕКТОРІВ ──
    tolerance_percent = getattr(settings, 'ai_detector_tolerance_percent', 25) or 25
    is_ai_allowed = bool(assignment and assignment.allow_ai_usage)
    prompt_lines.append("═══════════════════════════════════════════════════════════════════")
    prompt_lines.append("🤖 ПЕРЕВІРКА НА ВИКОРИСТАННЯ ШТУЧНОГО ІНТЕЛЕКТУ, ЗОБРАЖЕНЬ ТА ОБХОДУ ДЕТЕКТОРІВ (AI DETECTOR & BYPASS CHECK):")
    ai_check_instructions = [
        f"ПОЛІТИКА ВЧИТЕЛЯ ЩОДО ШІ ДЛЯ ЦЬОГО ЗАВДАННЯ: {'🟢 ДОЗВОЛЕНО використання ШІ учнями' if is_ai_allowed else '🔴 СУВОРО ЗАБОРОНЕНО використання ШІ (вимагається самостійна праця учня)'}.",
        f"ПОРІГ ТОЛЕРАНТНОСТІ СИСТЕМИ: {tolerance_percent}% (випадкові збіги термінів, формул, умов завдань чи цитат підручника в межах цього відсотка вважаються допустимими).",
        "",
        "ОБОВ'ЯЗКОВО ТА ПРИСКІПЛИВО ПРОАНАЛІЗУЙ УВЕСЬ ЗДАНИЙ МАТЕРІАЛ УЧНЯ (текст, код, прикріплені зображення, слайди, файли, посилання) НА ПРЕДМЕТ ГЕНЕРАЦІЇ ШІ ТА СПРОБ ОБХОДУ:",
        "",
        "1. АНАЛІЗ ПРИКРІПЛЕНИХ ЗОБРАЖЕНЬ ТА ГРАФІКИ (DALL-E, Midjourney, Stable Diffusion, Canva AI, Firefly тощо):",
        "   - Перевір кожне прикріплене зображення (малюнки, схеми, ілюстрації, скріншоти, фото робіт) на ознаки генерації нейромережами:",
        "     * Характерна синтетична гладкість, пластиковий блиск («airbrushed sheen»), неприродні світлотіні або надмірна розмитість деталей фону;",
        "     * Анатомічні та структурні артефакти: неприродна форма рук/пальців, спотворена симетрія об'єктів, дивна перспектива;",
        "     * Нерозбірливий, спотворений або вигаданий текст на зображенні (псевдо-шрифти, характерні для дифузійних моделей);",
        "     * Водяні знаки або характерні стилізовані патерни ШІ-генераторів;",
        "     * Якщо вчитель вимагав власноручний малюнок, рукописний конспект/зошит, схему, розв'язок на папері або фото реального досліду, а учень здав згенероване ШІ зображення чи його скріншот — це СТОВІДСОТКОВЕ використання ШІ ('ai_generated_percent': 100).",
        "",
        "2. АНАЛІЗ ТЕКСТУ ТА РОЗПІЗНАВАННЯ СЕРВІСІВ ОБХОДУ ДЕТЕКТОРІВ (Anti-AI Bypass, «Humanizers», рерайтери, QuillBot, Undetectable AI, StealthWriter, синонімайзери):",
        "   - Учні нерідко пропускають згенерований текст через спеціальні сервіси обходу («хуманізатори») та автоматичні перефразувальники, щоб приховати використання ШІ!",
        "   - Ознаки використання таких сервісів та спроб обману перевірки:",
        "     * Штучна синонімізація: поява незвичних, рідковживаних або стилістично неприродних слів у простих контекстах замість загальноприйнятих шкільних термінів (артефакти синонімайзерів/спинерів);",
        "     * Деформація синтаксису: неприродний порядок слів, штучно розірвані або штучно ускладнені конструкції для штучного підвищення метрик 'burstiness' (варіативність довжини) та 'perplexity' (непередбачуваність слів);",
        "     * Збереження логічного каркаса ШІ: незважаючи на змінені слова, зберігається типова структура нейромережі — однакові за обсягом абзаци, банальні риторичні зачини («У сучасному світі...», «Варто зауважити, що...», «Підсумовуючи зазначене...»), відсутність конкретики, реальних прикладів та живого дитячого стилю;",
        "     * Артефакти перекладу: текст згенеровано англійською, прогнано через сервіс обходу й машино перекладено українською (з калькованими зворотами).",
        "   - ПРАВИЛО ДЛЯ ШІ: Будь-який текст, отриманий шляхом перефразування згенерованого ШІ матеріалу через такі сервіси, ВВАЖАЄТЬСЯ ЗГЕНЕРОВАНИМ ШІ!",
        "",
        "3. АНАЛІЗ ПРОГРАМНОГО КОДУ ТА РОЗВ'ЯЗКІВ:",
        "   - Ознаки ШІ в коді: шаблонні коментарі до кожного елементарного рядка, назви функцій/змінних у стилі Copilot/ChatGPT, використання бібліотек або підходів, які не відповідають шкільній програмі даного класу.",
        "",
        "4. ПРАВИЛА ОЦІНЮВАННЯ ТА ВПЛИВ НА ОЦІНКУ В ЗАЛЕЖНОСТІ ВІД ПОЛІТИКИ ВЧИТЕЛЯ:",
    ]
    if is_ai_allowed:
        ai_check_instructions.extend([
            "   🟢 ПОЛІТИКА: ВЧИТЕЛЬ ДОЗВОЛИВ ВИКОРИСТАННЯ ШІ ДЛЯ ЦЬОГО ЗАВДАННЯ.",
            f"   - Об'єктивно визнач 'ai_generated_percent' (0-100%) та заповни 'ai_generated_details'.",
            "   - НЕ ЗНИЖУЙ ОЦІНКУ учневі виключно за факт використання ШІ!",
            "   - Оцінюй, як учень використав цей інструмент: чи перевірив факти, чи адаптував результат під умову, чи проявив власне розуміння теми."
        ])
    else:
        ai_check_instructions.extend([
            "   🔴 ПОЛІТИКА: ВЧИТЕЛЬ СУВОРО ЗАБОРОНИВ ВИКОРИСТАННЯ ШІ (САМОСТІЙНА РОБОТА).",
            f"   - Якщо частка згенерованого чи переробленого хуманізаторами матеріалу ПЕРЕВИЩУЄ поріг {tolerance_percent}%:",
            "     * Встанови 'ai_generated_detected': true, 'ai_generated_confidence': 'high' або 'medium'.",
            "     * Це є порушенням академічної доброчесності!",
            "     * КАТЕГОРИЧНО ЗАБОРОНЕНО виставляти високі бали (10-12 балів)! Знизь оцінку до початкового рівня (1-3 бали) або признач 'suggested_grade': 'Доопрацювати'.",
            "     * У полях 'weaknesses', 'feedback_comment' та 'summary' чітко й прямо попередь учня: «У завданні встановлено заборону на використання штучного інтелекту. Виявлено використання згенерованого контенту, зображень або сервісів обходу детекції (...%). Роботу необхідно виконати самостійно без використання сторонніх генераторів.»",
            f"   - Якщо частка підозрілого тексту становить {tolerance_percent}% або менше, ВВАЖАЙ РОБОТУ САМОСТІЙНОЮ: 'ai_generated_detected': false, 'ai_generated_confidence': 'none'."
        ])
    ai_check_instructions.extend([
        "",
        "ОБОВ'ЯЗКОВО поверни в JSON поля:",
        "- 'ai_generated_percent': ціле число від 0 до 100 (відсоток матеріалу, що має ознаки ШІ або сервісів обходу)",
        f"- 'ai_generated_detected': true (якщо ШІ > {tolerance_percent}%) або false",
        "- 'ai_generated_confidence': 'none' | 'low' | 'medium' | 'high'",
        "- 'ai_generated_details': детальний висновок українською мовою з поясненням виявлених ознак (зокрема по зображеннях або слідах хуманізаторів) або null."
    ])
    prompt_lines.append("\n".join(ai_check_instructions))
    prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    # ── ПЕРЕВІРКА НА СПІВАВТОРІВ ТА КОЛЕКТИВНУ РОБОТУ ─────────────────────────
    try:
        from .student_matcher import auto_bind_coauthors_from_comment
        auto_bind_coauthors_from_comment(submission)
    except Exception:
        pass

    if submission.is_group_work or submission.group_authors:
        authors_str = submission.group_authors or submission.get_student_full_name()
        prompt_lines.append(
            f"👥 КОЛЕКТИВНА РОБОТА / СПІВАВТОРИ: Роботу виконано спільно командою учнів ({authors_str}). "
            "Оцінюй виконання як командний проєкт, враховуючи спільний внесок."
        )

    # ── ВРАХУВАННЯ КОМЕНТАРЯ УЧНЯ ─────────────────────────────────────────────
    if submission.comment_student and submission.comment_student.strip():
        prompt_lines.append(
            f"💬 ВАЖЛИВИЙ КОМЕНТАР / ПОЯСНЕННЯ УЧНЯ:\n«{submission.comment_student.strip()}»\n"
            "⚠️ ШІ ЗОБОВ'ЯЗАНИЙ УВАЖНО ПРОЧИТАТИ ЦЕЙ КОМЕНТАР: учень міг написати тут важливі пояснення ходу виконання, текстову відповідь до завдання чи інші суттєві деталі."
        )

    prompt_lines.append(f"\nДАНІ УЧНЯ: {submission.get_student_full_name()} ({class_name})")
    prompt_lines.append("ВИКОНАНА РОБОТА УЧНЯ ДЛЯ ОЦІНЮВАННЯ:")
    prompt_lines.extend(text_parts)
    if is_traditional:
        prompt_lines.append(f"\nПроаналізуй роботу за класичною (традиційною) 12-бальною системою ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), unclear_task (true/false), summary, strengths (масив), weaknesses (масив), feedback_comment, ai_generated_percent (число 0-100), ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Поле 'gr_results' поверни порожнім масивом [] або null, оскільки групи результатів НЕ використовуються в класичній системі.")
    else:
        prompt_lines.append(f"\nПроаналізуй роботу згідно з обраними критеріями ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), unclear_task (true/false), summary, strengths (масив), weaknesses (масив), feedback_comment, gr_results (масив об'єктів з code, name, grade, level, comment), ai_generated_percent (число 0-100), ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Усі оцінки обов'язково мають бути цілими числами (без десятих часток), заокругленими на користь учня.")

    if custom_prompt:
        system_instruction = custom_prompt.strip()
    elif selected_preset:
        system_instruction = selected_preset.get_full_prompt().strip()
    else:
        system_instruction = (settings.system_prompt or DEFAULT_NUS_SYSTEM_PROMPT).strip()

    # Завжди гарантуємо правило Scope of Work в системній інструкції
    if "SCOPE OF WORK" not in system_instruction:
        system_instruction += (
            "\n\nПРІОРИТЕТ ВИМОГ ВЧИТЕЛЯ ТА ОБСЯГ ЗАВДАННЯ (SCOPE OF WORK):\n"
            "- Текст у полі «ЗАВДАННЯ ДО ВИКОНАННЯ» від вчителя задає мету, тему та вказівки до уроку.\n"
            "- Прикріплені файли вчителя (презентації .pptx/.pdf, документи, зображення) є повноцінним першоджерелом завдання та навчального контексту уроку.\n"
            "- Якщо вчитель дав короткий або загальний опис («Опрацювати презентацію», «Виконати завдання», «Домашнє завдання у файлі» чи вказав тему), САМЕ У ФАЙЛАХ ВЧИТЕЛЯ (на слайдах чи сторінках) міститься конкретне завдання для учня!\n"
            "- Якщо вчитель вказав виконати тільки одне конкретне завдання (наприклад, завдання 3): оцінюй ВИКЛЮЧНО вказане завдання. КАТЕГОРИЧНО ЗАБОРОНЕНО занижувати бал за «невиконання решти завдань» — вони вважаються незаданими!\n"
            "- Робота вважається виконаною у повному обсязі (100%), якщо якісно виконано саме задане вчителем завдання.\n"
        )

    if "ПРЕЗЕНТАЦІЇ ТА PDF ВЧИТЕЛЯ ЯК ДЖЕРЕЛО ЗАВДАННЯ" not in system_instruction:
        system_instruction += (
            "\n\nПРЕЗЕНТАЦІЇ, PDF, ЗОБРАЖЕННЯ ТА МАТЕРІАЛИ ВЧИТЕЛЯ ЯК ДЖЕРЕЛО ЗАВДАННЯ:\n"
            "- Вчителі часто дають завдання не в тексті опису, а безпосередньо на слайдах презентацій (.pptx/.ppt/.odp/PDF) або в прикріплених файлах/зображеннях (зокрема на фінальних слайдах під заголовками «Домашнє завдання», «Практична робота», «Завдання для закріплення», «Питання для самоперевірки», вправи, завдання 1-3 тощо).\n"
            "- ШІ ЗОБОВ'ЯЗАНИЙ перевірити всі слайди презентації, сторінки PDF та зображення вчителя, щоб знайти формулювання завдання і правильно зрозуміти контекст уроку.\n"
            "- Якщо учень виконав завдання, знайдене на слайдах презентації чи у PDF/файлах вчителя, це завдання є ЧІТКИМ І ЗРОЗУМІЛИМ. КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 'unclear_task': true або повертати на 'Доопрацювати' через «незрозумілість завдання»!\n"
            "- Оцінюй роботу учня (1-12 балів) за повнотою розкриття теми та правильністю розв'язання завдань зі слайдів/матеріалів уроку.\n"
        )

    if "БАГАТОЗАДАЧНІ УМОВИ ТА ПРАВИЛА ВИБОРУ" not in system_instruction:
        system_instruction += (
            "\n\nБАГАТОЗАДАЧНІ УМОВИ ТА ПРАВИЛА ВИБОРУ ЗАВДАНЬ («виконати будь-яке завдання на вибір»):\n"
            "- Якщо вчитель дозволив вибір: спочатку перевір, чи вказав учень номер завдання в коментарі або файлі.\n"
            "- Якщо учень зазначив завдання — оцінюй його без зниження оцінки за вибір.\n"
            "- Якщо учень НЕ зазначив, яке завдання обрав: автоматично визнач завдання за змістом, ОБОВ'ЯЗКОВО вкажи у відгуку, що учень не вказав завдання і це вплинуло на оцінку, та ЗНИЗЬ бал на 1-2.\n"
            "- Якщо НЕ ЗРОЗУМІЛО, яке завдання виконане: постав 'Доопрацювати', 'unclear_task': true, у 'format_warning' напиши: «Не зрозуміло, яке саме завдання виконане. Будь ласка, вкажіть номер завдання в коментарі або перевірте прикріплений файл.»\n"
        )

    if "ТОЧНЕ РОЗУМІННЯ СУТІ ЗАВДАННЯ" not in system_instruction:
        system_instruction += (
            "\n\nТОЧНЕ РОЗУМІННЯ СУТІ ЗАВДАННЯ, ЗМІСТОВА ВІДПОВІДНІСТЬ ТА ЗВОРОТНИЙ ЗВ'ЯЗОК:\n"
            "- Аналізуй, яку саме форму та зміст вимагає завдання (список дат з подіями, твір, таблиця, задачі тощо).\n"
            "- Оцінки 10-12 балів ставляться ВИКЛЮЧНО за повне, змістовне та структуроване виконання завдання. Фрагментарні або мінімальні відповіді (одне речення замість списку дат, картинка з парою слів) категорично не можуть отримувати 10-12 балів (максимум 4-6 балів, або 1-3/'Доопрацювати').\n"
            "- Якщо оцінка менше 10 балів (або 'Доопрацювати'): обов'язково опиши в 'weaknesses' та 'feedback_comment' в загальному, що саме виконано не так і чого не вистачає для досягнення вищого балу.\n"
        )

    prompt_content = "\n".join(prompt_lines)

    # Формуємо ланцюжок спроб: спочатку активний провайдер, потім резервний (failover)
    attempts_configs = []

    # 1. Спроби для активної конфігурації
    if act_provider == 'gemini':
        models_chain = settings.get_active_fallback_chain() if hasattr(settings, 'get_active_fallback_chain') else [act_model]
        models_to_try = [clean_model_name(m) for m in models_chain if m]
        if not models_to_try:
            models_to_try = [clean_model_name(act_model or 'gemini-2.5-flash')]
        for m in models_to_try:
            attempts_configs.append({
                'provider': act_provider,
                'api_key': act_key,
                'model': m,
                'custom_url': act_url,
                'is_backup': is_backup_active
            })
    else:
        attempts_configs.append({
            'provider': act_provider,
            'api_key': act_key,
            'model': act_model or get_default_model_for_provider(act_provider),
            'custom_url': act_url,
            'is_backup': is_backup_active
        })

    # 2. Резервна конфігурація у разі збою / 429 (failover)
    if settings.auto_failover_enabled and settings.has_backup_configured():
        b_prov, b_key, b_model, b_url = settings.get_backup_config()
        if b_key or b_prov == 'custom':
            m_target = b_model or get_default_model_for_provider(b_prov)
            attempts_configs.append({
                'provider': b_prov,
                'api_key': b_key,
                'model': clean_model_name(m_target) if b_prov == 'gemini' else m_target,
                'custom_url': b_url,
                'is_backup': not is_backup_active
            })

    attempted_errors = []

    for cfg_idx, cfg in enumerate(attempts_configs):
        c_provider = cfg['provider']
        c_key = cfg['api_key']
        c_model = cfg['model']
        c_url = cfg['custom_url']
        c_is_backup = cfg['is_backup']

        is_failover_call = (c_is_backup != is_backup_active)
        fallback_happened = is_failover_call or (cfg_idx > 0)
        max_retries = 1 if len(attempts_configs) > 1 else 2

        for attempt in range(max_retries + 1):
            try:
                # Оптимізація швидкодії: для Flash-моделей вимикаємо тривалий ланцюжок роздумів (thinkingBudget=0),
                # що скорочує час очікування відповіді з 25-40 секунд до 2-4 секунд!
                thinking_budget_val = 0 if ('flash' in c_model.lower() and c_provider == 'gemini') else None

                status_code, raw_text, err_msg, raw_data = call_ai_api(
                    prompt_text=prompt_content,
                    system_prompt=system_instruction,
                    inline_media=inline_media,
                    provider=c_provider,
                    api_key=c_key,
                    model_name=c_model,
                    custom_url=c_url,
                    temperature=float(settings.temperature or 0.2),
                    max_output_tokens=4096,
                    timeout=35,
                    json_mode=True,
                    thinking_budget=thinking_budget_val
                )

                if status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue

                if status_code != 200 or not raw_text:
                    if status_code == 429:
                        fail_reason = f"Перевищено ліміт запитів для {c_model} (429 Rate Limit)."
                    elif status_code in (401, 403):
                        fail_reason = f"Недійсний API Key для {c_provider.title()} ({c_model})."
                    else:
                        fail_reason = err_msg or f"Помилка HTTP {status_code}"

                    attempted_errors.append(f"[{c_provider}/{c_model}]: {fail_reason}")
                    break  # Переходимо до наступної моделі / резервного API

                raw_text = raw_text.strip()
                result_json = extract_json_from_text(raw_text)

                model_name = f"{c_model} ({c_provider.title()})" if c_provider != 'gemini' else c_model

                if is_failover_call:
                    try:
                        settings.last_failover_at = timezone.now()
                        settings.last_failover_reason = f"Автоматичне перемикання на резервний {c_provider.title()} ({c_model}). Попередня помилка: {'; '.join(attempted_errors[-2:])}"
                        settings.save(update_fields=['last_failover_at', 'last_failover_reason'])
                    except Exception:
                        pass

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

                    # Визначаємо, чи вдалося ШІ зрозуміти, яке завдання виконано
                    raw_unclear = result_json.get('unclear_task')
                    unclear_task = bool(raw_unclear and str(raw_unclear).lower() not in ['false', '0', 'none', 'null'])

                    fw_lower = (format_warning or '').lower()
                    sum_lower = (summary or '').lower()
                    fb_lower = (feedback_comment or '').lower()

                    if not unclear_task:
                        if ('не зрозуміло' in fw_lower and 'завдан' in fw_lower) or ('незрозуміло' in fw_lower and 'завдан' in fw_lower):
                            unclear_task = True
                        elif ('не зрозуміло' in sum_lower and 'завдан' in sum_lower) or ('незрозуміло' in sum_lower and 'завдан' in sum_lower):
                            unclear_task = True
                        elif ('не зрозуміло, яке саме завдання' in fb_lower) or ('не зрозуміло яке завдання' in fb_lower) or ('незрозуміло, яке завдання' in fb_lower):
                            unclear_task = True

                    if unclear_task:
                        suggested_grade = 'Доопрацювати'
                        if not format_warning or not (('не зрозуміло' in fw_lower or 'незрозуміло' in fw_lower) and 'завдан' in fw_lower):
                            format_warning = "Не зрозуміло, яке саме завдання виконане. Будь ласка, вкажіть номер завдання (наприклад, «Виконував завдання 2») у коментарі до здачі та надішліть роботу повторно."
                        unclear_weakness = "Не зрозуміло, яке саме завдання виконане з наданого списку завдань в умові вчителя (не вказано в роботі чи коментарі)."
                        if unclear_weakness not in weaknesses:
                            weaknesses.insert(0, unclear_weakness)

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
                        is_unclear_task_msg = (('не зрозуміло' in fw_lower or 'незрозуміло' in fw_lower) and 'завдан' in fw_lower)
                        is_technical = any(k in fw_lower for k in technical_keywords) or is_unclear_task_msg
                        has_content_keywords = any(k in fw_lower for k in ['замість', 'людин', 'не та тема', 'не той об\'єкт', 'не відповідає темі', 'інший малюнок', 'інше фото'])

                        if not is_technical or (has_content_keywords and not is_unclear_task_msg and not any(ext in fw_lower for ext in ['.py', '.doc', '.xlsx', '.txt', 'розширен'])):
                            if format_warning not in weaknesses:
                                weaknesses.append(format_warning)
                            format_warning = ''

                    if not format_warning and submission.file and os.path.exists(submission.file.path):
                        f_ext = os.path.splitext(submission.file.path)[1].lower()
                        if not f_ext:
                            format_warning = "Файл прикріплено без розширення (для належної здачі файл потрібно зберігати з відповідним розширенням, наприклад .py для коду)."

                    # Гарантуємо, що при оцінці менше 10 балів або "Доопрацювати" обов'язково є узагальнені зауваження (weaknesses)
                    is_sub_ten = False
                    if suggested_grade == 'Доопрацювати':
                        is_sub_ten = True
                    else:
                        try:
                            if int(suggested_grade) < 10:
                                is_sub_ten = True
                        except (ValueError, TypeError):
                            pass

                    if is_sub_ten and (not weaknesses or not isinstance(weaknesses, list) or len(weaknesses) == 0):
                        if summary:
                            weaknesses = [f"Робота виконана не в повному обсязі або потребує доопрацювання та детальнішого розкриття вимог ({summary})."]
                        else:
                            weaknesses = ["Робота виконана не в повному обсязі або потребує доопрацювання: окремі вимоги завдання виконані лише частково."]

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

                    # Виявлення використання ШІ у роботі з урахуванням порогу толерантності
                    raw_ai_percent = result_json.get('ai_generated_percent')
                    ai_generated_percent = None
                    if raw_ai_percent is not None:
                        try:
                            clean_p_str = str(raw_ai_percent).replace('%', '').strip()
                            ai_generated_percent = int(float(clean_p_str))
                            ai_generated_percent = max(0, min(100, ai_generated_percent))
                        except (ValueError, TypeError):
                            ai_generated_percent = None

                    tolerance = getattr(settings, 'ai_detector_tolerance_percent', 25) or 25
                    raw_ai_detected = result_json.get('ai_generated_detected')
                    ai_generated_detected = bool(raw_ai_detected and raw_ai_detected not in ['false', 'False', 0, '0', 'none', 'null'])

                    # Застосування порогу толерантності (якщо скопійовано лише 1-2 фрази чи відсоток <= допустимого)
                    if ai_generated_percent is not None:
                        if ai_generated_percent <= tolerance:
                            ai_generated_detected = False
                    elif not ai_generated_detected:
                        ai_generated_percent = 0

                    ai_generated_confidence = str(result_json.get('ai_generated_confidence') or 'none').lower().strip()
                    if ai_generated_confidence not in ['none', 'low', 'medium', 'high']:
                        ai_generated_confidence = 'medium' if ai_generated_detected else 'none'
                    if not ai_generated_detected:
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
                    submission.ai_generated_percent = ai_generated_percent
                    submission.ai_model_used = model_name
                    submission.ai_status = 'success'
                    submission.ai_error_reason = ''
                    submission.ai_reviewed_at = timezone.now()
                    submission.save(update_fields=[
                        'ai_suggested_grade', 'ai_score_level', 'ai_feedback', 'ai_gr_results',
                        'ai_generated_detected', 'ai_generated_confidence', 'ai_generated_details',
                        'ai_generated_percent', 'ai_model_used', 'ai_status', 'ai_error_reason', 'ai_reviewed_at'
                    ])

                    return {
                        'status': 'success',
                        'suggested_grade': suggested_grade,
                        'level': level,
                        'format_warning': format_warning,
                        'unclear_task': unclear_task,
                        'summary': summary,
                        'is_traditional': is_traditional,
                        'gr_results': [] if is_traditional else clean_gr_results,
                        'gr_avg': None if is_traditional else avg_gr_grade,
                        'ai_generated_detected': ai_generated_detected,
                        'ai_generated_percent': ai_generated_percent,
                        'ai_generated_confidence': ai_generated_confidence,
                        'ai_generated_details': ai_generated_details,
                        'strengths': strengths,
                        'weaknesses': weaknesses,
                        'feedback_comment': feedback_comment,
                        'feedback': combined_feedback,
                        'clean_feedback': clean_student_feedback,
                        'raw_json': result_json,
                        'model_used': model_name,
                        'fallback_activated': fallback_happened
                    }
                else:
                    from feed.utils import extract_clean_comment_from_raw_json, format_raw_json_feedback_for_display

                    grade_match = re.search(r'(?:suggested_grade["\']?\s*:\s*["\']?|\b)(1[0-2]|[1-9]|Доопрацювати)\b', raw_text, re.IGNORECASE)
                    suggested_grade = grade_match.group(1) if grade_match else 'Доопрацювати'

                    # Гарантуємо, що учням і вчителю не показується сирий JSON
                    clean_feedback = extract_clean_comment_from_raw_json(raw_text)
                    formatted_feedback = format_raw_json_feedback_for_display(raw_text)

                    unclear_task = ('не зрозуміло' in formatted_feedback.lower() and 'завдан' in formatted_feedback.lower()) or ('незрозуміло' in formatted_feedback.lower() and 'завдан' in formatted_feedback.lower())
                    if unclear_task:
                        suggested_grade = 'Доопрацювати'

                    submission.ai_suggested_grade = suggested_grade
                    submission.ai_feedback = formatted_feedback
                    submission.ai_model_used = model_name
                    submission.ai_status = 'success'
                    submission.ai_error_reason = ''
                    submission.ai_reviewed_at = timezone.now()
                    submission.save(update_fields=[
                        'ai_suggested_grade', 'ai_feedback', 'ai_model_used', 'ai_status', 'ai_error_reason', 'ai_reviewed_at'
                    ])
                    return {
                        'status': 'success',
                        'feedback': formatted_feedback,
                        'clean_feedback': clean_feedback,
                        'feedback_comment': clean_feedback,
                        'suggested_grade': suggested_grade,
                        'unclear_task': unclear_task,
                        'format_warning': "Не зрозуміло, яке саме завдання виконане. Будь ласка, вкажіть номер завдання у коментарі до здачі та надішліть роботу повторно." if unclear_task else "",
                        'summary': clean_feedback,
                        'model_used': model_name
                    }

            except Exception as e:
                attempted_errors.append(f"[{c_provider}/{c_model} виняток]: {str(e)}")
                break

    # Якщо всі спроби (включаючи резервний API) зазнали невдачі
    all_err_msg = " | ".join(attempted_errors) if attempted_errors else "Не вдалося отримати відповідь від жодної з налаштованих моделей або резервного API ШІ."
    submission.ai_status = 'failed'
    submission.ai_error_reason = all_err_msg
    submission.save(update_fields=['ai_status', 'ai_error_reason'])
    return {'status': 'failed', 'error': all_err_msg}


def generate_criteria_with_gemini(teacher_notes, assignment_title='', assignment_description='', subject_name='', class_group_name='', custom_model=None):
    """
    Генерує структуровані індивідуальні критерії оцінювання за 12-бальною шкалою НУШ
    на основі побажань вчителя, описаних звичайною мовою, та контексту завдання.
    Підтримує будь-якого налаштованого ШІ-провайдера та резервний API при збоях.
    """
    settings = get_ai_settings()
    if not settings.is_enabled:
        return {'status': 'error', 'message': 'Модуль ШІ вимкнено в налаштуваннях системи.'}

    act_provider, act_key, act_model, act_url, is_backup_active = settings.get_active_config()
    if not act_key and act_provider != 'custom':
        if settings.has_backup_configured():
            b_prov, b_key, b_model, b_url = settings.get_backup_config()
            if b_key or b_prov == 'custom':
                act_provider, act_key, act_model, act_url = b_prov, b_key, b_model, b_url
                is_backup_active = not is_backup_active
        if not act_key and act_provider != 'custom':
            return {'status': 'error', 'message': f'API-ключ для {act_provider.title()} не налаштовано в системі.'}

    teacher_notes = (teacher_notes or '').strip()
    assignment_title = (assignment_title or '').strip()
    assignment_description = (assignment_description or '').strip()
    subject_name = (subject_name or '').strip()
    class_group_name = (class_group_name or '').strip()

    if not teacher_notes and not assignment_description and not assignment_title:
        return {'status': 'error', 'message': 'Будь ласка, опишіть вимоги до завдання або вкажіть тему чи опис.'}

    # Формуємо контекст завдання
    context_lines = []
    if subject_name:
        context_lines.append(f"Предмет: {subject_name}")
    if class_group_name:
        context_lines.append(f"Клас: {class_group_name}")
    if assignment_title:
        context_lines.append(f"Назва/тема завдання: {assignment_title}")
    if assignment_description:
        context_lines.append(f"Текстовий опис/умова завдання від вчителя:\n«{assignment_description}»")

    prompt_parts = [
        "Ти — досвідчений методист української школи та експерт з оцінювання результатів навчання за стандартами Нової української школи (НУШ) та 12-бальної шкали оцінювання.",
        "Вчитель створює завдання та описує своїми словами (звичайною розмовною мовою), що саме вимагається від учнів, на що звернути особливу увагу або які його вимоги до оцінювання.",
        "",
        "КОНТЕКСТ ЗАВДАННЯ:",
        "\n".join(context_lines) if context_lines else "(Тема завдання уточнюється)",
        "",
        "ПОБАЖАННЯ ТА ВИМОГИ ВЧИТЕЛЯ (ОПИС СВОЇМИ СЛОВАМИ):",
        f"«{teacher_notes}»" if teacher_notes else "(Вчитель просить скласти критерії на основі зазначеної теми та опису завдання)",
        "",
        "ТВОЄ ЗАВДАННЯ:",
        "1. Перетвори опис та побажання вчителя у чіткі, зрозумілі, педагогічно вивірені індивідуальні критерії оцінювання за 12-БАЛЬНОЮ ШКАЛОЮ (1–12 балів).",
        "2. Структуруй критерії так, щоб вони були зрозумілими як для учнів (які ознайомляться з ними перед виконанням роботи), так і для ШІ та вчителя при оцінюванні.",
        "3. Формат виводу повинен бути лаконічним і структурованим (наприклад, розподіл балів за компонентами завдання до 12 балів, або за 4 рівнями: Початковий 1–3 б., Середній 4–6 б., Достатній 7–9 б., Високий 10–12 б.).",
        "4. Обов'язково чітко зазнач, що саме необхідно для отримання найвищого балу (10–12 балів), за що оцінка знижується, та які ключові акценти (власні думки, охайність, обґрунтованість тощо).",
        "5. Формулюй українською мовою, діловим, доброзичливим та доступним для школярів тоном.",
        "6. ВАЖЛИВО: Надай ТІЛЬКИ готовий текст критеріїв оцінювання, без жодних мета-вступів (на зразок «Ось критерії:», «Звісно...») та без кінцевих побажань."
    ]

    full_prompt = "\n".join(prompt_parts)

    try:
        temp_val = float(getattr(settings, 'temperature', 0.3) or 0.3)
        temperature = max(0.0, min(1.0, temp_val))
    except (ValueError, TypeError):
        temperature = 0.3

    # Ланцюжок спроб (активний провайдер + резервний)
    attempts_configs = []

    # 1. Активна конфігурація
    if act_provider == 'gemini':
        models_to_try = []
        if custom_model:
            models_to_try.append(clean_model_name(custom_model))
        if hasattr(settings, 'get_active_fallback_chain'):
            for m in settings.get_active_fallback_chain():
                m_clean = clean_model_name(m)
                if m_clean not in models_to_try:
                    models_to_try.append(m_clean)
        if hasattr(settings, 'model_name') and settings.model_name:
            m_clean = clean_model_name(settings.model_name)
            if m_clean not in models_to_try:
                models_to_try.append(m_clean)
        for fallback in ['gemini-2.5-flash', 'gemini-1.5-flash', 'gemini-3.6-flash']:
            if fallback not in models_to_try:
                models_to_try.append(fallback)
        for m in models_to_try:
            attempts_configs.append({
                'provider': act_provider,
                'api_key': act_key,
                'model': m,
                'custom_url': act_url,
                'is_backup': is_backup_active
            })
    else:
        m_chosen = clean_model_name(custom_model) if custom_model else (act_model or get_default_model_for_provider(act_provider))
        attempts_configs.append({
            'provider': act_provider,
            'api_key': act_key,
            'model': m_chosen,
            'custom_url': act_url,
            'is_backup': is_backup_active
        })

    # 2. Резервна конфігурація (якщо налаштована та дозволено failover)
    if settings.auto_failover_enabled and settings.has_backup_configured():
        b_prov, b_key, b_model, b_url = settings.get_backup_config()
        if b_key or b_prov == 'custom':
            m_target = b_model or get_default_model_for_provider(b_prov)
            attempts_configs.append({
                'provider': b_prov,
                'api_key': b_key,
                'model': clean_model_name(m_target) if b_prov == 'gemini' else m_target,
                'custom_url': b_url,
                'is_backup': not is_backup_active
            })

    attempted_errors = []

    for cfg in attempts_configs:
        c_provider = cfg['provider']
        c_key = cfg['api_key']
        c_model = cfg['model']
        c_url = cfg['custom_url']
        c_is_backup = cfg['is_backup']
        is_failover = (c_is_backup != is_backup_active)

        try:
            thinking_budget_val = 0 if ('flash' in c_model.lower() and c_provider == 'gemini') else None
            status_code, raw_text, err_msg, raw_data = call_ai_api(
                prompt_text=full_prompt,
                system_prompt="",
                inline_media=None,
                provider=c_provider,
                api_key=c_key,
                model_name=c_model,
                custom_url=c_url,
                temperature=temperature,
                max_output_tokens=3500,
                timeout=35,
                json_mode=False,
                thinking_budget=thinking_budget_val
            )

            if status_code == 200 and raw_text:
                result_text = raw_text.strip()
                if result_text.startswith('```'):
                    lines = result_text.splitlines()
                    if lines and lines[0].startswith('```'):
                        lines = lines[1:]
                    if lines and lines[-1].startswith('```'):
                        lines = lines[:-1]
                    result_text = "\n".join(lines).strip()

                if result_text:
                    if is_failover:
                        try:
                            settings.last_failover_at = timezone.now()
                            settings.last_failover_reason = f"Автоматичне перемикання на резервний {c_provider.title()} ({c_model}) при генерації критеріїв."
                            settings.save(update_fields=['last_failover_at', 'last_failover_reason'])
                        except Exception:
                            pass

                    model_display = f"{c_model} ({c_provider.title()})" if c_provider != 'gemini' else c_model
                    return {
                        'status': 'success',
                        'criteria': result_text,
                        'model_used': model_display
                    }

            if status_code == 429:
                attempted_errors.append(f"{c_provider}/{c_model}: вичерпано ліміт запитів (429 Rate Limit)")
            else:
                attempted_errors.append(f"{c_provider}/{c_model}: {err_msg or f'Помилка {status_code}'}")

        except Exception as e:
            attempted_errors.append(f"{c_provider}/{c_model}: {str(e)}")

    detail = f" ({'; '.join(attempted_errors)})" if attempted_errors else ""
    return {
        'status': 'error',
        'message': f"Не вдалося згенерувати критерії через тимчасову недоступність моделі ШІ{detail}. Спробуйте ще раз або перевірте налаштування ШІ."
    }

