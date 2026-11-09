from django.db import migrations


def backfill(apps, schema_editor):
    Submission = apps.get_model('feed', 'Submission')
    Submission.objects.filter(ai_generated_detected=True).update(ai_authorship_state='suspected')


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0076_ai_authorship_state'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
