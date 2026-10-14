from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('feed', '0058_assignment_teacher_ai_instructions')]
    operations = [migrations.AddField(model_name='submission', name='outside_student_id',
        field=models.UUIDField(null=True, blank=True, db_index=True))]
