"""Request-scoped policy shared by feeds, pupil lists, submissions and downloads."""
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings
from django.core import signing
from django.db.models import Q
from .models import Assignment, AssignmentFile, Submission, ClassGroup, Teacher

COOKIE = 'schoolnet_public_access'
SALT = 'schoolnet.public-access.v1'


def is_teacher(request):
    return request.user.is_authenticated and (request.user.is_superuser or hasattr(request.user, 'teacher_profile'))


def is_external(request, connection):
    """Forwarded headers can only make a request MORE restricted, never grant LAN access."""
    host = request.get_host().split(':')[0].lower().rstrip('.')
    if ('HTTP_TAILSCALE_FUNNEL_REQUEST' in request.META or host.endswith('.ts.net')
            or (connection.public_url and host == urlsplit(connection.public_url).hostname)):
        return True
    if not connection.enabled:
        return False
    if any(key in request.META for key in ('HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST',
            'HTTP_X_FORWARDED_PROTO', 'HTTP_FORWARDED')):
        return True
    try:
        address = ipaddress.ip_address(request.META.get('REMOTE_ADDR', ''))
        # is_private also includes reserved/documentation blocks; only real LAN ranges are exempt.
        local = any(address in ipaddress.ip_network(net) for net in
                    (('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16') if address.version == 4
                     else ('::1/128', 'fc00::/7', 'fe80::/10')))
        return not local
    except ValueError:
        return True


def grant_from_cookie(request):
    try:
        grant = signing.loads(request.COOKIES.get(COOKIE, ''), salt=SALT, max_age=60 * 60 * 24 * 365)
        if not isinstance(grant, dict) or not isinstance(grant.get('link'), int):
            return None
        return grant['link']
    except (signing.BadSignature, ValueError, TypeError):
        return None


def link_for(request):
    return getattr(request, 'access_link', None)


def scope_assignments(request, queryset):
    link = link_for(request)
    if not link or link.kind == 'site':
        return queryset
    if link.kind == 'assignment':
        return queryset.filter(pk=link.assignment_id)
    queryset = queryset.filter(classes__pk=link.restricted_class_id).distinct()
    if link.kind == 'student':
        # Individual tasks use names in the existing schema; do not show another pupil's task.
        queryset = queryset.filter(Q(is_individual=False) | Q(student_name__iexact=link.student.get_full_name())
            | Q(student_name__iexact=f'{link.student.last_name} {link.student.first_name}'))
    return queryset


def scope_classes(request, queryset):
    link = link_for(request)
    if not link or link.kind == 'site':
        return queryset
    if link.kind == 'assignment':
        assigned = link.assignment.classes.all()
        return queryset.filter(pk__in=assigned.values('pk')) if assigned.exists() else queryset
    return queryset.filter(pk=link.restricted_class_id)


def scope_students(request, queryset):
    link = link_for(request)
    if link and link.kind == 'student':
        return queryset.filter(pk=link.student_id)
    return queryset.filter(class_group__in=scope_classes(request, ClassGroup.objects.all()))


def scope_submissions(request, queryset):
    link = link_for(request)
    if not link or link.kind == 'site':
        return queryset
    queryset = queryset.filter(assignment__in=scope_assignments(request, Assignment.objects.all()))
    if link.kind == 'student':
        return queryset.filter(student_id=link.student_id)
    if link.kind == 'class':
        return queryset.filter(class_group_id=link.class_group_id)
    return queryset


def media_allowed(request, raw_path):
    """Unknown private uploads are never exposed through the public tunnel."""
    root = Path(settings.MEDIA_ROOT).resolve()
    target = (root / raw_path).resolve()
    if not target.is_relative_to(root):
        return False
    path = target.relative_to(root).as_posix()
    if Teacher.objects.filter(avatar_image=path).exists():
        return True
    material = AssignmentFile.objects.filter(file=path).first()
    preview = re.fullmatch(r'previews/(\d+)(?:\.pdf|/(?:presentation\.pdf|slide[-_]?[0-9]+\.(?:png|jpg)))', path)
    if not material and preview:
        material = AssignmentFile.objects.filter(pk=preview[1]).first()
    if material:
        assignment = material.assignment
        return assignment.status in ('published', 'archived') and scope_assignments(request, Assignment.objects.filter(pk=assignment.pk)).exists()
    submissions = Submission.objects.filter(
        Q(file=path) | Q(files__file=path), assignment__status__in=['published', 'archived'])
    # Group submissions can reference the same stored file; any permitted owner is enough.
    return scope_submissions(request, submissions).exists()


def bind_submission_form(request, form):
    """Enforce scope in Django validation, including tampered hidden inputs."""
    link = link_for(request)
    if not link:
        return form
    form.fields['class_group'].queryset = scope_classes(request, form.fields['class_group'].queryset)
    class_id = link.restricted_class_id
    if class_id:
        form.initial['class_group'] = class_id
        form.fields['class_group'].disabled = True
    if link.kind == 'student':
        form.public_personal_access = True
        form.initial.update(student_id=link.student_id, full_name=link.student.get_full_name(),
                            other_student=False, coauthor_ids='[]')
        for key in ('student_id', 'full_name', 'other_student', 'coauthor_ids'):
            form.fields[key].disabled = True
    return form


def _duplicate_warning(info):
    kind = (info or {}).get('type') or ''
    if kind == 'teacher_duplicate':
        return ('⚠️ Увага: ваша робота повністю збігається з матеріалами вчителя до цього '
                'завдання. Перевірте, чи ви не здали вихідний файл завдання замість розв\'язку.')
    if kind == 'student_duplicate':
        return ('⚠️ Виявлено збіг із раніше зданою роботою. Це підозра, а не доведений факт: '
                'перевірте свою відповідь або зверніться до вчителя.')
    if kind == 'ignored_collaboration':
        return '👥 Спільна/колективна робота: збіг із роботою співавтора. Плагіат виключено.'
    if kind == 'ignored_plagiarism':
        return 'ℹ️ Збіг із раніше зданою роботою проігноровано вчителем.'
    return ''


def _public_duplicate_info(info):
    """Duplicate facts without another pupil's name, class, time or submission id."""
    info = info if isinstance(info, dict) else {}
    public_matches = []
    for match in info.get('matches') or []:
        if not isinstance(match, dict):
            continue
        role = match.get('role') or ('teacher' if 'teacher' in str(match.get('type')) else 'student')
        public_matches.append({
            'type': str(match.get('type') or ''),
            'role': role,
            'reason': str(match.get('reason') or ''),
            'file': str(match.get('file') or ''),
            'other': ('матеріали вчителя' if role != 'student' else 'інший учень'),
        })
    return {
        'is_duplicate': bool(info.get('is_duplicate')),
        'is_duplicate_teacher': bool(info.get('is_duplicate_teacher')),
        'is_duplicate_student': bool(info.get('is_duplicate_student')),
        'plagiarism_ignored': bool(info.get('plagiarism_ignored')),
        'is_coauthor': bool(info.get('is_coauthor')),
        'type': str(info.get('type') or 'none'),
        'duplicate_file_name': str(info.get('duplicate_file_name') or ''),
        'matches': public_matches,
        'warning_message': _duplicate_warning(info),
    }


def scope_duplicate_info(request, info, role=None):
    """Duplicate metadata at the viewer boundary.

    Teachers keep full details (another pupil's name/class/time are needed for
    a plagiarism conversation). Pupils and public pages only learn that a
    duplicate was detected; identifiers of the other work are replaced with
    generic wording. ``role`` may be passed explicitly by callers that already
    know the audience; otherwise the request determines it.
    """
    if not info:
        return info
    if role in ('teacher', 'student', 'public'):
        teacher_view = role == 'teacher'
    else:
        teacher_view = request is not None and is_teacher(request)
    if teacher_view:
        return info
    return _public_duplicate_info(info)
