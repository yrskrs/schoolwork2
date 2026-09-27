from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0040_update_system_prompt_for_multi_task_ceiling'),
    ]

    operations = [
        migrations.AddField(
            model_name='assignment',
            name='ai_task_understanding',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Збережений результат попереднього аналізу завдання штучним інтелектом (виявлені завдання, вимоги, критерії)',
                verbose_name='Аналіз розуміння завдання ШІ (JSON)'
            ),
        ),
        migrations.AddField(
            model_name='assignment',
            name='ai_task_understanding_updated_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Час останнього аналізу розуміння ШІ'
            ),
        ),
    ]
