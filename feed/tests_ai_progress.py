"""Ordered failover and durable, private, observable evaluation progress."""
import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from . import tests_ai_connections as fixtures
from .ai_jobs import enqueue_submission_job, execute_job, fail_job
from .ai_progress import emit_event, track_job
from .gemini_service import evaluate_submission_with_gemini, call_ai_api
from .models import AIJob, AIRequestLog


class EvaluationProgressTests(TestCase):
    setUp = fixtures.AIConnectionsTests.setUp
    add_connection = fixtures.AIConnectionsTests.add_connection
    answer = fixtures.AIConnectionsTests.answer

    def job(self, kind='teacher_check'):
        return AIJob.objects.create(kind=kind, submission=self.sub, requested_by=self.user)

    @patch('feed.gemini_service.call_ai_api')
    def test_exact_reported_six_model_sequence_is_exhausted_once(self, call):
        models = ['gemini-3.1-flash-lite', 'gemini-3.6-flash', 'gemini-3.8-flash',
                  'gemini-3.7-flash', 'gemini-flash-latest', 'openai/gpt-oss-120b']
        for index, name in enumerate(models):
            self.add_connection('gemini' if index < 5 else 'groq', name)
        call.side_effect = [(code, '', 'synthetic failure', {}) for code in [503, 429, 429, 429, 429, 413]]
        job = self.job()
        execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual([c.kwargs['model_name'] for c in call.call_args_list], models)
        self.assertEqual(job.status, 'failed')
        self.assertEqual([e['model'] for e in job.events if e['kind'] == 'model_start'], models)
        self.assertEqual([e['status_code'] for e in job.events if e['kind'] == 'model_error'], [503,429,429,429,429,413])
        self.assertEqual(sum(e['kind'] == 'switching' for e in job.events), 5)
        self.assertEqual(job.events[-2]['kind'], 'exhausted')
        self.assertEqual(job.events[-1]['kind'], 'failed')
        self.assertIn('розміру, а не квоти', job.result['error'])
        result = self.client.get(reverse('ai_job_status', args=[job.pk])).json()
        self.assertEqual(result['events'], job.events)
        self.assertEqual([e['sequence'] for e in job.events], list(range(1, len(job.events)+1)))
        self.assertNotIn('synthetic-key', json.dumps(result))
        page = self.client.get(reverse('view_file', args=[self.sub.pk]))
        self.assertContains(page, 'Події останньої перевірки ШІ')
        self.assertContains(page, 'Наступної моделі')

    @patch('feed.gemini_service.call_ai_api')
    def test_progress_is_readable_while_request_is_running_and_success_stops_queue(self, call):
        self.add_connection('gemini', 'first')
        self.add_connection('groq', 'second')
        self.add_connection('openrouter', 'never-called')
        job = self.job()
        snapshots = []
        def respond(**kwargs):
            response = self.client.get(reverse('ai_job_status', args=[job.pk]))
            self.assertEqual(response.status_code, 202)
            snapshots.append(response.json()['events'])
            return (503, '', 'Overloaded', {}) if len(snapshots) == 1 else self.answer()
        call.side_effect = respond
        execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(call.call_count, 2)
        self.assertEqual(snapshots[0][-1]['model'], 'first')
        self.assertEqual(snapshots[1][-1]['model'], 'second')
        self.assertTrue(any(e['kind'] == 'switching' for e in snapshots[1]))
        self.assertEqual([e['kind'] for e in job.events[-2:]], ['model_success', 'completed'])

    @patch('feed.gemini_service.call_ai_api')
    def test_every_unusable_answer_advances_to_next_model(self, call):
        self.add_connection('gemini', 'first')
        self.add_connection('groq', 'second')
        failures = [(200, '   ', '', {}), (200, '', '', {}), (200, 'broken JSON', '', {}),
                    (200, json.dumps({'suggested_grade': None}), '', {}),
                    (200, json.dumps({'suggested_grade': 12, 'assessment_blocked': True}), '', {}),
                    (200, json.dumps({'suggested_grade': 'NaN'}), '', {}),
                    (200, '[]', '', {}), (403, '', 'Forbidden', {}), RuntimeError('connection interrupted')]
        for failure in failures:
            with self.subTest(failure=failure):
                call.reset_mock()
                call.side_effect = [failure, self.answer()]
                job = self.job()
                execute_job(job.pk)
                job.refresh_from_db()
                self.assertEqual(job.status, 'succeeded')
                self.assertEqual(call.call_count, 2)
                self.assertEqual(sum(e['kind'] == 'model_error' for e in job.events), 1)
                self.assertEqual(sum(e['kind'] == 'switching' for e in job.events), 1)

    @patch('feed.gemini_service.call_ai_api')
    def test_disabled_auto_failover_makes_no_false_switching_event(self, call):
        self.add_connection('gemini', 'first')
        self.add_connection('groq', 'second')
        self.settings.auto_failover_enabled = False
        self.settings.save()
        call.return_value = (429, '', 'Limit', {})
        job = self.job()
        execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(call.call_count, 1)
        self.assertFalse(any(e['kind'] == 'switching' for e in job.events))

    @patch('feed.gemini_service.call_ai_api')
    def test_secrets_from_any_connection_are_removed_from_events_and_errors(self, call):
        self.add_connection('gemini', 'first', key='secret-one')
        self.add_connection('groq', 'second', key='secret-two')
        call.side_effect = [(403, '', 'key=secret-one; secret-two', {}), self.answer()]
        job = self.job()
        execute_job(job.pk)
        job.refresh_from_db()
        data = json.dumps(job.events) + json.dumps(job.result)
        self.assertNotIn('secret-one', data)
        self.assertNotIn('secret-two', data)
        self.assertIn('приховано', str(job.events))

    def test_progress_is_bounded_and_context_does_not_leak_to_next_job(self):
        job = self.job()
        with track_job(job.pk):
            for index in range(210):
                emit_event('preparing', str(index))
        emit_event('preparing', 'outside worker')
        job.refresh_from_db()
        self.assertEqual(len(job.events), 200)
        self.assertEqual(job.events[-1]['sequence'], 210)
        self.assertEqual(job.events[-1]['message'], '209')
        fail_job(job.pk, 'Перевірку перервано.')
        job.refresh_from_db()
        self.assertEqual(job.events[-1]['kind'], 'failed')
        emit_event('preparing', 'must not reopen job', job_id=job.pk)
        job.refresh_from_db()
        self.assertEqual(job.events[-1]['kind'], 'failed')

    def test_teacher_progress_cannot_be_read_by_anonymous_student(self):
        job = self.job()
        emit_event('model_start', 'private teacher event', job_id=job.pk)
        self.client.logout()
        response = self.client.get(reverse('ai_job_status', args=[job.pk]))
        self.assertEqual(response.status_code, 403)
        page = self.client.get(reverse('submission_detail', args=[self.sub.pk]))
        self.assertNotContains(page, 'private teacher event')

    @patch('feed.gemini_service.call_ai_api')
    def test_student_failed_job_has_events_and_does_not_consume_attempt(self, call):
        self.assignment.allow_student_ai_check = True
        self.assignment.save()
        self.add_connection('gemini', 'first')
        call.return_value = (503, '', 'High demand', {})
        job = enqueue_submission_job(self.sub, 'student_check')
        self.assertEqual(job.status, 'failed')
        self.assertFalse(job.reservations.filter(used=True).exists())
        self.assertTrue(any(e['kind'] == 'model_error' for e in job.events))
        self.client.logout()
        response = self.client.get(reverse('ai_job_status', args=[job.pk]))
        self.assertEqual(response.json()['events'], job.events)

    @patch('feed.gemini_service._http_post_json')
    def test_visual_gpt_oss_strips_media_and_calls_api(self, post):
        # gpt-oss-* is text-only: media is stripped before the HTTP call but the
        # original caller's list must NOT be mutated.
        post.return_value = (200, {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}, '')
        media = [{'mime_type': 'application/pdf', 'data': 'synthetic', 'source': 'Карта знань'}]
        response = call_ai_api('Text', inline_media=media, provider='groq', model_name='openai/gpt-oss-120b', api_key='synthetic')
        # Should succeed (stripped media, text sent)
        self.assertEqual(response[0], 200)
        # HTTP was called once (text-only)
        post.assert_called_once()
        # Caller's media list was not mutated
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['source'], 'Карта знань')
        # No images in the payload that was sent
        sent_payload = post.call_args.args[1]
        user_content = sent_payload['messages'][-1]['content']
        image_parts = [p for p in (user_content if isinstance(user_content, list) else []) if p.get('type') == 'image_url']
        self.assertEqual(image_parts, [])

    @patch('feed.gemini_service._http_post_json')
    def test_text_only_gpt_oss_still_calls_groq(self, post):
        post.return_value = (200, {'choices': [{'message': {'content': 'text reply'}}]}, '')
        response = call_ai_api('Text', provider='groq', model_name='openai/gpt-oss-120b', api_key='synthetic')
        self.assertEqual(response[:2], (200, 'text reply'))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(AIRequestLog.objects.count(), 1)

    @patch('feed.gemini_service._http_post_json')
    @patch('feed.ai_payload.optimize_media', side_effect=lambda items, provider: items)
    def test_known_groq_vision_image_limit_is_capped_and_proceeds(self, optimize, post):
        # qwen3.8-27b accepts at most 3 images: excess images are capped (not blocked).
        post.return_value = (200, {'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]}, '')
        media = [{'mime_type': 'image/png', 'data': str(index)} for index in range(4)]
        response = call_ai_api('Text', inline_media=media, provider='groq', model_name='qwen/qwen3.8-27b', api_key='synthetic')
        # HTTP must be called (model proceeds with capped images)
        post.assert_called_once()
        # Caller's list is NOT mutated
        self.assertEqual(len(media), 4)
        # Payload contains at most 3 image parts
        sent_payload = post.call_args.args[1]
        user_content = sent_payload['messages'][-1]['content']
        image_parts = [p for p in (user_content if isinstance(user_content, list) else []) if p.get('type') == 'image_url']
        self.assertLessEqual(len(image_parts), 3)

    @patch('feed.gemini_service._http_post_json')
    @patch('feed.ai_payload.optimize_media', side_effect=lambda items, provider: items)
    def test_text_only_groq_calls_api_then_gemini_processes_visual(self, optimize, post):
        # gpt-oss-120b strips media and calls HTTP (text-only). Groq returns Gemini-
        # format payload which is unrecognised by the OpenAI response parser → empty
        # reply → queue advances to gemini which succeeds with the full media.
        self.add_connection('groq', 'openai/gpt-oss-120b')
        self.add_connection('gemini', 'vision-model')
        media = [{'mime_type': 'image/png', 'data': 'synthetic', 'source': 'Карта знань'}]
        answer = self.answer()[1]
        # Both groq and gemini calls return the same mock body.
        # Groq gets Gemini-format → empty reply → continues.
        # Gemini call succeeds with the real candidate.
        post.return_value = (200, {'candidates': [{'content': {'parts': [{'text': answer}]}}]}, '')
        original = call_ai_api
        def inject_media(**kwargs):
            kwargs['inline_media'] = list(media)  # copy so each invocation gets its own list
            return original(**kwargs)
        job = self.job()
        with patch('feed.gemini_service.call_ai_api', side_effect=inject_media):
            execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        # Two HTTP calls: one for groq (text-only), one for gemini (with image).
        self.assertEqual(post.call_count, 2)
        # Last call is to gemini and contains the image
        gemini_payload = post.call_args.args[1]
        image_data = gemini_payload['contents'][0]['parts'][-1]['inlineData']['data']
        self.assertEqual(image_data, 'synthetic')
        # Both calls logged; last one is gemini
        self.assertEqual(AIRequestLog.objects.count(), 2)
        self.assertEqual(AIRequestLog.objects.order_by('pk').last().provider, 'gemini')
        self.assertEqual([e['model'] for e in job.events if e['kind'] == 'model_start'], ['openai/gpt-oss-120b', 'vision-model'])

    @patch('feed.gemini_service._get_http_pool')
    @patch('feed.gemini_service.urllib.request.urlopen')
    def test_network_timeout_is_not_resent_via_another_transport(self, urlopen, pool):
        pool.return_value.request.side_effect = TimeoutError('timed out')
        result = call_ai_api('Text', provider='groq', model_name='openai/gpt-oss-120b', api_key='synthetic')
        self.assertEqual(result[0], 0)
        self.assertIn('timed out', result[2])
        self.assertIs(pool.return_value.request.call_args.kwargs['retries'], False)
        urlopen.assert_not_called()
