"""Normalize legacy factory AI prompts stored by older versions.

Historical factory prompts (migration 0043 era) do not carry the current rules
marker (``SCOPE OF WORK — 5 РІВНІВ ПРІОРИТЕТУ``) and were previously treated as
teacher-authored custom prompts. On an upgraded database this injected a huge
stale instruction into the system prompt and broke the compact/free budget.
The migration is idempotent: it only rewrites untouched legacy defaults and
leaves genuine teacher edits alone. Reverse is a no-op: restoring stale defaults
would re-introduce the bug.
"""

from django.db import migrations

LEGACY_SIGNATURES = (
    'Ти — висококваліфікований шкільний педагог-експерт',
    "Ти — суворий та об'єктивний шкільний педагог-експерт",
    'Ти — досвідчений шкільний педагог-експерт',
)
CURRENT_MARKER = 'SCOPE OF WORK — 5 РІВНІВ ПРІОРИТЕТУ'
ZERO_MARKER = 'НУЛЬОВА ТОЛЕРАНТНІСТЬ'


def _is_legacy_default(text):
    value = str(text or '')
    if not value.strip():
        return False
    if CURRENT_MARKER in value or ZERO_MARKER in value:
        return False
    return any(signature in value for signature in LEGACY_SIGNATURES)


def normalize_legacy_defaults(apps, schema_editor):
    from feed.models import (DEFAULT_NUS_SYSTEM_PROMPT, DEFAULT_NUS_GR_SYSTEM_PROMPT,
                             DEFAULT_TRADITIONAL_SYSTEM_PROMPT, ZERO_EVIDENCE_RULE)

    defaults = {
        'nus': DEFAULT_NUS_SYSTEM_PROMPT,
        'nus_gr': DEFAULT_NUS_GR_SYSTEM_PROMPT,
        'traditional': DEFAULT_TRADITIONAL_SYSTEM_PROMPT,
        'custom': DEFAULT_NUS_SYSTEM_PROMPT,
    }

    AISettings = apps.get_model('feed', 'AISettings')
    for settings in AISettings.objects.all().only('pk', 'system_prompt'):
        text = settings.system_prompt or ''
        if _is_legacy_default(text) and text.strip() != DEFAULT_NUS_SYSTEM_PROMPT.strip():
            AISettings.objects.filter(pk=settings.pk).update(
                system_prompt=DEFAULT_NUS_SYSTEM_PROMPT)

    AICriteriaPreset = apps.get_model('feed', 'AICriteriaPreset')
    for preset in AICriteriaPreset.objects.filter(is_system=True).only(
            'pk', 'system_prompt', 'evaluation_type'):
        text = preset.system_prompt or ''
        if not _is_legacy_default(text):
            continue
        base = defaults.get(str(preset.evaluation_type or '').strip().lower(),
                            DEFAULT_NUS_SYSTEM_PROMPT)
        AICriteriaPreset.objects.filter(pk=preset.pk).update(
            system_prompt=base + '\n\n' + ZERO_EVIDENCE_RULE)


class Migration(migrations.Migration):

    dependencies = [
        ('feed', '0081_submission_ai_technical_evidence'),
    ]

    operations = [
        migrations.RunPython(normalize_legacy_defaults, migrations.RunPython.noop),
    ]
