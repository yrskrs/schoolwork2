"""Persist small progress messages; never store prompts, keys or raw replies here."""
import logging
from contextlib import contextmanager
from contextvars import ContextVar

from django.db import transaction
from django.utils import timezone

_job_id = ContextVar('ai_progress_job', default=None)
logger = logging.getLogger(__name__)


@contextmanager
def track_job(job_id):
    token = _job_id.set(job_id)
    try:
        yield
    finally:
        _job_id.reset(token)


def emit_event(kind, message, *, provider='', model='', position=None, total=None, status_code=None, job_id=None):
    target = job_id or _job_id.get()
    if target is None:
        return
    from .models import AIJob
    try:
        with transaction.atomic():
            job = AIJob.objects.select_for_update().get(pk=target)
            if job.status in ('succeeded', 'failed'):
                return
            event = {'kind': kind, 'message': str(message)[:1000], 'at': timezone.now().isoformat(),
                     'provider': str(provider)[:50], 'model': str(model)[:200],
                     'position': position, 'total': total, 'status_code': status_code}
            events = job.events or []
            event['sequence'] = (events[-1]['sequence'] if events else 0) + 1
            job.events = (events + [event])[-200:]
            job.save(update_fields=['events'])
    except Exception:
        # A progress display must not turn a valid evaluation into a failed one.
        logger.exception('Could not save AI progress for job %s', target)
