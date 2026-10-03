from collections import Counter

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, AssignmentFile, AssignmentLink, ClassGroup, Subject, Teacher


class AssignmentDetailLayoutTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_user('detail_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.subject = Subject.objects.create(name='Інформатика')
        self.classes = [ClassGroup.objects.create(name=f'{grade}-А', grade=grade, letter='А') for grade in [5, 12]]
        self.assignment = Assignment.objects.create(
            teacher=self.teacher, subject=self.subject, title='Створення та оформлення результатів практичної роботи',
            description='<p>Опрацюй матеріал.</p><ol><li>Виконай вправу 6.</li><li>Збережи результат.</li></ol>',
            status=Assignment.STATUS_PUBLISHED, published_at=timezone.now(),
        )
        self.assignment.classes.add(*self.classes)
        self.material = AssignmentFile.objects.create(
            assignment=self.assignment, file=SimpleUploadedFile('practice.txt', b'Exercise 6: complete the task'),
            original_name='Практичне завдання.txt',
        )
        self.url = reverse('assignment_detail', args=[self.assignment.pk])

    def page(self, response):
        self.assertEqual(response.status_code, 200)
        return html.fromstring(response.content)

    def assert_submit_links(self, page, expected):
        base_url = reverse('submit_assignment', args=[self.assignment.pk])
        links = page.xpath('//a[starts-with(@href, $url)]/@href', url=base_url)
        self.assertTrue(links)
        self.assertEqual(set(links), {expected})

    def test_instructions_materials_and_submission_follow_reading_order(self):
        page = self.page(self.client.get(self.url))
        self.assertEqual(page.get_element_by_id('assignment-topic').text_content(), self.assignment.title)
        article = page.xpath('//article')[0]
        instruction = article.get_element_by_id('assignment-desc-content')
        self.assertEqual(instruction.xpath('.//li/text()'), ['Виконай вправу 6.', 'Збережи результат.'])
        section_ids = article.xpath('./section/@id')
        self.assertEqual(section_ids, ['assignment-instructions-card', 'assignment-materials-section', 'assignment-submit-section'])
        layout = article.getparent()
        self.assertEqual([child.tag for child in layout], ['article', 'aside'])
        self.assertEqual(len(layout.xpath('.//button[@onclick="openCriteriaModal()"]')), 1)
        preview = page.get_element_by_id('inline-file-preview-1')
        self.assertIn('display:none', preview.get('style'))
        button = page.get_element_by_id('preview-toggle-btn-1')
        self.assertEqual(button.get('aria-controls'), preview.get('id'))
        self.assertEqual(button.get('aria-expanded'), 'false')
        self.assertTrue(page.xpath('//a[@href=$url]', url=reverse('file_download', args=[self.material.pk])))
        ids = Counter(page.xpath('//*[@id]/@id'))
        self.assertFalse([key for key, count in ids.items() if count > 1])

    def test_class_context_is_preserved_for_fifth_and_twelfth_grade(self):
        for group in self.classes:
            with self.subTest(grade=group.grade):
                page = self.page(self.client.get(self.url, {'class': group.pk}))
                self.assert_submit_links(page, reverse('submit_assignment', args=[self.assignment.pk]) + f'?class={group.pk}')
                self.assertIn(group.name, page.get_element_by_id('assignment-topic').getparent().text_content())

    def test_unassigned_class_is_not_forwarded_to_submission(self):
        other = ClassGroup.objects.create(name='8-Б', grade=8, letter='Б')
        page = self.page(self.client.get(self.url, {'class': other.pk}))
        self.assert_submit_links(page, reverse('submit_assignment', args=[self.assignment.pk]))

    def test_missing_description_directs_student_to_attached_material(self):
        self.assignment.description = ''
        self.assignment.save(update_fields=['description'])
        page = self.page(self.client.get(self.url))
        self.assertIn('Вчитель додав завдання в матеріалах нижче.', page.get_element_by_id('assignment-desc-content').text_content())
        self.assertTrue(page.xpath('//a[@href="#assignment-materials-section"]'))

    def test_missing_description_and_materials_has_no_broken_section_links(self):
        self.material.delete()
        self.assignment.description = ''
        self.assignment.save(update_fields=['description'])
        page = self.page(self.client.get(self.url))
        self.assertIn('Уточни в нього, що потрібно виконати.', page.get_element_by_id('assignment-desc-content').text_content())
        self.assertFalse(page.xpath('//*[@id="assignment-materials-section"]'))
        for target in page.xpath('//*[contains(@class,"assignment-detail-page")]//a[starts-with(@href,"#")]/@href'):
            self.assertTrue(page.xpath('//*[@id=$target]', target=target[1:]), target)

    def test_no_submission_assignment_explains_that_upload_is_not_required(self):
        self.assignment.no_submission_required = True
        self.assignment.save(update_fields=['no_submission_required'])
        page = self.page(self.client.get(self.url))
        self.assertFalse(page.xpath('//a[starts-with(@href,$url)]', url=reverse('submit_assignment', args=[self.assignment.pk])))
        self.assertIn('Надсилати файли або відповідь на сайті не потрібно.', page.get_element_by_id('assignment-submit-section').text_content())
        self.assertFalse(page.xpath('//*[contains(@class,"assignment-detail-send-bar")]'))

    def test_archived_assignment_has_no_upload_action(self):
        self.assignment.status = Assignment.STATUS_ARCHIVED
        self.assignment.save(update_fields=['status'])
        page = self.page(self.client.get(self.url))
        self.assertFalse(page.xpath('//a[starts-with(@href,$url)]', url=reverse('submit_assignment', args=[self.assignment.pk])))
        self.assertIn('Прийом робіт закрито', page.get_element_by_id('assignment-submit-section').text_content())
        self.assertTrue(page.xpath('//a[@href=$url]', url=reverse('file_download', args=[self.material.pk])))

    def test_teacher_controls_and_student_ai_permission_remain_available(self):
        page = self.page(self.client.get(self.url))
        self.assertFalse(page.xpath('//button[contains(@onclick,"openAiUnderstandingModal")]'))
        self.assignment.allow_student_ai_understanding = True
        self.assignment.save(update_fields=['allow_student_ai_understanding'])
        page = self.page(self.client.get(self.url))
        self.assertEqual(len(page.xpath('//button[contains(@onclick,"openAiUnderstandingModal")]')), 1)
        self.client.force_login(self.user)
        page = self.page(self.client.get(self.url))
        self.assertIsNotNone(page.get_element_by_id('btn-quick-edit-top'))
        self.assertIsNotNone(page.get_element_by_id('btn-quick-duplicate-top'))
        self.assertIsNotNone(page.get_element_by_id('btn-ai-understanding-top'))
        self.assertTrue(page.xpath('//a[@href=$url]', url=reverse('assignment_edit', args=[self.assignment.pk])))
        self.assertTrue(page.xpath('//button[contains(@onclick,"openDuplicateModal")]'))

    def test_web_links_and_video_are_grouped_with_files(self):
        AssignmentLink.objects.create(assignment=self.assignment, url='https://example.org/material', label='Довідка до вправи')
        self.assignment.youtube_url = 'https://youtu.be/abcdefghijk'
        self.assignment.save(update_fields=['youtube_url'])
        page = self.page(self.client.get(self.url))
        materials = page.get_element_by_id('assignment-materials-section')
        self.assertTrue(materials.xpath('.//a[@href="https://example.org/material"]'))
        self.assertTrue(materials.xpath('.//iframe[contains(@src,"youtube.com/embed/abcdefghijk")]'))
        self.assertTrue(materials.xpath('.//*[@id="assignment-files-section"]'))
