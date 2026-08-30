import difflib
import re
from django.db.models import Q
from .student_matcher import is_same_student_identity, are_first_names_equivalent, are_last_names_equivalent

LAYOUT_EN_TO_UA = str.maketrans(
    "qwertyuiop[]asdfghjkl;'zxcvbnm,./QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?",
    "йцукенгшщзхїфівапролджєячсмитьбю.ЙЦУКЕНГШЩЗХЇФІВАПРОЛДЖЄЯЧСМИТЬБЮ,"
)

def translate_en_to_ua(text: str) -> str:
    """Перекладає рядок, набраний помилково англійською розкладкою, на українську."""
    if not text:
        return ""
    return text.translate(LAYOUT_EN_TO_UA)


def normalize_text(text: str) -> str:
    if not text:
        return ""
    return text.strip().lower()


def is_fuzzy_match(query_str: str, candidate_str: str) -> bool:
    q = normalize_text(query_str)
    c = normalize_text(candidate_str)
    
    if not q or not c:
        return False
        
    # Substring match: query is inside candidate
    if q in c:
        return True
    if len(c) >= 3 and c in q and len(c) >= len(q) * 0.8:
        return True

    # Check with keyboard layout translation
    q_translated = q.translate(LAYOUT_EN_TO_UA)
    if q_translated in c:
        return True
    if len(c) >= 3 and c in q_translated and len(c) >= len(q_translated) * 0.8:
        return True

    # Overall similarity
    ratio = difflib.SequenceMatcher(None, q, c).ratio()
    ratio_tr = difflib.SequenceMatcher(None, q_translated, c).ratio()
    if max(ratio, ratio_tr) >= 0.72:
        return True

    # Token-level matching (multi-word names or titles)
    q_tokens = [t for t in q.split() if len(t) >= 2]
    c_tokens = [t for t in c.split() if len(t) >= 2]

    if not q_tokens or not c_tokens:
        return False

    token_matches = 0
    for qt in q_tokens:
        qt_tr = qt.translate(LAYOUT_EN_TO_UA)
        matched = False
        for ct in c_tokens:
            if qt in ct or ct in qt or qt_tr in ct or ct in qt_tr:
                matched = True
                break
            r = max(
                difflib.SequenceMatcher(None, qt, ct).ratio(),
                difflib.SequenceMatcher(None, qt_tr, ct).ratio()
            )
            if r >= 0.75:
                matched = True
                break
        if matched:
            token_matches += 1

    return token_matches == len(q_tokens)


def fuzzy_search_submissions(queryset, query_str: str):
    """
    Фільтрує QuerySet здач робіт за точним та нечітким пошуком з виправленням розкладки.
    При введенні Прізвища та Імені знаходить саме відповідного учня, а не всіх однофамільців.
    """
    if not query_str or not query_str.strip():
        return queryset

    q_clean = query_str.strip()
    q_translated = q_clean.translate(LAYOUT_EN_TO_UA)
    tokens_orig = q_clean.split()
    tokens_trans = q_translated.split()

    # ── 1. Якщо запит складається з 2+ слів (наприклад "Коваленко Тарас" або "Практична робота №1") ──
    if len(tokens_orig) >= 2:
        cand_last1, cand_first1 = tokens_orig[0], " ".join(tokens_orig[1:])
        cand_last2, cand_first2 = tokens_orig[-1], " ".join(tokens_orig[:-1])
        cand_last_tr1, cand_first_tr1 = tokens_trans[0], " ".join(tokens_trans[1:])
        cand_last_tr2, cand_first_tr2 = tokens_trans[-1], " ".join(tokens_trans[:-1])

        # Вимагаємо, щоб кожен токен запиту був присутній у записі
        # (в імені/прізвищі учня або в назві завдання)
        token_q = Q()
        for i, t in enumerate(tokens_orig):
            t_tr = tokens_trans[i] if i < len(tokens_trans) else t
            token_q &= (
                Q(last_name__icontains=t) |
                Q(first_name__icontains=t) |
                Q(last_name__icontains=t_tr) |
                Q(first_name__icontains=t_tr) |
                Q(assignment__title__icontains=t) |
                Q(assignment__title__icontains=t_tr) |
                Q(class_group__name__icontains=t)
            )

        sql_results = queryset.filter(token_q)
        sql_matched_ids = set(sql_results.values_list('id', flat=True))

        # Нечітке доповнення (для пестливих імен, відмінків та одруків)
        candidates = queryset.values('id', 'first_name', 'last_name', 'assignment__title')
        fuzzy_matched_ids = set()

        for item in candidates:
            if item['id'] in sql_matched_ids:
                continue

            sub_last = item['last_name'] or ''
            sub_first = item['first_name'] or ''
            sub_title = item['assignment__title'] or ''

            # Інтелектуальна перевірка особи учня (Прізвище + Ім'я)
            is_student_match = (
                is_same_student_identity(cand_last1, cand_first1, sub_last, sub_first) or
                is_same_student_identity(cand_last2, cand_first2, sub_last, sub_first) or
                is_same_student_identity(cand_last_tr1, cand_first_tr1, sub_last, sub_first) or
                is_same_student_identity(cand_last_tr2, cand_first_tr2, sub_last, sub_first)
            )
            if is_student_match:
                fuzzy_matched_ids.add(item['id'])
                continue

            # Перевірка назви завдання
            if sub_title and (is_fuzzy_match(q_clean, sub_title) or is_fuzzy_match(q_translated, sub_title)):
                fuzzy_matched_ids.add(item['id'])
                continue

        all_matched_ids = sql_matched_ids | fuzzy_matched_ids
        return queryset.filter(id__in=all_matched_ids)

    # ── 2. Якщо запит складається з одного слова (наприклад "Коваленко", "Тарас" або "Фізика") ──
    token = tokens_orig[0]
    token_tr = tokens_trans[0]

    exact_q = (
        Q(last_name__icontains=token) |
        Q(first_name__icontains=token) |
        Q(last_name__icontains=token_tr) |
        Q(first_name__icontains=token_tr) |
        Q(assignment__title__icontains=token) |
        Q(assignment__title__icontains=token_tr) |
        Q(class_group__name__icontains=token)
    )

    sql_results = queryset.filter(exact_q)
    sql_matched_ids = set(sql_results.values_list('id', flat=True))

    candidates = queryset.values('id', 'first_name', 'last_name', 'assignment__title')
    fuzzy_matched_ids = set()

    for item in candidates:
        if item['id'] in sql_matched_ids:
            continue

        sub_last = item['last_name'] or ''
        sub_first = item['first_name'] or ''
        sub_title = item['assignment__title'] or ''

        # Нечітке порівняння одного слова
        if (are_last_names_equivalent(token, sub_last) or
            are_last_names_equivalent(token_tr, sub_last) or
            are_first_names_equivalent(token, sub_first) or
            are_first_names_equivalent(token_tr, sub_first) or
            (sub_title and (is_fuzzy_match(token, sub_title) or is_fuzzy_match(token_tr, sub_title)))):
            fuzzy_matched_ids.add(item['id'])

    all_matched_ids = sql_matched_ids | fuzzy_matched_ids
    return queryset.filter(id__in=all_matched_ids)

