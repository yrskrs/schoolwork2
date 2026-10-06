from django.test import SimpleTestCase
from feed.ai_model_catalog import get_model_capabilities, infer_model_provider


class AIModelCatalogTests(SimpleTestCase):
    def test_known_gemini_model_capabilities(self):
        caps = get_model_capabilities('gemini-3.8-flash', 'gemini')
        self.assertTrue(caps['supports_vision'])
        self.assertGreaterEqual(caps['context_tokens'], 1000000)

    def test_text_only_models_identified(self):
        o3_caps = get_model_capabilities('o3-mini', 'openai')
        self.assertFalse(o3_caps['supports_vision'])

        ds_caps = get_model_capabilities('deepseek-flash', 'deepseek')
        self.assertFalse(ds_caps['supports_vision'])

        groq_text = get_model_capabilities('openai/gpt-oss-120b', 'groq')
        self.assertFalse(groq_text['supports_vision'])

    def test_multimodal_models_identified(self):
        gpt4o = get_model_capabilities('gpt-4o', 'openai')
        self.assertTrue(gpt4o['supports_vision'])

        qwen = get_model_capabilities('qwen/qwen3.8-27b', 'groq')
        self.assertTrue(qwen['supports_vision'])

        cf_vision = get_model_capabilities('@cf/meta/llama-3.2-11b-vision-instruct', 'cloudflare')
        self.assertTrue(cf_vision['supports_vision'])

    def test_infer_model_provider(self):
        self.assertEqual(infer_model_provider('gemini-3.8-flash'), 'gemini')
        self.assertEqual(infer_model_provider('deepseek-chat'), 'deepseek')
        self.assertEqual(infer_model_provider('gpt-4.1-mini'), 'openai')
        self.assertEqual(infer_model_provider('llama-3.3-70b-versatile'), 'groq')
        self.assertEqual(infer_model_provider('deepseek/deepseek-chat'), 'openrouter')
        self.assertEqual(infer_model_provider('@cf/meta/llama-3.3-70b-instruct'), 'cloudflare')

    def test_all_providers_in_catalog(self):
        from feed.ai_model_catalog import PROVIDER_CATALOG
        providers = [group['provider'] for group in PROVIDER_CATALOG]
        self.assertIn('gemini', providers)
        self.assertIn('openai', providers)
        self.assertIn('deepseek', providers)
        self.assertIn('groq', providers)
        self.assertIn('openrouter', providers)
        self.assertIn('cloudflare', providers)
        self.assertIn('custom', providers)

