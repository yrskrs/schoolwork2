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
from django.utils import timezone

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
    Забезпечує правильну обробку повторних спроб (перездач):
    застарілі спроби без оцінки НЕ висять як неперевірені (⏳) у журналі.
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

    # Формуємо grades_by_date та numeric_grades для кожного учня з урахуванням перездач
    for cl in clusters:
        # Групуємо роботи учня за завданнями
        subs_by_assignment = {}
        for sub in cl['submissions']:
            asgn_key = sub.assignment_id if sub.assignment_id else f"none_{sub.id}"
            if asgn_key not in subs_by_assignment:
                subs_by_assignment[asgn_key] = []
            subs_by_assignment[asgn_key].append(sub)

        for asgn_key, asgn_subs in subs_by_assignment.items():
            # Визначаємо останню (найновішу) спробу
            latest_sub = None
            for s in asgn_subs:
                if getattr(s, 'is_latest_attempt', False):
                    latest_sub = s
                    break
            if not latest_sub:
                latest_sub = max(asgn_subs, key=lambda s: s.submitted_at if s.submitted_at else timezone.now())

            for s in asgn_subs:
                is_latest = (s.id == latest_sub.id)
                has_grade = bool(s.grade)

                # Головне правило: якщо спроба застаріла і НЕ має оцінки —
                # вона була замінена учнем новою спробою і НЕ повинна висіти в журналі як неперевірена (⏳)
                if not is_latest and not has_grade:
                    continue

                date_key = s.effective_grade_date
                if date_key not in cl['grades_by_date']:
                    cl['grades_by_date'][date_key] = []

                gr_results = s.get_ai_gr_results_list()
                gr_avg = s.get_ai_gr_average()
                item = {
                    'submission_id': s.id,
                    'grade': s.grade or '',
                    'has_grade': has_grade,
                    'is_latest_attempt': is_latest,
                    'is_superseded': not is_latest,
                    'resubmission_attempt': getattr(s, 'resubmission_attempt', 1),
                    'assignment_title': s.assignment.title if s.assignment else 'Завдання',
                    'submitted_at': s.submitted_at,
                    'graded_at': s.graded_at,
                    'effective_date': date_key,
                    'has_file': bool(s.file),
                    'has_link': bool(s.link),
                    'gr_results': gr_results,
                    'gr_avg': gr_avg,
                    'has_gr': bool(gr_results),
                }
                cl['grades_by_date'][date_key].append(item)

                # Враховуємо оцінку в середній бал (якщо це актуальна спроба або єдина з оцінкою)
                if s.grade and s.grade.isdigit():
                    latest_has_numeric = bool(latest_sub.grade and latest_sub.grade.isdigit())
                    if is_latest or not latest_has_numeric:
                        cl['numeric_grades'].append(int(s.grade))

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


def auto_bind_coauthors_from_comment(submission) -> List[Dict[str, Any]]:
    """
    Автоматично розпізнає співавторів у коментарі учня до зданої роботи (submission.comment_student),
    встановлює ознаку колективної роботи (is_group_work=True, group_authors) та створює зв'язані
    записи Submission для знайдених співавторів (якщо вони ще не створені).
    """
    if not submission or not submission.comment_student or not submission.comment_student.strip():
        return []

    from django.db.models import Q
    from .models import Submission, SubmissionFile, Student

    # Знаходимо згаданих у коментарі учнів
    coauthors = extract_coauthors_from_comment(
        submission.comment_student,
        class_group=submission.class_group,
        exclude_last_name=submission.last_name,
        exclude_first_name=submission.first_name
    )
    if not coauthors:
        return []

    # Формуємо актуальний список імен усіх авторів
    current_authors = [a.strip() for a in (submission.group_authors or '').split(',') if a.strip()]
    author_full = submission.get_student_full_name()
    if author_full and author_full not in current_authors:
        current_authors.append(author_full)

    for co in coauthors:
        co_full = co.get('full_name') or f"{co['last_name']} {co['first_name']}".strip()
        already_present = False
        for ca in current_authors:
            ca_ln, ca_fn = resolve_canonical_student_name(ca, submission.class_group)
            if is_same_student_identity(co['last_name'], co['first_name'], ca_ln, ca_fn):
                already_present = True
                break
        if not already_present and co_full:
            current_authors.append(co_full)

    new_group_authors = ", ".join(current_authors)

    fields_to_update = []
    if not submission.is_group_work:
        submission.is_group_work = True
        fields_to_update.append('is_group_work')
    if submission.group_authors != new_group_authors:
        submission.group_authors = new_group_authors
        fields_to_update.append('group_authors')

    if fields_to_update:
        submission.save(update_fields=fields_to_update)

    # Прив'язуємо / створюємо Submission для кожного знайденого співавтора
    for co in coauthors:
        co_st = co.get('student')
        co_ln = co['last_name']
        co_fn = co['first_name']

        if not co_st and submission.class_group:
            for s in Student.objects.filter(class_group=submission.class_group):
                if is_same_student_identity(co_ln, co_fn, s.last_name, s.first_name):
                    co_st = s
                    break
            if not co_st:
                co_st = Student.objects.create(
                    last_name=co_ln or "Учень",
                    first_name=co_fn,
                    class_group=submission.class_group
                )
            co['student'] = co_st

        # Перевіряємо чи вже є здача у цього співавтора
        existing_sub = None
        if co_st:
            existing_sub = Submission.objects.filter(
                assignment=submission.assignment,
                class_group=submission.class_group
            ).filter(
                Q(primary_submission=submission) |
                Q(student=co_st) |
                (Q(last_name__iexact=co_ln) & Q(first_name__iexact=co_fn))
            ).first()

        if not existing_sub:
            peer_comment = f"Колективна робота (спільно з {submission.get_student_full_name()}): {submission.comment_student.strip()}"
            peer_sub = Submission.objects.create(
                assignment=submission.assignment,
                student=co_st,
                last_name=co_ln,
                first_name=co_fn,
                class_group=submission.class_group,
                teacher=submission.teacher,
                file=submission.file,
                link=submission.link,
                comment_student=peer_comment,
                is_group_work=True,
                group_authors=new_group_authors,
                primary_submission=submission,
                is_latest_attempt=True,
            )
            # Копіюємо SubmissionFile
            for sf in submission.files.all():
                SubmissionFile.objects.create(
                    submission=peer_sub,
                    file=sf.file,
                    original_name=sf.original_name
                )

    return coauthors
