"""Material reading modes and review indicators retain access and data boundaries."""
import io
import zipfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from lxml import html

from .middleware import set_has_admin
from .models import Assignment, AssignmentFile, ClassGroup, Submission, SubmissionComment, Teacher


class MaterialDialogTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_user('material_dialog_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.group = ClassGroup.objects.create(name='7-А', grade=7, letter='А')
        self.teacher.classes.add(self.group)
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Вправа 6', status='published')
        self.assignment.classes.add(self.group)
        self.material = AssignmentFile.objects.create(assignment=self.assignment, file=SimpleUploadedFile('lesson.pptx', b'fixture'), original_name='Урок.pptx')
        self.preview_url = reverse('file_preview', args=[self.material.pk])

    @patch('feed.views.get_presentation_slides', return_value=(['/media/previews/1/slide-1.jpg', '/media/previews/1/slide-2.jpg'], '/media/previews/1/presentation.pdf'))
    def test_presentation_returns_slides_without_waiting_for_conversion(self, slides):
        response = self.client.get(self.preview_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['type'], 'slides')
        self.assertEqual(response.json()['count'], 2)
        self.assertFalse(slides.call_args.kwargs['wait_if_missing'])

    @patch('feed.views.get_pdf_preview_url', return_value=None)
    @patch('feed.views.get_presentation_slides', return_value=([], None))
    def test_preparing_presentation_returns_pending_not_empty_html(self, slides, pdf):
        self.assertEqual(self.client.get(self.preview_url).json(), {'type': 'pending'})

    @patch('feed.views.get_pdf_preview_url', return_value='/assignment/file/1/view/?preview_pdf=1')
    @patch('feed.views.get_presentation_slides', return_value=([], None))
    def test_prepared_pdf_is_available_when_slide_images_are_missing(self, slides, pdf):
        data = self.client.get(self.preview_url).json()
        self.assertEqual(data['type'], 'url')
        self.assertEqual(data['file_type'], 'pdf')
        self.assertIn('preview_pdf=1', data['url'])

    @patch('feed.views.get_presentation_slides')
    @patch('feed.views.convert_pptx_to_html', return_value=('<p>Вправа 6</p><script>alert(1)</script><img src="x" onerror="alert(1)">', None))
    def test_reading_mode_works_while_slides_are_preparing_and_sanitizes_content(self, convert, slides):
        data = self.client.get(self.preview_url, {'mode': 'text'}).json()
        self.assertEqual(data['type'], 'html')
        self.assertIn('Вправа 6', data['content'])
        self.assertNotIn('<script', data['content'])
        self.assertNotIn('onerror', data['content'])
        slides.assert_not_called()

    @patch('feed.views.convert_pptx_to_html')
    def test_draft_reading_mode_is_not_accessible_anonymously_or_by_another_teacher(self, convert):
        self.assignment.status = 'draft'; self.assignment.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.preview_url, {'mode': 'text'}).status_code, 404)
        other = User.objects.create_user('other_material_teacher', password='test-password')
        Teacher.objects.create(user=other, full_name='Інший вчитель')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.preview_url, {'mode': 'text'}).status_code, 404)
        convert.assert_not_called()
        self.client.force_login(self.user)
        convert.return_value = ('<p>Умова</p>', None)
        self.assertEqual(self.client.get(self.preview_url, {'mode': 'text'}).status_code, 200)

    @patch('feed.views.get_pdf_preview_url')
    @patch('feed.views.convert_docx_to_html', return_value=('<p>Текст для читання</p>', None))
    def test_word_reading_mode_does_not_depend_on_pdf_conversion(self, convert, pdf):
        self.material.file = SimpleUploadedFile('lesson.docx', b'fixture'); self.material.original_name = 'Урок.docx'; self.material.save()
        self.assertEqual(self.client.get(self.preview_url, {'mode': 'text'}).json()['type'], 'html')
        pdf.assert_not_called()

    def test_archive_listing_remains_available_in_the_dialog(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('exercise.txt', 'Task')
        self.material.file = SimpleUploadedFile('materials.zip', buffer.getvalue()); self.material.original_name = 'Матеріали.zip'; self.material.save()
        data = self.client.get(self.preview_url).json()
        self.assertEqual(data['type'], 'archive')
        self.assertEqual(data['items'][0]['name'], 'exercise.txt')

    @patch('feed.views.get_presentation_slides', return_value=([], None))
    @patch('feed.views.convert_pptx_to_html', return_value=('<p>Матеріал</p>', None))
    def test_student_dialog_has_reading_controls_and_never_contains_teacher_indicators(self, convert, slides):
        page = html.fromstring(self.client.get(reverse('assignment_detail', args=[self.assignment.pk])).content)
        dialog = page.get_element_by_id('assignment-file-modal')
        self.assertEqual(dialog.tag, 'dialog')
        self.assertFalse(dialog.xpath('ancestor::article'))
        self.assertEqual(dialog.xpath('.//*[@data-material-mode]/@data-material-mode'), ['document', 'text'])
        self.assertTrue(page.xpath('//*[@data-material-preview=$id and @aria-haspopup="dialog"]', id=str(self.material.pk)))
        self.assertFalse(page.xpath('//*[@id="fv-quick-ai" or @id="fv-grading-card"]'))

    def test_interactive_materials_have_their_own_document_lifecycle(self):
        for extension, parser in [('.sb3', 'feed.scratch_utils.parse_scratch_sb3'), ('.hex', 'feed.microbit_utils.parse_microbit_hex')]:
            with self.subTest(extension=extension):
                self.material.file = SimpleUploadedFile('material' + extension, b'fixture')
                self.material.original_name = 'Матеріал' + extension; self.material.save()
                with patch(parser, return_value=('<button onclick="exampleControl()">Пуск</button><script>function exampleControl(){}</script>', '', None)) as parse:
                    data = self.client.get(self.preview_url).json()
                    self.assertEqual(data['file_type'], 'embedded')
                    parse.assert_not_called()
                    response = self.client.get(data['url'], {'embedded': '1', 'theme': 'dark'})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
                    self.assertContains(response, '<script>function exampleControl()')
                    self.assertContains(response, 'data-theme="dark"')
                    self.assertIn(reverse('file_view', args=[self.material.pk]), parse.call_args.kwargs['raw_file_url'])

    def test_embedded_material_keeps_draft_access_checks(self):
        self.material.file = SimpleUploadedFile('material.sb3', b'fixture')
        self.material.original_name = 'Матеріал.sb3'; self.material.save()
        self.assignment.status = 'draft'; self.assignment.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.preview_url, {'embedded': '1'}).status_code, 404)
        self.assertEqual(self.client.get(self.preview_url).status_code, 404)

    @patch('feed.microbit_utils.parse_microbit_hex', return_value=('', '', 'Непідтримуваний файл'))
    def test_embedded_parser_error_has_readable_feedback(self, parse):
        self.material.file = SimpleUploadedFile('material.hex', b'fixture')
        self.material.original_name = 'Матеріал.hex'; self.material.save()
        response = self.client.get(self.preview_url, {'embedded': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Непідтримуваний файл')


class ReviewQuickStatusTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_user('review_indicators_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        self.group = ClassGroup.objects.create(name='8-А', grade=8, letter='А')
        self.teacher.classes.add(self.group)
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Вправа', status='published')
        self.assignment.classes.add(self.group)
        self.sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.group, first_name='Учень', last_name='Приклад', file=SimpleUploadedFile('answer.txt', b'Original answer'))
        self.client.force_login(self.user)

    def page(self):
        return html.fromstring(self.client.get(reverse('view_file', args=[self.sub.pk])).content)

    def test_unchecked_work_is_distinguished_from_a_negative_ai_result(self):
        self.assertIn('не перевірено', self.page().get_element_by_id('fv-quick-ai').text_content())
        self.sub.ai_status = 'success'; self.sub.save(update_fields=['ai_status'])
        self.assertIn('Авторство: невідоме', self.page().get_element_by_id('fv-quick-ai').text_content())

    def test_ai_group_and_feedback_are_visible_without_opening_information(self):
        self.sub.ai_status = 'success'; self.sub.ai_generated_detected = True; self.sub.ai_generated_percent = 75; self.sub.is_group_work = True; self.sub.save()
        Submission.objects.create(assignment=self.assignment, teacher=self.teacher, class_group=self.group, first_name='Співавтор', last_name='Приклад', primary_submission=self.sub)
        SubmissionComment.objects.create(submission=self.sub, author=self.user, text='Додайте висновок')
        page = self.page()
        toolbar = page.xpath('//*[@class="fv-workspace-toolbar"]')[0]
        self.assertIn('Ознаки ШІ', toolbar.get_element_by_id('fv-quick-ai').text_content())
        self.assertNotIn('75%', toolbar.get_element_by_id('fv-quick-ai').text_content())
        self.assertIn('Групова (2)', toolbar.get_element_by_id('fv-quick-group').text_content())
        self.assertEqual(page.get_element_by_id('fv-comment-count').text, '1')
        self.assertNotIn('hidden', page.get_element_by_id('fv-comment-count').attrib)
        self.sub.refresh_from_db(); self.assertIsNone(self.sub.grade)

    def test_duplicate_and_allowed_collaboration_have_distinct_indicators(self):
        AssignmentFile.objects.create(assignment=self.assignment, file=SimpleUploadedFile('task.txt', b'Original answer'), original_name='Умова.txt')
        self.assertIn('Перевіряємо', self.page().get_element_by_id('fv-quick-duplicate').text_content())
        result = self.client.get(reverse('review_duplicates', args=[self.sub.pk])).json()
        self.assertIn('Копія матеріалу', html.fromstring(result['chip']).text_content())
        self.sub.ignore_plagiarism = True; self.sub.save(update_fields=['ignore_plagiarism'])
        result = self.client.get(reverse('review_duplicates', args=[self.sub.pk])).json()
        self.assertIn('Збіг дозволено', html.fromstring(result['chip']).text_content())
        self.assertIn('Спільна', html.fromstring(result['group_chip']).text_content())
