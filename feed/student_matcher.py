"""
Модуль інтелектуального розпізнавання, нормалізації та нечіткого групування імен учнів для SchoolNet.

Забезпечує:
1. Виправлення порядку слів: "Прізвище Ім'я" та "Ім'я Прізвище" (наприклад: "Тарас Шевченко" == "Шевченко Тарас").
2. Розпізнавання зменшувальних, пестливих та скорочених імен (наприклад: "Олександр" == "Саша" == "Сашко", "Дмитро" == "Діма", "Владислав" == "Влад", "Софія" == "Соня", "Шевченко Т." == "Шевченко Тарас").
3. Стійкість до друкарських помилок та закінчень прізвищ (наприклад: "Шевченка Тарас", "Мельнік Софія", "Бондарчук Дмитро").
4. Прив'язку до канонічного профілю учня в межах класу в журналі оцінок, особистому кабінеті та списку здач.
"""

import re
import difflib
from typing import Tuple, List, Dict, Optional, Set, Any

# Словник еквівалентності українських імен (повні, скорочені, пестливі форми)
UKRAINIAN_NAME_VARIANTS: Dict[str, Set[str]] = {
    'олександр': {'олександр', 'саша', 'сашко', 'саня', 'сашуня', 'алекс', 'олесь', 'шура'},
    'олександра': {'олександра', 'саша', 'сашка', 'сашуня', 'леся'},
    'дмитро': {'дмитро', 'діма', 'дімка', 'митько', 'дима'},
    'володимир': {'володимир', 'вова', 'володя', 'володька', 'вовчик'},
    'владислав': {'владислав', 'влад', 'владік', 'владуся'},
    'владислава': {'владислава', 'влада', 'владка'},
    'михайло': {'михайло', 'міша', 'михась', 'михайлик', 'мішаня'},
    'максим': {'максим', 'макс', 'максимко', 'максимчик'},
    'богдан': {'богдан', 'бодя', 'богданчик', 'богданко'},
    'артем': {'артем', 'тьома', 'артемко', 'артемчик', 'артемій'},
    'андрій': {'андрій', 'андрійко', 'андрійчик', 'андрей'},
    'іван': {'іван', 'ваня', 'іванко', 'іванчик'},
    'микола': {'микола', 'коля', 'миколка', 'миколай'},
    'олексій': {'олексій', 'льоша', 'алексій', 'алексей'},
    'ярослав': {'ярослав', 'ярік', 'ярик', 'ярославчик'},
    'роман': {'роман', 'рома', 'ромчик', 'романчик'},
    'ростислав': {'ростислав', 'ростик', 'ростиславчик'},
    'денис': {'денис', 'ден', 'деник', 'дениско'},
    'назар': {'назар', 'назарій', 'назарко', 'назарчик'},
    'тимофій': {'тимофій', 'тіма', 'тиміш', 'тимофійко'},
    'матвій': {'матвій', 'мотя', 'матвійко', 'матвійчик'},
    'павло': {'павло', 'паша', 'павлик', 'павлуша'},
    'станіслав': {'станіслав', 'стас', 'стасік', 'станіславчик'},
    'євген': {'євген', 'євгеній', 'женя', 'євгенко'},
    'євгенія': {'євгенія', 'женя', 'євгенка'},
    'ілля': {'ілля', 'ілюша', 'ілько', 'илья'},
    'тарас': {'тарас', 'тарасик', 'тараско'},
    'софія': {'софія', 'соня', 'софійка', 'сонька'},
    'марія': {'марія', 'маша', 'марійка', 'маруся'},
    'анна': {'анна', 'ганна', 'аня', 'анечка', 'ануся', 'анічка'},
    'катерина': {'катерина', 'катя', 'катруся', 'катічка', 'катеринка'},
    'анастасія': {'анастасія', 'настя', 'настуся', 'настінька'},
    'вікторія': {'вікторія', 'віка', 'вікуся', 'вікторінка'},
    'дарія': {'дарія', 'дар\'я', 'даша', 'даринка', 'дарка'},
    'поліна': {'поліна', 'поля', 'полінка', 'поліночка'},
    'тетяна': {'тетяна', 'таня', 'танечка', 'танюша'},
    'олена': {'олена', 'альона', 'лена', 'оленка'},
    'юлія': {'юлія', 'юля', 'юленька', 'юлечка'},
    'верес': {'верес', 'вересик'},
    'вероніка': {'вероніка', 'ніка', 'веронічка'},
    'діана': {'діана', 'діанка', 'дія'},
    'єлизавета': {'єлизавета', 'ліза', 'лисавета'},
    'злата': {'злата', 'златка', 'златочка'},
    'ілона': {'ілона', 'ілонка'},
    'інна': {'інна', 'іннуся'},
    'кіра': {'кіра', 'кірочка'},
    'ксенія': {'ксенія', 'оксана', 'ксюша'},
    'людмила': {'людмила', 'люда', 'людочка', 'люся'},
    'надія': {'надія', 'надя', 'надійка'},
    'наталія': {'наталія', 'наталя', 'наташа', 'наталочка'},
    'світлана': {'світлана', 'свєта', 'світланка'},
    'юрій': {'юрій', 'юра', 'юрчик', 'юрко'},
    'кирило': {'кирило', 'кирил', 'кирюша'},
    'захар': {'захар', 'захарко', 'захарчик'},
    'святослав': {'святослав', 'святик', 'святославчик'},
    'арсен': {'арсен', 'арсеній', 'сеня'},
    'сергій': {'сергій', 'серьожа', 'сергійко', 'серж'},
    'віктор': {'віктор', 'вітя', 'вітьок'},
    'василь': {'василь', 'вася', 'василько'},
    'григорій': {'григорій', 'гріша', 'гриць'},
    'костянтин': {'костянтин', 'костя', 'костік'},
}

# Зворотний індекс імен для миттєвого пошуку канонічної форми
_FIRST_NAME_INDEX: Dict[str, Set[str]] = {}
for canonical, variants in UKRAINIAN_NAME_VARIANTS.items():
    for var in variants:
        if var not in _FIRST_NAME_INDEX:
            _FIRST_NAME_INDEX[var] = set()
        _FIRST_NAME_INDEX[var].update(variants)


def normalize_clean_string(s: str) -> str:
    """Видаляє спецсимволи та зайві пробіли."""
    if not s:
        return ""
    return re.sub(r'\s+', ' ', s.strip())


def are_first_names_equivalent(fn1: str, fn2: str) -> bool:
    """
    Перевіряє чи є два імені еквівалентними з урахуванням пестливих форм, ініціалів та одруків.
    """
    if not fn1 or not fn2:
        return False

    n1 = fn1.strip().lower().rstrip('.')
    n2 = fn2.strip().lower().rstrip('.')

    if n1 == n2:
        return True

    # Перевірка ініціалів: "Т" або "Т." збігається з "Тарас"
    if (len(n1) == 1 and n2.startswith(n1)) or (len(n2) == 1 and n1.startswith(n2)):
        return True

    # Перевірка за базою синонімів імен
    variants1 = _FIRST_NAME_INDEX.get(n1)
    if variants1 and n2 in variants1:
        return True

    variants2 = _FIRST_NAME_INDEX.get(n2)
    if variants2 and n1 in variants2:
        return True

    # Нечітке порівняння для дрібних одруків (наприклад: "Дмитро" і "Дмитрій")
    ratio = difflib.SequenceMatcher(None, n1, n2).ratio()
    if ratio >= 0.80:
        return True

    return False


def are_last_names_equivalent(ln1: str, ln2: str) -> bool:
    """
    Перевіряє чи є два прізвища еквівалентними з урахуванням закінчень, відмінків та одруків.
    """
    if not ln1 or not ln2:
        return False

    l1 = ln1.strip().lower()
    l2 = ln2.strip().lower()

    if l1 == l2:
        return True

    # Спільна основа прізвища (Шевченко / Шевченка, Бондар / Бондаря, Мельник / Мельника)
    min_len = min(len(l1), len(l2))
    if min_len >= 4:
        if l1.startswith(l2[:-1]) or l2.startswith(l1[:-1]):
            return True

    # Нечітке порівняння схожості прізвища
    ratio = difflib.SequenceMatcher(None, l1, l2).ratio()
    if ratio >= 0.82:
        return True

    return False


def is_same_student_identity(ln1: str, fn1: str, ln2: str, fn2: str) -> bool:
    """
    Визначає чи дві пари (Прізвище, Ім'я) належать одному і тому ж учню.
    Враховує прямий та зворотний порядок (Ім'я Прізвище).
    """
    # 1. Прямий порядок: Прізвище1 == Прізвище2 ТА Ім'я1 == Ім'я2
    if are_last_names_equivalent(ln1, ln2) and are_first_names_equivalent(fn1, fn2):
        return True

    # 2. Зворотний порядок: Прізвище1 == Ім'я2 ТА Ім'я1 == Прізвище2
    if are_last_names_equivalent(ln1, fn2) and are_first_names_equivalent(fn1, ln2):
        return True

    # 3. Якщо одне з імен відсутнє або одне слово містить обидва
    full1 = f"{ln1} {fn1}".strip().lower()
    full2 = f"{ln2} {fn2}".strip().lower()
    if full1 == full2:
        return True

    full2_rev = f"{fn2} {ln2}".strip().lower()
    if full1 == full2_rev:
        return True

    tokens1 = set(full1.split())
    tokens2 = set(full2.split())
    if tokens1 and tokens2 and tokens1 == tokens2:
        return True

    return False


def resolve_canonical_student_name(
    raw_full_name: str,
    class_group=None,
    existing_students: Optional[List[Tuple[str, str]]] = None
) -> Tuple[str, str]:
    """
    Аналізує введене учнем ім'я та зіставляє його з наявними учнями класу.
    
    Повертає канонічну пару (last_name, first_name) з великої літери.
    """
    clean_input = normalize_clean_string(raw_full_name)
    if not clean_input:
        return ("Учень", "")

    tokens = clean_input.split()
    if len(tokens) == 1:
        cand_last = tokens[0].capitalize()
        cand_first = ""
    else:
        # За замовчуванням перше слово - прізвище, решта - ім'я
        cand_last = tokens[0].capitalize()
        cand_first = " ".join(tokens[1:]).capitalize()

    # Отримуємо список існуючих учнів цього класу
    students_pool: List[Tuple[str, str]] = []
    if existing_students is not None:
        students_pool = existing_students
    elif class_group:
        try:
            from .models import Student, Submission
            # Спочатку шукаємо в базі учнів Student
            st_pool = Student.objects.filter(class_group=class_group).values_list('last_name', 'first_name')
            students_pool = [(ln.strip(), fn.strip()) for ln, fn in st_pool if ln.strip()]
            
            # Додаємо також здачі з Submission, якщо таких учнів ще немає в списку
            raw_pool = Submission.objects.filter(class_group=class_group).values_list('last_name', 'first_name').distinct()
            for ln, fn in raw_pool:
                ln_c = ln.strip()
                fn_c = fn.strip()
                if ln_c and (ln_c, fn_c) not in students_pool:
                    students_pool.append((ln_c, fn_c))
        except Exception:
            students_pool = []

    # Перевіряємо збіг серед існуючих учнів класу
    best_match: Optional[Tuple[str, str]] = None
    best_score = 0.0

    for ex_last, ex_first in students_pool:
        # Перевірка на точний або нечіткий збіг
        if is_same_student_identity(cand_last, cand_first, ex_last, ex_first):
            return (ex_last, ex_first)

        # Також перевіряємо, якщо введено навпаки (Ім'я Прізвище)
        if cand_first and is_same_student_identity(cand_first, cand_last, ex_last, ex_first):
            return (ex_last, ex_first)

    # Якщо збігу не знайдено — визначаємо чи перше слово є типовим іменем (наприклад "Тарас Шевченко")
    if len(tokens) >= 2:
        t0_lower = tokens[0].lower()
        t1_lower = tokens[1].lower()
        # Якщо перше слово є в базі імен, а друге - ні, міняємо місцями на (Прізвище, Ім'я)
        if t0_lower in _FIRST_NAME_INDEX and t1_lower not in _FIRST_NAME_INDEX:
            return (tokens[1].capitalize(), tokens[0].capitalize())

    return (cand_last, cand_first)


def cluster_submissions_by_student(submissions_list) -> List[Dict]:
    """
    Групує список здач робіт за інтелектуальним профілем учня.
    Об'єднує здачі з різним порядком слів, скороченими іменами та одруками в єдиний запис учня.
    """
    clusters: List[Dict] = []

    for sub in submissions_list:
        sub_last = sub.last_name.strip()
        sub_first = sub.first_name.strip()

        matched_cluster = None
        for cl in clusters:
            if is_same_student_identity(sub_last, sub_first, cl['last_name'], cl['first_name']):
                matched_cluster = cl
                break

        if not matched_cluster:
            # Створюємо новий кластер учня
            canonical_name = f"{sub_last} {sub_first}".strip()
            matched_cluster = {
                'last_name': sub_last,
                'first_name': sub_first,
                'full_name': canonical_name,
                'name_key': f"{sub_last}_{sub_first}",
                'submissions': [],
                'grades_by_date': {},
                'numeric_grades': [],
                'all_submissions_count': 0,
            }
            clusters.append(matched_cluster)

        matched_cluster['submissions'].append(sub)
        matched_cluster['all_submissions_count'] += 1

        date_key = sub.effective_grade_date
        if date_key not in matched_cluster['grades_by_date']:
            matched_cluster['grades_by_date'][date_key] = []

        gr_results = sub.get_ai_gr_results_list()
        gr_avg = sub.get_ai_gr_average()
        item = {
            'submission_id': sub.id,
            'grade': sub.grade or '',
            'has_grade': bool(sub.grade),
            'assignment_title': sub.assignment.title if sub.assignment else 'Завдання',
            'submitted_at': sub.submitted_at,
            'graded_at': sub.graded_at,
            'effective_date': date_key,
            'has_file': bool(sub.file),
            'has_link': bool(sub.link),
            'gr_results': gr_results,
            'gr_avg': gr_avg,
            'has_gr': bool(gr_results),
        }
        matched_cluster['grades_by_date'][date_key].append(item)

        if sub.grade and sub.grade.isdigit():
            matched_cluster['numeric_grades'].append(int(sub.grade))

    # Сортуємо учнів за алфавітом прізвища
    clusters.sort(key=lambda c: (c['last_name'].lower(), c['first_name'].lower()))
    return clusters


def normalize_ukrainian_name_declension(w: str) -> str:
    """
    Нормалізує форму імені чи прізвища з непрямих відмінків (зокрема орудного: ким? з ким?)
    до називного відмінка (наприклад: Максимом -> Максим, Тарасом -> Тарас, Шевченком -> Шевченко).
    """
    w = (w or '').strip()
    if not w:
        return ''
    lower = w.lower()

    subst = [
        ('енком', 'енко'), ('єнком', 'єнко'), ('чуком', 'чук'), ('щуком', 'щук'),
        ('овим', 'ов'), ('євим', 'єв'), ('евим', 'ев'),
        ('ським', 'ський'), ('цьким', 'цький'), ('зьким', 'зький'),
        ('ією', 'ія'), ('иєю', 'ия'), ('гою', 'га'), ('ною', 'на'), ('тою', 'та'),
        ('рою', 'ра'), ('лою', 'ла'), ('мою', 'ма'), ('вою', 'ва'), ('цою', 'ця'),
        ('ею', 'я'), ('єю', 'я'),
        ('тром', 'тро'), ('йлом', 'йло'), ('дром', 'др'), ('асом', 'ас'),
        ('аном', 'ан'), ('ієм', 'ій'), ('рієм', 'рій'), ('лем', 'ль'), ('зом', 'з'),
        ('сом', 'с'), ('мом', 'м'), ('ром', 'р'), ('ком', 'к')
    ]
    for suf, repl in subst:
        if lower.endswith(suf) and len(lower) >= len(suf) + 2:
            return (lower[:-len(suf)] + repl).capitalize()

    return w.capitalize()


def is_likely_first_name(word: str) -> bool:
    """Перевіряє, чи є слово відомим українським іменем."""
    if not word:
        return False
    w_norm = normalize_ukrainian_name_declension(word).lower()
    return w_norm in _FIRST_NAME_INDEX or word.lower() in _FIRST_NAME_INDEX


def extract_coauthors_from_comment(
    comment_text: str,
    class_group=None,
    exclude_last_name: str = '',
    exclude_first_name: str = ''
) -> List[Dict[str, Any]]:
    """
    Знаходить згадки співавторів (інших учнів) у коментарі до зданої роботи.
    Повертає список унікальних знайдених учнів (без повторень та без автора роботи).
    Кожен елемент: {'student': Student | None, 'first_name': str, 'last_name': str, 'full_name': str}
    """
    if not comment_text or not comment_text.strip():
        return []

    text = comment_text.strip()
    found_coauthors = []
    seen_identities = set()

    # Додаємо автора роботи до списку виключень
    if exclude_last_name:
        seen_identities.add(f"{exclude_last_name.strip().lower()}_{exclude_first_name.strip().lower()}")

    # 1. Якщо передано class_group, перевіряємо чи згадуються реальні учні цього класу
    if class_group:
        try:
            from .models import Student
            class_students = list(Student.objects.filter(class_group=class_group))
            lower_text = text.lower()

            for st in class_students:
                if is_same_student_identity(st.last_name, st.first_name, exclude_last_name, exclude_first_name):
                    continue

                st_key = f"{st.last_name.lower()}_{st.first_name.lower()}"
                if st_key in seen_identities:
                    continue

                ln = st.last_name.lower()
                fn = st.first_name.lower()

                ln_stem = ln[:-1] if len(ln) >= 5 else ln
                first_name_forms = _FIRST_NAME_INDEX.get(fn, {fn})

                has_last_name = bool(re.search(r'\b' + re.escape(ln_stem), lower_text, re.IGNORECASE))
                has_first_name = any(re.search(r'\b' + re.escape(form), lower_text, re.IGNORECASE) for form in first_name_forms)

                if has_last_name and has_first_name:
                    seen_identities.add(st_key)
                    found_coauthors.append({
                        'student': st,
                        'first_name': st.first_name,
                        'last_name': st.last_name,
                        'full_name': st.get_full_name(),
                    })
                elif has_last_name and any(kw in lower_text for kw in ['разом', 'викону', 'автор', 'група', 'парі', 'співавтор', 'робили', 'працювали']):
                    seen_identities.add(st_key)
                    found_coauthors.append({
                        'student': st,
                        'first_name': st.first_name,
                        'last_name': st.last_name,
                        'full_name': st.get_full_name(),
                    })
        except Exception:
            pass

    # 2. Інтелектуальний парсинг ключових фраз групової роботи
    keyword_patterns = [
        r'(?:(?:виконувал[иао]|виконал[иао]|працювал[иа]|робил[иа]|здавал[иа])\s*(?:разом|вдвох|в\s+парі|у\s+парі)?\s*(?:з|із|зі)?|'
        r'разом\s+(?:з|із|зі)|'
        r'спільно\s+(?:з|із|зі)|'
        r'вдвох\s+(?:з|із|зі)|'
        r'(?:у|в)\s+парі\s+(?:з|із|зі)|'
        r'разом[\s:]+|'
        r'співавтор[иів]*[\s:]+|'
        r'автор[иів]*\s*(?:роботи|проєкту|проекту)?[\s:]+|'
        r'учасник[иів]*[\s:]+)'
        r'[\s:]*([^\.\n;]+)',
    ]

    for pat in keyword_patterns:
        matches = re.finditer(pat, text, re.IGNORECASE)
        for m in matches:
            chunk = m.group(1).strip()
            names_raw = re.split(r'[,;]|\s+та\s+|\s+і\s+|\s+й\s+', chunk)
            for raw_n in names_raw:
                clean_words = [w for w in re.sub(r'[^\w\s]', '', raw_n).split() if w.isalpha()]
                if 2 <= len(clean_words) <= 3:
                    w1 = normalize_ukrainian_name_declension(clean_words[0])
                    w2 = normalize_ukrainian_name_declension(clean_words[1])

                    # Визначення порядку "Ім'я Прізвище" чи "Прізвище Ім'я"
                    if is_likely_first_name(clean_words[0]) and not is_likely_first_name(clean_words[1]):
                        fn, ln = w1, w2
                    else:
                        ln, fn = w1, w2

                    if is_same_student_identity(ln, fn, exclude_last_name, exclude_first_name):
                        continue

                    cand_key = f"{ln.lower()}_{fn.lower()}"
                    if cand_key not in seen_identities and not any(is_same_student_identity(ln, fn, fc['last_name'], fc['first_name']) for fc in found_coauthors):
                        seen_identities.add(cand_key)
                        found_coauthors.append({
                            'student': None,
                            'first_name': fn,
                            'last_name': ln,
                            'full_name': f"{ln} {fn}",
                        })

    return found_coauthors
