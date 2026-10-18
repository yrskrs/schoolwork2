"""Tests for web links embed preview and HTML site interactive rendering in teacher file viewer."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from django.conf import settings
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
        from django.urls import resolve
        self.assertEqual(resolve(resp.context['html_raw_url']).url_name, 'submission_html_asset')

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

    def test_interactive_html_keeps_scripts_and_styles_in_an_opaque_sandbox(self):
        sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
            class_group=self.group, first_name='Synthetic', last_name='HTML')
        item = SubmissionFile.objects.create(submission=sub, original_name='index.html',
            file=SimpleUploadedFile('index.html', b'<button onclick="this.textContent=1">0</button>'))
        url = reverse('view_submission_raw_file', args=[sub.pk])
        inline = self.client.get(url, {'inline': '1', 'file_id': item.pk})
        policy = inline['Content-Security-Policy']
        self.assertIn('sandbox allow-scripts;', policy)
        self.assertNotIn('allow-same-origin', policy)
        self.assertIn("style-src 'self' 'unsafe-inline'", policy)
        self.assertIn("connect-src 'none'", policy)
        b''.join(inline.streaming_content)
        download = self.client.get(url, {'file_id': item.pk})
        self.assertIn('attachment', download['Content-Disposition'])
        self.assertNotIn('allow-scripts', download['Content-Security-Policy'])
        b''.join(download.streaming_content)
        public = self.client.get(item.file.url, {'inline': '1'})
        self.assertNotIn('allow-scripts', public['Content-Security-Policy'])
        b''.join(public.streaming_content)
        page = self.client.get(reverse('view_file', args=[sub.pk]))
        self.assertContains(page, 'sandbox="allow-scripts"')
        self.assertNotContains(page, 'sandbox="allow-scripts allow-forms allow-same-origin')

    def test_html_asset_grant_is_limited_expiring_and_cookie_independent(self):
        from django.core import signing
        from django.test import Client
        from unittest.mock import patch
        sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
            class_group=self.group, first_name='Synthetic', last_name='Assets')
        html = SubmissionFile.objects.create(submission=sub, original_name='index.html',
            file=SimpleUploadedFile('index.html', b'<link rel="stylesheet" href="style.css">'))
        css = SubmissionFile.objects.create(submission=sub, original_name='style.css',
            file=SimpleUploadedFile('style.css', b'h1 {color:red}'))
        page = self.client.get(reverse('view_file', args=[sub.pk]))
        html_url = page.context['html_raw_url']
        anonymous = Client()
        response = anonymous.get(html_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('sandbox allow-scripts', response['Content-Security-Policy'])
        b''.join(response.streaming_content)
        asset_url = html_url.rsplit('/', 1)[0] + '/style.css'
        response = anonymous.get(asset_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'h1 {color:red}')
        self.assertEqual(response['Referrer-Policy'], 'no-referrer')
        self.assertEqual(anonymous.post(asset_url).status_code, 405)
        self.assertEqual(anonymous.get(asset_url.rsplit('/', 1)[0] + '/missing.css').status_code, 404)
        with patch('django.core.signing.time.time', return_value=__import__('time').time() + 1801):
            self.assertEqual(anonymous.get(asset_url).status_code, 404)
        token = signing.dumps({'submission': sub.pk, 'teacher': self.teacher.pk}, salt='submission-html-preview')
        other = Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
            class_group=self.group, first_name='Synthetic', last_name='Other')
        self.assertEqual(anonymous.get(reverse('submission_html_asset', args=[other.pk, token, 'style.css'])).status_code, 404)
        self.user.is_active = False
        self.user.save(update_fields=['is_active'])
        self.assertEqual(anonymous.get(asset_url).status_code, 404)

    def test_raw_assets_cannot_escape_into_sibling_directory_or_return_wrong_file(self):
        sub = Submission.objects.create(assignment=self.assignment, teacher=self.teacher,
            class_group=self.group, first_name='Synthetic', last_name='Paths')
        with TemporaryDirectory(dir=settings.MEDIA_ROOT, prefix='synthetic-asset-') as folder:
            root = Path(folder)
            inside = root / 'inside'
            outside = root / 'inside-private'
            inside.mkdir(); outside.mkdir()
            (inside / 'index.html').write_text('<h1>Synthetic</h1>')
            (outside / 'secret.txt').write_text('synthetic private file')
            SubmissionFile.objects.create(submission=sub, original_name='index.html',
                file=str((inside / 'index.html').relative_to(settings.MEDIA_ROOT)))
            for asset in ('../inside-private/secret.txt', 'missing.css'):
                url = reverse('view_submission_raw_file_asset', args=[sub.pk, asset])
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_web_proxy_serves_html_and_injects_base_tag(self):
        from unittest.mock import MagicMock, patch
        import io

        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Михайло',
            last_name='Грушевський',
            link='https://compx.ua/noutbuki/'
        )

        mock_html = '<!DOCTYPE html><html><head><title>CompX</title></head><body><h1>Ноутбуки</h1></body></html>'.encode('utf-8')
        mock_response = MagicMock()
        mock_response.read.return_value = mock_html
        mock_response.geturl.return_value = 'https://compx.ua/ua/noutbuki/'
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda k, d=None: {
            'content-type': 'text/html; charset=utf-8',
            'content-encoding': '',
            'x-frame-options': 'SAMEORIGIN'
        }.get(k.lower(), d)
        mock_headers.get_content_charset.return_value = 'utf-8'
        mock_response.headers = mock_headers

        proxy_url = reverse('view_submission_web_proxy', args=[sub.id])
        with patch('urllib.request.urlopen', return_value=mock_response):
            resp = self.client.get(proxy_url)

        self.assertEqual(resp.status_code, 200)
        self.assertIn('text/html', resp.headers.get('Content-Type', ''))
        body = resp.content.decode('utf-8')
        self.assertIn('<base href="https://compx.ua/ua/noutbuki/" target="_blank">', body)
        self.assertIn('Object.defineProperty(window,"top"', body)
        self.assertIn('Ноутбуки', body)

    def test_submission_viewer_includes_proxy_source_and_width_toggle(self):
        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Іван',
            last_name='Франко',
            link='https://compx.ua/'
        )

        url = reverse('view_file', args=[sub.id])
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.context.get('web_proxy_url'))

        content = resp.content.decode('utf-8')
        self.assertIn('btn-web-toggle-width', content)
        self.assertIn('toggleWebWidthExpand', content)
        self.assertIn('reloadWebPreviewFrame', content)
        self.assertIn(resp.context['web_proxy_url'], content)

    def test_rewrite_html_resources_to_absolute_opencart_and_relative_assets(self):
        from .views import _rewrite_html_resources_to_absolute

        html = """<!DOCTYPE html>
<html>
<head>
    <meta http-equiv="X-Frame-Options" content="SAMEORIGIN">
    <meta http-equiv="Content-Security-Policy" content="frame-ancestors 'self'">
    <base href="https://compx.ua/" />
    <link href="catalog/view/theme/oct_ultrastore/stylesheet/dynamic.css?v=1" rel="stylesheet" />
    <link rel="icon" href="/favicon.ico" />
    <script src="catalog/view/javascript/jquery/jquery-3.7.1.min.js"></script>
</head>
<body>
    <img src="image/catalog/logo.png" data-src="image/catalog/lazy-logo.png" alt="CompX">
    <picture>
        <source srcset="image/banner-1x.webp 1x, image/banner-2x.webp 2x">
        <img src="image/banner.jpg" alt="Banner">
    </picture>
    <a href="ua/noutbuki/">Ноутбуки</a>
    <a href="#reviews">Відгуки</a>
    <a href="javascript:void(0)" onclick="openCart()">Кошик</a>
</body>
</html>"""

        rewritten = _rewrite_html_resources_to_absolute(html, 'https://compx.ua/ua/noutbuki/')

        # 1. Base URL збережено як ефективний https://compx.ua/ з target="_blank"
        self.assertIn('<base href="https://compx.ua/" target="_blank">', rewritten)
        # 2. X-Frame-Options та CSP видалено
        self.assertNotIn('X-Frame-Options', rewritten)
        self.assertNotIn('frame-ancestors', rewritten)
        # 3. CSS-посилання перетворено на абсолютний URL
        self.assertIn('href="https://compx.ua/catalog/view/theme/oct_ultrastore/stylesheet/dynamic.css?v=1"', rewritten)
        self.assertIn('href="https://compx.ua/favicon.ico"', rewritten)
        # 4. Скрипти та картинки перетворено на абсолютні URL
        self.assertIn('src="https://compx.ua/catalog/view/javascript/jquery/jquery-3.7.1.min.js"', rewritten)
        self.assertIn('src="https://compx.ua/image/catalog/logo.png"', rewritten)
        self.assertIn('data-src="https://compx.ua/image/catalog/lazy-logo.png"', rewritten)
        self.assertIn('srcset="https://compx.ua/image/banner-1x.webp 1x, https://compx.ua/image/banner-2x.webp 2x"', rewritten)
        # 5. Посилання на сторінки отримали target="_blank" та абсолютний URL
        self.assertIn('href="https://compx.ua/ua/noutbuki/"', rewritten)
        self.assertIn('target="_blank"', rewritten)
        # 6. Якорі та JS посилання не змінювались
        self.assertIn('href="#reviews"', rewritten)
        self.assertIn('href="javascript:void(0)"', rewritten)

    def test_web_proxy_rewrites_relative_stylesheets_and_scripts(self):
        from unittest.mock import MagicMock, patch

        sub = Submission.objects.create(
            assignment=self.assignment,
            teacher=self.teacher,
            class_group=self.group,
            first_name='Леся',
            last_name='Косач',
            link='https://compx.ua/ua/noutbuki/'
        )

        sample_page = (
            '<!DOCTYPE html><html><head><base href="https://compx.ua/" />'
            '<link href="catalog/theme.css" rel="stylesheet">'
            '</head><body><h1>Тест</h1></body></html>'
        ).encode('utf-8')

        mock_resp = MagicMock()
        mock_resp.read.return_value = sample_page
        mock_resp.geturl.return_value = 'https://compx.ua/ua/noutbuki/'
        mock_headers = MagicMock()
        mock_headers.get.side_effect = lambda k, d=None: {
            'content-type': 'text/html; charset=utf-8',
            'content-encoding': '',
        }.get(k.lower(), d)
        mock_headers.get_content_charset.return_value = 'utf-8'
        mock_resp.headers = mock_headers

        proxy_url = reverse('view_submission_web_proxy', args=[sub.id])
        with patch('urllib.request.urlopen', return_value=mock_resp):
            resp = self.client.get(proxy_url)

        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode('utf-8')
        self.assertIn('href="https://compx.ua/catalog/theme.css"', body)
        self.assertIn('<base href="https://compx.ua/" target="_blank">', body)


