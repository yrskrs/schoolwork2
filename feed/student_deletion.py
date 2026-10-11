"""Explicit deletions use stored pupil IDs and retain an independent audit."""
import logging
from django.core.exceptions import PermissionDenied
from django.db import transaction
from .models import Student, Submission, SubmissionFile, SubmissionActivityLog

logger = logging.getLogger(__name__)


def _remove_unreferenced_files(files):
    for storage, name in files:
        if (Submission.objects.filter(file=name).exists()
                or SubmissionFile.objects.filter(file=name).exists()):
            continue
        try:
            storage.delete(name)
        except OSError:
            logger.exception('Не вдалося видалити файл зданої роботи після видалення запису.')


def _delete_submissions(submissions, actor):
    files = {}
    for submission in submissions:
        for field in [submission.file, *(item.file for item in submission.files.all())]:
            if field and field.name:
                files[(id(field.storage), field.name)] = (field.storage, field.name)
        SubmissionActivityLog.objects.create(actor=actor, action_type='delete',
            description=f'Видалено здачу #{submission.pk}, учень #{submission.student_id or "—"}, '
                f'клас #{submission.class_group_id}, завдання #{submission.assignment_id or "—"}: '
                f'«{submission.get_student_full_name()}».', submission=submission)
        submission.delete()
    transaction.on_commit(lambda: _remove_unreferenced_files(list(files.values())))


@transaction.atomic
def delete_student(student_id, actor):
    # Same lock order as roster imports and LocalRosterMixin.save.
    from school_sync.django_backend import operate
    from .roster_models import RosterState
    state, _ = RosterState.objects.get_or_create(pk=1)
    RosterState.objects.select_for_update().get(pk=state.pk)
    student = Student.objects.select_for_update().get(pk=student_id)
    class_id, name, class_name = student.class_group_id, student.get_full_name(), student.class_group.name
    teacher = getattr(actor, 'teacher_profile', None)
    if not actor.is_superuser and (not teacher or not teacher.classes.filter(pk=class_id).exists()):
        raise PermissionDenied('Немає доступу до класу.')
    submissions = list(Submission.objects.select_for_update(of=('self',)).filter(student=student)
        .select_related('assignment').prefetch_related('files'))
    if not actor.is_superuser and any(
            (row.assignment.teacher_id if row.assignment else row.teacher_id) != teacher.pk
            for row in submissions):
        raise PermissionDenied('Учень має роботи іншого вчителя. Повне видалення доступне адміністратору.')
    _delete_submissions(submissions, actor)
    SubmissionActivityLog.objects.create(actor=actor, action_type='student_deleted',
        description=f'Повністю видалено учня #{student.pk} «{name}» з класу «{class_name}» '
            f'(#{class_id}) та його здачі: {len(submissions)}. Імена тезок не використовувалися для видалення.')
    student.delete()
    operate(lambda engine, store: engine.capture())
    return class_id, name, class_name, len(submissions)


@transaction.atomic
def delete_submission(submission_id, actor):
    row = Submission.objects.select_for_update(of=('self',)).select_related('assignment').get(pk=submission_id)
    teacher = getattr(actor, 'teacher_profile', None)
    if not actor.is_superuser and (not teacher or not (
            row.teacher_id == teacher.pk or (row.assignment and row.assignment.teacher_id == teacher.pk))):
        raise PermissionDenied('Немає доступу до роботи.')
    assignment_id, name = row.assignment_id, row.get_student_full_name()
    _delete_submissions([row], actor)
    return assignment_id, name
