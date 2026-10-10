"""Read-only native-ID API. v1 remains available for existing clients."""
import datetime as dt
import math

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from .journal_api import get_authenticated_journal_key
from .models import ClassGroup, Student, Submission, Subject


def authorized(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or not header[7:].strip():
        return None
    key, error = get_authenticated_journal_key(request, 'export')
    return key


def reply(payload, status=200):
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
def roster(request):
    key = authorized(request)
    if not key:
        return reply({'error': 'Потрібен чинний Bearer-ключ із правом експорту.'}, 401)
    classes = ClassGroup.objects.all()
    subjects = Subject.objects.all()
    if key.teacher_id:
        classes = classes.filter(teacher=key.teacher)
        subjects = subjects.filter(teacher=key.teacher)
    class_ids = list(classes.values_list('pk', flat=True))
    return reply({'schema_version': 2, 'source': 'schoolwork',
        'classes': list(classes.values('id', 'name')),
        'subjects': list(subjects.values('id', 'name')),
        'students': [{'id': s.pk, 'class_id': s.class_group_id, 'name': s.get_full_name()}
            for s in Student.objects.filter(class_group_id__in=class_ids).order_by('pk')]})


@require_GET
def grades(request):
    key = authorized(request)
    if not key:
        return reply({'error': 'Потрібен чинний Bearer-ключ із правом експорту.'}, 401)
    try:
        class_id = int(request.GET['class_id'])
        subject_id = int(request.GET['subject_id'])
        cursor = int(request.GET.get('cursor', '0'))
        start = dt.date.fromisoformat(request.GET['date_from'])
        end = dt.date.fromisoformat(request.GET['date_to'])
        if class_id < 1 or subject_id < 1 or cursor < 0 or start > end:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return reply({'error': 'Потрібні коректні ID класу, предмета, період і курсор.'}, 400)
    qs = Submission.objects.filter(class_group_id=class_id, assignment__subject_id=subject_id,
        assignment__classes__id=class_id, is_latest_attempt=True).select_related('assignment', 'student')
    if key.teacher_id:
        qs = qs.filter(assignment__teacher_id=key.teacher_id)
    if qs.count() > 100000:
        return reply({'error': 'Забагато результатів у цьому контексті.'}, 422)
    # Pick the current attempt before filtering final grades: an unfinished newer
    # attempt must not resurrect an older mark. Preserve explicit coauthor IDs.
    winners = {}
    for sub in qs.order_by('-submitted_at', '-pk').iterator():
        identity = (sub.assignment_id, sub.student_id, sub.class_group_id) if sub.student_id else ('unmapped', sub.pk)
        winners.setdefault(identity, sub)
    records = []
    for sub in sorted(winners.values(), key=lambda sub: sub.pk):
        if sub.pk <= cursor or not sub.grade or not sub.graded_by_id or not sub.graded_at:
            continue
        date = sub.effective_grade_date
        if not start <= date <= end:
            continue
        try:
            points = float(sub.grade)
            if not math.isfinite(points) or not 0 <= points <= 12:
                points = 0
        except (ValueError, TypeError):
            points = 0
        records.append((sub.pk, {
            'source': 'schoolwork',
            'id': f'schoolwork:assignment:{sub.assignment_id}:class:{sub.class_group_id}:student:{sub.student_id}' if sub.student_id else f'schoolwork:unmapped:{sub.pk}',
            'student_id': sub.student_id, 'class_id': sub.class_group_id,
            'subject_id': sub.assignment.subject_id, 'work_id': sub.assignment_id,
            'title': sub.assignment.title, 'attempt_id': sub.pk,
            'points': points, 'max_points': 12, 'value': sub.grade, 'grading_scale': 12,
            'date': date.isoformat(), 'status': 'final',
            'updated_at': sub.graded_at.isoformat(),
            'is_ai': bool(sub.student_ai_accepted), 'approved_by_teacher': True,
        }))
        if len(records) == 501:
            break
    page = records[:500]
    return reply({'schema_version': 2, 'source': 'schoolwork', 'grades': [row for pk, row in page],
        'next_cursor': page[-1][0] if len(records) > 500 else None})
