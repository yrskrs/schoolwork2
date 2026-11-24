"""Expected duration and provider isolation of the AI progress panel.

The countdown next to the progress bar is a UI hint: it must be derived from
the model that actually runs the check and must never change how the check
itself is executed. These tests also lock the isolation of the local model's
learned limits and timeouts from the hosted providers.
"""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from . import tests_ai_connections as fixtures
from .ai_jobs import job_expected_seconds, job_response, job_timing
from .models import AIJob, SubmissionFile


class JobEtaTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp

    def make_job(self, provider='', kind='teacher_check', images=2, status='running', elapsed=0):
        job = AIJob.objects.create(kind=kind, submission=self.sub, status=status,
                                   started_at=timezone.now() - timedelta(seconds=elapsed))
        for index in range(images):
            SubmissionFile.objects.create(submission=self.sub, original_name=f'page{index}.png',
                                          file=SimpleUploadedFile(f'page{index}.png', b'x'))
        if provider:
            job.events = [{'kind': 'model_start', 'message': 'start', 'sequence': 1,
                           'provider': provider, 'model': 'synthetic'}]
            job.save(update_fields=['events'])
        return job

    def test_local_model_estimate_scales_with_the_number_of_pages(self):
        job = self.make_job(provider='custom', images=2)
        self.assertEqual(job_expected_seconds(job), 120 + 18 * 2)
        # A second submission keeps the page counts independent.
        from .models import Submission
        other = Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
                                          class_group=self.group, first_name='Другий', last_name='Учень')
        for index in range(6):
            SubmissionFile.objects.create(submission=other, original_name=f'slide{index}.png',
                                          file=SimpleUploadedFile(f'slide{index}.png', b'x'))
        job_many = AIJob.objects.create(kind='teacher_check', submission=other, status='running',
                                        started_at=timezone.now(),
                                        events=[{'kind': 'model_start', 'sequence': 1,
                                                 'provider': 'custom', 'model': 'synthetic'}])
        self.assertGreater(job_expected_seconds(job_many), job_expected_seconds(job))

    def test_hosted_provider_estimate_is_short(self):
        job = self.make_job(provider='gemini', images=2)
        self.assertEqual(job_expected_seconds(job), 15 + 3 * 2)

    def test_queued_job_uses_a_middle_estimate(self):
        job = self.make_job(provider='', images=2, status='queued')
        self.assertEqual(job_expected_seconds(job), 45 + 6 * 2)

    def test_understanding_estimate_stays_bounded(self):
        job = AIJob.objects.create(kind='understanding', assignment=self.assignment, status='running',
                                   started_at=timezone.now())
        self.assertLessEqual(job_expected_seconds(job), 180)

    def test_timing_reports_elapsed_and_remaining(self):
        job = self.make_job(provider='custom', images=2, elapsed=60)
        timing = job_timing(job)
        self.assertEqual(timing['eta_seconds'], 120 + 18 * 2)
        self.assertGreaterEqual(timing['elapsed_seconds'], 60)
        self.assertLessEqual(timing['eta_remaining_seconds'], timing['eta_seconds'] - 60 + 1)
        self.assertEqual(timing['eta_provider'], 'custom')

    def test_status_response_carries_the_countdown_fields(self):
        job = self.make_job(provider='custom', images=2, elapsed=30)
        response = job_response(job)
        self.assertEqual(response.status_code, 202)
        import json as _json
        payload = _json.loads(response.content)
        self.assertEqual(payload['eta_seconds'], 120 + 18 * 2)
        self.assertIn('eta_remaining_seconds', payload)
        self.assertIn('elapsed_seconds', payload)


class ProviderIsolationTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection

    def test_local_model_learned_limit_does_not_leak_to_hosted_models(self):
        from .ai_concurrency import record_model_context_limit
        from .ai_model_catalog import get_model_capabilities
        before = get_model_capabilities('gemini-3.6-flash', 'gemini')['context_tokens']
        record_model_context_limit('custom', 'qwen3-vl-8b-instruct',
                                   '{"error":{"message":"exceeds context","n_ctx":22016}}',
                                   est_tokens=22016)
        after = get_model_capabilities('gemini-3.6-flash', 'gemini')['context_tokens']
        self.assertEqual(before, after)
        self.assertEqual(get_model_capabilities('qwen3-vl-8b-instruct', 'custom')['context_tokens'], 22016)

    def test_enabled_local_connection_does_not_change_hosted_requests(self):
        import json
        from unittest.mock import patch

        self.add_connection('gemini', 'gemini-3.6-flash')
        self.add_connection('custom', 'qwen3-vl-8b-instruct', connection_url='http://192.168.1.12:1234')
        answer = {'suggested_grade': '9', 'level': 'Достатній', 'summary': 'Правила правильні.',
                  'feedback_comment': 'Добре виконано.', 'gr_results': []}
        with patch('feed.gemini_service.call_ai_api', return_value=(200, json.dumps(answer), '', {})) as call:
            from .gemini_service import evaluate_submission_with_gemini
            result = evaluate_submission_with_gemini(self.sub)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.kwargs['provider'], 'gemini')
        self.assertEqual(call.call_args.kwargs['timeout'], 50)
