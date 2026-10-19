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
import http.client
import http.cookiejar
import time
import urllib.request
import urllib.parse
import urllib.error
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from django.conf import settings
from django.utils import timezone
from .cloud_files import (CloudStorageError, provider_error, response_filename,
                          stream_response, clean_filename)


def _google_host(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ''
    return parsed.scheme == 'https' and not parsed.username and not parsed.password and any(
        host == domain or host.endswith('.' + domain)
        for domain in ('google.com', 'googleapis.com', 'googleusercontent.com'))


class GoogleRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _google_host(newurl):
            raise CloudStorageError('Диск перенаправив запит на непідтримувану адресу.', 'invalid', 400)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            redirected.remove_header('Authorization')
        return redirected


class GoogleDownloadFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action, self.fields, self.active = '', {}, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form' and attrs.get('id') == 'download-form' and attrs.get('method', 'get').lower() == 'get':
            self.action, self.active = attrs.get('action', ''), True
        elif self.active and tag == 'input' and attrs.get('type') == 'hidden' and attrs.get('name'):
            self.fields[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False


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
    if urllib.parse.urlsplit(text).scheme:
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme != 'https' or parsed.hostname not in ('drive.google.com', 'docs.google.com', 'drive.usercontent.google.com') or parsed.username or parsed.password:
            return None
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
        username_encoded = urllib.parse.quote(self.username, safe='')
        return f"{url}/remote.php/dav/files/{username_encoded}"

    def _get_ssl_context(self):
        """Повертає SSL-контекст з підтримкою самопідписаних сертифікатів для локальних серверів."""
        ctx = ssl.create_default_context()
        ca_file = getattr(settings, 'CLOUD_NEXTCLOUD_CA_FILE', '')
        if ca_file:
            ctx.load_verify_locations(cafile=ca_file)
        if self.ignore_ssl:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _open(self, request):
        """Preserve the original TLS hostname when connecting through a gateway."""
        context = self._get_ssl_context()
        original_host = urllib.parse.urlsplit(self.webdav_url).hostname
        actual_host = urllib.parse.urlsplit(request.full_url).hostname
        if request.full_url.startswith('https://') and actual_host != original_host:
            class RoutedHTTPSConnection(http.client.HTTPSConnection):
                def connect(connection):
                    connection.sock = connection._create_connection(
                        (connection.host, connection.port), connection.timeout, connection.source_address)
                    connection.sock = connection._context.wrap_socket(connection.sock, server_hostname=original_host)
            class RoutedHTTPSHandler(urllib.request.HTTPSHandler):
                def https_open(handler, req):
                    return handler.do_open(RoutedHTTPSConnection, req, context=context)
            return urllib.request.build_opener(urllib.request.ProxyHandler({}), RoutedHTTPSHandler()).open(request, timeout=self.timeout)
        return urllib.request.urlopen(request, timeout=self.timeout, context=context)

    @staticmethod
    def _clean_path(rel_path):
        path = (rel_path or '').strip().strip('/')
        if any(part in ('.', '..') for part in urllib.parse.unquote(path).split('/')) or '\\' in path or '\x00' in path:
            raise CloudStorageError('Некоректний шлях до файла Nextcloud.', 'invalid', 400)
        return path

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
            with self._open(req) as resp:
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
                    with self._open(leg_req) as leg_resp:
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
        clean_rel = self._clean_path(rel_path)
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
        with self._open(req) as resp:
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

    def download_file(self, rel_path, progress=None, destination=None):
        clean_rel = self._clean_path(rel_path)
        if not clean_rel:
            raise CloudStorageError('Оберіть файл Nextcloud.', 'invalid', 400)
        encoded = '/'.join(urllib.parse.quote(segment, safe='') for segment in clean_rel.split('/'))
        url, headers = self._resolve_url_and_headers(
            f'{self.webdav_url}/{encoded}', {'Authorization': self._make_auth_header(),
                                             'User-Agent': 'SchoolNet-Cloud-Sync/1.0'})
        try:
            with self._open(urllib.request.Request(url, headers=headers)) as response:
                checksum = response.headers.get('OC-Checksum', '')
                algorithm, _, digest = checksum.partition(':')
                return stream_response(response, filename=clean_rel.split('/')[-1],
                    checksum=digest, algorithm=algorithm.lower(), progress=progress,
                    destination=destination, source_id=clean_rel, authoritative_name=True)
        except CloudStorageError:
            raise
        except Exception as error:
            raise provider_error(error) from error


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

        href = urllib.parse.urljoin('https://drive.google.com/', href)
        if not extract_google_drive_id(href):
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
                    'file_url': href
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
                mime = "application/vnd.google-apps." + {"document":"document", "spreadsheets":"spreadsheet", "presentation":"presentation"}[doc_type]
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
                    'folder_url': href
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
            raw_html = resp.read(4 * 1024 * 1024 + 1)
            if len(raw_html) > 4 * 1024 * 1024:
                raise CloudStorageError('Публічний список завеликий. Підключіть Google OAuth.', 'too_large', 422)
            html_text = raw_html.decode('utf-8', errors='replace')
    except CloudStorageError:
        raise
    except Exception as error:
        raise provider_error(error) from error

    if any(marker in html_text for marker in ('ServiceLogin', 'Sign in to your Google Account', 'Allow Google Drive access')):
        raise CloudStorageError('Папка приватна. Для доступу увійдіть через Google OAuth.', 'permission', 403)

    parser = DriveHTMLParser(clean_id)
    try:
        parser.feed(html_text)
    except Exception as error:
        raise CloudStorageError('Не вдалося прочитати публічний список Google Drive. Спробуйте OAuth.', 'format', 502) from error
    if not parser.title and not parser.items:
        raise CloudStorageError('Google повернув невідомий формат списку. Спробуйте OAuth.', 'format', 502)

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
    - OAuth для приватних файлів та публічні посилання без пароля Gmail
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
        with self._open(req) as resp:
            data = json.loads(resp.read(2 * 1024 * 1024).decode('utf-8'))

        access_token = data.get('access_token', '')
        refresh_token = data.get('refresh_token', '')
        if not access_token:
            raise CloudStorageError('Google не підтвердив доступ. Повторіть OAuth-вхід.', 'authorization', 401)
        expires_in = data.get('expires_in', 3600)

        email = ''
        if access_token:
            try:
                uireq = urllib.request.Request(
                    self.OAUTH_USERINFO_URL,
                    headers={'Authorization': f'Bearer {access_token}', 'User-Agent': 'SchoolNet-Cloud-Sync/1.0'}
                )
                with self._open(uireq) as uiresp:
                    uidata = json.loads(uiresp.read(2 * 1024 * 1024).decode('utf-8'))
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
            with self._open(req) as resp:
                data = json.loads(resp.read(2 * 1024 * 1024).decode('utf-8'))

            self.access_token = data.get('access_token', '')
            if not self.access_token:
                raise CloudStorageError('Поновіть авторизацію Google Drive.', 'authorization', 401)
            expires_in = data.get('expires_in', 3600)
            from datetime import timedelta
            self.token_expiry = timezone.now() + timedelta(seconds=expires_in)

            if callable(save_callback):
                save_callback(self.access_token, self.token_expiry)

        return self.access_token

    def _open(self, request):
        if not hasattr(self, '_opener'):
            self._opener = urllib.request.build_opener(
                GoogleRedirectHandler(), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        return self._opener.open(request, timeout=self.timeout)

    def _api_json(self, endpoint, save_callback=None, extra_headers=None):
        for attempt in range(3):
            token = self.ensure_valid_token(save_callback=save_callback) if (self.access_token or self.refresh_token) else ''
            separator = '&' if '?' in endpoint else '?'
            url = self.DRIVE_API_BASE + endpoint
            if self.api_key and not token:
                url += separator + urllib.parse.urlencode({'key': self.api_key})
            headers = {'User-Agent': 'SchoolNet-Cloud-Sync/2.0'}
            headers.update(extra_headers or {})
            if token:
                headers['Authorization'] = 'Bearer ' + token
            try:
                with self._open(urllib.request.Request(url, headers=headers)) as response:
                    return json.loads(response.read(2 * 1024 * 1024))
            except urllib.error.HTTPError as error:
                if error.code == 401 and self.refresh_token and attempt == 0:
                    self.access_token = ''
                    continue
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(.25 * (2 ** attempt))
                    continue
                raise provider_error(error) from error
            except CloudStorageError:
                raise
            except Exception as error:
                raise provider_error(error) from error
        raise CloudStorageError('Не вдалося прочитати Google Drive.')

    @staticmethod
    def _reference(value):
        fid = extract_google_drive_id(value)
        if not fid:
            raise CloudStorageError('Вставте коректне посилання на файл Google Drive або Google Документ.', 'invalid', 400)
        parsed = urllib.parse.urlsplit(str(value))
        params = urllib.parse.parse_qs(parsed.query)
        resource_key = params.get('resourcekey', [''])[0]
        if resource_key and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', resource_key):
            raise CloudStorageError('Некоректний ключ посилання Google Drive.', 'invalid', 400)
        native = next((kind for kind in ('document', 'spreadsheets', 'presentation')
                       if re.search(r'/' + kind + r'/d/', parsed.path)), '')
        return fid, resource_key, native

    EXPORTS = {
        'application/vnd.google-apps.document': ('document', 'docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'),
        'application/vnd.google-apps.spreadsheet': ('spreadsheets', 'xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'),
        'application/vnd.google-apps.presentation': ('presentation', 'pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation'),
    }

    def test_connection(self, save_callback=None):
        try:
            if self.access_token or self.refresh_token:
                self._api_json('/about?fields=user', save_callback)
                if self.folder_link:
                    folder_id, _, _ = self._reference(self.folder_link)
                    metadata = self._metadata(self.folder_link, save_callback)
                    if metadata.get('mimeType') != 'application/vnd.google-apps.folder':
                        return False, 'Посилання має вести на папку Google Drive.'
                return True, 'Доступ до Google Drive через OAuth підтверджено.'
            if self.folder_link:
                folder_id, _, _ = self._reference(self.folder_link)
                if self.api_key:
                    metadata = self._api_json('/files/' + folder_id + '?fields=id,name,mimeType', save_callback)
                    if metadata.get('mimeType') != 'application/vnd.google-apps.folder':
                        return False, 'Посилання має вести на папку Google Drive.'
                else:
                    fetch_embedded_google_folder(folder_id, self.timeout)
                return True, 'Читання публічної папки Google Drive підтверджено.'
            return False, 'Для приватних файлів увійдіть через Google OAuth. Для публічних матеріалів вкажіть посилання на папку. Email і пароль Gmail не надають доступу до Drive.'
        except CloudStorageError as error:
            return False, str(error)
        except Exception as error:
            return False, str(provider_error(error))

    def list_directory(self, folder_id='root', save_callback=None):
        folder_id = (folder_id or 'root').strip()
        linked_id = extract_google_drive_id(self.folder_link) if self.folder_link else None
        if folder_id != 'root' and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', folder_id):
            raise CloudStorageError('Некоректна папка Google Drive.', 'invalid', 400)
        use_api = bool(self.access_token or self.refresh_token or self.api_key)
        search_folder = linked_id if folder_id == 'root' and linked_id and use_api else folder_id
        crumbs = [{'name': 'Google Drive', 'id': 'root'}]
        if search_folder != 'root':
            crumbs.append({'name': 'Папка матеріалів', 'id': search_folder})
        result = {'items': [], 'breadcrumbs': crumbs, 'current_folder_id': search_folder,
                  'linked_folder_id': linked_id, 'folder_link': self.folder_link,
                  'direct_link_enabled': True, 'mode': 'api' if use_api else 'public'}
        if use_api and (self.access_token or self.refresh_token or search_folder != 'root'):
            page_token, seen = '', set()
            for page in range(100):
                params = {'q': f"'{search_folder}' in parents and trashed = false",
                          'fields': 'nextPageToken,files(id,name,mimeType,size,modifiedTime,resourceKey,md5Checksum,sha256Checksum)',
                          'pageSize': 100, 'orderBy': 'folder,name',
                          'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
                if page_token:
                    params['pageToken'] = page_token
                data = self._api_json('/files?' + urllib.parse.urlencode(params), save_callback)
                for entry in data.get('files', []):
                    mime, name = entry.get('mimeType', ''), entry.get('name', 'file')
                    native = self.EXPORTS.get(mime)
                    if native and not name.lower().endswith('.' + native[1]):
                        name += '.' + native[1]
                    is_dir = mime == 'application/vnd.google-apps.folder'
                    size = int(entry.get('size') or 0)
                    size_known = entry.get('size') is not None
                    source = 'https://drive.google.com/file/d/' + entry['id'] + '/view'
                    if native:
                        source = 'https://docs.google.com/' + native[0] + '/d/' + entry['id'] + '/edit'
                    if entry.get('resourceKey'):
                        source += '?resourcekey=' + urllib.parse.quote(entry['resourceKey'], safe='')
                    result['items'].append({'id': entry['id'], 'name': name, 'is_dir': is_dir,
                        'size': size, 'size_known': size_known, 'exported': bool(native),
                        'size_display': '' if is_dir else ((format_bytes(size) + (' у Drive' if native else '')) if size_known else ('Після експорту' if native else 'Розмір недоступний')),
                        'modified': entry.get('modifiedTime', ''), 'mime_type': mime,
                        'file_url': source, 'icon': get_file_icon(name, is_dir)})
                next_token = data.get('nextPageToken', '')
                if not next_token:
                    return result
                if next_token in seen:
                    raise CloudStorageError('Google Drive повторив сторінку списку. Оновіть папку.', 'pagination', 502)
                seen.add(next_token)
                page_token = next_token
            raise CloudStorageError('Папка завелика для одного перегляду. Відкрийте підпапку.', 'too_many', 422)
        if folder_id == 'root':
            if linked_id:
                result['items'] = [{'id': linked_id, 'name': 'Прив’язана папка Google Drive',
                    'is_dir': True, 'size': 0, 'size_display': '', 'modified': '', 'icon': '📁'}]
            return result
        public = fetch_embedded_google_folder(folder_id, self.timeout)
        for item in public['items']:
            native = item['mime_type'] in self.EXPORTS
            item.update(size=0, size_known=False, exported=native,
                        size_display='' if item['is_dir'] else ('Після експорту' if native else 'Уточнення…'),
                        modified='', icon=get_file_icon(item['name'], item['is_dir']))
        result['items'] = public['items']
        result['warning'] = 'Публічний перегляд. Для стабільного повного списку та приватних файлів підключіть Google OAuth.'
        return result

    def _public_response(self, source):
        fid, resource_key, native = self._reference(source)
        if native:
            export = next(value for value in self.EXPORTS.values() if value[0] == native)
            url = f'https://docs.google.com/{native}/d/{fid}/export?' + urllib.parse.urlencode({'format': export[1]})
            fallback = f'Google_{fid[:8]}.{export[1]}'
        else:
            params = {'export': 'download', 'id': fid}
            if resource_key:
                params['resourcekey'] = resource_key
            url = 'https://drive.google.com/uc?' + urllib.parse.urlencode(params)
            fallback = 'Drive_' + fid[:8]
        try:
            response = self._open(urllib.request.Request(url, headers={'User-Agent': 'SchoolNet-Cloud-Sync/2.0'}))
            content_type = response.headers.get('Content-Type', '').lower()
            disposition = response.headers.get('Content-Disposition', '').lower()
            if 'text/html' in content_type and 'attachment' not in disposition:
                html_text = response.read(512 * 1024).decode('utf-8', errors='replace')
                response.close()
                form = GoogleDownloadFormParser()
                form.feed(html_text)
                action = urllib.parse.urljoin(url, form.action)
                if native or not form.action or not _google_host(action) or form.fields.get('id') != fid:
                    raise CloudStorageError('Файл недоступний за публічним посиланням. Перевірте доступ або увійдіть через Google OAuth.', 'permission', 403)
                # Keep the exact source ID/resource key across Google's download confirmation.
                params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(action).query))
                params.update(form.fields)
                if resource_key:
                    params['resourcekey'] = resource_key
                action = urllib.parse.urlunsplit((*urllib.parse.urlsplit(action)[:3], urllib.parse.urlencode(params), ''))
                response = self._open(urllib.request.Request(action, headers={'User-Agent': 'SchoolNet-Cloud-Sync/2.0'}))
            return response, fallback, bool(native), fid
        except CloudStorageError:
            raise
        except Exception as error:
            raise provider_error(error) from error

    def _metadata(self, source, save_callback=None):
        fid, resource_key, _ = self._reference(source)
        params = {'fields': 'id,name,mimeType,size,md5Checksum,sha256Checksum,capabilities(canDownload),shortcutDetails,version', 'supportsAllDrives': 'true'}
        headers = {'X-Goog-Drive-Resource-Keys': fid + '/' + resource_key} if resource_key else None
        metadata = self._api_json('/files/' + fid + '?' + urllib.parse.urlencode(params), save_callback, headers)
        if metadata.get('id') != fid:
            raise CloudStorageError('Google Drive повернув інший файл. Імпорт зупинено.', 'identity', 422)
        if metadata.get('mimeType') == 'application/vnd.google-apps.shortcut':
            raise CloudStorageError('Це ярлик Google Drive. Вставте посилання на сам файл.', 'shortcut', 422)
        if metadata.get('capabilities', {}).get('canDownload') is False:
            raise CloudStorageError('Власник заборонив завантажувати цей файл.', 'permission', 403)
        return metadata

    def resolve_file(self, source, save_callback=None):
        if self.access_token or self.refresh_token or self.api_key:
            metadata = self._metadata(source, save_callback)
            mime = metadata.get('mimeType', '')
            if mime == 'application/vnd.google-apps.folder':
                raise CloudStorageError('Це папка. Відкрийте її та оберіть файли.', 'folder', 400)
            name = metadata.get('name', 'file')
            export = self.EXPORTS.get(mime)
            if export and not name.lower().endswith('.' + export[1]):
                name += '.' + export[1]
            return {'id': metadata['id'], 'name': clean_filename(name), 'is_dir': False,
                    'mime_type': mime, 'size': int(metadata.get('size') or 0),
                    'size_known': metadata.get('size') is not None,
                    'size_display': (format_bytes(int(metadata['size'])) + (' у Drive' if export else '')) if metadata.get('size') is not None else ('Після експорту' if export else 'Розмір недоступний'),
                    'file_url': source, 'exported': bool(export), 'icon': get_file_icon(name)}
        response, fallback, exported, fid = self._public_response(source)
        with response:
            name = response_filename(response.headers, fallback)
            mime = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0]
            if 'text/html' in mime and 'attachment' not in response.headers.get('Content-Disposition', '').lower():
                raise CloudStorageError('Замість файла повернулася сторінка Google. Перевірте доступ.', 'permission', 403)
            if not exported and name == fallback:
                raise CloudStorageError('Google не повідомив назву файла. Підключіть OAuth або вставте пряме посилання на Google Документ.', 'metadata', 422)
            return {'id': fid, 'name': name, 'is_dir': False, 'mime_type': mime,
                    'size': int(response.headers.get('Content-Length') or 0), 'file_url': source,
                    'size_known': response.headers.get('Content-Length') is not None,
                    'size_display': format_bytes(int(response.headers['Content-Length'])) if response.headers.get('Content-Length') is not None else 'Розмір недоступний',
                    'export_size_known': exported and response.headers.get('Content-Length') is not None,
                    'exported': exported, 'icon': get_file_icon(name)}

    def download_file(self, source, save_callback=None, progress=None, destination=None):
        if self.access_token or self.refresh_token or self.api_key:
            metadata = self._metadata(source, save_callback)
            mime, fid = metadata.get('mimeType', ''), metadata['id']
            name = clean_filename(metadata.get('name', 'file'))
            export = self.EXPORTS.get(mime)
            if mime == 'application/vnd.google-apps.folder' or (mime.startswith('application/vnd.google-apps.') and not export):
                raise CloudStorageError('Цей тип Google файла не підтримує завантаження. Оберіть інший файл.', 'unsupported', 422)
            if export:
                endpoint = f'/files/{fid}/export?' + urllib.parse.urlencode({'mimeType': export[2]})
                if not name.lower().endswith('.' + export[1]):
                    name += '.' + export[1]
            else:
                endpoint = f'/files/{fid}?' + urllib.parse.urlencode({'alt':'media', 'supportsAllDrives':'true'})
            token = self.ensure_valid_token(save_callback)
            url = self.DRIVE_API_BASE + endpoint
            if not token and self.api_key:
                url += '&' + urllib.parse.urlencode({'key': self.api_key})
            headers = {'User-Agent': 'SchoolNet-Cloud-Sync/2.0'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            _, resource_key, _ = self._reference(source)
            if resource_key:
                headers['X-Goog-Drive-Resource-Keys'] = fid + '/' + resource_key
            try:
                with self._open(urllib.request.Request(url, headers=headers)) as response:
                    return stream_response(response, filename=name,
                        expected_size=int(metadata['size']) if not export and 'size' in metadata else None,
                        checksum=(metadata.get('sha256Checksum') or metadata.get('md5Checksum', '')) if not export else '',
                        algorithm='sha256' if metadata.get('sha256Checksum') else 'md5',
                        progress=progress, destination=destination, exported=bool(export), source_id=fid, authoritative_name=True)
            except CloudStorageError:
                raise
            except Exception as error:
                raise provider_error(error) from error
        response, fallback, exported, fid = self._public_response(source)
        with response:
            return stream_response(response, filename=fallback, progress=progress,
                                   destination=destination, exported=exported, source_id=fid)
