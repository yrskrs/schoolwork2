from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0077_backfill_authorship_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='submission',
            name='ai_authorship_teacher_label',
            field=models.CharField(
                choices=[
                    ('unknown', 'Не визначено'),
                    ('ai', 'Підтверджено використання ШІ'),
                    ('not_ai', 'Самостійна робота'),
                ],
                db_index=True,
                default='unknown',
                max_length=10,
                verbose_name='Мітка вчителя щодо походження',
            ),
        ),
        migrations.AddField(
            model_name='submission',
            name='ai_authorship_teacher_note',
            field=models.TextField(blank=True, default='', verbose_name='Коментар учителя до мітки походження'),
        ),
        migrations.AddField(
            model_name='submission',
            name='ai_authorship_labeled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Коли вчитель позначив походження'),
        ),
    ]
