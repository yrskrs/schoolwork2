"""
Модуль розумного неточного (fuzzy) пошуку для SchoolNet.
Забезпечує:
1. Толерантність до друкарських помилок (typos, Levenshtein distance).
2. Автоматичне виправлення розкладки клавіатури (EN <-> UK).
3. Врахування українських відмінків та закінчень (стемінг).
4. Комплексний пошук за заголовком, описом, предметом, вчителем, учнем, класом та назвами файлів.
5. Релевантне ранжування результатів.
"""

import re
import unicodedata
from difflib import SequenceMatcher
from django.db.models import Q


# Таблиця розкладки EN -> UK
EN_TO_UK_MAP = {
    'q': 'й', 'w': 'ц', 'e': 'у', 'r': 'к', 't': 'е', 'y': 'н', 'u': 'г', 'i': 'ш', 'o': 'щ', 'p': 'з',
    '[': 'х', ']': 'ї', 'a': 'ф', 's': 'і', 'd': 'в', 'f': 'а', 'g': 'п', 'h': 'р', 'j': 'о', 'k': 'л',
    'l': 'д', ';': 'ж', "'": 'є', 'z': 'я', 'x': 'ч', 'c': 'с', 'v': 'м', 'b': 'и', 'n': 'т', 'm': 'ь',
    ',': 'б', '.': 'ю', '`': 'ʼ', '~': 'ʼ',
    'Q': 'Й', 'W': 'Ц', 'E': 'У', 'R': 'К', 'T': 'Е', 'Y': 'Н', 'U': 'Г', 'I': 'Ш', 'O': 'Щ', 'P': 'З',
    '{': 'Х', '}': 'Ї', 'A': 'Ф', 'S': 'І', 'D': 'В', 'F': 'А', 'G': 'П', 'H': 'Р', 'J': 'О', 'K': 'Л',
    'L': 'Д', ':': 'Ж', '"': 'Є', 'Z': 'Я', 'X': 'Ч', 'C': 'С', 'V': 'М', 'B': 'И', 'N': 'Т', 'M': 'Ь',
    '<': 'Б', '>': 'Ю', '?': '?'
}

# Таблиця розкладки UK -> EN
UK_TO_EN_MAP = {v: k for k, v in EN_TO_UK_MAP.items() if len(k) == 1 and len(v) == 1}


# Словник синонімів та абревіатур (шкільні терміни та формати файлів)
COMMON_SYNONYMS = {
    'пдф': ['pdf'],
    'pdf': ['пдф'],
    'ворд': ['word', 'doc', 'docx', 'документ'],
    'word': ['ворд', 'doc', 'docx'],
    'ексель': ['excel', 'xls', 'xlsx', 'таблиця'],
    'excel': ['ексель', 'xls', 'xlsx'],
    'презентація': ['pptx', 'ppt', 'powerpoint', 'слайди'],
    'слайди': ['pptx', 'ppt', 'презентація'],
    'ютуб': ['youtube', 'відео'],
    'youtube': ['ютуб', 'відео'],
    'відео': ['youtube', 'mp4', 'mov', 'webm'],
    'картинка': ['фото', 'зображення', 'jpg', 'png'],
    'зображення': ['фото', 'картинка', 'jpg', 'png', 'малюнок'],
    'фото': ['картинка', 'зображення', 'jpg', 'png'],
    'дз': ['домашнє', 'завдання', 'домашня'],
    'кр': ['контрольна', 'робота'],
    'ср': ['самостійна', 'робота'],
}


def normalize_text(text: str) -> str:
    """Нормалізує текст: нижній регістр, уніфікація апострофів, видалення зайвих символів."""
    if not text:
        return ""
    text = unicodedata.normalize('NFKC', str(text)).lower()
    # Уніфікація апострофів
    text = re.sub(r"['`’ʼ\"]", "'", text)
    # Заміна тире та дефісів на пробіли/уніфікований дефіс
    text = re.sub(r"[\t\r\n]+", " ", text)
    return text.strip()


def convert_keyboard_layout(text: str) -> str:
    """Перетворює текст, набраний у невірній розкладці (наприклад, 'vfnjvfnbrf' -> 'математика')."""
    if not text:
        return ""
    # Якщо містить переважно латиницю — пробуємо перетворити на кирилицю
    has_latin = any('a' <= c.lower() <= 'z' for c in text)
    has_cyrillic = any('а' <= c.lower() <= 'я' or c.lower() in 'ієїґ' for c in text)

    if has_latin and not has_cyrillic:
        converted = "".join(EN_TO_UK_MAP.get(c, c) for c in text)
        return converted
    elif has_cyrillic and not has_latin:
        converted = "".join(UK_TO_EN_MAP.get(c, c) for c in text)
        return converted
    return text


def ukrainian_stem(word: str) -> str:
    """
    Простий та швидкий стемер для української мови.
    Відсікає типові закінчення іменників та прикметників для знаходження спільної основи.
    """
    if len(word) <= 3:
        return word

    w = word.lower()

    # Специфічні закінчення
    endings = [
        'івський', 'евський', 'ського', 'цького', 'ському', 'цькому', 'ськими', 'цькими',
        'ований', 'ованого', 'ованому', 'ованих',
        'ями', 'ами', 'ими', 'іми', 'ого', 'ому', 'ою', 'ею', 'єю', 'их', 'іх',
        'ям', 'ам', 'ім', 'им', 'ах', 'ях',
        'ів', 'ев', 'єв', 'ей', 'ій', 'ий', 'ой', 'ай',
        'а', 'я', 'у', 'ю', 'е', 'є', 'и', 'і', 'о'
    ]

    for ending in sorted(endings, key=len, reverse=True):
        if w.endswith(ending) and len(w) - len(ending) >= 3:
            return w[:-len(ending)]

    return w


def tokenize(text: str) -> list[str]:
    """Розбиває рядок на слова та токени (букви + цифри)."""
    if not text:
        return []
    cleaned = normalize_text(text)
    # Зберігаємо слова та цифрові комбінації
    tokens = re.findall(r"[\w'-]+", cleaned)
    return [t.strip("'-") for t in tokens if len(t.strip("'-")) > 0]


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Обчислює коефіцієнт схожості між двома рядками від 0.0 до 1.0."""
    if not s1 or not s2:
        return 0.0
    if s1 == s2:
        return 1.0
    return SequenceMatcher(None, s1, s2).ratio()


def fuzzy_match_token(query_token: str, target_token: str) -> float:
    """
    Оцінює схожість між пошуковим токеном та словом у завданні.
    Повертає оцінку від 0.0 до 1.0.
    """
    q = query_token.lower()
    t = target_token.lower()

    if q == t:
        return 1.0

    # Якщо один є початком іншого
    if t.startswith(q) and len(q) >= 3:
        return 0.95
    if q.startswith(t) and len(t) >= 3:
        return 0.85

    # Порівняння за стемінгом
    stem_q = ukrainian_stem(q)
    stem_t = ukrainian_stem(t)
    if stem_q == stem_t or (len(stem_q) >= 3 and (stem_t.startswith(stem_q) or stem_q.startswith(stem_t))):
        return 0.92

    # Порівняння за подібністю рядків (Levenshtein)
    sim = levenshtein_similarity(q, t)
    if sim >= 0.72:
        return sim

    return 0.0


def score_assignment(assignment, query_tokens: list[str], raw_query: str) -> float:
    """
    Розраховує оцінку релевантності завдання для заданого пошукового запиту.
    """
    total_score = 0.0

    # Збираємо текстові поля завдання з відповідними вагами
    fields = [
        (assignment.title or '', 35.0, 'title'),
        (str(assignment.subject.name) if assignment.subject else '', 30.0, 'subject'),
        (assignment.teacher.full_name if assignment.teacher else '', 25.0, 'teacher'),
        (assignment.student_name or '', 25.0, 'student'),
        (assignment.description or '', 15.0, 'description'),
    ]

    # Додаємо класи завдання
    classes_str = " ".join([c.name for c in assignment.classes.all()] + [f"{c.grade}{c.letter}" for c in assignment.classes.all()] + [f"{c.grade} {c.letter}" for c in assignment.classes.all()])
    if classes_str:
        fields.append((classes_str, 25.0, 'classes'))

    # Додаємо назви файлів
    files_str = " ".join([f.original_name for f in assignment.files.all()])
    if files_str:
        fields.append((files_str, 18.0, 'files'))

    raw_normalized = normalize_text(raw_query)

    # 1. Пряме входження всієї фрази
    for text, weight, field_name in fields:
        norm_text = normalize_text(text)
        if raw_normalized and raw_normalized in norm_text:
            total_score += weight * 2.0

    # 2. Пошук по окремих токенах
    for q_token in query_tokens:
        token_best_score = 0.0

        for text, weight, field_name in fields:
            target_tokens = tokenize(text)
            for t_token in target_tokens:
                match_val = fuzzy_match_token(q_token, t_token)
                if match_val > 0:
                    field_score = match_val * weight
                    if field_score > token_best_score:
                        token_best_score = field_score

        total_score += token_best_score

    return total_score


def search_assignments(queryset, query_str: str):
    """
    Головна функція неточного розумного пошуку.
    Приймає базовий QuerySet завдань та рядок запиту.
    Повертає відфільтрований та відсортований за релевантністю список або QuerySet завдань.
    """
    if not query_str or not query_str.strip():
        return queryset

    raw_query = query_str.strip()
    layout_converted = convert_keyboard_layout(raw_query)

    # Токенізуємо оригінальний та конвертований запит
    tokens_orig = tokenize(raw_query)
    tokens_conv = tokenize(layout_converted) if layout_converted != raw_query else []

    all_tokens = list(dict.fromkeys(tokens_orig + tokens_conv))

    # Додаємо синоніми для знайдених токенів
    synonym_tokens = []
    for t in all_tokens:
        t_low = t.lower()
        if t_low in COMMON_SYNONYMS:
            synonym_tokens.extend(COMMON_SYNONYMS[t_low])

    all_query_tokens = list(dict.fromkeys(all_tokens + synonym_tokens))

    if not all_query_tokens:
        return queryset

    # Будуємо оптимізований базовий SQL фільтр для швидкого вибору кандидатів
    q_filter = Q()

    # Точні фрази
    q_filter |= Q(title__icontains=raw_query) | Q(description__icontains=raw_query) | Q(teacher__full_name__icontains=raw_query)
    if layout_converted != raw_query:
        q_filter |= Q(title__icontains=layout_converted) | Q(description__icontains=layout_converted)

    # Токени та їх стеми
    for token in all_query_tokens:
        stem = ukrainian_stem(token)
        q_filter |= Q(title__icontains=token)
        q_filter |= Q(description__icontains=token)
        q_filter |= Q(subject__name__icontains=token)
        q_filter |= Q(teacher__full_name__icontains=token)
        q_filter |= Q(student_name__icontains=token)
        q_filter |= Q(classes__name__icontains=token)
        q_filter |= Q(files__original_name__icontains=token)

        if len(stem) >= 3 and stem != token:
            q_filter |= Q(title__icontains=stem)
            q_filter |= Q(description__icontains=stem)
            q_filter |= Q(subject__name__icontains=stem)

    # Завантажуємо попередньо відфільтровані кандидати
    candidates = list(queryset.filter(q_filter).distinct().select_related('teacher', 'subject').prefetch_related('classes', 'files'))

    # Якщо прямий SQL-фільтр нічого не повернув (наприклад через серйозні помилки на зразок 'математека' замість 'математика'),
    # перевіряємо всі видимі завдання в поточному зрізі (до 300 останніх завдань)
    if not candidates:
        candidates = list(queryset.all().select_related('teacher', 'subject').prefetch_related('classes', 'files')[:300])

    # Вираховуємо fuzzy-оцінку релевантності для кожного завдання
    scored_items = []
    for assignment in candidates:
        score = score_assignment(assignment, all_query_tokens, raw_query)
        if score >= 14.0:  # Поріг відсікання нерелевантного сміття
            scored_items.append((score, assignment))

    # Сортуємо: спочатку за найвищою релевантністю (score), потім за свіжістю публікації
    scored_items.sort(key=lambda x: (x[0], x[1].published_at or x[1].created_at), reverse=True)

    result_assignments = [item[1] for item in scored_items]
    return result_assignments
