"""Cross-process, owner-checked leases and rolling quota reservations in the DB."""
import hashlib
import time
from contextlib import contextmanager, nullcontext
from django.conf import settings
from django.db import transaction, connection


@contextmanager
def state(key):
    from .models import AIResourceState
    from .ai_preparation import singleflight
    # SQLite has no row locks. Local SQLite deployments use a process-safe file
    # lock as well; PostgreSQL coordination works across hosts via row locks.
    lock_key = hashlib.sha256((str(connection.settings_dict['NAME']) + key).encode()).hexdigest()
    lock = singleflight('coord-' + lock_key) if connection.vendor == 'sqlite' else nullcontext()
    with lock:
        with transaction.atomic():
            AIResourceState.objects.get_or_create(key=key)
            row = AIResourceState.objects.select_for_update().get(key=key)
            yield row.data
            row.save(update_fields=['data'])


def resource_key(provider, model='', connection_id=''):
    return 'lease:' + hashlib.sha256(f'{provider}:{connection_id}:{model}'.encode()).hexdigest()


def acquire(provider, model, holder, ttl, connection_id=''):
    with state(resource_key(provider, model, connection_id)) as data:
        if data.get('expires', 0) > time.time():
            return False
        data.update(holder=holder, expires=time.time() + ttl)
        return True


def release(provider, model, holder, connection_id=''):
    with state(resource_key(provider, model, connection_id)) as data:
        if data.get('holder') == holder:
            data.clear()


def busy(provider, model, connection_id=''):
    from .models import AIResourceState
    data = AIResourceState.objects.filter(key=resource_key(provider, model, connection_id)).values_list('data', flat=True).first() or {}
    return data.get('expires', 0) > time.time()


def policy(config):
    from .models import AISettings
    connection = AISettings.get_solo().get_connection(config.get('connection_id')) or {}
    limits = connection.get('quota_limits') or {}
    # Conservative default: different models/keys at one provider share a group.
    group = str(connection.get('quota_group') or config['provider'])
    for other in AISettings.get_solo().provider_connections:
        if str(other.get('quota_group') or other['provider']) != group:
            continue
        other_limits = other.get('quota_limits') or {}
        for name in ('rpm', 'rpd', 'tpm', 'tpd', 'itpm', 'otpm', 'concurrency'):
            candidates = [int(value) for value in (limits.get(name), other_limits.get(name)) if value]
            if candidates:
                limits[name] = min(candidates)
    return 'quota:' + hashlib.sha256(group.encode()).hexdigest(), limits


def reserve(config, holder, input_tokens, output_tokens):
    key, limits = policy(config)
    now = time.time()
    # Conservative rolling 24h avoids assuming that every provider resets at
    # UTC midnight (projects/accounts can have a different reset timezone).
    day_start = now - 86400
    with state(key) as data:
        for name, amount in (('rpm', 1), ('rpd', 1), ('tpm', input_tokens + output_tokens),
                             ('tpd', input_tokens + output_tokens), ('itpm', input_tokens), ('otpm', output_tokens)):
            if int(limits.get(name, 0)) and amount > int(limits[name]):
                return False, -1
        records = [r for r in data.get('requests', []) if r['at'] >= min(day_start, now - 60)]
        leases = {k: v for k, v in data.get('leases', {}).items() if v > now}
        if data.get('paused_until', 0) > now:
            return False, max(1, int(data['paused_until'] - now) + 1)
        if len(leases) >= int(limits.get('concurrency', 1)):
            return False, 2
        minute = [r for r in records if r['at'] > now - 60]
        daily = [r for r in records if r['at'] >= day_start]
        for name, rows, amount, field in (
            ('rpm', minute, 1, None), ('rpd', daily, 1, None),
            ('tpm', minute, input_tokens + output_tokens, 'tokens'),
            ('tpd', daily, input_tokens + output_tokens, 'tokens'),
            ('itpm', minute, input_tokens, 'input'), ('otpm', minute, output_tokens, 'output')):
            limit = int(limits.get(name, 0))
            used = sum(r[field] for r in rows) if field else len(rows)
            if limit and amount > limit:
                return False, -1  # This request cannot fit even an empty window.
            if limit and used + amount > limit:
                reset = min(r['at'] for r in rows) + (86400 if name in ('rpd', 'tpd') else 60)
                return False, max(1, int(reset - now) + 1)
        records.append({'id': holder, 'at': now, 'input': input_tokens, 'output': output_tokens,
                        'tokens': input_tokens + output_tokens})
        leases[holder] = now + settings.AI_REQUEST_DEADLINE + 15
        data.update(requests=records, leases=leases)
        return True, 0


def finish(config, holder, usage=None, pause=0, refund=False):
    key, _ = policy(config)
    with state(key) as data:
        leases = data.get('leases', {})
        leases.pop(holder, None)
        data['leases'] = leases
        if refund:
            data['requests'] = [row for row in data.get('requests', []) if row['id'] != holder]
        for row in data.get('requests', []):
            if row['id'] == holder and usage:
                # Missing counters keep their conservative reservation.
                row['input'] = usage.get('prompt_tokens') if usage.get('prompt_tokens') is not None else row['input']
                row['output'] = usage.get('completion_tokens') if usage.get('completion_tokens') is not None else row['output']
                row['tokens'] = usage.get('total_tokens') if usage.get('total_tokens') is not None else row['input'] + row['output']
        if pause:
            data['paused_until'] = max(data.get('paused_until', 0), time.time() + pause)


def release_job_resources(job_id):
    """A killed subprocess cannot run finally; free only its owned leases.

    Usage reservations remain charged because a timed-out POST may have reached
    the provider. A newer request's slot is never released by this cleanup.
    """
    from .models import AIResourceState
    prefix = str(job_id) + ':'
    for key, initial in AIResourceState.objects.values_list('key', 'data'):
        if not str(initial.get('holder', '')).startswith(prefix) and not any(
                str(owner).startswith(prefix) for owner in initial.get('leases', {})):
            continue
        with state(key) as data:
            if str(data.get('holder', '')).startswith(prefix):
                data.clear()
            elif data.get('leases'):
                data['leases'] = {owner: expiry for owner, expiry in data['leases'].items() if not owner.startswith(prefix)}
