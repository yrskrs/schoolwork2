"""Portable criteria snapshots: class names survive an import into another database."""
import os
import hashlib

from django.core.files.base import ContentFile

from .models import AICriteriaPreset


def _document_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def export_ai_policy(assignment, archive):
    presets = {}

    def snapshot(preset):
        if not preset:
            return None
        key = str(preset.pk)
        if key not in presets:
            fields = ('name', 'description', 'evaluation_type', 'system_prompt', 'gr_definitions',
                      'document_name', 'extracted_criteria_text')
            presets[key] = {field: getattr(preset, field) for field in fields}
            if preset.document_file and os.path.exists(preset.document_file.path):
                path = f'criteria/{assignment.pk}/{preset.pk}/{os.path.basename(preset.document_file.name)}'
                archive.write(preset.document_file.path, path)
                presets[key]['document_zip_path'] = path
                presets[key]['document_sha256'] = _document_hash(preset.document_file.path)
        return key

    classes = {}
    for group in assignment.classes.all():
        if str(group.pk) in (assignment.class_ai_overrides or {}):
            preset, grs = assignment.get_ai_policy(group)
            classes[group.name] = {'preset': snapshot(preset), 'grs': grs}
    return {'presets': presets, 'default_preset': snapshot(assignment.default_ai_preset),
            'default_grs': assignment.get_default_gr_list(), 'classes': classes,
            'thinking_mode': assignment.ai_thinking_mode,
            'student_understanding': assignment.allow_student_ai_understanding}


def import_ai_policy(assignment, data, archive):
    if not isinstance(data, dict):
        return
    loaded = {}
    fields = ('name', 'description', 'evaluation_type', 'system_prompt', 'gr_definitions',
              'document_name', 'extracted_criteria_text')
    for key, snapshot in (data.get('presets') or {}).items():
        if not isinstance(snapshot, dict):
            continue
        values = {field: snapshot.get(field, '') for field in fields}
        # A matching snapshot can be reused. An existing teacher preset is never overwritten.
        document_path = snapshot.get('document_zip_path')
        expected_hash = snapshot.get('document_sha256')
        preset = None
        for candidate in AICriteriaPreset.objects.filter(**values):
            if document_path:
                if candidate.document_file and os.path.exists(candidate.document_file.path):
                    actual_hash = _document_hash(candidate.document_file.path)
                    if actual_hash == (expected_hash or hashlib.sha256(archive.read(document_path)).hexdigest()):
                        preset = candidate
                        break
            elif not candidate.document_file:
                preset = candidate
                break
        if preset is None:
            preset = AICriteriaPreset(**values)
            if document_path and document_path in archive.namelist():
                preset.document_file.save(os.path.basename(document_path), ContentFile(archive.read(document_path)), save=False)
            preset.save()
        loaded[str(key)] = preset
    assignment.default_ai_preset = loaded.get(str(data.get('default_preset')))
    import json
    assignment.default_ai_grs = json.dumps(data.get('default_grs') or [], ensure_ascii=False)
    overrides = {}
    for group in assignment.classes.all():
        choice = (data.get('classes') or {}).get(group.name, {})
        preset = loaded.get(str(choice.get('preset')))
        if preset:
            allowed = {gr['code'] for gr in preset.get_gr_list()}
            grs = [gr for gr in choice.get('grs') or [] if gr in allowed]
            overrides[str(group.pk)] = {'preset_id': preset.pk, 'grs': grs if preset.evaluation_type != 'traditional' else []}
    assignment.class_ai_overrides = overrides
    assignment.ai_thinking_mode = bool(data.get('thinking_mode'))
    assignment.allow_student_ai_understanding = bool(data.get('student_understanding'))
    assignment.save(update_fields=['default_ai_preset', 'default_ai_grs', 'class_ai_overrides',
                                   'ai_thinking_mode', 'allow_student_ai_understanding'])
