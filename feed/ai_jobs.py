"""Database-backed queue. No student account or access code is required."""
import hashlib
import json
import logging
import unicodedata
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone

from .models import AIJob, StudentAICheckReservation, Submission

logger = logging.getLogger(__name__)


class SelfCheckUnavailable(Exception):
    def __init__(self, message, http_status=403):
        super().__init__(message)
        self.http_status = http_status


def _identity_key(submission):
    # Canonical Student is stable across resubmissions and corrected name spellings.
    identity = f'student:{submission.student_id}' if submission.student_id else ' '.join(unicodedata.normalize(
        'NFKC', f'{submission.last_name.casefold()} {submission.first_name.casefold()}'
    ).split())
    value = f'{submission.assignment_id}:{submission.class_group_id}:{identity}'
    return hashlib.sha256(value.encode()).hexdigest()


def enqueue_submission_job(submission, kind, user=None, parameters=None):
    parameters = parameters or {}
    with transaction.atomic():
        # Lock a stable identity row to serialize competing checks of different attempts.
        from .models import ClassGroup
        if submission.class_group_id:
            ClassGroup.objects.select_for_update().get(pk=submission.class_group_id)
        submission = Submission.objects.select_for_update().get(pk=submission.pk)
        current = AIJob.objects.filter(submission=submission, status__in=['queued', 'running']).first()
        if current:
            if current.kind == kind and current.parameters == parameters:
                return current
            raise SelfCheckUnavailable('Цю роботу вже перевіряють. Дочекайтеся результату.', 409)
        reservations = []
        if kind == 'student_check':
            if not submission.assignment.allow_student_ai_check:
                raise SelfCheckUnavailable('Самоперевірка не дозволена для цього завдання')
            members = submission.get_all_group_submissions()
            for member in sorted(members, key=_identity_key):
                attempts = member.get_all_attempts()
                pending = AIJob.objects.filter(kind='student_check', status__in=['queued', 'running'],
                                               submission_id__in=[attempt.pk for attempt in attempts]).first()
                if pending:
                    return pending
                key = _identity_key(member)
                StudentAICheckReservation.objects.get_or_create(key=key, defaults={'assignment': submission.assignment})
                reservation = StudentAICheckReservation.objects.select_for_update().get(key=key)
                if reservation.used or member.has_used_student_ai_check_for_assignment():
                    raise SelfCheckUnavailable('Ви вже скористалися самоперевіркою ШІ для цього завдання (дозволено лише 1 раз).')
                if reservation.job_id and reservation.job.status in ('queued', 'running'):
                    return reservation.job
                reservations.append(reservation)
        job = AIJob.objects.create(kind=kind, submission=submission, requested_by=user, parameters=parameters)
        for reservation in reservations:
            reservation.job = job
        if reservations:
            StudentAICheckReservation.objects.bulk_update(reservations, ['job'])
    if settings.AI_JOBS_EAGER:
        execute_job(job.pk)
        job.refresh_from_db()
    return job


def enqueue_understanding_job(assignment, user=None, force_refresh=False, eager=True):
    from .models import Assignment
    with transaction.atomic():
        Assignment.objects.select_for_update().get(pk=assignment.pk)
        job = AIJob.objects.filter(assignment=assignment, kind='understanding', status__in=['queued', 'running']).first()
        if not job:
            job = AIJob.objects.create(kind='understanding', assignment=assignment, requested_by=user,
                                       parameters={'force_refresh': force_refresh})
    if settings.AI_JOBS_EAGER and eager:
        execute_job(job.pk)
        job.refresh_from_db()
    return job


def job_response(job, is_teacher=False, request=None):
    if job.status in ('succeeded', 'failed', 'cancelled'):
        result = dict(job.result)
        result['events'] = job.events
        result['job_id'] = str(job.pk)
        if job.kind == 'student_check':
            from .ai_student_feedback import compact_student_feedback, student_feedback_parts
            public_parts = student_feedback_parts(result=result)
            for field in ('feedback', 'clean_feedback', 'feedback_comment'):
                if field in result:
                    result[field] = compact_student_feedback(text=result[field])
            result.update(summary=public_parts['summary'], strengths=public_parts['strengths'],
                          weaknesses=public_parts['weaknesses'], revision_advice=public_parts['weaknesses'])
            result.pop('grade_explanation', None)
            result.pop('raw_json', None)
            result.pop('criteria_results', None)
        if request is not None and 'duplicate_info' in result:
            from .access_control import scope_duplicate_info
            result['duplicate_info'] = scope_duplicate_info(request, result['duplicate_info'])
        if job.kind == 'understanding':
            result['is_teacher'] = is_teacher
        return JsonResponse(result, status=job.http_status)
    return JsonResponse({'status': job.status, 'job_id': str(job.pk),
                         'events': job.events,
                         'retry_at': job.not_before.isoformat() if job.not_before else None,
                         'status_url': reverse('ai_job_status', args=[job.pk])}, status=202)


def fail_job(job_id, message):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.status in ('succeeded', 'failed', 'cancelled'):
            return
        from .ai_progress import emit_event
        emit_event('failed', message, job_id=job_id)
        job.status = 'failed'
        job.result = {'ok': False, 'status': 'failed', 'error': message}
        job.http_status = 503
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'result', 'http_status', 'finished_at'])
        job.reservations.filter(used=False).update(job=None)
    from .ai_scheduler import release_job_resources
    release_job_resources(job_id)


def claim_next_job(kinds=None):
    from django.db.models import Q
    from .ai_scheduler import state
    now = timezone.now()
    expired = now - timedelta(seconds=settings.AI_JOB_TIMEOUT + 60)
    for job_id in AIJob.objects.filter(status='running', started_at__lt=expired).values_list('pk', flat=True):
        fail_job(job_id, 'Перевірку перервано. Спробуйте ще раз; спробу самоперевірки не використано.')
    # One scheduling row serializes claims across all worker processes/containers.
    with state('job-scheduling') as scheduling:
        jobs = AIJob.objects.filter(status='queued').filter(Q(not_before__isnull=True) | Q(not_before__lte=now))
        if kinds is not None:
            jobs = jobs.filter(kind__in=kinds)
        else:
            jobs = jobs.exclude(kind='prepare')
        running = AIJob.objects.filter(status='running')
        if kinds == ('prepare',):
            if running.filter(kind='prepare').count() >= settings.AI_PREP_CONCURRENCY:
                return None
        else:
            running = running.exclude(kind='prepare')
            if running.count() >= settings.AI_WORKER_CONCURRENCY:
                return None
            normal = jobs.exclude(kind='student_check')
            student = jobs.filter(kind='student_check')
            normal_capacity = max(1, settings.AI_WORKER_CONCURRENCY - settings.AI_STUDENT_RESERVED)
            if running.exclude(kind='student_check').count() >= normal_capacity:
                jobs = student
            elif student.exists() and (scheduling.get('student_burst', 0) < 3 or not normal.exists()):
                jobs = student
            else:
                jobs = normal
        jobs = jobs.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else jobs.select_for_update()
        job = jobs.first()
        if job:
            scheduling['student_burst'] = scheduling.get('student_burst', 0) + 1 if job.kind == 'student_check' else 0
            job.status = 'running'
            job.started_at = now
            job.not_before = None
            metrics = dict(job.metrics or {})
            metrics.setdefault('queue_wait_ms', []).append(round((now - job.created_at).total_seconds() * 1000, 3))
            job.metrics = metrics
            job.save(update_fields=['status', 'started_at', 'not_before', 'metrics'])
        return job


def defer_job(job_id, seconds):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.status != 'running':
            return
        if job.batch_id and job.batch.cancelled_at:
            job.status = 'cancelled'
            job.finished_at = timezone.now()
            job.result = {'status': 'cancelled', 'error': 'Пакет скасовано під час очікування квоти.'}
            job.save(update_fields=['status', 'finished_at', 'result'])
            return
        job.status = 'queued'
        job.not_before = timezone.now() + timedelta(seconds=max(1, min(86400, int(seconds))))
        job.started_at = None
        job.save(update_fields=['status', 'started_at', 'not_before'])
        from .ai_progress import emit_event
        emit_event('waiting_quota', 'Очікує доступної моделі або поновлення квоти; спробу не використано.', job_id=job_id)


def enqueue_preparation(submission_id):
    # No API call and no student reservation. Independent from the assessment's
    # unique active-job constraint because this job has no submission FK.
    if settings.AI_JOBS_EAGER:
        return None
    with transaction.atomic():
        submission = Submission.objects.select_for_update().get(pk=submission_id)
        current = AIJob.objects.filter(kind='prepare', status__in=['queued', 'running'],
            parameters__prepare_submission_id=submission.pk).first()
        return current or AIJob.objects.create(kind='prepare', parameters={'prepare_submission_id': submission.pk})


def prepare_submission(submission):
    from .gemini_service import extract_submission_content
    from .ai_context import teacher_materials
    from .ai_provenance import submission_provenance
    from .ai_metrics import measure
    with measure('prepare_submission'):
        extract_submission_content(submission)
        submission_provenance(submission)
    with measure('prepare_assignment'):
        teacher_materials(submission.assignment)



def execute_job(job_id):
    from .ai_progress import track_job
    with track_job(job_id):
        _execute_job(job_id)


def _execute_job(job_id):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.status in ('succeeded', 'failed', 'cancelled'):
            return
        if job.status == 'queued':
            job.status, job.started_at = 'running', timezone.now()
            job.save(update_fields=['status', 'started_at'])
    try:
        from .ai_progress import emit_event
        if job.kind in ('student_check', 'teacher_check', 'batch_check'):
            from .models import AISettings
            if not AISettings.get_solo().is_enabled:
                _finish_job(job_id, {'status': 'failed', 'error': 'Модуль ШІ вимкнено; спробу самоперевірки не використано.'}, 403)
                return
        emit_event('preparing', 'Читаю завдання, критерії та всі матеріали роботи.')
        if job.kind == 'prepare':
            if job.parameters.get('prepare_submission_id'):
                submission = Submission.objects.select_related('assignment').get(pk=job.parameters['prepare_submission_id'])
                prepare_submission(submission)
            else:
                from .models import Assignment
                from .ai_context import teacher_materials
                teacher_materials(Assignment.objects.get(pk=job.parameters['prepare_assignment_id']))
            _finish_job(job_id, {'status': 'success'}, 200)
            return
        if job.kind == 'understanding':
            from .gemini_service import analyze_assignment_task_understanding
            result = analyze_assignment_task_understanding(job.assignment, **job.parameters)
            _finish_job(job_id, result, 200)
            return
        if job.kind == 'student_check':
            from .views import _evaluate_student_submission, _perform_student_ai_check
            submission = Submission.objects.select_related(
                'assignment', 'assignment__default_ai_preset', 'class_group').get(pk=job.submission_id)
            if not submission.assignment.allow_student_ai_check:
                raise SelfCheckUnavailable('Самоперевірку вимкнено вчителем')
            evaluation = _evaluate_student_submission(submission)
            if evaluation.get('status') == 'deferred':
                defer_job(job_id, evaluation.get('retry_after', 2))
                return
            # The successful result, quota and every group member commit together.
            with transaction.atomic():
                response = _perform_student_ai_check(submission, evaluation)
                result, status_code = json.loads(response.content), response.status_code
                _finish_job(job_id, result, status_code)
            return
        else:
            from .gemini_service import evaluate_submission_with_gemini
            submission = Submission.objects.select_related('assignment', 'class_group').get(pk=job.submission_id)
            from types import SimpleNamespace
            from .permissions import can_manage_submission
            if not job.requested_by or not job.requested_by.is_active or not can_manage_submission(
                    SimpleNamespace(user=job.requested_by), submission):
                _finish_job(job_id, {'status': 'failed', 'error': 'Немає доступу до роботи'}, 403)
                return
            teacher = getattr(job.requested_by, 'teacher_profile', None)
            result = evaluate_submission_with_gemini(submission, teacher=teacher, **job.parameters)
            if result.get('status') == 'deferred':
                defer_job(job_id, result.get('retry_after', 2))
                return
            status_code = 200
            if job.kind == 'batch_check':
                submission.refresh_from_db()
                result = {
                    'id': submission.pk,
                    'student_name': submission.get_student_full_name(),
                    'status': result.get('status'),
                    'suggested_grade': (submission.ai_suggested_grade or '—') if result.get('status') == 'success' else '—',
                    'level': submission.ai_score_level or '',
                    'feedback': submission.get_formatted_ai_feedback() or '',
                    'clean_feedback': submission.get_clean_ai_feedback_for_student(),
                    'ai_generated_detected': bool(submission.ai_generated_detected),
                    'ai_generated_percent': submission.ai_generated_percent,
                    'ai_generated_confidence': submission.get_ai_generated_confidence_display() if hasattr(submission, 'get_ai_generated_confidence_display') else submission.ai_generated_confidence,
                    'ai_generated_details': submission.ai_generated_details or '',
                    'is_ai_allowed': bool(submission.is_ai_allowed),
                    'gr_results': submission.get_ai_gr_results_list(),
                    'gr_avg': submission.get_ai_gr_average(),
                    'model_used': submission.ai_model_used,
                    'request_metadata': submission.get_ai_request_metadata(),
                    'format_warning': result.get('format_warning') or '',
                    'error': result.get('error') or submission.ai_error_reason or ''
                }
            _finish_job(job_id, result, status_code)
    except Exception as error:
        logger.error('AI job %s failed (%s)', job_id, type(error).__name__)
        fail_job(job_id, 'ШІ тимчасово недоступний. Спробуйте ще раз.')


def _finish_job(job_id, result, status_code):
    import time
    started = time.monotonic()
    from .ai_metrics import record
    record('result_success', int(status_code < 400 and (result.get('ok') is True or result.get('status') in ('success', 'ok'))))
    success = status_code < 400 and (result.get('ok') is True or result.get('status') in ('success', 'ok'))
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        from .ai_progress import emit_event
        emit_event('completed' if success else 'failed',
                   'Перевірку завершено. Результат збережено.' if success else
                   'Перевірку завершено без оцінки. Перегляньте причини нижче.', job_id=job_id)
        job.status = 'succeeded' if success else 'failed'
        job.result, job.http_status, job.finished_at = result, status_code, timezone.now()
        job.save(update_fields=['status', 'result', 'http_status', 'finished_at'])
        if success and job.kind == 'student_check':
            job.reservations.update(used=True)
        elif not success:
            job.reservations.filter(used=False).update(job=None)
    record('persist_ms', (time.monotonic() - started) * 1000)
