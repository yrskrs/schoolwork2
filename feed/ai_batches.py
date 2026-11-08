"""Teacher-owned durable batches. Browser closure never stops queued work."""
import uuid
import functools
from django.db import transaction, connection
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.gzip import gzip_page
from .models import AIBatch, AIJob, Submission
from .permissions import teacher_submissions
from .views import teacher_required, _teacher_ai_selection, _teacher_ai_privacy_options
from .ai_jobs import enqueue_submission_job, SelfCheckUnavailable


def serialized_create(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        from .ai_scheduler import state
        with state('batch-submissions'):
            return function(*args, **kwargs)
    return wrapped


def snapshot(request, batch):
    jobs = list(batch.jobs.select_related('submission__assignment', 'submission__class_group'))
    identities = {job.submission_id for job in jobs}
    allowed = set(teacher_submissions(request, Submission.objects.filter(pk__in=identities)).values_list('pk', flat=True))
    if None in identities or allowed != identities:
        return JsonResponse({'error': 'Доступ до частини робіт змінився.'}, status=403)
    items = []
    for job in jobs:
        sub = job.submission
        result = job.result or {}
        items.append(dict(result, id=sub.pk, job_id=str(job.pk), job_status=job.status,
            retry_at=job.not_before.isoformat() if job.not_before else None,
            student_name=sub.get_student_full_name(), class_name=str(sub.class_group or '—'),
            assignment_title=sub.assignment.title, current_grade=sub.grade or '—',
            ai_status='processing' if job.status in ('queued', 'running') else result.get('status', job.status),
            ai_grade=result.get('suggested_grade', '—'), ai_feedback=result.get('feedback', ''),
            ai_error=result.get('error', '')))
    pending = sum(job.status in ('queued', 'running') for job in jobs)
    return JsonResponse({'batch_id': str(batch.pk), 'items': items, 'pending': pending,
        'cancelled': batch.cancelled_at is not None,
        'status': 'running' if pending else 'finished',
        'status_url': f'/api/ai/batches/{batch.pk}/'})


@teacher_required
@require_POST
@gzip_page
@serialized_create
@transaction.atomic
def create(request):
    try:
        identities = list(dict.fromkeys(int(value) for value in request.POST.getlist('submission_ids')))
        if not identities or len(identities) > 300:
            raise ValueError('Оберіть від 1 до 300 робіт.')
        identity = uuid.UUID(request.POST.get('request_id', ''))
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Некоректний список робіт або ідентифікатор пакета.'}, status=400)
    existing = AIBatch.objects.filter(pk=identity).first()
    if existing:
        if existing.requested_by_id != request.user.pk:
            return JsonResponse({'error': 'Пакет недоступний.'}, status=403)
        return snapshot(request, existing)
    submissions = list(teacher_submissions(request, Submission.objects.filter(pk__in=identities))
        .select_related('assignment', 'class_group').order_by('pk'))
    if len(submissions) != len(identities):
        return JsonResponse({'error': 'Немає доступу до всіх вибраних робіт.'}, status=403)
    try:
        # A savepoint rolls back every reservation if one selection is invalid.
        with transaction.atomic():
            batch = AIBatch.objects.create(pk=identity, requested_by=request.user)
            for sub in submissions:
                if AIJob.objects.filter(submission=sub, status__in=('queued', 'running')).exists():
                    raise SelfCheckUnavailable('Одну з робіт уже перевіряють. Оновіть список.', 409)
                preset, codes = _teacher_ai_selection(request, sub)
                job = enqueue_submission_job(sub, 'batch_check', user=request.user,
                    parameters={'preset_id': preset, 'selected_gr_codes': codes, **_teacher_ai_privacy_options(request)})
                job.batch = batch
                job.save(update_fields=['batch'])
    except (ValueError, SelfCheckUnavailable) as error:
        return JsonResponse({'error': str(error)}, status=getattr(error, 'http_status', 400))
    return snapshot(request, batch)


@teacher_required
@require_GET
@gzip_page
def status(request, batch_id):
    return snapshot(request, get_object_or_404(AIBatch, pk=batch_id, requested_by=request.user))


@teacher_required
@require_GET
@gzip_page
def latest(request):
    batch = AIBatch.objects.filter(requested_by=request.user).order_by('-created_at').first()
    return snapshot(request, batch) if batch else JsonResponse({'batch_id': None})


@teacher_required
@require_POST
@gzip_page
@transaction.atomic
def cancel(request, batch_id):
    batch = get_object_or_404(AIBatch.objects.select_for_update(), pk=batch_id, requested_by=request.user)
    jobs_query = batch.jobs.select_related('submission__assignment')
    # PostgreSQL cannot FOR UPDATE the nullable side of the submission join.
    # Only AIJob rows need a lock here; permissions are checked separately.
    jobs_query = jobs_query.select_for_update(of=('self',)) if connection.features.has_select_for_update_of else jobs_query.select_for_update()
    jobs = list(jobs_query)
    identities = {job.submission_id for job in jobs}
    allowed = set(teacher_submissions(request, Submission.objects.filter(pk__in=identities)).values_list('pk', flat=True))
    if None in identities or identities != allowed:
        return JsonResponse({'error': 'Немає доступу.'}, status=403)
    batch.cancelled_at = timezone.now()
    batch.save(update_fields=['cancelled_at'])
    batch.jobs.filter(status='queued').update(status='cancelled', finished_at=timezone.now(),
        result={'status': 'cancelled', 'error': 'Перевірку скасовано до початку.'})
    return snapshot(request, batch)
