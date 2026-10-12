import json
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse

from feed.models import Teacher
from feed.services.updates import (
    get_current_local_version,
    parse_version_tuple,
    parse_changelog_markdown,
    filter_releases_between,
    check_updates_available,
    get_update_status,
    start_update,
    start_rollback,
)


class SystemUpdatesUnitTests(TestCase):
    """Unit tests for version parsing, changelog parsing, and intermediate releases filtering."""

    def test_version_tuple_parsing(self):
        self.assertEqual(parse_version_tuple('4.4.4'), (4, 4, 4))
        self.assertEqual(parse_version_tuple('v4.4.4'), (4, 4, 4))
        self.assertEqual(parse_version_tuple('4.5.0-rc1'), (4, 5, 0, 1))
        self.assertEqual(parse_version_tuple('4.10.2'), (4, 10, 2))
        self.assertTrue(parse_version_tuple('4.4.5') > parse_version_tuple('4.4.4'))
        self.assertTrue(parse_version_tuple('4.10.0') > parse_version_tuple('4.4.9'))
        self.assertFalse(parse_version_tuple('4.4.4') > parse_version_tuple('4.4.4'))
        self.assertEqual(parse_version_tuple('invalid'), (0,))

    def test_parse_changelog_markdown(self):
        sample_md = """
# Changelog

## [4.4.6] - 2026-10-15 - Виправлення та покращення
- Нова функція безпеки
- Оптимізація бази даних

## [4.4.5] - 2026-10-12 - Оновлення інтерфейсу
* Покращено мобільну навігацію
* Додано нові фільтри
        """.strip()

        releases = parse_changelog_markdown(sample_md)
        self.assertEqual(len(releases), 2)
        self.assertEqual(releases[0]['version'], '4.4.6')
        self.assertEqual(releases[0]['date'], '2026-10-15')
        self.assertEqual(releases[0]['title'], 'Виправлення та покращення')
        self.assertEqual(len(releases[0]['changes']), 2)
        self.assertIn('Нова функція безпеки', releases[0]['changes'])

        self.assertEqual(releases[1]['version'], '4.4.5')
        self.assertEqual(releases[1]['date'], '2026-10-12')
        self.assertEqual(releases[1]['title'], 'Оновлення інтерфейсу')
        self.assertEqual(len(releases[1]['changes']), 2)

    def test_filter_releases_between(self):
        sample_releases = [
            {'version': '4.5.0', 'title': 'Майбутня версія', 'changes': ['a']},
            {'version': '4.4.6', 'title': 'Реліз 4.4.6', 'changes': ['b', 'c']},
            {'version': '4.4.5', 'title': 'Реліз 4.4.5', 'changes': ['d']},
            {'version': '4.4.4', 'title': 'Поточна версія', 'changes': ['e']},
            {'version': '4.4.3', 'title': 'Стара версія', 'changes': ['f']},
        ]

        # Оновлення з 4.4.4 до 4.4.6 має повернути рівно 2 версії: 4.4.6 та 4.4.5
        between = filter_releases_between(sample_releases, '4.4.4', '4.4.6')
        self.assertEqual(len(between), 2)
        self.assertEqual(between[0]['version'], '4.4.6')
        self.assertEqual(between[1]['version'], '4.4.5')

        # Якщо версії однакові — порожній список
        self.assertEqual(filter_releases_between(sample_releases, '4.4.4', '4.4.4'), [])

        # Якщо оновлення з 4.4.3 до 4.5.0 — повертає 4.5.0, 4.4.6, 4.4.5, 4.4.4
        wide = filter_releases_between(sample_releases, '4.4.3', '4.5.0')
        self.assertEqual(len(wide), 4)
        versions = [r['version'] for r in wide]
        self.assertEqual(versions, ['4.5.0', '4.4.6', '4.4.5', '4.4.4'])


class SystemUpdatesViewAndAPITests(TestCase):
    """Integration and permissions tests for system updates views and API."""

    def setUp(self):
        self.client = Client()

        # Звичайний вчитель
        self.teacher_user = User.objects.create_user(
            username='regular_teacher',
            password='Password123!',
            is_staff=False,
            is_superuser=False
        )
        Teacher.objects.create(user=self.teacher_user, full_name='Звичайний Вчитель')

        # Супер-адміністратор
        self.admin_user = User.objects.create_user(
            username='super_admin',
            password='Password123!',
            is_staff=True,
            is_superuser=True
        )
        Teacher.objects.create(user=self.admin_user, full_name='Головний Адмін')

    def test_unauthenticated_user_redirected_to_login(self):
        resp = self.client.get(reverse('system_updates'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/teacher/login/', resp.url)

    def test_regular_teacher_forbidden(self):
        self.client.force_login(self.teacher_user)
        resp = self.client.get(reverse('system_updates'))
        self.assertEqual(resp.status_code, 403)

        api_resp = self.client.get(reverse('updates_check_api'))
        self.assertEqual(api_resp.status_code, 403)

        start_resp = self.client.post(reverse('updates_start_api'), {'version': '4.4.5'})
        self.assertEqual(start_resp.status_code, 403)

    def test_admin_can_view_updates_page(self):
        self.client.force_login(self.admin_user)
        resp = self.client.get(reverse('system_updates'))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Оновлення системи')
        self.assertContains(resp, 'Поточна встановлена версія')
        self.assertContains(resp, get_current_local_version())
        self.assertTrue(resp.context['can_update'])

    def test_superadmin_mode_session_allows_access(self):
        # Користувач без is_superuser, але з активною сесією superadmin_mode
        self.client.force_login(self.teacher_user)
        session = self.client.session
        session['superadmin_mode'] = True
        session.save()

        resp = self.client.get(reverse('system_updates'))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context['can_update'])

    def test_check_updates_api_with_releases_between(self):
        self.client.force_login(self.admin_user)
        mock_response_data = {
            'ok': True,
            'current_version': '4.4.4',
            'latest_version': '4.4.6',
            'has_update': True,
            'releases_between': [
                {
                    'version': '4.4.6',
                    'date': '2026-10-15',
                    'title': 'Патч безпеки',
                    'changes': ['Оновлено захист сесій', 'Виправлено помилку експорту']
                },
                {
                    'version': '4.4.5',
                    'date': '2026-10-13',
                    'title': 'Нові звіти',
                    'changes': ['Додано PDF-звіт']
                }
            ],
            'target_release': {'version': '4.4.6', 'title': 'Патч безпеки'}
        }

        with patch('feed.views_updates.check_updates_available', return_value=mock_response_data):
            resp = self.client.get(reverse('updates_check_api'))
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data['ok'])
            self.assertTrue(data['has_update'])
            self.assertEqual(data['latest_version'], '4.4.6')
            self.assertEqual(len(data['releases_between']), 2)
            self.assertEqual(data['releases_between'][0]['version'], '4.4.6')
            self.assertEqual(data['releases_between'][1]['version'], '4.4.5')

    def test_check_updates_service_via_updater_daemon(self):
        with patch('urllib.request.urlopen') as mock_urlopen:
            resp_mock = MagicMock()
            resp_mock.read.return_value = json.dumps({
                'ok': True,
                'current_version': '4.4.4',
                'latest_version': '4.4.5',
                'has_update': True,
                'releases_between': [{'version': '4.4.5', 'title': 'Тест через сервіс', 'changes': ['Зміна 1']}]
            }).encode('utf-8')
            resp_mock.__enter__.return_value = resp_mock
            mock_urlopen.return_value = resp_mock

            result = check_updates_available()
            self.assertTrue(result['ok'])
            self.assertEqual(result['latest_version'], '4.4.5')
            self.assertTrue(result['has_update'])
            self.assertEqual(len(result['releases_between']), 1)

    def test_check_updates_service_fallback_to_github(self):
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, 'full_url') else str(req)
            if 'updater' in url:
                raise Exception("Updater daemon offline")
            if url.endswith('VERSION'):
                resp = MagicMock()
                resp.read.return_value = b'4.4.7'
                resp.__enter__.return_value = resp
                return resp
            if url.endswith('RELEASES.json'):
                resp = MagicMock()
                resp.read.return_value = json.dumps([
                    {
                        'version': '4.4.7',
                        'date': '2026-10-15',
                        'title': 'Нова версія 4.4.7',
                        'changes': ['Зміна А', 'Зміна Б']
                    },
                    {
                        'version': '4.4.6',
                        'date': '2026-10-13',
                        'title': 'Проміжна версія 4.4.6',
                        'changes': ['Зміна В']
                    },
                    {
                        'version': '4.4.5',
                        'date': '2026-10-11',
                        'title': 'Поточна версія',
                        'changes': []
                    }
                ]).encode('utf-8')
                resp.__enter__.return_value = resp
                return resp
            raise ValueError(f"Unexpected url {url}")

        with patch('urllib.request.urlopen', side_effect=fake_urlopen):
            result = check_updates_available()
            self.assertTrue(result['ok'])
            self.assertEqual(result['latest_version'], '4.4.7')
            self.assertTrue(result['has_update'])
            # Повинні бути включені 4.4.7 та 4.4.6 (відносно поточної 4.4.5)
            self.assertEqual(len(result['releases_between']), 2)
            self.assertEqual(result['releases_between'][0]['version'], '4.4.7')
            self.assertEqual(result['releases_between'][1]['version'], '4.4.6')

    def test_check_updates_network_error_graceful_handling(self):
        def fake_urlopen_fail(req, timeout=None):
            raise Exception("No internet connection")

        with patch('urllib.request.urlopen', side_effect=fake_urlopen_fail):
            result = check_updates_available()
            self.assertFalse(result['ok'])
            self.assertFalse(result['has_update'])
            self.assertIn("error", result)

    def test_status_api(self):
        self.client.force_login(self.admin_user)
        with patch('feed.views_updates.get_update_status', return_value={'state': 'idle', 'progress': 0, 'step': 0, 'logs': []}):
            resp = self.client.get(reverse('updates_status_api'))
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data['state'], 'idle')
            self.assertEqual(data['progress'], 0)

    def test_start_update_api(self):
        self.client.force_login(self.admin_user)
        with patch('feed.views_updates.start_update', return_value={'ok': True, 'message': 'Оновлення розпочато'}):
            resp = self.client.post(reverse('updates_start_api'), {'version': '4.4.6', 'ref': 'v4.4.6'})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()['ok'])

    def test_rollback_api(self):
        self.client.force_login(self.admin_user)
        with patch('feed.views_updates.start_rollback', return_value={'ok': True, 'message': 'Відновлення запущено'}):
            resp = self.client.post(reverse('updates_rollback_api'), {'backup_file': '/path/to/backup'})
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()['ok'])

    def test_healthz_endpoint(self):
        resp = self.client.get(reverse('healthz'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'ok')
        self.assertEqual(data['version'], get_current_local_version())
