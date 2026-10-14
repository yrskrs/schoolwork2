from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('feed', '0059_submission_outside_student_id')]

    operations = [
        migrations.AlterField(
            model_name='submission', name='ai_score_level',
            field=models.TextField('Рівень оцінки ШІ', blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='submission', name='student_ai_level',
            field=models.TextField('Рівень чернової оцінки (від учня)', blank=True, default=''),
        ),
    ]
