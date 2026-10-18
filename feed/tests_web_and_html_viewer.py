"""Tests for web links embed preview and HTML site interactive rendering in teacher file viewer."""
import os
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from .middleware import set_has_admin
from .models import Assignment, ClassGroup, Submission, SubmissionFile, Teacher
from .utils import parse_embed_url


class WebAndHtmlViewerTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        self.user = User.objects.create_user('html_viewer_teacher', password='test-password')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель Інформатики')
        self.group = ClassGroup.objects.create(name='9-Б', grade=9, letter='Б')
        self.teacher.classes.add(self.group)
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Веб-проєкт HTML', status='published')
        self.assignment.classes.add(self.group)
        self.client.force_login(self.user)

    def test_parse_embed_url_schemes_and_platforms(self):
        # 1. Without scheme -> auto prepends https://
        res1 = parse_embed_url('mysite.org/project')
        self.assertEqual(res1['embed_url'], 'https://mysite.org/project')
        self.assertEqual(res1['embed_type'], 'generic')

        # 2. Local network IP without scheme -> auto prepends http://
        res2 = parse_embed_url('192.168.1.13:8080/demo/')
        self.assertEqual(res2['embed_url'], 'http://192.168.1.13:8080/demo/')

        # 3. Scratch projects
        res3 = parse_embed_url('https://scratch.mit.edu/projects/987654321/')
        self.assertEqual(res3['embed_type'], 'scratch')
        self.assertEqual(res3['embed_url'], 'https://scratch.mit.edu/projects/987654321/embed')
        self.assertEqual(res3['platform'], 'Scratch')

        # 4. YouTube
        res4 = parse_embed_url('https://www.youtube.com/watch?v=dQw4w9WgXcQ')
        self.assertEqual(res4['embed_type'], 'youtube')
        self.assertIn('https://www.youtube.com/embed/dQw4w9WgXcQ', res4['embed_url'])

        # 5. Google Drive
        res5 = parse_embed_url('https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing')
        self.assertEqual(res5['embed_type'], 'google_drive')
        self.assertEqual(res5['embed_url'], 'https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/preview')

        # 6. Google Docs
        res6 = parse_embed_url('https://docs.google.com/document/d/1XyZ/edit')
        self.assertEqual(res6['embed_type'], 'google_docs')
        self.assertEqual(res6['embed_url'], 'https://docs.google.com/document/d/1XyZ/preview')

    def test_submission_with_link_renders_web_preview_container(self):
        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Богдан',
            last_name='Хмельницький',
            link='https://example.edu.ua/school-site'
        )

        url = reverse('view_file', args=[sub.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Перевірка контексту
        self.assertEqual(resp.context['file_type'], 'link')
        self.assertEqual(resp.context['active_file_id'], 'link')

        # Перевірка HTML-контенту
        content = resp.content.decode('utf-8')
        self.assertIn('fv-web-preview-container', content)
        self.assertIn('fv-web-preview-iframe', content)
        self.assertIn('https://example.edu.ua/school-site', content)
        self.assertIn('Відкрити у новій вкладці', content)
        self.assertIn('На весь екран', content)
        self.assertIn('Оновити', content)

    def test_submission_with_both_file_and_link_allows_switching(self):
        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Оксана',
            last_name='Забужко',
            link='https://scratch.mit.edu/projects/12345678/'
        )
        sf = SubmissionFile.objects.create(
            submission=sub,
            file=SimpleUploadedFile('notes.txt', b'Hello notes'),
            original_name='notes.txt'
        )

        # 1. За замовчуванням відкривається перший файл, але в селекті є і файл, і посилання
        url = reverse('view_file', args=[sub.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_items_count'], 2)
        content = resp.content.decode('utf-8')
        self.assertIn('fv-student-files-toolbar', content)
        self.assertIn('file_id=link', content)

        # 2. Перемикання на посилання через file_id=link
        resp_link = self.client.get(url, {'file_id': 'link'})
        self.assertEqual(resp_link.status_code, 200)
        self.assertEqual(resp_link.context['file_type'], 'link')
        self.assertEqual(resp_link.context['active_file_id'], 'link')
        content_link = resp_link.content.decode('utf-8')
        self.assertIn('fv-web-preview-container', content_link)
        self.assertIn('scratch.mit.edu/projects/12345678/embed', content_link)

    def test_html_file_submission_renders_interactive_preview_and_code_toggle(self):
        html_code = (
            '<!DOCTYPE html><html><head><title>Мій сайт</title></head>'
            '<body><h1>Вітаю на моєму сайті</h1><p>Робота учня 9 класу</p></body></html>'
        )
        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Тарас',
            last_name='Шевченко'
        )
        sf = SubmissionFile.objects.create(
            submission=sub,
            file=SimpleUploadedFile('index.html', html_code.encode('utf-8')),
            original_name='index.html'
        )

        # Перевірка типу файлу в моделях
        self.assertEqual(sf.get_file_type(), 'html')
        self.assertEqual(sf.get_file_icon(), '🌐')

        # Запит переглядача
        url = reverse('view_file', args=[sub.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

        # Перевірка контексту
        self.assertEqual(resp.context['file_type'], 'html')
        self.assertIn('inline=1', resp.context['html_raw_url'])

        # Перевірка наявності елементів на сторінці
        content = resp.content.decode('utf-8')
        self.assertIn('fv-html-site-container', content)
        self.assertIn('fv-html-sandbox-frame', content)
        self.assertIn('btn-html-view-preview', content)
        self.assertIn('btn-html-view-code', content)
        self.assertIn('fv-html-code-panel', content)
        self.assertIn('Вітаю на моєму сайті', content)
        self.assertIn('switchHtmlViewMode', content)

    def test_view_submission_raw_file_inline_for_html(self):
        html_code = '<html><body><h1>Тестовий HTML</h1></body></html>'
        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Леся',
            last_name='Українка'
        )
        sf = SubmissionFile.objects.create(
            submission=sub,
            file=SimpleUploadedFile('page.html', html_code.encode('utf-8')),
            original_name='page.html'
        )

        raw_url = reverse('view_submission_raw_file', args=[sub.id])

        # Без inline=1 -> завантажується як файл
        resp_download = self.client.get(raw_url)
        self.assertEqual(resp_download.status_code, 200)
        self.assertIn('attachment', resp_download.headers.get('Content-Disposition', ''))

        # З inline=1 -> відкривається у браузері / фреймі як веб-сторінка
        resp_inline = self.client.get(raw_url, {'inline': '1'})
        self.assertEqual(resp_inline.status_code, 200)
        self.assertIn('text/html', resp_inline.headers.get('Content-Type', ''))
        self.assertIn('inline', resp_inline.headers.get('Content-Disposition', ''))
        body = b''.join(resp_inline.streaming_content).decode('utf-8')
        self.assertEqual(body, html_code)
