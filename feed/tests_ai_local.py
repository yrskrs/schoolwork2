"""Local AI endpoints: detection, own limits and isolation from hosted models."""
import base64
import io

from PIL import Image
from django.test import SimpleTestCase, TestCase

from . import tests_ai_connections as fixtures
from .ai_connections import request_configs, save_connection, selectable_ai_models
from .ai_local import (DEFAULT_LOCAL_DEADLINE, is_local_connection, is_local_url,
                       local_limits)


class DetectionTests(SimpleTestCase):
    def test_loopback_and_private_addresses_are_local(self):
        for url in ('http://localhost:1234/v1', 'http://127.0.0.1:1234', 'http://192.168.1.12:1234/v1',
                    'http://10.0.0.5:8000/v1', 'http://172.16.3.4:1234/v1',
                    'http://host.docker.internal:1234/v1', 'http://server.local:1234/v1',
                    'http://[::1]:1234/v1'):
            with self.subTest(url=url):
                self.assertTrue(is_local_url(url))

    def test_public_endpoints_are_not_local(self):
        for url in ('https://api.groq.com/openai/v1', 'https://openrouter.ai/api/v1',
                    'https://generativelanguage.googleapis.com', '', None, 'not a url'):
            with self.subTest(url=url):
                self.assertFalse(is_local_url(url))

    def test_only_custom_provider_can_be_local(self):
        self.assertFalse(is_local_connection({'provider': 'gemini', 'custom_url': 'http://127.0.0.1:1234'}))
        self.assertTrue(is_local_connection({'provider': 'custom', 'custom_url': 'http://192.168.1.12:1234'}))
        # Explicit teacher choice wins over address detection.
        self.assertFalse(is_local_connection(
            {'provider': 'custom', 'custom_url': 'http://192.168.1.12:1234', 'local': False}))

    def test_limits_are_clamped_and_bounded_by_the_job_timeout(self):
        limits = local_limits({'provider': 'custom', 'custom_url': 'http://127.0.0.1:1234',
                               'local_deadline': 99999, 'local_timeout': 1, 'local_image_px': 100})
        self.assertEqual(limits['local'], True)
        self.assertLessEqual(limits['local_deadline'], 1800)
        self.assertEqual(limits['local_timeout'], 60)
        self.assertEqual(limits['local_image_px'], 512)
        capped = local_limits({'provider': 'custom', 'custom_url': 'http://127.0.0.1:1234',
                               'local_deadline': 1800}, job_timeout=600)
        self.assertEqual(capped['local_deadline'], 540)


class ConnectionSettingsTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def test_local_mode_and_limits_are_saved(self):
        connection_id = self.add_connection('custom', 'qwen3-vl-8b-instruct',
                                            connection_url='http://192.168.1.12:1234')
        settings = save_connection(self.settings.pk, {
            'connection_id': connection_id, 'connection_provider': 'custom',
            'connection_label': 'LM Studio', 'connection_model': 'qwen3-vl-8b-instruct',
            'connection_url': 'http://192.168.1.12:1234',
            'local': 'yes', 'local_deadline': '900', 'local_timeout': '420', 'local_image_px': '768'})
        connection = settings.get_connection(connection_id)
        self.assertTrue(connection['local'])
        self.assertEqual(connection['local_deadline'], 900)
        self.assertEqual(connection['local_timeout'], 420)
        self.assertEqual(connection['local_image_px'], 768)

    def test_out_of_range_limits_are_rejected(self):
        connection_id = self.add_connection('custom', 'qwen3-vl-8b-instruct',
                                            connection_url='http://192.168.1.12:1234')
        with self.assertRaises(ValueError):
            save_connection(self.settings.pk, {
                'connection_id': connection_id, 'connection_provider': 'custom',
                'connection_label': 'LM Studio', 'connection_url': 'http://192.168.1.12:1234',
                'local_deadline': '5'})

    def test_auto_mode_keeps_detection(self):
        settings = save_connection(self.settings.pk, {
            'connection_provider': 'custom', 'connection_label': 'Local',
            'connection_url': 'http://127.0.0.1:1234', 'connection_model': 'local-model'})
        connection = settings.provider_connections[-1]
        self.assertNotIn('local', connection)
        self.assertTrue(local_limits(connection)['local'])
        configs = request_configs(settings)
        self.assertTrue(configs[0]['local'])
        self.assertEqual(configs[0]['local_deadline'], DEFAULT_LOCAL_DEADLINE)

    def test_hosted_configs_are_not_marked_local(self):
        self.add_connection('gemini', 'gemini-3.6-flash')
        configs = request_configs(self.settings)
        self.assertFalse(configs[0]['local'])
        self.assertEqual(configs[0]['local_deadline'], DEFAULT_LOCAL_DEADLINE)

    def test_selectable_models_mark_local_options(self):
        self.add_connection('gemini', 'gemini-3.6-flash')
        self.add_connection('custom', 'qwen3-vl-8b-instruct', connection_url='http://192.168.1.12:1234')
        options = {option['model']: option for option in selectable_ai_models(self.settings)}
        self.assertFalse(options['gemini-3.6-flash']['local'])
        self.assertNotIn('локальна', options['gemini-3.6-flash']['label'])
        self.assertTrue(options['qwen3-vl-8b-instruct']['local'])
        self.assertIn('локальна', options['qwen3-vl-8b-instruct']['label'])
        self.assertIn('ліміт', options['qwen3-vl-8b-instruct']['label'])


class LocalEvaluationBudgetTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def answer(self):
        import json
        return (200, json.dumps({'suggested_grade': '9', 'level': 'Достатній',
                                 'summary': 'Правила правильні.', 'feedback_comment': 'Добре.',
                                 'gr_results': []}), '', {})

    def test_local_check_gets_its_own_deadline_and_timeout(self):
        import time
        from unittest.mock import patch
        from .ai_transport import evaluation_deadline
        from .gemini_service import evaluate_submission_with_gemini

        connection_id = self.add_connection('custom', 'qwen3-vl-8b-instruct',
                                            connection_url='http://192.168.1.12:1234')
        save_connection(self.settings.pk, {
            'connection_id': connection_id, 'connection_provider': 'custom',
            'connection_label': 'LM Studio', 'connection_url': 'http://192.168.1.12:1234',
            'local': 'yes', 'local_deadline': '600', 'local_timeout': '300'})
        seen = {}

        def fake_call(**kwargs):
            seen['timeout'] = kwargs['timeout']
            seen['image_px'] = kwargs.get('image_px')
            seen['remaining'] = (evaluation_deadline.get() or 0) - time.monotonic()
            return self.answer()

        with patch('feed.gemini_service.call_ai_api', side_effect=fake_call):
            result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(seen['timeout'], 300)
        self.assertEqual(seen['image_px'], 1024)
        self.assertGreater(seen['remaining'], 500)

    def test_hosted_check_keeps_the_hosted_budget(self):
        import time
        from unittest.mock import patch
        from .ai_transport import evaluation_deadline
        from .gemini_service import evaluate_submission_with_gemini

        self.add_connection('gemini', 'gemini-3.6-flash')
        seen = {}

        def fake_call(**kwargs):
            seen['timeout'] = kwargs['timeout']
            seen['image_px'] = kwargs.get('image_px')
            seen['remaining'] = (evaluation_deadline.get() or 0) - time.monotonic()
            return self.answer()

        with patch('feed.gemini_service.call_ai_api', side_effect=fake_call):
            result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(seen['timeout'], 50)
        self.assertIsNone(seen['image_px'])
        self.assertLess(seen['remaining'], 305)


class LocalImageSizeTests(SimpleTestCase):
    def picture(self, size):
        buffer = io.BytesIO()
        Image.new('RGB', (size, size), 'white').save(buffer, format='PNG')
        return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(),
                'source': 'page.png'}

    def test_smaller_page_size_is_applied_for_local_models(self):
        from .ai_payload import optimize_media
        optimized = optimize_media([self.picture(1600)], 'custom', image_px=512)[0]
        image = Image.open(io.BytesIO(base64.b64decode(optimized['data'])))
        self.assertLessEqual(max(image.size), 512)

    def test_default_page_size_stays_1024(self):
        from .ai_payload import optimize_media
        optimized = optimize_media([self.picture(1600)], 'custom')[0]
        image = Image.open(io.BytesIO(base64.b64decode(optimized['data'])))
        self.assertLessEqual(max(image.size), 1024)
        self.assertGreater(max(image.size), 512)
