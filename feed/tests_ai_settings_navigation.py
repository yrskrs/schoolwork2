from urllib.parse import parse_qs, urlsplit

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from lxml import html

from .changelog import CHANGELOG_DATA, SITE_VERSION
from .middleware import set_has_admin
from .models import AICriteriaPreset, AIErrorLog, AISettings, School, Teacher


class AISettingsNavigationTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_superuser('settings_teacher', password='test-password')
        Teacher.objects.create(user=self.user, full_name='Вчитель')
        School.objects.create(name='Тестова школа', admin=self.user)
        self.client.force_login(self.user)
        self.url = reverse('teacher_settings')

    def page(self, query):
        response = self.client.get(self.url, query)
        self.assertEqual(response.status_code, 200)
        return response, html.fromstring(response.content)

    def assert_section(self, response, section, days=None):
        self.assertEqual(response.status_code, 302)
        params = parse_qs(urlsplit(response.url).query)
        self.assertEqual(params['tab'], ['ai'])
        self.assertEqual(params['ai_section'], [section])
        if days:
            self.assertEqual(params['stats_days'], [days])

    def test_each_link_opens_only_its_panel_without_javascript(self):
        sections = ['connection', 'criteria', 'models', 'statistics', 'errors']
        for section in sections:
            with self.subTest(section=section):
                _, page = self.page({'tab': 'ai', 'ai_section': section, 'stats_days': '14'})
                root = page.xpath('//*[@data-ai-settings]')[0]
                panels = root.xpath('./section[@data-ai-panel]')
                self.assertEqual(len(panels), 5)
                self.assertEqual([p.get('data-ai-panel') for p in panels if 'hidden' not in p.attrib], [section])
                tabs = root.xpath('.//*[@data-ai-tab]')
                self.assertEqual([t.get('data-ai-tab') for t in tabs if t.get('aria-selected') == 'true'], [section])
                for tab in tabs:
                    self.assertEqual(parse_qs(urlsplit(tab.get('href')).query)['stats_days'], ['14'])
                    self.assertEqual(len(root.xpath('./section[@id=$panel]', panel=tab.get('aria-controls'))), 1)

    def test_default_and_unknown_section_open_connection(self):
        for query in ({'tab': 'ai'}, {'tab': 'ai', 'ai_section': 'unknown'}):
            response, page = self.page(query)
            self.assertEqual(response.context['ai_section'], 'connection')
            self.assertEqual(page.xpath('//*[@data-ai-panel and not(@hidden)]/@data-ai-panel'), ['connection'])

    def test_collapsed_advanced_form_preserves_saved_values_on_submit(self):
        config = AISettings.get_solo()
        config.api_key = 'fake-primary-key'
        config.backup_api_key = 'fake-backup-key'
        config.backup_model_name = 'gemini-2.5-flash'
        config.system_prompt = 'Критерії вчителя: перевірити вкладену роботу.'
        config.temperature = 0.4
        config.ai_detector_tolerance_percent = 37
        config.default_thinking_mode = True
        config.save()
        _, page = self.page({'tab': 'ai'})
        advanced = page.get_element_by_id('ai-advanced-settings')
        self.assertNotIn('open', advanced.attrib)
        form = page.get_element_by_id('ai-global-config-form')
        self.assertIn(advanced, form.iterdescendants())
        # Serialize the actual successful HTML controls, including closed details.
        fields = {}
        for control in form.xpath('.//input[@name] | .//textarea[@name] | .//select[@name]'):
            if 'disabled' in control.attrib:
                continue
            if control.get('type') in ('checkbox', 'radio') and 'checked' not in control.attrib:
                continue
            if control.tag == 'textarea':
                value = control.text or ''
            elif control.tag == 'select':
                options = control.xpath('./option[@selected]') or control.xpath('./option')[:1]
                value = options[0].get('value', options[0].text or '') if options else ''
            else:
                value = control.get('value', 'on' if control.get('type') == 'checkbox' else '')
            fields[control.get('name')] = value
        response = self.client.post(self.url + '?tab=ai', fields)
        self.assert_section(response, 'connection')
        config.refresh_from_db()
        self.assertEqual([c['api_key'] for c in config.provider_connections], ['fake-primary-key', 'fake-backup-key'])
        self.assertTrue(config.unified_model_queue)
        self.assertEqual(config.api_key, '')
        self.assertEqual(config.backup_api_key, '')
        self.assertEqual(config.backup_model_name, 'gemini-2.5-flash')
        self.assertEqual(config.system_prompt, 'Критерії вчителя: перевірити вкладену роботу.')
        self.assertAlmostEqual(config.temperature, 0.4)
        self.assertEqual(config.ai_detector_tolerance_percent, 37)
        self.assertTrue(config.default_thinking_mode)

    def test_model_action_returns_to_models_and_preserves_period(self):
        response = self.client.post(self.url + '?tab=ai&stats_days=30', {
            'action': 'add_custom_model', 'new_model_name': 'gemini-test-model', 'priority': '99',
        })
        self.assert_section(response, 'models', '30')
        self.assertIn('gemini-test-model', AISettings.get_solo().get_saved_models())

    def test_criteria_actions_return_to_criteria(self):
        response = self.client.post(self.url, {
            'action': 'create_criteria_preset', 'preset_name': 'Власний шаблон',
            'system_prompt': 'Перевірити результат роботи',
        })
        self.assert_section(response, 'criteria')
        self.assertTrue(AICriteriaPreset.objects.filter(name='Власний шаблон').exists())

    def test_clear_errors_returns_to_errors(self):
        AIErrorLog.objects.create(action='evaluation', error_message='Test failure')
        response = self.client.post(self.url, {'action': 'clear_ai_error_logs'})
        self.assert_section(response, 'errors')
        self.assertFalse(AIErrorLog.objects.exists())

    def test_default_criteria_form_keeps_statistics_period(self):
        AICriteriaPreset.ensure_default_presets()
        AICriteriaPreset.objects.create(name='Додатковий шаблон', is_default=False)
        _, page = self.page({'tab': 'ai', 'ai_section': 'criteria', 'stats_days': '14'})
        form = page.xpath('//form[.//input[@name="action" and @value="set_default_criteria_preset"]]')[0]
        fields = {field.get('name'): field.get('value') for field in form.xpath('.//input[@name]')}
        target = form.get('action') or self.url + '?tab=ai&ai_section=criteria&stats_days=14'
        self.assert_section(self.client.post(target, fields), 'criteria', '14')

    def test_invalid_section_and_period_do_not_enter_redirect(self):
        response = self.client.post(self.url + '?stats_days=invalid', {
            'action': 'clear_ai_error_logs', 'ai_section': 'https://invalid.example/',
        })
        self.assert_section(response, 'connection')
        self.assertNotIn('stats_days', parse_qs(urlsplit(response.url).query))

    def test_statistics_period_survives_page_reload(self):
        response, page = self.page({'tab': 'ai', 'ai_section': 'statistics', 'stats_days': 'all'})
        self.assertEqual(response.context['stats_days'], 'all')
        self.assertEqual(page.xpath('//*[@data-ai-panel and not(@hidden)]/@data-ai-panel'), ['statistics'])

    def test_catalog_is_collapsed_and_kept_in_models_panel(self):
        _, page = self.page({'tab': 'ai', 'ai_section': 'models'})
        catalog = page.get_element_by_id('ai-model-catalog')
        self.assertNotIn('open', catalog.attrib)
        self.assertEqual(catalog.xpath('ancestor::section/@data-ai-panel'), ['models'])
        self.assertGreater(len(catalog.xpath('.//button[contains(@onclick, "selectModelToInput")]')), 0)

    def test_platform_updates_include_current_and_previous_changes(self):
        _, page = self.page({'tab': 'ai'})
        changelog = page.get_element_by_id('changelog-pane-teacher').text_content()
        self.assertEqual(CHANGELOG_DATA[0]['version'], SITE_VERSION)
        self.assertEqual(sum(release['is_latest'] for release in CHANGELOG_DATA), 1)
        self.assertIn('Компактні налаштування ШІ у п’яти вкладках', changelog)
        self.assertIn('Збережена черга ШІ та узгоджені критерії', changelog)
