import datetime
import json
from collections import Counter
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import (
    AICriteriaPreset, AIRequestLog, AISettings, Assignment, AssignmentScheduleTarget,
    BellSchedule, ClassGroup, School, Subject, Submission, Teacher, TeacherLessonSchedule,
)


class AssignmentEditorLayoutTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_superuser('editor_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        School.objects.create(name='Тестова школа', admin=user)
        self.subject = Subject.objects.create(name='Інформатика')
        self.classes = [ClassGroup.objects.create(name=f'9-{letter}', grade=9, letter=letter) for letter in ['А', 'Б']]
        self.teacher.subjects.add(self.subject)
        self.teacher.classes.add(*self.classes)
        self.slot = BellSchedule.objects.create(lesson_number=1, start_time=datetime.time(8, 30), end_time=datetime.time(9, 15))
        TeacherLessonSchedule.objects.create(teacher=self.teacher, class_group=self.classes[0], subject=self.subject, bell_slot=self.slot, day_of_week=1)
        self.preset = AICriteriaPreset.objects.create(name='Власні критерії', evaluation_type='nus_gr', gr_definitions='ГР 1: Результат роботи')
        self.client.force_login(user)

    def page(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def successful_fields(self, page):
        fields = {}
        for control in page.get_element_by_id('assignment-form').xpath('.//input[@name] | .//textarea[@name] | .//select[@name]'):
            if 'disabled' in control.attrib or control.get('type') == 'file':
                continue
            if control.get('type') in ('radio', 'checkbox') and 'checked' not in control.attrib:
                continue
            if control.tag == 'textarea':
                value = control.text or ''
            elif control.tag == 'select':
                options = control.xpath('.//option[@selected]') or control.xpath('.//option')[:1]
                value = options[0].get('value', '') if options else ''
            else:
                value = control.get('value', 'on' if control.get('type') == 'checkbox' else '')
            fields.setdefault(control.get('name'), []).append(value)
        return fields

    def data(self, **overrides):
        data = {
            'subject': str(self.subject.pk), 'title': 'Створити програму',
            'description': 'Обчислити суму чисел', 'publish_choice': 'draft',
            'classes': [str(cls.pk) for cls in self.classes],
        }
        return {**data, **overrides}

    def test_sidebar_controls_and_main_content_belong_to_one_form(self):
        page = self.page(reverse('assignment_create'))
        form = page.get_element_by_id('assignment-form')
        self.assertFalse(form.xpath('.//form'))
        identifiers = Counter(form.xpath('.//*[@id]/@id'))
        self.assertFalse([key for key, count in identifiers.items() if count > 1])
        for field in ['subject', 'classes', 'due_date', 'publish_choice', 'default_ai_preset', 'allow_student_ai_check', 'no_submission_required']:
            self.assertTrue(form.xpath('.//aside//*[@name=$name]', name=field), field)
        for field in ['subject', 'title', 'description', 'files', 'custom_criteria']:
            self.assertTrue(form.xpath('.//*[@name=$name]', name=field), field)
        self.assertEqual(len(page.get_element_by_id('assignment-save-button').xpath('ancestor::form')), 1)

    def test_subject_is_in_recipients_and_single_subject_is_preselected(self):
        page = self.page(reverse('assignment_create'))
        field = page.get_element_by_id('id_subject')
        self.assertTrue(field.xpath('ancestor::section[@aria-labelledby="assignment-recipients-title"]'))
        self.assertEqual(field.xpath('./option[@selected]/@value'), [str(self.subject.pk)])
        self.assertFalse(page.xpath('//section[@aria-labelledby="assignment-content-title"]//*[@name="subject"]'))
        other = Subject.objects.create(name='Математика')
        self.teacher.subjects.add(other)
        field = self.page(reverse('assignment_create')).get_element_by_id('id_subject')
        self.assertEqual(set(field.xpath('./option/@value')), {'', str(self.subject.pk), str(other.pk)})

    @patch('feed.views.prewarm_assignment_files_preview')
    def test_actual_form_submits_sidebar_and_collapsed_fields_with_files(self, _prewarm):
        fields = self.successful_fields(self.page(reverse('assignment_create')))
        fields.update(self.data(
            due_date='2026-10-10', no_submission_required='1',
            default_ai_preset=str(self.preset.pk), default_ai_grs=['ГР 1'],
            custom_criteria='Повнота результату — 12 балів', allow_student_ai_check='1',
            files=SimpleUploadedFile('instruction.txt', b'Instruction'),
        ))
        response = self.client.post(reverse('assignment_create'), fields)
        self.assertEqual(response.status_code, 302)
        assignment = Assignment.objects.get(title='Створити програму')
        self.assertEqual(set(assignment.classes.values_list('pk', flat=True)), {cls.pk for cls in self.classes})
        self.assertEqual(assignment.due_date.isoformat(), '2026-10-10')
        self.assertTrue(assignment.no_submission_required)
        self.assertTrue(assignment.allow_student_ai_check)
        self.assertEqual(assignment.default_ai_preset_id, self.preset.pk)
        self.assertEqual(json.loads(assignment.default_ai_grs), ['ГР 1'])
        self.assertEqual(assignment.custom_criteria, 'Повнота результату — 12 балів')
        self.assertEqual(assignment.files.count(), 1)

    def test_validation_keeps_ai_choices_and_opens_sections(self):
        response = self.client.post(reverse('assignment_create'), self.data(
            title='', publish_choice='scheduled', default_ai_preset=str(self.preset.pk),
            default_ai_grs=['ГР 1'], allow_student_ai_check='1', allow_ai_usage='1',
            allow_student_ai_understanding='1', ai_thinking_mode='1', no_submission_required='1',
        ))
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.content)
        for name in ['allow_student_ai_check', 'allow_ai_usage', 'allow_student_ai_understanding', 'ai_thinking_mode', 'no_submission_required']:
            self.assertTrue(page.xpath('//input[@name=$name and @checked]', name=name), name)
        self.assertEqual(page.get_element_by_id('id_default_ai_preset').xpath('.//option[@selected]/@value'), [str(self.preset.pk)])
        self.assertEqual(json.loads(page.get_element_by_id('assignment-submitted-grs').text), ['ГР 1'])
        self.assertIn('open', page.get_element_by_id('assignment-ai-options').attrib)
        self.assertIn('open', page.get_element_by_id('assignment-custom-criteria').attrib)
        self.assertNotIn('display:none', page.get_element_by_id('scheduled-section').get('style'))
        self.assertIn('Запланувати', page.get_element_by_id('assignment-save-button').text_content())

    def test_edit_form_preserves_schedule_dates_and_checked_fields(self):
        scheduled = timezone.now() + datetime.timedelta(days=3)
        assignment = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Редагування', description='Умова',
            status='scheduled', scheduled_at=scheduled, custom_criteria='Критерії',
            default_ai_preset=self.preset, allow_student_ai_check=True,
        )
        assignment.classes.add(self.classes[0])
        target_date = scheduled.date()
        AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=self.classes[0], target_date=target_date, target_day_of_week=1, bell_slot=self.slot)
        url = reverse('assignment_edit', args=[assignment.pk])
        page = self.page(url)
        date_field = page.xpath('//input[@name=$name]', name=f'class_schedule_target_date_{self.classes[0].pk}')[0]
        self.assertEqual(date_field.get('value'), target_date.isoformat())
        self.assertIn('open', page.get_element_by_id('assignment-custom-criteria').attrib)
        fields = self.successful_fields(page)
        self.assertTrue(fields['allow_student_ai_check'])
        response = self.client.post(url, fields)
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertTrue(assignment.allow_student_ai_check)
        self.assertEqual(assignment.custom_criteria, 'Критерії')
        self.assertEqual(assignment.schedule_targets.get(class_group=self.classes[0]).target_date, target_date)

    def test_edit_validation_preserves_explicitly_cleared_fields(self):
        assignment = Assignment.objects.create(teacher=self.teacher, subject=self.subject, title='Чернетка', description='Умова', status='draft', custom_criteria='Старі критерії', allow_student_ai_check=True, no_submission_required=True)
        assignment.classes.add(self.classes[0])
        response = self.client.post(reverse('assignment_edit', args=[assignment.pk]), self.data(title='', custom_criteria=''))
        self.assertEqual(response.status_code, 200)
        page = html.fromstring(response.content)
        self.assertFalse(page.xpath('//input[@name="allow_student_ai_check" and @checked]'))
        self.assertFalse(page.xpath('//input[@name="no_submission_required" and @checked]'))
        self.assertEqual(page.get_element_by_id('id_custom_criteria').text or '', '')

    def test_individual_name_is_visible_on_validation_error(self):
        response = self.client.post(reverse('assignment_create'), self.data(is_individual='on', student_name=''))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('display:none', html.fromstring(response.content).get_element_by_id('student-name-section').get('style'))

    def test_statistics_keeps_long_model_name_and_active_status_together(self):
        model_name = 'gemini-3.1-flash-lite-preview-long-model-name'
        config = AISettings.get_solo()
        config.model_name = model_name
        config.api_key = 'synthetic-key'
        config.save()
        AIRequestLog.objects.create(model_name=model_name, prompt_tokens=500, total_tokens=500)
        page = self.page(reverse('teacher_settings') + '?tab=ai&ai_section=statistics')
        card = page.xpath('//article[@class="ai-model-stat-card"]')[0]
        identity = card.xpath('.//div[@class="ai-model-identity"]')[0]
        self.assertEqual(identity.xpath('./code')[0].text_content().strip(), model_name)
        self.assertIn('Активна', identity.text_content())
        self.assertEqual(len(card.xpath('.//dl/div')), 6)
        self.assertFalse(page.xpath('//table[contains(@class, "ai-model-usage-table")]'))

    def test_assignment_create_preselects_previous_subject_and_classes(self):
        # 1. Створюємо інший предмет і додаємо вчителю
        physics = Subject.objects.create(name='Фізика')
        self.teacher.subjects.add(physics)

        # 2. Вчитель створює перше завдання з фізики для класу 9-Б
        post_data = self.data(
            subject=str(physics.pk),
            classes=[str(self.classes[1].pk)],
            title='Завдання з фізики',
            description='Умова фізики',
        )
        resp = self.client.post(reverse('assignment_create'), post_data)
        self.assertEqual(resp.status_code, 302)

        # 3. Відкриваємо створення наступного завдання (GET без параметрів)
        page = self.page(reverse('assignment_create'))

        # Перевіряємо, що предмет попереднього вибору (Фізика) автоматично обрано
        subject_select = page.get_element_by_id('id_subject')
        self.assertEqual(subject_select.xpath('./option[@selected]/@value'), [str(physics.pk)])

        # Перевіряємо, що клас попереднього вибору (9-Б) автоматично відмічено чекбоксом
        checked_classes = page.xpath('//input[@name="classes" and @checked]/@value')
        self.assertEqual(checked_classes, [str(self.classes[1].pk)])

    def test_teacher_ai_instructions_saved_edited_and_duplicated(self):
        secret_notes = "Для 9-А класу: лояльніше оцінювати бюлетень, головне зміст!"
        post_data = self.data(
            teacher_ai_instructions=secret_notes,
            custom_criteria="Критерії від вчителя",
        )
        resp = self.client.post(reverse('assignment_create'), post_data)
        self.assertEqual(resp.status_code, 302)

        assignment = Assignment.objects.get(title='Створити програму')
        self.assertEqual(assignment.teacher_ai_instructions, secret_notes)

        # Редагування
        updated_notes = "Оновлені вказівки для ШІ"
        resp_edit = self.client.post(reverse('assignment_edit', args=[assignment.pk]), self.data(
            teacher_ai_instructions=updated_notes,
        ))
        self.assertEqual(resp_edit.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.teacher_ai_instructions, updated_notes)

        # Дублювання
        dup_resp = self.client.post(reverse('assignment_duplicate', args=[assignment.pk]))
        self.assertEqual(dup_resp.status_code, 302)
        duplicate = Assignment.objects.exclude(pk=assignment.pk).filter(duplicated_from=assignment).first()
        self.assertIsNotNone(duplicate)
        self.assertEqual(duplicate.teacher_ai_instructions, updated_notes)

    def test_teacher_ai_instructions_visible_in_review_but_hidden_from_students(self):
        secret_notes = "Секретний коментар для ШІ: пробачити помилки у бюлетені"
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Тестове завдання з секретом',
            description='Умова для всіх',
            teacher_ai_instructions=secret_notes,
            status=Assignment.STATUS_PUBLISHED,
            published_at=timezone.now(),
        )
        assignment.classes.add(self.classes[0])

        submission = Submission.objects.create(
            assignment=assignment,
            teacher=self.teacher,
            class_group=self.classes[0],
            first_name='Олександр',
            last_name='Коваленко',
        )

        # 1. Перевірка панелі перевірки вчителя (view_file)
        viewer_resp = self.client.get(reverse('view_file', args=[submission.pk]))
        self.assertEqual(viewer_resp.status_code, 200)
        self.assertIn(secret_notes, viewer_resp.content.decode('utf-8'))
        self.assertIn("Ваші вказівки для ШІ:", viewer_resp.content.decode('utf-8'))

        # 2. Перевірка учнівського перегляду завдання (assignment_detail)
        student_resp = self.client.get(reverse('assignment_detail', args=[assignment.pk]))
        self.assertEqual(student_resp.status_code, 200)
        self.assertNotIn(secret_notes, student_resp.content.decode('utf-8'))

        # 3. Перевірка учнівського модального вікна критеріїв (criteria modal)
        self.assertNotIn('teacher_ai_instructions', student_resp.content.decode('utf-8'))

    def test_evaluate_submission_prompt_contains_teacher_ai_instructions(self):
        secret_notes = "ШІ, будь уважний: оцінюй лише таблицю, бюлетень не обов'язковий"
        assignment = Assignment.objects.create(
            teacher=self.teacher,
            subject=self.subject,
            title='Завдання для ШІ тесту',
            description='Опис',
            teacher_ai_instructions=secret_notes,
            status=Assignment.STATUS_PUBLISHED,
        )
        assignment.classes.add(self.classes[0])

        submission = Submission.objects.create(
            assignment=assignment,
            teacher=self.teacher,
            class_group=self.classes[0],
            first_name='Марія',
            last_name='Сидоренко',
            comment_student='Ось моя виконана робота',
        )

        # Перевіряємо формування запиту для Gemini
        from feed.gemini_service import evaluate_submission_with_gemini
        with patch('feed.gemini_service.call_ai_api') as mock_call:
            mock_call.return_value = (200, '{"suggested_grade": "10", "summary": "Добре"}', '', {})
            with patch('feed.gemini_service.get_ai_settings') as mock_settings:
                settings_obj = AISettings.get_solo()
                settings_obj.api_key = 'test-key'
                settings_obj.save()
                mock_settings.return_value = settings_obj
                evaluate_submission_with_gemini(submission, teacher=self.teacher)

            self.assertTrue(mock_call.called)
            # Отримуємо prompt_text (може передаватися як keyword або positional аргумент)
            called_prompt = mock_call.call_args.kwargs.get('prompt_text')
            if not called_prompt and mock_call.call_args.args:
                called_prompt = mock_call.call_args.args[0]
            if isinstance(called_prompt, list):
                called_prompt = "\n".join(str(p) for p in called_prompt)
            self.assertIn(secret_notes, str(called_prompt))
            self.assertEqual(str(called_prompt).count(secret_notes), 1)
            self.assertIn("СУВОРО КОНФІДЕНЦІЙНО", str(called_prompt))

