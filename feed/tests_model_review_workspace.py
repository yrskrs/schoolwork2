"""Regression coverage for provider identity, credential routing and review controls."""
import datetime
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .ai_usage_statistics import build_model_usage_stats
from .middleware import set_has_admin
from .models import AISettings, AIRequestLog, Assignment, ClassGroup, School, Subject, Submission, Teacher


class ModelAndReviewWorkspaceTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_superuser('model_review_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        School.objects.create(name='Тестова школа', admin=user)
        self.subject = Subject.objects.create(name='Інформатика')
        self.group = ClassGroup.objects.create(name='9-А', grade=9, letter='А')
        self.teacher.subjects.add(self.subject)
        self.teacher.classes.add(self.group)
        self.assignment = Assignment.objects.create(teacher=self.teacher, subject=self.subject, title='Створити програму', description='Перевірити результат.', status='published')
        self.assignment.classes.add(self.group)
        self.client.force_login(user)
        self.settings = AISettings.get_solo()
        self.settings.api_key = 'primary-gemini-key'
        self.settings.model_name = 'gemini-primary'
        self.settings.backup_ai_provider = 'openai'
        self.settings.backup_api_key = 'backup-openai-key'
        self.settings.backup_model_name = 'gpt-secondary'
        self.settings.saved_models_list = json.dumps([
            {'provider':'gemini','name':'gemini-primary','priority':1,'enabled':True},
            {'provider':'openai','name':'gpt-secondary','priority':2,'enabled':True},
        ])
        self.settings.save()

    def post_model(self, **fields):
        return self.client.post(reverse('teacher_settings') + '?tab=ai&ai_section=models&stats_days=30', {'action':'add_custom_model', **fields})

    def test_add_provider_and_model_persists_and_routes_to_matching_key(self):
        response = self.post_model(model_provider='openai', new_model_name='gpt-4.1-mini', priority='2')
        self.assertEqual(response.status_code, 302)
        self.assertIn('ai_section=models', response.url)
        self.settings.refresh_from_db()
        item = next(m for m in self.settings.get_models_with_priority() if m['name']=='gpt-4.1-mini')
        self.assertEqual(item['provider'], 'openai')
        config = next(c for c in self.settings.get_request_configs() if c['model']=='gpt-4.1-mini')
        self.assertEqual(config['api_key'], 'backup-openai-key')
        self.assertTrue(config['is_backup'])
        self.assertFalse(any(c['provider']=='gemini' and c['model'].startswith('gpt-') for c in self.settings.get_request_configs()))

    def test_model_names_at_two_providers_remain_independent(self):
        self.settings.add_saved_model('shared-id', provider='gemini')
        self.settings.add_saved_model('shared-id', provider='openai')
        self.settings.toggle_model_enabled('shared-id', provider='gemini')
        shared = [m for m in self.settings.get_models_with_priority() if m['name']=='shared-id']
        self.assertEqual(len(shared), 2)
        self.assertFalse(next(m for m in shared if m['provider']=='gemini')['enabled'])
        self.assertTrue(next(m for m in shared if m['provider']=='openai')['enabled'])
        self.settings.remove_saved_model('shared-id', provider='gemini')
        self.assertEqual([m['provider'] for m in self.settings.get_models_with_priority() if m['name']=='shared-id'], ['openai'])

    def test_unconfigured_provider_is_saved_but_never_uses_other_key(self):
        self.post_model(model_provider='groq', new_model_name='openai/gpt-oss-20b', priority='1')
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.ai_provider, 'gemini')
        self.assertEqual(self.settings.model_name, 'gemini-primary')
        self.assertFalse(any(c['provider']=='groq' for c in self.settings.get_request_configs()))
        self.assertTrue(any(m['provider']=='groq' for m in self.settings.get_models_with_priority()))

    def test_make_active_switches_to_configured_backup_without_copying_keys(self):
        self.post_model(model_provider='openai', new_model_name='gpt-4.1-mini', priority='1')
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.active_api_type, 'backup')
        self.assertEqual(self.settings.get_active_config()[:3], ('openai','backup-openai-key','gpt-4.1-mini'))
        self.assertEqual(self.settings.api_key, 'primary-gemini-key')
        self.assertEqual(self.settings.get_request_configs()[0]['provider'], 'openai')

    def test_disabled_and_alternative_provider_skip_when_failover_off(self):
        self.settings.add_saved_model('gemini-disabled', provider='gemini', enabled=False)
        self.settings.auto_failover_enabled=False
        self.settings.save()
        configs=self.settings.get_request_configs()
        self.assertTrue(configs)
        self.assertEqual({c['provider'] for c in configs}, {'gemini'})
        self.assertFalse(any(c['model']=='gemini-disabled' for c in configs))

    def test_legacy_queue_names_infer_provider_and_gemini_chain_stays_compatible(self):
        self.settings.saved_models_list=json.dumps(['gemini-primary','gpt-4.1-mini','deepseek-flash'])
        self.settings.save()
        providers={m['name']:m['provider'] for m in self.settings.get_models_with_priority()}
        self.assertEqual(providers['gpt-4.1-mini'], 'openai')
        self.assertEqual(providers['deepseek-flash'], 'deepseek')
        self.assertEqual(self.settings.get_active_fallback_chain('gemini'), ['gemini-primary'])

    @patch('feed.gemini_service.call_ai_api')
    def test_criteria_request_falls_back_across_providers_with_matching_credentials(self, call):
        from .gemini_service import generate_criteria_with_gemini
        self.settings.is_enabled=True; self.settings.save()
        call.side_effect=[(429,'','Rate limit',{}),(200,'Перевірити правильність результату.','',{})]
        result=generate_criteria_with_gemini('Оцінити результат',assignment_title='Створити програму')
        self.assertEqual(result['status'],'success')
        self.assertEqual([c.kwargs['provider'] for c in call.call_args_list],['gemini','openai'])
        self.assertEqual([c.kwargs['api_key'] for c in call.call_args_list],['primary-gemini-key','backup-openai-key'])
        self.assertIn('Openai',result['model_used'])

    def test_invalid_provider_and_model_do_not_change_saved_queue(self):
        old=self.settings.saved_models_list
        for fields in [{'model_provider':'unsupported','new_model_name':'model'}, {'model_provider':'openai','new_model_name':'two words'}, {'model_provider':'openai','new_model_name':'a'*101}]:
            self.post_model(**fields)
            self.settings.refresh_from_db()
            self.assertEqual(self.settings.saved_models_list, old)

    def test_custom_provider_without_key_uses_its_own_url_and_default_backup_model(self):
        self.settings.backup_ai_provider='custom'; self.settings.backup_api_key=''; self.settings.backup_custom_api_url='http://127.0.0.1:11434/v1'; self.settings.backup_model_name='local-model';self.settings.active_api_type='backup';self.settings.save()
        configs=self.settings.get_request_configs()
        self.assertEqual(configs[0]['provider'],'custom')
        self.assertEqual(configs[0]['custom_url'],'http://127.0.0.1:11434/v1')
        self.assertEqual(configs[0]['api_key'],'')
        self.settings.backup_ai_provider='openai';self.settings.backup_model_name='';self.settings.backup_api_key='backup-openai-key';self.settings.active_api_type='primary';self.settings.save()
        self.assertEqual(self.settings.get_backup_config()[2], 'gpt-4o-mini')

    @patch('feed.gemini_service.call_ai_api', return_value=(200,'Підключено','',{}))
    def test_connection_test_resolves_saved_key_for_target_provider(self, call):
        response=self.client.post(reverse('api_test_gemini_connection'), {'provider':'openai','model_name':'gpt-4.1-mini'})
        self.assertTrue(response.json()['success'])
        self.assertEqual(call.call_args.kwargs['api_key'],'backup-openai-key')
        self.assertEqual(call.call_args.kwargs['provider'],'openai')
        self.assertEqual(call.call_args.kwargs['action'],'test_connection')
        self.assertEqual(self.settings.model_name,'gemini-primary')

    @patch('feed.gemini_service.call_ai_api')
    def test_unconfigured_connection_never_calls_remote_api(self, call):
        response=self.client.post(reverse('api_test_gemini_connection'), {'provider':'groq','model_name':'openai/gpt-oss-20b'})
        self.assertFalse(response.json()['success']); call.assert_not_called()
        response=self.client.post(reverse('api_test_gemini_connection'), {'provider':'unknown'})
        self.assertEqual(response.status_code,400)

    def test_model_catalog_has_all_supported_providers_and_real_form_fields(self):
        response=self.client.get(reverse('teacher_settings'), {'tab':'ai','ai_section':'models'})
        page=html.fromstring(response.content)
        catalog=json.loads(page.get_element_by_id('ai-provider-catalog-data').text)
        self.assertEqual({g['provider'] for g in catalog}, {'gemini','openai','deepseek','groq','openrouter','custom'})
        form=page.get_element_by_id('ai-add-model-form')
        self.assertTrue(form.xpath('.//select[@name="model_provider"]'))
        self.assertTrue(form.xpath('.//input[@name="new_model_name"]'))
        rows=page.xpath('//table//tr[td]')
        self.assertTrue(any(row.xpath('.//input[@name="model_provider" and @value="openai"]') for row in rows))

    def test_statistics_subviews_keep_data_accessible_without_long_default_page(self):
        for view in ['usage','models','overview','unknown']:
            response=self.client.get(reverse('teacher_settings'), {'tab':'ai','ai_section':'statistics','stats_view':view})
            page=html.fromstring(response.content)
            expected='usage' if view=='unknown' else view
            self.assertEqual(page.xpath('//*[@data-stats-panel and not(@hidden)]/@data-stats-panel'),[expected])
            self.assertEqual(len(page.xpath('//*[@id="stats_days_select"]')),1)
        self.assertEqual(page.get_element_by_id('ai-usage-display').xpath('./option/@value'),['bars','lines','curves'])

    def test_model_usage_groups_same_id_by_provider_in_constant_queries(self):
        now=timezone.now()
        for provider in ['gemini','openai']:
            for i in range(10):AIRequestLog.objects.create(model_name='shared-id',provider=provider,total_tokens=i+1,is_success=i%2==0)
        with self.assertNumQueries(3):
            stats=build_model_usage_stats(AIRequestLog.objects.all(), now+datetime.timedelta(seconds=1),now-datetime.timedelta(days=7),7,'openai','shared-id')
        self.assertEqual(len(stats),2)
        self.assertEqual(stats[0]['provider'],'openai')
        self.assertTrue(stats[0]['is_active'])
        self.assertEqual([s['total_tokens'] for s in stats],[55,55])
        self.assertEqual([s['period_failed_reqs'] for s in stats],[5,5])

    def test_viewer_grading_is_available_above_document_and_workbench(self):
        sub=Submission.objects.create(assignment=self.assignment,teacher=self.teacher,class_group=self.group,last_name='Учень',first_name='Тест',file=SimpleUploadedFile('a.txt',b'Answer'))
        Submission.objects.create(assignment=self.assignment,teacher=self.teacher,class_group=self.group,last_name='Інший',first_name='Тест',file=SimpleUploadedFile('b.txt',b'Answer'))
        response=self.client.get(reverse('view_file',args=[sub.pk]))
        self.assertEqual(response.status_code,200)
        page=html.fromstring(response.content)
        toolbar=page.xpath('//*[@class="fv-workspace-toolbar"]')[0]
        self.assertTrue(toolbar.xpath('following-sibling::*[1][contains(@class,"fv-grid")]'))
        grade=page.get_element_by_id('fv-grading-card')
        self.assertEqual(grade.getparent(),toolbar)
        self.assertFalse(grade.xpath('ancestor::*[@role="tabpanel"]'))
        self.assertEqual(len(page.xpath('//*[@id="grade-input"]')),1)
        self.assertIsNotNone(page.get_element_by_id('save-grade-next-btn'))
        self.assertEqual(page.get_element_by_id('grade-saved-msg').get('role'),'status')
        self.assertEqual(len(page.xpath('//*[@data-review-panel]')),4)
        self.assertFalse(page.get_element_by_id('fv-workbench').xpath('.//details[contains(@class,"fv-context-details") or contains(@class,"fv-ai-details")]'))
        self.assertFalse(page.get_element_by_id('assignment-file-modal').xpath('ancestor::aside'))
        self.assertFalse(page.xpath('//footer/ancestor::*[contains(@class,"fv-container")]'))
        self.assertContains(response,'review_workspace.css')
