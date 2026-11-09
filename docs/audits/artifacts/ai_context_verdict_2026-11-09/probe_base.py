"""Read-only measurement of what the assessment prompt actually contains.

Creates clearly-labelled synthetic objects in the disposable lab database,
builds the real prompt twice (full and compact) and prints measured facts.
"""
import io
import json

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image, ImageDraw

from feed.models import (AICriteriaPreset, Assignment, AssignmentFile, ClassGroup,
                         Submission, SubmissionFile, Teacher, AISettings)
from feed.ai_context import build_assessment_request, teacher_materials
from feed.gemini_service import extract_submission_content, resolve_assignment_scope

User = get_user_model()


def png_bytes(w, h, text=''):
    image = Image.new('RGB', (w, h), 'white')
    draw = ImageDraw.Draw(image)
    if text:
        draw.text((40, 40), text, fill='black')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def docx_bytes(paragraphs, image=None):
    import docx
    document = docx.Document()
    for text in paragraphs:
        document.add_paragraph(text)
    if image:
        document.add_picture(io.BytesIO(image), width=docx.shared.Cm(12))
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


user, _ = User.objects.get_or_create(username='labprobe')
teacher, _ = Teacher.objects.get_or_create(user=user, defaults={'full_name': 'Лабораторний Вчитель'})
group, _ = ClassGroup.objects.get_or_create(name='8-П', defaults={'grade': 8})
preset, _ = AICriteriaPreset.objects.get_or_create(
    name='LABPROBE preset', defaults={
        'evaluation_type': 'nus_gr',
        'system_prompt': 'Оцінюй за критеріями НУШ.',
        'gr_definitions': json.dumps([{'code': 'ГР 1', 'name': 'Зміст'}]),
    })

Assignment.objects.filter(title='LABPROBE assignment').delete()
assignment = Assignment.objects.create(
    teacher=teacher, title='LABPROBE assignment',
    description='Опрацюйте презентацію. Виконайте лише завдання 2.',
    status='published', default_ai_preset=preset, default_ai_grs=json.dumps(['ГР 1']))
assignment.classes.add(group)

# Teacher reference file: long theory, only marker fragments matter.
theory = [f'MARK_THEORY_{i:02d} ' + ('Теоретичний абзац про тему. ' * 20) for i in range(12)]
AssignmentFile.objects.create(
    assignment=assignment, original_name='theory.docx',
    file=SimpleUploadedFile('theory.docx', docx_bytes(theory)))

# Primary task file with three exercises, one of them assigned.
lesson = [
    'MARK_LESSON_HEADER Практична робота',
    'Завдання 1. MARK_TASK1 Побудуйте таблицю.',
    'Завдання 2. MARK_TASK2 Поясніть поняття та наведіть приклад.',
    'Завдання 3. MARK_TASK3 Зробіть висновок.',
]
AssignmentFile.objects.create(
    assignment=assignment, original_name='lesson.docx',
    file=SimpleUploadedFile('lesson.docx', docx_bytes(lesson, png_bytes(900, 500, 'MARK_TASK_IMAGE'))),
    is_task_source_for_ai=True)

Submission.objects.filter(last_name='LABPROBE').delete()
student_paragraphs = [f'MARK_STUDENT_{i:02d} ' + ('Відповідь учня до завдання. ' * 8) for i in range(40)]
student_paragraphs.append('MARK_STUDENT_TAIL останній абзац роботи учня.')
submission = Submission.objects.create(
    assignment=assignment, teacher=teacher, class_group=group,
    first_name='Учень', last_name='LABPROBE',
    comment_student='MARK_COMMENT виконував завдання 2, висновок у коментарі.',
    file=SimpleUploadedFile('work.docx', docx_bytes(student_paragraphs, png_bytes(800, 600, 'MARK_STUDENT_IMAGE'))))
SubmissionFile.objects.create(
    submission=submission, original_name='photo.jpg',
    file=SimpleUploadedFile('photo.jpg', png_bytes(2400, 3200, 'MARK_PHOTO')))

settings_obj = AISettings.get_solo()
settings_obj.privacy_mode = 'redact'
settings_obj.save(update_fields=['privacy_mode'])

text_parts, inline_media, extract_error = extract_submission_content(submission)
primary, reference, teacher_media, coverage = teacher_materials(assignment)

scope = resolve_assignment_scope(
    assignment_title=assignment.title, assignment_desc=assignment.description,
    custom_criteria='', primary_task_content=primary, teacher_files_content=primary + reference)

gr = [{'code': 'ГР 1', 'name': 'Зміст'}]


def measure(label, prompt, system):
    return {
        'variant': label,
        'prompt_chars': len(prompt),
        'system_chars': len(system),
        'total_chars': len(prompt) + len(system),
        'theory_markers': prompt.count('MARK_THEORY_'),
        'lesson_markers': prompt.count('MARK_LESSON'),
        'task1': 'MARK_TASK1' in prompt, 'task2': 'MARK_TASK2' in prompt, 'task3': 'MARK_TASK3' in prompt,
        'student_markers': sum(prompt.count(f'MARK_STUDENT_{i:02d}') for i in range(40)),
        'student_tail': 'MARK_STUDENT_TAIL' in prompt,
        'comment': 'MARK_COMMENT' in prompt,
        'photo_source': 'photo.jpg' in prompt or 'photo' in prompt.lower(),
        'final_task_understanding': 'FINAL TASK UNDERSTANDING' in prompt,
        'scope_of_work': 'SCOPE OF WORK' in prompt,
        'teacher_hidden_confidential': 'КОНФІДЕНЦІЙНО' in prompt,
        'assessment_rules': 'ПРАВИЛА ДОКАЗОВОГО ОЦІНЮВАННЯ' in system or 'ПРАВИЛА ДОКАЗОВОГО ОЦІНЮВАННЯ' in prompt,
        'compact_rules': 'COMPACT' in system or len(system) < 3000,
    }


prompt_full, system_full = build_assessment_request(
    submission, preset, gr, scope, text_parts, primary, reference, coverage,
    ai_settings=settings_obj)
prompt_compact, system_compact = build_assessment_request(
    submission, preset, gr, scope, text_parts, primary, reference, coverage,
    ai_settings=settings_obj, compact=True)

result = {
    'student_text_chars': sum(len(t) for t in text_parts),
    'student_media_items': len(inline_media),
    'student_media_sources': [str(m.get('source'))[:60] for m in inline_media],
    'teacher_primary_chars': sum(len(t) for t in primary),
    'teacher_reference_chars': sum(len(t) for t in reference),
    'theory_raw_chars': sum(len(t) for t in theory),
    'scope_task_nums': scope.get('teacher_specific_task_nums'),
    'scope_assigned_count': scope.get('assigned_task_count'),
    'scope_questions': scope.get('questions_expected'),
    'coverage': coverage,
    'full': measure('full', prompt_full, system_full),
    'compact': measure('compact', prompt_compact, system_compact),
}
print('PROBE_RESULT ' + json.dumps(result, ensure_ascii=False, indent=1))

# ── extra measurements: dead prompt blocks, media payload, guards, PDF case ──
dead_markers = {
    'anti_empty_block': 'ANTI-EMPTY & IRRELEVANT SUBMISSION CHECK',
    'multi_task_ceiling': 'РОЗДІЛЬНИЙ АНАЛІЗ КОЖНОГО ЗАВДАННЯ',
    'teacher_materials_as_source': 'ПРЕЗЕНТАЦІЇ, PDF, ЗОБРАЖЕННЯ ТА МАТЕРІАЛИ ВЧИТЕЛЯ ЯК ДЖЕРЕЛО',
    'unassigned_material_block': 'Що НЕ є завданням (unassigned_material',
    'final_understanding_block': 'FINAL TASK UNDERSTANDING',
}
result['dead_blocks_in_payload'] = {k: (v in prompt_full or v in system_full) for k, v in dead_markers.items()}

assignment.teacher_ai_instructions = 'MARK_HIDDEN_CONF не знижуй бал за оформлення'
assignment.save(update_fields=['teacher_ai_instructions'])
prompt_hidden, _ = build_assessment_request(
    submission, preset, gr, scope, text_parts, primary, reference, coverage, ai_settings=settings_obj)
result['hidden_instruction_present'] = 'MARK_HIDDEN_CONF' in prompt_hidden

from PIL import Image as _Image
media_detail = []
for item in inline_media:
    raw = __import__('base64').b64decode(item['data'])
    try:
        with _Image.open(io.BytesIO(raw)) as im:
            size = im.size
    except Exception:
        size = None
    media_detail.append({'mime': item['mime_type'], 'kb': round(len(raw) / 1024, 1), 'size': size,
                         'source': str(item.get('source'))[:50]})
result['media_detail'] = media_detail

from feed.ai_payload import media_guard_error
result['guards'] = {
    'groq/qwen': media_guard_error(inline_media, 'groq', 'qwen/qwen3.8-27b'),
    'groq/gpt-oss': media_guard_error(inline_media, 'groq', 'openai/gpt-oss-120b'),
    'gemini/flash': media_guard_error(inline_media, 'gemini', 'gemini-3.6-flash'),
}

# 8-page image-only PDF submission (photos of a notebook)
pages = [png_bytes(1200, 1600, f'PAGE {i}') for i in range(1, 9)]
pdf_buffer = io.BytesIO()
_images = [_Image.open(io.BytesIO(p)).convert('RGB') for p in pages]
_images[0].save(pdf_buffer, format='PDF', save_all=True, append_images=_images[1:], resolution=150)
pdf_sub = Submission.objects.create(
    assignment=assignment, teacher=teacher, class_group=group,
    first_name='Учень', last_name='LABPROBE-PDF',
    file=SimpleUploadedFile('scan.pdf', pdf_buffer.getvalue()))
pdf_text, pdf_media, pdf_err = extract_submission_content(pdf_sub)
result['pdf_case'] = {
    'text_chars': sum(len(t) for t in pdf_text),
    'media': len(pdf_media), 'mimes': [m['mime_type'] for m in pdf_media],
    'guard_qwen': media_guard_error(pdf_media, 'groq', 'qwen/qwen3.8-27b'),
    'guard_gemini': media_guard_error(pdf_media, 'gemini', 'gemini-3.6-flash'),
}
print('PROBE_EXTRA ' + json.dumps({k: result[k] for k in (
    'dead_blocks_in_payload', 'hidden_instruction_present', 'media_detail', 'guards', 'pdf_case')},
    ensure_ascii=False, indent=1))
