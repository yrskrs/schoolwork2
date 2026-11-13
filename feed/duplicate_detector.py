"""
Модуль для перевірки дублікатів та плагіату при здачі учнівських робіт:
1. Порівняння файлу учня з вихідними матеріалами вчителя до завдання.
2. Порівняння файлу учня з раніше зданими роботами інших учнів до того самого завдання.
3. Хешування файлів (SHA-256) та нормалізація тексту/коду для виявлення однакового вмісту.
Оптимізовано з кешуванням хешів та нормалізованого тексту для миттєвої перевірки без затримок.
"""

import os
import hashlib
from .document_parsers import extract_text_from_document

# In-memory кеші для запобігання повторному важкому парсингу файлів
_SHA256_CACHE = {}
_NORM_TEXT_CACHE = {}
_MAX_CACHE_SIZE = 5000


def get_file_sha256(file_path):
    """Обчислює SHA-256 хеш двійкового вмісту файлу з кешуванням за шляхом, mtime та розміром."""
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        from .ai_context import file_cache_key, evidence_cache
        shared_key = file_cache_key(file_path, 'sha256')
        cached = evidence_cache().get(shared_key)
        if cached is not None:
            return cached
        stat = os.stat(file_path)
        cache_key = shared_key
        if cache_key in _SHA256_CACHE:
            return _SHA256_CACHE[cache_key]

        hasher = hashlib.sha256()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        h = hasher.hexdigest()

        if len(_SHA256_CACHE) >= _MAX_CACHE_SIZE:
            _SHA256_CACHE.clear()
        _SHA256_CACHE[cache_key] = h
        evidence_cache().set(shared_key, h, 604800)
        return h
    except Exception:
        return None


def get_normalized_file_content(file_path, original_filename=None):
    """
    Витягує та нормалізує текстовий вміст файлу для порівняння з кешуванням:
    - Прибирає зайві пробіли на кінцях рядків
    - Уніфікує перенесення рядків (CRLF -> LF)
    - Ігнорує порожні рядки
    """
    if not file_path or not os.path.exists(file_path):
        return ""

    shared_key = None
    try:
        from .ai_context import file_cache_key, evidence_cache
        shared_key = file_cache_key(file_path, 'normalised-text', str(original_filename))
        cached = evidence_cache().get(shared_key)
        if cached is not None:
            return cached
        stat = os.stat(file_path)
        cache_key = shared_key
        if cache_key in _NORM_TEXT_CACHE:
            return _NORM_TEXT_CACHE[cache_key]
    except Exception:
        cache_key = None

    filename = original_filename or os.path.basename(file_path)
    ext = os.path.splitext(filename)[1].lower()

    res = ""
    # Спеціальна обробка Scratch 3 (.sb3)
    if ext == '.sb3':
        from .scratch_utils import parse_scratch_sb3
        _, text_summary, _ = parse_scratch_sb3(file_path)
        if text_summary:
            lines = [line.strip() for line in text_summary.splitlines() if line.strip()]
            res = "\n".join(lines)

    # Спеціальна обробка BBC micro:bit (.hex)
    elif ext == '.hex':
        from .microbit_utils import parse_microbit_hex
        _, text_summary, _ = parse_microbit_hex(file_path)
        if text_summary:
            lines = [line.strip() for line in text_summary.splitlines() if line.strip()]
            res = "\n".join(lines)

    # Спеціальна обробка Microsoft Access (.mdb, .accdb)
    elif ext in ['.mdb', '.accdb']:
        try:
            from .access_utils import extract_access_text_for_ai
            access_text = extract_access_text_for_ai(file_path)
            if access_text:
                lines = [line.strip() for line in access_text.splitlines() if line.strip()]
                res = "\n".join(lines)
        except Exception:
            pass

    if not res:
        # Спробуємо розпарсити через універсальний екстрактор документів
        text, success, _ = extract_text_from_document(file_path, filename)
        if success and text:
            # Нормалізація тексту
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            res = "\n".join(lines)

    if not res:
        # Якщо це файл з кодом або простий текст
        encs = ['utf-8-sig', 'utf-8', 'cp1251', 'windows-1251', 'cp866', 'iso-8859-5']
        for enc in encs:
            try:
                with open(file_path, 'r', encoding=enc) as f:
                    content = f.read()
                    lines = [line.strip() for line in content.splitlines() if line.strip()]
                    res = "\n".join(lines)
                    break
            except Exception:
                continue

    if cache_key:
        if len(_NORM_TEXT_CACHE) >= _MAX_CACHE_SIZE:
            _NORM_TEXT_CACHE.clear()
        _NORM_TEXT_CACHE[cache_key] = res
        if shared_key:
            evidence_cache().set(shared_key, res, 604800)

    return res


# Повні збіги приносить детермінований контроль, а не лише підказка в промпті.
DUPLICATE_GRADE_CEILING = 4
_MAX_MATCHES = 10
_DOC_TEXT_EXTENSIONS = {'.doc', '.docx', '.odt', '.rtf', '.txt', '.md', '.pdf', '.tex'}


def _file_entry(record, file_field, name, role):
    """Describe one real on-disk file; ``None`` when it cannot be read."""
    if not file_field:
        return None
    path = getattr(file_field, 'path', None)
    try:
        if not path or not os.path.exists(path):
            return None
    except Exception:
        return None
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None
    return {
        'path': path,
        'name': name or os.path.basename(path),
        'record': record,
        'role': role,
        'size': size,
        'hash': None,
        'text': None,
    }


def _submission_files(submission):
    """Every attached file of a submission, including multi-file uploads.

    The legacy ``Submission.file`` is included once and is not duplicated when
    ``SubmissionFile`` rows reference the same stored name.
    """
    entries = []
    seen = set()
    try:
        related = list(submission.files.all())
    except Exception:
        related = []
    for item in related:
        field = getattr(item, 'file', None)
        name = getattr(item, 'original_name', '') or os.path.basename(
            getattr(field, 'name', '') or '')
        entry = _file_entry(item, field, name, 'student')
        if not entry:
            continue
        key = (os.path.realpath(entry['path']), entry['name'])
        if key in seen:
            continue
        seen.add(key)
        entries.append(entry)
    legacy = getattr(submission, 'file', None)
    if legacy:
        name = os.path.basename(getattr(legacy, 'name', '') or '')
        entry = _file_entry(submission, legacy, name, 'student')
        if entry:
            key = (os.path.realpath(entry['path']), entry['name'])
            if key not in seen:
                seen.add(key)
                entries.append(entry)
    return entries


def _entry_hash(entry):
    if entry['hash'] is None:
        entry['hash'] = get_file_sha256(entry['path']) or ''
    return entry['hash']


def _entry_text(entry):
    if entry['text'] is None:
        try:
            entry['text'] = get_normalized_file_content(entry['path'], entry['name']) or ''
        except Exception:
            entry['text'] = ''
    return entry['text']


def _extension(entry):
    return os.path.splitext(entry['name'] or entry['path'])[1].lower()


def _text_extensions_compatible(first, second):
    first_ext, second_ext = _extension(first), _extension(second)
    if first_ext == second_ext:
        return True
    return first_ext in _DOC_TEXT_EXTENSIONS and second_ext in _DOC_TEXT_EXTENSIONS


def _match_reason(one, other, min_text=30, any_text=True):
    """Return ``'binary'``/``'text'`` when the two files are identical copies."""
    if (one.get('size') is not None and other.get('size') is not None
            and one['size'] != other['size']):
        # Different byte length can still normalize to the same text; the list
        # of student files may combine differently saved copies of one answer.
        different_size = True
    else:
        different_size = False
    if not different_size:
        first_hash, second_hash = _entry_hash(one), _entry_hash(other)
        if first_hash and second_hash and first_hash == second_hash:
            return 'binary'
    if any_text or _text_extensions_compatible(one, other):
        first_text = _entry_text(one)
        if len(first_text) >= min_text:
            second_text = _entry_text(other)
            if second_text and first_text == second_text:
                return 'text'
    return None


def _same_author(submission, other):
    return (
        str(getattr(other, 'first_name', '') or '').strip().lower()
        == str(getattr(submission, 'first_name', '') or '').strip().lower()
        and str(getattr(other, 'last_name', '') or '').strip().lower()
        == str(getattr(submission, 'last_name', '') or '').strip().lower()
        and other.class_group_id == submission.class_group_id
    )


def duplicate_ceiling(info):
    """Deterministic grade ceiling for a proven full student duplicate.

    Full-match duplicate evidence lowers the mark to at most 4; collaboration
    and teacher-ignored matches never do. Integration applies the ceiling from
    the same ``check_submission_duplicates`` structure it already receives.
    """
    if not isinstance(info, dict):
        return None
    if info.get('is_duplicate_student') and not info.get('plagiarism_ignored') \
            and not info.get('is_coauthor'):
        return DUPLICATE_GRADE_CEILING
    return None


def check_submission_duplicates(submission):
    """
    Перевіряє роботу учня на наявність дублікатів:
    1. Збіг з файлами вчителя до цього завдання.
    2. Збіг з файлами інших учнів, зданими раніше по цьому ж завданню.

    Перевіряються ВСІ прикріплені файли (``SubmissionFile`` + legacy
    ``Submission.file``), а збіги повертаються ще й списком ``matches``.
    Legacy-ключі (is_duplicate_student/teacher, duplicate_student_name/class,
    type, warning_message) збережено для наявних споживачів.
    """
    result = {
        'is_duplicate': False,
        'is_duplicate_teacher': False,
        'teacher_file_name': None,
        'is_duplicate_student': False,
        'duplicate_student_name': None,
        'duplicate_student_first_name': None,
        'duplicate_student_last_name': None,
        'duplicate_class': None,
        'duplicate_submission_id': None,
        'duplicate_submitted_at': None,
        'duplicate_file_name': None,
        'duplicate_other_file_name': None,
        'type': 'none',
        'warning_message': '',
        'matches': [],
        'submission_files': [],
        'plagiarism_ignored': getattr(submission, 'ignore_plagiarism', False),
        'is_coauthor': False,
    }

    if not submission:
        return result

    if hasattr(submission, '_cached_dup_info'):
        return submission._cached_dup_info

    assignment = getattr(submission, 'assignment', None)
    if assignment is None:
        submission._cached_dup_info = result
        return result

    my_files = _submission_files(submission)
    result['submission_files'] = [entry['name'] for entry in my_files]
    if not my_files:
        submission._cached_dup_info = result
        return result

    ignore_plagiarism = bool(getattr(submission, 'ignore_plagiarism', False))
    file_hashes = sorted(entry_hash for entry_hash in
                         (_entry_hash(entry) for entry in my_files) if entry_hash)
    fingerprint = hashlib.sha256('|'.join(file_hashes).encode()).hexdigest() if file_hashes else ''
    dup_cache_key = None
    if submission.pk and fingerprint:
        dup_cache_key = (f"dup_info_v4_{submission.pk}_{assignment.pk}_"
                         f"{fingerprint}_{int(ignore_plagiarism)}")
        try:
            from .ai_context import evidence_cache
            cached_dup = evidence_cache().get(dup_cache_key)
            if cached_dup is not None:
                submission._cached_dup_info = cached_dup
                return cached_dup
        except Exception:
            pass

    def _finalize(res):
        submission._cached_dup_info = res
        if dup_cache_key:
            try:
                from .ai_context import evidence_cache
                evidence_cache().set(dup_cache_key, res, 3600)
            except Exception:
                pass
        return res

    def _teacher_match(entry, teacher_entry, reason, message):
        result.update({
            'is_duplicate': True,
            'is_duplicate_teacher': True,
            'teacher_file_name': teacher_entry['name'],
            'duplicate_file_name': entry['name'],
            'duplicate_other_file_name': teacher_entry['name'],
            'type': 'teacher_duplicate',
            'warning_message': message,
        })
        result['matches'] = [{
            'type': 'teacher_duplicate',
            'role': 'teacher',
            'reason': reason,
            'file': entry['name'],
            'other_file': teacher_entry['name'],
        }]
        return _finalize(result)

    # ── 1. ПЕРЕВІРКА НА ЗБІГ З ФАЙЛАМИ ВЧИТЕЛЯ ─────────────────────────────────
    if not ignore_plagiarism:
        same_assignment_files = []
        for assignment_file in assignment.files.all():
            entry = _file_entry(
                assignment_file, getattr(assignment_file, 'file', None),
                getattr(assignment_file, 'original_name', '') or os.path.basename(
                    getattr(getattr(assignment_file, 'file', None), 'name', '') or ''),
                'teacher')
            if entry:
                same_assignment_files.append(entry)
        for entry in my_files:
            for teacher_entry in same_assignment_files:
                reason = _match_reason(entry, teacher_entry, min_text=20)
                if not reason:
                    continue
                if len(my_files) > 1:
                    message = (
                        f"⚠️ Увага: вміст файлу «{entry['name']}» повністю збігається з файлом "
                        f"вчителя «{teacher_entry['name']}» до цього завдання. Схоже, що ви здали "
                        f"вихідний файл завдання замість виконаної роботи!")
                else:
                    message = (
                        f"⚠️ Увага: вміст прикріпленого файлу повністю збігається з файлом "
                        f"вчителя «{teacher_entry['name']}» до цього завдання. Схоже, що ви "
                        f"здали вихідний файл завдання замість виконаної роботи!")
                return _teacher_match(entry, teacher_entry, reason, message)

        # Перевірка на збіг із файлами вчителя до інших завдань (іншого класу або іншої теми)
        try:
            from .models import AssignmentFile
            other_af_qs = AssignmentFile.objects.exclude(assignment=assignment).filter(file__isnull=False)
            if getattr(assignment, 'teacher_id', None):
                other_af_qs = other_af_qs.filter(assignment__teacher_id=assignment.teacher_id)
            for other_af in other_af_qs[:50]:
                other_entry = _file_entry(
                    other_af, getattr(other_af, 'file', None),
                    getattr(other_af, 'original_name', '') or os.path.basename(
                        getattr(getattr(other_af, 'file', None), 'name', '') or ''),
                    'teacher')
                if not other_entry:
                    continue
                for entry in my_files:
                    reason = _match_reason(entry, other_entry, min_text=20, any_text=False)
                    if not reason:
                        continue
                    other_title = other_af.assignment.title if other_af.assignment else "іншого завдання"
                    message = (
                        f"⚠️ Увага: вміст файлу «{entry['name']}» повністю збігається з "
                        f"матеріалами вчителя «{other_entry['name']}» із завдання "
                        f"«{other_title}» (іншого класу або теми). Ви здали практичну "
                        f"роботу/завдання вчителя замість виконаної учнем роботи!")
                    if len(my_files) == 1:
                        message = (
                            f"⚠️ Увага: вміст прикріпленого файлу повністю збігається з "
                            f"матеріалами вчителя «{other_entry['name']}» із завдання "
                            f"«{other_title}» (іншого класу або теми). Ви здали практичну "
                            f"роботу/завдання вчителя замість виконаної учнем роботи!")
                    matched = _teacher_match(entry, other_entry, reason, message)
                    matched['matches'][0]['other_assignment'] = other_title
                    matched['matches'][0]['role'] = 'teacher_material'
                    return matched
        except Exception:
            pass

    # ── 2. ПЕРЕВІРКА НА ЗБІГ З РОБОТАМИ ІНШИХ УЧНІВ ────────────────────────────
    from django.db.models import Q
    other_submissions = assignment.submissions.filter(
        Q(file__isnull=False) | Q(files__isnull=False)).distinct()
    if submission.id:
        other_submissions = other_submissions.exclude(id=submission.id)

    for other in other_submissions.select_related('class_group').order_by('submitted_at'):
        if _same_author(submission, other):
            continue
        other_files = _submission_files(other)
        if not other_files:
            continue
        matched = None
        for entry in my_files:
            for other_entry in other_files:
                reason = _match_reason(entry, other_entry, min_text=30)
                if reason:
                    matched = (entry, other_entry, reason)
                    break
            if matched:
                break
        if not matched:
            continue

        entry, other_entry, reason = matched
        other_name = other.get_student_full_name()
        other_class = str(other.class_group)
        is_coauthor = (
            (hasattr(submission, 'is_coauthor_with') and submission.is_coauthor_with(other)) or
            (hasattr(other, 'is_coauthor_with') and other.is_coauthor_with(submission)) or
            (submission.primary_submission_id and submission.primary_submission_id == other.primary_submission_id) or
            (submission.primary_submission_id and submission.primary_submission_id == other.id) or
            (other.primary_submission_id and other.primary_submission_id == submission.id)
        )
        is_plagiarism_explicitly_ignored = (
            getattr(submission, 'ignore_plagiarism', False) or
            getattr(other, 'ignore_plagiarism', False)
        )
        base_match = {
            'file': entry['name'],
            'other_file': other_entry['name'],
            'reason': reason,
            'student_name': other_name,
            'class': other_class,
            'submission_id': other.id,
            'submitted_at': other.submitted_at,
        }

        if is_coauthor:
            if not result['is_duplicate_student']:
                result.update({
                    'is_duplicate': False,
                    'is_duplicate_student': False,
                    'plagiarism_ignored': True,
                    'is_coauthor': True,
                    'duplicate_submission_id': other.id,
                    'duplicate_student_name': other_name,
                    'duplicate_student_first_name': other.first_name,
                    'duplicate_student_last_name': other.last_name,
                    'duplicate_class': other_class,
                    'duplicate_submitted_at': other.submitted_at,
                    'duplicate_file_name': entry['name'],
                    'duplicate_other_file_name': other_entry['name'],
                    'type': 'ignored_collaboration',
                    'warning_message': (
                        f"👥 Спільна/колективна робота: файл «{entry['name']}» збігається з "
                        f"роботою співавтора {other_name} ({other_class}). Плагіат виключено "
                        f"(робота оцінюється за спільний результат)."
                    ),
                    'matches': [dict(base_match, type='ignored_collaboration')],
                })
                return _finalize(result)
            continue
        if is_plagiarism_explicitly_ignored:
            if not result['is_duplicate_student']:
                result.update({
                    'is_duplicate': False,
                    'is_duplicate_student': False,
                    'plagiarism_ignored': True,
                    'is_coauthor': False,
                    'duplicate_submission_id': other.id,
                    'duplicate_student_name': other_name,
                    'duplicate_student_first_name': other.first_name,
                    'duplicate_student_last_name': other.last_name,
                    'duplicate_class': other_class,
                    'duplicate_submitted_at': other.submitted_at,
                    'duplicate_file_name': entry['name'],
                    'duplicate_other_file_name': other_entry['name'],
                    'type': 'ignored_plagiarism',
                    'warning_message': (
                        f"ℹ️ Збіг файлу «{entry['name']}» з роботою {other_name} ({other_class}) "
                        f"проігноровано вчителем."
                    ),
                    'matches': [dict(base_match, type='ignored_plagiarism')],
                })
                return _finalize(result)
            continue

        match_reason = (
            f"вміст файлу «{entry['name']}» повністю збігається з файлом"
            if reason == 'binary'
            else f"текстовий вміст файлу «{entry['name']}» на 100% збігається з роботою")
        result['matches'].append(dict(base_match, type='student_duplicate'))
        if not result['is_duplicate_student']:
            result.update({
                'is_duplicate': True,
                'is_duplicate_student': True,
                'duplicate_submission_id': other.id,
                'duplicate_student_name': other_name,
                'duplicate_student_first_name': other.first_name,
                'duplicate_student_last_name': other.last_name,
                'duplicate_class': other_class,
                'duplicate_submitted_at': other.submitted_at,
                'duplicate_file_name': entry['name'],
                'duplicate_other_file_name': other_entry['name'],
                'type': 'student_duplicate',
                'warning_message': (
                    f"⚠️ Увага: {match_reason}, яку раніше здав(ла) {other_name} ({other_class}). "
                    f"Система зафіксувала однаковий вміст файлу як підозру на дублікат або списування!"
                ),
            })
        if len(result['matches']) >= _MAX_MATCHES:
            break

    result['is_duplicate'] = bool(result['is_duplicate_teacher'] or result['is_duplicate_student'])
    return _finalize(result)
