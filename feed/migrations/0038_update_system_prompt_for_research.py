from django.db import migrations
import feed.models


def update_system_prompts_for_research(apps, schema_editor):
    AISettings = apps.get_model('feed', 'AISettings')
    for setting in AISettings.objects.all():
        if setting.system_prompt and 'Ти — висококваліфікований шкільний педагог-експерт' in setting.system_prompt:
            if 'ДОСЛІДНИЦЬКІ, ПОШУКОВІ ЗАВДАННЯ' not in setting.system_prompt:
                setting.system_prompt = feed.models.DEFAULT_NUS_SYSTEM_PROMPT
                setting.save(update_fields=['system_prompt'])


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0037_alter_aisettings_system_prompt'),
    ]

    operations = [
        migrations.RunPython(update_system_prompts_for_research, reverse_code=migrations.RunPython.noop),
    ]
