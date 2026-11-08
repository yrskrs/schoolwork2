"""Регресійні тести точної перевірки class_mismatch (4.8.13b).

Перевіряють, що версії/метадані, ZIP-архіви та код не створюють хибних
блокувань, а явні згадки іншого класу (у тексті, Excel чи назві файлу)
продовжують блокуватися.
"""
import json
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .gemini_service import (
    detect_invalid_or_teacher_template_submission,
    extract_submission_content,
)
from .models import Assignment, ClassGroup, Subject, Submission, Teacher


class ClassMismatchRegressionTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory(prefix='schoolwork-class-mismatch-')
        self.addCleanup(self.media_dir.cleanup)
        media_override = override_settings(MEDIA_ROOT=Path(self.media_dir.name))
        media_override.enable()
        self.addCleanup(media_override.disable)

        user = User.objects.create_user(username='class_mismatch_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=user, full_name='Вчитель Тест')
        self.subject = Subject.objects.create(name='Інформатика')

        self.group_8b = ClassGroup.objects.create(name='8-Б', grade=8, letter='Б')
        self.group_9v = ClassGroup.objects.create(name='9-В', grade=9, letter='В')
        self.assignment_8b = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='ML-модель', status='published',
        )
        self.assignment_8b.classes.add(self.group_8b)
        self.assignment_9v = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Таблиця даних', status='published',
        )
        self.assignment_9v.classes.add(self.group_9v)

        self.sub_8b = Submission.objects.create(
            assignment=self.assignment_8b, teacher=self.teacher, class_group=self.group_8b,
            first_name='Учень', last_name='Восьмий',
        )
        self.sub_9v = Submission.objects.create(
            assignment=self.assignment_9v, teacher=self.teacher, class_group=self.group_9v,
            first_name='Учень', last_name='Дев’ятий',
        )

    def detect(self, text, submission=None, text_parts=None):
        submission = submission or self.sub_8b
        parts = text_parts if text_parts is not None else [text]
        with patch('feed.gemini_service.check_submission_duplicates', return_value={}):
            return detect_invalid_or_teacher_template_submission(submission, parts)

    def test_version_and_metadata_fragments_are_not_class_mentions(self):
        cases = [
            '"packageVersion":"0.8.4-alpha2"',
            '{"packageVersion": "0.8.4-alpha2"}',
            '{"version": "0.8.4-alpha2"}',
            '1.2.3-beta',
            'v2.0-rc',
            'model 10-a-model.tflite',
            '0.8.4-alpha2 / 1.2.3-beta / v2.0-rc',
            '550e8400-e29b-41d4-a716-446655440000',
            'deadbeefcafebabe',
            'TG9yZW0gaXBzdW0gZG9sb3Igc2l0IGFtZXQ=',
            '"version": "11-А клас"',
        ]
        for text in cases:
            with self.subTest(text=text):
                is_invalid, reason, code = self.detect(text)
                self.assertFalse(is_invalid, f'Хибне блокування для {text!r}: {reason}')
                self.assertEqual(code, 'none')

    def test_teachable_machine_zip_content_is_not_class_checked(self):
        zip_path = Path(self.media_dir.name) / 'tm-my-image-model.zip'
        model_json = json.dumps({
            'packageVersion': '0.8.4-alpha2',
            'modelName': 'tm-my-image-model',
            'labels': ['11-А клас', '8-Б клас'],
        }, ensure_ascii=False)
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr('model.json', model_json)

        self.sub_8b.file = SimpleUploadedFile(
            'tm-my-image-model.zip', zip_path.read_bytes(), content_type='application/zip',
        )
        self.sub_8b.save()

        text_parts, _media, error = extract_submission_content(self.sub_8b)
        self.assertIsNone(error)
        joined = '\n'.join(text_parts)
        self.assertIn('0.8.4-alpha2', joined)
        self.assertIn('11-А клас', joined)

        is_invalid, reason, code = self.detect('', submission=self.sub_8b, text_parts=text_parts)
        self.assertFalse(is_invalid, f'Вміст ZIP не має перевірятися на клас: {reason}')
        self.assertEqual(code, 'none')

    def test_archive_filename_with_explicit_class_still_blocks(self):
        self.sub_8b.file = SimpleUploadedFile('Робота 11-А клас.zip', b'PK\x03\x04', content_type='application/zip')
        self.sub_8b.save()
        is_invalid, reason, code = self.detect(
            'Вміст прикріпленого архіву (Робота 11-А клас.zip):\n• model.json',
            submission=self.sub_8b,
        )
        self.assertTrue(is_invalid)
        self.assertEqual(code, 'class_mismatch')
        self.assertIn('11', reason)

    def test_fenced_code_content_is_not_class_checked(self):
        is_invalid, reason, code = self.detect(
            'Пояснення до проєкту:\n```python\n# завдання для 11-А класу\nprint("ok")\n```'
        )
        self.assertFalse(is_invalid, reason)
        self.assertEqual(code, 'none')

    def test_code_file_content_is_not_class_checked(self):
        self.sub_8b.file = SimpleUploadedFile(
            'main.py', '# завдання для 11-А класу\nprint("ok")\n'.encode('utf-8'),
            content_type='text/x-python',
        )
        self.sub_8b.save()
        is_invalid, reason, code = self.detect(
            'Вміст роботи учня (main.py):\n# завдання для 11-А класу\nprint("ok")',
            submission=self.sub_8b,
        )
        self.assertFalse(is_invalid, reason)
        self.assertEqual(code, 'none')

    def test_bare_class_token_blocks_only_with_explicit_class_context(self):
        # У таблиці SUB 378 заголовок «Клас» стоїть окремо, а в клітинках — «11-А».
        is_invalid, reason, code = self.detect('Прізвище | Клас | Жанр\nУчень | 11-А | Фантастика')
        self.assertTrue(is_invalid, reason)
        self.assertEqual(code, 'class_mismatch')
        self.assertIn('11-А', reason)

        # Без слова «клас»/«кл.» токен «11-А» не є явною згадкою класу.
        is_invalid, reason, code = self.detect('Учень | 11-А | класика')
        self.assertFalse(is_invalid, reason)
        self.assertEqual(code, 'none')

        is_invalid, reason, code = self.detect('11-А')
        self.assertFalse(is_invalid, reason)
        self.assertEqual(code, 'none')

    def test_explicit_class_mentions_still_block(self):
        cases = [
            'Завдання для 11 класу',
            'Робота 11-А клас',
            'Дані учня 11-го класу',
            'Робота для 11 кл.',
        ]
        for text in cases:
            with self.subTest(text=text):
                is_invalid, reason, code = self.detect(text)
                self.assertTrue(is_invalid, f'Пропущено явну згадку класу: {text!r}')
                self.assertEqual(code, 'class_mismatch')
                self.assertIn('11', reason)
                self.assertIn('Фрагмент-тригер', reason)

    def test_own_class_mention_is_not_a_mismatch(self):
        is_invalid, reason, code = self.detect('Робота для 8-Б класу')
        self.assertFalse(is_invalid, reason)
        self.assertEqual(code, 'none')

    def test_sub_378_like_excel_with_11a_data_still_blocks(self):
        from openpyxl import Workbook

        xlsx_path = Path(self.media_dir.name) / 'sub_378.xlsx'
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Sheet1'
        # Відтворюємо структуру SUB 378: заголовок «Клас» і клітинки «11-А».
        sheet.append(['Прізвище', 'Клас', 'Жанр', 'Прочитано'])
        sheet.append(['Учень', '11-А', 'Фантастика', 12])
        sheet.append(['Інший учень', '11-А', 'класика', 5])
        workbook.save(xlsx_path)

        self.sub_9v.file = SimpleUploadedFile(
            'sub_378.xlsx', xlsx_path.read_bytes(),
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        self.sub_9v.save()

        text_parts, _media, error = extract_submission_content(self.sub_9v)
        self.assertIsNone(error)
        self.assertIn('11-А', '\n'.join(text_parts))
        self.assertIn('Клас', '\n'.join(text_parts))

        is_invalid, reason, code = self.detect('', submission=self.sub_9v, text_parts=text_parts)
        self.assertTrue(is_invalid, 'SUB 378-подібна робота 9-В з даними 11-А має блокуватися')
        self.assertEqual(code, 'class_mismatch')
        self.assertIn('11 класу', reason)
        self.assertIn('Фрагмент-тригер', reason)

    def test_blocking_is_logged_with_code_and_trigger(self):
        with self.assertLogs('feed.gemini_service', level='WARNING') as captured:
            is_invalid, _reason, code = self.detect('Робота 11-А клас')
        self.assertTrue(is_invalid)
        self.assertEqual(code, 'class_mismatch')
        log_text = '\n'.join(captured.output)
        self.assertIn('class_mismatch', log_text)
        self.assertIn('11-А клас', log_text)
