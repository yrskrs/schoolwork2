"""Evidence integrity: binary files never become prompt text, references survive."""
import base64
import io
import json
import os
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from PIL import Image

from .ai_context import _full_text, build_assessment_request, compact_reference_material, extract_file_evidence
from .ai_model_catalog import get_model_capabilities
from .gemini_service import level_for_grade, normalized_level
from .ai_files import upload_gemini_file, wait_gemini_file
from .ai_jobs import (_parameters_key, clone_snapshot_files, snapshot_drift,
                      snapshot_submission_files, source_snapshot)
from .ai_payload import payload_manifest
from .ai_privacy import PrivacyBlocked, Redactor, _current
from .gemini_service import (_raw_call_ai_api, evaluate_media_in_batches, extract_submission_content,
                            resolve_assignment_scope)
from .middleware import set_has_admin
from .models import AICriteriaPreset, AIJob, Assignment, ClassGroup, School, Submission, Teacher


def jpeg_bytes(size=(640, 480), quality=92):
    image = Image.effect_noise(size, 60).convert('RGB')
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=quality)
    return buffer.getvalue()


class BinaryEvidenceTests(SimpleTestCase):
    def test_image_evidence_has_no_text_extract(self):
        handle = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        handle.write(jpeg_bytes())
        handle.close()
        try:
            evidence = extract_file_evidence(handle.name, '.jpg')
        finally:
            os.unlink(handle.name)
        self.assertEqual(evidence['text'], '')
        self.assertEqual(len(evidence['media']), 1)
        self.assertTrue(evidence['media'][0]['mime_type'].startswith('image/'))

    def test_unknown_binary_returns_empty_text(self):
        handle = tempfile.NamedTemporaryFile(suffix='.bin', delete=False)
        handle.write(bytes(range(256)) * 40)
        handle.close()
        try:
            self.assertEqual(_full_text(handle.name, '.bin'), '')
        finally:
            os.unlink(handle.name)

    def test_docx_text_is_not_mistaken_for_binary(self):
        import docx
        document = docx.Document()
        for index in range(20):
            document.add_paragraph(f'MARK_{index} ' + 'Текст відповіді учня. ' * 6)
        buffer = io.BytesIO()
        document.save(buffer)
        handle = tempfile.NamedTemporaryFile(suffix='.docx', delete=False)
        handle.write(buffer.getvalue())
        handle.close()
        try:
            text = _full_text(handle.name, '.docx')
        finally:
            os.unlink(handle.name)
        self.assertIn('MARK_19', text)
        self.assertGreater(len(text), 2000)


class ReferenceCompactionTests(SimpleTestCase):
    def test_single_newline_document_is_not_discarded(self):
        text = '\n'.join(f'MARK_{index} ' + 'Пояснення до теми. ' * 20 for index in range(12))
        compacted = compact_reference_material(text, 'theory.docx', theory_budget=1500)
        self.assertIn('MARK_0', compacted)
        self.assertGreater(len(compacted), 800)
        self.assertNotEqual(compacted.strip(), '[...довідковий теоретичний матеріал скорочено для ШІ...]')


class PrivacyPhotoTests(SimpleTestCase):
    def test_photo_is_not_reencoded_as_lossless_png(self):
        raw = jpeg_bytes((1600, 1200))
        media = [{'mime_type': 'image/jpeg', 'data': base64.b64encode(raw).decode(), 'source': 'photo.jpg'}]
        redacted = Redactor('redact').media(media)
        self.assertEqual(redacted[0]['mime_type'], 'image/jpeg')
        encoded = base64.b64decode(redacted[0]['data'])
        self.assertLess(len(encoded), len(raw) * 1.2)


class DuplicatePolicyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('evidence_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.group = ClassGroup.objects.create(name='8-Е', grade=8)
        self.preset = AICriteriaPreset.objects.create(
            name='Evidence preset', evaluation_type='nus_gr', system_prompt='Оцінюй за критеріями.',
            gr_definitions=json.dumps([{'code': 'ГР 1', 'name': 'Зміст'}]))
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Перевірка', description='Виконайте завдання 2.',
            status='published', default_ai_preset=self.preset, default_ai_grs=json.dumps(['ГР 1']))
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Тестовий', file=SimpleUploadedFile('work.txt', b'Vidpovid uchnia.'))

    def test_duplicate_policy_is_sent_to_the_model(self):
        text_parts, media, error = extract_submission_content(self.submission)
        self.assertIsNone(error)
        primary, reference, teacher_media, coverage = [], [], [], []
        scope = resolve_assignment_scope(
            assignment_title=self.assignment.title, assignment_desc=self.assignment.description,
            custom_criteria='', primary_task_content=primary, teacher_files_content=reference)
        scope['_duplicate_facts'] = {'is_duplicate_teacher': False, 'is_duplicate_student': True,
                                     'is_coauthor': False, 'plagiarism_ignored': False}
        prompt, system = build_assessment_request(
            self.submission, self.preset, [{'code': 'ГР 1', 'name': 'Зміст'}], scope,
            text_parts, primary, reference, coverage)
        self.assertIn('ВСТАНОВЛЕНО ПОВНИЙ ЗБІГ', prompt)
        self.assertIn('duplicate_facts', prompt)


class PayloadManifestTests(SimpleTestCase):
    def test_manifest_describes_the_payload_without_evidence(self):
        media = [{'mime_type': 'image/png', 'data': 'A' * 100}]
        manifest = payload_manifest('p' * 50, 's' * 20, media, provider='gemini', model='test-model')
        self.assertEqual(manifest['builder'], 'assessment-v5')
        self.assertEqual(manifest['prompt_chars'], 50)
        self.assertEqual(manifest['system_chars'], 20)
        self.assertEqual(manifest['media_count'], 1)
        self.assertEqual(manifest['media_bytes'], 100)
        self.assertEqual(len(manifest['sha256']), 64)
        self.assertGreater(manifest['est_tokens'], 0)
        self.assertNotIn('A' * 100, str(manifest))


class ManifestWiringTests(SimpleTestCase):
    @patch('feed.gemini_service._http_post_json')
    def test_raw_call_records_manifest_after_privacy(self, post):
        post.return_value = (200, {'candidates': [{'content': {'parts': [{'text': '{}'}]},
                                                   'finishReason': 'STOP'}]}, '')
        manifest = {}
        status, text, error, data = _raw_call_ai_api(
            'p' * 40, 's' * 10, [], provider='gemini', api_key='synthetic', model_name='gemini-3.6-flash',
            manifest=manifest)
        self.assertEqual(status, 200)
        self.assertEqual(manifest['prompt_chars'], 40)
        self.assertEqual(manifest['system_chars'], 10)
        self.assertEqual(manifest['model'], 'gemini-3.6-flash')
        self.assertEqual(len(manifest['sha256']), 64)


class PayloadPageTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        User = get_user_model()
        self.user = User.objects.create_user('payload_teacher', password='test-password')
        School.objects.create(name='Школа пакета', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Пакета')
        self.group = ClassGroup.objects.create(name='8-П', grade=8)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Пакет', description='Виконайте завдання.',
            status='published')
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Пакетний', file=SimpleUploadedFile('work.txt', b'text'))
        self.job = AIJob.objects.create(
            kind='teacher_check', submission=self.submission, requested_by=self.user, status='succeeded',
            result={'status': 'success', 'payload_manifest': {
                'builder': 'assessment-v5', 'rules_version': '2026-11-09.1', 'provider': 'gemini',
                'model': 'test-model', 'prompt_chars': 100, 'system_chars': 50, 'media_count': 0,
                'media_bytes': 0, 'est_tokens': 20, 'sha256': 'a' * 64,
                'sources': [{'target': 'work.txt', 'limitations': []}]}})

    def test_teacher_can_open_payload_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('ai_job_payload', args=[self.job.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'assessment-v5')
        self.assertContains(response, 'a' * 64)

    def test_anonymous_cannot_open_payload_page(self):
        response = self.client.get(reverse('ai_job_payload', args=[self.job.pk]))
        self.assertEqual(response.status_code, 404)

    def test_other_teacher_cannot_open_payload_page(self):
        User = get_user_model()
        other = User.objects.create_user('other_teacher', password='test-password')
        Teacher.objects.create(user=other, full_name='Інший')
        self.client.force_login(other)
        response = self.client.get(reverse('ai_job_payload', args=[self.job.pk]))
        self.assertEqual(response.status_code, 404)


class MediaBatchTests(TestCase):
    @staticmethod
    def image(index):
        buffer = io.BytesIO()
        Image.new('RGB', (24, 24), (index, 0, 0)).save(buffer, format='PNG')
        return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(),
                'source': f'page-{index}'}

    @patch('feed.gemini_service.call_ai_api')
    def test_eight_pages_become_3_3_2_then_one_synthesis(self, call):
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            if not kwargs.get('inline_media'):
                return 200, json.dumps({'suggested_grade': '7', 'summary': 'synthesis'}), None, {}
            return 200, json.dumps({'suggested_grade': '6', 'criteria_results': []}), None, {}

        call.side_effect = fake
        status, text, error, data, meta = evaluate_media_in_batches(
            'prompt', 'system', [self.image(i) for i in range(8)],
            provider='groq', api_key='synthetic', model_name='qwen/qwen3.8-27b',
            max_images=3, max_output_tokens=1200, timeout=10)
        self.assertEqual(status, 200)
        self.assertIn('synthesis', text)
        self.assertEqual(meta['batch_count'], 3)
        self.assertEqual([len(item['inline_media']) for item in calls[:3]], [3, 3, 2])
        self.assertEqual(calls[3]['inline_media'], [])
        self.assertEqual(len(calls), 4)

    @patch('feed.gemini_service.call_ai_api', return_value=(500, None, 'boom', {}))
    def test_failed_batches_do_not_produce_a_fake_result(self, call):
        status, text, error, data, meta = evaluate_media_in_batches(
            'prompt', 'system', [self.image(i) for i in range(4)],
            provider='groq', api_key='synthetic', model_name='qwen/qwen3.8-27b',
            max_images=3, max_output_tokens=1200, timeout=10)
        self.assertEqual(call.call_count, 2)
        self.assertIsNone(text)
        self.assertIn('Жодна партія', error)
        self.assertEqual(meta['batch_count'], 2)


class SourceSnapshotTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('snapshot_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Снапшота')
        self.group = ClassGroup.objects.create(name='8-С', grade=8)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Снапшот', description='Виконайте завдання.',
            status='published')
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Снапшотний',
            file=SimpleUploadedFile('work.txt', b'text'))

    def test_snapshot_is_stable_and_drift_is_detected(self):
        snapshot = source_snapshot(self.submission)
        self.assertEqual(len(snapshot['fingerprint']), 64)
        job = AIJob(parameters={'snapshot': snapshot})
        self.assertFalse(snapshot_drift(job, submission=self.submission))
        self.assignment.description = 'Змінена умова під час черги.'
        self.assignment.save(update_fields=['description'])
        self.assertTrue(snapshot_drift(job, submission=self.submission))

    def test_snapshot_is_not_part_of_deduplication_key(self):
        parameters = {'preset_id': 3, 'snapshot': {'fingerprint': 'x'}}
        self.assertEqual(_parameters_key(parameters), {'preset_id': 3})


class AudioEvidenceTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('audio_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Аудіо')
        self.group = ClassGroup.objects.create(name='8-А', grade=8)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Аудіо', description='Запишіть відповідь.', status='published')
        self.assignment.classes.add(self.group)

    def test_audio_is_attached_as_media_not_binary_text(self):
        submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Аудіо',
            file=SimpleUploadedFile('answer.mp3', b'ID3' + b'\x00' * 200))
        text_parts, media, error = extract_submission_content(submission)
        self.assertIsNone(error)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['mime_type'], 'audio/mpeg')
        self.assertTrue(any('Аудіо' in part for part in text_parts))
        self.assertFalse(any('\x00' in part for part in text_parts))

    def test_video_is_attached_as_media(self):
        submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Відео',
            file=SimpleUploadedFile('demo.mp4', b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 100))
        text_parts, media, error = extract_submission_content(submission)
        self.assertIsNone(error)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['mime_type'], 'video/mp4')
        self.assertTrue(any('Відео' in part for part in text_parts))

    def test_large_video_uses_file_api_upload_path(self):
        payload = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * (9 * 1024 * 1024)
        submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='ВеликеВідео',
            file=SimpleUploadedFile('big.mp4', payload))
        text_parts, media, error = extract_submission_content(submission)
        self.assertIsNone(error)
        self.assertEqual(len(media), 1)
        self.assertEqual(media[0]['mime_type'], 'video/mp4')
        self.assertIn('upload_path', media[0])
        self.assertNotIn('data', media[0])
        self.assertTrue(any('File API' in part for part in text_parts))

    def test_strict_privacy_refuses_audio_but_redact_allows_it(self):
        media = [{'mime_type': 'audio/mpeg', 'data': base64.b64encode(b'ID3' + b'\x00' * 40).decode(),
                  'source': 'answer.mp3'}]
        with self.assertRaises(PrivacyBlocked):
            Redactor('strict').media(media)
        self.assertEqual(Redactor('redact').media(media)[0]['mime_type'], 'audio/mpeg')


class SnapshotFileTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('snapfile_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Файлів')
        self.group = ClassGroup.objects.create(name='8-Ф', grade=8)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Файли', description='Прочитайте.', status='published')
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Файловий',
            file=SimpleUploadedFile('work.txt', b'ORIGINAL EVIDENCE'))

    def test_preset_rubric_is_snapshotted(self):
        preset = AICriteriaPreset.objects.create(
            name='Рубрика', document_file=SimpleUploadedFile('rubric.txt', b'RUBRIC TEXT'))
        self.assignment.default_ai_preset = preset
        self.assignment.save(update_fields=['default_ai_preset'])
        mapping = snapshot_submission_files('snap-preset', self.submission)
        self.assertIn(str(preset.pk), mapping.get('preset', {}))

    def test_extraction_prefers_snapshot_copy(self):
        mapping = snapshot_submission_files('snap-test', self.submission)
        snapshot_path = mapping['submission'][str(self.submission.pk)]
        with open(self.submission.file.path, 'w', encoding='utf-8') as handle:
            handle.write('CHANGED AFTER QUEUE')
        text_parts, media, error = extract_submission_content(
            self.submission, path_overrides=mapping['submission'])
        self.assertIsNone(error)
        joined = '\n'.join(text_parts)
        self.assertIn('ORIGINAL EVIDENCE', joined)
        self.assertNotIn('CHANGED AFTER QUEUE', joined)
        self.assertTrue(os.path.exists(snapshot_path))


class LevelAndPolicyTests(SimpleTestCase):
    def test_level_is_derived_from_grade_band(self):
        self.assertEqual(level_for_grade('11'), 'Високий (10-12)')
        self.assertEqual(level_for_grade('8'), 'Достатній (7-9)')
        self.assertEqual(level_for_grade('5'), 'Середній (4-6)')
        self.assertEqual(level_for_grade('2'), 'Початковий (1-3)')
        self.assertEqual(level_for_grade('Доопрацювати'), 'Початковий (1-3)')

    def test_nonsense_level_is_replaced_but_meaningful_level_is_kept(self):
        self.assertEqual(normalized_level('11 клас', '9'), 'Достатній (7-9)')
        self.assertEqual(normalized_level('Достатній рівень: учень пояснив', '9'),
                         'Достатній рівень: учень пояснив')

    def test_qwen_is_text_only_for_media(self):
        caps = get_model_capabilities('qwen/qwen3.8-27b', 'groq')
        self.assertEqual(caps.get('media_policy'), 'text_only')


class GeminiFileApiTests(SimpleTestCase):
    @patch('feed.ai_files._request')
    def test_upload_start_put_and_wait(self, request):
        request.side_effect = [
            (200, {'X-Goog-Upload-URL': 'https://upload.example/1'}, b'{}'),
            (200, {}, json.dumps({'file': {'name': 'files/abc', 'uri': 'https://files/abc',
                                           'mimeType': 'video/mp4'}}).encode()),
            (200, {}, json.dumps({'state': 'ACTIVE'}).encode()),
        ]
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as handle:
            handle.write(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 32)
            path = handle.name
        try:
            info = upload_gemini_file(path, 'video/mp4', 'synthetic', 'demo.mp4')
            self.assertEqual(info['name'], 'files/abc')
            self.assertEqual(info['uri'], 'https://files/abc')
            self.assertEqual(wait_gemini_file('files/abc', 'synthetic')['state'], 'ACTIVE')
        finally:
            os.unlink(path)
        self.assertEqual(request.call_count, 3)

    @patch('feed.ai_files.wait_gemini_file', return_value={'state': 'ACTIVE'})
    @patch('feed.ai_files.upload_gemini_file', return_value={
        'name': 'files/abc', 'uri': 'https://files/abc', 'mime_type': 'video/mp4'})
    @patch('feed.gemini_service._http_post_json')
    def test_raw_call_sends_file_data_for_large_video(self, post, upload, wait):
        post.return_value = (200, {'candidates': [{'content': {'parts': [{'text': '{}'}]},
                                                   'finishReason': 'STOP'}]}, '')
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as handle:
            handle.write(b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 32)
            path = handle.name
        try:
            status, text, error, data = _raw_call_ai_api(
                'prompt', 'system', [{'mime_type': 'video/mp4', 'upload_path': path, 'source': 'v'}],
                provider='gemini', api_key='synthetic', model_name='gemini-3.6-flash')
        finally:
            os.unlink(path)
        self.assertEqual(status, 200)
        parts = post.call_args.args[1]['contents'][0]['parts']
        self.assertTrue(any('file_data' in part and part['file_data']['file_uri'] == 'https://files/abc'
                            for part in parts))

    @patch('feed.ai_files.upload_gemini_file')
    def test_non_gemini_provider_rejects_upload_media(self, upload):
        status, text, error, data = _raw_call_ai_api(
            'prompt', 'system', [{'mime_type': 'video/mp4', 'upload_path': '/tmp/x.mp4'}],
            provider='groq', api_key='synthetic', model_name='qwen/qwen3.8-27b')
        self.assertEqual(status, 415)
        upload.assert_not_called()


class ParallelBatchTests(TestCase):
    @staticmethod
    def image(index):
        buffer = io.BytesIO()
        Image.new('RGB', (20, 20), (index, 0, 0)).save(buffer, format='PNG')
        return {'mime_type': 'image/png', 'data': base64.b64encode(buffer.getvalue()).decode(),
                'source': f'p{index}'}

    @patch('feed.gemini_service.log_ai_request_metric')
    @patch('feed.gemini_service._http_post_json')
    def test_parallel_batches_keep_redaction_context(self, post, metric):
        post.return_value = (200, {'candidates': [{'content': {'parts': [{'text': '{}'}]},
                                                   'finishReason': 'STOP'}]}, '')
        redactor = Redactor('redact')
        redactor.add('SECRET', '[X]')
        token = _current.set(redactor)
        try:
            status, text, error, data, meta = evaluate_media_in_batches(
                'prompt SECRET', 'system', [self.image(i) for i in range(5)],
                provider='gemini', api_key='synthetic', model_name='gemini-3.6-flash',
                max_images=2, max_output_tokens=1000, timeout=10)
        finally:
            _current.reset(token)
        self.assertEqual(status, 200)
        self.assertEqual(meta['batch_count'], 3)
        prompts = [call.args[1]['contents'][0]['parts'][0]['text'] for call in post.call_args_list]
        self.assertTrue(prompts)
        for prompt in prompts:
            self.assertIn('[X]', prompt)
            self.assertNotIn('SECRET', prompt)


class RepeatJobTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user('repeat_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Повтору')
        self.group = ClassGroup.objects.create(name='8-Р', grade=8)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Повтор', description='Виконайте.', status='published')
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Учень', last_name='Повторний',
            file=SimpleUploadedFile('work.txt', b'ORIGINAL'))
        self.job = AIJob.objects.create(
            kind='teacher_check', submission=self.submission, requested_by=self.user,
            status='succeeded', result={'status': 'success'})
        files = snapshot_submission_files(self.job.pk, self.submission)
        self.job.parameters = {'snapshot': source_snapshot(self.submission), 'snapshot_files': files}
        self.job.save(update_fields=['parameters'])

    def test_repeat_copies_snapshot_into_new_job(self):
        from django.test import override_settings
        self.client.force_login(self.user)
        with override_settings(AI_JOBS_EAGER=False):
            response = self.client.post(reverse('ai_job_repeat', args=[self.job.pk]))
        self.assertEqual(response.status_code, 302)
        jobs = AIJob.objects.filter(submission=self.submission).exclude(pk=self.job.pk)
        self.assertEqual(jobs.count(), 1)
        new_job = jobs.first()
        self.assertEqual(new_job.parameters.get('repeat_of'), str(self.job.pk))
        new_files = new_job.parameters.get('snapshot_files') or {}
        self.assertTrue(new_files.get('submission'))
        self.assertNotEqual(new_files.get('root'), (self.job.parameters.get('snapshot_files') or {}).get('root'))
        for path in new_files['submission'].values():
            self.assertTrue(os.path.exists(path))

    def test_anonymous_cannot_repeat(self):
        response = self.client.post(reverse('ai_job_repeat', args=[self.job.pk]))
        self.assertEqual(response.status_code, 404)


class CoauthorNameTests(SimpleTestCase):
    def test_hyphenated_surname_is_not_a_coauthor_match(self):
        self.assertFalse(Submission._name_component_in('Коваль', ['коваль-мельник іван']))
        self.assertTrue(Submission._name_component_in('Коваль', ['коваль іван']))
