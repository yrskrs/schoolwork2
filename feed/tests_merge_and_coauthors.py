from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .middleware import set_has_admin
from .models import (
    Assignment,
    ClassGroup,
    School,
    Student,
    Submission,
    SubmissionActivityLog,
    SubmissionFile,
    Teacher,
)


class MergeAndCoauthorsTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_superuser('teacher_admin', password='test-password')
        self.school = School.objects.create(name='Тестова гімназія', admin=self.user)
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Інформатики')
        self.group = ClassGroup.objects.create(name='7-Б', grade=7)
        self.teacher.classes.add(self.group)

        self.assignment = Assignment.objects.create(
            teacher=self.teacher,
            title='Створення веб-сторінки',
            description='Створіть HTML сторінку на вільну тему.',
            status='published'
        )
        self.assignment.classes.add(self.group)

        # Create two students: target (correct profile) and source (duplicate profile with typo)
        self.target_student = Student.objects.create(
            class_group=self.group,
            first_name='Олександр',
            last_name='Коваленко',
            notes='Активний на уроках'
        )
        self.source_student = Student.objects.create(
            class_group=self.group,
            first_name='Олександр',
            last_name='Коваленко-дубль',
            notes='Помилково зареєстрований'
        )

        self.client.force_login(self.user)

    def test_student_merge_success_and_activity_logged(self):
        # Create an earlier submission for target
        sub_target = Submission.objects.create(
            assignment=self.assignment,
            student=self.target_student,
            class_group=self.group,
            teacher=self.teacher,
            first_name=self.target_student.first_name,
            last_name=self.target_student.last_name,
            is_latest_attempt=True
        )

        # Create a newer submission for source (duplicate)
        sub_source = Submission.objects.create(
            assignment=self.assignment,
            student=self.source_student,
            class_group=self.group,
            teacher=self.teacher,
            first_name=self.source_student.first_name,
            last_name=self.source_student.last_name,
            is_latest_attempt=True
        )

        url = reverse('teacher_student_merge')
        response = self.client.post(url, {
            'source_student_id': self.source_student.id,
            'target_student_id': self.target_student.id,
            'format': 'json'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['merged_count'], 1)

        # Verify source student deleted
        self.assertFalse(Student.objects.filter(id=self.source_student.id).exists())

        # Verify sub_source reassigned to target student
        sub_source.refresh_from_db()
        self.assertEqual(sub_source.student, self.target_student)
        self.assertEqual(sub_source.last_name, self.target_student.last_name)
        self.assertEqual(sub_source.first_name, self.target_student.first_name)

        # Verify notes combined
        self.target_student.refresh_from_db()
        self.assertIn('Активний на уроках', self.target_student.notes)
        self.assertIn('Помилково зареєстрований', self.target_student.notes)

        # Verify latest attempt flag: later submission is True, earlier is False
        sub_target.refresh_from_db()
        self.assertFalse(sub_target.is_latest_attempt)
        self.assertTrue(sub_source.is_latest_attempt)

        # Verify activity log entry
        log_entry = SubmissionActivityLog.objects.filter(action_type='students_merged').first()
        self.assertIsNotNone(log_entry)
        self.assertIn('Коваленко-дубль', log_entry.description)
        self.assertIn('Коваленко', log_entry.description)

    def test_student_merge_same_student_fails(self):
        url = reverse('teacher_student_merge')
        response = self.client.post(url, {
            'source_student_id': self.target_student.id,
            'target_student_id': self.target_student.id,
            'format': 'json'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])

    def test_assign_coauthors_creates_linked_submission_and_logs(self):
        # Create second student
        student2 = Student.objects.create(
            class_group=self.group,
            first_name='Марія',
            last_name='Шевченко'
        )

        # Primary submission with file and grade
        primary_sub = Submission.objects.create(
            assignment=self.assignment,
            student=self.target_student,
            class_group=self.group,
            teacher=self.teacher,
            first_name=self.target_student.first_name,
            last_name=self.target_student.last_name,
            grade='11',
            teacher_comment='Відмінний проект!'
        )
        test_file = SimpleUploadedFile('project.html', b'<h1>Hello World</h1>')
        SubmissionFile.objects.create(
            submission=primary_sub,
            file=test_file,
            original_name='project.html'
        )

        url = reverse('teacher_submission_coauthors', kwargs={'sub_id': primary_sub.id})
        response = self.client.post(url, {
            'student_ids': [student2.id],
            'format': 'json'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(data['is_group_work'])

        # Refresh primary
        primary_sub.refresh_from_db()
        self.assertTrue(primary_sub.is_group_work)
        self.assertIn('Шевченко Марія', primary_sub.group_authors)
        self.assertIn('Коваленко Олександр', primary_sub.group_authors)

        # Verify student2 submission created and linked
        coauthor_sub = Submission.objects.filter(
            assignment=self.assignment,
            student=student2
        ).first()
        self.assertIsNotNone(coauthor_sub)
        self.assertEqual(coauthor_sub.primary_submission, primary_sub)
        self.assertTrue(coauthor_sub.is_group_work)
        self.assertEqual(str(coauthor_sub.grade), '11')
        self.assertEqual(coauthor_sub.teacher_comment, 'Відмінний проект!')
        self.assertEqual(coauthor_sub.files.count(), 1)
        self.assertEqual(coauthor_sub.files.first().original_name, 'project.html')

        # Verify activity log
        log_entry = SubmissionActivityLog.objects.filter(
            submission=primary_sub,
            action_type='group_authors'
        ).first()
        self.assertIsNotNone(log_entry)
        self.assertIn('оновив склад колективної роботи', log_entry.description.lower())

    def test_remove_coauthors_resets_primary_and_unlinks(self):
        student2 = Student.objects.create(
            class_group=self.group,
            first_name='Іван',
            last_name='Мельник'
        )
        primary_sub = Submission.objects.create(
            assignment=self.assignment,
            student=self.target_student,
            class_group=self.group,
            teacher=self.teacher,
            first_name=self.target_student.first_name,
            last_name=self.target_student.last_name,
            is_group_work=True,
            group_authors='Коваленко Олександр, Мельник Іван'
        )
        coauthor_sub = Submission.objects.create(
            assignment=self.assignment,
            student=student2,
            class_group=self.group,
            teacher=self.teacher,
            first_name=student2.first_name,
            last_name=student2.last_name,
            is_group_work=True,
            group_authors='Коваленко Олександр, Мельник Іван',
            primary_submission=primary_sub
        )

        url = reverse('teacher_submission_coauthors', kwargs={'sub_id': primary_sub.id})
        # Empty POST with no coauthor IDs
        response = self.client.post(url, {
            'format': 'json'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 200)
        primary_sub.refresh_from_db()
        coauthor_sub.refresh_from_db()

        self.assertFalse(primary_sub.is_group_work)
        self.assertEqual(primary_sub.group_authors, '')
        self.assertIsNone(coauthor_sub.primary_submission)
        self.assertFalse(coauthor_sub.is_group_work)
