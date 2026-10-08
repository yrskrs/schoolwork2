"""Unit tests for isolated model statistics per connection and AI error log analysis & reporting."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .ai_connections import save_connection
from .ai_error_analyzer import generate_error_report_with_ai, classify_errors_heuristically
from .middleware import set_has_admin
from .models import (
    AICriteriaPreset,
    AIErrorLog,
    AIErrorReport,
    AISettings,
    Assignment,
    ClassGroup,
    School,
    Submission,
    Teacher,
)


class AIErrorReportsAndModelStatsTests(TestCase):
    def setUp(self):
        from django.core.cache import caches
        caches['default'].clear()
        caches['ai_materials'].clear()
        set_has_admin(True)

        self.user = User.objects.create_superuser('reports_teacher', password='test-password')
        School.objects.create(name='Школа звітності', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Тест')
        self.group = ClassGroup.objects.create(name='9-Б', grade=9)
        self.teacher.classes.add(self.group)
        self.preset = AICriteriaPreset.objects.create(name='Шаблон оцінки', evaluation_type='traditional')
        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            title='Практична робота',
            description='Опис роботи.',
            status='published',
            default_ai_preset=self.preset
        )
        self.assignment.classes.add(self.group)

        self.settings = AISettings.objects.create(
            id=1,
            unified_model_queue=True,
            saved_models_list='[]',
            is_enabled=True
        )
        self.client.force_login(self.user)

    def add_connection(self, provider, model='shared-model', key=None, label=None, **extra):
        payload = {
            'connection_provider': provider,
            'connection_key': key or f'{provider}-synthetic-key',
            'connection_model': model,
            **extra
        }
        if label:
            payload['connection_label'] = label
        self.settings = save_connection(self.settings.pk, payload)
        return self.settings.provider_connections[-1]['id']

    def test_isolated_model_stats_for_multiple_accounts_with_same_model(self):
        """When user adds two Gemini accounts with the same model name,

        request counts must be isolated per connection, not duplicated globally."""
        conn_1 = self.add_connection('gemini', model='gemini-2.5-flash', label='Gemini Акаунт 1')
        conn_2 = self.add_connection('gemini', model='gemini-2.5-flash', label='Gemini Акаунт 2')

        # Create successful check for account 1
        Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Ольга',
            last_name='Коваль',
            file=SimpleUploadedFile('test1.txt', b'content 1'),
            ai_model_used='gemini-2.5-flash',
            ai_connection_id=conn_1,
            ai_status='success'
        )

        # Create failover error log for account 1
        AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            connection_id=conn_1,
            error_type='rate_limit',
            status_code=429,
            error_message='Rate limit exceeded on account 1'
        )

        # Create failed check for account 2
        Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Іван',
            last_name='Бойко',
            file=SimpleUploadedFile('test2.txt', b'content 2'),
            ai_model_used='gemini-2.5-flash',
            ai_connection_id=conn_2,
            ai_status='failed'
        )

        # AIErrorLog for account 2
        AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            connection_id=conn_2,
            error_type='auth_error',
            status_code=401,
            error_message='API key invalid on account 2'
        )

        # Request teacher settings AI tab
        response = self.client.get(reverse('teacher_settings') + '?tab=ai&ai_section=priority')
        self.assertEqual(response.status_code, 200)

        model_stats = response.context.get('model_stats', [])
        stats_by_conn = {ms['connection_id']: ms for ms in model_stats}

        self.assertIn(conn_1, stats_by_conn)
        self.assertIn(conn_2, stats_by_conn)

        stats_1 = stats_by_conn[conn_1]
        stats_2 = stats_by_conn[conn_2]

        # Account 1: 1 success submission, 1 error log
        self.assertEqual(stats_1['success_checks'], 1)
        self.assertEqual(stats_1['failed_checks'], 1)

        # Account 2: 0 success submissions, 1 failed submission + error log
        self.assertEqual(stats_2['success_checks'], 0)
        self.assertEqual(stats_2['failed_checks'], 1)

    def test_generate_ai_error_report_marks_errors_read_and_creates_report(self):
        """Generating AI report marks all error logs as read and saves an AIErrorReport."""
        conn_id = self.add_connection('gemini', model='gemini-2.5-flash')

        # Create 3 error logs
        e1 = AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            connection_id=conn_id,
            error_type='rate_limit',
            status_code=429,
            error_message='Quota exhausted / ResourceExhausted 429',
            is_read=False
        )
        e2 = AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            connection_id=conn_id,
            error_type='server_overload',
            status_code=503,
            error_message='Model overloaded on Google servers 503',
            is_read=False
        )
        e3 = AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            connection_id=conn_id,
            error_type='timeout',
            status_code=0,
            error_message='Connection timed out after 35s',
            is_read=False
        )

        url = reverse('teacher_settings') + '?tab=ai&ai_section=errors&errors_view=analysis'
        
        # Verify initial unread count
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.context.get('unread_ai_errors_count'), 3)

        # Mock AI API to simulate AI response
        ai_mock_response = (
            "### 1. Вердикт та статус системи\n"
            "Збої мають виключно **зовнішній характер (сервер провайдера / ліміти квот)**. Втручання в код платформи **НЕ потрібне**.\n\n"
            "### 2. Аналіз помилок\n"
            "- HTTP 429: тимчасове вичерпання ліміту запитів на хвилину.\n"
            "- HTTP 503: перевантаження серверів Google Gemini.\n\n"
            "### 3. Рекомендації\n"
            "Система автоматично виконує перехід (failover) на наступну модель у черзі."
        )

        with patch('feed.gemini_service.call_ai_api', return_value=(200, ai_mock_response, '', {})):
            post_resp = self.client.post(url, {'action': 'generate_ai_error_report'})
            self.assertEqual(post_resp.status_code, 302)

        # Verify all error logs are now marked is_read=True
        e1.refresh_from_db()
        e2.refresh_from_db()
        e3.refresh_from_db()
        self.assertTrue(e1.is_read)
        self.assertTrue(e2.is_read)
        self.assertTrue(e3.is_read)
        self.assertIsNotNone(e1.read_at)

        # Verify AIErrorReport created
        reports = AIErrorReport.objects.all()
        self.assertEqual(reports.count(), 1)
        report = reports.first()
        self.assertEqual(report.unread_count_before, 3)
        self.assertEqual(report.verdict, 'external_issue')
        self.assertIn('зовнішній', report.verdict_title.lower())
        self.assertIn('не потрібно', report.verdict_title.lower())
        self.assertIn('### 1. Вердикт', report.report_text)

        # Follow redirect and inspect rendered analysis page
        follow_resp = self.client.get(post_resp.url)
        self.assertEqual(follow_resp.status_code, 200)
        self.assertEqual(follow_resp.context.get('unread_ai_errors_count'), 0)
        self.assertEqual(follow_resp.context.get('latest_ai_error_report').pk, report.pk)
        self.assertContains(follow_resp, 'Вердикт та статус системи')

    def test_heuristic_classification_needs_fix(self):
        """When errors contain HTTP 500 or JSON parser crashes, verdict must be needs_fix."""
        AIErrorLog.objects.create(
            provider='gemini',
            model_name='gemini-2.5-flash',
            error_type='json_parse_error',
            status_code=500,
            error_message='JSONDecodeError: Expecting value: line 1 column 1 (char 0)'
        )
        logs = AIErrorLog.objects.all()
        verdict, title = classify_errors_heuristically(logs)
        self.assertEqual(verdict, 'needs_fix')
        self.assertIn('перевірку коду', title.lower())

    def test_mark_all_errors_read_action(self):
        """Action mark_all_errors_read marks all unread logs as read without generating report."""
        AIErrorLog.objects.create(
            provider='openrouter',
            model_name='anthropic/claude-3-haiku',
            error_type='network',
            error_message='Connection reset by peer',
            is_read=False
        )
        AIErrorLog.objects.create(
            provider='openrouter',
            model_name='anthropic/claude-3-haiku',
            error_type='network',
            error_message='Connection reset by peer',
            is_read=False
        )

        self.assertEqual(AIErrorLog.objects.filter(is_read=False).count(), 2)

        url = reverse('teacher_settings') + '?tab=ai&ai_section=errors'
        resp = self.client.post(url, {'action': 'mark_all_errors_read'})
        self.assertEqual(resp.status_code, 302)

        self.assertEqual(AIErrorLog.objects.filter(is_read=False).count(), 0)
        self.assertEqual(AIErrorLog.objects.filter(is_read=True).count(), 2)
