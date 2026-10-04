from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('feed', '0049_assignment_class_ai_overrides')]
    operations = [migrations.AlterField(
        model_name='submission', name='ai_generated_percent',
        field=models.IntegerField(blank=True, default=None, null=True,
                                  verbose_name='Історична оцінка частки ШІ (не доказ авторства)'))]
