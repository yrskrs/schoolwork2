"""Connection identities and a single, provider-independent model queue."""
import json
import re
from uuid import uuid4
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction


def configured(connection):
    return bool(connection.get('api_key') or (
        connection.get('provider') == 'custom' and connection.get('custom_url')))


def save_connection(settings_id, data, delete=False):
    from .models import AISettings, AI_PROVIDER_CHOICES
    with transaction.atomic():
        settings = AISettings.objects.select_for_update().get(pk=settings_id)
        settings.import_legacy_connections()
        connection_id = data.get('connection_id', '').strip()
        existing = settings.get_connection(connection_id) if connection_id else None
        if connection_id and not existing:
            raise ValueError('Підключення більше не існує. Оновіть сторінку.')
        if delete:
            if not existing:
                raise ValueError('Оберіть підключення.')
            settings.provider_connections = [c for c in settings.provider_connections if c['id'] != connection_id]
            settings._save_model_queue([r for r in model_queue(settings) if r['connection_id'] != connection_id])
        else:
            provider = data.get('connection_provider', '').strip()
            if provider not in dict(AI_PROVIDER_CHOICES):
                raise ValueError('Оберіть підтримуваного постачальника.')
            if existing and existing['provider'] != provider:
                raise ValueError('Для іншого постачальника створіть нове підключення.')
            label = data.get('connection_label', '').strip() or dict(AI_PROVIDER_CHOICES)[provider]
            if not existing and not data.get('connection_label', '').strip():
                count = sum(c['provider'] == provider for c in settings.provider_connections)
                if count:
                    base = label
                    number = count + 1
                    labels = {c['label'] for c in settings.provider_connections}
                    while f'{base} · {number}' in labels:
                        number += 1
                    label = f'{base} · {number}'
            key = data.get('connection_key', '').strip() or (existing['api_key'] if existing else '')
            url = data.get('connection_url', '').strip()
            name = data.get('connection_model', '').strip()
            if len(label) > 80 or len(key) > 4096 or len(url) > 255:
                raise ValueError('Завелике значення назви, ключа або адреси API.')
            if provider != 'custom' and not key:
                raise ValueError('Вкажіть API ключ.')
            if provider == 'custom' and not url:
                raise ValueError('Вкажіть Base URL власного API.')
            if url and provider != 'cloudflare':
                try:
                    URLValidator(schemes=['http', 'https'])(url)
                except ValidationError:
                    raise ValueError('Base URL має бути коректною адресою HTTP або HTTPS.')
            elif url and provider == 'cloudflare':
                if url.startswith(('http://', 'https://')):
                    try:
                        URLValidator(schemes=['http', 'https'])(url)
                    except ValidationError:
                        raise ValueError('Адреса API має бути коректною адресою HTTP або HTTPS.')
                elif not re.match(r'^[a-zA-Z0-9_\-]{10,80}$', url):
                    raise ValueError('Вкажіть коректний Cloudflare Account ID або повний Base URL.')
            if name and (len(name) > 100 or any(ch.isspace() for ch in name)):
                raise ValueError('Вкажіть ідентифікатор моделі без пробілів, до 100 символів.')
            connection = {'id': connection_id or uuid4().hex, 'provider': provider,
                          'label': label, 'api_key': key, 'custom_url': url}
            if existing:
                settings.provider_connections = [connection if c['id'] == connection_id else c for c in settings.provider_connections]
            else:
                settings.provider_connections = [*settings.provider_connections, connection]
            if name:
                settings.add_saved_model(name, provider=provider, connection_id=connection['id'])
        settings.save(update_fields=['provider_connections', 'updated_at'])
        return settings


def legacy_configuration(settings):
    """Import the two historical slots once; retain keys and disabled models."""
    from .ai_model_catalog import infer_model_provider
    connections, slots = [], {}
    for slot, prefix in [('primary', ''), ('backup', 'backup_')]:
        provider = getattr(settings, prefix + 'ai_provider') or 'gemini'
        key = getattr(settings, prefix + 'api_key').strip()
        url = getattr(settings, prefix + 'custom_api_url').strip()
        model = getattr(settings, prefix + 'model_name').strip()
        if key or url:
            connection = {'id': uuid4().hex, 'provider': provider, 'label': provider.title(),
                          'api_key': key, 'custom_url': url}
            connections.append(connection)
            slots[slot] = (connection, model or settings._default_provider_model(provider))
    try:
        raw = json.loads(settings.saved_models_list or '[]')
    except (ValueError, TypeError):
        raw = []
    if not isinstance(raw, list):
        raw = []
    rows = []
    for index, entry in enumerate(raw):
        item = entry if isinstance(entry, dict) else {'name': entry}
        name = str(item.get('name', '')).strip()
        if not name:
            continue
        provider = item.get('provider') or infer_model_provider(name, settings.ai_provider)
        try:
            priority = int(item.get('priority', index + 1))
        except (ValueError, TypeError):
            priority = index + 1
        rows.append({'name': name, 'provider': provider, 'priority': priority,
                     'enabled': bool(item.get('enabled', True))})
    rows.sort(key=lambda row: row['priority'])
    active_slot = settings.active_api_type if settings.active_api_type in slots else 'primary'
    if active_slot in slots:
        connection, model = slots[active_slot]
        active = next((row for row in rows if (row['provider'], row['name']) == (connection['provider'], model)), None)
        if active:
            rows.remove(active)
        else:
            active = {'name': model, 'provider': connection['provider'], 'enabled': True}
        rows.insert(0, active)
    ordered_slots = sorted(slots.items(), key=lambda pair: pair[0] != active_slot)
    queue, seen = [], set()
    for row in rows:
        connection = next((c for _, (c, _) in ordered_slots if c['provider'] == row['provider']), None)
        if connection is None:
            connection = next((c for c in connections if c['provider'] == row['provider']), None)
        if connection is None:
            # Preserve an unconfigured provider's saved model without borrowing a key.
            connection = {'id': uuid4().hex, 'provider': row['provider'], 'label': row['provider'].title(),
                          'api_key': '', 'custom_url': ''}
            connections.append(connection)
        identity = (connection['id'], row['name'])
        if identity not in seen:
            queue.append({**row, 'connection_id': connection['id']})
            seen.add(identity)
    for connection, model in slots.values():
        if (connection['id'], model) not in seen:
            queue.append({'name': model, 'provider': connection['provider'], 'connection_id': connection['id'], 'enabled': True})
    counts = {}
    for connection in connections:
        provider = connection['provider']
        counts[provider] = counts.get(provider, 0) + 1
    positions = {}
    for connection in connections:
        provider = connection['provider']
        if counts[provider] > 1:
            positions[provider] = positions.get(provider, 0) + 1
            connection['label'] += f" · {positions[provider]}"
    for index, row in enumerate(queue, 1):
        row['priority'] = index
    return connections, queue


def model_queue(settings):
    try:
        raw = json.loads(settings.saved_models_list or '[]')
    except (ValueError, TypeError):
        raw = []
    connections = {c['id']: c for c in settings.provider_connections}
    rows, seen = [], set()
    for index, item in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(item, dict):
            continue
        connection = connections.get(item.get('connection_id'))
        name = str(item.get('name', '')).strip()
        identity = (item.get('connection_id'), name)
        if not connection or not name or identity in seen:
            continue
        try:
            priority = int(item.get('priority', index + 1))
        except (ValueError, TypeError):
            priority = index + 1
        rows.append({'name': name, 'connection_id': connection['id'], 'provider': connection['provider'],
                     'priority': priority, 'enabled': bool(item.get('enabled', True)),
                     'connection_label': connection['label']})
        seen.add(identity)
    rows.sort(key=lambda item: item['priority'])
    for index, row in enumerate(rows, 1):
        row['priority'] = index
    return rows


def request_configs(settings, custom_model=None):
    from .gemini_service import clean_model_name
    connections = {c['id']: c for c in settings.provider_connections}
    configs = []
    for row in model_queue(settings):
        connection = connections[row['connection_id']]
        if row['enabled'] and configured(connection):
            configs.append({'provider': connection['provider'], 'api_key': connection['api_key'],
                            'custom_url': connection['custom_url'], 'connection_id': connection['id'],
                            'is_backup': False, 'model': clean_model_name(row['name'], connection['provider'])})
    if custom_model:
        # Explicit choices reuse only their own saved connection, never another supplier's key.
        selected = next((item for item in configs if item['model'] == custom_model), None)
        if selected:
            configs.remove(selected)
            configs.insert(0, selected)
    return configs if settings.auto_failover_enabled else configs[:1]


def mutate_model(settings, action, name, provider=None, connection_id=None, priority=None, enabled=None, direction=None):
    rows = model_queue(settings)
    matches = [c for c in settings.provider_connections if (not connection_id or c['id'] == connection_id)
               and (not provider or c['provider'] == provider)]
    if len(matches) != 1:
        raise ValueError('Оберіть підключення для цієї моделі.')
    connection = matches[0]
    identity = (connection['id'], name)
    existing = next((row for row in rows if (row['connection_id'], row['name']) == identity), None)
    if action == 'add':
        position = max(1, min(len(rows) + 1, int(priority))) if priority is not None else (existing['priority'] if existing else len(rows) + 1)
        if existing:
            rows.remove(existing)
        rows.insert(position - 1, {'name': name, 'provider': connection['provider'], 'connection_id': connection['id'],
                                   'enabled': True if enabled is None else bool(enabled)})
    elif existing:
        if action == 'remove':
            rows.remove(existing)
        elif action == 'toggle':
            existing['enabled'] = not existing['enabled'] if enabled is None else bool(enabled)
        elif action == 'activate':
            rows.remove(existing)
            existing['enabled'] = True
            rows.insert(0, existing)
        elif action == 'move' and direction in ('up', 'down'):
            index = rows.index(existing)
            target = index + (-1 if direction == 'up' else 1)
            if 0 <= target < len(rows):
                rows[index], rows[target] = rows[target], rows[index]
    else:
        raise ValueError('Модель більше не міститься в черзі. Оновіть сторінку.')
    settings._save_model_queue(rows)
