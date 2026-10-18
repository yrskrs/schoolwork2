"""Regression coverage for the actual lesson occurrence saved by the editor."""
from datetime import datetime, time
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Assignment, BellSchedule, Subject, TeacherLessonSchedule
from . import tests_editor_layout as editor_fixtures


class LessonScheduleTests(TestCase):
    setUp = editor_fixtures.AssignmentEditorLayoutTests.setUp
    data = editor_fixtures.AssignmentEditorLayoutTests.data

    def now(self, hour, minute=0, day=21):
        return timezone.make_aware(datetime(2026, 10, day, hour, minute))

    def wednesday(self):
        slot = BellSchedule.objects.create(lesson_number=2, start_time=time(10), end_time=time(10, 45))
        return TeacherLessonSchedule.objects.create(teacher=self.teacher, class_group=self.classes[0],
            subject=self.subject, bell_slot=slot, day_of_week=3)

    def test_create_auto_uses_todays_upcoming_or_ongoing_lesson_instead_of_first_weekday(self):
        lesson = self.wednesday()
        for hour, minute in [(9, 50), (10, 0), (10, 20)]:
            with self.subTest(hour=hour, minute=minute), patch('django.utils.timezone.now', return_value=self.now(hour, minute)):
                response = self.client.post(reverse('assignment_create'), self.data(classes=[self.classes[0].pk]))
                self.assertEqual(response.status_code, 302)
                target = Assignment.objects.latest('pk').schedule_targets.get()
                self.assertEqual(target.target_date.isoformat(), '2026-10-21')
                self.assertEqual(target.bell_slot_id, lesson.bell_slot_id)

    def test_finished_lesson_is_not_reused_and_week_wrap_is_correct(self):
        self.wednesday()
        with patch('django.utils.timezone.now', return_value=self.now(10, 45)):
            response = self.client.post(reverse('assignment_create'), self.data(classes=[self.classes[0].pk]))
        self.assertEqual(response.status_code, 302)
        target = Assignment.objects.latest('pk').schedule_targets.get()
        self.assertEqual(target.target_date.isoformat(), '2026-10-26')
        self.assertEqual(target.bell_slot_id, self.slot.pk)

    def test_auto_uses_selected_subject_and_ignores_a_different_subject(self):
        right = self.wednesday()
        physics = Subject.objects.create(name='Фізика')
        slot = BellSchedule.objects.create(lesson_number=3, start_time=time(9, 30), end_time=time(10, 15))
        TeacherLessonSchedule.objects.create(teacher=self.teacher, class_group=self.classes[0],
            subject=physics, bell_slot=slot, day_of_week=3)
        with patch('django.utils.timezone.now', return_value=self.now(9, 50)):
            self.client.post(reverse('assignment_create'), self.data(classes=[self.classes[0].pk]))
        self.assertEqual(Assignment.objects.latest('pk').schedule_targets.get().bell_slot_id, right.bell_slot_id)

    def test_scheduled_publication_uses_its_date_instead_of_creation_time(self):
        lesson = self.wednesday()
        with patch('django.utils.timezone.now', return_value=self.now(12, day=19)):
            response = self.client.post(reverse('assignment_create'), self.data(
                classes=[self.classes[0].pk], publish_choice='scheduled', scheduled_at='2026-10-21T09:50'))
        self.assertEqual(response.status_code, 302)
        target = Assignment.objects.latest('pk').schedule_targets.get()
        self.assertEqual(target.target_date.isoformat(), '2026-10-21')
        self.assertEqual(target.bell_slot_id, lesson.bell_slot_id)

    def test_explicit_date_and_no_schedule_selection_are_preserved(self):
        lesson = self.wednesday()
        with patch('django.utils.timezone.now', return_value=self.now(10, 20)):
            self.client.post(reverse('assignment_create'), self.data(classes=[self.classes[0].pk], **{
                f'class_schedule_target_{self.classes[0].pk}': f'slot_3_{lesson.bell_slot_id}',
                f'class_schedule_target_date_{self.classes[0].pk}': '2026-10-22'}))
            self.assertEqual(Assignment.objects.latest('pk').schedule_targets.get().target_date.isoformat(), '2026-10-22')
            self.client.post(reverse('assignment_create'), self.data(classes=[self.classes[0].pk], **{
                f'class_schedule_target_{self.classes[0].pk}': 'none'}))
            self.assertFalse(Assignment.objects.latest('pk').schedule_targets.exists())

    def test_api_returns_ongoing_lesson_and_agrees_with_creation(self):
        lesson = self.wednesday()
        with patch('django.utils.timezone.now', return_value=self.now(10, 20)):
            response = self.client.get(reverse('api_next_lesson_slot'), {'class_id': self.classes[0].pk,
                                                                       'subject_id': self.subject.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_date'], '2026-10-21')
        self.assertEqual(response.json()['bell_slot_id'], lesson.bell_slot_id)
        self.assertIn('Зараз', response.json()['info'])

    def test_api_cannot_use_another_teachers_schedule(self):
        from django.contrib.auth.models import User
        from .models import Teacher
        other = Teacher.objects.create(user=User.objects.create_user('other-teacher'))
        TeacherLessonSchedule.objects.filter(teacher=self.teacher).update(teacher=other)
        response = self.client.get(reverse('api_next_lesson_slot'), {'class_id': self.classes[0].pk})
        self.assertFalse(response.json()['has_schedule'])
