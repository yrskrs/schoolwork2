"""Concurrency control, model cooldowns, and context limits memory for AI models."""
import json
import logging
import time
from typing import Dict, Optional, Tuple
from django.core.cache import caches

logger = logging.getLogger(__name__)

CACHE_ALIAS = 'ai_materials'
COOLDOWN_PREFIX = 'ai_cooldown:'
INFLIGHT_PREFIX = 'ai_inflight:'
LIMIT_KEY = 'ai_recorded_model_limits'


def _get_cache():
    try:
        return caches[CACHE_ALIAS]
    except Exception:
        from django.core.cache import cache
        return cache


def _model_cache_key(provider: str, model: str) -> str:
    clean_p = str(provider or 'gemini').strip().lower()
    clean_m = str(model or '').strip().lower().replace('/', '__').replace('@', 'at_')
    return f"{clean_p}:{clean_m}"


# ─────────────────────────────────────────────────────────────────────────────
# 1. Concurrency check & in-flight model locks
# ─────────────────────────────────────────────────────────────────────────────

def is_model_busy(provider: str, model: str) -> bool:
    """Перевіряє, чи модель наразі використовується іншим паралельним запитом."""
    cache = _get_cache()
    key = INFLIGHT_PREFIX + _model_cache_key(provider, model)
    try:
        val = cache.get(key)
        return bool(val)
    except Exception:
        return False


def acquire_model_slot(provider: str, model: str, holder_id: str = 'active', timeout: int = 60) -> bool:
    """
    Захоплює слот для моделі на час виконання перевірки.
    timeout=60 гарантує автоматичне зняття блокування, якщо процес перервано.
    Повертає True, якщо слот захоплено успішно, False — якщо зайнятий.
    """
    cache = _get_cache()
    key = INFLIGHT_PREFIX + _model_cache_key(provider, model)
    try:
        # cache.add є атомарною операцією (успішна лише якщо ключа ще немає)
        acquired = cache.add(key, holder_id, timeout=timeout)
        return bool(acquired)
    except Exception:
        return True


def release_model_slot(provider: str, model: str) -> None:
    """Звільняє слот моделі після завершення запиту."""
    cache = _get_cache()
    key = INFLIGHT_PREFIX + _model_cache_key(provider, model)
    try:
        cache.delete(key)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 2. Cooldown / Backoff pause on model overload or errors
# ─────────────────────────────────────────────────────────────────────────────

def set_model_cooldown(provider: str, model: str, duration_seconds: int = 45, reason: str = '') -> None:
    """
    Встановлює паузу (cooldown) для моделі після помилки перевантаження (429, 503, 502/504).
    """
    cache = _get_cache()
    key = COOLDOWN_PREFIX + _model_cache_key(provider, model)
    expires_at = time.time() + max(5, duration_seconds)
    data = {
        'provider': provider,
        'model': model,
        'expires_at': expires_at,
        'duration': duration_seconds,
        'reason': str(reason)[:500],
    }
    try:
        cache.set(key, data, timeout=max(10, duration_seconds + 5))
    except Exception as e:
        logger.warning("Failed to set cooldown for %s/%s: %s", provider, model, e)


def get_model_cooldown(provider: str, model: str) -> Tuple[bool, int, str]:
    """
    Повертає (is_in_cooldown, remaining_seconds, reason).
    """
    cache = _get_cache()
    key = COOLDOWN_PREFIX + _model_cache_key(provider, model)
    try:
        data = cache.get(key)
        if not data or not isinstance(data, dict):
            return False, 0, ''
        now = time.time()
        expires_at = data.get('expires_at', 0)
        remaining = int(expires_at - now)
        if remaining > 0:
            return True, remaining, data.get('reason', '')
        # Якщо час вичерпано
        cache.delete(key)
        return False, 0, ''
    except Exception:
        return False, 0, ''


def clear_model_cooldown(provider: str, model: str) -> None:
    """Знімає паузу з моделі."""
    cache = _get_cache()
    key = COOLDOWN_PREFIX + _model_cache_key(provider, model)
    try:
        cache.delete(key)
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 3. Persistent context / volume limit memory
# ─────────────────────────────────────────────────────────────────────────────

def record_model_context_limit(provider: str, model: str, error_message: str, est_tokens: Optional[int] = None) -> None:
    """
    Запам'ятовує та позначає помилку перевищення контексту для моделі (413, context exceeded тощо).
    Зберігається в налаштуваннях та кеші, щоб не повторювати важкі запити.
    """
    prov_key = _model_cache_key(provider, model)
    clean_err = str(error_message or 'Перевищено допустимий обсяг контексту моделі')[:400]
    
    # 1. Зберігаємо у cache
    cache = _get_cache()
    try:
        limits = cache.get(LIMIT_KEY) or {}
        if not isinstance(limits, dict):
            limits = {}
        limits[prov_key] = {
            'provider': provider,
            'model': model,
            'error': clean_err,
            'est_tokens': est_tokens or 0,
            'flagged_at': time.strftime('%d.%m.%Y %H:%M'),
        }
        cache.set(LIMIT_KEY, limits, timeout=86400 * 30)
    except Exception as e:
        logger.warning("Failed to record model limit in cache: %s", e)

    # 2. Зберігаємо у базі даних (AISettings)
    try:
        from .models import AISettings
        settings = AISettings.get_solo()
        raw_list = settings.saved_models_list or '[]'
        models_data = json.loads(raw_list) if raw_list else []
        updated = False
        for m in models_data:
            if isinstance(m, dict) and m.get('name', '').lower() == str(model).lower():
                m['context_limit_flagged'] = True
                m['context_limit_error'] = clean_err
                if est_tokens:
                    m['context_limit_tokens'] = est_tokens
                updated = True
        if updated:
            settings.saved_models_list = json.dumps(models_data, ensure_ascii=False)
            settings.save(update_fields=['saved_models_list', 'updated_at'])
    except Exception as e:
        logger.warning("Failed to record model limit in AISettings: %s", e)


def get_model_context_limit(provider: str, model: str) -> Optional[Dict]:
    """
    Повертає зафіксовані обмеження контексту для моделі (якщо вони були зафіксовані).
    """
    prov_key = _model_cache_key(provider, model)
    cache = _get_cache()
    try:
        limits = cache.get(LIMIT_KEY)
        if isinstance(limits, dict) and prov_key in limits:
            return limits[prov_key]
    except Exception:
        pass

    # Перевірка з AISettings
    try:
        from .models import AISettings
        settings = AISettings.get_solo()
        raw_list = settings.saved_models_list or '[]'
        models_data = json.loads(raw_list) if raw_list else []
        for m in models_data:
            if isinstance(m, dict) and m.get('name', '').lower() == str(model).lower():
                if m.get('context_limit_flagged'):
                    return {
                        'provider': provider,
                        'model': model,
                        'error': m.get('context_limit_error', 'Перевищено обсяг контексту'),
                        'est_tokens': m.get('context_limit_tokens', 0),
                        'flagged_at': '',
                    }
    except Exception:
        pass

    return None


def clear_model_context_limit(provider: str, model: str):
    """Очищає позначку про обмеження контексту для моделі."""
    prov_key = _model_cache_key(provider, model)
    cache = _get_cache()
    try:
        limits = cache.get(LIMIT_KEY)
        if isinstance(limits, dict) and prov_key in limits:
            del limits[prov_key]
            cache.set(LIMIT_KEY, limits, timeout=86400 * 30)
    except Exception:
        pass


def is_context_limit_error(status_code: Optional[int], error_text: str) -> bool:
    """Визначає, чи є помилка збоєм через перевищення ліміту контексту чи розміру запиту."""
    if status_code == 413:
        return True
    text = (error_text or '').lower()
    patterns = [
        '413', 'payload too large', 'request entity too large',
        'context length', 'context window', 'too many tokens',
        'maximum context length', 'token limit exceeded',
        'prompt is too long', 'exceeds maximum context',
        'input is too large', 'maximum prompt length'
    ]
    return any(p in text for p in patterns)


def is_overload_error(status_code: Optional[int], error_text: str) -> bool:
    """Визначає, чи є помилка перевантаженням моделі або вичерпанням квот (429, 503, 502/504)."""
    if status_code in (429, 503, 502, 504):
        return True
    text = (error_text or '').lower()
    patterns = [
        'rate limit', 'too many requests', 'high demand',
        'service unavailable', 'overloaded', 'capacity',
        'resource has been exhausted', 'quota exceeded'
    ]
    return any(p in text for p in patterns)
