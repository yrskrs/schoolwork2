from unittest.mock import patch
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.cache import caches

from feed.models import Teacher, ClassGroup, Subject, Assignment, Submission
from feed.ai_concurrency import (
    acquire_model_slot,
    release_model_slot,
    is_model_busy,
    set_model_cooldown,
    get_model_cooldown,
    clear_model_cooldown,
    record_model_context_limit,
    get_model_context_limit,
    clear_model_context_limit,
    is_context_limit_error,
    is_overload_error,
)
from feed.gemini_service import (
    get_provider_endpoint,
    DEFAULT_MODELS_BY_PROVIDER,
)
from feed.ai_model_catalog import infer_model_provider, get_model_capabilities


class CloudflareAndConcurrencyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='teacher1', password='password123', is_staff=True, is_superuser=True, first_name='Олена', last_name='Коваль')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Олена Коваль')
        self.class_9a = ClassGroup.objects.create(name='9-А', grade=9, letter='А')
        self.subject = Subject.objects.create(name='Інформатика')
        self.teacher.classes.add(self.class_9a)
        self.teacher.subjects.add(self.subject)
        self.client.force_login(self.user)

    def test_cloudflare_endpoint_resolution(self):
        # 1. Custom URL format with Account ID
        url, headers, model = get_provider_endpoint(
            provider='cloudflare',
            model_name='@cf/meta/llama-3.3-70b-instruct',
            api_key='secret-cf-token',
            custom_url='acc123456'
        )
        self.assertIn('acc123456', url)
        self.assertIn('chat/completions', url)
        self.assertEqual(headers.get('Authorization'), 'Bearer secret-cf-token')
        self.assertEqual(model, '@cf/meta/llama-3.3-70b-instruct')

        # 2. Key with account_id:token format
        url2, headers2, model2 = get_provider_endpoint(
            provider='cloudflare',
            model_name=DEFAULT_MODELS_BY_PROVIDER['cloudflare'][0],
            api_key='my_cf_acc:token999',
            custom_url=''
        )
        self.assertIn('my_cf_acc', url2)
        self.assertEqual(headers2.get('Authorization'), 'Bearer token999')

        # 3. Provider catalog inference
        self.assertEqual(infer_model_provider('@cf/meta/llama-3.3-70b-instruct'), 'cloudflare')
        self.assertEqual(infer_model_provider('@cf/deepseek-ai/deepseek-r1-distill-qwen-32b'), 'cloudflare')

    def test_model_concurrency_slots(self):
        model_name = 'test-concurrent-model'
        provider = 'groq'

        # Initial state: not busy
        self.assertFalse(is_model_busy(provider, model_name))

        # Acquire slot
        acquired = acquire_model_slot(provider, model_name, timeout=10)
        self.assertTrue(acquired)
        self.assertTrue(is_model_busy(provider, model_name))

        # Second acquire should fail because slot is busy
        acquired_second = acquire_model_slot(provider, model_name, timeout=10)
        self.assertFalse(acquired_second)

        # Release slot
        release_model_slot(provider, model_name)
        self.assertFalse(is_model_busy(provider, model_name))

    def test_model_cooldown_behavior(self):
        model_name = 'test-cooldown-model'
        provider = 'cloudflare'

        # Initially no cooldown
        is_cd, rem, _ = get_model_cooldown(provider, model_name)
        self.assertFalse(is_cd)
        self.assertEqual(rem, 0)

        # Set cooldown
        set_model_cooldown(provider, model_name, duration_seconds=60, reason='Rate limit 429')
        is_cd, remaining, reason = get_model_cooldown(provider, model_name)
        self.assertTrue(is_cd)
        self.assertGreater(remaining, 0)
        self.assertIn('429', reason)

        # Clear cooldown
        clear_model_cooldown(provider, model_name)
        is_cd, rem, _ = get_model_cooldown(provider, model_name)
        self.assertFalse(is_cd)
        self.assertEqual(rem, 0)

    def test_context_limit_error_detection_and_memory(self):
        # Error detection
        self.assertTrue(is_context_limit_error(413, 'Request entity too large'))
        self.assertTrue(is_context_limit_error(400, 'Context length exceeded: maximum context is 8192 tokens'))
        self.assertFalse(is_context_limit_error(401, 'Authentication failed'))

        # Overload error detection
        self.assertTrue(is_overload_error(429, 'Rate limit exceeded'))
        self.assertTrue(is_overload_error(503, 'Service Unavailable / Model overloaded'))
        self.assertFalse(is_overload_error(200, 'Invalid JSON response'))

        # Memory of context limit
        model_name = 'context-limited-model'
        provider = 'openai'
        clear_model_context_limit(provider, model_name)
        self.assertIsNone(get_model_context_limit(provider, model_name))

        record_model_context_limit(provider, model_name, error_message='Payload exceeds 16k context window')
        limit_data = get_model_context_limit(provider, model_name)
        self.assertIsNotNone(limit_data)
        self.assertIn('Payload exceeds', limit_data.get('error', ''))

        # Capabilities should reflect recorded limit
        caps = get_model_capabilities(model_name, provider=provider)
        self.assertTrue(caps.get('is_context_flagged'))

    def test_conducted_lessons_tab(self):
        # Create published lesson
        a1 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Основи програмування на Python',
            status=Assignment.STATUS_PUBLISHED
        )
        a1.classes.add(self.class_9a)

        # Create archived lesson
        a2 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Алгоритми сортування (Минулий семестр)',
            status=Assignment.STATUS_ARCHIVED
        )
        a2.classes.add(self.class_9a)

        # Create draft lesson (should NOT be included in conducted lessons)
        a3 = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Чернетка майбутнього уроку',
            status=Assignment.STATUS_DRAFT
        )
        a3.classes.add(self.class_9a)

        response = self.client.get('/teacher/students/?tab=lessons')
        self.assertEqual(response.status_code, 200)

        # Context checks
        self.assertEqual(response.context['current_tab'], 'lessons')
        self.assertEqual(response.context['total_conducted_lessons_count'], 2)
        self.assertEqual(response.context['published_conducted_lessons_count'], 1)
        self.assertEqual(response.context['archived_conducted_lessons_count'], 1)

        titles = [l['title'] for l in response.context['conducted_lessons']]
        self.assertIn('Основи програмування на Python', titles)
        self.assertIn('Алгоритми сортування (Минулий семестр)', titles)
        self.assertNotIn('Чернетка майбутнього уроку', titles)

        # Search test
        resp_search = self.client.get('/teacher/students/?tab=lessons&search=сортування')
        self.assertEqual(resp_search.status_code, 200)
        self.assertEqual(len(resp_search.context['conducted_lessons']), 1)
        self.assertEqual(resp_search.context['conducted_lessons'][0]['title'], 'Алгоритми сортування (Минулий семестр)')

        # HTML content check
        self.assertContains(response, 'Журнал проведених тем уроків')
        self.assertContains(response, 'Основи програмування на Python')
        self.assertContains(response, 'Алгоритми сортування (Минулий семестр)')
