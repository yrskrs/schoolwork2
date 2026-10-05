from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('feed', '0052_unified_ai_connections')]
    operations = [migrations.AddField(
        model_name='aijob', name='events', field=models.JSONField(default=list))]
