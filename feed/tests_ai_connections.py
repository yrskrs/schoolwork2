"""Unified credentials, provider-independent failover and per-request accounting."""
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from lxml import html

from .ai_connections import save_connection
from .ai_request_metadata import parse_usage
from .gemini_service import evaluate_submission_with_gemini, log_ai_request_metric, generate_criteria_with_gemini
from .middleware import set_has_admin
from .models import AICriteriaPreset, AIRequestLog, AISettings, Assignment, ClassGroup, School, Submission, Teacher


class AIConnectionsTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_superuser('connections_teacher', password='test-password')
        School.objects.create(name='Тестова школа', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.group = ClassGroup.objects.create(name='10-А', grade=10)
        self.teacher.classes.add(self.group)
        self.preset = AICriteriaPreset.objects.create(name='Традиційна перевірка', evaluation_type='traditional')
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Правила безпеки',
            description='Напиши правила безпечної роботи в інтернеті.', status='published', default_ai_preset=self.preset)
        self.assignment.classes.add(self.group)
        self.sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Тест', file=SimpleUploadedFile('work.txt', 'Використовуй складні паролі.'.encode()))
        self.settings = AISettings.objects.create(id=1, unified_model_queue=True, saved_models_list='[]', is_enabled=True)
        self.client.force_login(self.user)
        self.url = reverse('teacher_settings') + '?tab=ai&ai_section=connection'

    def add_connection(self, provider, model='shared-model', key=None, **extra):
        self.settings = save_connection(self.settings.pk, {'connection_provider': provider,
            'connection_key': key or provider + '-synthetic-key', 'connection_model': model, **extra})
        return self.settings.provider_connections[-1]['id']

    def answer(self, data=None):
        result = {'suggested_grade': '9', 'level': 'Достатній', 'summary': 'Правила правильні.',
                  'feedback_comment': 'Добре виконано.', 'gr_results': []}
        return (200, json.dumps(result), '', data or {})

    def test_any_number_of_providers_and_same_provider_keys_have_exact_queue_order(self):
        identities = [self.add_connection(p, p + '-model') for p in ['gemini', 'openrouter', 'groq', 'groq']]
        self.settings.move_model_priority('groq-model', 'up', connection_id=identities[-1])
        configs = self.settings.get_request_configs()
        self.assertEqual([c['connection_id'] for c in configs], identities[:2] + identities[:1:-1])
        self.assertEqual([c['provider'] for c in configs], ['gemini', 'openrouter', 'groq', 'groq'])
        self.assertFalse(any(c['is_backup'] for c in configs))
        from .gemini_service import get_provider_endpoint
        self.assertEqual(get_provider_endpoint('gemini', model_name='models/gemini-2.5-flash', api_key='synthetic')[2], 'gemini-2.5-flash')

    def test_queue_does_not_prepend_historical_active_model(self):
        first = self.add_connection('gemini', 'gemini-later')
        second = self.add_connection('groq', 'groq-first')
        self.settings.activate_saved_model('groq-first', connection_id=second)
        self.settings.model_name = 'gemini-historical'
        self.settings.active_api_type = 'backup'
        self.settings.save()
        self.assertEqual([c['connection_id'] for c in self.settings.get_request_configs()], [second, first])
        self.assertEqual(self.settings.get_active_config()[:3], ('groq', 'groq-synthetic-key', 'groq-first'))

    def test_disabled_first_model_is_never_called_and_failover_off_means_one_model(self):
        first = self.add_connection('gemini')
        second = self.add_connection('groq')
        self.add_connection('openrouter')
        self.settings.toggle_model_enabled('shared-model', connection_id=first)
        self.settings.auto_failover_enabled = False
        self.settings.save()
        self.assertEqual([c['connection_id'] for c in self.settings.get_request_configs()], [second])

    def test_identical_model_names_at_two_keys_mutate_independently(self):
        first = self.add_connection('groq', key='first-key')
        second = self.add_connection('groq', key='second-key')
        self.settings.toggle_model_enabled('shared-model', connection_id=first)
        self.assertEqual([c['api_key'] for c in self.settings.get_request_configs()], ['second-key'])
        self.settings.remove_saved_model('shared-model', connection_id=first)
        self.assertEqual([m['connection_id'] for m in self.settings.get_models_with_priority()], [second])

    def test_model_forms_move_toggle_and_activate_only_selected_connection(self):
        first = self.add_connection('groq', key='first-key')
        second = self.add_connection('groq', key='second-key')
        self.client.post(self.url, {'action': 'move_model_priority', 'connection_id': second,
                                  'model_name': 'shared-model', 'direction': 'up'})
        self.client.post(self.url, {'action': 'toggle_model_enabled', 'connection_id': second, 'model_name': 'shared-model'})
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.get_request_configs()[0]['connection_id'], first)
        self.client.post(self.url, {'action': 'switch_model', 'connection_id': second, 'switch_to_model': 'shared-model'})
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.get_request_configs()[0]['api_key'], 'second-key')
        self.assertTrue(self.settings.get_models_with_priority()[0]['enabled'])

    def test_unconfigured_models_skip_without_borrowing_another_provider_key(self):
        first = self.add_connection('gemini')
        second = self.add_connection('groq')
        self.settings.provider_connections[0]['api_key'] = ''
        self.settings.save()
        self.assertEqual([c['connection_id'] for c in self.settings.get_request_configs()], [second])
        self.assertIsNone(self.settings.get_provider_config('groq', connection_id=first))

    def test_custom_local_provider_can_have_url_without_key(self):
        self.settings = save_connection(self.settings.pk, {'connection_provider': 'custom',
            'connection_url': 'http://127.0.0.1:11434/v1', 'connection_model': 'local-model'})
        config = self.settings.get_request_configs()[0]
        self.assertEqual((config['provider'], config['api_key'], config['custom_url']),
                         ('custom', '', 'http://127.0.0.1:11434/v1'))

    def test_delete_connection_removes_only_its_models_and_cannot_resurrect_legacy_keys(self):
        first = self.add_connection('gemini')
        second = self.add_connection('groq')
        self.settings = save_connection(self.settings.pk, {'connection_id': first}, delete=True)
        self.assertEqual([m['connection_id'] for m in self.settings.get_models_with_priority()], [second])
        self.settings = save_connection(self.settings.pk, {'connection_id': second}, delete=True)
        self.assertEqual(self.settings.get_request_configs(), [])
        self.assertIsNone(self.settings.get_backup_config())

    def test_update_blank_key_keeps_stored_key_and_does_not_reorder_models(self):
        identity = self.add_connection('groq', key='stored-synthetic-key')
        self.add_connection('gemini')
        old_queue = self.settings.saved_models_list
        self.settings = save_connection(self.settings.pk, {'connection_id': identity, 'connection_provider': 'groq',
            'connection_label': 'Оновлена назва', 'connection_key': ''})
        self.assertEqual(self.settings.get_connection(identity)['api_key'], 'stored-synthetic-key')
        self.assertEqual(self.settings.saved_models_list, old_queue)
        self.assertEqual(self.settings.get_models_with_priority()[0]['connection_label'], 'Оновлена назва')

    def test_validation_does_not_store_invalid_connection_or_change_supplier_of_existing_key(self):
        identity = self.add_connection('groq')
        before = self.settings.provider_connections
        for fields in [{'connection_provider': 'unsupported'}, {'connection_provider': 'custom', 'connection_url': 'file:///tmp/test'},
                       {'connection_provider': 'groq', 'connection_key': ''},
                       {'connection_provider': 'gemini', 'connection_id': identity, 'connection_key': ''}]:
            with self.assertRaises(ValueError):
                save_connection(self.settings.pk, fields)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.provider_connections, before)

    def test_connection_form_saves_many_keys_without_exposing_stored_secrets(self):
        for provider in ['gemini', 'groq', 'openrouter']:
            response = self.client.post(self.url, {'action': 'save_ai_connection', 'connection_provider': provider,
                'connection_key': provider + '-secret-not-in-dom', 'connection_model': 'shared-model'})
            self.assertEqual(response.status_code, 302)
        response = self.client.get(self.url)
        page = html.fromstring(response.content)
        self.assertEqual(len(page.xpath('//*[@data-ai-connection-form]')), 3)
        for provider in ['gemini', 'groq', 'openrouter']:
            self.assertNotContains(response, provider + '-secret-not-in-dom')
        self.assertFalse(page.xpath('//*[@name="backup_api_key" or @name="active_api_type"]'))
        public = json.loads(page.get_element_by_id('ai-connections-data').text)
        self.assertFalse(any('api_key' in item for item in public))

    @patch('feed.gemini_service.call_ai_api', return_value=(200, 'Підключено', '', {}))
    def test_connection_test_uses_exact_selected_key_without_client_copy(self, call):
        self.add_connection('groq', key='first-key')
        second = self.add_connection('groq', key='second-key')
        response = self.client.post(reverse('api_test_gemini_connection'), {'connection_id': second, 'model_name': 'shared-model'})
        self.assertTrue(response.json()['success'])
        self.assertEqual(call.call_args.kwargs['api_key'], 'second-key')
        response = self.client.post(reverse('api_test_gemini_connection'), {'connection_id': second, 'provider': 'gemini'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(call.call_count, 1)

    @patch('feed.gemini_service.call_ai_api')
    def test_review_falls_back_once_per_model_and_persists_successful_request_usage(self, call):
        self.add_connection('gemini', 'gemini-first')
        self.add_connection('groq', 'groq-second')
        self.add_connection('openrouter', 'openrouter-third')
        usage = {'usage': {'prompt_tokens': 123, 'completion_tokens': 45, 'total_tokens': 168}, 'model': 'actual-third-model'}
        call.side_effect = [(429, '', 'Rate limit', {}), (401, '', 'Invalid key', {}), self.answer(usage)]
        result = evaluate_submission_with_gemini(self.sub, ai_settings=self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual([c.kwargs['provider'] for c in call.call_args_list], ['gemini', 'groq', 'openrouter'])
        self.assertEqual([c.kwargs['api_key'] for c in call.call_args_list], ['gemini-synthetic-key', 'groq-synthetic-key', 'openrouter-synthetic-key'])
        self.assertTrue(result['fallback_activated'])
        self.sub.refresh_from_db()
        self.assertEqual(result['request_metadata'], self.sub.get_ai_request_metadata())
        self.assertEqual((self.sub.ai_provider_used, self.sub.ai_request_model, self.sub.ai_total_tokens), ('openrouter', 'actual-third-model', 168))
        page = self.client.get(reverse('view_file', args=[self.sub.pk]))
        details = html.fromstring(page.content).xpath('//*[@class="ai-request-metadata"]')[0]
        self.assertIn('actual-third-model', details.text_content())
        self.assertIn('168', details.text_content())

    @patch('feed.gemini_service.call_ai_api')
    def test_malformed_answer_moves_to_next_provider_and_uses_its_usage(self, call):
        self.add_connection('groq')
        self.add_connection('gemini')
        call.side_effect = [(200, 'broken answer', '', {'usage': {'total_tokens': 500}}),
            self.answer({'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 5, 'totalTokenCount': 40}})]
        result = evaluate_submission_with_gemini(self.sub, ai_settings=self.settings)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['request_metadata']['total_tokens'], 40)
        self.assertEqual(call.call_count, 2)

    @patch('feed.gemini_service.call_ai_api')
    def test_unknown_usage_overwrites_previous_counter_without_inventing_tokens(self, call):
        self.add_connection('gemini')
        self.sub.ai_total_tokens = 99
        self.sub.save()
        call.return_value = self.answer()
        result = evaluate_submission_with_gemini(self.sub, ai_settings=self.settings)
        self.assertEqual(result['status'], 'success')
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.ai_total_tokens)
        self.assertIsNone(result['request_metadata']['total_tokens'])

    @patch('feed.gemini_service.call_ai_api')
    def test_criteria_generation_uses_same_cross_provider_queue(self, call):
        self.add_connection('groq')
        self.add_connection('gemini')
        call.side_effect = [(503, '', 'Overloaded', {}), (200, 'Перевірити правила.', '', {})]
        result = generate_criteria_with_gemini('Правила безпеки')
        self.assertEqual(result['status'], 'success')
        self.assertEqual([c.kwargs['provider'] for c in call.call_args_list], ['groq', 'gemini'])

    @patch('feed.gemini_service.call_ai_api')
    def test_batch_job_and_reloaded_queue_return_saved_metadata(self, call):
        self.add_connection('groq')
        page = self.client.get(reverse('ai_batch_check'))
        self.assertNotContains(page, 'Немає доступної моделі')
        call.return_value = self.answer({'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}})
        response = self.client.post(reverse('api_ai_process_item', args=[self.sub.pk]))
        self.assertEqual(response.json()['status'], 'success')
        self.assertEqual(response.json()['request_metadata']['total_tokens'], 15)
        queue = self.client.post(reverse('api_ai_get_batch_queue')).json()['queue']
        self.assertEqual(queue[0]['request_metadata'], response.json()['request_metadata'])

    def test_data_migration_preserves_two_keys_of_same_provider_and_disabled_models(self):
        self.settings.unified_model_queue = False
        self.settings.api_key, self.settings.backup_api_key = 'first-old-key', 'second-old-key'
        self.settings.ai_provider = self.settings.backup_ai_provider = 'groq'
        self.settings.model_name = self.settings.backup_model_name = 'shared-model'
        self.settings.active_api_type = 'backup'
        self.settings.saved_models_list = json.dumps([{'name': 'disabled-model', 'provider': 'groq', 'enabled': False},
                                                     {'name': 'shared-model', 'provider': 'groq'}])
        self.settings.save()
        migration = importlib.import_module('feed.migrations.0052_unified_ai_connections')
        migration.migrate_connections(apps, SimpleNamespace(connection=connection))
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.unified_model_queue)
        self.assertEqual([c['api_key'] for c in self.settings.provider_connections], ['first-old-key', 'second-old-key'])
        self.assertEqual([c['api_key'] for c in self.settings.get_request_configs()], ['second-old-key', 'first-old-key'])
        self.assertFalse(next(m for m in self.settings.get_models_with_priority() if m['name'] == 'disabled-model')['enabled'])
        self.assertEqual((self.settings.api_key, self.settings.backup_api_key), ('', ''))

    def test_usage_parsing_preserves_zero_and_api_total_including_reasoning(self):
        usage = parse_usage({'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 0, 'totalTokenCount': 40}})
        self.assertEqual(usage, {'prompt_tokens': 20, 'completion_tokens': 0, 'total_tokens': 40})
        self.assertEqual(parse_usage({'usage': {'prompt_tokens': -1, 'completion_tokens': 'broken', 'total_tokens': None}}),
                         {'prompt_tokens': None, 'completion_tokens': None, 'total_tokens': None})

    def test_request_log_records_only_reported_usage(self):
        log_ai_request_metric('no-usage', data={})
        log_ai_request_metric('zero-usage', data={'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}})
        log_ai_request_metric('gemini-usage', data={'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 5, 'totalTokenCount': 40}})
        unknown = AIRequestLog.objects.get(model_name='no-usage')
        self.assertEqual(unknown.total_tokens, 0)
        self.assertFalse(unknown.usage_reported)
        self.assertTrue(AIRequestLog.objects.get(model_name='zero-usage').usage_reported)
        self.assertEqual(AIRequestLog.objects.get(model_name='gemini-usage').total_tokens, 40)
