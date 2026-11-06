"""Directory links must identify pupils and respect the profile view's access rules."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from feed.models import Teacher, ClassGroup, Student


class StudentProfileLinkTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('profile-link-admin', password='synthetic')
        self.user = User.objects.create_user('profile-link-audit')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Перевірка')
        self.group = ClassGroup.objects.create(name='5-Т', grade=5, letter='Т')
        self.student = Student.objects.create(class_group=self.group, last_name='Вигаданий', first_name='Учень')
        self.client.force_login(self.user)

    def test_assigned_pupil_without_submissions_has_working_id_link(self):
        self.teacher.classes.add(self.group)
        # Two identical names must never send the teacher to an ambiguous name-only page.
        twin = Student.objects.create(class_group=self.group, last_name='Вигаданий', first_name='Учень')
        page = self.client.get(reverse('teacher_students'))
        for student in (self.student, twin):
            url = reverse('student_detail', args=[student.get_full_name()]) + f'?student_id={student.pk}'
            self.assertContains(page, 'href="' + url.replace('&', '&amp;') + '"')
            profile = self.client.get(url)
            self.assertEqual(profile.status_code, 200)
            self.assertEqual(profile.context['student_obj'].pk, student.pk)

    def test_unassigned_directory_row_has_no_inaccessible_profile_link(self):
        page = self.client.get(reverse('teacher_students'))
        self.assertContains(page, self.student.last_name)
        self.assertNotContains(page, 'href="' + reverse('student_detail', args=[self.student.get_full_name()]))
        url = reverse('student_detail', args=[self.student.get_full_name()]) + f'?student_id={self.student.pk}'
        self.assertEqual(self.client.get(url).status_code, 403)
