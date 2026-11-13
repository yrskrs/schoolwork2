"""Synthetic end-to-end contracts. HTTP is intercepted, no real keys or work."""
import base64
import io
import json
from uuid import uuid4
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from . import tests_ai_connections as fixtures
from .models import AIJob, AIBatch, AIResourceState, Submission, StudentAICheckReservation
from .ai_jobs import claim_next_job, execute_job, enqueue_submission_job, defer_job
from .ai_privacy import Redactor, PrivacyBlocked, _current, for_submission
from .ai_scheduler import acquire, release, busy, reserve, finish
from .ai_transport import guarded_http, request_config, payload_budget
from .gemini_service import call_ai_api, evaluate_submission_with_gemini


def picture():
    buffer = io.BytesIO()
    image = Image.new('RGB', (32, 24), 'blue')
    image.save(buffer, format='PNG')
    return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(), 'source': 'Синтетичний файл'}


class ParallelPrivacyTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection
    answer = fixtures.AIConnectionsTests.answer

    def job(self, kind='teacher_check'):
        return AIJob.objects.create(kind=kind, submission=self.sub, requested_by=self.user)

    def extra_submission(self, index):
        return Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
            class_group=self.group, first_name='Синтетичний', last_name=f'Учасник{index}')

    def config(self, provider='gemini', **limits):
        identity = self.add_connection(provider, 'synthetic-model', **{'quota_' + k: str(v) for k, v in limits.items()})
        return next(c for c in self.settings.get_request_configs() if c['connection_id'] == identity)

    def test_slot_owner_expiry_and_connection_isolation(self):
        with patch('feed.ai_scheduler.time.time', return_value=100):
            self.assertTrue(acquire('gemini', 'model', 'old-owner', 10, 'a'))
            self.assertFalse(acquire('gemini', 'model', 'other-owner', 10, 'a'))
            self.assertTrue(acquire('gemini', 'model', 'other-owner', 10, 'b'))
            release('gemini', 'model', 'other-owner', 'a')
            self.assertTrue(busy('gemini', 'model', 'a'))
        with patch('feed.ai_scheduler.time.time', return_value=111):
            self.assertTrue(acquire('gemini', 'model', 'new-owner', 10, 'a'))
            release('gemini', 'model', 'old-owner', 'a')
            self.assertTrue(busy('gemini', 'model', 'a'))

    def test_distinct_providers_run_while_shared_provider_group_waits(self):
        first = self.config('gemini')
        second = self.config('groq')
        third = self.config('gemini')
        self.assertTrue(reserve(first, 'a', 100, 100)[0])
        self.assertTrue(reserve(second, 'b', 100, 100)[0])
        self.assertFalse(reserve(third, 'c', 100, 100)[0])
        finish(first, 'a')
        self.assertTrue(reserve(third, 'c', 100, 100)[0])

    def test_rolling_rpm_and_actual_token_reconciliation(self):
        config = self.config(rpm=1, tpm=1000)
        with patch('feed.ai_scheduler.time.time', return_value=100):
            self.assertTrue(reserve(config, 'a', 500, 400)[0])
            finish(config, 'a', {'prompt_tokens': 100, 'completion_tokens': 20, 'total_tokens': 120})
            self.assertFalse(reserve(config, 'b', 1, 1)[0])
        with patch('feed.ai_scheduler.time.time', return_value=161):
            self.assertTrue(reserve(config, 'c', 500, 400)[0])
            finish(config, 'c')
            self.assertEqual(reserve(config, 'too-large', 1001, 1), (False, -1))

    def test_provider_retry_after_is_respected_without_calling_http_again(self):
        import time
        config = dict(self.config(), deadline=time.monotonic() + 60)
        calls = []
        @guarded_http
        def fake(url, payload, **kwargs):
            calls.append(payload)
            return 429, {'_schoolnet_retry_after': 75}, 'busy'
        token = request_config.set(config)
        try:
            fake('https://synthetic.invalid', {'messages': [{'content': 'test'}]})
            response = fake('https://synthetic.invalid', {'messages': [{'content': 'test'}]})
        finally:
            request_config.reset(token)
        self.assertEqual(len(calls), 1)
        self.assertGreater(response[1]['_schoolnet_deferred'], 60)

    def test_compatibility_retries_each_reserve_their_own_request(self):
        import time
        config = dict(self.config(rpm=1), deadline=time.monotonic() + 60)
        calls = []
        @guarded_http
        def fake(url, payload, **kwargs):
            calls.append(1)
            return 400, {}, 'unsupported parameter'
        token = request_config.set(config)
        try:
            fake('https://synthetic.invalid', {})
            second = fake('https://synthetic.invalid', {})
        finally:
            request_config.reset(token)
        self.assertEqual(calls, [1])
        self.assertTrue(second[1]['_schoolnet_local_preflight'])

    @override_settings(AI_WORKER_CONCURRENCY=3, AI_STUDENT_RESERVED=1)
    def test_worker_reserves_student_capacity(self):
        jobs = [AIJob.objects.create(kind='batch_check', submission=self.extra_submission(i)) for i in range(3)]
        self.assertIsNotNone(claim_next_job())
        self.assertIsNotNone(claim_next_job())
        self.assertIsNone(claim_next_job())
        student = self.job('student_check')
        self.assertEqual(claim_next_job().pk, student.pk)
        self.assertIsNone(claim_next_job())

    def test_three_student_claims_then_teacher_gets_a_turn(self):
        teacher = self.job()
        for index in range(4):
            AIJob.objects.create(kind='student_check', submission=self.extra_submission(index))
        for index in range(3):
            job = claim_next_job()
            self.assertEqual(job.kind, 'student_check')
            AIJob.objects.filter(pk=job.pk).update(status='succeeded')
        self.assertEqual(claim_next_job().pk, teacher.pk)

    @patch('feed.views._evaluate_student_submission')
    def test_deferral_does_not_consume_student_attempt(self, evaluate):
        self.assignment.allow_student_ai_check = True
        self.assignment.save()
        with override_settings(AI_JOBS_EAGER=False):
            job = enqueue_submission_job(self.sub, 'student_check')
        evaluate.return_value = {'status': 'deferred', 'retry_after': 30}
        execute_job(job.pk)
        job.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertGreater(job.not_before, timezone.now())
        self.assertFalse(self.sub.student_ai_checked)
        self.assertFalse(StudentAICheckReservation.objects.filter(job=job, used=True).exists())
        self.assertIsNone(claim_next_job())

    def test_text_redaction_keeps_solution_and_removes_private_query(self):
        redactor = for_submission(self.sub)
        text = redactor.text(f'{self.sub.get_student_full_name()}: x = 2 + 3; пошта student@example.org; '
                            '+380 67 123 45 67; https://example.org/work?token=private-value')
        self.assertNotIn(self.sub.get_student_full_name(), text)
        self.assertNotIn('student@example.org', text)
        self.assertNotIn('private-value', text)
        self.assertNotIn('+380', text)
        self.assertIn('x = 2 + 3', text)

    @patch('feed.gemini_service._http_post_json')
    def test_strict_mode_allows_clean_image_without_embedded_data(self, post):
        # Strict mode must not block a plain image merely because it is visual.
        post.return_value = 200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
                                  'choices': [{'message': {'content': '{}'}}]}, ''
        token = _current.set(Redactor('strict'))
        try:
            response = call_ai_api('test', inline_media=[picture()], provider='gemini', model_name='synthetic')
        finally:
            _current.reset(token)
        self.assertFalse(response[3].get('_schoolnet_privacy_blocked'))
        post.assert_called_once()

    @patch('feed.gemini_service._http_post_json')
    def test_strict_mode_blocks_image_with_embedded_personal_data(self, post):
        # A real personal-data finding (EXIF author) is what strict mode blocks.
        buffer = io.BytesIO()
        image = Image.new('RGB', (32, 24), 'blue')
        exif = image.getexif()
        exif[315] = 'Іван Петренко'  # Artist tag
        image.save(buffer, format='PNG', exif=exif)
        media = {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(), 'source': 'Синтетичний файл'}
        token = _current.set(Redactor('strict'))
        try:
            response = call_ai_api('test', inline_media=[media], provider='gemini', model_name='synthetic')
        finally:
            _current.reset(token)
        self.assertTrue(response[3]['_schoolnet_privacy_blocked'])
        post.assert_not_called()

    @patch('feed.gemini_service._http_post_json')
    def test_redact_mode_allows_image_with_embedded_data_after_stripping(self, post):
        # Redact mode strips metadata and still sends the image.
        post.return_value = 200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
                                  'choices': [{'message': {'content': '{}'}}]}, ''
        buffer = io.BytesIO()
        image = Image.new('RGB', (32, 24), 'blue')
        exif = image.getexif()
        exif[315] = 'Іван Петренко'
        image.save(buffer, format='PNG', exif=exif)
        media = {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(), 'source': 'Синтетичний файл'}
        token = _current.set(Redactor('redact'))
        try:
            response = call_ai_api('test', inline_media=[media], provider='gemini', model_name='synthetic')
        finally:
            _current.reset(token)
        self.assertFalse(response[3].get('_schoolnet_privacy_blocked'))
        post.assert_called_once()

    @patch('feed.gemini_service._http_post_json')
    def test_every_provider_gets_redacted_serialized_text(self, post):
        post.return_value = 200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}}],
                                  'choices': [{'message': {'content': '{}'}}]}, ''
        token = _current.set(for_submission(self.sub, 'redact'))
        try:
            for provider in ('gemini', 'groq', 'openrouter', 'openai', 'custom', 'deepseek', 'cloudflare'):
                call_ai_api(self.sub.get_student_full_name() + ' student@example.org x=5',
                    provider=provider, model_name='synthetic', api_key='synthetic')
                sent = json.dumps(post.call_args.args[1], ensure_ascii=False)
                self.assertNotIn(self.sub.get_student_full_name(), sent)
                self.assertNotIn('student@example.org', sent)
                self.assertIn('x=5', sent)
        finally:
            _current.reset(token)

    @patch('feed.gemini_service.call_ai_api')
    def test_all_three_teacher_fields_and_resubmission_context_survive(self, call):
        self.add_connection('gemini', 'synthetic')
        self.assignment.description = 'Створи програму. TASK_SENTINEL'
        self.assignment.custom_criteria = 'CRITERIA_SENTINEL'
        self.assignment.teacher_ai_instructions = 'TEACHER_SENTINEL'
        self.assignment.save()
        previous = self.extra_submission(1)
        previous.teacher_comment = 'PREVIOUS_SENTINEL'
        previous.save()
        self.sub.previous_submission = previous
        self.sub.is_resubmission = True
        self.sub.resubmission_attempt = 2
        self.sub.save()
        call.return_value = self.answer()
        evaluate_submission_with_gemini(self.sub)
        prompt = call.call_args.kwargs['prompt_text']
        for marker in ('TASK_SENTINEL', 'CRITERIA_SENTINEL', 'TEACHER_SENTINEL', 'PREVIOUS_SENTINEL'):
            self.assertIn(marker, prompt)

    @patch('feed.gemini_service.call_ai_api')
    def test_origin_claim_does_not_lower_content_grade(self, call):
        self.add_connection('gemini', 'synthetic')
        self.assignment.allow_ai_usage = False
        self.assignment.save()
        answer = json.loads(self.answer()[1])
        answer.update(ai_generated_percent=95, ai_generated_detected=True,
            ai_generated_details='Припущення про стиль.', ai_generated_confidence='high')
        call.return_value = 200, json.dumps(answer), '', {}
        result = evaluate_submission_with_gemini(self.sub)
        # P1-9: model-only detected/percent не є доказом (evidence-gate), тому
        # confidence='none', а змістовий бал не залежить від припущення про ШІ.
        self.assertEqual(result['suggested_grade'], '9')
        self.assertEqual(result['ai_generated_confidence'], 'none')
        self.assertFalse(result['ai_generated_detected'])

    @patch('feed.gemini_service.call_ai_api')
    def test_unverifiable_gr_blocks_numeric_grade(self, call):
        """An unconfirmed active GR must withhold the numeric grade entirely."""
        from .models import AICriteriaPreset
        preset = AICriteriaPreset.objects.create(
            name='НУШ ГР синтетична', evaluation_type='nus_gr',
            gr_definitions=json.dumps([{'code': 'ГР 1', 'name': 'Опрацювання інформації'}]))
        self.assignment.default_ai_preset = preset
        self.assignment.save()
        self.add_connection('gemini', 'synthetic')
        answer = json.loads(self.answer()[1])
        answer.update(gr_results=[{'code': 'ГР 1', 'name': 'Опрацювання інформації',
            'grade': '', 'status': 'unverifiable', 'comment': 'Немає доказів.'}])
        call.return_value = 200, json.dumps(answer), '', {}
        result = evaluate_submission_with_gemini(self.sub)
        self.assertTrue(result['assessment_blocked'])
        self.assertIsNone(result['suggested_grade'])
        self.assertIn('ГР 1', ' '.join(result['blocked_reasons']))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.ai_suggested_grade, '')
        self.assertEqual(self.sub.ai_score_level, '')

    @override_settings(AI_JOBS_EAGER=False)
    def test_batch_is_idempotent_restorable_and_cancels_only_queued_jobs(self):
        other = self.extra_submission(1)
        identity = str(uuid4())
        data = {'request_id': identity, 'submission_ids': [self.sub.pk, other.pk]}
        response = self.client.post(reverse('ai_batch_create'), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pending'], 2)
        self.assertEqual(self.client.post(reverse('ai_batch_create'), data).status_code, 200)
        self.assertEqual(AIBatch.objects.count(), 1)
        self.assertEqual(AIJob.objects.count(), 2)
        self.assertEqual(self.client.get(reverse('ai_batch_latest')).json()['batch_id'], identity)
        running = AIJob.objects.first()
        running.status = 'running'
        running.save()
        self.client.post(reverse('ai_batch_cancel', args=[identity]))
        running.refresh_from_db()
        self.assertEqual(running.status, 'running')
        self.assertEqual(AIJob.objects.filter(status='cancelled').count(), 1)

    @override_settings(AI_JOBS_EAGER=False)
    def test_batch_access_is_owner_scoped_and_validation_is_atomic(self):
        identity = str(uuid4())
        data = {'request_id': identity, 'submission_ids': [self.sub.pk], 'preset_id': 'invalid'}
        self.assertEqual(self.client.post(reverse('ai_batch_create'), data).status_code, 400)
        self.assertFalse(AIBatch.objects.exists())
        self.assertFalse(AIJob.objects.exists())
        data.pop('preset_id')
        self.client.post(reverse('ai_batch_create'), data)
        stranger = User.objects.create_superuser('stranger', password='synthetic')
        from .models import Teacher
        Teacher.objects.create(user=stranger, full_name='Інший учитель')
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(reverse('ai_batch_status', args=[identity])).status_code, 404)
        self.assertEqual(self.client.post(reverse('ai_batch_cancel', args=[identity])).status_code, 404)

    @patch('feed.gemini_service._http_post_json')
    def test_generic_bad_request_does_not_trigger_three_blind_retries(self, post):
        post.return_value = 400, {'error': {'message': 'invalid model'}}, 'invalid model'
        call_ai_api('test', 'rules', provider='gemini', model_name='synthetic', json_mode=True, thinking_budget=0)
        self.assertEqual(post.call_count, 1)

    def test_preparation_is_local_and_does_not_create_student_reservation(self):
        from .ai_preparation import queue_after_commit
        with override_settings(AI_JOBS_EAGER=False), self.captureOnCommitCallbacks(execute=True):
            queue_after_commit('submission', self.sub.pk)
            queue_after_commit('submission', self.sub.pk)
        self.assertEqual(AIJob.objects.filter(kind='prepare').count(), 1)
        self.assertFalse(StudentAICheckReservation.objects.exists())
        with patch('feed.gemini_service.call_ai_api') as api:
            execute_job(AIJob.objects.get(kind='prepare').pk)
        api.assert_not_called()
        self.assertEqual(AIJob.objects.get(kind='prepare').status, 'succeeded')

    def test_payload_estimate_does_not_tokenize_base64_as_text(self):
        count, output = payload_budget({'messages': [{'content': [{'type': 'text', 'text': 'x=5'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + 'A' * 100000}}]}], 'max_tokens': 200})
        self.assertLess(count, 3000)
        self.assertEqual(output, 200)

    def test_public_query_is_preserved_but_encoded_contacts_are_redacted(self):
        redactor = Redactor()
        public = 'https://example.org/search?q=math&page=2'
        self.assertEqual(redactor.text(public), public)
        private = redactor.text('https://example.org/search?q=student%40example.org&token=private-access-token')
        self.assertNotIn('student', private)
        self.assertNotIn('private-access-token', private)

    def test_daily_quota_is_conservative_across_midnight(self):
        config = self.config(rpd=1)
        with patch('feed.ai_scheduler.time.time', return_value=86399):
            self.assertTrue(reserve(config, 'a', 10, 10)[0])
            finish(config, 'a')
        with patch('feed.ai_scheduler.time.time', return_value=86401):
            allowed, wait = reserve(config, 'b', 10, 10)
            self.assertFalse(allowed)
            self.assertGreater(wait, 86000)

    @patch('feed.gemini_service._http_post_json')
    def test_rejected_thinking_option_is_remembered_for_next_request(self, post):
        from django.core.cache import caches
        caches['ai_materials'].clear()
        good = (200, {'candidates': [{'content': {'parts': [{'text': '{}'}]}}]}, '')
        post.side_effect = [(400, {}, 'thinkingConfig is not supported'), good, good]
        for repeat in range(2):
            response = call_ai_api('test', provider='gemini', model_name='compat-synthetic', thinking_budget=0)
            self.assertEqual(response[0], 200)
        self.assertEqual(post.call_count, 3)
        self.assertNotIn('thinkingConfig', post.call_args.args[1]['generationConfig'])

    @patch('feed.gemini_service.call_ai_api')
    def test_received_429_defers_student_job_without_consuming_attempt(self, call):
        self.add_connection('gemini', 'synthetic')
        self.assignment.allow_student_ai_check = True
        self.assignment.save()
        with override_settings(AI_JOBS_EAGER=False):
            job = enqueue_submission_job(self.sub, 'student_check')
        call.return_value = 429, None, 'Synthetic rate limit', {'_schoolnet_retry_after': 73}
        execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertGreater((job.not_before - timezone.now()).total_seconds(), 70)
        self.assertFalse(job.reservations.filter(used=True).exists())

    def test_local_preflight_is_refunded_and_oversize_is_not_deferred_forever(self):
        import time
        config = dict(self.config(rpm=1, tpm=1000), deadline=time.monotonic() + 60)
        @guarded_http
        def fake(url, payload, **kwargs):
            return 413, {'_schoolnet_local_preflight': True}, 'local size check'
        token = request_config.set(config)
        try:
            for repeat in range(2):
                self.assertEqual(fake('https://synthetic.invalid', {'max_tokens': 100})[0], 413)
            response = fake('https://synthetic.invalid', {'max_tokens': 2000})
        finally:
            request_config.reset(token)
        self.assertEqual(response[0], 422)
        self.assertFalse(response[1]['_schoolnet_deferred'])

    def test_cancelled_batch_cannot_requeue_its_running_job(self):
        batch = AIBatch.objects.create(requested_by=self.user, cancelled_at=timezone.now())
        job = self.job('batch_check')
        job.batch = batch
        job.status = 'running'
        job.save()
        defer_job(job.pk, 5)
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')

    def test_batch_permission_checks_have_bounded_query_count(self):
        from types import SimpleNamespace
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from .ai_batches import snapshot
        batch = AIBatch.objects.create(requested_by=self.user)
        for index in range(20):
            AIJob.objects.create(kind='batch_check', batch=batch, submission=self.extra_submission(index))
        with CaptureQueriesContext(connection) as queries:
            response = snapshot(SimpleNamespace(user=self.user), batch)
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 4)

    def test_interrupted_job_releases_only_owned_slots_and_preserves_usage(self):
        from .ai_jobs import fail_job
        config = self.config()
        job = self.job()
        owner = str(job.pk) + ':request'
        self.assertTrue(acquire('gemini', 'model', owner, 600, config['connection_id']))
        self.assertTrue(acquire('groq', 'other', 'unrelated-owner', 600, 'other'))
        self.assertTrue(reserve(config, owner, 100, 100)[0])
        fail_job(job.pk, 'Synthetic interruption')
        self.assertFalse(busy('gemini', 'model', config['connection_id']))
        self.assertTrue(busy('groq', 'other', 'other'))
        from .ai_scheduler import policy
        data = AIResourceState.objects.get(pk=policy(config)[0]).data
        self.assertFalse(data['leases'])
        self.assertEqual(len(data['requests']), 1)

    @patch('feed.gemini_service.call_ai_api')
    def test_disabled_module_stops_previously_enqueued_assessment(self, call):
        self.add_connection('gemini', 'synthetic')
        job = self.job()
        self.settings.is_enabled = False
        self.settings.save(update_fields=['is_enabled'])
        execute_job(job.pk)
        job.refresh_from_db()
        call.assert_not_called()
        self.assertEqual(job.status, 'failed')
        self.assertEqual(job.http_status, 403)

    def test_parallel_pdf_preparation_converts_shared_document_only_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path
        import time
        from .ai_context import media_for_provider

        def convert(command, **kwargs):
            time.sleep(.05)
            Image.new('RGB', (16, 16), 'white').save(str(command[-1]) + '-1.jpg', format='JPEG')
        media = [{'mime_type': 'application/pdf', 'data': base64.b64encode(b'synthetic-pdf-for-converter-mock').decode(),
                  'source': 'Спільний матеріал'}]
        with patch('feed.ai_context.subprocess.run', side_effect=convert) as process:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda index: media_for_provider(media, 'groq'), range(4)))
        self.assertEqual(process.call_count, 1)
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(len(results[0]), 1)
