# Generated on 2026-11-15

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0082_normalize_legacy_ai_defaults'),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslink',
            name='restrict_access',
            field=models.BooleanField(default=True, verbose_name='Обмежувати доступ лише вибраним (клас/учень/завдання)'),
        ),
    ]
