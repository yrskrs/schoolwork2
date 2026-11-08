"""Cross-process failed-login throttle without storing passwords or usernames."""
import hashlib
from datetime import timedelta

from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.db import transaction
from django.dispatch import receiver
from django.utils import timezone

from .models import LoginThrottle

MAX_FAILURES = 5
WINDOW = timedelta(minutes=15)
LOCK = timedelta(minutes=15)


def _key(request, username):
    # REMOTE_ADDR only: never trust arbitrary X-Forwarded-* for throttling.
    source = request.META.get('REMOTE_ADDR', '') or 'unknown'
    value = f'{source}|{str(username or "").strip().casefold()}'.encode('utf-8')
    return hashlib.sha256(value).hexdigest()


def _remaining(row, now):
    if row and row.locked_until and row.locked_until > now:
        return max(1, int((row.locked_until - now).total_seconds() + .999))
    return 0


def wait_seconds(request, username):
    now = timezone.now()
    row = LoginThrottle.objects.filter(key=_key(request, username)).first()
    if row and row.window_started < now - WINDOW and not _remaining(row, now):
        # Expired window is not a lock; record_failure will reset it.
        return 0
    return _remaining(row, now)


def record_failure(request, username):
    now = timezone.now()
    key = _key(request, username)
    with transaction.atomic():
        row, _ = LoginThrottle.objects.select_for_update().get_or_create(
            key=key, defaults={'window_started': now})
        if row.window_started < now - WINDOW:
            row.failures = 0
            row.window_started = now
            row.locked_until = None
        row.failures += 1
        if row.failures >= MAX_FAILURES:
            row.locked_until = now + LOCK
        row.save(update_fields=['failures', 'window_started', 'locked_until', 'updated_at'])
        return _remaining(row, now)


def clear(request, username):
    LoginThrottle.objects.filter(key=_key(request, username)).delete()


@receiver(user_login_failed)
def _record_failed_login(sender, credentials, request=None, **kwargs):
    if request is None:
        return
    path = getattr(request, 'path', '')
    if path.startswith('/teacher/login') or path.startswith('/admin/login'):
        record_failure(request, credentials.get('username', ''))


@receiver(user_logged_in)
def _clear_successful_login(sender, request, user, **kwargs):
    if request is not None:
        clear(request, getattr(user, 'username', ''))
