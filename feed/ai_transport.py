"""Reserve quotas for each actual POST, including adapter compatibility retries."""
import functools
import time
from contextvars import ContextVar
from uuid import uuid4

request_config = ContextVar('ai_transport_config', default=None)
evaluation_deadline = ContextVar('ai_evaluation_deadline', default=None)


def retry_after(data, headers):
    if not isinstance(data, dict):
        return data
    value = headers.get('Retry-After') if headers else None
    if isinstance(value, (str, int, float)):
        try:
            seconds = float(value)
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                return data
        data = dict(data, _schoolnet_retry_after=max(1, min(86400, int(seconds + .999))))
    return data


def configured_call(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        from django.conf import settings
        import inspect
        values = inspect.signature(function).bind_partial(*args, **kwargs).arguments
        config = {'provider': values.get('provider', 'gemini'), 'connection_id': values.get('connection_id') or '',
                  'model': values.get('model_name', ''),
                  'deadline': evaluation_deadline.get() or time.monotonic() + settings.AI_REQUEST_DEADLINE}
        from .ai_quotas import credential_fingerprint
        config['credential_fingerprint'] = credential_fingerprint(values.get('api_key', ''), values.get('custom_url', ''))
        token = request_config.set(config)
        try:
            return function(*args, **kwargs)
        finally:
            request_config.reset(token)
    return wrapped


def capture_quota_headers(headers, status):
    from .ai_quotas import learn_groq_headers
    try:
        learn_groq_headers(request_config.get(), headers, status)
    except Exception as error:
        # A metadata update must not discard an already received assessment.
        import logging
        logging.getLogger(__name__).warning('Quota metadata update failed (%s)', type(error).__name__)


def payload_budget(payload):
    from .ai_payload import estimate_text_tokens
    texts, images = [], 0

    def walk(value):
        nonlocal images
        if isinstance(value, dict):
            if 'inlineData' in value or value.get('type') in ('image_url', 'file'):
                images += 1
                return
            for key, child in value.items():
                if key == 'text' or key == 'content' and isinstance(child, str):
                    texts.append(child)
                else:
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(payload)
    output = payload.get('max_completion_tokens', payload.get('max_tokens', payload.get('generationConfig', {}).get('maxOutputTokens', 3500)))
    return estimate_text_tokens('\n'.join(texts)) + images * 2048 + 350, int(output)


# Hard byte ceilings per provider. OpenRouter rejects bodies above ~7.5 MB with
# HTTP 413 before the model is even selected; Cloudflare and Gemini have their
# own practical limits. These are enforced locally so we never send a request
# that is guaranteed to fail.
PAYLOAD_BYTE_LIMITS = {
    'openrouter': 7_500_000,
    'cloudflare': 20_000_000,
    'gemini': 20_000_000,
    'openai': 20_000_000,
    'groq': 20_000_000,
    'deepseek': 20_000_000,
    'custom': 20_000_000,
}
DEFAULT_PAYLOAD_BYTE_LIMIT = 20_000_000


def payload_bytes(payload):
    """Serialized size of the request body in bytes (UTF-8, compact JSON)."""
    import json
    return len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())


def payload_byte_limit(provider):
    return PAYLOAD_BYTE_LIMITS.get((provider or '').lower().strip(), DEFAULT_PAYLOAD_BYTE_LIMIT)


def guarded_http(function):
    @functools.wraps(function)
    def wrapped(url, payload, *args, **kwargs):
        from .ai_metrics import measure, record
        from .ai_scheduler import reserve, finish
        from .ai_request_metadata import parse_usage
        config = request_config.get()
        from .ai_progress import _job_id
        owner = str(_job_id.get() or 'direct') + ':' + uuid4().hex
        # Hard byte-size pre-flight: never send a body the provider will reject
        # with 413. This runs before quota reservation so no quota is wasted.
        try:
            body_size = payload_bytes(payload)
            provider = (config or {}).get('provider', '')
            limit = payload_byte_limit(provider)
            if body_size > limit:
                message = (f'Обсяг запиту {body_size / 1_000_000:.2f} МБ перевищує безпечний ліміт '
                           f'{limit / 1_000_000:.2f} МБ для {provider or "провайдера"}. '
                           f'Запит не відправлено; зменште кількість або розмір матеріалів.')
                return 413, {'error': {'message': message}, '_schoolnet_local_preflight': True,
                             '_schoolnet_payload_too_large': True}, message
        except Exception:
            pass
        if config:
            if time.monotonic() >= config['deadline']:
                return 0, {'_schoolnet_local_preflight': True}, 'Вичерпано загальний бюджет часу перевірки.'
            incoming, outgoing = payload_budget(payload)
            allowed, wait = reserve(config, owner, incoming, outgoing)
            if not allowed:
                message = ('Запит перевищує налаштований бюджет токенів.' if wait < 0 else
                           'Підключення зайняте або очікує поновлення квоти.')
                return (422 if wait < 0 else 429), {'error': {'message': message}, '_schoolnet_local_preflight': True,
                             '_schoolnet_deferred': wait if wait > 0 else None}, message
            if time.monotonic() >= config['deadline']:
                finish(config, owner, refund=True)
                return 0, {'_schoolnet_local_preflight': True}, 'Вичерпано загальний бюджет часу перевірки.'
            kwargs['timeout'] = min(float(kwargs.get('timeout', 35)), max(.1, config['deadline'] - time.monotonic()))
        response = None
        try:
            import json
            record('payload_bytes', len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()))
            with measure('http'):
                response = function(url, payload, *args, **kwargs)
            return response
        finally:
            if config:
                code, data, _ = response or (0, {}, '')
                data = data if isinstance(data, dict) else {}
                pause = max(1, min(86400, int(data.get('_schoolnet_retry_after') or 45))) if code == 429 else 0
                local = bool(data.get('_schoolnet_local_preflight'))
                finish(config, owner, parse_usage(data), pause, refund=local)
                record('http_actual', int(not local))
                for name, value in parse_usage(data).items():
                    if value is not None:
                        record(name, value)
                try:
                    record('http_status', code)
                except Exception:
                    pass
    return wrapped
