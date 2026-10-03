from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .forms import SubmissionForm
from .middleware import set_has_admin
from .models import Assignment, AssignmentFile, ClassGroup, Submission, Subject, Teacher


class AuditRegressionTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.owner = User.objects.create_user('audit_owner', password='test-password')
        self.other = User.objects.create_user('audit_other', password='test-password')
        self.teacher = Teacher.objects.create(user=self.owner, full_name='Власник')
        Teacher.objects.create(user=self.other, full_name='Інший вчитель')
        self.cls = ClassGroup.objects.create(name='9-А', grade=9, letter='А')
        self.subject = Subject.objects.create(name='Інформатика')
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Створити програму',
            description='Обчислити суму чисел', status='published', published_at=timezone.now(),
            allow_student_ai_check=True,
        )
        self.assignment.classes.add(self.cls)
        self.teacher.classes.add(self.cls)
        self.teacher.subjects.add(self.subject)
        self.sub = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.cls,
            last_name='Тестовий', first_name='Учень', grade='11', ai_suggested_grade='10',
            file=SimpleUploadedFile('answer.txt', b'answer'),
        )

    def test_other_teacher_cannot_run_or_apply_ai(self):
        self.client.force_login(self.other)
        with patch('feed.gemini_service.evaluate_submission_with_gemini') as evaluate:
            for name in ['ai_check_single_submission', 'api_ai_process_item', 'ai_apply_suggested_grade']:
                response = self.client.post(reverse(name, args=[self.sub.pk]))
                self.assertEqual(response.status_code, 403, name)
            evaluate.assert_not_called()
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.grade, '11')
        self.assertEqual(self.sub.teacher_id, self.teacher.pk)
        response = self.client.post(reverse('api_ai_get_batch_queue'))
        self.assertEqual(response.json()['queue'], [])

    def test_teacher_grade_hidden_in_public_attempt_history(self):
        self.sub.is_latest_attempt = False
        self.sub.save(update_fields=['is_latest_attempt'])
        Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.cls,
                                  last_name=self.sub.last_name, first_name=self.sub.first_name,
                                  previous_submission=self.sub, resubmission_attempt=2, is_resubmission=True)
        response = self.client.get(reverse('student_submissions_portal'))
        self.assertContains(response, 'Перевірено вчителем')
        self.assertNotContains(response, 'Оцінка: 11')
        self.assertEqual(self.client.get(reverse('submission_detail', args=[self.sub.pk])).status_code, 200)

    def test_unpublished_material_is_hidden_on_all_routes(self):
        self.assignment.status = 'draft'
        self.assignment.save(update_fields=['status'])
        material = AssignmentFile.objects.create(assignment=self.assignment,
                                                file=SimpleUploadedFile('secret.txt', b'draft'),
                                                original_name='secret.txt')
        for url in [reverse('assignment_detail', args=[self.assignment.pk]),
                    reverse('file_view', args=[material.pk]), reverse('file_download', args=[material.pk]),
                    reverse('file_preview', args=[material.pk]), material.file.url]:
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('file_download', args=[material.pk])).status_code, 200)

    def test_html_is_downloaded_and_sandboxed_but_upload_is_allowed(self):
        form = SubmissionForm({'full_name': 'HTML Учень', 'class_group': self.cls.pk},
                              {'files': [SimpleUploadedFile('site.html', b'<script>window.x=1</script>')]},
                              assignment=self.assignment)
        self.assertTrue(form.is_valid(), form.errors)
        sub = form.save(self.assignment)
        response = self.client.get(sub.file.url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.streaming)
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertIn('sandbox', response['Content-Security-Policy'])
        self.assertIn(b'<script>', b''.join(response.streaming_content))

    def test_large_file_download_uses_byte_ranges(self):
        response = self.client.get(self.sub.file.url, HTTP_RANGE='bytes=1-3')
        self.assertEqual(response.status_code, 206)
        self.assertEqual(b''.join(response.streaming_content), b'nsw')
        self.assertEqual(response['Content-Range'], 'bytes 1-3/6')
        self.assertEqual(self.client.get(self.sub.file.url, HTTP_RANGE='bytes=100-').status_code, 416)

    def test_invalid_grade_cannot_be_saved_through_either_teacher_form(self):
        self.client.force_login(self.owner)
        for url, data in [(reverse('grade_submission', args=[self.sub.pk]), {'grade': '999'}),
                          (reverse('view_file', args=[self.sub.pk]), {'action': 'grade', 'grade': '0'}),
                          (reverse('mass_grade_submissions'), {'submission_ids': [self.sub.pk], 'grade': '-3'})]:
            self.assertEqual(self.client.post(url, data).status_code, 400)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.grade, '11')

    def test_failed_upload_preserves_old_attempt_and_removes_new_files(self):
        previous_count = Submission.objects.count()
        form = SubmissionForm({'full_name': self.sub.get_student_full_name(), 'class_group': self.cls.pk},
                              {'files': [SimpleUploadedFile('new.txt', b'new')]}, assignment=self.assignment)
        self.assertTrue(form.is_valid(), form.errors)
        with patch('feed.student_matcher.auto_bind_coauthors_from_comment', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                form.save(self.assignment)
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.is_latest_attempt)
        self.assertEqual(Submission.objects.count(), previous_count)
        for storage, name in form._saved_uploads:
            self.assertFalse(storage.exists(name))

    def test_connection_settings_require_teacher_but_student_portal_stays_open(self):
        with patch('feed.gemini_service.test_ai_connection') as connect:
            self.assertEqual(self.client.post(reverse('api_test_gemini_connection')).status_code, 302)
            connect.assert_not_called()
        self.assertEqual(self.client.get(reverse('student_submissions_portal')).status_code, 200)

    def test_grade_archives_and_logs_cannot_be_downloaded_anonymously(self):
        from pathlib import Path
        from django.conf import settings
        for prefix in ['school_archives', 'logs_archives']:
            path = Path(settings.MEDIA_ROOT) / prefix / 'private.csv'
            path.parent.mkdir(exist_ok=True)
            path.write_text('Учень;Оцінка\nТест;11')
            url = settings.MEDIA_URL + prefix + '/private.csv'
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.owner)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Оцінка', b''.join(response.streaming_content).decode())

    def test_multiple_submission_files_are_exported_as_a_stream(self):
        import io
        import zipfile
        from .models import SubmissionFile
        for name in ['first.txt', 'second.txt']:
            SubmissionFile.objects.create(submission=self.sub, original_name=name,
                                          file=SimpleUploadedFile(name, name.encode()))
        self.client.force_login(self.owner)
        response = self.client.get(reverse('download_submission_files_zip', args=[self.sub.pk]))
        self.assertTrue(response.streaming)
        self.assertIn('filename*=', response['Content-Disposition'])
        with zipfile.ZipFile(io.BytesIO(b''.join(response.streaming_content))) as archive:
            self.assertEqual(archive.read('first.txt'), b'first.txt')
            self.assertEqual(archive.read('second.txt'), b'second.txt')


    def test_office_preview_removes_active_links_and_preserves_tables_and_images(self):
        from types import SimpleNamespace
        from .utils import convert_docx_to_html
        value = '<script>alert(1)</script><table><tr><td>Відповідь</td></tr></table><a href="javascript:alert(1)">Посилання</a><img src="data:image/png;base64,YQ==" onerror="alert(1)">'
        with patch('mammoth.convert_to_html', return_value=SimpleNamespace(value=value)):
            preview, error = convert_docx_to_html(self.sub.file.path)
        self.assertIsNone(error)
        self.assertNotIn('<script', preview)
        self.assertNotIn('javascript:', preview)
        self.assertNotIn('onerror', preview)
        self.assertIn('<table>', preview)
        self.assertIn('data:image/png;base64,YQ==', preview)


@override_settings(AI_JOBS_EAGER=False)
class DurableAIQueueTests(TestCase):
    setUp = AuditRegressionTests.setUp

    def enqueue(self, submission=None):
        return self.client.post(reverse('student_ai_self_check', args=[(submission or self.sub).pk]))

    def test_request_returns_immediately_and_duplicate_reuses_job(self):
        from .models import AIJob
        with patch('feed.views._evaluate_student_submission') as evaluate:
            first, second = self.enqueue(), self.enqueue()
            self.assertEqual(first.status_code, 202)
            self.assertEqual(first.json()['job_id'], second.json()['job_id'])
            self.assertEqual(AIJob.objects.count(), 1)
            evaluate.assert_not_called()
        self.assertEqual(self.client.get(first.json()['status_url']).status_code, 202)
        self.assertContains(self.client.get(reverse('submission_detail', args=[self.sub.pk])), 'data-ai-job-status')

    def test_pending_check_is_shared_across_resubmissions(self):
        first = self.enqueue()
        next_attempt = Submission.objects.create(
            assignment=self.assignment, class_group=self.cls, teacher=self.teacher,
            last_name=self.sub.last_name, first_name=self.sub.first_name,
            previous_submission=self.sub, resubmission_attempt=2)
        second = self.enqueue(next_attempt)
        self.assertEqual(first.json()['job_id'], second.json()['job_id'])

    def test_success_is_persisted_and_consumes_only_one_attempt(self):
        from .ai_jobs import execute_job
        response = self.enqueue()
        with patch('feed.views._evaluate_student_submission', return_value={
            'status': 'success', 'suggested_grade': '10', 'feedback_comment': 'Порада'}), \
                patch('feed.views.check_submission_duplicates', return_value={}):
            execute_job(response.json()['job_id'])
        result = self.client.get(response.json()['status_url'])
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.json()['ok'])
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.student_ai_checked)
        self.assertEqual(self.sub.grade, '11')
        self.assertEqual(self.enqueue().status_code, 403)
        next_attempt = Submission.objects.create(
            assignment=self.assignment, class_group=self.cls, teacher=self.teacher,
            last_name=self.sub.last_name, first_name=self.sub.first_name, previous_submission=self.sub)
        self.assertEqual(self.enqueue(next_attempt).status_code, 403)

    def test_failed_check_does_not_consume_the_students_attempt(self):
        from .ai_jobs import execute_job
        response = self.enqueue()
        with patch('feed.views._evaluate_student_submission', return_value={'status': 'failed', 'error': 'private API error'}):
            execute_job(response.json()['job_id'])
        result = self.client.get(response.json()['status_url'])
        self.assertEqual(result.status_code, 503)
        self.assertNotIn('private API error', result.content.decode())
        second = self.enqueue()
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(response.json()['job_id'], second.json()['job_id'])

    def test_timeout_releases_reservation_for_retry(self):
        from datetime import timedelta
        from .ai_jobs import claim_next_job
        from .models import AIJob
        response = self.enqueue()
        AIJob.objects.filter(pk=response.json()['job_id']).update(
            status='running', started_at=timezone.now() - timedelta(hours=1))
        self.assertIsNone(claim_next_job())
        self.assertEqual(self.enqueue().status_code, 202)

    def test_teacher_job_result_is_not_public(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse('ai_check_single_submission', args=[self.sub.pk]))
        self.assertEqual(response.status_code, 202)
        url = response.json()['status_url']
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_worker_rechecks_teacher_permission_after_ownership_changes(self):
        from .ai_jobs import execute_job
        self.client.force_login(self.owner)
        response = self.client.post(reverse('ai_check_single_submission', args=[self.sub.pk]))
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])
        with patch('feed.gemini_service.evaluate_submission_with_gemini') as evaluate:
            execute_job(response.json()['job_id'])
            evaluate.assert_not_called()

    def test_understanding_uses_queue_and_hides_private_assignment(self):
        self.assignment.allow_student_ai_understanding = True
        self.assignment.save(update_fields=['allow_student_ai_understanding'])
        with patch('feed.gemini_service.analyze_assignment_task_understanding') as analyze:
            response = self.client.get(reverse('assignment_ai_understanding', args=[self.assignment.pk]))
            self.assertEqual(response.status_code, 202)
            analyze.assert_not_called()
        self.assignment.status = 'draft'
        self.assignment.save(update_fields=['status'])
        self.assertEqual(self.client.get(response.json()['status_url']).status_code, 403)

    def test_student_check_uses_teacher_preset_and_groups(self):
        import json
        from .views import _evaluate_student_submission
        from .models import AICriteriaPreset
        preset = AICriteriaPreset.objects.create(name='Авторські критерії', evaluation_type='traditional')
        self.assignment.default_ai_preset = preset
        self.assignment.default_ai_grs = json.dumps(['ГР1'])
        self.assignment.save(update_fields=['default_ai_preset', 'default_ai_grs'])
        self.sub.refresh_from_db()
        with patch('feed.gemini_service.evaluate_submission_with_gemini') as evaluate:
            _evaluate_student_submission(self.sub)
            self.assertEqual(evaluate.call_args.kwargs['criteria_preset'], preset)
            self.assertEqual(evaluate.call_args.kwargs['selected_gr_codes'], ['ГР1'])


class PublicURLSafetyTests(TestCase):
    def test_private_addresses_are_blocked_before_connection(self):
        from .safe_http import validate_public_url
        for address in ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.1.1']:
            with patch('feed.safe_http.socket.getaddrinfo', return_value=[(2, 1, 6, '', (address, 80))]):
                with self.assertRaises(ValueError):
                    validate_public_url('http://example.com/resource')

    def test_public_redirect_cannot_access_private_network(self):
        from .safe_http import PublicRedirectHandler
        from urllib.request import Request
        with patch('feed.safe_http.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]):
            with self.assertRaises(ValueError):
                PublicRedirectHandler().redirect_request(Request('https://example.com'), None, 302, '', {}, 'http://localhost')

    def test_dns_rebinding_is_checked_when_connecting(self):
        from .safe_http import PublicHTTPConnection, validate_public_url
        public = [(2, 1, 6, '', ('8.8.8.8', 80))]
        private = [(2, 1, 6, '', ('127.0.0.1', 80))]
        with patch('feed.safe_http.socket.getaddrinfo', side_effect=[public, private]), \
                patch('feed.safe_http.socket.socket') as stream:
            validate_public_url('http://example.com')
            with self.assertRaises(ValueError):
                PublicHTTPConnection('example.com').connect()
            stream.assert_not_called()


class LargeUploadAndFeedTests(TestCase):
    setUp = AuditRegressionTests.setUp

    def test_512mb_file_is_accepted_and_downloaded_as_a_stream(self):
        from django.core.files.uploadedfile import TemporaryUploadedFile
        upload = TemporaryUploadedFile('project.bin', 'application/octet-stream', 512 * 1024 * 1024, None)
        upload.file.truncate(upload.size)
        upload.seek(0)
        try:
            form = SubmissionForm({'full_name': 'Великий Проєкт', 'class_group': self.cls.pk},
                                  {'files': [upload]}, assignment=self.assignment)
            self.assertTrue(form.is_valid(), form.errors)
            sub = form.save(self.assignment)
            self.assertEqual(sub.file.size, upload.size)
            response = self.client.get(sub.file.url, HTTP_RANGE='bytes=536870908-')
            self.assertEqual(response.status_code, 206)
            self.assertEqual(b''.join(response.streaming_content), b'\0' * 4)
        finally:
            upload.close()

    def test_feed_queries_do_not_grow_with_entire_assignment_history(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        self.client.get(reverse('index'))
        with CaptureQueriesContext(connection) as before:
            self.client.get(reverse('index'))
        for index in range(60):
            item = Assignment.objects.create(teacher=self.teacher, subject=self.subject, title=f'Робота {index}',
                                             status='published', published_at=timezone.now())
            item.classes.add(self.cls)
        with CaptureQueriesContext(connection) as after:
            response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(after), len(before) + 12)


@skipUnless(connection.vendor == 'postgresql', 'Перевірка блокувань потребує PostgreSQL')
@override_settings(AI_JOBS_EAGER=False)
class PostgreSQLQueueConcurrencyTests(TransactionTestCase):
    setUp = AuditRegressionTests.setUp

    def test_simultaneous_student_requests_create_only_one_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from .ai_jobs import enqueue_submission_job
        from .models import AIJob
        barrier = Barrier(4)
        def enqueue(_):
            try:
                submission = Submission.objects.get(pk=self.sub.pk)
                barrier.wait(timeout=10)
                return str(enqueue_submission_job(submission, 'student_check').pk)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(enqueue, range(4)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(AIJob.objects.count(), 1)

    def test_two_workers_cannot_claim_the_same_job(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from .ai_jobs import enqueue_submission_job, claim_next_job
        enqueue_submission_job(self.sub, 'student_check')
        barrier = Barrier(2)
        def claim(_):
            try:
                barrier.wait(timeout=10)
                job = claim_next_job()
                return job.pk if job else None
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
