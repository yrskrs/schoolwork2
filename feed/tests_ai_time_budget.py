"""Time budget of one assessment: provider timeouts versus the shared deadline.

4.14.4 raised the local-model request timeout to 240 s inside a 300 s
`AI_REQUEST_DEADLINE`. These tests keep the contract explicit: the deadline is
the outer limit, every HTTP request is clamped to the remaining budget, and an
exhausted budget never sends more requests.
"""
import time
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase

from . import tests_ai_connections as fixtures
from .ai_transport import evaluation_deadline, guarded_http, request_config
from .gemini_service import evaluate_media_in_batches, evaluate_submission_with_gemini


def picture(index=0):
    import base64
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (24, 24), (index * 20, 10, 10)).save(buffer, format='PNG')
    return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(),
            'source': f'сторінка {index + 1}'}


class DeadlineContractTests(TestCase):
    def test_deadline_has_room_for_the_local_model_timeout(self):
        self.assertGreaterEqual(settings.AI_REQUEST_DEADLINE, 240)
        self.assertLess(settings.AI_REQUEST_DEADLINE, settings.AI_JOB_TIMEOUT)

    def test_request_timeout_is_clamped_to_the_remaining_deadline(self):
        seen = {}

        @guarded_http
        def post(url, payload, timeout=35, **kwargs):
            seen['timeout'] = timeout
            return 200, {'usage': {}}, ''

        config = {'provider': 'custom', 'connection_id': '', 'model': 'qwen3-vl-8b-instruct',
                  'credential_fingerprint': 'synthetic', 'deadline': time.monotonic() + 30}
        token = request_config.set(config)
        try:
            post('http://192.168.1.12:1234/v1/chat/completions', {'messages': [{'role': 'user', 'content': 'x'}]},
                 timeout=240)
        finally:
            request_config.reset(token)
        # A 240 s request must not outlive a 30 s budget.
        self.assertLessEqual(seen['timeout'], 30)
        self.assertGreater(seen['timeout'], 20)

    def test_direct_call_keeps_the_full_provider_timeout(self):
        seen = {}

        @guarded_http
        def post(url, payload, timeout=35, **kwargs):
            seen['timeout'] = timeout
            return 200, {'usage': {}}, ''

        post('http://192.168.1.12:1234/v1/chat/completions', {'messages': []}, timeout=240)
        self.assertEqual(seen['timeout'], 240)

    def test_exhausted_deadline_sends_no_further_requests(self):
        media = [picture(0), picture(1), picture(2)]
        with patch('feed.gemini_service.call_ai_api') as call:
            token = evaluation_deadline.set(time.monotonic() - 1)
            try:
                status, text, error, data, meta = evaluate_media_in_batches(
                    'Оціни роботу', 'Правила', media, provider='custom', api_key='synthetic',
                    model_name='qwen3-vl-8b-instruct', custom_url='http://192.168.1.12:1234',
                    max_images=1, max_output_tokens=1500, timeout=240)
            finally:
                evaluation_deadline.reset(token)
        call.assert_not_called()
        self.assertEqual(status, 0)
        self.assertIsNone(text)
        self.assertIn('Жодна партія', error)
        self.assertTrue(all(batch.get('error') == 'deadline' for batch in meta['batches']))


class CustomProviderTimeoutTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def answer(self):
        import json
        result = {'suggested_grade': '9', 'level': 'Достатній', 'summary': 'Правила правильні.',
                  'feedback_comment': 'Добре виконано.', 'gr_results': []}
        return (200, json.dumps(result), '', {})

    @patch('feed.gemini_service.call_ai_api')
    def test_custom_evaluation_allows_a_long_local_generation(self, call):
        self.add_connection('custom', 'qwen3-vl-8b-instruct', connection_url='http://192.168.1.12:1234')
        call.return_value = self.answer()
        result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(call.call_args.kwargs['timeout'], 240)

    @patch('feed.gemini_service.call_ai_api')
    def test_hosted_providers_keep_short_timeouts(self, call):
        self.add_connection('gemini', 'gemini-3.6-flash')
        call.return_value = self.answer()
        result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(call.call_args.kwargs['timeout'], 50)
