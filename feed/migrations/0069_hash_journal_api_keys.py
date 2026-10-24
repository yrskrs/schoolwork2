"""Keep existing Bearer tokens valid while removing recoverable source secrets.

Downgrading this one-way security migration requires the private pre-update
backup; the updater's existing database rollback provides that backup.
"""
from importlib import import_module

from django.conf import settings
from django.db import migrations

from feed.secret_fields import HashedTokenField


def hash_existing(apps, schema_editor):
    Key = apps.get_model('feed', 'JournalAPIKey')
    for key in Key.objects.only('pk', 'key').iterator():
        Key.objects.filter(pk=key.pk).update(key=key.key)
    # Previous UI versions briefly stored the one-time display in the session.
    Session = apps.get_model('sessions', 'Session')
    Store = import_module(settings.SESSION_ENGINE).SessionStore
    for row in Session.objects.all().iterator():
        store = Store(session_key=row.session_key)
        data = store.decode(row.session_data)
        if 'just_created_api_key' in data or 'just_created_api_key_name' in data:
            data.pop('just_created_api_key', None)
            data.pop('just_created_api_key_name', None)
            row.session_data = store.encode(data)
            row.save(update_fields=['session_data'])


class Migration(migrations.Migration):
    dependencies = [('feed', '0068_sync_notification_template_labels'), ('sessions', '0001_initial')]
    operations = [
        migrations.AlterField(model_name='journalapikey', name='key',
            field=HashedTokenField('API ключ / Токен', max_length=71, unique=True, db_index=True)),
        migrations.RunPython(hash_existing),
    ]
