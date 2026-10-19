"""
Тести для функціоналу підключення хмарних сховищ (Nextcloud та Google Drive).
"""

from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from feed.models import Teacher, TeacherCloudStorage, Subject, ClassGroup, Assignment
from feed.cloud_storage import NextcloudClient, GoogleDriveClient, format_bytes, get_file_icon


class CloudStorageModelAndClientTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='teacher1', password='password123')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Іваненко Петро Олексійович')

    def test_teacher_cloud_storage_initial_state(self):
        """Перевірка початкового стану хмарного сховища вчителя."""
        self.assertFalse(self.teacher.has_cloud_storage)
        storage = self.teacher.get_cloud_storage()
        self.assertIsInstance(storage, TeacherCloudStorage)
        self.assertEqual(storage.teacher, self.teacher)
        self.assertFalse(storage.has_nextcloud())
        self.assertFalse(storage.has_google_drive())
        self.assertFalse(storage.has_any_connected())

    def test_teacher_cloud_storage_with_nextcloud(self):
        """Перевірка активації Nextcloud."""
        storage = self.teacher.get_cloud_storage()
        storage.nextcloud_enabled = True
        storage.nextcloud_url = 'http://localhost:8080'
        storage.nextcloud_username = 'petya'
        storage.nextcloud_password = 'secret-app-token'
        storage.save()

        self.assertTrue(storage.has_nextcloud())
        self.assertFalse(storage.has_google_drive())
        self.assertTrue(storage.has_any_connected())
        self.assertTrue(self.teacher.has_cloud_storage)

    def test_teacher_cloud_storage_with_google(self):
        """Перевірка активації Google Drive."""
        storage = self.teacher.get_cloud_storage()
        storage.google_drive_enabled = True
        storage.google_access_token = 'ya29.sample_token'
        storage.google_user_email = 'teacher@school.org'
        storage.save()

        self.assertFalse(storage.has_nextcloud())
        self.assertTrue(storage.has_google_drive())
        self.assertTrue(storage.has_any_connected())
        self.assertTrue(self.teacher.has_cloud_storage)

    def test_teacher_cloud_storage_with_google_email_password(self):
        """Перевірка активації Google Drive за логіном (Email) та паролем."""
        storage = self.teacher.get_cloud_storage()
        storage.google_drive_enabled = True
        storage.google_user_email = 'teacher@gmail.com'
        storage.google_password = 'mypassword123'
        storage.save()

        self.assertFalse(storage.has_nextcloud())
        self.assertTrue(storage.has_google_drive())
        self.assertTrue(storage.has_any_connected())
        self.assertTrue(self.teacher.has_cloud_storage)

    def test_extract_google_drive_id(self):
        """Перевірка витягування ID файлу з посилань Google Drive."""
        from feed.cloud_storage import extract_google_drive_id
        self.assertEqual(extract_google_drive_id('https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I0J/view'), '1A2B3C4D5E6F7G8H9I0J')
        self.assertEqual(extract_google_drive_id('https://docs.google.com/document/d/1DocID_12345/edit'), '1DocID_12345')
        self.assertEqual(extract_google_drive_id('https://docs.google.com/spreadsheets/d/1SheetID_67890/edit'), '1SheetID_67890')
        self.assertEqual(extract_google_drive_id('1SimpleFileId12345'), '1SimpleFileId12345')

    def test_format_bytes(self):
        """Перевірка форматування розміру файлів."""
        self.assertEqual(format_bytes(0), '0 Б')
        self.assertEqual(format_bytes(512), '512 Б')
        self.assertEqual(format_bytes(2048), '2.0 КБ')
        self.assertEqual(format_bytes(1048576 * 3.5), '3.5 МБ')

    def test_get_file_icon(self):
        """Перевірка визначення emoji-іконок для типів файлів."""
        self.assertEqual(get_file_icon('folder', is_dir=True), '📁')
        self.assertEqual(get_file_icon('document.pdf'), '📕')
        self.assertEqual(get_file_icon('table.xlsx'), '📊')
        self.assertEqual(get_file_icon('presentation.pptx'), '📽️')
        self.assertEqual(get_file_icon('code.py'), '💻')
        self.assertEqual(get_file_icon('image.png'), '🖼️')
        self.assertEqual(get_file_icon('archive.zip'), '🗜️')

    def test_nextcloud_url_normalization(self):
        """Перевірка нормалізації WebDAV URL для Nextcloud."""
        # Без протоколу
        c1 = NextcloudClient('cloud.school.ua', 'ivan', 'pass')
        self.assertEqual(c1.webdav_url, 'https://cloud.school.ua/remote.php/dav/files/ivan')

        # З локальним портом
        c2 = NextcloudClient('http://localhost:8080/', 'maria', 'pass')
        self.assertEqual(c2.webdav_url, 'http://localhost:8080/remote.php/dav/files/maria')

        # З уже готовим шляхом WebDAV
        c3 = NextcloudClient('https://cloud.com/remote.php/dav/files/user', 'user', 'pass')
        self.assertEqual(c3.webdav_url, 'https://cloud.com/remote.php/dav/files/user')

    @patch('feed.cloud_storage.get_docker_host_gateway')
    def test_nextcloud_docker_gateway_resolution(self, mock_gw):
        """Перевірка автоматичного перенаправлення localhost на gateway у Docker, коли локальний порт закритий."""
        mock_gw.return_value = '172.22.0.1'
        c = NextcloudClient('http://localhost:59999', 'admin', 'pass')
        actual_url, headers = c._resolve_url_and_headers('http://localhost:59999/remote.php/dav/files/admin/')
        self.assertEqual(headers.get('Host'), 'localhost:59999')
        self.assertIn('172.22.0.1:59999', actual_url)


class CloudStorageViewsTest(TestCase):
    def setUp(self):
        from feed import middleware
        middleware._has_admin = None
        # Потрібен суперкористувач, щоб FirstRunSetupMiddleware не перенаправляв на /setup/
        User.objects.create_superuser('admin', 'admin@example.com', 'adminpass')
        self.user = User.objects.create_user(username='teacher1', password='password123')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Іваненко Петро Олексійович')
        self.subject = Subject.objects.create(name='Інформатика', icon='💻', created_by=self.teacher)
        self.class_group = ClassGroup.objects.create(grade=9, letter='А', created_by=self.teacher)
        self.teacher.subjects.add(self.subject)
        self.teacher.classes.add(self.class_group)

        self.client = Client()
        self.client.force_login(self.user)

    def test_cloud_status_api(self):
        """Перевірка API статусу хмарних сховищ."""
        resp = self.client.get(reverse('teacher_cloud_status'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data['has_any'])
        self.assertFalse(data['nextcloud']['enabled'])
        self.assertFalse(data['google_drive']['enabled'])

    @patch('feed.cloud_storage.NextcloudClient.test_connection')
    def test_test_nextcloud_api_success(self, mock_test):
        """Тестування перевірки підключення до Nextcloud (успіх)."""
        mock_test.return_value = (True, "З'єднання успішне! 🟢")
        resp = self.client.post(reverse('teacher_cloud_test_nextcloud'), {
            'url': 'http://localhost:8080',
            'username': 'teacher',
            'password': 'token',
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])

    @patch('feed.cloud_storage.NextcloudClient.test_connection')
    def test_save_nextcloud_and_disconnect(self, mock_test):
        """Збереження налаштувань Nextcloud та наступне від'єднання."""
        mock_test.return_value = (True, "OK")
        resp = self.client.post(reverse('teacher_cloud_save_nextcloud'), {
            'cloud_action': 'save',
            'url': 'http://localhost:8080',
            'username': 'admin',
            'password': 'password',
            'ignore_ssl': '1',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(resp.status_code, 200)
        storage = self.teacher.get_cloud_storage()
        self.assertTrue(storage.has_nextcloud())
        self.assertEqual(storage.nextcloud_url, 'http://localhost:8080')
        self.assertEqual(storage.nextcloud_username, 'admin')
        self.assertTrue(storage.nextcloud_ignore_ssl)

        # Від'єднання
        resp2 = self.client.post(reverse('teacher_cloud_save_nextcloud'), {
            'cloud_action': 'disconnect'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp2.status_code, 200)
        storage.refresh_from_db()
        self.assertFalse(storage.has_nextcloud())

    @patch('feed.cloud_storage.NextcloudClient.list_directory')
    def test_cloud_browse_nextcloud(self, mock_list):
        """Перегляд папок у Nextcloud."""
        storage = self.teacher.get_cloud_storage()
        storage.nextcloud_enabled = True
        storage.nextcloud_url = 'http://localhost:8080'
        storage.nextcloud_username = 'teacher'
        storage.nextcloud_password = 'pass'
        storage.save()

        mock_list.return_value = {
            'items': [
                {'name': 'Матеріали', 'path': 'Матеріали', 'is_dir': True, 'size': 0, 'size_display': '', 'modified': '', 'icon': '📁'},
                {'name': 'Урок 1.pdf', 'path': 'Урок 1.pdf', 'is_dir': False, 'size': 1024, 'size_display': '1.0 КБ', 'modified': '', 'icon': '📕'},
            ],
            'breadcrumbs': [{'name': '🏠 Хмарний диск', 'path': ''}],
            'current_path': '',
        }

        resp = self.client.get(reverse('teacher_cloud_browse') + '?drive=nextcloud')
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data['success'])
        self.assertEqual(len(data['items']), 2)
        self.assertTrue(data['items'][0]['is_dir'])
        self.assertFalse(data['items'][1]['is_dir'])

    @patch('feed.cloud_storage.NextcloudClient.download_file')
    def test_cloud_download_nextcloud(self, mock_dl):
        """Завантаження файлу з Nextcloud через проксі."""
        storage = self.teacher.get_cloud_storage()
        storage.nextcloud_enabled = True
        storage.nextcloud_url = 'http://localhost:8080'
        storage.nextcloud_username = 'teacher'
        storage.nextcloud_password = 'pass'
        storage.save()

        mock_dl.return_value = (b'%PDF-1.4 test content', 'application/pdf', 'Урок 1.pdf')

        resp = self.client.get(reverse('teacher_cloud_download') + '?drive=nextcloud&path=Урок 1.pdf')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b'%PDF-1.4 test content')
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        import urllib.parse
        self.assertIn('Урок 1.pdf', urllib.parse.unquote(resp['Content-Disposition']))

    def test_assignment_create_page_contains_cloud_button_when_connected(self):
        """Кнопка 'Додати з хмарного диска' з'являється у формі завдання, коли підключено сховище."""
        # 1. Поки не підключено:
        resp = self.client.get(reverse('assignment_create'))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.context['has_cloud_storage'])
        self.assertNotIn('btn-cloud-picker-open', resp.content.decode('utf-8'))

        # 2. Підключаємо Nextcloud:
        storage = self.teacher.get_cloud_storage()
        storage.nextcloud_enabled = True
        storage.nextcloud_url = 'http://localhost:8080'
        storage.nextcloud_username = 'teacher'
        storage.nextcloud_password = 'pass'
        storage.save()

        # Тепер сторінка містить кнопку та модальне вікно
        resp2 = self.client.get(reverse('assignment_create'))
        self.assertEqual(resp2.status_code, 200)
        self.assertTrue(resp2.context['has_cloud_storage'])
        content = resp2.content.decode('utf-8')
        self.assertIn('btn-cloud-picker-open', content)
        self.assertIn('Додати з хмарного диска', content)
        self.assertIn('cloud-picker-modal', content)

    @patch('feed.cloud_storage.GoogleDriveClient.test_connection')
    def test_save_google_direct_login_and_disconnect(self, mock_test):
        """Збереження облікових даних Google за логіном та паролем, а потім від'єднання."""
        mock_test.return_value = (True, "OK")
        resp = self.client.post(reverse('teacher_cloud_save_google'), {
            'cloud_action': 'save',
            'email': 'teacher@school.org',
            'password': 'secretpassword',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(resp.status_code, 200)
        storage = self.teacher.get_cloud_storage()
        self.assertTrue(storage.has_google_drive())
        self.assertEqual(storage.google_user_email, 'teacher@school.org')
        self.assertEqual(storage.google_password, 'secretpassword')

        # Від'єднання
        resp2 = self.client.post(reverse('teacher_cloud_save_google'), {
            'cloud_action': 'disconnect'
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(resp2.status_code, 200)
        storage.refresh_from_db()
        self.assertFalse(storage.has_google_drive())

    def test_cloud_google_auth_start_without_client_id_redirects_safely(self):
        """Google OAuth start без Client ID не видає помилку, а безпечно перенаправляє на налаштування."""
        resp = self.client.get(reverse('teacher_cloud_google_auth_start'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('settings', resp.url)

    @patch('urllib.request.urlopen')
    def test_nextcloud_list_directory_multi_propstat_detects_folders(self, mock_urlopen):
        """Перевірка правильного розпізнавання папок і файлів навіть за кількох блоків propstat від Nextcloud."""
        from feed.cloud_storage import NextcloudClient
        from unittest.mock import MagicMock

        xml_response = b'''<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/teacher/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/teacher/%d1%81%d1%82%d0%b0%d0%bd%d0%b4%d0%b0%d1%80%d1%82%d0%bd%d1%96%20%d1%84%d0%b0%d0%b9%d0%bb%d0%b8/</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>\xd1\x81\xd1\x82\xd0\xb0\xd0\xbd\xd0\xb4\xd0\xb0\xd1\x80\xd1\x82\xd0\xbd\xd1\x96 \xd1\x84\xd0\xb0\xd0\xb9\xd0\xbb\xd0\xb8</d:displayname>
        <d:resourcetype><d:collection/></d:resourcetype>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
    <d:propstat>
      <d:prop><d:getcontentlength/></d:prop>
      <d:status>HTTP/1.1 404 Not Found</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/teacher/%d0%91%d0%b5%d0%b7%20%d0%bd%d0%b0%d0%b7%d0%b2%d0%b8.ods</d:href>
    <d:propstat>
      <d:prop>
        <d:displayname>\xd0\x91\xd0\xb5\xd0\xb7 \xd0\xbd\xd0\xb0\xd0\xb7\xd0\xb2\xd0\xb8.ods</d:displayname>
        <d:getcontentlength>667</d:getcontentlength>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>'''
        mock_resp = MagicMock()
        mock_resp.read.return_value = xml_response
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        mock_urlopen.return_value = mock_resp

        client = NextcloudClient(
            base_url='http://localhost:8090',
            username='teacher',
            password='secretpassword'
        )
        res = client.list_directory('')
        self.assertEqual(len(res['items']), 2)

        folder_item = res['items'][0]
        self.assertEqual(folder_item['name'], 'стандартні файли')
        self.assertTrue(folder_item['is_dir'])
        self.assertEqual(folder_item['icon'], '📁')

        file_item = res['items'][1]
        self.assertEqual(file_item['name'], 'Без назви.ods')
        self.assertFalse(file_item['is_dir'])
        self.assertEqual(file_item['size'], 667)

    def test_google_drive_linked_folder_is_directory(self):
        """Перевірка того, що спільна/прив'язана папка Google Drive відображається як папка (is_dir: True), а не файл."""
        client = GoogleDriveClient(
            email='teacher@gmail.com',
            folder_link='https://drive.google.com/drive/folders/1I1RtmeKwgZ80GZnWkgSOCRTKTVFTzwKn'
        )
        # У корені має бути папка (не файл!)
        res = client.list_directory('root')
        self.assertIn('items', res)
        self.assertEqual(len(res['items']), 1)
        folder_item = res['items'][0]
        self.assertTrue(folder_item['is_dir'], "Прив'язана папка Google Drive повинна мати is_dir=True")
        self.assertEqual(folder_item['id'], '1I1RtmeKwgZ80GZnWkgSOCRTKTVFTzwKn')
        self.assertEqual(folder_item['icon'], '📁')

        # При заході в цю папку мають повертатися хлібні крихти та навігаційні дані
        sub_res = client.list_directory('1I1RtmeKwgZ80GZnWkgSOCRTKTVFTzwKn')
        self.assertEqual(sub_res['current_folder_id'], '1I1RtmeKwgZ80GZnWkgSOCRTKTVFTzwKn')
        self.assertEqual(len(sub_res['breadcrumbs']), 2)
        self.assertEqual(sub_res['breadcrumbs'][0]['name'], '🔺 Google Drive')
        self.assertEqual(sub_res['breadcrumbs'][0]['id'], 'root')
        self.assertIn('📁', sub_res['breadcrumbs'][1]['name'])
        self.assertIn('drive.google.com', sub_res['folder_link'])

    @patch('urllib.request.urlopen')
    def test_google_drive_api_key_test_connection(self, mock_urlopen):
        """Перевірка тестування підключення за допомогою Google Drive API Key."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        mock_urlopen.return_value = mock_resp

        client = GoogleDriveClient(api_key='AIzaSyFakeKeyTest12345')
        ok, msg = client.test_connection()
        self.assertTrue(ok)
        self.assertIn('Google Drive API Key дійсний', msg)

    def test_drive_html_parser_and_embedded_view(self):
        """Перевірка парсингу файлів, Google Docs та підпапок із HTML embeddedfolderview."""
        from feed.cloud_storage import DriveHTMLParser

        sample_html = '''<html><head><title>Матеріали до уроку - Google Drive</title></head>
        <body>
        <a href="https://drive.google.com/file/d/1A2B3C4D5E6F7G8H9I0J1K2L3M/view">Підручник.pdf</a>
        <a href="https://docs.google.com/document/d/1X2Y3Z4A5B6C7D8E9F0G1H2I3J/edit">Конспект_уроку</a>
        <a href="https://docs.google.com/spreadsheets/d/2A3B4C5D6E7F8G9H0I1J2K3L4M/edit">Оцінки_і_бали</a>
        <a href="https://drive.google.com/drive/folders/3A4B5C6D7E8F9G0H1I2J3K4L5M">Додаткові вправи</a>
        </body></html>'''

        parser = DriveHTMLParser('root')
        parser.feed(sample_html)
        self.assertEqual(len(parser.items), 4)

        items_by_name = {it['name']: it for it in parser.items}
        self.assertIn('Підручник.pdf', items_by_name)
        self.assertFalse(items_by_name['Підручник.pdf']['is_dir'])

        self.assertIn('Конспект_уроку.docx', items_by_name)
        self.assertFalse(items_by_name['Конспект_уроку.docx']['is_dir'])

        self.assertIn('Додаткові вправи', items_by_name)
        self.assertTrue(items_by_name['Додаткові вправи']['is_dir'])


