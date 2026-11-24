from collections import Counter
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, AssignmentFile, AssignmentLink, ClassGroup, Submission, Subject, Teacher


class StudentSubmissionLayoutTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_user('submission_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=user, full_name='Вчитель')
        self.subject = Subject.objects.create(name='Інформатика')
        self.classes = [ClassGroup.objects.create(name=f'9-{letter}', grade=9, letter=letter) for letter in ['А', 'Б']]
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject,
            title='Практична робота: створення та форматування електронної таблиці з підсумками',
            description='Обчисліть суму та збережіть таблицю.',
            status=Assignment.STATUS_PUBLISHED, published_at=timezone.now(),
            allow_ai_usage=True, allow_student_ai_check=True,
        )
        self.assignment.classes.add(*self.classes)
        self.url = reverse('submit_assignment', args=[self.assignment.pk])

    def page(self, response):
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def assert_class_summary(self, page, expected):
        self.assertEqual(page.xpath('//*[@data-submission-class]/text()'), [expected])

    def data(self, **overrides):
        return {
            'full_name': 'Тестовий Учень', 'class_group': str(self.classes[1].pk),
            'comment_student': 'Відповідь на завдання', **overrides,
        }

    def test_complete_topic_class_and_secondary_actions_are_available(self):
        page = self.page(self.client.get(self.url, {'class': self.classes[1].pk}))
        self.assertEqual(page.get_element_by_id('submission-topic').text_content(), self.assignment.title)
        self.assert_class_summary(page, self.classes[1].name)
        self.assertEqual(page.xpath('//select[@id="id_class_group"]/option[@selected]/@value'), [str(self.classes[1].pk)])
        self.assertNotIn('Читати умову', page.get_element_by_id('submit-page-wrapper').text_content())
        form = page.get_element_by_id('submit-form')
        self.assertEqual(len(page.xpath('//form[@id="submit-form"]')), 1)
        self.assertFalse(form.xpath('.//form'))
        self.assertTrue(form.xpath('.//*[@id="group_full_name"]//div[contains(@class,"submission-student-row")]//button[@id="btn-add-coauthor" and @type="button"]'))
        self.assertTrue(form.xpath('.//*[@id="group_full_name"]//div[contains(@class,"submission-student-row")]//div[@id="student-roster-picker-wrapper"]'))
        self.assertTrue(form.xpath('.//aside//button[@onclick="openCriteriaModal()"]'))
        self.assertFalse(form.xpath('.//aside//button[@id="btn-add-coauthor"]'))
        self.assertTrue(form.xpath('.//*[@id="group_full_name"]//button[@id="btn-add-coauthor" and contains(@class,"submission-coauthor-button")]'))
        coauthor_btn = page.get_element_by_id('btn-add-coauthor')
        self.assertIn('Додати співавтора', coauthor_btn.get('title', ''))
        self.assertIn('Для роботи в парі чи групі', coauthor_btn.get('title', ''))
        self.assertEqual(form.xpath('.//section[contains(@class,"submission-form-card")]/@aria-labelledby'), ['submission-identity-title', 'submission-work-title'])
        self.assertTrue(form.xpath('.//a[@href="#id_class_group"]'))
        self.assertTrue(form.xpath('.//*[@data-submission-name]'))
        self.assertTrue(form.xpath('.//*[@data-submission-work]'))
        self.assertTrue(form.xpath('.//*[@data-submission-coauthors-row and @hidden]//*[@data-submission-coauthors]'))
        self.assertTrue(page.xpath('//a[@href=$url]', url=reverse('assignment_detail', args=[self.assignment.pk]) + f'?class={self.classes[1].pk}'))
        self.assertTrue(form.xpath('.//footer//button[@type="submit" and @id="submission-send-button"]'))
        for name in ['full_name', 'class_group', 'files', 'link', 'comment_student', 'csrfmiddlewaretoken']:
            self.assertTrue(form.xpath('.//*[@name=$name]', name=name), name)
        self.assertTrue(page.get_element_by_id('submission-optional-fields') is not None)
        self.assertTrue(form.xpath('.//label[@for="id_link"] and not(.//label[@for="id_link" and contains(@style,"display:none")])'))
        self.assertTrue(form.xpath('.//label[@for="id_comment_student"] and not(.//label[@for="id_comment_student" and contains(@style,"display:none")])'))
        self.assertIn('висновки по своїй роботі', page.get_element_by_id('submission-optional-fields').text_content())
        self.assertNotIn('open', page.get_element_by_id('submission-condition-details').attrib)
        ids = Counter(page.xpath('//*[@id]/@id'))
        self.assertEqual([key for key, count in ids.items() if count > 1], [])

    def test_invalid_form_keeps_selected_class_and_comment_visible(self):
        response = self.client.post(self.url, self.data(full_name=''))
        page = self.page(response)
        self.assertIn('full_name', response.context['form'].errors)
        self.assert_class_summary(page, self.classes[1].name)
        self.assertTrue(page.get_element_by_id('submission-optional-fields') is not None)
        self.assertEqual(page.get_element_by_id('id_comment_student').text.lstrip('\n'), 'Відповідь на завдання')
        self.assertFalse(Submission.objects.exists())

    def test_invalid_class_does_not_display_a_different_class_as_selected(self):
        other = ClassGroup.objects.create(name='10-А', grade=10, letter='А')
        for value in [str(other.pk), 'invalid-id', '']:
            with self.subTest(value=value):
                response = self.client.post(self.url, self.data(class_group=value))
                page = self.page(response)
                self.assertIn('class_group', response.context['form'].errors)
                self.assert_class_summary(page, 'Клас не обрано')
                options = page.xpath('//select[@id="id_class_group"]/option')
                self.assertEqual(options[0].get('value'), '')
        self.assertFalse(Submission.objects.exists())

    def test_class_in_link_must_belong_to_assignment(self):
        other = ClassGroup.objects.create(name='10-А', grade=10, letter='А')
        page = self.page(self.client.get(self.url, {'class': other.pk}))
        self.assert_class_summary(page, self.classes[0].name)
        self.assertNotIn(other.name, page.xpath('//select[@id="id_class_group"]')[0].text_content())

    def test_saved_comment_and_link_are_sent_from_rendered_form(self):
        for name, value in [('comment_student', 'Текст виконаної роботи'), ('link', 'https://example.org/answer')]:
            with self.subTest(name=name):
                page = self.page(self.client.get(self.url, {'class': self.classes[1].pk}))
                form = page.get_element_by_id('submit-form')
                payload = {}
                for field in form.xpath('.//input[@name and @type!="file"] | .//select[@name] | .//textarea[@name]'):
                    if field.tag == 'select':
                        payload[field.get('name')] = field.xpath('.//option[@selected]/@value')[0]
                    elif field.tag == 'textarea':
                        payload[field.get('name')] = field.text or ''
                    else:
                        payload[field.get('name')] = field.get('value', '')
                payload.update(full_name='Тестовий Учень', **{name: value})
                response = self.client.post(self.url, payload)
                self.assertRedirects(response, reverse('submit_success', args=[self.assignment.pk]))
                sub = Submission.objects.filter(primary_submission__isnull=True).latest('pk')
                self.assertEqual(sub.class_group, self.classes[1])
                self.assertEqual(getattr(sub, name), value)

    def test_file_and_coauthor_submission_still_work(self):
        response = self.client.post(self.url, self.data(
            comment_student='', coauthors=['Другий Учень'],
            files=[SimpleUploadedFile('answer.txt', b'Completed answer')],
        ))
        self.assertRedirects(response, reverse('submit_success', args=[self.assignment.pk]))
        sub = Submission.objects.get(primary_submission__isnull=True)
        self.assertEqual(sub.class_group, self.classes[1])
        self.assertEqual(sub.files.count(), 1)
        self.assertEqual(sub.coauthor_submissions.count(), 1)

    def test_file_save_error_keeps_class_summary_and_displays_error(self):
        with patch('feed.forms.SubmissionForm.save', side_effect=OSError('test failure')):
            response = self.client.post(self.url, self.data())
        page = self.page(response)
        self.assert_class_summary(page, self.classes[1].name)
        self.assertTrue(page.xpath('//*[@role="alert"]'))
        self.assertTrue(response.context['form'].non_field_errors())
        self.assertFalse(Submission.objects.exists())

    def test_ai_notice_uses_teacher_permission_and_preserves_self_check_notice(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Використання ШІ дозволено:')
        self.assertContains(response, 'Після здачі доступна перевірка роботи через ШІ!')
        self.assignment.allow_ai_usage = False
        self.assignment.save(update_fields=['allow_ai_usage'])
        response = self.client.get(self.url)
        self.assertContains(response, 'Самостійна робота:')
        self.assertNotContains(response, 'Використання ШІ дозволено:')

    def test_all_teacher_material_destinations_remain_in_condition_disclosure(self):
        material = AssignmentFile.objects.create(
            assignment=self.assignment, file=SimpleUploadedFile('instructions.txt', b'Teacher instructions'),
            original_name='Інструкція.txt',
        )
        self.assignment.link_url = 'https://example.org/primary-material'
        self.assignment.youtube_url = 'https://youtu.be/abcdefghijk'
        self.assignment.save(update_fields=['link_url', 'youtube_url'])
        AssignmentLink.objects.create(assignment=self.assignment, url='https://example.org/extra-material')
        page = self.page(self.client.get(self.url))
        condition = page.get_element_by_id('submission-condition-details')
        destinations = condition.xpath('.//a/@href')
        for target in [reverse('file_download', args=[material.pk]), 'https://example.org/primary-material',
                       'https://example.org/extra-material', 'https://www.youtube.com/watch?v=abcdefghijk']:
            self.assertIn(target, destinations)
        self.assertIn(self.assignment.description, condition.text_content())

    def test_readiness_does_not_require_files_when_text_or_link_is_supplied(self):
        for field, value in [('comment_student', 'Готова текстова відповідь'), ('link', 'https://example.org/answer')]:
            with self.subTest(field=field):
                page = self.page(self.client.post(self.url, self.data(full_name='', **{field: value})))
                self.assertEqual(page.xpath('//*[@data-submission-work]/text()'), ['Відповідь додано'])
                self.assertTrue(page.get_element_by_id('submission-send-button').getparent().xpath('.//*[@data-submission-work]'))

    def test_manual_student_entry_toggle_controls_ui_and_post_submission(self):
        from .models import Student
        Student.objects.create(class_group=self.classes[1], last_name='Шевченко', first_name='Тарас')

        get_disabled = self.client.get(self.url, {'class': self.classes[1].pk})
        self.assertFalse(self.assignment.allow_manual_student_entry)
        self.assertContains(get_disabled, 'const ALLOW_MANUAL_STUDENT_ENTRY = false;')
        page_disabled = self.page(get_disabled)
        self.assertTrue(page_disabled.xpath('//input[@id="id_other_student"]/ancestor::label[@hidden]'))

        post_disabled = self.client.post(self.url, self.data(
            full_name='Гість Позакласом', other_student='on',
        ))
        self.assertEqual(post_disabled.status_code, 200)
        self.assertIn('full_name', post_disabled.context['form'].errors)
        self.assertIn('вчитель заборонив', post_disabled.context['form'].errors['full_name'][0])
        self.assertFalse(Submission.objects.exists())

        self.assignment.allow_manual_student_entry = True
        self.assignment.save(update_fields=['allow_manual_student_entry'])
        get_enabled = self.client.get(self.url, {'class': self.classes[1].pk})
        self.assertContains(get_enabled, 'const ALLOW_MANUAL_STUDENT_ENTRY = true;')
        page_enabled = self.page(get_enabled)
        self.assertFalse(page_enabled.xpath('//input[@id="id_other_student"]/ancestor::label[@hidden]'))

        post_enabled = self.client.post(self.url, self.data(
            full_name='Гість Позакласом', other_student='on',
        ))
        self.assertEqual(post_enabled.status_code, 302)
        self.assertEqual(Submission.objects.count(), 1)

    def test_disabled_manual_entry_explains_whom_to_ask(self):
        from .forms import SubmissionForm
        from .models import RosterState, Student

        group = self.classes[1]
        Student.objects.create(class_group=group, last_name='Шевченко', first_name='Тарас')
        RosterState.objects.update_or_create(pk=1, defaults={'data': {'journal_imports': {
            f'{self.teacher.pk}:synthetic': {'class_id': group.pk, 'students': {}}}}})
        self.assertFalse(self.assignment.allow_manual_student_entry)
        form = SubmissionForm({
            'full_name': 'Гість Позакласом', 'class_group': str(group.pk),
            'comment_student': 'Відповідь на завдання',
        }, {}, assignment=self.assignment)
        self.assertFalse(form.is_valid())
        self.assertIn('попросіть учителя', form.errors['full_name'][0])

    def test_legacy_assignments_keep_manual_entry_and_new_ones_stay_opt_in(self):
        from importlib import import_module
        from django.apps import apps as django_apps

        legacy = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Завдання до 4.14.0',
            description='Створене раніше.', status=Assignment.STATUS_PUBLISHED,
        )
        # 0084 added the switch with default=False, so old rows start disabled.
        self.assertFalse(legacy.allow_manual_student_entry)

        backfill = import_module('feed.migrations.0085_assignment_manual_entry_backfill')
        backfill.enable_manual_entry_for_existing(django_apps, None)

        legacy.refresh_from_db()
        self.assertTrue(legacy.allow_manual_student_entry)
        # New assignments keep the teacher-controlled opt-in default.
        fresh = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Завдання після 4.15.0',
            description='Створене після оновлення.', status=Assignment.STATUS_PUBLISHED,
        )
        fresh.refresh_from_db()
        self.assertFalse(fresh.allow_manual_student_entry)
