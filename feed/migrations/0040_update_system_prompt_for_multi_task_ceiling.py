from django.db import migrations, models
import feed.models


def update_system_prompts_for_multi_task_ceiling(apps, schema_editor):
    AISettings = apps.get_model('feed', 'AISettings')
    for setting in AISettings.objects.all():
        if setting.system_prompt and 'Ти — висококваліфікований шкільний педагог-експерт' in setting.system_prompt:
            if 'MULTI-TASK COMPLETION & STRICT CEILING' not in setting.system_prompt:
                setting.system_prompt = feed.models.DEFAULT_NUS_SYSTEM_PROMPT
                setting.save(update_fields=['system_prompt'])

    AICriteriaPreset = apps.get_model('feed', 'AICriteriaPreset')
    for preset in AICriteriaPreset.objects.all():
        if preset.is_default and preset.system_prompt and 'Ти — висококваліфікований шкільний педагог-експерт' in preset.system_prompt:
            if 'MULTI-TASK COMPLETION & STRICT CEILING' not in preset.system_prompt:
                preset.system_prompt = feed.models.DEFAULT_NUS_SYSTEM_PROMPT
                preset.save(update_fields=['system_prompt'])


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0039_update_system_prompt_for_irrelevant_and_templates'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aisettings',
            name='system_prompt',
            field=models.TextField(default=feed.models.DEFAULT_NUS_SYSTEM_PROMPT, verbose_name='Системний промт (Критерії НУШ)'),
        ),
        migrations.RunPython(update_system_prompts_for_multi_task_ceiling, reverse_code=migrations.RunPython.noop),
    ]
