"""Repair only explicitly linked copies; preserve original values in audit."""
from django.db import migrations


def repair_coauthors(apps, schema_editor):
    Submission = apps.get_model('feed', 'Submission')
    Log = apps.get_model('feed', 'SubmissionActivityLog')
    alias = schema_editor.connection.alias
    for copy in Submission.objects.using(alias).filter(
        is_group_work=True, primary_submission__isnull=False,
    ).select_related('primary_submission').iterator():
        root = copy.primary_submission
        # Same work and same physical file/link are evidence of a copied result.
        same_content = (bool(copy.file) and copy.file.name == root.file.name) or (
            bool(copy.link) and copy.link == root.link)
        if (not same_content or copy.assignment_id != root.assignment_id
                or copy.class_group_id != root.class_group_id
                or copy.submitted_at <= root.submitted_at
                or Log.objects.using(alias).filter(submission_id=copy.pk, action_type='submission').exists()):
            continue
        original = copy.submitted_at.isoformat()
        Submission.objects.using(alias).filter(pk=copy.pk).update(submitted_at=root.submitted_at)
        Log.objects.using(alias).create(
            submission_id=copy.pk, action_type='group_authors',
            description=f'Час здачі співавтора узгоджено з основною роботою #{root.pk}. '
                        f'Попередній час створення запису: {original}; '
                        f'час первинної здачі: {root.submitted_at.isoformat()}.',
        )


class Migration(migrations.Migration):
    dependencies = [('feed', '0069_hash_journal_api_keys')]
    operations = [migrations.RunPython(repair_coauthors, migrations.RunPython.noop)]
