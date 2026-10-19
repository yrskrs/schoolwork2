"""Database queue and private staging for cloud materials, shared by web workers."""
from datetime import timedelta
import hmac
import logging
import os
from pathlib import Path
import threading
import time
import uuid

from django.conf import settings
from django.core.files import File
from django.db import close_old_connections, transaction
from django.db.models import F
from django.utils import timezone

from .cloud_files import CloudStorageError, fingerprint, provider_error
from .models import AssignmentFile, CloudImport

logger = logging.getLogger(__name__)
HEARTBEAT = Path('/tmp/schoolnet-cloud-worker-heartbeat')


def touch_heartbeat():
    HEARTBEAT.touch()
    root = Path(settings.CLOUD_IMPORT_ROOT)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    (root / '.heartbeat').touch()


def worker_available():
    try:
        return time.time() - (Path(settings.CLOUD_IMPORT_ROOT) / '.heartbeat').stat().st_mtime < 60
    except OSError:
        return False


def material_transaction(view):
    from functools import wraps
    from django.shortcuts import render

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            with transaction.atomic():
                return view(request, *args, **kwargs)
        except CloudStorageError as error:
            return render(request, 'feed/cloud_import_error.html', {'import_error': str(error)}, status=422)
    return wrapped


def staging_path(job, lease=None):
    root = Path(settings.CLOUD_IMPORT_ROOT)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / (str(job.pk) + ('.' + str(lease) + '.part' if lease else '.bin'))


def describe(job):
    return {'id': str(job.pk), 'status': job.status, 'name': job.filename,
            'size': job.size, 'received': job.bytes_received, 'total': job.total_bytes,
            'sha256': job.sha256 if job.status in ('ready', 'attached') else '',
            'source_verified': job.source_verified, 'exported': job.exported,
            'error': job.error, 'error_code': job.error_code}


def enqueue(teacher, provider, source, request_key, filename=''):
    if provider not in ('nextcloud', 'google') or not source or len(source) > 1500 or not request_key or len(request_key) > 80:
        raise CloudStorageError('Некоректний запит імпорту.', 'invalid', 400)
    storage = teacher.get_cloud_storage()
    if not (storage.has_nextcloud() if provider == 'nextcloud' else storage.has_google_drive()):
        raise CloudStorageError('Спочатку підключіть обраний хмарний диск.', 'disconnected', 400)
    if provider == 'google':
        storage.get_google_drive_client()._reference(source)
    else:
        storage.get_nextcloud_client()._clean_path(source)
    existing = CloudImport.objects.filter(teacher=teacher, request_key=request_key).first()
    if existing:
        if existing.provider != provider or existing.source != source:
            raise CloudStorageError('Ідентифікатор імпорту вже використано.', 'conflict', 409)
        return existing
    if CloudImport.objects.filter(teacher=teacher, status__in=('queued', 'running')).count() >= 100:
        raise CloudStorageError('Забагато незавершених імпортів. Дочекайтеся завершення.', 'busy', 429)
    job, _ = CloudImport.objects.get_or_create(teacher=teacher, request_key=request_key, defaults={
        'provider': provider, 'source': source, 'filename': filename[:500],
        'expires_at': timezone.now() + timedelta(hours=24)})
    if job.provider != provider or job.source != source:
        raise CloudStorageError('Ідентифікатор імпорту вже використано.', 'conflict', 409)
    return job


def claim():
    now = timezone.now()
    stale = now - timedelta(seconds=settings.CLOUD_IMPORT_TIMEOUT + 60)
    CloudImport.objects.filter(status='running', updated_at__lt=stale, attempts__lt=3).update(
        status='queued', lease=None, updated_at=now, bytes_received=0)
    CloudImport.objects.filter(status='running', updated_at__lt=stale, attempts__gte=3).update(
        status='failed', error='Імпорт перервано кілька разів. Повторіть спробу.', error_code='interrupted', updated_at=now)
    for job in CloudImport.objects.filter(status='queued', expires_at__gt=now)[:10]:
        lease = uuid.uuid4()
        if CloudImport.objects.filter(pk=job.pk, status='queued').update(
                status='running', lease=lease, attempts=F('attempts')+1, updated_at=now):
            job.refresh_from_db()
            return job
    return None


def run_import(job):
    part = staging_path(job, job.lease)
    last_update = 0
    download = None

    def progress(received, total):
        nonlocal last_update
        monotonic = time.monotonic()
        if received and monotonic - last_update < .25:
            return
        last_update = monotonic
        touch_heartbeat()
        changed = CloudImport.objects.filter(pk=job.pk, lease=job.lease, status='running').update(
            bytes_received=received, total_bytes=total, updated_at=timezone.now())
        if not changed:
            raise CloudStorageError('Імпорт скасовано.', 'cancelled', 409)

    try:
        storage = job.teacher.get_cloud_storage()
        if job.provider == 'google':
            def save_token(token, expiry):
                storage.google_access_token, storage.google_token_expiry = token, expiry
                storage.save(update_fields=['google_access_token', 'google_token_expiry'])
            download = storage.get_google_drive_client().download_file(
                job.source, save_callback=save_token, progress=progress, destination=part)
        else:
            download = storage.get_nextcloud_client().download_file(job.source, progress=progress, destination=part)
        download.file.flush()
        os.fsync(download.file.fileno())
        actual_size, actual_hash = fingerprint(download.file)
        if actual_size != download.size or not hmac.compare_digest(actual_hash, download.sha256):
            raise CloudStorageError('Контрольна сума тимчасового файла не збігається.', 'integrity', 422)
        download.close()
        with transaction.atomic():
            current = CloudImport.objects.select_for_update().get(pk=job.pk)
            if current.status != 'running' or current.lease != job.lease:
                return
            part.replace(staging_path(job))
            current.status, current.filename = 'ready', download.filename
            current.content_type, current.size = download.content_type, download.size
            current.bytes_received = current.total_bytes = download.size
            current.sha256, current.source_verified = download.sha256, download.source_verified
            current.exported, current.updated_at = download.exported, timezone.now()
            current.save()
    except Exception as error:
        safe = error if isinstance(error, CloudStorageError) else provider_error(error)
        CloudImport.objects.filter(pk=job.pk, lease=job.lease, status='running').update(
            status='failed', error=str(safe)[:600], error_code=safe.code, updated_at=timezone.now())
    finally:
        if download:
            download.close()
        part.unlink(missing_ok=True)


def cancel(job):
    with transaction.atomic():
        current = CloudImport.objects.select_for_update().filter(pk=job.pk).first()
        if current is None:
            job.status = 'cancelled'
            return job
        if current.status in ('queued', 'running', 'ready'):
            current.status, current.updated_at = 'cancelled', timezone.now()
            current.save(update_fields=['status', 'updated_at'])
            staging_path(job).unlink(missing_ok=True)
    return current


def prepare(ids, teacher, assignment=None):
    if not isinstance(ids, list) or len(ids) > 100 or any(not isinstance(value, str) for value in ids):
        raise CloudStorageError('Некоректний список хмарних файлів.', 'invalid', 400)
    try:
        ids = list(dict.fromkeys(str(uuid.UUID(value)) for value in ids))
    except ValueError as error:
        raise CloudStorageError('Некоректний ідентифікатор хмарного файла.', 'invalid', 400) from error
    found = {str(job.pk): job for job in CloudImport.objects.filter(pk__in=ids, teacher=teacher)}
    result = []
    for value in ids:
        job = found.get(value)
        if not job or job.expires_at <= timezone.now():
            raise CloudStorageError('Хмарний файл недоступний або термін імпорту минув. Додайте його повторно.', 'expired', 422)
        if job.status == 'attached' and assignment and job.assignment_file_id and job.assignment_file.assignment_id == assignment.pk:
            continue
        if job.status != 'ready':
            raise CloudStorageError('Дочекайтеся перевірки всіх обраних хмарних файлів.', 'not_ready', 422)
        try:
            with staging_path(job).open('rb') as source:
                actual = fingerprint(source)
        except OSError as error:
            raise CloudStorageError('Тимчасовий файл втрачено. Додайте його повторно.', 'missing', 422) from error
        if actual != (job.size, job.sha256):
            raise CloudStorageError('Хмарний файл змінився після перевірки. Додайте його повторно.', 'integrity', 422)
        result.append(job)
    return result


def attach(jobs, assignment, teacher, ai_task_file=''):
    created = []
    try:
        with transaction.atomic():
            locked = list(CloudImport.objects.select_for_update().filter(pk__in=[job.pk for job in jobs], teacher=teacher))
            checked = prepare([str(job.pk) for job in locked], teacher, assignment)
            for job in checked:
                with staging_path(job).open('rb') as source:
                    material = AssignmentFile(assignment=assignment, original_name=job.filename,
                        source_provider=job.provider, is_task_source_for_ai=(ai_task_file == 'cloud:' + str(job.pk)))
                    material.file = File(source, name=job.filename)
                    material.save()
                created.append(material)
                if (material.verified_size, material.content_sha256) != (job.size, job.sha256):
                    raise CloudStorageError('Файл змінився під час прикріплення. Повторіть імпорт.', 'integrity', 422)
                job.status, job.assignment_file, job.updated_at = 'attached', material, timezone.now()
                job.save(update_fields=['status', 'assignment_file', 'updated_at'])
            # Staged bytes are kept until expiry so an enclosing transaction can roll back safely.
        return created
    except BaseException:
        for material in created:
            material.file.delete(save=False)
        raise


def cleanup():
    now = timezone.now()
    expired = CloudImport.objects.filter(expires_at__lt=now)
    for job in expired.iterator(chunk_size=100):
        staging_path(job).unlink(missing_ok=True)
    expired.delete()
    cutoff = time.time() - settings.CLOUD_IMPORT_TIMEOUT - 120
    for path in Path(settings.CLOUD_IMPORT_ROOT).glob('*.part'):
        if path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


def worker_loop(stop_event, once=False):
    last_cleanup = 0
    while not stop_event.is_set():
        close_old_connections()
        touch_heartbeat()
        try:
            if time.monotonic() - last_cleanup > 60:
                cleanup()
                last_cleanup = time.monotonic()
            job = claim()
            if job:
                run_import(job)
        except Exception as error:
            logger.warning('Cloud import worker cycle failed (%s)', type(error).__name__)
        finally:
            close_old_connections()
        if once:
            return
        stop_event.wait(.5)


def start_worker(stop_event):
    thread = threading.Thread(target=worker_loop, args=(stop_event,), name='cloud-import-worker', daemon=True)
    thread.start()
    return thread
