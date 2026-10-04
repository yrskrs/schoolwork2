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


def job_response(job, is_teacher=False):
    if job.status in ('succeeded', 'failed'):
        result = dict(job.result)
        if job.kind == 'student_check':
            from .ai_context import strip_teacher_criteria
            for field in ('feedback', 'clean_feedback', 'feedback_comment'):
                if field in result:
                    result[field] = strip_teacher_criteria(result[field])
            result.pop('criteria_results', None)
        if job.kind == 'understanding':
            result['is_teacher'] = is_teacher
        return JsonResponse(result, status=job.http_status)
    return JsonResponse({'status': job.status, 'job_id': str(job.pk),
                         'status_url': reverse('ai_job_status', args=[job.pk])}, status=202)


def fail_job(job_id, message):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.status in ('succeeded', 'failed'):
            return
        job.status = 'failed'
        job.result = {'ok': False, 'status': 'failed', 'error': message}
        job.http_status = 503
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'result', 'http_status', 'finished_at'])
        job.reservations.filter(used=False).update(job=None)


def claim_next_job():
    expired = timezone.now() - timedelta(seconds=settings.AI_JOB_TIMEOUT + 60)
    for job_id in AIJob.objects.filter(status='running', started_at__lt=expired).values_list('pk', flat=True):
        fail_job(job_id, 'Перевірку перервано. Спробуйте ще раз; спробу самоперевірки не використано.')
    with transaction.atomic():
        jobs = AIJob.objects.filter(status='queued')
        jobs = jobs.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else jobs.select_for_update()
        job = jobs.first()
        if job:
            job.status = 'running'
            job.started_at = timezone.now()
            job.save(update_fields=['status', 'started_at'])
        return job


def execute_job(job_id):
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        if job.status in ('succeeded', 'failed'):
            return
        if job.status == 'queued':
            job.status, job.started_at = 'running', timezone.now()
            job.save(update_fields=['status', 'started_at'])
    try:
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
            status_code = 200
            if job.kind == 'batch_check':
                submission.refresh_from_db()
                result = {'id': submission.pk, 'student_name': submission.get_student_full_name(),
                          'status': result.get('status'), 'suggested_grade': submission.ai_suggested_grade or '—',
                          'level': submission.ai_score_level or '', 'feedback': submission.get_formatted_ai_feedback() or '',
                          'clean_feedback': submission.get_clean_ai_feedback_for_student(),
                          'gr_results': submission.get_ai_gr_results_list(), 'gr_avg': submission.get_ai_gr_average(),
                          'format_warning': result.get('format_warning') or '', 'error': submission.ai_error_reason or ''}
        _finish_job(job_id, result, status_code)
    except Exception:
        logger.exception('AI job %s failed', job_id)
        fail_job(job_id, 'ШІ тимчасово недоступний. Спробуйте ще раз.')


def _finish_job(job_id, result, status_code):
    success = status_code < 400 and (result.get('ok') is True or result.get('status') in ('success', 'ok'))
    with transaction.atomic():
        job = AIJob.objects.select_for_update().get(pk=job_id)
        job.status = 'succeeded' if success else 'failed'
        job.result, job.http_status, job.finished_at = result, status_code, timezone.now()
        job.save(update_fields=['status', 'result', 'http_status', 'finished_at'])
        if success and job.kind == 'student_check':
            job.reservations.update(used=True)
        elif not success:
            job.reservations.filter(used=False).update(job=None)
