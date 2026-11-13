"""Endpoint ШІ-помічника опису завдання: авторизація, валідація, ліміти та безпека."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from feed.gemini_service import get_ai_settings
from feed.models import Teacher


class AssignmentDescriptionRefineEndpointTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            'teacher_desc', password='pass', is_superuser=True, is_staff=True)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Учитель')
        self.url = reverse('teacher_refine_assignment_description')
        self.client = Client()
        self.client.force_login(self.user)
        settings = get_ai_settings()
        settings.is_enabled = True
        settings.api_key = 'AIzaValidKey'
        settings.save()

    def tearDown(self):
        cache.clear()

    def post_json(self, payload):
        return self.client.post(self.url, data=json.dumps(payload), content_type='application/json')

    # ── Авторизація та метод ────────────────────────────────────────────────
    def test_requires_teacher_session(self):
        response = Client().post(
            self.url, data=json.dumps({'teacher_notes': 'x'}), content_type='application/json')
        self.assertIn(response.status_code, (302, 403))

    def test_student_cannot_use_helper(self):
        pupil = User.objects.create_user('pupil_desc', password='pass')
        client = Client(); client.force_login(pupil)
        response = client.post(
            self.url, data=json.dumps({'teacher_notes': 'x'}), content_type='application/json')
        self.assertIn(response.status_code, (302, 403))

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(
            self.url, data=json.dumps({'teacher_notes': 'x'}), content_type='application/json')
        self.assertEqual(response.status_code, 403)

    # ── Валідація вводу ─────────────────────────────────────────────────────
    def test_empty_input_returns_400(self):
        response = self.post_json({})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')

    def test_invalid_json_returns_400(self):
        response = self.client.post(self.url, data=b'{not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_non_object_json_returns_400(self):
        self.assertEqual(self.post_json(['a']).status_code, 400)

    def test_non_string_fields_return_400_not_500(self):
        for payload in ({'teacher_notes': 123}, {'teacher_notes': None},
                        {'current_description': {'a': 1}}, {'assignment_title': 5}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post_json(payload).status_code, 400)

    def test_too_long_fields_return_400(self):
        self.assertEqual(self.post_json({'teacher_notes': 'x' * 9000}).status_code, 400)
        self.assertEqual(self.post_json({'assignment_title': 'x' * 400}).status_code, 400)

    def test_form_encoded_post_is_supported(self):
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '**Опис**', None, {})
            response = self.client.post(self.url, {'teacher_notes': 'Зробити таблицю'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'success')

    # ── Виклик ШІ та відповіді ──────────────────────────────────────────────
    @patch('feed.gemini_service.call_ai_api')
    def test_success_contract_and_action_metric(self, mock_call):
        mock_call.return_value = (200, '**Опис** завдання', None, {})
        response = self.post_json({'teacher_notes': 'Опис завдання'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'success')
        self.assertIn('<strong>Опис</strong>', data['html'])
        self.assertEqual(data['markdown'], '**Опис** завдання')
        self.assertTrue(data['model_used'])
        self.assertEqual(mock_call.call_args.kwargs['action'], 'description_refinement')

    @patch('feed.gemini_service.call_ai_api')
    def test_failure_message_does_not_leak_provider_details(self, mock_call):
        mock_call.return_value = (500, None, 'boom https://internal-ai.example secret=abc', {})
        response = self.post_json({'teacher_notes': 'Опис завдання'})
        self.assertEqual(response.status_code, 400)
        body = response.content.decode()
        self.assertNotIn('internal-ai.example', body)
        self.assertNotIn('secret=abc', body)
        self.assertNotIn('boom', body)
        self.assertEqual(response.json()['status'], 'error')

    @patch('feed.gemini_service.call_ai_api')
    def test_ai_disabled_returns_400_with_human_message(self, mock_call):
        settings = get_ai_settings()
        settings.is_enabled = False
        settings.save()
        response = self.post_json({'teacher_notes': 'Опис завдання'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('вимкнено', response.json()['message'])
        mock_call.assert_not_called()

    @patch('feed.gemini_service.call_ai_api')
    def test_rate_limit_returns_429(self, mock_call):
        mock_call.return_value = (200, '**Опис**', None, {})
        for _ in range(20):
            self.assertEqual(self.post_json({'teacher_notes': 'Опис'}).status_code, 200)
        response = self.post_json({'teacher_notes': 'Опис'})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Забагато запитів', response.json()['message'])
