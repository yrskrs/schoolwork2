"""Object permissions shared by grading, file previews and AI endpoints."""
from django.db.models import Q
from django.http import Http404


def teacher_submissions(request, queryset):
    if request.user.is_superuser:
        return queryset
    teacher = getattr(request.user, 'teacher_profile', None)
    if teacher is None:
        return queryset.none()
    return queryset.filter(Q(assignment__teacher=teacher) | Q(teacher=teacher))


def can_manage_submission(request, submission):
    return teacher_submissions(request, type(submission).objects.filter(pk=submission.pk)).exists()


def can_view_assignment(request, assignment):
    if assignment.status in ('published', 'archived'):
        return True
    return request.user.is_authenticated and (
        request.user.is_superuser
        or assignment.teacher_id == getattr(getattr(request.user, 'teacher_profile', None), 'pk', None)
    )


def require_assignment_access(request, assignment):
    if not can_view_assignment(request, assignment):
        raise Http404('Завдання ще не опубліковане')
