"""Numeric, job-scoped timings. No work content or private URLs in telemetry."""
import time
import functools
from contextlib import contextmanager
from .ai_progress import _job_id


def record(name, value):
    from .models import AIJob
    from django.db import transaction
    if _job_id.get() is None:
        return
    try:
        with transaction.atomic():
            job = AIJob.objects.select_for_update().get(pk=_job_id.get())
            metrics = dict(job.metrics or {})
            values = metrics.setdefault(name, [])
            values.append(round(float(value), 3))
            metrics[name] = values[-100:]
            job.metrics = metrics
            job.save(update_fields=['metrics'])
    except Exception:
        # A rolled-back/deleted job or a telemetry write failure is harmless.
        pass


@contextmanager
def measure(name):
    start = time.monotonic()
    try:
        yield
    finally:
        # Telemetry must not convert a successful assessment into a failure.
        try:
            record(name + '_ms', (time.monotonic() - start) * 1000)
        except Exception:
            pass


def timed(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return wrapped
    return decorate
