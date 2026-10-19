"""Bounded, verified cloud transfers. Never log provider credentials or URLs."""
from dataclasses import dataclass
from email.message import Message
from email.utils import collapse_rfc2231_value
import hashlib
import hmac
from pathlib import Path
import tempfile
import time
import zipfile
import shutil

from django.conf import settings


class CloudStorageError(ValueError):
    def __init__(self, message, code='unavailable', status=502):
        super().__init__(message)
        self.code, self.status = code, status


def provider_error(error):
    code = getattr(error, 'code', None)
    if code == 401:
        return CloudStorageError('Потрібно поновити авторизацію хмарного диска.', 'authorization', 401)
    if code == 403:
        return CloudStorageError('Немає дозволу читати цей файл або папку. Для приватного Google Drive увійдіть через OAuth.', 'permission', 403)
    if code == 404:
        return CloudStorageError('Файл або папку не знайдено чи доступ до них закрито.', 'not_found', 404)
    if code == 429:
        return CloudStorageError('Хмарний диск тимчасово обмежив кількість запитів. Спробуйте пізніше.', 'rate_limit', 429)
    return CloudStorageError('Хмарний диск не відповідає. Перевірте з’єднання та повторіть спробу.', 'unavailable', 502)


def clean_filename(name, fallback='file'):
    name = str(name or '').replace('\\', '/').split('/')[-1]
    name = ''.join(c for c in name if ord(c) >= 32 and c != '\x7f').strip().strip('.')
    if not name:
        name = fallback
    # Keep the extension when a provider returns an exceptionally long title.
    suffix = Path(name).suffix[:20]
    return name if len(name) <= 240 else name[:240-len(suffix)] + suffix


def response_filename(headers, fallback):
    message = Message()
    message['content-disposition'] = headers.get('Content-Disposition', '')
    extended = next((value for key, value in message.get_params(header='content-disposition', unquote=True) or []
                     if key.lower() == 'filename' and isinstance(value, tuple)), None)
    name = collapse_rfc2231_value(extended) if extended else message.get_filename()
    return clean_filename(name or fallback)


def fingerprint(source):
    source.seek(0)
    digest, size = hashlib.sha256(), 0
    for chunk in iter(lambda: source.read(256 * 1024), b''):
        digest.update(chunk)
        size += len(chunk)
    source.seek(0)
    return size, digest.hexdigest()


def validate_format(source, name):
    """Catch truncated/incorrect documents, without rewriting their contents."""
    suffix = Path(name).suffix.lower()
    try:
        if suffix in ('.docx', '.xlsx', '.pptx', '.zip', '.sb3', '.odt', '.ods', '.odp'):
            with zipfile.ZipFile(source) as archive:
                entries = archive.infolist()
                if len(entries) > 20000 or sum(item.file_size for item in entries) > 2 * 1024**3:
                    raise ValueError('archive limits')
                required = {'.docx':'word/document.xml', '.xlsx':'xl/workbook.xml',
                            '.pptx':'ppt/presentation.xml', '.sb3':'project.json'}
                if suffix in required and required[suffix] not in archive.namelist():
                    raise ValueError('wrong document type')
                if archive.testzip() is not None:
                    raise ValueError('archive checksum')
        elif suffix == '.pdf':
            from pypdf import PdfReader
            reader = PdfReader(source, strict=False)
            if not reader.is_encrypted and len(reader.pages) == 0:
                raise ValueError('empty pdf')
        elif suffix in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
            from PIL import Image
            with Image.open(source) as image:
                image.verify()
    except Exception as error:
        raise CloudStorageError('Файл пошкоджений або його вміст не відповідає формату. Його не прикріплено.', 'integrity', 422) from error
    finally:
        source.seek(0)


@dataclass
class DownloadedFile:
    file: object
    filename: str
    content_type: str
    size: int
    sha256: str
    source_verified: bool = False
    exported: bool = False
    source_id: str = ''

    def close(self):
        self.file.close()


def stream_response(response, *, filename, expected_size=None, checksum='', algorithm='',
                    progress=None, destination=None, exported=False, source_id='', authoritative_name=False):
    """RAM is bounded by a chunk; readiness follows length/hash/format checks."""
    filename = clean_filename(filename) if authoritative_name else response_filename(response.headers, filename)
    content_type = response.headers.get('Content-Type', 'application/octet-stream').split(';')[0].lower()
    disposition = response.headers.get('Content-Disposition', '').lower()
    if 'text/html' in content_type and not ((authoritative_name or 'attachment' in disposition) and Path(filename).suffix.lower() in ('.html', '.htm')):
        raise CloudStorageError('Замість файла диск повернув сторінку входу або повідомлення. Файл не прикріплено.', 'permission', 403)
    header_length = response.headers.get('Content-Length')
    try:
        header_length = int(header_length) if header_length is not None else None
    except (TypeError, ValueError):
        raise CloudStorageError('Хмарний диск повернув некоректний розмір файла.', 'integrity', 422)
    limit = getattr(settings, 'CLOUD_IMPORT_MAX_BYTES', 1024**3)
    total = expected_size if expected_size is not None else header_length
    if total is not None and (total < 0 or total > limit):
        raise CloudStorageError('Файл перевищує дозволений розмір хмарного імпорту.', 'too_large', 413)
    disk_path = Path(destination).parent if destination else Path(tempfile.gettempdir())
    if shutil.disk_usage(disk_path).free < (total or 0) + 64 * 1024**2:
        raise CloudStorageError('Недостатньо вільного місця для імпорту. Звільніть місце та повторіть спробу.', 'disk_full', 507)
    handle = open(destination, 'w+b') if destination else tempfile.TemporaryFile('w+b')
    sha256, received = hashlib.sha256(), 0
    provider_digest = hashlib.new(algorithm) if checksum and algorithm in ('sha256', 'sha1', 'md5') else None
    deadline = time.monotonic() + getattr(settings, 'CLOUD_IMPORT_TIMEOUT', 300)
    try:
        if progress:
            progress(0, total)
        while True:
            if time.monotonic() > deadline:
                raise CloudStorageError('Час завантаження вичерпано. Повторіть імпорт.', 'timeout', 504)
            chunk = response.read(256 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > limit:
                raise CloudStorageError('Файл перевищує дозволений розмір хмарного імпорту.', 'too_large', 413)
            if received % (64 * 1024**2) < len(chunk) and shutil.disk_usage(disk_path).free < 64 * 1024**2:
                raise CloudStorageError('Недостатньо вільного місця для імпорту.', 'disk_full', 507)
            handle.write(chunk)
            sha256.update(chunk)
            if provider_digest:
                provider_digest.update(chunk)
            if progress:
                progress(received, total)
        if any(length is not None and received != length for length in (expected_size, header_length)):
            raise CloudStorageError('Файл завантажено не повністю: розмір не збігається. Повторіть імпорт.', 'integrity', 422)
        if provider_digest and not hmac.compare_digest(provider_digest.hexdigest().lower(), checksum.lower()):
            raise CloudStorageError('Контрольна сума файла не збігається з хмарним диском. Файл не прикріплено.', 'integrity', 422)
        handle.flush()
        handle.seek(0)
        validate_format(handle, filename)
        return DownloadedFile(handle, filename, content_type, received, sha256.hexdigest(),
                              bool(provider_digest), exported, source_id)
    except BaseException:
        handle.close()
        raise
