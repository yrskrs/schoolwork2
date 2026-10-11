from django.test import TestCase, Client
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.auth.models import User
from django.utils import timezone
from feed.models import Student, Submission, SubmissionFile, SubmissionActivityLog, Teacher, RosterReplica
from feed import tests_school_api_v2 as fixtures
from school_sync.django_backend import operate


class StudentDeletionTests(TestCase):
    setUp = fixtures.SchoolAPIV2Tests.setUp
    sub = fixtures.SchoolAPIV2Tests.sub
    export = fixtures.SchoolAPIV2Tests.export

    def delete(self, **values):
        return self.client.post(reverse('teacher_student_delete', args=[self.student.pk]), values)

    def test_deletes_pupil_all_attempts_and_files_logs_survive_and_twin_is_preserved(self):
        first = self.sub(file=SimpleUploadedFile('individual.txt', b'synthetic work'))
        second = self.sub()
        extra_file = SubmissionFile.objects.create(submission=second, file=SimpleUploadedFile('extra.txt', b'another synthetic file'))
        files = [(first.file.storage, first.file.name), (extra_file.file.storage, extra_file.file.name)]
        twin_work = Submission.objects.create(student=self.twin, assignment=self.assignment, class_group=self.group, last_name='Вигаданий', first_name='Учень')
        student_pk = self.student.pk
        with self.captureOnCommitCallbacks(execute=True):
            response = self.delete()
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Student.objects.filter(pk=student_pk).exists())
        self.assertFalse(Submission.objects.filter(pk__in=[first.pk, second.pk]).exists())
        self.assertFalse(SubmissionFile.objects.filter(pk=extra_file.pk).exists())
        self.assertTrue(Student.objects.filter(pk=self.twin.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=twin_work.pk).exists())
        self.assertTrue(all(not storage.exists(name) for storage, name in files))
        self.assertEqual(SubmissionActivityLog.objects.filter(action_type='delete').count(), 2)
        log = SubmissionActivityLog.objects.get(action_type='student_deleted')
        self.assertEqual(log.actor_id, self.user.pk)
        self.assertIn(f'#{student_pk}', log.description)
        self.assertFalse(SubmissionActivityLog.objects.filter(action_type='delete', submission__isnull=False).exists())
        self.assertNotContains(self.client.get(reverse('teacher_students')), f'name="student_id" value="{student_pk}"')
        self.assertEqual(self.export().json()['grades'], [])

    def test_shared_group_files_and_other_authors_grades_survive(self):
        root = self.sub(file=SimpleUploadedFile('shared.txt', b'shared group work'))
        sf = SubmissionFile.objects.create(submission=root, file=SimpleUploadedFile('attachment.txt', b'group attachment'))
        peer = Submission.objects.create(student=self.twin, assignment=self.assignment, class_group=self.group,
            last_name='Вигаданий', first_name='Учень', primary_submission=root, is_group_work=True,
            file=root.file, grade='10', graded_by=self.user, graded_at=timezone.now())
        copy = SubmissionFile.objects.create(submission=peer, file=sf.file)
        with self.captureOnCommitCallbacks(execute=True): self.delete()
        peer.refresh_from_db()
        self.assertIsNone(peer.primary_submission_id)
        self.assertEqual(peer.grade, '10')
        self.assertTrue(peer.file.storage.exists(peer.file.name))
        self.assertTrue(copy.file.storage.exists(copy.file.name))

    def test_shared_roster_records_capture_deletion_without_recreating_native_profile(self):
        operate(lambda engine, store: engine.share('class', self.group.pk))
        ref = RosterReplica.objects.get(data__kind='student', data__native_id=self.student.pk).ref
        self.delete()
        replica = RosterReplica.objects.get(ref=ref)
        self.assertFalse(replica.data['payload']['active'])
        operate(lambda engine, store: engine.capture())
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())

    def test_remote_change_of_deleted_profile_becomes_manual_conflict(self):
        import uuid
        operate(lambda engine, store: engine.share('class', self.group.pk))
        self.delete()
        incoming = operate(lambda engine, store: engine.snapshot())
        row = next(row for row in incoming['records'] if row['kind'] == 'student' and row['payload']['name'] == self.student.get_full_name() and not row['payload']['active'])
        row['payload']['active'] = True
        incoming['origin'] = str(uuid.uuid4())
        row['clock'][incoming['origin']] = 1
        operate(lambda engine, store: engine.receive(incoming))
        self.assertFalse(Student.objects.filter(pk=self.student.pk).exists())
        self.assertEqual(RosterReplica.objects.get(ref=row['ref']).data['status'], 'conflict')

    def test_delete_rejects_get_csrf_and_teacher_without_class(self):
        url = reverse('teacher_student_delete', args=[self.student.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(url).status_code, 403)
        other_user = User.objects.create_user('unassigned-delete')
        Teacher.objects.create(user=other_user, full_name='Інший')
        self.client.force_login(other_user)
        self.assertEqual(self.delete().status_code, 403)
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())

    def test_foreign_teacher_submission_blocks_whole_delete_with_no_partial_change(self):
        self.sub()
        other_user = User.objects.create_user('another-owner-delete')
        teacher = Teacher.objects.create(user=other_user, full_name='Інший')
        Submission.objects.create(student=self.student, class_group=self.group, teacher=teacher, last_name='Вигаданий', first_name='Учень')
        self.assertEqual(self.delete().status_code, 403)
        self.assertEqual(self.student.submissions.count(), 2)
        self.assertFalse(SubmissionActivityLog.objects.filter(action_type='delete').exists())

    def test_single_submission_delete_logs_and_keeps_shared_file(self):
        root = self.sub(file=SimpleUploadedFile('retained.txt', b'synthetic file'))
        other = self.sub(file=root.file)
        url = reverse('delete_submission', args=[root.pk])
        with self.captureOnCommitCallbacks(execute=True): response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Submission.objects.filter(pk=other.pk).exists())
        self.assertTrue(other.file.storage.exists(other.file.name))
        log = SubmissionActivityLog.objects.get(action_type='delete')
        self.assertIn(f'#{root.pk}', log.description)
        self.assertIsNone(log.submission_id)

    def test_file_deletion_waits_for_commit_and_rollbacks_preserve_files(self):
        from django.db import transaction
        from feed.student_deletion import delete_student
        row = self.sub(file=SimpleUploadedFile('rollback.txt', b'synthetic preserved work'))
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                delete_student(self.student.pk, self.user)
                raise RuntimeError('synthetic rollback')
        self.assertTrue(Student.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(Submission.objects.filter(pk=row.pk).exists())
        self.assertTrue(row.file.storage.exists(row.file.name))
        self.assertFalse(SubmissionActivityLog.objects.filter(action_type='student_deleted').exists())

    def test_stable_profile_page_has_delete_form_and_deleted_profile_returns_404(self):
        url = reverse('student_detail', args=[self.student.get_full_name()]) + f'?student_id={self.student.pk}'
        self.assertContains(self.client.get(url), 'Видалити учня та роботи')
        self.delete()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_legacy_name_link_never_exposes_delete_action_for_ambiguous_twins(self):
        self.sub()
        response = self.client.get(reverse('student_detail', args=[self.student.get_full_name()]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'action="{reverse("teacher_student_delete", args=[self.student.pk])}"')
