"""Regression tests for FIX-5: duplicates, PII scoping, provenance, backups, journal AI flag."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .access_control import scope_duplicate_info
from .ai_provenance import normalize_authorship
from .cloud_backups import Adapter, schema, schema_compatibility
from .duplicate_detector import (DUPLICATE_GRADE_CEILING, check_submission_duplicates,
                                 duplicate_ceiling)
from .models import (Assignment, AssignmentFile, ClassGroup, JournalAPIKey, School, Student,
                     Submission, SubmissionFile, Subject, Teacher)


class DuplicateBase(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('dup_teacher', password='pass')
        School.objects.create(name='Школа дублікатів', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Дублікатів')
        self.group = ClassGroup.objects.create(name='9-Д', grade=9, letter='Д')
        self.teacher.classes.add(self.group)
        self.subject = Subject.objects.create(name='Інформатика')
        self.teacher.subjects.add(self.subject)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Практична',
            description='Завдання.', status=Assignment.STATUS_PUBLISHED)
        self.assignment.classes.add(self.group)

    def make_submission(self, first, last, files=()):
        submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name=first, last_name=last, is_latest_attempt=True)
        for name, content in files:
            SubmissionFile.objects.create(
                submission=submission,
                original_name=name,
                file=SimpleUploadedFile(name, content))
        return submission


class DuplicateMultiFileTests(DuplicateBase):
    def test_multi_file_student_duplicate_is_detected(self):
        content = b'print("shared solution")  # identical copy\n'
        first = self.make_submission('Іван', 'Перший', [('solution.py', content)])
        second = self.make_submission('Петро', 'Другий', [('other_name.py', content)])
        info = check_submission_duplicates(second)
        self.assertTrue(info['is_duplicate'])
        self.assertTrue(info['is_duplicate_student'])
        self.assertEqual(info['duplicate_file_name'], 'other_name.py')
        self.assertTrue(info['matches'])
        self.assertEqual(info['matches'][0]['file'], 'other_name.py')
        self.assertEqual(info['submission_files'], ['other_name.py'])
        self.assertTrue(info['duplicate_student_name'])
        self.assertIsNotNone(first.pk)

    def test_multi_file_teacher_material_duplicate_is_detected(self):
        AssignmentFile.objects.create(
            assignment=self.assignment, original_name='material.txt',
            file=SimpleUploadedFile('material.txt', b'teacher material bytes'))
        submission = self.make_submission('Іван', 'Третій', [
            ('answer.txt', b'teacher material bytes')])
        info = check_submission_duplicates(submission)
        self.assertTrue(info['is_duplicate'])
        self.assertTrue(info['is_duplicate_teacher'])
        self.assertIn('вчителя', info['warning_message'].lower())

    def test_duplicate_ceiling_is_deterministic(self):
        content = b'def solution():\n    return 42\n'
        self.make_submission('Іван', 'Перший', [('a.py', content)])
        second = self.make_submission('Петро', 'Другий', [('b.py', content)])
        info = check_submission_duplicates(second)
        self.assertEqual(duplicate_ceiling(info), DUPLICATE_GRADE_CEILING)
        self.assertEqual(DUPLICATE_GRADE_CEILING, 4)
        self.assertIsNone(duplicate_ceiling({'is_duplicate_student': False}))
        self.assertIsNone(duplicate_ceiling({'is_duplicate_student': True, 'plagiarism_ignored': True}))
        self.assertIsNone(duplicate_ceiling({'is_duplicate_teacher': True}))
        self.assertIsNone(duplicate_ceiling(None))

    def test_ignored_plagiarism_still_reports_ignored(self):
        content = b'print("same")\n'
        first = self.make_submission('Іван', 'Перший', [('a.py', content)])
        second = self.make_submission('Петро', 'Другий', [('b.py', content)])
        first.ignore_plagiarism = True
        first.save(update_fields=['ignore_plagiarism'])
        info = check_submission_duplicates(second)
        self.assertFalse(info['is_duplicate'])
        self.assertTrue(info['plagiarism_ignored'])
        self.assertEqual(info['type'], 'ignored_plagiarism')
        self.assertIsNone(duplicate_ceiling(info))

    def test_submission_without_files_has_no_duplicate(self):
        empty = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, class_group=self.group,
            first_name='Нема', last_name='Файлів')
        info = check_submission_duplicates(empty)
        self.assertFalse(info['is_duplicate'])
        self.assertEqual(info['matches'], [])


class DuplicatePiiScopeTests(DuplicateBase):
    def raw_info(self):
        return {
            'is_duplicate': True, 'is_duplicate_student': True, 'is_duplicate_teacher': False,
            'type': 'student_duplicate', 'plagiarism_ignored': False, 'is_coauthor': False,
            'warning_message': 'Збіг: Іваненко Іван (9-Б)',
            'duplicate_student_name': 'Іваненко Іван', 'duplicate_class': '9-Б',
            'duplicate_student_first_name': 'Іван', 'duplicate_student_last_name': 'Іваненко',
            'duplicate_submission_id': 999, 'duplicate_submitted_at': timezone.now(),
            'duplicate_file_name': 'work.py',
            'matches': [{'type': 'student_duplicate', 'role': 'student', 'reason': 'binary',
                         'file': 'work.py', 'other_file': 'peer.py', 'student_name': 'Іваненко Іван',
                         'class': '9-Б', 'submission_id': 999}],
        }

    def test_public_scope_hides_other_pupil_details(self):
        request = RequestFactory().get('/')
        request.user = type('Anonymous', (), {'is_authenticated': False})()
        public = scope_duplicate_info(request, self.raw_info())
        serialized = json.dumps(public, ensure_ascii=False, default=str)
        self.assertTrue(public['is_duplicate'])
        for leaked in ('Іваненко', '9-Б', 'peer.py', '999'):
            self.assertNotIn(leaked, serialized)
        self.assertIn('інший учень', serialized)

    def test_teacher_scope_keeps_details(self):
        request = RequestFactory().get('/')
        request.user = self.user  # superuser -> teacher view
        raw = self.raw_info()
        visible = scope_duplicate_info(request, raw)
        self.assertIs(visible, raw)
        self.assertEqual(visible['duplicate_student_name'], 'Іваненко Іван')

    def test_explicit_role_override(self):
        public = scope_duplicate_info(None, self.raw_info(), role='public')
        self.assertNotIn('Іваненко', json.dumps(public, ensure_ascii=False, default=str))
        teacher = scope_duplicate_info(None, self.raw_info(), role='teacher')
        self.assertEqual(teacher['duplicate_student_name'], 'Іваненко Іван')


class ProvenanceEvidenceGateTests(SimpleTestCase):
    def test_model_boolean_and_percent_alone_never_accuse(self):
        result = normalize_authorship(
            {'ai_generated_detected': True, 'ai_generated_percent': 95,
             'ai_generated_details': 'Схоже на ШІ.'},
            False, tolerance_percent=25)
        self.assertFalse(result['ai_generated_detected'])
        self.assertNotEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_percent'], 95)
        self.assertIn('Некалібрована оцінка моделі', result['ai_generated_details'])
        report = result['ai_provenance_report']
        self.assertFalse(report['evidence_gate']['verified_medium_signal'])
        self.assertTrue(report['evidence_gate']['model_claim_ignored_as_uncalibrated'])
        self.assertEqual(report['model_estimate']['percent'], 95)
        self.assertFalse(report['model_estimate']['calibrated'])

    def test_model_claim_with_checked_files_is_none_not_suspected(self):
        result = normalize_authorship(
            {'ai_generated_detected': True, 'ai_generated_percent': 80}, False,
            provenance=[{'source': 'photo.jpg', 'checked': True, 'signals': [], 'limitations': []}])
        self.assertFalse(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'none')

    def test_verified_metadata_signal_still_suspected(self):
        result = normalize_authorship(
            {'ai_generated_detected': True, 'ai_generated_percent': 10}, False,
            provenance=[{'source': 'work.docx', 'checked': True, 'limitations': [], 'signals': [{
                'location': 'docProps/app.xml', 'basis': 'metadata', 'strength': 'medium',
                'observation': 'Застосунок документа згадує Gamma.'}]}])
        self.assertTrue(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'suspected')
        self.assertEqual(result['ai_generated_confidence'], 'medium')

    def test_verified_quote_remains_evidence(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'evidence': [{
                'source': 'work.txt', 'location': 'абзац 1', 'basis': 'self_disclosure',
                'observation': 'У роботі є зізнання: «Це речення справді є в роботі».'}]}},
            False, student_text='Тут є речення: це речення справді є в роботі.',
            file_names=['work.txt'])
        self.assertTrue(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'suspected')

    def test_unverified_style_alone_is_not_evidence(self):
        result = normalize_authorship(
            {'ai_authorship_analysis': {'evidence': [{
                'basis': 'text_style', 'observation': 'Типовий вступ.'}]}}, False)
        self.assertFalse(result['ai_generated_detected'])
        self.assertEqual(result['ai_authorship_state'], 'insufficient')


class BackupSchemaCompatibilityTests(TestCase):
    def test_new_field_missing_in_old_snapshot_is_ignored_with_warning(self):
        current = schema()
        old = {label: list(fields) for label, fields in current.items()}
        old['feed.submission'] = [name for name in old['feed.submission']
                                  if name != 'ai_technical_evidence']
        compatible, ignored, reason = schema_compatibility(old, current)
        self.assertTrue(compatible, reason)
        self.assertIn('feed.submission.ai_technical_evidence', ignored)
        self.assertEqual(reason, '')

    def test_current_snapshot_is_compatible_without_warnings(self):
        current = schema()
        compatible, ignored, _ = schema_compatibility(current, current)
        self.assertTrue(compatible)
        self.assertEqual(ignored, [])

    def test_removed_field_rejects_backup(self):
        current = schema()
        newer = {label: list(fields) for label, fields in current.items()}
        newer['feed.submission'].append('legacy_removed_field')
        compatible, _ignored, reason = schema_compatibility(newer, current)
        self.assertFalse(compatible)
        self.assertIn('legacy_removed_field', reason)

    def test_old_index_load_returns_warning(self):
        current = schema()
        old = {label: list(fields) for label, fields in current.items()}
        old['feed.submission'] = [name for name in old['feed.submission']
                                  if name != 'ai_technical_evidence']
        index = {'app': 'schoolwork', 'protocol': 1, 'scope': 'server', 'owner': 1, 'user': 1,
                 'schema': old, 'records': 0,
                 'options': {'materials': True, 'students': True, 'student_files': True}}
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'index.json').write_text(json.dumps(index), encoding='utf-8')
            with patch('feed.cloud_backups.read_records', return_value=[]):
                loaded_index, objects, pairs = Adapter().load(1, {'scope': 'server'}, directory)
                self.assertEqual(objects, [])
                self.assertEqual(pairs, [])
                self.assertTrue(loaded_index['schema_warnings'])
                self.assertIn('ai_technical_evidence', loaded_index['schema_warnings'][0])
                checked = Adapter().validate(1, {'scope': 'server'}, directory)
                self.assertTrue(checked.get('warnings'))

    def test_old_row_without_new_field_deserializes_to_default(self):
        from django.core import serializers
        rows = [{'model': 'feed.submission', 'pk': 424242, 'fields': {
            'first_name': 'Тест', 'last_name': 'Бекап'}}]
        objects = list(serializers.deserialize('json', json.dumps(rows)))
        self.assertEqual(objects[0].object.ai_technical_evidence, {})


class JournalAiSuggestionFlagTests(TestCase):
    def setUp(self):
        from .middleware import set_has_admin
        set_has_admin(True)
        self.user = User.objects.create_superuser('journal_teacher', password='pass')
        School.objects.create(name='Школа журналу', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Журналу')
        self.group = ClassGroup.objects.create(name='8-Ж', grade=8, letter='Ж')
        self.teacher.classes.add(self.group)
        self.student = Student.objects.create(first_name='Тарас', last_name='Шевченко',
                                              class_group=self.group)
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, title='Робота', status=Assignment.STATUS_PUBLISHED)
        self.assignment.classes.add(self.group)
        self.submission = Submission.objects.create(
            assignment=self.assignment, teacher=self.teacher, student=self.student,
            first_name='Тарас', last_name='Шевченко', class_group=self.group,
            ai_suggested_grade='7', ai_status='success', is_latest_attempt=True)
        self.token = JournalAPIKey.generate_key()
        JournalAPIKey.objects.create(name='Ключ', key=self.token, teacher=self.teacher,
                                     can_export_grades=True, is_active=True)

    def test_ai_grade_is_flagged_as_suggestion(self):
        response = self.client.get(reverse('api_journal_export_grades'),
                                   {'only_graded': '0'},
                                   HTTP_AUTHORIZATION=f'Bearer {self.token}')
        self.assertEqual(response.status_code, 200)
        row = response.json()['grades'][0]
        self.assertEqual(row['ai_suggested_grade'], '7')
        self.assertTrue(row['is_ai_suggestion'])
        self.assertFalse(row['ai_suggestion_confirmed_by_teacher'])
        self.assertEqual(row['grade'], '')
