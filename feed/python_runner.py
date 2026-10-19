"""Teacher-only proxy to the private code broker. No local execution fallback."""
import base64
import json
import logging
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.core import signing
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_http_methods

from runner.dependencies import MAX_BYTES, MAX_FILES, validate_bundle

logger = logging.getLogger(__name__)
SALT = 'schoolnet-python-run'


class RunnerError(Exception):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


def broker_request(path, payload, method='POST'):
    if not settings.CODE_RUNNER_URL or not settings.CODE_RUNNER_TOKEN:
        raise RunnerError('Сервіс запуску Python ще не налаштовано. Інструкція для адміністратора: docs/PYTHON_RUNNER.md.')
    url = settings.CODE_RUNNER_URL.rstrip('/') + path
    body = None
    if method == 'GET':
        url += '?' + urlencode(payload)
    else:
        body = json.dumps(payload).encode()
    request = Request(url, data=body, method=method, headers={
        'Authorization': 'Bearer ' + settings.CODE_RUNNER_TOKEN, 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except HTTPError as exc:
        try:
            message = json.loads(exc.read(4096)).get('error')
        except (ValueError, AttributeError):
            message = None
        raise RunnerError(message or 'Сервіс запуску повернув помилку.',
                          exc.code if exc.code in (400, 404, 409, 413, 429) else 503) from exc
    except (URLError, OSError, ValueError) as exc:
        logger.warning('Python runner unavailable: %s', type(exc).__name__)
        raise RunnerError('Сервіс запуску Python недоступний. Спробуйте пізніше або зверніться до адміністратора.') from exc


def submission_bundle(submission, file_id):
    attached = list(submission.files.all())
    if file_id:
        selected = next((f for f in attached if str(f.pk) == str(file_id)), None)
        if not selected:
            raise Http404('Файл не знайдено')
    else:
        selected = attached[0] if attached else None
    active = selected.file if selected else submission.file
    name = (selected.original_name or selected.file.name) if selected else (active.name if active else '')
    if not active or PurePosixPath(name).suffix.lower() not in ('.py', '.pyw'):
        raise RunnerError('Оберіть файл Python (.py або .pyw).', 400)
    # Only files from this submission; teacher materials and server paths are excluded.
    candidates = [(f.file, f.original_name or f.file.name) for f in attached]
    if not selected:
        candidates = [(active, name)]
    if len(candidates) > MAX_FILES:
        raise RunnerError(f'Дозволено до {MAX_FILES} файлів у запуску.', 400)
    files, total = [], 0
    for field, original in candidates:
        filename = PurePosixPath(original.replace('\\', '/')).name
        with field.open('rb') as source:
            content = source.read(MAX_BYTES - total + 1)
        total += len(content)
        if total > MAX_BYTES:
            raise RunnerError('Файли роботи перевищують ліміт 10 МБ.', 400)
        files.append({'name': filename, 'content': base64.b64encode(content).decode()})
    payload = {'entry': PurePosixPath(name.replace('\\', '/')).name, 'files': files}
    try:
        validate_bundle(payload)
    except ValueError as exc:
        raise RunnerError(str(exc), 400) from exc
    return payload


@require_http_methods(['GET', 'POST'])
def python_run(request, submission_id):
    # Import lazily to avoid the views/urls import cycle.
    from .views import _review_submission
    if not request.user.is_authenticated or not hasattr(request.user, 'teacher_profile'):
        return JsonResponse({'error': 'Увійдіть як учитель.'}, status=403)
    submission = _review_submission(request, submission_id)
    try:
        data = request.GET if request.method == 'GET' else json.loads(request.body or b'{}')
        owner = str(request.user.pk)
        if request.method == 'POST' and data.get('action') == 'start':
            payload = submission_bundle(submission, data.get('file_id'))
            result = broker_request('/runs', {**payload, 'owner': owner})
            token = signing.dumps({'job': result['id'], 'user': request.user.pk,
                                   'submission': submission.pk}, salt=SALT)
            return JsonResponse({'token': token, 'state': result['state']}, status=201)
        try:
            grant = signing.loads(data.get('token', ''), salt=SALT, max_age=900)
        except signing.BadSignature as exc:
            raise RunnerError('Термін запуску минув. Натисніть Play ще раз.', 404) from exc
        if grant['user'] != request.user.pk or grant['submission'] != submission.pk:
            raise Http404('Запуск не знайдено')
        path = '/runs/' + grant['job']
        if request.method == 'GET':
            result = broker_request(path, {'owner': owner, 'cursor': data.get('cursor', 0),
                                          'frame': data.get('frame', 0)}, 'GET')
        elif data.get('action') in ('input', 'stop'):
            result = broker_request(path + '/' + data['action'],
                                    {'owner': owner, 'input': data.get('input')})
        else:
            raise RunnerError('Невідома дія.', 400)
        response = JsonResponse(result)
        response['Cache-Control'] = 'no-store'
        return response
    except RunnerError as exc:
        return JsonResponse({'error': str(exc)}, status=exc.status)
    except OSError:
        return JsonResponse({'error': 'Не вдалося прочитати прикріплені файли роботи.'}, status=400)
    except (ValueError, TypeError, KeyError, AttributeError):
        return JsonResponse({'error': 'Некоректні дані запиту.'}, status=400)
