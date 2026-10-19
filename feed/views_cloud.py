"""
Контролери (Views) для інтеграції хмарних сховищ Nextcloud та Google Drive.
"""

import os
import json
import urllib.parse
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.utils.crypto import get_random_string

from .views import teacher_required
from .cloud_storage import NextcloudClient, GoogleDriveClient, format_bytes, get_file_icon


@teacher_required
def teacher_cloud_status(request):
    """Повертає JSON зі статусом підключених хмарних сховищ для поточного вчителя."""
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    data = {
        'has_any': storage.has_any_connected(),
        'nextcloud': {
            'enabled': storage.has_nextcloud(),
            'url': storage.nextcloud_url,
            'username': storage.nextcloud_username,
            'ignore_ssl': storage.nextcloud_ignore_ssl,
        },
        'google_drive': {
            'enabled': storage.has_google_drive(),
            'email': storage.google_user_email,
            'has_token': bool(storage.google_access_token or storage.google_refresh_token),
            'has_password': bool(storage.google_password),
            'folder_link': storage.google_folder_link,
        }
    }
    return JsonResponse(data)


@teacher_required
@require_http_methods(['POST'])
def teacher_cloud_test_nextcloud(request):
    """
    Тестування підключення до Nextcloud.
    Може перевіряти або передані у запиті параметри, або збережені в профілі.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    url = request.POST.get('url') or storage.nextcloud_url
    username = request.POST.get('username') or storage.nextcloud_username
    password = request.POST.get('password') or storage.nextcloud_password
    ignore_ssl = request.POST.get('ignore_ssl')
    if ignore_ssl is not None:
        ignore_ssl = (ignore_ssl == 'true' or ignore_ssl == '1' or ignore_ssl == 'on')
    else:
        ignore_ssl = storage.nextcloud_ignore_ssl

    if not url or not username or not password:
        return JsonResponse({
            'success': False,
            'message': 'Будь ласка, заповніть усі поля: URL сервера, логін та пароль.'
        })

    client = NextcloudClient(
        base_url=url,
        username=username,
        password=password,
        ignore_ssl=ignore_ssl
    )
    success, msg = client.test_connection()
    return JsonResponse({'success': success, 'message': msg})


@teacher_required
@require_http_methods(['POST'])
def teacher_cloud_save_nextcloud(request):
    """
    Збереження налаштувань Nextcloud або від'єднання.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    action = request.POST.get('cloud_action', 'save')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')

    if action == 'disconnect':
        storage.nextcloud_enabled = False
        storage.nextcloud_url = ''
        storage.nextcloud_username = ''
        storage.nextcloud_password = ''
        storage.save()

        msg = "Nextcloud успішно від'єднано від вашого профілю."
        if is_ajax:
            return JsonResponse({'success': True, 'message': msg})
        messages.info(request, msg)
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    # Збереження нових даних
    url = request.POST.get('url', '').strip()
    username = request.POST.get('username', '').strip()
    password = request.POST.get('password', '').strip()
    ignore_ssl = (request.POST.get('ignore_ssl') in ('1', 'true', 'on'))

    if not url or not username or not password:
        msg = "Для підключення Nextcloud заповніть адресу сервера, логін та пароль."
        if is_ajax:
            return JsonResponse({'success': False, 'message': msg})
        messages.error(request, msg)
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    # Перевірка з'єднання
    client = NextcloudClient(base_url=url, username=username, password=password, ignore_ssl=ignore_ssl)
    ok, test_msg = client.test_connection()
    if not ok:
        if is_ajax:
            return JsonResponse({'success': False, 'message': f"Помилка перевірки з'єднання: {test_msg}"})
        messages.error(request, f"Не вдалося підключити Nextcloud: {test_msg}")
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    storage.nextcloud_enabled = True
    storage.nextcloud_url = url
    storage.nextcloud_username = username
    storage.nextcloud_password = password
    storage.nextcloud_ignore_ssl = ignore_ssl
    storage.save()

    msg = "Nextcloud успішно підключено та протестовано! ☁️"
    if is_ajax:
        return JsonResponse({'success': True, 'message': msg})
    messages.success(request, msg)
    return redirect(f"{reverse('teacher_settings')}?tab=profile")


@teacher_required
def teacher_cloud_google_auth_start(request):
    """
    Початок процесу OAuth 2.0 авторизації для Google Drive.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    # Отримуємо Client ID / Secret із налаштувань, .env або форми
    client_id = request.GET.get('client_id') or storage.google_client_id
    client_secret = request.GET.get('client_secret') or storage.google_client_secret

    client = GoogleDriveClient(client_id=client_id, client_secret=client_secret)
    if not client.client_id:
        messages.info(request, "Для OAuth входу потрібен Google Client ID. Ви можете скористатися швидким входом за логіном (Email) та паролем у формі нижче.")
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    # Зберігаємо у сесії state для захисту від CSRF
    state = get_random_string(32)
    request.session['google_oauth_state'] = state
    if client_id and client_id != storage.google_client_id:
        request.session['google_oauth_client_id'] = client_id
    if client_secret and client_secret != storage.google_client_secret:
        request.session['google_oauth_client_secret'] = client_secret

    redirect_uri = request.build_absolute_uri(reverse('teacher_cloud_google_callback'))
    auth_url = client.get_auth_url(redirect_uri=redirect_uri, state=state)
    return redirect(auth_url)


@teacher_required
def teacher_cloud_google_callback(request):
    """
    Обробка повернення (callback) від Google OAuth 2.0.
    """
    code = request.GET.get('code')
    state = request.GET.get('state')
    error = request.GET.get('error')

    if error:
        messages.error(request, f"Google відхилив авторизацію: {error}")
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    expected_state = request.session.pop('google_oauth_state', None)
    if not state or state != expected_state:
        messages.error(request, "Помилка безпеки OAuth state. Спробуйте авторизуватися знову.")
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    client_id = request.session.pop('google_oauth_client_id', storage.google_client_id)
    client_secret = request.session.pop('google_oauth_client_secret', storage.google_client_secret)

    client = GoogleDriveClient(client_id=client_id, client_secret=client_secret)
    redirect_uri = request.build_absolute_uri(reverse('teacher_cloud_google_callback'))

    try:
        token_data = client.exchange_code(code, redirect_uri)
    except Exception as e:
        messages.error(request, f"Не вдалося отримати токен від Google: {str(e)}")
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    from datetime import timedelta
    from django.utils import timezone
    storage.google_drive_enabled = True
    if client_id:
        storage.google_client_id = client_id
    if client_secret:
        storage.google_client_secret = client_secret
    storage.google_access_token = token_data.get('access_token', '')
    if token_data.get('refresh_token'):
        storage.google_refresh_token = token_data['refresh_token']
    storage.google_token_expiry = timezone.now() + timedelta(seconds=token_data.get('expires_in', 3600))
    storage.google_user_email = token_data.get('email', '')
    storage.save()

    label = f" ({storage.google_user_email})" if storage.google_user_email else ""
    messages.success(request, f"Google Drive успішно підключено{label}! ☁️")
    return redirect(f"{reverse('teacher_settings')}?tab=profile")


@teacher_required
@require_http_methods(['POST'])
def teacher_cloud_test_google(request):
    """
    Тестування підключення до Google Drive.
    Підтримує тестування за переданими в POST даними (email, password, folder_link, api_key)
    або збереженими в профілі налаштуваннями.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()

    email = request.POST.get('email', '').strip() or storage.google_user_email
    password = request.POST.get('password', '').strip() or storage.google_password
    folder_link = request.POST.get('folder_link', '').strip() or storage.google_folder_link
    api_key = request.POST.get('api_key', '').strip() or storage.google_api_key

    client = GoogleDriveClient(
        client_id=storage.google_client_id,
        client_secret=storage.google_client_secret,
        access_token=storage.google_access_token,
        refresh_token=storage.google_refresh_token,
        email=email,
        password=password,
        folder_link=folder_link,
        api_key=api_key,
    )

    def _save_callback(new_token, new_expiry):
        storage.google_access_token = new_token
        storage.google_token_expiry = new_expiry
        storage.save(update_fields=['google_access_token', 'google_token_expiry'])

    success, msg = client.test_connection(save_callback=_save_callback)
    return JsonResponse({'success': success, 'message': msg})


@teacher_required
@require_http_methods(['POST'])
def teacher_cloud_save_google(request):
    """
    Збереження налаштувань Google Drive (вхід за логіном і паролем, посиланням на папку, API Key або OAuth) або від'єднання.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()
    action = request.POST.get('cloud_action', 'save')
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in request.headers.get('Accept', '')

    if action == 'disconnect':
        storage.google_drive_enabled = False
        storage.google_access_token = ''
        storage.google_refresh_token = ''
        storage.google_token_expiry = None
        storage.google_user_email = ''
        storage.google_password = ''
        storage.google_folder_link = ''
        storage.google_api_key = ''
        storage.save()

        msg = "Google Drive успішно від'єднано від вашого профілю."
        if is_ajax:
            return JsonResponse({'success': True, 'message': msg})
        messages.info(request, msg)
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    # 1. Отримуємо поля форми
    email = request.POST.get('email', '').strip()
    password = request.POST.get('password', '').strip()
    folder_link = request.POST.get('folder_link', '').strip()
    api_key = request.POST.get('api_key', '').strip()

    # Опціональні OAuth поля (якщо передані)
    client_id = request.POST.get('client_id', '').strip()
    client_secret = request.POST.get('client_secret', '').strip()
    refresh_token = request.POST.get('refresh_token', '').strip()

    if email and password:
        client = GoogleDriveClient(email=email, password=password, folder_link=folder_link, api_key=api_key)
        ok, test_msg = client.test_connection()
        if not ok:
            if is_ajax:
                return JsonResponse({'success': False, 'message': f"Помилка авторизації Google: {test_msg}"})
            messages.error(request, f"Не вдалося підключити Google акаунт: {test_msg}")
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        storage.google_drive_enabled = True
        storage.google_user_email = email
        storage.google_password = password
        if folder_link:
            storage.google_folder_link = folder_link
        if api_key:
            storage.google_api_key = api_key
        storage.save()

        msg = f"Google акаунт ({email}) успішно підключено! ☁️"
        if is_ajax:
            return JsonResponse({'success': True, 'message': msg})
        messages.success(request, msg)
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    # 2. Якщо зберігаються параметри (API Key, Folder Link, Email, OAuth)
    if email:
        storage.google_user_email = email
    if folder_link:
        storage.google_folder_link = folder_link
        storage.google_drive_enabled = True
    if api_key:
        storage.google_api_key = api_key
        storage.google_drive_enabled = True

    if client_id:
        storage.google_client_id = client_id
    if client_secret:
        storage.google_client_secret = client_secret
    if refresh_token:
        storage.google_refresh_token = refresh_token
        storage.google_drive_enabled = True

    if not (email and password) and not folder_link and not api_key and not refresh_token and not storage.has_google_drive():
        msg = "Для підключення Google Drive вкажіть Email (логін), посилання на папку або API Key."
        if is_ajax:
            return JsonResponse({'success': False, 'message': msg})
        messages.error(request, msg)
        return redirect(f"{reverse('teacher_settings')}?tab=profile")

    storage.save()
    msg = "Налаштування Google Drive збережено."
    if is_ajax:
        return JsonResponse({'success': True, 'message': msg})
    messages.success(request, msg)
    return redirect(f"{reverse('teacher_settings')}?tab=profile")


@teacher_required
def teacher_cloud_browse(request):
    """
    API для перегляду вмісту хмарного сховища (Nextcloud або Google Drive).
    Параметри GET:
    - drive: 'nextcloud' або 'google'
    - path: відносний шлях у Nextcloud (за замовчуванням '')
    - folder_id: ID папки в Google Drive (за замовчуванням 'root')
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()
    drive = request.GET.get('drive', '').lower()

    if not drive:
        # Автоматично обираємо перший підключений
        if storage.has_nextcloud():
            drive = 'nextcloud'
        elif storage.has_google_drive():
            drive = 'google'
        else:
            return JsonResponse({
                'success': False,
                'error': 'Жодне хмарне сховище не підключено у вашому профілі.'
            }, status=400)

    try:
        if drive == 'nextcloud':
            if not storage.has_nextcloud():
                return JsonResponse({'success': False, 'error': 'Nextcloud не підключено або вимкнено.'}, status=400)
            client = storage.get_nextcloud_client()
            path = request.GET.get('path', '')
            data = client.list_directory(rel_path=path)
            data['success'] = True
            data['drive'] = 'nextcloud'
            data['drive_title'] = 'Nextcloud'
            return JsonResponse(data)

        elif drive == 'google':
            if not storage.has_google_drive():
                return JsonResponse({'success': False, 'error': 'Google Drive не підключено або вимкнено.'}, status=400)

            def _save_callback(new_token, new_expiry):
                storage.google_access_token = new_token
                storage.google_token_expiry = new_expiry
                storage.save(update_fields=['google_access_token', 'google_token_expiry'])

            client = storage.get_google_drive_client()
            folder_id = request.GET.get('folder_id') or request.GET.get('folder') or 'root'
            data = client.list_directory(folder_id=folder_id, save_callback=_save_callback)
            data['success'] = True
            data['drive'] = 'google'
            data['drive_title'] = 'Google Drive'
            return JsonResponse(data)

        else:
            return JsonResponse({'success': False, 'error': f'Невідомий тип хмари: {drive}'}, status=400)

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@teacher_required
def teacher_cloud_download(request):
    """
    Завантажує файл із хмарного диску та повертає його бінарний вміст.
    Використовується клієнтським JS для додавання файлу до форми завдання у DataTransfer.
    """
    teacher = request.user.teacher_profile
    storage = teacher.get_cloud_storage()
    drive = request.GET.get('drive', '').lower()

    try:
        if drive == 'nextcloud':
            if not storage.has_nextcloud():
                return HttpResponseBadRequest("Nextcloud не підключено.")
            path = request.GET.get('path', '')
            if not path:
                return HttpResponseBadRequest("Не вказано шлях до файлу Nextcloud.")
            client = storage.get_nextcloud_client()
            content, content_type, filename = client.download_file(path)

        elif drive == 'google':
            if not storage.has_google_drive():
                return HttpResponseBadRequest("Google Drive не підключено.")
            file_id = request.GET.get('file_id', '') or request.GET.get('link', '') or request.GET.get('path', '')
            if not file_id:
                return HttpResponseBadRequest("Не вказано file_id або посилання Google Drive.")

            def _save_callback(new_token, new_expiry):
                storage.google_access_token = new_token
                storage.google_token_expiry = new_expiry
                storage.save(update_fields=['google_access_token', 'google_token_expiry'])

            client = storage.get_google_drive_client()
            content, content_type, filename = client.download_file(file_id, save_callback=_save_callback)

        else:
            return HttpResponseBadRequest("Невідомий тип хмари.")

        # Формуємо коректний Content-Disposition для імені файлу (зокрема з кирилицею)
        quoted_filename = urllib.parse.quote(filename)
        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{quoted_filename}"; filename*=UTF-8\'\'{quoted_filename}'
        response['Content-Length'] = len(content)
        return response

    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
