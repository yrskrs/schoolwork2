from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('feed', '0048_aijob_one_active_ai_understanding_per_assignment')]
    operations = [migrations.AddField(
        model_name='assignment', name='class_ai_overrides',
        field=models.JSONField(blank=True, default=dict, verbose_name='Критерії ШІ для окремих класів',
                               help_text='Для кожного класу: шаблон та обрані групи результатів. Порожнє значення успадковує критерії завдання.'),
    )]
