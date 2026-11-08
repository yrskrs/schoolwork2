"""Teacher-only single-run visual consent never changes pupil privacy policy."""
import json
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.urls import reverse
from lxml import html

from . import tests_ai_connections as fixtures
from .tests_ai_parallel_privacy import picture
from .models import AIJob, AISettings, Teacher
from .ai_jobs import execute_job
from .ai_privacy import _current
from .gemini_service import evaluate_submission_with_gemini
import io
import base64
from PIL import Image


def picture_with_personal_data():
    buffer = io.BytesIO()
    image = Image.new('RGB', (32, 24), 'blue')
    exif = image.getexif()
    exif[315] = 'Іван Петренко'
    image.save(buffer, format='PNG', exif=exif)
    return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(), 'source': 'Синтетичний файл'}


@override_settings(AI_JOBS_EAGER=False)
class TeacherPrivacyOverrideTests(TestCase):
    def setUp(self):
        fixtures.AIConnectionsTests.setUp(self)
        self.settings.privacy_mode = 'strict'
        self.settings.save(update_fields=['privacy_mode'])

    add_connection = fixtures.AIConnectionsTests.add_connection
    answer = fixtures.AIConnectionsTests.answer

    def endpoint(self):
        return reverse('ai_check_single_submission', args=[self.sub.pk])

    def visual_answer(self):
        return 200, {'candidates': [{'content': {'parts': [{'text': self.answer()[1]}]}}]}, ''

    def test_teacher_single_check_persists_explicit_choice_with_its_requester(self):
        response = self.client.post(self.endpoint(), {'ignore_strict_privacy': '1'})
        self.assertEqual(response.status_code, 202)
        job = AIJob.objects.get(pk=response.json()['job_id'])
        self.assertTrue(job.parameters['ignore_strict_privacy'])
        self.assertEqual(job.requested_by, self.user)
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.privacy_mode, 'strict')

    def test_normal_teacher_request_does_not_inherit_previous_override(self):
        first = self.client.post(self.endpoint(), {'ignore_strict_privacy': '1'})
        AIJob.objects.filter(pk=first.json()['job_id']).update(status='succeeded')
        second = self.client.post(self.endpoint())
        self.assertEqual(second.status_code, 202)
        job = AIJob.objects.get(pk=second.json()['job_id'])
        self.assertNotIn('ignore_strict_privacy', job.parameters)

    def test_batch_choice_is_scoped_to_the_created_batch(self):
        response = self.client.post(reverse('ai_batch_create'), {'request_id': str(uuid4()),
            'submission_ids': [self.sub.pk], 'ignore_strict_privacy': '1'})
        self.assertEqual(response.status_code, 200)
        job = AIJob.objects.get(batch_id=response.json()['batch_id'])
        self.assertTrue(job.parameters['ignore_strict_privacy'])
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.privacy_mode, 'strict')

    def test_legacy_batch_item_endpoint_accepts_teacher_choice(self):
        response = self.client.post(reverse('api_ai_process_item', args=[self.sub.pk]),
            {'ignore_strict_privacy': '1'})
        self.assertEqual(response.status_code, 202)
        self.assertTrue(AIJob.objects.get().parameters['ignore_strict_privacy'])

    def test_student_post_cannot_enable_override(self):
        self.assignment.allow_student_ai_check = True
        self.assignment.save()
        self.client.logout()
        response = self.client.post(reverse('student_ai_self_check', args=[self.sub.pk]),
            {'ignore_strict_privacy': '1', 'privacy_mode': 'redact'})
        self.assertEqual(response.status_code, 202)
        job = AIJob.objects.get(pk=response.json()['job_id'])
        self.assertEqual(job.kind, 'student_check')
        self.assertEqual(job.parameters, {})

    @patch('feed.gemini_service._http_post_json')
    def test_direct_override_without_teacher_identity_is_denied(self, post):
        self.add_connection('gemini', 'synthetic-vision')
        with self.assertRaises(PermissionDenied):
            evaluate_submission_with_gemini(self.sub, ignore_strict_privacy=True)
        post.assert_not_called()
        self.assertIsNone(_current.get())

    @patch('feed.gemini_service._http_post_json')
    def test_other_teacher_cannot_bypass_access_checks(self, post):
        other = User.objects.create_user('privacy_other', password='synthetic')
        teacher = Teacher.objects.create(user=other, full_name='Інший учитель')
        with self.assertRaises(PermissionDenied):
            evaluate_submission_with_gemini(self.sub, teacher=teacher, ignore_strict_privacy=True)
        post.assert_not_called()
        self.client.force_login(other)
        self.assertEqual(self.client.post(self.endpoint(), {'ignore_strict_privacy': '1'}).status_code, 403)
        self.assertFalse(AIJob.objects.exists())

    @patch('feed.gemini_service.extract_submission_content')
    @patch('feed.gemini_service._http_post_json')
    def test_teacher_override_sends_visuals_but_keeps_text_redaction_and_audit(self, post, extract):
        self.add_connection('gemini', 'synthetic-vision')
        extract.return_value = ([self.sub.get_student_full_name() + ': Використовуй складні паролі. student@example.org'], [picture()], None)
        post.return_value = self.visual_answer()
        response = self.client.post(self.endpoint(), {'ignore_strict_privacy': '1'})
        job = AIJob.objects.get(pk=response.json()['job_id'])
        execute_job(job.pk)
        job.refresh_from_db()
        self.assertEqual(job.status, 'succeeded')
        payload = post.call_args.args[1]
        self.assertTrue(any('inlineData' in part for part in payload['contents'][0]['parts']))
        wire = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(self.sub.get_student_full_name(), wire)
        self.assertNotIn('student@example.org', wire)
        self.assertTrue(any(event['kind'] == 'privacy_override' for event in job.events))
        self.settings.refresh_from_db()
        self.assertEqual(self.settings.privacy_mode, 'strict')
        self.assertIsNone(_current.get())

    @patch('feed.gemini_service.extract_submission_content')
    @patch('feed.gemini_service._http_post_json')
    def test_next_student_check_remains_strict_even_if_job_parameters_are_forged(self, post, extract):
        self.add_connection('gemini', 'synthetic-vision')
        extract.return_value = (['Використовуй складні паролі.'], [picture_with_personal_data()], None)
        post.return_value = self.visual_answer()
        first = self.client.post(self.endpoint(), {'ignore_strict_privacy': '1'})
        execute_job(first.json()['job_id'])
        self.assertEqual(post.call_count, 1)
        self.assignment.allow_student_ai_check = True
        self.assignment.save()
        student = AIJob.objects.create(kind='student_check', submission=self.sub,
            parameters={'ignore_strict_privacy': True})
        execute_job(student.pk)
        student.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(student.status, 'failed')
        self.assertEqual(post.call_count, 1)
        self.assertFalse(self.sub.student_ai_checked)
        self.assertIn('Суворе знеособлення', student.result['error'])

    @patch('feed.gemini_service.extract_submission_content')
    @patch('feed.gemini_service._http_post_json')
    @override_settings(AI_PRIVACY_ALLOWED_PROVIDERS=['custom'])
    def test_override_does_not_bypass_provider_allowlist(self, post, extract):
        self.add_connection('gemini', 'synthetic-vision')
        extract.return_value = (['Використовуй складні паролі.'], [picture()], None)
        result = evaluate_submission_with_gemini(self.sub, teacher=self.teacher, ignore_strict_privacy=True)
        self.assertEqual(result['status'], 'failed')
        post.assert_not_called()

    def test_teacher_option_is_unchecked_and_explains_visual_data(self):
        page = self.client.get(reverse('view_file', args=[self.sub.pk]))
        self.assertEqual(page.status_code, 200)
        tree = html.fromstring(page.content)
        checkbox = tree.get_element_by_id('ignore-strict-privacy-checkbox')
        self.assertIsNone(checkbox.get('checked'))
        self.assertIn('видимі особисті дані', tree.get_element_by_id('teacher-privacy-override-hint').text_content())
        batch = self.client.get(reverse('ai_batch_check'))
        tree = html.fromstring(batch.content)
        self.assertIsNone(tree.get_element_by_id('batch-ignore-strict-privacy').get('checked'))
