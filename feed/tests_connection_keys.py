"""Source keys stay usable without keeping or redisplaying their plaintext."""
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.contrib.sessions.models import Session
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import JournalAPIKey
from .secret_fields import PREFIX, token_hash
from . import tests_school_api_v2 as fixtures


class ConnectionKeyTests(TestCase):
    setUp = fixtures.SchoolAPIV2Tests.setUp

    @override_settings(PUBLIC_BASE_URL='http://obsolete.invalid')
    def test_current_teacher_address_is_used_in_connection_help_and_export_code(self):
        response = self.client.post(reverse('teacher_settings'), {'action': 'create_simple_journal_api_key'},
            HTTP_HOST='schoolserver.local:9080')
        self.assertEqual(response.context['journal_site_url'], 'http://schoolserver.local:9080')
        self.assertContains(response, 'http://schoolserver.local:9080')
        self.assertNotContains(response, 'http://obsolete.invalid')

    def test_database_has_only_digest_and_digest_cannot_authenticate(self):
        with connection.cursor() as cursor:
            cursor.execute('SELECT key FROM feed_journalapikey WHERE id = %s', [self.key.pk])
            stored = cursor.fetchone()[0]
        self.assertEqual(stored, token_hash('synthetic-key'))
        self.assertEqual(self.client.get('/api/v2/journal/roster/',
            HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 200)
        self.assertEqual(self.client.get('/api/v2/journal/roster/',
            HTTP_AUTHORIZATION='Bearer ' + stored).status_code, 401)
        self.assertNotIn('synthetic-key', str(self.key))

    def test_one_time_creation_does_not_store_plaintext_in_session(self):
        response = self.client.post(reverse('teacher_settings'), {'action': 'create_simple_journal_api_key'})
        self.assertEqual(response.status_code, 200)
        token = response.context['just_created_api_key']
        self.assertTrue(token)
        self.assertEqual(JournalAPIKey.objects.get(key=token).key, token_hash(token))
        self.assertNotIn('just_created_api_key', self.client.session)
        page = self.client.get(reverse('teacher_settings'), {'tab': 'api'})
        self.assertNotContains(page, token)
        self.assertNotContains(page, 'key-full-')
        self.assertNotContains(page, token_hash(token))

    def test_migration_hashes_existing_key_and_removes_only_pending_session_secret(self):
        raw = 'synthetic-pre-migration-key'
        with connection.cursor() as cursor:
            cursor.execute('UPDATE feed_journalapikey SET key = %s WHERE id = %s', [raw, self.key.pk])
        session = self.client.session
        session['just_created_api_key'] = raw
        session['just_created_api_key_name'] = 'Стара одноразова відповідь'
        session['synthetic_other_setting'] = 'preserve'
        session.save()
        module = import_module('feed.migrations.0069_hash_journal_api_keys')
        module.hash_existing(apps, None)
        self.key.refresh_from_db()
        self.assertEqual(self.key.key, token_hash(raw))
        self.assertTrue(self.key.key.startswith(PREFIX))
        data = Session.objects.get(session_key=session.session_key).get_decoded()
        self.assertNotIn('just_created_api_key', data)
        self.assertEqual(data['synthetic_other_setting'], 'preserve')
        self.assertIn('_auth_user_id', data)
        self.assertEqual(self.client.get('/api/v2/journal/roster/', HTTP_AUTHORIZATION='Bearer ' + raw).status_code, 200)
        module.hash_existing(apps, None)
        self.assertEqual(JournalAPIKey.objects.get(pk=self.key.pk).key, token_hash(raw))

    def test_legacy_query_key_is_accepted_without_log_or_online_metadata_leak(self):
        from .middleware import _active_clients
        with patch('feed.middleware._safe_log') as log:
            response = self.client.get('/api/v1/journal/grades/', {'api_key': 'synthetic-key'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('synthetic-key', str(log.call_args_list))
        self.assertNotIn('synthetic-key', str(_active_clients))
