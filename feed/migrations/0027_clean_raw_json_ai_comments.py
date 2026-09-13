from django.db import migrations
import re
import json


def clean_raw_json_records(apps, schema_editor):
    """
    Автоматично очищає збережені коментарі та відгуки ШІ, якщо через попередню версію
    в базу даних потрапив сирий JSON із фігурними дужками та технічними ключами.
    """
    SubmissionComment = apps.get_model('feed', 'SubmissionComment')
    Submission = apps.get_model('feed', 'Submission')

    def extract_clean_comment(text):
        if not text:
            return ""
        work_text = str(text).strip()
        prefix = ""
        tag = "🤖 [Рекомендації та відгук ШІ]:"
        if tag in work_text:
            prefix = tag + "\n"
            work_text = work_text.replace(tag, "").strip()

        if not (work_text.startswith('{') or '"feedback_comment"' in work_text or '"suggested_grade"' in work_text or '"summary"' in work_text):
            return text

        # 1. Спроба json.loads
        try:
            sanitized = re.sub(r"(?<!\\)\\'", "'", work_text)
            sanitized = re.sub(r',\s*([\]}])', r'\1', sanitized)
            data = json.loads(sanitized, strict=False)
            if isinstance(data, dict):
                fc = data.get('feedback_comment') or data.get('summary')
                if fc and str(fc).strip():
                    return prefix + str(fc).strip()
        except Exception:
            pass

        # 2. Regex пошук feedback_comment
        fc_m = re.search(r'"feedback_comment"\s*:\s*"((?:[^"\\]|\\.)*)"', work_text)
        if fc_m:
            try:
                fc_val = json.loads('"' + fc_m.group(1) + '"')
            except Exception:
                fc_val = fc_m.group(1).replace(r'\"', '"').replace(r'\n', '\n').replace(r"\'", "'")
            if fc_val and len(str(fc_val).strip()) > 3:
                return prefix + str(fc_val).strip()

        # 3. Regex пошук summary
        sum_m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', work_text)
        if sum_m:
            try:
                sum_val = json.loads('"' + sum_m.group(1) + '"')
            except Exception:
                sum_val = sum_m.group(1).replace(r'\"', '"').replace(r'\n', '\n').replace(r"\'", "'")
            if sum_val and len(str(sum_val).strip()) > 3:
                return prefix + str(sum_val).strip()

        # 4. Очищення від JSON синтаксису
        cleaned = re.sub(r'[{}\[\]"]', '', work_text)
        cleaned = re.sub(r'(?:suggested_grade|format_warning|feedback_comment|status|strengths|weaknesses|gr_results|level|summary|ai_generated_\w+)\s*:', '', cleaned)
        lines = [l.strip() for l in cleaned.split('\n') if l.strip()]
        return prefix + '\n'.join(lines)

    # Очищаємо всі коментарі SubmissionComment з сирим JSON
    for comment in SubmissionComment.objects.all():
        if comment.text and (str(comment.text).strip().startswith('{') or '"feedback_comment"' in comment.text or '"suggested_grade"' in comment.text):
            cleaned_text = extract_clean_comment(comment.text)
            if cleaned_text != comment.text:
                comment.text = cleaned_text
                comment.save(update_fields=['text'])

    # Очищаємо всі student_ai_feedback та ai_feedback у Submission
    for sub in Submission.objects.all():
        changed_fields = []
        if sub.student_ai_feedback and (str(sub.student_ai_feedback).strip().startswith('{') or '"feedback_comment"' in sub.student_ai_feedback):
            sub.student_ai_feedback = extract_clean_comment(sub.student_ai_feedback)
            changed_fields.append('student_ai_feedback')
        if sub.ai_feedback and (str(sub.ai_feedback).strip().startswith('{') or '"feedback_comment"' in sub.ai_feedback or '"suggested_grade"' in sub.ai_feedback):
            sub.ai_feedback = extract_clean_comment(sub.ai_feedback)
            changed_fields.append('ai_feedback')
        if changed_fields:
            sub.save(update_fields=changed_fields)


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0026_alter_aisettings_system_prompt_and_more'),
    ]

    operations = [
        migrations.RunPython(clean_raw_json_records, reverse_code=migrations.RunPython.noop),
    ]
