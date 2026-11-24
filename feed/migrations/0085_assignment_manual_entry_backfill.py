"""Enable manual student entry for assignments created before 4.14.0.

4.14.0 added the per-assignment switch ``allow_manual_student_entry`` with
``default=False``. Assignments that already existed kept that default, so the
«✏️ Ввести вручну» button and the ``other_student`` checkbox silently
disappeared for every old assignment after the upgrade: a pupil missing from
the synced class roster could no longer submit work at all.

The teacher-facing decision stays per assignment: new assignments keep the
opt-in default ``False`` and the teacher enables the switch in the assignment
form. This data migration restores the pre-4.14.0 behaviour only for rows that
already existed when the field was introduced. Reverse is a no-op because the
flag records a teacher-owned preference and reverting it would hide the button
again.
"""

from django.db import migrations


def enable_manual_entry_for_existing(apps, schema_editor):
    Assignment = apps.get_model('feed', 'Assignment')
    Assignment.objects.update(allow_manual_student_entry=True)


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0084_assignment_allow_manual_student_entry'),
    ]

    operations = [
        migrations.RunPython(enable_manual_entry_for_existing, migrations.RunPython.noop),
    ]
