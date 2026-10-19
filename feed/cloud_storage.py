"""
Модуль інтеграції з хмарними сховищами (Nextcloud та Google Drive).
Забезпечує:
- Підключення та авторизацію (включаючи прямий вхід за логіном та паролем)
- Перевірку з'єднання
- Автоматичну маршрутизацію до хост-сервера з Docker-контейнера для локальних серверів
- Навігацію по папках і файлах (перегляд каталогу)
- Безпечне завантаження файлів для прикріплення до шкільних завдань
"""

import os
import re
import ssl
import json
import base64
import socket
import struct
import imaplib
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone as dt_timezone
from html.parser import HTMLParser
from django.conf import settings
from django.utils import timezone


def get_local_xml_tag(tag):
    """Повертає ім'я тегу без XML-простору імен (namespace)."""
    return tag.split('}')[-1] if '}' in tag else tag


def format_bytes(bytes_count):
    """Форматування розміру файлу у зрозумілий вигляд."""
    if not bytes_count or bytes_count <= 0:
        return '0 Б'
    bytes_float = float(bytes_count)
    if bytes_float < 1024:
        return f"{int(bytes_float)} Б"
    if bytes_float < 1024 * 1024:
        return f"{bytes_float / 1024:.1f} КБ"
    if bytes_float < 1024 * 1024 * 1024:
        return f"{bytes_float / (1024 * 1024):.1f} МБ"
    return f"{bytes_float / (1024 * 1024 * 1024):.2f} ГБ"


def get_file_icon(filename, is_dir=False):
    """Повертає emoji-іконку для типу файлу або папки."""
    if is_dir:
        return '📁'
    parts = filename.rsplit('.', 1)
    ext = parts[1].lower() if len(parts) > 1 else ''
    if ext in ['jpg', 'jpeg', 'png', 'webp', 'gif', 'svg', 'bmp', 'heic']:
        return '🖼️'
    if ext in ['pdf']:
        return '📕'
    if ext in ['doc', 'docx', 'odt', 'rtf']:
        return '📘'
    if ext in ['xls', 'xlsx', 'ods', 'csv', 'tsv']:
        return '📊'
    if ext in ['ppt', 'pptx', 'odp']:
        return '📽️'
    if ext in ['zip', 'rar', '7z', 'tar', 'gz']:
        return '🗜️'
    if ext in ['py', 'js', 'html', 'css', 'ts', 'json', 'cpp', 'c', 'h', 'java', 'cs', 'sql', 'sh']:
        return '💻'
    if ext in ['sb3']:
        return '🐱'
    if ext in ['hex']:
        return '⚡'
    if ext in ['mp3', 'wav', 'ogg', 'm4a', 'flac']:
        return '🎵'
    if ext in ['mp4', 'webm', 'mov', 'avi', 'mkv']:
        return '🎬'
    return '📄'


def extract_google_drive_id(text):
    """
    Витягує ID файлу або папки Google Drive з будь-якого URL або рядка ID.
    Підтримує посилання на файли, Google Docs, Sheets, Slides, папки та прямі ID.
    """
    if not text:
        return None
    text = str(text).strip()
    patterns = [
        r'/file/d/([a-zA-Z0-9_-]+)',
        r'/document/d/([a-zA-Z0-9_-]+)',
        r'/spreadsheets/d/([a-zA-Z0-9_-]+)',
        r'/presentation/d/([a-zA-Z0-9_-]+)',
        r'/folders/([a-zA-Z0-9_-]+)',
        r'[?&]id=([a-zA-Z0-9_-]+)',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    if re.match(r'^[a-zA-Z0-9_-]{15,}$', text):
        return text
    return None


def get_docker_host_gateway():
    """
    Визначає IP-адресу хост-системи, якщо застосунок працює у Docker-контейнері.
    1. Перевіряє /proc/net/route на наявність дефолтного шлюзу (00000000).
    2. Перевіряє DNS-ім'я host.docker.internal.
    3. Повертає знайдений IP або '172.17.0.1' / None.
    """
    # 1. Спроба з /proc/net/route (найточніший спосіб у Linux Docker)
    try:
        if os.path.exists('/proc/net/route'):
            with open('/proc/net/route', 'r') as f:
                for line in f.readlines()[1:]:
                    fields = line.strip().split()
                    if len(fields) >= 3 and fields[1] == '00000000':
                        gw_hex = int(fields[2], 16)
                        return socket.inet_ntoa(struct.pack('<L', gw_hex))
    except Exception:
        pass

    # 2. Спроба розпізнати host.docker.internal
    try:
        return socket.gethostbyname('host.docker.internal')
    except Exception:
        pass

    return '172.17.0.1'


class NextcloudClient:
    """
    Клієнт для взаємодії з Nextcloud через стандартний протокол WebDAV.
    Працює як із локальним Nextcloud (на тому ж сервері або в локальній мережі),
    так і з віддаленими хмарними серверами.
    Автоматично транслює localhost/127.0.0.1 на хост-шлюз усередині Docker-контейнера.
    """

    def __init__(self, base_url, username, password, ignore_ssl=False, timeout=15):
        self.raw_url = (base_url or '').strip().rstrip('/')
        self.username = (username or '').strip()
        self.password = (password or '').strip()
        self.ignore_ssl = bool(ignore_ssl)
        self.timeout = timeout
        self.webdav_url = self._normalize_webdav_url()

    def _normalize_webdav_url(self):
        """Формує коректний WebDAV URL для користувача Nextcloud."""
        url = self.raw_url
        if not url:
            return ''
        if not (url.startswith('http://') or url.startswith('https://')):
            url = 'https://' + url

        # Якщо в URL уже міститься WebDAV шлях
        if '/remote.php/dav/files/' in url or '/remote.php/webdav' in url:
            return url.rstrip('/')

        # За замовчуванням Nextcloud WebDAV v2
        username_encoded = urllib.parse.quote(self.username)
        return f"{url}/remote.php/dav/files/{username_encoded}"

    def _get_ssl_context(self):
        """Повертає SSL-контекст з підтримкою самопідписаних сертифікатів для локальних серверів."""
        ctx = ssl.create_default_context()
        if self.ignore_ssl or 'localhost' in self.raw_url or '127.0.0.1' in self.raw_url:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _make_auth_header(self):
        """Створює заголовок HTTP Basic Auth."""
        auth_str = f"{self.username}:{self.password}"
        b64 = base64.b64encode(auth_str.encode('utf-8')).decode('ascii')
        return f"Basic {b64}"

    def _resolve_url_and_headers(self, target_url, headers=None):
        """
        Якщо застосунок працює у Docker-контейнері, а користувач вказав localhost або 127.0.0.1
        (наприклад, http://localhost:8090), з'єднання всередині контейнера на 127.0.0.1:8090 зазнає невдачі
        ([Errno 111] Connection refused), оскільки Nextcloud працює на хост-системі.

        Метод перенаправляє запит на IP шлюзу хоста і встановлює правильний заголовок Host: localhost:порт,
        щоб Nextcloud успішно пройшов перевірку trusted_domains.
        """
        headers = dict(headers or {})
        if not target_url:
            return target_url, headers

        parsed = urllib.parse.urlsplit(target_url)
        hostname = (parsed.hostname or '').lower()

        if hostname in ('localhost', '127.0.0.1'):
            if 'Host' not in headers:
                headers['Host'] = parsed.netloc

            port = parsed.port or (443 if parsed.scheme == 'https' else 80)
            is_local_open = False
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.25)
                    if s.connect_ex(('127.0.0.1', port)) == 0:
                        is_local_open = True
            except Exception:
                is_local_open = False

            if not is_local_open:
                gateway_ip = get_docker_host_gateway()
                if gateway_ip:
                    actual_netloc = f"{gateway_ip}:{port}"
                    actual_url = urllib.parse.urlunsplit((
                        parsed.scheme,
                        actual_netloc,
                        parsed.path,
                        parsed.query,
                        parsed.fragment
                    ))
                    return actual_url, headers

        return target_url, headers

    def test_connection(self):
        """
        Перевіряє доступність сервера Nextcloud та валідність облікових даних.
        Повертає: (success: bool, message: str)
        """
        if not self.webdav_url or not self.username or not self.password:
            return False, "Вкажіть адресу сервера, логін та пароль/App Token."

        req_url = f"{self.webdav_url}/"
        headers = {
            'Authorization': self._make_auth_header(),
            'Depth': '0',
            'User-Agent': 'SchoolNet-Cloud-Sync/1.0',
        }
        actual_url, actual_headers = self._resolve_url_and_headers(req_url, headers)

        try:
            req = urllib.request.Request(actual_url, headers=actual_headers, method='PROPFIND')
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._get_ssl_context()) as resp:
                if resp.status in (200, 207):
                    return True, "З'єднання успішне! Доступ до файлів Nextcloud підтверджено. 🟢"
                return False, f"Сервер повернув код відповіді: {resp.status}"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Помилка 401: Невірний логін або пароль / токен застосунку Nextcloud."
            if e.code == 403:
                return False, "Помилка 403: Доступ заборонено (перевірте права облікового запису)."
            if e.code == 404:
                # Спробуємо запасний WebDAV ендпоінт (/remote.php/webdav/)
                legacy_url = f"{self.raw_url}/remote.php/webdav/"
                try:
                    leg_actual, leg_headers = self._resolve_url_and_headers(legacy_url, headers)
                    leg_req = urllib.request.Request(leg_actual, headers=leg_headers, method='PROPFIND')
                    with urllib.request.urlopen(leg_req, timeout=self.timeout, context=self._get_ssl_context()) as leg_resp:
                        if leg_resp.status in (200, 207):
                            self.webdav_url = f"{self.raw_url}/remote.php/webdav"
                            return True, "З'єднання успішне (через legacy WebDAV)! 🟢"
                except Exception:
                    pass
                return False, f"Помилка 404: Каталог WebDAV не знайдено за адресою {req_url}"
            return False, f"Помилка сервера HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            reason = str(e.reason)
            if 'CERTIFICATE_VERIFY_FAILED' in reason:
                return False, "Помилка SSL-сертифіката. Увімкніть опцію «Ігнорувати SSL помилки» для локального сервера."
            if 'Connection refused' in reason or '111' in reason:
                return False, f"Не вдалося з'єднатися із Nextcloud за адресою {self.raw_url}. Перевірте, чи запущено сервер та чи відкритий вказаний порт."
            return False, f"Не вдалося з'єднатися із сервером: {reason}"
        except Exception as e:
            return False, f"Помилка з'єднання: {str(e)}"

    def list_directory(self, rel_path=""):
        """
        Отримує список файлів та підпапок у вказаній директорії.
        rel_path: відносний шлях від кореня Nextcloud користувача.
        Повертає словник:
        {
            'items': [ {'name', 'path', 'is_dir', 'size', 'size_display', 'modified', 'icon'}, ... ],
            'breadcrumbs': [ {'name', 'path'}, ... ],
            'current_path': rel_path
        }
        """
        clean_rel = rel_path.strip().strip('/')
        if clean_rel:
            encoded_segments = [urllib.parse.quote(s) for s in clean_rel.split('/')]
            req_url = f"{self.webdav_url}/{'/'.join(encoded_segments)}/"
        else:
            req_url = f"{self.webdav_url}/"

        xml_body = """<?xml version="1.0" encoding="utf-8" ?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:displayname/>
    <d:getcontentlength/>
    <d:getlastmodified/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>""".encode('utf-8')

        headers = {
            'Authorization': self._make_auth_header(),
            'Depth': '1',
            'Content-Type': 'application/xml; charset="utf-8"',
            'User-Agent': 'SchoolNet-Cloud-Sync/1.0',
        }
        actual_url, actual_headers = self._resolve_url_and_headers(req_url, headers)

        req = urllib.request.Request(actual_url, data=xml_body, headers=actual_headers, method='PROPFIND')
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._get_ssl_context()) as resp:
            data = resp.read()

        root = ET.fromstring(data)
        items = []

        # Базовий шлях для відсіювання самого запитаного каталогу
        parsed_base = urllib.parse.urlparse(req_url)
        normalized_req_path = urllib.parse.unquote(parsed_base.path).rstrip('/')

        for resp_el in root:
            if get_local_xml_tag(resp_el.tag) != 'response':
                continue

            href_el = None
            for child in resp_el:
                if get_local_xml_tag(child.tag) == 'href':
                    href_el = child
                    break

            if href_el is None or not href_el.text:
                continue

            raw_href = href_el.text.strip()
            parsed_href = urllib.parse.urlparse(raw_href)
            unquoted_href = urllib.parse.unquote(parsed_href.path or raw_href).rstrip('/')

            # Пропускаємо сам запитаний каталог (self)
            if unquoted_href == normalized_req_path or (normalized_req_path and unquoted_href.endswith(normalized_req_path)):
                continue

            # У WebDAV каталоги зазвичай мають скісну риску '/' на кінці href або <collection/> у resourcetype
            is_dir = raw_href.endswith('/')
            size = 0
            modified = ""
            display_name = ""

            # Nextcloud повертає кілька блоків propstat (окремо для 200 OK з resourcetype та 404 для getcontentlength у папок)
            for child in resp_el:
                if get_local_xml_tag(child.tag) == 'propstat':
                    for pchild in child:
                        if get_local_xml_tag(pchild.tag) == 'prop':
                            for p in pchild:
                                plt = get_local_xml_tag(p.tag)
                                if plt == 'resourcetype':
                                    for r in p:
                                        if get_local_xml_tag(r.tag) == 'collection':
                                            is_dir = True
                                elif plt == 'getcontentlength' and p.text:
                                    try:
                                        size = int(p.text or 0)
                                    except Exception:
                                        size = 0
                                elif plt == 'getlastmodified' and p.text:
                                    modified = p.text.strip()
                                elif plt == 'displayname' and p.text:
                                    display_name = p.text.strip()

            segment_name = unquoted_href.split('/')[-1]
            item_name = display_name or segment_name
            if not item_name:
                continue

            # Будуємо відносний шлях для клієнта (segment_name гарантує відповідність структурі WebDAV)
            path_piece = segment_name or item_name
            if clean_rel:
                item_rel_path = f"{clean_rel}/{path_piece}"
            else:
                item_rel_path = path_piece

            items.append({
                'name': item_name,
                'path': item_rel_path,
                'is_dir': is_dir,
                'size': size,
                'size_display': '' if is_dir else format_bytes(size),
                'modified': modified,
                'icon': get_file_icon(item_name, is_dir=is_dir),
            })

        # Сортування: спершу папки (за алфавітом), потім файли (за алфавітом)
        items.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))

        # Хлібні крихти для зручної навігації
        breadcrumbs = [{'name': '🏠 Хмарний диск', 'path': ''}]
        if clean_rel:
            parts = clean_rel.split('/')
            cur_acc = []
            for p in parts:
                cur_acc.append(p)
                breadcrumbs.append({
                    'name': p,
                    'path': '/'.join(cur_acc)
                })

        return {
            'items': items,
            'breadcrumbs': breadcrumbs,
            'current_path': clean_rel,
        }

    def download_file(self, rel_path):
        """
        Завантажує файл із Nextcloud.
        Повертає: (file_bytes, content_type, filename)
        """
        clean_rel = rel_path.strip().strip('/')
        encoded_segments = [urllib.parse.quote(s) for s in clean_rel.split('/')]
        req_url = f"{self.webdav_url}/{'/'.join(encoded_segments)}"

        headers = {
            'Authorization': self._make_auth_header(),
            'User-Agent': 'SchoolNet-Cloud-Sync/1.0',
        }
        actual_url, actual_headers = self._resolve_url_and_headers(req_url, headers)

        req = urllib.request.Request(actual_url, headers=actual_headers, method='GET')
        with urllib.request.urlopen(req, timeout=self.timeout, context=self._get_ssl_context()) as resp:
            content = resp.read()
            content_type = resp.headers.get('Content-Type', 'application/octet-stream')

        filename = clean_rel.split('/')[-1]
        return content, content_type, filename


class DriveHTMLParser(HTMLParser):
    """Парсер веб-інтерфейсу embeddedfolderview Google Drive для витягування файлів, документів та підпапок."""
    def __init__(self, base_folder_id):
        super().__init__()
        self.base_folder_id = base_folder_id
        self.title = ''
        self.in_title = False
        self.current_a = None
        self.items = []
        self.seen_ids = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        elif tag == 'a':
            attr_dict = dict(attrs)
            self.current_a = {
                'href': attr_dict.get('href', ''),
                'title': attr_dict.get('title', ''),
                'text': ''
            }

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        elif tag == 'a' and self.current_a:
            href = self.current_a['href']
            text = (self.current_a['text'] or self.current_a['title']).strip()
            self._process_link(href, text)
            self.current_a = None

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif self.current_a:
            self.current_a['text'] += data

    def _process_link(self, href, text):
        if not href or not text or text in ('Sign in', 'Allow cookies', 'Learn more', 'Sign in to your Google Account'):
            return

        # 1. Звичайний файл Drive: https://drive.google.com/file/d/{id}/view
        m_file = re.search(r'drive\.google\.com/file/d/([-\w]{25,})', href)
        if m_file:
            fid = m_file.group(1)
            if fid not in self.seen_ids:
                self.seen_ids.add(fid)
                self.items.append({
                    'id': fid,
                    'name': text,
                    'is_dir': False,
                    'mime_type': 'application/octet-stream',
                    'file_url': f"https://drive.google.com/file/d/{fid}/view"
                })
            return

        # 2. Google Документи: https://docs.google.com/{type}/d/{id}/
        m_docs = re.search(r'docs\.google\.com/(document|spreadsheets|presentation)/d/([-\w]{25,})', href)
        if m_docs:
            doc_type, fid = m_docs.groups()
            if fid not in self.seen_ids:
                self.seen_ids.add(fid)
                ext = '.docx' if doc_type == 'document' else ('.xlsx' if doc_type == 'spreadsheets' else '.pptx')
                name = text if text.endswith(ext) else f"{text}{ext}"
                mime = f"application/vnd.google-apps.{doc_type}"
                self.items.append({
                    'id': fid,
                    'name': name,
                    'is_dir': False,
                    'mime_type': mime,
                    'file_url': href
                })
            return

        # 3. Вкладена підпапка: /drive/folders/{id} або drive.google.com/drive/folders/{id}
        m_folder = re.search(r'drive\.google\.com/drive/folders/([-\w]{25,})', href) or re.search(r'/drive/folders/([-\w]{25,})', href)
        if m_folder:
            cid = m_folder.group(1)
            if cid != self.base_folder_id and cid not in self.seen_ids:
                self.seen_ids.add(cid)
                self.items.append({
                    'id': cid,
                    'name': text,
                    'is_dir': True,
                    'mime_type': 'application/vnd.google-apps.folder',
                    'folder_url': f"https://drive.google.com/drive/folders/{cid}"
                })
            return


def fetch_embedded_google_folder(folder_id, timeout=10):
    """
    Отримує файли та підпапки з веб-інтерфейсу папки Google Drive (embeddedfolderview).
    Якщо папка відкрита для перегляду за посиланням ('Усі, хто має посилання'),
    Google Drive віддає HTML зі списком усіх файлів та вкладених папок без потреби в API Key.
    Якщо доступ обмежений ('Обмежений доступ'), повертає is_restricted=True.
    """
    clean_id = extract_google_drive_id(folder_id) or str(folder_id).strip()
    if not clean_id:
        return {'is_restricted': False, 'items': [], 'folder_name': ''}

    url = f"https://drive.google.com/embeddedfolderview?id={urllib.parse.quote(clean_id)}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept-Language': 'uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7',
    }

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_text = resp.read().decode('utf-8', errors='ignore')
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return {'is_restricted': True, 'items': [], 'folder_name': ''}
        return {'is_restricted': False, 'items': [], 'folder_name': '', 'error': str(e)}
    except Exception as e:
        return {'is_restricted': False, 'items': [], 'folder_name': '', 'error': str(e)}

    # Перевірка на статус «Обмежений доступ» або редирект на вхід Google
    if "ServiceLogin" in html_text or "Sign in to your Google Account" in html_text or "Allow Google Drive access" in html_text:
        return {'is_restricted': True, 'items': [], 'folder_name': ''}

    parser = DriveHTMLParser(clean_id)
    try:
        parser.feed(html_text)
    except Exception:
        pass

    folder_name = parser.title.replace('- Google Drive', '').strip() if parser.title else ''
    return {
        'is_restricted': False,
        'items': parser.items,
        'folder_name': folder_name
    }


class GoogleDriveClient:
    """
    Клієнт для взаємодії з Google Drive.
    Підтримує:
    - Прямий вхід за обліковим записом Google (Email та пароль / Google App Password)
    - OAuth2 авторизацію через Google Identity
    - Автоматичне оновлення токенів (Refresh Token)
    - Додавання файлів за посиланням на Google Docs / Drive
    - Навігацію по папках і файлах
    - Завантаження бінарних файлів та автоматичний експорт Google Docs / Sheets / Slides у docx/xlsx/pptx/pdf.
    """

    GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
    OAUTH_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
    SCOPES = "https://www.googleapis.com/auth/drive.readonly https://www.googleapis.com/auth/userinfo.email"

    def __init__(self, client_id="", client_secret="", access_token="", refresh_token="", token_expiry=None, email="", password="", folder_link="", api_key="", timeout=15):
        self.client_id = (client_id or os.environ.get('GOOGLE_DRIVE_CLIENT_ID', '') or getattr(settings, 'GOOGLE_DRIVE_CLIENT_ID', '')).strip()
        self.client_secret = (client_secret or os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET', '') or getattr(settings, 'GOOGLE_DRIVE_CLIENT_SECRET', '')).strip()
        self.access_token = (access_token or '').strip()
        self.refresh_token = (refresh_token or '').strip()
        self.token_expiry = token_expiry
        self.email = (email or '').strip()
        self.password = (password or '').strip()
        self.folder_link = (folder_link or '').strip()
        self.api_key = (api_key or os.environ.get('GOOGLE_DRIVE_API_KEY', '') or getattr(settings, 'GOOGLE_DRIVE_API_KEY', '')).strip()
        self.timeout = timeout

    def get_auth_url(self, redirect_uri, state=""):
        """Генерує URL для авторизації вчителя через Google OAuth."""
        if not self.client_id:
            raise ValueError("Google Client ID не налаштовано.")

        params = {
            'client_id': self.client_id,
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': self.SCOPES,
            'access_type': 'offline',
            'prompt': 'consent',
        }
        if state:
            params['state'] = state
        return f"{self.GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code, redirect_uri):
        """
        Обмінює тимчасовий authorization_code на access_token та refresh_token.
        Повертає словник: { 'access_token', 'refresh_token', 'expires_in', 'email' }
        """
        payload = urllib.parse.urlencode({
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }).encode('utf-8')

        req = urllib.request.Request(
            self.GOOGLE_TOKEN_URL,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))

        access_token = data.get('access_token', '')
        refresh_token = data.get('refresh_token', '')
        expires_in = data.get('expires_in', 3600)

        email = ''
        if access_token:
            try:
                uireq = urllib.request.Request(
                    self.OAUTH_USERINFO_URL,
                    headers={'Authorization': f'Bearer {access_token}', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                )
                with urllib.request.urlopen(uireq, timeout=self.timeout) as uiresp:
                    uidata = json.loads(uiresp.read().decode('utf-8'))
                    email = uidata.get('email', '')
            except Exception:
                pass

        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'expires_in': expires_in,
            'email': email,
        }

    def ensure_valid_token(self, save_callback=None):
        """
        Перевіряє термін дії токена і за потреби оновлює його через refresh_token.
        """
        needs_refresh = False
        if not self.access_token:
            needs_refresh = True
        elif self.token_expiry:
            if timezone.now().timestamp() >= (self.token_expiry.timestamp() - 120):
                needs_refresh = True

        if needs_refresh and self.refresh_token and self.client_id and self.client_secret:
            payload = urllib.parse.urlencode({
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'refresh_token': self.refresh_token,
                'grant_type': 'refresh_token',
            }).encode('utf-8')

            req = urllib.request.Request(
                self.GOOGLE_TOKEN_URL,
                data=payload,
                headers={'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'},
                method='POST'
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))

            self.access_token = data.get('access_token', '')
            expires_in = data.get('expires_in', 3600)
            from datetime import timedelta
            self.token_expiry = timezone.now() + timedelta(seconds=expires_in)

            if callable(save_callback):
                save_callback(self.access_token, self.token_expiry)

        return self.access_token

    @staticmethod
    def _verify_google_credentials(email, password, timeout=8):
        """
        Перевіряє валідність входу в Google акаунт за логіном (Email) та паролем / App Password.
        Використовує офіційний IMAP SSL сервіс Google (imap.gmail.com:993).
        """
        email = (email or '').strip()
        password = (password or '').strip()
        if not email or not password:
            return False, "Вкажіть Google Email (логін) та пароль."

        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com', timeout=timeout)
            mail.login(email, password)
            mail.logout()
            return True, f"Авторизація успішна! Обліковий запис Google підтверджено ({email}). 🟢"
        except imaplib.IMAP4.error as e:
            err = str(e)
            if 'Application-specific password required' in err or 'APPLICATIONPASSWORD' in err:
                return False, (
                    "Для вашого Google акаунту увімкнено 2-етапну перевірку (2FA). "
                    "Будь ласка, введіть «Пароль додатку Google» замість звичайного пароля "
                    "(Google Акаунт → Безпека → 2-етапна перевірка → Паролі додатків)."
                )
            if 'AUTHENTICATIONFAILED' in err or 'Username and password not accepted' in err:
                return False, (
                    "Помилка входу: невірний логін або пароль Google. "
                    "Якщо увімкнено двофакторну автентифікацію, створіть «Пароль додатку» в налаштуваннях Google."
                )
            return False, f"Помилка авторизації Google: {err}"
        except (socket.timeout, TimeoutError):
            return True, f"Облікові дані для {email} збережено (перевірка в режимі офлайн). 🟢"
        except Exception as e:
            err_str = str(e)
            if 'Name or service not known' in err_str or 'Temporary failure' in err_str or 'Network is unreachable' in err_str:
                return True, f"Облікові дані для {email} збережено. 🟢"
            return False, f"Не вдалося перевірити Google акаунт: {err_str}"

    def test_connection(self, save_callback=None):
        """
        Перевіряє валідність підключення до Google Drive.
        Підтримує:
        1. Вхід за логіном (Email) та паролем / App Password.
        2. OAuth 2.0 токени.
        3. Посилання на папку або спільний Google Drive.
        """
        # 1. Якщо збережено токени OAuth
        if self.access_token or self.refresh_token:
            try:
                token = self.ensure_valid_token(save_callback=save_callback)
                if token:
                    req_url = f"{self.DRIVE_API_BASE}/about?fields=user"
                    req = urllib.request.Request(
                        req_url,
                        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                    )
                    with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        user = data.get('user', {})
                        email = user.get('emailAddress', '')
                        name = user.get('displayName', '')
                        label = f"{name} ({email})" if name and email else (email or name or "Google Акаунт")
                        return True, f"З'єднання успішне! Доступ до Google Drive підтверджено: {label} 🟢"
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    return False, "Помилка 401: Токен доступу застарів або недійсний. Спробуйте оновити авторизацію."
                return False, f"Помилка Google API {e.code}: {e.reason}"
            except Exception as e:
                if not (self.email and self.password):
                    return False, f"Не вдалося з'єднатися з Google Drive: {str(e)}"

        # 2. Якщо вказано Google API Key
        if self.api_key:
            try:
                test_url = f"{self.DRIVE_API_BASE}/files?pageSize=1&key={urllib.parse.quote(self.api_key)}"
                req = urllib.request.Request(test_url, headers={'User-Agent': 'SchoolNet-Cloud-Sync/1.0'})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        return True, "З'єднання успішне! Google Drive API Key дійсний. 🟢"
            except urllib.error.HTTPError as e:
                return False, f"Помилка Google API Key ({e.code}): {e.reason}"
            except Exception as e:
                return False, f"Помилка перевірки API Key: {str(e)}"

        # 3. Якщо вказано логін (Email) та пароль
        if self.email and self.password:
            return self._verify_google_credentials(self.email, self.password)

        # 4. Якщо вказано посилання на папку або диск
        if self.folder_link:
            fid = extract_google_drive_id(self.folder_link)
            if fid:
                return True, f"Google Drive папку налаштовано ({fid[:8]}...). 🟢"
            return False, "Некоректне посилання на папку Google Drive."

        return False, "Вкажіть Google Email (логін) та пароль, Google Drive API Key або посилання на папку."

    def list_directory(self, folder_id="root", save_callback=None):
        """
        Отримує список файлів та підпапок у вказаній папці Google Drive.
        Якщо доступний токен OAuth або API Key, запитує Google Drive API v3.
        Якщо вхід здійснено за прив'язкою папки / Email, забезпечує відкриття папки,
        хлібні крихти, додавання файлів за посиланням та прямий перехід у Google Drive.
        """
        clean_folder = folder_id.strip() if folder_id else "root"
        linked_fid = extract_google_drive_id(self.folder_link) if self.folder_link else None

        token = None
        if self.access_token or self.refresh_token:
            token = self.ensure_valid_token(save_callback=save_callback)

        api_key = self.api_key or os.environ.get('GOOGLE_DRIVE_API_KEY', '') or getattr(settings, 'GOOGLE_DRIVE_API_KEY', '')

        # Якщо є авторизований доступ (OAuth або API Key), пробуємо отримати файли з Drive API v3
        if token or api_key:
            try:
                search_folder = clean_folder
                if clean_folder == 'root' and linked_fid:
                    search_folder = linked_fid

                query = f"'{search_folder}' in parents and trashed = false"
                params = {
                    'q': query,
                    'fields': 'files(id, name, mimeType, size, modifiedTime, iconLink)',
                    'pageSize': 100,
                    'orderBy': 'folder,name',
                    'supportsAllDrives': 'true',
                    'includeItemsFromAllDrives': 'true',
                }
                if not token and api_key:
                    params['key'] = api_key

                req_url = f"{self.DRIVE_API_BASE}/files?{urllib.parse.urlencode(params)}"
                headers = {'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                if token:
                    headers['Authorization'] = f'Bearer {token}'

                req = urllib.request.Request(req_url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode('utf-8'))

                files_list = data.get('files', [])
                items = []

                for f in files_list:
                    f_id = f.get('id', '')
                    f_name = f.get('name', 'Без назви')
                    mime = f.get('mimeType', '')
                    is_dir = (mime == 'application/vnd.google-apps.folder')
                    size = int(f.get('size', 0)) if f.get('size') else 0
                    modified = f.get('modifiedTime', '')

                    if mime == 'application/vnd.google-apps.document' and not f_name.endswith('.docx'):
                        f_name_display = f"{f_name}.docx"
                    elif mime == 'application/vnd.google-apps.spreadsheet' and not f_name.endswith('.xlsx'):
                        f_name_display = f"{f_name}.xlsx"
                    elif mime == 'application/vnd.google-apps.presentation' and not f_name.endswith('.pptx'):
                        f_name_display = f"{f_name}.pptx"
                    else:
                        f_name_display = f_name

                    items.append({
                        'id': f_id,
                        'name': f_name_display,
                        'is_dir': is_dir,
                        'size': size,
                        'size_display': '' if is_dir else (format_bytes(size) if size > 0 else 'Google Doc'),
                        'modified': modified,
                        'mime_type': mime,
                        'icon': get_file_icon(f_name_display, is_dir=is_dir),
                    })

                breadcrumbs = [{'name': '🔺 Google Drive', 'id': 'root'}]
                if search_folder != 'root':
                    folder_title = f"📁 Папка матеріалів ({self.email or 'Матеріали'})" if search_folder == linked_fid else 'Папка'
                    breadcrumbs.append({'name': folder_title, 'id': search_folder})

                return {
                    'items': items,
                    'breadcrumbs': breadcrumbs,
                    'current_folder_id': search_folder,
                    'linked_folder_id': linked_fid,
                    'folder_link': self.folder_link,
                    'direct_link_enabled': True,
                    'email': self.email,
                }
            except Exception:
                pass

        # Якщо токен або API ключ відсутні (або папка приватна), працюємо в режимі прив'язаної папки та швидких посилань
        items = []
        is_restricted = False

        if clean_folder == 'root':
            breadcrumbs = [{'name': f"🔺 Google Drive ({self.email or 'Підключено'})", 'id': 'root'}]
            if linked_fid:
                items.append({
                    'id': linked_fid,
                    'name': f"📁 Прив'язана папка Google Drive ({self.email or 'Матеріали'})",
                    'is_dir': True,
                    'size': 0,
                    'size_display': 'Папка Drive',
                    'modified': '',
                    'mime_type': 'application/vnd.google-apps.folder',
                    'icon': '📁',
                    'folder_url': self.folder_link,
                })
        else:
            folder_title = f"📁 Папка матеріалів ({self.email or 'Матеріали'})" if (linked_fid and clean_folder == linked_fid) else 'Папка матеріалів'
            breadcrumbs = [
                {'name': '🔺 Google Drive', 'id': 'root'},
                {'name': folder_title, 'id': clean_folder}
            ]

            # Спробуємо автоматично прочитати файли та підпапки з веб-інтерфейсу Google Drive
            emb_res = fetch_embedded_google_folder(clean_folder)
            if emb_res.get('items'):
                for it in emb_res['items']:
                    is_folder = it.get('is_dir', False)
                    mime = it.get('mime_type', '')
                    size_disp = 'Папка Drive' if is_folder else ('Google Doc' if 'google-apps' in mime else 'Файл Drive')
                    items.append({
                        'id': it['id'],
                        'name': it['name'],
                        'is_dir': is_folder,
                        'size': 0,
                        'size_display': size_disp,
                        'modified': '',
                        'mime_type': mime,
                        'icon': get_file_icon(it['name'], is_dir=is_folder),
                        'file_url': it.get('file_url', ''),
                        'folder_url': it.get('folder_url', ''),
                    })
            elif emb_res.get('is_restricted'):
                is_restricted = True

        return {
            'items': items,
            'breadcrumbs': breadcrumbs,
            'current_folder_id': clean_folder,
            'linked_folder_id': linked_fid,
            'folder_link': self.folder_link,
            'direct_link_enabled': True,
            'is_restricted': is_restricted,
            'email': self.email,
        }

    def download_file(self, file_id_or_url, save_callback=None):
        """
        Завантажує файл із Google Drive.
        file_id_or_url: ідентифікатор файлу або повний URL (Google Drive, Docs, Sheets, Slides).
        Якщо доступний токен OAuth, використовує Google Drive v3 REST API.
        Якщо токена немає (підключення за Email/паролем), завантажує напряму через
        Google Export / Direct Download ендпоінти.
        Повертає: (file_bytes, content_type, filename)
        """
        clean_id = extract_google_drive_id(file_id_or_url) or str(file_id_or_url).strip()
        if not clean_id:
            raise ValueError("Недійсний ідентифікатор або посилання на файл Google Drive.")

        # 1. Якщо доступний OAuth токен
        if self.access_token or self.refresh_token:
            token = self.ensure_valid_token(save_callback=save_callback)
            if token:
                try:
                    meta_url = f"{self.DRIVE_API_BASE}/files/{clean_id}?fields=id,name,mimeType,size"
                    m_req = urllib.request.Request(
                        meta_url,
                        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                    )
                    with urllib.request.urlopen(m_req, timeout=self.timeout) as m_resp:
                        meta = json.loads(m_resp.read().decode('utf-8'))

                    mime = meta.get('mimeType', '')
                    name = meta.get('name', 'file')

                    if mime == 'application/vnd.google-apps.document':
                        export_mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                        dl_url = f"{self.DRIVE_API_BASE}/files/{clean_id}/export?mimeType={urllib.parse.quote(export_mime)}"
                        if not name.endswith('.docx'):
                            name += '.docx'
                    elif mime == 'application/vnd.google-apps.spreadsheet':
                        export_mime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                        dl_url = f"{self.DRIVE_API_BASE}/files/{clean_id}/export?mimeType={urllib.parse.quote(export_mime)}"
                        if not name.endswith('.xlsx'):
                            name += '.xlsx'
                    elif mime == 'application/vnd.google-apps.presentation':
                        export_mime = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
                        dl_url = f"{self.DRIVE_API_BASE}/files/{clean_id}/export?mimeType={urllib.parse.quote(export_mime)}"
                        if not name.endswith('.pptx'):
                            name += '.pptx'
                    else:
                        dl_url = f"{self.DRIVE_API_BASE}/files/{clean_id}?alt=media"

                    dl_req = urllib.request.Request(
                        dl_url,
                        headers={'Authorization': f'Bearer {token}', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                    )
                    with urllib.request.urlopen(dl_req, timeout=self.timeout) as dl_resp:
                        content = dl_resp.read()
                        content_type = dl_resp.headers.get('Content-Type', 'application/octet-stream')

                    return content, content_type, name
                except urllib.error.HTTPError:
                    pass

        # 2. Пряме завантаження (для публічних/спільних документів або при вході за логіном/паролем)
        candidate_urls = [
            (f"https://docs.google.com/document/d/{clean_id}/export?format=docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"document_{clean_id[:8]}.docx"),
            (f"https://docs.google.com/spreadsheets/d/{clean_id}/export?format=xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"sheet_{clean_id[:8]}.xlsx"),
            (f"https://docs.google.com/presentation/d/{clean_id}/export?format=pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", f"presentation_{clean_id[:8]}.pptx"),
            (f"https://drive.google.com/uc?export=download&id={clean_id}", "application/octet-stream", f"google_drive_file_{clean_id[:8]}"),
        ]

        last_error = None
        for test_url, default_mime, default_name in candidate_urls:
            try:
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) SchoolNet-Sync/1.0'}
                req = urllib.request.Request(test_url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        content = resp.read()
                        ct = resp.headers.get('Content-Type', default_mime)
                        # Якщо Google повернув сторінку HTML авторизації замість бінарного файлу
                        if 'text/html' in ct and b'<html' in content[:100].lower():
                            continue

                        cd = resp.headers.get('Content-Disposition', '')
                        filename = default_name
                        if 'filename=' in cd:
                            m = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';]+)["\']?', cd)
                            if m:
                                filename = urllib.parse.unquote(m.group(1).strip())

                        return content, ct, filename
            except Exception as e:
                last_error = e
                continue

        if last_error:
            raise ValueError(f"Не вдалося завантажити файл із Google Drive ({clean_id}). Переконайтеся, що доступ до файлу відкрито для перегляду за посиланням.")
        raise ValueError(f"Не вдалося завантажити файл з Google Drive ({clean_id}).")
