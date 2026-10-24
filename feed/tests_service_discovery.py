from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from . import tests_school_api_v2 as fixtures


class ServiceDiscoveryTests(TestCase):
    setUp = fixtures.SchoolAPIV2Tests.setUp

    def test_ping_is_anonymous_stable_and_has_no_people_or_credentials(self):
        self.client.logout()
        with CaptureQueriesContext(connection) as queries:
            first = self.client.get('/api/v2/ping')
            second = self.client.get('/api/v2/ping/')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(set(first.json()), {'source', 'instance_id', 'version'})
        self.assertEqual(first.json()['source'], 'schoolwork')
        self.assertLess(len(first.content), 256)
        self.assertNotContains(first, 'synthetic-key')
        self.assertNotContains(first, 'Вигаданий')
        self.assertIn('no-store', first['Cache-Control'])
        self.assertFalse(any('feed_rosterreplica' in q['sql'].lower() for q in queries))
        self.assertEqual(self.client.post('/api/v2/ping').status_code, 405)

    def test_connection_info_reports_real_permissions_without_roster(self):
        self.key.can_import_roster = False; self.key.save()
        response = self.client.get('/api/v2/journal/connection/', HTTP_AUTHORIZATION='Bearer synthetic-key')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['permissions'], {'export': True, 'roster_import': False})
        self.assertNotIn('students', response.json())
        self.assertNotContains(response, 'synthetic-key')
        self.assertEqual(self.client.post('/api/v2/journal/roster/', '{}', content_type='application/json',
            HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 403)

    def test_invalid_key_is_401_but_missing_export_right_is_403(self):
        self.assertEqual(self.client.get('/api/v2/journal/connection/').status_code, 401)
        self.key.can_export_grades = False; self.key.save()
        self.assertEqual(self.client.get('/api/v2/journal/connection/', HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 200)
        self.assertEqual(self.client.get('/api/v2/journal/grades/', HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 403)
        self.assertEqual(self.client.get('/api/v2/journal/roster/', HTTP_AUTHORIZATION='Bearer synthetic-key').status_code, 403)
