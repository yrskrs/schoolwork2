"""Local evidence preparation and cross-process single-flight cache fills."""
import functools
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def singleflight(key):
    from django.conf import settings
    root = Path(getattr(settings, 'AI_PREPARATION_LOCK_DIR',
        str(Path(os.environ.get('AI_CACHE_DIR', tempfile.gettempdir())) / 'preparation-locks')))
    root.mkdir(parents=True, exist_ok=True)
    with (root / (key + '.lock')).open('a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.write(b'0')
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock, fcntl.LOCK_UN)


def evidence_lock(function):
    @functools.wraps(function)
    def wrapped(path, *args, **kwargs):
        from .ai_context import file_cache_key
        from .ai_metrics import measure
        with singleflight(file_cache_key(path, function.__name__)):
            with measure(function.__name__):
                return function(path, *args, **kwargs)
    return wrapped


def queue_after_commit(kind, identity):
    from django.db import transaction
    from django.conf import settings
    if settings.AI_JOBS_EAGER:
        return

    def enqueue():
        import logging
        from .models import AIJob, Assignment
        from .ai_jobs import enqueue_preparation
        try:
            if kind == 'submission':
                enqueue_preparation(identity)
            else:
                with transaction.atomic():
                    Assignment.objects.select_for_update().get(pk=identity)
                    if not AIJob.objects.filter(kind='prepare', status__in=['queued', 'running'],
                        parameters__prepare_assignment_id=identity).exists():
                        AIJob.objects.create(kind='prepare', parameters={'prepare_assignment_id': identity})
        except Exception:
            logging.getLogger(__name__).exception('Could not queue local evidence preparation')
    transaction.on_commit(enqueue)
