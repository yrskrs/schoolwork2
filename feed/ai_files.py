"""Gemini File API for evidence that is too large for an inline request.

Only the video/audio bytes travel to Google; the local path never does. Files
expire on Google's side after 48 hours, and we also delete them after the call.
"""
import json
import os
import time
import urllib.error
import urllib.request

UPLOAD_URL = 'https://generativelanguage.googleapis.com/upload/v1beta/files'
FILES_URL = 'https://generativelanguage.googleapis.com/v1beta'
ACTIVE_STATES = {'ACTIVE'}
FAILED_STATES = {'FAILED'}


class FileApiError(RuntimeError):
    pass


def _request(url, data=None, headers=None, method=None, timeout=120):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            return response.status, dict(response.headers), body
    except urllib.error.HTTPError as error:
        body = error.read()[:2000]
        raise FileApiError(f'HTTP {error.code}: {body.decode("utf-8", "replace")}') from error
    except urllib.error.URLError as error:
        raise FileApiError(f'Network error: {error.reason}') from error


def upload_gemini_file(path, mime_type, api_key, display_name='', timeout=180):
    """Resumable upload of one file; returns {'name', 'uri', 'mime_type'}."""
    if not path or not os.path.exists(path):
        raise FileApiError('Файл для завантаження не знайдено.')
    size = os.path.getsize(path)
    if size <= 0:
        raise FileApiError('Порожній файл не можна завантажити.')
    metadata = json.dumps({'file': {'display_name': display_name or os.path.basename(path)}}).encode()
    status, headers, _ = _request(UPLOAD_URL, metadata, {
        'x-goog-api-key': api_key,
        'X-Goog-Upload-Protocol': 'resumable',
        'X-Goog-Upload-Command': 'start',
        'X-Goog-Upload-Header-Content-Length': str(size),
        'X-Goog-Upload-Header-Content-Type': mime_type,
        'Content-Type': 'application/json',
    }, 'POST', timeout=timeout)
    upload_url = headers.get('X-Goog-Upload-URL') or headers.get('x-goog-upload-url')
    if status not in (200, 201) or not upload_url:
        raise FileApiError('Не вдалося розпочати завантаження у Gemini File API.')
    with open(path, 'rb') as handle:
        payload = handle.read()
    status, _, body = _request(upload_url, payload, {
        'Content-Length': str(len(payload)),
        'X-Goog-Upload-Offset': '0',
        'X-Goog-Upload-Command': 'upload, finalize',
    }, 'POST', timeout=timeout)
    if status not in (200, 201):
        raise FileApiError(f'Завантаження не завершено (HTTP {status}).')
    data = json.loads(body.decode('utf-8'))
    info = data.get('file') or {}
    if not info.get('name') or not info.get('uri'):
        raise FileApiError('Gemini File API не повернув name/uri.')
    return {'name': info['name'], 'uri': info['uri'], 'mime_type': info.get('mimeType', mime_type)}


def wait_gemini_file(name, api_key, timeout=240, interval=2.0):
    """Poll until Google finishes processing the uploaded file."""
    deadline = time.monotonic() + timeout
    url = f'{FILES_URL}/{name}'
    while time.monotonic() < deadline:
        status, _, body = _request(url, None, {'x-goog-api-key': api_key}, 'GET', timeout=60)
        if status == 200:
            info = json.loads(body.decode('utf-8'))
            state = str(info.get('state') or '').upper()
            if state in ACTIVE_STATES:
                return info
            if state in FAILED_STATES:
                raise FileApiError('Gemini не зміг обробити завантажений файл.')
        time.sleep(interval)
    raise FileApiError('Gemini File API не завершив обробку вчасно.')


def delete_gemini_file(name, api_key):
    """Best-effort cleanup; a failure must not fail the assessment."""
    try:
        _request(f'{FILES_URL}/{name}', None, {'x-goog-api-key': api_key}, 'DELETE', timeout=30)
    except Exception:
        pass


def validate_upload_media(items):
    """Provider-side limits before any byte leaves the server."""
    from django.conf import settings
    max_files = max(1, int(getattr(settings, 'AI_FILE_API_MAX_FILES', 4)))
    max_file = max(1, int(getattr(settings, 'AI_FILE_API_MAX_FILE_MB', 200))) * 1024 * 1024
    max_total = max(1, int(getattr(settings, 'AI_FILE_API_MAX_TOTAL_MB', 400))) * 1024 * 1024
    items = [item for item in (items or []) if isinstance(item, dict)]
    if len(items) > max_files:
        return 413, f'Забагато файлів для Gemini File API: {len(items)} > {max_files}.'
    total = 0
    for item in items:
        path = item.get('upload_path')
        if not path or not os.path.exists(path):
            return 422, 'Файл для завантаження зник; потрібна повторна підготовка.'
        try:
            size = os.path.getsize(path)
        except OSError:
            return 422, 'Не вдалося прочитати файл для завантаження.'
        if size > max_file:
            return 413, (f'Файл {os.path.basename(path)} завеликий для File API '
                         f'({size // (1024 * 1024)} МБ > {max_file // (1024 * 1024)} МБ).')
        total += size
    if total > max_total:
        return 413, (f'Сумарний обсяг медіа завеликий для одного запиту '
                     f'({total // (1024 * 1024)} МБ > {max_total // (1024 * 1024)} МБ).')
    return None
