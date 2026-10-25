"""Server receipt time survives exports and delayed coauthor registration."""
from datetime import datetime, timezone as dt_timezone
from importlib import import_module
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from .tests_merge_and_coauthors import MergeAndCoauthorsTests
from .models import Submission, SubmissionActivityLog


class SubmissionTimeTests(MergeAndCoauthorsTests):
    def make_submission(self, **extra):
        return Submission.objects.create(
            assignment=self.assignment, student=self.target_student,
            class_group=self.group, teacher=self.teacher,
            first_name=self.target_student.first_name,
            last_name=self.target_student.last_name, **extra)

    def test_later_coauthor_keeps_original_time_and_edits_do_not_reset_it(self):
        root = self.make_submission(file='synthetic/shared.txt')
        original = datetime(2026, 10, 23, 11, 27, tzinfo=dt_timezone.utc)
        Submission.objects.filter(pk=root.pk).update(submitted_at=original)
        root.refresh_from_db()
        copy = self.make_submission(file=root.file.name, is_group_work=True, primary_submission=root)
        self.assertEqual(copy.submitted_at, original)
        copy.grade = '10'
        copy.save(update_fields=['grade'])
        copy.refresh_from_db()
        self.assertEqual(copy.submitted_at, original)

    def test_repair_only_explicit_copies_and_records_previous_time(self):
        root = self.make_submission(file='synthetic/shared.txt')
        copy = self.make_submission(file=root.file.name, is_group_work=True, primary_submission=root)
        unlinked = self.make_submission(file=root.file.name)
        later = root.submitted_at + timezone.timedelta(days=3)
        Submission.objects.filter(pk=copy.pk).update(submitted_at=later)
        own_upload = self.make_submission(file=root.file.name, is_group_work=True, primary_submission=root)
        Submission.objects.filter(pk=own_upload.pk).update(submitted_at=later)
        SubmissionActivityLog.objects.create(submission=own_upload, action_type='submission', description='Synthetic upload')
        migration = import_module('feed.migrations.0070_coauthor_submission_time')
        from types import SimpleNamespace
        migration.repair_coauthors(apps, SimpleNamespace(connection=connection))
        copy.refresh_from_db(); unlinked.refresh_from_db(); own_upload.refresh_from_db()
        self.assertEqual(copy.submitted_at, root.submitted_at)
        self.assertNotEqual(unlinked.submitted_at, root.submitted_at)
        self.assertEqual(own_upload.submitted_at, later)
        audit = SubmissionActivityLog.objects.get(submission=copy)
        self.assertIn(later.isoformat(), audit.description)

    @override_settings(TIME_ZONE='Europe/Kyiv')
    def test_csv_uses_kyiv_time(self):
        sub = self.make_submission(grade="10")
        for utc, expected in [
            (datetime(2026,10,23,11,27,tzinfo=dt_timezone.utc), '23.10.2026 14:27'),
            (datetime(2026,10,25,11,27,tzinfo=dt_timezone.utc), '25.10.2026 13:27'),
        ]:
            Submission.objects.filter(pk=sub.pk).update(submitted_at=utc)
            response = self.client.get(reverse('export_grades'))
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.content.decode('utf-8-sig'))

    def test_detailed_pdf_uses_submission_date_offset(self):
        import io
        from pypdf import PdfReader
        from .reports_service import generate_teacher_report_pdf
        sub = self.make_submission(grade='10')
        Submission.objects.filter(pk=sub.pk).update(
            submitted_at=datetime(2026, 10, 23, 11, 27, tzinfo=dt_timezone.utc))
        sub.refresh_from_db()
        buffer = io.BytesIO()
        generate_teacher_report_pdf(buffer, {'title':'Synthetic','teacher_name':'Synthetic'}, [sub], mode='detailed')
        text = '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(buffer.getvalue())).pages)
        self.assertIn('23.10.2026 14:27', text)
