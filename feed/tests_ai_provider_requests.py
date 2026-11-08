"""Regression tests for AI provider request pre-flight validation.

These tests cover the four provider error classes reported in production:
- Cloudflare 400 "too many images in request (max 32)"
- Cloudflare 400 "not a multimodal model"
- OpenRouter 413 (payload larger than the safe byte limit)
- Gemini 400 "invalid argument" (optional config rejected)

No real network calls are made; HTTP is intercepted.
"""
import base64
import io
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from .ai_model_catalog import get_model_capabilities
from .ai_transport import (
    payload_bytes,
    payload_byte_limit,
    PAYLOAD_BYTE_LIMITS,
    guarded_http,
    request_config,
)


def _png_b64():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode()


class ModelCatalogCapabilityTests(SimpleTestCase):
    """Cloudflare models must expose max_images so the request guard can act."""

    def test_cloudflare_text_model_has_zero_images(self):
        caps = get_model_capabilities('@cf/meta/llama-3.3-70b-instruct', 'cloudflare')
        self.assertFalse(caps['supports_vision'])
        self.assertEqual(caps['max_images'], 0)

    def test_cloudflare_glm_flash_is_text_only(self):
        caps = get_model_capabilities('@cf/zai-org/glm-4.7-flash', 'cloudflare')
        self.assertFalse(caps['supports_vision'])
        self.assertEqual(caps['max_images'], 0)

    def test_cloudflare_vision_model_capped_at_32(self):
        caps = get_model_capabilities('@cf/meta/llama-3.2-11b-vision-instruct', 'cloudflare')
        self.assertTrue(caps['supports_vision'])
        self.assertEqual(caps['max_images'], 32)

    def test_cloudflare_unknown_vision_heuristic_has_cap(self):
        # Unknown @cf/ model with a vision keyword must still get a numeric cap.
        caps = get_model_capabilities('@cf/some/new-vision-model', 'cloudflare')
        self.assertTrue(caps['supports_vision'])
        self.assertEqual(caps['max_images'], 32)

    def test_cloudflare_unknown_text_heuristic_blocks_images(self):
        caps = get_model_capabilities('@cf/some/new-text-model', 'cloudflare')
        self.assertFalse(caps['supports_vision'])
        self.assertEqual(caps['max_images'], 0)


class PayloadByteLimitTests(SimpleTestCase):
    """A hard byte ceiling must exist per provider and be enforced locally."""

    def test_openrouter_limit_is_7_5_mb(self):
        self.assertEqual(payload_byte_limit('openrouter'), 7_500_000)
        self.assertEqual(PAYLOAD_BYTE_LIMITS['openrouter'], 7_500_000)

    def test_unknown_provider_gets_default_limit(self):
        self.assertEqual(payload_byte_limit('mystery'), 20_000_000)

    def test_payload_bytes_counts_utf8(self):
        size = payload_bytes({'text': 'Привіт'})
        self.assertGreater(size, len('Привіт'))

    def test_guarded_http_blocks_oversized_payload(self):
        big = {'messages': [{'role': 'user', 'content': 'x' * 8_000_000}]}
        config = {'provider': 'openrouter', 'connection_id': '', 'model': 'x',
                  'deadline': 10 ** 12}

        @guarded_http
        def fake_post(url, payload, **kwargs):
            raise AssertionError('HTTP must not be called for oversized payload')

        token = request_config.set(config)
        try:
            status, data, message = fake_post('https://example.invalid', big)
        finally:
            request_config.reset(token)
        self.assertEqual(status, 413)
        self.assertTrue(data.get('_schoolnet_payload_too_large'))
        self.assertIn('перевищує', message)

    def test_guarded_http_allows_small_payload(self):
        config = {'provider': 'openrouter', 'connection_id': '', 'model': 'x',
                  'deadline': 10 ** 12}
        calls = []

        @guarded_http
        def fake_post(url, payload, **kwargs):
            calls.append(payload)
            return 200, {'ok': True}, ''

        # Quota reservation touches the DB; stub it so this stays a unit test.
        with patch('feed.ai_scheduler.reserve', return_value=(True, 0)), \
                patch('feed.ai_scheduler.finish'):
            token = request_config.set(config)
            try:
                status, data, message = fake_post('https://example.invalid', {'messages': []})
            finally:
                request_config.reset(token)
        self.assertEqual(status, 200)
        self.assertEqual(len(calls), 1)


class GeminiRecoveryTests(SimpleTestCase):
    """Gemini 400 'invalid argument' must progressively strip optional config."""

    def test_generic_400_strips_thinking_config(self):
        from . import gemini_service

        sent_payloads = []

        def fake_post(url, payload, headers=None, timeout=30):
            sent_payloads.append(json.loads(json.dumps(payload)))
            if 'thinkingConfig' in payload.get('generationConfig', {}):
                return 400, {'error': {'message': 'Request contains an invalid argument.'}}, \
                    'Request contains an invalid argument.'
            return 200, {'candidates': [{'content': {'parts': [{'text': '{"suggested_grade": 9}'}]},
                                         'finishReason': 'STOP'}]}, ''

        with patch.object(gemini_service, '_http_post_json', side_effect=fake_post):
            status, text, error, data = gemini_service._raw_call_ai_api(
                'prompt', system_prompt='sys', inline_media=None, provider='gemini',
                api_key='key', model_name='gemini-3.5-flash-lite', thinking_budget=0)

        self.assertEqual(status, 200)
        self.assertIsNone(error)
        # First attempt carried thinkingConfig, the retry did not.
        self.assertIn('thinkingConfig', sent_payloads[0]['generationConfig'])
        self.assertNotIn('thinkingConfig', sent_payloads[-1]['generationConfig'])


class DeterministicPayloadTests(SimpleTestCase):
    """Evaluation requests must carry a fixed seed where the provider supports it."""

    @patch('feed.gemini_service._http_post_json')
    def test_gemini_payload_contains_seed(self, post):
        from . import gemini_service
        post.return_value = (200, {'candidates': [{'content': {'parts': [{'text': '{"ok": true}'}]}}]}, '{"ok": true}')
        status, reply, error, _ = gemini_service._raw_call_ai_api(
            'synthetic prompt', provider='gemini', model_name='gemini-3.1-flash-lite', seed=42)
        self.assertEqual(status, 200)
        payload = post.call_args.args[1]
        self.assertEqual(payload['generationConfig']['seed'], 42)

    @patch('feed.gemini_service._http_post_json')
    def test_openai_compatible_payload_contains_seed(self, post):
        from . import gemini_service
        post.return_value = (200, {'choices': [{'message': {'content': '{"ok": true}'}}]}, '{"ok": true}')
        status, reply, error, _ = gemini_service._raw_call_ai_api(
            'synthetic prompt', provider='groq', model_name='qwen/qwen3.8-27b', seed=42)
        self.assertEqual(status, 200)
        payload = post.call_args.args[1]
        self.assertEqual(payload['seed'], 42)
