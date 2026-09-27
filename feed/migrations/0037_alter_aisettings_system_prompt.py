from django.db import migrations, models
import feed.models


def update_default_system_prompts(apps, schema_editor):
    AISettings = apps.get_model('feed', 'AISettings')
    for setting in AISettings.objects.all():
        if setting.system_prompt and 'Ти — висококваліфікований шкільний педагог-експерт' in setting.system_prompt:
            if 'ПЕРЕВІРКА ТА ОЦІНЮВАННЯ ДІАГРАМ' not in setting.system_prompt:
                setting.system_prompt = feed.models.DEFAULT_NUS_SYSTEM_PROMPT
                setting.save(update_fields=['system_prompt'])


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0036_alter_aisettings_system_prompt'),
    ]

    operations = [
        migrations.AlterField(
            model_name='aisettings',
            name='system_prompt',
            field=models.TextField(default=feed.models.DEFAULT_NUS_SYSTEM_PROMPT, verbose_name='Системний промт (Критерії НУШ)'),
        ),
        migrations.RunPython(update_default_system_prompts, reverse_code=migrations.RunPython.noop),
    ]
