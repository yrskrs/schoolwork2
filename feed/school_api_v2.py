"""Optional journal roster copies and native-ID grades; v1 remains available."""
import datetime as dt
import math

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
import json

from .journal_api import get_authenticated_journal_key
from .models import ClassGroup, Student, Submission, Subject
from school_sync.django_backend import operate
from school_sync.core import SyncError


def authorized(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer ') or not header[7:].strip():
        return None
    key, error = get_authenticated_journal_key(request, 'export')
    if key and key.teacher_id and not key.teacher.user.is_active:
        return None
    return key


def reply(payload, status=200):
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def roster(request):
    key = authorized(request)
    if not key:
        return reply({'error': 'Потрібен чинний Bearer-ключ із правом експорту.'}, 401)
    if request.method == 'POST':
        if not key.can_import_roster:
            return reply({'error': 'Цей ключ не дозволяє передавати списки учнів.'}, 403)
        from school_sync.journal_roster import import_students
        try:
            if len(request.body) > 2 * 1024 * 1024:
                raise SyncError('Список перевищує 2 МБ.')
            payload = json.loads(request.body)
            def receive(engine, store):
                classes = ClassGroup.objects.all()
                if key.teacher_id:
                    classes = classes.filter(teacher=key.teacher)
                owner_key = key.teacher_id or 'admin'
                imports = store.meta.get('journal_imports', {})
                binding_k = f"{owner_key}:{payload.get('journal_instance_id')}:{payload.get('class', {}).get('id')}"
                binding = imports.get(binding_k)
                target_class_id = binding['class_id'] if binding else payload.get('source_class_id')
                if target_class_id:
                    try:
                        target_class_id = int(target_class_id)
                        claimed_ids = {entry['native_id'] for entry in binding['students'].values()} if binding else set()
                        for s_row in payload.get('students', []):
                            if s_row.get('local_id'):
                                claimed_ids.add(int(s_row['local_id']))
                        local_students = [(sid, engine.native.read('student', sid)) for sid in engine.native.students(target_class_id)]
                        local_students = [(sid, s) for sid, s in local_students if s and sid not in claimed_ids]

                        def norm(n):
                            return ' '.join(n.casefold().replace('’', "'").replace('`', "'").split())

                        from collections import Counter
                        local_counts = Counter(norm(s['name']) for sid, s in local_students)
                        payload_counts = Counter(norm(' '.join(part for part in (r.get('last_name'), r.get('first_name'), r.get('middle_name')) if part)) for r in payload.get('students', []))
                        for s_row in payload.get('students', []):
                            if not s_row.get('local_id'):
                                full_name = norm(' '.join(part for part in (s_row.get('last_name'), s_row.get('first_name'), s_row.get('middle_name')) if part))
                                if local_counts.get(full_name) == 1 and payload_counts.get(full_name) == 1:
                                    match_id = next(sid for sid, s in local_students if norm(s['name']) == full_name)
                                    s_row['local_id'] = match_id
                                    claimed_ids.add(match_id)
                    except (ValueError, TypeError, KeyError):
                        pass
                result = import_students(engine, store, payload, owner_key, set(classes.values_list('pk', flat=True)))
                group = ClassGroup.objects.get(pk=result['class_id'])
                if key.teacher_id:
                    key.teacher.classes.add(group)
                name = payload.get('subject', {}).get('name', '').strip()
                if not name or len(name) > 100:
                    raise SyncError('Потрібна назва предмета до 100 символів.')
                subject, _ = Subject.objects.get_or_create(name=name)
                if key.teacher_id:
                    key.teacher.subjects.add(subject)
                result.update(source='schoolwork', instance_id=engine.origin, subject_id=subject.pk)
                return result
            return reply(operate(receive))
        except (SyncError, ValueError, TypeError, KeyError, AttributeError):
            return reply({'error': 'Не вдалося передати список. Перевірте відповідності та права власника ключа; місцеві дані збережено.'}, 400)
    from .outside_students import teaching_classes
    classes = teaching_classes(ClassGroup.objects.all())
    subjects = Subject.objects.all()
    if key.teacher_id:
        classes = classes.filter(teacher=key.teacher)
        subjects = subjects.filter(teacher=key.teacher)
    class_ids = list(classes.values_list('pk', flat=True))
    return reply({'schema_version': 2, 'source': 'schoolwork', 'capabilities': ['journal_roster_import'], 'instance_id': operate(lambda engine, store: engine.origin),
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
        identity = (sub.assignment_id, sub.student_id or str(sub.outside_student_id), sub.class_group_id) if sub.student_id or sub.outside_student_id else ('unmapped', sub.pk)
        winners.setdefault(identity, sub)
    records = []
    for sub in sorted(winners.values(), key=lambda sub: sub.pk):
        if sub.pk <= cursor or not sub.grade or not sub.graded_by_id or not sub.graded_at:
            continue
        # This is a request for another attempt, not a final journal mark.
        # Filter after choosing the latest attempt so an older mark stays hidden.
        if sub.grade.strip().casefold() == 'доопрацювати':
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
            'id': f'schoolwork:assignment:{sub.assignment_id}:class:{sub.class_group_id}:student:{sub.student_id}' if sub.student_id else f'schoolwork:assignment:{sub.assignment_id}:class:{sub.class_group_id}:outside:{sub.outside_student_id}' if sub.outside_student_id else f'schoolwork:unmapped:{sub.pk}',
            'student_id': sub.student_id, 'student_name': sub.student.get_full_name() if sub.student_id else sub.get_student_full_name(),
            'student_key': str(sub.student_id) if sub.student_id else f'outside:{sub.outside_student_id}' if sub.outside_student_id else f'unmapped:{sub.pk}',
            'outside_student_id': str(sub.outside_student_id) if sub.outside_student_id and not sub.student_id else None,
            'class_id': sub.class_group_id,
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
