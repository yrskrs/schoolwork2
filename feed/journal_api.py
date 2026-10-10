"""
REST API модуль для двосторонньої інтеграції SchoolNet із зовнішніми та локальними журналами оцінок.

Ендпоінти:
1. GET  /api/v1/journal/grades/  — Витягування оцінок (експорт у журнал з іменами, класами, предметами)
2. GET  /api/v1/journal/roster/  — Отримання списку зареєстрованих учнів та класів
3. POST /api/v1/journal/roster/  — Отримання/синхронізація списків учнів і класів із журналу у SchoolNet
4. POST /api/v1/journal/pull/    — Активне витягування даних учнів за URL локального журналу
"""

import json
import logging
import urllib.request
import urllib.error
from functools import wraps
from typing import Optional, Tuple, Dict, Any, List

from django.http import JsonResponse, HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime, parse_date
from django.views.decorators.csrf import csrf_exempt
from django.db.models import F, Q

from .models import JournalAPIKey, ClassGroup, Student, Submission, Assignment, Subject, Teacher
from .student_matcher import resolve_canonical_student_name, is_same_student_identity
from .student_importer import get_or_create_class_group, parse_student_line

logger = logging.getLogger(__name__)


# ─── АВТЕНТИФІКАЦІЯ API КЛЮЧЕМ ────────────────────────────────────────────────

def get_authenticated_journal_key(request: HttpRequest, required_permission: Optional[str] = None) -> Tuple[Optional[JournalAPIKey], Optional[str]]:
    """
    Витягує та перевіряє API ключ з заголовків (Authorization, X-API-Key)
    або query/body параметрів (?api_key=...).
    """
    raw_token = None

    # 1. Заголовок Authorization: Bearer <key>
    auth_header = request.headers.get('Authorization', '').strip()
    if auth_header:
        if auth_header.lower().startswith('bearer '):
            raw_token = auth_header[7:].strip()
        else:
            raw_token = auth_header

    # 2. Заголовок X-API-Key
    if not raw_token:
        raw_token = request.headers.get('X-API-Key', '').strip()

    # 3. GET / POST параметр api_key
    if not raw_token:
        raw_token = request.GET.get('api_key', '').strip()
    if not raw_token and request.method == 'POST':
        raw_token = request.POST.get('api_key', '').strip()

    if not raw_token:
        return None, "API ключ не надано. Вкажіть заголовок 'Authorization: Bearer <token>' або 'X-API-Key: <token>'."

    key_obj = JournalAPIKey.objects.filter(key=raw_token).first()
    if not key_obj:
        return None, "Недійсний або невідомий API ключ."

    if not key_obj.is_active:
        return None, "API ключ деактивовано адміністратором або вчителем."

    if required_permission == 'export' and not key_obj.can_export_grades:
        return None, "Даний API ключ не має дозволу на вивантаження оцінок (can_export_grades=False)."

    if required_permission == 'import' and not key_obj.can_import_roster:
        return None, "Даний API ключ не має дозволу на імпорт або синхронізацію учнів (can_import_roster=False)."

    # Оновлюємо лічильник запитів та час останнього використання
    try:
        JournalAPIKey.objects.filter(pk=key_obj.pk).update(
            last_used_at=timezone.now(),
            requests_count=F('requests_count') + 1
        )
    except Exception as e:
        logger.warning(f"Failed to update JournalAPIKey stats: {e}")

    return key_obj, None


def require_journal_api_key(permission: Optional[str] = None):
    """Декоратор для перевірки API ключа в ендпоінтах журналу."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request: HttpRequest, *args, **kwargs):
            key_obj, err_msg = get_authenticated_journal_key(request, required_permission=permission)
            if not key_obj:
                return JsonResponse({
                    'status': 'error',
                    'error': 'Unauthorized',
                    'detail': err_msg
                }, status=401 if 'не надано' in (err_msg or '') else 403)
            request.journal_key = key_obj
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


# ─── 1. ЕКСПОРТ ОЦІНОК ДЛЯ ЛОКАЛЬНОГО ЖУРНАЛУ ─────────────────────────────────

@csrf_exempt
@require_journal_api_key(permission='export')
def api_journal_export_grades(request: HttpRequest) -> JsonResponse:
    """
    GET /api/v1/journal/grades/
    Повертає список оцінок учнів для імпорту в локальний журнал.

    Параметри фільтрації (Query params):
      - class_name (або class_id, class): назва класу (наприклад '10-А') або ID
      - subject (або subject_id): назва або ID навчального предмету
      - assignment_id: ID конкретного завдання
      - since: ISO datetime (наприклад '2026-10-01T00:00:00Z') для інкрементальної синхронізації
      - date_from, date_to: діапазон дат у форматі YYYY-MM-DD
      - only_graded: 1/true (за замовчуванням true) — повертати тільки перевірені роботи з оцінкою
      - only_latest: 1/true (за замовчуванням true) — повертати тільки останню актуальну спробу
    """
    if request.method != 'GET':
        return JsonResponse({'status': 'error', 'detail': 'Підтримується тільки GET-запит'}, status=405)

    qs = Submission.objects.select_related('assignment', 'assignment__subject', 'class_group', 'graded_by', 'student')

    # Фільтр по класу
    class_name = request.GET.get('class_name', '').strip()
    class_id = request.GET.get('class_id') or request.GET.get('class')
    if class_id:
        qs = qs.filter(class_group_id=class_id)
    elif class_name:
        qs = qs.filter(Q(class_group__name__iexact=class_name) | Q(class_group__name__iexact=class_name.replace('-', '')))

    # Фільтр по предмету
    subject_param = request.GET.get('subject', '').strip()
    subject_id = request.GET.get('subject_id')
    if subject_id:
        qs = qs.filter(assignment__subject_id=subject_id)
    elif subject_param:
        qs = qs.filter(Q(assignment__subject__name__iexact=subject_param) | Q(assignment__subject__name__icontains=subject_param))

    # Фільтр по завданню
    assignment_id = request.GET.get('assignment_id')
    if assignment_id:
        qs = qs.filter(assignment_id=assignment_id)

    # Інкрементальна синхронізація через 'since'
    since_param = request.GET.get('since', '').strip()
    if since_param:
        dt_since = parse_datetime(since_param)
        if dt_since:
            qs = qs.filter(Q(submitted_at__gte=dt_since) | Q(graded_at__gte=dt_since))

    # Діапазон дат
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    if date_from:
        d_from = parse_date(date_from)
        if d_from:
            qs = qs.filter(Q(graded_at__date__gte=d_from) | Q(submitted_at__date__gte=d_from))
    if date_to:
        d_to = parse_date(date_to)
        if d_to:
            qs = qs.filter(Q(graded_at__date__lte=d_to) | Q(submitted_at__date__lte=d_to))

    # Тільки оцінені (за замовчуванням так)
    only_graded = request.GET.get('only_graded', 'true').strip().lower() in ('1', 'true', 'yes')
    if only_graded:
        qs = qs.exclude(grade__isnull=True).exclude(grade__exact='')

    # Тільки останні актуальні здачі учнів (без старих спроб)
    only_latest = request.GET.get('only_latest', 'true').strip().lower() in ('1', 'true', 'yes')
    if only_latest:
        qs = qs.filter(is_latest_attempt=True, primary_submission__isnull=True)

    # Якщо ключ прив'язано до конкретного вчителя і запитано my_only
    if request.GET.get('my_only', '').lower() in ('1', 'true') and getattr(request, 'journal_key', None) and request.journal_key.teacher:
        qs = qs.filter(teacher=request.journal_key.teacher)

    qs = qs.order_by('-submitted_at')

    # Ліміт вибірки
    try:
        limit = min(max(1, int(request.GET.get('limit', 500))), 2000)
    except (ValueError, TypeError):
        limit = 500

    submissions_list = list(qs[:limit])

    grades_data = []
    for sub in submissions_list:
        student_obj = sub.student
        coauthors = sub.get_all_student_identities_display() if hasattr(sub, 'get_all_student_identities_display') else []
        graded_by_name = None
        if sub.graded_by:
            graded_by_name = sub.graded_by.get_full_name() or sub.graded_by.username

        grades_data.append({
            'submission_id': sub.id,
            'student_id': student_obj.id if student_obj else None,
            'student': {
                'id': student_obj.id if student_obj else None,
                'last_name': sub.last_name,
                'first_name': sub.first_name,
                'full_name': f"{sub.last_name} {sub.first_name}".strip(),
            },
            'class': {
                'id': sub.class_group_id,
                'name': sub.class_group.name if sub.class_group else '',
                'grade': sub.class_group.grade if sub.class_group else None,
                'letter': sub.class_group.letter if sub.class_group else '',
            },
            'assignment': {
                'id': sub.assignment_id,
                'title': sub.assignment.title if sub.assignment else '',
                'subject': sub.assignment.subject.name if (sub.assignment and sub.assignment.subject) else '',
                'subject_id': sub.assignment.subject_id if sub.assignment else None,
                'max_grade': getattr(sub.assignment, 'max_grade', 12) if sub.assignment else 12,
                'due_date': sub.assignment.due_date.isoformat() if (sub.assignment and sub.assignment.due_date) else None,
            },
            'grade': sub.grade or '',
            'ai_suggested_grade': sub.ai_suggested_grade or '',
            'teacher_comment': sub.teacher_comment or '',
            'submitted_at': sub.submitted_at.isoformat() if sub.submitted_at else None,
            'graded_at': sub.graded_at.isoformat() if sub.graded_at else None,
            'graded_by': graded_by_name,
            'is_group_work': sub.is_group_work,
            'coauthors': coauthors,
        })

    return JsonResponse({
        'status': 'success',
        'count': len(grades_data),
        'server_time': timezone.now().isoformat(),
        'grades': grades_data
    })


# ─── 2. РОСТЕР УЧНІВ ТА КЛАСІВ (GET / POST) ───────────────────────────────────

@csrf_exempt
@require_journal_api_key()
def api_journal_roster(request: HttpRequest) -> JsonResponse:
    """
    /api/v1/journal/roster/
    GET:  Отримати список зареєстрованих класів та учнів.
    POST: Синхронізувати (імпортувати) список учнів та класів з локального журналу.
    """
    if request.method == 'GET':
        return _get_roster_response(request)
    elif request.method == 'POST':
        # Перевірка дозволу на імпорт
        key = getattr(request, 'journal_key', None)
        if key and not key.can_import_roster:
            return JsonResponse({
                'status': 'error',
                'detail': 'Даний API ключ не має дозволу на імпорт учнів (can_import_roster=False).'
            }, status=403)
        return _sync_roster_from_payload(request)
    else:
        return JsonResponse({'status': 'error', 'detail': 'Метод не підтримується'}, status=405)


def _get_roster_response(request: HttpRequest) -> JsonResponse:
    """Обробка GET-запиту: повертає перелік класів та учнів школи."""
    class_name = request.GET.get('class_name', '').strip()
    class_id = request.GET.get('class_id') or request.GET.get('class')

    classes_qs = ClassGroup.objects.all().order_by('grade', 'letter', 'name')
    if class_id:
        classes_qs = classes_qs.filter(id=class_id)
    elif class_name:
        classes_qs = classes_qs.filter(Q(name__iexact=class_name) | Q(name__iexact=class_name.replace('-', '')))

    result_classes = []
    total_students_count = 0

    for cg in classes_qs:
        students_qs = Student.objects.filter(class_group=cg).order_by('last_name', 'first_name')
        st_list = []
        for s in students_qs:
            st_list.append({
                'id': s.id,
                'last_name': s.last_name,
                'first_name': s.first_name,
                'full_name': s.get_full_name(),
            })
        total_students_count += len(st_list)
        result_classes.append({
            'id': cg.id,
            'name': cg.name,
            'grade': cg.grade,
            'letter': cg.letter,
            'students_count': len(st_list),
            'students': st_list,
        })

    return JsonResponse({
        'status': 'success',
        'count_classes': len(result_classes),
        'count_students': total_students_count,
        'server_time': timezone.now().isoformat(),
        'classes': result_classes
    })


def _sync_roster_from_payload(request: HttpRequest) -> JsonResponse:
    """Обробка POST-запиту: приймає JSON-список класів та учнів і зберігає в БД."""
    payload = {}
    if request.body:
        try:
            payload = json.loads(request.body.decode('utf-8'))
        except Exception:
            try:
                payload = json.loads(request.body.decode('latin-1'))
            except Exception:
                return JsonResponse({
                    'status': 'error',
                    'detail': 'Помилка розбору JSON у тілі запиту.'
                }, status=400)

    # Підтримка передачі як масиву безпосередньо: [{"last_name": ...}, ...]
    if isinstance(payload, list):
        payload = {'students': payload}

    teacher = None
    key = getattr(request, 'journal_key', None)
    if key and key.teacher:
        teacher = key.teacher

    sync_result = sync_roster_data(payload, teacher=teacher)

    return JsonResponse({
        'status': 'success',
        'message': 'Синхронізацію списків успішно завершено',
        'created_students': sync_result['created_students'],
        'updated_students': sync_result['updated_students'],
        'total_processed': sync_result['total_processed'],
        'created_classes': sync_result['created_classes'],
        'errors': sync_result['errors']
    })


def sync_roster_data(payload: Dict[str, Any], teacher: Optional[Teacher] = None) -> Dict[str, Any]:
    """
    Універсальна функція синхронізації списків класів та учнів.
    Підтримує 3 формати:
    1. {"classes": [{"name": "10-А", "students": [{"last_name": "...", "first_name": "..."}]}]}
    2. {"students": [{"last_name": "...", "first_name": "...", "class_name": "10-А"}]}
    3. {"students_text": "Шевченко Тарас, 10-А\\nФранко Іван, 10-А"}
    """
    created_students = 0
    updated_students = 0
    total_processed = 0
    created_classes = set()
    errors = []

    # ── Формат 1: Ієрархічний об'єкт класів ────────────────────────────────────
    classes_list = payload.get('classes')
    if isinstance(classes_list, list):
        for c_data in classes_list:
            if not isinstance(c_data, dict):
                continue
            c_name = str(c_data.get('name') or c_data.get('class_name') or '').strip()
            if not c_name:
                continue
            cg, was_created = get_or_create_class_group(c_name, teacher=teacher)
            if was_created:
                created_classes.add(cg.name)

            st_entries = c_data.get('students') or []
            if isinstance(st_entries, list):
                for st in st_entries:
                    total_processed += 1
                    res = _upsert_student(st, cg)
                    if res == 'created':
                        created_students += 1
                    elif res == 'updated':
                        updated_students += 1

    # ── Формат 2: Плоский список учнів ─────────────────────────────────────────
    flat_students = payload.get('students')
    if isinstance(flat_students, list) and not classes_list:
        for st in flat_students:
            if not isinstance(st, dict):
                continue
            total_processed += 1
            raw_c = str(st.get('class_name') or st.get('class') or st.get('class_group') or '').strip()
            if not raw_c:
                errors.append(f"Учень '{st.get('last_name')} {st.get('first_name')}': не вказано клас.")
                continue

            cg, was_created = get_or_create_class_group(raw_c, teacher=teacher)
            if was_created:
                created_classes.add(cg.name)

            res = _upsert_student(st, cg)
            if res == 'created':
                created_students += 1
            elif res == 'updated':
                updated_students += 1

    # ── Формат 3: Текстовий список учнів (students_text / raw_text) ───────────
    raw_text = payload.get('students_text') or payload.get('raw_text') or ''
    if raw_text and isinstance(raw_text, str) and not classes_list and not flat_students:
        for line in raw_text.splitlines():
            line_clean = line.strip()
            if not line_clean:
                continue
            parsed = parse_student_line(line_clean)
            if not parsed:
                continue
            total_processed += 1
            raw_c = parsed.get('raw_class_name') or ''
            if not raw_c:
                errors.append(f"Рядок '{line_clean}': не вдалося визначити клас.")
                continue
            cg, was_created = get_or_create_class_group(raw_c, teacher=teacher)
            if was_created:
                created_classes.add(cg.name)

            res = _upsert_student({
                'last_name': parsed['last_name'],
                'first_name': parsed['first_name']
            }, cg)
            if res == 'created':
                created_students += 1
            elif res == 'updated':
                updated_students += 1

    return {
        'created_students': created_students,
        'updated_students': updated_students,
        'total_processed': total_processed,
        'created_classes': sorted(list(created_classes)),
        'errors': errors
    }


def _upsert_student(st_data: Dict[str, Any], class_group: ClassGroup) -> Optional[str]:
    """Створює або оновлює учня в зазначеному класі без дублювання."""
    raw_name = str(st_data.get('full_name') or '').strip()
    ln = str(st_data.get('last_name') or '').strip()
    fn = str(st_data.get('first_name') or '').strip()

    if raw_name and (not ln or not fn):
        resolved_ln, resolved_fn = resolve_canonical_student_name(raw_name)
        if not ln:
            ln = resolved_ln
        if not fn:
            fn = resolved_fn

    if not ln and not fn:
        return None

    # Пошук існуючого учня в класі
    existing_students = list(Student.objects.filter(class_group=class_group))
    matched_student = None
    for s in existing_students:
        if is_same_student_identity(ln, fn, s.last_name, s.first_name):
            matched_student = s
            break

    if matched_student:
        updated = False
        # Якщо нове ім'я більш інформативне
        if fn and not matched_student.first_name:
            matched_student.first_name = fn
            updated = True
        if updated:
            matched_student.save(update_fields=['first_name', 'updated_at'])
            matched_student.sync_submissions()
            return 'updated'
        return 'existing'
    else:
        new_s = Student.objects.create(
            last_name=ln or "Учень",
            first_name=fn,
            class_group=class_group
        )
        new_s.sync_submissions()
        return 'created'


# ─── 3. АКТИВНЕ ВИТЯГУВАННЯ РОСТЕРУ ЗА URL ЛОКАЛЬНОГО ЖУРНАЛУ ──────────────────

@csrf_exempt
def api_journal_pull_from_external(request: HttpRequest) -> JsonResponse:
    """
    POST /api/v1/journal/pull/
    Дозволяє викликати активне опитування зовнішнього локального журналу:
    SchoolNet надсилає GET-запит на journal_url, отримує JSON зі списком учнів
    та автоматично синхронізує їх.
    """
    # Доступ дозволено або через API-ключ, або автентифікованому вчителю
    teacher = None
    if request.user.is_authenticated and hasattr(request.user, 'teacher_profile'):
        teacher = request.user.teacher_profile
    else:
        key_obj, err_msg = get_authenticated_journal_key(request, required_permission='import')
        if not key_obj:
            return JsonResponse({'status': 'error', 'detail': err_msg}, status=401)
        if key_obj.teacher:
            teacher = key_obj.teacher

    target_url = request.POST.get('journal_url', '').strip()
    if not target_url and request.body:
        try:
            body_json = json.loads(request.body.decode('utf-8'))
            target_url = str(body_json.get('journal_url', '')).strip()
        except Exception:
            pass

    if not target_url and getattr(request, 'journal_key', None) and request.journal_key.local_journal_url:
        target_url = request.journal_key.local_journal_url

    if not target_url:
        return JsonResponse({
            'status': 'error',
            'detail': 'Вкажіть journal_url (наприклад http://127.0.0.1:8000/api/journal/students).'
        }, status=400)

    try:
        req = urllib.request.Request(
            target_url,
            headers={'User-Agent': 'SchoolNet-Journal-Sync/1.0', 'Accept': 'application/json'}
        )
        with urllib.request.urlopen(req, timeout=8) as response:
            resp_bytes = response.read()
            data = json.loads(resp_bytes.decode('utf-8'))
    except urllib.error.URLError as e:
        return JsonResponse({
            'status': 'error',
            'detail': f'Не вдалося підключитися до локального журналу за адресою {target_url}: {e.reason}'
        }, status=502)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'detail': f'Помилка запиту або відповіді локального журналу: {str(e)}'
        }, status=500)

    if not isinstance(data, (dict, list)):
        return JsonResponse({
            'status': 'error',
            'detail': 'Локальний журнал повернув дані у непідтримуваному форматі (очікується JSON-обʼєкт або список).'
        }, status=422)

    payload = data if isinstance(data, dict) else {'students': data}
    sync_result = sync_roster_data(payload, teacher=teacher)

    return JsonResponse({
        'status': 'success',
        'message': f"Дані успішно витягнуто з {target_url}!",
        'created_students': sync_result['created_students'],
        'updated_students': sync_result['updated_students'],
        'total_processed': sync_result['total_processed'],
        'created_classes': sync_result['created_classes'],
        'errors': sync_result['errors']
    })
