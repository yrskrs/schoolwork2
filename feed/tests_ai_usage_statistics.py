import datetime
import json
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .ai_usage_statistics import build_usage_chart
from .middleware import set_has_admin
from .models import AIRequestLog, School, Teacher


@override_settings(TIME_ZONE='Europe/Kyiv')
class AIUsageStatisticsTests(TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 10, 3, 12, tzinfo=ZoneInfo('Europe/Kyiv'))
        set_has_admin(True)
        self.user = User.objects.create_user('usage_teacher', password='test-password')
        Teacher.objects.create(user=self.user, full_name='Вчитель')
        School.objects.create(name='Школа', admin=self.user)
        self.client.force_login(self.user)

    def log(self, at, **kwargs):
        defaults = {'model_name': 'model-a', 'provider': 'gemini', 'action': 'evaluation',
                    'prompt_tokens': 20, 'completion_tokens': 5, 'total_tokens': 25}
        item = AIRequestLog.objects.create(**{**defaults, **kwargs})
        AIRequestLog.objects.filter(pk=item.pk).update(created_at=at)
        return item

    def chart(self, start=None):
        return build_usage_chart(AIRequestLog.objects.all(), self.now, start)

    def test_daily_buckets_include_zero_days_and_keep_exact_totals(self):
        self.log(self.now - datetime.timedelta(days=2))
        self.log(self.now - datetime.timedelta(days=2), is_success=False, status_code=429,
                 prompt_tokens=2, completion_tokens=1, total_tokens=3)
        self.log(self.now - datetime.timedelta(days=8))
        self.log(self.now + datetime.timedelta(hours=1))
        chart = self.chart(self.now - datetime.timedelta(days=7))
        self.assertEqual(chart['resolution'], 'day')
        self.assertEqual(len(chart['labels']), 8)
        self.assertEqual(sum(row['requests'] for row in chart['rows']), 2)
        self.assertEqual(sum(row['total_tokens'] for row in chart['rows']), 28)
        self.assertEqual({row['index'] for row in chart['rows']}, {5})
        self.assertEqual({row['is_success'] for row in chart['rows']}, {True, False})

    def test_filters_can_distinguish_models_providers_actions_and_status(self):
        self.log(self.now, model_name='shared-model')
        self.log(self.now, model_name='shared-model', provider='openai', action='understanding', is_success=False)
        self.log(self.now, model_name='model-b', provider='openai')
        chart = self.chart(self.now - datetime.timedelta(days=7))
        self.assertEqual(chart['models'], ['model-b', 'shared-model'])
        self.assertEqual(chart['providers'], ['gemini', 'openai'])
        self.assertEqual(chart['actions'], ['evaluation', 'understanding'])
        self.assertEqual(len(chart['rows']), 3)
        self.assertEqual(sum(row['requests'] for row in chart['rows'] if row['provider'] == 'openai'), 2)

    def test_hourly_chart_uses_local_time_instead_of_utc_midnight(self):
        self.now = datetime.datetime(2026, 10, 3, 0, 30, tzinfo=ZoneInfo('Europe/Kyiv'))
        self.log(self.now - datetime.timedelta(minutes=10))
        chart = self.chart(self.now - datetime.timedelta(days=1))
        self.assertEqual(chart['resolution'], 'hour')
        self.assertEqual(len(chart['labels']), 25)
        self.assertEqual(chart['labels'][-1], '03.10 00:00')
        self.assertEqual(chart['rows'][0]['index'], 24)

    def test_long_all_time_history_groups_by_month_without_losing_requests(self):
        self.log(self.now - datetime.timedelta(days=400))
        self.log(self.now - datetime.timedelta(days=399))
        self.log(self.now)
        chart = self.chart()
        self.assertEqual(chart['resolution'], 'month')
        self.assertLess(len(chart['labels']), 16)
        self.assertEqual(sum(row['requests'] for row in chart['rows']), 3)
        self.assertEqual(sum(row['total_tokens'] for row in chart['rows']), 75)

    def test_empty_history_and_empty_period_are_valid(self):
        chart = self.chart()
        self.assertEqual(chart['labels'], [])
        self.assertEqual(chart['rows'], [])
        self.log(self.now - datetime.timedelta(days=10))
        chart = self.chart(self.now - datetime.timedelta(days=3))
        self.assertEqual(chart['rows'], [])
        self.assertEqual(len(chart['labels']), 4)

    def test_aggregation_query_count_does_not_grow_with_number_of_models(self):
        for i in range(20):
            self.log(self.now, model_name=f'model-{i}')
        with self.assertNumQueries(1):
            chart = self.chart(self.now - datetime.timedelta(days=7))
        self.assertEqual(len(chart['models']), 20)

    def test_statistics_renders_safe_payload_filters_and_cards(self):
        name = '</script><script>unexpected()</script>'
        self.log(self.now, model_name=name)
        with patch('feed.views.timezone.now', return_value=self.now):
            response = self.client.get(reverse('teacher_settings'), {'tab': 'ai', 'ai_section': 'statistics', 'stats_days': '7'})
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.content)
        source = page.get_element_by_id('ai-usage-chart-data').text
        self.assertNotIn('<script>', source)
        payload = json.loads(source)
        self.assertEqual(payload['models'], [name])
        self.assertNotIn('created_at', payload['rows'][0])
        self.assertEqual(len(page.xpath('//*[@data-usage-filter]')), 5)
        self.assertEqual(len(page.xpath('//*[@id="stats_days_select"]')), 1)
        self.assertEqual(page.xpath('//article[@class="ai-model-stat-card"]//code')[0].text_content(), name)
        self.assertFalse(page.xpath('//table[contains(@class,"ai-model-usage-table")]'))

    def test_teacher_permission_and_local_today_count_are_preserved(self):
        self.now = datetime.datetime(2026, 10, 3, 0, 30, tzinfo=ZoneInfo('Europe/Kyiv')).astimezone(datetime.timezone.utc)
        self.log(self.now - datetime.timedelta(minutes=15))
        with patch('feed.views.timezone.now', return_value=self.now):
            response = self.client.get(reverse('teacher_settings'), {'tab': 'ai', 'ai_section': 'statistics'})
        self.assertEqual(response.context['used_model_usage_stats'][0]['requests_today'], 1)
        self.client.logout()
        response = self.client.get(reverse('teacher_settings'), {'tab': 'ai', 'ai_section': 'statistics'})
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('teacher_login'), response.url)
