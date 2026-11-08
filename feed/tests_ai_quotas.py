"""Synthetic quota contracts: no real provider calls or learner materials."""
import io
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, SimpleTestCase
from . import tests_ai_connections as fixtures
from .ai_connections import save_connection
from .ai_quotas import apply_profile, credential_fingerprint, groq_limits, learn_groq_headers, read_provider_limits
from .ai_scheduler import reserve, finish, policy
from .models import AISettings, AIResourceState


class QuotaTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def install(self, provider='gemini', model='fast', key='first', group='shared', global_limits=None, profiles=None, fallback=None):
        identity = self.add_connection(provider, model, key=key)
        c = self.settings.get_connection(identity)
        c.update(quota_group=group, quota_limits=global_limits or {},
                 model_quota_limits=profiles or {'fast': {'rpm': 1}, 'lite': {'rpm': 2}},
                 quota_fallback_limits=fallback or {})
        self.settings.save()
        return {'provider': provider, 'connection_id': identity, 'model': model,
                'credential_fingerprint': credential_fingerprint(key), 'deadline': time.monotonic() + 60}

    def use(self, config, holder, incoming=10, outgoing=10):
        result = reserve(config, holder, incoming, outgoing)
        if result[0]:
            finish(config, holder)
        return result[0]

    def test_models_have_separate_windows(self):
        config = self.install()
        self.assertTrue(self.use(config, 'one'))
        self.assertFalse(self.use(config, 'two'))
        self.assertTrue(self.use(dict(config, model='lite'), 'three'))
        self.assertTrue(self.use(dict(config, model='lite'), 'four'))
        self.assertFalse(self.use(dict(config, model='lite'), 'five'))

    def test_two_keys_do_not_double_same_model_budget(self):
        first = self.install()
        second = self.install(key='second')
        self.assertTrue(self.use(first, 'one'))
        self.assertFalse(self.use(second, 'two'))

    def test_group_limit_also_applies_across_models(self):
        config = self.install(global_limits={'rpd': 1})
        self.assertTrue(self.use(config, 'one'))
        self.assertFalse(self.use(dict(config, model='lite'), 'two'))

    def test_input_tpm_does_not_charge_output(self):
        config = self.install(profiles={'fast': {'itpm': 50}})
        self.assertTrue(self.use(config, 'one', 25, 1000))
        self.assertTrue(self.use(config, 'two', 25, 1000))
        self.assertFalse(self.use(config, 'three', 1, 1))

    def test_unknown_model_uses_explicit_local_fallback(self):
        config = self.install(fallback={'rpd': 1})
        self.assertTrue(self.use(dict(config, model='unknown'), 'one'))
        self.assertFalse(self.use(dict(config, model='unknown'), 'two'))
        self.assertTrue(self.use(config, 'known'))

    def test_legacy_usage_is_not_refunded_on_upgrade(self):
        config = self.install()
        self.assertTrue(self.use(config, 'one'))
        state = AIResourceState.objects.get(key=policy(config)[0])
        del state.data['requests'][0]['model']
        state.save()
        self.assertFalse(self.use(config, 'two'))

    def test_model_prefix_normalized_and_stricter_shared_limit_wins(self):
        first = self.install(model='models/fast', profiles={'fast': {'rpm': 2}})
        self.install(key='second', profiles={'fast': {'rpm': 1}})
        self.assertTrue(self.use(first, 'one'))
        self.assertFalse(self.use(first, 'two'))

    def test_header_learning_is_persisted_and_never_raises_teacher_budget(self):
        config = self.install(provider='groq', profiles={'fast': {'rpd': 500}})
        self.assertTrue(learn_groq_headers(config, {'X-RateLimit-Limit-Requests': '1000', 'x-ratelimit-limit-tokens': '8000'}, 200))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.get_connection(config['connection_id'])['model_quota_limits']['fast'], {'rpd': 500, 'tpm': 8000})
        self.assertFalse(learn_groq_headers(config, {'x-ratelimit-limit-requests': '1000'}, 200))
        self.assertTrue(learn_groq_headers(config, {'x-ratelimit-limit-requests': '400'}, 429))

    def test_old_key_response_cannot_update_rotated_connection(self):
        config = self.install(provider='groq')
        self.settings.get_connection(config['connection_id'])['api_key'] = 'replacement'
        self.settings.save()
        self.assertFalse(learn_groq_headers(config, {'x-ratelimit-limit-tokens': '8000'}, 200))

    def test_label_save_keeps_model_profile_key_rotation_discards_verified_limits(self):
        config = self.install(provider='groq', global_limits={'rpd': 1000})
        self.settings.get_connection(config['connection_id'])['quota_provenance'] = {'api_observations': {'pool': {'limits': {'rpd': 1000}}}}
        self.settings.save()
        data = {'connection_id': config['connection_id'], 'connection_provider': 'groq', 'connection_label': 'Renamed'}
        result = save_connection(1, data)
        self.assertIn('model_quota_limits', result.get_connection(config['connection_id']))
        result = save_connection(1, dict(data, connection_key='new-key'))
        row = result.get_connection(config['connection_id'])
        self.assertNotIn('model_quota_limits', row)
        self.assertNotIn('rpd', row['quota_limits'])

    def test_command_preview_and_apply_with_public_output(self):
        self.install(provider='openrouter', model='openrouter/free', profiles={'fast': {'rpm': 2}})
        output = io.StringIO()
        observation = [{'provider': 'openrouter', 'model': None, 'limits': {'rpd': 1000}, 'source': 'provider_api'}]
        with patch('feed.management.commands.sync_ai_quotas.read_provider_limits', return_value=observation):
            call_command('sync_ai_quotas', stdout=output)
            self.settings.refresh_from_db()
            self.assertEqual(self.settings.provider_connections[0]['quota_limits'], {})
            call_command('sync_ai_quotas', apply=True, stdout=output)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.provider_connections[0]['quota_limits']['rpd'], 1000)
        self.assertNotIn('first', output.getvalue())
        self.assertNotIn('api_key', output.getvalue())

    def test_openrouter_free_limit_not_attached_to_paid_route(self):
        self.install(provider='openrouter', model='paid/model')
        with patch('feed.management.commands.sync_ai_quotas.read_provider_limits', return_value=[{'provider': 'openrouter', 'model': None, 'limits': {'rpd': 1000}, 'source': 'provider_api'}]):
            call_command('sync_ai_quotas', apply=True, stdout=io.StringIO())
        self.settings.refresh_from_db()
        self.assertNotIn('rpd', self.settings.provider_connections[0]['quota_limits'])

    def test_command_rejects_concurrent_changes(self):
        self.install(provider='groq')
        def edit(*args, **kwargs):
            AISettings.objects.filter(pk=1).update(provider_connections=[])
            return []
        with patch('feed.management.commands.sync_ai_quotas.read_provider_limits', side_effect=edit):
            with self.assertRaises(CommandError):
                call_command('sync_ai_quotas', apply=True, stdout=io.StringIO())

    def test_reapplying_profile_offline_keeps_verified_and_stricter_limits(self):
        old = [{'provider': 'groq', 'quota_limits': {'rpm': 10},
                'model_quota_limits': {'fast': {'rpd': 400}},
                'quota_provenance': {'api_observations': {'fast': {'limits': {'rpd': 1000}}}}}]
        profile = {'providers': {'groq': {'quota_limits': {'rpm': 30}, 'model_quota_limits': {}}}}
        updated = apply_profile(old, profile)[0]
        self.assertEqual(updated['quota_limits']['rpm'], 10)
        self.assertEqual(updated['model_quota_limits']['fast']['rpd'], 400)
        self.assertIn('api_observations', updated['quota_provenance'])

    def test_pool_response_learns_model_quota_through_transport_context(self):
        from .ai_transport import request_config
        from .gemini_service import _http_post_json
        config = self.install(provider='groq')
        response = MagicMock(status=200, headers={'x-ratelimit-limit-tokens': '8000'})
        response.read1.side_effect = [b'{}', b'']
        token = request_config.set(config)
        try:
            with patch('feed.gemini_service._get_http_pool') as pool:
                pool.return_value.request.return_value = response
                self.assertEqual(_http_post_json('https://api.groq.com/openai/v1/chat/completions', {})[0], 200)
        finally:
            request_config.reset(token)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.provider_connections[0]['model_quota_limits']['fast']['tpm'], 8000)

    def test_fallback_response_learns_model_quota_through_transport_context(self):
        from .ai_transport import request_config
        from .gemini_service import _http_post_json
        config = self.install(provider='groq')
        token = request_config.set(config)
        try:
            with patch('feed.gemini_service._get_http_pool', return_value=False), patch('feed.gemini_service.urllib.request.urlopen') as opening:
                response = opening.return_value.__enter__.return_value
                response.getcode.return_value = 200
                response.headers = {'x-ratelimit-limit-requests': '1000'}
                response.read1.side_effect = [b'{}', b'']
                self.assertEqual(_http_post_json('https://api.groq.com/openai/v1/chat/completions', {})[0], 200)
        finally:
            request_config.reset(token)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.provider_connections[0]['model_quota_limits']['fast']['rpd'], 1000)

    def test_public_projection_excludes_credentials_and_raw_metadata(self):
        self.install()
        public = json.dumps(self.settings.public_connections())
        self.assertNotIn('api_key', public)
        self.assertNotIn('credential_fingerprint', public)
        self.assertIn('quota_model_rows', public)


class QuotaParsingTests(SimpleTestCase):
    def test_groq_headers_are_daily_requests_and_minute_tokens(self):
        self.assertEqual(groq_limits({'X-RateLimit-Limit-Requests': '1000', 'X-RateLimit-Limit-Tokens': '8000'}), {'rpd': 1000, 'tpm': 8000})
        self.assertEqual(groq_limits({'x-ratelimit-limit-requests': '-1', 'x-ratelimit-limit-tokens': 'oops'}), {})

    def test_profile_rejects_bad_values_without_mutating_connections(self):
        original = [{'provider': 'gemini', 'api_key': 'synthetic'}]
        for value in (True, 0, -1, 10**10, '5'):
            with self.assertRaises(ValueError):
                apply_profile(original, {'providers': {'gemini': {'quota_limits': {'rpm': value}}}})
        self.assertEqual(original, [{'provider': 'gemini', 'api_key': 'synthetic'}])

    def test_supplied_gemini_caps_are_denominators_and_input_only(self):
        profile = json.loads((Path(__file__).resolve().parent.parent / 'config/ai_quota_profile.json').read_text())
        c = apply_profile([{'provider': p} for p in profile['providers']], profile)[0]
        self.assertEqual(c['model_quota_limits']['gemini-3.8-flash'], {'rpm': 5, 'rpd': 20, 'itpm': 250000})
        self.assertEqual(c['model_quota_limits']['gemini-3.5-flash-lite']['rpd'], 500)

    def test_openrouter_only_reads_actual_free_request_limit(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Response(json.dumps({'data': {'limit': 999999, 'rate_limit': {'requests': 200}, 'free_model_daily_requests': {'limit': 1000}}}).encode())
        opener = Opener()
        result = read_provider_limits({'provider': 'openrouter', 'api_key': 'secret', 'custom_url': 'https://evil.invalid'}, [], opener=opener)
        self.assertEqual(opener.request.full_url, 'https://openrouter.ai/api/v1/key')
        self.assertEqual(result[0]['limits'], {'rpd': 1000})
        self.assertNotIn('secret', json.dumps(result))

    def test_groq_probe_requires_explicit_flag(self):
        self.assertEqual(read_provider_limits({'provider': 'groq'}, ['model']), [])
