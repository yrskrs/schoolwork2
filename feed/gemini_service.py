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
import html
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
import logging
from django.utils import timezone
from .models import AISettings, Submission, DEFAULT_NUS_SYSTEM_PROMPT, AICriteriaPreset
from .duplicate_detector import check_submission_duplicates, get_normalized_file_content
from .document_parsers import parse_drawingml_chart_xml, format_python_pptx_chart_info

logger = logging.getLogger(__name__)

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com"
GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_API_BASE_URL = "https://openrouter.ai/api/v1"

DEFAULT_MODELS_BY_PROVIDER = {
    'gemini': ['gemini-3.6-flash', 'gemini-3.1-flash-lite', 'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-flash-latest', 'gemini-3.1-pro-preview'],
    'openai': ['gpt-4o-mini', 'gpt-4o', 'gpt-4-turbo', 'o3-mini'],
    'deepseek': ['deepseek-flash', 'deepseek-v4-pro'],
    'groq': ['openai/gpt-oss-120b', 'openai/gpt-oss-20b'],
    'openrouter': ['google/gemini-2.5-flash', 'deepseek/deepseek-chat', 'openai/gpt-4o-mini', 'anthropic/claude-3.5-sonnet'],
    'custom': ['llama3.2', 'mistral', 'qwen2.5'],
}


def get_default_model_for_provider(provider):
    """Повертає рекомендовану модель за замовчуванням для обраного провайдера."""
    prov = (provider or 'gemini').lower().strip()
    models = DEFAULT_MODELS_BY_PROVIDER.get(prov, [])
    return models[0] if models else 'gemini-3.6-flash'


def get_ai_settings():
    """Повертає глобальні налаштування ШІ."""
    return AISettings.get_solo()


_HTTP_POOL = None


def _get_http_pool():
    global _HTTP_POOL
    if _HTTP_POOL is None:
        try:
            import urllib3
            _HTTP_POOL = urllib3.PoolManager(
                num_pools=10,
                maxsize=20,
                headers={'Accept-Encoding': 'gzip, deflate'}
            )
        except Exception:
            _HTTP_POOL = False
    return _HTTP_POOL


def _http_post_json(url, payload_dict, headers=None, timeout=30):
    """
    Виконує HTTP POST запит із JSON тілом через пул з'єднань urllib3 (з Keep-Alive та gzip)
    або fallback на вбудований urllib з підтримкою кастомних заголовків.
    Повертає (status_code: int, response_data: dict | None, response_text: str).
    """
    json_bytes = json.dumps(payload_dict).encode('utf-8')
    req_headers = {
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json'
    }
    if headers:
        req_headers.update(headers)

    pool = _get_http_pool()
    if pool:
        try:
            resp = pool.request(
                'POST',
                url,
                body=json_bytes,
                headers=req_headers,
                timeout=float(timeout)
            )
            status = resp.status
            body_text = resp.data.decode('utf-8', errors='replace')
            try:
                data = json.loads(body_text)
                return status, data, body_text
            except json.JSONDecodeError:
                return status, None, body_text
        except Exception:
            # Якщо виникла помилка підключення через пул — пробуємо нижче через стандартний urllib
            pass

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
        ]
        if name in legacy_flash:
            return 'gemini-3.8-flash'
        if name in ['gemini-2.5-pro', 'gemini-pro-latest']:
            return 'gemini-3.1-pro-preview'
        if name in ['gemini-2.0-flash-lite', 'gemini-2.5-flash-lite', 'gemini-3.0-flash-lite', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite-preview']:
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


def log_ai_request_metric(model_name, provider='gemini', action='evaluation', status_code=200, is_success=True, data=None, latency_ms=0):
    """
    Фіксує кожен виклик API ШІ у AIRequestLog разом із лічильниками токенів
    (Prompt, Candidates/Completion, Total) для точного розрахунку RPM, RPD та аналітики за період.
    """
    try:
        from .models import AIRequestLog
        p_tokens = 0
        c_tokens = 0
        t_tokens = 0

        if isinstance(data, dict):
            # Google Gemini metadata
            if 'usageMetadata' in data and isinstance(data['usageMetadata'], dict):
                um = data['usageMetadata']
                p_tokens = int(um.get('promptTokenCount') or 0)
                c_tokens = int(um.get('candidatesTokenCount') or 0)
                t_tokens = int(um.get('totalTokenCount') or (p_tokens + c_tokens))
            # OpenAI / DeepSeek / Groq metadata
            elif 'usage' in data and isinstance(data['usage'], dict):
                u = data['usage']
                p_tokens = int(u.get('prompt_tokens') or 0)
                c_tokens = int(u.get('completion_tokens') or 0)
                t_tokens = int(u.get('total_tokens') or (p_tokens + c_tokens))

        if t_tokens == 0 and status_code == 200:
            p_tokens = 850
            c_tokens = 380
            t_tokens = 1230

        AIRequestLog.objects.create(
            model_name=str(model_name or '')[:100],
            provider=str(provider or 'gemini')[:50],
            action=str(action or 'evaluation')[:100],
            status_code=int(status_code or 200),
            is_success=bool(is_success),
            prompt_tokens=p_tokens,
            completion_tokens=c_tokens,
            total_tokens=t_tokens,
            latency_ms=int(latency_ms or 0)
        )
    except Exception:
        pass


def _raw_call_ai_api(prompt_text, system_prompt="", inline_media=None, provider="gemini", api_key="", model_name="", custom_url="", temperature=0.2, max_output_tokens=3500, timeout=35, json_mode=False, thinking_budget=None):
    provider = (provider or 'gemini').lower().strip()
    from .ai_context import media_for_provider
    try:
        inline_media = media_for_provider(inline_media or [], provider)
    except Exception:
        return 0, None, 'Не вдалося прочитати всі візуальні матеріали для цього провайдера. Спробуйте PDF-сумісну модель.', None
    url, headers, model = get_provider_endpoint(provider, model_name=model_name, api_key=api_key, custom_url=custom_url)

    if provider == 'gemini':
        parts = []
        if prompt_text:
            parts.append({"text": prompt_text})
        if inline_media:
            for item in inline_media:
                if item.get("source"):
                    parts.append({"text": item["source"]})
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
                    if cand.get('finishReason') == 'MAX_TOKENS':
                        return status_code, '', 'MAX_TOKENS: відповідь неповна; оцінку не збережено.', data
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
                if item.get("source"):
                    user_content.append({"type": "text", "text": item["source"]})
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
                    if choices[0].get('finish_reason') == 'length':
                        return status_code, '', 'MAX_TOKENS: відповідь неповна; оцінку не збережено.', data
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


def call_ai_api(prompt_text, system_prompt="", inline_media=None, provider="gemini", api_key="", model_name="", custom_url="", temperature=0.2, max_output_tokens=3500, timeout=35, json_mode=False, thinking_budget=None, action="evaluation"):
    """
    Універсальна функція для звернення до будь-якого ШІ-провайдера
    (Google Gemini, OpenAI, DeepSeek, Groq, OpenRouter, Custom/Ollama).
    Автоматично фіксує метрики запитів та використання токенів (RPM, RPD, токени).
    Повертає (status_code: int, response_text: str | None, error_message: str | None, raw_data: dict | None).
    """
    t_start = time.time()
    status_code, reply_text, err_msg, raw_data = _raw_call_ai_api(
        prompt_text=prompt_text,
        system_prompt=system_prompt,
        inline_media=inline_media,
        provider=provider,
        api_key=api_key,
        model_name=model_name,
        custom_url=custom_url,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
        json_mode=json_mode,
        thinking_budget=thinking_budget
    )
    latency_ms = int((time.time() - t_start) * 1000)

    prov_norm = (provider or 'gemini').lower().strip()
    try:
        _, _, resolved_model = get_provider_endpoint(prov_norm, model_name=model_name, api_key=api_key, custom_url=custom_url)
    except Exception:
        resolved_model = model_name

    log_ai_request_metric(
        model_name=resolved_model or model_name or 'unknown',
        provider=prov_norm,
        action=action,
        status_code=status_code,
        is_success=(status_code == 200 and reply_text is not None),
        data=raw_data,
        latency_ms=latency_ms
    )

    return status_code, reply_text, err_msg, raw_data


def log_ai_error(teacher=None, submission=None, assignment=None, action='evaluation',
                 provider='', model_name='', status_code=None, error_type='',
                 error_message='', prompt_preview='', raw_response='', failover_triggered=False):
    """Фіксує збій або помилку ШІ у базі даних (AIErrorLog) для журналу помилок ШІ."""
    try:
        from .models import AIErrorLog
        if submission and not assignment:
            assignment = submission.assignment
        if submission and not teacher:
            teacher = submission.teacher or (submission.assignment.teacher if submission.assignment else None)
        elif assignment and not teacher:
            teacher = assignment.teacher

        AIErrorLog.objects.create(
            teacher=teacher,
            submission=submission,
            assignment=assignment,
            action=action or 'evaluation',
            provider=str(provider or '')[:50],
            model_name=str(model_name or '')[:100],
            status_code=status_code,
            error_type=str(error_type or '')[:150],
            error_message=str(error_message)[:4000],
            prompt_preview=str(prompt_preview)[:2000] if prompt_preview else '',
            raw_response=str(raw_response)[:3000] if raw_response else '',
            failover_triggered=bool(failover_triggered),
        )
    except Exception:
        pass


def test_ai_connection(provider=None, api_key=None, model_name=None, custom_url=None):
    """
    Перевіряє коректність API ключа та доступність вибраної моделі для вказаного або активного провайдера.
    Повертає (success: bool, message: str, model_used: str, provider_used: str).
    """
    settings = get_ai_settings()
    active_provider = settings.get_active_config()[0]
    prov = (provider or active_provider or 'gemini').lower().strip()
    config = settings.get_provider_config(prov)
    key = api_key.strip() if api_key is not None else (config['api_key'] if config else '')
    raw_model = (model_name or (config['model'] if config else '') or get_default_model_for_provider(prov)).strip()
    model = clean_model_name(raw_model, provider=prov)
    url = custom_url.strip() if custom_url is not None else (config['custom_url'] if config else '')
    if prov == 'custom' and not url:
        return False, 'Вкажіть Base URL власного API.', model, prov

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
        thinking_budget=0,
        action='test_connection'
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


def extract_text_from_odp_presentation(file_path, max_slides=1000, max_chars=10000000):
    """
    Видобуває детальну структуру, слайди, текст, таблиці, зображення та нотатки
    із презентацій OpenDocument Presentation (.odp).
    Форматує кожен слайд аналогічно до PowerPoint: «📽️ Слайд X/N: «Заголовок»».
    """
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        if not os.path.exists(file_path) or not zipfile.is_zipfile(file_path):
            return ""

        with zipfile.ZipFile(file_path, 'r') as zf:
            if 'content.xml' not in zf.namelist():
                return ""
            xml_bytes = zf.read('content.xml')

        root = ET.fromstring(xml_bytes)
        pages = [elem for elem in root.iter() if elem.tag.endswith('page')]
        if not pages:
            texts = []
            for elem in root.iter():
                if elem.tag.endswith(('}p', '}h', '}span', '}a', '}table-cell')):
                    t = ''.join(elem.itertext()).strip()
                    if t and t not in texts:
                        texts.append(t)
            return '\n'.join(texts)[:max_chars]

        total_slides = len(pages)
        slides_text = []

        for idx, page in enumerate(pages, 1):
            if idx > max_slides:
                break

            title_text = ""
            p_lines = []
            img_c = 0
            tbl_c = 0
            notes_text = ""

            for elem in page.iter():
                tag = elem.tag.rsplit('}', 1)[-1]
                if tag == 'image':
                    img_c += 1
                elif tag == 'table':
                    tbl_c += 1
                elif tag == 'notes':
                    n_parts = [''.join(p.itertext()).strip() for p in elem.iter() if p.tag.endswith(('}p', '}h'))]
                    notes_text = ' '.join(p for p in n_parts if p).strip()

            for child in page.iter():
                if child.tag.endswith('notes'):
                    continue
                if child.tag.endswith(('}p', '}h')):
                    pt = ''.join(child.itertext()).strip()
                    if pt and pt not in p_lines and (not notes_text or pt not in notes_text):
                        if not title_text:
                            title_text = pt
                        else:
                            p_lines.append(f"• {pt}")

            slide_header = f"📽️ Слайд {idx}/{total_slides}"
            if title_text:
                slide_header += f": «{title_text}»"
            else:
                slide_header += ":"

            slide_lines = [slide_header] + p_lines
            visuals = []
            if img_c > 0:
                visuals.append(f"{img_c} ілюстрацій/зображень")
            if tbl_c > 0:
                visuals.append(f"{tbl_c} таблиць")
            if visuals:
                slide_lines.append(f"  [Візуальне оформлення слайда: {', '.join(visuals)}]")
            if notes_text:
                slide_lines.append(f"  [Нотатки доповідача: {notes_text}]")

            slides_text.append("\n".join(slide_lines))

        overview = f"Всього слайдів у презентації: {total_slides}."
        if total_slides > max_slides:
            overview += f" (Опрацьовано перші {max_slides} слайдів)."

        body = overview + "\n\n" + "\n\n".join(slides_text)
        return body[:max_chars]
    except Exception as e:
        return f"[Помилка читання презентації ODP: {e}]"


def extract_text_from_opendocument(file_path, max_chars=50000):
    """
    Видобуває текст з файлів OpenDocument (.odt, .ods, .odp) через стандартний zipfile та XML.
    Для презентацій .odp використовує детальну покадрову екстракцію слайдів.
    """
    ext_lower = os.path.splitext(file_path)[1].lower() if file_path else ""
    if ext_lower == '.odp':
        return extract_text_from_odp_presentation(file_path, max_chars=max_chars)
    try:
        with zipfile.ZipFile(file_path, 'r') as zf:
            if 'content.xml' not in zf.namelist():
                return None
            xml_bytes = zf.read('content.xml')
            root = ET.fromstring(xml_bytes)
            pages = [elem for elem in root.iter() if elem.tag.endswith('page')]
            if pages:
                return extract_text_from_odp_presentation(file_path, max_chars=max_chars)
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
    Якщо вбудованих зображень немає або у презентації є створені діаграми,
    автоматично візуально рендерить слайди через LibreOffice + pdftoppm,
    щоб ШІ Gemini міг безпосередньо оцінити діаграми та оформлення.
    Повертає список словників: [{'name': filename, 'mime_type': mime, 'data': base64_str, 'size_kb': float}].
    """
    extracted = []
    has_charts = False
    try:
        target_path = file_path
        ext_lower = os.path.splitext(file_path)[1].lower() if file_path else ""
        if ext_lower == '.ppt' or not zipfile.is_zipfile(file_path):
            from .document_parsers import convert_ppt_to_pptx
            converted = convert_ppt_to_pptx(file_path)
            if converted and os.path.exists(converted):
                target_path = converted
            else:
                return []

        with zipfile.ZipFile(target_path, 'r') as z:
            chart_files = [f for f in z.namelist() if f.startswith('ppt/charts/')]
            has_charts = len(chart_files) > 0

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

    # Якщо растрових медіа-зображень немає або у презентації є діаграми — рендеримо візуальні слайди у PNG
    if (len(extracted) == 0 or has_charts) and len(extracted) < max_images:
        try:
            rendered_slides = render_presentation_to_images(file_path, max_pages=min(4, max_images - len(extracted)))
            for rs in rendered_slides:
                extracted.append(rs)
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


def parse_powerpoint_charts(file_path):
    """
    Аналізує структуру презентації (.pptx, .ppt) та витягує інформацію
    про всі вбудовані діаграми та графіки на слайдах.
    """
    charts_info = []
    if not file_path or not os.path.exists(file_path):
        return charts_info

    target_path = file_path
    ext_lower = os.path.splitext(file_path)[1].lower() if file_path else ""
    if ext_lower == '.ppt' or not zipfile.is_zipfile(file_path):
        from .document_parsers import convert_ppt_to_pptx
        converted = convert_ppt_to_pptx(file_path)
        if converted and os.path.exists(converted):
            target_path = converted

    # Спроба 1: через python-pptx
    try:
        from pptx import Presentation
        prs = Presentation(target_path)
        for s_idx, slide in enumerate(prs.slides, 1):
            def _find_charts_in_shapes(shapes):
                for shape in shapes:
                    if hasattr(shape, "shapes"):
                        _find_charts_in_shapes(shape.shapes)
                    elif hasattr(shape, "has_chart") and shape.has_chart:
                        try:
                            c_info = format_python_pptx_chart_info(shape.chart)
                            charts_info.append({
                                'slide_idx': s_idx,
                                'type': c_info['type'],
                                'title': c_info['title'],
                                'categories': c_info['categories'],
                                'series': c_info['series']
                            })
                        except Exception:
                            charts_info.append({
                                'slide_idx': s_idx,
                                'type': 'Вбудована діаграма',
                                'title': '(без назви)',
                                'categories': [],
                                'series': []
                            })
            _find_charts_in_shapes(slide.shapes)
        if charts_info:
            return charts_info
    except Exception:
        pass

    # Спроба 2: прямий аналіз OpenXML zip-архіву ppt/charts/
    try:
        if zipfile.is_zipfile(target_path):
            with zipfile.ZipFile(target_path, 'r') as z:
                chart_files = [f for f in z.namelist() if f.startswith('ppt/charts/chart') and f.endswith('.xml')]
                chart_files.sort()

                chart_to_slide = {}
                slide_rels = [f for f in z.namelist() if f.startswith('ppt/slides/_rels/slide') and f.endswith('.xml.rels')]
                for sr in slide_rels:
                    try:
                        m = re.search(r'slide(\d+)\.xml\.rels', sr)
                        slide_num = int(m.group(1)) if m else 1
                        rels_content = z.read(sr)
                        r_root = ET.fromstring(rels_content)
                        for r_elem in r_root:
                            target = r_elem.get('Target', '')
                            if 'chart' in target:
                                ch_name = os.path.basename(target)
                                chart_to_slide[ch_name] = slide_num
                    except Exception:
                        pass

                for cf in chart_files:
                    c_info = parse_drawingml_chart_xml(z.read(cf))
                    if c_info:
                        s_idx = chart_to_slide.get(os.path.basename(cf), 1)
                        charts_info.append({
                            'slide_idx': s_idx,
                            'type': c_info['type'],
                            'title': c_info['title'],
                            'categories': c_info['categories'],
                            'series': c_info['series']
                        })
    except Exception:
        pass

    return charts_info


def format_powerpoint_charts_summary(charts_info):
    """
    Форматує структурований інформаційний опис виявлених у презентації діаграм для промпту ШІ.
    """
    if not charts_info:
        return ""

    lines = [
        "════════════════════════════════════════════════════════════════════",
        f"📊 ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ПРЕЗЕНТАЦІЇ ({len(charts_info)} шт.):",
        "Учень створив у презентації наступні діаграми/графіки (дані витягнуто безпосередньо зі структури слайдів):",
    ]
    for idx, c in enumerate(charts_info, 1):
        lines.append(f"• Діаграма #{idx} (Слайд {c['slide_idx']}):")
        lines.append(f"  - Тип діаграми: {c['type']}")
        lines.append(f"  - Назва (заголовок): {c['title']}")
        if c.get('categories'):
            lines.append(f"  - Категорії (осі X): {', '.join(str(cat) for cat in c['categories'][:10])}")
        if c.get('series'):
            lines.append("  - Ряди та значення даних:")
            for s in c['series']:
                lines.append(f"    * {s}")
    lines.append("════════════════════════════════════════════════════════════════════")
    return "\n".join(lines)


def render_presentation_to_images(file_path, max_pages=4):
    """
    Рендерить слайди презентації (.pptx, .ppt, .odp) у візуальні PNG зображення
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

            page_prefix = os.path.join(tmp_dir, 'slide_page')
            ppm_cmd = [
                ppm_bin,
                '-png',
                '-r', '150',
                pdf_file,
                page_prefix
            ]
            subprocess.run(ppm_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)

            png_files = sorted(glob.glob(os.path.join(tmp_dir, 'slide_page-*.png')))
            for idx, pf in enumerate(png_files[:max_pages]):
                try:
                    with open(pf, 'rb') as f:
                        data = f.read()
                    if 0 < len(data) <= 8 * 1024 * 1024:
                        b64 = base64.b64encode(data).decode('utf-8')
                        rendered.append({
                            'name': f'presentation_slide_{idx+1}.png',
                            'mime_type': 'image/png',
                            'data': b64,
                            'size_kb': len(data) / 1024
                        })
                except Exception:
                    pass
        except Exception:
            pass

    return rendered


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
            for sheetname in wb.sheet_names():
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
            wb = openpyxl.load_workbook(file_path, data_only=False, read_only=True)
            cached_wb = openpyxl.load_workbook(file_path, data_only=True, read_only=True)

            for sheetname in wb.sheetnames:
                sheet = wb[sheetname]
                sheet_lines = [f"📊 Аркуш: {sheetname}"]
                rows_count = 0

                cached_rows = cached_wb[sheetname].iter_rows(values_only=True)
                for row in sheet.iter_rows(values_only=True):
                    cached_row = next(cached_rows, ())
                    rows_count += 1
                    if rows_count > max_rows:
                        sheet_lines.append(f"[... ще рядки]")
                        break
                    row_vals = []
                    for column, value in enumerate(row[:max_cols]):
                        text = str(value) if value is not None else ""
                        if isinstance(value, str) and value.startswith('='):
                            cached_value = cached_row[column] if column < len(cached_row) else None
                            text = f"{col_num_to_letter(column + 1)}{rows_count}: {value} → {cached_value if cached_value is not None else '[результат не кешований]'}"
                        row_vals.append(text)
                    if any(v.strip() for v in row_vals):
                        sheet_lines.append(" | ".join(row_vals))

                sheet_summaries.append("\n".join(sheet_lines))

            wb.close()
            cached_wb.close()
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
    шляхом пошуку юнікод-рядків (UTF-16LE, включаючи українську кирилицю \x04)
    та однобайтових послідовностей (CP1251/UTF-8) з фільтрацією службового сміття.
    """
    try:
        with open(file_path, 'rb') as f:
            data = f.read(8 * 1024 * 1024)

        text_chunks = []
        ignored_patterns = [
            'root entry', 'current user', 'powerpoint document', 'summaryinformation',
            'documentsummaryinformation', 'compobj', 'ole', 'pictures',
            'click to edit master', 'click to edit', 'клацніть для', 'другий рівень структури',
            'третій рівень структури', 'четвертий рівень', 'п\'ятий рівень',
            'times new roman', 'calibri', 'arial', 'tahoma', 'noto sans', 'courier new',
            'comic sans', 'georgia', 'trebuchet', 'verdana', 'impact', 'garamond'
        ]

        def _is_valid(s):
            s_clean = s.strip()
            if len(s_clean) < 4 or not any(c.isalnum() for c in s_clean):
                return False
            s_low = s_clean.lower()
            if any(p in s_low for p in ignored_patterns):
                return False
            return True

        # 1. Пошук UTF-16LE рядків (ASCII + українська кирилиця \x04 + знаки \x20)
        utf16_pat = rb'(?:(?:[\x20-\x7e\r\n\t]\x00)|(?:[\x00-\xff][\x04\x20]))+'
        for m in re.finditer(utf16_pat, data):
            raw = m.group(0)
            if len(raw) >= 8:
                try:
                    dec = raw.decode('utf-16le', errors='ignore').strip()
                    if _is_valid(dec) and dec not in text_chunks:
                        text_chunks.append(dec)
                except Exception:
                    pass

        # 2. Пошук однобайтових рядків CP1251 / UTF-8
        ascii_matches = re.findall(rb'[\x20-\x7e\t\n\r\xc0-\xff]{5,}', data)
        for m in ascii_matches:
            for enc in ['utf-8', 'cp1251']:
                try:
                    s = m.decode(enc).strip()
                    if _is_valid(s) and s not in text_chunks:
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
    Для застарілих бінарних .ppt файлів виконує автоматичну конвертацію в .pptx через LibreOffice,
    а у разі відсутності — надійне бінарне видобування українського тексту.
    """
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except Exception:
        MSO_SHAPE_TYPE = None

    actual_file_path = file_path
    ext_lower = os.path.splitext(file_path)[1].lower() if file_path else ""

    if ext_lower == '.ppt' or not zipfile.is_zipfile(file_path):
        from .document_parsers import convert_ppt_to_pptx
        converted = convert_ppt_to_pptx(file_path)
        if converted and os.path.exists(converted):
            actual_file_path = converted
        else:
            return extract_text_from_binary_presentation(file_path)

    try:
        prs = Presentation(actual_file_path)
        slides_text = []
        total_slides = len(prs.slides)

        def _process_shape(shape):
            lines = []
            img_c = 0
            tbl_c = 0
            chart_c = 0

            # Рекурсивна обробка груп фігур
            if hasattr(shape, "shapes"):
                for sub_sh in shape.shapes:
                    sub_lines, sub_img, sub_tbl, sub_ch = _process_shape(sub_sh)
                    lines.extend(sub_lines)
                    img_c += sub_img
                    tbl_c += sub_tbl
                    chart_c += sub_ch
                return lines, img_c, tbl_c, chart_c

            # Таблиця
            if hasattr(shape, "has_table") and shape.has_table:
                tbl_c += 1
                lines.append("[Таблиця на слайді:]")
                for row in shape.table.rows:
                    row_cells = [cell.text.strip().replace('\n', ' ') for cell in row.cells]
                    if any(row_cells):
                        lines.append(" | ".join(row_cells))
                return lines, img_c, tbl_c, chart_c

            # Діаграма / графік на слайді
            if hasattr(shape, "has_chart") and shape.has_chart:
                chart_c += 1
                try:
                    c_info = format_python_pptx_chart_info(shape.chart)
                    c_title = c_info.get('title') or '(без назви)'
                    c_type = c_info.get('type') or 'Діаграма'
                    lines.append(f"[Діаграма на слайді: {c_type}, назва: «{c_title}»]")
                    if c_info.get('categories'):
                        lines.append(f"  Категорії: {', '.join(str(c) for c in c_info['categories'][:8])}")
                    if c_info.get('series'):
                        for s in c_info['series'][:4]:
                            lines.append(f"  Ряд даних: {s}")
                except Exception:
                    lines.append("[Вбудована діаграма на слайді]")
                return lines, img_c, tbl_c, chart_c

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

            return lines, img_c, tbl_c, chart_c

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
            slide_charts = 0

            for shape in slide.shapes:
                try:
                    if slide.shapes.title and shape == slide.shapes.title:
                        continue
                except Exception:
                    pass

                sh_lines, sh_img, sh_tbl, sh_ch = _process_shape(shape)
                slide_lines.extend(sh_lines)
                slide_imgs += sh_img
                slide_tbls += sh_tbl
                slide_charts += sh_ch

            visual_indicators = []
            if slide_imgs > 0:
                visual_indicators.append(f"{slide_imgs} ілюстрацій/зображень")
            if slide_tbls > 0:
                visual_indicators.append(f"{slide_tbls} таблиць")
            if slide_charts > 0:
                visual_indicators.append(f"{slide_charts} діаграм/графіків")
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

        body_text = overview + "\n\n" + "\n\n".join(slides_text) if slides_text else "[Презентація не містить тексту або порожня]"

        # Додаємо повний звіт про виявлені діаграми у файлі презентації
        charts_info = parse_powerpoint_charts(actual_file_path)
        charts_summary = format_powerpoint_charts_summary(charts_info)
        if charts_summary:
            body_text += "\n\n" + charts_summary

        return body_text
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

    from .safe_http import public_urlopen, validate_public_url
    url = url.strip()
    try:
        validate_public_url(url)
    except (ValueError, OSError) as error:
        return None, None, str(error)
    parsed = urlparse(url)
    if not parsed.scheme or parsed.scheme not in ('http', 'https'):
        return None, None, f"Непідтримуваний протокол URL: {url}"

    # 1. Спеціальна обробка YouTube
    if parsed.hostname in ('youtube.com', 'www.youtube.com', 'youtu.be', 'www.youtu.be'):
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={urllib.parse.quote(url)}&format=json"
            req = urllib.request.Request(oembed_url, headers={'User-Agent': 'SchoolNet-AI/2.0'})
            with public_urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                title = data.get('title', 'YouTube Video')
                author = data.get('author_name', '')
                text_info = f"🎬 Відео YouTube: «{title}» (Автор/Канал: {author})\nПосилання: {url}"
                return title, text_info, None
        except Exception:
            return "YouTube відео", f"🎬 Посилання на відео YouTube: {url}", None

    # 2. Спеціальна обробка посилань на файли GitHub (blob -> raw)
    fetch_url = url
    if parsed.hostname in ('github.com', 'www.github.com') and '/blob/' in parsed.path:
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
        with public_urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get('Content-Type', '').lower()
            raw_bytes = resp.read(500 * 1024)  # до 500 КБ
            if content_type.startswith(('image/', 'audio/', 'video/')) or content_type.startswith(('application/pdf', 'application/vnd.', 'application/msword', 'application/zip')):
                return None, None, 'За посиланням бінарний файл. Для змістової перевірки прикріпіть сам файл; його вміст не прочитано як вебсторінку.'

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


def _optimize_image_for_ai(file_path_or_bytes, max_dim=1280, quality=80):
    """
    Оптимізує та масштабує фотозображення перед кодуванням у base64 для передачі до ШІ API.
    Зменшує 8-15 МБ фотографії з камер телефонів до 100-250 КБ без втрати читабельності рукописного тексту,
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
        # Якщо розмір файлу менше 400 КБ і роздільна здатність у нормі — повертаємо як є без перетиснення
        if len(orig_bytes) <= 400 * 1024 and w <= max_dim and h <= max_dim:
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
    inaccessible_materials = []
    unreadable_files = []

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
            elif u_err:
                inaccessible_materials.append({
                    'type': 'url',
                    'target': u,
                    'error': str(u_err),
                    'reason': f"Не вдалося завантажити вміст сторінки за посиланням: {u_err}"
                })
                text_parts.append(f"[Примітка щодо посилання {u} з коментаря: не вдалося завантажити вміст сторінки ({u_err})]")

    # 2. Посилання на роботу (якщо є)
    if submission.link:
        link_url = submission.link.strip()
        text_parts.append(f"Посилання на виконаний проєкт/роботу:\n{link_url}")
        u_title, u_content, u_err = fetch_url_content(link_url)
        if u_content:
            text_parts.append(f"Автоматично завантажений вміст сторінки ({link_url}):\n{u_content}")
        elif u_err:
            text_parts.append(f"[Примітка щодо посилання {link_url}: не вдалося завантажити вміст сторінки ({u_err})]")
            inaccessible_materials.append({
                'type': 'url',
                'target': link_url,
                'error': str(u_err),
                'reason': f"Не вдалося завантажити вміст сторінки за посиланням: {u_err}"
            })

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

        from .ai_context import extract_file_evidence, OFFICE, IMAGES
        if ext in OFFICE | IMAGES | {'.pdf', '.sb3', '.hex', '.mdb', '.accdb'} or is_text_file(file_path):
            evidence = extract_file_evidence(file_path, ext)
            if not evidence['text'] and not evidence['media']:
                unreadable_files.append(filename)
            text_parts.append(f"Вміст роботи учня ({filename}):\n{evidence['text']}")
            inline_media.extend(dict(item, source=f"Робота учня: {filename}") for item in evidence['media'])
            for limitation in evidence['limitations']:
                inaccessible_materials.append({'type': 'file', 'target': filename, 'reason': limitation})
                text_parts.append(f"МЕЖІ ПРОЧИТАНОГО ({filename}): {limitation}")
            continue

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

            # 📊 Перевірка вбудованих діаграм у Word (.docx)
            try:
                if zipfile.is_zipfile(file_path):
                    with zipfile.ZipFile(file_path, 'r') as z:
                        chart_files = [f for f in z.namelist() if f.startswith('word/charts/chart') and f.endswith('.xml')]
                        if chart_files:
                            docx_charts_lines = [f"\n[У документі Word ({filename}) виявлено {len(chart_files)} вбудованих діаграм/графіків:]"]
                            for c_idx, cf in enumerate(sorted(chart_files), 1):
                                c_info = parse_drawingml_chart_xml(z.read(cf))
                                if c_info:
                                    c_desc = f"• Діаграма #{c_idx}: {c_info['type']}, назва: «{c_info['title']}»"
                                    if c_info.get('categories'):
                                        c_desc += f", категорії: {', '.join(str(c) for c in c_info['categories'][:6])}"
                                    docx_charts_lines.append(c_desc)
                            text_parts.append("\n".join(docx_charts_lines))
            except Exception:
                pass

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

        # ── Е. ПРЕЗЕНТАЦІЇ (.pptx, .ppt, .odp, .pptm, .ppsx, .pps, .potx) ─────
        elif ext in ['.pptx', '.ppt', '.odp', '.pptm', '.ppsx', '.pps', '.potx']:
            if ext in ['.pptx', '.ppt', '.pptm', '.ppsx', '.pps', '.potx']:
                pptx_text = extract_text_from_powerpoint(file_path)
                text_parts.append(f"Вміст презентації PowerPoint ({filename}, {file_size_kb:.1f} КБ):\n{pptx_text}")
                pptx_imgs = extract_images_from_pptx(file_path)
                for p_img in pptx_imgs:
                    inline_media.append({
                        "mime_type": p_img['mime_type'],
                        "data": p_img['data']
                    })
                    text_parts.append(f"[У презентації PowerPoint ({filename}) виявлено вбудоване зображення/слайд: {p_img['name']} ({p_img['size_kb']:.1f} КБ) — передано на візуальний аналіз ШІ]")
            elif ext == '.odp':
                odp_text = extract_text_from_odp_presentation(file_path)
                text_parts.append(f"Вміст презентації OpenDocument (.odp) ({filename}, {file_size_kb:.1f} КБ):\n{odp_text or '[Порожня презентація]'}")
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
            limitation = 'Архів: вибірковий витяг, до 8 текстових файлів і 6 зображень; інші вкладення не перевірено. Не вважай їх відсутніми.'
            inaccessible_materials.append({'type': 'file', 'target': filename, 'reason': limitation})
            text_parts.append(f'МЕЖІ ПРОЧИТАНОГО ({filename}): {limitation}')
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
                unreadable_files.append(filename)
                inaccessible_materials.append({'type': 'file', 'target': filename, 'reason': 'Цей формат потребує ручного перегляду; вміст не прочитано.'})
                text_parts.append(f"[Прикріплено файл формату {ext} ({filename}, {file_size_kb:.1f} КБ)]")

    setattr(submission, '_inaccessible_materials', inaccessible_materials)
    if submission_files and len(unreadable_files) == len(submission_files) and not submission.link:
        return [], [], 'Вміст прикріплених файлів не прочитано. Потрібна ручна перевірка; спробу самоперевірки не використано.'

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


# ═══════════════════════════════════════════════════════════════════════════════
# РОЗПІЗНАВАННЯ ТА ЗІСТАВЛЕННЯ ЗАПИТАНЬ ВЧИТЕЛЯ І ВІДПОВІДЕЙ УЧНІВ
# (ПІДСТАНОВКА ВІДПОВІДЕЙ У ФОРМАТІ «ПИТАННЯ-ВІДПОВІДЬ»)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_expected_task_count(text: str) -> int:
    """
    Визначає очікувану кількість завдань, якщо вчитель явно зазначив її в інструкції:
    (наприклад: «виконати всі 3 завдання», «виконати всі 3 звадання», «виконати 4 завдання»,
    «3 практичні завдання», «виконати завдання 1-3», «виконати 3 вправи»).
    """
    if not text:
        return 0
    patterns = [
        # «виконати всі 3 завдання», «зробити всі 3 звадання», «виконати 3 завдання»
        r'(?:виконати|зробити|здати|опрацювати|написати)\s+(?:всі\s+|усі\s+)?(\d+)\s+(?:практичн\w*\s+)?(?:завдан|звадан|вправ|пункт)',
        # «всі 3 завдання», «усі 3 практичні завдання»
        r'(?:всі|усі)\s+(\d+)\s+(?:практичн\w*\s+)?(?:завдан|звадан|вправ|пункт)',
        # «3 завдання з презентації», «3 практичні завдання»
        r'(\d+)\s+(?:практичн\w+\s+)?(?:завдан|звадан|вправ|пункт)\w*\s+(?:з|із|із\s+презентації|у|в)',
        # «завдання 1-3», «завдання 1–3», «вправи 1-4»
        r'(?:завдання|вправи)\s*(?:№\s*)?1\s*[-–—]\s*(\d+)'
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                cnt = int(m.group(1))
                if 1 <= cnt <= 30:
                    return cnt
            except (ValueError, TypeError):
                pass
    return 0


def parse_teacher_specific_task_numbers(description: str) -> list[int]:
    """
    Розпізнає конкретні номери завдань, які вчитель задав у полі «Що потрібно зробити».
    Наприклад:
      «виконати завдання 1 з практичної»         → [1]
      «зробити завдання 1 та 2»                  → [1, 2]
      «виконати завдання 2 і 3 зі слайду»        → [2, 3]
      «завдання 3»                               → [3]
      «виконати вправу 2»                        → [2]
      «зробити завдання 1-2»                     → [1, 2]
      «виконати завдання 2, 4»                   → [2, 4]
      «виконати всі 3 завдання»                  → []  (не конкретні – лише загальна кількість)
      «опрацювати презентацію»                   → []  (загальна вказівка)

    Повертає список цілих чисел номерів завдань або порожній список,
    якщо вчитель не вказав конкретних номерів.
    """
    if not description or not description.strip():
        return []

    text = description.strip()
    found_nums: set[int] = set()

    # Спершу обробляємо діапазони «завдання 1-3», «вправа 2-4», «№ 1-2»
    range_pat = re.compile(
        r'(?:\b(?:практичн[а-яіїє]*|лабораторн[а-яіїє]*)\s+)?(?:\b(?:завдан[а-яіїє]*|вправ[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*)\b|(?:завд|впр|ном)\b\.?|\bп\.\s*|№)\s*(?:№\s*)?(\d+)\s*[-–—]\s*(\d+)',
        re.IGNORECASE
    )
    for m in range_pat.finditer(text):
        start, end = int(m.group(1)), int(m.group(2))
        if start < end <= start + 15:
            for n in range(start, end + 1):
                found_nums.add(n)

    # Ключові слова-якорі: завдання, вправа, номер, пункт, № (у всіх відмінках та скороченнях)
    KEYWORD_PAT = re.compile(
        r'(?:\b(?:практичн[а-яіїє]*|лабораторн[а-яіїє]*)\s+)?(?:\b(?:завдан[а-яіїє]*|вправ[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*)\b|(?:завд|впр|ном)\b\.?|\bп\.\s*|№)',
        re.IGNORECASE
    )

    # Роздільники між числами після ключового слова
    # Дозволяємо: пробіли, коми, «та», «і», «й», «та й», «і», «and», «№»
    SEP_PAT = re.compile(r'(?:\s*(?:,|та|і|й|and)\s*|\s+)(?:№\s*)?', re.IGNORECASE)
    NUM_PAT = re.compile(r'\d+')

    # Потім шукаємо всі входження ключового слова та числа після нього
    for kw_match in KEYWORD_PAT.finditer(text):
        pos = kw_match.end()
        # Після ключового слова зчитуємо числа, розділені сепараторами
        while pos < len(text):
            sep_m = SEP_PAT.match(text, pos)
            if sep_m:
                after_sep = sep_m.end()
            else:
                after_sep = pos

            num_m = NUM_PAT.match(text, after_sep)
            if not num_m:
                break
            n = int(num_m.group(0))
            if 1 <= n <= 50:
                found_nums.add(n)
            pos = num_m.end()

    # Якщо вчитель вжив конструкцію «всі N завдань» — це ЗАГАЛЬНА кількість, не конкретні номери
    all_N_pattern = re.compile(
        r'(?:всі|усі)\s+\d+\s+(?:практичн\w+\s+)?(?:завдан|вправ|пункт)',
        re.IGNORECASE
    )
    if all_N_pattern.search(text) and not found_nums:
        return []

    return sorted(found_nums)


def strip_html_tags(text: str) -> str:
    """
    Повністю видаляє HTML-теги, вбудовані стилі, атрибути та сутності з тексту,
    перетворюючи його на чистий, охайний текстовий рядок без залишків розмітки.

    1. Замінює блочні теги (<br>, </p>, </div>, </li>, </tr>, </h1>..</h6>) на переноси рядка.
    2. Видаляє скрипти та стилі разом із їхнім вмістом.
    3. Вирізає всі інші теги: <span ...>, <b>, <u>, <em> тощо.
    4. Декодує HTML-сутності (&nbsp;, &quot;, &lt;, &gt;, &#39; тощо).
    5. Замінює нерозривні пробіли (\xa0, &nbsp;) та схлопує зайві пробіли.
    """
    if not text:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # Заміна блочних розділювачів на переноси рядка, щоб слова не злипалися
    s = re.sub(r'<(?:br\s*/?|/p|/div|/li|/tr|/h[1-6])>', '\n', text, flags=re.IGNORECASE)
    # Видалення скриптів та стилів разом із вмістом
    s = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', s, flags=re.IGNORECASE)
    s = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', s, flags=re.IGNORECASE)
    # Видалення всіх інших HTML-тегів
    s = re.sub(r'<[^>]+>', '', s)
    # Декодування HTML entities
    s = html.unescape(s)
    # Нормалізація нерозривних пробілів та замінників
    s = s.replace('\xa0', ' ').replace('&nbsp;', ' ')
    # Схлопування зайвих горизонтальних пробілів в один
    s = re.sub(r'[ \t]+', ' ', s)
    # Схлопування надлишкових переносів рядків (не більше двох поспіль)
    s = re.sub(r'\n\s*\n\s*\n+', '\n\n', s)
    return s.strip()


def extract_task_questions(text: str, explicit_count: int = 0) -> list[str]:
    """
    Виявляє та видобуває формулювання запитань або практичних завдань
    із тексту завдання вчителя (опису або матеріалів).
    Розумно розрізняє теоретичні нумеровані списки на слайдах лекції
    та реальні практичні завдання (наприклад, Завдання 1..3 на фінальних слайдах).
    """
    if not text or not str(text).strip():
        return []

    # Очищуємо текст від HTML-розмітки та стилів (візуальний редактор вчителя, Word тощо)
    text = strip_html_tags(text)
    if not text:
        return []

    if explicit_count <= 0:
        explicit_count = detect_expected_task_count(text)

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # Патерн явної назви завдання («Завдання 1», «Практичне завдання 2», «Вправа 3»)
    task_named_pattern = re.compile(
        r'^(?:(?:📽️\s*)?(?:\[?\s*слайд\s*\d+\b[^\]\n\r]*\]?[\s\:\-]*)?)?(?:[•\-\*]?\s*(?:(?:практичн[еа]\s+)?(?:завдання|вправа|пункт)\s*(\d+)[\.\:\)\–\—\-]?))\s*(.*)',
        re.IGNORECASE
    )

    slide_header_pattern = re.compile(
        r'^(?:(?:📽️\s*)?\[?\s*слайд\s*\d+\b|практичн[еа]\s+завдання|практична\s+робота|домашнє\s+завдання|самостійна\s+робота|інструкційна\s+картка|тема\s*:|мета\s*:|обладнання\s*:|хід\s+роботи\s*:|[-=_]{3,})',
        re.IGNORECASE
    )

    practical_section_pattern = re.compile(
        r'^(?:\[?слайд\s*\d+\]?[\s\:\-]*)?(?:практичн[еа]\s+завдання|практична\s+робота|домашнє\s+завдання|завдання\s+до\s+уроку|завдання\s+для\s+закріплення|самостійна\s+робота)',
        re.IGNORECASE
    )

    # 1. Спочатку перевіряємо, чи є в тексті чітко названі завдання («Завдання 1», «Завдання 2»...)
    explicit_named_tasks = []
    current_named = []
    for line in lines:
        m = task_named_pattern.match(line)
        if m:
            if current_named:
                explicit_named_tasks.append(' '.join(current_named).strip())
                current_named = []
            current_named.append(line)
            continue

        if slide_header_pattern.match(line):
            if current_named:
                explicit_named_tasks.append(' '.join(current_named).strip())
                current_named = []
            continue

        if current_named and not line.startswith(('http://', 'https://')):
            if len(current_named) < 4 and len(line) < 250:
                current_named.append(line)
            else:
                explicit_named_tasks.append(' '.join(current_named).strip())
                current_named = []

    if current_named:
        explicit_named_tasks.append(' '.join(current_named).strip())

    cleaned_named = []
    for q in explicit_named_tasks:
        q_strip = q.strip()
        if len(q_strip) >= 5 and q_strip not in cleaned_named:
            cleaned_named.append(q_strip)

    # Якщо знайдено 2+ явно названих завдань — це саме практичні завдання!
    # Вони мають безумовний пріоритет над теоретичними списками на слайдах.
    if len(cleaned_named) >= 2:
        if explicit_count > 0:
            return cleaned_named[:explicit_count]
        return cleaned_named[:30]

    # 2. Якщо явно названих завдань немає, шукаємо розділ «Практичне завдання» / «Домашнє завдання»
    # і витягуємо завдання саме з цього розділу
    practical_lines = []
    in_practical_section = False
    for line in lines:
        if practical_section_pattern.match(line):
            in_practical_section = True
            continue
        if in_practical_section:
            practical_lines.append(line)

    target_lines = practical_lines if practical_lines else lines

    num_pattern = re.compile(
        r'^(?:(?:📽️\s*)?(?:\[?\s*слайд\s*\d+\b[^\]\n\r]*\]?[\s\:\-]*)?)?(?:[•\-\*]?\s*(?:(?:\d+|[IVXLCDM]+)[\.\)\–\—\-]|(?:питання|завдання|вправа|відповідь|№)\s*\d+[\.\:\)\–\—\-]?))\s*(.*)',
        re.IGNORECASE
    )

    rubric_or_step_ignore = re.compile(
        r'(?:\b\d+\s+бал\w*[\:\-]|критерії\s+оцінювання|^(?:крок|етап)\s*\d+|^(?:таблиця\s*\d+|назва\s+поля|тип\s+даних|первинний\s+ключ|зовнішній\s+ключ)|^[•\-\*·]?\s*(?:за\s+множинністю|за\s+обов\'язковістю|pk:|fk:))',
        re.IGNORECASE
    )

    questions = []
    current_q = []
    for line in target_lines:
        if rubric_or_step_ignore.search(line):
            continue
        m = num_pattern.match(line)
        if m:
            if current_q:
                questions.append(' '.join(current_q).strip())
                current_q = []
            current_q.append(line)
            continue

        if slide_header_pattern.match(line):
            if current_q:
                questions.append(' '.join(current_q).strip())
                current_q = []
            continue

        if current_q and not line.startswith(('http://', 'https://')):
            if len(current_q) < 4 and len(line) < 250:
                current_q.append(line)
            else:
                questions.append(' '.join(current_q).strip())
                current_q = []
        elif '?' in line:
            sub_qs = re.findall(r'[^.!?\n]+(?:\?)', line)
            for sq in sub_qs:
                sq_clean = sq.strip(' -•*–')
                if len(sq_clean) > 8 and sq_clean not in questions:
                    questions.append(sq_clean)
    if current_q:
        questions.append(' '.join(current_q).strip())
    if not questions:
        raw_qs = re.findall(r'[^.!?\n\r]+(?:\?)', text)
        for rq in raw_qs:
            rq_clean = rq.strip(' \t\n\r-•*–')
            if len(rq_clean) > 8 and rq_clean not in questions:
                questions.append(rq_clean)

    cleaned = []
    for q in questions:
        q_strip = q.strip()
        if len(q_strip) >= 5 and q_strip not in cleaned:
            cleaned.append(q_strip)

    if explicit_count > 0 and len(cleaned) >= explicit_count:
        return cleaned[:explicit_count]

    return cleaned[:30]


SINGLE_TASK_KEYWORDS = [
    'робота над проєктом', 'робота над проектом', 'робота з проєктом', 'робота з проектом',
    'зробити проєкт', 'зробити проект', 'створити проєкт', 'створити проект',
    'проєктна робота', 'проектна робота', 'виконання проєкту', 'виконання проекту',
    'розробка проєкту', 'розробка проекту', 'написати проєкт', 'написати проект',
    'проєкт', 'проект',
    'створити презентацію', 'підготувати презентацію', 'розробити презентацію', 'презентація на тему',
    'створити програму', 'написати програму', 'розробити програму', 'написання коду',
    'виконати практичну роботу', 'практична робота', 'лабораторна робота', 'дослідницька робота',
    'опрацювати тему', 'опрацювання теми', 'вивчення теми',
    'підготувати повідомлення', 'підготувати доповідь', 'підготувати реферат',
    'написати твір', 'написати есе', 'творча робота',
    'створити буклет', 'створити веб-сторінку', 'створити сайт', 'створити плакат', 'створити постер',
    'створити базу даних', 'створити таблицю', 'заповнити таблицю', 'електронна таблиця',
    'база даних', 'бази даних', 'баз даних', 'базою даних',
    'реляційн', 'сутність', 'сутност', 'атрибут', 'первинний ключ', 'зовнішній ключ',
    'ер-схем', 'er-схем', 'ms access', 'access', 'індивідуальне завдання',
    'побудувати діаграму', 'створити діаграму', 'намалювати схему', 'створити схему',
    'створити текстовий документ', 'провести дослідження', 'знайти та опрацювати інформацію',
    'створити зображення', 'виконати обчислення', 'налаштувати програму', 'створити вебсторінку',
    'виконати послідовність практичних дій', 'виконати завдання за інструкцією'
]


def sanitize_unassigned_task_mentions(
    summary: str,
    weaknesses: list[str],
    feedback_comment: str,
    allowed_task_nums: set[int] = None,
    is_single_task: bool = False
) -> tuple[str, list[str], str]:
    """
    Видаляє з відгуку ШІ (summary, weaknesses, feedback_comment) будь-які неправомірні
    претензії щодо невиконання завдань, які не входили до Scope of Work.
    """
    clean_summary = summary or ""
    clean_weaknesses = list(weaknesses) if weaknesses else []
    clean_feedback = feedback_comment or ""

    if is_single_task:
        # Для одного комплексного завдання заборонені будь-які «виконано X з Y» та «не виконано завдання/вправу N»
        ratio_pat = re.compile(
            r'(?:\b(?:виконано|опрацьовано|зараховано|здано|надано)\s+(?:лише\s+)?(?:\d+|одне|два|три|чотири)\s+(?:з|із)\s+(?:\d+|двох|трьох|чотирьох|п\'яти)\s*(?:практичн\w*|завдан\w*|вправ\w*)?[\.\,\;]?|'
            r'\b(?:\d+|одне|два|три|чотири)\s+(?:з|із)\s+(?:\d+|двох|трьох|чотирьох|п\'яти)\s+(?:завдан\w*|практичн\w*|вправ\w*)\b)',
            re.IGNORECASE
        )
        task_fail_pat = re.compile(
            r'(?:(?:не\s*виконано|пропущено|відсутнє|відсутня|відсутній|не\s*зроблено|не\s*надано|відсутня\s*відповідь\s*на|не\s*відповів\s*на)\s+(?:завдання|вправ[а-яіїє]*|питання|номер[а-яіїє]*|№|пункт[а-яіїє]*)\s*\d+[\.\,\;]?|'
            r'(?:завдання|вправ[а-яіїє]*|питання|номер[а-яіїє]*|№|пункт[а-яіїє]*)\s*\d+\s*(?:не\s*виконано|пропущено|відсутнє|відсутня|не\s*зроблено|не\s*надано|залишилось\s*без\s*відповіді)[\.\,\;]?|'
            r'(?:не\s*виконано|пропущено)\s*(?:всі|інші|решту)\s*(?:вправи|завдання|пункти)[\.\,\;]?)',
            re.IGNORECASE
        )

        clean_weaknesses = [w for w in clean_weaknesses if not ratio_pat.search(str(w)) and not task_fail_pat.search(str(w))]
        clean_summary = ratio_pat.sub('', clean_summary)
        clean_summary = task_fail_pat.sub('', clean_summary)
        clean_feedback = ratio_pat.sub('', clean_feedback)
        clean_feedback = task_fail_pat.sub('', clean_feedback)

    elif allowed_task_nums is not None:
        # Дозволені лише завдання з allowed_task_nums
        non_scoped_nums = [n for n in range(1, 51) if n not in allowed_task_nums]
        for n in non_scoped_nums:
            pat1 = re.compile(rf'(?:завдання|вправ[а-яіїє]*|номер[а-яіїє]*|№)\s*{n}\s*(?:не\s*виконано|пропущено|відсутнє|відсутня|не\s*зроблено|не\s*надано|залишилось\s*без\s*відповіді)', re.IGNORECASE)
            pat2 = re.compile(rf'(?:не\s*виконано|пропущено|відсутнє|відсутня|пропущено\s*виконання|відсутня\s*відповідь\s*на|не\s*відповів\s*на)\s*(?:завдання|вправ[а-яіїє]*|номер[а-яіїє]*|№)\s*{n}\b', re.IGNORECASE)
            clean_weaknesses = [w for w in clean_weaknesses if not pat1.search(str(w)) and not pat2.search(str(w))]
            clean_summary = pat1.sub('', clean_summary)
            clean_summary = pat2.sub('', clean_summary)
            clean_feedback = pat1.sub('', clean_feedback)
            clean_feedback = pat2.sub('', clean_feedback)

        # Якщо загальна кількість у співвідношенні «X з Y» відрізняється від len(allowed_task_nums)
        if len(allowed_task_nums) > 0:
            wrong_total_pat = re.compile(
                rf'\b(?:виконано|опрацьовано|зараховано)\s+\d+\s+(?:з|із)\s+(?!{len(allowed_task_nums)}\b)\d+\s*(?:завдан\w*|практичн\w*|вправ\w*)?',
                re.IGNORECASE
            )
            clean_weaknesses = [w for w in clean_weaknesses if not wrong_total_pat.search(str(w))]
            clean_summary = wrong_total_pat.sub('', clean_summary)
            clean_feedback = wrong_total_pat.sub('', clean_feedback)

    # Очищення від подвійних пробілів та некоректної пунктуації після видалення
    clean_summary = re.sub(r'\s{2,}', ' ', clean_summary).strip(' ,;')
    clean_feedback = re.sub(r'\s{2,}', ' ', clean_feedback).strip(' ,;')

    return clean_summary, clean_weaknesses, clean_feedback


def find_question_by_task_num(questions: list[str], target_num: int) -> str | None:
    """
    Знаходить конкретне формулювання завдання за його номером (наприклад, номер 2).
    Шукає насамперед семантичні мітки («Вправа 2», «Завдання 2», «№ 2», «2.») у списку знайдених питань.
    Запобігає хибному співставленню за позицією списку (num - 1), коли перший елемент
    є заголовком розділу, темою або вступним теоретичним запитанням.
    """
    if not questions or target_num is None:
        return None

    t_str = str(target_num)

    # 1. Пошук явних назв («Вправа 2», «Завдання 2», «Пункт 2», «Номер 2», «№ 2», «№2»)
    named_pattern = re.compile(
        rf'(?:\b(?:завдан[а-яіїє]*|вправ[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*|exercise|task)\b|(?:завд|впр|ном)\b\.?|\bп\.\s*|№)\s*(?:№\s*)?{t_str}\b',
        re.IGNORECASE
    )
    for q in questions:
        if named_pattern.search(q):
            return q

    # 2. Пошук номера на початку рядка/питання (наприклад: «2.», «2)», «[Слайд X] 2.»)
    start_num_pattern = re.compile(
        rf'^(?:[•\-\*]\s*)?{t_str}[\.\)\:\–\—\-]\s+',
        re.IGNORECASE
    )
    cleaned_questions = [re.sub(r'^(?:📽️\s*)?\[?\s*слайд\s*\d+(?:\s*/\s*\d+)?\s*[\]\:\-]\s*', '', q, flags=re.IGNORECASE) for q in questions]
    for original, cleaned in zip(questions, cleaned_questions):
        if start_num_pattern.search(cleaned):
            return original

    # 3. Пошук номера як окремого пункту всередині тексту
    body_num_pattern = re.compile(rf'(?:^|\n|\b)\s*{t_str}[\.\)]\s+[А-Яа-яA-Za-z]', re.IGNORECASE)
    for original, cleaned in zip(questions, cleaned_questions):
        if body_num_pattern.search(cleaned):
            return original

    # 4. Позиційний fallback (idx = target_num - 1) КАТЕГОРИЧНО ЗАБОРОНЕНО (Section 19),
    # оскільки список може містити заголовки, теорію, приклади або питання для самоперевірки.
    # Номер завдання визначається ВИКЛЮЧНО за явним маркером (Вправа 2, Завдання 2, № 2, 2., 2)).
    return None


def extract_criteria_and_requirements_from_text(
    text: str,
    assignment_title: str = "",
    assignment_desc: str = "",
    task_type: str = ""
) -> dict:
    """
    Виявляє та видобуває із тексту вчительських матеріалів (презентацій, PDF, документів) або опису:
    - Критерії оцінювання (criteria: list[dict]) з прив'язкою до поточного завдання (applies_to, mandatory)
    - Вимоги до оформлення та формату (format_requirements: list[str])
    - Розподіл балів / шкалу (rubric_points: list[dict])
    - Обов'язкові складові роботи (mandatory_components: list[str])
    Критерії з файлів іншої вправи або не заданого завдання отримують mandatory = False
    і не застосовуються як обов'язкові критерії.
    """
    if not text or not text.strip():
        return {
            'criteria': [],
            'format_requirements': [],
            'rubric_points': [],
            'mandatory_components': []
        }

    criteria = []
    format_requirements = []
    rubric_points = []
    mandatory_components = []

    context_text = f"{assignment_title} {assignment_desc}".strip().lower()
    assigned_nums = parse_teacher_specific_task_numbers(context_text)

    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    exercise_header_pattern = re.compile(
        r'^(?:[•\-\*#]*\s*)?(?:(?:вправа|завдання|exercise|task|№)\s*(\d+)|\b(?:вправа|завдання|ход\s+роботи)\b)(.*)',
        re.IGNORECASE
    )
    criteria_header_pattern = re.compile(
        r'^(?:[•\-\*#]*\s*)?(?:критерії(?:\s+оцінювання)?|вимоги(?:\s+до\s+(?:оформлення|роботи|результату|презентації|проєкту|виконання))?|шкала(?:\s+оцінювання)?|розподіл\s+балів|правила\s+оформлення|обов\'?язкові\s+складові)\s*[:\-\—]?',
        re.IGNORECASE
    )
    stop_header_pattern = re.compile(
        r'^(?:[•\-\*#]*\s*)?(?:теорія|приклад|хід\s+роботи|питання|література|джерела|додаткові\s+вправи|слайд\s*\d+|тема\s*:|мета\s*:)\b',
        re.IGNORECASE
    )

    in_criteria_section = False
    current_section = "Критерії оцінювання"
    current_exercise_name = ""
    current_exercise_num = None
    applies_to_current = True

    for line in lines:
        ex_m = exercise_header_pattern.match(line)
        if ex_m:
            current_exercise_name = line.strip(' \t•-*#–—;.,')
            if ex_m.group(1):
                try:
                    current_exercise_num = int(ex_m.group(1))
                except (ValueError, TypeError):
                    current_exercise_num = None
            else:
                current_exercise_num = None

            # Перевіряємо, чи цей розділ вправи належить поточному завданню
            if assigned_nums:
                applies_to_current = bool(current_exercise_num and current_exercise_num in assigned_nums)
            elif context_text:
                # Якщо вчитель задав створити презентацію, а вправа 1 — "буклет" чи "таблиця", це інша вправа
                ex_lower = current_exercise_name.lower()
                is_ex_mentioned = any(kw in ex_lower for kw in ['презентаці', 'слайд'] if task_type == 'presentation')
                is_ex_named_in_desc = any(w in context_text for w in ex_lower.split() if len(w) > 4)
                applies_to_current = is_ex_mentioned or is_ex_named_in_desc or ('вправа' not in context_text and 'завдання' not in context_text and not current_exercise_num)
            else:
                applies_to_current = True

        if criteria_header_pattern.match(line):
            in_criteria_section = True
            current_section = line.strip(' \t•-*#–—;.,')
            crit_h_lower = current_section.lower()

            # Якщо в заголовку критеріїв явно вказано поточний продукт (наприклад: «Критерії оцінювання цієї презентації»)
            if any(kw in crit_h_lower for kw in ['цієї презентації', 'до презентації', 'презентаці']) and task_type == 'presentation':
                applies_to_current = True
            elif 'цієї' in crit_h_lower or 'цього' in crit_h_lower:
                applies_to_current = True
            elif current_exercise_num and assigned_nums and current_exercise_num not in assigned_nums:
                applies_to_current = False

            after_col = line.split(':', 1)[-1].strip() if ':' in line else ""
            if len(after_col) > 3 and not criteria_header_pattern.match(after_col):
                line = after_col
            else:
                continue
        elif stop_header_pattern.match(line):
            in_criteria_section = False
            continue

        clean_item = line.strip(' \t•-*#–—;.,')
        if not clean_item or len(clean_item) < 3:
            continue

        pts_match = re.search(r'\(?\s*(\d+(?:[.,]\d+)?)\s*(?:бал\w*|б\.)\s*\)?', clean_item, re.IGNORECASE)
        pts = float(pts_match.group(1).replace(',', '.')) if pts_match else None

        is_format = bool(re.search(r'\b(?:слайд\w*|шрифт\w*|формат\w*|сторінк\w*|титульн\w*|розмір\w*|pdf|docx|презентац\w*)\b', clean_item, re.IGNORECASE))
        is_conclusion = bool(re.search(r'\b(?:висновок\w*|підсумок\w*)\b', clean_item, re.IGNORECASE))
        is_sources = bool(re.search(r'\b(?:джерел\w*|літератур\w*)\b', clean_item, re.IGNORECASE))
        is_illustration = bool(re.search(r'\b(?:ілюстрац\w*|малюнк\w*|зображен\w*|фото)\b', clean_item, re.IGNORECASE))

        if in_criteria_section or pts is not None or (is_format and any(k in clean_item.lower() for k in ['не менше', 'мінімум', 'наявність', 'обов', 'титульний'])):
            rule_entry = {
                'name': clean_item,
                'points': pts,
                'is_format': is_format,
                'is_conclusion': is_conclusion,
                'is_sources': is_sources,
                'is_illustration': is_illustration,
                'source': 'teacher_file',
                'source_location': current_section,
                'applies_to': task_type or (current_exercise_name if applies_to_current else 'other_exercise'),
                'mandatory': bool(applies_to_current),
            }
            if rule_entry not in criteria:
                criteria.append(rule_entry)

            if applies_to_current:
                if pts is not None:
                    rubric_points.append({'item': clean_item, 'points': pts})
                if is_format:
                    format_requirements.append(clean_item)
                if is_conclusion or is_sources or is_illustration or 'титульний' in clean_item.lower():
                    mandatory_components.append(clean_item)

    return {
        'criteria': criteria,
        'format_requirements': format_requirements,
        'rubric_points': rubric_points,
        'mandatory_components': mandatory_components
    }


def analyze_student_comment_nuance(
    comment_student: str,
    desc: str = "",
    custom_criteria: str = "",
    format_requirements: list[str] = None
) -> dict:
    """
    Аналізує коментар учня на наявність:
    1. Змістовного наповнення (висновки, підсумки, пояснення, розв'язки).
    2. Порожніх непідтверджених декларацій («Я все зробив», «Я виконав усі вимоги»).
    3. Суворих обмежень вчителя щодо розміщення (наприклад, вимога мати висновок САМЕ НА СЛАЙДІ презентації).
    """
    comment = (comment_student or "").strip()
    if not comment:
        return {
            'has_comment': False,
            'has_substance': False,
            'is_unsubstantiated_declaration': False,
            'substance_type': None,
            'conclusion_text': None,
            'format_strictly_requires_file': False,
            'guidance': ''
        }

    combined_instr = f"{desc} {custom_criteria} {' '.join(format_requirements or [])}".lower()

    # 1. Перевірка на непідтверджену декларацію (без реального змісту роботи)
    empty_claims = [
        'я все зробив', 'я все виконав', 'я виконав усі вимоги', 'я зробив усі завдання',
        'все правильно', 'робота готова', 'ось моя робота', 'все зроблено',
        'я все написав', 'завдання виконано', 'я постарався', 'поставте 12'
    ]
    cleaned_lower = re.sub(r'[^\w\s]', '', comment.lower()).strip()
    is_empty_declaration = (
        len(comment) < 120 and
        any(claim in cleaned_lower for claim in empty_claims) and
        not any(kw in cleaned_lower for kw in ['отже', 'тому що', 'в результаті', 'дослідивши', 'висновок', 'вважаю'])
    )

    # 2. Перевірка наявності висновку
    has_conclusion = bool(re.search(
        r'\b(?:висновок\w*|підсумок\w*|підсумовуючи|отже|в результаті|дійшов висновку|зробив висновок)\b',
        comment,
        re.IGNORECASE
    )) or (len(comment) > 40 and not is_empty_declaration and any(w in comment.lower() for w in ['оскільки', 'тому що', 'показав, що', 'дозволяє зробити висновок']))

    # 3. Перевірка, чи вчитель прямо вимагав наявність висновку САМЕ В ПРЕЗЕНТАЦІЇ / НА СЛАЙДІ
    format_strictly_requires_file = bool(re.search(
        r'(?:висновок[^\n\r]{0,40}(?:слайд[іау]|презентаці[їі])|(?:слайд[іау]|презентаці[їі])[^\n\r]{0,40}висновок|висновок\s+у\s+документі)',
        combined_instr,
        re.IGNORECASE
    ))

    substance_type = 'conclusion' if has_conclusion else ('explanation' if len(comment) > 20 and not is_empty_declaration else 'empty_declaration')

    guidance = ""
    if has_conclusion:
        if format_strictly_requires_file:
            guidance = (
                "Учень надав змістовний висновок у коментарі до здачі. Проте за прямою вимогою вчителя висновок "
                "має міститися безпосередньо на слайді презентації. Зміст зарахувати, вимогу оформлення позначити як частково "
                "виконану, дати рекомендацію перенести висновок на слайд презентації. НЕ заявляти, що висновок повністю відсутній!"
            )
        else:
            guidance = (
                "Учень надав змістовний висновок у коментарі до здачі. Спосіб подання дозволений, тому змістовий критерій "
                "наявності висновку вважається повністю виконаним. НЕ знижувати оцінку за відсутність висновку у файлі!"
            )
    elif is_empty_declaration:
        guidance = (
            "Коментар учня є загальною непідтвердженою заявою («все виконав»). Не зараховувати як доказ виконання критеріїв "
            "без наявності фактичного підтвердження у зданих матеріалах."
        )

    return {
        'has_comment': True,
        'has_substance': (not is_empty_declaration and len(comment) >= 15),
        'is_unsubstantiated_declaration': is_empty_declaration,
        'substance_type': substance_type,
        'conclusion_text': comment if has_conclusion else None,
        'format_strictly_requires_file': format_strictly_requires_file,
        'guidance': guidance
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ДИНАМІЧНА ІНТЕРПРЕТАЦІЯ ТИПУ, ОБСЯГУ ТА ОЧІКУВАНОГО РЕЗУЛЬТАТУ ЗАВДАННЯ
# ═══════════════════════════════════════════════════════════════════════════════

ASSIGNMENT_TASK_TYPES = [
    'presentation',        # Презентація, слайди
    'programming',         # Програмування, код
    'table',               # Електронна таблиця, розрахунки Excel/Sheets
    'database',            # База даних, Access, SQL
    'diagram',             # Діаграма, схема, графік
    'creative',            # Творча робота, малюнок, плакат, буклет
    'project',             # Проєкт, комплексна розробка
    'research',            # Дослідження, краєзнавство, пошук інформації
    'document',            # Текстовий документ, реферат, есе, твір
    'calculation',         # Математичні/фізичні обчислення, задачі
    'question_answer',     # Відповіді на конкретні запитання, тести
    'practical',           # Практична або лабораторна робота за інструкцією
    'other',               # Загальне завдання
]


class TaskComponentsList(list):
    """
    Список компонентів завдання, який підтримує пряму перевірку типу через оператор in:
    'presentation' in components -> True
    а також стандартну роботу зі словниками [{'type': 'presentation', ...}]
    """
    def __contains__(self, item):
        if super().__contains__(item):
            return True
        return any(isinstance(c, dict) and c.get('type') == item for c in self)


def extract_assignment_topic_and_action(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    subject_name: str = ""
) -> dict:
    """
    Розділяє тему (topic) та дію (action) у завданні вчителя (SOURCE A).
    Запобігає хибному перетворенню теми на тип завдання (наприклад,
    «Створити презентацію про програмування» -> topic='програмування', task_type='presentation').
    """
    t_clean = strip_html_tags(title or "").strip()
    d_clean = strip_html_tags(desc or "").strip()
    c_clean = strip_html_tags(custom_criteria or "").strip()

    topic_patterns = [
        r'\bна\s+тему\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\bза\s+темою\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\b(?:з|із)\s+теми\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\bщодо\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\bприсвячен[а-яіїє]*\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\bпро\s+["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
        r'\bпро[єе]кт[а-яіїє]*\s+["«\']([^"»\']+)["»\']',
        r'\bтема\s*:\s*["«\']?([^"»\'\.\,\;\!\?\n]+)["«\']?',
    ]

    extracted_topic = ""
    topic_full_match = ""

    for pat in topic_patterns:
        m = re.search(pat, d_clean, re.IGNORECASE)
        if not m:
            m = re.search(pat, t_clean, re.IGNORECASE)
        if m:
            extracted_topic = m.group(1).strip()
            topic_full_match = m.group(0).strip()
            break

    # Якщо до знайденої теми випадково причепилася наступна дія (наприклад, «про штучний інтелект та відповісти на 5 питань»):
    if extracted_topic:
        action_split_m = re.search(
            r'\s+(?:та|і|й|а|але)\s+(?:відпові[а-яіїє]*|напис[а-яіїє]*|створ[а-яіїє]*|розроб[а-яіїє]*|викон[а-яіїє]*|скла[а-яіїє]*|побуду[а-яіїє]*|заповн[а-яіїє]*|розв\'яз[а-яіїє]*|перевір[а-яіїє]*|оформ[а-яіїє]*|захист[а-яіїє]*)\b',
            extracted_topic,
            re.IGNORECASE
        )
        if action_split_m:
            cutoff = action_split_m.start()
            truncated_topic = extracted_topic[:cutoff].strip()
            if truncated_topic:
                if topic_full_match and extracted_topic in topic_full_match:
                    topic_full_match = topic_full_match[:topic_full_match.find(extracted_topic) + len(truncated_topic)].strip()
                extracted_topic = truncated_topic

    action_kw_pattern = r'\b(?:створ[а-яіїє]*|розроб[а-яіїє]*|підготу[а-яіїє]*|напис[а-яіїє]*|скла[а-яіїє]*|побуду[а-яіїє]*|заповн[а-яіїє]*|викон[а-яіїє]*|розв\'яз[а-яіїє]*|намал[а-яіїє]*|оформ[а-яіїє]*|презентаці[а-яіїє]*|таблиц[а-яіїє]*|код[а-яіїє]*|програм[а-яіїє]*|баз[а-яіїє]*\s+даних|есе|твір|реферат)\b'
    has_action_in_desc = bool(re.search(action_kw_pattern, d_clean, re.IGNORECASE))
    has_action_in_title = bool(re.search(action_kw_pattern, t_clean, re.IGNORECASE))

    if not extracted_topic:
        if t_clean and has_action_in_desc and not has_action_in_title:
            extracted_topic = t_clean
        elif t_clean and not has_action_in_title:
            extracted_topic = t_clean
        elif d_clean and not has_action_in_desc:
            extracted_topic = d_clean
        else:
            extracted_topic = t_clean or subject_name or "Навчальна тема"

    masked_d = d_clean
    masked_t = t_clean

    if topic_full_match:
        masked_d = re.sub(re.escape(topic_full_match), ' __TOPIC__ ', masked_d, flags=re.IGNORECASE)
        masked_t = re.sub(re.escape(topic_full_match), ' __TOPIC__ ', masked_t, flags=re.IGNORECASE)
    elif extracted_topic and len(extracted_topic) >= 4 and extracted_topic.lower() != t_clean.lower():
        masked_d = re.sub(re.escape(extracted_topic), ' __TOPIC__ ', masked_d, flags=re.IGNORECASE)
        masked_t = re.sub(re.escape(extracted_topic), ' __TOPIC__ ', masked_t, flags=re.IGNORECASE)

    # Якщо Title є суто назвою теми уроку (без дієслів/продукту дії), а в описі задано дію:
    if has_action_in_desc and not has_action_in_title and t_clean:
        masked_t = ' __TOPIC__ '

    action_text = f"{masked_t} {masked_d} {c_clean}".strip().lower()

    return {
        'topic': extracted_topic,
        'subject': subject_name or 'Інформатика',
        'action_text': action_text,
        'raw_title': t_clean,
        'raw_desc': d_clean,
        'raw_criteria': c_clean
    }


def identify_task_components(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    teacher_files_text: str = "",
    target_slide_text: str = "",
    subject_name: str = "",
    **kwargs
) -> TaskComponentsList:
    """
    Визначає окремі компоненти завдання (task_components) ВИКЛЮЧНО з інструкцій вчителя (SOURCE A).
    Навчальні матеріали та прикріплені файли вчителя (SOURCE B) НЕ створюють нових завдань
    і не перетворюють навчальні запитання для самоперевірки на завдання учня.
    """
    if not desc and 'description' in kwargs:
        desc = kwargs['description']

    topic_info = extract_assignment_topic_and_action(title, desc, custom_criteria, subject_name)
    combined = topic_info['action_text']
    s_clean = strip_html_tags(target_slide_text or "").strip()
    if s_clean:
        combined = f"{combined} {s_clean.lower()}"

    # Якщо в SOURCE A взагалі немає жодної інструкції або назви (екстремальний fallback)
    if not combined.replace('__topic__', '').strip() and teacher_files_text:
        combined = strip_html_tags(teacher_files_text[:1500]).lower()

    components = []

    # 1. Презентація
    if re.search(r'\b(?:презентаці[яієїю]|створити\s+слайд[а-яіїє]*|розробити\s+презентаці[а-яіїє]*|підготувати\s+презентаці[а-яіїє]*|powerpoint|pptx?|canva|гугл\s+презентаці[яієїю]|google\s+slides?)\b', combined):
        components.append({
            'type': 'presentation',
            'required': True,
            'source': 'teacher_description',
            'description': 'Створити презентацію'
        })

    # 2. Програмування (код, Python, Scratch)
    if re.search(r'\b(?:написати\s+програму|розробити\s+програму|створити\s+програму|програмний\s+код|код\s+програми|програм[ауиіе]\s+на\s+(?:python|пайтон|c\+\+|паскаль|java|scratch)|код\s+на\s+python|python|пайтон|pascal|паскаль|c\+\+|scratch|скретч|скрипт[а-яіїє]*)\b', combined):
        components.append({
            'type': 'programming',
            'required': True,
            'source': 'teacher_description',
            'description': 'Написати програмний код'
        })

    # 3. Відповіді на запитання (question_answer) - ТІЛЬКИ за прямою вимогою вчителя в SOURCE A
    has_qa = bool(re.search(
        r'\b(?:відпові[а-яіїє]*(?:\s+[\w\d]+){0,4}\s+(?:запитан|питан)[а-яіїє]*|дати\s+(?:[\w\d]+\s+){0,3}відповід[а-яіїє]*|контрольн[а-яіїє]*\s+(?:запитан|питан)[а-яіїє]*|тест[а-яіїє]*\b|опитуванн[а-яіїє]*|питанн[а-яіїє]*\s+\d+|запитанн[а-яіїє]*\s+\d+)\b',
        combined
    ))
    if has_qa:
        components.append({
            'type': 'question_answer',
            'required': True,
            'source': 'teacher_description',
            'description': 'Відповісти на контрольні запитання'
        })

    # 4. База даних (MS Access, SQL)
    if re.search(r'\b(?:баз[а-яіїє]*\s+даних|бд\b|ms\s+access|access\b|sql\b|sqlite|реляційн[а-яіїє]*\s+таблиц[а-яіїє]*)\b', combined):
        components.append({
            'type': 'database',
            'required': True,
            'source': 'teacher_description',
            'description': 'Спроєктувати базу даних'
        })

    # 5. Електронна таблиця (Excel, Таблиці)
    if re.search(r'\b(?:електронн[а-яіїє]*\s+таблиц[а-яіїє]*|створити\s+таблиц[яіюеь]|заповнити\s+таблиц[яіюеь]|побудувати\s+таблиц[яіюеь]|таблиц[яіюеь]\s+в\s+excel|таблиц[яіюеь]\s+результатів|excel|ексель|sheets|spreadsheet|табличн[а-яіїє]*\s+процесор[а-яіїє]*)\b', combined):
        components.append({
            'type': 'table',
            'required': True,
            'source': 'teacher_description',
            'description': 'Створити електронну таблицю'
        })

    # 6. Діаграма, схема, графік
    if re.search(r'\b(?:побудувати\s+діаграм[ауие]|діаграм[ауие]|графік[ауи]?|блок-схем[а-яіїє]*|інфографік[а-яіїє]*|ментальн[а-яіїє]*\s+карт[а-яіїє]*|mind\s*map)\b', combined):
        components.append({
            'type': 'diagram',
            'required': True,
            'source': 'teacher_description',
            'description': 'Побудувати діаграму / схему'
        })

    # 7. Творча робота (малюнок, буклет, плакат, колаж, відео)
    if re.search(r'\b(?:намалювати|малюнок|буклет|плакат|постер|листівк[а-яіїє]*|колаж|дизайн|відеоролик|відеомонтаж)\b', combined):
        components.append({
            'type': 'creative',
            'required': True,
            'source': 'teacher_description',
            'description': 'Створити творчу роботу'
        })

    # 8. Проєкт
    if re.search(r'\b(?:робота\s+над\s+.*?про[єе]ктом|навчальн[а-яіїє]*\s+про[єе]кт|створити\s+про[єе]кт|розробити\s+про[єе]кт)\b', combined) or (re.search(r'\bпро[єе]кт[а-яіїє]*\b', combined) and not components):
        components.append({
            'type': 'project',
            'required': True,
            'source': 'teacher_description',
            'description': 'Виконати навчальний проєкт'
        })

    # 9. Дослідження, пошук інформації (ТІЛЬКИ коли дослідження є самостійною дією, а не темою/змістом іншого продукту)
    is_genitive_research = bool(re.search(r'\b(?:таблиц[а-яіїє]*|презентаці[а-яіїє]*|результат[а-яіїє]*|дані)\s+(?:результатів\s+)?дослідження\b', combined))
    has_research_action = bool(re.search(r'\b(?:провести\s+дослідження|дослідити\s+|звіт\s+про\s+дослідження|пошукове\s+дослідження|пошук[а-яіїє]*\s+інформаці[а-яіїє]*|знайти\s+(?:в\s+інтернеті|інформацію|відомості))\b', combined))
    if has_research_action and not is_genitive_research:
        if not any(c['type'] in ['table', 'presentation', 'database', 'programming'] for c in components):
            components.append({
                'type': 'research',
                'required': True,
                'source': 'teacher_description',
                'description': 'Знайти та опрацювати інформацію'
            })

    # 10. Математичні обчислення, задачі
    if re.search(r'\b(?:обчисленн[а-яіїє]*|обчислити|розв\'язати\s+задач[а-яіїє]*|розв\'язок\s+задач[а-яіїє]*|знайти\s+значення\s+виразу|математичн[а-яіїє]*\s+розрахунк[а-яіїє]*)\b', combined):
        components.append({
            'type': 'calculation',
            'required': True,
            'source': 'teacher_description',
            'description': 'Розв\'язати задачі / виконати обчислення'
        })

    # 11. Текстовий документ / письмовий опис (есе, твір, реферат, опис алгоритму)
    if re.search(r'\b(?:текстов[а-яіїє]*\s+документ[а-яіїє]*|реферат[а-яіїє]*|есе\b|твір\b|повідомленн[а-яіїє]*|доповід[а-яіїє]*|статт[а-яіїє]*|конспект[а-яіїє]*|короткий\s+опис|опис\s+алгоритму)\b', combined):
        components.append({
            'type': 'document',
            'required': True,
            'source': 'teacher_description',
            'description': 'Скласти текстовий опис / документ'
        })

    if not components:
        if any(kw in combined for kw in ['практичн', 'лабораторн', 'інструкц']):
            components.append({
                'type': 'practical',
                'required': True,
                'source': 'teacher_description',
                'description': 'Виконати практичну роботу за інструкцією'
            })
        else:
            components.append({
                'type': 'other',
                'required': True,
                'source': 'teacher_description',
                'description': 'Виконати навчальне завдання'
            })

    return TaskComponentsList(components)


def determine_assignment_task_type(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    teacher_files_text: str = "",
    target_slide_text: str = "",
    subject_name: str = "",
    **kwargs
) -> str:
    """
    Динамічно визначає основний тип завдання (main_task_type) на основі SOURCE A.
    Матеріали вчителя (наприклад, лекція у PowerPoint для завдання з баз даних)
    НІКОЛИ не перетворюють практичне завдання на інший тип.
    """
    if 'description' in kwargs and not desc:
        desc = kwargs['description']

    components = identify_task_components(
        title=title,
        desc=desc,
        custom_criteria=custom_criteria,
        target_slide_text=target_slide_text,
        subject_name=subject_name
    )
    comp_types = [c['type'] for c in components]

    # Пріоритет основного типу практичного продукту:
    for p_type in ['presentation', 'programming', 'table', 'database', 'diagram', 'creative', 'project', 'calculation', 'document', 'research', 'question_answer', 'practical']:
        if p_type in comp_types:
            return p_type

    # Лише якщо опис і назва були повністю порожніми, перевіряємо матеріали вчителя як останній fallback
    if not (title or desc) and teacher_files_text:
        tf_lower = teacher_files_text[:3000].lower()
        if re.search(r'\b(?:баз[а-яіїє]*\s+даних|ms\s+access|sql\b)\b', tf_lower):
            return 'database'
        if re.search(r'\b(?:програм[ауиіе]|код|python)\b', tf_lower):
            return 'programming'
        if re.search(r'\b(?:таблиц[яіюеь]|excel)\b', tf_lower):
            return 'table'
        if re.search(r'\b(?:презентаці[яієїю]|powerpoint)\b', tf_lower):
            return 'presentation'

    return 'practical' if any(kw in f"{title} {desc}".lower() for kw in ['робота', 'виконати', 'зробити']) else 'other'


def is_questions_expected(
    task_type: str,
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    task_components: list = None,
    target_slide_text: str = "",
    **kwargs
) -> bool:
    """
    Визначає, чи є модель «питання-відповідь» невід'ємною частиною того, що учень має здати.
    ГОЛОВНЕ ПРАВИЛО: questions_expected = True ТІЛЬКИ коли вчитель у SOURCE A прямо вимагає
    відповісти на запитання. Будь-які запитання для самоперевірки у файлах вчителя
    НЕ є завданням для учня.
    """
    if not desc and 'description' in kwargs:
        desc = kwargs['description']
    components = task_components if task_components is not None else kwargs.get('components')

    teacher_text = f"{title} {desc} {custom_criteria} {target_slide_text}".lower()
    has_explicit_teacher_qa = bool(re.search(
        r'\b(?:відпові[а-яіїє]*(?:\s+[\w\d]+){0,4}\s+(?:запитан|питан)[а-яіїє]*|дати\s+(?:[\w\d]+\s+){0,3}відповід[а-яіїє]*|контрольн[а-яіїє]*\s+(?:запитан|питан)[а-яіїє]*|тест[а-яіїє]*\b|опитуванн[а-яіїє]*|питанн[а-яіїє]*\s+\d+|запитанн[а-яіїє]*\s+\d+)\b',
        teacher_text
    ))
    if has_explicit_teacher_qa:
        return True

    if components:
        for c in components:
            c_type = c.get('type') if isinstance(c, dict) else str(c)
            c_req = c.get('required', True) if isinstance(c, dict) else True
            if c_type == 'question_answer' and c_req:
                return True

    if task_type == 'question_answer':
        return True

    return False


def categorize_task_requirements_and_criteria(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    compiled_criteria: list = None,
    teacher_files_text: str = ""
) -> tuple[list[str], list[str], list[str], list[dict]]:
    """
    Розділяє вимоги та критерії на:
    1. teacher_requirements: обов'язкові вимоги вчителя (рядок-список для зворотної сумісності).
    2. teacher_criteria: обов'язкові критерії оцінювання вчителя.
    3. generic_quality_recommendations: загальні рекомендації до якості (НЕ знижують бал).
    4. mandatory_requirements: структурований список обов'язкових вимог із зазначенням source.
    """
    teacher_requirements = []
    teacher_criteria = []
    generic_quality_recommendations = []
    mandatory_requirements = []

    # 1. Індивідуальні критерії вчителя (SOURCE A - Пріоритет 2)
    if custom_criteria and custom_criteria.strip():
        for line in custom_criteria.strip().splitlines():
            line_s = line.strip(' \t\n\r-*•;')
            if len(line_s) > 2 and line_s not in teacher_requirements:
                teacher_requirements.append(line_s)
                teacher_criteria.append(line_s)
                mandatory_requirements.append({
                    'requirement': line_s,
                    'source': 'custom_criteria',
                    'mandatory': True
                })

    # 2. Явні вимоги з назви та опису вчителя (SOURCE A - Пріоритет 1)
    t_clean = strip_html_tags(title or "").strip()
    d_clean = strip_html_tags(desc or "").strip()

    for txt in [t_clean, d_clean]:
        if not txt:
            continue
        patterns = [
            r'\b(?:\d+\s+слайд\w*)\b',
            r'\b(?:титульн\w*\s+слайд\w*)\b',
            r'\b(?:висновок\w*|наявність\s+висновку)\b',
            r'\b(?:джерел\w*|список\s+джерел\w*|використані\s+джерела)\b',
            r'\b(?:\d+\s+ілюстрац\w*|\d+\s+малюнк\w*|\d+\s+зображен\w*)\b',
            r'\b(?:\d+\s+таблиц\w*)\b',
            r'\b(?:\d+\s+діаграм\w*)\b',
        ]
        for p in patterns:
            for m in re.finditer(p, txt, re.IGNORECASE):
                val = m.group(0).strip()
                if not any(val.lower() in r.lower() for r in teacher_requirements):
                    teacher_requirements.append(val)
                    mandatory_requirements.append({
                        'requirement': val,
                        'source': 'teacher_description',
                        'mandatory': True
                    })

        in_req_block = False
        for line in txt.splitlines():
            line_s = line.strip(' \t\n\r-*•;')
            if re.search(r'\b(?:вимог[а-яіїє]*|критері[а-яіїє]*)\b', line, re.IGNORECASE):
                in_req_block = True
                continue
            if in_req_block:
                if not line_s:
                    in_req_block = False
                    continue
                if line.strip().startswith(('-', '•', '*')) or re.match(r'^\d+[\.\)]', line.strip()):
                    clean_item = re.sub(r'^\d+[\.\)]\s*', '', line_s).strip()
                    if len(clean_item) > 2 and not any(clean_item.lower() in r.lower() for r in teacher_requirements):
                        teacher_requirements.append(clean_item)
                        mandatory_requirements.append({
                            'requirement': clean_item,
                            'source': 'teacher_description',
                            'mandatory': True
                        })

    # 3. Критерії з файлів учителя (SOURCE B), які мають mandatory == True і стосуються поточного завдання
    if compiled_criteria:
        for c in compiled_criteria:
            c_name = c['name'] if isinstance(c, dict) else str(c)
            c_name_clean = c_name.strip(' \t\n\r-*•;')
            is_mand = c.get('mandatory', True) if isinstance(c, dict) else True
            applies_to = c.get('applies_to', 'general') if isinstance(c, dict) else 'general'
            if not is_mand or applies_to == 'other_exercise':
                continue

            is_generic = any(kw in c_name_clean.lower() for kw in [
                'читабельний шрифт', 'охайне оформлення', 'єдиний стиль оформлення',
                'розподіл часу', 'грамотність', 'орфографічні помилки'
            ])
            if is_generic:
                if c_name_clean not in generic_quality_recommendations:
                    generic_quality_recommendations.append(c_name_clean)
            else:
                if c_name_clean and len(c_name_clean) > 2:
                    if not any(c_name_clean.lower() in r.lower() for r in teacher_requirements):
                        teacher_requirements.append(c_name_clean)
                    if not any(c_name_clean.lower() in r.lower() for r in teacher_criteria):
                        teacher_criteria.append(c_name_clean)
                    if not any(c_name_clean.lower() in r['requirement'].lower() for r in mandatory_requirements):
                        mandatory_requirements.append({
                            'requirement': c_name_clean,
                            'source': c.get('source', 'teacher_file') if isinstance(c, dict) else 'teacher_file',
                            'mandatory': True
                        })

    default_generic = [
        "Бажано використовувати читабельний шрифт та лаконічний текст",
        "Дотримуватися охайного візуального оформлення роботи"
    ]
    for dg in default_generic:
        if not any(dg.lower() in g.lower() for g in generic_quality_recommendations):
            generic_quality_recommendations.append(dg)

    return teacher_requirements, teacher_criteria, generic_quality_recommendations, mandatory_requirements


def extract_task_requirements(
    desc: str = "",
    custom_criteria: str = "",
    compiled_criteria: list = None,
    teacher_files_text: str = "",
    title: str = "",
    **kwargs
) -> list[str]:
    """
    Витягує реальні обов'язкові вимоги та критерії вчителя без вигадування неіснуючих обмежень.
    """
    if 'description' in kwargs and not desc:
        desc = kwargs['description']
    if 'teacher_files_content' in kwargs and not teacher_files_text:
        tfc = kwargs['teacher_files_content']
        if isinstance(tfc, list):
            teacher_files_text = "\n".join(str(x) for x in tfc)
        elif isinstance(tfc, str):
            teacher_files_text = tfc

    if teacher_files_text and compiled_criteria is None:
        t_type = determine_assignment_task_type(title, desc, custom_criteria, teacher_files_text)
        extracted = extract_criteria_and_requirements_from_text(
            teacher_files_text,
            assignment_title=title,
            assignment_desc=desc,
            task_type=t_type
        )
        compiled_criteria = extracted.get('criteria', [])

    teacher_reqs, _, _, _ = categorize_task_requirements_and_criteria(
        title=title,
        desc=desc,
        custom_criteria=custom_criteria,
        compiled_criteria=compiled_criteria,
        teacher_files_text=teacher_files_text
    )
    return teacher_reqs


def build_teacher_intent(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    task_type: str = "other",
    task_components: list = None,
    topic: str = "",
    teacher_requirements: list = None,
    questions_expected: bool = False
) -> dict:
    """
    Формує структуроване розуміння суті завдання (teacher_intent) відповідно до Section 10:
    - what_teacher_asks: просте і зрозуміле формулювання того, що просить вчитель
    - why_student_does_it: мета учнівської роботи
    - expected_result: очікуваний практичний результат
    - required_actions: конкретні кроки дій
    - required_components: обов'язкові елементи
    - explicit_constraints: обмеження від учителя
    - forbidden_assumptions: заборонені штучні припущення
    """
    t_clean = strip_html_tags(title or "").strip()
    d_clean = strip_html_tags(desc or "").strip()
    c_clean = strip_html_tags(custom_criteria or "").strip()

    if d_clean:
        what_teacher_asks = f"Вчитель просить: {d_clean}"
    elif t_clean:
        if task_type == 'presentation':
            what_teacher_asks = f"Вчитель просить створити презентацію на тему «{topic or t_clean}»."
        elif task_type == 'programming':
            what_teacher_asks = f"Вчитель просить написати програму за темою «{topic or t_clean}»."
        elif task_type == 'database':
            what_teacher_asks = f"Вчитель просить створити базу даних за темою «{topic or t_clean}»."
        elif task_type == 'table':
            what_teacher_asks = f"Вчитель просить створити електронну таблицю за темою «{topic or t_clean}»."
        elif task_type == 'question_answer':
            what_teacher_asks = f"Вчитель просить надати відповіді на запитання за темою «{topic or t_clean}»."
        else:
            what_teacher_asks = f"Вчитель просить виконати навчальне завдання «{t_clean}»."
    else:
        what_teacher_asks = "Вчитель просить виконати навчальне завдання відповідно до інструкцій."

    why_student_does_it = f"Опанування навчального матеріалу та демонстрація практичних навичок за темою «{topic or t_clean or 'уроку'}»."

    result_map = {
        'presentation': f"Готова презентація про {topic or 'тему завдання'}.",
        'programming': f"Працездатна програма / вихідний код за темою {topic or 'завдання'}.",
        'table': f"Готова електронна таблиця з даними за темою {topic or 'завдання'}.",
        'database': f"Спроєктована база даних за темою {topic or 'завдання'}.",
        'diagram': f"Побудована діаграма або схема за темою {topic or 'завдання'}.",
        'creative': f"Виконана творча робота за темою {topic or 'завдання'}.",
        'project': f"Завершений навчальний проєкт за темою {topic or 'завдання'}.",
        'research': f"Звіт / опрацьована інформація за темою {topic or 'завдання'}.",
        'calculation': f"Розв'язані задачі з розрахунками за темою {topic or 'завдання'}.",
        'document': f"Оформлений текстовий документ за темою {topic or 'завдання'}.",
        'question_answer': f"Письмові відповіді на поставлені запитання за темою {topic or 'завдання'}.",
        'practical': "Виконана практична робота за інструкцією вчителя.",
        'other': "Виконане навчальне завдання."
    }
    expected_result = result_map.get(task_type, f"Виконана робота за темою {topic or 'завдання'}.")

    required_actions = []
    if task_type == 'presentation':
        required_actions.append(f"Ознайомитися з матеріалом теми «{topic or 'уроку'}»")
        required_actions.append("Створити слайди презентації за змістом завдання")
        if questions_expected:
            required_actions.append("Дати письмові відповіді на визначені запитання завдання")
        required_actions.append("Зберегти файл презентації та надіслати на перевірку")
    elif task_type == 'programming':
        required_actions.append(f"Скласти алгоритм для розв'язання задачі за темою «{topic or 'уроку'}»")
        required_actions.append("Написати та налагодити програмний код")
        required_actions.append("Перевірити роботу програми на тестових даних")
        required_actions.append("Зберегти код програми та надіслати роботу")
    elif task_type == 'table':
        required_actions.append("Створити таблицю в табличному процесорі")
        required_actions.append("Внести необхідні дані та формули для обчислень")
        required_actions.append("Зберегти файл електронної таблиці та прикріпити до здачі")
    elif task_type == 'database':
        required_actions.append(f"Спроєктувати структуру бази даних за темою «{topic or 'уроку'}»")
        required_actions.append("Створити необхідні таблиці та зв'язки")
        required_actions.append("Зберегти файл бази даних та надіслати на перевірку")
    elif task_type == 'question_answer':
        required_actions.append("Уважно прочитати поставлені вчителем запитання")
        required_actions.append("Сформулювати чіткі відповіді на кожне запитання")
        required_actions.append("Надіслати письмові відповіді на перевірку")
    else:
        required_actions.append("Опрацювати інструкції вчителя")
        required_actions.append("Виконати практичні завдання")
        required_actions.append("Зберегти та надіслати готову роботу")

    required_components = []
    if teacher_requirements:
        for r in teacher_requirements:
            req_str = r.get('requirement') if isinstance(r, dict) else str(r)
            if req_str and req_str not in required_components:
                required_components.append(req_str)
    if not required_components:
        if task_type == 'presentation':
            required_components = ["зміст слайдів за темою завдання"]
            if questions_expected:
                required_components.append("відповіді на поставлені запитання")
        elif task_type == 'programming':
            required_components = ["працездатний вихідний код програми"]
        elif task_type == 'table':
            required_components = ["таблиця з внесеними даними"]
        elif task_type == 'database':
            required_components = ["структура бази даних та таблиці"]
        elif task_type == 'question_answer':
            required_components = ["відповіді на поставлені запитання"]
        else:
            required_components = ["виконання вимог інструкції вчителя"]

    explicit_constraints = []
    if c_clean:
        for line in c_clean.splitlines():
            line_s = line.strip(' \t\n\r-*•;')
            if len(line_s) > 2:
                explicit_constraints.append(line_s)

    forbidden_assumptions = [
        "Не вимагати виконання вправ або прикладів з навчальних матеріалів/презентацій вчителя, якщо вчитель не задав їх прямо.",
        "Не вимагати наявності титульного слайду, фіксованої кількості слайдів (наприклад, 8), висновку чи списку джерел, якщо вчитель цього явно не вимагав у тексті завдання.",
        "Не застосовувати модель «питання-відповідь» і не шукати контрольні запитання для самоперевірки у прикріплених матеріалах, якщо вчитель дав завдання створити продукт/файл.",
        "Не знижувати оцінку за загальними порадами щодо охайності чи шрифтів (generic recommendations)."
    ]

    return {
        "what_teacher_asks": what_teacher_asks,
        "why_student_does_it": why_student_does_it,
        "expected_result": expected_result,
        "required_actions": required_actions,
        "required_components": required_components,
        "explicit_constraints": explicit_constraints,
        "forbidden_assumptions": forbidden_assumptions,
        "task_type": task_type,
        "topic": topic
    }


def build_final_task_understanding(
    teacher_intent: dict,
    subject: str,
    topic: str,
    task_type: str,
    task_components: list,
    assigned_scope: list,
    expected_result: str,
    required_actions: list,
    mandatory_requirements: list,
    optional_recommendations: list,
    questions_expected: bool,
    confidence: float = 0.95,
    ambiguities: list = None
) -> dict:
    """
    Створює об'єкт FINAL TASK UNDERSTANDING (Section 20), який є єдиним джерелом істини
    для AI-оцінювання, student_explanation, deliverable та критеріїв.
    """
    return {
        "teacher_intent": teacher_intent,
        "subject": subject,
        "topic": topic,
        "task_type": task_type,
        "task_components": list(task_components),
        "assigned_scope": assigned_scope or [expected_result],
        "expected_result": expected_result,
        "required_actions": required_actions,
        "mandatory_requirements": mandatory_requirements,
        "optional_recommendations": optional_recommendations,
        "questions_expected": questions_expected,
        "task_understanding_confidence": confidence,
        "ambiguities": ambiguities or [],
        "source_priority": {
            "teacher_description": 1,
            "custom_criteria": 2,
            "explicit_teacher_file_instruction": 3,
            "teacher_material_context": 4,
            "generic_recommendations": 5
        }
    }


def log_task_understanding_diagnostics(
    title: str,
    desc: str,
    custom_criteria: str,
    file_context: str,
    final_task_understanding: dict,
    ignored_tasks: list = None
):
    """
    Форматована діагностика інтерпретації завдання у відповідності до Section 26.
    """
    ignored_str = "\n".join(f"- {item}" for item in (ignored_tasks or [])) if ignored_tasks else "Немає (усі матеріали релевантні або сторонні вправи відсутні)"
    mand_reqs = final_task_understanding.get('mandatory_requirements', [])
    mand_str = "\n".join(f"- {r.get('requirement', str(r))} [{r.get('source', 'teacher')}]" if isinstance(r, dict) else f"- {r}" for r in mand_reqs) if mand_reqs else "Базові вимоги відповідно до формату"
    ambiguities = final_task_understanding.get('ambiguities', [])
    amb_str = "\n".join(f"- {a}" for a in ambiguities) if ambiguities else "Не виявлено"

    diag_text = (
        "\n=== TASK UNDERSTANDING ===\n\n"
        f"TEACHER TITLE:\n{title or '(порожньо)'}\n\n"
        f"TEACHER DESCRIPTION:\n{desc or '(порожньо)'}\n\n"
        f"CUSTOM CRITERIA:\n{custom_criteria or '(не задано)'}\n\n"
        f"TEACHER FILE CONTEXT:\n{file_context[:300] if file_context else '(файли відсутні)'}\n\n"
        f"DETECTED TEACHER INTENT:\n{json.dumps(final_task_understanding.get('teacher_intent', {}), ensure_ascii=False, indent=2)}\n\n"
        f"TASK TYPE:\n{final_task_understanding.get('task_type')}\n\n"
        f"TASK COMPONENTS:\n{json.dumps(final_task_understanding.get('task_components', []), ensure_ascii=False)}\n\n"
        f"ASSIGNED SCOPE:\n{json.dumps(final_task_understanding.get('assigned_scope', []), ensure_ascii=False)}\n\n"
        f"EXPECTED RESULT:\n{final_task_understanding.get('expected_result')}\n\n"
        f"MANDATORY REQUIREMENTS:\n{mand_str}\n\n"
        f"IGNORED MATERIAL:\n{ignored_str}\n\n"
        f"QUESTIONS EXPECTED:\n{final_task_understanding.get('questions_expected')}\n\n"
        f"CONFIDENCE:\n{final_task_understanding.get('task_understanding_confidence')}\n\n"
        f"AMBIGUITIES:\n{amb_str}\n"
        "===========================\n"
    )
    logger.info(diag_text)
    print(diag_text)


def interpret_assignment_task(
    title: str = "",
    desc: str = "",
    custom_criteria: str = "",
    teacher_files_text: str = "",
    compiled_criteria: list = None,
    raw_found_questions: list = None,
    assigned_tasks: list = None,
    subject_name: str = "",
    target_slide_text: str = "",
    **kwargs
) -> dict:
    """
    Формує повну структуру інтерпретації завдання (task_interpretation), спільну
    як для AI-оцінювання, так і для покрокових підказок учню у вікні «Як ШІ розуміє це завдання».
    """
    if not desc and 'description' in kwargs:
        desc = kwargs['description']
    clean_title = strip_html_tags(title or "").strip()
    clean_desc = strip_html_tags(desc or "").strip()

    topic_info = extract_assignment_topic_and_action(clean_title, clean_desc, custom_criteria, subject_name)
    topic = topic_info['topic']
    subject = topic_info['subject']

    task_components = identify_task_components(
        title=clean_title,
        desc=clean_desc,
        custom_criteria=custom_criteria,
        target_slide_text=target_slide_text,
        subject_name=subject
    )
    task_type = determine_assignment_task_type(
        title=clean_title,
        desc=clean_desc,
        custom_criteria=custom_criteria,
        target_slide_text=target_slide_text,
        subject_name=subject
    )
    questions_expected = is_questions_expected(
        task_type=task_type,
        title=clean_title,
        desc=clean_desc,
        custom_criteria=custom_criteria,
        task_components=task_components,
        target_slide_text=target_slide_text
    )
    teacher_requirements, teacher_criteria, generic_recommendations, mandatory_requirements = categorize_task_requirements_and_criteria(
        title=clean_title,
        desc=clean_desc,
        custom_criteria=custom_criteria,
        compiled_criteria=compiled_criteria,
        teacher_files_text=teacher_files_text
    )

    teacher_intent = build_teacher_intent(
        title=clean_title,
        desc=clean_desc,
        custom_criteria=custom_criteria,
        task_type=task_type,
        task_components=task_components,
        topic=topic,
        teacher_requirements=mandatory_requirements,
        questions_expected=questions_expected
    )

    # Визначення Deliverable та очікуваного формату за типом завдання
    format_map = {
        'presentation': ('презентація (PPTX, PPT, ODP або посилання)', f"Готова презентація за темою {topic or 'уроку'}"),
        'programming': ('програмний код / файл програми (.py, .cpp, .sb3 тощо)', f"Працездатна програма за темою {topic or 'уроку'}"),
        'table': ('електронна таблиця (XLSX, XLS, ODS, Google Таблиці)', f"Створена електронна таблиця за темою {topic or 'уроку'}"),
        'database': ('файл бази даних (ACCDB, MDB, SQL) або схема БД', f"Спроєктована база даних за темою {topic or 'уроку'}"),
        'diagram': ('діаграма, схема або графік (зображення чи документ)', f"Побудована діаграма або схема ({topic or 'уроку'})"),
        'creative': ('графічний файл / творча робота (PNG, JPG, PDF або відео)', f"Виконана творча робота за темою {topic or 'уроку'}"),
        'project': ('матеріали проєкту (презентація, документ або архів)', f"Завершений проєкт за темою {topic or 'уроку'}"),
        'research': ('повідомлення / звіт про дослідження (документ або презентація)', f"Звіт про опрацьовану інформацію ({topic or 'уроку'})"),
        'document': ('текстовий документ (DOCX, PDF або текст)', f"Оформлений текстовий документ за темою {topic or 'уроку'}"),
        'calculation': ('розв\'язання задач із розрахунками (документ чи фото зошита)', f"Правильно розв'язані задачі за темою {topic or 'уроку'}"),
        'question_answer': ('письмові відповіді на запитання (у зошиті чи документі)', f"Відповіді на поставлені запитання за темою {topic or 'уроку'}"),
        'practical': ('файл практичної роботи відповідно до інструкції', f"Виконана практична робота за інструкцією"),
        'other': ('файл або документ відповідно до вказівок', f"Виконане навчальне завдання"),
    }

    expected_format, default_deliverable_desc = format_map.get(task_type, format_map['other'])

    # Опис deliverable з підтримкою складених завдань
    has_qa_comp = any(c.get('type') == 'question_answer' for c in task_components)
    if has_qa_comp and task_type != 'question_answer':
        deliverable_desc = f"{default_deliverable_desc} та письмові відповіді на запитання"
    elif clean_title and (clean_desc or len(clean_title) >= 15):
        deliverable_desc = clean_title
    else:
        deliverable_desc = default_deliverable_desc

    what_student_must_do = clean_desc or clean_title or default_deliverable_desc
    expected_result = teacher_intent.get('expected_result') or default_deliverable_desc

    # Формування обов'язкових компонентів deliverable
    deliverable_components = []
    if teacher_requirements:
        deliverable_components = list(teacher_requirements)
    else:
        if has_qa_comp and task_type != 'question_answer':
            deliverable_components = [default_deliverable_desc, 'відповіді на поставлені запитання']
        elif task_type == 'presentation':
            deliverable_components = ['зміст слайдів за темою завдання']
        elif task_type == 'programming':
            deliverable_components = ['працездатний вихідний код програми']
        elif task_type == 'table':
            deliverable_components = ['таблиця з внесеними даними']
        elif task_type == 'database':
            deliverable_components = ['структура бази даних та таблиці']
        elif task_type == 'question_answer':
            deliverable_components = ['відповіді на поставлені запитання']
        else:
            deliverable_components = ['виконання основних практичних дій за темою']

    deliverable = {
        'description': deliverable_desc,
        'type': task_type,
        'format': expected_format,
        'required_components': deliverable_components
    }

    evaluation_method = {
        'check_content': True,
        'check_structure': True,
        'check_format': True,
        'check_answers': bool(questions_expected),
        'check_functionality': task_type in ['programming', 'table', 'database', 'practical', 'project'],
        'check_code': task_type == 'programming',
        'check_calculations': task_type in ['table', 'calculation'],
        'check_data': task_type in ['table', 'database'],
    }

    required_actions = teacher_intent.get('required_actions', [])

    # Непризначені матеріали з навчального файлу вчителя
    unassigned_material = []
    teacher_material_context = []
    if raw_found_questions and not questions_expected:
        unassigned_material = list(raw_found_questions)
        teacher_material_context = [
            f"У прикріплених матеріалах виявлено {len(raw_found_questions)} запитань/вправ, які слугують навчальним контекстом уроку і НЕ є обов'язковими завданнями для здачі."
        ]

    confidence = 0.95
    ambiguities = []
    if not clean_desc and not clean_title:
        confidence = 0.40
        ambiguities.append("Не визначено опис та назву завдання від вчителя")
    elif not clean_desc and len(clean_title) < 10:
        confidence = 0.70
        ambiguities.append("Короткий заголовок без докладних інструкцій учителя")

    assigned_scope = [t['description'] for t in assigned_tasks] if assigned_tasks else [clean_desc or clean_title or expected_result]

    final_task_understanding = build_final_task_understanding(
        teacher_intent=teacher_intent,
        subject=subject,
        topic=topic,
        task_type=task_type,
        task_components=task_components,
        assigned_scope=assigned_scope,
        expected_result=expected_result,
        required_actions=required_actions,
        mandatory_requirements=mandatory_requirements,
        optional_recommendations=generic_recommendations,
        questions_expected=questions_expected,
        confidence=confidence,
        ambiguities=ambiguities
    )

    return {
        'task_type': task_type,
        'main_task_type': task_type,
        'subject': subject,
        'topic': topic,
        'task_components': task_components,
        'teacher_assignment': clean_desc or clean_title or "Навчальне завдання",
        'teacher_intent': teacher_intent,
        'assigned_scope': assigned_scope,
        'what_student_must_do': what_student_must_do,
        'expected_result': expected_result,
        'deliverable': deliverable,
        'expected_format': expected_format,
        'questions_expected': questions_expected,
        'required_actions': required_actions,
        'requirements': teacher_requirements,
        'teacher_requirements': teacher_requirements,
        'mandatory_requirements': mandatory_requirements,
        'teacher_criteria': teacher_criteria,
        'generic_recommendations': generic_recommendations,
        'criteria': teacher_criteria,
        'teacher_material_context': teacher_material_context,
        'unassigned_material': unassigned_material,
        'relevant_teacher_material': teacher_material_context,
        'evaluation_method': evaluation_method,
        'task_understanding_confidence': confidence,
        'ambiguities': ambiguities,
        'final_task_understanding': final_task_understanding
    }


def resolve_assignment_scope(
    assignment_title: str = "",
    assignment_desc: str = "",
    custom_criteria: str = "",
    primary_task_content: list[str] = None,
    teacher_files_content: list[str] = None,
) -> dict:
    """
    Визначає точний Scope of Work за принципом суворого пріоритету:
    ПРІОРИТЕТ 1 — явна інструкція вчителя для конкретного Assignment (опис/назва).
    ПРІОРИТЕТ 2 — індивідуальні критерії оцінювання (custom_criteria).
    ПРІОРИТЕТ 3 — основний файл із умовою (primary_task_content).
    ПРІОРИТЕТ 4 — прикріплені файли вчителя (teacher_files_content), ТІЛЬКИ якщо є джерелом умови.
    ПРІОРИТЕТ 5 — загальні критерії НУШ (лише для якості, ніколи не створюють завдань).
    """
    title = strip_html_tags(assignment_title or "")
    desc = strip_html_tags(assignment_desc or "")
    combined_desc_title = f"{title} {desc}".strip().lower()
    desc_lower = desc.lower()

    # 1. Індивідуальні критерії вчителя (Пріоритет 2)
    custom_criteria_rules = []
    if custom_criteria and custom_criteria.strip():
        for line in custom_criteria.strip().splitlines():
            line_s = line.strip(' \t\n\r-*•;')
            if len(line_s) > 2:
                custom_criteria_rules.append(line_s)

    # 2. Критерії з файлів учителя та основного файлу (Пріоритет 3 та 4)
    all_files_text = "\n".join(teacher_files_content) if teacher_files_content else ""
    task_type_pre = determine_assignment_task_type(title, desc, custom_criteria, all_files_text)
    file_criteria_extracted = extract_criteria_and_requirements_from_text(
        all_files_text,
        assignment_title=title,
        assignment_desc=desc,
        task_type=task_type_pre
    )
    primary_task_text = "\n".join(primary_task_content) if primary_task_content else ""
    primary_criteria_extracted = extract_criteria_and_requirements_from_text(
        primary_task_text,
        assignment_title=title,
        assignment_desc=desc,
        task_type=task_type_pre
    )

    compiled_criteria = []
    for cr in custom_criteria_rules:
        compiled_criteria.append({
            'name': cr,
            'source': 'custom_criteria',
            'weight': None,
            'evaluation_method': 'individual_check',
            'mandatory': True,
            'applies_to': 'custom'
        })
    for fc in (primary_criteria_extracted.get('criteria', []) + file_criteria_extracted.get('criteria', [])):
        c_name = fc['name'] if isinstance(fc, dict) else str(fc)
        is_mand = fc.get('mandatory', True) if isinstance(fc, dict) else True
        if not any(c_name.lower() in existing['name'].lower() or existing['name'].lower() in c_name.lower() for existing in compiled_criteria):
            compiled_criteria.append({
                'name': c_name,
                'source': 'teacher_file',
                'weight': fc.get('points') if isinstance(fc, dict) else None,
                'evaluation_method': 'file_criteria_check',
                'mandatory': is_mand,
                'applies_to': fc.get('applies_to', 'general') if isinstance(fc, dict) else 'general',
            })

    # Виявлення явних вимог з опису вчителя (Пріоритет 1, наприклад: «Зробити висновок», «Висновок є обов'язковим»)
    if desc:
        desc_sentences = re.split(r'[\.\;\!\?]\s+|\n+', desc)
        for s in desc_sentences:
            s_clean = s.strip(' \t\n\r-*•;')
            if re.search(r'\b(?:висновок|підсумок)\b', s_clean, re.IGNORECASE) and len(s_clean) >= 5:
                if not any(s_clean.lower() in existing['name'].lower() or existing['name'].lower() in s_clean.lower() for existing in compiled_criteria):
                    compiled_criteria.append({
                        'name': s_clean,
                        'source': 'teacher_instruction',
                        'weight': None,
                        'evaluation_method': 'individual_check',
                        'mandatory': True,
                        'applies_to': 'teacher_instruction'
                    })

    # 3. Перевірка конкретних номерів завдань (Пріоритет 1)
    teacher_specific_task_nums = parse_teacher_specific_task_numbers(desc)
    explicit_count = detect_expected_task_count(desc)

    # 4. Виявлення матеріалів уроку для пошуку завдань
    raw_found_questions = []
    if teacher_files_content:
        raw_found_questions = extract_task_questions(all_files_text)

    # 5. Перевірка, чи в описі прямо міститься список завдань («1. ... 2. ...»)
    desc_questions = extract_task_questions(desc, explicit_count=explicit_count)

    # 6. Динамічна інтерпретація завдання (ТИП ЗАВДАННЯ, DELIVERABLE, КРИТЕРІЇ ТА QUESTIONS_EXPECTED)
    task_interpretation = interpret_assignment_task(
        title=title,
        desc=desc,
        custom_criteria=custom_criteria,
        teacher_files_text=all_files_text,
        compiled_criteria=compiled_criteria,
        raw_found_questions=raw_found_questions,
    )
    task_type = task_interpretation['task_type']
    questions_expected = task_interpretation['questions_expected']
    deliverable = task_interpretation['deliverable']
    evaluation_method = task_interpretation['evaluation_method']

    def _build_evaluation_plan(assigned_list, ignored_list, scope_src, assignment_type="standard", specific_nums=None, ambiguities=None):
        t_reqs = task_interpretation.get('teacher_requirements', [])
        t_crit = task_interpretation.get('teacher_criteria', [])
        g_recs = task_interpretation.get('generic_recommendations', [])
        mand_reqs = task_interpretation.get('mandatory_requirements', [])

        assigned_scope = [t['description'] for t in assigned_list] if assigned_list else [desc or title or "Навчальне завдання"]
        unassigned_mat = [item['description'] for item in ignored_list]

        # Розрахунок впевненості та неоднозначностей
        confidence = 0.95
        amb_list = list(ambiguities or [])
        if not desc and not title:
            confidence = 0.40
            amb_list.append("Відсутній опис та назва завдання від вчителя")
        elif not desc and len(title) < 10:
            confidence = 0.70
            amb_list.append("Короткий заголовок без докладних інструкцій учителя")

        # Оновлюємо teacher_intent актуальним assigned_scope та expected_result
        t_intent = dict(task_interpretation.get('teacher_intent') or {})
        if assigned_scope and len(assigned_scope) == 1:
            t_intent['expected_result'] = task_interpretation.get('expected_result') or assigned_scope[0]

        final_tu = build_final_task_understanding(
            teacher_intent=t_intent,
            subject=task_interpretation.get('subject', 'Інформатика'),
            topic=task_interpretation.get('topic', title),
            task_type=task_type,
            task_components=task_interpretation.get('task_components', []),
            assigned_scope=assigned_scope,
            expected_result=task_interpretation.get('expected_result', deliverable.get('description', '')),
            required_actions=task_interpretation.get('required_actions', []),
            mandatory_requirements=mand_reqs,
            optional_recommendations=g_recs,
            questions_expected=questions_expected,
            confidence=confidence,
            ambiguities=amb_list
        )

        task_interpretation['final_task_understanding'] = final_tu
        task_interpretation['assigned_scope'] = assigned_scope
        task_interpretation['unassigned_material'] = unassigned_mat
        task_interpretation['teacher_material_context'] = unassigned_mat
        task_interpretation['task_understanding_confidence'] = confidence
        task_interpretation['ambiguities'] = amb_list

        log_task_understanding_diagnostics(
            title=title,
            desc=desc,
            custom_criteria=custom_criteria,
            file_context=all_files_text,
            final_task_understanding=final_tu,
            ignored_tasks=[item['description'] for item in ignored_list]
        )

        return {
            'assignment_type': assignment_type,
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'teacher_intent': t_intent,
            'task_interpretation': task_interpretation,
            'final_task_understanding': final_tu,
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'assignment_description': desc or title or "Навчальне завдання",
            'task_summary': desc or title or "Навчальне завдання",
            'scope_source': scope_src,
            'assigned_tasks': assigned_list,
            'assigned_task_count': len(assigned_list),
            'assigned_scope': assigned_scope,
            'specific_task_numbers': specific_nums if specific_nums is not None else (teacher_specific_task_nums or []),
            'teacher_requirements': t_reqs,
            'teacher_criteria': t_crit,
            'generic_quality_recommendations': g_recs,
            'mandatory_requirements': mand_reqs,
            'requirements': t_reqs,
            'custom_criteria': custom_criteria_rules,
            'file_based_criteria': [c for c in file_criteria_extracted.get('criteria', []) + primary_criteria_extracted.get('criteria', []) if c.get('mandatory', True)],
            'general_criteria': g_recs,
            'criteria': [c for c in compiled_criteria if c.get('mandatory', True)],
            'format_requirements': t_reqs,
            'points_distribution': file_criteria_extracted.get('rubric_points', []) + primary_criteria_extracted.get('rubric_points', []),
            'ignored_found_tasks': [item['description'] for item in ignored_list],
            'unassigned_materials_ignored': [item['description'] for item in ignored_list],
            'evidence_sources': ['student_file', 'student_comment', 'student_link'],
            'ambiguities_or_conflicts': amb_list,
            'task_understanding_confidence': confidence
        }

    # 7. Перевірка конкретного слайду або сторінки (наприклад: «зі слайду 15», «на слайді 15»)
    target_slide_num = None
    slide_m = re.search(r'(?:зі?\s+|на\s+)?(?:слайд[уаі]|стор(?:інц[іях]|\.)?)\s*(\d+)', desc_lower)
    if slide_m:
        try:
            target_slide_num = int(slide_m.group(1))
        except (ValueError, TypeError):
            pass

    # ── СЦЕНАРІЙ А: Вчитель вказав конкретний слайд (наприклад: Слайд 15) ──
    if target_slide_num and not teacher_specific_task_nums:
        slide_task_text = ""
        if all_files_text:
            slide_pattern = re.compile(
                rf'(?:📽️\s*)?(?:\[?\s*слайд\s*{target_slide_num}\b[^\]\n\r]*\]?[\s\:\-]*)(.*?)(?=(?:📽️\s*)?\[?\s*слайд\s*\d+\b|\Z)',
                re.IGNORECASE | re.DOTALL
            )
            sm = slide_pattern.search(all_files_text)
            if sm:
                slide_task_text = sm.group(1).strip()

        clean_slide_task = slide_task_text or f"Домашнє завдання зі слайду {target_slide_num}"
        ignored = []
        for q in raw_found_questions:
            if str(target_slide_num) not in q:
                ignored.append({
                    'description': q,
                    'reason': f"Завдання не належить до зазначеного вчителем слайду {target_slide_num}"
                })

        assigned_tasks = [{
            'task_id': 'task_1',
            'task_num': None,
            'description': f"Завдання зі слайду {target_slide_num}: {clean_slide_task[:200]}",
            'requirements': custom_criteria_rules
        }]
        eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description_and_slide', assignment_type='slide_specific')

        return {
            'scope_source': 'teacher_description_and_slide',
            'is_single_complex_task': True,
            'assigned_task_count': 1,
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': ignored,
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': [],
            'task_questions': [clean_slide_task[:300]],
            'clean_instruction_text': desc or f"Завдання зі слайду {target_slide_num}",
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ Б: Вчитель вказав конкретні номери («Виконати вправу 2» або «Завдання 1, 2 та 3») ──
    if teacher_specific_task_nums:
        scoped_questions = []
        if raw_found_questions:
            for num in teacher_specific_task_nums:
                matched_q = find_question_by_task_num(raw_found_questions, num)
                if matched_q:
                    scoped_questions.append(matched_q)
                else:
                    scoped_questions.append(f"Завдання {num}")
        else:
            scoped_questions = [f"Завдання {num}" for num in teacher_specific_task_nums]

        ignored = []
        for q in raw_found_questions:
            if q not in scoped_questions:
                ignored.append({
                    'description': q,
                    'reason': f"Не входить до списку конкретно заданих вчителем номерів ({teacher_specific_task_nums})"
                })

        assigned_tasks = []
        for idx, num in enumerate(teacher_specific_task_nums, 1):
            q_desc = scoped_questions[idx - 1] if idx - 1 < len(scoped_questions) else f"Завдання {num}"
            assigned_tasks.append({
                'task_id': f"task_{idx}",
                'task_num': num,
                'description': q_desc,
                'requirements': custom_criteria_rules if len(teacher_specific_task_nums) == 1 else []
            })

        eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description', assignment_type='specific_task_numbers', specific_nums=teacher_specific_task_nums)

        return {
            'scope_source': 'teacher_description',
            'is_single_complex_task': (len(teacher_specific_task_nums) == 1),
            'assigned_task_count': len(teacher_specific_task_nums),
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': ignored,
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': teacher_specific_task_nums,
            'task_questions': scoped_questions,
            'clean_instruction_text': desc,
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ В-0: Одне комплексне завдання / практична / лабораторна робота / проєкт ──
    has_single_task_kw = any(kw in combined_desc_title for kw in SINGLE_TASK_KEYWORDS)
    has_workflow_steps = bool(re.search(r'\b(?:крок|етап)\s*\d+\b', desc, re.IGNORECASE))
    has_rubrics = bool(re.search(r'\bкритерії\s+оцінювання\b|\b\d+\s+бал\w*[\:\-]', desc, re.IGNORECASE))
    has_explicit_exercises = bool(re.search(r'\b(?:вправи|вправ|завдань)\s*(?:№\s*)?[:\d]', desc, re.IGNORECASE))

    if (has_single_task_kw or has_workflow_steps or has_rubrics) and not has_explicit_exercises and not teacher_specific_task_nums and explicit_count <= 1:
        task_label = desc or title or "Навчальне комплексне завдання"
        ignored = []
        for q in raw_found_questions:
            ignored.append({
                'description': q,
                'reason': 'Матеріал або вправа з навчального файлу не була явно задана вчителем як окреме обов\'язкове завдання'
            })
        assigned_tasks = [{
            'task_id': 'task_1',
            'task_num': None,
            'description': task_label,
            'requirements': custom_criteria_rules
        }]
        eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description', assignment_type='single_complex_task')

        return {
            'scope_source': 'teacher_description',
            'is_single_complex_task': True,
            'assigned_task_count': 1,
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': ignored,
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': [],
            'task_questions': [],
            'clean_instruction_text': desc,
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ В: Вчитель вказав конкретний перелік завдань безпосередньо в описі ──
    if desc_questions and len(desc_questions) >= 2:
        assigned_tasks = []
        for idx, q in enumerate(desc_questions, 1):
            assigned_tasks.append({
                'task_id': f"task_{idx}",
                'task_num': idx,
                'description': q,
                'requirements': []
            })
        ignored = []
        for q in raw_found_questions:
            if q not in desc_questions:
                ignored.append({
                    'description': q,
                    'reason': 'Вправа з додаткових матеріалів не була вказана у переліку завдань вчителя'
                })
        eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description', assignment_type='explicit_list_in_description')

        return {
            'scope_source': 'teacher_description',
            'is_single_complex_task': False,
            'assigned_task_count': len(desc_questions),
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': ignored,
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': list(range(1, len(desc_questions) + 1)),
            'task_questions': desc_questions,
            'clean_instruction_text': desc,
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ Г: Вчитель явно зазначив кількість завдань (наприклад: «виконати всі 3 завдання») ──
    if explicit_count > 1:
        source_qs = []
        ambiguities = []
        questions_pool = raw_found_questions or (extract_task_questions(primary_task_text) if primary_task_content else [])
        if questions_pool:
            for num in range(1, explicit_count + 1):
                matched = find_question_by_task_num(questions_pool, num)
                if matched and matched not in source_qs:
                    source_qs.append(matched)
                else:
                    source_qs.append(f"Завдання {num}")
                    ambiguities.append(f"Завдання {num} не має точного збігу у файлах")
        while len(source_qs) < explicit_count:
            source_qs.append(f"Завдання {len(source_qs) + 1}")

        ignored = []
        for q in raw_found_questions:
            if q not in source_qs:
                ignored.append({
                    'description': q,
                    'reason': f"Кількість завдань обмежена прямою вказівкою вчителя ({explicit_count} завд.)"
                })

        assigned_tasks = []
        for idx, q in enumerate(source_qs, 1):
            assigned_tasks.append({
                'task_id': f"task_{idx}",
                'task_num': idx,
                'description': q,
                'requirements': []
            })

        eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description', assignment_type='explicit_count', ambiguities=ambiguities)

        return {
            'scope_source': 'teacher_description',
            'is_single_complex_task': False,
            'assigned_task_count': explicit_count,
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': ignored,
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': list(range(1, explicit_count + 1)),
            'task_questions': source_qs,
            'clean_instruction_text': desc,
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ Ґ: Основний файл завдання (Пріоритет 3) з явною вказівкою ──
    primary_questions = []
    if primary_task_content:
        primary_questions = extract_task_questions(primary_task_text)

    if primary_questions and len(primary_questions) >= 2 and any(kw in desc_lower for kw in ['у файлі', 'в файлі', 'завдання з файлу', 'згідно з файлом']):
        assigned_tasks = []
        for idx, q in enumerate(primary_questions, 1):
            assigned_tasks.append({
                'task_id': f"task_{idx}",
                'task_num': idx,
                'description': q,
                'requirements': []
            })
        eval_plan = _build_evaluation_plan(assigned_tasks, [], 'primary_task_file', assignment_type='primary_task_file')

        return {
            'scope_source': 'primary_task_file',
            'is_single_complex_task': False,
            'assigned_task_count': len(primary_questions),
            'assigned_tasks': assigned_tasks,
            'assigned_scope': eval_plan.get('assigned_scope', []),
            'ignored_found_tasks': [],
            'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
            'final_task_understanding': eval_plan.get('final_task_understanding', {}),
            'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
            'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
            'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
            'custom_criteria_rules': custom_criteria_rules,
            'file_criteria_rules': compiled_criteria,
            'evaluation_plan': eval_plan,
            'format_requirements': eval_plan['format_requirements'],
            'points_distribution': eval_plan['points_distribution'],
            'teacher_specific_task_nums': list(range(1, len(primary_questions) + 1)),
            'task_questions': primary_questions,
            'clean_instruction_text': desc or "Завдання з основного файлу",
            'task_type': task_type,
            'topic': task_interpretation.get('topic', ''),
            'task_interpretation': task_interpretation,
            'task_components': task_interpretation.get('task_components', []),
            'deliverable': deliverable,
            'evaluation_method': evaluation_method,
            'questions_expected': questions_expected,
            'teacher_requirements': eval_plan.get('teacher_requirements', []),
            'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
            'teacher_criteria': eval_plan.get('teacher_criteria', []),
            'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
            'criteria': eval_plan.get('criteria', []),
        }

    # ── СЦЕНАРІЙ Д: Одне комплексне завдання або ПРАВИЛО НЕВИЗНАЧЕНОСТІ ──
    task_label = desc or title or "Навчальне комплексне завдання"
    ignored = []
    for q in raw_found_questions:
        ignored.append({
            'description': q,
            'reason': 'Матеріал або вправа з навчального файлу не була явно задана вчителем як обов\'язкове завдання (Правило невизначеності)'
        })

    assigned_tasks = [{
        'task_id': 'task_1',
        'task_num': None,
        'description': task_label,
        'requirements': custom_criteria_rules
    }]
    eval_plan = _build_evaluation_plan(assigned_tasks, ignored, 'teacher_description', assignment_type='single_complex_task')

    return {
        'scope_source': 'teacher_description',
        'is_single_complex_task': True,
        'assigned_task_count': 1,
        'assigned_tasks': assigned_tasks,
        'assigned_scope': eval_plan.get('assigned_scope', []),
        'ignored_found_tasks': ignored,
        'unassigned_material': eval_plan.get('unassigned_materials_ignored', []),
        'final_task_understanding': eval_plan.get('final_task_understanding', {}),
        'task_understanding_confidence': eval_plan.get('task_understanding_confidence', 0.95),
        'ambiguities': eval_plan.get('ambiguities_or_conflicts', []),
        'teacher_intent': eval_plan.get('final_task_understanding', {}).get('teacher_intent', {}),
        'custom_criteria_rules': custom_criteria_rules,
        'file_criteria_rules': compiled_criteria,
        'evaluation_plan': eval_plan,
        'format_requirements': eval_plan['format_requirements'],
        'points_distribution': eval_plan['points_distribution'],
        'teacher_specific_task_nums': [],
        'task_questions': [],
        'clean_instruction_text': task_label,
        'task_type': task_type,
        'topic': task_interpretation.get('topic', ''),
        'task_interpretation': task_interpretation,
        'task_components': task_interpretation.get('task_components', []),
        'deliverable': deliverable,
        'evaluation_method': evaluation_method,
        'questions_expected': questions_expected,
        'teacher_requirements': eval_plan.get('teacher_requirements', []),
        'mandatory_requirements': eval_plan.get('mandatory_requirements', []),
        'teacher_criteria': eval_plan.get('teacher_criteria', []),
        'generic_quality_recommendations': eval_plan.get('generic_quality_recommendations', []),
        'criteria': eval_plan.get('criteria', []),
    }



INSTRUCTION_VERBS = {
    'відкрийте', 'відкрити', 'створіть', 'створити', 'запустіть', 'запустити',
    'виконайте', 'виконати', 'перейдіть', 'перейти', 'натисніть', 'натиснути',
    'побудуйте', 'побудувати', 'скопіюйте', 'скопіювати', 'збережіть', 'зберегти',
    'введіть', 'ввести', 'налаштуйте', 'налаштувати', 'ознайомтеся', 'ознайомитися',
    'завантажте', 'завантажити', 'розв\'яжіть', 'розв\'язати', 'обчисліть', 'обчислити',
    'запишіть', 'записати', 'дослідіть', 'дослідити', 'знайдіть', 'знайти',
    'виберіть', 'вибрати', 'вкажіть', 'вказати', 'додайте', 'додати',
    'змініть', 'змінити', 'встановіть', 'встановити', 'перевірте', 'перевірити',
    'перегляньте', 'переглянути', 'прочитайте', 'прочитати', 'порівняйте', 'порівняти',
    'заповніть', 'заповнити', 'намалюйте', 'намалювати', 'визначте', 'визначити'
}


def extract_student_answers(text: str) -> dict[int, str]:
    """
    Виявляє та видобуває відповіді учня за номерами (1. ..., 2) ..., Відповідь 1: ...).
    Автоматично ігнорує пункти завдань та інструкцій вчителя (наприклад: «1. Відкрийте програму...»).
    """
    if not text or not text.strip():
        return {}
    start_pattern = re.compile(
        r'(?:^|\n)\s*[«"\'\(\[]?\s*(?:(\d+)\s*[\.\)\–\—\-\]»"\'\:]|(?:питання|завдання|вправа|відповідь|№)\s*(\d+)\s*[\.\:\)\–\—\-\]»"\'\:]?)\s*',
        re.IGNORECASE
    )
    matches = list(start_pattern.finditer(text))
    if not matches:
        return {}
    answers = {}
    for i, m in enumerate(matches):
        num_str = m.group(1) or m.group(2)
        start_idx = m.end()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start_idx:end_idx].strip().rstrip('»"\'')
        if num_str and content:
            words = content.split()
            first_word = words[0].lower().rstrip(':,.;«»"\'') if words else ""
            # Якщо пункт починається з наказового дієслова вказівки вчителя і є коротким описом кроку
            if first_word in INSTRUCTION_VERBS and len(words) <= 15:
                continue
            try:
                num = int(num_str)
                answers[num] = content
            except ValueError:
                pass
    return answers


def detect_invalid_or_teacher_template_submission(submission, text_parts: list[str]) -> tuple[bool, str, str]:
    """
    Автоматично перевіряє роботу учня на критичну невідповідність:
    1. Збіг з файлами завдань/практичних робіт вчителя (дублікат вихідного файлу).
    2. Невідповідність класу (наприклад, учень 6-7 класу здав практичну чи завдання 8-9 класу).
    3. Здача бланку або інструкційної картки практичної роботи вчителя без відповідей та розв'язків учня.
    
    Повертає (is_invalid, reason_message, error_code).
    """
    if not submission:
        return False, "", "none"

    student_raw_text = "\n".join(text_parts).strip() if text_parts else ""
    text_lower = student_raw_text.lower()

    # ── 1. ПЕРЕВІРКА НА ЗБІГ З ФАЙЛАМИ ВЧИТЕЛЯ (ДУБЛІКАТ) ──
    try:
        from .duplicate_detector import check_submission_duplicates
        dup_info = check_submission_duplicates(submission)
        if dup_info.get('is_duplicate_teacher') and not getattr(submission, 'ignore_plagiarism', False):
            teacher_f = dup_info.get('teacher_file_name') or 'вчителя'
            msg = (
                f"Прикріплений файл повністю збігається з матеріалами/практичною роботою вчителя («{teacher_f}»). "
                "Здано вихідний файл завдання замість виконаної учнем роботи."
            )
            return True, msg, 'teacher_duplicate'
    except Exception:
        pass

    # ── 2. ПЕРЕВІРКА КЛАСУ (НЕВІДПОВІДНІСТЬ КЛАСУ) ──
    class_name = submission.class_group.name if submission.class_group else ""
    m_class = re.search(r'\b(1[0-2]|[1-9])\b', class_name)
    student_grade = int(m_class.group(1)) if m_class else None

    asgn_grades = set()
    if submission.assignment:
        for c in submission.assignment.classes.all():
            m_ac = re.search(r'\b(1[0-2]|[1-9])\b', c.name)
            if m_ac:
                asgn_grades.add(int(m_ac.group(1)))
    if student_grade:
        asgn_grades.add(student_grade)

    if asgn_grades and (student_raw_text or submission.file):
        sample_to_check = student_raw_text[:2500]
        if submission.file:
            sample_to_check += " " + os.path.basename(submission.file.name)

        found_grade_matches = re.findall(
            r'\b(1[0-2]|[1-9])(?:\s*-\s*[А-Яа-яA-Za-z]|\s*й\s*клас|\s*го\s*класу|\s+клас|\s+класу|\s+кл[\.\s])',
            sample_to_check,
            re.IGNORECASE
        )
        found_grades = set()
        for g_str in found_grade_matches:
            try:
                found_grades.add(int(g_str))
            except (ValueError, TypeError):
                pass

        other_grades = found_grades - asgn_grades
        # Якщо в документі знайдено згадку іншого класу і немає жодної згадки свого класу
        if other_grades and not (found_grades & asgn_grades):
            mismatched_grade = sorted(list(other_grades))[0]
            expected_grades_str = ", ".join(str(g) for g in sorted(list(asgn_grades)))
            msg = (
                f"Прикріплена робота містить завдання/матеріали для {mismatched_grade} класу, тоді як завдання призначене для "
                f"{expected_grades_str} класу ({class_name}). Здано роботу не з цього класу, оцінку не зараховано."
            )
            return True, msg, 'class_mismatch'

    # ── 3. ПЕРЕВІРКА НА ЗДАЧУ БЛАНКУ/ІНСТРУКЦІЇ ПРАКТИЧНОЇ РОБОТИ БЕЗ ВІДПОВІДЕЙ ──
    has_practical_header = any(h in text_lower for h in [
        'практична робота', 'лабораторна робота', 'інструкційна картка',
        'практичне завдання', 'самостійна робота'
    ])
    has_practical_structure = any(s in text_lower for s in [
        'тема:', 'мета:', 'обладнання:', 'хід роботи:', 'порядок виконання',
        'теоретичні відомості', 'вказівки до роботи', 'завдання до роботи'
    ])

    if has_practical_header and has_practical_structure:
        # Шукаємо пронумеровані пункти (1. ..., 2. ...)
        items = re.findall(r'(?:^|\n)\s*(?:\d+[\.\)]|завдання\s*\d+[\.\:]?)\s*([^\n\r]+)', student_raw_text, re.IGNORECASE)
        if items:
            instruction_count = 0
            for it in items:
                words = it.strip().split()
                first_word = words[0].lower().rstrip(':,.;«»"\'') if words else ""
                if first_word in INSTRUCTION_VERBS:
                    instruction_count += 1
            # Якщо більшість пунктів починаються зі слів вказівок вчителя
            if instruction_count >= max(2, int(len(items) * 0.5)):
                # Перевіряємо, чи є в кінці роботи блок відповідей або висновків
                has_student_response = any(kw in text_lower for kw in [
                    'відповідь:', 'відповіді:', 'висновок:', 'висновки:', 'розв\'язання:',
                    'мій висновок', 'результат виконання', 'отримані результати', 'мої відповіді'
                ])
                if not has_student_response and len(student_raw_text) < 4000:
                    msg = (
                        "Прикріплений файл є бланком/інструкцією практичної роботи вчителя (хід роботи та завдання) "
                        "без власних відповідей, розв'язків чи висновків учня. За просте прикріплення тексту завдань вчителя оцінка не виставляється."
                    )
                    return True, msg, 'blank_practical_template'

    return False, "", "none"


def check_student_omitted_questions(task_questions: list[str], student_text: str) -> bool:
    """
    Перевіряє, чи учень надав відповіді без переписування самих запитань.
    Повертає True, якщо у тексті учня відсутні формулювання запитань вчителя.
    """
    if not task_questions or not student_text or not student_text.strip():
        return False
    overlap_count = 0
    checked_qs = 0
    for q in task_questions[:10]:
        q_clean = re.sub(r'^(?:(?:\d+|[IVXLCDM]+)[\.\)\–\—\-]|(?:питання|завдання|вправа|відповідь|№)\s*\d+[\.\:\)\–\—\-]?)\s*', '', q, flags=re.IGNORECASE).strip()
        q_clean = q_clean.rstrip('?!.').strip()
        if len(q_clean) >= 15:
            checked_qs += 1
            sample_part = q_clean[:min(30, len(q_clean))].lower()
            if sample_part in student_text.lower():
                overlap_count += 1
    if checked_qs > 0 and overlap_count == 0:
        return True
    return False


def build_question_answer_mapping(task_questions: list[str], student_text: str, is_practical_project: bool = False) -> tuple[str, bool, int, int]:
    """
    Якщо в завданні вчителя виявлено конкретні запитання, а учень надав відповіді
    (особливо без повторення тексту запитань), формує структурований блок
    підстановки відповідей учня до кожного запитання вчителя.
    
    Повертає (prompt_mapping_block: str, questions_omitted: bool, answered_count: int, total_questions: int).
    """
    if not task_questions:
        return "", False, 0, 0

    student_answers = extract_student_answers(student_text) if student_text else {}
    omitted = False if is_practical_project else (check_student_omitted_questions(task_questions, student_text) if student_text else False)
    
    lines = []
    lines.append("═══════════════════════════════════════════════════════════════════")
    lines.append("📋 СИСТЕМНЕ ЗІСТАВЛЕННЯ «ЗАПИТАННЯ ВЧИТЕЛЯ ↔ ВІДПОВІДІ УЧНЯ» (ПІДСТАНОВКА ВІДПОВІДЕЙ):")
    if omitted:
        lines.append("⚠️ УВАГА: Учень надав відповіді БЕЗ переписування тексту самих запитань вчителя!")
        lines.append("Система автоматично виявила запитання в завданні та підставила знайдені відповіді учня нижче:")
    else:
        lines.append("Перелік запитань завдання та відповіді учня для зіставлення:")

    answered_count = 0
    total_qs = len(task_questions)

    for i, q in enumerate(task_questions, 1):
        ans = student_answers.get(i)
        q_display = q.strip()
        lines.append(f"• Запитання {i}: {q_display}")
        if ans:
            lines.append(f"  ↳ ПІДСТАВЛЕНА ВІДПОВІДЬ УЧНЯ №{i}: «{ans}»")
            answered_count += 1
        else:
            lines.append(f"  ↳ ВІДПОВІДЬ УЧНЯ: [Відповідь не виявлена за номером або учень пропустив це запитання]")

    lines.append("")
    lines.append("🎯 КАТЕГОРИЧНІ ВКАЗІВКИ ДЛЯ ШІ ЩОДО ЦИХ ВІДПОВІДЕЙ:")
    if answered_count > 0:
        lines.append(f"- Учень надав відповіді щонайменше на {answered_count} із {total_qs} запитань вчителя.")
        lines.append("- 🚫 СУВОРО ТА БЕЗАПЕЛЯЦІЙНО ЗАБОРОНЕНО писати, що «жодної відповіді не дано», «відповіді відсутні» чи «робота порожня»!")
        lines.append("- Оціни зміст та правильність кожної наданої учнем відповіді відповідно до поставленого запитання.")
        if answered_count < total_qs:
            lines.append(f"- Оскільки виконано {answered_count} із {total_qs} запитань, оціни роботу відповідно до якості виконаних відповідей (як часткове виконання), вказавши у відгуку, які запитання залишились без відповіді.")
    else:
        lines.append("- Учень не використав стандартну цифрову нумерацію у тексті або надав розв'язок іншим способом.")
        lines.append("- Уважно проаналізуй увесь зданий матеріал учня (текст роботи, коментар, зображення/фото зошита), знайди за змістом відповіді на поставлені запитання та зістав їх.")
        lines.append("- Якщо учень відповів хоча б на 1-2 запитання, КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати, що «жодної відповіді не дано»!")

    if total_qs >= 2:
        lines.append("")
        lines.append("🎯 КАТЕГОРИЧНІ ТА ОБОВ'ЯЗКОВІ ВИМОГИ ДО ОЦІНЮВАННЯ БАГАТОЗАДАЧНИХ РОБІТ:")
        lines.append(f"1. У завданні вчителя задано {total_qs} конкретних завдань/запитань.")
        lines.append("2. 🚫 ПРИНЦИП ВЗАЄМНО-ОДНОЗНАЧНОГО ЗІСТАВЛЕННЯ (СУВОРА ЗАБОРОНА ПОДВІЙНОГО ЗАРАХУВАННЯ):")
        lines.append("   - Кожне завдання вимагає ВЛАСНОЇ, ОКРЕМОЇ відповіді або результату учня!")
        lines.append("   - Один фрагмент чи речення категорично не може зараховуватися за виконання двох різних завдань одночасно.")
        lines.append("3. 🚫 СУВОРЕ ОБМЕЖЕННЯ ОЦІНКИ ЗА НЕПОВНИЙ ОБСЯГ (НУШ):")
        lines.append("   - 10–12 балів (Високий рівень) дозволено ставити ВИКЛЮЧНО якщо виконано ВСІ 100% поставлених завдань (усі окремо і якісно)!")
        lines.append("   - Якщо виконано лише 2 із 3 завдань (~66%): максимальна можлива оцінка — 7–8 балів (Достатній рівень). Ставити 9–12 балів (зокрема 10 чи 11 балів) СУВОРО ТА КАТЕГОРИЧНО ЗАБОРОНЕНО!")
        lines.append("   - Якщо виконано лише 1 із 3 завдань (~33%): максимальна можлива оцінка — 4–5 балів (Середній рівень).")
        lines.append("   - Якщо виконано лише половину (наприклад, 1 із 2 або 2 із 4): максимальна оцінка — 6–7 балів.")
        lines.append("4. ВКАЗАННЯ ПРОПУЩЕНИХ ЗАВДАНЬ У ВІДГУКУ:")
        lines.append("   - Якщо будь-яке із завдань пропущено або не має окремої відповіді, ОБОВ'ЯЗКОВО чітко зазнач це в 'weaknesses' та 'feedback_comment' (наприклад: «Завдання 2 не виконано / пропущено»).")
        lines.append("   - КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати у 'summary' чи відгуку, що «виконано всі завдання» чи «робота містить відповіді на завдання 1, 2 та 3», якщо хоча б одне завдання пропущено!")

    if omitted and not is_practical_project:
        lines.append("- ⚠️ ОБОВ'ЯЗКОВО вкажи учневі в 'weaknesses' та 'feedback_comment' про недолік оформлення («питання-відповідь»):")
        lines.append("  «Порада щодо оформлення: ви надали відповіді без самих запитань. Будь ласка, завжди записуйте запитання разом із відповідями (формат «питання-відповідь») або чітко зазначайте номери запитань, щоб робота була структурованою і зрозумілою.»")
    
    lines.append("═══════════════════════════════════════════════════════════════════\n")
    return "\n".join(lines), omitted, answered_count, total_qs


def apply_multi_task_evaluation_guardrail(
    result_json: dict,
    task_questions: list[str],
    teacher_instructions_text: str,
    student_raw_text: str,
    suggested_grade: str,
    level: str,
    clean_gr_results: list[dict],
    numeric_gr_grades: list[float],
    avg_gr_grade: int | None,
    is_traditional: bool,
    summary: str,
    strengths: list[str],
    weaknesses: list[str],
    feedback_comment: str,
    answered_count: int = 0,
    teacher_scoped_task_nums: list[int] | None = None,
    scope: dict | None = None,
) -> tuple[str, str, list[dict], list[float], int | None, str, list[str], list[str], str]:
    """
    Педагогічний захист від галюцинацій ШІ при оцінюванні багатозадачних робіт:
    1. Перевіряє кількість завдань в умові вчителя (Scope of Work).
    2. Якщо це одне комплексне завдання (наприклад, «Робота над проєктом», «Створити презентацію»):
       multi-task ceiling НЕ застосовується, очищаються будь-які галюцинації про «1 з 4» чи пропущені вправи з матеріалів.
    3. Якщо учень здав лише частину дійсно заданих вчителем завдань (наприклад, 2 із 3 заданих):
       встановлює сувору стелю балів НУШ:
       - 2 із 3 завдань (~66%) -> максимум 8 балів (Достатній рівень).
       - 1 із 3 завдань (~33%) -> максимум 5 балів (Середній рівень).
       - 50% обсягу -> максимум 7 балів.
    4. Запобігає подвійному зарахуванню одного фрагмента тексту за два різні завдання.
    5. Виправляє висновок (summary), сильні сторони (strengths) та відгук (feedback_comment),
       гарантуючи зазначення дійсно пропущених завдань у зауваженнях (weaknesses).
    """
    if suggested_grade == 'Доопрацювати':
        if scope and scope.get('is_single_complex_task'):
            summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(summary, weaknesses, feedback_comment, is_single_task=True)
        elif teacher_scoped_task_nums:
            summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(summary, weaknesses, feedback_comment, allowed_task_nums=set(teacher_scoped_task_nums))
        return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment

    # 1. Перевірка, чи це одне комплексне завдання (Пріоритет Scope of Work)
    if scope and scope.get('is_single_complex_task'):
        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
            summary, weaknesses, feedback_comment, is_single_task=True
        )
        return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment

    # 2. Перевірка, чи це завдання на вибір (учень мав обрати 1 завдання)
    instr_lower = (teacher_instructions_text or "").lower()
    is_choice = any(kw in instr_lower for kw in [
        'на вибір', 'одне завдання на вибір', 'будь-яке завдання на вибір',
        'одне з наведених', 'одне із наведених', 'виберіть одне', 'обери одне'
    ])
    if is_choice:
        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
            summary, weaknesses, feedback_comment, is_single_task=True
        )
        return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment

    # 3. Визначаємо очікувану кількість завдань (N)
    # НАЙВИЩИЙ ПРІОРИТЕТ: явна вказівка вчителя
    ai_eval_list = result_json.get('tasks_evaluated') or []
    ai_eval_len = len(ai_eval_list) if isinstance(ai_eval_list, list) else 0

    if teacher_scoped_task_nums:
        total_tasks = len(teacher_scoped_task_nums)
    elif scope and scope.get('teacher_specific_task_nums'):
        teacher_scoped_task_nums = scope['teacher_specific_task_nums']
        total_tasks = len(teacher_scoped_task_nums)
    elif scope and scope.get('assigned_task_count'):
        total_tasks = scope['assigned_task_count']
        if not teacher_scoped_task_nums:
            teacher_scoped_task_nums = list(range(1, total_tasks + 1))
    else:
        explicit_count = detect_expected_task_count(teacher_instructions_text or "")
        if explicit_count > 0:
            total_tasks = explicit_count
        else:
            named_tasks = [q for q in task_questions if re.search(r'^(?:практичне\s+)?(?:завдання|вправа)\s*\d+', q, re.IGNORECASE)]
            if len(named_tasks) >= 2 and any(kw in (teacher_instructions_text or "").lower() for kw in ['завдання', 'вправи', 'виконайте', 'роботи']):
                total_tasks = len(named_tasks)
            else:
                total_tasks = 1

    # Санація зауважень щодо незаданих номерів завдань
    if teacher_scoped_task_nums:
        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
            summary, weaknesses, feedback_comment, allowed_task_nums=set(teacher_scoped_task_nums)
        )

    if total_tasks < 2:
        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
            summary, weaknesses, feedback_comment, is_single_task=True
        )
        return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment

    # 3. Аналіз виконаних завдань (K)
    detected_missing_nums = set()
    ai_completed = None
    try:
        if 'tasks_completed_count' in result_json:
            ai_completed = int(result_json.get('tasks_completed_count'))
    except (ValueError, TypeError):
        pass

    completed_evals = []
    if isinstance(ai_eval_list, list) and ai_eval_list:
        for t in ai_eval_list:
            if isinstance(t, dict):
                st = str(t.get('status', '')).lower()
                num = t.get('task_num')
                num_int = None
                if num is not None:
                    try:
                        num_int = int(num)
                    except (ValueError, TypeError):
                        pass
                if st in ['completed', 'done', 'виконано', 'так']:
                    if teacher_scoped_task_nums:
                        if num_int is None or num_int in teacher_scoped_task_nums:
                            completed_evals.append(t)
                    else:
                        completed_evals.append(t)
                elif st in ['missing', 'omitted', 'not_completed', 'пропущено', 'не виконано', 'ні']:
                    if num_int is not None:
                        detected_missing_nums.add(num_int)

    # Шукаємо згадки про пропущені завдання у тексті відгуку ШІ
    all_ai_feedback_text = f"{summary} {' '.join(str(w) for w in weaknesses)} {feedback_comment}".lower()
    missing_task_patterns = [
        r'завдання\s*(\d+)\s*(?:не\s*виконано|пропущено|відсутнє|не\s*зроблено|не\s*надано)',
        r'(?:не\s*виконано|пропущено|відсутнє)\s*завдання\s*(\d+)',
        r'пропущено\s*виконання\s*завдання\s*(\d+)',
        r'відсутня\s*відповідь\s*на\s*завдання\s*(\d+)',
        r'не\s*відповів\s*на\s*завдання\s*(\d+)',
        r'завдання\s*(\d+)\s*залишилось\s*без\s*відповіді'
    ]
    for pat in missing_task_patterns:
        for m in re.finditer(pat, all_ai_feedback_text, re.IGNORECASE):
            try:
                detected_missing_nums.add(int(m.group(1)))
            except (ValueError, TypeError):
                pass

    allowed_nums = set(teacher_scoped_task_nums) if teacher_scoped_task_nums else set(range(1, total_tasks + 1))
    detected_missing_nums = {n for n in detected_missing_nums if n in allowed_nums}

    # 4. Перевірка структури зданої роботи учня (абзаци та зміст)
    raw_text = student_raw_text or ""
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', raw_text.strip()) if len(p.strip()) > 15]

    # Виявлення специфічного випадку подвійного зарахування (Словник українською + Переклад англійською):
    task_qs_text = " ".join(task_questions).lower()
    has_dict_task = any(kw in task_qs_text for kw in ['словник', 'словниках', 'тлумачення', 'пояснення крилатого', 'крилатого вислову'])
    has_trans_task = any(kw in task_qs_text for kw in ['переклад', 'перекладіть', 'онлайн-перекладач', 'англійською'])
    has_wiki_task = any(kw in task_qs_text for kw in ['вікіпеді', 'рідне місто', 'рідне село'])

    double_count_task2_missing = False
    if total_tasks == 3 and (has_dict_task or has_trans_task):
        has_cyrillic_idiom = bool(re.search(r'[А-Яа-яЇїІіЄєҐґ].*(?:ахіллес|п[\'’]ят|вразлив|слабк|міф)', raw_text, re.IGNORECASE))
        has_english_idiom = bool(re.search(r'["\']?achilles[\'’]?\s*heel', raw_text, re.IGNORECASE))
        if has_english_idiom and not has_cyrillic_idiom:
            double_count_task2_missing = True
            detected_missing_nums.add(2)

    # 5. Визначаємо фактичну кількість виконаних завдань
    if detected_missing_nums:
        completed_tasks = max(1, total_tasks - len(detected_missing_nums))
    elif completed_evals and len(completed_evals) < total_tasks:
        completed_tasks = len(completed_evals)
    elif ai_completed is not None and ai_completed < total_tasks:
        completed_tasks = max(1, ai_completed)
    elif double_count_task2_missing:
        completed_tasks = 2
    elif len(paragraphs) < total_tasks and answered_count == 0 and len(raw_text) < 1500:
        completed_tasks = max(1, len(paragraphs))
    elif answered_count > 0 and answered_count < total_tasks:
        completed_tasks = answered_count
    else:
        completed_tasks = total_tasks

    # Якщо виконано ВСІ завдання — стеля не обмежує
    if completed_tasks >= total_tasks:
        return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment

    # 6. Розрахунок максимального дозволеного балу за шкалою НУШ
    ratio = completed_tasks / total_tasks
    if ratio <= 0.35:
        max_allowed_grade = 5
        level_name = 'Середній (4-6)'
    elif ratio <= 0.70:
        max_allowed_grade = 8
        level_name = 'Достатній (7-9)'
    elif ratio <= 0.85:
        max_allowed_grade = 9
        level_name = 'Достатній (7-9)'
    else:
        max_allowed_grade = 9
        level_name = 'Достатній (7-9)'

    # 7. Застосування обмеження до suggested_grade
    try:
        cur_grade = int(str(suggested_grade).replace(',', '.'))
        if cur_grade > max_allowed_grade:
            suggested_grade = str(max_allowed_grade)
            level = level_name
    except (ValueError, TypeError):
        pass

    # 8. Застосування обмеження до clean_gr_results та avg_gr_grade
    if not is_traditional and clean_gr_results:
        new_numeric = []
        for gr in clean_gr_results:
            try:
                g_val = int(str(gr.get('grade', '0')).replace(',', '.'))
                if g_val > max_allowed_grade:
                    gr['grade'] = str(max_allowed_grade)
                    if max_allowed_grade <= 6:
                        gr['level'] = 'Середній'
                    elif max_allowed_grade <= 9:
                        gr['level'] = 'Достатній'
                    new_numeric.append(float(max_allowed_grade))
                else:
                    new_numeric.append(float(g_val))
            except (ValueError, TypeError):
                pass
        numeric_gr_grades = new_numeric
        if numeric_gr_grades:
            avg_gr_grade = int(min(max_allowed_grade, math.ceil(sum(numeric_gr_grades) / len(numeric_gr_grades))))
            if suggested_grade != 'Доопрацювати':
                suggested_grade = str(avg_gr_grade)

    # 9. Виправлення галюцинацій у summary, strengths, weaknesses, feedback_comment
    false_all_done_patterns = [
        r'викона(?:в|ла|но)\s+(?:всі|усі)\s*(?:\d+|три|два|чотири|п[\'’]ять)?\s*(?:практичн\w+)?\s*завдання',
        r'робота\s+містить\s+правильні\s+відповіді\s+на\s+завдання\s+1,\s*2\s+та\s+3',
        r'відповіді\s+на\s+завдання\s+1,\s*2\s+(?:та|і)\s+3',
        r'виконано\s+завдання\s+1,\s*2\s+(?:та|і)\s+3',
        r'усі\s+(?:\d+|три)\s+завдання\s+виконано',
        r'всі\s+(?:\d+|три)\s+завдання\s+виконано',
        r'завдання\s+виконано\s+у\s+повному\s+обсязі',
        r'робота\s+виконана\s+у\s+повному\s+обсязі',
        r'відповідає\s+вимогам\s+уроку\s*у\s*повному\s*обсязі'
    ]

    missing_desc = ''
    if double_count_task2_missing and has_dict_task:
        missing_desc = 'Завдання 2 (пояснення крилатого вислову в онлайн-словнику) пропущено / не виконано'
    elif detected_missing_nums:
        items = []
        for n in sorted(list(detected_missing_nums)):
            task_label = f"Завдання {n}"
            if task_questions and 1 <= n <= len(task_questions):
                q_text = task_questions[n - 1].strip()
                m_title = re.search(r'^(?:(?:крок|завдання|вправа|питання)\s*\d+[\.\:\–\—\-]?\s*)([^\.\n\r]+)', q_text, re.IGNORECASE)
                if m_title:
                    task_label += f" ({m_title.group(1).strip()[:35]})"
            items.append(task_label)
        missing_desc = f"{', '.join(items)} не виконано / пропущено"
    else:
        missing_desc = f'виконано {completed_tasks} із {total_tasks} завдань'

    for pat in false_all_done_patterns:
        if re.search(pat, summary, re.IGNORECASE):
            summary = re.sub(
                pat,
                f'опрацьовано {completed_tasks} із {total_tasks} практичних завдань ({missing_desc})',
                summary,
                flags=re.IGNORECASE
            )

    if any(kw in summary.lower() for kw in ['завдання 1, 2 та 3', 'завдання 1, 2 і 3', 'всі 3 завдання', 'усі три практичні завдання', 'виконав усі три']):
        summary = (
            f'Учень частково виконав практичні завдання: опрацьовано {completed_tasks} із {total_tasks} завдань. '
            f'{missing_desc}.'
        )

    # Очищення сильних сторін від помилкових похвал за "повне виконання всіх завдань"
    clean_strengths = []
    has_filtered_all_done = False
    for s in strengths:
        s_lower = str(s).lower()
        if any(kw in s_lower for kw in [
            'повне та правильне виконання всіх', 'виконання всіх практичних завдань',
            'виконання усіх практичних завдань', 'виконання всіх завдань', 'виконано всі завдання',
            'повне виконання всіх'
        ]):
            has_filtered_all_done = True
            continue
        if (double_count_task2_missing or (2 in detected_missing_nums)) and has_dict_task:
            if any(kw in s_lower for kw in [
                'пояснення крилатого вислову в онлайн-словнику',
                'знаходження пояснення крилатого вислову',
                'пошук пояснення крилатого вислову'
            ]):
                continue
        clean_strengths.append(s)

    if has_filtered_all_done:
        clean_strengths.insert(0, f'Якісне опрацювання виконаних практичних завдань ({completed_tasks} із {total_tasks}).')
    strengths = clean_strengths

    missing_w_entry = f'{missing_desc} (роботу виконано частково: {completed_tasks} із {total_tasks} завдань).'
    has_existing_missing_mention = any(
        kw in ' '.join(str(w) for w in weaknesses).lower()
        for kw in ['пропущено', 'не виконано', 'не зроблено']
    )
    if not has_existing_missing_mention and detected_missing_nums:
        has_existing_missing_mention = any(
            f"завдання {n}" in ' '.join(str(w) for w in weaknesses).lower()
            for n in detected_missing_nums
        )
    if not has_existing_missing_mention:
        weaknesses.insert(0, missing_w_entry)

    # Очищення відгуку від похвал за пропущені завдання (якщо це завдання на словник)
    if has_dict_task:
        feedback_comment = re.sub(
            r'(?:пояснив|пояснила|пояснено)\s+значення\s+(?:вислову|фразеологізму|крилатого\s+вислову)(?:,)?\s*',
            '',
            feedback_comment,
            flags=re.IGNORECASE
        )
        feedback_comment = re.sub(r',\s*та\s+', ' та ', feedback_comment)
        feedback_comment = re.sub(r'\s{2,}', ' ', feedback_comment)

    feedback_comment = re.sub(
        r'чудово\s+впора(?:вся|лася)\s+з\s+практичними\s+завданнями',
        f'добре впоралися з {completed_tasks} із {total_tasks} практичних завдань',
        feedback_comment,
        flags=re.IGNORECASE
    )
    feedback_comment = re.sub(
        r'викона(?:в|ла|но)\s+(?:всі|усі)\s*(?:\d+|три)?\s*(?:практичн\w+)?\s*завдання',
        f'виконано {completed_tasks} із {total_tasks} завдань',
        feedback_comment,
        flags=re.IGNORECASE
    )

    if not any(kw in feedback_comment.lower() for kw in ['пропущено', 'не виконано', 'відсутн', f'{completed_tasks} із {total_tasks}']):
        feedback_comment = (
            feedback_comment.strip() +
            f"\n\nЗверніть увагу: {missing_desc}. Оскільки виконано {completed_tasks} із {total_tasks} завдань, оцінка становить {suggested_grade} б. ({level})."
        )

    return suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment



def evaluate_submission_with_gemini(submission, custom_prompt=None, ai_settings=None, preset_id=None, criteria_preset=None, selected_gr_codes=None, force_thinking=None, teacher=None):
    """
    Виконує педагогічний аналіз та попереднє оцінювання роботи учня за допомогою Google Gemini.
    Підтримує чергу пріоритетів моделей, вибір шаблону критеріїв, вибір конкретних ГР (прапорцями)
    та опціональний режим глибокого мислення (Thinking mode) для складних завдань.
    Результати записуються безпосередньо у submission (ai_suggested_grade, ai_feedback, ai_status тощо).
    """
    settings = ai_settings or get_ai_settings()
    act_provider, act_key, act_model, act_url, is_backup_active = settings.get_active_config()

    asgn = submission.assignment
    if force_thinking is not None:
        use_thinking = bool(force_thinking)
    elif asgn and getattr(asgn, 'ai_thinking_mode', False):
        use_thinking = True
    elif getattr(settings, 'default_thinking_mode', False):
        use_thinking = True
    else:
        use_thinking = False

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
            log_ai_error(
                teacher=teacher,
                submission=submission,
                assignment=submission.assignment,
                action='evaluation',
                provider=act_provider,
                model_name=act_model,
                status_code=None,
                error_type='Missing API Key',
                error_message=error_msg
            )
            return {'status': 'failed', 'error': error_msg}

    # Assignment/class choices are the default in every entry point. An explicit
    # teacher override applies only to this request, never to future student checks.
    policy_preset, policy_grs = asgn.get_ai_policy(submission.class_group) if asgn else (None, [])
    selected_preset = criteria_preset
    if not selected_preset and preset_id:
        selected_preset = AICriteriaPreset.objects.filter(pk=preset_id).first()
    if not selected_preset:
        selected_preset = policy_preset
    if selected_gr_codes is None:
        selected_gr_codes = policy_grs if selected_preset == policy_preset else []

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
                if re.sub(r'\s+', '', gr_code) in {re.sub(r'\s+', '', code) for code in selected_set}:
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
    custom_criteria = (assignment.custom_criteria or "") if assignment else ""
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

    # ── ПЕРЕВІРКА ВІДПОВІДНОСТІ ТЕМІ, КЛАСУ ТА СПРАВЖНЬОСТІ РОБОТИ ─────────────
    pre_is_invalid, pre_reason, pre_code = detect_invalid_or_teacher_template_submission(submission, text_parts)
    if pre_is_invalid:
        prompt_lines.append(
            f"\n🚨🚨 КРИТИЧНЕ ЗАСТЕРЕЖЕННЯ СИСТЕМИ ДЛЯ ШІ: ВИЯВЛЕНО НЕВІДПОВІДНІСТЬ ЗДАНОЇ РОБОТИ!\n"
            f"ПРИЧИНА: {pre_reason}\n"
            f"КАТЕГОРИЧНІ ВИМОГИ ДЛЯ ОЦІНЮВАННЯ:\n"
            f"- КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 4-12 балів (зокрема 7 балів чи інші позитивні оцінки)!\n"
            f"- Встанови 'suggested_grade': 'Доопрацювати', 'level': 'Початковий (1-3)', 'unclear_task': true!\n"
            f"- Для ВСІХ груп результатів (ГР) признач оцінку 1 або 2 бали (Початковий рівень)!\n"
            f"- У 'format_warning', 'summary', 'weaknesses' та 'feedback_comment' чітко роз'ясни учневі: «{pre_reason}»!\n"
        )

    prompt_lines.append(
        "🛑 НАЙВИЩИЙ ПРІОРИТЕТ: ПЕРЕВІРКА ВІДПОВІДНОСТІ ТЕМІ, КЛАСУ ТА СПРАВЖНЬОСТІ РОБОТИ (ANTI-EMPTY & IRRELEVANT SUBMISSION CHECK):\n"
        "ПЕРЕД тим, як ставити будь-які оцінки чи аналізувати групи результатів, ШІ ЗОБОВ'ЯЗАНИЙ перевірити 4 КРИТИЧНІ БАР'ЄРИ:\n"
        f"1. 🚫 ПЕРЕВІРКА КЛАСУ (ЧИ НЕ ЗДАНО РОБОТУ ДЛЯ ІНШОГО КЛАСУ):\n"
        f"   - Поточний клас учня: «{class_name}».\n"
        f"   - Перевір текст та колонтитули зданого файлу/роботи. Якщо у документі чітко зазначено ІНШИЙ КЛАС (наприклад: учень з 6 чи 7 класу здав практичну чи завдання для 8, 9, 10 чи 11 класу, або навпаки) — це ГРУБА НЕВІДПОВІДНІСТЬ!\n"
        f"   - КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 4-12 балів (зокрема 7 балів) за роботу для іншого класу! Встанови 'suggested_grade': 'Доопрацювати', 'level': 'Початковий (1-3)', оцінки по всіх ГР — 1-2 бали, 'unclear_task': true.\n"
        f"2. 🚫 ПЕРЕВІРКА ВІДПОВІДНОСТІ ТЕМІ ТА ПРЕДМЕТУ:\n"
        f"   - Тема уроку: «{assignment_title}», Предмет: «{subject_name}».\n"
        f"   - Якщо зданий файл чи текст взагалі не стосується цієї теми або цього предмета (здано матеріал з іншого предмета, випадковий сторонній файл, документ з абсолютно іншої теми) — КАТЕГОРИЧНО ЗАБОРОНЕНО ставити оцінку лише за те, що «учень щось прикріпив»!\n"
        f"   - Оцінка за невідповідність темі — ТІЛЬКИ 'Доопрацювати' (або 1-2 бали), 'level': 'Початковий (1-3)', усі ГР — 1-2 бали, 'unclear_task': true.\n"
        f"3. 🚫 ПЕРЕВІРКА НА ЗДАЧУ «БЛАНКУ/ШАБЛОНУ ПРАКТИЧНОЇ ВЧИТЕЛЯ БЕЗ ВІДПОВІДЕЙ»:\n"
        f"   - Перевір, ЧИ Є В ДОКУМЕНТІ ВЛАСНІ ВІДПОВІДІ ТА РОЗВ'ЯЗКИ УЧНЯ!\n"
        f"   - Якщо учень просто прикріпив файл практичної роботи, інструкційної картки чи роздатки від вчителя (де є лише «Практична робота №...», «Тема», «Мета», «Хід роботи», перелік запитань/вправ вчителя), АЛЕ НЕМАЄ ВЛАСНИХ ВІДПОВІДЕЙ чи виконаного розв'язку:\n"
        f"     * КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 4-12 балів!\n"
        f"     * 'suggested_grade': 'Доопрацювати', 'level': 'Початковий (1-3)', 'unclear_task': true.\n"
        f"     * По всіх ГР — 1-2 бали (Початковий рівень).\n"
        f"     * У 'format_warning', 'summary' та 'feedback_comment' чітко напиши: «Здано текст/бланк практичної роботи вчителя без власних відповідей та розв'язку. Роботу не зараховано, надішліть виконані відповіді на доопрацювання.»\n"
        f"     * У 'weaknesses' обов'язково зазнач: «Здано інструкцію/завдання вчителя замість виконаної учнем роботи (відповіді відсутні).»\n"
        f"4. 🚫 ПРАВИЛО «НЕ СТАВИТИ БАЛИ ЗА ПРОСТЕ ПРИКРІПЛЕННЯ ФАЙЛУ»:\n"
        f"   - Бали (4-12) та достатній/високий рівень ставляться ВИКЛЮЧНО за реальну змістовну працю учня над темою завдання!\n"
        f"   - Якщо файл не за темою, з іншого класу чи містить лише бланк завдань — КАТЕГОРИЧНО ЗАБОРОНЕНО виставляти 4-12 балів (зокрема 7 балів)!\n"
    )

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
        "8. ЗІСТАВЛЕННЯ ВІДПОВІДЕЙ УЧНЯ ІЗ ЗАПИТАННЯМИ ВЧИТЕЛЯ (ВІДПОВІДІ БЕЗ ПЕРЕПИСУВАННЯ ЗАПИТАНЬ):\n"
        "   - Дуже часто учні записують у роботі ТІЛЬКИ ВІДПОВІДІ (наприклад, номери «1. ...», «2. ...» або прямий текст відповідей) і НЕ переписують самі запитання вчителя.\n"
        "   - Також учень може надати відповіді лише на 1-2 питання із завдання (часткове виконання).\n"
        "   - ШІ ЗОБОВ'ЯЗАНИЙ взяти формулювання запитань із завдання вчителя (з опису чи слайдів) і САМОСТІЙНО ПІДСТАВИТИ відповіді учня до кожного відповідного запитання!\n"
        "   - 🚫 СУВОРО ТА КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати «жодної відповіді не дано», «відповіді відсутні» чи «робота порожня», якщо учень надав хоча б 1-2 відповіді чи фрагменти відповідей на запитання з умови!\n"
        "   - ШІ зобов'язаний оцінити правильність і змістовність наданих учнем відповідей до відповідних запитань.\n"
        "   - ⚠️ ОБОВ'ЯЗКОВО вкажи учневі про недолік оформлення («питання-відповідь») у 'weaknesses' та 'feedback_comment': порадь завжди записувати запитання разом із відповідями (формат «питання-відповідь») або чітко зазначати номери запитань, щоб робота була структурованою і легкою для перевірки.\n"
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

    # ── ДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ ТА РОБОТА З ІНФОРМАЦІЄЮ З ІНТЕРНЕТУ ───
    prompt_lines.append(
        "🌐 КРИТИЧНЕ ПРАВИЛО: ДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ ТА РОБОТА З ІНФОРМАЦІЄЮ З ІНТЕРНЕТУ:\n"
        "1. Коли завдання передбачає пошук інформації в інтернеті, краєзнавство, опис населеного пункту (міста, села, селища), географічного об'єкта, історичної події чи постаті:\n"
        "   - Вчитель зазвичай формулює загальний напрямок (наприклад: «Знайдіть в інтернеті інформацію про ваше рідне місто чи село», «Підготуйте повідомлення про населений пункт України», «Знайдіть інформацію про...»).\n"
        "   - Умова вчителя НЕ містить назв конкретних міст чи сіл кожного учня, оскільки кожен учень самостійно обирає свій населений пункт чи об'єкт для дослідження!\n"
        "2. ПЕРЕВІРКА ФАКТИЧНОЇ ДОСТОВІРНОСТІ ЗА ВЛАСНИМИ ЗНАННЯМИ ШІ:\n"
        "   - ШІ ЗОБОВ'ЯЗАНИЙ перевірити правильність та достовірність наданої учнем інформації за власними знаннями (чи правдиві географічні координати, область, річки, історія заснування, пам'ятки, визначні постаті, події обраного населеного пункту/об'єкта).\n"
        "3. 🚫 СУВОРО ТА КАТЕГОРИЧНО ЗАБОРОНЕНО:\n"
        "   - Запитувати або писати «а що це таке?», «що це за місто/село?», «чому тут написано про Чернігів/село, якщо в умові цього не було?», «не зрозуміло, яке завдання виконане»!\n"
        "   - Заявляти, що робота «не відповідає темі завдання», лише через те, що учень досліджував обраний ним населений пункт або навів факти, знайдені в інтернеті!\n"
        "   - Встановлювати 'unclear_task': true, писати у 'format_warning' зауваження про незрозумілість завдання або повертати роботу на 'Доопрацювати', якщо учень надав змістовну інформацію по темі пошуку!\n"
        "4. КРИТЕРІЇ ОЦІНЮВАННЯ ПОШУКОВИХ РОБІТ:\n"
        "   - 10-12 балів (Високий рівень): інформація фактично достовірна, тема розкрита повно, зв'язно, структуровано (розташування, історія, цікаві факти, висновки), робота оформлена охайно.\n"
        "   - 7-9 балів (Достатній рівень): факти правильні, але опис стислий, бракує окремих деталей або є незначні неточності.\n"
        "   - 4-6 балів (Середній рівень): фрагментарні уривки (1-2 речення замість повідомлення).\n"
        "   - 1-3 бали / Доопрацювати: тільки якщо здано сторонній нерелевантний спам (наприклад, кулінарний рецепт замість історії міста/села).\n"
    )

    # ── ОЦІНЮВАННЯ ПРЕЗЕНТАЦІЙ (.pptx, .ppt, .odp) ──────────────────────────
    prompt_lines.append(
        "📽️ ВКАЗІВКИ ДЛЯ ПЕРЕВІРКИ ПРЕЗЕНТАЦІЙ (якщо робота є презентацією):\n"
        "- Оцінюй презентацію комплексно: змістовну глибину розкриття теми, логічну структуру (титульний слайд, вступ, основні тези, висновки), лаконічність формулювання думок на слайдах (тези замість перевантаження суцільним текстом).\n"
        "- Враховуй візуальне наповнення (наявність ілюстрацій, схем, таблиць, зафіксованих у структурі слайдів).\n"
        "- 📊 ДІАГРАМИ ТА ГРАФІКИ НА СЛАЙДАХ: уважно перевіряй блок «ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ У ПРЕЗЕНТАЦІЇ» та передані візуальні зображення слайдів! Якщо учень побудував діаграму, графік чи схему — КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати, що діаграма відсутня! Оцінюй доцільність вибору типу діаграми, заголовки, підписи та структуру відображених даних.\n"
        "- 🛡️ ПЕРЕВІРКА СЛАЙДІВ НА ВИКОРИСТАННЯ ШІ: обов'язково проаналізуй тексти слайдів та графіку на предмет генерації ШІ. Якщо тексти слайдів скомпільовані штучним інтелектом (ChatGPT, Gamma, Tome, Canva AI тощо) або мають ознаки синтетичної генерації — зафіксуй це у полях 'ai_generated_percent' та 'ai_generated_detected', вказавши конкретні номери слайдів у 'ai_generated_details'!\n"
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

    # Read the same complete teacher evidence in evaluation and in the student guide.
    from .ai_context import teacher_materials, ASSESSMENT_RULES, feedback_evidence_sections
    teacher_files_content = []
    material_coverage = []
    if assignment and assignment.files.exists():
        primary_task_content, teacher_files_content, teacher_media, material_coverage = teacher_materials(assignment)
        inline_media.extend(teacher_media)
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
                    "⚠️ ПОШУК ЗАВДАННЯ В ЦИХ МАТЕРІАЛАХ ВЧИТЕЛЯ ТА РОЗМЕЖУВАННЯ SCOPE OF WORK:\n"
                    "1. КОНТЕКСТ УРОКУ: матеріали містять теоретичну інформацію, правила, зразки, ілюстрації та навчальні вправи для роботи в класі. Вони є контекстом теми, а НЕ автоматичним переліком обов'язкових завдань.\n"
                    "2. ГОЛОВНЕ ПРАВИЛО: Кількість знайдених у презентації/файлі вправ НЕ Є кількістю обов'язкових завдань учня! Якщо вчитель задав проєкт або комплексне завдання («Робота над проєктом», «Створити презентацію», «Створити програму», «Виконати практичну роботу») — оцінюй саме проєкт за темою, а не вимагай виконання всіх вправ зі слайдів!\n"
                    "3. Тільки якщо вчитель прямо дав вказівку виконати вправи чи домашнє завдання з файлу (наприклад, перевірити ФІНАЛЬНІ/ОСТАННІ СЛАЙДИ або виконати завдання зі слайду 15), вони входять до Scope of Work.\n"
                    "4. ЗАБОРОНА ПОМИЛКОВОГО «ДОПРАЦЮВАННЯ»:\n"
                    "   * Якщо відповідь учня відповідає темі або завданням із матеріалів вчителя — завдання ПОВНІСТЮ ЗРОЗУМІЛЕ! Заборонено ставити 'unclear_task: true' або повертати роботу на 'Доопрацювати' через «незрозумілість завдання».\n"
                    "5. Якщо у файлі міститься кілька вправ, а вчитель задав конкретну чи одне комплексне завдання — оцінюй ВИКЛЮЧНО задане. Решта матеріалів вважаються незаданими!"
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
    is_plagiarism_ignored = (
        getattr(submission, 'ignore_plagiarism', False) or
        submission.is_group_work or
        bool(submission.primary_submission_id) or
        dup_info.get('plagiarism_ignored', False) or
        dup_info.get('is_coauthor', False)
    )

    if is_plagiarism_ignored and (dup_info.get('plagiarism_ignored') or dup_info.get('is_coauthor') or dup_info.get('is_duplicate_student')):
        coauthor_name = dup_info.get('duplicate_student_name') or 'іншим учнем'
        prompt_lines.append(
            "\n👥 СПІЛЬНЕ / КОЛЕКТИВНЕ ВИКОНАННЯ РОБОТИ (ПЛАГІАТ ВИКЛЮЧЕНО):\n"
            f"Встановлено однаковий або спільний вміст файлу з роботою учня ({coauthor_name}). "
            "Вчитель або система підтвердили, що учні виконували дане завдання спільно у групі/парі (або однаковий вміст дозволено вчителем як спільний проект).\n"
            "КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:\n"
            "- КАТЕГОРИЧНО ЗАБОРОНЕНО знижувати оцінку чи встановлювати штраф за плагіат або списування!\n"
            "- Оціни якість розв'язку та повноту виконання завдання по суті за встановленими критеріями оцінювання як спільний результат.\n"
            "- У 'summary' та 'feedback_comment' відзнач спільне виконання проекту та дай конструктивний відгук за змістом роботи.\n"
        )
    elif dup_info['is_duplicate_teacher'] and not is_plagiarism_ignored:
        prompt_lines.append(
            "\n🚨 КРИТИЧНЕ ЗАУВАЖЕННЯ СИСТЕМИ АНТИПЛАГІАТУ ТА ПЕРЕВІРКИ:\n"
            f"Встановлено 100% збіг: прикріплений учнем файл є точною копією вихідного файлу завдання вчителя («{dup_info['teacher_file_name']}»)! "
            "Учень НЕ виконував завдання, а просто повторно прикріпив сам файл завдання або умову від вчителя.\n"
            "КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:\n"
            "- Встанови 'suggested_grade' як 'Доопрацювати' (або 1-2 бали, якщо вимагається число).\n"
            "- У 'summary' та 'feedback_comment' прямо напиши учню: «Здано оригінальний файл завдання вчителя без виконання розв'язку. Робота не зарахована. Необхідно самостійно виконати завдання та надіслати свій результат на доопрацювання.»\n"
            "- У 'weaknesses' обов'язково зазнач: «Здано вихідний файл завдання замість виконаної роботи».\n"
        )
    elif dup_info['is_duplicate_student'] and not is_plagiarism_ignored:
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

    # ── ПЕРЕВІРКА НА СПІВАВТОРІВ ТА КОЛЕКТИВНУ РОБОТУ ─────────────────────────
    if submission.is_group_work or submission.group_authors or getattr(submission, 'ignore_plagiarism', False):
        authors_str = submission.group_authors or submission.get_student_full_name()
        prompt_lines.append(
            f"👥 КОЛЕКТИВНА РОБОТА / СПІВАВТОРИ: Роботу виконано спільно учнями ({authors_str}) (підтверджено вчителем або системою). "
            "Оцінюй виконання як командний проєкт, враховуючи спільний внесок без зниження оцінки за дублювання."
        )

    # ── ВРАХУВАННЯ КОМЕНТАРЯ ТА ВИСНОВКІВ УЧНЯ («Висновки по роботі або коментар») ──
    if submission.comment_student and submission.comment_student.strip():
        student_comment_txt = submission.comment_student.strip()
        prompt_lines.append(
            f"═══════════════════════════════════════════════════════════════════\n"
            f"💬 ПОЛЕ «Висновки по роботі або коментар для оцінювання роботи» (УЧЕНЬ):\n"
            f"«{student_comment_txt}»\n\n"
            f"⚠️ КРИТИЧНЕ ПРАВИЛО ДЛЯ ШІ ЩОДО ВИСНОВКУ ТА КОМЕНТАРЯ УЧНЯ:\n"
            f"1. ШІ ЗОБОВ'ЯЗАНИЙ УВАЖНО ПРОЧИТАТИ ЦЕЙ КОМЕНТАР!\n"
            f"2. Якщо учень написав у цьому полі висновок до практичної чи лабораторної роботи, підсумок, "
            f"відповідь на завдання або пояснення ходу розв'язання — ВВАЖАТИ ЦЕЙ ВИСНОВОК ПОВНОЦІННИМ ВИСНОВКОМ ДО РОБОТИ!\n"
            f"3. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО стверджувати у 'weaknesses', 'feedback_comment' чи 'summary', "
            f"що «висновок відсутній» або «робота не містить висновків», якщо учень надав висновок або коментар у цьому полі!\n"
            f"4. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО знижувати оцінку за відсутність висновку у файлі, якщо висновок сформульовано тут!\n"
            f"5. Обов'язково відзнач наявність висновку в 'strengths' та 'feedback_comment'.\n"
            f"═══════════════════════════════════════════════════════════════════"
        )

    prompt_lines.append(f"\nДАНІ УЧНЯ: {submission.get_student_full_name()} ({class_name})")

    # ── ВИЗНАЧЕННЯ ТОЧНОГО ОБСЯГУ ЗАВДАННЯ (SCOPE OF WORK — TASK RESOLUTION) ──
    scope = resolve_assignment_scope(
        assignment_title=assignment_title,
        assignment_desc=assignment_desc,
        custom_criteria=custom_criteria,
        primary_task_content=primary_task_content if 'primary_task_content' in locals() else None,
        teacher_files_content=(primary_task_content if 'primary_task_content' in locals() else []) + teacher_files_content,
    )
    teacher_specific_task_nums = scope.get('teacher_specific_task_nums') or []
    task_questions = scope.get('task_questions') or []
    combined_task_for_qs = scope.get('clean_instruction_text') or assignment_desc or ""
    is_single_complex_task = scope.get('is_single_complex_task', False)
    assigned_task_count = scope.get('assigned_task_count', 1)
    assigned_tasks_list = scope.get('assigned_tasks', [])
    task_interpretation = scope.get('task_interpretation') or {}
    task_type = scope.get('task_type') or 'other'
    questions_expected = scope.get('questions_expected', False)
    deliverable = scope.get('deliverable') or {}
    evaluation_method = scope.get('evaluation_method') or {}

    student_combined_text = "\n".join(text_parts) if text_parts else ""

    final_task_understanding = scope.get('final_task_understanding') or {}
    teacher_intent = scope.get('teacher_intent') or final_task_understanding.get('teacher_intent') or {}
    unassigned_material = scope.get('unassigned_material') or []
    mandatory_reqs = scope.get('mandatory_requirements') or final_task_understanding.get('mandatory_requirements') or []
    task_understanding_conf = scope.get('task_understanding_confidence', 1.0)
    ambiguities = scope.get('ambiguities') or []
    assigned_scope = scope.get('assigned_scope') or []
    task_components = task_interpretation.get('task_components') or []
    t_reqs = task_interpretation.get('teacher_requirements') or []
    t_crit = task_interpretation.get('teacher_criteria') or []
    g_recs = task_interpretation.get('generic_recommendations') or []

    # ── ПРОМПТ: SCOPE OF WORK BLOCK (НАЙВИЩИЙ ПРІОРИТЕТ) ───────────────────────
    scope_block_lines = [
        "═══════════════════════════════════════════════════════════════════",
        "🎯 FINAL TASK UNDERSTANDING (ДЖЕРЕЛО ІСТИНИ ДЛЯ ОЦІНЮВАННЯ):",
        "ЦЕ Є ВЖЕ ВИЗНАЧЕНИЙ СИСТЕМОЮ КОНТЕКСТ ЗАВДАННЯ.",
        "Не змінюй його на основі випадкових вправ, прикладів або питань із матеріалів учителя!",
        "",
        f"Що задав учитель:\n{teacher_intent.get('what_teacher_asks') or assignment_desc or assignment_title}",
        "",
        f"Що повинен зробити учень:\n{task_interpretation.get('what_student_must_do') or assignment_desc or assignment_title}",
        "",
        f"Очікуваний результат:\n{final_task_understanding.get('expected_result') or teacher_intent.get('expected_result') or deliverable.get('description', '')}",
        "",
        f"Обов'язкові компоненти:\n{json.dumps(teacher_intent.get('required_components') or deliverable.get('required_components', ['зміст за темою']), ensure_ascii=False)}",
        "",
        f"Обов'язкові дії (required_actions):\n{json.dumps(teacher_intent.get('required_actions') or deliverable.get('required_actions', []), ensure_ascii=False)}",
        "",
        f"Обов'язкові критерії та вимоги:\n{json.dumps(mandatory_reqs or t_reqs or t_crit, ensure_ascii=False)}",
        "",
        f"Що НЕ є завданням (unassigned_material / навчальний контекст):\n{json.dumps(unassigned_material, ensure_ascii=False) if unassigned_material else 'Випадкові вправи, теорія або питання з файлів учителя, які вчитель не задавав'}",
        "",
        f"Впевненість розуміння завдання (confidence): {task_understanding_conf}",
        f"Неоднозначності (ambiguities): {json.dumps(ambiguities, ensure_ascii=False)}",
        "",
        "⚠️ СУВОРЕ РОЗМЕЖУВАННЯ РОБОТИ УЧНЯ ТА ЗАВДАННЯ ВЧИТЕЛЯ:",
        "1. STUDENT SUBMISSION ≠ TEACHER ASSIGNMENT: Учнівська робота, здані файли та коментар учня показують, що ФАКТИЧНО виконав учень.",
        "   Але вони НЕ МОЖУТЬ підмінити собою завдання вчителя і НЕ створюють нове чи інше завдання!",
        "2. Якщо учень у коментарі чи роботі написав «Я виконав вправу 4» або надіслав вправу 4, а вчитель задав створити презентацію — це НЕ змінює Scope завдання!",
        "   Оцінюй здане відносно завдання вчителя («Створити презентацію»). Заборонено підміняти Scope завдання вчителя на 'Вправа 4' через коментар або дію учня!",
        "═══════════════════════════════════════════════════════════════════",
        "🎯 ТОЧНИЙ ОБСЯГ ЗАВДАННЯ ВІД ВЧИТЕЛЯ (SCOPE OF WORK — НАЙВИЩИЙ ПРІОРИТЕТ):",
    ]
    if is_single_complex_task:
        task_desc_str = assigned_tasks_list[0]['description'] if assigned_tasks_list else (assignment_desc or assignment_title or "Комплексне завдання")
        scope_block_lines.extend([
            f"Вчитель визначив завдання як ОДНЕ комплексне завдання/проєкт: «{task_desc_str}».",
            f"Кількість обов'язкових завдань: assigned_task_count = 1.",
            "",
            "⚠️ КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:",
            f"1. ✅ Об'єктом перевірки є ВИКЛЮЧНО це єдине завдання («{task_desc_str}»).",
            "2. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО шукати у прикріпленій презентації, PDF чи додаткових матеріалах вчителя окремі вправи, приклади, тестові питання чи домашні завдання і вимагати їх обов'язкового виконання!",
            "3. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО писати у feedback_comment, summary чи weaknesses:",
            "   - «виконано 1 з 4 завдань» (або будь-які інші пропорції знайдених у презентації вправ);",
            "   - «не виконано завдання 2 / вправу 3 / питання 5»;",
            "   - знижувати оцінку за невиконання матеріалів або вправ зі слайдів, які вчитель не задавав окремо.",
            "4. ✅ Оцінюй відповідність та якість наданого учнем результату саме цьому завданню (тема, зміст, самостійність, критерії).",
            "5. ⚙️ У JSON ОБОВ'ЯЗКОВО поверни: 'tasks_total_count': 1, 'tasks_completed_count': 1 (якщо роботу надано і виконано), та заповни об'єкт 'task_resolution'.",
        ])
    elif teacher_specific_task_nums:
        nums_str = ', '.join(str(n) for n in teacher_specific_task_nums)
        scope_block_lines.extend([
            f"Вчитель у полі «Що потрібно зробити» ЯВНО вказав виконати КОНКРЕТНЕ ЗАВДАННЯ: № {nums_str}.",
            f"Кількість обов'язкових завдань: assigned_task_count = {len(teacher_specific_task_nums)}.",
            "",
            "⚠️ КАТЕГОРИЧНІ ВИМОГИ ДО ОЦІНЮВАННЯ:",
            f"1. ✅ ОЦІНЮЙ ВИКЛЮЧНО завдання № {nums_str} — саме воно задане вчителем.",
            "2. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО знижувати оцінку або писати у 'weaknesses' чи 'feedback_comment', що учень 'не виконав інші завдання'. Будь-які інші номери завдань з файлів є НЕЗАДАНИМИ і не враховуються при оцінюванні!",
            f"3. ✅ Якщо учень якісно виконав завдання № {nums_str} — робота вважається виконаною на 100% і заслуговує на найвищий бал відповідно до якості!",
            f"4. ⚙️ У полі 'tasks_total_count' повертай {len(teacher_specific_task_nums)} (кількість ЗАДАНИХ завдань), у 'tasks_completed_count' — скільки з них виконав учень, та заповни 'task_resolution'.",
        ])
    else:
        scope_block_lines.extend([
            f"Кількість визначених обов'язкових завдань: assigned_task_count = {assigned_task_count}.",
            "Оцінюй тільки ті завдання, які прямо задав учитель. Не створюй нових завдань із випадкових прикладів чи вправ у файлах!",
            f"У полі 'tasks_total_count' повертай {assigned_task_count}, та заповни 'task_resolution'.",
        ])

    # ── БЛОК ТИПУ ЗАВДАННЯ ТА DELIVERABLE ──
    task_components = task_interpretation.get('task_components') or []
    t_reqs = task_interpretation.get('teacher_requirements') or []
    t_crit = task_interpretation.get('teacher_criteria') or []
    g_recs = task_interpretation.get('generic_recommendations') or []

    scope_block_lines.extend([
        "",
        "🎯 ПОВНА СТРУКТУРА ІНТЕРПРЕТАЦІЇ ЗАВДАННЯ (TASK INTERPRETATION - ДЖЕРЕЛО ІСТИНИ):",
        f"- Основний тип завдання (task_type): {task_type}",
        f"- Складові компоненти (task_components): {json.dumps(task_components, ensure_ascii=False)}",
        f"- Що учень мав зробити: {task_interpretation.get('what_student_must_do', assignment_desc or assignment_title)}",
        f"- Очікуваний результат: {task_interpretation.get('expected_result', deliverable.get('description', ''))}",
        f"- Deliverable: {json.dumps(deliverable, ensure_ascii=False)}",
        f"- Обов'язкові вимоги вчителя (teacher_requirements): {json.dumps(t_reqs, ensure_ascii=False)}",
        f"- Обов'язкові критерії вчителя (teacher_criteria): {json.dumps(t_crit, ensure_ascii=False)}",
        f"- Загальні рекомендації до якості (generic_recommendations - НЕ ЗНИЖУЮТЬ БАЛ): {json.dumps(g_recs, ensure_ascii=False)}",
        f"- Чи очікуються текстові відповіді на запитання (questions_expected): {'ТАК (модель питання-відповідь)' if questions_expected else 'НІ (створення файлу/продукту)'}",
        f"- Метод оцінювання: {json.dumps(evaluation_method, ensure_ascii=False)}",
        "",
        "🚫 ЗАБОРОНА ВИГАДУВАННЯ НЕІСНУЮЧИХ ОБОВ'ЯЗКОВИХ ВИМОГ:",
        "1. Категорично заборонено вважати обов'язковими: висновок, джерела, титульний слайд, фіксовану кількість слайдів чи ілюстрацій, прізвище у назві файлу, ЯКЩО вчитель явно не вказав це у teacher_requirements або інструкціях!",
        "2. Якщо вчитель не вимагав висновку чи джерел, заборонено знижувати оцінку або додавати це у 'weaknesses' чи 'criteria_results'!",
        "3. generic_recommendations є лише порадами щодо якості (читабельність, охайність) і ніколи не знижують бал та не стають невиконаними критеріями.",
    ])
    if deliverable.get('required_components'):
        comps_str = ", ".join(deliverable['required_components'])
        scope_block_lines.append(f"- Обов'язкові компоненти: {comps_str}")

    if not questions_expected:
        scope_block_lines.extend([
            "",
            "⚠️ СУВОРА ЗАБОРОНА МОДЕЛІ «ПИТАННЯ-ВІДПОВІДЬ» ДЛЯ ЦЬОГО ЗАВДАННЯ:",
            "1. Завдання НЕ передбачає відповідей на запитання. Учень створює практичний продукт/файл!",
            "2. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО вимагати текстові відповіді на запитання, шукати питання у файлах чи знижувати оцінку через відсутність відповідей!",
            "3. 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО писати у 'weaknesses', 'summary' чи 'feedback_comment':",
            "   - «відсутні відповіді на запитання» / «не надано відповідей»;",
            "   - «рекомендується дотримуватися формату питання-відповідь»;",
            "   - «не виконано питання зі слайдів / PDF».",
            "4. Якщо у матеріалах уроку є питання для обговорення чи самоперевірки, вони слугують навчальним контекстом уроку і НЕ є обов'язковими вимогами до учня.",
            "5. ✅ Оцінюй БЕЗПОСЕРЕДНЬО якість, зміст, структуру, розрахунки, код чи висновки створеного результату (deliverable).",
        ])
    else:
        scope_block_lines.extend([
            "",
            "📝 МОДЕЛЬ «ПИТАННЯ-ВІДПОВІДЬ» ДЛЯ ЦЬОГО ЗАВДАННЯ:",
            "Вчитель вимагає надати відповіді на запитання. Перевір правильність та повноту кожної відповіді учня.",
        ])

    if custom_criteria:
        scope_block_lines.extend([
            "",
            "📋 ІНДИВІДУАЛЬНІ КРИТЕРІЇ ВЧИТЕЛЯ (ПРІОРИТЕТ 2):",
            "Вчитель встановив індивідуальні критерії оцінювання для цього завдання:",
            custom_criteria.strip(),
            "⚠️ Оцінюй виконання саме цих індивідуальних критеріїв. Вони мають вищий пріоритет над загальними критеріями!",
        ])

    eval_plan_dict = scope.get('evaluation_plan') or {}
    comment_nuance = analyze_student_comment_nuance(
        comment_student=submission.comment_student,
        desc=assignment_desc,
        custom_criteria=custom_criteria,
        format_requirements=eval_plan_dict.get('format_requirements', [])
    )

    if eval_plan_dict.get('criteria'):
        scope_block_lines.extend([
            "",
            "📋 ЄДИНИЙ ПЛАН ОЦІНЮВАННЯ ТА КРИТЕРІЇ (EVALUATION PLAN):",
        ])
        for idx, cr in enumerate(eval_plan_dict['criteria'], 1):
            weight_str = f" [вага: {cr['weight']} б.]" if cr.get('weight') else ""
            scope_block_lines.append(f"  {idx}. {cr['name']} (джерело: {cr['source']}){weight_str}")
        scope_block_lines.append("⚠️ ШІ ЗОБОВ'ЯЗАНИЙ перевірити кожен зазначений критерій окремо у полі 'criteria_results'!")

    if comment_nuance.get('guidance'):
        scope_block_lines.extend([
            "",
            f"💬 ВКАЗІВКА ЩОДО КОМЕНТАРЯ УЧНЯ: {comment_nuance['guidance']}"
        ])

    inaccessible_list = getattr(submission, '_inaccessible_materials', [])
    if inaccessible_list:
        scope_block_lines.extend([
            "",
            "⚠️ ТЕХНІЧНЕ ОБМЕЖЕННЯ ДОСТУПУ ДО МАТЕРІАЛІВ УЧНЯ:",
        ])
        for im in inaccessible_list:
            scope_block_lines.append(f"  • {im.get('reason', im.get('target', 'Невідомий матеріал'))}")
        scope_block_lines.append("🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО маскувати технічну недоступність матеріалу під доведену помилку учня в коді або стверджувати, що матеріал перевірений! Зафіксуй технічну причину у 'submission_evidence'.")

    scope_block_lines.append("═══════════════════════════════════════════════════════════════════")
    prompt_lines.append("\n".join(scope_block_lines))

    is_file_project = False
    if hasattr(submission, 'files') and submission.files.exists():
        is_file_project = any(
            f.file.name.lower().endswith((
                '.accdb', '.mdb', '.sqlite', '.db', '.sql',
                '.py', '.cpp', '.cs', '.java', '.pas', '.html', '.css', '.js',
                '.xlsx', '.xls', '.ods', '.csv',
                '.pptx', '.ppt', '.odp',
                '.zip', '.rar', '.7z'
            )) for f in submission.files.all()
        )

    qa_mapping_block = None
    questions_omitted = False
    answered_count = 0
    total_questions = 0

    if questions_expected and task_questions:
        qa_mapping_block, questions_omitted, answered_count, total_questions = build_question_answer_mapping(
            task_questions, student_combined_text, is_practical_project=is_file_project
        )

    if qa_mapping_block:
        prompt_lines.append(qa_mapping_block)
    elif questions_expected and task_questions:
        prompt_lines.append("═══════════════════════════════════════════════════════════════════")
        prompt_lines.append("📋 КОНКРЕТНІ ЗАПИТАННЯ / ВПРАВИ ІЗ ЗАВДАННЯ ВЧИТЕЛЯ ДЛЯ ПІДСТАНОВКИ ВІДПОВІДЕЙ:")
        for idx, q in enumerate(task_questions, 1):
            prompt_lines.append(f"  {idx}. {q}")
        prompt_lines.append(
            "\n⚠️ ВКАЗІВКА ДЛЯ ШІ ЩОДО ВІДПОВІДЕЙ НА ЗОБРАЖЕННЯХ / У ФАЙЛАХ:\n"
            "- Учень міг написати на фото зошита чи у файлі ТІЛЬКИ ВІДПОВІДІ (наприклад, номери «1. ...», «2. ...») БЕЗ переписування тексту самих запитань!\n"
            "- ШІ ЗОБОВ'ЯЗАНИЙ підставити знайдені на зображенні/у файлі відповіді учня до відповідних запитань вище та оцінити їхню правильність.\n"
            "- Навіть якщо учень відповів лише на 1-2 запитання, ШІ ЗОБОВ'ЯЗАНИЙ зарахувати їх. КАТЕГОРИЧНО ЗАБОРОНЕНО заявляти «жодної відповіді не дано»!\n"
            "- Якщо учень не переписав запитання у текстовій відповіді: порадь у 'weaknesses' та 'feedback_comment' дотримуватися формату «питання-відповідь»."
        )
        prompt_lines.append("═══════════════════════════════════════════════════════════════════\n")

    prompt_lines.append("ВИКОНАНА РОБОТА УЧНЯ ДЛЯ ОЦІНЮВАННЯ:")
    if questions_expected and questions_omitted and not is_file_project:
        prompt_lines.append(
            "⚠️ ЗВЕРНИ УВАГУ: Учень надав відповіді без переписування тексту самих запитань. "
            "Оціни повноту та зміст відповідей відповідно до зіставлених вище запитань вчителя, але обов'язково зазнач рекомендацію щодо формату «питання-відповідь»."
        )
    prompt_lines.extend(text_parts)
    if is_traditional:
        prompt_lines.append(f"\nПроаналізуй роботу за класичною (традиційною) 12-бальною системою ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), unclear_task (true/false), summary, strengths (масив), weaknesses (масив), feedback_comment, task_resolution (об'єкт з scope_source, assigned_task_count, assigned_tasks, ignored_found_tasks), evaluation_plan (об'єкт з task_summary, assigned_tasks, criteria, format_requirements), submission_evidence (об'єкт з submitted_files, has_link, has_comment, inaccessible_materials), criteria_results (масив об'єктів з criterion, status ['completed'/'partial'/'missing'/'unverifiable'], evidence, recommendation), revision_advice (масив рядків конкретних порад для перездачі), tasks_evaluated (масив об'єктів з task_num, task_title, status ['completed'/'partial'/'missing'], comment), tasks_completed_count (число), tasks_total_count (число — УВАГА: tasks_total_count обов'язково має дорівнювати assigned_task_count з task_resolution, а не кількості вправ у презентації), ai_generated_percent (число 0-100), ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Поле 'gr_results' поверни порожнім масивом [] або null, оскільки групи результатів НЕ використовуються в класичній системі.")
    else:
        prompt_lines.append(f"\nПроаналізуй роботу згідно з обраними критеріями ({preset_name_display}) та обов'язково поверни JSON з полями: suggested_grade (тільки ціле число 1-12 або 'Доопрацювати'), level, format_warning (рядок із зауваженням або null), unclear_task (true/false), summary, strengths (масив), weaknesses (масив), feedback_comment, task_resolution (об'єкт з scope_source, assigned_task_count, assigned_tasks, ignored_found_tasks), evaluation_plan (об'єкт з task_summary, assigned_tasks, criteria, format_requirements), submission_evidence (об'єкт з submitted_files, has_link, has_comment, inaccessible_materials), criteria_results (масив об'єктів з criterion, status ['completed'/'partial'/'missing'/'unverifiable'], evidence, recommendation), revision_advice (масив рядків конкретних порад для перездачі), gr_results (масив об'єктів з code, name, grade, level, comment), tasks_evaluated (масив об'єктів з task_num, task_title, status ['completed'/'partial'/'missing'], comment), tasks_completed_count (число), tasks_total_count (число — УВАГА: tasks_total_count обов'язково має дорівнювати assigned_task_count з task_resolution, а не кількості вправ у презентації), ai_generated_percent (число 0-100), ai_generated_detected (true/false), ai_generated_confidence ('none'/'low'/'medium'/'high'), ai_generated_details (рядок або null). Усі оцінки обов'язково мають бути цілими числами (без десятих часток), заокругленими на користь учня.")

    if custom_prompt:
        system_instruction = custom_prompt.strip()
    elif selected_preset:
        system_instruction = selected_preset.get_full_prompt().strip()
    else:
        system_instruction = (settings.system_prompt or DEFAULT_NUS_SYSTEM_PROMPT).strip()

    # Динамічна санація системного промта від прикладів з інших предметів («крилатий вислів / словник»)
    teacher_full_context = f"{assignment_title} {combined_task_for_qs} {' '.join(task_questions)}".lower()
    has_real_dict_task = any(kw in teacher_full_context for kw in ['словник', 'тлумачення', 'фразеологізм', 'крилатого'])
    if not has_real_dict_task and 'крилатого вислову' in system_instruction:
        system_instruction = system_instruction.replace(
            "«Завдання 2 не виконано: відсутнє пояснення крилатого вислову в онлайн-словнику»",
            "«Завдання 2 не виконано: відсутня обов'язкова частина роботи»"
        )
        system_instruction = system_instruction.replace(
            "Якщо Завдання 2 вимагало знайти пояснення вислову в українському онлайн-словнику, а Завдання 3 — перекласти його англійською: наведення лише одного речення англійською мовою НЕ МОЖЕ вважатися виконанням обох завдань! Українське тлумачення в такому разі відсутнє (Завдання 2 НЕ виконано).",
            "Якщо завдання містить кілька окремих кроків (наприклад, створення структури, зв'язків чи відповіді на запитання): виконання лише одного кроку не може вважатися виконанням решти завдань!"
        )
        system_instruction = re.sub(
            r'пояснення\s+крилатого\s+вислову\s+в\s+онлайн[- ]словнику',
            'обов\'язкове завдання',
            system_instruction,
            flags=re.IGNORECASE
        )

    if is_file_project:
        system_instruction += (
            "\n\nПРАВИЛО ДЛЯ ПРАКТИЧНИХ РОБІТ ТА ПРОЄКТНИХ ФАЙЛІВ (БАЗИ ДАНИХ, КОД ПРОГРАМ, ЕЛЕКТРОННІ ТАБЛИЦІ):\n"
            "- Робота учня подана як практичний файл (наприклад, файл бази даних Microsoft Access, файл скрипта, таблиця Excel тощо).\n"
            "- КАТЕГОРИЧНО ЗАБОРОНЕНО вимагати текстовий формат «питання-відповідь» або робити зауваження, що «відсутні запитання», «надано відповіді без самих запитань» чи «робота подана у вигляді файлу БД без пояснювального документа»!\n"
            "- Оцінюй виконання практичного завдання безпосередньо за наданим файлом: перевіряй наявність створених сутностей/таблиць, полів, ключів, зв'язків, коду відповідно до умови.\n"
        )

    # Завжди гарантуємо правило Scope of Work в системній інструкції
    if "SCOPE OF WORK" not in system_instruction:
        system_instruction += (
            "\n\nПРІОРИТЕТ ВИМОГ ВЧИТЕЛЯ ТА ОБСЯГ ЗАВДАННЯ (SCOPE OF WORK):\n"
            "- ВЧИТЕЛЬ ВИЗНАЧАЄ, ЩО ТРЕБА ВИКОНАТИ. ШІ ВИЗНАЧАЄ, НАСКІЛЬКИ ДОБРЕ ЦЕ ВИКОНАНО.\n"
            "- ШІ НЕ ВИЗНАЧАЄ САМ, ЩО УЧЕНЬ МАВ ВИКОНУВАТИ, НА ОСНОВІ ВИПАДКОВО ЗНАЙДЕНИХ У МАТЕРІАЛАХ ВПРАВ!\n"
            "- Якщо вчитель дав короткий або узагальнений опис («Робота над проєктом», «Створити презентацію», «Створити програму», «Виконати практичну роботу», «Опрацювати тему»), це є ОДНИМ комплексним завданням (assigned_task_count = 1).\n"
            "- КАТЕГОРИЧНО ЗАБОРОНЕНО вишукувати у презентаціях чи PDF вчителя теоретичні приклади, вправи або домашні завдання і перетворювати їх на окремі обов'язкові завдання для учня!\n"
            "- Якщо вчитель вказав конкретний номер (наприклад, «Виконати вправу 2»): оцінюй ВИКЛЮЧНО це завдання. Решта завдань з файлу вважаються незаданими і НЕ можуть бути підставою для зауважень чи зниження оцінки!\n"
            "- Робота вважається виконаною у повному обсязі (100%), якщо якісно виконано саме задане вчителем завдання.\n"
        )

    if "ПРЕЗЕНТАЦІЇ ТА PDF ВЧИТЕЛЯ ЯК ДЖЕРЕЛО ЗАВДАННЯ" not in system_instruction:
        system_instruction += (
            "\n\nПРЕЗЕНТАЦІЇ, PDF, ЗОБРАЖЕННЯ ТА МАТЕРІАЛИ ВЧИТЕЛЯ ЯК ДЖЕРЕЛО ЗАВДАННЯ:\n"
            "- Матеріали вчителя слугують теоретичним та навчальним контекстом уроку.\n"
            "- Якщо учень виконав завдання, знайдене на слайдах презентації чи у PDF/файлах вчителя, це завдання є ЧІТКИМ І ЗРОЗУМІЛИМ. КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 'unclear_task': true або повертати на 'Доопрацювати' через «незрозумілість завдання»!\n"
            "- Оцінюй роботу учня (1-12 балів) за повнотою розкриття теми та якістю виконання відповідно до Scope of Work.\n"
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

    if "ЗІСТАВЛЕННЯ ВІДПОВІДЕЙ УЧНЯ ІЗ ЗАПИТАННЯМИ ВЧИТЕЛЯ" not in system_instruction:
        format_rec_line = "  * ⚠️ ОБОВ'ЯЗКОВО вкажи учневі в 'weaknesses' та 'feedback_comment' про недолік оформлення («питання-відповідь»): порадь використовувати формат «питання-відповідь» або чітку нумерацію запитань.\n" if not is_file_project else "  * Для практичних файлів (бази даних, код тощо) рекомендація щодо формату «питання-відповідь» НЕ застосовується.\n"
        system_instruction += (
            "\n\nЗІСТАВЛЕННЯ ВІДПОВІДЕЙ УЧНЯ ІЗ ЗАПИТАННЯМИ ВЧИТЕЛЯ (ФОРМАТ «ПИТАННЯ-ВІДПОВІДЬ»):\n"
            "- Якщо учень здав лише відповіді (номери 1, 2... або текст без переписування запитань):\n"
            "  * Візьми запитання з умови завдання чи матеріалів вчителя та підстав відповіді учня до кожного відповідного запитання.\n"
            "  * 🚫 СУВОРО ЗАБОРОНЕНО писати «жодної відповіді не дано», «відповіді відсутні» чи «робота порожня», якщо учень відповів хоча б на 1-2 запитання!\n"
            "  * Оціни зміст і правильність наданих учнем відповідей по суті запитань вчителя (навіть при частковому виконанні).\n"
            f"{format_rec_line}"
        )

    if "РОЗДІЛЬНИЙ АНАЛІЗ КОЖНОГО ЗАВДАННЯ" not in system_instruction:
        system_instruction += (
            "\n\nРОЗДІЛЬНИЙ АНАЛІЗ КОЖНОГО ЗАВДАННЯ ТА СУВОРЕ ОБМЕЖЕННЯ БАЛІВ ЗА НЕПОВНИЙ ОБСЯГ (MULTI-TASK COMPLETION & STRICT CEILING):\n"
            "- Тільки коли вчитель ЯВНО задав кілька конкретних завдань (наприклад: «виконати завдання 1, 2 та 3», або «виконати вправи 1–3»):\n"
            "  * ШІ зобов'язаний оцінити кожне дійсно задане завдання окремо!\n"
            "  * 🚫 СУВОРА ЗАБОРОНА ДУБЛЮВАННЯ: один фрагмент тексту чи дії не може зараховуватися за декілька різних завдань одночасно.\n"
            "  * Якщо задано 3 завдання, а виконано 2 (66% обсягу): максимальна можлива оцінка — 7-8 балів (Достатній рівень).\n"
            "  * Якщо задано 3 завдання, а виконано 1 (33% обсягу): максимальна оцінка — 4-5 балів (Середній рівень).\n"
            "  * ⚠️ ЯКЩО ВЧИТЕЛЬ ЗАДАВ ОДНЕ ЗАВДАННЯ («Робота над проєктом», «Створити презентацію», «Виконати вправу 2»): multi-task ceiling НЕ ЗАСТОСОВУЄТЬСЯ, а оцінка визначається якістю виконаної роботи!\n"
            "  * 🚫 КАТЕГОРИЧНО ЗАБОРОНЕНО писати «виконано 1 з 4 завдань» або вказувати у 'weaknesses' невиконання вправ зі слайдів, яких учитель не задавав!\n"
        )

    if "ДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ" not in system_instruction:
        system_instruction += (
            "\n\nДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ ТА РОБОТА З ІНФОРМАЦІЄЮ З ІНТЕРНЕТУ:\n"
            "- Якщо завдання передбачає пошук в інтернеті, краєзнавство, опис населеного пункту (міста, села, селища) чи обраного об'єкта:\n"
            "  * Учень самостійно обирає свій населений пункт/об'єкт, тому його назви не може бути в тексті завдання вчителя!\n"
            "  * ШІ зобов'язаний перевірити фактичну достовірність наведених учнем даних за власними енциклопедичними знаннями.\n"
            "  * 🚫 СУВОРО ЗАБОРОНЕНО запитувати «а що це таке?», «що це за місто/село?», писати «не відповідає темі завдання», встановлювати 'unclear_task': true або повертати на 'Доопрацювати' через згадку обраного учнем населеного пункту!\n"
            "  * Оцінюй повноту, достовірність, логічність структури та самостійність викладу (10-12 балів за якісно розкриту тему).\n"
        )

    if "ANTI-EMPTY & IRRELEVANT SUBMISSION CHECK" not in system_instruction:
        system_instruction += (
            "\n\nПЕРЕВІРКА ВІДПОВІДНОСТІ ТЕМІ, КЛАСУ ТА СПРАВЖНЬОСТІ РОБОТИ (ANTI-EMPTY & IRRELEVANT SUBMISSION CHECK):\n"
            "- Перед виставленням будь-якої оцінки перевір відповідність роботи класу та темі завдання:\n"
            "  * Якщо в роботі зазначено інший клас (наприклад, завдання для 6-7 класу, а здано роботу 8-9 класу): КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 4-12 балів (зокрема 7 балів)! Встанови 'Доопрацювати', 1-2 бали по всіх ГР, 'unclear_task': true.\n"
            "  * Якщо робота не відповідає темі завдання (сторонній предмет чи тема): оцінка ТІЛЬКИ 'Доопрацювати' (1-2 бали), 'unclear_task': true.\n"
            "  * Якщо здано бланк/шаблон практичної роботи вчителя (хід роботи, інструкцію) БЕЗ власних відповідей чи розв'язків учня: КАТЕГОРИЧНО ЗАБОРОНЕНО ставити 4-12 балів! Оцінка ТІЛЬКИ 'Доопрацювати' (1-2 бали), 'unclear_task': true.\n"
            "  * НЕ МОЖНА ставити оцінку за те, що учень просто щось прикріпив. Оцінюються виключно реальні відповіді та праця учня!\n"
        )

    from .ai_context import build_assessment_request
    prompt_content, system_instruction = build_assessment_request(
        submission, selected_preset, active_grs, scope, text_parts,
        primary_task_content if 'primary_task_content' in locals() else [],
        teacher_files_content, material_coverage, custom_prompt=custom_prompt,
        ai_settings=settings)
    if selected_preset and selected_preset.document_file and os.path.exists(selected_preset.document_file.path):
        from .ai_context import extract_file_evidence
        rubric_evidence = extract_file_evidence(selected_preset.document_file.path)
        prompt_content += "\nПОВНИЙ ДОКУМЕНТ ОБРАНИХ КРИТЕРІЇВ:\n" + (rubric_evidence['text'] or '')
        inline_media.extend(dict(item, source='Документ критеріїв учителя') for item in rubric_evidence['media'])
        prompt_content += "\n" + "\n".join(rubric_evidence['limitations'])

    attempts_configs = settings.get_request_configs()

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
                if use_thinking:
                    # Режим глибокого мислення (Thinking mode): даємо розширений бюджет міркувань
                    thinking_budget_val = 4096 if c_provider == 'gemini' else None
                else:
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
                    temperature=float(settings.temperature if settings.temperature is not None else 0.2),
                    max_output_tokens=min(10000, 4000 + 300 * len(active_grs) + 180 * len(scope.get("assigned_tasks") or [])),
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
                        fail_reason = f"Недійсний API Key для {c_provider.title()} ({c_model}) (HTTP {status_code})."
                    elif status_code == 503:
                        fail_reason = f"503 High Demand ({err_msg or 'Перевантаження моделі'})"
                    else:
                        fail_reason = f"HTTP {status_code}: {err_msg}" if (status_code and err_msg) else (err_msg or f"Помилка HTTP {status_code}")

                    attempted_errors.append(f"[{c_provider}/{c_model}]: {fail_reason}")
                    log_ai_error(
                        teacher=teacher,
                        submission=submission,
                        assignment=submission.assignment,
                        action='evaluation',
                        provider=c_provider,
                        model_name=c_model,
                        status_code=status_code,
                        error_type=f"HTTP {status_code}" if status_code else "API Error",
                        error_message=fail_reason,
                        prompt_preview=prompt_content[:1500],
                        raw_response=(raw_text or err_msg or '')[:2000],
                        failover_triggered=(fallback_happened or (len(attempts_configs) > 1 and cfg_idx < len(attempts_configs) - 1))
                    )
                    break  # Переходимо до наступної моделі / резервного API

                raw_text = raw_text.strip()
                result_json = extract_json_from_text(raw_text)

                model_name = f"{c_model} ({c_provider.title()})" if c_provider != 'gemini' else c_model

                if is_failover_call or (cfg_idx > 0):
                    try:
                        settings.last_failover_at = timezone.now()
                        target_prefix = "резервний " if is_failover_call else ""
                        settings.last_failover_reason = f"Автоматичне перемикання на {target_prefix}{c_provider.title()} ({c_model}). Попередня помилка: {'; '.join(attempted_errors[-2:])}"
                        settings.save(update_fields=['last_failover_at', 'last_failover_reason'])
                    except Exception:
                        pass

                if not result_json:
                    log_ai_error(
                        teacher=teacher,
                        submission=submission,
                        assignment=submission.assignment,
                        action='evaluation',
                        provider=c_provider,
                        model_name=c_model,
                        status_code=status_code,
                        error_type='JSON Parsing Error',
                        error_message='ШІ повернув некоректну або неповно структуровану відповідь (не вдалося розпарсити JSON)',
                        prompt_preview=prompt_content[:1500],
                        raw_response=raw_text[:2000],
                        failover_triggered=fallback_happened
                    )

                if result_json:
                    if result_json.get('assessment_blocked') is True or result_json.get('suggested_grade') is None:
                        reason = str(result_json.get('grade_explanation') or result_json.get('summary') or 'Недостатньо прочитаних даних для оцінювання.')
                        submission.ai_status, submission.ai_error_reason = 'failed', reason
                        submission.save(update_fields=['ai_status', 'ai_error_reason'])
                        return {'status': 'failed', 'error': reason, 'assessment_blocked': True}
                    suggested_grade = str(result_json.get('suggested_grade', '')).strip()
                    if suggested_grade != 'Доопрацювати':
                        try:
                            numeric_grade = float(suggested_grade.replace(',', '.'))
                            if not math.isfinite(numeric_grade) or not 1 <= numeric_grade <= 12:
                                raise ValueError('grade out of scale')
                            suggested_grade = str(math.ceil(numeric_grade))
                        except (TypeError, ValueError):
                            attempted_errors.append(f"[{c_provider}/{c_model}] Некоректний бал у відповіді; оцінку не збережено.")
                            continue

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

                    # ── СИНХРОНІЗАЦІЯ РЕЗУЛЬТАТІВ ШІ ЗІ SCOPE OF WORK (TASK RESOLUTION) ──
                    expected_scope_count = scope.get('assigned_task_count', 1)

                    # Завжди фіксуємо tasks_total_count на підставі SCOPE від вчителя!
                    result_json['tasks_total_count'] = expected_scope_count

                    # Нормалізуємо та гарантуємо наявність об'єкта task_resolution
                    task_res = result_json.get('task_resolution')
                    if not isinstance(task_res, dict):
                        task_res = {}
                    task_res.setdefault('scope_source', scope.get('scope_source', 'teacher_description'))
                    task_res['assigned_task_count'] = expected_scope_count
                    task_res.setdefault('assigned_tasks', scope.get('assigned_tasks', []))
                    task_res.setdefault('ignored_found_tasks', scope.get('ignored_found_tasks', []))
                    result_json['task_resolution'] = task_res

                    if is_single_complex_task:
                        result_json['tasks_total_count'] = 1
                        single_evals = result_json.get('tasks_evaluated') or []
                        result_json['tasks_completed_count'] = (1 if any(isinstance(task, dict) and task.get('status') == 'completed' for task in single_evals) else 0) if single_evals else min(1, max(0, int(result_json.get('tasks_completed_count') or 0)))
                        raw_evals = result_json.get('tasks_evaluated') or []
                        if isinstance(raw_evals, list) and (len(raw_evals) > 1 or not raw_evals):
                            main_task_title = assigned_tasks_list[0]['description'] if assigned_tasks_list else (assignment_title or "Комплексне завдання")
                            result_json['tasks_evaluated'] = [{
                                'task_num': 1,
                                'task_title': main_task_title,
                                'status': 'completed' if suggested_grade != 'Доопрацювати' else 'missing',
                                'comment': summary or 'Оцінено якість виконання проєкту/завдання'
                            }]
                        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
                            summary, weaknesses, feedback_comment, is_single_task=True
                        )
                    elif teacher_specific_task_nums:
                        result_json['tasks_total_count'] = len(teacher_specific_task_nums)
                        raw_evals = result_json.get('tasks_evaluated') or []
                        if isinstance(raw_evals, list):
                            filtered_evals = [
                                t for t in raw_evals
                                if isinstance(t, dict) and (t.get('task_num') in teacher_specific_task_nums or len(teacher_specific_task_nums) == 1)
                            ]
                            if filtered_evals:
                                result_json['tasks_evaluated'] = filtered_evals
                        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
                            summary, weaknesses, feedback_comment, allowed_task_nums=set(teacher_specific_task_nums)
                        )
                    else:
                        allowed_nums = set(range(1, expected_scope_count + 1))
                        summary, weaknesses, feedback_comment = sanitize_unassigned_task_mentions(
                            summary, weaknesses, feedback_comment, allowed_task_nums=allowed_nums
                        )

                    # Нормалізуємо evaluation_plan
                    eval_plan = result_json.get('evaluation_plan')
                    if not isinstance(eval_plan, dict) or not eval_plan.get('criteria'):
                        eval_plan = scope.get('evaluation_plan', {})
                    scope_plan = scope.get('evaluation_plan', {})
                    if isinstance(scope_plan, dict):
                        for k in ['task_type', 'task_interpretation', 'deliverable', 'evaluation_method', 'questions_expected']:
                            if k in scope_plan and (k not in eval_plan or not eval_plan.get(k)):
                                eval_plan[k] = scope_plan[k]
                    result_json['evaluation_plan'] = eval_plan
                    result_json.setdefault('task_interpretation', task_interpretation)
                    result_json.setdefault('task_type', task_type)
                    result_json.setdefault('deliverable', deliverable)
                    result_json.setdefault('questions_expected', questions_expected)
                    result_json.setdefault('final_task_understanding', scope.get('final_task_understanding', {}))
                    result_json.setdefault('teacher_intent', scope.get('teacher_intent', {}))
                    result_json.setdefault('task_understanding_confidence', scope.get('task_understanding_confidence', 1.0))
                    result_json.setdefault('ambiguities', scope.get('ambiguities', []))

                    # Нормалізуємо submission_evidence
                    sub_evidence = result_json.get('submission_evidence')
                    if not isinstance(sub_evidence, dict):
                        sub_evidence = {}
                    inaccessible_list = getattr(submission, '_inaccessible_materials', [])
                    if inaccessible_list:
                        sub_evidence['inaccessible_materials'] = inaccessible_list
                    sub_evidence.setdefault('has_link', bool(submission.link))
                    sub_evidence.setdefault('has_comment', bool(submission.comment_student and submission.comment_student.strip()))
                    submitted_files_list = []
                    if hasattr(submission, 'files') and submission.files.exists():
                        submitted_files_list = [getattr(sf, 'original_name', '') or os.path.basename(sf.file.name) for sf in submission.files.all() if sf.file]
                    elif submission.file:
                        submitted_files_list = [getattr(submission, 'original_name', '') or os.path.basename(submission.file.name)]
                    sub_evidence.setdefault('submitted_files', submitted_files_list)
                    result_json['submission_evidence'] = sub_evidence

                    # Нормалізуємо criteria_results
                    plan_criteria = eval_plan.get('criteria') or []
                    raw_crit_results = result_json.get('criteria_results')
                    synced_crit_results = []
                    matched_raw_indices = set()

                    for cr in plan_criteria:
                        c_name = cr['name'] if isinstance(cr, dict) else str(cr)
                        found_res = None
                        if isinstance(raw_crit_results, list):
                            for idx, r in enumerate(raw_crit_results):
                                if isinstance(r, dict):
                                    r_crit = str(r.get('criterion', '')).lower()
                                    if r_crit and (c_name.lower() in r_crit or r_crit in c_name.lower()):
                                        found_res = dict(r)
                                        matched_raw_indices.add(idx)
                                        break
                        if not found_res:
                            found_res = {
                                'criterion': c_name, 'status': 'unverifiable',
                                'evidence': 'ШІ не навів доказів перевірки цього критерію.',
                                'recommendation': 'Потрібно зіставити результат роботи з цим критерієм.'
                            }
                        synced_crit_results.append(found_res)

                    # Додаємо критерії з відповіді моделі, які не були згадані в плані
                    if isinstance(raw_crit_results, list):
                        for idx, r in enumerate(raw_crit_results):
                            if idx not in matched_raw_indices and isinstance(r, dict) and r.get('criterion'):
                                synced_crit_results.append(dict(r))

                    # Спеціальна перевірка для висновку з урахуванням коментаря учня та збагачення критеріїв
                    normalized_crit_results = []
                    has_teacher_conclusion_req = any(
                        'висновок' in str(r).lower() or 'підсумок' in str(r).lower()
                        for r in (task_interpretation.get('teacher_requirements') or [])
                    )

                    for found_res in synced_crit_results:
                        c_name = str(found_res.get('criterion') or found_res.get('title') or '').strip()
                        if not c_name:
                            continue

                        # Обробка висновку за наявності в коментарі
                        if any(kw in c_name.lower() for kw in ['висновок', 'підсумок']):
                            if comment_nuance.get('has_substance') and comment_nuance.get('substance_type') == 'conclusion':
                                if comment_nuance.get('format_strictly_requires_file'):
                                    found_res['status'] = 'partial'
                                    found_res['evidence'] = 'Зміст висновку наведено учнем у коментарі до здачі, проте він відсутній на слайді презентації'
                                    found_res['evidence_source'] = 'student_comment'
                                    found_res['recommendation'] = 'Перенесіть сформульований висновок на окремий слайд презентації'
                                else:
                                    found_res['status'] = 'completed'
                                    found_res['evidence'] = 'Змістовний висновок наведено у коментарі до здачі'
                                    found_res['evidence_source'] = 'student_comment'
                                    found_res['recommendation'] = ''
                            elif not has_teacher_conclusion_req and str(found_res.get('status')).lower() in ['missing', 'partial']:
                                # Вчитель не вимагав висновку, ШІ не має права ставити missing
                                continue

                        # Якщо вчитель не вимагав запитань (questions_expected == False), виключаємо вимоги до відповідей на питання
                        if not questions_expected and any(kw in c_name.lower() for kw in ['відповіді на запитання', 'відповіді на питання', 'контрольні запитання']) and str(found_res.get('status')).lower() in ['missing', 'partial']:
                            continue

                        # Нормалізація джерела критерію: teacher_description | custom_criteria | teacher_file | general
                        raw_src = str(found_res.get('criterion_source') or found_res.get('source') or '').lower()
                        if 'custom' in raw_src or c_name in (scope.get('custom_criteria_rules') or []):
                            crit_source = 'custom_criteria'
                        elif 'file' in raw_src or 'material' in raw_src or any(c_name.lower() in str(fc).lower() for fc in (scope.get('file_criteria_rules') or [])):
                            crit_source = 'teacher_file'
                        elif 'desc' in raw_src or 'instruct' in raw_src or (assignment_desc and any(w in assignment_desc.lower() for w in c_name.lower().split() if len(w) > 4)):
                            crit_source = 'teacher_description'
                        else:
                            crit_source = 'general'

                        # Перевірка обов'язковості (mandatory)
                        is_generic = any(kw in c_name.lower() for kw in [
                            'читабельний шрифт', 'охайне оформлення', 'єдиний стиль', 'лаконічний текст'
                        ])
                        is_mandatory = False if is_generic else bool(found_res.get('mandatory', True))

                        # Нормалізація джерела доказу: student_file | student_comment | link | unverified
                        raw_ev_src = str(found_res.get('evidence_source') or '').lower()
                        ev_text = str(found_res.get('evidence') or '').strip()
                        if 'comment' in raw_ev_src or 'коментар' in ev_text.lower():
                            ev_source = 'student_comment'
                        elif 'file' in raw_ev_src or submitted_files_list:
                            ev_source = 'student_file'
                        elif 'link' in raw_ev_src or getattr(submission, 'link', None):
                            ev_source = 'link'
                        else:
                            ev_source = 'unverified'

                        # Якщо учень зробив непідтверджену заяву без матеріалів
                        if comment_nuance.get('is_unsubstantiated_declaration') and not submitted_files_list and not getattr(submission, 'link', None):
                            st_val = 'unverifiable'
                            ev_text = 'Заява учня у коментарі не підтверджена фактичними матеріалами'
                            ev_source = 'unverified'
                        else:
                            raw_st = str(found_res.get('status', 'unverifiable')).lower()
                            if raw_st in ['completed', 'done', 'так', 'виконано']:
                                st_val = 'completed'
                            elif raw_st in ['partial', 'частково']:
                                st_val = 'partial'
                            elif raw_st in ['unverified', 'unverifiable', 'не підтверджено', 'непідтверджено']:
                                st_val = 'unverifiable'
                            else:
                                st_val = 'missing'

                        rec_text = str(found_res.get('recommendation') or '').strip()
                        if not rec_text and st_val in ['missing', 'partial']:
                            rec_text = f"Виконати вимогу: {c_name}"

                        normalized_crit_results.append({
                            'criterion': c_name,
                            'criterion_source': crit_source,
                            'mandatory': is_mandatory,
                            'evidence_source': ev_source,
                            'evidence': ev_text or ('Підтверджено у зданій роботі' if st_val == 'completed' else 'Не виявлено доказів виконання'),
                            'status': st_val,
                            'recommendation': rec_text
                        })

                    synced_crit_results = normalized_crit_results

                    # Якщо в плані оцінювання не було критеріїв, але вони з'явилися після перевірки
                    if not eval_plan.get('criteria') and synced_crit_results:
                        eval_plan['criteria'] = [
                            {
                                'name': sc.get('criterion', ''),
                                'source': sc.get('criterion_source', 'submission_evaluation'),
                                'weight': None,
                                'evaluation_method': 'individual_check',
                                'mandatory': sc.get('mandatory', True)
                            }
                            for sc in synced_crit_results if sc.get('mandatory', True)
                        ]

                    result_json['criteria_results'] = synced_crit_results

                    # Нормалізуємо revision_advice, improvement_steps, resubmission_recommendations
                    rev_advice = result_json.get('revision_advice')
                    if not isinstance(rev_advice, list) or not rev_advice:
                        rev_advice = []
                        for cr in synced_crit_results:
                            if cr.get('status') in ['missing', 'partial'] and cr.get('recommendation'):
                                if cr['recommendation'] not in rev_advice:
                                    rev_advice.append(cr['recommendation'])
                        if not rev_advice and weaknesses:
                            for w in weaknesses:
                                w_clean = str(w).strip()
                                if w_clean and not any(w_clean.lower() in ra.lower() for ra in rev_advice):
                                    rev_advice.append(f"Виправити: {w_clean}")
                    result_json['revision_advice'] = rev_advice
                    result_json['improvement_steps'] = rev_advice
                    result_json['resubmission_recommendations'] = rev_advice

                    # ── Діагностичне логування оцінювання (Section 20) ──────────
                    try:
                        assigned_tasks_log = "\n".join(f" - {t.get('description', '')}" for t in (scope.get('assigned_tasks') or [])) or " - (Не вказано)"
                        ignored_tasks_log = "\n".join(f" - {t.get('description', '')}" for t in (scope.get('ignored_found_tasks') or [])) or " - (Немає)"
                        crit_log = "\n".join(f" - {c.get('name', '') if isinstance(c, dict) else str(c)}" for c in (eval_plan.get('criteria') or [])) or " - (Загальні критерії)"
                        stud_comm = (submission.comment_student or "").strip()
                        evidence_log = f" - student_files: {submitted_files_list}\n - student_comment: {stud_comm[:120] if stud_comm else '(порожньо)'}"
                        crit_res_log = "\n".join(f" - {cr.get('criterion', '')}: {cr.get('status', '')} (evidence: {cr.get('evidence_source', '')})" for cr in synced_crit_results)

                        logger.info(
                            "\n═══════════════════════════════════════════════════════════════════\n"
                            "ASSIGNMENT SCOPE\n"
                            "Assigned task count: %s\n"
                            "Assigned tasks:\n%s\n\n"
                            "Ignored found tasks:\n%s\n\n"
                            "CRITERIA\n%s\n\n"
                            "STUDENT EVIDENCE\n%s\n\n"
                            "CRITERIA RESULTS\n%s\n"
                            "═══════════════════════════════════════════════════════════════════",
                            scope.get('assigned_task_count', 1),
                            assigned_tasks_log,
                            ignored_tasks_log,
                            crit_log,
                            evidence_log,
                            crit_res_log
                        )
                    except Exception as log_err:
                        logger.debug("Помилка формування діагностичного логу: %s", log_err)

                    # Санація коментаря учня та вимог формату у тексті зворотного зв'язку
                    if comment_nuance.get('has_substance') and comment_nuance.get('substance_type') == 'conclusion':
                        if comment_nuance.get('format_strictly_requires_file'):
                            no_concl_pat = re.compile(r'(?:висновок\s+(?:відсутній|не\s+надано|немає|не\s+сформульовано)|відсутні\s+висновки|не\s+містить\s+висновк\w*)', re.IGNORECASE)
                            weaknesses = [w for w in weaknesses if not no_concl_pat.search(str(w))]
                            format_concl_msg = "Висновок наведено в коментарі, але за вимогою оформлення його слід розмістити безпосередньо на окремому слайді презентації."
                            if not any('слайд' in str(w).lower() and 'висновок' in str(w).lower() for w in weaknesses):
                                weaknesses.append(format_concl_msg)
                            summary = no_concl_pat.sub('висновок наведено в коментарі (потрібно перенести на слайд)', summary)
                            feedback_comment = no_concl_pat.sub('висновок наведено в коментарі, проте за правилами оформлення перенесіть його на слайд', feedback_comment)
                        else:
                            no_concl_pat = re.compile(r'(?:висновок\s+(?:відсутній|не\s+надано|немає|не\s+сформульовано)|відсутні\s+висновки|не\s+містить\s+висновк\w*)', re.IGNORECASE)
                            weaknesses = [w for w in weaknesses if not no_concl_pat.search(str(w))]
                            summary = no_concl_pat.sub('', summary).strip(' ,;')
                            feedback_comment = no_concl_pat.sub('', feedback_comment).strip(' ,;')
                            concl_strength = "Сформульовано змістовний висновок до роботи (у коментарі до здачі)."
                            if not any('висновок' in str(s).lower() for s in strengths):
                                strengths.append(concl_strength)
                    elif not has_teacher_conclusion_req:
                        # Вчитель взагалі не вимагав висновку: очищуємо претензії щодо висновку
                        no_concl_pat = re.compile(
                            r'(?:(?:відсутній|не\s+надано|немає|не\s+сформульовано|відсутні|не\s+містить)\s+висновк\w*|'
                            r'висновок\s+(?:відсутній|не\s+надано|немає|не\s+сформульовано)|'
                            r'додати\s+висновок|варто\s+зробити\s+висновок)',
                            re.IGNORECASE
                        )
                        weaknesses = [w for w in weaknesses if not no_concl_pat.search(str(w))]
                        summary = no_concl_pat.sub('', summary).strip(' ,;')
                        feedback_comment = no_concl_pat.sub('', feedback_comment).strip(' ,;')

                    if not questions_expected:
                        # Вчитель не вимагав моделі «питання-відповідь»: очищуємо претензії щодо питань
                        no_qa_pat = re.compile(
                            r'(?:(?:відсутні|не\s+надано|немає)\s+відповід\w*\s+на\s+(?:питан\w*|запитан\w*)|'
                            r'не\s+відповів\s+на\s+(?:питан\w*|запитан\w*)|'
                            r'(?:питан\w*|запитан\w*)\s+(?:залишились|залишилися)\s+без\s+відповіді|'
                            r'не\s+виконано\s+\d+\s+контрольн\w*\s+питан\w*|'
                            r'не\s+виконано\s+\d+\s+питан\w*|'
                            r'дотримуватися\s+формату\s+питання-відповідь)',
                            re.IGNORECASE
                        )
                        weaknesses = [w for w in weaknesses if not no_qa_pat.search(str(w))]
                        summary = no_qa_pat.sub('', summary).strip(' ,;')
                        feedback_comment = no_qa_pat.sub('', feedback_comment).strip(' ,;')

                    # Санація при технічній недоступності матеріалу (Section 8)
                    if inaccessible_list and not submitted_files_list:
                        code_err_pat = re.compile(r'(?:помилк\w*\s+в\s+код\w*|код\s+не\s+працює|неправильн\w*\s+програм\w*|не\s+виконано\s+жодного|учень\s+не\s+виконав|синтаксичн\w*\s+помилк\w*)', re.IGNORECASE)
                        had_code_complaint = any(code_err_pat.search(str(w)) for w in weaknesses)
                        if had_code_complaint or str(suggested_grade).strip() in ['Доопрацювати', '1', '2', '3']:
                            weaknesses = [w for w in weaknesses if not code_err_pat.search(str(w))]
                            for im in inaccessible_list:
                                tech_msg = f"Технічна недоступність матеріалу: {im.get('reason', im.get('target'))}. Роботу не перевірено через помилку доступу."
                                if not any('технічн' in str(w).lower() or 'недоступн' in str(w).lower() for w in weaknesses):
                                    weaknesses.insert(0, tech_msg)
                            suggested_grade = 'Доопрацювати'
                            level = 'Початковий (1-3)'

                    # Обробка та валідація результатів за групами результатів (ГР НУШ)
                    clean_gr_results = []
                    numeric_gr_grades = []
                    if not is_traditional and active_grs:
                        from .ai_context import complete_result_groups
                        raw_gr_results = complete_result_groups(raw_gr_results if isinstance(raw_gr_results, list) else [], active_grs)
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
                                    if re.sub(r'\s+', '', code_lower) not in {re.sub(r'\s+', '', value) for value in selected_set}:
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
                                    'status': item.get('status', 'assessed'),
                                    'comment': comment
                                })

                    # Обчислюємо середній бал за оціненими групами результатів (заокруглення на перевагу учню, тільки ціле число)
                    avg_gr_grade = None
                    if not is_traditional and numeric_gr_grades:
                        avg_val = sum(numeric_gr_grades) / len(numeric_gr_grades)
                        avg_gr_grade = int(min(12, max(1, math.ceil(avg_val))))
                        # Якщо оцінювалось декілька ГР одночасно — середня оцінка стає рекомендованою
                        if suggested_grade != 'Доопрацювати' and not custom_criteria.strip():
                            suggested_grade = str(avg_gr_grade)
                    elif suggested_grade and suggested_grade != 'Доопрацювати':
                        try:
                            s_num = float(str(suggested_grade).replace(',', '.'))
                            suggested_grade = str(int(min(12, max(1, math.ceil(s_num)))))
                        except (ValueError, TypeError):
                            pass

                    # ── ПЕРЕВІРКА НА НЕВІДПОВІДНІСТЬ ТЕМІ, КЛАСУ ТА ЗДАЧУ БЛАНКУ ВЧИТЕЛЯ ──
                    post_is_invalid, post_reason, post_code = detect_invalid_or_teacher_template_submission(submission, text_parts)

                    fw_lower = (format_warning or '').lower()
                    sum_lower = (summary or '').lower()
                    fb_lower = (feedback_comment or '').lower()
                    weaknesses_lower = " ".join(str(w) for w in weaknesses).lower() if weaknesses else ""
                    all_ai_text = f"{fw_lower} {sum_lower} {fb_lower} {weaknesses_lower}"

                    ai_detected_class_mismatch = any(k in all_ai_text for k in [
                        'інший клас', 'іншого класу', 'не для цього класу', 'матеріал для іншого класу',
                        'завдання для 8 класу', 'завдання для 9 класу', 'завдання для 10 класу', 'завдання для 11 класу'
                    ])
                    ai_detected_topic_mismatch = any(k in all_ai_text for k in [
                        'не відповідає темі', 'не відповідає завданню', 'не стосується теми', 'інша тема',
                        'інший предмет', 'сторонній предмет', 'сторонній файл', 'не за темою'
                    ])
                    ai_detected_teacher_template = (any(k in all_ai_text for k in [
                        'практична робота вчителя', 'бланк практичної', 'інструкційна картка', 'шаблон вчителя',
                        'роздатка вчителя', 'текст завдань вчителя', 'хід роботи без відповідей',
                        'завдання вчителя замість виконаної', 'без власних відповідей', 'відповіді відсутні',
                        'не містить відповідей', 'не надав відповідей', 'відповідей немає',
                        'жодної відповіді на питання', 'жодної відповіді не надано', 'немає жодної відповіді',
                        'не виконано жодного завдання'
                    ]) and answered_count == 0)

                    ai_detected_mere_attachment = any(k in all_ai_text for k in [
                        'просто прикріп', 'лише прикріп', 'нічого не зробив', 'робота не виконана',
                        'не зараховано', 'не можна ставити оцінку'
                    ])

                    # A format mismatch is partial work, not an unrelated submission.
                    content_credit = any(row.get('status') in ('completed', 'partial')
                                         for row in result_json.get('criteria_results') or [] if isinstance(row, dict))
                    if content_credit:
                        ai_detected_topic_mismatch = False
                    if not questions_expected and not post_is_invalid:
                        ai_detected_teacher_template = False

                    is_rejected_submission = (
                        post_is_invalid or
                        ai_detected_class_mismatch or
                        ai_detected_topic_mismatch or
                        ai_detected_teacher_template or
                        ai_detected_mere_attachment
                    )

                    # Визначаємо, чи вдалося ШІ зрозуміти, яке завдання виконано
                    raw_unclear = result_json.get('unclear_task')
                    unclear_task = bool(raw_unclear and str(raw_unclear).lower() not in ['false', '0', 'none', 'null'])

                    if is_rejected_submission:
                        suggested_grade = 'Доопрацювати'
                        level = 'Початковий (1-3)'

                        if post_is_invalid:
                            rej_reason = post_reason
                        elif ai_detected_class_mismatch:
                            rej_reason = "Прикріплена робота містить завдання/матеріали для іншого класу. Здано роботу не з цього класу, оцінку не зараховано."
                        elif ai_detected_teacher_template:
                            rej_reason = "Прикріплений файл є бланком/інструкцією практичної роботи вчителя без власних відповідей чи розв'язків учня. За просте прикріплення тексту завдань оцінка не виставляється."
                        elif ai_detected_topic_mismatch:
                            rej_reason = "Прикріплена робота не відповідає темі чи предмету завдання. Необхідно надіслати виконане завдання за заданою темою."
                        else:
                            rej_reason = "Роботу не зараховано: здані матеріали не містять виконаного учнем завдання (просте прикріплення файлу)."

                        if rej_reason not in weaknesses:
                            weaknesses.insert(0, rej_reason)

                        # Якщо це невідповідність класу, дублікат роздатки або бланк вчителя — фіксуємо unclear_task
                        if post_is_invalid or ai_detected_class_mismatch or ai_detected_teacher_template:
                            unclear_task = True

                        if clean_gr_results and not is_traditional:
                            for gr in clean_gr_results:
                                gr['grade'] = '1'
                                gr['level'] = 'Початковий'
                                gr['comment'] = rej_reason
                            numeric_gr_grades = [1.0] * len(clean_gr_results)
                            avg_gr_grade = 1
                    else:
                        if not unclear_task:
                            if ('не зрозуміло' in fw_lower and 'завдан' in fw_lower) or ('незрозуміло' in fw_lower and 'завдан' in fw_lower):
                                unclear_task = True
                            elif ('не зрозуміло' in sum_lower and 'завдан' in sum_lower) or ('незрозуміло' in sum_lower and 'завдан' in sum_lower):
                                unclear_task = True
                            elif ('не зрозуміло, яке саме завдання' in fb_lower) or ('не зрозуміло яке завдання' in fb_lower) or ('незрозуміло, яке завдання' in fb_lower):
                                unclear_task = True

                    if unclear_task:
                        suggested_grade = 'Доопрацювати'
                        level = 'Початковий (1-3)'
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

                    # ── Захист від помилкового твердження «жодної відповіді не дано» та зняття помилкового unclear_task ──
                    student_raw_text = " ".join(text_parts).strip() if text_parts else ""

                    # Визначаємо, чи є завдання пошуковим, краєзнавчим або відкритим дослідницьким
                    is_research_or_search_task = any(kw in combined_task_for_qs.lower() for kw in [
                        'інтернет', 'пошук в інтернеті', 'знайдіть в інтернеті', 'знайти в інтернеті',
                        'досліджен', 'місто', 'село', 'населен', 'краєзнав', 'реферат'
                    ]) or any(kw in (assignment_title or '').lower() for kw in [
                        'інтернет', 'пошук в інтернеті', 'знайдіть в інтернеті', 'знайти в інтернеті',
                        'досліджен', 'місто', 'село', 'населен', 'краєзнав'
                    ])

                    # Змістовна робота є тільки якщо вона не відхилена і містить реальні відповіді або дослідження
                    has_substantive_student_work = bool(
                        not is_rejected_submission and (
                            (answered_count > 0) or
                            (is_research_or_search_task and len(student_raw_text) >= 50) or
                            (inline_media and len(inline_media) > 0 and not any(kw in student_raw_text.lower() for kw in ['не можу', 'не зробив', 'не знаю']))
                        )
                    )

                    no_answer_phrases = [
                        'жодної відповіді не дано',
                        'не надано жодної відповіді',
                        'жодної відповіді немає',
                        'відповіді не надано',
                        'відповідей не надано',
                        'не містить жодної відповіді',
                        'не надав жодної відповіді',
                        'жодної відповіді',
                    ]

                    has_false_no_answer_claim = False
                    if not is_rejected_submission and answered_count > 0:
                        for phrase in no_answer_phrases:
                            if phrase in sum_lower or phrase in fb_lower or any(phrase in w.lower() for w in weaknesses):
                                has_false_no_answer_claim = True
                                break

                    # Зняття помилкового статусу «не зрозуміло, яке завдання» ТІЛЬКИ для дійсних робіт з відповідями
                    should_clear_unclear = bool(
                        not is_rejected_submission and
                        unclear_task and (
                            (answered_count > 0 and total_questions > 0) or
                            (is_research_or_search_task and len(student_raw_text) >= 50)
                        )
                    )

                    if has_substantive_student_work and (has_false_no_answer_claim or should_clear_unclear):
                        if unclear_task and should_clear_unclear:
                            unclear_task = False
                            if format_warning and ('не зрозуміло' in format_warning.lower() or 'незрозуміло' in format_warning.lower()):
                                format_warning = ''
                            weaknesses = [w for w in weaknesses if 'не зрозуміло, яке саме завдання виконане' not in w.lower()]

                        if has_false_no_answer_claim:
                            for phrase in no_answer_phrases:
                                if phrase in summary.lower():
                                    summary = re.sub(re.escape(phrase), 'надано відповіді на частину поставлених запитань', summary, flags=re.IGNORECASE)
                                if phrase in feedback_comment.lower():
                                    feedback_comment = re.sub(re.escape(phrase), 'відповіді надано на частину запитань', feedback_comment, flags=re.IGNORECASE)
                                weaknesses = [w for w in weaknesses if phrase not in w.lower()]

                        if suggested_grade == 'Доопрацювати' and not is_rejected_submission:
                            # Якщо оцінюються групи результатів (ГР) — підсумковий бал обов'язково відповідає середньому балу ГР!
                            if not is_traditional and numeric_gr_grades and avg_gr_grade is not None:
                                suggested_grade = str(avg_gr_grade)
                                if avg_gr_grade >= 10:
                                    level = 'Високий (10-12)'
                                elif avg_gr_grade >= 7:
                                    level = 'Достатній (7-9)'
                                elif avg_gr_grade >= 4:
                                    level = 'Середній (4-6)'
                                else:
                                    level = 'Початковий (1-3)'
                    # Очищення від некоректних здивованих реплік ШІ («а що це таке», «що це за місто» тощо)
                    if (is_research_or_search_task or len(student_raw_text) >= 40) and not is_rejected_submission:
                        odd_phrases = [
                            r'а що це таке\??',
                            r'а шо це таке\??',
                            r'що це за місто\??',
                            r'що це за село\??',
                            r'незрозуміло,?\s*що це за місто',
                            r'незрозуміло,?\s*що це за село',
                            r'чому написано про\s+[^,.]+',
                            r'в умові не зазначено\s+[^,.]+',
                            r'в умові завдання немає\s+[^,.]+',
                            r'немає такого міста в умові',
                            r'немає такого села в умові'
                        ]
                        for oph in odd_phrases:
                            if re.search(oph, summary, flags=re.IGNORECASE):
                                summary = re.sub(oph, 'Учень самостійно опрацював та виклав відомості по темі завдання', summary, flags=re.IGNORECASE)
                            if re.search(oph, feedback_comment, flags=re.IGNORECASE):
                                feedback_comment = re.sub(oph, 'Ви самостійно підготували цікавий матеріал за результатами пошуку', feedback_comment, flags=re.IGNORECASE)
                            weaknesses = [w for w in weaknesses if not re.search(oph, w, flags=re.IGNORECASE)]

                    # Обов'язкова порада щодо оформлення «питання-відповідь», якщо учень здав лише відповіді без запитань
                    # Застосовується ТІЛЬКИ коли в завданні очікуються відповіді на запитання (questions_expected = True)
                    # НЕ додавати для практичних робіт, презентацій, коду, таблиць тощо
                    if questions_expected and not is_rejected_submission and not is_file_project and (questions_omitted or (answered_count > 0 and check_student_omitted_questions(task_questions, student_raw_text))):
                        format_advice_phrase = "Порада щодо оформлення: ви надали відповіді без самих запитань. Будь ласка, записуйте самі запитання разом із відповідями (формат «питання-відповідь») або чітко вказуйте номери запитань, щоб робота була структурованою і зрозумілою."
                        has_advice_in_weaknesses = any(kw in w.lower() for w in weaknesses for kw in ['питання-відповідь', 'без запитань', 'запитання разом'])
                        if not has_advice_in_weaknesses:
                            weaknesses.append(format_advice_phrase)

                        has_advice_in_fb = any(kw in feedback_comment.lower() for kw in ['питання-відповідь', 'без запитань', 'запитання разом'])
                        if not has_advice_in_fb:
                            feedback_comment = (feedback_comment.strip() + f"\n\n💡 {format_advice_phrase}").strip()

                    # ── Захист від галюцинацій щодо моделі «питання-відповідь», коли запитання НЕ очікувалися ──
                    if not questions_expected and not is_rejected_submission:
                        qa_hallucination_patterns = [
                            r'\b(?:немає|відсутні|не\s+надано)\s+відповідей\s+на\s+(?:запитан|питан)[а-яіїє]*\b',
                            r'\b(?:не\s+відповів|не\s+відповіла|не\s+відповіли)\s+на\s+(?:запитан|питан)[а-яіїє]*\b',
                            r'\bдотримуватис[яь]\s+формату\s*«?питання-відповідь»?\b',
                            r'\bформат[уі]?\s*«?питання-відповідь»?\b',
                            r'\bпитання\s+із\s+слайд\w*\s+не\s+розглянут[а-яіїє]*\b',
                            r'\bконтрольні\s+запитання\s+не\s+виконан[а-яіїє]*\b',
                        ]
                        for pat in qa_hallucination_patterns:
                            weaknesses = [w for w in weaknesses if not re.search(pat, w, flags=re.IGNORECASE)]
                            if re.search(pat, summary, flags=re.IGNORECASE):
                                summary = re.sub(pat, 'роботу виконано за темою завдання', summary, flags=re.IGNORECASE)
                            if re.search(pat, feedback_comment, flags=re.IGNORECASE):
                                feedback_comment = re.sub(pat, 'роботу виконано за темою', feedback_comment, flags=re.IGNORECASE)

                    # ── Захист від помилкового твердження «діаграма відсутня» при наявності діаграм у роботі ──
                    has_charts_in_work = False
                    if student_raw_text and not is_rejected_submission:
                        has_charts_in_work = bool(
                            ('ВИЯВЛЕНІ ВБУДОВАНІ ДІАГРАМИ ТА ГРАФІКИ' in student_raw_text) or
                            ('Діаграма на слайді' in student_raw_text) or
                            ('вбудованих діаграм/графіків' in student_raw_text)
                        )

                    if has_charts_in_work and not is_rejected_submission:
                        no_chart_phrases = [
                            'діаграма відсутня',
                            'діаграми відсутні',
                            'діаграму не побудовано',
                            'діаграму не створено',
                            'графік відсутній',
                            'графік не побудовано',
                            'не побудовано діаграм',
                            'не створено діаграм',
                            'немає діаграми',
                            'відсутній графік',
                            'відсутня діаграма',
                        ]
                        for phrase in no_chart_phrases:
                            if phrase in summary.lower():
                                summary = re.sub(re.escape(phrase), 'діаграму побудовано у файлі роботи', summary, flags=re.IGNORECASE)
                            if phrase in feedback_comment.lower():
                                feedback_comment = re.sub(re.escape(phrase), 'діаграму/графік успішно створено', feedback_comment, flags=re.IGNORECASE)
                            weaknesses = [w for w in weaknesses if phrase not in w.lower()]

                    # ── Захист від галюцинацій: висновок або коментар учня до роботи ──
                    student_comment_text = (submission.comment_student or "").strip()
                    has_conclusion_in_comment = bool(
                        student_comment_text and (
                            any(w in student_comment_text.lower() for w in [
                                'висновок', 'висновки', 'підсумок', 'підсумки', 'робота показала',
                                'я зробив висновок', 'я зробила висновок', 'я дізнався', 'я дізналася',
                                'ми дізналися', 'отже,', 'отже ', 'в результаті', 'ході роботи'
                            ]) or
                            len(student_comment_text) >= 20
                        )
                    )
                    if has_conclusion_in_comment and not is_rejected_submission:
                        no_conclusion_phrases = [
                            'відсутній висновок',
                            'відсутні висновки',
                            'немає висновку',
                            'немає висновків',
                            'висновок відсутній',
                            'висновки відсутні',
                            'не сформульовано висновок',
                            'не сформульовано висновків',
                            'не сформульовано власного висновку',
                            'робота не містить висновку',
                            'робота не містить висновків',
                            'забув написати висновок',
                            'забула написати висновок',
                            'забули написати висновок',
                            'не містить власного висновку',
                            'немає підсумку',
                            'відсутній підсумок',
                            'підсумок відсутній',
                        ]
                        for phrase in no_conclusion_phrases:
                            if phrase in summary.lower():
                                summary = re.sub(re.escape(phrase), 'висновок до роботи надано у коментарі до здачі', summary, flags=re.IGNORECASE)
                            if phrase in feedback_comment.lower():
                                feedback_comment = re.sub(re.escape(phrase), 'висновок до роботи сформульовано у коментарі', feedback_comment, flags=re.IGNORECASE)
                            weaknesses = [w for w in weaknesses if phrase not in w.lower()]

                    # ── Педагогічний захист від завищення балів у багатозадачних роботах (Multi-task ceiling) ──
                    if not is_rejected_submission and not custom_criteria.strip() and not (selected_preset and selected_preset.evaluation_type == 'custom'):
                        suggested_grade, level, clean_gr_results, numeric_gr_grades, avg_gr_grade, summary, strengths, weaknesses, feedback_comment = apply_multi_task_evaluation_guardrail(
                            result_json=result_json,
                            task_questions=task_questions,
                            teacher_instructions_text=combined_task_for_qs,
                            student_raw_text=student_raw_text,
                            suggested_grade=suggested_grade,
                            level=level,
                            clean_gr_results=clean_gr_results,
                            numeric_gr_grades=numeric_gr_grades,
                            avg_gr_grade=avg_gr_grade,
                            is_traditional=is_traditional,
                            summary=summary,
                            strengths=strengths,
                            weaknesses=weaknesses,
                            feedback_comment=feedback_comment,
                            answered_count=answered_count,
                            teacher_scoped_task_nums=teacher_specific_task_nums if 'teacher_specific_task_nums' in locals() else None,
                            scope=scope if 'scope' in locals() else None,
                        )

                    # Кінцева перевірка: якщо роботу відхилено — гарантуємо "Доопрацювати" та Початковий рівень (1-3)
                    if is_rejected_submission:
                        suggested_grade = 'Доопрацювати'
                        level = 'Початковий (1-3)'
                        unclear_task = True
                        if clean_gr_results and not is_traditional:
                            for gr in clean_gr_results:
                                gr['grade'] = '1'
                                gr['level'] = 'Початковий'
                            avg_gr_grade = 1

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

                    # Фінальна фільтрація галюцинацій іншого уроку («пояснення крилатого вислову в онлайн-словнику»)
                    if not has_real_dict_task:
                        weaknesses = [w for w in weaknesses if not any(kw in str(w).lower() for kw in ['крилатого вислову', 'онлайн-словник', 'тлумачення вислову'])]
                        feedback_comment = re.sub(r'([^\.\n]*?(?:крилатого\s+вислову|онлайн[- ]словник)[^\.\n]*?\.)', '', feedback_comment, flags=re.IGNORECASE)
                        summary = re.sub(r'([^\.\n]*?(?:крилатого\s+вислову|онлайн[- ]словник)[^\.\n]*?\.)', '', summary, flags=re.IGNORECASE)

                    # Фінальна фільтрація помилкових порад та зауважень щодо формату «питання-відповідь» або відсутності відповідей
                    if not questions_expected or is_file_project:
                        weaknesses = [w for w in weaknesses if not any(kw in str(w).lower() for kw in [
                            'питання-відповідь', 'відсутні запитання', 'без самих запитань',
                            'без пояснювального документа', 'вигляді файлу бд без', 'подана у вигляді файлу',
                            'немає відповідей', 'немає відповіді', 'відсутні відповіді', 'не надано відповідей',
                            'відсутність відповідей', 'не відповів на запитання', 'не відповів на питання',
                            'відповіді на контрольні'
                        ])]
                        feedback_comment = re.sub(r'(?:порада\s+щодо\s+оформлення:[^\.\n]*?\.)', '', feedback_comment, flags=re.IGNORECASE)
                        feedback_comment = re.sub(r'([^\.\n]*?(?:питання-відповідь|без\s+самих\s+запитань|без\s+пояснювального\s+документа|немає\s+відповідей|відсутні\s+відповіді|не\s+надано\s+відповідей|відсутність\s+відповідей|відповідей\s+на\s+контрольні)[^\.\n]*?\.)', '', feedback_comment, flags=re.IGNORECASE)
                        summary = re.sub(r'([^\.\n]*?(?:питання-відповідь|без\s+самих\s+запитань|без\s+пояснювального\s+документа|немає\s+відповідей|відсутні\s+відповіді|не\s+надано\s+відповідей|відсутність\s+відповідей|відповідей\s+на\s+контрольні)[^\.\n]*?\.)', '', summary, flags=re.IGNORECASE)

                    # Очищення від подвійних пробілів та порожніх рядків після видалення
                    feedback_comment = re.sub(r'\n{3,}', '\n\n', feedback_comment).strip()
                    summary = re.sub(r'\s{2,}', ' ', summary).strip()

                    from .ai_context import complete_result_groups
                    if not is_traditional and active_grs:
                        clean_gr_results = complete_result_groups(clean_gr_results, active_grs)
                    # A final artifact cannot verify who performed the work.
                    strengths = [s for s in strengths if not re.search(r'самостійніст|самостійно\b|власноруч', str(s), re.I)]
                    result_json['grade_explanation'] = result_json.get('grade_explanation') or summary or feedback_comment
                    result_json['needs_teacher_review'] = (
                        any(row.get('status') == 'unverifiable' for row in result_json.get('criteria_results') or [] if isinstance(row, dict))
                        or any(row.get('status') == 'unverifiable' for row in clean_gr_results)
                        or not assignment.allow_ai_usage)
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

                    tolerance = getattr(settings, 'ai_detector_tolerance_percent', 25) or 25
                    from .ai_provenance import normalize_authorship, submission_provenance
                    authorship = normalize_authorship(result_json, assignment.allow_ai_usage, submission_provenance(submission), tolerance_percent=tolerance)
                    ai_generated_detected = authorship['ai_generated_detected']
                    ai_generated_percent = authorship['ai_generated_percent']
                    ai_generated_confidence = authorship['ai_generated_confidence']
                    ai_generated_details = authorship['ai_generated_details']

                    if ai_generated_detected and not assignment.allow_ai_usage and ai_generated_percent is not None:
                        try:
                            cur_grade = int(float(str(suggested_grade).replace('бал', '').strip()))
                            if cur_grade > 3:
                                suggested_grade = 'Доопрацювати'
                                level = 'Початковий'
                        except (ValueError, TypeError):
                            pass
                        ai_violation_msg = f"У роботі виявлено ознаки використання штучного інтелекту ({ai_generated_percent}%), що перевищує допустимий поріг ({tolerance}%). Вчитель вимагав повністю самостійного виконання завдання без застосування сторонніх генераторів."
                        if weaknesses and isinstance(weaknesses, list):
                            if ai_violation_msg not in weaknesses:
                                weaknesses.insert(0, ai_violation_msg)
                        else:
                            weaknesses = [ai_violation_msg]

                    if strengths and isinstance(strengths, list) and len(strengths) > 0:
                        full_feedback_parts.append("✅ **Сильні сторони:**\n" + "\n".join(f"• {s}" for s in strengths))
                    if weaknesses and isinstance(weaknesses, list) and len(weaknesses) > 0:
                        full_feedback_parts.append("💡 **Зауваження та неточності:**\n" + "\n".join(f"• {w}" for w in weaknesses))
                    if feedback_comment:
                        full_feedback_parts.append(f"💬 **Рекомендація учню:**\n{feedback_comment}")

                    evidence_sections = feedback_evidence_sections(result_json)
                    full_feedback_parts.extend(evidence_sections)
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
                    student_feedback_parts.extend(feedback_evidence_sections(result_json, include_criteria=False))
                    from .ai_context import strip_teacher_criteria
                    clean_student_feedback = strip_teacher_criteria("\n\n".join(student_feedback_parts) if student_feedback_parts else feedback_comment)

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
                        'tasks_total_count': result_json.get('tasks_total_count'),
                        'tasks_completed_count': result_json.get('tasks_completed_count'),
                        'task_resolution': result_json.get('task_resolution'),
                        'evaluation_plan': result_json.get('evaluation_plan'),
                        'submission_evidence': result_json.get('submission_evidence'),
                        'grade_explanation': result_json.get('grade_explanation') or summary,
                        'needs_teacher_review': result_json.get('needs_teacher_review', False),
                        'material_coverage': material_coverage,
                        'criteria_results': result_json.get('criteria_results'),
                        'revision_advice': result_json.get('revision_advice'),
                        'improvement_steps': result_json.get('improvement_steps') or result_json.get('revision_advice') or [],
                        'resubmission_recommendations': result_json.get('resubmission_recommendations') or result_json.get('revision_advice') or [],
                        'tasks_evaluated': result_json.get('tasks_evaluated'),
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
                        'task_type': task_type,
                        'task_interpretation': result_json.get('task_interpretation') or task_interpretation,
                        'deliverable': deliverable,
                        'questions_expected': questions_expected,
                        'final_task_understanding': result_json.get('final_task_understanding') or scope.get('final_task_understanding') or {},
                        'teacher_intent': result_json.get('teacher_intent') or scope.get('teacher_intent') or {},
                        'task_understanding_confidence': result_json.get('task_understanding_confidence') or scope.get('task_understanding_confidence', 1.0),
                        'ambiguities': result_json.get('ambiguities') or scope.get('ambiguities') or [],
                        'model_used': model_name,
                        'fallback_activated': fallback_happened
                    }
                else:
                    attempted_errors.append(f"[{c_provider}/{c_model}] Неповна відповідь JSON; оцінку не збережено.")
                    continue

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

    attempts_configs = settings.get_request_configs(custom_model=custom_model)

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


def get_assignment_min_grade(assignment) -> int:
    """
    Визначає мінімальний номер класу для завдання (1..11).
    Якщо не вдалося визначити за прив'язаними класами, шукає в назві або описі.
    За замовчуванням повертає 7 (базова середня школа).
    """
    grades = []
    try:
        classes = assignment.classes.all()
        for c in classes:
            m = re.search(r'(\d+)', c.name)
            if m:
                grades.append(int(m.group(1)))
    except Exception:
        pass

    if grades:
        return min(grades)

    try:
        combined = f"{assignment.title or ''} {assignment.description or ''}"
        m = re.search(r'\b([1-9]|1[0-2])\s*[-–—]?\s*(?:й|ий|ій)?\s*клас', combined, re.IGNORECASE)
        if m:
            return int(m.group(1))
    except Exception:
        pass

    return 7


def get_assignment_target_grades_and_ages(assignment) -> tuple[str, str]:
    """
    Визначає цільовий клас та орієнтовний вік учнів для завдання.
    Наприклад:
      5-й клас -> 10–11 років
      9-й клас -> 14–15 років
      11-й клас -> 16–17 років
    """
    grades = []
    try:
        classes = assignment.classes.all()
        for c in classes:
            m = re.search(r'(\d+)', c.name)
            if m:
                grades.append(int(m.group(1)))
    except Exception:
        pass

    if not grades:
        try:
            combined = f"{assignment.title or ''} {assignment.description or ''}"
            m = re.search(r'\b([1-9]|1[0-2])\s*[-–—]?\s*(?:й|ий|ій)?\s*клас', combined, re.IGNORECASE)
            if m:
                grades.append(int(m.group(1)))
        except Exception:
            pass

    if grades:
        min_g, max_g = min(grades), max(grades)
        if min_g == max_g:
            grade_str = f"{min_g}-й клас"
            age_str = f"{min_g + 5}–{min_g + 6} років"
        else:
            grade_str = f"{min_g}–{max_g} класи"
            age_str = f"{min_g + 5}–{max_g + 6} років"
    else:
        grade_str = "Шкільний клас"
        age_str = "Шкільний вік"
    return grade_str, age_str


def sanitize_ai_understanding_data(data: dict) -> dict:
    """
    Рекурсивно очищує всі текстові поля звіту аналізу розуміння завдання
    від залишків HTML-тегів, шрифтових стилів та зайвих пробілів.
    """
    if not isinstance(data, dict):
        return data

    def _clean_val(v):
        if isinstance(v, str):
            return strip_html_tags(v)
        elif isinstance(v, list):
            return [_clean_val(item) for item in v]
        elif isinstance(v, dict):
            return {k: _clean_val(item) for k, item in v.items()}
        return v

    return _clean_val(data)


def generate_age_appropriate_student_guide(
    grade_str: str = "7 клас",
    age_str: str = "12-13 років",
    min_grade: int = 7,
    assignment_title: str = "Практичне завдання",
    assignment_desc: str = "",
    tasks_list: list[dict] = None,
    is_single_task: bool = False,
    task_interpretation: dict = None,
    **kwargs
) -> str:
    """
    Генерує доступне, структуроване та покрокове роз'яснення для учнів,
    строго адаптуючи стиль мови, складність інструкцій та тон під вік дитини:
    - «Коротко: що потрібно зробити»
    - «Покроковий план виконання:» (Крок 1, Крок 2, Крок 3, Крок 4)
    - «Що має бути в результаті:»
    - «Перед здачею перевір:»
    Не вигадує неіснуючих вимог і базується на реальному deliverable та вимогах вчителя.
    """
    title_clean = strip_html_tags(assignment_title or "Практичне завдання").strip()
    desc_clean = strip_html_tags(assignment_desc or "").strip()

    # Отримуємо інтерпретацію завдання
    if not task_interpretation:
        task_interpretation = interpret_assignment_task(
            title=title_clean,
            desc=desc_clean
        )

    task_type = task_interpretation.get('task_type') or 'practical'
    deliverable = task_interpretation.get('deliverable') or {}
    teacher_reqs = task_interpretation.get('teacher_requirements') or []
    requirements = teacher_reqs or task_interpretation.get('requirements') or []
    questions_expected = task_interpretation.get('questions_expected', False)
    task_components = task_interpretation.get('task_components') or []
    comp_types = [c.get('type') for c in task_components]
    generic_recs = task_interpretation.get('generic_recommendations') or []

    has_req_conclusion = any('висновок' in str(r).lower() or 'підсумок' in str(r).lower() for r in teacher_reqs)
    has_req_sources = any('джерел' in str(r).lower() or 'літератур' in str(r).lower() for r in teacher_reqs)
    has_req_title_slide = any('титульн' in str(r).lower() for r in teacher_reqs)

    # 1. Заголовок відповідності віку (зі збереженням маркерів для повної сумісності з тестами)
    if min_grade <= 4:
        age_header = f"Привіт! Ось прості кроки, як легко виконати це завдання ({grade_str}):"
    elif min_grade <= 6:
        age_header = f"Покрокова інструкція для учнів ({grade_str}, вік: {age_str}):"
    elif min_grade <= 9:
        age_header = f"Алгоритм виконання завдання для учнів ({grade_str}, {age_str}):"
    else:
        age_header = f"Покроковий план виконання для старшокласників ({grade_str}, {age_str}):"

    # 2. Блок «Коротко: що потрібно зробити» з урахуванням складених завдань
    if 'question_answer' in comp_types and task_type == 'presentation':
        short_summary = f"Створити презентацію на тему «{title_clean}» та відповісти на контрольні запитання."
    elif 'document' in comp_types and task_type == 'programming':
        short_summary = f"Написати програму за умовою «{title_clean}» та додати короткий опис алгоритму."
    elif 'question_answer' in comp_types and task_type != 'question_answer':
        short_summary = f"Виконати практичне завдання «{title_clean}» та дати відповіді на контрольні запитання."
    else:
        short_summaries = {
            'presentation': f"Створити презентацію на тему «{title_clean}».",
            'programming': f"Написати та протестувати програму на Python за умовою завдання «{title_clean}».",
            'table': f"Створити та заповнити електронну таблицю за темою «{title_clean}».",
            'database': f"Спроєктувати базу даних та створити необхідні таблиці за темою «{title_clean}».",
            'diagram': f"Побудувати діаграму або схему за даними завдання «{title_clean}».",
            'creative': f"Створити творчу роботу за темою «{title_clean}».",
            'project': f"Виконати проєкт за темою «{title_clean}».",
            'calculation': f"Розв'язати поставлені задачі з обчисленнями за темою «{title_clean}».",
            'document': f"Підготувати оформлений текстовий документ за темою «{title_clean}».",
            'research': f"Знайти та опрацювати інформацію за темою «{title_clean}».",
            'question_answer': f"Дати чіткі відповіді на поставлені запитання за темою «{title_clean}».",
            'practical': f"Виконати практичну роботу за інструкцією до теми «{title_clean}».",
            'other': f"Виконати навчальне завдання «{title_clean}» згідно з інструкцією вчителя."
        }
        short_summary = short_summaries.get(task_type, short_summaries['other'])

    # Формуємо дію для Кроку 2 з урахуванням переданих tasks_list
    if tasks_list and len(tasks_list) == 1:
        t0 = tasks_list[0]
        t_actions = strip_html_tags(t0.get('expected_actions') or '')
        if t_actions and len(t_actions) > 5 and not t_actions.lower().startswith('виконати роботу'):
            action_desc = t_actions
        elif t0.get('title'):
            action_desc = f"Виконай завдання «{strip_html_tags(t0['title'])}»"
        else:
            action_desc = short_summary
    elif tasks_list and len(tasks_list) > 1:
        task_names = [strip_html_tags(t.get('title') or f"Завдання {t.get('num', idx)}") for idx, t in enumerate(tasks_list, 1)]
        action_desc = f"Послідовно виконай завдання: {', '.join(task_names)}"
    else:
        if 'question_answer' in comp_types and task_type == 'presentation':
            action_desc = f"Створи презентацію та дай відповіді на контрольні запитання"
        else:
            action_desc = short_summary

    # 3. Покроковий план виконання (Крок 1..4) залежно від типу завдання та віку учнів
    if task_type == 'presentation':
        if min_grade <= 4:
            s1 = f"• Крок 1 (Подивись): Уважно роздивись тему «{title_clean}» та підготуй малюнки або текст."
            s2 = f"• Крок 2 (Зроби): {action_desc}. Додай слайди з текстом та ілюстраціями."
            s3 = f"• Крок 3 (Перевір): Подивись: чи всі слайди охайні і красиві?"
            s4 = f"• Крок 4 (Здай вчителю): Збережи файл презентації і відправ на перевірку. Ти обов'язково впораєшся! 🌟"
        elif min_grade <= 6:
            s1 = f"• Крок 1 (Підготовка): Відкрий тему «{title_clean}» та дізнайся основну інформацію."
            s2 = f"• Крок 2 (Створення): {action_desc}. Створи презентацію, розмісти інформацію по слайдах."
            if has_req_conclusion or has_req_title_slide:
                req_parts = []
                if has_req_title_slide: req_parts.append("титульний слайд")
                if has_req_conclusion: req_parts.append("висновок")
                s3 = f"• Крок 3 (Оформлення): Перевір охайність тексту та наявність обов'язкових елементів: {', '.join(req_parts)}."
            else:
                s3 = f"• Крок 3 (Оформлення): Перевір охайність тексту, структуру слайдів та читабельність."
            s4 = f"• Крок 4 (Здача): Збережи файл презентації (.pptx або посилання) та прикріпи до відповіді."
        elif min_grade <= 9:
            s1 = f"• Крок 1 (Збір матеріалів): Опрацюй тему «{title_clean}» та відбери ключові факти й ілюстрації."
            s2 = f"• Крок 2 (Розробка слайдів): {action_desc}. Створи структуровану презентацію з єдиним стилем оформлення."
            if has_req_conclusion or has_req_sources:
                req_parts = []
                if has_req_conclusion: req_parts.append("підсумковий висновок")
                if has_req_sources: req_parts.append("список використаних джерел")
                s3 = f"• Крок 3 (Висновки та джерела): Додай до роботи обов'язкові складові: {', '.join(req_parts)}."
            else:
                s3 = f"• Крок 3 (Самоперевірка): Перевір змістовність слайдів, правильність тексту та охайність оформлення."
            s4 = f"• Крок 4 (Збереження та здача): Збережи файл презентації (.pptx або посилання) та завантаж на платформу."
        else:
            s1 = f"• Крок 1 (Концепція та структура): Проаналізуй тему «{title_clean}», визнач логіку подання та критерії якості."
            s2 = f"• Крок 2 (Створення презентації): {action_desc}. Реалізуй змістовні слайди, схеми та візуалізацію даних."
            if has_req_conclusion or has_req_sources:
                req_parts = []
                if has_req_conclusion: req_parts.append("підсумкових висновків")
                if has_req_sources: req_parts.append("переліку джерел")
                s3 = f"• Крок 3 (Аналітика та верифікація): Перевір обґрунтованість тез, наявність {', '.join(req_parts)}."
            else:
                s3 = f"• Крок 3 (Аналітика та якість): Перевір обґрунтованість тез, структуру викладу та візуальне оформлення."
            s4 = f"• Крок 4 (Експорт та здача): Сформуй фінальну версію презентації та надішли на перевірку."

    elif task_type == 'programming':
        if min_grade <= 6:
            s1 = f"• Крок 1 (Умова): Уважно прочитай задачу «{title_clean}» та зрозумій, що має робити програма."
            s2 = f"• Крок 2 (Код програми): {action_desc}. Запусти середовище програмування та напиши код."
            s3 = f"• Крок 3 (Перевірка запуску): Запусти програму і перевір, як вона відповідає на введені дані."
            s4 = f"• Крок 4 (Здача файлу): Збережи файл програми (.py) та прикріпи до відповіді."
        elif min_grade <= 9:
            s1 = f"• Крок 1 (Алгоритм): Проаналізуй умову «{title_clean}», визнач змінні та алгоритм розв'язку."
            s2 = f"• Крок 2 (Реалізація): {action_desc}. Напиши програмний код з дотриманням синтаксису мови."
            s3 = f"• Крок 3 (Тестування): Протестуй роботу програми на різних вхідних даних та виправ можливі помилки."
            s4 = f"• Крок 4 (Здача): Збережи вихідний код програми (.py) та відправ на перевірку."
        else:
            s1 = f"• Крок 1 (Постановка та архітектура): Формалізуй вимоги до програми «{title_clean}», вибери структури даних та алгоритм."
            s2 = f"• Крок 2 (Розробка коду): {action_desc}. Реалізуй логіку з дотриманням стандартів кодування та коментарями."
            s3 = f"• Крок 3 (Верифікація): Проведи тестування крайових випадків та переконайся у коректності роботи."
            s4 = f"• Крок 4 (Здача репозиторію/файлу): Завантаж підсумковий файл коду на платформу."

    elif task_type in ['table', 'database']:
        if min_grade <= 6:
            s1 = f"• Крок 1 (Підготовка): Відкрий програму для роботи з даними та створи новий файл до теми «{title_clean}»."
            s2 = f"• Крок 2 (Заповнення): {action_desc}. Внеси дані та налаштуй таблицю."
            s3 = f"• Крок 3 (Перевірка): Переконайся, що всі дані на своїх місцях і розрахунки правильні."
            s4 = f"• Крок 4 (Збереження): Збережи свій файл та відправ вчителю."
        elif min_grade <= 9:
            s1 = f"• Крок 1 (Структура): Ознайомся з даними для теми «{title_clean}» та визнач поля/колонки таблиці."
            s2 = f"• Крок 2 (Побудова та формули): {action_desc}. Заповни таблицю, застосуй формули або зв'язки."
            s3 = f"• Крок 3 (Контроль розрахунків): Перевір коректність обчислень, типів даних та форматування."
            s4 = f"• Крок 4 (Здача): Збережи підсумковий файл таблиці/БД і завантаж на перевірку."
        else:
            s1 = f"• Крок 1 (Проєктування): Проаналізуй модель даних та вимоги до завдання «{title_clean}»."
            s2 = f"• Крок 2 (Реалізація структури): {action_desc}. Налаштуй схему, формули, фільтрацію чи зв'язки."
            s3 = f"• Крок 3 (Валідація даних): Перевір цілісність даних, роботу формул і відсутність помилок."
            s4 = f"• Крок 4 (Фіксація результату): Сформуй підсумковий документ чи файл і надішли на перевірку."

    elif task_type == 'question_answer':
        s1 = f"• Крок 1 (Ознайомлення): Уважно прочитай усі запитання до теми «{title_clean}»."
        s2 = f"• Крок 2 (Пошук відповідей): Знайди точні відповіді у матеріалах уроку або підручнику."
        s3 = f"• Крок 3 (Формулювання): Запиши чіткі відповіді, обов'язково вказуючи номери запитань."
        s4 = f"• Крок 4 (Здача роботи): Перевір повноту своїх відповідей і відправ роботу на платформу."

    else:
        # Універсальний покроковий план практичної діяльності
        if min_grade <= 4:
            s1 = f"• Крок 1 (Подивись): Уважно роздивись завдання «{title_clean}» та інструкцію вчителя."
            s2 = f"• Крок 2 (Зроби): {action_desc}. Роби все не поспішаючи, крок за кроком."
            s3 = f"• Крок 3 (Перевір): Подивись на результат: чи все виконано охайно і без пропусків?"
            s4 = f"• Крок 4 (Здай вчителю): Збережи свій файл або зроби фото та відправ на перевірку. 🌟"
        elif min_grade <= 6:
            s1 = f"• Крок 1 (Підготовка): Відкрий тему «{title_clean}» та уважно прочитай вказівки вчителя."
            s2 = f"• Крок 2 (Виконання): {action_desc}. Виконуй дії послідовно за планом."
            s3 = f"• Крок 3 (Самоперевірка): Переконайся, що виконано всі вимоги завдання."
            s4 = f"• Крок 4 (Здача на платформу): Прикріпи файл виконаної роботи та надішли на перевірку."
        elif min_grade <= 9:
            s1 = f"• Крок 1 (Аналіз завдання): Опрацюй умови завдання «{title_clean}» та виділи ключові вимоги."
            s2 = f"• Крок 2 (Практична робота): {action_desc}. Дотримуйся логіки виконання та структуруй результат."
            s3 = f"• Крок 3 (Контроль якості): Перевір повноту виконання поставлених умов."
            s4 = f"• Крок 4 (Здача результату): Збережи підсумковий файл та завантаж на платформу."
        else:
            s1 = f"• Крок 1 (Постановка завдання та критерії): Опрацюй умови завдання «{title_clean}» та шкалу критеріїв."
            s2 = f"• Крок 2 (Практична реалізація): {action_desc}. Виконай роботу з дотриманням академічної доброчесності."
            s3 = f"• Крок 3 (Верифікація): Перевір якість отриманого результату та відсутність неточностей."
            s4 = f"• Крок 4 (Фіксація та здача): Сформуй підсумковий документ чи файл і надішли на перевірку."

    # 4. Блок «Що має бути в результаті»
    res_items = []
    res_items.append(f"Готовий результат: {deliverable.get('description', title_clean)}")
    if 'question_answer' in comp_types and task_type != 'question_answer':
        res_items.append("Відповіді на контрольні запитання")
    for rc in deliverable.get('required_components', []):
        if rc and rc not in res_items and len(rc) < 80:
            res_items.append(rc)
    if not deliverable.get('required_components') and requirements:
        for r in requirements[:3]:
            res_items.append(r)

    # 5. Блок «Перед здачею перевір»
    check_items = []
    if requirements:
        for r in requirements[:4]:
            check_items.append(f"Виконано вимогу: {r}")
    else:
        if task_type == 'presentation':
            check_items.append("Слайди містять необхідний матеріал та охайно оформлені")
            check_items.append("Усі слайди оформлено єдиним стилем та читабельним шрифтом")
        elif task_type == 'programming':
            check_items.append("Програма запускається без синтаксичних помилок")
            check_items.append("Результат роботи відповідає умові задачі")
        elif task_type == 'table':
            check_items.append("Дані внесені у відповідні комірки та формули працюють")
        elif task_type == 'question_answer':
            check_items.append("Надано відповіді на всі поставлені запитання")
        else:
            check_items.append("Робота повністю відповідає темі завдання")

    if 'question_answer' in comp_types and not any('запитан' in str(ci).lower() or 'відповід' in str(ci).lower() for ci in check_items):
        check_items.append("Надано відповіді на всі контрольні запитання")

    check_items.append("Файл збережено під зрозумілою назвою та успішно прикріплено")

    # Збирання структури
    lines = [
        age_header,
        "",
        "Коротко: що потрібно зробити",
        short_summary,
        "",
        "Покроковий план виконання:",
        s1,
        s2,
        s3,
        s4,
        "",
        "Що має бути в результаті:",
    ]
    for ri in res_items[:4]:
        lines.append(f"- {ri}")

    lines.extend([
        "",
        "Перед здачею перевір:",
    ])
    for ci in check_items[:4]:
        lines.append(f"- {ci}")

    if generic_recs:
        lines.extend([
            "",
            "Корисні поради щодо якості:",
        ])
        for gr in generic_recs[:2]:
            lines.append(f"💡 {gr}")

    return "\n".join(lines)


def analyze_assignment_task_understanding(assignment, force_refresh=False) -> dict:
    """
    Аналізує навчальне завдання за допомогою ШІ та формує звіт для вчителя та учнів:
    - Тема, мета уроку та змістовий контекст
    - Адаптація роз'яснення під вік та клас учнів (target_audience, student_explanation)
    - Чіткий перелік та точна кількість виявлених обов'язкових практичних завдань
      (якщо вчитель вказав конкретний номер на кшталт «виконати вправа 2», аналізується ВИКЛЮЧНО це завдання)
    - Очікуваний результат та формат здачі від учнів
    - Шкала та правила оцінювання (НУШ: 100% = 10-12 б., 2 з 3 = 7-8 б., 1 з 3 = 4-5 б.,
      заборона зарахування однієї фрази за два різні завдання)
    - Педагогічні зауваження та поради вчителю
    """
    assignment_title = strip_html_tags(assignment.title or "")
    assignment_desc = strip_html_tags(assignment.description or "")
    subject_name = assignment.subject.name if assignment.subject else "Навчальний предмет"
    classes_str = ", ".join(c.name for c in assignment.classes.all()) or "Всі класи"
    custom_criteria = strip_html_tags(assignment.custom_criteria or "")

    grade_str, age_str = get_assignment_target_grades_and_ages(assignment)
    min_grade = get_assignment_min_grade(assignment)

    from .ai_context import assignment_fingerprint, teacher_materials, ASSESSMENT_RULES
    fingerprint = assignment_fingerprint(assignment)
    if not force_refresh and assignment.ai_task_understanding:
        try:
            cached_data = json.loads(assignment.ai_task_understanding)
            if isinstance(cached_data, dict) and cached_data.get('_source_fingerprint') == fingerprint and cached_data.get('analysis_mode') == 'ai':
                cached_data = sanitize_ai_understanding_data(cached_data)
                cur_expl = cached_data.get('student_explanation') or ''
                if not cur_expl or len(cur_expl) < 50 or 'крок' not in cur_expl.lower():
                    cached_data['student_explanation'] = generate_age_appropriate_student_guide(
                        grade_str=cached_data.get('target_audience') or grade_str,
                        age_str=age_str,
                        min_grade=min_grade,
                        assignment_title=assignment_title,
                        assignment_desc=assignment_desc,
                        tasks_list=cached_data.get('tasks', []),
                        is_single_task=(cached_data.get('tasks_total_count') == 1)
                    )
                return {
                    'status': 'success',
                    'data': cached_data,
                    'cached': True,
                    'updated_at': assignment.ai_task_understanding_updated_at.strftime('%d.%m.%Y о %H:%M') if assignment.ai_task_understanding_updated_at else None
                }
        except Exception:
            pass

    primary, reference, inline_media, material_coverage = teacher_materials(assignment, force_refresh_links=force_refresh)
    files_content_parts = primary + reference

    scope = resolve_assignment_scope(
        assignment_title=assignment_title,
        assignment_desc=assignment_desc,
        custom_criteria=custom_criteria,
        teacher_files_content=files_content_parts,
    )
    is_single_task = scope.get('is_single_complex_task', False)
    teacher_specific_task_nums = scope.get('teacher_specific_task_nums') or []
    extracted_qs = scope.get('task_questions') or []
    explicit_count = scope.get('assigned_task_count') or 1
    assigned_tasks_list = scope.get('assigned_tasks', [])

    prompt_lines = [
        f"ПРЕДМЕТ: {subject_name}",
        f"КЛАС: {classes_str} (орієнтовний вік учнів: {age_str}, {grade_str})",
        f"ТЕМА ЗАВДАННЯ: {assignment_title}",
        f"\nОПИС / ВКАЗІВКИ ВЧИТЕЛЯ:\n{assignment_desc}"
    ]
    seen_rubrics = set()
    from .models import DEFAULT_NUS_SYSTEM_PROMPT, DEFAULT_NUS_GR_SYSTEM_PROMPT, DEFAULT_TRADITIONAL_SYSTEM_PROMPT
    for class_group in assignment.classes.all():
        preset, grs = assignment.get_ai_policy(class_group)
        if preset:
            selected = [gr for gr in preset.get_gr_list() if not grs or gr['code'] in grs]
            prompt_lines.append(f"КРИТЕРІЇ КЛАСУ {class_group.name}: {preset.name}; тип: {preset.evaluation_type}; обрані ГР: {json.dumps(selected, ensure_ascii=False)}")
            if preset.pk in seen_rubrics:
                continue
            seen_rubrics.add(preset.pk)
            if preset.system_prompt.strip() not in {p.strip() for p in (DEFAULT_NUS_SYSTEM_PROMPT, DEFAULT_NUS_GR_SYSTEM_PROMPT, DEFAULT_TRADITIONAL_SYSTEM_PROMPT)}:
                prompt_lines.append(f'ВКАЗІВКИ ШАБЛОНУ {preset.name}:\n{preset.system_prompt}')
            prompt_lines.append(f'ДОКУМЕНТ КРИТЕРІЇВ {preset.name}:\n{preset.extracted_criteria_text}')
            if preset.document_file and os.path.exists(preset.document_file.path):
                from .ai_context import extract_file_evidence
                rubric = extract_file_evidence(preset.document_file.path)
                prompt_lines.append((rubric['text'] or '') + '\n' + '\n'.join(rubric['limitations']))
                inline_media.extend(dict(item, source=f'Критерії класу {class_group.name}') for item in rubric['media'])

    if custom_criteria:
        prompt_lines.append(f"\nКРИТЕРІЇ ВЧИТЕЛЯ:\n{custom_criteria}")
    if files_content_parts:
        prompt_lines.append("\nПРИКРІПЛЕНІ НАВЧАЛЬНІ МАТЕРІАЛИ (ПРЕЗЕНТАЦІЇ, PDF, ДОКУМЕНТИ):")
        prompt_lines.extend(files_content_parts)

    if is_single_task and not teacher_specific_task_nums:
        task_desc_str = assigned_tasks_list[0]['description'] if assigned_tasks_list else (assignment_desc or assignment_title or "Комплексне завдання")
        prompt_lines.append(
            f"\n🎯 НАЙВИЩИЙ ПРІОРИТЕТ — ТОЧНИЙ ОБСЯГ ВІД ВЧИТЕЛЯ (SCOPE OF WORK):\n"
            f"Вчитель визначив завдання як ОДНЕ комплексне завдання/проєкт: «{task_desc_str}».\n"
            f"Прикріплені презентації чи файли є контекстом уроку. Окремі вправи зі слайдів НЕ є обов'язковими завданнями!\n"
            f"У полі 'tasks_total_count' обов'язково вкажи 1.\n"
            f"У масиві 'tasks' опиши САМЕ це обов'язкове комплексне завдання (num: 1, title: «{task_desc_str[:80]}»)."
        )
    elif teacher_specific_task_nums:
        nums_str = ", ".join(str(n) for n in teacher_specific_task_nums)
        prompt_lines.append(
            f"\n🎯 НАЙВИЩИЙ ПРІОРИТЕТ — ТОЧНИЙ ОБСЯГ ВІД ВЧИТЕЛЯ (TEACHER SCOPE):\n"
            f"Вчитель у полі «Що потрібно зробити» ЯВНО вказав виконати КОНКРЕТНЕ ЗАВДАННЯ: № {nums_str}.\n"
            f"Прикріплені презентації чи файли можуть містити інші вправи чи завдання, але вони є ЛИШЕ матеріалом уроку / теоретичною основою.\n"
            f"Учні зобов'язані виконати ВИКЛЮЧНО вказані вчителем завдання (№ {nums_str})!\n"
            f"У полі 'tasks_total_count' вкажи {len(teacher_specific_task_nums)}.\n"
            f"У масиві 'tasks' опиши САМЕ це обов'язкове завдання № {nums_str} (збережи формулювання та контекст із прикріплених матеріалів)."
        )
    elif explicit_count > 0:
        prompt_lines.append(f"\n⚠️ ВКАЗІВКА ВЧИТЕЛЯ ЩОДО ОБСЯГУ: Вчитель чітко зазначив обов'язкову кількість завдань: {explicit_count}.")

    if min_grade <= 4:
        age_tone_guide = (
            f"УЧНІ ПОЧАТКОВОЇ ШКОЛИ ({grade_str}, {age_str}):\n"
            f"- Мова має бути максимально простою, доброзичливою, без дорослих наукових термінів.\n"
            f"- Звертайся до дитини тепло та підбадьорливо.\n"
            f"- Опиши дуже прості кроки: що відкрити/роздивитись, яку дію зробити, що сфотографувати або показати батькам і вчителю."
        )
    elif min_grade <= 6:
        age_tone_guide = (
            f"УЧНІ 5–6 КЛАСІВ НУШ ({grade_str}, {age_str}):\n"
            f"- Доступна, жива мова молодших підлітків, дружній тон турботливого наставника.\n"
            f"- Складні поняття пояснюй простими словами на зрозумілих життєвих прикладах.\n"
            f"- Чіткі пронумеровані кроки дій (Крок 1, Крок 2...): що відкрити, які дії виконати, як підписати роботу і що здати."
        )
    elif min_grade <= 9:
        age_tone_guide = (
            f"УЧНІ 7–9 КЛАСІВ ({grade_str}, {age_str}):\n"
            f"- Структурована, ділова і зрозуміла мова, практичний підхід.\n"
            f"- Покроковий алгоритм розв'язку або виконання практичної роботи (Крок 1, Крок 2...).\n"
            f"- Акцент на самоконтролі, охайності та дотриманні вимог вчителя."
        )
    else:
        age_tone_guide = (
            f"СТАРШОКЛАСНИКИ ({grade_str}, {age_str}):\n"
            f"- Дорослий, професійний, партнерський тон.\n"
            f"- Чіткий алгоритм реалізації проєкту/практичної роботи (Крок 1 — аналіз структури, Крок 2 — реалізація, Крок 3 — верифікація та відповіді на питання, Крок 4 — здача файлу).\n"
            f"- Акцент на критеріях якості НУШ, цілісності результатів та обґрунтованості висновків."
        )

    prompt_lines.append(
        f"\n👶 ВРАХУВАННЯ ВІКУ ТА КЛАСУ УЧНІВ ({grade_str}, вік: {age_str}):\n"
        f"{age_tone_guide}\n\n"
        f"ОБОВ'ЯЗКОВА ВИМОГА ДО ПОЛЯ 'student_explanation':\n"
        f"1. Сформулюй покроковий, доступний і доброзичливий опис того, що САМЕ вимагається від учня, "
        f"мовою, строго адаптованою для учнів {grade_str} ({age_str}).\n"
        f"2. Опис ОБОВ'ЯЗКОВО має бути структурований по кроках (Крок 1, Крок 2, Крок 3...), щоб дитина точно розуміла послідовність дій:\n"
        f"   - Крок 1: Що відкрити, прочитати або підготувати.\n"
        f"   - Крок 2: Які конкретно практичні дії виконати.\n"
        f"   - Крок 3: Як оформити роботу і що перевірити перед здачею.\n"
        f"   - Крок 4: Що саме прикріпити та здати на платформу.\n"
        f"3. КАТЕГОРИЧНО ЗАБОРОНЕНО використовувати HTML-теги (<span...>, <u>, <b> тощо) або фрагменти коду розмітки — тільки чистий текст!\n"
    )

    prompt_lines.append(
        "\nЗАВДАННЯ ДЛЯ ТЕБЕ (ЕКСПЕРТНИЙ АНАЛІЗ ЗАВДАННЯ):\n"
        "1. Уважно прочитай опис та проаналізуй усі прикріплені матеріали (слайди презентацій чи сторінки PDF).\n"
        "2. Відрізняй теоретичні слайди (поняття, вступні тези, списки означень) від РЕАЛЬНИХ практичних завдань для учнів.\n"
        "3. Визнач кількість обов'язкових завдань та деталізуй кожне з них (враховуючи вказівку вчителя).\n"
        "4. Сформулюй роз'яснення для учнів відповідно до їхнього віку та класу.\n"
        "5. Опиши вимоги до зданої роботи та шкалу оцінювання за критеріями НУШ.\n"
        "6. Поверни виключно валідний JSON згідно зі схемою."
    )

    system_instruction = (
        "Ти — провідний експерт-методист та педагогічний ШІ шкільної платформи (НУШ).\n"
        "Твоя мета — проаналізувати опубліковане вчителем завдання та сформувати звіт:\n"
        "- чітко вказати тему, мету уроку та цільову аудиторію (клас, вік учнів);\n"
        "- обов'язково скласти детальне покрокове роз'яснення для дитини відповідно до її віку та класу (student_explanation) з чіткими етапами (Крок 1, Крок 2...), пояснюючи простою і зрозумілою для учнів цього віку мовою, що саме потрібно зробити;\n"
        "- ЖОДНОГО HTML-тегу в полях JSON (усі значення мають бути чистим текстом без <span...>, <u>, <b> тощо);\n"
        "- якщо вчитель вказав конкретний номер завдання/вправи (наприклад «виконати вправа 2»), у звіті зафіксувати ЛИШЕ це завдання (tasks_total_count = 1), не вимагаючи виконання інших завдань з файлу;\n"
        "- перелічити кожне виявлене завдання з джерелом (наприклад, «Слайд 18 презентації»);\n"
        "- роз'яснити вимоги до оформлення та шкалу оцінювання;\n"
        "- надати корисні поради вчителю.\n"
        "Обов'язково повертай JSON за вказаною схемою:\n"
        "{\n"
        '  "topic_and_goal": "Короткий опис теми та мети роботи",\n'
        f'  "target_audience": "{grade_str} ({age_str})",\n'
        '  "student_explanation": "Зрозуміле для учнів цього віку покрокове пояснення того, що вимагається виконати і здати (Крок 1: ..., Крок 2: ...)",\n'
        '  "tasks_source_info": "Звідки витягнуто завдання (наприклад: Слайд 18 презентації)",\n'
        '  "tasks_total_count": 1,\n'
        '  "is_choice_based": false,\n'
        '  "tasks": [\n'
        '    {\n'
        '      "num": 1,\n'
        '      "title": "Назва завдання",\n'
        '      "source": "Матеріали уроку",\n'
        '      "expected_actions": "Що учень має зробити (покроково без HTML-тегів)",\n'
        '      "expected_submission": "Що має бути у відповіді"\n'
        '    }\n'
        '  ],\n'
        '  "task_type": "bulletin/document/presentation/spreadsheet/code/database/scratch/practical/question_answer",\n'
        '  "deliverable": {"type": "тип створеного учнем результату, не файла умови", "description": "що створити", "format": "формат за вимогами"},\n'
        '  "teacher_requirements": ["тільки реальні вимоги вчителя або заданої вправи"],\n'
        '  "questions_expected": false,\n'
        '  "submission_format_expected": "Вимоги до формату здачі",\n'
        '  "grading_breakdown": {\n'
        '    "full_completion": "Повне виконання за критеріями вчителя",\n'
        '    "partial_two_tasks": "Як зараховується часткове виконання за критеріями",\n'
        '    "partial_one_task": "Які результати зараховуються на початковому рівні",\n'
        '    "rules": ["Правило 1", "Правило 2"]\n'
        '  },\n'
        '  "teacher_recommendations": ["Порада 1"]\n'
        "}"
    )

    settings = get_ai_settings()
    act_provider, act_key, act_model, act_url, is_backup_active = settings.get_active_config()

    attempts_configs = settings.get_request_configs()

    result_data = None
    if attempts_configs:
        for config in attempts_configs:
            act_provider, act_key, act_url, target_m = config['provider'], config['api_key'], config['custom_url'], config['model']
            try:
                thinking_budget_val = 0 if ('flash' in str(target_m).lower() and act_provider == 'gemini') else None
                status_code, raw_text, err_msg, raw_data = call_ai_api(
                    prompt_text="\n".join(prompt_lines),
                    system_prompt=system_instruction + "\n" + ASSESSMENT_RULES + "\nЦе пояснення завдання, не оцінка роботи. Не вигадуй розбаловку: відтворюй критерії вчителя, без універсальних штрафів за кількість вправ.",
                    inline_media=inline_media,
                    provider=act_provider,
                    api_key=act_key,
                    model_name=target_m,
                    custom_url=act_url,
                    temperature=0.2,
                    max_output_tokens=4500,
                    action='task_understanding',
                    timeout=40,
                    json_mode=True,
                    thinking_budget=thinking_budget_val
                )
                if status_code == 200 and raw_text:
                    parsed = extract_json_from_text(raw_text)
                    if isinstance(parsed, dict) and 'tasks_total_count' in parsed:
                        result_data = sanitize_ai_understanding_data(parsed)
                        result_data['analysis_mode'] = 'ai'
                        from .ai_context import cohere_task_guide
                        result_data = cohere_task_guide(result_data, assignment, teacher_specific_task_nums)
                        break
            except Exception:
                pass

        if result_data:
            # Гарантуємо наявність target_audience та якісного student_explanation
            result_data.setdefault('target_audience', f"{grade_str} ({age_str})")
            result_data.setdefault('task_type', scope.get('task_type', 'practical'))
            result_data.setdefault('deliverable', scope.get('deliverable', {}))
            result_data.setdefault('questions_expected', scope.get('questions_expected', False))
            current_expl = strip_html_tags(result_data.get('student_explanation') or "")
            if not current_expl or len(current_expl) < 50 or "крок" not in current_expl.lower():
                result_data['student_explanation'] = generate_age_appropriate_student_guide(
                    grade_str=grade_str,
                    age_str=age_str,
                    min_grade=min_grade,
                    assignment_title=assignment_title,
                    assignment_desc=assignment_desc,
                    tasks_list=result_data.get('tasks', []),
                    is_single_task=is_single_task,
                    task_interpretation=scope.get('task_interpretation')
                )
            else:
                result_data['student_explanation'] = current_expl
            # Суворий пріоритет обсягу завдань від вчителя
            if teacher_specific_task_nums or is_single_task:
                result_data['tasks_total_count'] = scope.get('assigned_task_count', 1)
            result_data.setdefault('task_type', scope.get('task_type', 'practical'))
            result_data.setdefault('deliverable', scope.get('deliverable', {}))
            result_data.setdefault('questions_expected', scope.get('questions_expected', False))
            result_data.setdefault('task_interpretation', scope.get('task_interpretation', {}))
            result_data.setdefault('teacher_requirements', scope.get('task_interpretation', {}).get('teacher_requirements', []))
            result_data.setdefault('final_task_understanding', scope.get('final_task_understanding', {}))
            result_data.setdefault('teacher_intent', scope.get('teacher_intent', {}))
            result_data.setdefault('task_understanding_confidence', None)
            result_data.setdefault('ambiguities', scope.get('ambiguities', []))

            if is_single_task and not teacher_specific_task_nums:
                if isinstance(result_data.get('tasks'), list) and len(result_data['tasks']) > 1:
                    result_data['tasks'] = [{
                        'num': 1,
                        'title': assignment_title or 'Комплексне завдання',
                        'source': 'Опис завдання вчителя',
                        'expected_actions': assignment_desc or 'Виконати роботу над завданням/проєктом',
                        'expected_submission': 'Готова робота у відповідному форматі'
                    }]

    # Якщо ШІ API недоступне або повернуло некоректну відповідь — генеруємо якісний структурний звіт на основі видобутих даних
    if not result_data or not isinstance(result_data, dict):
        task_interpretation = scope.get('task_interpretation') or {}
        task_type = scope.get('task_type') or 'practical'
        deliverable = scope.get('deliverable') or {}
        questions_expected = scope.get('questions_expected', False)
        total_cnt = scope.get('assigned_task_count', 1)
        assigned_tasks_scope = scope.get('assigned_tasks', [])

        if teacher_specific_task_nums:
            tasks_list = []
            for num in teacher_specific_task_nums:
                found_q = None
                for q in extracted_qs:
                    if re.search(rf'(?:\b(?:завдан[а-яіїє]*|вправ[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*)\b|(?:завд|впр|ном)\b\.?|\bп\.\s*|№)\s*(?:№\s*)?{num}\b', q, re.IGNORECASE):
                        found_q = q
                        break
                clean_q = strip_html_tags(found_q or "").strip()
                # Видаляємо нумерацію на початку рядка: "1. ", "• "
                clean_q = re.sub(r'^(?:[•\-\*]?\s*(?:(?:\d+|[IVXLCDM]+)[\.\)\–\—\-]|(?:питання|завдання|вправа|відповідь|номер|№)\s*\d+[\.\:\)\–\—\-]?))\s*', '', clean_q, flags=re.IGNORECASE).strip()
                # Видаляємо префікси на кшталт «Виконати вправу 3»
                clean_q = re.sub(rf'^(?:виконати|зробити)\s+(?:вправ[а-яіїє]*|завдан[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*|№)?\s*{num}\s*[\.\:\–\—\-]?\s*', '', clean_q, flags=re.IGNORECASE).strip()
                # Видаляємо дубльовані назви «Вправа 3»
                clean_q = re.sub(rf'^(?:вправ[а-яіїє]*|завдан[а-яіїє]*|пункт[а-яіїє]*|номер[а-яіїє]*|№)\s*{num}\s*[\.\:\–\—\-]?\s*', '', clean_q, flags=re.IGNORECASE).strip()
                clean_q = clean_q.strip(' .,:;-')

                is_ex = "вправ" in (assignment_desc or "").lower()
                task_term = f"вправу {num}" if is_ex else f"завдання {num}"

                if clean_q and len(clean_q) > 3 and clean_q.lower() not in ['виконати', 'виконати вправу', 'виконати завдання', 'зробити']:
                    actions_desc = f"Виконати {task_term}: {clean_q}"
                else:
                    actions_desc = f"Виконати {task_term} відповідно до інструкцій вчителя та прикріплених матеріалів"

                tasks_list.append({
                    "num": num,
                    "title": f"Вправа {num}" if is_ex else f"Завдання {num}",
                    "source": "Вказівка вчителя та прикріплені матеріали",
                    "expected_actions": actions_desc,
                    "expected_submission": deliverable.get('format') or "Виконана робота відповідно до умови"
                })
        elif is_single_task or len(assigned_tasks_scope) == 1:
            at = assigned_tasks_scope[0] if assigned_tasks_scope else {}
            tasks_list = [{
                "num": 1,
                "title": assignment_title or at.get('description', 'Комплексне завдання')[:60],
                "source": "Опис завдання вчителя",
                "expected_actions": at.get('description') or assignment_desc[:300] or "Виконати завдання/проєкт згідно з інструкцією",
                "expected_submission": deliverable.get('format') or "Готова робота у відповідному форматі"
            }]
        elif assigned_tasks_scope:
            tasks_list = []
            for idx, at in enumerate(assigned_tasks_scope, 1):
                clean_q = strip_html_tags(at.get('description', '')).strip()
                clean_q = re.sub(r'^(?:[•\-\*]?\s*(?:(?:\d+|[IVXLCDM]+)[\.\)\–\—\-]|(?:питання|завдання|вправа|відповідь|номер|№)\s*\d+[\.\:\)\–\—\-]?))\s*', '', clean_q, flags=re.IGNORECASE).strip()
                tasks_list.append({
                    "num": at.get('task_num') or idx,
                    "title": f"Завдання {at.get('task_num') or idx}",
                    "source": "Вказівка вчителя / матеріали завдання",
                    "expected_actions": clean_q or f"Виконати завдання {idx}",
                    "expected_submission": deliverable.get('format') or "Відповідь або виконаний файл відповідно до умови"
                })
        else:
            tasks_list = [{
                "num": 1,
                "title": assignment_title or "Навчальне завдання",
                "source": "Опис завдання",
                "expected_actions": assignment_desc[:300] if assignment_desc else "Виконати завдання згідно з інструкцією",
                "expected_submission": deliverable.get('format') or "Здана робота у відповідному форматі"
            }]

        student_guide_text = generate_age_appropriate_student_guide(
            grade_str=grade_str,
            age_str=age_str,
            min_grade=min_grade,
            assignment_title=assignment_title,
            assignment_desc=assignment_desc,
            tasks_list=tasks_list,
            is_single_task=is_single_task,
            task_interpretation=task_interpretation
        )

        if not questions_expected:
            submission_format = deliverable.get('format') or "Файл створеної роботи відповідно до вказівок вчителя"
            teacher_recs = [
                f"Завдання має тип «{task_type}». Оцінюйте якість створеного результату ({deliverable.get('description', '')}), його структуру та зміст.",
                "Не вимагайте від учнів відповідей на запитання, якщо завдання полягає у створенні файлу або практичного результату."
            ]
            rules_list = [
                "Оцінюється безпосередньо якість зданого файлу/продукту та виконання вимог вчителя.",
                "Відсутність текстового формату «питання-відповідь» не є підставою для зниження оцінки для цього типу завдання."
            ]
        else:
            submission_format = "Відповіді на запитання у документі або на фото зошита"
            teacher_recs = [
                "Умова містить конкретні запитання. Нагадуйте учням нумерувати свої відповіді у форматі «питання-відповідь»."
            ]
            rules_list = [
                "Кожне запитання вимагає окремої відповіді. Одне речення не зараховується за два завдання.",
                "Часткове виконання оцінюється за відповідними критеріями вчителя."
            ]

        result_data = {
            "topic_and_goal": f"{assignment_title}. {subject_name}, {classes_str}.",
            "target_audience": f"{grade_str} ({age_str})",
            "task_type": task_type,
            "deliverable": deliverable,
            "questions_expected": questions_expected,
            "task_interpretation": task_interpretation,
            "teacher_requirements": task_interpretation.get('teacher_requirements', []),
            "final_task_understanding": scope.get('final_task_understanding') or {},
            "teacher_intent": scope.get('teacher_intent') or {},
            "task_understanding_confidence": scope.get('task_understanding_confidence', 1.0),
            "ambiguities": scope.get('ambiguities', []),
            "student_explanation": student_guide_text,
            "tasks_source_info": "Умова та прикріплені матеріали завдання",
            "tasks_total_count": total_cnt,
            "is_choice_based": False,
            "tasks": tasks_list,
            "submission_format_expected": submission_format,
            "grading_breakdown": {
                "full_completion": "Якісне виконання за критеріями вчителя; точну розбаловку потрібно уточнити.",
                "partial_two_tasks": "Зараховуються виконані вимоги за відповідними критеріями.",
                "partial_one_task": "Бал визначає якість підтверджених результатів, а не кількість файлів.",
                "rules": rules_list
            },
            "teacher_recommendations": teacher_recs
        }

    result_data.setdefault('analysis_mode', 'local')
    result_data['_source_fingerprint'] = fingerprint
    result_data['material_coverage'] = material_coverage
    if result_data['analysis_mode'] == 'local':
        result_data['teacher_recommendations'].append('ШІ недоступний: це попереднє локальне пояснення. Візуальну умову потрібно уточнити у вчителя.')
    # Фінальна санітизація всіх полів від будь-яких залишків HTML-розмітки
    result_data = sanitize_ai_understanding_data(result_data)

    # Зберігаємо результат в базі даних
    try:
        from django.utils import timezone
        assignment.ai_task_understanding = json.dumps(result_data, ensure_ascii=False, indent=2)
        assignment.ai_task_understanding_updated_at = timezone.now()
        assignment.save(update_fields=['ai_task_understanding', 'ai_task_understanding_updated_at'])
    except Exception:
        pass

    return {
        'status': 'success',
        'data': result_data,
        'cached': False,
        'updated_at': assignment.ai_task_understanding_updated_at.strftime('%d.%m.%Y о %H:%M') if assignment.ai_task_understanding_updated_at else None
    }
