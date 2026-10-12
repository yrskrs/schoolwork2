from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST, require_GET

from feed.services.updates import (
    get_current_local_version,
    get_current_release,
    check_updates_available,
    get_update_status,
    start_update,
    start_rollback,
)


def _user_can_manage_updates(user, session=None):
    """Checks whether the user has administrator privileges to view or perform updates."""
    if not user.is_authenticated:
        return False
    is_super = user.is_superuser
    if session and session.get('superadmin_mode', False):
        is_super = True
    return is_super


@login_required(login_url='teacher_login')
def system_updates_view(request):
    """Web interface for system updates."""
    can_update = _user_can_manage_updates(request.user, request.session)
    if not can_update:
        return HttpResponseForbidden('Доступ до розділу оновлень мають лише адміністратори системи.')

    current_version = get_current_local_version()
    status_info = get_update_status()

    context = {
        'current_version': current_version,
        'current_release': get_current_release(),
        'status_info': status_info,
        'can_update': can_update,
        'active_tab': 'updates',
        'is_superadmin_mode': bool(request.user.is_superuser and request.session.get('superadmin_mode', False)),
    }
    return render(request, 'feed/system_updates.html', context)


@login_required
def updates_check_api_view(request):
    """Checks GitHub for new releases and returns releases_between changelog diff."""
    if not _user_can_manage_updates(request.user, request.session):
        return HttpResponseForbidden('Тільки адміністратор може перевіряти оновлення.')
    result = check_updates_available()
    return JsonResponse(result)


@login_required
@require_POST
def updates_start_api_view(request):
    """Triggers the background update execution via updater service."""
    if not _user_can_manage_updates(request.user, request.session):
        return HttpResponseForbidden('Тільки адміністратор може запускати оновлення системи.')

    target_ref = request.POST.get('ref', 'origin/main')
    target_ver = request.POST.get('version')
    result = start_update(target_ref=target_ref, target_ver=target_ver)
    return JsonResponse(result)


@login_required
@require_GET
def updates_status_api_view(request):
    """Returns current update step, progress percentage, and live terminal logs."""
    if not _user_can_manage_updates(request.user, request.session):
        return HttpResponseForbidden('Тільки адміністратор може переглядати статус оновлення.')
    status_info = get_update_status()
    return JsonResponse(status_info)


@login_required
@require_POST
def updates_rollback_api_view(request):
    """Rolls back the system to the specified backup snapshot."""
    if not _user_can_manage_updates(request.user, request.session):
        return HttpResponseForbidden('Тільки адміністратор може запускати відновлення системи.')

    backup_file = request.POST.get('backup_file')
    result = start_rollback(backup_file=backup_file)
    return JsonResponse(result)


@require_GET
def healthz_view(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return JsonResponse({'status': 'error', 'error': 'Database unavailable'}, status=503)
    return JsonResponse({'status': 'ok', 'version': get_current_local_version()})
