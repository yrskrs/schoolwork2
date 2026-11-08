"""Account quota metadata, never credentials or student work in public output."""
import copy
import hashlib
import json
import urllib.error
import urllib.request

LIMIT_NAMES = ('rpm', 'rpd', 'tpm', 'tpd', 'itpm', 'otpm', 'concurrency')


def credential_fingerprint(key, endpoint=''):
    return hashlib.sha256((str(key) + '\0' + str(endpoint or '')).encode()).hexdigest()


def validate_limits(value, *, model=False):
    if not isinstance(value, dict) or set(value) - set(LIMIT_NAMES):
        raise ValueError('Невідоме поле квоти.')
    result = {}
    for name, number in value.items():
        if isinstance(number, bool) or not isinstance(number, int) or number < 1 or number > (8 if name == 'concurrency' else 10**9):
            raise ValueError('Квоти профілю мають бути додатними цілими числами.')
        if model and name == 'concurrency':
            raise ValueError('Паралельність задається для спільної групи, не для моделі.')
        result[name] = number
    return result


def stricter(existing, incoming):
    result = dict(existing or {})
    for name, value in incoming.items():
        result[name] = min(result.get(name) or value, value)
    return result


def apply_profile(connections, profile):
    """Pure transformation; callers lock settings before applying the result."""
    if not isinstance(profile, dict) or not isinstance(profile.get('providers'), dict):
        raise ValueError('Профіль повинен містити providers.')
    updated = copy.deepcopy(connections)
    known = {c['provider'] for c in updated}
    if set(profile['providers']) - known:
        raise ValueError('У профілі є провайдер без налаштованого підключення.')
    for connection in updated:
        item = profile['providers'].get(connection['provider'])
        if item is None:
            continue
        if not isinstance(item, dict) or not isinstance(item.get('quota_provenance', {}), dict):
            raise ValueError('Профіль підключення та походження квот мають бути об’єктами.')
        if set(item) - {'quota_group', 'quota_limits', 'model_quota_limits', 'quota_fallback_limits', 'quota_fallback_note', 'quota_provenance'}:
            raise ValueError('Невідоме поле профілю підключення.')
        connection['quota_group'] = str(item.get('quota_group') or connection.get('quota_group') or connection['provider'])
        if len(connection['quota_group']) > 80:
            raise ValueError('Завелика назва групи квоти.')
        connection['quota_limits'] = stricter(connection.get('quota_limits'), validate_limits(item.get('quota_limits', {})))
        models = item.get('model_quota_limits', {})
        if not isinstance(models, dict) or any(not isinstance(name, str) or not name or len(name) > 200 or any(c.isspace() for c in name) for name in models):
            raise ValueError('Некоректний профіль моделі.')
        previous = copy.deepcopy(connection.get('model_quota_limits') or {})
        for name, limits in models.items():
            previous[name] = stricter(previous.get(name), validate_limits(limits, model=True))
        connection['model_quota_limits'] = previous
        connection['quota_fallback_limits'] = validate_limits(item.get('quota_fallback_limits', {}), model=True)
        connection['quota_fallback_note'] = str(item.get('quota_fallback_note') or '')[:1000]
        connection['quota_provenance'] = {
            **(connection.get('quota_provenance') or {}), **copy.deepcopy(item.get('quota_provenance') or {})}
    return updated


def groq_limits(headers):
    """Groq's request header means RPD; the token header means TPM, never RPM."""
    lower = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    limits = {}
    for header, field in [('x-ratelimit-limit-requests', 'rpd'), ('x-ratelimit-limit-tokens', 'tpm')]:
        value = lower.get(header, '')
        if value.isdecimal() and 0 < int(value) <= 10**9:
            limits[field] = int(value)
    return limits


def learn_groq_headers(config, headers, status):
    if not config or config.get('provider') != 'groq' or status not in (200, 429):
        return False
    limits = groq_limits(headers)
    if not limits or not config.get('connection_id') or not config.get('model'):
        return False
    from django.db import transaction
    from django.utils import timezone
    from .models import AISettings
    with transaction.atomic():
        settings = AISettings.objects.select_for_update().get(pk=1)
        connection = settings.get_connection(config['connection_id'])
        if not connection or connection['provider'] != 'groq':
            return False
        expected = credential_fingerprint(connection.get('api_key', ''), connection.get('custom_url', ''))
        if config.get('credential_fingerprint') != expected:
            return False  # Do not attach an old in-flight response to a rotated key.
        models = connection.setdefault('model_quota_limits', {})
        current = dict(models.get(config['model']) or {})
        # Existing values may be a stricter teacher budget. Never raise them
        # merely because the provider permits more.
        for name, value in limits.items():
            current[name] = min(current.get(name) or value, value)
        if models.get(config['model']) == current:
            return False
        models[config['model']] = current
        provenance = connection.setdefault('quota_provenance', {})
        provenance.setdefault('model_sources', {})[config['model']] = {
            'source': 'groq_response_headers', 'verified_at': timezone.now().isoformat(),
            'reported_limits': limits,
        }
        settings.save(update_fields=['provider_connections', 'updated_at'])
        return True


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_provider_limits(connection, models, *, probe_groq=False, opener=None):
    """Only documented, fixed official endpoints. No school data or raw errors."""
    provider = connection['provider']
    jobs = []
    if provider == 'openrouter':
        jobs.append(('https://openrouter.ai/api/v1/key', None, None))
    elif provider == 'groq' and probe_groq:
        for model in dict.fromkeys(models):
            payload = {'model': model, 'messages': [{'role': 'user', 'content': 'Return OK.'}],
                       'max_completion_tokens': 32, 'temperature': 0}
            if model == 'qwen/qwen3.8-27b':
                payload['reasoning_effort'] = 'none'
            elif model.startswith('openai/gpt-oss'):
                payload['reasoning_effort'] = 'low'
            jobs.append(('https://api.groq.com/openai/v1/chat/completions', payload, model))
    else:
        return []
    opener = opener or urllib.request.build_opener(NoRedirect)
    results = []
    for url, payload, model in jobs:
        request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload else None,
            headers={'Authorization': 'Bearer ' + connection['api_key'], 'Content-Type': 'application/json',
                     'User-Agent': 'SchoolNet-quota-sync/1.0'})
        item = {'provider': provider, 'model': model, 'limits': {}, 'source': 'provider_api'}
        try:
            try:
                response = opener.open(request, timeout=20)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                item['http_status'] = response.status
                body = response.read(300000)
                if provider == 'groq' and response.status in (200, 429):
                    item['limits'] = groq_limits(response.headers)
                elif provider == 'openrouter' and response.status == 200:
                    data = json.loads(body).get('data') or {}
                    daily = data.get('free_model_daily_requests') or {}
                    number = daily.get('limit')
                    if isinstance(number, int) and not isinstance(number, bool) and 0 < number <= 10**9:
                        item['limits']['rpd'] = number
                    # Credit balances and the deprecated rate_limit object are
                    # not request/token quotas and must never become RPM/RPD.
        except (OSError, ValueError):
            item['error'] = 'Метадані ліміту недоступні; ключ і сиру відповідь не показано.'
        results.append(item)
    return results
