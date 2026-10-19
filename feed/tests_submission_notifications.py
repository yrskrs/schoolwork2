from datetime import timedelta
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .forms import SubmissionForm
from .models import Assignment, ClassGroup, School, Student, Subject, Submission, Teacher
from .submission_notifications import (
    SubmissionNotification, SubmissionNotificationSettings,
    submission_notifications,
)


class SubmissionNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('notice-admin', password='test')
        cls.user = get_user_model().objects.create_user('notice-teacher', password='test')
        cls.teacher = Teacher.objects.create(user=cls.user, full_name='Тестовий Учитель')
        cls.other_user = get_user_model().objects.create_user('notice-other', password='test')
        cls.other_teacher = Teacher.objects.create(user=cls.other_user, full_name='Інший Учитель')
        School.objects.create(name='Тестова школа', admin=cls.admin)
        cls.group = ClassGroup.objects.create(name='7-А')
        cls.other_group = ClassGroup.objects.create(name='8-Б')
        cls.teacher.classes.add(cls.group)
        cls.subject = Subject.objects.create(name='Інформатика')
        cls.student = Student.objects.create(first_name='Олександра', last_name='Шевченко', class_group=cls.group)
        cls.assignment = Assignment.objects.create(
            teacher=cls.teacher, subject=cls.subject, title='Практична робота', status='published',
            published_at=timezone.now(),
        )
        cls.assignment.classes.add(cls.group, cls.other_group)
        cls.settings = SubmissionNotificationSettings.objects.create(teacher=cls.teacher)

    def setUp(self):
        self.client.force_login(self.user)

    def enable(self, **kwargs):
        values = {'teacher_enabled': True, 'students_enabled': True, **kwargs}
        SubmissionNotificationSettings.objects.filter(pk=self.settings.pk).update(**values)

    def event(self, **kwargs):
        sub = Submission.objects.create(assignment=self.assignment, class_group=self.group,
            first_name=self.student.first_name, last_name=self.student.last_name, **kwargs)
        SubmissionNotification.objects.create(submission=sub, teacher=self.teacher, for_teacher=True, for_students=True)
        return sub

    def poll(self, client=None, **params):
        return (client or self.client).get(reverse('submission_notifications'), params)

    def form(self, **kwargs):
        form = SubmissionForm({'student_id': self.student.pk, 'full_name': self.student.get_full_name(),
            'class_group': self.group.pk, 'comment_student': 'Виконана робота', **kwargs}, assignment=self.assignment)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_defaults_disabled_and_initial_poll_does_not_replay_history(self):
        self.event()
        data = self.poll(since=0).json()
        self.assertFalse(data['enabled'])
        self.assertEqual(data['events'], [])
        self.assertEqual(data['poll_after'], 30)
        self.enable()
        data = self.poll().json()
        self.assertEqual(data['events'], [])
        self.assertEqual(data['cursor'], SubmissionNotification.objects.latest('pk').pk)

    def test_real_submit_route_records_one_notice_and_repeat_flags(self):
        self.enable(teacher_sound=True, students_sound=False, duration_seconds=9)
        student = Client()
        baseline = self.poll().json()['cursor']
        values = {'student_id': self.student.pk, 'full_name': self.student.get_full_name(),
                  'class_group': self.group.pk, 'comment_student': 'Готово'}
        for attempt in (1, 2):
            response = student.post(reverse('submit_assignment', args=[self.assignment.pk]), values)
            self.assertEqual(response.status_code, 302, response.content[:500])
            data = self.poll(since=baseline).json()
            self.assertEqual(len(data['events']), 1)
            event = data['events'][0]
            self.assertEqual(event['name'], 'Шевченко Олександра')
            self.assertEqual(event['class_name'], '7-А')
            self.assertEqual(event['resubmission'], attempt == 2)
            self.assertEqual(event['attempt'], attempt)
            self.assertEqual(self.client.get(event['url']).status_code, 200)
            self.assertEqual(data['policies'][str(self.teacher.pk)], {
                'sound': True, 'duration': 9, 'sticky': False, 'click_opens_task': True,
                'all_classes': True, 'background_active': True, 'show_class': False,
                'template': 'work_submitted', 'sound_preset': 'chime',
            })
            baseline = data['cursor']
        self.assertEqual(SubmissionNotification.objects.count(), 2)
        self.assertEqual(self.poll(since=baseline).json()['events'], [])

    def test_grading_and_file_field_updates_do_not_create_notices(self):
        self.enable()
        sub = self.form().save(self.assignment)
        sub.grade = '10'
        sub.save()
        self.assertEqual(SubmissionNotification.objects.count(), 1)

    def test_invalid_form_and_rolled_back_submission_create_no_notice(self):
        self.enable()
        response = Client().post(reverse('submit_assignment', args=[self.assignment.pk]), {})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SubmissionNotification.objects.exists())
        form = self.form()
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                form.save(self.assignment)
                raise RuntimeError('Upload transaction failed')
        self.assertFalse(SubmissionNotification.objects.exists())
        self.assertFalse(Submission.objects.exists())

    def test_group_work_produces_one_event_with_authors(self):
        self.enable()
        coauthor = Student.objects.create(first_name='Андрій', last_name='Коваль', class_group=self.group)
        form = self.form(coauthor_ids=json.dumps([coauthor.pk]))
        submission = form.save(self.assignment)
        self.assertEqual(SubmissionNotification.objects.count(), 1)
        self.assertEqual(SubmissionNotification.objects.get().submission, submission)
        self.assertEqual(Submission.objects.filter(primary_submission=submission).count(), 1)
        self.enable()
        self.assertIn('Коваль', self.poll(since=0).json()['events'][0]['authors'])

    def test_enabling_does_not_replay_work_submitted_while_recipient_was_disabled(self):
        # With both recipients disabled, there is no event to store or replay.
        self.form().save(self.assignment)
        self.assertFalse(SubmissionNotification.objects.exists())
        self.enable(teacher_enabled=False)
        self.form().save(self.assignment)
        event = SubmissionNotification.objects.get()
        self.assertFalse(event.for_teacher)
        self.assertTrue(event.for_students)
        self.enable()
        self.assertEqual(self.poll(since=0).json()['events'], [])
        self.assertEqual(len(self.poll(Client(), since=0, **{'class': self.group.pk}).json()['events']), 1)

    def test_teachers_only_receive_their_own_events_even_with_role_parameters(self):
        self.enable()
        sub = self.event()
        self.client.force_login(self.other_user)
        SubmissionNotificationSettings.objects.create(teacher=self.other_teacher, teacher_enabled=True)
        data = self.poll(since=0, role='teacher', teacher=self.teacher.pk, **{'class': self.group.pk}).json()
        self.assertEqual(data['events'], [])
        self.assertNotEqual(self.client.get(reverse('view_file', args=[sub.pk])).status_code, 200)

    def test_students_receive_only_selected_class_and_no_review_links_or_grades(self):
        self.enable(students_click_opens_task=False, students_all_classes=False)
        self.event(grade='12', teacher_comment='Приватний коментар')
        student = Client()
        data = self.poll(student, since=0, **{'class': self.group.pk}).json()
        self.assertEqual(len(data['events']), 1)
        self.assertEqual(data['events'][0]['url'], '')
        self.assertNotIn('grade', data['events'][0])
        self.assertNotIn('teacher_comment', data['events'][0])
        self.assertFalse(self.poll(student, since=0).json()['enabled'])
        self.assertEqual(self.poll(student, since=0, **{'class': self.other_group.pk}).json()['events'], [])

    def test_student_task_link_and_all_classes_delivery(self):
        self.enable(students_click_opens_task=True, students_all_classes=True, show_class_badge=False)
        self.event()
        student = Client()
        # Student receives notice without class parameter
        data = self.poll(student, since=0).json()
        self.assertTrue(data['enabled'])
        self.assertEqual(len(data['events']), 1)
        event = data['events'][0]
        self.assertEqual(event['url'], reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertFalse(event['show_class'])
        self.assertNotIn('/teacher/', event['url'])

        # Now test show_class_badge
        self.enable(show_class_badge=True)
        data_badge = self.poll(student, since=0).json()
        self.assertTrue(data_badge['events'][0]['show_class'])

    def test_individual_draft_and_archived_tasks_are_hidden_from_student_notices(self):
        self.enable()
        self.event()
        for values in ({'is_individual': True}, {'status': 'draft'}, {'status': 'archived'}):
            Assignment.objects.filter(pk=self.assignment.pk).update(is_individual=False, status='published')
            Assignment.objects.filter(pk=self.assignment.pk).update(**values)
            self.assertEqual(self.poll(Client(), since=0, **{'class': self.group.pk}).json()['events'], [])
            self.assertEqual(len(self.poll(since=0).json()['events']), 1)

    def test_student_and_teacher_switches_and_sounds_are_independent(self):
        self.event()
        self.enable(teacher_enabled=False, students_sound=True)
        self.assertEqual(self.poll(since=0).json()['events'], [])
        data = self.poll(Client(), since=0, **{'class': self.group.pk}).json()
        self.assertTrue(data['policies'][str(self.teacher.pk)]['sound'])
        self.enable(students_enabled=False)
        self.assertEqual(self.poll(Client(), since=0, **{'class': self.group.pk}).json()['events'], [])
        self.assertEqual(len(self.poll(since=0).json()['events']), 1)

    def test_pagination_has_no_drops_even_after_long_time_in_hidden_tab(self):
        self.enable()
        for _ in range(28):
            self.event()
        data = self.poll(since=0).json()
        self.assertEqual(len(data['events']), 25)
        self.assertTrue(data['has_more'])
        rest = self.poll(since=data['cursor']).json()
        self.assertEqual(len(rest['events']), 3)
        self.assertFalse(rest['has_more'])
        self.assertEqual(len({row['id'] for row in data['events'] + rest['events']}), 28)
        SubmissionNotification.objects.update(created_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(len(self.poll(since=0).json()['events']), 25)

    def test_restore_and_deleted_submission_are_handled(self):
        self.enable()
        sub = self.event()
        self.assertEqual(self.poll(since=99999).json()['events'], [])
        sub.delete()
        self.assertFalse(SubmissionNotification.objects.exists())

    def test_changed_assignment_owner_does_not_leak_old_notice(self):
        self.enable()
        self.event()
        Assignment.objects.filter(pk=self.assignment.pk).update(teacher=self.other_teacher)
        self.assertEqual(self.poll(since=0).json()['events'], [])

    def test_settings_are_saved_for_current_teacher_only(self):
        response = self.client.post(reverse('teacher_settings'), {
            'action': 'save_submission_notifications', 'teacher': self.other_teacher.pk,
            'teacher_enabled': 'on', 'students_enabled': 'on', 'students_sound': 'on',
            'duration_seconds': '12', 'until_dismissed': 'on',
            'text_template': 'task_completed', 'sound_preset': 'marimba',
        })
        self.assertRedirects(response, reverse('teacher_settings') + '?tab=environment&section=notifications#submission-notification-settings')
        self.settings.refresh_from_db()
        self.assertTrue(self.settings.teacher_enabled)
        self.assertFalse(self.settings.teacher_sound)
        self.assertTrue(self.settings.students_sound)
        self.assertTrue(self.settings.until_dismissed)
        self.assertEqual(self.settings.duration_seconds, 12)
        self.assertEqual(self.settings.text_template, 'task_completed')
        self.assertEqual(self.settings.sound_preset, 'marimba')
        self.assertFalse(SubmissionNotificationSettings.objects.filter(teacher=self.other_teacher).exists())

    def test_text_template_and_sound_preset_customization(self):
        self.enable(text_template='submission_success', sound_preset='bell')
        self.event()
        data = self.poll(since=0).json()
        event = data['events'][0]
        self.assertEqual(event['template'], 'submission_success')
        self.assertEqual(event['sound_preset'], 'bell')
        self.assertEqual(data['policies'][str(self.teacher.pk)]['template'], 'submission_success')
        self.assertEqual(data['policies'][str(self.teacher.pk)]['sound_preset'], 'bell')

    def test_student_does_not_receive_notice_for_own_submission(self):
        self.enable(students_enabled=True, students_all_classes=True)
        submitting_student = Client()
        classmate = Client()
        baseline = self.poll().json()['cursor']
        values = {'student_id': self.student.pk, 'full_name': self.student.get_full_name(),
                  'class_group': self.group.pk, 'comment_student': 'Я здав роботу'}
        response = submitting_student.post(reverse('submit_assignment', args=[self.assignment.pk]), values)
        self.assertEqual(response.status_code, 302)
        # Учень, який щойно здав роботу, НЕ отримує сповіщення про власну здачу
        sub_data = submitting_student.get(reverse('submission_notifications'), {'since': baseline}).json()
        self.assertEqual(sub_data['events'], [])
        # Однокласник та вчитель отримують сповіщення
        classmate_data = classmate.get(reverse('submission_notifications'), {'since': baseline}).json()
        self.assertEqual(len(classmate_data['events']), 1)
        self.assertEqual(classmate_data['events'][0]['name'], 'Шевченко Олександра')
        teacher_data = self.poll(since=baseline).json()
        self.assertEqual(len(teacher_data['events']), 1)

    def test_duration_validation_and_anonymous_settings_protection(self):
        for duration in ('0', '61', 'invalid'):
            response = self.client.post(reverse('teacher_settings'), {
                'action': 'save_submission_notifications', 'duration_seconds': duration, 'teacher_enabled': 'on',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['active_tab'], 'environment')
            self.assertTrue(response.context['submission_notification_form'].errors)
        self.settings.refresh_from_db()
        self.assertFalse(self.settings.teacher_enabled)
        self.assertEqual(Client().post(reverse('teacher_settings'), {'action': 'save_submission_notifications'}).status_code, 302)

    def test_malformed_parameters_and_post_rejected_and_no_store(self):
        for params in ({'since': '-1'}, {'since': 'x'}, {'class': 'x'}, {'class': '9' * 30}):
            self.assertEqual(self.poll(**params).status_code, 400)
        self.assertEqual(self.client.post(reverse('submission_notifications')).status_code, 405)
        self.assertIn('no-store', self.poll().headers['Cache-Control'])

    def test_poll_uses_three_queries_independent_of_number_of_events(self):
        self.enable()
        for _ in range(4):
            self.event()
        request = RequestFactory().get('/api/submission-notifications/', {'since': '0'})
        request.user = self.user
        # Cache the profile exactly as middleware/context normally would.
        request.user.teacher_profile
        with self.assertNumQueries(3):
            response = submission_notifications(request)
        self.assertEqual(response.status_code, 200)

    def test_settings_and_base_markup_render_on_desktop_and_student_pages(self):
        response = self.client.get(reverse('teacher_settings'), {'tab': 'environment'})
        self.assertContains(response, 'id="submission-notification-form"')
        self.assertContains(response, 'data-submission-preview="repeat"')
        self.assertContains(response, 'data-viewer="teacher-')
        response = Client().get(reverse('assignment_detail', args=[self.assignment.pk]))
        self.assertContains(response, 'data-class-id=""')
        # Multi-class assignment has no implied audience until a class is selected.
        response = Client().get(reverse('assignment_detail', args=[self.assignment.pk]), {'class': self.group.pk})
        self.assertContains(response, f'data-class-id="{self.group.pk}"')
