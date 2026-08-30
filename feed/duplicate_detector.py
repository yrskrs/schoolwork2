"""
Модуль для перевірки дублікатів та плагіату при здачі учнівських робіт:
1. Порівняння файлу учня з вихідними матеріалами вчителя до завдання.
2. Порівняння файлу учня з раніше зданими роботами інших учнів до того самого завдання.
3. Хешування файлів (SHA-256) та нормалізація тексту/коду для виявлення однакового вмісту.
"""

import os
import hashlib
from .document_parsers import extract_text_from_document


def get_file_sha256(file_path):
    """Обчислює SHA-256 хеш двійкового вмісту файлу."""
    if not file_path or not os.path.exists(file_path):
        return None
    hasher = hashlib.sha256()
    try:
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return None


def get_normalized_file_content(file_path, original_filename=None):
    """
    Витягує та нормалізує текстовий вміст файлу для порівняння:
    - Прибирає зайві пробіли на кінцях рядків
    - Уніфікує перенесення рядків (CRLF -> LF)
    - Ігнорує порожні рядки
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    filename = original_filename or os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()

    # Спеціальна обробка Scratch 3 (.sb3)
    if ext == '.sb3':
        from .scratch_utils import parse_scratch_sb3
        _, text_summary, _ = parse_scratch_sb3(file_path)
        if text_summary:
            lines = [line.strip() for line in text_summary.splitlines() if line.strip()]
            return "\n".join(lines)

    # Спеціальна обробка BBC micro:bit (.hex)
    if ext == '.hex':
        from .microbit_utils import parse_microbit_hex
        _, text_summary, _ = parse_microbit_hex(file_path)
        if text_summary:
            lines = [line.strip() for line in text_summary.splitlines() if line.strip()]
            return "\n".join(lines)

    # Спробуємо розпарсити через універсальний екстрактор документів
    text, success, _ = extract_text_from_document(file_path, filename)
    if success and text:
        # Нормалізація тексту
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        return "\n".join(lines)

    # Якщо це файл з кодом або простий текст
    encs = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5']
    for enc in encs:
        try:
            with open(file_path, 'r', encoding=enc) as f:
                content = f.read()
                lines = [line.strip() for line in content.splitlines() if line.strip()]
                return "\n".join(lines)
        except Exception:
            continue

    return ""


def check_submission_duplicates(submission):
    """
    Перевіряє роботу учня на наявність дублікатів:
    1. Збіг з файлами вчителя до цього завдання.
    2. Збіг з файлами інших учнів, зданими раніше по цьому ж завданню.

    Повертає словник:
    {
        'is_duplicate': bool,
        'is_duplicate_teacher': bool,
        'teacher_file_name': str | None,
        'is_duplicate_student': bool,
        'duplicate_student_name': str | None,
        'duplicate_class': str | None,
        'duplicate_submitted_at': datetime | None,
        'type': 'teacher_duplicate' | 'student_duplicate' | 'none',
        'warning_message': str
    }
    """
    result = {
        'is_duplicate': False,
        'is_duplicate_teacher': False,
        'teacher_file_name': None,
        'is_duplicate_student': False,
        'duplicate_student_name': None,
        'duplicate_class': None,
        'duplicate_submitted_at': None,
        'type': 'none',
        'warning_message': ''
    }

    if not submission or not submission.file or not submission.assignment:
        return result

    file_path = submission.file.path if submission.file else None
    if not file_path or not os.path.exists(file_path):
        return result

    sub_hash = get_file_sha256(file_path)
    sub_norm_text = get_normalized_file_content(file_path, submission.file.name)
    sub_text_len = len(sub_norm_text)

    assignment = submission.assignment

    # ── 1. ПЕРЕВІРКА НА ЗБІГ З ФАЙЛАМИ ВЧИТЕЛЯ ─────────────────────────────────
    for af in assignment.files.all():
        if not af.file or not os.path.exists(af.file.path):
            continue

        teacher_file_path = af.file.path
        teacher_hash = get_file_sha256(teacher_file_path)
        teacher_name = af.original_name or os.path.basename(af.file.name)

        # Точний двійковий збіг
        if sub_hash and teacher_hash and sub_hash == teacher_hash:
            result.update({
                'is_duplicate': True,
                'is_duplicate_teacher': True,
                'teacher_file_name': teacher_name,
                'type': 'teacher_duplicate',
                'warning_message': (
                    f"⚠️ Увага: вміст прикріпленого файлу повністю збігається з файлом вчителя «{teacher_name}» до цього завдання. "
                    f"Схоже, що ви здали вихідний файл завдання замість виконаної роботи!"
                )
            })
            return result

        # Текстовий/нормалізований збіг (якщо текст не порожній і довший 20 символів)
        if sub_text_len >= 20:
            teacher_norm_text = get_normalized_file_content(teacher_file_path, af.file.name)
            if teacher_norm_text and sub_norm_text == teacher_norm_text:
                result.update({
                    'is_duplicate': True,
                    'is_duplicate_teacher': True,
                    'teacher_file_name': teacher_name,
                    'type': 'teacher_duplicate',
                    'warning_message': (
                        f"⚠️ Увага: текстовий вміст вашого файлу повністю збігається з вихідним файлом вчителя «{teacher_name}». "
                        f"Перевірте, чи ви не здали умову завдання без розв'язку!"
                    )
                })
                return result

    # ── 2. ПЕРЕВІРКА НА ЗБІГ З РОБОТАМИ ІНШИХ УЧНІВ ────────────────────────────
    other_submissions = assignment.submissions.filter(file__isnull=False)
    if submission.id:
        other_submissions = other_submissions.exclude(id=submission.id)

    # Перевіряємо в хронологічному порядку здачі
    for other in other_submissions.select_related('class_group').order_by('submitted_at'):
        if not other.file or not os.path.exists(other.file.path):
            continue

        other_file_path = other.file.path
        other_hash = get_file_sha256(other_file_path)
        other_name = other.get_student_full_name()
        other_class = str(other.class_group)

        # Якщо це робота того ж самого учня (повторна здача) — пропускаємо
        if other.first_name.strip().lower() == submission.first_name.strip().lower() and \
           other.last_name.strip().lower() == submission.last_name.strip().lower() and \
           other.class_group_id == submission.class_group_id:
            continue

        # Точний двійковий збіг з роботою іншого учня
        if sub_hash and other_hash and sub_hash == other_hash:
            result.update({
                'is_duplicate': True,
                'is_duplicate_student': True,
                'duplicate_submission_id': other.id,
                'duplicate_student_name': other_name,
                'duplicate_student_first_name': other.first_name,
                'duplicate_student_last_name': other.last_name,
                'duplicate_class': other_class,
                'duplicate_submitted_at': other.submitted_at,
                'type': 'student_duplicate',
                'warning_message': (
                    f"⚠️ Увага: вміст прикріпленого файлу повністю збігається з файлом, який раніше вже здав(ла) {other_name} ({other_class}). "
                    f"Система зафіксувала однаковий вміст файлу як підозру на дублікат або списування!"
                )
            })
            return result

        # Текстовий/нормалізований збіг коду чи тексту
        if sub_text_len >= 30:
            other_norm_text = get_normalized_file_content(other_file_path, other.file.name)
            if other_norm_text and sub_norm_text == other_norm_text:
                result.update({
                    'is_duplicate': True,
                    'is_duplicate_student': True,
                    'duplicate_submission_id': other.id,
                    'duplicate_student_name': other_name,
                    'duplicate_student_first_name': other.first_name,
                    'duplicate_student_last_name': other.last_name,
                    'duplicate_class': other_class,
                    'duplicate_submitted_at': other.submitted_at,
                    'type': 'student_duplicate',
                    'warning_message': (
                        f"⚠️ Увага: текстовий вміст вашої роботи на 100% збігається з роботою, яку здав(ла) {other_name} ({other_class}). "
                        f"Система зафіксувала однаковий вміст як підозру на дублікат або списування!"
                    )
                })
                return result

    return result
