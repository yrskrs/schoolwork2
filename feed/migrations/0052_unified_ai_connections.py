import json
from uuid import uuid4
from django.db import migrations

DEFAULT_MODELS = {'gemini': 'gemini-3.6-flash', 'openai': 'gpt-4o-mini', 'deepseek': 'deepseek-flash',
                  'groq': 'openai/gpt-oss-120b', 'openrouter': 'google/gemini-2.5-flash', 'custom': 'llama3.2'}


def infer_model_provider(name, fallback):
    name = str(name).lower()
    if name.startswith(('gemini-', 'models/gemini-')): return 'gemini'
    if name.startswith('deepseek-'): return 'deepseek'
    if name.startswith(('gpt-', 'o1', 'o3', 'o4')): return 'openai'
    if name.startswith(('llama-', 'mixtral-')): return 'groq'
    if '/' in name: return 'openrouter'
    return fallback


def legacy_configuration(settings):
    """Import the two historical slots once; retain keys and disabled models."""
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
            slots[slot] = (connection, model or DEFAULT_MODELS.get(provider, ''))
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


def migrate_connections(apps, schema_editor):
    settings_model = apps.get_model('feed', 'AISettings')
    for settings in settings_model.objects.using(schema_editor.connection.alias).all().iterator():
        connections, queue = legacy_configuration(settings)
        settings_model.objects.using(schema_editor.connection.alias).filter(pk=settings.pk).update(
            provider_connections=connections, saved_models_list=json.dumps(queue, ensure_ascii=False),
            unified_model_queue=True, api_key='', backup_api_key='', custom_api_url='', backup_custom_api_url='')


class Migration(migrations.Migration):
    dependencies = [('feed', '0051_airequestlog_usage_reported_and_more')]
    # A rollback to two slots cannot retain arbitrary connections: restore the pre-release backup instead.
    operations = [migrations.RunPython(migrate_connections)]
