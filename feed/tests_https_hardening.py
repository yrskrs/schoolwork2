from django.contrib.auth.models import User
from django.test import TestCase
from django.test import Client

from .access_models import TailscaleConnection
from .models import Teacher


class HTTPSHardeningTests(TestCase):
    def setUp(self):
        user = User.objects.create_superuser('https_test_admin', password='test-password')
        Teacher.objects.create(user=user, full_name='HTTPS Test')
        TailscaleConnection.objects.update_or_create(pk=1, defaults={
            'enabled': True,
            'public_url': 'https://localwork.tail9ec47d.ts.net',
            'allow_without_link': True,
        })

    def test_funnel_login_gets_hsts_and_secure_csrf(self):
        client = Client()
        response = client.get('/teacher/login/', HTTP_HOST='localwork.tail9ec47d.ts.net',
                              HTTP_X_FORWARDED_PROTO='https', REMOTE_ADDR='172.20.0.1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Strict-Transport-Security'], 'max-age=86400')
        cookie = response.cookies.get('schoolwork2_csrftoken')
        self.assertIsNotNone(cookie)
        self.assertTrue(cookie['secure'])

    def test_funnel_login_session_cookie_is_secure(self):
        client = Client()
        response = client.post('/teacher/login/', {
            'username': 'https_test_admin', 'password': 'test-password',
        }, HTTP_HOST='localwork.tail9ec47d.ts.net', HTTP_X_FORWARDED_PROTO='https',
            REMOTE_ADDR='172.20.0.1')
        self.assertEqual(response.status_code, 302)
        session = response.cookies.get('schoolwork2_sessionid')
        self.assertIsNotNone(session)
        self.assertTrue(session['secure'])

    def test_lan_http_keeps_working_without_secure_or_hsts(self):
        client = Client()
        response = client.get('/teacher/login/', HTTP_HOST='192.168.1.13:8080',
                              REMOTE_ADDR='192.168.1.12')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Strict-Transport-Security', response)
        cookie = response.cookies.get('schoolwork2_csrftoken')
        self.assertIsNotNone(cookie)
        self.assertFalse(cookie['secure'])

    def test_forwarded_headers_from_untrusted_source_do_not_trigger_hardening(self):
        client = Client()
        response = client.get('/teacher/login/', HTTP_HOST='localwork.tail9ec47d.ts.net',
                              HTTP_X_FORWARDED_PROTO='https', REMOTE_ADDR='8.8.8.8')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Strict-Transport-Security', response)
        self.assertFalse(response.cookies['schoolwork2_csrftoken']['secure'])
