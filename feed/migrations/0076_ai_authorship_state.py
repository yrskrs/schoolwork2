from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0075_loginthrottle'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='ai_authorship_state',
            field=models.CharField(
                choices=[
                    ('none', 'Ознак не знайдено'),
                    ('suspected', 'Є ознаки використання ШІ'),
                    ('insufficient', 'Недостатньо даних'),
                ],
                db_index=True,
                default='insufficient',
                max_length=20,
                verbose_name='Стан походження роботи',
            ),
        ),
        migrations.AddField(
            model_name='submission',
            name='ai_provenance_report',
            field=models.JSONField(
                blank=True,
                default=dict,
                verbose_name='Структурований звіт походження',
            ),
        ),
    ]
