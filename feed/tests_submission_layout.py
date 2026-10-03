from collections import Counter
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, ClassGroup, Submission, Subject, Teacher


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
        self.assertEqual(page.xpath('//*[@data-submission-class]/text()'), [expected, expected])

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
        self.assertTrue(form.xpath('.//aside//button[@id="btn-add-coauthor" and @type="button"]'))
        self.assertTrue(form.xpath('.//aside//button[@onclick="openCriteriaModal()"]'))
        self.assertTrue(form.xpath('.//footer//button[@type="submit" and @id="submission-send-button"]'))
        for name in ['full_name', 'class_group', 'files', 'link', 'comment_student', 'csrfmiddlewaretoken']:
            self.assertTrue(form.xpath('.//*[@name=$name]', name=name), name)
        self.assertNotIn('open', page.get_element_by_id('submission-optional-fields').attrib)
        self.assertNotIn('open', page.get_element_by_id('submission-condition-details').attrib)
        ids = Counter(page.xpath('//*[@id]/@id'))
        self.assertEqual([key for key, count in ids.items() if count > 1], [])

    def test_invalid_form_keeps_selected_class_and_comment_visible(self):
        response = self.client.post(self.url, self.data(full_name=''))
        page = self.page(response)
        self.assertIn('full_name', response.context['form'].errors)
        self.assert_class_summary(page, self.classes[1].name)
        self.assertIn('open', page.get_element_by_id('submission-optional-fields').attrib)
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
