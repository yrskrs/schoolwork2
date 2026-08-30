"""
Модуль інтелектуального імпорту та парсингу списків учнів та класів для SchoolNet.
Підтримує імпорт з текстових списків, CSV, TSV та TXT файлів.
"""

import re
import csv
import io
from typing import List, Dict, Tuple, Optional, Any
from .models import ClassGroup, Student, Submission, Teacher
from .student_matcher import resolve_canonical_student_name, normalize_clean_string, is_same_student_identity


def parse_class_group_name(raw_name: str) -> Tuple[str, int, str]:
    """
    Аналізує сирий рядок класу (наприклад '9-А', '9А', ' 10-Б ', '11В', '8')
    та повертає (нормалізована_назва, grade_цифра, letter_літера).
    """
    raw = raw_name.strip()
    if not raw:
        return ("9-А", 9, "А")

    # Спроба знайти цифру та літеру, наприклад 9-А, 10Б, 11 В
    match = re.search(r'(\d{1,2})\s*[-_/\s]?\s*([А-Яа-яA-Za-zІіЇїЄєҐґ]*)', raw)
    if match:
        grade_str = match.group(1)
        letter_str = match.group(2).upper() if match.group(2) else "А"
        grade = int(grade_str)
        # Формуємо красиву назву, наприклад "9-А" або зберігаємо оригінальний формат
        if letter_str:
            norm_name = f"{grade}-{letter_str}"
        else:
            norm_name = f"{grade}"
        return (norm_name, grade, letter_str)

    return (raw, 9, "А")


def get_or_create_class_group(class_name_str: str, teacher: Optional[Teacher] = None) -> Tuple[ClassGroup, bool]:
    """
    Знаходить або створює ClassGroup за назвою (наприклад '9-А' чи '9А').
    Якщо вчитель переданий, додає клас до його списку класів.
    """
    raw_clean = class_name_str.strip()
    norm_name, grade, letter = parse_class_group_name(raw_clean)

    # 1. Спроба точного пошуку
    cg = ClassGroup.objects.filter(name__iexact=raw_clean).first()
    if not cg:
        # 2. Спроба пошуку за нормалізованою назвою (наприклад 9-А або 9А)
        cg = ClassGroup.objects.filter(name__iexact=norm_name).first()
    if not cg:
        # 3. Пошук варіантів без дефіса або з дефісом
        alt_name = norm_name.replace('-', '')
        cg = ClassGroup.objects.filter(name__iexact=alt_name).first()
    if not cg:
        # 4. Пошук за grade та letter
        cg = ClassGroup.objects.filter(grade=grade, letter__iexact=letter).first()

    created = False
    if not cg:
        cg = ClassGroup.objects.create(
            name=norm_name,
            grade=grade,
            letter=letter or 'А',
            created_by=teacher
        )
        created = True

    if teacher and cg not in teacher.classes.all():
        teacher.classes.add(cg)

    return cg, created


def parse_student_line(line: str, default_class: Optional[ClassGroup] = None) -> Optional[Dict[str, Any]]:
    """
    Розбирає один рядок списку учнів.
    Формати:
    - "Іваненко Тарас, 9-А"
    - "Іваненко, Тарас, 9-А"
    - "Іваненко Тарас (9-А)"
    - "9-А: Іваненко Тарас"
    - "9-А - Іваненко Тарас"
    - "1. Іваненко Тарас" (якщо задано default_class)
    - "Іваненко Тарас" (якщо задано default_class)
    """
    clean = line.strip()
    if not clean:
        return None

    # Ігноруємо заголовки таблиць
    low = clean.lower()
    if any(h in low for h in ['прізвище', 'ім\'я', 'імʼя', 'full name', 'last name', 'first name', 'клас']):
        if ',' in clean or ';' in clean or '\t' in clean:
            return None

    # Прибираємо порядковий номер на початку рядка, наприклад "1. ", "1) ", "1.  " (але не класи на зразок 9-А)
    clean = re.sub(r'^\d{1,3}\s*[\.\)]\s*', '', clean).strip()
    if not clean:
        return None

    extracted_class_name = None
    extracted_name = None

    # Формат 1: "9-А: Іваненко Тарас" або "9А - Іваненко Тарас"
    prefix_class_match = re.match(r'^(\d{1,2}\s*[-_/\s]?\s*[А-Яа-яA-Za-zІіЇїЄєҐґ]*)\s*[:\-–—]\s*(.+)$', clean)
    if prefix_class_match:
        cand_c = prefix_class_match.group(1).strip()
        # Переконуємось що це схоже на клас, а не прізвище
        if re.search(r'\d', cand_c):
            extracted_class_name = cand_c
            extracted_name = prefix_class_match.group(2).strip()

    # Формат 2: "Іваненко Тарас (9-А)"
    if not extracted_name:
        paren_match = re.search(r'^(.+?)\s*\((.+?)\)\s*$', clean)
        if paren_match:
            cand_name = paren_match.group(1).strip()
            cand_class = paren_match.group(2).strip()
            if re.search(r'\d', cand_class):
                extracted_name = cand_name
                extracted_class_name = cand_class

    # Формат 3: Розділення комами, крапками з комою або табуляцією
    if not extracted_name:
        delimiter = None
        if '\t' in clean:
            delimiter = '\t'
        elif ';' in clean:
            delimiter = ';'
        elif ',' in clean:
            delimiter = ','

        if delimiter:
            parts = [p.strip() for p in clean.split(delimiter) if p.strip()]
            if len(parts) >= 3:
                # Наприклад: "Іваненко", "Тарас", "9-А" або "9-А", "Іваненко", "Тарас"
                if re.search(r'\d', parts[0]) and not re.search(r'\d', parts[2]):
                    extracted_class_name = parts[0]
                    extracted_name = f"{parts[1]} {parts[2]}"
                else:
                    extracted_name = f"{parts[0]} {parts[1]}"
                    extracted_class_name = parts[2]
            elif len(parts) == 2:
                # Наприклад: "Іваненко Тарас", "9-А" або "9-А", "Іваненко Тарас"
                p0, p1 = parts[0], parts[1]
                if re.search(r'\d', p0) and not re.search(r'\d', p1):
                    extracted_class_name = p0
                    extracted_name = p1
                elif re.search(r'\d', p1) and not re.search(r'\d', p0):
                    extracted_name = p0
                    extracted_class_name = p1
                else:
                    # Два слова без цифр: можливо "Прізвище, Ім'я"
                    extracted_name = f"{p0} {p1}"

    # Якщо клас не знайдено, використовуємо весь рядок як ім'я
    if not extracted_name:
        extracted_name = clean

    # Визначаємо прізвище та ім'я
    tokens = extracted_name.split()
    if len(tokens) == 0:
        return None
    elif len(tokens) == 1:
        last_name = tokens[0].capitalize()
        first_name = ""
    else:
        # Канонічна нормалізація
        last_name, first_name = resolve_canonical_student_name(extracted_name)

    return {
        'last_name': last_name,
        'first_name': first_name,
        'raw_class_name': extracted_class_name,
        'default_class': default_class
    }


def import_students_from_text(
    text: str,
    default_class: Optional[ClassGroup] = None,
    teacher: Optional[Teacher] = None,
    create_missing_classes: bool = True
) -> Dict[str, Any]:
    """
    Імпортує список учнів із тексту.
    Повертає статистику: {
        'total_lines': int,
        'created_count': int,
        'existing_count': int,
        'created_classes': List[str],
        'errors': List[str],
        'students': List[Student]
    }
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    created_count = 0
    existing_count = 0
    created_classes_set = set()
    errors = []
    processed_students = []

    for line_num, line in enumerate(lines, 1):
        parsed = parse_student_line(line, default_class=default_class)
        if not parsed:
            continue

        ln = parsed['last_name']
        fn = parsed['first_name']
        raw_c = parsed['raw_class_name']

        # Визначаємо клас
        target_class = None
        if raw_c:
            if create_missing_classes:
                target_class, was_cls_created = get_or_create_class_group(raw_c, teacher=teacher)
                if was_cls_created:
                    created_classes_set.add(target_class.name)
            else:
                target_class = ClassGroup.objects.filter(name__iexact=raw_c).first()
                if not target_class:
                    norm_c, _, _ = parse_class_group_name(raw_c)
                    target_class = ClassGroup.objects.filter(name__iexact=norm_c).first()

        if not target_class:
            target_class = default_class

        if not target_class:
            errors.append(f"Рядок {line_num}: Не вказано клас для учня «{ln} {fn}»")
            continue

        # Перевірка наявності такого учня в класі (з урахуванням нечіткого збігу)
        existing_students = list(Student.objects.filter(class_group=target_class))
        matched_student = None
        for es in existing_students:
            if is_same_student_identity(ln, fn, es.last_name, es.first_name):
                matched_student = es
                break

        if matched_student:
            existing_count += 1
            # Оновлюємо ім'я, якщо воно більш повне
            if fn and not matched_student.first_name:
                matched_student.first_name = fn
                matched_student.save(update_fields=['first_name', 'updated_at'])
            # Синхронізуємо здачі
            matched_student.sync_submissions()
            processed_students.append(matched_student)
        else:
            # Створюємо нового учня
            student = Student.objects.create(
                last_name=ln,
                first_name=fn,
                class_group=target_class
            )
            created_count += 1
            student.sync_submissions()
            processed_students.append(student)

    return {
        'total_lines': len(lines),
        'created_count': created_count,
        'existing_count': existing_count,
        'created_classes': list(created_classes_set),
        'errors': errors,
        'students': processed_students
    }


def import_students_from_file(
    file_obj,
    default_class: Optional[ClassGroup] = None,
    teacher: Optional[Teacher] = None,
    create_missing_classes: bool = True
) -> Dict[str, Any]:
    """
    Імпортує учнів із завантаженого файлу (CSV, TXT, TSV).
    """
    # Спроба прочитати як текст у різних кодуваннях (utf-8, utf-8-sig, cp1251)
    content = ""
    file_bytes = file_obj.read()
    for enc in ['utf-8-sig', 'utf-8', 'windows-1251', 'cp1251', 'latin1']:
        try:
            content = file_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue

    if not content:
        return {
            'total_lines': 0,
            'created_count': 0,
            'existing_count': 0,
            'created_classes': [],
            'errors': ['Не вдалося розпізнати кодування файлу. Будь ласка, збережіть файл у кодуванні UTF-8.'],
            'students': []
        }

    return import_students_from_text(
        content,
        default_class=default_class,
        teacher=teacher,
        create_missing_classes=create_missing_classes
    )


def sync_all_existing_submissions_to_students():
    """
    Автоматично створює записи Student для всіх унікальних учнів,
    які вже є у таблиці Submission, та прив'язує здачі.
    Викликається при першому відкритті сторінки або для синхронізації.
    """
    all_class_groups = ClassGroup.objects.all()
    created_total = 0

    for cg in all_class_groups:
        raw_names = Submission.objects.filter(class_group=cg).values_list('last_name', 'first_name').distinct()
        existing_students = list(Student.objects.filter(class_group=cg))

        for ln, fn in raw_names:
            ln_clean = ln.strip()
            fn_clean = fn.strip()
            if not ln_clean and not fn_clean:
                continue

            # Перевіряємо чи є вже такий учень
            found = False
            for es in existing_students:
                if is_same_student_identity(ln_clean, fn_clean, es.last_name, es.first_name):
                    found = True
                    # Прив'язуємо FK до submission
                    Submission.objects.filter(
                        class_group=cg,
                        last_name=ln,
                        first_name=fn,
                        student__isnull=True
                    ).update(student=es)
                    break

            if not found:
                # Створюємо студента
                new_student = Student.objects.create(
                    last_name=ln_clean or "Учень",
                    first_name=fn_clean,
                    class_group=cg
                )
                existing_students.append(new_student)
                created_total += 1
                new_student.sync_submissions()

    return created_total
