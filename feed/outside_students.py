"""Browser-held identities for pupils who explicitly submit outside a roster."""
import hashlib
from uuid import uuid4
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import render
from django.views.decorators.http import require_GET


def browser_identity(request, teacher_id, name):
    key = hashlib.sha256(f'{teacher_id}:{" ".join(name.split()).casefold()}'.encode()).hexdigest()
    identities = dict(request.session.get('outside_students', {}))
    if key not in identities:
        identities[key] = str(uuid4())
        identities = dict(list(identities.items())[-30:])
        request.session['outside_students'] = identities
    return identities[key]


def teaching_classes(queryset):
    # ClassGroup.save() derives the letter from the name, so old copies may
    # have letter='А' even when the journal originally sent 'ПК'.
    return queryset.exclude(name__endswith=' (Позакласні)')


def outside_predicate(user, target_teacher=None, class_id=None):
    from django.db.models import Q
    from .models import RosterState
    state = RosterState.objects.filter(pk=1).values_list('data', flat=True).first() or {}
    teacher_id = None
    if target_teacher and str(target_teacher).isdigit():
        teacher_id = int(target_teacher)
    if not teacher_id and target_teacher:
        from .models import Teacher
        teacher_id = Teacher.objects.filter(full_name__iexact=str(target_teacher).strip()).values_list('pk', flat=True).first()
    if not teacher_id:
        teacher_id = getattr(getattr(user, 'teacher_profile', None), 'pk', None)
    bindings = [item for key, item in state.get('journal_imports', {}).items()
                if (teacher_id and key.startswith(str(teacher_id) + ':')) or (user.is_superuser and not teacher_id)]
    has_roster_bindings = bool(bindings)
    if class_id and str(class_id).isdigit():
        cls_int = int(class_id)
        bindings = [item for item in bindings if str(item.get('class_id')) == str(cls_int)]
    known = {entry['native_id'] for item in bindings
             if not item.get('remote_class', {}).get('name', '').endswith(' (Позакласні)')
             for entry in item.get('students', {}).values() if entry.get('native_id') is not None}
    return Q(student__isnull=True) | ~Q(student_id__in=known) if has_roster_bindings else Q(student__isnull=True)


def outside_works(user):
    from .models import Submission
    rows = Submission.objects.all().select_related('assignment', 'class_group')
    if not user.is_superuser:
        rows = rows.filter(assignment__teacher__user=user)
    rows = rows.filter(outside_predicate(user))
    return rows.order_by('-submitted_at', '-pk')


@login_required
@require_GET
def overview(request):
    if not request.user.is_superuser and not hasattr(request.user, 'teacher_profile'):
        raise PermissionDenied
    rows = outside_works(request.user)
    from .models import ClassGroup
    classes = teaching_classes(ClassGroup.objects.filter(
        pk__in=rows.values('class_group_id')))
    search = request.GET.get('q', '').strip()
    if search:
        from django.db.models import Q
        for token in search.split():
            rows = rows.filter(Q(last_name__icontains=token) | Q(first_name__icontains=token))
    class_id = request.GET.get('class', '')
    if class_id.isdigit():
        rows = rows.filter(class_group_id=int(class_id))
    groups = {}
    for row in rows:
        key = f'student:{row.student_id}' if row.student_id else f'outside:{row.outside_student_id}' if row.outside_student_id else f'legacy:{row.pk}'
        group = groups.setdefault(key, {'name': row.get_student_full_name(), 'works': [], 'key': key})
        group['works'].append(row)
    return render(request, 'feed/outside_students.html', {
        'page_obj': Paginator(list(groups.values()), 25).get_page(request.GET.get('page')),
        'classes': classes, 'search': search, 'selected_class_id': class_id,
    })
