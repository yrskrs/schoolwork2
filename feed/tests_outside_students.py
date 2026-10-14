import uuid
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from feed import tests_school_api_v2 as fixtures
from feed.forms import SubmissionForm
from feed.models import ClassGroup, Student, Submission, Teacher, RosterState
from feed.outside_students import browser_identity
from django.contrib.sessions.backends.signed_cookies import SessionStore
from types import SimpleNamespace


class OutsideStudentsTests(TestCase):
    setUp = fixtures.SchoolAPIV2Tests.setUp
    export = fixtures.SchoolAPIV2Tests.export

    def submit(self, identity):
        form = SubmissionForm({'full_name': 'Підмінний Учень', 'class_group': self.group.pk,
            'other_student': 'on', 'comment_student': 'Синтетична відповідь'}, assignment=self.assignment)
        self.assertTrue(form.is_valid(), form.errors)
        form.outside_student_id = identity
        return form.save(self.assignment)

    def test_two_works_and_retake_use_one_identity_without_a_class_or_profile(self):
        identity = uuid.uuid4()
        before = (ClassGroup.objects.count(), Student.objects.count())
        first = self.submit(identity)
        first.grade, first.graded_by, first.graded_at = '8', self.user, timezone.now()
        first.save()
        exported = self.export().json()['grades'][0]
        second = self.submit(identity)
        self.assertEqual(second.previous_submission_id, first.pk)
        self.assertEqual([row.pk for row in second.get_all_attempts()], [first.pk, second.pk])
        self.assertEqual(self.export().json()['grades'], [])
        second.grade, second.graded_by, second.graded_at = '10', self.user, timezone.now()
        second.save()
        updated = self.export().json()['grades'][0]
        self.assertEqual(exported['id'], updated['id'])
        self.assertEqual(updated['student_key'], 'outside:' + str(identity))
        self.assertEqual(before, (ClassGroup.objects.count(), Student.objects.count()))
        page = self.client.get(reverse('outside_students'))
        self.assertContains(page, 'Підмінний Учень')
        self.assertEqual(len(page.context['page_obj']), 1)
        self.assertEqual(len(page.context['page_obj'][0]['works']), 2)

    def test_browser_identity_is_stable_but_names_and_browsers_are_distinct(self):
        request = SimpleNamespace(session=SessionStore())
        first = browser_identity(request, self.teacher.pk, 'Підмінний Учень')
        self.assertEqual(first, browser_identity(request, self.teacher.pk, 'Підмінний  Учень'))
        other = SimpleNamespace(session=SessionStore())
        self.assertNotEqual(first, browser_identity(other, self.teacher.pk, 'Підмінний Учень'))
        self.assertNotEqual(first, browser_identity(request, self.teacher.pk, 'Інший Учень'))

    def test_real_submit_route_reuses_the_browser_identity(self):
        self.assignment.status = self.assignment.STATUS_PUBLISHED
        self.assignment.save()
        pupil = Client()
        url = reverse('submit_assignment', args=[self.assignment.pk])
        data = {'full_name': 'Підмінний Учень', 'class_group': self.group.pk,
                'other_student': 'on', 'comment_student': 'Відповідь'}
        self.assertEqual(pupil.post(url, data).status_code, 302)
        first = Submission.objects.latest('pk')
        self.assertEqual(pupil.post(url, data).status_code, 302)
        second = Submission.objects.latest('pk')
        self.assertEqual(first.outside_student_id, second.outside_student_id)
        self.assertEqual(second.previous_submission_id, first.pk)

    def test_same_names_and_other_teachers_history_are_not_combined(self):
        self.submit(uuid.uuid4())
        self.submit(uuid.uuid4())
        page = self.client.get(reverse('outside_students'), {'q': 'Підмінний', 'class': self.group.pk})
        self.assertEqual(len(page.context['page_obj']), 2)
        self.assertEqual(Client().get(reverse('outside_students')).status_code, 302)
        other = Teacher.objects.create(user=get_user_model().objects.create_user(username='another-outside-teacher'), full_name='Інший учитель')
        self.assignment.teacher = other
        self.assignment.save()
        page = self.client.get(reverse('outside_students'))
        self.assertEqual(len(page.context['page_obj']), 0)

    def test_explicit_outside_work_cannot_be_claimed_by_same_name_profile(self):
        outside = self.submit(uuid.uuid4())
        pupil = Student.objects.create(class_group=self.group, last_name=outside.last_name, first_name=outside.first_name)
        self.assertEqual(pupil.sync_submissions(), 0)
        self.assertEqual(pupil.get_submissions_count(), 0)
        outside.refresh_from_db()
        self.assertIsNone(outside.student_id)

    def test_old_service_class_is_not_imported_or_offered_as_a_teaching_class(self):
        from feed.tests_roster_transfer import JournalRosterReceiveTests
        payload = JournalRosterReceiveTests.payload(self)
        payload['class'].update(name='7-А (Позакласні)', letter='ПК')
        before = ClassGroup.objects.count()
        response = self.client.post('/api/v2/journal/roster/', payload, content_type='application/json',
            HTTP_AUTHORIZATION='Bearer synthetic-key')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(ClassGroup.objects.count(), before)

    def test_native_pupil_absent_from_journal_list_is_visible_as_separate(self):
        fixtures.SchoolAPIV2Tests.sub(self)
        Submission.objects.create(assignment=self.assignment, student=self.twin, class_group=self.group,
            last_name=self.twin.last_name, first_name=self.twin.first_name)
        RosterState.objects.update_or_create(pk=1, defaults={'data': {'journal_imports': {
            f'{self.teacher.pk}:synthetic': {'class_id': self.group.pk,
                'students': {'known': {'native_id': self.student.pk}}}}}})
        page = self.client.get(reverse('outside_students'))
        self.assertEqual(len(page.context['page_obj']), 1)
        self.assertEqual(page.context['page_obj'][0]['key'], f'student:{self.twin.pk}')
