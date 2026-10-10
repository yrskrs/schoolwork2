from django.core.exceptions import ValidationError
from .models import ClassGroup, Student, RosterReplica, RosterState
from school_sync.core import SyncError


def can_access(user):
    return user.is_authenticated and (user.is_superuser or hasattr(user, 'teacher_profile'))


def allowed_classes(user):
    groups = ClassGroup.objects.all() if user.is_superuser else user.teacher_profile.classes.all()
    return list(groups.values('id', 'name'))


class Native:
    def students(self, class_id):
        return list(Student.objects.filter(class_group_id=class_id).values_list('pk', flat=True))

    def read(self, kind, native_id):
        if kind == 'class':
            row = ClassGroup.objects.filter(pk=native_id).first()
            return None if row is None else {'name': row.name, 'grade_level': row.grade, 'letter': row.letter, 'active': row.integration_active}
        row = Student.objects.filter(pk=native_id).first()
        return None if row is None else {'name': row.get_full_name(), 'first_name': row.first_name,
            'last_name': row.last_name, 'middle_name': row.middle_name, 'native_class_id': row.class_group_id, 'active': row.integration_active}

    def collision(self, kind, payload):
        if kind == 'class':
            return ClassGroup.objects.filter(name=payload['name']).exists()
        return any(row.get_full_name().casefold() == payload['name'].casefold()
            for row in Student.objects.filter(class_group_id=payload['native_class_id']))

    def write(self, kind, native_id, payload):
        if kind == 'class':
            row = ClassGroup.objects.get(pk=native_id)
            row.name, row.grade, row.letter = payload['name'], payload.get('grade_level', row.grade), payload.get('letter', row.letter)
            row.integration_active = payload['active']
        else:
            row = Student.objects.get(pk=native_id)
            row.first_name, row.last_name, row.middle_name = payload.get('first_name', ''), payload.get('last_name', ''), payload.get('middle_name', '')
            row.class_group_id, row.integration_active = payload['native_class_id'], payload['active']
        row.full_clean(validate_unique=True)
        # Do not call name-based sync_submissions: stored FKs and history remain.
        row.save()

    def create(self, kind, payload):
        if kind == 'class':
            row = ClassGroup(name=payload['name'], grade=payload.get('grade_level', 1), letter=payload.get('letter', ''), integration_active=payload['active'])
        else:
            row = Student(first_name=payload.get('first_name', ''), last_name=payload.get('last_name', ''),
                middle_name=payload.get('middle_name', ''), class_group_id=payload['native_class_id'], integration_active=payload['active'])
        row.full_clean()
        row.save()
        return row.pk
