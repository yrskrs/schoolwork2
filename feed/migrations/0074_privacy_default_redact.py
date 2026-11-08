from django.db import migrations, models


def strict_to_redact(apps, schema_editor):
    """Move every stored strict value to redact.

    The default becomes redact for all rows; strict stays available only as a
    new explicit teacher choice made after this migration.
    """
    AISettings = apps.get_model('feed', 'AISettings')
    AISettings.objects.filter(privacy_mode='strict').update(privacy_mode='redact')


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0073_ai_parallel_privacy'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aisettings',
            name='privacy_mode',
            field=models.CharField(
                choices=[
                    ('redact', 'Очищення тексту; візуальні матеріали дозволені з EXIF-очищенням'),
                    ('strict', 'Суворий: блокувати лише за реально виявлені персональні дані'),
                ],
                default='redact',
                max_length=16,
                verbose_name='Знеособлення',
            ),
        ),
        migrations.RunPython(strict_to_redact, migrations.RunPython.noop),
    ]
