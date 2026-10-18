import io
import json
import os
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .assignment_archive import export_bundle, import_bundle
from .middleware import set_has_admin
from .models import (Assignment, AssignmentFile, AssignmentLink, AssignmentScheduleTarget,
                     AssignmentYouTubeLink, AICriteriaPreset, BellSchedule, ClassGroup, School, Subject, Teacher)


class AssignmentArchiveTests(TestCase):
    def setUp(self):
        admin = User.objects.create_superuser('archive-admin', password='test')
        School.objects.create(name='Тестова школа', admin=admin)
        set_has_admin(True)
        self.user = User.objects.create_user('archive-teacher', password='test')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель архівів')
        other_user = User.objects.create_user('archive-other', password='test')
        self.other = Teacher.objects.create(user=other_user, full_name='Інший вчитель')
        self.group = ClassGroup.objects.create(name='6-Б')
        self.older = ClassGroup.objects.create(name='10-А')
        self.subject = Subject.objects.create(name='Архівна інформатика')
        self.slot = BellSchedule.objects.create(lesson_number=2, start_time=time(9, 25), end_time=time(10, 10), order=2)
        self.client.force_login(self.user)
        self.export_url = reverse('export_assignments_day_zip')
        self.import_url = reverse('import_assignments_zip')

    def make_assignment(self, title='Урок у вівторок', day=date(2026, 10, 13), weekday=2, **kwargs):
        assignment = Assignment.objects.create(teacher=self.teacher, title=title, subject=self.subject, **kwargs)
        assignment.classes.add(self.group)
        if day is not False:
            AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=self.group,
                                                    target_date=day, target_day_of_week=weekday, bell_slot=self.slot)
        return assignment

    def response_manifest(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        payload = b''.join(response.streaming_content)
        # Django's test client closes the response when this iterator finishes.
        # Closing it again emits request_finished twice and closes PostgreSQL's
        # connection inside the enclosing TestCase transaction.
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return json.loads(archive.read('manifest.json')), payload

    def zip_upload(self, rows, files=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'version': '1.0', 'assignments': rows}))
            for name, content in (files or {}).items():
                archive.writestr(name, content)
        return SimpleUploadedFile('tasks.zip', buffer.getvalue(), content_type='application/zip')

    def test_button_opens_selection_and_does_not_download_or_fallback(self):
        self.make_assignment()
        response = self.client.get(self.export_url)
        self.assertContains(response, 'Обрати всі знайдені')
        self.assertContains(response, 'Вівторок')
        self.assertContains(response, 'Урок у вівторок')
        self.assertNotIn('Content-Disposition', response)
        empty = self.client.get(self.export_url, {'date': '2026-11-01'})
        self.assertEqual(empty.context['assignment_count'], 0)
        self.assertNotContains(empty, 'Урок у вівторок')

    def test_date_weekday_and_lesson_filters_match_same_binding(self):
        tuesday = self.make_assignment()
        monday = self.make_assignment('Понеділок', date(2026, 10, 12), 1)
        recurrent = self.make_assignment('Щовівторка', None)
        # Publication on Tuesday must not override an explicit Monday lesson.
        Assignment.objects.filter(pk=monday.pk).update(published_at=timezone.make_aware(datetime(2026, 10, 13, 12)))
        response = self.client.get(self.export_url, {'weekdays': '2', 'lesson': '2'})
        self.assertContains(response, tuesday.title)
        self.assertContains(response, recurrent.title)
        shown_ids = {row['assignment'].pk for group in response.context['groups'] for row in group['rows']}
        self.assertNotIn(monday.pk, shown_ids)
        response = self.client.get(self.export_url, {'date': '2026-10-13'})
        self.assertEqual(response.context['assignment_count'], 1)
        # A Monday binding with lesson 3 and Tuesday with lesson 2 must not match Tuesday + lesson 3.
        slot3 = BellSchedule.objects.create(lesson_number=3, start_time=time(10, 25), end_time=time(11, 10))
        tuesday.classes.add(self.older)
        AssignmentScheduleTarget.objects.create(assignment=tuesday, class_group=self.older,
                                                target_date=date(2026, 10, 12), target_day_of_week=1, bell_slot=slot3)
        response = self.client.get(self.export_url, {'weekdays': '2', 'lesson': '3'})
        self.assertEqual(response.context['assignment_count'], 0)

    def test_date_range_and_multiple_weekdays_include_unbound_tasks_by_original_date(self):
        self.make_assignment('Перший вівторок')
        self.make_assignment('Другий вівторок', date(2026, 10, 20))
        self.make_assignment('Понеділок', date(2026, 10, 12), 1)
        unbound = self.make_assignment('Без розкладу', False)
        Assignment.objects.filter(pk=unbound.pk).update(created_at=timezone.make_aware(datetime(2026, 10, 13, 8)))
        response = self.client.get(self.export_url, {'date': '2026-10-12', 'date_to': '2026-10-13', 'weekdays': ['1', '2']})
        self.assertEqual(response.context['assignment_count'], 3)
        self.assertContains(response, 'Без розкладу')
        self.assertNotContains(response, 'Другий вівторок')
        invalid = self.client.get(self.export_url, {'date': 'invalid'})
        self.assertEqual(invalid.context['assignment_count'], 0)
        self.assertTrue(invalid.context['filter_form'].errors)

    def test_only_explicitly_selected_owned_assignments_are_exported_once(self):
        selected = self.make_assignment()
        omitted = self.make_assignment('Не обрано')
        manifest, _ = self.response_manifest(self.client.post(self.export_url, {'assignments': [selected.pk, selected.pk]}))
        self.assertEqual([item['title'] for item in manifest['assignments']], [selected.title])
        self.assertNotEqual(selected.pk, omitted.pk)
        foreign = Assignment.objects.create(teacher=self.other, title='Чуже завдання')
        response = self.client.post(self.export_url, {'assignments': [selected.pk, foreign.pk]})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Content-Disposition', response)
        response = self.client.post(self.export_url)
        self.assertContains(response, 'Позначте хоча б одне')

    def test_full_roundtrip_restores_schedule_timestamps_files_and_class_policy(self):
        stamp = timezone.make_aware(datetime(2026, 10, 1, 10, 30))
        scheduled = timezone.now() + timedelta(days=30)
        assignment = self.make_assignment(description='<p>Умова</p>', status=Assignment.STATUS_SCHEDULED,
                                           scheduled_at=scheduled, due_date=date(2026, 11, 20),
                                           is_individual=True, student_name='Тестовий Учень',
                                           link_url='https://example.com/task', link_label='Матеріал',
                                           youtube_url='https://youtu.be/dQw4w9WgXcQ',
                                           allow_student_ai_check=True, allow_ai_usage=True, no_submission_required=True,
                                           ai_thinking_mode=True, allow_student_ai_understanding=True,
                                           teacher_ai_instructions='Перевіряй зміст', custom_criteria='Зміст — 12 балів')
        assignment.classes.add(self.older)
        AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=self.older,
                                                target_date=None, target_day_of_week=4, bell_slot=self.slot)
        Assignment.objects.filter(pk=assignment.pk).update(created_at=stamp, updated_at=stamp, published_at=stamp)
        assignment.refresh_from_db()
        preset = AICriteriaPreset.objects.create(name='Переносні критерії', evaluation_type='traditional')
        preset.document_file.save('rubric.txt', SimpleUploadedFile('rubric.txt', b'rubric'))
        assignment.default_ai_preset = preset
        assignment.class_ai_overrides = {str(self.older.pk): {'preset_id': preset.pk, 'grs': []}}
        assignment.save(update_fields=['default_ai_preset', 'class_ai_overrides'])
        assignment.refresh_from_db()
        AssignmentLink.objects.create(assignment=assignment, url='https://example.com/extra', label='Додаткове')
        AssignmentYouTubeLink.objects.create(assignment=assignment, url='https://youtu.be/dQw4w9WgXcQ', title='Відео')
        AssignmentFile.objects.create(assignment=assignment, file=SimpleUploadedFile('умова.txt', b'task bytes'),
                                      original_name='умова.txt', is_task_source_for_ai=True)
        _, payload = self.response_manifest(self.client.post(self.export_url, {'assignments': [assignment.pk]}))
        # A different database may have neither these classes nor this bell slot.
        assignment.delete()
        self.group.delete()
        self.older.delete()
        self.slot.delete()
        preset.delete()
        self.client.force_login(self.other.user)
        response = self.client.post(self.import_url, {'archive': SimpleUploadedFile('tasks.zip', payload)})
        self.assertEqual(response.status_code, 302)
        clone = Assignment.objects.get(teacher=self.other)
        for field in ('title', 'description', 'status', 'scheduled_at', 'published_at', 'created_at', 'updated_at',
                      'due_date', 'is_individual', 'student_name', 'link_url', 'link_label', 'youtube_url',
                      'allow_student_ai_check', 'allow_ai_usage', 'no_submission_required', 'ai_thinking_mode',
                      'allow_student_ai_understanding', 'teacher_ai_instructions', 'custom_criteria'):
            self.assertEqual(getattr(clone, field), getattr(assignment, field), field)
        self.assertEqual(set(clone.classes.values_list('name', flat=True)), {'6-Б', '10-А'})
        targets = {target.class_group.name: target for target in clone.schedule_targets.all()}
        self.assertEqual(targets['6-Б'].target_date, date(2026, 10, 13))
        self.assertEqual(targets['10-А'].target_day_of_week, 4)
        self.assertIsNone(targets['10-А'].target_date)
        self.assertEqual(targets['10-А'].bell_slot.lesson_number, 2)
        self.assertEqual(targets['10-А'].bell_slot.start_time, time(9, 25))
        attachment = clone.files.get()
        self.assertEqual(attachment.file.read(), b'task bytes')
        self.assertTrue(attachment.is_task_source_for_ai)
        self.assertEqual(clone.additional_links.get().label, 'Додаткове')
        self.assertEqual(clone.youtube_links.get().title, 'Відео')
        older = clone.classes.get(name='10-А')
        self.assertEqual(clone.get_ai_policy(older)[0].name, 'Переносні критерії')
        self.assertEqual(clone.default_ai_preset.document_file.read(), b'rubric')

    def test_bulk_and_single_export_share_format_and_keep_all_classes(self):
        first = self.make_assignment()
        second = self.make_assignment('У четвер', date(2026, 10, 15), 4)
        first.classes.add(self.older)
        AssignmentScheduleTarget.objects.create(assignment=first, class_group=self.older,
                                                target_day_of_week=3, bell_slot=self.slot)
        page = self.client.get(self.export_url)
        shared_groups = [group for group in page.context['groups']
                         if any(row['assignment'].pk == first.pk for row in group['rows'])]
        self.assertEqual(len(shared_groups), 2)
        self.assertEqual(page.context['assignment_count'], 2)
        single, _ = self.response_manifest(self.client.get(reverse('export_assignment_zip', args=[first.pk])))
        bulk, payload = self.response_manifest(self.client.post(self.export_url, {'assignments': [first.pk, second.pk]}))
        self.assertEqual(single['assignments'][0], bulk['assignments'][0])
        count, _ = import_bundle(io.BytesIO(payload), self.other)
        self.assertEqual(count, 2)
        self.assertEqual(Assignment.objects.get(teacher=self.other, title=first.title).schedule_targets.count(), 2)

    def test_import_preserves_overdue_scheduled_state_and_does_not_fetch_external_titles(self):
        assignment = self.make_assignment(status=Assignment.STATUS_SCHEDULED,
                                           scheduled_at=timezone.now() + timedelta(days=1),
                                           link_url='https://example.com/task', link_label='Матеріал')
        original_time = timezone.now() - timedelta(days=2)
        Assignment.objects.filter(pk=assignment.pk).update(scheduled_at=original_time, link_label='')
        assignment.refresh_from_db()
        buffer = export_bundle([assignment], self.teacher)
        with buffer, patch('feed.utils.fetch_url_title', side_effect=AssertionError('Import must stay local')) as fetch:
            import_bundle(buffer, self.other)
        fetch.assert_not_called()
        clone = Assignment.objects.get(teacher=self.other)
        self.assertEqual(clone.status, Assignment.STATUS_SCHEDULED)
        self.assertEqual(clone.scheduled_at, original_time)
        self.assertIsNone(clone.published_at)
        self.assertEqual(clone.link_label, '')

    def test_legacy_archive_restores_undated_weekday_and_existing_slot(self):
        count, _ = import_bundle(self.zip_upload([{'title': 'Старий урок', 'class_names': ['6-Б'],
                                                  'schedule_targets': [{'class_name': '6-Б', 'target_date': None,
                                                                        'target_day_of_week': 2, 'lesson_number': 2}]}]), self.teacher)
        self.assertEqual(count, 1)
        target = Assignment.objects.get(title='Старий урок').schedule_targets.get()
        self.assertEqual(target.target_day_of_week, 2)
        self.assertEqual(target.bell_slot, self.slot)

    def test_import_failure_rolls_back_records_and_written_files(self):
        before = set(Path(settings.MEDIA_ROOT).rglob('*'))
        upload = self.zip_upload([
            {'title': 'Перше', 'class_names': ['Новий'], 'subject_name': 'Новий предмет',
             'files': [{'zip_path': 'files/task.txt', 'original_name': 'task.txt'}],
             'ai_policy': {'presets': {'1': {'name': 'Новий шаблон', 'evaluation_type': 'traditional',
                                           'document_zip_path': 'criteria/rubric.txt'}}, 'default_preset': '1'}},
            {'title': 'Зіпсоване', 'status': 'wrong'},
        ], {'files/task.txt': b'task', 'criteria/rubric.txt': b'rubric'})
        response = self.client.post(self.import_url, {'archive': upload})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Assignment.objects.filter(teacher=self.teacher).exists())
        self.assertFalse(ClassGroup.objects.filter(name='Новий').exists())
        self.assertFalse(Subject.objects.filter(name='Новий предмет').exists())
        self.assertFalse(AICriteriaPreset.objects.filter(name='Новий шаблон').exists())
        self.assertFalse([path for path in set(Path(settings.MEDIA_ROOT).rglob('*')) - before if path.is_file()])

    def test_missing_files_invalid_dates_and_unavailable_legacy_slots_abort_whole_import(self):
        for row in ({'title': 'Брак файла', 'files': [{'zip_path': 'files/missing.txt'}]},
                    {'title': 'Брак дати', 'due_date': '2026-99-99'},
                    {'title': 'Брак уроку', 'class_names': ['6-Б'], 'schedule_targets': [{'class_name': '6-Б', 'lesson_number': 99}]}):
            with self.subTest(row=row), self.assertRaises(ValueError):
                import_bundle(self.zip_upload([{'title': 'Коректне'}, row]), self.teacher)
            self.assertFalse(Assignment.objects.filter(teacher=self.teacher).exists())

    def test_missing_source_file_stops_export_instead_of_losing_material(self):
        assignment = self.make_assignment()
        attachment = AssignmentFile.objects.create(assignment=assignment, file=SimpleUploadedFile('task.txt', b'task'))
        os.unlink(attachment.file.path)
        response = self.client.post(self.export_url, {'assignments': [assignment.pk]})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Не знайдено файл', status_code=400)

    def test_login_required_for_export_and_import(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.export_url).status_code, 302)
        self.assertEqual(self.client.post(self.import_url).status_code, 302)
