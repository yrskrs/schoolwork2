
def _get_database_size_display():
    try:
        from django.conf import settings
        db_path = settings.DATABASES['default'].get('NAME')
        if db_path and os.path.exists(str(db_path)):
            sz = os.path.getsize(str(db_path))
            if sz > 1024 * 1024:
                return f"{sz / (1024 * 1024):.2f} MB"
            return f"{sz / 1024:.1f} KB"
    except Exception:
        pass
    return "—"
"""
Views (представлення) для SchoolNet.

Публічна частина:
    - index          — стрічка завдань з фільтрацією по класу
    - assignment_detail — детальна сторінка/AJAX для модального вікна

Панель вчителя (захищена сесією):
    - teacher_login      — форма входу
    - teacher_logout     — вихід
    - teacher_dashboard  — головна панель (список завдань)
    - assignment_create  — створення нового завдання
    - assignment_edit    — редагування завдання
    - assignment_delete  — видалення завдання
    - assignment_duplicate — дублювання завдання
    - assignment_archive — архівування завдання
    - teacher_profile    — редагування профілю
    - publish_scheduled  — AJAX: перевірка та публікація відкладених
"""

import os
import re
import csv
import json
import zipfile
import io
import mimetypes
from datetime import datetime, timedelta


from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse, HttpResponse, FileResponse
from django.utils import timezone
from django.db.models import Q
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from django.views.decorators.http import require_POST
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt


from .models import (
    Assignment, AssignmentFile, AssignmentLink, AssignmentYouTubeLink,
    Teacher, ClassGroup, Student, Subject,
    Submission, SubmissionComment, SubmissionActivityLog, School, log_submission_activity,
    AISettings, DEFAULT_NUS_SYSTEM_PROMPT, DEFAULT_NUS_GR_SYSTEM_PROMPT, AICriteriaPreset, DEFAULT_TRADITIONAL_SYSTEM_PROMPT,
    BellSchedule, TeacherLessonSchedule, AssignmentScheduleTarget, SystemNotification,
    AssignmentRescheduleLog
)

from .forms import (
    AssignmentForm, TeacherLoginForm, ClassSelectForm,
    TeacherProfileForm, SubjectForm, ClassGroupForm,
    TeacherCreateForm, PasswordResetForm, SubmissionForm,
    StudentForm, StudentImportForm, FirstRunSetupForm
)
from .search import search_assignments
from .fuzzy_search import fuzzy_search_submissions
from .utils import (
    convert_docx_to_html, convert_xlsx_to_html, convert_pptx_to_html,
    convert_odt_to_html, convert_ods_to_html, convert_odp_to_html,
    get_archive_content, get_file_type_info, parse_embed_url
)
from .duplicate_detector import check_submission_duplicates
from .access_utils import convert_access_to_html



# ═══════════════════════════════════════════════════════════════════════════════
# ДОПОМІЖНІ ФУНКЦІЇ
# ═══════════════════════════════════════════════════════════════════════════════

def get_teacher_or_none(request):
    """Повертає профіль вчителя або None якщо не авторизовано."""
    if request.user.is_authenticated:
        try:
            return request.user.teacher_profile
        except Teacher.DoesNotExist:
            pass
    return None


def teacher_required(view_func):
    """Декоратор: перевіряє чи є активна сесія вчителя."""
    def wrapper(request, *args, **kwargs):
        teacher = get_teacher_or_none(request)
        if not teacher:
            messages.warning(request, 'Необхідно увійти як вчитель.')
            return redirect('teacher_login')
        return view_func(request, *args, **kwargs)
    wrapper.__name__ = view_func.__name__
    return wrapper


def auto_archive_expired_assignments():
    """
    Автоматично архівує всі опубліковані завдання, яким понад 14 днів з моменту публікації або розархівації.
    """
    from datetime import timedelta
    cutoff = timezone.now() - timedelta(days=14)
    # Завдання без розархівації — рахуємо від первинної дати публікації / створення
    q_normal = Q(unarchived_at__isnull=True) & (
        Q(published_at__lte=cutoff) | (Q(published_at__isnull=True) & Q(created_at__lte=cutoff))
    )
    # Завдання з розархівацією — рахуємо 14 днів від дати розархівації
    q_unarchived = Q(unarchived_at__isnull=False) & Q(unarchived_at__lte=cutoff)

    expired_count = Assignment.objects.filter(
        status=Assignment.STATUS_PUBLISHED
    ).filter(
        q_normal | q_unarchived
    ).update(
        status=Assignment.STATUS_ARCHIVED
    )
    return expired_count


def get_visible_assignments(class_group_id=None):
    """
    Повертає QuerySet опублікованих завдань.
    - Автоматично архівує завдання старше 14 днів.
    - Автоматично публікує відкладені, якщо час настав.
    """
    # Автоматична архівація завдань, старших за 14 днів
    auto_archive_expired_assignments()

    # Оновлюємо відкладені публікації
    Assignment.objects.filter(
        status=Assignment.STATUS_SCHEDULED,
        scheduled_at__lte=timezone.now()
    ).update(
        status=Assignment.STATUS_PUBLISHED,
        published_at=timezone.now()
    )

    qs = Assignment.objects.filter(
        status=Assignment.STATUS_PUBLISHED
    ).select_related(
        'teacher', 'subject'
    ).prefetch_related(
        'classes', 'files'
    )

    if class_group_id:
        qs = qs.filter(classes__id=class_group_id)

    return qs.order_by('-published_at')


# ═══════════════════════════════════════════════════════════════════════════════
# ПУБЛІЧНА ЧАСТИНА — СТРІЧКА НОВИН
# ═══════════════════════════════════════════════════════════════════════════════

def get_calendar_context(year=None, month=None, selected_date_str=None, class_group_id=None, subject_id=None):
    """
    Формує матрицю днів та кількість завдань для віджета Google Calendar.
    """
    import calendar
    from datetime import timedelta, datetime
    from django.utils import timezone
    from collections import Counter

    today = timezone.localtime(timezone.now()).date()

    if not year or not month:
        if selected_date_str:
            try:
                d = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
                year, month = d.year, d.month
            except Exception:
                year, month = today.year, today.month
        else:
            year, month = today.year, today.month
    else:
        try:
            year = int(year)
            month = int(month)
        except Exception:
            year, month = today.year, today.month

    # Назви місяців українською
    uk_months = [
        '', 'Січень', 'Лютий', 'Березень', 'Квітень', 'Травень', 'Червень',
        'Липень', 'Серпень', 'Вересень', 'Жовтень', 'Листопад', 'Грудень'
    ]

    # Навігація по місяцях
    if month <= 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month >= 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    # Отримуємо всі опубліковані завдання для підрахунку на календарі
    visible_qs = get_visible_assignments(class_group_id)
    if subject_id:
        visible_qs = visible_qs.filter(subject__id=subject_id)

    task_counts = Counter()
    for a in visible_qs:
        if a.published_at:
            p_date = timezone.localtime(a.published_at).date()
            task_counts[p_date.isoformat()] += 1
        if a.unarchived_at:
            u_date = timezone.localtime(a.unarchived_at).date()
            p_date = timezone.localtime(a.published_at).date() if a.published_at else None
            if u_date != p_date:
                task_counts[u_date.isoformat()] += 1

    cal = calendar.Calendar(firstweekday=0)  # Понеділок (0)
    month_weeks = cal.monthdatescalendar(year, month)

    fourteen_days_ago = today - timedelta(days=14)

    weeks_data = []
    for week in month_weeks:
        week_days = []
        for d in week:
            d_str = d.isoformat()
            c = task_counts.get(d_str, 0)
            week_days.append({
                'date': d,
                'day_num': d.day,
                'date_str': d_str,
                'is_current_month': (d.month == month),
                'is_today': (d == today),
                'is_selected': (d_str == selected_date_str),
                'is_in_14_days': (fourteen_days_ago <= d <= today),
                'tasks_count': c,
                'has_tasks': c > 0,
            })
        weeks_data.append(week_days)

    selected_date_display = ""
    if selected_date_str:
        try:
            dt = datetime.strptime(selected_date_str, '%Y-%m-%d').date()
            uk_weekdays = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', 'Пʼятниця', 'Субота', 'Неділя']
            w_name = uk_weekdays[dt.weekday()]
            selected_date_display = f"{w_name}, {dt.strftime('%d.%m.%Y')}"
        except Exception:
            pass

    return {
        'year': year,
        'month': month,
        'month_name': uk_months[month],
        'prev_year': prev_year,
        'prev_month': prev_month,
        'next_year': next_year,
        'next_month': next_month,
        'weeks': weeks_data,
        'selected_date': selected_date_str,
        'selected_date_display': selected_date_display,
        'today_str': today.isoformat(),
    }


def _parse_filter_params(request):
    """Допоміжна функція: парсить та очищує параметри фільтрації класу, предмета, дати та пошуку."""
    class_group_id = None
    if 'class' in request.GET:
        c_val = str(request.GET.get('class', '')).strip()
        if c_val in ('', 'all', '0', 'none', 'None'):
            class_group_id = None
            if 'selected_class' in request.session:
                del request.session['selected_class']
        else:
            try:
                class_group_id = int(c_val)
                request.session['selected_class'] = class_group_id
            except (ValueError, TypeError):
                class_group_id = None
                if 'selected_class' in request.session:
                    del request.session['selected_class']
    elif 'clear' in request.GET:
        class_group_id = None
        if 'selected_class' in request.session:
            del request.session['selected_class']
    else:
        class_group_id = request.session.get('selected_class')

    subject_id = None
    if 'subject' in request.GET:
        s_val = str(request.GET.get('subject', '')).strip()
        if s_val not in ('', 'all', '0', 'none', 'None'):
            try:
                subject_id = int(s_val)
            except (ValueError, TypeError):
                subject_id = None

    date_str = None
    if 'date' in request.GET:
        d_val = str(request.GET.get('date', '')).strip()
        if d_val not in ('', 'all', '0', 'none', 'None'):
            try:
                from datetime import datetime
                datetime.strptime(d_val, '%Y-%m-%d')
                date_str = d_val
            except (ValueError, TypeError):
                date_str = None

    query = request.GET.get('q', '').strip()
    return class_group_id, subject_id, date_str, query


def index(request):
    """
    Головна сторінка — стрічка завдань.
    Підтримує фільтрацію по класу, предмету, даті (календар) та нечіткий пошук.
    """
    class_group_id, subject_id, date_str, query = _parse_filter_params(request)

    assignments = get_visible_assignments(class_group_id)

    if subject_id:
        assignments = assignments.filter(subject__id=subject_id)

    if date_str:
        from datetime import datetime
        dt_val = datetime.strptime(date_str, '%Y-%m-%d').date()
        assignments = assignments.filter(
            Q(published_at__date=dt_val) | Q(unarchived_at__date=dt_val)
        )

    if query:
        assignments = search_assignments(assignments, query)

    # Пагінація (10 завдань на сторінку)
    paginator = Paginator(assignments, 10)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    # Дані для UI
    all_classes = ClassGroup.objects.filter(teacher__isnull=False).distinct().order_by('grade', 'letter')
    all_subjects = Subject.objects.filter(teacher__isnull=False).distinct()
    has_multiple_subjects = all_subjects.count() > 1
    selected_class = None
    if class_group_id:
        selected_class = ClassGroup.objects.filter(id=class_group_id).first()

    cal_year = request.GET.get('cal_year')
    cal_month = request.GET.get('cal_month')
    calendar_ctx = get_calendar_context(
        year=cal_year,
        month=cal_month,
        selected_date_str=date_str,
        class_group_id=class_group_id,
        subject_id=subject_id
    )

    context = {
        'assignments': page_obj.object_list,
        'page_obj': page_obj,
        'all_classes': all_classes,
        'all_subjects': all_subjects,
        'has_multiple_subjects': has_multiple_subjects,
        'selected_class': selected_class,
        'selected_class_id': class_group_id,
        'selected_subject_id': subject_id,
        'selected_date': date_str,
        'selected_date_display': calendar_ctx.get('selected_date_display', ''),
        'calendar': calendar_ctx,
        'query': query,
        'form': ClassSelectForm(initial={'class_group': class_group_id}),
    }
    return render(request, 'feed/index.html', context)



def assignment_detail(request, pk):
    """
    Детальний перегляд завдання.
    Якщо AJAX — повертає JSON для модального вікна.
    Інакше — повна сторінка.
    """
    assignment = get_object_or_404(
        Assignment.objects.select_related('teacher', 'subject').prefetch_related('classes', 'files', 'additional_links', 'youtube_links'),
        pk=pk,
        status=Assignment.STATUS_PUBLISHED
    )

    # Фіксація перегляду завдання (1 раз на годину з 1 комп'ютера, перегляди вчителя не рахуються)
    assignment.record_view(request)

    is_teacher = bool(
        request.user.is_authenticated and
        (hasattr(request.user, 'teacher_profile') or request.user.is_superuser)
    )
    can_edit = bool(
        is_teacher and
        (request.user.is_superuser or (hasattr(request.user, 'teacher_profile') and request.user.teacher_profile == assignment.teacher))
    )

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        # AJAX-відповідь для модального вікна
        files_data = []
        for f in assignment.files.all():
            files_data.append({
                'id': f.id,
                'name': f.original_name,
                'url': f.file.url,
                'download_url': f'/assignment/file/{f.id}/download/',
                'type': f.get_file_type(),
                'icon': f.get_file_icon(),
                'size': f.get_size_display(),
                'viewable': f.is_browser_viewable(),
                'extension': f.get_extension(),
            })

        # Формуємо рядок класів
        classes_str = ', '.join(
            str(c) for c in assignment.classes.all()
        )

        # Додаткові посилання
        extra_links = [
            {'url': lnk.url, 'label': lnk.label or lnk.url}
            for lnk in assignment.additional_links.all()
        ]

        data = {
            'id': assignment.id,
            'title': assignment.title,
            'description': assignment.description,
            'teacher': assignment.teacher.full_name,
            'teacher_avatar_url': assignment.teacher.avatar_image.url if assignment.teacher.avatar_image else '',
            'teacher_avatar_color': assignment.teacher.avatar_color,
            'subject': str(assignment.subject) if assignment.subject else '',
            'subject_color': assignment.subject.color if assignment.subject else '#6366f1',
            'classes': classes_str,
            'is_individual': assignment.is_individual,
            'student_name': assignment.student_name,
            'student_display': assignment.student_display,
            'views_count': assignment.views_count,
            'is_teacher': is_teacher,
            'published_at': assignment.published_at.strftime('%d.%m.%Y %H:%M') if assignment.published_at else '',
            'relative_published_at': assignment.relative_published_display,
            'is_today': assignment.is_published_today,
            'is_yesterday': assignment.is_published_yesterday,
            'days_ago': assignment.days_ago,
            'unarchived_at': assignment.unarchived_at.strftime('%d.%m.%Y %H:%M') if assignment.unarchived_at else '',
            'unarchived_display': assignment.unarchived_display,
            'due_date': assignment.due_date.strftime('%d.%m.%Y') if assignment.due_date else '',
            'link_url': assignment.link_url,
            'link_label': assignment.link_label or assignment.link_url,
            'youtube_url': assignment.youtube_embed_url,
            'youtube_watch_url': assignment.youtube_watch_url,
            'youtube_videos': assignment.all_youtube_videos,
            'extra_links': extra_links,
            'files': files_data,
            'can_edit': bool(
                is_teacher and
                (request.user.is_superuser or (hasattr(request.user, 'teacher_profile') and request.user.teacher_profile == assignment.teacher))
            ),
        }
        return JsonResponse(data)

    # Інші завдання цього класу або вчителя для сайдбару
    related_assignments = Assignment.objects.filter(
        status=Assignment.STATUS_PUBLISHED
    ).exclude(pk=assignment.pk).filter(
        Q(classes__in=assignment.classes.all()) | Q(teacher=assignment.teacher)
    ).distinct().select_related('teacher', 'subject')[:5]

    submissions_count = Submission.objects.filter(assignment=assignment).count()

    # Підготовка матеріалів для вбудованого перегляду у тій же вкладці
    files_with_preview = []
    archive_files = ['.zip', '.rar', '.7z', '.tar', '.gz', '.tgz']
    office_preview = ['.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.odt', '.ods', '.odp']
    text_files = ['.txt', '.text', '.log', '.csv', '.md', '.py', '.js', '.html', '.css', '.json', '.sh', '.cpp', '.c', '.java', '.xml', '.sql', '.pas', '.cs', '.php', '.rb', '.go', '.rs', '.kt', '.swift', '.ts']

    for af in assignment.files.all():
        ext = af.get_extension()
        file_path = af.file.path if af.file else None
        preview_type = 'download_only'
        html_preview = None
        text_preview = None
        error_preview = None
        archive_items = None
        slide_urls = None
        pdf_preview_url = None

        if file_path and os.path.exists(file_path):
            if ext in ['.docx', '.doc']:
                preview_type = 'office'
                html_preview, error_preview = convert_docx_to_html(file_path)
            elif ext in ['.xlsx', '.xls']:
                preview_type = 'office'
                html_preview, error_preview = convert_xlsx_to_html(file_path)
            elif ext in ['.pptx', '.ppt', '.odp']:
                preview_type = 'office'
                html_preview, error_preview = convert_pptx_to_html(file_path)
                slide_urls, pdf_preview_url = get_presentation_slides(af.id, file_path)
            elif ext == '.odt':
                preview_type = 'office'
                html_preview, error_preview = convert_odt_to_html(file_path)
            elif ext == '.ods':
                preview_type = 'office'
                html_preview, error_preview = convert_ods_to_html(file_path)
            elif ext in ['.txt', '.text', '.log', '.csv']:
                preview_type = 'text'
                # Читання з мультикодуванням
                encs = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5']
                for enc in encs:
                    try:
                        with open(file_path, 'r', encoding=enc) as f_read:
                            text_preview = f_read.read()
                            break
                    except Exception:
                        continue
                if text_preview is None:
                    try:
                        with open(file_path, 'r', encoding='utf-8', errors='replace') as f_read:
                            text_preview = f_read.read()
                    except Exception as e:
                        error_preview = str(e)
            elif ext in text_files:
                preview_type = 'code'
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as f_read:
                        text_preview = f_read.read()
                except Exception as e:
                    error_preview = str(e)
            elif ext in ['.pdf']:
                preview_type = 'pdf'
            elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp']:
                preview_type = 'image'
            elif ext in ['.mp3', '.wav', '.ogg', '.flac', '.aac']:
                preview_type = 'audio'
            elif ext in ['.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp']:
                preview_type = 'video'
            elif ext == '.sb3':
                preview_type = 'scratch'
                from .scratch_utils import parse_scratch_sb3
                raw_url = request.build_absolute_uri(reverse('file_view', args=[af.id]))
                html_preview, text_preview, error_preview = parse_scratch_sb3(file_path, raw_file_url=raw_url)
            elif ext == '.hex':
                preview_type = 'microbit'
                from .microbit_utils import parse_microbit_hex
                raw_url = request.build_absolute_uri(reverse('file_view', args=[af.id]))
                html_preview, text_preview, error_preview = parse_microbit_hex(file_path, raw_file_url=raw_url)
            elif ext in archive_files:
                preview_type = 'archive'
                archive_items, error_preview = get_archive_content(file_path, ext)

        files_with_preview.append({
            'obj': af,
            'id': af.id,
            'original_name': af.original_name or os.path.basename(af.file.name),
            'url': af.file.url if af.file else '',
            'size_display': af.get_size_display(),
            'icon': af.get_file_icon(),
            'extension': ext,
            'preview_type': preview_type,
            'html_preview': html_preview,
            'text_preview': text_preview,
            'archive_items': archive_items,
            'error_preview': error_preview,
            'slide_urls': slide_urls,
            'slide_count': len(slide_urls) if slide_urls else 0,
            'pdf_preview_url': pdf_preview_url,
        })

    context = {
        'assignment': assignment,
        'is_teacher': is_teacher,
        'can_edit': can_edit,
        'related_assignments': related_assignments,
        'submissions_count': submissions_count,
        'all_classes': ClassGroup.objects.all().order_by('grade', 'letter'),
        'files_with_preview': files_with_preview,
    }
    return render(request, 'feed/assignment_detail.html', context)



def student_submissions_portal(request):
    """
    Публічний розділ для учнів «Здані роботи»:
    - Учні можуть знайти свої здані роботи за прізвищем/іменем або класом.
    - Переглянути статус (Очікує перевірки / Оцінено).
    - Прочитати коментарі та зауваження вчителя.
    """
    class_groups = ClassGroup.objects.all().order_by('grade', 'letter', 'name')
    selected_class_id = request.GET.get('class')
    search_query = request.GET.get('search', '').strip()

    submissions_qs = Submission.objects.all().select_related(
        'assignment', 'class_group', 'teacher', 'assignment__subject'
    ).prefetch_related('comments', 'comments__author').order_by('-submitted_at')

    if selected_class_id:
        try:
            cid = int(selected_class_id)
            submissions_qs = submissions_qs.filter(class_group_id=cid)
        except (ValueError, TypeError):
            pass

    if search_query:
        submissions_qs = fuzzy_search_submissions(submissions_qs, search_query)

    paginator = Paginator(submissions_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'feed/student_submissions_portal.html', {
        'page_obj': page_obj,
        'class_groups': class_groups,
        'selected_class_id': selected_class_id,
        'search_query': search_query,
    })



def clear_class_filter(request):
    """Скидає фільтр класу з сесії."""
    if 'selected_class' in request.session:
        del request.session['selected_class']
    return redirect('index')


def feed_fragment(request):
    """
    AJAX: повертає лише HTML фрагмент стрічки завдань (картки + пагінація).
    Використовується при клінці на фільтр класу/предмету/дати без перезавантаження сторінки.
    """
    class_group_id, subject_id, date_str, query = _parse_filter_params(request)
    page_number = request.GET.get('page', 1)

    assignments = get_visible_assignments(class_group_id)

    if subject_id:
        assignments = assignments.filter(subject__id=subject_id)

    if date_str:
        from datetime import datetime
        dt_val = datetime.strptime(date_str, '%Y-%m-%d').date()
        assignments = assignments.filter(
            Q(published_at__date=dt_val) | Q(unarchived_at__date=dt_val)
        )

    if query:
        assignments = search_assignments(assignments, query)

    paginator = Paginator(assignments, 10)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    all_subjects = Subject.objects.filter(teacher__isnull=False).distinct()
    has_multiple_subjects = all_subjects.count() > 1
    selected_class = ClassGroup.objects.filter(id=class_group_id).first() if class_group_id else None

    selected_date_display = ""
    if date_str:
        try:
            from datetime import datetime
            dt = datetime.strptime(date_str, '%Y-%m-%d').date()
            uk_weekdays = ['Понеділок', 'Вівторок', 'Середа', 'Четвер', 'Пʼятниця', 'Субота', 'Неділя']
            w_name = uk_weekdays[dt.weekday()]
            selected_date_display = f"{w_name}, {dt.strftime('%d.%m.%Y')}"
        except Exception:
            pass

    context = {
        'assignments': page_obj.object_list,
        'page_obj': page_obj,
        'selected_class': selected_class,
        'selected_class_id': class_group_id,
        'selected_subject_id': subject_id,
        'selected_date': date_str,
        'selected_date_display': selected_date_display,
        'has_multiple_subjects': has_multiple_subjects,
        'query': query,
    }
    return render(request, 'feed/feed_fragment.html', context)


def calendar_fragment(request):
    """
    AJAX: повертає HTML-фрагмент календаря при зміні місяця або фільтрів.
    """
    class_group_id, subject_id, date_str, _ = _parse_filter_params(request)
    cal_year = request.GET.get('cal_year')
    cal_month = request.GET.get('cal_month')

    calendar_ctx = get_calendar_context(
        year=cal_year,
        month=cal_month,
        selected_date_str=date_str,
        class_group_id=class_group_id,
        subject_id=subject_id
    )
    return render(request, 'feed/calendar_widget.html', {'calendar': calendar_ctx})


def feed_check_updates(request):
    """
    Надлегка перевірка наявності нових або змінених завдань для стрічки.
    Виконує 1 швидкий запит агрегації в БД (<1 мс), не навантажуючи сервер.
    """
    class_group_id, subject_id, date_str, query = _parse_filter_params(request)

    assignments = get_visible_assignments(class_group_id)
    if subject_id:
        assignments = assignments.filter(subject__id=subject_id)
    if date_str:
        from datetime import datetime
        dt_val = datetime.strptime(date_str, '%Y-%m-%d').date()
        assignments = assignments.filter(
            Q(published_at__date=dt_val) | Q(unarchived_at__date=dt_val)
        )
    if query:
        assignments = assignments.filter(
            Q(title__icontains=query) |
            Q(description__icontains=query) |
            Q(teacher__full_name__icontains=query)
        )

    from django.db.models import Max, Count
    agg = assignments.aggregate(
        total_count=Count('id'),
        max_id=Max('id'),
        max_pub=Max('published_at')
    )

    pub_ts = int(agg['max_pub'].timestamp()) if agg['max_pub'] else 0
    fingerprint = f"{agg['total_count']}-{agg['max_id'] or 0}-{pub_ts}"

    return JsonResponse({
        'fingerprint': fingerprint,
        'count': agg['total_count'] or 0,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# МАЙСТЕР ПЕРШОГО ЗАПУСКУ (FIRST-RUN SETUP WIZARD)
# ═══════════════════════════════════════════════════════════════════════════════

def first_run_setup(request):
    """
    Майстер первинного налаштування системи.
    Доступний ТІЛЬКИ якщо в системі немає жодного суперкористувача.
    Створює першого адміністратора, прив'язує профіль вчителя та,
    за вибором користувача, ініціалізує стандартні предмети, класи та критерії НУШ.
    """
    from .middleware import set_has_admin, check_has_admin

    # Безпека: якщо суперкористувач уже є в системі — не дозволяємо доступ
    if check_has_admin():
        if get_teacher_or_none(request):
            return redirect('teacher_dashboard')
        messages.info(request, 'Систему вже налаштовано. Будь ласка, увійдіть зі своїм логіном та паролем.')
        return redirect('teacher_login')

    form = FirstRunSetupForm()

    if request.method == 'POST':
        form = FirstRunSetupForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            full_name = form.cleaned_data['full_name']
            email = form.cleaned_data.get('email', '')
            password = form.cleaned_data['password']
            seed_default_data = form.cleaned_data.get('seed_default_data', True)

            # 1. Створюємо суперкористувача Django
            user = User.objects.create_superuser(
                username=username,
                email=email,
                password=password
            )
            name_parts = full_name.strip().split()
            if len(name_parts) >= 2:
                user.last_name = name_parts[0]
                user.first_name = " ".join(name_parts[1:])
            else:
                user.first_name = full_name
            user.save()

            # 2. Створюємо профіль Teacher
            teacher = Teacher.objects.create(
                user=user,
                full_name=full_name,
                avatar_color='#6366f1'
            )

            # 3. Прив'язуємо або створюємо установу (School)
            try:
                school, created_s = School.objects.get_or_create(id=1, defaults={'name': 'Школа', 'admin': user})
                if not school.admin:
                    school.admin = user
                    school.save(update_fields=['admin'])
            except Exception:
                pass

            # 4. Якщо увімкнено — створюємо стандартні предмети, класи та критерії НУШ
            if seed_default_data:
                default_subjects = [
                    {'name': 'Інформатика', 'icon': '💻', 'color': '#6366f1'},
                    {'name': 'Математика', 'icon': '📐', 'color': '#3b82f6'},
                    {'name': 'Фізика', 'icon': '⚡', 'color': '#8b5cf6'},
                    {'name': 'Хімія', 'icon': '🧪', 'color': '#10b981'},
                    {'name': 'Біологія', 'icon': '🧬', 'color': '#06b6d4'},
                    {'name': 'Українська мова', 'icon': '📖', 'color': '#f59e0b'},
                    {'name': 'Українська література', 'icon': '📚', 'color': '#ef4444'},
                    {'name': 'Англійська мова', 'icon': '🌍', 'color': '#ec4899'},
                    {'name': 'Географія', 'icon': '🗺️', 'color': '#84cc16'},
                    {'name': 'Історія', 'icon': '🏛️', 'color': '#f97316'},
                    {'name': 'Мистецтво', 'icon': '🎨', 'color': '#e879f9'},
                    {'name': 'Фізична культура', 'icon': '⚽', 'color': '#14b8a6'},
                ]
                created_subs = []
                for sub_info in default_subjects:
                    s, _ = Subject.objects.get_or_create(
                        name=sub_info['name'],
                        defaults={
                            'icon': sub_info['icon'],
                            'color': sub_info['color'],
                            'created_by': teacher
                        }
                    )
                    created_subs.append(s)

                default_classes = [
                    {'name': '5А', 'grade': 5, 'letter': 'А'},
                    {'name': '6А', 'grade': 6, 'letter': 'А'},
                    {'name': '7А', 'grade': 7, 'letter': 'А'},
                    {'name': '8А', 'grade': 8, 'letter': 'А'},
                    {'name': '9А', 'grade': 9, 'letter': 'А'},
                    {'name': '9Б', 'grade': 9, 'letter': 'Б'},
                    {'name': '10А', 'grade': 10, 'letter': 'А'},
                    {'name': '11А', 'grade': 11, 'letter': 'А'},
                ]
                created_classes = []
                for cls_info in default_classes:
                    c, _ = ClassGroup.objects.get_or_create(
                        name=cls_info['name'],
                        defaults={
                            'grade': cls_info['grade'],
                            'letter': cls_info['letter'],
                            'created_by': teacher
                        }
                    )
                    created_classes.append(c)

                # Пов'язуємо вчителя з усіма створеними предметами та класами
                teacher.subjects.set(created_subs)
                teacher.classes.set(created_classes)

                # Ініціалізуємо пресети критеріїв НУШ
                try:
                    AICriteriaPreset.ensure_default_presets()
                except Exception:
                    pass

                # Ініціалізуємо базові налаштування ШІ
                try:
                    from .models import AISettings
                    AISettings.get_settings()
                except Exception:
                    pass

            # 5. Оновлюємо прапорець наявності адміністратора в пам'яті
            set_has_admin(True)

            # 6. Виконуємо автоматичний вхід під новим обліковим записом
            login(request, user)
            log_submission_activity(user, 'setup', f"Перший запуск: створено адміністратора {full_name}")

            messages.success(
                request,
                f'🎉 Вітаємо, {full_name}! Систему SchoolNet успішно ініціалізовано. '
                f'Ваш обліковий запис адміністратора активовано!'
            )
            return redirect('teacher_dashboard')

    return render(request, 'feed/first_run_setup.html', {'form': form})


# ═══════════════════════════════════════════════════════════════════════════════
# АВТОРИЗАЦІЯ ВЧИТЕЛЯ
# ═══════════════════════════════════════════════════════════════════════════════

def teacher_login(request):
    """Сторінка входу для вчителя."""
    if get_teacher_or_none(request):
        return redirect('teacher_dashboard')

    form = TeacherLoginForm()

    if request.method == 'POST':
        form = TeacherLoginForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            password = form.cleaned_data['password']
            user = authenticate(request, username=username, password=password)

            if user is not None:
                # Перевіряємо що є профіль вчителя
                try:
                    teacher = user.teacher_profile
                    login(request, user)
                    log_submission_activity(user, 'login', f"Вчитель {teacher.full_name} увійшов в систему")
                    messages.success(request, f'Вітаємо, {teacher.full_name}! 👋')
                    return redirect('teacher_dashboard')
                except Teacher.DoesNotExist:
                    messages.error(request, 'Профіль вчителя не знайдено для цього облікового запису.')
            else:
                messages.error(request, 'Неправильний логін або пароль.')

    return render(request, 'feed/teacher_login.html', {'form': form})


def teacher_logout(request):
    """Вихід вчителя."""
    log_submission_activity(request.user, 'logout', f"Вчитель {request.user.get_full_name() or request.user.username} вийшов із системи")
    logout(request)
    messages.info(request, 'Ви вийшли з системи.')
    return redirect('index')


# ═══════════════════════════════════════════════════════════════════════════════
# ПАНЕЛЬ ВЧИТЕЛЯ (захищена)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def teacher_dashboard(request):
    """
    Головна панель вчителя.
    Відображає всі завдання, згруповані за статусом.
    Підтримує AJAX-запити для плавної анімації вкладок.
    """
    teacher = request.user.teacher_profile

    # Автоматично архівуємо завдання, опубліковані понад 14 днів тому
    auto_archive_expired_assignments()

    # Автоматично оновлюємо відкладені публікації
    Assignment.objects.filter(
        teacher=teacher,
        status=Assignment.STATUS_SCHEDULED,
        scheduled_at__lte=timezone.now()
    ).update(
        status=Assignment.STATUS_PUBLISHED,
        published_at=timezone.now()
    )

    # Фільтр по вкладці
    tab = request.GET.get('tab', 'published')
    status_map = {
        'published': Assignment.STATUS_PUBLISHED,
        'draft': Assignment.STATUS_DRAFT,
        'scheduled': Assignment.STATUS_SCHEDULED,
        'archived': Assignment.STATUS_ARCHIVED,
    }
    status_filter = status_map.get(tab, Assignment.STATUS_PUBLISHED)

    from django.db.models import Count, Q as DbQ
    assignments = Assignment.objects.filter(
        teacher=teacher,
        status=status_filter
    ).prefetch_related('classes', 'files').select_related('subject').annotate(
        submissions_count=Count('submissions', distinct=True),
        ungraded_count=Count(
            'submissions',
            filter=DbQ(submissions__grade__isnull=True) | DbQ(submissions__grade=''),
            distinct=True
        )
    ).order_by('-created_at')

    # Пагінація: не більше 20 завдань на одній сторінці
    paginator = Paginator(assignments, 20)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Підраховуємо кількість у кожній вкладці
    counts = {
        'published': Assignment.objects.filter(teacher=teacher, status=Assignment.STATUS_PUBLISHED).count(),
        'draft': Assignment.objects.filter(teacher=teacher, status=Assignment.STATUS_DRAFT).count(),
        'scheduled': Assignment.objects.filter(teacher=teacher, status=Assignment.STATUS_SCHEDULED).count(),
        'archived': Assignment.objects.filter(teacher=teacher, status=Assignment.STATUS_ARCHIVED).count(),
    }

    # Загальна кількість неоцінених здач для значку
    total_ungraded = Submission.objects.filter(
        assignment__teacher=teacher
    ).filter(Q(grade__isnull=True) | Q(grade='')).count()

    context = {
        'teacher': teacher,
        'assignments': page_obj,
        'page_obj': page_obj,
        'tab': tab,
        'counts': counts,
        'total_ungraded': total_ungraded,
    }

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.GET.get('fragment') == '1':
        return render(request, 'feed/teacher_dashboard_fragment.html', context)

    return render(request, 'feed/teacher_dashboard.html', context)



@teacher_required
def assignment_create(request):
    """Створення нового завдання."""
    teacher = request.user.teacher_profile
    form = AssignmentForm(teacher=teacher)

    if request.method == 'POST':
        form = AssignmentForm(teacher=teacher, data=request.POST, files=request.FILES)
        if form.is_valid():
            assignment = form.save_with_status(teacher=teacher)

            # ── Зберігаємо дефолтні налаштування ШІ для завдання
            ai_preset_id = request.POST.get('default_ai_preset')
            if ai_preset_id:
                request.session['last_ai_preset_id'] = ai_preset_id
                from .models import AICriteriaPreset
                try:
                    assignment.default_ai_preset = AICriteriaPreset.objects.get(pk=ai_preset_id)
                except AICriteriaPreset.DoesNotExist:
                    pass
            import json as _json
            grs_raw = request.POST.getlist('default_ai_grs')
            assignment.default_ai_grs = _json.dumps(grs_raw) if grs_raw else ''
            assignment.allow_student_ai_check = bool(request.POST.get('allow_student_ai_check'))
            assignment.allow_ai_usage = bool(request.POST.get('allow_ai_usage'))
            assignment.save(update_fields=['default_ai_preset', 'default_ai_grs', 'allow_student_ai_check', 'allow_ai_usage'])

            # Зберігаємо всі прикріплені файли
            for f in request.FILES.getlist('files'):
                AssignmentFile.objects.create(
                    assignment=assignment,
                    file=f,
                    original_name=f.name
                )

            # Зберігаємо додаткові посилання
            for url, label in zip(request.POST.getlist('extra_link_url'), request.POST.getlist('extra_link_label')):
                if url.strip():
                    AssignmentLink.objects.create(
                        assignment=assignment,
                        url=url.strip(),
                        label=label.strip()
                    )

            # Зберігаємо додаткові YouTube відео
            for yurl, ytitle in zip(request.POST.getlist('extra_youtube_url'), request.POST.getlist('extra_youtube_title')):
                if yurl.strip():
                    AssignmentYouTubeLink.objects.create(
                        assignment=assignment,
                        url=yurl.strip(),
                        title=ytitle.strip()
                    )

            # ── Зберігаємо прив'язку до розкладу уроків для класів
            teacher_lesson_schedules = TeacherLessonSchedule.objects.filter(teacher=teacher).select_related('bell_slot', 'class_group')
            for class_obj in assignment.classes.all():
                target_val = request.POST.get(f"class_schedule_target_{class_obj.id}", 'auto')
                if target_val and target_val.startswith('slot_'):
                    parts = target_val.split('_')
                    if len(parts) == 3:
                        day_num = int(parts[1])
                        slot_id = int(parts[2])
                        AssignmentScheduleTarget.objects.update_or_create(
                            assignment=assignment,
                            class_group=class_obj,
                            defaults={
                                'target_day_of_week': day_num,
                                'bell_slot_id': slot_id
                            }
                        )
                elif target_val == 'auto':
                    cls_sch = teacher_lesson_schedules.filter(class_group=class_obj).first()
                    if cls_sch:
                        AssignmentScheduleTarget.objects.update_or_create(
                            assignment=assignment,
                            class_group=class_obj,
                            defaults={
                                'target_day_of_week': cls_sch.day_of_week,
                                'bell_slot_id': cls_sch.bell_slot_id
                            }
                        )

            status_labels = {
                Assignment.STATUS_PUBLISHED: 'опубліковано',
                Assignment.STATUS_DRAFT: 'збережено як чернетку',
                Assignment.STATUS_SCHEDULED: 'заплановано до публікації',
            }
            label = status_labels.get(assignment.status, 'збережено')
            messages.success(request, f'Завдання "{assignment.title}" — {label}! ✅')
            return redirect('teacher_dashboard')

    from .models import AICriteriaPreset
    AICriteriaPreset.ensure_default_presets()
    criteria_presets = AICriteriaPreset.objects.all()

    teacher_lesson_schedules = list(TeacherLessonSchedule.objects.filter(teacher=teacher).select_related('bell_slot', 'class_group').order_by('day_of_week', 'bell_slot__lesson_number'))

    last_preset_id = request.session.get('last_ai_preset_id')
    last_grs_json = '[]'
    if not last_preset_id:
        last_asg = Assignment.objects.filter(teacher=teacher, default_ai_preset__isnull=False).order_by('-created_at').first()
        if last_asg:
            last_preset_id = str(last_asg.default_ai_preset_id)
            last_grs_json = last_asg.default_ai_grs or '[]'
    else:
        last_asg = Assignment.objects.filter(teacher=teacher, default_ai_preset_id=last_preset_id).order_by('-created_at').first()
        if last_asg and last_asg.default_ai_grs:
            last_grs_json = last_asg.default_ai_grs

    context = {
        'teacher': teacher,
        'form': form,
        'is_edit': False,
        'criteria_presets': criteria_presets,
        'last_ai_preset_id': int(last_preset_id) if last_preset_id and str(last_preset_id).isdigit() else None,
        'last_ai_grs': last_grs_json,
        'teacher_lesson_schedules': teacher_lesson_schedules,
    }
    return render(request, 'feed/assignment_form.html', context)


@teacher_required
def assignment_edit(request, pk):
    """Редагування існуючого завдання."""
    teacher = request.user.teacher_profile
    # Суперадмін може редагувати будь-яке завдання, звичайний вчитель — лише власні
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)


    form = AssignmentForm(teacher=teacher, instance=assignment)
    existing_files = assignment.files.all()
    existing_links = assignment.additional_links.all()
    existing_youtube_links = assignment.youtube_links.all()

    if request.method == 'POST':
        form = AssignmentForm(
            teacher=teacher,
            data=request.POST,
            files=request.FILES,
            instance=assignment
        )

        if form.is_valid():
            # Видаляємо файли, відмічені для видалення
            files_to_delete = request.POST.getlist('delete_files')
            if files_to_delete:
                AssignmentFile.objects.filter(
                    id__in=files_to_delete,
                    assignment=assignment
                ).delete()

            # Зберігаємо оновлене завдання
            assignment = form.save_with_status(teacher=teacher)

            # ── Зберігаємо дефолтні налаштування ШІ
            ai_preset_id = request.POST.get('default_ai_preset')
            if ai_preset_id:
                request.session['last_ai_preset_id'] = ai_preset_id
                try:
                    assignment.default_ai_preset = AICriteriaPreset.objects.get(pk=ai_preset_id)
                except AICriteriaPreset.DoesNotExist:
                    assignment.default_ai_preset = None
            else:
                assignment.default_ai_preset = None
            import json as _json
            grs_raw = request.POST.getlist('default_ai_grs')
            assignment.default_ai_grs = _json.dumps(grs_raw) if grs_raw else ''
            assignment.allow_student_ai_check = bool(request.POST.get('allow_student_ai_check'))
            assignment.allow_ai_usage = bool(request.POST.get('allow_ai_usage'))
            assignment.save(update_fields=['default_ai_preset', 'default_ai_grs', 'allow_student_ai_check', 'allow_ai_usage'])

            # Додаємо нові файли
            for f in request.FILES.getlist('files'):
                AssignmentFile.objects.create(
                    assignment=assignment,
                    file=f,
                    original_name=f.name
                )

            # Оновлюємо додаткові посилання (перезаписуємо)
            assignment.additional_links.all().delete()
            for url, label in zip(request.POST.getlist('extra_link_url'), request.POST.getlist('extra_link_label')):
                if url.strip():
                    AssignmentLink.objects.create(
                        assignment=assignment,
                        url=url.strip(),
                        label=label.strip()
                    )

            # Оновлюємо додаткові YouTube відео (перезаписуємо)
            assignment.youtube_links.all().delete()
            for yurl, ytitle in zip(request.POST.getlist('extra_youtube_url'), request.POST.getlist('extra_youtube_title')):
                if yurl.strip():
                    AssignmentYouTubeLink.objects.create(
                        assignment=assignment,
                        url=yurl.strip(),
                        title=ytitle.strip()
                    )

            # ── Оновлюємо прив'язку до розкладу уроків
            teacher_lesson_schedules_all = TeacherLessonSchedule.objects.filter(teacher=teacher).select_related('bell_slot', 'class_group')
            for class_obj in assignment.classes.all():
                target_val = request.POST.get(f"class_schedule_target_{class_obj.id}", 'auto')
                if target_val and target_val.startswith('slot_'):
                    parts = target_val.split('_')
                    if len(parts) == 3:
                        day_num = int(parts[1])
                        slot_id = int(parts[2])
                        AssignmentScheduleTarget.objects.update_or_create(
                            assignment=assignment,
                            class_group=class_obj,
                            defaults={
                                'target_day_of_week': day_num,
                                'bell_slot_id': slot_id
                            }
                        )
                elif target_val == 'auto':
                    cls_sch = teacher_lesson_schedules_all.filter(class_group=class_obj).first()
                    if cls_sch:
                        AssignmentScheduleTarget.objects.update_or_create(
                            assignment=assignment,
                            class_group=class_obj,
                            defaults={
                                'target_day_of_week': cls_sch.day_of_week,
                                'bell_slot_id': cls_sch.bell_slot_id
                            }
                        )
                elif target_val == 'none':
                    AssignmentScheduleTarget.objects.filter(assignment=assignment, class_group=class_obj).delete()

            messages.success(request, f'Завдання "{assignment.title}" оновлено! ✅')
            return redirect('teacher_dashboard')

    AICriteriaPreset.ensure_default_presets()
    criteria_presets = AICriteriaPreset.objects.all()

    teacher_lesson_schedules = list(TeacherLessonSchedule.objects.filter(teacher=teacher).select_related('bell_slot', 'class_group').order_by('day_of_week', 'bell_slot__lesson_number'))
    existing_targets = {t.class_group_id: (t.target_day_of_week, t.bell_slot_id) for t in assignment.schedule_targets.all()}
    for sch in teacher_lesson_schedules:
        sch.is_selected_for_assignment = (existing_targets.get(sch.class_group_id) == (sch.day_of_week, sch.bell_slot_id))

    context = {
        'teacher': teacher,
        'form': form,
        'assignment': assignment,
        'existing_files': existing_files,
        'existing_links': existing_links,
        'existing_youtube_links': existing_youtube_links,
        'is_edit': True,
        'criteria_presets': criteria_presets,
        'teacher_lesson_schedules': teacher_lesson_schedules,
    }
    return render(request, 'feed/assignment_form.html', context)


@teacher_required
def assignment_delete(request, pk):
    """Видалення завдання (POST-запит)."""
    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    if request.method == 'POST':
        title = assignment.title
        assignment.delete()
        messages.success(request, f'Завдання "{title}" видалено.')

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect('teacher_dashboard')


@teacher_required
def assignment_unarchive(request, pk):
    """
    Розархівація завдання з архіву (POST-запит).
    Повертає статус 'published', зберігаючи первинну дату публікації 'published_at',
    та встановлює час розархівації у полі 'unarchived_at'.
    """
    teacher = request.user.teacher_profile
    assignment = get_object_or_404(Assignment, pk=pk)

    if not (request.user.is_superuser or assignment.teacher == teacher):
        messages.error(request, 'У вас немає прав для розархівації цього завдання.')
        return redirect('teacher_dashboard')

    if request.method == 'POST':
        assignment.status = Assignment.STATUS_PUBLISHED
        assignment.unarchived_at = timezone.now()
        # Первинна дата published_at залишається незмінною!
        assignment.save(update_fields=['status', 'unarchived_at', 'updated_at'])
        messages.success(request, f'Завдання «{assignment.title}» успішно розархівовано (первинну дату збережено)! ♻️')

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect(f"{reverse('teacher_dashboard')}?tab=published")


@teacher_required
def assignment_duplicate(request, pk):
    """
    Дублювання завдання.
    Створює нову чернетку на основі обраного завдання без слова [Копія].
    """
    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        original = get_object_or_404(Assignment, pk=pk)
    else:
        original = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    clean_title = original.title
    if clean_title.startswith('[Копія] '):
        clean_title = clean_title[len('[Копія] '):]
    elif clean_title.startswith('Копія '):
        clean_title = clean_title[len('Копія '):]

    # Створюємо дублікат
    duplicate = Assignment.objects.create(
        teacher=teacher,
        subject=original.subject,
        title=clean_title,
        description=original.description,
        is_individual=original.is_individual,
        student_name=original.student_name,
        link_url=original.link_url,
        link_label=original.link_label,
        due_date=original.due_date,
        status=Assignment.STATUS_DRAFT,
        duplicated_from=original,
        published_at=None,
        default_ai_preset=original.default_ai_preset,
        default_ai_grs=original.default_ai_grs,
        allow_student_ai_check=original.allow_student_ai_check,
        allow_ai_usage=original.allow_ai_usage,
    )
    duplicate.classes.set(original.classes.all())

    # Копіюємо файли (посилання на ті самі файли, без фізичного копіювання)
    for f in original.files.all():
        AssignmentFile.objects.create(
            assignment=duplicate,
            file=f.file,
            original_name=f.original_name,
        )

    # Копіюємо додаткові посилання
    for lnk in original.additional_links.all():
        AssignmentLink.objects.create(
            assignment=duplicate,
            url=lnk.url,
            label=lnk.label,
        )

    # Копіюємо відео YouTube
    for ytb in original.youtube_links.all():
        AssignmentYouTubeLink.objects.create(
            assignment=duplicate,
            url=ytb.url,
            title=ytb.title,
        )

    messages.success(request, f'Створено дублікат завдання "{duplicate.title}". Можна відредагувати та опублікувати.')
    return redirect('assignment_edit', pk=duplicate.pk)


@teacher_required
def assignment_archive(request, pk):
    """Переміщення завдання до архіву."""
    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    if request.method == 'POST':
        assignment.status = Assignment.STATUS_ARCHIVED
        assignment.save()
        messages.info(request, f'Завдання "{assignment.title}" переміщено до архіву.')

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect('teacher_dashboard')


@teacher_required
def assignment_publish(request, pk):
    """Миттєва публікація чернетки або відкладеного завдання."""
    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    if request.method == 'POST':
        assignment.status = Assignment.STATUS_PUBLISHED
        if not assignment.published_at or assignment.duplicated_from_id is not None:
            assignment.published_at = timezone.now()
        assignment.save()
        messages.success(request, f'Завдання "{assignment.title}" опубліковано! ✅')

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect('teacher_dashboard')


@teacher_required
def teacher_profile(request):
    """
    Редагування профілю вчителя:
    - Персональні предмети та класи вчителя (додавання/видалення з власного профілю).
    - Створення нових предметів та класів.
    - Функції Супер-Адміністратора: створення нових вчителів та скидання їхніх паролів.
    """
    teacher = request.user.teacher_profile
    profile_form = TeacherProfileForm(instance=teacher)
    subject_form = SubjectForm()
    class_form = ClassGroupForm()
    teacher_create_form = TeacherCreateForm()
    password_reset_form = PasswordResetForm()

    if request.method == 'POST':
        action = request.POST.get('action')

        # ── Оновлення основних даних (ПІБ, колір, фото аватара) ──────────────
        if action == 'update_profile':
            profile_form = TeacherProfileForm(request.POST, request.FILES, instance=teacher)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Профіль та аватар успішно збережено! ✅')
                return redirect('teacher_profile')
            else:
                for field, errors in profile_form.errors.items():
                    for err in errors:
                        messages.error(request, f'{err}')

        elif action == 'remove_avatar':
            if teacher.avatar_image:
                try:
                    import os
                    if os.path.exists(teacher.avatar_image.path):
                        os.remove(teacher.avatar_image.path)
                except Exception:
                    pass
                teacher.avatar_image = None
                teacher.save(update_fields=['avatar_image'])
                messages.info(request, 'Фото аватара видалено. Використовуються кольорові ініціали.')
            return redirect('teacher_profile')

        # ── Персональні предмети вчителя ─────────────────────────────────────
        elif action == 'add_teacher_subject':
            subject_id = request.POST.get('subject_id')
            if subject_id:
                subject = get_object_or_404(Subject, pk=subject_id)
                teacher.subjects.add(subject)
                messages.success(request, f'Предмет «{subject.name}» додано до вашого профілю! 📚')
            return redirect('teacher_profile')

        elif action == 'remove_teacher_subject':
            subject_id = request.POST.get('subject_id')
            if subject_id:
                subject = get_object_or_404(Subject, pk=subject_id)
                teacher.subjects.remove(subject)
                messages.info(request, f'Предмет «{subject.name}» видалено з вашого профілю.')
            return redirect('teacher_profile')

        # ── Персональні класи вчителя ─────────────────────────────────────────
        elif action == 'add_teacher_class':
            class_id = request.POST.get('class_id')
            if class_id:
                cgroup = get_object_or_404(ClassGroup, pk=class_id)
                teacher.classes.add(cgroup)
                messages.success(request, f'Клас «{cgroup.name}» додано до вашого профілю! 🏫')
            return redirect('teacher_profile')

        elif action == 'remove_teacher_class':
            class_id = request.POST.get('class_id')
            if class_id:
                cgroup = get_object_or_404(ClassGroup, pk=class_id)
                teacher.classes.remove(cgroup)
                messages.info(request, f'Клас «{cgroup.name}» видалено з вашого профілю.')
            return redirect('teacher_profile')

        # ── Створення нового предмету (і авто-додавання вчителю) ──────────────
        elif action == 'create_subject':
            subject_form = SubjectForm(request.POST)
            if subject_form.is_valid():
                subject = subject_form.save(commit=False)
                subject.created_by = teacher
                subject.save()
                teacher.subjects.add(subject)
                messages.success(request, f'Новий предмет «{subject.name}» створено та додано до профілю! 📚')
                return redirect('teacher_profile')
            else:
                messages.error(request, 'Помилка при створенні предмету.')

        # ── Створення нового класу (і авто-додавання вчителю) ─────────────────
        elif action == 'create_class':
            class_form = ClassGroupForm(request.POST)
            if class_form.is_valid():
                cgroup = class_form.save(commit=False)
                cgroup.created_by = teacher
                cgroup.save()
                teacher.classes.add(cgroup)
                messages.success(request, f'Новий клас «{cgroup.name}» створено та додано до профілю! 🏫')
                return redirect('teacher_profile')
            else:
                messages.error(request, 'Помилка при створенні класу.')

        # ── Функції Супер-Адміністратора: Створення акаунта вчителя ─────────
        elif action == 'create_teacher_account':
            if not request.user.is_superuser:
                messages.error(request, 'Тільки Супер-Адміністратор може створювати акаунти.')
                return redirect('teacher_profile')

            teacher_create_form = TeacherCreateForm(request.POST)
            if teacher_create_form.is_valid():
                username = teacher_create_form.cleaned_data['username']
                password = teacher_create_form.cleaned_data['password']
                full_name = teacher_create_form.cleaned_data['full_name']
                is_super = teacher_create_form.cleaned_data['is_superuser']

                new_user = User.objects.create_user(username=username, password=password)
                new_user.is_superuser = is_super
                new_user.is_staff = is_super
                new_user.save()

                Teacher.objects.create(user=new_user, full_name=full_name)
                role = "Супер-Адміністратор" if is_super else "Вчитель"
                messages.success(request, f'Акаунт ({role}) «{full_name}» ({username}) створено! 👤')
                return redirect('teacher_profile')
            else:
                for field, errors in teacher_create_form.errors.items():
                    for err in errors:
                        messages.error(request, f'{err}')

        # ── Функції Супер-Адміністратора: Скидання пароля ────────────────────
        elif action == 'reset_teacher_password':
            if not request.user.is_superuser:
                messages.error(request, 'Тільки Супер-Адміністратор може скидати паролі.')
                return redirect('teacher_profile')

            target_user_id = request.POST.get('user_id')
            new_pass = request.POST.get('new_password', '').strip()

            if target_user_id and new_pass:
                target_user = get_object_or_404(User, pk=target_user_id)
                target_user.set_password(new_pass)
                target_user.save()
                messages.success(request, f'Пароль для користувача {target_user.username} змінено! 🔑')
            else:
                messages.error(request, 'Вкажіть новий пароль.')
            return redirect('teacher_profile')

        # ── Функції Супер-Адміністратора: Видалення вчителя та матеріалів ─────
        elif action == 'delete_teacher':
            if not request.user.is_superuser:
                messages.error(request, 'Тільки Супер-Адміністратор може видаляти вчителів.')
                return redirect('teacher_profile')

            target_teacher_id = request.POST.get('teacher_id')
            if target_teacher_id:
                target_teacher = get_object_or_404(Teacher, pk=target_teacher_id)
                
                # Запобігаємо видаленню самого себе
                if target_teacher.user == request.user:
                    messages.error(request, 'Ви не можете видалити власний акаунт.')
                    return redirect('teacher_profile')

                # Видаляємо фізичні файли завдань вчителя з диска
                import os
                for assignment in target_teacher.assignments.all():
                    for file_obj in assignment.files.all():
                        if file_obj.file and os.path.exists(file_obj.file.path):
                            try:
                                os.remove(file_obj.file.path)
                            except Exception:
                                pass

                # Видаляємо користувача (і пов'язаного вчителя cascade)
                user_to_delete = target_teacher.user
                full_name_deleted = target_teacher.full_name
                username_deleted = user_to_delete.username
                user_to_delete.delete()

                messages.success(request, f'Вчителя «{full_name_deleted}» ({username_deleted}) та всі його матеріали успішно видалено! 🗑️')
            else:
                messages.error(request, 'Вкажіть вчителя для видалення.')
            return redirect('teacher_profile')


    # Отримуємо дані для сторінки (тільки предмети та класи, створені вчителями)
    teacher_subjects = teacher.subjects.all()
    teacher_classes = teacher.classes.all().order_by('grade', 'letter')

    other_subjects = Subject.objects.filter(created_by__isnull=False).exclude(id__in=teacher_subjects.values_list('id', flat=True))
    other_classes = ClassGroup.objects.filter(created_by__isnull=False).exclude(id__in=teacher_classes.values_list('id', flat=True)).order_by('grade', 'letter')


    all_teachers = Teacher.objects.select_related('user').all() if request.user.is_superuser else []

    context = {
        'teacher': teacher,
        'form': profile_form,
        'subject_form': subject_form,
        'class_form': class_form,
        'teacher_create_form': teacher_create_form,
        'password_reset_form': password_reset_form,
        'teacher_subjects': teacher_subjects,
        'teacher_classes': teacher_classes,
        'other_subjects': other_subjects,
        'other_classes': other_classes,
        'all_teachers': all_teachers,
    }
    return render(request, 'feed/teacher_profile.html', context)


from django.views.decorators.clickjacking import xframe_options_exempt


def get_presentation_slides(file_id, file_path):
    """
    Генерує або дістає з кешу високоякісні зображення слайдів презентації (.pptx, .ppt, .odp)
    та PDF версію через headless LibreOffice + pdftoppm.
    Повертає (slide_urls, pdf_url).
    """
    import os
    import glob
    import shutil
    import subprocess
    from django.conf import settings
    
    cache_dir = os.path.join(settings.MEDIA_ROOT, 'previews', str(file_id))
    os.makedirs(cache_dir, exist_ok=True)
    pdf_path = os.path.join(cache_dir, 'presentation.pdf')
    
    # 1. Перевіряємо чи є вже PDF
    if not os.path.exists(pdf_path):
        try:
            if shutil.which('libreoffice') or shutil.which('soffice'):
                cmd = ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', cache_dir, file_path]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
                base_name = os.path.splitext(os.path.basename(file_path))[0]
                gen_pdf = os.path.join(cache_dir, base_name + '.pdf')
                if os.path.exists(gen_pdf) and gen_pdf != pdf_path:
                    os.rename(gen_pdf, pdf_path)
        except Exception:
            pass
            
    if not os.path.exists(pdf_path):
        return [], None

    # Також створюємо прямий PDF для перегляду у previews/<file_id>.pdf
    direct_pdf = os.path.join(settings.MEDIA_ROOT, 'previews', f"{file_id}.pdf")
    if not os.path.exists(direct_pdf):
        try:
            shutil.copyfile(pdf_path, direct_pdf)
        except Exception:
            pass
        
    # 2. Перевіряємо чи є вже зображення слайдів
    slide_files = sorted(glob.glob(os.path.join(cache_dir, 'slide-*.jpg')))
    if not slide_files:
        try:
            if shutil.which('pdftoppm'):
                prefix = os.path.join(cache_dir, 'slide')
                cmd = ['pdftoppm', '-jpeg', '-r', '130', pdf_path, prefix]
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
                slide_files = sorted(glob.glob(os.path.join(cache_dir, 'slide-*.jpg')))
        except Exception:
            pass
            
    media_url = settings.MEDIA_URL.rstrip('/')
    slide_urls = [f'{media_url}/previews/{file_id}/{os.path.basename(p)}' for p in slide_files]
    pdf_url = f'{media_url}/previews/{file_id}/presentation.pdf'
    return slide_urls, pdf_url


def get_pdf_preview_url(file_obj):
    """
    Конвертує pptx, docx, або xlsx у PDF за допомогою headless LibreOffice, якщо його ще немає в кеші.
    Повертає URL до PDF файлу або None у разі помилки.
    """
    import os
    import subprocess
    from django.conf import settings
    
    previews_dir = os.path.join(settings.MEDIA_ROOT, 'previews')
    os.makedirs(previews_dir, exist_ok=True)
    
    pdf_filename = f"{file_obj.id}.pdf"
    pdf_path = os.path.join(previews_dir, pdf_filename)
    
    if os.path.exists(pdf_path):
        from django.urls import reverse
        return reverse('file_view', args=[file_obj.id]) + "?preview_pdf=1"
        
    try:
        # Запускаємо LibreOffice для конвертації
        cmd = [
            'libreoffice',
            '--headless',
            '--convert-to', 'pdf',
            '--outdir', previews_dir,
            file_obj.file.path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=20)
        
        # Отримуємо назву оригінального файлу та міняємо розширення на .pdf
        base_name = os.path.basename(file_obj.file.path)
        raw_pdf_name = os.path.splitext(base_name)[0] + '.pdf'
        raw_pdf_path = os.path.join(previews_dir, raw_pdf_name)
        
        if os.path.exists(raw_pdf_path):
            os.rename(raw_pdf_path, pdf_path)
            from django.urls import reverse
            return reverse('file_view', args=[file_obj.id]) + "?preview_pdf=1"
    except Exception as e:
        print(f"Error converting file {file_obj.id} to PDF preview: {str(e)}")
        
    return None


@xframe_options_exempt
def file_view(request, file_id):
    """Служить файл безпосередньо у браузері з інлайновим Content-Disposition та правильним MIME-типом (зокрема для PDF)."""
    file_obj = get_object_or_404(AssignmentFile, pk=file_id)
    try:
        import mimetypes
        import os
        from django.conf import settings
        
        # Якщо запит йде на PDF прев'ю сконвертованого офісного файлу
        if request.GET.get('preview_pdf') == '1':
            path = os.path.join(settings.MEDIA_ROOT, 'previews', f"{file_obj.id}.pdf")
            mime_type = 'application/pdf'
        else:
            path = file_obj.file.path
            ext = os.path.splitext(path)[1].lower()
            if ext == '.pdf':
                mime_type = 'application/pdf'
            elif ext in ['.jpg', '.jpeg']:
                mime_type = 'image/jpeg'
            elif ext == '.png':
                mime_type = 'image/png'
            elif ext == '.webp':
                mime_type = 'image/webp'
            elif ext == '.svg':
                mime_type = 'image/svg+xml'
            elif ext in ['.mp4', '.m4v']:
                mime_type = 'video/mp4'
            elif ext == '.webm':
                mime_type = 'video/webm'
            elif ext == '.ogv':
                mime_type = 'video/ogg'
            elif ext == '.mov':
                mime_type = 'video/quicktime'
            elif ext == '.mkv':
                mime_type = 'video/x-matroska'
            elif ext == '.avi':
                mime_type = 'video/x-msvideo'
            elif ext == '.3gp':
                mime_type = 'video/3gpp'
            elif ext == '.mp3':
                mime_type = 'audio/mpeg'
            elif ext == '.wav':
                mime_type = 'audio/wav'
            elif ext == '.ogg':
                mime_type = 'audio/ogg'
            elif ext == '.sb3':
                mime_type = 'application/x.scratch.sb3'
            elif ext == '.hex':
                mime_type = 'text/plain; charset=utf-8'
            else:
                mime_type, _ = mimetypes.guess_type(path)
                if not mime_type:
                    mime_type = 'application/octet-stream'
                
        if os.path.exists(path):
            with open(path, 'rb') as f:
                response = HttpResponse(f.read(), content_type=mime_type)
            
            # Завжди передаємо чистий ASCII 'inline', щоб уникнути помилкового MIME-кодування заголовка
            # з кириличними літерами (через що браузери сприймають це як 'attachment' і примусово завантажують файл)
            response['Content-Disposition'] = 'inline'
            response['X-Frame-Options'] = 'SAMEORIGIN'
            return response
    except Exception as e:
        return HttpResponse(f"Помилка завантаження файлу: {str(e)}", status=500)
    return HttpResponse("Файл не знайдено", status=404)


def file_download(request, file_id):
    """Завантажує файл завдання (матеріали вчителя) з правильним ім'ям та розширенням.
    Завжди повертає Content-Disposition: attachment, щоб браузер пропонував зберегти файл.
    """
    import mimetypes
    import os
    from django.http import FileResponse

    file_obj = get_object_or_404(AssignmentFile, pk=file_id)
    try:
        path = file_obj.file.path
        if not os.path.exists(path):
            return HttpResponse("Файл не знайдено на сервері", status=404)

        original_name = file_obj.original_name or os.path.basename(path)
        mime_type, _ = mimetypes.guess_type(path)
        if not mime_type:
            mime_type = 'application/octet-stream'

        response = FileResponse(
            open(path, 'rb'),
            content_type=mime_type,
            as_attachment=True,
            filename=original_name,
        )
        return response
    except Exception as e:
        return HttpResponse(f"Помилка завантаження: {str(e)}", status=500)


def file_preview(request, file_id):
    """Служба для генерації інлайнового прев'ю файлу (docx, xlsx, pptx, python та інших код-файлів)."""
    file_obj = get_object_or_404(AssignmentFile, pk=file_id)
    ext = file_obj.get_extension()

    
    # Спочатку пробуємо якісну конвертацію у PDF через LibreOffice
    if ext in {'.pptx', '.ppt', '.docx', '.doc', '.xlsx', '.xls'}:
        pdf_url = get_pdf_preview_url(file_obj)
        if pdf_url:
            return JsonResponse({
                'type': 'url',
                'url': request.build_absolute_uri(pdf_url),
                'file_type': 'pdf'
            })
            
    # Резервна обробка (fallback), якщо LibreOffice не зміг згенерувати PDF
    if ext == '.docx':
        try:
            import mammoth
            with open(file_obj.file.path, 'rb') as docx_file:
                result = mammoth.convert_to_html(docx_file)
                html = result.value
                styled_html = f"<div class='docx-preview-content' style='text-align:left; width:100%; color:var(--color-text-primary); line-height:1.6;'>{html}</div>"
                return JsonResponse({
                    'type': 'html',
                    'content': styled_html
                })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Помилка конвертації файлу Word: {str(e)}'
            })

    elif ext == '.xlsx':
        try:
            import openpyxl
            wb = openpyxl.load_workbook(file_obj.file.path, read_only=True, data_only=True)
            html = []
            for sheet_name in wb.sheetnames[:3]:
                sheet = wb[sheet_name]
                html.append(f"<h3 style='margin-top:20px; margin-bottom:10px; color:var(--color-primary); text-align:left;'>📊 Аркуш: {sheet_name}</h3>")
                html.append("<div style='overflow-x:auto; width:100%; border-radius:8px; border:1px solid var(--color-border); margin-bottom:20px;'><table style='width:100%; border-collapse:collapse; font-size:13px; background:var(--color-surface);'>")
                
                for r_idx, row in enumerate(sheet.iter_rows(values_only=True)):
                    if r_idx > 100:
                        html.append("<tr><td colspan='100' style='text-align:center; color:var(--color-text-muted); padding:8px;'>... відображено перші 100 рядків ...</td></tr>")
                        break
                    if not any(row):
                        continue
                        
                    html.append("<tr style='border-bottom:1px solid var(--color-border);'>")
                    for cell_value in row:
                        val = str(cell_value) if cell_value is not None else ""
                        if r_idx == 0:
                            html.append(f"<th style='border-right:1px solid var(--color-border); padding:8px; background:var(--color-bg-secondary); font-weight:600; text-align:left;'>{val}</th>")
                        else:
                            html.append(f"<td style='border-right:1px solid var(--color-border); padding:8px; text-align:left;'>{val}</td>")
                    html.append("</tr>")
                html.append("</table></div>")
            
            styled_html = "".join(html)
            return JsonResponse({
                'type': 'html',
                'content': styled_html
            })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Помилка конвертації таблиці Excel: {str(e)}'
            })

    elif ext in ['.pptx', '.ppt', '.odp']:
        try:
            slide_urls, pdf_url = get_presentation_slides(file_obj.id, file_obj.file.path)
            if slide_urls:
                return JsonResponse({
                    'type': 'slides',
                    'slides': slide_urls,
                    'count': len(slide_urls),
                    'pdf_url': pdf_url
                })
            else:
                from .utils import convert_pptx_to_html
                h, err = convert_pptx_to_html(file_obj.file.path)
                return JsonResponse({
                    'type': 'html',
                    'content': h or f"Помилка конвертації: {err}"
                })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Помилка обробки презентації: {str(e)}'
            })

    # 4. Обробка Scratch 3 (.sb3) проєктів
    elif ext == '.sb3':
        try:
            from .scratch_utils import parse_scratch_sb3
            raw_url = request.build_absolute_uri(reverse('file_view', args=[file_obj.id]))
            s_html, _, s_err = parse_scratch_sb3(file_obj.file.path, raw_file_url=raw_url)
            if s_html:
                return JsonResponse({'type': 'html', 'content': s_html})
            return JsonResponse({
                'type': 'error',
                'message': s_err or 'Не вдалося розібрати Scratch 3 (.sb3) проєкт'
            })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Помилка відкриття Scratch проєкту: {str(e)}'
            })

    # 5. Обробка BBC micro:bit (.hex) файлів
    elif ext == '.hex':
        try:
            from .microbit_utils import parse_microbit_hex
            raw_url = request.build_absolute_uri(reverse('file_view', args=[file_obj.id]))
            h_html, _, h_err = parse_microbit_hex(file_obj.file.path, raw_file_url=raw_url)
            if h_html:
                return JsonResponse({'type': 'html', 'content': h_html})
            return JsonResponse({
                'type': 'error',
                'message': h_err or 'Не вдалося розібрати BBC micro:bit (.hex) файл'
            })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Помилка відкриття micro:bit файлу: {str(e)}'
            })

    # 6. Обробка текстових файлів та код-файлів (включаючи .py, .js, .css, .json, .sh тощо)
    elif file_obj.get_file_type() == 'text' or ext in {'.py', '.js', '.css', '.json', '.sh', '.cpp', '.h', '.c', '.java', '.pas', '.sql', '.html', '.htm', '.xml', '.ts', '.php', '.md', '.txt', '.log', '.csv', '.cs', '.rb', '.go', '.rs', '.kt', '.swift'}:
        try:
            import os
            if os.path.exists(file_obj.file.path):
                encs = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5']
                content = None
                for enc in encs:
                    try:
                        with open(file_obj.file.path, 'r', encoding=enc) as f:
                            content = f.read()
                            break
                    except Exception:
                        continue
                if content is None:
                    with open(file_obj.file.path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()

                lang = ext[1:].lower() if ext.startswith('.') else ext.lower()
                if ext == '.py':
                    lang = 'python'
                elif ext == '.js':
                    lang = 'javascript'
                elif ext in ['.html', '.htm', '.xml']:
                    lang = 'markup'
                elif ext in ['.cpp', '.c', '.h', '.java', '.cs', '.pas']:
                    lang = 'clike'
                elif ext == '.css':
                    lang = 'css'
                elif ext == '.json':
                    lang = 'json'

                is_code = ext in {'.py', '.js', '.css', '.json', '.sh', '.cpp', '.h', '.c', '.java', '.pas', '.sql', '.html', '.htm', '.xml', '.ts', '.php', '.cs', '.rb', '.go', '.rs', '.kt', '.swift'}

                return JsonResponse({
                    'type': 'code' if is_code else 'text',
                    'content': content,
                    'lang': lang,
                    'file_name': file_obj.original_name,
                    'extension': ext
                })
        except Exception as e:
            return JsonResponse({
                'type': 'error',
                'message': f'Не вдалося прочитати файл: {str(e)}'
            })
            
    # 7. Дефолтна обробка для інших типів (зображення, pdf, відео, аудіо) -> перенаправляємо на inline-view
    from django.urls import reverse
    inline_view_url = reverse('file_view', args=[file_obj.id])
    return JsonResponse({
        'type': 'url',
        'url': inline_view_url,
        'file_type': file_obj.get_file_type()
    })


def server_stats(request):
    """
    API ендпоінт для PyQt6 лаунчера та моніторингу:
    Повертає статистику сервера та кількість підключених онлайн-клієнтів.
    """
    from .middleware import get_online_stats
    stats = get_online_stats()
    stats['status'] = 'running'
    stats['total_assignments'] = Assignment.objects.filter(status=Assignment.STATUS_PUBLISHED).count()
    return JsonResponse(stats)


from django.views.decorators.csrf import csrf_exempt


def client_heartbeat(request):
    """
    Періодичний heartbeat від браузера на всіх сторінках сайту.
    Оновлює активність та поточну сторінку клієнта.
    """
    import time
    from datetime import datetime
    from .middleware import get_client_ip, _active_clients, _lock
    
    ip = get_client_ip(request)
    current_path = request.GET.get('path', '/')
    now = time.time()
    with _lock:
        _active_clients[ip] = {
            'timestamp': now,
            'last_path': current_path,
            'last_time_str': datetime.now().strftime('%H:%M:%S')
        }
    return HttpResponse("OK")


@csrf_exempt
def client_disconnect(request):
    """
    Викликається браузером через navigator.sendBeacon при закритті вкладки/сайту,
    щоб миттєво відняти клієнта з лічильника онлайн.
    """
    from .middleware import get_client_ip, remove_client_ip
    ip = get_client_ip(request)
    remove_client_ip(ip)
    return HttpResponse("OK")





# ═══════════════════════════════════════════════════════════════════════════════
# ЗДАЧА РОБОТИ УЧНЕМ
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# ЗДАЧА РОБОТИ УЧНЕМ ТА ПУБЛІЧНИЙ ПЕРЕГЛЯД
# ═══════════════════════════════════════════════════════════════════════════════

def submit_assignment(request, pk):
    """
    Публічна сторінка для здачі роботи учнем.
    Відображає завдання та форму здачі.
    """
    assignment = get_object_or_404(
        Assignment.objects.select_related('teacher', 'subject').prefetch_related('classes', 'files'),
        pk=pk,
        status=Assignment.STATUS_PUBLISHED
    )

    initial_data = {}
    if assignment.is_individual and assignment.student_name:
        initial_data['full_name'] = assignment.student_name

    class_param = request.GET.get('class')
    if class_param:
        try:
            initial_data['class_group'] = ClassGroup.objects.get(id=class_param)
        except (ClassGroup.DoesNotExist, ValueError):
            pass
    elif assignment.classes.exists():
        initial_data['class_group'] = assignment.classes.all().order_by('grade', 'letter', 'name').first()

    form = SubmissionForm(assignment=assignment, initial=initial_data)

    if request.method == 'POST':
        form = SubmissionForm(request.POST, request.FILES, assignment=assignment)
        if form.is_valid():
            submission = form.save(assignment=assignment)
            dup_info = check_submission_duplicates(submission)
            if dup_info['is_duplicate']:
                request.session['submission_duplicate_warning'] = dup_info['warning_message']
                request.session['submission_duplicate_type'] = dup_info['type']

            # Зберігаємо ID останньої здачі для кнопки самоперевірки учня (ШІ)
            request.session['last_submission_id'] = submission.id

            log_submission_activity(
                None,
                'submission',
                f"Учень {submission.last_name} {submission.first_name} ({submission.class_group}) здав роботу: «{assignment.title}»",
                submission=submission
            )
            return redirect('submit_success', pk=assignment.pk)


    context = {
        'assignment': assignment,
        'form': form,
    }
    return render(request, 'feed/submit_assignment.html', context)



def submit_success(request, pk):
    """Сторінка підтвердження успішної здачі роботи."""
    assignment = get_object_or_404(Assignment, pk=pk, status=Assignment.STATUS_PUBLISHED)
    # Відображаємо тільки актуальні (останні) здачі учнів без дублювання записів
    submissions = Submission.objects.filter(assignment=assignment, is_latest_attempt=True).order_by('-submitted_at').select_related('class_group')
    
    search_query = request.GET.get('search', '').strip()
    if search_query:
        submissions = fuzzy_search_submissions(submissions, search_query)
        
    paginator = Paginator(submissions, 10)
    page_obj = paginator.get_page(request.GET.get('page'))

    duplicate_warning = request.session.pop('submission_duplicate_warning', None)
    duplicate_type = request.session.pop('submission_duplicate_type', None)

    # Визначаємо останню здачу для кнопки самоперевірки учня
    latest_submission_id = request.session.pop('last_submission_id', None)
    latest_submission = None
    can_student_ai_check = False
    ai_check_already_used = False
    if assignment.allow_student_ai_check and latest_submission_id:
        try:
            latest_submission = Submission.objects.get(pk=latest_submission_id, assignment=assignment)
            if latest_submission.has_used_student_ai_check_for_assignment():
                can_student_ai_check = False
                ai_check_already_used = True
            else:
                can_student_ai_check = True
        except Submission.DoesNotExist:
            pass
    
    return render(request, 'feed/submit_success.html', {
        'assignment': assignment,
        'page_obj': page_obj,
        'search_query': search_query,
        'duplicate_warning': duplicate_warning,
        'duplicate_type': duplicate_type,
        'latest_submission': latest_submission,
        'can_student_ai_check': can_student_ai_check,
        'ai_check_already_used': ai_check_already_used,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# САМОПЕРЕВІРКА УЧНЕМ (Student AI Self-Check — одноразова перевірка ШІ)
# ═══════════════════════════════════════════════════════════════════════════════

def student_ai_self_check(request, submission_id):
    """
    Одноразова перевірка роботи ШІ самим учнем після здачі.
    POST-запит (AJAX) → повертає JSON з результатом.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не підтримується'}, status=405)

    submission = get_object_or_404(
        Submission.objects.select_related('assignment', 'assignment__default_ai_preset', 'class_group'),
        id=submission_id
    )
    assignment = submission.assignment
    if not assignment or not assignment.allow_student_ai_check:
        return JsonResponse({'error': 'Самоперевірка не дозволена для цього завдання'}, status=403)

    # Одноразова перевірка на завдання — якщо учень вже перевіряв у цій чи будь-якій іншій спробі, відмовляємо
    if submission.has_used_student_ai_check_for_assignment():
        return JsonResponse({'error': 'Ви вже скористалися своєю спробою самоперевірки ШІ для цього завдання (дозволено лише 1 раз).'}, status=403)

    # Викликаємо Gemini AI
    try:
        from .gemini_service import evaluate_submission_with_gemini
        from .models import AISettings, AICriteriaPreset
        ai_settings = AISettings.objects.first()

        # Визначаємо preset та ГР завдання
        preset = assignment.default_ai_preset
        selected_gr_codes = None
        if assignment.default_ai_grs:
            import json as _json
            try:
                selected_gr_codes = _json.loads(assignment.default_ai_grs)
            except Exception:
                selected_gr_codes = None

        result = evaluate_submission_with_gemini(
            submission,
            ai_settings=ai_settings,
            criteria_preset=preset,
            selected_gr_codes=selected_gr_codes or [],
        )
    except Exception as e:
        return JsonResponse({'error': f'Помилка ШІ: {str(e)}'}, status=500)

    if not result or result.get('status') not in ('success', 'ok'):
        return JsonResponse({'error': result.get('error', 'ШІ не зміг перевірити роботу')}, status=500)

    # Зберігаємо чернову оцінку
    import json as _json
    is_traditional = bool(result.get('is_traditional') or (assignment.default_ai_preset and assignment.default_ai_preset.evaluation_type == 'traditional'))
    gr_results = [] if is_traditional else result.get('gr_results', [])

    submission.student_ai_checked = True
    submission.student_ai_checked_at = timezone.now()
    submission.student_ai_grade = str(result.get('suggested_grade', ''))
    submission.student_ai_level = result.get('level', '')
    submission.student_ai_summary = result.get('summary', '')
    submission.student_ai_feedback = result.get('feedback_comment', '')
    submission.student_ai_gr_results = _json.dumps(gr_results, ensure_ascii=False) if (gr_results and not is_traditional) else ''
    submission.save(update_fields=[
        'student_ai_checked', 'student_ai_checked_at',
        'student_ai_grade', 'student_ai_level', 'student_ai_summary',
        'student_ai_feedback', 'student_ai_gr_results',
    ])

    # Готуємо відповідь для учня (без технічних полів)
    student_gr_view = []
    if not is_traditional:
        for gr in gr_results:
            student_gr_view.append({
                'code': gr.get('code', ''),
                'name': gr.get('name', ''),
                'grade': gr.get('grade', ''),
                'comment': gr.get('comment', ''),
            })

    return JsonResponse({
        'ok': True,
        'grade': submission.student_ai_grade,
        'level': submission.student_ai_level,
        'summary': submission.student_ai_summary,
        'feedback': submission.student_ai_feedback,
        'is_traditional': is_traditional,
        'gr_results': student_gr_view,
        'ai_generated_detected': submission.ai_generated_detected,
        'ai_generated_confidence': submission.ai_generated_confidence,
        'ai_generated_details': submission.ai_generated_details,
        'allow_ai_usage': bool(assignment.allow_ai_usage),
    })


@teacher_required
def accept_student_ai_grade(request, sub_id):
    """
    Вчитель приймає чернову оцінку, виставлену ШІ за самоперевіркою учня.
    POST-запит (AJAX) → повертає JSON ok=True.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Метод не підтримується'}, status=405)

    submission = get_object_or_404(Submission, id=sub_id)
    teacher = request.user.teacher_profile
    if not (request.user.is_superuser or submission.teacher == teacher or
            (submission.assignment and submission.assignment.teacher == teacher)):
        return JsonResponse({'error': 'Немає доступу'}, status=403)

    if not submission.student_ai_checked or not submission.student_ai_grade:
        return JsonResponse({'error': 'Чернова оцінка відсутня'}, status=400)

    # Копіюємо чернову оцінку учня до основних полів
    submission.grade = submission.student_ai_grade
    submission.teacher_comment = submission.student_ai_feedback
    submission.ai_suggested_grade = submission.student_ai_grade
    submission.ai_score_level = submission.student_ai_level
    submission.ai_feedback = submission.student_ai_feedback
    submission.ai_gr_results = submission.student_ai_gr_results
    submission.ai_status = 'success'
    submission.graded_by = request.user
    submission.graded_at = timezone.now()
    submission.student_ai_accepted = True
    submission.save(update_fields=[
        'grade', 'teacher_comment',
        'ai_suggested_grade', 'ai_score_level', 'ai_feedback',
        'ai_gr_results', 'ai_status',
        'graded_by', 'graded_at', 'student_ai_accepted',
    ])

    log_submission_activity(
        request.user,
        'grade',
        f"Вчитель прийняв оцінку учнівської самоперевірки ШІ: «{submission.assignment.title if submission.assignment else ''}» — {submission.grade}",
        submission=submission
    )

    return JsonResponse({'ok': True, 'grade': submission.grade})




def submission_detail(request, submission_id):
    """
    Публічний або учнівський перегляд статусу здачі роботи (з історією коментарів вчителя).
    """
    submission = get_object_or_404(
        Submission.objects.select_related('assignment', 'class_group', 'teacher'),
        id=submission_id
    )
    comments = submission.comments.all().select_related('author').order_by('created_at')

    return render(request, 'feed/submission_detail.html', {
        'submission': submission,
        'comments': comments,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ПЕРЕГЛЯД ТА ОЦІНЮВАННЯ РОБІТ (ФЛАГМАНСЬКИЙ FILE VIEWER)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def view_file(request, submission_id):
    """
    Головний двоколонковий інтерактивний переглядач робіт:
    - Повний рендеринг будь-яких файлів (Word, Excel з рядком fx, PPTX, PDF, Code, Архіви, YouTube/Google Docs)
    - Швидка навігація («Попередня» / «Наступна») для потокової перевірки
    - Бокова панель оцінювання та діалогу з учнем через AJAX
    """
    submission = get_object_or_404(
        Submission.objects.select_related('assignment', 'class_group', 'teacher', 'assignment__teacher', 'assignment__subject').prefetch_related('assignment__classes', 'assignment__files', 'assignment__additional_links', 'assignment__youtube_links'),
        id=submission_id
    )

    teacher = request.user.teacher_profile

    # Перевірка прав доступу
    if not (request.user.is_superuser or (submission.teacher == teacher) or (submission.assignment and submission.assignment.teacher == teacher)):
        messages.error(request, "У вас немає доступу до цієї роботи.")
        return redirect('teacher_dashboard')

    # Обробка AJAX/POST запитів (оцінювання та коментарі)
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'comment':
            comment_text = request.POST.get('comment', '').strip()
            if comment_text:
                comment = SubmissionComment.objects.create(
                    submission=submission,
                    author=request.user,
                    text=comment_text
                )
                log_submission_activity(
                    request.user,
                    'comment',
                    f"Вчитель {teacher.full_name} додав коментар до роботи {submission.get_student_full_name()}",
                    submission=submission
                )

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({
                        'status': 'success',
                        'comment': {
                            'id': comment.id,
                            'text': comment.text,
                            'author': comment.get_author_name(),
                            'created_at': comment.created_at.strftime("%d.%m.%Y %H:%M")
                        }
                    })
                messages.success(request, 'Коментар додано!')
                return redirect('view_file', submission_id=submission_id)

        elif action == 'grade':
            grade = request.POST.get('grade', '').strip()
            if grade:
                submission.grade = grade
                submission.graded_by = request.user
                submission.graded_at = timezone.now()
                submission.save(update_fields=['grade', 'graded_by', 'graded_at'])

                log_submission_activity(
                    request.user,
                    'grading',
                    f"Вчитель {teacher.full_name} оцінив роботу {submission.get_student_full_name()}: {grade}",
                    submission=submission
                )

                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'status': 'success', 'grade': grade})

                messages.success(request, f'Оцінку {grade} успішно збережено!')
                return redirect('view_file', submission_id=submission_id)

    # Логіка навігації (Попередня / Наступна робота)
    assignment_id = request.GET.get('assignment') or (submission.assignment.id if submission.assignment else None)
    if assignment_id:
        nav_qs = Submission.objects.filter(assignment_id=assignment_id).order_by('-submitted_at')
    else:
        if request.user.is_superuser:
            nav_qs = Submission.objects.all().order_by('-submitted_at')
        else:
            nav_qs = Submission.objects.filter(
                Q(assignment__teacher=teacher) | Q(teacher=teacher)
            ).order_by('-submitted_at')

    submission_list = list(nav_qs)
    try:
        current_index = [s.id for s in submission_list].index(submission.id)
        prev_submission = submission_list[current_index - 1] if current_index > 0 else None
        next_submission = submission_list[current_index + 1] if current_index < len(submission_list) - 1 else None
    except ValueError:
        prev_submission = None
        next_submission = None

    # Отримуємо всі прикріплені файли здачі
    submission_files = list(submission.files.all())
    selected_file_obj = None
    file_id_param = request.GET.get('file_id')

    if file_id_param and submission_files:
        try:
            fid = int(file_id_param)
            for sf in submission_files:
                if sf.id == fid:
                    selected_file_obj = sf
                    break
        except (ValueError, TypeError):
            pass

    if not selected_file_obj and submission_files:
        selected_file_obj = submission_files[0]

    # Визначаємо порядковий номер, попередній та наступний файли
    active_file_num = 1
    prev_file_id = None
    next_file_id = None
    if selected_file_obj and submission_files:
        try:
            cur_idx = [sf.id for sf in submission_files].index(selected_file_obj.id)
            active_file_num = cur_idx + 1
            if cur_idx > 0:
                prev_file_id = submission_files[cur_idx - 1].id
            if cur_idx < len(submission_files) - 1:
                next_file_id = submission_files[cur_idx + 1].id
        except ValueError:
            pass

    all_image_files = [sf for sf in submission_files if sf.get_file_type() == 'image']

    # Визначення типу файлу та контенту
    file_type = 'unknown'
    content = None
    html_content = None
    archive_content = None
    error_message = None
    file_ext = ''
    embed_info = None

    active_file = None
    if selected_file_obj:
        active_file = selected_file_obj.file
        file_name = selected_file_obj.original_name or os.path.basename(selected_file_obj.file.name)
        file_ext = selected_file_obj.get_extension()
    elif submission.file:
        active_file = submission.file
        file_name = os.path.basename(submission.file.name)
        file_ext = submission.get_file_extension() or ''
    elif submission.link:
        file_name = submission.link
        file_type = 'link'
        embed_info = parse_embed_url(submission.link)
    else:
        file_name = 'Текстова відповідь учня'

    archive_files = ['.zip', '.rar', '.7z', '.tar', '.gz', '.tgz']
    office_preview = ['.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.odt', '.ods', '.odp', '.rtf']
    access_files = ['.mdb', '.accdb']
    image_files = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.tiff', '.tif', '.heic']
    video_files = ['.mp4', '.webm', '.ogv', '.mov', '.m4v', '.mkv', '.avi', '.3gp']
    audio_files = ['.mp3', '.wav', '.ogg', '.flac', '.aac']
    scratch_files = ['.sb3']
    microbit_files = ['.hex']
    text_files = [
        '.txt', '.text', '.log', '.csv', '.tsv', '.md', '.markdown', '.rst',
        '.py', '.pyw', '.ipynb', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.vue', '.svelte',
        '.html', '.htm', '.css', '.scss', '.sass', '.less', '.json', '.json5', '.sh', '.bash', '.zsh',
        '.cpp', '.c', '.cc', '.cxx', '.h', '.hpp', '.cs', '.java', '.kt', '.scala', '.groovy',
        '.xml', '.sql', '.pas', '.pp', '.dpr', '.php', '.rb', '.go', '.rs', '.swift', '.lua',
        '.ini', '.yaml', '.yml', '.toml', '.cfg', '.conf', '.env', '.tex', '.properties', '.gradle', '.bat', '.cmd', '.ps1'
    ]

    if active_file and hasattr(active_file, 'path') and os.path.exists(active_file.path):
        file_path = active_file.path
        from .gemini_service import is_text_file

        is_text = (file_ext in text_files) or (not file_ext and is_text_file(file_path)) or (
            file_ext not in (archive_files + office_preview + access_files + image_files + video_files + audio_files + scratch_files + microbit_files + ['.pdf']) and is_text_file(file_path)
        )

        if is_text:
            file_type = 'code' if file_ext and file_ext not in ['.txt', '.text', '.log'] else 'text'
            encodings = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5', 'latin-1']
            read_success = False
            for enc in encodings:
                try:
                    with open(file_path, 'r', encoding=enc) as f:
                        content = f.read()
                        read_success = True
                        break
                except (UnicodeDecodeError, UnicodeError):
                    continue
                except Exception as e:
                    content = f"Помилка при читанні файлу: {str(e)}"
                    read_success = True
                    break
            
            if not read_success:
                try:
                    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                        content = f.read()
                except Exception as e:
                    content = f"Помилка відкриття файлу: {str(e)}"

        elif file_ext in office_preview:
            file_type = 'office_preview'
            try:
                if file_ext in ['.docx', '.doc']:
                    html_content, error_message = convert_docx_to_html(file_path)
                elif file_ext in ['.xlsx', '.xls']:
                    html_content, error_message = convert_xlsx_to_html(file_path)
                elif file_ext in ['.pptx', '.ppt']:
                    html_content, error_message = convert_pptx_to_html(file_path)
                elif file_ext == '.odt':
                    html_content, error_message = convert_odt_to_html(file_path)
                elif file_ext == '.ods':
                    html_content, error_message = convert_ods_to_html(file_path)
                elif file_ext == '.odp':
                    html_content, error_message = convert_odp_to_html(file_path)
            except Exception as e:
                error_message = f"Помилка конвертації документа: {str(e)}"

        elif file_ext in archive_files:
            file_type = 'archive'
            archive_content, error_message = get_archive_content(file_path, file_ext)

        elif file_ext in access_files:
            file_type = 'access_db'
            try:
                html_content, error_message = convert_access_to_html(file_path)
            except Exception as e:
                error_message = f"Помилка обробки Access бази даних: {str(e)}"

        elif file_ext in scratch_files:
            file_type = 'scratch'
            try:
                from .scratch_utils import parse_scratch_sb3
                raw_url = request.build_absolute_uri(reverse('view_submission_raw_file', args=[submission.id]))
                if selected_file_obj:
                    raw_url += f"?file_id={selected_file_obj.id}"
                html_content, content, error_message = parse_scratch_sb3(file_path, raw_file_url=raw_url)
            except Exception as e:
                error_message = f"Помилка розбору Scratch проєкту: {str(e)}"

        elif file_ext in microbit_files:
            file_type = 'microbit'
            try:
                from .microbit_utils import parse_microbit_hex
                raw_url = request.build_absolute_uri(reverse('view_submission_raw_file', args=[submission.id]))
                if selected_file_obj:
                    raw_url += f"?file_id={selected_file_obj.id}"
                html_content, content, error_message = parse_microbit_hex(file_path, raw_file_url=raw_url)
            except Exception as e:
                error_message = f"Помилка розбору micro:bit файлу: {str(e)}"

        elif file_ext in video_files:
            file_type = 'video'

        elif file_ext in audio_files:
            file_type = 'audio'

        elif file_ext in image_files:
            file_type = 'image'

        elif file_ext in ['.pdf']:
            file_type = 'pdf'

        else:
            file_type = 'office'

    comments = submission.comments.all().select_related('author').order_by('created_at')

    AICriteriaPreset.ensure_default_presets()
    criteria_presets = AICriteriaPreset.objects.all()
    default_preset = AICriteriaPreset.objects.filter(is_default=True).first() or criteria_presets.first()

    dup_info = check_submission_duplicates(submission)

    context = {
        'submission': submission,
        'submission_files': submission_files,
        'selected_file_obj': selected_file_obj,
        'active_file_id': selected_file_obj.id if selected_file_obj else None,
        'active_file_num': active_file_num,
        'prev_file_id': prev_file_id,
        'next_file_id': next_file_id,
        'total_files_count': len(submission_files),
        'all_image_files': all_image_files,
        'active_file': active_file,
        'file_name': file_name,
        'file_ext': file_ext,
        'file_type': file_type,
        'content': content,
        'html_content': html_content,
        'archive_content': archive_content,
        'error_message': error_message,
        'embed_info': embed_info,
        'prev_submission': prev_submission,
        'next_submission': next_submission,
        'comments': comments,
        'assignment': submission.assignment,
        'assignment_id': assignment_id,
        'teacher': teacher,
        'criteria_presets': criteria_presets,
        'default_preset': default_preset,
        'dup_info': dup_info,
    }
    return render(request, 'feed/file_viewer.html', context)



@teacher_required
@xframe_options_exempt
def view_submission_raw_file(request, submission_id):
    """Служить сирий файл здачі учня безпосередньо у браузері."""
    submission = get_object_or_404(Submission, id=submission_id)
    teacher = request.user.teacher_profile

    if not (request.user.is_superuser or (submission.teacher == teacher) or (submission.assignment and submission.assignment.teacher == teacher)):
        return HttpResponse('Немає доступу', status=403)

    target_file = None
    file_id = request.GET.get('file_id')
    if file_id:
        try:
            sf = submission.files.filter(id=int(file_id)).first()
            if sf and sf.file:
                target_file = sf.file
        except (ValueError, TypeError):
            pass

    if not target_file:
        first_sf = submission.files.first()
        if first_sf and first_sf.file:
            target_file = first_sf.file
        elif submission.file:
            target_file = submission.file

    if not target_file:
        return HttpResponse('Файл не знайдено', status=404)

    try:
        import mimetypes
        path = target_file.path
        ext = os.path.splitext(path)[1].lower()
        if ext == '.pdf':
            mime_type = 'application/pdf'
        elif ext in ['.jpg', '.jpeg']:
            mime_type = 'image/jpeg'
        elif ext == '.png':
            mime_type = 'image/png'
        elif ext == '.webp':
            mime_type = 'image/webp'
        elif ext == '.svg':
            mime_type = 'image/svg+xml'
        elif ext in ['.mp4', '.m4v']:
            mime_type = 'video/mp4'
        elif ext == '.webm':
            mime_type = 'video/webm'
        elif ext == '.ogv':
            mime_type = 'video/ogg'
        elif ext == '.mov':
            mime_type = 'video/quicktime'
        elif ext == '.mkv':
            mime_type = 'video/x-matroska'
        elif ext == '.avi':
            mime_type = 'video/x-msvideo'
        elif ext == '.3gp':
            mime_type = 'video/3gpp'
        elif ext == '.mp3':
            mime_type = 'audio/mpeg'
        elif ext == '.wav':
            mime_type = 'audio/wav'
        elif ext == '.ogg':
            mime_type = 'audio/ogg'
        elif ext == '.sb3':
            mime_type = 'application/x.scratch.sb3'
        elif ext == '.hex':
            mime_type = 'text/plain; charset=utf-8'
        else:
            mime_type, _ = mimetypes.guess_type(path)
            if not mime_type:
                from .gemini_service import is_text_file
                if is_text_file(path):
                    mime_type = 'text/plain; charset=utf-8'
                else:
                    mime_type = 'application/octet-stream'

        if os.path.exists(path):
            with open(path, 'rb') as f:
                response = HttpResponse(f.read(), content_type=mime_type)
            filename = os.path.basename(target_file.name)
            response['Content-Disposition'] = f'inline; filename="{filename}"'
            response['X-Frame-Options'] = 'SAMEORIGIN'
            response['Access-Control-Allow-Origin'] = '*'
            response['Access-Control-Allow-Methods'] = 'GET, OPTIONS'
            response['Access-Control-Allow-Headers'] = '*'
            return response
    except Exception as e:
        return HttpResponse(f'Помилка завантаження: {str(e)}', status=500)


@teacher_required
def download_submission_files_zip(request, sub_id):
    """Завантажує всі прикріплені файли однієї здачі учня у вигляді ZIP-архіву."""
    submission = get_object_or_404(Submission.objects.select_related('assignment', 'class_group', 'teacher'), id=sub_id)
    teacher = request.user.teacher_profile

    if not (request.user.is_superuser or submission.teacher == teacher or (submission.assignment and submission.assignment.teacher == teacher)):
        messages.error(request, "У вас немає доступу до цієї здачі.")
        return redirect('teacher_dashboard')

    sub_files = list(submission.files.all())
    if not sub_files and not submission.file:
        messages.warning(request, "У цій здачі немає завантажених файлів.")
        return redirect('view_file', sub_id=sub_id)

    # Якщо прикріплено лише 1 файл — віддаємо його напряму
    if len(sub_files) == 1 and sub_files[0].file and os.path.exists(sub_files[0].file.path):
        from django.http import FileResponse
        return FileResponse(open(sub_files[0].file.path, 'rb'), as_attachment=True, filename=sub_files[0].original_name)
    if not sub_files and submission.file and os.path.exists(submission.file.path):
        from django.http import FileResponse
        return FileResponse(open(submission.file.path, 'rb'), as_attachment=True, filename=os.path.basename(submission.file.name))

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for sf in sub_files:
            if sf.file and os.path.exists(sf.file.path):
                fname = sf.original_name or os.path.basename(sf.file.name)
                zip_file.write(sf.file.path, fname)

    zip_buffer.seek(0)
    student_name = f"{submission.last_name}_{submission.first_name}"
    filename = f"Robota_{student_name}.zip"
    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response



@teacher_required
@require_POST
def delete_comment(request, comment_id):
    """AJAX: видалення коментаря вчителя."""
    comment = get_object_or_404(SubmissionComment, id=comment_id)

    if comment.author == request.user or request.user.is_superuser:
        comment.delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'status': 'success'})
        messages.success(request, 'Коментар видалено!')
        return redirect('view_file', submission_id=comment.submission_id)

    return JsonResponse({'status': 'error', 'message': 'Немає прав на видалення'}, status=403)


# ═══════════════════════════════════════════════════════════════════════════════
# ПЕРЕГЛЯД ЗДАЧ ПО ЗАВДАННЮ ТА ЗАГАЛЬНИЙ ДАШБОРД
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def assignment_submissions(request, pk):
    """
    Сторінка вчителя: всі здачі робіт по конкретному завданню.
    """
    teacher = request.user.teacher_profile
    assignment = get_object_or_404(
        Assignment.objects.prefetch_related('classes', 'files'),
        pk=pk
    )

    if not (request.user.is_superuser or assignment.teacher == teacher):
        messages.error(request, 'У вас немає доступу до цього завдання.')
        return redirect('teacher_dashboard')

    show_mode = request.GET.get('mode', 'grouped')

    submissions_qs = Submission.objects.filter(
        assignment=assignment
    ).select_related('class_group', 'teacher', 'previous_submission').prefetch_related('comments', 'files', 'subsequent_submissions').order_by('-submitted_at')

    if show_mode == 'grouped':
        submissions_qs = submissions_qs.filter(is_latest_attempt=True)

    # Фільтр по класу
    class_filter = request.GET.get('class')
    selected_class_id = None
    if class_filter:
        try:
            selected_class_id = int(class_filter)
            submissions_qs = submissions_qs.filter(class_group_id=selected_class_id)
        except (ValueError, TypeError):
            pass

    # Фільтр по оцінці
    grade_filter = request.GET.get('grade_filter', 'all')
    if grade_filter == 'ungraded':
        submissions_qs = submissions_qs.filter(Q(grade__isnull=True) | Q(grade=''))
    elif grade_filter == 'graded':
        submissions_qs = submissions_qs.exclude(grade__isnull=True).exclude(grade='')

    # Пошук
    search_query = request.GET.get('search', '').strip()
    if search_query:
        submissions_qs = fuzzy_search_submissions(submissions_qs, search_query)

    # Пагінація
    paginator = Paginator(submissions_qs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Статистика
    all_sub = Submission.objects.filter(assignment=assignment, is_latest_attempt=True)
    total_count = all_sub.count()
    graded_count = all_sub.exclude(grade__isnull=True).exclude(grade='').count()
    ungraded_count = total_count - graded_count

    # Розподіл оцінок та середня успішність
    grade_dist = {'low': 0, 'medium': 0, 'good': 0, 'excellent': 0}
    numeric_scores = []
    for s in all_sub:
        if s.grade and s.grade.isdigit():
            val = int(s.grade)
            numeric_scores.append(val)
            if val >= 10:
                grade_dist['excellent'] += 1
            elif val >= 7:
                grade_dist['good'] += 1
            elif val >= 4:
                grade_dist['medium'] += 1
            else:
                grade_dist['low'] += 1

    avg_score = round(sum(numeric_scores) / len(numeric_scores), 1) if numeric_scores else None

    assigned_classes = assignment.classes.all().order_by('grade', 'letter')

    # Пошук учнів класу, які ще не здали це завдання (боржники)
    from .student_matcher import is_same_student_identity
    submitted_students = list(all_sub.values_list('last_name', 'first_name', 'class_group_id'))
    
    classes_to_check = [selected_class_id] if selected_class_id else list(assigned_classes.values_list('id', flat=True))
    all_class_students = Submission.objects.filter(class_group_id__in=classes_to_check).values('last_name', 'first_name', 'class_group__name', 'class_group_id').distinct()


    unsubmitted_students = []
    seen_unsub = set()
    for cs in all_class_students:
        c_last = cs['last_name'].strip()
        c_first = cs['first_name'].strip()
        c_cls_id = cs['class_group_id']
        key = (c_last.lower(), c_first.lower(), c_cls_id)
        if key in seen_unsub:
            continue
        seen_unsub.add(key)

        has_submitted = False
        for s_last, s_first, s_cls_id in submitted_students:
            if s_cls_id == c_cls_id and is_same_student_identity(c_last, c_first, s_last, s_first):
                has_submitted = True
                break

        if not has_submitted:
            unsubmitted_students.append({
                'last_name': c_last,
                'first_name': c_first,
                'full_name': f"{c_last} {c_first}".strip(),
                'class_name': cs['class_group__name'],
            })

    unsubmitted_students.sort(key=lambda x: (x['class_name'], x['last_name'].lower()))

    context = {
        'assignment': assignment,
        'page_obj': page_obj,
        'show_mode': show_mode,
        'total_count': total_count,
        'graded_count': graded_count,
        'ungraded_count': ungraded_count,
        'assigned_classes': assigned_classes,
        'selected_class_id': selected_class_id,
        'grade_filter': grade_filter,
        'search_query': search_query,
        'teacher': teacher,
        'grade_dist': grade_dist,
        'avg_score': avg_score,
        'unsubmitted_students': unsubmitted_students,
    }
    return render(request, 'feed/assignment_submissions.html', context)



@teacher_required
def all_submissions_dashboard(request):
    """
    Загальна таблиця всіх здач робіт для вчителя з розумним пошуком, фільтрами та сортуванням.
    """
    teacher = request.user.teacher_profile

    if request.user.is_superuser:
        submissions_qs = Submission.objects.all()
    else:
        submissions_qs = Submission.objects.filter(
            Q(assignment__teacher=teacher) | Q(teacher=teacher)
        )

    submissions_qs = submissions_qs.select_related(
        'assignment', 'class_group', 'teacher'
    ).prefetch_related('comments').order_by('-submitted_at')

    class_filter = request.GET.get('class')
    assignment_filter = request.GET.get('assignment')
    grade_filter = request.GET.get('grade_filter', 'all')
    search_query = request.GET.get('search', '').strip()
    date_filter = request.GET.get('date', '')

    selected_class_id = None
    if class_filter:
        try:
            selected_class_id = int(class_filter)
            submissions_qs = submissions_qs.filter(class_group_id=selected_class_id)
        except (ValueError, TypeError):
            pass

    selected_assignment_id = None
    if assignment_filter:
        try:
            selected_assignment_id = int(assignment_filter)
            submissions_qs = submissions_qs.filter(assignment_id=selected_assignment_id)
        except (ValueError, TypeError):
            pass

    if grade_filter == 'ungraded':
        submissions_qs = submissions_qs.filter(Q(grade__isnull=True) | Q(grade=''))
    elif grade_filter == 'graded':
        submissions_qs = submissions_qs.exclude(grade__isnull=True).exclude(grade='')

    if search_query:
        submissions_qs = fuzzy_search_submissions(submissions_qs, search_query)

    if date_filter:
        try:
            from datetime import datetime as dt
            filter_date = dt.strptime(date_filter, '%Y-%m-%d').date()
            submissions_qs = submissions_qs.filter(submitted_at__date=filter_date)
        except (ValueError, TypeError):
            date_filter = ''

    sort_by = request.GET.get('sort', 'date')
    sort_order = request.GET.get('order', 'desc')

    if sort_by == 'student':
        order = ['-last_name', '-first_name'] if sort_order == 'desc' else ['last_name', 'first_name']
    elif sort_by == 'class':
        order = ['-class_group__name'] if sort_order == 'desc' else ['class_group__name']
    elif sort_by == 'assignment':
        order = ['-assignment__title'] if sort_order == 'desc' else ['assignment__title']
    elif sort_by == 'grade':
        order = ['-grade'] if sort_order == 'desc' else ['grade']
    else:
        order = ['-submitted_at'] if sort_order == 'desc' else ['submitted_at']

    submissions_qs = submissions_qs.order_by(*order)

    paginator = Paginator(submissions_qs, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    if request.user.is_superuser:
        all_classes = ClassGroup.objects.all().order_by('grade', 'letter')
        all_assignments = Assignment.objects.all().order_by('-published_at')[:100]
    else:
        teacher_class_ids = teacher.classes.values_list('id', flat=True)
        all_classes = ClassGroup.objects.filter(id__in=teacher_class_ids).order_by('grade', 'letter')
        all_assignments = Assignment.objects.filter(teacher=teacher).order_by('-published_at')[:100]

    all_sub = submissions_qs
    total = paginator.count
    graded = all_sub.exclude(grade__isnull=True).exclude(grade='').count()

    context = {
        'page_obj': page_obj,
        'total_count': total,
        'graded_count': graded,
        'ungraded_count': total - graded,
        'all_classes': all_classes,
        'all_assignments': all_assignments,
        'selected_class_id': selected_class_id,
        'selected_assignment_id': selected_assignment_id,
        'grade_filter': grade_filter,
        'search_query': search_query,
        'date_filter': date_filter,
        'sort_by': sort_by,
        'sort_order': sort_order,
        'teacher': teacher,
    }
    return render(request, 'feed/all_submissions_dashboard.html', context)


def sync_grades_to_coauthors(submission, grade, graded_by_user):
    """
    Автоматично проставляє оцінку всім співавторам групової роботи без повторень.
    Повертає список імен оновлених/створених робіт співавторів.
    """
    from .student_matcher import extract_coauthors_from_comment, is_same_student_identity
    from .models import Submission, Student

    if not submission.assignment or not grade:
        return []

    coauthors = extract_coauthors_from_comment(
        submission.comment_student,
        class_group=submission.class_group,
        exclude_last_name=submission.last_name,
        exclude_first_name=submission.first_name,
    )

    graded_names = []
    processed_identities = set()

    for co in coauthors:
        co_st = co.get('student')
        co_ln = co['last_name']
        co_fn = co['first_name']

        ident_key = f"{co_ln.lower()}_{co_fn.lower()}"
        if ident_key in processed_identities:
            continue
        processed_identities.add(ident_key)

        # Шукаємо чи є вже здача від цього співавтора на це завдання
        existing_sub = None
        if co_st:
            existing_sub = Submission.objects.filter(
                assignment=submission.assignment,
                class_group=submission.class_group,
                student=co_st
            ).exclude(pk=submission.pk).first()

        if not existing_sub:
            for s in Submission.objects.filter(assignment=submission.assignment, class_group=submission.class_group).exclude(pk=submission.pk):
                if is_same_student_identity(co_ln, co_fn, s.last_name, s.first_name):
                    existing_sub = s
                    break

        if existing_sub:
            existing_sub.grade = grade
            existing_sub.graded_by = graded_by_user
            existing_sub.graded_at = timezone.now()
            if submission.teacher_comment:
                existing_sub.teacher_comment = submission.teacher_comment
            existing_sub.save(update_fields=['grade', 'graded_by', 'graded_at', 'teacher_comment'])
            graded_names.append(existing_sub.get_student_full_name())
        else:
            new_sub = Submission.objects.create(
                assignment=submission.assignment,
                student=co_st,
                first_name=co_fn,
                last_name=co_ln,
                class_group=submission.class_group,
                teacher=submission.teacher,
                file=submission.file,
                link=submission.link,
                comment_student=f"Групова робота (спільно з {submission.get_student_full_name()})",
                teacher_comment=submission.teacher_comment,
                grade=grade,
                graded_by=graded_by_user,
                graded_at=timezone.now(),
                ai_suggested_grade=submission.ai_suggested_grade,
                ai_score_level=submission.ai_score_level,
                ai_feedback=submission.ai_feedback,
                ai_status=submission.ai_status,
            )
            graded_names.append(new_sub.get_student_full_name())

    return graded_names


@teacher_required
def grade_submission(request, sub_id):
    """AJAX: виставлення або зміна оцінки здачі роботи з підтримкою співавторів."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Method not allowed'}, status=405)

    submission = get_object_or_404(Submission, id=sub_id)
    teacher = request.user.teacher_profile

    if not (request.user.is_superuser or (submission.teacher == teacher) or (submission.assignment and submission.assignment.teacher == teacher)):
        return JsonResponse({'status': 'error', 'message': 'Немає доступу'}, status=403)

    grade = request.POST.get('grade', '').strip()
    submission.grade = grade or None
    submission.graded_by = request.user
    if grade:
        submission.graded_at = timezone.now()
    submission.save(update_fields=['grade', 'graded_by', 'graded_at'])

    coauthors_graded = []
    if grade:
        coauthors_graded = sync_grades_to_coauthors(submission, grade, request.user)

    log_msg = f"Оцінено роботу {submission.get_student_full_name()} -> {grade or 'оцінку знято'}"
    if coauthors_graded:
        log_msg += f" (також виставлено оцінку співавторам: {', '.join(coauthors_graded)})"

    log_submission_activity(
        request.user,
        'grading',
        log_msg,
        submission=submission
    )

    return JsonResponse({
        'status': 'success',
        'grade': submission.grade or '',
        'is_graded': bool(submission.grade),
        'coauthors_graded': coauthors_graded,
        'message': log_msg,
    })


@teacher_required
def add_submission_comment(request, sub_id):
    """AJAX: додавання коментаря вчителя до здачі роботи."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error'}, status=405)

    submission = get_object_or_404(Submission, id=sub_id)
    teacher = request.user.teacher_profile

    if not (request.user.is_superuser or (submission.teacher == teacher) or (submission.assignment and submission.assignment.teacher == teacher)):
        return JsonResponse({'status': 'error', 'message': 'Немає доступу'}, status=403)

    text = request.POST.get('text', '').strip()
    if not text:
        return JsonResponse({'status': 'error', 'message': 'Порожній коментар'}, status=400)

    comment = SubmissionComment.objects.create(
        submission=submission,
        author=request.user,
        text=text
    )

    log_submission_activity(
        request.user,
        'comment',
        f"Коментар до роботи {submission.get_student_full_name()}: {text[:50]}",
        submission=submission
    )

    return JsonResponse({
        'status': 'success',
        'comment': {
            'id': comment.id,
            'author': comment.get_author_name(),
            'text': comment.text,
            'created_at': comment.created_at.strftime('%d.%m.%Y %H:%M'),
        }
    })


@teacher_required
def delete_submission(request, sub_id):
    """Видалення здачі роботи (POST)."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error'}, status=405)

    submission = get_object_or_404(Submission, id=sub_id)
    teacher = request.user.teacher_profile
    assignment_pk = submission.assignment_id

    if not (request.user.is_superuser or (submission.teacher == teacher) or (submission.assignment and submission.assignment.teacher == teacher)):
        messages.error(request, 'Немає доступу.')
        return redirect('teacher_dashboard')

    if submission.file:
        try:
            if os.path.exists(submission.file.path):
                os.remove(submission.file.path)
        except Exception:
            pass

    student_name = submission.get_student_full_name()
    submission.delete()
    messages.success(request, f'Здачу від {student_name} видалено.')

    if assignment_pk:
        return redirect('assignment_submissions', pk=assignment_pk)
    return redirect('all_submissions_dashboard')


# ═══════════════════════════════════════════════════════════════════════════════
# КЕРУВАННЯ УЧНЯМИ ТА КЛАСАМИ (STUDENT ROSTER & PROFILE MANAGEMENT)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def teacher_students(request):
    """
    Панель керування учнями та класами для вчителів:
    - Перегляд учнів по класах
    - Редагування імен/прізвищ учнів (виправлення помилок)
    - Зміна класу учня з автоматичною синхронізацією робіт та журналу
    - Додавання нового учня вручну
    - Імпорт списків учнів та класів з файлу / тексту
    - Експорт списків учнів у CSV
    """
    from .student_importer import sync_all_existing_submissions_to_students
    
    # Автоматично синхронізуємо наявні здачі в учнів при першому запуску, якщо таблиця пуста
    if not Student.objects.exists() and Submission.objects.exists():
        sync_all_existing_submissions_to_students()

    teacher = request.user.teacher_profile if hasattr(request.user, 'teacher_profile') else None

    # Отримуємо доступні класи (суперадмін бачить усі, вчитель — свої або всі для школи)
    if request.user.is_superuser:
        all_class_groups = list(ClassGroup.objects.all().order_by('grade', 'letter'))
    elif teacher and teacher.classes.exists():
        my_classes = list(teacher.classes.all().order_by('grade', 'letter'))
        other_classes = list(ClassGroup.objects.exclude(id__in=[c.id for c in my_classes]).order_by('grade', 'letter'))
        all_class_groups = my_classes + other_classes
    else:
        all_class_groups = list(ClassGroup.objects.all().order_by('grade', 'letter'))

    selected_class_id = request.GET.get('class_group') or request.GET.get('class')
    search_query = request.GET.get('search', '').strip()

    students_qs = Student.objects.select_related('class_group').order_by('class_group__grade', 'class_group__letter', 'last_name', 'first_name')

    selected_class = None
    if selected_class_id:
        try:
            cid = int(selected_class_id)
            students_qs = students_qs.filter(class_group_id=cid)
            selected_class = ClassGroup.objects.filter(id=cid).first()
        except (ValueError, TypeError):
            pass

    if search_query:
        tokens = search_query.split()
        if len(tokens) == 1:
            q_token = tokens[0]
            students_qs = students_qs.filter(
                Q(last_name__icontains=q_token) |
                Q(first_name__icontains=q_token) |
                Q(class_group__name__icontains=q_token)
            )
        else:
            q_first, q_last = tokens[0], tokens[1]
            students_qs = students_qs.filter(
                (Q(last_name__icontains=q_first) & Q(first_name__icontains=q_last)) |
                (Q(last_name__icontains=q_last) & Q(first_name__icontains=q_first)) |
                Q(last_name__icontains=search_query) |
                Q(first_name__icontains=search_query)
            )

    # Статистика
    total_students_count = Student.objects.count()
    total_classes_count = ClassGroup.objects.count()
    total_submissions_count = Submission.objects.count()

    # Кількість учнів у класах для бейджів
    from django.db.models import Count
    class_counts_map = dict(Student.objects.values('class_group_id').annotate(c=Count('id')).values_list('class_group_id', 'c'))
    for cg in all_class_groups:
        cg.students_count = class_counts_map.get(cg.id, 0)
        cg.is_selected = bool(selected_class and cg.id == selected_class.id)

    # Список учнів з підрахунком робіт
    students_list = list(students_qs)
    submissions_by_class = {}
    for sub in Submission.objects.filter(class_group_id__in=[s.class_group_id for s in students_list if s.class_group_id]).values('id', 'last_name', 'first_name', 'class_group_id', 'grade'):
        cid = sub['class_group_id']
        if cid not in submissions_by_class:
            submissions_by_class[cid] = []
        submissions_by_class[cid].append(sub)

    from .student_matcher import is_same_student_identity
    for st in students_list:
        c_subs = submissions_by_class.get(st.class_group_id, [])
        st_subs = [s for s in c_subs if is_same_student_identity(st.last_name, st.first_name, s['last_name'], s['first_name'])]
        st.subs_count = len(st_subs)
        st.graded_count = len([s for s in st_subs if s.get('grade')])

    paginator = Paginator(students_list, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    # Форми
    add_form = StudentForm(initial={'class_group': selected_class} if selected_class else None)
    import_form = StudentImportForm(initial={'default_class': selected_class} if selected_class else None)

    current_tab = request.GET.get('tab', 'students')

    # Обробка дій через POST
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add_student':
            add_form = StudentForm(request.POST)
            if add_form.is_valid():
                new_st = add_form.save()
                if add_form.cleaned_data.get('sync_submissions', True):
                    new_st.sync_submissions()
                log_submission_activity(
                    request.user,
                    'student_created',
                    f"Додано нового учня «{new_st.get_full_name()}» до класу {new_st.class_group.name}"
                )
                messages.success(request, f"Учня «{new_st.get_full_name()}» успішно додано до класу {new_st.class_group.name}!")
                return redirect(f"{reverse('teacher_students')}?class_group={new_st.class_group_id}")

        elif action == 'save_teacher_schedule' and teacher:
            bell_slots_all = BellSchedule.objects.all().order_by('order', 'lesson_number')
            days_list = [1, 2, 3, 4, 5, 6]
            saved_count = 0
            cleared_count = 0

            for day_num in days_list:
                for slot in bell_slots_all:
                    class_key = f"cell_{day_num}_{slot.id}_class"
                    subject_key = f"cell_{day_num}_{slot.id}_subject"

                    class_id_str = request.POST.get(class_key, '').strip()
                    subject_id_str = request.POST.get(subject_key, '').strip()

                    if class_id_str and class_id_str.isdigit():
                        class_id = int(class_id_str)
                        subject_id = int(subject_id_str) if (subject_id_str and subject_id_str.isdigit()) else None
                        TeacherLessonSchedule.objects.update_or_create(
                            teacher=teacher,
                            day_of_week=day_num,
                            bell_slot=slot,
                            defaults={
                                'class_group_id': class_id,
                                'subject_id': subject_id
                            }
                        )
                        saved_count += 1
                    else:
                        deleted, _ = TeacherLessonSchedule.objects.filter(
                            teacher=teacher,
                            day_of_week=day_num,
                            bell_slot=slot
                        ).delete()
                        if deleted:
                            cleared_count += 1

            messages.success(request, f"Тижневий розклад уроків збережено! Зафіксовано {saved_count} уроків на тиждень.")
            return redirect(f"{reverse('teacher_students')}?tab=schedule")

        elif action == 'clear_teacher_schedule' and teacher:
            TeacherLessonSchedule.objects.filter(teacher=teacher).delete()
            messages.success(request, "Ваш тижневий розклад уроків успішно очищено.")
            return redirect(f"{reverse('teacher_students')}?tab=schedule")

    # Дані для розкладу уроків вчителя
    bell_slots = BellSchedule.objects.all().order_by('order', 'lesson_number')
    days_of_week = [
        (1, 'Понеділок'),
        (2, 'Вівторок'),
        (3, 'Середа'),
        (4, 'Четвер'),
        (5, "П'ятниця"),
    ]
    # Якщо у вчителя призначено уроки на суботу або обрано суботу
    if teacher and TeacherLessonSchedule.objects.filter(teacher=teacher, day_of_week=6).exists():
        days_of_week.append((6, 'Субота'))

    teacher_classes = []
    if teacher and teacher.classes.exists():
        teacher_classes = list(teacher.classes.all().order_by('grade', 'letter'))
    else:
        teacher_classes = all_class_groups

    teacher_subjects = []
    if teacher and teacher.subjects.exists():
        teacher_subjects = list(teacher.subjects.all().order_by('name'))
    else:
        teacher_subjects = list(Subject.objects.all().order_by('name'))

    schedule_rows = []
    total_scheduled_lessons = 0
    if teacher:
        schedules = TeacherLessonSchedule.objects.filter(teacher=teacher).select_related('bell_slot', 'class_group', 'subject')
        total_scheduled_lessons = schedules.count()
        schedule_map = {(s.day_of_week, s.bell_slot_id): s for s in schedules}
        for slot in bell_slots:
            row_days = []
            for day_num, day_name in days_of_week:
                entry = schedule_map.get((day_num, slot.id))
                row_days.append({
                    'day_num': day_num,
                    'day_name': day_name,
                    'entry': entry,
                    'class_id': entry.class_group_id if entry else None,
                    'subject_id': entry.subject_id if entry else None,
                })
            schedule_rows.append({
                'slot': slot,
                'days': row_days,
            })

    return render(request, 'feed/teacher_students.html', {
        'page_obj': page_obj,
        'students_list': students_list,
        'class_groups': all_class_groups,
        'selected_class': selected_class,
        'selected_class_id': selected_class_id,
        'search_query': search_query,
        'total_students_count': total_students_count,
        'total_classes_count': total_classes_count,
        'total_submissions_count': total_submissions_count,
        'add_form': add_form,
        'import_form': import_form,
        'current_tab': current_tab,
        'bell_slots': bell_slots,
        'days_of_week': days_of_week,
        'teacher_classes': teacher_classes,
        'teacher_subjects': teacher_subjects,
        'schedule_rows': schedule_rows,
        'total_scheduled_lessons': total_scheduled_lessons,
    })


@teacher_required
def teacher_student_edit(request, student_id):
    """
    Редагування профілю учня:
    - Зміна прізвища або імені (виправлення одруків)
    - Зміна класу учня з автоматичним перенесенням зданих робіт та журналу
    """
    student = get_object_or_404(Student, id=student_id)
    old_last_name = student.last_name
    old_first_name = student.first_name
    old_class_group = student.class_group

    if request.method == 'POST':
        form = StudentForm(request.POST, instance=student)
        if form.is_valid():
            updated_student = form.save()
            sync_subs = form.cleaned_data.get('sync_submissions', True)

            updated_subs_count = 0
            if sync_subs:
                updated_subs_count = updated_student.sync_submissions(
                    old_last_name=old_last_name,
                    old_first_name=old_first_name,
                    old_class_group=old_class_group
                )

            changes_desc = []
            if old_last_name != updated_student.last_name or old_first_name != updated_student.first_name:
                changes_desc.append(f"ПІБ змінено з «{old_last_name} {old_first_name}» на «{updated_student.get_full_name()}»")
            if old_class_group != updated_student.class_group:
                changes_desc.append(f"Клас змінено з {old_class_group.name if old_class_group else '—'} на {updated_student.class_group.name} (перенесено {updated_subs_count} робіт)")

            log_desc = "; ".join(changes_desc) or f"Оновлено дані учня {updated_student.get_full_name()}"
            log_submission_activity(request.user, 'student_updated', log_desc)

            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('format') == 'json':
                return JsonResponse({
                    'success': True,
                    'message': f"Профіль учня «{updated_student.get_full_name()}» успішно оновлено!",
                    'student': {
                        'id': updated_student.id,
                        'first_name': updated_student.first_name,
                        'last_name': updated_student.last_name,
                        'full_name': updated_student.get_full_name(),
                        'class_name': updated_student.class_group.name,
                        'class_id': updated_student.class_group.id,
                        'synced_subs_count': updated_subs_count,
                    }
                })

            messages.success(request, f"Профіль учня «{updated_student.get_full_name()}» успішно оновлено! {('Синхронізовано ' + str(updated_subs_count) + ' зданих робіт.') if updated_subs_count else ''}")
            return redirect(f"{reverse('teacher_students')}?class_group={updated_student.class_group_id}")
        else:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('format') == 'json':
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)

    else:
        form = StudentForm(instance=student)

    return render(request, 'feed/teacher_student_edit.html', {
        'form': form,
        'student': student,
    })


@teacher_required
@require_POST
def teacher_student_delete(request, student_id):
    """Видаляє учня зі списку класу."""
    student = get_object_or_404(Student, id=student_id)
    class_id = student.class_group_id
    st_name = student.get_full_name()
    class_name = student.class_group.name if student.class_group else '—'

    # Відв'язуємо FK у зданих роботах, зберігаючи історію
    Submission.objects.filter(student=student).update(student=None)
    student.delete()

    log_submission_activity(request.user, 'student_deleted', f"Видалено учня «{st_name}» з класу {class_name}")
    messages.success(request, f"Учня «{st_name}» видалено зі списку класу {class_name}.")
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'success': True, 'message': f"Учня «{st_name}» успішно видалено."})

    return redirect(f"{reverse('teacher_students')}?class_group={class_id}")


@teacher_required
def teacher_students_import(request):
    """
    Обробка імпорту списку учнів та класів з файлу або вставки тексту.
    """
    from .student_importer import import_students_from_file, import_students_from_text

    teacher = request.user.teacher_profile if hasattr(request.user, 'teacher_profile') else None

    if request.method == 'POST':
        form = StudentImportForm(request.POST, request.FILES)
        if form.is_valid():
            import_mode = form.cleaned_data.get('import_mode')
            default_class = form.cleaned_data.get('default_class')
            create_missing = form.cleaned_data.get('create_missing_classes', True)

            if import_mode == 'file':
                uploaded_file = form.cleaned_data.get('file')
                if not uploaded_file:
                    messages.error(request, "Будь ласка, оберіть файл для завантаження.")
                    return redirect('teacher_students')
                result = import_students_from_file(
                    uploaded_file,
                    default_class=default_class,
                    teacher=teacher,
                    create_missing_classes=create_missing
                )
            else:
                raw_text = form.cleaned_data.get('raw_text', '')
                if not raw_text.strip():
                    messages.error(request, "Будь ласка, вставте текст зі списком учнів.")
                    return redirect('teacher_students')
                result = import_students_from_text(
                    raw_text,
                    default_class=default_class,
                    teacher=teacher,
                    create_missing_classes=create_missing
                )

            created_cnt = result['created_count']
            existing_cnt = result['existing_count']
            created_classes = result['created_classes']
            errors = result['errors']

            cls_msg = f", створено класів: {len(created_classes)} ({', '.join(created_classes)})" if created_classes else ""
            msg = f"🎉 Імпорт завершено! Додано нових учнів: {created_cnt}, оновлено наявних: {existing_cnt}{cls_msg}."
            messages.success(request, msg)

            if errors:
                for err in errors[:5]:
                    messages.warning(request, err)

            log_submission_activity(
                request.user,
                'students_imported',
                f"Імпортовано учнів: {created_cnt} нових, {existing_cnt} оновлено{cls_msg}"
            )

            target_redirect = reverse('teacher_students')
            if default_class:
                target_redirect += f"?class_group={default_class.id}"
            elif result['students']:
                first_cls = result['students'][0].class_group_id
                target_redirect += f"?class_group={first_cls}"

            return redirect(target_redirect)
        else:
            for err_list in form.errors.values():
                for err in err_list:
                    messages.error(request, err)

    return redirect('teacher_students')


@teacher_required
def teacher_students_export(request):
    """
    Експорт списку учнів обраного класу (або всіх класів) у CSV файл.
    Підтримує кодування UTF-8 з BOM для коректного відкриття в Microsoft Excel.
    """
    selected_class_id = request.GET.get('class_group') or request.GET.get('class')
    students_qs = Student.objects.select_related('class_group').order_by('class_group__grade', 'class_group__letter', 'last_name', 'first_name')

    class_suffix = "all"
    if selected_class_id:
        try:
            cid = int(selected_class_id)
            students_qs = students_qs.filter(class_group_id=cid)
            cls_obj = ClassGroup.objects.filter(id=cid).first()
            if cls_obj:
                class_suffix = cls_obj.name.replace(' ', '_')
        except (ValueError, TypeError):
            pass

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    filename = f"students_list_{class_suffix}_{timezone.now().strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response, delimiter=';', quotechar='"', quoting=csv.QUOTE_MINIMAL)
    # Заголовки стовпчиків
    writer.writerow(['№', 'Клас', 'Прізвище', 'Імʼя', 'Повне ПІБ', 'Кількість зданих робіт', 'Нотатки'])

    for idx, st in enumerate(students_qs, 1):
        subs_count = st.get_submissions_count()
        writer.writerow([
            idx,
            st.class_group.name if st.class_group else '—',
            st.last_name,
            st.first_name,
            st.get_full_name(),
            subs_count,
            st.notes.replace('\n', ' ') if st.notes else ''
        ])

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# ІСТОРІЯ УЧНЯ (STUDENT DETAIL)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def student_detail(request, student_name):
    """
    Відображає повну історію робіт конкретного учня з урахуванням нечіткого зіставлення,
    різного порядку слів та пестливих/скорочених імен.
    """
    from .student_matcher import is_same_student_identity
    
    clean_target = student_name.replace('+', ' ').replace('_', ' ').strip()
    parts = clean_target.split()
    target_last = parts[0] if parts else clean_target
    target_first = " ".join(parts[1:]) if len(parts) > 1 else ""

    all_subs = Submission.objects.all().select_related('assignment', 'class_group', 'teacher').prefetch_related('comments').order_by('-submitted_at')
    
    # Первинна фільтрація за коренем імені або прізвища
    initial_q = (
        Q(last_name__icontains=target_last[:4]) |
        Q(first_name__icontains=target_last[:4])
    )
    if target_first:
        initial_q |= (
            Q(last_name__icontains=target_first[:4]) |
            Q(first_name__icontains=target_first[:4])
        )
    candidate_subs = all_subs.filter(initial_q)
    if not candidate_subs.exists():
        candidate_subs = all_subs

    matched_ids = []
    for sub in candidate_subs:
        if is_same_student_identity(target_last, target_first, sub.last_name, sub.first_name):
            matched_ids.append(sub.id)

    if matched_ids:
        submissions = all_subs.filter(id__in=matched_ids)
    else:
        submissions = all_subs.filter(
            Q(last_name__icontains=target_last) | Q(first_name__icontains=target_last)
        )

    # Знаходимо відповідний профіль Student
    student_obj = None
    first_sub = submissions.first()
    target_class = first_sub.class_group if first_sub else None
    if target_class:
        for st in Student.objects.filter(class_group=target_class):
            if is_same_student_identity(target_last, target_first, st.last_name, st.first_name):
                student_obj = st
                break

    paginator = Paginator(submissions, 15)
    page_obj = paginator.get_page(request.GET.get('page'))

    display_name = f"{target_last} {target_first}".strip()

    return render(request, 'feed/student_detail.html', {
        'page_obj': page_obj,
        'student_name': display_name,
        'raw_student_name': student_name,
        'student_obj': student_obj,
        'target_class': target_class,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# ЕЛЕКТРОННИЙ ЖУРНАЛ ОЦІНОК ТА ЕКСПОРТ (GRADEBOOK & EXPORT)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def gradebook(request):
    """
    Електронний журнал оцінок:
    - Матриця оцінок (Учні × Дати/Завдання) по обраному класу
    - Загальна таблиця всіх виставлених оцінок
    """
    teacher = request.user.teacher_profile

    if request.user.is_superuser:
        class_groups = list(ClassGroup.objects.all().order_by('grade', 'letter'))
    else:
        class_groups = list(teacher.classes.all().order_by('grade', 'letter'))

    selected_class_id = request.GET.get('class_group')
    view_mode = request.GET.get('view')

    if view_mode == 'all_grades':
        from django.db.models.functions import Coalesce
        all_subs = Submission.objects.filter(
            grade__isnull=False
        ).exclude(
            grade=''
        ).select_related(
            'assignment', 'class_group', 'teacher'
        ).annotate(
            sort_date=Coalesce('graded_at', 'submitted_at')
        ).order_by('-sort_date', '-submitted_at')

        if selected_class_id:
            all_subs = all_subs.filter(class_group_id=selected_class_id)
        elif not request.user.is_superuser:
            all_subs = all_subs.filter(Q(assignment__teacher=teacher) | Q(teacher=teacher))

        paginator = Paginator(all_subs, 25)
        page_obj = paginator.get_page(request.GET.get('page'))

        return render(request, 'feed/gradebook_all.html', {
            'page_obj': page_obj,
            'class_groups': class_groups,
            'selected_class_id': selected_class_id,
        })

    for cls in class_groups:
        cls.selected = (str(cls.id) == str(selected_class_id))

    if not selected_class_id:
        return render(request, 'feed/gradebook.html', {
            'class_groups': class_groups,
            'selected_class': None,
        })

    try:
        selected_class = ClassGroup.objects.get(id=selected_class_id)
    except ClassGroup.DoesNotExist:
        selected_class = None

    submissions = Submission.objects.filter(
        class_group_id=selected_class_id
    ).select_related('assignment', 'teacher').order_by('last_name', 'first_name', 'submitted_at')

    # Інтелектуальне групування учнів у журналі
    from .student_matcher import cluster_submissions_by_student
    clusters = cluster_submissions_by_student(submissions)

    dates = set()
    date_assignments = {}
    for sub in submissions:
        d = sub.effective_grade_date
        dates.add(d)
        if d not in date_assignments and sub.assignment:
            date_assignments[d] = sub.assignment.title

    sorted_dates = sorted(dates)

    # ── Фільтрація діапазону оцінок та дат ────────────────────────────────────
    import datetime

    date_from_str = request.GET.get('date_from', '').strip()
    date_to_str = request.GET.get('date_to', '').strip()
    single_date_str = request.GET.get('single_date', '').strip()
    grade_filter = request.GET.get('grade_filter', '').strip()
    date_preset = request.GET.get('date_preset', '').strip()

    today_date = timezone.localtime(timezone.now()).date()

    if date_preset == 'today':
        single_date_str = today_date.strftime('%Y-%m-%d')
    elif date_preset == 'week':
        start_week = today_date - datetime.timedelta(days=today_date.weekday())
        date_from_str = start_week.strftime('%Y-%m-%d')
        date_to_str = today_date.strftime('%Y-%m-%d')
    elif date_preset == 'month':
        start_month = today_date.replace(day=1)
        date_from_str = start_month.strftime('%Y-%m-%d')
        date_to_str = today_date.strftime('%Y-%m-%d')

    parsed_single_date = None
    if single_date_str:
        try:
            parsed_single_date = datetime.datetime.strptime(single_date_str, '%Y-%m-%d').date()
        except ValueError:
            try:
                parsed_single_date = datetime.datetime.strptime(single_date_str, '%d.%m.%Y').date()
            except ValueError:
                parsed_single_date = None

    parsed_date_from = None
    if date_from_str:
        try:
            parsed_date_from = datetime.datetime.strptime(date_from_str, '%Y-%m-%d').date()
        except ValueError:
            try:
                parsed_date_from = datetime.datetime.strptime(date_from_str, '%d.%m.%Y').date()
            except ValueError:
                parsed_date_from = None

    parsed_date_to = None
    if date_to_str:
        try:
            parsed_date_to = datetime.datetime.strptime(date_to_str, '%Y-%m-%d').date()
        except ValueError:
            try:
                parsed_date_to = datetime.datetime.strptime(date_to_str, '%d.%m.%Y').date()
            except ValueError:
                parsed_date_to = None

    # Відбираємо дати згідно з фільтрами
    filtered_dates = []
    for d in sorted_dates:
        if parsed_single_date and d != parsed_single_date:
            continue
        if parsed_date_from and d < parsed_date_from:
            continue
        if parsed_date_to and d > parsed_date_to:
            continue
        filtered_dates.append(d)

    def match_grade_filter(item, g_filt):
        if not g_filt or g_filt == 'all':
            return True
        val = str(item.get('grade', '')).strip().lower()
        if g_filt == '10-12':
            return val in ['10', '11', '12']
        elif g_filt == '7-9':
            return val in ['7', '8', '9']
        elif g_filt == '4-6':
            return val in ['4', '5', '6']
        elif g_filt == '1-3':
            return val in ['1', '2', '3']
        elif g_filt == 'rework':
            return val in ['доопрацювати', 'на доопрацювання', 'д']
        elif g_filt == 'ungraded':
            return not item.get('has_grade') or val == ''
        return True

    has_active_filter = bool(parsed_single_date or parsed_date_from or parsed_date_to or (grade_filter and grade_filter != 'all') or date_preset)

    student_list = []
    class_all_numeric_grades = []

    for cl in clusters:
        cells = []
        legacy_grade_list = []
        student_matching_items_count = 0
        student_numeric_grades = []

        for d in filtered_dates:
            raw_items = cl['grades_by_date'].get(d, [])
            filtered_items = [it for it in raw_items if match_grade_filter(it, grade_filter)]
            cells.append({
                'date': d,
                'items': filtered_items,
            })
            for it in filtered_items:
                if it.get('grade'):
                    legacy_grade_list.append([it['grade']])
                    if str(it['grade']).isdigit():
                        student_numeric_grades.append(int(it['grade']))
                student_matching_items_count += 1

        if grade_filter and grade_filter != 'all' and student_matching_items_count == 0:
            continue

        avg_score = None
        if student_numeric_grades:
            avg_score = round(sum(student_numeric_grades) / len(student_numeric_grades), 1)
            class_all_numeric_grades.extend(student_numeric_grades)
        elif not has_active_filter and cl['numeric_grades']:
            avg_score = round(sum(cl['numeric_grades']) / len(cl['numeric_grades']), 1)
            class_all_numeric_grades.extend(cl['numeric_grades'])

        student_list.append({
            'last_name': cl['last_name'],
            'first_name': cl['first_name'],
            'full_name': cl['full_name'],
            'name_key': cl['name_key'],
            'cells': cells,
            'grades': legacy_grade_list,
            'avg_score': avg_score,
            'total_submissions': cl['all_submissions_count'],
            'submissions_count': cl['all_submissions_count'],
            'graded_count': len(student_numeric_grades) if has_active_filter else len(cl['numeric_grades']),
            'grades_count': len(student_numeric_grades) if has_active_filter else len(cl['numeric_grades']),
        })

    class_avg = round(sum(class_all_numeric_grades) / len(class_all_numeric_grades), 1) if class_all_numeric_grades else None

    dates_meta = []
    for d in filtered_dates:
        dates_meta.append({
            'date': d,
            'title': date_assignments.get(d, f"Заняття {d.strftime('%d.%m.%Y')}"),
        })

    view_mode = request.GET.get('view_mode', 'journal')

    return render(request, 'feed/gradebook.html', {
        'class_groups': class_groups,
        'selected_class': selected_class,
        'selected_class_id': selected_class_id,
        'students': student_list,
        'student_list': student_list,
        'dates': filtered_dates,
        'sorted_dates': filtered_dates,
        'all_available_dates': sorted_dates,
        'dates_meta': dates_meta,
        'date_assignments': date_assignments,
        'class_avg': class_avg,
        'view_mode': view_mode,
        'total_submissions': len(submissions),
        'total_graded': len(class_all_numeric_grades),
        'single_date': single_date_str,
        'date_from': date_from_str,
        'date_to': date_to_str,
        'grade_filter': grade_filter,
        'date_preset': date_preset,
        'has_active_filter': has_active_filter,
    })




@teacher_required
def export_grades(request):
    """
    Експорт оцінок у формат CSV з кодуванням UTF-8-sig для коректного відкриття в Excel.
    """
    class_group_id = request.GET.get('class_group')
    teacher = request.user.teacher_profile

    submissions = Submission.objects.filter(grade__isnull=False).exclude(grade='').select_related('assignment', 'class_group', 'teacher').order_by('class_group__grade', 'class_group__letter', 'last_name', 'submitted_at')

    if not request.user.is_superuser:
        submissions = submissions.filter(Q(assignment__teacher=teacher) | Q(teacher=teacher))

    filename = 'grades.csv'

    if class_group_id:
        try:
            selected_class = ClassGroup.objects.get(id=class_group_id)
            submissions = submissions.filter(class_group_id=class_group_id)
            filename = f'grades_{selected_class.name}.csv'
        except ClassGroup.DoesNotExist:
            pass

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    import csv
    writer = csv.writer(response)
    writer.writerow(['Прізвище', "Ім'я", 'Клас', 'Завдання', 'Вчитель', 'Дата здачі', 'Дата оцінювання', 'Оцінка'])

    for sub in submissions:
        assignment_title = sub.assignment.title if sub.assignment else '-'
        teacher_name = sub.teacher.full_name if sub.teacher else (sub.assignment.teacher.full_name if sub.assignment else '-')
        graded_at_str = sub.graded_at.strftime('%d.%m.%Y %H:%M') if sub.graded_at else sub.submitted_at.strftime('%d.%m.%Y %H:%M')
        writer.writerow([
            sub.last_name,
            sub.first_name,
            sub.class_group.name if sub.class_group else '',
            assignment_title,
            teacher_name,
            sub.submitted_at.strftime('%d.%m.%Y %H:%M'),
            graded_at_str,
            sub.grade
        ])

    return response


# ═══════════════════════════════════════════════════════════════════════════════
# ЖУРНАЛ ДІЙ ТА АВТОАРХІВАЦІЯ ЛОГІВ (ACTIVITY LOG)
# ═══════════════════════════════════════════════════════════════════════════════

def archive_old_activity_logs(days=30):
    """Архівує логи старші за 30 днів у файл на диску та видаляє їх з БД."""
    from datetime import timedelta
    cutoff_date = timezone.now() - timedelta(days=days)
    old_logs = list(SubmissionActivityLog.objects.filter(timestamp__lt=cutoff_date).order_by('timestamp'))

    if not old_logs:
        return 0, None

    from django.conf import settings
    logs_dir = os.path.join(settings.MEDIA_ROOT, 'logs_archives')
    os.makedirs(logs_dir, exist_ok=True)

    min_date = old_logs[0].timestamp.strftime("%Y%m%d")
    max_date = old_logs[-1].timestamp.strftime("%Y%m%d")
    filename = f"Архів_логів_{min_date}_{max_date}.csv"
    filepath = os.path.join(logs_dir, filename)

    if os.path.exists(filepath):
        filename = f"Архів_логів_{min_date}_{max_date}_{int(datetime.now().timestamp())}.csv"
        filepath = os.path.join(logs_dir, filename)

    rows = [["ID", "Час", "Користувач", "Тип дії", "Опис", "ID роботи"]]
    for log in old_logs:
        rows.append([
            str(log.id),
            log.timestamp.strftime("%d.%m.%Y %H:%M:%S"),
            log.actor.username if log.actor else "Гість / Учень",
            log.get_action_type_display(),
            log.description,
            str(log.submission_id or "-")
        ])

    import io, csv
    csv_buffer = io.StringIO()
    csv_writer = csv.writer(csv_buffer)
    for row in rows:
        csv_writer.writerow(row)

    with open(filepath, 'w', encoding='utf-8-sig') as f:
        f.write(csv_buffer.getvalue())

    log_ids = [l.id for l in old_logs]
    SubmissionActivityLog.objects.filter(id__in=log_ids).delete()

    return len(old_logs), filename


def get_logs_archives_list():
    """Повертає список збережених CSV-архівів логів на диску."""
    from django.conf import settings
    logs_dir = os.path.join(settings.MEDIA_ROOT, 'logs_archives')
    archives_list = []
    if os.path.exists(logs_dir):
        for f in sorted(os.listdir(logs_dir), reverse=True):
            if f.endswith('.csv'):
                fpath = os.path.join(logs_dir, f)
                fsize = os.path.getsize(fpath)
                mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
                archives_list.append({
                    'filename': f,
                    'size': f"{round(fsize/1024, 1)} КБ" if fsize > 1024 else f"{fsize} Б",
                    'date': mtime.strftime("%d.%m.%Y %H:%M"),
                })
    return archives_list


@teacher_required
def activity_log(request):
    """
    Журнал дій системи та перегляд архівів на диску.
    """
    logs_qs = SubmissionActivityLog.objects.all().select_related('actor', 'submission').order_by('-timestamp')

    action_type = request.GET.get('action_type')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')

    if action_type:
        logs_qs = logs_qs.filter(action_type=action_type)

    if date_from:
        try:
            from datetime import datetime as dt
            df = dt.strptime(date_from, '%Y-%m-%d')
            logs_qs = logs_qs.filter(timestamp__gte=df)
        except Exception:
            pass

    if date_to:
        try:
            from datetime import datetime as dt
            dt_obj = dt.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            logs_qs = logs_qs.filter(timestamp__lt=dt_obj)
        except Exception:
            pass

    total_logs_count = SubmissionActivityLog.objects.count()
    paginator = Paginator(logs_qs, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    logs_archives = get_logs_archives_list()

    action_choices = [
        {'code': 'submission', 'name': 'Здача роботи', 'selected': action_type == 'submission'},
        {'code': 'grading', 'name': 'Оцінювання', 'selected': action_type == 'grading'},
        {'code': 'comment', 'name': 'Коментар', 'selected': action_type == 'comment'},
        {'code': 'login', 'name': 'Вхід в систему', 'selected': action_type == 'login'},
        {'code': 'delete', 'name': 'Видалення', 'selected': action_type == 'delete'},
    ]

    return render(request, 'feed/activity_log.html', {
        'page_obj': page_obj,
        'total_logs_count': total_logs_count,
        'logs_archives': logs_archives,
        'action_choices': action_choices,
        'action_type': action_type,
        'date_from': date_from,
        'date_to': date_to,
        'is_superadmin': request.user.is_superuser,
    })


@teacher_required
@require_POST
def trigger_log_archive(request):
    """Ручний запуск архівації старих логів."""
    if not request.user.is_superuser:
        messages.error(request, "Тільки адміністратор може запускати архівацію логів.")
        return redirect('activity_log')

    count, filename = archive_old_activity_logs(days=30)
    if count > 0:
        messages.success(request, f"Успішно заархівовано {count} старих логів у файл: {filename}")
    else:
        messages.info(request, "Немає логів старших за 30 днів для архівації.")

    return redirect('activity_log')


@teacher_required
def view_log_archive(request, filename):
    """Перегляд вмісту CSV-архіву логів у вигляді HTML-таблиці."""
    from django.conf import settings
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(settings.MEDIA_ROOT, 'logs_archives', safe_filename)

    if not os.path.exists(filepath):
        messages.error(request, "Файл архіву не знайдено.")
        return redirect('activity_log')

    rows = []
    try:
        import csv
        with open(filepath, 'r', encoding='utf-8-sig', errors='ignore') as f:
            reader = csv.reader(f)
            rows = list(reader)
    except Exception as e:
        messages.error(request, f"Помилка читання архіву: {str(e)}")
        return redirect('activity_log')

    headers = rows[0] if rows else []
    data_rows = rows[1:] if len(rows) > 1 else []

    return render(request, 'feed/log_archive_viewer.html', {
        'filename': safe_filename,
        'headers': headers,
        'rows': data_rows,
        'total_rows': len(data_rows),
    })


@teacher_required
def download_log_archive(request, filename):
    """Завантаження CSV-архіву логів."""
    from django.conf import settings
    safe_filename = os.path.basename(filename)
    filepath = os.path.join(settings.MEDIA_ROOT, 'logs_archives', safe_filename)

    if os.path.exists(filepath):
        from django.http import FileResponse
        return FileResponse(open(filepath, 'rb'), as_attachment=True, filename=safe_filename)

    messages.error(request, "Файл не знайдено.")
    return redirect('activity_log')


# ═══════════════════════════════════════════════════════════════════════════════
# КЕРУВАННЯ ЗАКЛАДОМ ТА ВЧИТЕЛЯМИ (TEACHER PROFILES & SUPERADMIN)
# ═══════════════════════════════════════════════════════════════════════════════

def get_master_school_archive_info():
    """Повертає метадані єдиного централізованого ZIP-архіву школи на диску."""
    from django.conf import settings
    import zipfile
    master_zip_path = os.path.join(settings.MEDIA_ROOT, 'school_archives', "Архів_робіт_школи.zip")
    if os.path.exists(master_zip_path):
        f_size = os.path.getsize(master_zip_path)
        f_time = datetime.fromtimestamp(os.path.getmtime(master_zip_path))
        files_count = 0
        try:
            with zipfile.ZipFile(master_zip_path, 'r') as zf:
                files_count = len([m for m in zf.namelist() if not m.endswith('/') and m != 'список_всіх_робіт.csv'])
        except Exception:
            pass
        return {
            'name': 'Архів_робіт_школи.zip',
            'url': f"{settings.MEDIA_URL}school_archives/Архів_робіт_школи.zip",
            'size': f"{round(f_size / (1024 * 1024), 2)} МБ" if f_size > 1048576 else f"{round(f_size / 1024, 1)} КБ",
            'date': f_time.strftime("%d.%m.%Y %H:%M"),
            'files_count': files_count
        }
    return None


def archive_submissions_to_master_zip(teacher_name, teacher_submissions):
    """Зберігає роботи в єдиний централізований накопичувальний архів школи."""
    from django.conf import settings
    import zipfile, io, csv
    archives_dir = os.path.join(settings.MEDIA_ROOT, 'school_archives')
    os.makedirs(archives_dir, exist_ok=True)
    master_zip_path = os.path.join(archives_dir, "Архів_робіт_школи.zip")
    temp_zip_path = os.path.join(archives_dir, "Архів_робіт_школи.tmp.zip")

    existing_members = set()
    existing_summary_rows = []

    if os.path.exists(master_zip_path):
        try:
            with zipfile.ZipFile(master_zip_path, 'r') as old_zip:
                for item in old_zip.infolist():
                    existing_members.add(item.filename)
                if "список_всіх_робіт.csv" in existing_members:
                    csv_content = old_zip.read("список_всіх_робіт.csv").decode('utf-8-sig', errors='ignore')
                    csv_reader = csv.reader(csv_content.splitlines())
                    existing_summary_rows = list(csv_reader)
        except Exception:
            pass

    if not existing_summary_rows:
        existing_summary_rows = [
            ["Прізвище", "Ім'я", "Клас", "Вчитель", "Дата здачі", "Оцінка", "Файл/Посилання", "Коментарі", "Дата архівації"]
        ]

    archived_at_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    files_to_delete = []

    with zipfile.ZipFile(temp_zip_path, 'w', zipfile.ZIP_DEFLATED) as new_zip:
        if os.path.exists(master_zip_path):
            with zipfile.ZipFile(master_zip_path, 'r') as old_zip:
                for item in old_zip.infolist():
                    if item.filename != "список_всіх_робіт.csv":
                        new_zip.writestr(item, old_zip.read(item.filename))

        for sub in teacher_submissions:
            comments_list = [f"{c.get_author_name()}: {c.text}" for c in sub.comments.all()]
            comments_str = " | ".join(comments_list)
            work_item = os.path.basename(sub.file.name) if sub.file else (sub.link or '')
            existing_summary_rows.append([
                sub.last_name,
                sub.first_name,
                sub.class_group.name if sub.class_group else "-",
                teacher_name,
                sub.submitted_at.strftime("%d.%m.%Y %H:%M"),
                sub.grade or "-",
                work_item,
                comments_str,
                archived_at_str
            ])
            if sub.file:
                try:
                    if os.path.exists(sub.file.path):
                        file_path = sub.file.path
                        files_to_delete.append(file_path)
                        safe_teacher = "".join(c for c in teacher_name if c.isalnum() or c in (' ', '_', '-')).strip().replace(' ', '_')
                        class_name = sub.class_group.name if sub.class_group else "Загальні"
                        arcname = f"{safe_teacher}/{class_name}/{sub.last_name}_{sub.first_name}/{os.path.basename(file_path)}"
                        
                        counter = 1
                        orig_arcname = arcname
                        while arcname in existing_members:
                            name_part, ext_part = os.path.splitext(orig_arcname)
                            arcname = f"{name_part}_{counter}{ext_part}"
                            counter += 1
                        new_zip.write(file_path, arcname)
                        existing_members.add(arcname)
                except Exception:
                    pass

        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)
        for row in existing_summary_rows:
            csv_writer.writerow(row)
        new_zip.writestr("список_всіх_робіт.csv", "\ufeff" + csv_buffer.getvalue())

    if os.path.exists(master_zip_path):
        os.remove(master_zip_path)
    os.rename(temp_zip_path, master_zip_path)

    return master_zip_path, files_to_delete


@teacher_required
def enter_superadmin_mode(request):

    if not request.user.is_superuser:
        messages.error(request, "У вас немає прав супер-адміністратора.")
        return redirect('teacher_profiles')

    request.session['superadmin_mode'] = True
    messages.warning(request, "👑 РЕЖИМ СУПЕР-АДМІНІСТРАТОРА АКТИВОВАНО.")
    return redirect(request.META.get('HTTP_REFERER') or 'teacher_profiles')


@teacher_required
def exit_superadmin_mode(request):
    if 'superadmin_mode' in request.session:
        request.session['superadmin_mode'] = False
    messages.info(request, "Ви вийшли з режиму супер-адміністратора.")
    return redirect(request.META.get('HTTP_REFERER') or 'teacher_dashboard')


@teacher_required
def teacher_profiles_view(request):
    """
    Сумісність: делегування до Єдиного Центру Налаштувань (вкладка Середовище та заклад).
    """
    if request.method == 'GET':
        q = request.GET.copy()
        q['tab'] = 'environment'
        request.GET = q
    return teacher_settings_view(request)


@teacher_required
def teacher_profile_edit(request, profile_id):
    is_superadmin_mode = request.user.is_superuser and request.session.get('superadmin_mode', False)
    teacher_obj = get_object_or_404(Teacher, id=profile_id)

    if not is_superadmin_mode and teacher_obj.user != request.user:
        messages.error(request, "У вас немає прав редагувати цей профіль.")
        return redirect('teacher_profiles')

    if request.method == 'POST':
        form = TeacherProfileForm(request.POST, request.FILES, instance=teacher_obj)
        if form.is_valid():
            form.save()
            messages.success(request, f"Профіль вчителя «{teacher_obj.full_name}» оновлено!")
            return redirect('teacher_profiles')
    else:
        form = TeacherProfileForm(instance=teacher_obj)

    return render(request, 'feed/teacher_profile_edit.html', {
        'form': form,
        'teacher_obj': teacher_obj,
    })


@teacher_required
@require_POST
def teacher_profile_delete(request, profile_id):
    if not request.user.is_superuser:
        messages.error(request, "Тільки супер-адміністратор може видаляти профілі.")
        return redirect('teacher_profiles')

    teacher_obj = get_object_or_404(Teacher, id=profile_id)
    t_name = teacher_obj.full_name
    linked_user = teacher_obj.user

    teacher_submissions = list(
        Submission.objects.filter(
            Q(teacher=teacher_obj) | Q(assignment__teacher=teacher_obj)
        ).distinct().select_related('class_group', 'teacher')
    )

    submissions_count = len(teacher_submissions)
    master_zip_path, files_to_delete = archive_submissions_to_master_zip(t_name, teacher_submissions)

    for fpath in files_to_delete:
        try:
            if os.path.exists(fpath):
                os.remove(fpath)
        except Exception:
            pass

    if submissions_count > 0:
        Submission.objects.filter(id__in=[s.id for s in teacher_submissions]).delete()

    teacher_obj.delete()
    if linked_user and not linked_user.is_superuser and linked_user != request.user:
        linked_user.delete()

    log_submission_activity(
        request.user,
        'delete',
        f"Супер-адмін видалив вчителя '{t_name}'. {submissions_count} робіт заархівовано в Архів_робіт_школи.zip"
    )
    messages.success(
        request,
        f"Акаунт вчителя «{t_name}» видалено. {submissions_count} робіт надійно збережено в архів школи."
    )
    return redirect('teacher_profiles')


@teacher_required
@require_POST
def toggle_superadmin(request, user_id):
    if not request.user.is_superuser:
        messages.error(request, "Тільки супер-адміністратор може змінювати права.")
        return redirect('teacher_profiles')

    target_user = get_object_or_404(User, id=user_id)
    if target_user == request.user and target_user.is_superuser:
        messages.warning(request, "Ви не можете зняти права супер-адміністратора із себе.")
        return redirect('teacher_profiles')

    target_user.is_superuser = not target_user.is_superuser
    target_user.is_staff = True
    target_user.save()

    status_str = "призначено супер-адміністратором школи" if target_user.is_superuser else "переведено у звичайні вчителі"
    messages.success(request, f"Користувача «{target_user.username}» {status_str}!")
    return redirect('teacher_profiles')


@teacher_required
@require_POST
def school_settings(request):
    if not request.user.is_superuser:
        messages.error(request, "Тільки супер-адміністратор може змінювати налаштування закладу.")
        return redirect('teacher_profiles')

    school = School.objects.first()
    if not school:
        school = School.objects.create(name="Наш Заклад Освіти", admin=request.user)

    school_name = request.POST.get('school_name', '').strip()
    if school_name:
        school.name = school_name
        school.admin = request.user
        school.save()
        messages.success(request, f"Назву закладу успішно оновлено: «{school_name}»!")

    return redirect(f"{reverse('teacher_settings')}?tab=environment")


# ═══════════════════════════════════════════════════════════════════════════════
# НОВІ СЕРВІСНІ ТА ПЕДАГОГІЧНІ ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

def api_students_autocomplete(request):
    """
    Повертає список унікальних імен учнів для миттєвого автодоповнення у формі здачі робіт.
    """
    class_group_id = request.GET.get('class_group_id')
    query = request.GET.get('query', '').strip().lower()

    students_qs = Student.objects.all()
    submissions = Submission.objects.all()
    if class_group_id:
        students_qs = students_qs.filter(class_group_id=class_group_id)
        submissions = submissions.filter(class_group_id=class_group_id)

    raw_names = list(students_qs.values_list('last_name', 'first_name')) + list(submissions.values_list('last_name', 'first_name').distinct())
    
    seen = set()
    students = []
    for ln, fn in raw_names:
        ln_clean = ln.strip()
        fn_clean = fn.strip()
        full_name = f"{ln_clean} {fn_clean}".strip()
        if not full_name:
            continue
        key = (ln_clean.lower(), fn_clean.lower())
        if key in seen:
            continue
        seen.add(key)
        
        if not query or query in full_name.lower() or query in f"{fn_clean} {ln_clean}".lower():
            students.append({
                'last_name': ln_clean,
                'first_name': fn_clean,
                'full_name': full_name,
            })

    students.sort(key=lambda x: (x['last_name'].lower(), x['first_name'].lower()))
    return JsonResponse({'students': students[:15]})


@teacher_required
def download_assignment_submissions_zip(request, pk):
    """
    Масове завантаження робіт учнів по завданню одним структурованим ZIP-архівом.
    """
    import zipfile
    import io
    from django.http import HttpResponse

    assignment = get_object_or_404(Assignment, pk=pk)
    teacher = request.user.teacher_profile

    if not (request.user.is_superuser or assignment.teacher == teacher):
        messages.error(request, "У вас немає доступу до експорту робіт цього завдання.")
        return redirect('teacher_dashboard')

    class_id = request.GET.get('class_group')
    submissions = assignment.submissions.all().select_related('class_group')
    if class_id:
        submissions = submissions.filter(class_group_id=class_id)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        files_added = 0
        for sub in submissions:
            class_name = sub.class_group.name if sub.class_group else "Загальний"
            student_name = f"{sub.last_name}_{sub.first_name}".strip()
            
            sub_files = list(sub.files.all())
            if sub_files:
                for idx, sf in enumerate(sub_files):
                    if sf.file and os.path.exists(sf.file.path):
                        ext = sf.get_extension() or ''
                        orig_base = os.path.splitext(sf.original_name or os.path.basename(sf.file.name))[0]
                        prefix = f"_{idx+1}" if len(sub_files) > 1 else ""
                        arc_name = f"{class_name}/{student_name}_{orig_base}{prefix}{ext}"
                        zip_file.write(sf.file.path, arc_name)
                        files_added += 1
            elif sub.file and os.path.exists(sub.file.path):
                ext = sub.get_file_extension() or ''
                orig_base = os.path.splitext(os.path.basename(sub.file.name))[0]
                arc_name = f"{class_name}/{student_name}_{orig_base}{ext}"
                zip_file.write(sub.file.path, arc_name)
                files_added += 1
            elif sub.link:
                arc_name = f"{class_name}/{student_name}_посилання.txt"
                content = f"Учень: {sub.get_student_full_name()}\nКлас: {class_name}\nПосилання на роботу:\n{sub.link}\n"
                if sub.comment_student:
                    content += f"\nКоментар учня:\n{sub.comment_student}\n"
                zip_file.writestr(arc_name, content)
                files_added += 1

    if files_added == 0:
        messages.warning(request, "У цьому завданні немає зданих файлів або посилань для завантаження.")
        return redirect('assignment_submissions', pk=pk)

    zip_buffer.seek(0)
    safe_title = re.sub(r'[^\w\-_\. ]', '_', assignment.title)[:30]
    filename = f"Roboty_{safe_title}.zip"

    response = HttpResponse(zip_buffer.getvalue(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@teacher_required
def download_database_backup(request):
    """
    Створення та завантаження резервної копії бази даних у 1 клік (тільки для супер-адміністраторів школи).
    """
    if not request.user.is_superuser:
        messages.error(request, "Резервне копіювання бази даних доступне лише супер-адміністраторам.")
        return redirect('teacher_dashboard')

    from django.conf import settings
    from django.http import HttpResponse, FileResponse
    from datetime import datetime
    import io

    db_path = settings.DATABASES['default'].get('NAME')
    today_str = datetime.now().strftime('%Y-%m-%d_%H-%M')

    if db_path and os.path.exists(str(db_path)):
        backup_filename = f"schoolnet_backup_{today_str}.sqlite3"
        return FileResponse(
            open(db_path, 'rb'),
            as_attachment=True,
            filename=backup_filename,
            content_type='application/x-sqlite3'
        )
    else:
        # Резервний експорт через dumpdata у JSON
        from django.core.management import call_command
        buf = io.StringIO()
        call_command('dumpdata', stdout=buf)
        buf.seek(0)
        resp = HttpResponse(buf.getvalue(), content_type='application/json')
        resp['Content-Disposition'] = f'attachment; filename="schoolnet_backup_{today_str}.json"'
        return resp


# ═══════════════════════════════════════════════════════════════════════════════
# МОДУЛЬ ШТУЧНОГО ІНТЕЛЕКТУ (GOOGLE GEMINI AI)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required

# ═══════════════════════════════════════════════════════════════════════════════
# ЄДИНИЙ ЦЕНТР НАЛАШТУВАНЬ ВЧИТЕЛЯ ТА АДМІНІСТРАТОРА (ПРОФІЛЬ, СЕРЕДОВИЩЕ, ШІ)
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def teacher_settings_view(request):
    """
    Єдиний Центр Налаштувань для вчителів та адміністраторів.
    Об'єднує:
    1. Профіль вчителя (ПІБ, фото аватара, колір, власні предмети та класи, зміна пароля).
    2. Середовище закладу (Назва школи, супер-адмін режим, бекап БД, керування вчителями).
    3. Штучний інтелект (API ключ, системний промт, пріоритети моделей, черга fallback, видалення моделей, статистика).
    """
    teacher = request.user.teacher_profile
    ai_settings = AISettings.get_solo()
    school = School.objects.first()
    if not school:
        school = School.objects.create(name="Наш Заклад Освіти", admin=request.user)

    tab = request.GET.get('tab', 'profile')
    if tab not in ['profile', 'environment', 'ai']:
        tab = 'profile'

    profile_form = TeacherProfileForm(instance=teacher)
    subject_form = SubjectForm()
    class_form = ClassGroupForm()
    teacher_create_form = TeacherCreateForm()

    if request.method == 'POST':
        action = request.POST.get('action')
        is_superadmin_mode = bool(request.user.is_superuser and request.session.get('superadmin_mode', False))

        # ── 1. ДІЇ ПРОФІЛЮ ────────────────────────────────────────────────────
        if action == 'update_profile':
            profile_form = TeacherProfileForm(request.POST, request.FILES, instance=teacher)
            if profile_form.is_valid():
                profile_form.save()
                messages.success(request, 'Профіль та аватар успішно збережено! ✅')
            else:
                for field, errors in profile_form.errors.items():
                    for err in errors:
                        messages.error(request, f'{err}')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'remove_avatar':
            if teacher.avatar_image:
                try:
                    if os.path.exists(teacher.avatar_image.path):
                        os.remove(teacher.avatar_image.path)
                except Exception:
                    pass
                teacher.avatar_image = None
                teacher.save(update_fields=['avatar_image'])
                messages.info(request, 'Фото аватара видалено. Використовуються кольорові ініціали.')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'change_own_password':
            new_pass = request.POST.get('new_password', '').strip()
            if len(new_pass) < 4:
                messages.error(request, 'Пароль занадто короткий (мінімум 4 символи).')
            else:
                request.user.set_password(new_pass)
                request.user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                messages.success(request, 'Ваш пароль успішно змінено! 🔐')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'add_teacher_subject':
            subject_id = request.POST.get('subject_id')
            if subject_id:
                subject = get_object_or_404(Subject, pk=subject_id)
                teacher.subjects.add(subject)
                messages.success(request, f'Предмет «{subject.name}» додано до вашого профілю! 📚')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'remove_teacher_subject':
            subject_id = request.POST.get('subject_id')
            if subject_id:
                subject = get_object_or_404(Subject, pk=subject_id)
                teacher.subjects.remove(subject)
                messages.info(request, f'Предмет «{subject.name}» видалено з вашого профілю.')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'add_teacher_class':
            class_id = request.POST.get('class_id')
            if class_id:
                cgroup = get_object_or_404(ClassGroup, pk=class_id)
                teacher.classes.add(cgroup)
                messages.success(request, f'Клас «{cgroup.name}» додано до вашого профілю! 🏫')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'remove_teacher_class':
            class_id = request.POST.get('class_id')
            if class_id:
                cgroup = get_object_or_404(ClassGroup, pk=class_id)
                teacher.classes.remove(cgroup)
                messages.info(request, f'Клас «{cgroup.name}» видалено з вашого профілю.')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'create_subject':
            subject_form = SubjectForm(request.POST)
            if subject_form.is_valid():
                subject = subject_form.save(commit=False)
                subject.created_by = teacher
                subject.save()
                teacher.subjects.add(subject)
                messages.success(request, f'Предмет «{subject.name}» успішно створено та додано до вашого профілю! 📚')
            else:
                for field, errors in subject_form.errors.items():
                    for err in errors:
                        messages.error(request, f'{err}')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        elif action == 'create_class':
            class_form = ClassGroupForm(request.POST)
            if class_form.is_valid():
                cgroup = class_form.save(commit=False)
                cgroup.created_by = teacher
                cgroup.save()
                teacher.classes.add(cgroup)
                messages.success(request, f'Клас «{cgroup.name}» успішно створено та додано до вашого профілю! 🏫')
            else:
                for field, errors in class_form.errors.items():
                    for err in errors:
                        messages.error(request, f'{err}')
            return redirect(f"{reverse('teacher_settings')}?tab=profile")

        # ── 2. ДІЇ СЕРЕДОВИЩА ТА АДМІНІСТРУВАННЯ ──────────────────────────────
        elif action == 'update_school_name':
            if not is_superadmin_mode:
                messages.error(request, "Для зміни назви закладу необхідно увімкнути режим супер-адміністратора 👑.")
            else:
                s_name = request.POST.get('school_name', '').strip()
                if s_name:
                    school.name = s_name
                    school.admin = request.user
                    school.save()
                    messages.success(request, f"Назву закладу успішно змінено на «{s_name}»! 🏫")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'create_teacher_account':
            if not is_superadmin_mode:
                messages.error(request, "Для створення акаунтів вчителів необхідно увімкнути режим супер-адміністратора 👑.")
            else:
                t_form = TeacherCreateForm(request.POST)
                if t_form.is_valid():
                    username = t_form.cleaned_data['username']
                    password = t_form.cleaned_data['password']
                    full_name = t_form.cleaned_data['full_name']
                    is_super = t_form.cleaned_data.get('is_superuser', False)

                    user = User.objects.create_user(username=username, password=password, is_superuser=is_super, is_staff=is_super)
                    Teacher.objects.create(user=user, full_name=full_name)
                    messages.success(request, f"Акаунт вчителя «{full_name}» (логін: {username}) успішно створено! 👤")
                else:
                    for field, errors in t_form.errors.items():
                        for err in errors:
                            messages.error(request, f'{err}')
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'reset_teacher_password':
            if not is_superadmin_mode:
                messages.error(request, "Для скидання паролів необхідно увімкнути режим супер-адміністратора 👑.")
            else:
                u_id = request.POST.get('user_id')
                new_pass = request.POST.get('new_password', '').strip()
                if u_id and new_pass:
                    target_user = get_object_or_404(User, pk=u_id)
                    target_user.set_password(new_pass)
                    target_user.save()
                    messages.success(request, f"Пароль для «{target_user.teacher_profile.full_name}» ({target_user.username}) успішно змінено! 🔑")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'toggle_teacher_superadmin':
            if not is_superadmin_mode:
                messages.error(request, "Для зміни прав супер-адміністратора необхідно увімкнути відповідний режим 👑.")
            else:
                u_id = request.POST.get('user_id')
                if u_id:
                    target_user = get_object_or_404(User, pk=u_id)
                    if target_user == request.user:
                        messages.warning(request, "Ви не можете змінити статус супер-адміністратора для власного акаунта.")
                    else:
                        target_user.is_superuser = not target_user.is_superuser
                        target_user.is_staff = target_user.is_superuser
                        target_user.save()
                        status_msg = "надано права супер-адміністратора" if target_user.is_superuser else "переведено у звичайні вчителі"
                        messages.success(request, f"Користувачу «{target_user.username}» {status_msg}!")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'delete_teacher':
            if not is_superadmin_mode:
                messages.error(request, "Для видалення вчителів необхідно увімкнути режим супер-адміністратора 👑.")
            else:
                t_id = request.POST.get('teacher_id')
                if t_id:
                    t_del = get_object_or_404(Teacher, pk=t_id)
                    if t_del.user == request.user:
                        messages.error(request, "Ви не можете видалити власний профіль.")
                    else:
                        u_del = t_del.user
                        t_name = t_del.full_name
                        t_del.delete()
                        u_del.delete()
        # ── 2.2. ДІЇ РОЗКЛАДУ ДЗВІНКІВ ──────────────────────────────────────
        elif action == 'save_bell_slot':
            slot_id = request.POST.get('slot_id')
            lesson_number = request.POST.get('lesson_number')
            start_time_str = request.POST.get('start_time', '').strip()
            end_time_str = request.POST.get('end_time', '').strip()

            if lesson_number and start_time_str and end_time_str:
                import datetime
                try:
                    num = int(lesson_number)
                    sh, sm = map(int, start_time_str.split(':'))
                    eh, em = map(int, end_time_str.split(':'))
                    s_time = datetime.time(sh, sm)
                    e_time = datetime.time(eh, em)

                    if s_time >= e_time:
                        messages.error(request, "Час завершення уроку має бути пізніше за час початку.")
                    else:
                        if slot_id:
                            slot = get_object_or_404(BellSchedule, pk=slot_id)
                            slot.lesson_number = num
                            slot.start_time = s_time
                            slot.end_time = e_time
                            slot.order = num
                            slot.save()
                            messages.success(request, f"Урок №{num} успішно оновлено! 🔔")
                        else:
                            BellSchedule.objects.update_or_create(
                                lesson_number=num,
                                defaults={'start_time': s_time, 'end_time': e_time, 'order': num}
                            )
                            messages.success(request, f"Урок №{num} ({start_time_str} – {end_time_str}) успішно збережено в розкладі дзвінків! 🔔")
                except Exception as ex:
                    messages.error(request, f"Помилка формату часу: {ex}")
            else:
                messages.error(request, "Будь ласка, вкажіть номер уроку, час початку та час закінчення.")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'delete_bell_slot':
            slot_id = request.POST.get('slot_id')
            if slot_id:
                slot = get_object_or_404(BellSchedule, pk=slot_id)
                num = slot.lesson_number
                slot.delete()
                messages.success(request, f"Урок №{num} видалено з розкладу дзвінків.")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        elif action == 'reset_default_bells':
            BellSchedule.objects.all().delete()
            BellSchedule.seed_default_schedule()
            messages.success(request, "Відновлено стандартний розклад дзвінків (1–8 уроки)! 🔔")
            return redirect(f"{reverse('teacher_settings')}?tab=environment")

        # ── 3. ДІЇ ШТУЧНОГО ІНТЕЛЕКТУ ТА ПРІОРИТЕТІВ МОДЕЛЕЙ ─────────────────
        elif action == 'save_ai_config' or (not action and ('api_key' in request.POST or 'temperature' in request.POST or 'system_prompt' in request.POST or 'ai_detector_tolerance_percent' in request.POST)):
            api_key = request.POST.get('api_key', '').strip()
            model_name = request.POST.get('model_name', '').strip()
            system_prompt = request.POST.get('system_prompt', DEFAULT_NUS_SYSTEM_PROMPT).strip()
            temperature_val = float(request.POST.get('temperature', 0.2))
            is_enabled = bool(request.POST.get('is_enabled'))
            tolerance_val = request.POST.get('ai_detector_tolerance_percent')

            ai_settings.api_key = api_key
            if model_name:
                ai_settings.model_name = model_name
                ai_settings.add_saved_model(model_name)
            ai_settings.system_prompt = system_prompt
            ai_settings.temperature = temperature_val
            ai_settings.is_enabled = is_enabled
            if tolerance_val is not None:
                try:
                    ai_settings.ai_detector_tolerance_percent = max(0, min(100, int(tolerance_val)))
                except (ValueError, TypeError):
                    pass
            ai_settings.save()
            messages.success(request, "Параметри Google Gemini AI успішно збережено! 🤖")
            if request.path == reverse('ai_settings'):
                return redirect('ai_settings')
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'add_custom_model':
            new_model = request.POST.get('new_model_name', '').strip()
            priority_val = request.POST.get('priority', '').strip()
            p_int = int(priority_val) if priority_val.isdigit() else None
            make_active = bool(request.POST.get('make_active'))

            if new_model:
                if make_active:
                    p_int = 1
                ai_settings.add_saved_model(new_model, priority=p_int, enabled=True)
                if make_active:
                    ai_settings.model_name = new_model
                    ai_settings.save(update_fields=['model_name', 'updated_at'])
                messages.success(request, f"Модель «{new_model}» успішно додано з пріоритетом #{p_int or 'черги'}! 🚀")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'move_model_priority':
            m_name = request.POST.get('model_name', '').strip()
            direction = request.POST.get('direction', '').strip()
            if m_name and direction in ['up', 'down']:
                ai_settings.move_model_priority(m_name, direction)
                messages.info(request, f"Порядок моделі «{m_name}» оновлено.")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'toggle_model_enabled':
            m_name = request.POST.get('model_name', '').strip()
            if m_name:
                ai_settings.toggle_model_enabled(m_name)
                messages.info(request, f"Статус активності моделі «{m_name}» змінено.")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'delete_model':
            del_model = request.POST.get('model_to_delete', '').strip()
            if del_model:
                ai_settings.remove_saved_model(del_model)
                messages.warning(request, f"Модель «{del_model}» видалено зі списку.")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'switch_model':
            switch_to = request.POST.get('switch_to_model', '').strip()
            if switch_to:
                ai_settings.model_name = switch_to
                ai_settings.add_saved_model(switch_to, priority=1)
                messages.success(request, f"Активну модель змінено на «{switch_to}» (Пріоритет #1)!")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'reset_prompt':
            ai_settings.system_prompt = DEFAULT_NUS_SYSTEM_PROMPT
            ai_settings.save(update_fields=['system_prompt', 'updated_at'])
            messages.success(request, "Системний промт успішно скинуто до офіційного стандарту НУШ! 🔄")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        # ── 4. ШАБЛОНИ КРИТЕРІЇВ ОЦІНЮВАННЯ ТА ФАЙЛИ МОН ─────────────────────
        elif action == 'create_criteria_preset':
            p_name = request.POST.get('preset_name', '').strip()
            p_eval_type = request.POST.get('evaluation_type', 'nus_gr').strip()
            p_desc = request.POST.get('description', '').strip()
            p_prompt = request.POST.get('system_prompt', '').strip()
            p_gr_definitions = request.POST.get('gr_definitions', '').strip()
            p_doc = request.FILES.get('document_file')
            p_is_def = (request.POST.get('is_default') == 'on' or request.POST.get('is_default') == '1')

            if p_name:
                preset = AICriteriaPreset.objects.create(
                    name=p_name,
                    evaluation_type=p_eval_type,
                    description=p_desc,
                    system_prompt=p_prompt,
                    gr_definitions=p_gr_definitions,
                    document_file=p_doc,
                    is_default=p_is_def,
                    is_system=False
                )
                if p_doc:
                    preset.document_name = p_doc.name
                    preset.extract_and_save_document_text()
                    preset.save()
                messages.success(request, f"Шаблон критеріїв «{p_name}» успішно створено!")
            else:
                messages.error(request, "Вкажіть назву шаблону критеріїв.")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'update_criteria_preset':
            preset_id = request.POST.get('preset_id')
            preset = get_object_or_404(AICriteriaPreset, id=preset_id)
            p_name = request.POST.get('preset_name', '').strip()
            p_eval_type = request.POST.get('evaluation_type', 'nus_gr').strip()
            p_desc = request.POST.get('description', '').strip()
            p_prompt = request.POST.get('system_prompt', '').strip()
            p_gr_definitions = request.POST.get('gr_definitions', '').strip()
            p_doc = request.FILES.get('document_file')
            remove_doc = (request.POST.get('remove_document') == '1')
            p_is_def = (request.POST.get('is_default') == 'on' or request.POST.get('is_default') == '1')

            if p_name:
                preset.name = p_name
                preset.evaluation_type = p_eval_type
                preset.description = p_desc
                preset.system_prompt = p_prompt
                preset.gr_definitions = p_gr_definitions
                if p_is_def:
                    preset.is_default = True

                if remove_doc:
                    if preset.document_file:
                        try:
                            preset.document_file.delete(save=False)
                        except Exception:
                            pass
                    preset.document_file = None
                    preset.document_name = ''
                    preset.extracted_criteria_text = ''
                elif p_doc:
                    preset.document_file = p_doc
                    preset.document_name = p_doc.name
                    preset.extract_and_save_document_text()

                preset.save()
                messages.success(request, f"Шаблон критеріїв «{p_name}» оновлено!")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'delete_criteria_preset':
            preset_id = request.POST.get('preset_id')
            preset = get_object_or_404(AICriteriaPreset, id=preset_id)
            p_name = preset.name
            was_default = preset.is_default

            if preset.document_file:
                try:
                    preset.document_file.delete(save=False)
                except Exception:
                    pass
            preset.delete()

            # Якщо видалено активний дефолтний шаблон, призначаємо новий
            if was_default:
                other_preset = AICriteriaPreset.objects.first()
                if other_preset:
                    other_preset.is_default = True
                    other_preset.save(update_fields=['is_default'])
                else:
                    AICriteriaPreset.ensure_default_presets()

            messages.warning(request, f"Шаблон критеріїв «{p_name}» успішно видалено.")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'restore_default_presets':
            AICriteriaPreset.ensure_default_presets(force_recreate=True)
            messages.success(request, "Стандартні галузеві шаблони критеріїв МОН України відновлено та оновлено! ⭐")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

        elif action == 'set_default_criteria_preset':
            preset_id = request.POST.get('preset_id')
            preset = get_object_or_404(AICriteriaPreset, id=preset_id)
            preset.is_default = True
            preset.save()
            messages.success(request, f"Шаблон «{preset.name}» встановлено як активний за замовчуванням ⭐")
            return redirect(f"{reverse('teacher_settings')}?tab=ai")

    # Підготовка даних контексту для сторінки налаштувань
    teacher_subjects = teacher.subjects.all().order_by('name')
    teacher_classes = teacher.classes.all().order_by('grade', 'letter', 'name')
    other_subjects = Subject.objects.exclude(id__in=teacher_subjects.values_list('id', flat=True)).order_by('name')
    other_classes = ClassGroup.objects.exclude(id__in=teacher_classes.values_list('id', flat=True)).order_by('grade', 'letter', 'name')
    all_teachers = Teacher.objects.select_related('user').all()

    # Гарантуємо наявність базових шаблонів критеріїв оцінювання
    AICriteriaPreset.ensure_default_presets()
    criteria_presets = AICriteriaPreset.objects.all()
    default_preset = AICriteriaPreset.objects.filter(is_default=True).first() or criteria_presets.first()

    # Дані AI та статистики
    models_with_priority = ai_settings.get_models_with_priority()
    saved_models = [m['name'] for m in models_with_priority]
    active_fallback_chain = ai_settings.get_active_fallback_chain()

    # Перевірки ШІ: рахуємо і вчительські (ai_status != 'none') і самоперевірки учнів (student_ai_checked=True)
    teacher_ai_checks = Submission.objects.exclude(ai_status__in=['none', '']).count()
    student_ai_checks = Submission.objects.filter(student_ai_checked=True).count()
    total_ai_checks = teacher_ai_checks + student_ai_checks

    total_ai_success = Submission.objects.filter(ai_status='success').count()
    total_ai_failed = Submission.objects.filter(ai_status='failed').count()
    total_ai_unsupported = Submission.objects.filter(ai_status='unsupported').count()

    # Оцінки застосовані (або через AI, або напряму)
    total_applied_grades = Submission.objects.filter(
        Q(graded_by__isnull=False) | Q(grade__isnull=False)
    ).count()

    # Середній відсоток ШІ-генерованості (де є дані)
    from django.db.models import Avg
    avg_ai_percent_result = Submission.objects.filter(
        ai_generated_percent__isnull=False
    ).aggregate(avg=Avg('ai_generated_percent'))
    avg_ai_percent = round(avg_ai_percent_result['avg'] or 0, 1)

    # Підозрілі (ai_generated_percent > порогового значення)
    tolerance = getattr(ai_settings, 'ai_detector_tolerance_percent', 70) or 70
    total_ai_detected = Submission.objects.filter(
        ai_generated_percent__isnull=False,
        ai_generated_percent__gte=tolerance,
    ).count()

    db_models = list(Submission.objects.exclude(ai_model_used='').values_list('ai_model_used', flat=True).distinct())
    all_known_models = list(dict.fromkeys(saved_models + db_models))
    model_stats = []

    for m in all_known_models:
        m_count = Submission.objects.filter(ai_model_used=m).count()
        m_success = Submission.objects.filter(ai_model_used=m, ai_status='success').count()
        m_failed = Submission.objects.filter(ai_model_used=m, ai_status='failed').count()
        m_last = Submission.objects.filter(ai_model_used=m).order_by('-ai_reviewed_at').first()
        m_priority_info = next((item for item in models_with_priority if item['name'] == m), None)

        model_stats.append({
            'name': m,
            'total_checks': m_count,
            'success_checks': m_success,
            'failed_checks': m_failed,
            'is_active': (m == ai_settings.model_name),
            'is_saved': (m in saved_models),
            'priority': m_priority_info['priority'] if m_priority_info else None,
            'enabled': m_priority_info['enabled'] if m_priority_info else False,
            'last_used': m_last.ai_reviewed_at if m_last else None,
        })

    # Сортування статистики: спочатку за пріоритетом
    model_stats.sort(key=lambda x: (x['priority'] if x['priority'] is not None else 999, x['name']))

    level_high = Submission.objects.filter(ai_score_level__icontains='висок').count()
    level_sufficient = Submission.objects.filter(ai_score_level__icontains='достат').count()
    level_medium = Submission.objects.filter(ai_score_level__icontains='серед').count()
    level_initial = Submission.objects.filter(ai_score_level__icontains='почат').count()
    level_rework = Submission.objects.filter(
        Q(ai_suggested_grade__icontains='доопрацю') | Q(ai_score_level__icontains='доопрацю')
    ).count()

    # Дані середовища
    env_stats = {
        'total_teachers': all_teachers.count(),
        'total_subjects': Subject.objects.count(),
        'total_classes': ClassGroup.objects.count(),
        'total_assignments': Assignment.objects.count(),
        'total_submissions': Submission.objects.count(),
        'db_size': _get_database_size_display(),
    }

    context = {
        'active_tab': tab,
        'teacher': teacher,
        'school': school,
        'profile_form': profile_form,
        'subject_form': subject_form,
        'class_form': class_form,
        'teacher_create_form': teacher_create_form,
        'teacher_subjects': teacher_subjects,
        'teacher_classes': teacher_classes,
        'other_subjects': other_subjects,
        'other_classes': other_classes,
        'all_teachers': all_teachers,
        'is_superuser': request.user.is_superuser,
        'is_superadmin_mode': request.session.get('superadmin_mode', False),
        'bell_schedules': BellSchedule.objects.all().order_by('lesson_number'),
        'env_stats': env_stats,
        'ai_settings': ai_settings,
        'saved_models': saved_models,
        'models_with_priority': models_with_priority,
        'active_fallback_chain': active_fallback_chain,
        'model_stats': model_stats,
        'default_prompt': DEFAULT_NUS_SYSTEM_PROMPT,
        'default_nus_gr_prompt': DEFAULT_NUS_GR_SYSTEM_PROMPT,
        'default_traditional_prompt': DEFAULT_TRADITIONAL_SYSTEM_PROMPT,
        'criteria_presets': criteria_presets,
        'default_preset': default_preset,
        'total_ai_checks': total_ai_checks,
        'teacher_ai_checks': teacher_ai_checks,
        'student_ai_checks': student_ai_checks,
        'total_ai_success': total_ai_success,
        'total_ai_failed': total_ai_failed,
        'total_ai_unsupported': total_ai_unsupported,
        'total_applied_grades': total_applied_grades,
        'avg_ai_percent': avg_ai_percent,
        'total_ai_detected': total_ai_detected,
        'level_high': level_high,
        'level_sufficient': level_sufficient,
        'level_medium': level_medium,
        'level_initial': level_initial,
        'level_rework': level_rework,
    }
    return render(request, 'feed/settings.html', context)


@teacher_required
def ai_settings_view(request):
    """
    Сумісність: перенаправлення або відображення налаштувань ШІ у Єдиному центрі.
    """
    if request.method == 'GET':
        q = request.GET.copy()
        q['tab'] = 'ai'
        request.GET = q
    return teacher_settings_view(request)


@require_POST
def api_test_gemini_connection(request):
    """
    AJAX endpoint для перевірки валідності API ключа Google Gemini.
    """
    from .gemini_service import test_gemini_connection
    api_key = request.POST.get('api_key', '').strip()
    model_name = request.POST.get('model_name', '').strip()

    success, message, model_used = test_gemini_connection(api_key=api_key, model_name=model_name)
    return JsonResponse({
        'success': success,
        'message': message,
        'model': model_used
    })


@teacher_required
@require_POST
def ai_check_single_submission(request, submission_id):
    """
    Виконує ШІ-перевірку конкретної роботи учня за допомогою Google Gemini.
    Підтримує вибір шаблону критеріїв та вибір конкретних груп результатів (ГР) прапорцями.
    """
    from .gemini_service import evaluate_submission_with_gemini
    submission = get_object_or_404(Submission.objects.select_related('assignment', 'class_group'), id=submission_id)

    custom_prompt = request.POST.get('custom_prompt', '').strip() or None
    preset_id = request.POST.get('preset_id') or request.GET.get('preset_id')
    selected_gr_codes_raw = request.POST.get('selected_gr_codes')
    selected_gr_codes = None
    if selected_gr_codes_raw:
        try:
            selected_gr_codes = json.loads(selected_gr_codes_raw)
        except Exception:
            selected_gr_codes = [c.strip() for c in selected_gr_codes_raw.split(',') if c.strip()]
    elif request.POST.getlist('selected_gr_codes'):
        selected_gr_codes = request.POST.getlist('selected_gr_codes')

    result = evaluate_submission_with_gemini(
        submission,
        custom_prompt=custom_prompt,
        preset_id=preset_id,
        selected_gr_codes=selected_gr_codes
    )

    # Якщо запит із AJAX, fetch, або ?format=json — повертаємо чистий JSON
    is_ajax = (
        request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest'
        or request.GET.get('format') == 'json'
        or 'application/json' in request.headers.get('accept', '').lower()
        or request.content_type == 'application/json'
    )
    if is_ajax or request.method == 'POST':
        return JsonResponse(result)

    if result.get('status') == 'success':
        messages.success(request, f"ШІ оцінив роботу учня {submission.get_student_full_name()} на «{submission.ai_suggested_grade}» ({submission.ai_score_level})!")
    else:
        messages.warning(request, f"ШІ не зміг перевірити роботу: {result.get('error')}")

    return redirect('view_file', submission_id=submission.id)



@teacher_required
def ai_batch_check_view(request):
    """
    Сторінка масової перевірки робіт учнів штучним інтелектом:
    - Фільтр за класом або завданням
    - Вибір шаблону критеріїв оцінювання (НУШ, Класична 1-12, шаблони МОН)
    - Вибір: тільки неоцінені чи всі роботи
    - Інтерактивний покроковий запуск із прогрес-баром
    """
    ai_settings = AISettings.get_solo()
    all_classes = ClassGroup.objects.all().order_by('grade', 'letter')
    all_assignments = Assignment.objects.all().order_by('-published_at')

    AICriteriaPreset.ensure_default_presets()
    criteria_presets = AICriteriaPreset.objects.all()
    default_preset = AICriteriaPreset.objects.filter(is_default=True).first() or criteria_presets.first()

    # Підрахунок доступних робіт
    total_submissions = Submission.objects.count()
    ungraded_submissions = Submission.objects.filter(Q(grade__isnull=True) | Q(grade='')).count()
    ai_evaluated_submissions = Submission.objects.filter(ai_status='success').count()

    context = {
        'ai_settings': ai_settings,
        'all_classes': all_classes,
        'all_assignments': all_assignments,
        'criteria_presets': criteria_presets,
        'default_preset': default_preset,
        'total_submissions': total_submissions,
        'ungraded_submissions': ungraded_submissions,
        'ai_evaluated_submissions': ai_evaluated_submissions,
    }
    return render(request, 'feed/ai_batch_check.html', context)


@teacher_required
@require_POST
def api_ai_get_batch_queue(request):
    """
    AJAX endpoint: повертає список ID робіт для пакетної обробки за фільтрами.
    """
    class_id = request.POST.get('class_id')
    assignment_id = request.POST.get('assignment_id')
    only_ungraded = request.POST.get('only_ungraded') == 'true'
    only_unreviewed_ai = request.POST.get('only_unreviewed_ai') == 'true'

    qs = Submission.objects.all().select_related('class_group', 'assignment')

    if class_id:
        qs = qs.filter(class_group_id=class_id)
    if assignment_id:
        qs = qs.filter(assignment_id=assignment_id)
    if only_ungraded:
        qs = qs.filter(Q(grade__isnull=True) | Q(grade=''))
    if only_unreviewed_ai:
        qs = qs.exclude(ai_status='success')

    items = []
    for s in qs.order_by('-submitted_at')[:300]:
        items.append({
            'id': s.id,
            'student_name': s.get_student_full_name(),
            'class_name': s.class_group.name if s.class_group else '—',
            'assignment_title': s.assignment.title if s.assignment else '—',
            'current_grade': s.grade or 'Немає',
            'ai_status': s.ai_status,
            'ai_grade': s.ai_suggested_grade or '—',
            'ai_feedback': s.ai_feedback or '',
            'gr_results': s.get_ai_gr_results_list(),
            'gr_avg': s.get_ai_gr_average(),
            'has_format_warning': ('Зауваження до формату файлу' in (s.ai_feedback or ''))
        })

    return JsonResponse({'queue': items, 'total': len(items)})


@teacher_required
@require_POST
def api_ai_process_item(request, submission_id):
    """
    AJAX endpoint: обробляє один елемент із черги пакетної ШІ-перевірки за обраним шаблоном критеріїв.
    """
    from .gemini_service import evaluate_submission_with_gemini
    submission = get_object_or_404(Submission.objects.select_related('assignment', 'class_group'), id=submission_id)

    preset_id = request.POST.get('preset_id') or None
    selected_gr_codes_raw = request.POST.get('selected_gr_codes')
    selected_gr_codes = None
    if selected_gr_codes_raw:
        try:
            selected_gr_codes = json.loads(selected_gr_codes_raw)
        except Exception:
            selected_gr_codes = [c.strip() for c in selected_gr_codes_raw.split(',') if c.strip()]

    res = evaluate_submission_with_gemini(submission, preset_id=preset_id, selected_gr_codes=selected_gr_codes)
    return JsonResponse({
        'id': submission.id,
        'student_name': submission.get_student_full_name(),
        'status': res.get('status'),
        'suggested_grade': submission.ai_suggested_grade or '—',
        'level': submission.ai_score_level or '',
        'feedback': submission.ai_feedback or '',
        'clean_feedback': submission.get_clean_ai_feedback_for_student(),
        'gr_results': submission.get_ai_gr_results_list(),
        'gr_avg': submission.get_ai_gr_average(),
        'format_warning': res.get('format_warning') or '',
        'error': submission.ai_error_reason or ''
    })


@teacher_required
@require_POST
def ai_apply_suggested_grade(request, submission_id):
    """
    Застосовує попередню оцінку та коментар ШІ як офіційну оцінку вчителя в 1 клік.
    Оцінки за окремими ГР зберігаються в системі конфіденційно, а у відгук учню додаються
    лише рекомендації та педагогічні зауваження.
    """
    submission = get_object_or_404(Submission, id=submission_id)

    if not submission.ai_suggested_grade:
        return JsonResponse({'status': 'error', 'message': 'У цієї роботи немає попередньої оцінки ШІ.'}, status=400)

    # Встановлюємо оцінку
    submission.grade = submission.ai_suggested_grade
    submission.graded_by = request.user
    submission.graded_at = timezone.now()
    if hasattr(request.user, 'teacher_profile'):
        submission.teacher = request.user.teacher_profile
    submission.save(update_fields=['grade', 'graded_by', 'graded_at', 'teacher'])

    # Додаємо коментар ШІ у відгуки вчителя якщо його ще немає (використовуючи очищений від оцінок ГР відгук)
    clean_feedback = submission.get_clean_ai_feedback_for_student()
    if clean_feedback:
        ai_tag = "🤖 [Рекомендації та відгук ШІ]:"
        already_exists = submission.comments.filter(text__startswith="🤖 [").exists()
        if not already_exists:
            SubmissionComment.objects.create(
                submission=submission,
                author=request.user,
                text=f"{ai_tag}\n{clean_feedback}"
            )

    # Синхронізуємо оцінку зі співавторами
    coauthors_graded = sync_grades_to_coauthors(submission, submission.grade, request.user)

    log_msg = f"Вчитель прийняв оцінку ШІ «{submission.grade}» для учня {submission.get_student_full_name()}"
    if coauthors_graded:
        log_msg += f" (також виставлено оцінку співавторам: {', '.join(coauthors_graded)})"

    log_submission_activity(
        request.user,
        'grade_submission',
        log_msg,
        submission=submission
    )

    is_ajax = (
        request.headers.get('x-requested-with', '').lower() == 'xmlhttprequest'
        or request.GET.get('format') == 'json'
        or 'application/json' in request.headers.get('accept', '').lower()
        or request.content_type == 'application/json'
    )
    if is_ajax or request.method == 'POST':
        return JsonResponse({
            'status': 'success',
            'grade': submission.grade,
            'coauthors_graded': coauthors_graded,
            'message': f"Оцінку «{submission.grade}» успішно встановлено!" + (f" (також співавторам: {', '.join(coauthors_graded)})" if coauthors_graded else "")
        })

    succ_msg = f"Оцінку «{submission.grade}» успішно застосовано до роботи учня {submission.get_student_full_name()}!"
    if coauthors_graded:
        succ_msg += f" Оцінку також виставлено співавторам: {', '.join(coauthors_graded)}."
    messages.success(request, succ_msg)
    return redirect('view_file', submission_id=submission.id)


# ═══════════════════════════════════════════════════════════════════════════════
# ПЕРЕНЕСЕННЯ УРОКІВ, 30-ДЕННИЙ КАЛЕНДАР, РОЗКЛАД ТА ZIP ІМПОРТ/ЕКСПОРТ
# ═══════════════════════════════════════════════════════════════════════════════

@teacher_required
def reschedule_assignment(request, pk):
    """
    Перенесення уроку/завдання на іншу дату та урок із фіксацією причини.
    Оновлює цільовий розклад (AssignmentScheduleTarget) та за потреби дедлайн.
    """
    from datetime import datetime, timedelta, date
    from django.http import JsonResponse
    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.POST.get('is_ajax')

        class_id = request.POST.get('class_group')
        new_date_str = request.POST.get('new_date')
        new_slot_id = request.POST.get('new_bell_slot')
        reason_type = request.POST.get('reason_type', AssignmentRescheduleLog.REASON_AIR_RAID)
        reason_comment = request.POST.get('reason_comment', '').strip()
        update_due_date = request.POST.get('update_due_date') in ['1', 'true', 'on', True]

        if not new_date_str:
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'Вкажіть нову дату уроку.'}, status=400)
            messages.error(request, 'Помилка: обов\'язково вкажіть нову дату уроку.')
            return redirect(request.META.get('HTTP_REFERER') or 'teacher_dashboard')

        try:
            new_date = datetime.strptime(new_date_str, '%Y-%m-%d').date()
        except ValueError:
            if is_ajax:
                return JsonResponse({'success': False, 'error': 'Некоректний формат дати.'}, status=400)
            messages.error(request, 'Помилка: некоректний формат дати.')
            return redirect(request.META.get('HTTP_REFERER') or 'teacher_dashboard')

        class_group = None
        if class_id:
            class_group = ClassGroup.objects.filter(id=class_id).first()

        new_slot = None
        if new_slot_id:
            new_slot = BellSchedule.objects.filter(id=new_slot_id).first()

        # Визначаємо початкову дату та урок
        existing_target = None
        if class_group:
            existing_target = assignment.schedule_targets.filter(class_group=class_group).first()
        if not existing_target:
            existing_target = assignment.schedule_targets.first()

        if existing_target and existing_target.target_date:
            orig_date = existing_target.target_date
            orig_slot = existing_target.bell_slot
        else:
            orig_date = assignment.published_at.date() if assignment.published_at else timezone.now().date()
            orig_slot = None

        # Фіксуємо перенесення в базі даних
        reschedule_log = AssignmentRescheduleLog.objects.create(
            assignment=assignment,
            teacher=teacher,
            class_group=class_group,
            original_date=orig_date,
            original_bell_slot=orig_slot,
            new_date=new_date,
            new_bell_slot=new_slot,
            reason_type=reason_type,
            reason_comment=reason_comment
        )

        # Оновлюємо цільову дату для класів
        target_dow = new_date.weekday() + 1
        targets_to_update = []
        if class_group:
            t, _ = AssignmentScheduleTarget.objects.get_or_create(
                assignment=assignment,
                class_group=class_group
            )
            targets_to_update.append(t)
        else:
            assigned_classes = list(assignment.classes.all())
            if assigned_classes:
                for cg in assigned_classes:
                    t, _ = AssignmentScheduleTarget.objects.get_or_create(
                        assignment=assignment,
                        class_group=cg
                    )
                    targets_to_update.append(t)
            else:
                t, _ = AssignmentScheduleTarget.objects.get_or_create(
                    assignment=assignment,
                    class_group=None
                )
                targets_to_update.append(t)

        for t in targets_to_update:
            t.target_date = new_date
            t.target_day_of_week = target_dow
            t.bell_slot = new_slot
            t.save()

        if update_due_date:
            assignment.due_date = new_date
            assignment.save(update_fields=['due_date'])

        reason_label = reschedule_log.get_reason_type_display()
        log_submission_activity(
            request.user,
            'rescheduled',
            f"Урок «{assignment.title}» перенесено з {orig_date.strftime('%d.%m.%Y')} на {new_date.strftime('%d.%m.%Y')} ({reason_label})"
        )

        slot_text = f" ({new_slot.lesson_number}-й урок)" if new_slot else ""
        msg = f"Урок «{assignment.title}» успішно перенесено на {new_date.strftime('%d.%m.%Y')}{slot_text}! Причина: {reason_label}."
        messages.success(request, msg)

        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': msg,
                'new_date': new_date.strftime('%d.%m.%Y'),
                'reason_display': reason_label,
                'reason_icon': reschedule_log.reason_icon,
                'new_slot_text': slot_text
            })

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect('teacher_dashboard')


@teacher_required
def api_next_lesson_slot(request):
    """
    API для авто-визначення наступного уроку за розкладом вчителя для обраного класу.
    """
    from datetime import datetime, timedelta, date
    from django.http import JsonResponse
    from django.utils import timezone

    teacher = request.user.teacher_profile
    class_id = request.GET.get('class_id')
    if not class_id:
        return JsonResponse({'success': False, 'error': 'Не вказано клас'}, status=400)

    now = timezone.localtime(timezone.now())
    today = now.date()
    today_dow = today.weekday() + 1
    now_min = now.time().hour * 60 + now.time().minute

    schedules = list(TeacherLessonSchedule.objects.filter(
        teacher=teacher,
        class_group_id=class_id
    ).select_related('bell_slot').order_by('day_of_week', 'bell_slot__lesson_number'))

    if not schedules:
        # Немає розкладу для цього класу — пропонуємо завтра, 1-й урок
        tomorrow = today + timedelta(days=1)
        first_slot = BellSchedule.objects.order_by('lesson_number').first()
        return JsonResponse({
            'success': True,
            'has_schedule': False,
            'next_date': tomorrow.strftime('%Y-%m-%d'),
            'next_date_display': tomorrow.strftime('%d.%m.%Y'),
            'bell_slot_id': first_slot.id if first_slot else None,
            'lesson_number': first_slot.lesson_number if first_slot else 1,
            'info': 'Розклад для цього класу не налаштовано. Запропоновано наступний день.'
        })

    # Шукаємо уроки сьогодні пізніше поточного часу
    for s in schedules:
        if s.day_of_week == today_dow:
            s_min = s.bell_slot.start_time.hour * 60 + s.bell_slot.start_time.minute
            if s_min > now_min:
                return JsonResponse({
                    'success': True,
                    'has_schedule': True,
                    'next_date': today.strftime('%Y-%m-%d'),
                    'next_date_display': today.strftime('%d.%m.%Y'),
                    'bell_slot_id': s.bell_slot.id,
                    'lesson_number': s.bell_slot.lesson_number,
                    'start_time': s.bell_slot.start_time.strftime('%H:%M'),
                    'info': f"Сьогодні, {s.bell_slot.lesson_number}-й урок о {s.bell_slot.start_time.strftime('%H:%M')}"
                })

    # Шукаємо уроки в майбутні дні поточного тижня
    for s in schedules:
        if s.day_of_week > today_dow:
            delta_days = s.day_of_week - today_dow
            target_d = today + timedelta(days=delta_days)
            return JsonResponse({
                'success': True,
                'has_schedule': True,
                'next_date': target_d.strftime('%Y-%m-%d'),
                'next_date_display': target_d.strftime('%d.%m.%Y'),
                'bell_slot_id': s.bell_slot.id,
                'lesson_number': s.bell_slot.lesson_number,
                'start_time': s.bell_slot.start_time.strftime('%H:%M'),
                'info': f"{s.get_day_of_week_display()}, {s.bell_slot.lesson_number}-й урок ({target_d.strftime('%d.%m')})"
            })

    # Якщо на цьому тижні немає — беремо найперший урок на наступному тижні
    first_sch = schedules[0]
    days_ahead = (7 - today_dow) + first_sch.day_of_week
    next_week_d = today + timedelta(days=days_ahead)
    return JsonResponse({
        'success': True,
        'has_schedule': True,
        'next_date': next_week_d.strftime('%Y-%m-%d'),
        'next_date_display': next_week_d.strftime('%d.%m.%Y'),
        'bell_slot_id': first_sch.bell_slot.id,
        'lesson_number': first_sch.bell_slot.lesson_number,
        'start_time': first_sch.bell_slot.start_time.strftime('%H:%M'),
        'info': f"{first_sch.get_day_of_week_display()}, {first_sch.bell_slot.lesson_number}-й урок ({next_week_d.strftime('%d.%m')})"
    })


@teacher_required
def teacher_rescheduled_calendar(request):
    """
    Окремий 30-денний календар перенесених уроків для вчителя.
    Відображає 30-денну сітку з відмітками причин перенесень, детальну таблицю
    та статистику за типами причин (повітряна тривога, хвороба тощо).
    """
    from datetime import datetime, timedelta, date
    from django.utils import timezone
    teacher = request.user.teacher_profile
    is_super = request.user.is_superuser or request.session.get('superadmin_mode')

    now = timezone.localtime(timezone.now())
    today = now.date()

    # Вікно на 30 днів (від -14 днів до +15 днів)
    center_date_str = request.GET.get('date')
    if center_date_str:
        try:
            center_date = datetime.strptime(center_date_str, '%Y-%m-%d').date()
        except ValueError:
            center_date = today
    else:
        center_date = today

    start_date = center_date - timedelta(days=14)
    end_date = center_date + timedelta(days=15)  # 30 днів

    qs = AssignmentRescheduleLog.objects.all().select_related(
        'assignment', 'teacher', 'class_group', 'original_bell_slot', 'new_bell_slot', 'assignment__subject'
    )
    if not is_super:
        qs = qs.filter(teacher=teacher)

    selected_class_id = request.GET.get('class_group')
    if selected_class_id:
        qs = qs.filter(class_group_id=selected_class_id)

    selected_reason = request.GET.get('reason_type')
    if selected_reason:
        qs = qs.filter(reason_type=selected_reason)

    logs_30_days = list(qs.filter(
        Q(new_date__range=[start_date, end_date]) | Q(original_date__range=[start_date, end_date])
    ).order_by('-new_date', '-rescheduled_at'))

    days_grid = []
    uk_weekdays_short = {1: 'Пн', 2: 'Вт', 3: 'Ср', 4: 'Чт', 5: 'Пт', 6: 'Сб', 7: 'Нд'}

    logs_by_new_date = {}
    logs_by_orig_date = {}
    for log in logs_30_days:
        logs_by_new_date.setdefault(log.new_date, []).append(log)
        logs_by_orig_date.setdefault(log.original_date, []).append(log)

    uk_months_short = {1: 'Січ', 2: 'Лют', 3: 'Бер', 4: 'Кві', 5: 'Тра', 6: 'Чер',
                       7: 'Лип', 8: 'Сер', 9: 'Вер', 10: 'Жов', 11: 'Лис', 12: 'Гру'}
    curr = start_date
    while curr <= end_date:
        new_logs = logs_by_new_date.get(curr, [])
        orig_logs = logs_by_orig_date.get(curr, [])
        days_grid.append({
            'date': curr,
            'date_str': curr.strftime('%Y-%m-%d'),
            'is_today': (curr == today),
            'is_weekend': (curr.weekday() >= 5),
            'weekday_name': uk_weekdays_short.get(curr.weekday() + 1, ''),
            'day_number': curr.day,
            'month_name': uk_months_short.get(curr.month, ''),
            'new_logs': new_logs,
            'orig_logs': orig_logs,
            'has_events': bool(new_logs or orig_logs),
            'count': len(new_logs) + len(orig_logs),
        })
        curr += timedelta(days=1)

    # Статистика за типами причин
    reason_stats = {}
    for choice_code, choice_title in AssignmentRescheduleLog.REASON_CHOICES:
        count = sum(1 for log in logs_30_days if log.reason_type == choice_code)
        if count > 0:
            reason_stats[choice_code] = {
                'title': choice_title,
                'count': count,
                'percent': int((count / len(logs_30_days)) * 100) if logs_30_days else 0,
            }

    teacher_classes = teacher.classes.all().order_by('grade', 'letter', 'name') if teacher else ClassGroup.objects.all().order_by('grade', 'letter', 'name')

    context = {
        'today': today,
        'center_date': center_date,
        'start_date': start_date,
        'end_date': end_date,
        'prev_date': (center_date - timedelta(days=30)).strftime('%Y-%m-%d'),
        'next_date': (center_date + timedelta(days=30)).strftime('%Y-%m-%d'),
        'days_grid': days_grid,
        'logs_list': logs_30_days,
        'total_rescheduled_count': len(logs_30_days),
        'reason_stats': reason_stats,
        'reason_choices': AssignmentRescheduleLog.REASON_CHOICES,
        'teacher_classes': teacher_classes,
        'selected_class_id': int(selected_class_id) if selected_class_id and selected_class_id.isdigit() else None,
        'selected_reason': selected_reason,
    }
    return render(request, 'feed/teacher_rescheduled_calendar.html', context)


@login_required
def api_today_schedule(request):
    """
    Повертає детальний JSON із розкладом на сьогодні, живим статусом поточного уроку
    та тривалістю перерв для модального вікна «Розклад на сьогодні».
    """
    from django.http import JsonResponse
    from django.utils import timezone

    teacher = getattr(request.user, 'teacher_profile', None)
    now = timezone.localtime(timezone.now())
    today_date = now.date()
    today_weekday = today_date.weekday() + 1
    now_minutes = now.time().hour * 60 + now.time().minute

    uk_weekdays = {1: 'Понеділок', 2: 'Вівторок', 3: 'Середа', 4: 'Четвер', 5: "П'ятниця", 6: 'Субота', 7: 'Неділя'}
    uk_months = {1: 'січня', 2: 'лютого', 3: 'березня', 4: 'квітня', 5: 'травня', 6: 'червня', 7: 'липня', 8: 'серпня', 9: 'вересня', 10: 'жовтня', 11: 'листопада', 12: 'грудня'}

    date_str = f"{today_date.day} {uk_months.get(today_date.month, '')} {today_date.year}"
    weekday_str = uk_weekdays.get(today_weekday, '')

    bell_slots = list(BellSchedule.objects.all().order_by('lesson_number'))
    lessons = []

    teacher_schedule_map = {}
    if teacher:
        for sch in TeacherLessonSchedule.objects.filter(teacher=teacher, day_of_week=today_weekday).select_related('bell_slot', 'class_group', 'subject'):
            teacher_schedule_map[sch.bell_slot_id] = sch

    active_status = 'free'
    current_lesson_num = None

    for i, slot in enumerate(bell_slots):
        s_min = slot.start_time.hour * 60 + slot.start_time.minute
        e_min = slot.end_time.hour * 60 + slot.end_time.minute

        is_current = (s_min <= now_minutes < e_min)
        is_past = (now_minutes >= e_min)
        is_future = (now_minutes < s_min)

        sch_entry = teacher_schedule_map.get(slot.id)
        class_name = sch_entry.class_group.name if sch_entry and sch_entry.class_group else None
        subject_name = sch_entry.subject.name if sch_entry and sch_entry.subject else None
        subject_icon = sch_entry.subject.icon if sch_entry and sch_entry.subject else '📚'
        subject_color = sch_entry.subject.color if sch_entry and sch_entry.subject else '#4f46e5'

        break_min = slot.get_break_after()
        is_break_now = False
        break_rem = 0
        if break_min and i + 1 < len(bell_slots):
            next_s_min = bell_slots[i + 1].start_time.hour * 60 + bell_slots[i + 1].start_time.minute
            if e_min <= now_minutes < next_s_min:
                is_break_now = True
                break_rem = next_s_min - now_minutes
                active_status = 'in_break'

        if is_current:
            active_status = 'in_lesson'
            current_lesson_num = slot.lesson_number

        rem_minutes = max(0, e_min - now_minutes) if is_current else 0

        lessons.append({
            'slot_id': slot.id,
            'lesson_number': slot.lesson_number,
            'start_time': slot.start_time.strftime('%H:%M'),
            'end_time': slot.end_time.strftime('%H:%M'),
            'duration_minutes': slot.duration_minutes,
            'has_class': bool(class_name),
            'class_name': class_name,
            'subject_name': subject_name,
            'subject_icon': subject_icon,
            'subject_color': subject_color,
            'is_current': is_current,
            'is_past': is_past,
            'is_future': is_future,
            'rem_minutes': rem_minutes,
            'break_after': break_min,
            'is_break_now': is_break_now,
            'break_rem_minutes': break_rem,
        })

    conducted_info = teacher.get_conducted_lessons_info() if teacher else None

    return JsonResponse({
        'success': True,
        'date_display': date_str,
        'weekday_display': weekday_str,
        'is_teacher': bool(teacher),
        'active_status': active_status,
        'current_lesson_num': current_lesson_num,
        'lessons': lessons,
        'conducted_info': conducted_info,
    })


@teacher_required
def update_conducted_lessons(request):
    """
    Оновлює кількість фактично проведених уроків вчителем
    або скидає її до автоматичного підрахунку системи.
    """
    from django.http import JsonResponse
    teacher = request.user.teacher_profile
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'reset':
            teacher.conducted_lessons_manual_count = None
            teacher.conducted_lessons_last_modified = timezone.now()
            teacher.save(update_fields=['conducted_lessons_manual_count', 'conducted_lessons_last_modified'])
            messages.success(request, 'Лічильник проведених уроків скинуто до автоматичного розрахунку системи! 🔄')
        else:
            count_val = request.POST.get('count')
            if count_val is not None and str(count_val).strip().isdigit():
                teacher.conducted_lessons_manual_count = int(count_val)
                teacher.conducted_lessons_last_modified = timezone.now()
                teacher.save(update_fields=['conducted_lessons_manual_count', 'conducted_lessons_last_modified'])
                messages.success(request, f'Кількість проведених уроків встановлено: {teacher.conducted_lessons_manual_count}! ✅')
            else:
                messages.error(request, 'Введіть коректне додатне число проведених уроків.')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            info = teacher.get_conducted_lessons_info()
            return JsonResponse({'success': True, 'info': info})

    referer = request.META.get('HTTP_REFERER')
    if referer and request.get_host() in referer:
        return redirect(referer)
    return redirect('teacher_students')


@teacher_required
def export_assignment_zip(request, pk):
    """
    Експорт конкретного завдання з усіма його файлами у структурований ZIP-пакет для флешки.
    """
    import zipfile
    import io
    import json
    from django.http import HttpResponse

    teacher = request.user.teacher_profile
    if request.user.is_superuser or request.session.get('superadmin_mode'):
        assignment = get_object_or_404(Assignment, pk=pk)
    else:
        assignment = get_object_or_404(Assignment, pk=pk, teacher=teacher)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        files_metadata = []
        for idx, af in enumerate(assignment.files.all()):
            if af.file and os.path.exists(af.file.path):
                ext = af.get_extension() or ''
                safe_name = os.path.basename(af.original_name or af.file.name)
                arc_name = f"files/{idx+1}_{safe_name}"
                zip_file.write(af.file.path, arc_name)
                files_metadata.append({
                    'original_name': af.original_name or safe_name,
                    'zip_path': arc_name,
                    'file_type': af.get_file_type(),
                })

        # Прив'язки до розкладу
        st_list = []
        for st in assignment.schedule_targets.all():
            st_list.append({
                'class_name': st.class_group.name if st.class_group else '',
                'target_date': st.target_date.strftime('%Y-%m-%d') if st.target_date else None,
                'target_day_of_week': st.target_day_of_week,
                'lesson_number': st.bell_slot.lesson_number if st.bell_slot else None,
            })

        assignment_data = {
            'title': assignment.title,
            'description': assignment.description,
            'subject_name': assignment.subject.name if assignment.subject else None,
            'subject_color': assignment.subject.color if assignment.subject else '#2563eb',
            'subject_icon': assignment.subject.icon if assignment.subject else '📚',
            'class_names': [c.name for c in assignment.classes.all()],
            'is_individual': assignment.is_individual,
            'student_name': assignment.student_name,
            'link_url': assignment.link_url,
            'link_label': assignment.link_label,
            'youtube_url': assignment.youtube_url,
            'status': assignment.status,
            'due_date': assignment.due_date.strftime('%Y-%m-%d') if assignment.due_date else None,
            'allow_student_ai_check': assignment.allow_student_ai_check,
            'allow_ai_usage': assignment.allow_ai_usage,
            'additional_links': [{'url': l.url, 'label': l.label} for l in assignment.additional_links.all()],
            'youtube_links': [{'url': y.url, 'title': y.title} for y in assignment.youtube_links.all()],
            'schedule_targets': st_list,
            'files': files_metadata,
        }

        manifest = {
            'version': '1.0',
            'exported_at': timezone.now().isoformat(),
            'teacher_name': teacher.full_name,
            'assignments': [assignment_data]
        }

        zip_file.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))

    zip_buffer.seek(0)
    safe_title = "".join(c for c in assignment.title if c.isalnum() or c in (' ', '_', '-')).strip()[:40] or "task"
    filename = f"schoolnet_task_{assignment.id}_{safe_title}.zip"

    response = HttpResponse(zip_buffer.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@teacher_required
def export_assignments_day_zip(request):
    """
    Масовий експорт завдань на обраний день або всіх активних завдань вчителя у ZIP-пакет для флешки.
    """
    import zipfile
    import io
    import json
    from django.http import HttpResponse

    teacher = request.user.teacher_profile
    date_str = request.GET.get('date')
    if date_str:
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = timezone.localtime(timezone.now()).date()
    else:
        target_date = timezone.localtime(timezone.now()).date()

    # Завдання, прив'язані до цієї дати уроку або створені/опубліковані в цей день
    assignments = list(Assignment.objects.filter(
        Q(schedule_targets__target_date=target_date) | Q(published_at__date=target_date),
        teacher=teacher
    ).distinct())

    if not assignments:
        # Якщо немає на дату, експортуємо останні активні опубліковані завдання
        assignments = list(Assignment.objects.filter(
            teacher=teacher,
            status=Assignment.STATUS_PUBLISHED
        ).order_by('-published_at')[:10])

    if not assignments:
        messages.warning(request, "Не знайдено завдань для експорту на цю дату.")
        return redirect('teacher_dashboard')

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        assignments_meta = []
        for a_idx, assignment in enumerate(assignments):
            files_metadata = []
            for f_idx, af in enumerate(assignment.files.all()):
                if af.file and os.path.exists(af.file.path):
                    safe_name = os.path.basename(af.original_name or af.file.name)
                    arc_name = f"files/task_{a_idx+1}_{f_idx+1}_{safe_name}"
                    zip_file.write(af.file.path, arc_name)
                    files_metadata.append({
                        'original_name': af.original_name or safe_name,
                        'zip_path': arc_name,
                        'file_type': af.get_file_type(),
                    })

            st_list = []
            for st in assignment.schedule_targets.all():
                st_list.append({
                    'class_name': st.class_group.name if st.class_group else '',
                    'target_date': st.target_date.strftime('%Y-%m-%d') if st.target_date else None,
                    'target_day_of_week': st.target_day_of_week,
                    'lesson_number': st.bell_slot.lesson_number if st.bell_slot else None,
                })

            assignments_meta.append({
                'title': assignment.title,
                'description': assignment.description,
                'subject_name': assignment.subject.name if assignment.subject else None,
                'subject_color': assignment.subject.color if assignment.subject else '#2563eb',
                'subject_icon': assignment.subject.icon if assignment.subject else '📚',
                'class_names': [c.name for c in assignment.classes.all()],
                'is_individual': assignment.is_individual,
                'student_name': assignment.student_name,
                'link_url': assignment.link_url,
                'link_label': assignment.link_label,
                'youtube_url': assignment.youtube_url,
                'status': assignment.status,
                'due_date': assignment.due_date.strftime('%Y-%m-%d') if assignment.due_date else None,
                'allow_student_ai_check': assignment.allow_student_ai_check,
                'allow_ai_usage': assignment.allow_ai_usage,
                'additional_links': [{'url': l.url, 'label': l.label} for l in assignment.additional_links.all()],
                'youtube_links': [{'url': y.url, 'title': y.title} for y in assignment.youtube_links.all()],
                'schedule_targets': st_list,
                'files': files_metadata,
            })

        manifest = {
            'version': '1.0',
            'exported_at': timezone.now().isoformat(),
            'teacher_name': teacher.full_name,
            'target_date': target_date.strftime('%Y-%m-%d'),
            'assignments': assignments_meta,
        }
        zip_file.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))

    zip_buffer.seek(0)
    filename = f"schoolnet_export_{target_date.strftime('%Y%m%d')}_{len(assignments)}_tasks.zip"
    response = HttpResponse(zip_buffer.read(), content_type='application/zip')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@teacher_required
def import_assignments_zip(request):
    """
    Імпорт завдань із ZIP-пакету з флешки (відновлює завдання, налаштування та бінарні файли).
    """
    import zipfile
    import json
    from django.core.files.base import ContentFile

    teacher = request.user.teacher_profile

    if request.method == 'POST':
        zip_file = request.FILES.get('archive')
        if not zip_file:
            messages.error(request, 'Будь ласка, оберіть ZIP-архів із завданнями для завантаження.')
            return redirect('teacher_dashboard')

        try:
            with zipfile.ZipFile(zip_file, 'r') as zf:
                if 'manifest.json' not in zf.namelist():
                    messages.error(request, 'Некоректний ZIP-архів: відсутній файл опису manifest.json.')
                    return redirect('teacher_dashboard')

                manifest_content = zf.read('manifest.json').decode('utf-8')
                manifest = json.loads(manifest_content)

                assignments_data = manifest.get('assignments', [])
                if not assignments_data:
                    messages.warning(request, 'В архіві не знайдено жодного завдання для імпорту.')
                    return redirect('teacher_dashboard')

                imported_count = 0
                imported_files_count = 0

                for a_data in assignments_data:
                    # 1. Предмет
                    subject = None
                    s_name = a_data.get('subject_name')
                    if s_name and str(s_name).strip():
                        subject, _ = Subject.objects.get_or_create(
                            name=str(s_name).strip(),
                            defaults={
                                'color': a_data.get('subject_color', '#2563eb'),
                                'icon': a_data.get('subject_icon', '📚')
                            }
                        )
                        teacher.subjects.add(subject)

                    # 2. Термін виконання
                    due_d = None
                    if a_data.get('due_date'):
                        try:
                            due_d = datetime.strptime(a_data['due_date'], '%Y-%m-%d').date()
                        except ValueError:
                            pass

                    # 3. Створюємо завдання
                    assignment = Assignment.objects.create(
                        teacher=teacher,
                        subject=subject,
                        title=a_data.get('title', 'Імпортоване завдання'),
                        description=a_data.get('description', ''),
                        is_individual=bool(a_data.get('is_individual', False)),
                        student_name=a_data.get('student_name', ''),
                        link_url=a_data.get('link_url', ''),
                        link_label=a_data.get('link_label', ''),
                        youtube_url=a_data.get('youtube_url', ''),
                        status=a_data.get('status', Assignment.STATUS_PUBLISHED),
                        due_date=due_d,
                        published_at=timezone.now(),
                        allow_student_ai_check=bool(a_data.get('allow_student_ai_check', False)),
                        allow_ai_usage=bool(a_data.get('allow_ai_usage', False)),
                    )

                    # 4. Прив'язка класів
                    for c_name in a_data.get('class_names', []):
                        if c_name and str(c_name).strip():
                            cg, _ = ClassGroup.objects.get_or_create(name=str(c_name).strip())
                            assignment.classes.add(cg)
                            teacher.classes.add(cg)

                    # 5. Додаткові посилання
                    for link_item in a_data.get('additional_links', []):
                        if link_item.get('url'):
                            AssignmentLink.objects.create(
                                assignment=assignment,
                                url=link_item['url'],
                                label=link_item.get('label', '')
                            )

                    # 6. YouTube відео
                    for y_item in a_data.get('youtube_links', []):
                        if y_item.get('url'):
                            AssignmentYouTubeLink.objects.create(
                                assignment=assignment,
                                url=y_item['url'],
                                title=y_item.get('title', '')
                            )

                    # 7. Файли завдання
                    for f_info in a_data.get('files', []):
                        zpath = f_info.get('zip_path')
                        orig_name = f_info.get('original_name', 'document')
                        if zpath and zpath in zf.namelist():
                            file_bytes = zf.read(zpath)
                            af = AssignmentFile(
                                assignment=assignment,
                                original_name=orig_name
                            )
                            af.file.save(orig_name, ContentFile(file_bytes), save=True)
                            imported_files_count += 1

                    # 8. Прив'язки до розкладу уроків
                    for st_item in a_data.get('schedule_targets', []):
                        c_name = st_item.get('class_name')
                        t_date_str = st_item.get('target_date')
                        l_num = st_item.get('lesson_number')

                        cg = ClassGroup.objects.filter(name=c_name).first() if c_name else None
                        t_date = None
                        if t_date_str:
                            try:
                                t_date = datetime.strptime(t_date_str, '%Y-%m-%d').date()
                            except ValueError:
                                pass

                        bell_slot = BellSchedule.objects.filter(lesson_number=l_num).first() if l_num else None

                        if cg or t_date:
                            AssignmentScheduleTarget.objects.update_or_create(
                                assignment=assignment,
                                class_group=cg,
                                defaults={
                                    'target_date': t_date,
                                    'target_day_of_week': (t_date.weekday() + 1) if t_date else None,
                                    'bell_slot': bell_slot,
                                }
                            )

                    imported_count += 1

                log_submission_activity(
                    request.user,
                    'assignment_imported',
                    f"Вчитель імпортував {imported_count} завдань та {imported_files_count} файлів з флешки"
                )

                messages.success(
                    request,
                    f"🎉 Успішно імпортовано з флешки {imported_count} завдань та {imported_files_count} навчальних файлів! Всі матеріали повністю готові до роботи."
                )

        except Exception as e:
            messages.error(request, f"Помилка під час обробки ZIP-архіву: {e}")

    return redirect('teacher_dashboard')






