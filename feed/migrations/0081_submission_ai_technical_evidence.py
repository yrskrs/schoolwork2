# Generated for the deterministic technical audit of submission files (Рівень 3).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0080_ai_generation_observability'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='ai_technical_evidence',
            field=models.JSONField(
                blank=True, default=dict,
                verbose_name='Технічний аудит файлів (детерміновані факти)'),
        ),
    ]
