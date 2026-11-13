"""Deterministic post-processing guard for AI grading (Рівень 3, contract v2).

The model may praise a requirement that the server already proved absent — for
example an .xlsx workbook with zero formulas while the summary claims a correct
``IF`` function, or the Python file with a ``SyntaxError`` while strengths call
the code "working". Such contradictions are not a matter of model quality: the
technical passport is a deterministic fact, so the fabricated evidence must be
removed from the student-facing fields and the grade must respect a ceiling.

Contract v2 (see ``docs/audits/AI_EVIDENCE_GAPS_REVIEW_2026-11-13.md``):

* per-file evidence — an absence flag is suppressed when another verified
  passport of the same kind proves that the element exists (a mixed submission
  with an old manual workbook and a corrected one is not punished);
* claim cross-check — a function (IF/SUM/…) or a code construct (loop/function/
  branch) praised by the model must exist in the passport facts; otherwise the
  ``function_absent`` / ``construct_absent`` signal strips the claim and caps
  the grade;
* ``only_static_output`` only matters for algorithmic requirements, and
  ``no_code_blocks`` cap only for script/game/animation requirements;
* an unverifiable format (PDF/image/CSV/archive/legacy Office/unverified
  passport) cannot prove a formula/code/relationship: the claim is downgraded
  to ``unverifiable`` (never ``missing``) and the grade is capped at 9;
* proven absence (absence flag or a contradicted claim) turns criteria
  ``completed``/``partial``/``unverifiable`` into ``missing``;
* GR comments are cleaned by the same claim filter;
* if a fabricated claim was removed and no specific ceiling applies, the grade
  is capped at 9 (never "Високий" with disproved evidence);
* a teacher waiver in ``teacher_instructions``/``custom_criteria`` disables a
  specific ceiling (the fabricated praise is still removed);
* idempotent: a repeated call never duplicates remarks or lowers the grade again.
  If the passports are empty (or contain no gap evidence) the result is
  returned byte-for-byte unchanged (no false positives for correct work).
"""

import math
import re

# Grade ceilings applied only when the teacher's requirement really demanded
# the missing element. Kept in sync with the zero-evidence rules in
# ``feed/ai_context.py`` (ASSESSMENT_RULES / COMPACT_ASSESSMENT_RULES).
TECHNICAL_GRADE_CAPS = {
    'formulas_absent': 6,
    'syntax_error': 4,
    'only_static_output': 6,
    'all_slides_empty': 4,
    'too_few_slides': 6,
    'no_code_blocks': 4,
    'empty_document': 3,
    'short_document': 6,
    'no_code_constructs': 6,
    'function_absent': 6,
    'construct_absent': 6,
    'no_queries': 6,
    'no_relationships': 6,
    'implementation_unverified': 9,
    'too_few_slides_minor': 9,
}

# When several evidence flags are active the first one provides the factual
# summary/conclusion (the most specific observation wins).
_FLAG_PRIORITY = (
    'formulas_absent',
    'function_absent',
    'syntax_error',
    'construct_absent',
    'no_code_constructs',
    'only_static_output',
    'no_code_blocks',
    'empty_document',
    'all_slides_empty',
    'too_few_slides',
    'short_document',
    'no_queries',
    'no_relationships',
    'implementation_unverified',
)

# Flags that prove the element is really absent (criteria become ``missing``).
# Volume-only observations (too few slides, short document) are not proof of
# absence and must not rewrite criteria statuses.
_PROVEN_MISSING_FLAGS = (
    'formulas_absent',
    'function_absent',
    'syntax_error',
    'construct_absent',
    'no_code_constructs',
    'only_static_output',
    'no_code_blocks',
    'empty_document',
    'all_slides_empty',
    'no_queries',
    'no_relationships',
)

_LEVELS = (
    (10, 'Високий (10-12)'),
    (7, 'Достатній (7-9)'),
    (4, 'Середній (4-6)'),
    (1, 'Початковий (1-3)'),
)

_GENERIC_CEILING = 9

# Patterns that mark a sentence/remark as a factual statement about missing
# work. Negative markers are applied per clause (not per whole sentence), so
# "застосував IF, але оформлення потребує…" is still treated as praise of IF.
# Word boundaries matter: "правильне застосування" must not match the negation
# "не застос…".
_NEGATIVE_MARKERS = (
    r'відсут', r'\bнемає\b', r'\bнема\b', r'\bне\s+використ', r'\bне\s+застос',
    r'\bне\s+створ', r'\bне\s+реаліз', r'\bне\s+містить', r'\bне\s+викон',
    r'\bне\s+працю', r'\bне\s+запуск', r'\bне\s+надано', r'\bбез\s+жодн',
    r'\bне\s+підтверд', r'порожн', r'\bне\s+вдал', r'\bне\s+мож',
    r'\bне\s+сформул', r'\bне\s+продемонстр', r'\bне\s+виконано',
    r'\bне\s+має', r'\bбез\s+формул', r'\bне\s+знайдено', r'\bне\s+виявлен',
    r'\bне\s+встановлен', r'потребує', r'\bслід\s+додати', r'\bпотрібно\s+додати',
    r'\bдодай', r'\bдодайте', r'\bзастосуй', r'\bзастосуйте', r'\bвикористай',
    r'\bвикористайте', r'\bствори\b', r'\bстворіть\b', r'\bнапиши', r'\bнапишіть',
    r'\bзроби\b', r'\bзробіть\b', r'\bвстав\b', r'\bвставте\b', r'\bзаміни\b', r'\bзамініть\b',
    r'\bвиправ', r'\bперевір', r'\bрозшир', r'\bзамість',
    r'недостатн', r'\bне\s+повн', r'\bлише',
)

# Generic "everything is fine" phrasing that contradicts a confirmed absence
# when the teacher's requirement truly demanded the missing element.
_GENERIC_SUCCESS_MARKERS = (
    'успішно', 'повністю', 'в повному обсязі', 'у повному обсязі',
    'усіх вимог', 'всіх вимог', 'виконано правильно', 'без помилок',
    'завдання виконано', 'роботу виконано', 'повне розкриття', 'розкрито',
)

# ── Requirement model ──────────────────────────────────────────────────────
# Families are detected from the requirement text. Every element family maps to
# the passport kinds that can deterministically prove it.
_FAMILY_KINDS = {
    'formula': {'excel'},
    'code': {'python', 'code'},
    'slides': {'presentation'},
    'scratch': {'scratch'},
    'database': {'access'},
    # A plain-text essay (.txt/.md) is a verified text_document passport (WS3);
    # formula/code/scratch/database deliberately do NOT accept it as proof.
    'essay': {'document', 'text_document'},
}

# Families whose implementation can be hidden behind a non-auditable export.
# Essays and presentations are visible in a PDF/text preview, therefore they are
# intentionally excluded (a PDF essay is not an "unverifiable implementation").
_UNVERIFIED_APPLICABLE_FAMILIES = frozenset({'formula', 'code', 'scratch', 'database'})

_CODE_STRONG = re.compile(
    r'\bкод(?:у|ом|і|и)?\b|програм\w*|алгоритм\w*|\bpython\b|пітон\w*|\bjs\b|'
    r'javascript|\bscript\w*|скрипт\w*|\bцикл(?:у|и|ів|ом|ах|ами)?\b|розроб\w*',
    re.I,
)

# A mathematical "function" ("Побудувати графік функції y = 2x + 1") is not a
# programming task: the generic function→code heuristic must not fire for it.
_MATH_FUNCTION_CONTEXT = re.compile(
    r'графік\w*|рівнян\w*|рівняння|координат\w*|\by\s*=|f\s*\(\s*x\s*\)|'
    r'парабол\w*|гіпербол\w*|тригонометр\w*|монотонн\w*|нулі\s+функці',
    re.I,
)

# A formula requirement is either explicit ("з формулами", "=IF", "логічна
# функція") or a spreadsheet context combined with a known spreadsheet
# function. The words SUM/AVERAGE/RANK alone do not prove a spreadsheet task.
_FORMULA_EXPLICIT = re.compile(
    r'формул|=if|\bif\b|\bякщо\b|\bесли\b|обчисл|розрахун|'
    r'логічн\w*\s+функці|логічн\w*\s+вираз|автозаповн',
    re.I,
)
_FORMULA_FUNCTION_NAMES = re.compile(
    r'\bsum\b|\baverage\b|\brank\b|countif|sumif|vlookup|hlookup',
    re.I,
)
_SPREADSHEET_CONTEXT = re.compile(
    r'\bexcel\b|xlsx|spreadsheet|таблиц\w*|аркуш\w*|клітин\w*|комірк\w*',
    re.I,
)

_FAMILY_PATTERNS = {
    'formula': re.compile(
        r'формул|=if|\bif\b|\bякщо\b|\bесли\b|'
        r'логічн\w*\s+функці|логічн\w*\s+вираз|обчисл|розрахун',
        re.I,
    ),
    'code': re.compile(
        r'\bкод(?:у|ом|і|и)?\b|програм\w*|алгоритм\w*|\bpython\b|пітон\w*|'
        r'\bjs\b|javascript|\bscript\w*|скрипт\w*|\bцикл(?:у|и|ів|ом|ах|ами)?\b|розроб\w*|\bcode\b',
        re.I,
    ),
    'slides': re.compile(r'презентац\w*|\bслайд\w*|\bslide\w*|presentation', re.I),
    'scratch': re.compile(
        r'scratch|скретч|\bспрайт\w*|\bгра\b|\bгри\b|\bгру\b|\bгрі\b|грою|'
        r'анімац\w*|рух\w*|керуван\w*|\bgame\b|animation|sprite|поді[яй]\w*',
        re.I,
    ),
    'database': re.compile(
        r'\baccess\b|баз[аи]\s+даних|зв[’\'ʼ]?яз\w*|\bquery\b|relationship|'
        r'\bdatabase\b',
        re.I,
    ),
    'essay': re.compile(
        r'\bтвір\w*|\bесе\b|реферат\w*|доповід\w*|повідомлен\w*|\bessay\b|'
        r'\breport\b',
        re.I,
    ),
}

_FORMULA_STRONG = re.compile(
    r'\bexcel\b|xlsx|spreadsheet|таблиц\w*|аркуш\w*|клітин\w*|комірк\w*|=if|'
    r'\bsum\b|\brank\b|countif|sumif',
    re.I,
)

# The cap for ``no_code_blocks`` is meaningful only for a programming/scripting
# Scratch task, not for an art task ("create costumes for the sprites").
_SCRATCH_SCRIPT_REQUIREMENT = re.compile(
    r'скрипт\w*|програм\w*|алгоритм\w*|анімац\w*|\bгра\b|\bгри\b|\bгру\b|'
    r'\bгрі\b|грою|рух\w*|керуван\w*|поді[яй]\w*|\bgame\b|animation|'
    r'керувати',
    re.I,
)

# ``only_static_output`` is activated only by a genuinely algorithmic demand.
_ALGORITHMIC_STRONG = re.compile(
    r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|розгалуж\w*|алгоритм\w*|обчисл\w*|'
    r'\bсум[аиу]?\b|середн\w*|пораху\w*|знайд\w*|мінімум|максимум',
    re.I,
)

_ALGORITHMIC_SOFT = re.compile(r'функці\w*|умов\w*', re.I)

# ── Claim vocabulary ───────────────────────────────────────────────────────
# Every rule is a set of regex patterns: ``element`` (what the sentence is
# about), ``claim`` (what is asserted — normally the same as the element) and
# ``positive`` (praise words). A clause is fabricated praise when it is not
# negated and (claim + positive) match; for list items (strengths) the claim
# alone is enough.
_EVIDENCE_RULES = {
    'formulas_absent': {
        'element': r'формул|=if|\bif\b|\bякщо\b|\bесли\b|'
                   r'логічн\w*\s+функці|логічн\w*\s+вираз|'
                   r'\brank\b|countif|sumif|\bsum\b|обчисл|автозаповн',
        'claim': r'формул|=if|\bif\b|\bякщо\b|\bесли\b|'
                 r'логічн\w*\s+функці|логічн\w*\s+вираз|'
                 r'\brank\b|countif|sumif|\bsum\b|обчисл|автозаповн',
        'positive': r'застос|використ|виконан|застосан|створен|продемонструв|'
                    r'правильн|коректн|успішн|вдал|чудов|реаліз|зроблен|допомагає|'
                    r'працює|обчисл|визнач',
    },
    'function_absent': {
        'element': r'формул|=if|\bif\b|\bякщо\b|\bесли\b|'
                   r'логічн\w*\s+функці|логічн\w*\s+вираз|'
                   r'\brank\b|countif|sumif|\bsum\b|обчисл|автозаповн|функці',
        'claim': r'формул|=if|\bif\b|\bякщо\b|\bесли\b|'
                 r'логічн\w*\s+функці|логічн\w*\s+вираз|'
                 r'\brank\b|countif|sumif|\bsum\b|обчисл|автозаповн|функці',
        'positive': r'застос|використ|виконан|застосан|створен|продемонструв|'
                    r'правильн|коректн|успішн|вдал|чудов|реаліз|зроблен|допомагає|'
                    r'працює|обчисл|визнач',
    },
    'syntax_error': {
        'element': r'\bкод|програм|алгоритм|скрипт|python|пітон|застосун|консоль',
        'claim': r'працю|запуск|виконуєть|виконує|функціону|без помилок|працездат|'
                 r'результат виконання|обчислює|виводить|готов',
        'positive': r'коректн|правильн|чудов|відмінн|вдал|успішн|добре|'
                    r'структурован|логічн|якісн|повноцінн|працю|запуск|'
                    r'без помилок|складн',
    },
    'construct_absent': {
        'element': r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|розгалуж\w*|умов\w*|\bфункці|'
                   r'скрипт\w*|\bкод|програм\w*|алгоритм\w*',
        'claim': r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|розгалуж\w*|умов\w*|\bфункці|'
                 r'скрипт\w*|\bкод|програм\w*|алгоритм\w*',
        'positive': r'застос|використ|реаліз|створен|побудов|виконан|правильн|'
                    r'коректн|добре|чудов|складн|логічн|повноцінн|працю|функціону|'
                    r'обчисл',
    },
    'no_code_constructs': {
        'element': r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|розгалуж\w*|умов\w*|\bфункці|'
                   r'алгоритм\w*|повноцінн',
        'claim': r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|розгалуж\w*|умов\w*|\bфункці|'
                 r'алгоритм\w*|повноцінн',
        'positive': r'застос|використ|реаліз|створен|побудов|виконан|правильн|'
                    r'коректн|добре|чудов|складн|логічн|повноцінн|працю|обчисл',
    },
    'only_static_output': {
        'element': r'алгоритм\w*|\bцикл(?:у|и|ів|ом|ах|ами)?\b|розгалуж\w*|\bфункці|повноцінн',
        'claim': r'алгоритм\w*|\bцикл(?:у|и|ів|ом|ах|ами)?\b|розгалуж\w*|\bфункці|повноцінн|реаліз|'
                 r'використ|застос|побудов|працю|обчисл|складн',
        'positive': r'повноцінн|реаліз|використ|застос|побудов|створен|правильн|'
                    r'коректн|добре|чудов|складн|логічн|повн',
    },
    'no_code_blocks': {
        'element': r'\bгра\b|\bгрі\b|\bгру\b|\bгри\b|грою|анімац\w*|скрипт\w*|'
                   r'блок\w*\s+коду|програм\w*|алгоритм\w*|поді[яй]\w*|'
                   r'рухаєть|керуванн\w*',
        'claim': r'працю|грає|створен|реаліз|функціону|анімац|гра|цікав|запуска|'
                 r'керуванн|рухаєть|блок|скрипт',
        'positive': r'працю|створен|реаліз|цікав|чудов|вдал|добре|якісн|коректн|'
                    r'функціону|грає|повноцінн',
    },
    'empty_document': {
        'element': r'твір|текст|документ|зміст|робот|есе|повідомлен|реферат|'
                   r'доповід|аналіз',
        'claim': r'твір|текст|зміст|глибок|розкрит|повн|обґрунтован|'
                 r'структурован|документ',
        'positive': r'глибок|змістовн|повн|розкрит|цікав|чудов|вдал|структурован|'
                    r'обґрунтован|якісн|добре|вичерпн|детальн|написан',        'generic': True,
    },
    'all_slides_empty': {
        'element': r'слайд|презентац|візуальн|оформлен|наочн',
        'claim': r'слайд|презентац',
        'positive': r'повн|розкрит|вдал|чудов|коректн|якісн|структурован|цікав|'
                    r'наочн|заповн|детальн|вичерпн|створен',        'generic': True,
    },
    'too_few_slides': {
        'element': r'слайд|презентац',
        'claim': r'слайд|презентац|повн|розкрит|вичерпн|достатн|охопл|кожн|усі|'
                 r'всі|детальн',
        'positive': r'повн|розкрит|вдал|чудов|якісн|структурован|цікав|достатн|'
                    r'вичерпн|детальн|усі|всі|кожн',        'generic': True,
    },
    'short_document': {
        'element': r'твір|текст|документ|зміст|есе|повідомлен|реферат|доповід|'
                   r'аналіз',
        'claim': r'глибок|повн|розкрит|вичерпн|обґрунтован|змістовн|детальн|'
                 r'достатн|твір|есе|аналіз',
        'positive': r'глибок|змістовн|повн|розкрит|цікав|чудов|вдал|структурован|'
                    r'обґрунтован|якісн|добре|вичерпн|детальн|написан',        'generic': True,
    },
    'no_queries': {
        'element': r'запит\w*|\bquery\w*',
        'claim': r'запит\w*|\bquery\w*|створен|реаліз|використ|працю',
        'positive': r'створен|реаліз|використ|працю|коректн|правильн|чудов|якісн|'
                    r'добре',
    },
    'no_relationships': {
        'element': r'зв[’\'ʼ]?яз\w*|relationship',
        'claim': r'зв[’\'ʼ]?яз\w*|relationship|створен|реаліз|використ|працю',
        'positive': r'створен|реаліз|використ|працю|коректн|правильн|чудов|якісн|'
                    r'добре',
    },
}

# Claim vocabulary per unverifiable family (``implementation_unverified``).
_UNVERIFIED_FAMILY_TERMS = {
    'formula': r'формул|=if|\bif\b|логічн\w*\s+функці|\bфункці|обчисл|\bsum\b|'
               r'\brank\b|countif',
    'code': r'\bкод|програм\w*|алгоритм\w*|\bцикл(?:у|и|ів|ом|ах|ами)?\b|розгалуж\w*|\bфункці|'
            r'скрипт\w*|python|пітон',
    'slides': r'слайд|презентац',
    'scratch': r'\bгра\b|\bгри\b|\bгру\b|анімац\w*|скрипт\w*|\bспрайт\w*|scratch',
    'database': r'запит|зв[’\'ʼ]?яз|баз[аи]\s+даних|\baccess\b',
    'essay': r'\bтвір\w*|\bесе\b|реферат|доповід|повідомлен|текст|документ|аналіз',
}

_UNVERIFIED_POSITIVE = (
    r'застос|використ|створен|реаліз|виконан|правильн|коректн|успішн|вдал|'
    r'чудов|працю|розкрит|повн|глибок|цікав|ефективн|наочн|обґрунтован|'
    r'структурован'
)

_UNVERIFIED_MESSAGE = (
    'Заявлені елементи (формули/код/зв’язки) неможливо перевірити за наданим '
    'форматом; потрібна ручна перевірка.'
)

# ── Teacher waiver ─────────────────────────────────────────────────────────
# An explicit pedagogical waiver ("не знижуй за відсутність формул, оцінюй
# лише зміст") disables the ceiling for the corresponding flag. A waiver is
# clause-scoped: the marker and the element term must be in the SAME clause,
# otherwise "Достатньо однієї таблиці. Формули обовʼязкові." would silently
# cancel a formula ceiling. A clause that mandates the element ("обовʼязкові",
# "вимагається") never waives anything.
_WAIVER_CLAUSE_SPLIT = re.compile(r'[.!?;…\n]+|,\s+')
_WAIVER_MARKERS = re.compile(
    r'не\s+знижуй|не\s+знижувати|не\s+штрафуй|не\s+штрафувати|'
    r'не\s+враховуй|не\s+враховувати|не\s+оцінюй|не\s+оцінювати|'
    r'не\s+обов[’\'ʼ]?язков\w*|лише\s+зміст|тільки\s+зміст|'
    r'можна\s+без|дозволено\s+без',
    re.I,
)
# "достатньо" alone is not a waiver; only an explicit construction such as
# "достатньо змісту" / "достатньо без формул" counts.
_WAIVER_ENOUGH = re.compile(
    r'достатньо\s+(?:лише\s+|тільки\s+)?(?:зміст|без\s+)', re.I)
# A mandatory element can never be waived by the same clause.
_WAIVER_VETO = re.compile(
    r'(?<![а-яіїєґ])(?<!не\s)обов[’\'ʼ]?язков\w*|обовязков\w*|'
    r'вимага[єю]ть?ся',
    re.I,
)

_WAIVER_TERMS = {
    'formulas_absent': r'формул|=if|\bif\b|логічн|обчисл',
    'function_absent': r'формул|=if|\bif\b|логічн|обчисл',
    'syntax_error': r'\bкод|програм|алгоритм',
    'construct_absent': r'\bкод|програм|\bцикл|функці|розгалуж',
    'no_code_constructs': r'\bкод|програм|\bцикл|функці|розгалуж',
    'only_static_output': r'алгоритм|\bцикл|статичн|вивід',
    'no_code_blocks': r'scratch|скретч|блок|скрипт|анімац|\bгра\b|\bгру\b',
    'empty_document': r'твір|\bесе\b|текст|документ|обсяг',
    'all_slides_empty': r'слайд|презентац',
    'too_few_slides': r'слайд|презентац',
    'short_document': r'твір|\bесе\b|обсяг|слів|текст',
    'no_queries': r'запит|баз[аи]\s+даних',
    'no_relationships': r'зв[’\'ʼ]?яз|баз[аи]\s+даних',
    'implementation_unverified': r'перевір|аудит|формат|ручн',
}

# ── Passport helpers ───────────────────────────────────────────────────────
_CYRILLIC_UPPER = 'АБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ'


def _as_text(value):
    if value is None:
        return ''
    return str(value).strip()


def _norm(value):
    return _as_text(value).casefold()


def _to_int(value):
    try:
        number = int(float(str(value).replace(',', '.')))
    except (TypeError, ValueError):
        return None
    return number


def _int_fact(facts, key):
    if not isinstance(facts, dict):
        return 0
    return _to_int(facts.get(key)) or 0


def _level_for_grade(grade):
    """Deterministic NUS level from a numeric grade (mirrors gemini_service)."""
    text = _as_text(grade)
    if not text or text.casefold() == 'доопрацювати':
        return 'Початковий (1-3)'
    try:
        value = float(text.replace(',', '.'))
    except (TypeError, ValueError):
        return 'Початковий (1-3)'
    for threshold, label in _LEVELS:
        if value >= threshold:
            return label
    return 'Початковий (1-3)'


def _cap_grade(value, cap):
    """Return the grade string lowered to ``cap`` (non-numeric values untouched)."""
    text = _as_text(value)
    if not text or text.casefold() in ('доопрацювати', 'none', 'null', '—', '-'):
        return text
    try:
        number = float(text.replace(',', '.'))
    except (TypeError, ValueError):
        return text
    if not math.isfinite(number):
        return text
    return str(min(max(1, int(math.ceil(number))), int(cap)))


def _passport_kind(passport):
    return _norm(passport.get('kind'))


def _passport_facts(passport):
    facts = passport.get('facts')
    return facts if isinstance(facts, dict) else {}


def _audit_complete(passport):
    """A partially scanned passport must never prove an absence (FIX-3).

    WS3 emits ``facts['audit_complete'] = False`` for truncated scans (DoS
    limits). Counts from such a passport are partial facts: they may be shown
    as a factual limitation, but never used for a ceiling or a proven-missing
    criterion.
    """
    facts = _passport_facts(passport)
    return facts.get('audit_complete') is not False


def _passport_absence(passport):
    absence = passport.get('absence')
    if not isinstance(absence, (list, tuple)):
        return set()
    return {_as_text(flag) for flag in absence if _as_text(flag)}


def _verified_passports(passports):
    if not isinstance(passports, (list, tuple)):
        return []
    return [item for item in passports
            if isinstance(item, dict) and item.get('verified') is not False]


def _excel_formulas_total(facts):
    total = _int_fact(facts, 'formulas_count')
    for key in ('cell_formulas_count', 'conditional_formulas_count',
                'data_validation_formulas_count', 'defined_name_formulas_count'):
        total += _int_fact(facts, key)
    return total


def _has_excel_formulas(passport):
    facts = _passport_facts(passport)
    if _excel_formulas_total(facts) > 0:
        return True
    functions = facts.get('functions')
    return bool(isinstance(functions, (list, tuple)) and functions)


def _code_construct_total(passport):
    facts = _passport_facts(passport)
    return (_int_fact(facts, 'loops') + _int_fact(facts, 'functions')
            + _int_fact(facts, 'branches'))


def _has_code_constructs(passport):
    return _code_construct_total(passport) > 0


def _syntax_ok(passport):
    facts = _passport_facts(passport)
    return facts.get('syntax_ok') is True


def _has_scratch_blocks(passport):
    return _int_fact(_passport_facts(passport), 'total_code_blocks') > 0


def _presentation_has_content(passport):
    facts = _passport_facts(passport)
    slides = _int_fact(facts, 'slides_count')
    empty = _int_fact(facts, 'empty_slides')
    return slides > 0 and empty < slides


def _document_has_content(passport):
    facts = _passport_facts(passport)
    if _int_fact(facts, 'words_count') > 0:
        return True
    return any(_int_fact(facts, key) > 0 for key in ('tables', 'images', 'charts'))


def _access_has_queries(passport):
    return _int_fact(_passport_facts(passport), 'queries_count') > 0


def _access_has_relationships(passport):
    return _int_fact(_passport_facts(passport), 'relationships_count') > 0


# flag → (kinds that can prove the element exists, predicate on such passport)
_SUPPRESSION = {
    'formulas_absent': (frozenset({'excel'}), _has_excel_formulas),
    'syntax_error': (frozenset({'python', 'code'}), _syntax_ok),
    'only_static_output': (frozenset({'python', 'code'}), _has_code_constructs),
    'no_code_constructs': (frozenset({'python', 'code'}), _has_code_constructs),
    'no_code_blocks': (frozenset({'scratch'}), _has_scratch_blocks),
    'all_slides_empty': (frozenset({'presentation'}), _presentation_has_content),
    'empty_document': (frozenset({'document'}), _document_has_content),
    'no_queries': (frozenset({'access'}), _access_has_queries),
    'no_relationships': (frozenset({'access'}), _access_has_relationships),
}

# ── Requirement analysis ───────────────────────────────────────────────────
_UK_SLIDE_NUMBER_STEMS = (
    ('дванадцят', 12), ('одинадцят', 11), ('десят', 10), ('девят', 9),
    ('дев’ят', 9), ("дев'ят", 9), ('вісім', 8), ('вісем', 8), ('сім', 7),
    ('шіст', 6), ('пят', 5), ('пять', 5), ('чотир', 4), ('три', 3), ('вос', 8),
    ('дв', 2),
)


def _slide_number_word(word):
    word = _norm(word)
    for stem, number in _UK_SLIDE_NUMBER_STEMS:
        if word.startswith(stem):
            return number
    return None


def _requirement_families(requirement_text):
    """Detect which element families the teacher's requirement demands."""
    text = _norm(requirement_text)
    if not text:
        return set()
    families = set()
    for family, pattern in _FAMILY_PATTERNS.items():
        if pattern.search(text):
            families.add(family)
    if 'formula' not in families:
        # SUM/AVERAGE/RANK are only formula evidence in a spreadsheet context.
        if _SPREADSHEET_CONTEXT.search(text) and _FORMULA_FUNCTION_NAMES.search(text):
            families.add('formula')
    elif _SPREADSHEET_CONTEXT.search(text) or _FORMULA_EXPLICIT.search(text):
        families.add('formula')
    strong_code = bool(_CODE_STRONG.search(text))
    # A bare "IF" in a programming task describes a construct, not a formula.
    if 'code' in families and 'formula' in families:
        spreadsheet = bool(_SPREADSHEET_CONTEXT.search(text)
                           or re.search(r'формул|=if|excel|spreadsheet', text))
        if not spreadsheet:
            families.discard('formula')
    # A generic "function" task is a coding task, but only when the task is not
    # actually a spreadsheet formula task or a mathematical function exercise.
    if 'code' not in families and 'formula' not in families and 'scratch' not in families:
        if (re.search(r'\bфункці\w*', text)
                and not _MATH_FUNCTION_CONTEXT.search(text)):
            families.add('code')
    # "create a game in Scratch" is not a Python/code task; "presentation about
    # Scratch" is not a Scratch product task.
    if 'scratch' in families and not strong_code:
        families.discard('code')
    if 'slides' in families:
        families.discard('scratch')
    # "write an essay about formulas" is an essay task, not a spreadsheet task.
    if 'essay' in families and 'formula' in families:
        if not _FORMULA_STRONG.search(text) and not strong_code:
            families.discard('formula')
    return families


def _expected_slides(requirement_text):
    text = _norm(requirement_text)
    match = re.search(r'(\d+)\s*(?:[-–—]\s*(?:\d+|[а-яіїєґ\']+)\s+)?(?:слайд|slide)', text)
    if match:
        return _to_int(match.group(1))
    match = re.search(
        r'([а-яіїєґ\']+)\s*(?:[-–—]\s*(?:\d+|[а-яіїєґ\']+)\s+)?(?:слайд|slide)', text)
    if match:
        return _slide_number_word(match.group(1))
    return None


def _algorithmic_requirement(requirement_text, families):
    if _ALGORITHMIC_STRONG.search(requirement_text):
        return True
    if 'code' in families and _ALGORITHMIC_SOFT.search(requirement_text):
        return True
    return False


# Which evidence family every absence flag belongs to (used to decide whether
# the teacher's custom_criteria really demands that family, P1-4).
_FLAG_FAMILIES = {
    'formulas_absent': 'formula',
    'function_absent': 'formula',
    'syntax_error': 'code',
    'construct_absent': 'code',
    'no_code_constructs': 'code',
    'only_static_output': 'code',
    'no_code_blocks': 'scratch',
    'all_slides_empty': 'slides',
    'too_few_slides': 'slides',
    'empty_document': 'essay',
    'short_document': 'essay',
    'no_queries': 'database',
    'no_relationships': 'database',
}


def _flag_required(flag, families, requirement_text):
    if flag in ('formulas_absent', 'function_absent'):
        return 'formula' in families
    if flag in ('syntax_error', 'construct_absent', 'no_code_constructs'):
        return 'code' in families
    if flag in ('all_slides_empty', 'too_few_slides'):
        return 'slides' in families
    if flag == 'no_code_blocks':
        return bool(_SCRATCH_SCRIPT_REQUIREMENT.search(requirement_text or ''))
    if flag in ('empty_document', 'short_document'):
        return 'essay' in families
    if flag == 'only_static_output':
        return _algorithmic_requirement(requirement_text, families)
    if flag in ('no_queries', 'no_relationships'):
        return 'database' in families
    if flag == 'implementation_unverified':
        return True
    return False


# ── Text helpers ───────────────────────────────────────────────────────────
_CLAUSE_SPLIT = re.compile(r'\s*(?:,\s*)?(?:але|проте|однак|хоча)\s+', re.I)
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?…])\s+|\n+')


def _iter_clauses(text):
    for sentence in _SENTENCE_SPLIT.split(_as_text(text)):
        sentence = sentence.strip()
        if not sentence:
            continue
        for clause in _CLAUSE_SPLIT.split(sentence):
            clause = clause.strip(' ,;:')
            if clause:
                yield clause


def _claim_norm(value):
    """Normalize claim wording before matching (IF-логіка → функція if…)."""
    low = _norm(value)
    low = re.sub(r'\bif\s*[-\s]?\s*логік\w*', 'функція if', low)
    low = re.sub(r'логічн\w*\s+вираз', 'функція if', low)
    low = re.sub(r'функці\w*\s+(?:якщо|если)', 'функція if', low)
    low = re.sub(r'\bфункція\s*\(?\s*if\s*\)?', 'функція if', low)
    return low


def _is_negated(low):
    return any(re.search(marker, low) for marker in _NEGATIVE_MARKERS)


def _hit(low, pattern):
    if not pattern:
        return False
    return bool(re.search(pattern, low))


def _matches_claim(text, flags, rules, strength=False):
    """True when the text praises/claims an element proved absent."""
    for clause in _iter_clauses(text):
        low = _claim_norm(clause)
        if not low or _is_negated(low):
            continue
        for flag in flags:
            rule = rules.get(flag)
            if not rule:
                continue
            element_hit = _hit(low, rule.get('element'))
            positive_hit = _hit(low, rule.get('positive'))
            # The element must be named explicitly: a generic verb such as
            # "створено" is not enough (it would remove praise about costumes in
            # a Scratch art task or about a template table). Volume rules keep
            # their tailored praise phrase ("повне розкриття теми") because such
            # a claim is directly contradicted by a 1-slide/empty file.
            if element_hit and positive_hit:
                return True
            if rule.get('generic') and positive_hit and _hit(low, rule.get('claim')):
                return True
    return False


def _element_flag(text, flags, rules):
    """Return the first active flag whose element is described by the text."""
    for clause in _iter_clauses(text):
        low = _claim_norm(clause)
        if not low or _is_negated(low):
            continue
        for flag in flags:
            rule = rules.get(flag)
            if rule and _hit(low, rule.get('element')):
                return flag
    return None


def _element_flag_ignoring_negation(text, flags, rules):
    """Match an element even inside an absence statement.

    A proven-absent element can be named in a negative sentence ("у роботі не
    знайдено підтвердження функції RANK") that the deterministic audit itself
    already confirms. This fallback is used only for criteria whose status is
    already ``unverifiable`` and only for proven-missing flags, so it cannot
    turn a formatting remark into a fabricated requirement failure.
    """
    for clause in _iter_clauses(text):
        low = _claim_norm(clause)
        if not low:
            continue
        for flag in flags:
            rule = rules.get(flag)
            if rule and _hit(low, rule.get('element')):
                return flag
    return None


def _clean_list(items, flags, rules):
    cleaned = []
    changed = False
    for item in items or []:
        text = _as_text(item)
        if text and _matches_claim(text, flags, rules, strength=True):
            changed = True
            continue
        cleaned.append(text)
    return cleaned, changed


def _clean_sentences(text, flags, rules):
    raw = _as_text(text)
    if not raw:
        return '', False
    clauses = list(_iter_clauses(raw))
    kept = [clause for clause in clauses if not _matches_claim(clause, flags, rules)]
    removed = len(kept) != len(clauses)
    if not kept:
        return '', removed
    joined = ' '.join(kept).strip()
    if joined:
        joined = joined[0].upper() + joined[1:]
        if joined[-1] not in '.!?…':
            joined += '.'
    return joined, removed


def _add_adjustment(adjustments, field, before, after, reason):
    if str(before) != str(after):
        adjustments.append({'field': field, 'before': before, 'after': after, 'reason': reason})


def _cap_numeric_list(values):
    output = []
    for value in values or []:
        try:
            output.append(float(value))
        except (TypeError, ValueError):
            continue
    return output


# ── Function / construct claim detection ──────────────────────────────────
_FUNCTION_PATTERNS = {
    'IF': re.compile(r'\bIF\b|\bIf\b|\bif\s*\(|\bякщо\s*\(|'
                     r'(?:функці\w*|логічн\w*)\s+(?:якщо|если)\b|'
                     r'\bЯКЩО\b|\bЕСЛИ\b|if\s*-\s*логік'),
    'SUM': re.compile(r'\bSUM\b|\bsum\s*\('),
    'SUMIF': re.compile(r'\bSUMIF\b|\bsumif\s*\('),
    'SUMIFS': re.compile(r'\bSUMIFS\b'),
    'COUNTIF': re.compile(r'\bCOUNTIF\b'),
    'COUNTIFS': re.compile(r'\bCOUNTIFS\b'),
    'RANK': re.compile(r'\bRANK(?:\.EQ)?\b'),
    'VLOOKUP': re.compile(r'\bVLOOKUP\b'),
    'HLOOKUP': re.compile(r'\bHLOOKUP\b'),
    'XLOOKUP': re.compile(r'\bXLOOKUP\b'),
    'AVERAGE': re.compile(r'\bAVERAGE\b'),
    'MAX': re.compile(r'\bMAX\b'),
    'MIN': re.compile(r'\bMIN\b'),
    'ROUND': re.compile(r'\bROUND\b'),
    'AND': re.compile(r'\bAND\s*\('),
    'OR': re.compile(r'\bOR\s*\('),
    'NOT': re.compile(r'\bNOT\s*\('),
    'TEXT': re.compile(r'\bTEXT\s*\('),
    'MOD': re.compile(r'\bMOD\s*\('),
    'IFERROR': re.compile(r'\bIFERROR\b'),
    'INDEX': re.compile(r'\bINDEX\s*\('),
    'MATCH': re.compile(r'\bMATCH\s*\('),
}

_CONSTRUCT_PATTERNS = {
    'loops': re.compile(r'\bцикл(?:у|и|ів|ом|ах|ами)?\b|\bfor\b|\bwhile\b|повторен\w*'),
    'functions': re.compile(r'функці(?!\w*\s+if\b)\w*|\bdef\b|\bfunction\b'),
    'branches': re.compile(r'розгалуж\w*|умовн\w*|умов[аиу]\b|\bswitch\b|\bcase\b'),
}

_CONSTRUCT_LABELS = {
    'loops': 'цикли/повторення',
    'functions': 'функції',
    'branches': 'розгалуження/умови',
}


def _mentioned_functions(text):
    found = set()
    for clause in _iter_clauses(text):
        low = _claim_norm(clause)
        if not low or _is_negated(low):
            continue
        for name, pattern in _FUNCTION_PATTERNS.items():
            if pattern.search(clause) or pattern.search(low):
                found.add(name)
    return found


def _normalize_function_name(value):
    name = _as_text(value).upper().replace(' ', '')
    name = name.replace('()', '')
    if name.endswith('.EQ') or name.endswith('.AVG'):
        name = name.rsplit('.', 1)[0]
    return name


def _available_functions(passports):
    available = set()
    for passport in passports:
        functions = _passport_facts(passport).get('functions')
        if not isinstance(functions, (list, tuple)):
            continue
        for function in functions:
            name = _normalize_function_name(function)
            if name:
                available.add(name)
    return available


def _mentioned_constructs(text):
    claimed = set()
    for clause in _iter_clauses(text):
        low = _claim_norm(clause)
        if not low or _is_negated(low):
            continue
        for kind, pattern in _CONSTRUCT_PATTERNS.items():
            if pattern.search(low):
                claimed.add(kind)
    return claimed


def _available_constructs(passports):
    available = {'loops': 0, 'functions': 0, 'branches': 0}
    for passport in passports:
        for kind in available:
            available[kind] += _int_fact(_passport_facts(passport), kind)
    return available


def _collect_claim_text(result, additions):
    parts = []
    for key in ('summary', 'grade_explanation', 'feedback_comment'):
        parts.append(_as_text(additions.get(key)))
    for item in additions.get('strengths') or []:
        parts.append(_as_text(item))
    for row in result.get('criteria_results') or []:
        if isinstance(row, dict):
            for key in ('criterion', 'title', 'evidence', 'comment', 'recommendation'):
                parts.append(_as_text(row.get(key)))
    for task in result.get('tasks_evaluated') or []:
        if isinstance(task, dict):
            for key in ('task_title', 'title', 'comment'):
                parts.append(_as_text(task.get(key)))
    for row in additions.get('clean_gr_results') or []:
        if isinstance(row, dict):
            parts.append(_as_text(row.get('comment')))
    return ' . '.join(part for part in parts if part)


# ── Signal collection ──────────────────────────────────────────────────────
def _suppressed(flag, verified):
    check = _SUPPRESSION.get(flag)
    if not check:
        return False
    kinds, predicate = check
    return any(_passport_kind(item) in kinds and predicate(item) for item in verified)


def _passport_signals(passports, requirement_text, families):
    """Collect confirmed passport evidence (per-file, with suppression)."""
    signals = {}
    verified = _verified_passports(passports)
    requirement = _norm(requirement_text)
    for passport in verified:
        kind = _passport_kind(passport)
        facts = dict(_passport_facts(passport))
        file_name = _as_text(passport.get('file'))
        absence_flags = _passport_absence(passport)
        if not _audit_complete(passport):
            # Partial scan: no proven absence, only factual limitations.
            continue
        for flag in _FLAG_PRIORITY:
            if flag not in absence_flags:
                continue
            if flag not in _SUPPRESSION and flag not in (
                    'only_static_output', 'too_few_slides', 'short_document',
                    'empty_document', 'all_slides_empty'):
                # unknown/derived absence flags are handled below
                continue
            if flag == 'only_static_output':
                if not _algorithmic_requirement(requirement_text, families):
                    continue
            if flag == 'short_document':
                continue  # derived below
            if flag == 'too_few_slides':
                continue  # derived below
            if _suppressed(flag, verified):
                continue
            signals.setdefault(flag, []).append(
                {'file': file_name, 'kind': kind, 'facts': facts})

    # Presentation volume: a number in the requirement is authoritative; without
    # a number only a single slide is obviously too few. The best (largest)
    # presentation represents the submission.
    if 'all_slides_empty' not in signals:
        presentations = [item for item in verified
                         if _passport_kind(item) == 'presentation'
                         and _audit_complete(item)]
        expected = _expected_slides(requirement_text)
        candidates = []
        for passport in presentations:
            facts = _passport_facts(passport)
            slides = _int_fact(facts, 'slides_count')
            if slides <= 0:
                continue
            too_few = bool(expected and slides < expected) or (
                slides <= 1 and 'slides' in families)
            if too_few:
                candidates.append((slides, passport, dict(facts)))
        if candidates:
            slides, passport, facts = max(candidates, key=lambda item: item[0])
            if expected:
                ratio = slides / float(expected)
                cap = 6 if ratio < 0.5 else TECHNICAL_GRADE_CAPS['too_few_slides_minor']
            else:
                cap = TECHNICAL_GRADE_CAPS['too_few_slides']
            signals['too_few_slides'] = [{
                'file': _as_text(passport.get('file')),
                'kind': 'presentation',
                'facts': dict(facts, slides_count=slides, expected_slides=expected),
                'cap': cap,
            }]

    # Written volume: only for a written composition, never for a data-analysis
    # report that proves its content with tables/images/charts.
    if 'empty_document' not in signals:
        # Volume ceilings (short_document) stay for rich documents only: a
        # plain-text essay is a verified text_document (essay family supported,
        # FIX-3) but a short .txt note must not be capped by a paragraph count.
        documents = [item for item in verified
                     if _passport_kind(item) == 'document' and _audit_complete(item)]
        written_genre = 'essay' in families or bool(
            re.search(r'сторінк|слів|обсяг', requirement))
        if written_genre and documents:
            adequate = False
            candidates = []
            for passport in documents:
                facts = _passport_facts(passport)
                words = _int_fact(facts, 'words_count')
                has_objects = any(_int_fact(facts, key) > 0
                                  for key in ('tables', 'images', 'charts'))
                if words >= 60 or has_objects:
                    adequate = True
                elif 0 < words < 60:
                    candidates.append((words, passport, dict(facts)))
            if candidates and not adequate:
                words, passport, facts = max(candidates, key=lambda item: item[0])
                signals['short_document'] = [{
                    'file': _as_text(passport.get('file')),
                    'kind': 'document',
                    'facts': dict(facts, words_count=words),
                }]

    # Implementation evidence: a required family with no verified passport able
    # to prove it (PDF export, image, CSV, archive, legacy Office, unverified
    # passport) cannot be validated deterministically.
    #
    # P1-3: strictly per-family. A verified helper.py does NOT close a formula
    # gap of an unauditable PDF scan. The only "exception" is implicit and
    # legitimate: the family is supported when a verified passport of exactly
    # that family's kind exists (code family + verified python, formula family
    # + verified excel, …). No other implementation file can suppress the gap.
    if passports:
        verified_kinds = {_passport_kind(item) for item in verified}
        unverified_families = set()
        for family in families:
            if family not in _UNVERIFIED_APPLICABLE_FAMILIES:
                continue
            if not (verified_kinds & _FAMILY_KINDS[family]):
                unverified_families.add(family)
        if unverified_families:
            signals['implementation_unverified'] = [{
                'file': '',
                'kind': '',
                'facts': {},
                'families': sorted(unverified_families),
            }]
    return signals


def _claim_signals(result, additions, verified, families):
    """Cross-check functions/constructs claimed by the model against facts."""
    signals = {}
    claim_text = _collect_claim_text(result, additions)
    if not claim_text:
        return signals

    excel_passports = [item for item in verified if _passport_kind(item) == 'excel']
    mentioned_functions = _mentioned_functions(claim_text)
    # A truncated workbook scan cannot prove that a function is absent: the
    # function may live in the unscanned part (FIX-3 audit_complete).
    excel_complete = bool(excel_passports) and all(
        _audit_complete(item) for item in excel_passports)
    if excel_complete and mentioned_functions:
        available = _available_functions(excel_passports)
        missing = sorted(mentioned_functions - available)
        if missing:
            signals['function_absent'] = [{
                'file': ', '.join(_as_text(item.get('file')) for item in excel_passports
                                  if _as_text(item.get('file'))),
                'kind': 'excel',
                'facts': {},
                'functions': missing,
                'available': sorted(available),
            }]

    code_passports = [item for item in verified
                      if _passport_kind(item) in ('python', 'code')]
    code_complete = bool(code_passports) and all(
        _audit_complete(item) for item in code_passports)
    if code_complete and 'code' in families:
        claimed = _mentioned_constructs(claim_text)
        if claimed:
            available = _available_constructs(code_passports)
            missing = sorted(kind for kind in claimed if not available.get(kind))
            if missing:
                signals['construct_absent'] = [{
                    'file': ', '.join(_as_text(item.get('file')) for item in code_passports
                                      if _as_text(item.get('file'))),
                    'kind': 'code',
                    'facts': {},
                    'constructs': missing,
                    'available': available,
                }]
    return signals


def _waived_flags(flags, waiver_text):
    """Flags explicitly waived by the teacher, clause by clause.

    The marker ("не знижуй…", "достатньо без…", "не обовʼязково") and the
    element term must be in the same clause; a clause with a mandate
    ("обовʼязкові", "вимагається") can never waive its own element.
    """
    waived = []
    text = _as_text(waiver_text)
    if not text:
        return waived
    clauses = []
    for clause in _WAIVER_CLAUSE_SPLIT.split(text):
        clause = _norm(clause).strip(' -—:')
        if clause:
            clauses.append(clause)
    for flag in flags:
        terms = _WAIVER_TERMS.get(flag)
        if not terms:
            continue
        for clause in clauses:
            if _WAIVER_VETO.search(clause):
                continue
            if not (_WAIVER_MARKERS.search(clause)
                    or _WAIVER_ENOUGH.search(clause)):
                continue
            if re.search(terms, clause):
                waived.append(flag)
                break
    return waived


# ── Factual wording ────────────────────────────────────────────────────────
def _join_names(values):
    values = [str(value) for value in values or [] if value]
    if not values:
        return '—'
    if len(values) == 1:
        return values[0]
    return ', '.join(values[:-1]) + ' і ' + values[-1]


def _fact_texts(flag, entry, required):
    """Factual Ukrainian wording for one active evidence flag.

    ``required`` selects the strict variant ("вимогу не виконано"), otherwise a
    neutral factual conclusion is used: a workbook without formulas is not a
    defect when the teacher never asked for formulas.
    """
    facts = entry.get('facts') or {}
    requirement = entry.get('requirement') or ''

    if flag == 'formulas_absent':
        marking = any(keyword in requirement for keyword in
                      ('маркуван', 'колір', 'світлофор', 'парт'))
        required_summary = (
            'Таблицю створено, проте формули не використано '
            '(значення маркування введено вручну статичним текстом).'
            if marking else
            'Таблицю створено, проте формули не використано '
            '(значення введено вручну статичним текстом).')
        return {
            'summary_required': required_summary,
            'summary_neutral': ('У наданому файлі не виявлено заявлених формул; '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': ('У наданому файлі немає жодної формули: значення введено '
                                  'вручну статичним текстом, тому вимогу щодо формул або '
                                  'логічних функцій не виконано.'),
            'weakness': ('Відсутність формул у наданому файлі (лише статичні дані): '
                         'обчислення за допомогою формул не виконано.'),
            'weakness_marker': 'відсутність формул',
            'feedback': ('У файлі немає жодної формули: значення введено вручну статичним '
                         'текстом. Додай у клітинки формули (наприклад, =IF(...)), щоб '
                         'таблиця обчислювала результат автоматично.'),
            'feedback_marker': 'немає жодної формули',
            'action': 'Вставити формули (наприклад, =IF(...)) замість статичних значень.',
            'short': 'формули відсутні, дані введено статично',
            'gr_comment': 'Фактично: у файлі немає формул, значення введено статично.',
        }
    if flag == 'function_absent':
        missing = _join_names(entry.get('functions'))
        available = _join_names(entry.get('available'))
        available_note = (f'; наявні функції: {available}'
                          if entry.get('available') else '')
        return {
            'summary_required': (f'У наданому файлі немає заявленої функції {missing}'
                                 f'{available_note}: твердження про її застосування '
                                 'не підтверджено.'),
            'summary_neutral': (f'У наданому файлі не виявлено заявленої функції '
                                f'{missing}; оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': (f'Заявленої функції {missing} у файлі немає'
                                  f'{available_note}, тому твердження про її '
                                  'застосування не відповідає вмісту файлу.'),
            'weakness': (f'Заявленої функції {missing} у наданому файлі немає'
                         f'{available_note}: твердження про її застосування не '
                         'підтверджено.'),
            'weakness_marker': 'заявленої функції',
            'feedback': (f'У файлі немає функції {missing}{available_note}. Застосуй саме '
                         'цю функцію у формулах таблиці.'),
            'feedback_marker': 'немає функції',
            'action': f'Застосувати у файлі заявлену функцію {missing}.',
            'short': f'заявленої функції {missing} немає',
            'gr_comment': f'Фактично: заявленої функції {missing} у файлі немає.',
        }
    if flag == 'syntax_error':
        return {
            'summary_required': ('Код подано, проте він містить синтаксичну помилку і не '
                                 'запускається — результат виконання не підтверджено.'),
            'summary_neutral': ('У наданому коді виявлено синтаксичну помилку; оцінка '
                                'спирається на фактичний вміст роботи.'),
            'grade_explanation': ('Код містить синтаксичну помилку і не може бути '
                                  'запущений, тому роботу зараховано лише частково.'),
            'weakness': ('Код містить синтаксичну помилку і не запускається: '
                         'результат виконання програми не підтверджено.'),
            'weakness_marker': 'синтаксичну помилку',
            'feedback': ('Код не запускається через синтаксичну помилку. Виправ помилку '
                         '(перевір дужки, двокрапки та відступи) і перевір запуск '
                         'програми перед здачею.'),
            'feedback_marker': 'через синтаксичну помилку',
            'action': 'Знайти й виправити синтаксичну помилку та перевірити запуск коду.',
            'short': 'код містить синтаксичну помилку і не запускається',
            'gr_comment': 'Фактично: код не запускається через синтаксичну помилку.',
        }
    if flag == 'construct_absent':
        missing = _join_names([_CONSTRUCT_LABELS.get(kind, kind)
                               for kind in entry.get('constructs') or []])
        return {
            'summary_required': (f'У коді немає заявлених конструкцій ({missing}): '
                                 'твердження про їх реалізацію не підтверджено.'),
            'summary_neutral': (f'У коді не виявлено заявлених конструкцій ({missing}); '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': (f'У коді немає заявлених конструкцій ({missing}), тому '
                                  'твердження про їх реалізацію не відповідає вмісту '
                                  'файлу.'),
            'weakness': (f'У коді немає заявлених конструкцій ({missing}): заявлений '
                         'алгоритм не реалізовано.'),
            'weakness_marker': 'немає заявлених конструкцій',
            'feedback': (f'У коді немає заявлених конструкцій ({missing}). Реалізуй їх, '
                         'щоб програма виконувала потрібні дії.'),
            'feedback_marker': 'немає заявлених конструкцій',
            'action': f'Реалізувати заявлені конструкції: {missing}.',
            'short': f'немає заявлених конструкцій ({missing})',
            'gr_comment': f'Фактично: у коді немає заявлених конструкцій ({missing}).',
        }
    if flag == 'no_code_constructs':
        return {
            'summary_required': ('Код не містить циклів, розгалужень і функцій: заявлений '
                                 'алгоритм не реалізовано.'),
            'summary_neutral': ('У наданому коді не виявлено конструкцій алгоритму; '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': ('У коді немає циклів, розгалужень і функцій, тому '
                                  'заявлений алгоритм не реалізовано.'),
            'weakness': ('У коді немає циклів, розгалужень і функцій: заявлений алгоритм '
                         'не реалізовано.'),
            'weakness_marker': 'немає циклів',
            'feedback': ('У коді немає циклів, розгалужень і функцій. Додай справжній '
                         'алгоритм обробки даних.'),
            'feedback_marker': 'немає циклів',
            'action': 'Додати цикли, розгалуження та функції для реалізації алгоритму.',
            'short': 'у коді немає циклів, розгалужень і функцій',
            'gr_comment': 'Фактично: у коді немає циклів, розгалужень і функцій.',
        }
    if flag == 'only_static_output':
        return {
            'summary_required': ('Код містить лише статичний вивід: алгоритм (цикли, '
                                 'розгалуження, функції) не реалізовано.'),
            'summary_neutral': ('У наданому коді виявлено лише статичний вивід; оцінка '
                                'спирається на фактичний вміст роботи.'),
            'grade_explanation': ('Програма лише виводить статичний текст: у ній немає '
                                  'циклів, розгалужень і функцій, тобто алгоритму, який '
                                  'вимагало завдання.'),
            'weakness': ('Подано лише статичний вивід print(...): немає циклів, '
                         'розгалужень і функцій, тобто справжнього алгоритму.'),
            'weakness_marker': 'лише статичний',
            'feedback': ('Твій код лише виводить статичний текст. Додай справжній алгоритм: '
                         'цикли, умови (if) і функції, які обробляють дані.'),
            'feedback_marker': 'лише виводить статичний текст',
            'action': 'Реалізувати алгоритм: цикли, умовні розгалуження та функції.',
            'short': 'лише статичний вивід без алгоритму',
            'gr_comment': 'Фактично: код містить лише статичний вивід.',
        }
    if flag == 'no_code_blocks':
        return {
            'summary_required': ('Scratch-проєкт не містить жодного блоку коду: гра/анімація '
                                 'фактично не працює.'),
            'summary_neutral': ('У наданому Scratch-проєкті не виявлено блоків коду; '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': ('У проєкті Scratch немає жодного блоку коду, тому '
                                  'запланована гра/анімація не функціонує.'),
            'weakness': ('У проєкті Scratch немає жодного блоку коду: спрайти залишилися '
                         'без скриптів.'),
            'weakness_marker': 'немає жодного блоку коду',
            'feedback': ('У проєкті немає блоків коду. Додай скрипти для спрайтів '
                         '(події, рух, цикли), щоб гра або анімація працювала.'),
            'feedback_marker': 'немає блоків коду',
            'action': 'Додати скрипти (блоки коду) для спрайтів проєкту.',
            'short': 'у проєкті немає жодного блоку коду',
            'gr_comment': 'Фактично: у Scratch-проєкті немає жодного блоку коду.',
        }
    if flag == 'empty_document':
        return {
            'summary_required': ('Документ не містить змістовного тексту: завдання фактично '
                                 'не виконано.'),
            'summary_neutral': ('У наданому документі не виявлено змістовного тексту; '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': ('Документ порожній: змістовного тексту немає, тому '
                                  'вимогу щодо письмової роботи не виконано.'),
            'weakness': ('Документ не містить змістовного тексту (лише порожні '
                         'сторінки або бланк).'),
            'weakness_marker': 'не містить змістовного тексту',
            'feedback': ('Документ порожній. Напиши текст за темою: вступ, основна '
                         'частина з аргументами та висновок.'),
            'feedback_marker': 'документ порожній',
            'action': 'Написати змістовний текст за темою у документі.',
            'short': 'документ не містить змістовного тексту',
            'gr_comment': 'Фактично: документ не містить змістовного тексту.',
        }
    if flag == 'all_slides_empty':
        return {
            'summary_required': ('Презентацію створено, проте всі слайди порожні: зміст теми '
                                 'не розкрито.'),
            'summary_neutral': ('У наданій презентації всі слайди порожні; оцінка '
                                'спирається на фактичний вміст роботи.'),
            'grade_explanation': ('Усі слайди презентації порожні (без тексту й '
                                  'зображень), тому тему не розкрито.'),
            'weakness': ('Усі слайди презентації порожні (немає тексту й зображень): '
                         'тему не розкрито.'),
            'weakness_marker': 'слайди презентації порожні',
            'feedback': ('Слайди презентації порожні. Додай на кожен слайд текст і '
                         'зображення або схеми за темою.'),
            'feedback_marker': 'слайди презентації порожні',
            'action': 'Заповнити слайди текстом і зображеннями за темою.',
            'short': 'усі слайди презентації порожні',
            'gr_comment': 'Фактично: усі слайди презентації порожні.',
        }
    if flag == 'too_few_slides':
        slides = _to_int(facts.get('slides_count')) or 0
        expected = _to_int(facts.get('expected_slides')) or 0
        if expected:
            weakness = (f'Презентація містить лише {slides} слайд(ів), тоді як вимога '
                        f'передбачає {expected}.')
            feedback = (f'Додай слайди до {expected} (у роботі лише {slides}): розкрий '
                        'тему повніше.')
            short = f'лише {slides} слайд(ів) замість {expected}'
            marker = f'лише {slides} слайд'
        else:
            weakness = (f'Презентація містить лише {slides} слайд(ів): обсягу '
                        'недостатньо для розкриття теми.')
            feedback = (f'У презентації лише {slides} слайд(ів). Додай слайди, щоб '
                        'розкрити тему повніше.')
            short = f'лише {slides} слайд(ів)'
            marker = f'лише {slides} слайд'
        return {
            'summary_required': weakness,
            'summary_neutral': (f'У презентації лише {slides} слайдів; оцінка спирається '
                                'на фактичний вміст роботи.'),
            'grade_explanation': weakness,
            'weakness': weakness,
            'weakness_marker': marker,
            'feedback': feedback,
            'feedback_marker': 'додай слайди',
            'action': 'Додати слайди й розкрити тему відповідно до вимоги.',
            'short': short,
            'gr_comment': f'Фактично: {short}.',
        }
    if flag == 'short_document':
        words = _to_int(facts.get('words_count')) or 0
        return {
            'summary_required': (f'Документ містить лише {words} слів: обсягу недостатньо '
                                 'для розкриття теми.'),
            'summary_neutral': (f'Документ містить лише {words} слів; оцінка спирається на '
                                'фактичний вміст роботи.'),
            'grade_explanation': (f'Обсяг роботи — лише {words} слів (менше 60): тему '
                                  'розкрито поверхово.'),
            'weakness': (f'Обсяг документа — лише {words} слів (менше 60): тема '
                         'розкрита поверхово.'),
            'weakness_marker': 'менше 60',
            'feedback': (f'Текст містить лише {words} слів. Розшир роботу: вступ, '
                         'аргументи, приклади та висновок.'),
            'feedback_marker': 'слів. Розшир',
            'action': ('Розширити текст до змістовного обсягу (вступ, аргументи, '
                       'приклади, висновок).'),
            'short': f'лише {words} слів замість змістовного обсягу',
            'gr_comment': f'Фактично: обсяг документа — лише {words} слів.',
        }
    if flag == 'no_queries':
        return {
            'summary_required': ('Базу даних створено, проте в ній немає жодного запиту: '
                                 'завдання щодо запитів не виконано.'),
            'summary_neutral': ('У наданій базі даних не виявлено запитів; оцінка '
                                'спирається на фактичний вміст роботи.'),
            'grade_explanation': ('У базі даних немає жодного запиту, тому вимогу щодо '
                                  'запитів не виконано.'),
            'weakness': 'У базі даних немає жодного запиту.',
            'weakness_marker': 'немає жодного запиту',
            'feedback': ('У базі даних немає запитів. Створи запити (SELECT/UPDATE тощо) '
                         'відповідно до завдання.'),
            'feedback_marker': 'немає запитів',
            'action': 'Створити запити в базі даних.',
            'short': 'у базі даних немає жодного запиту',
            'gr_comment': 'Фактично: у базі даних немає жодного запиту.',
        }
    if flag == 'no_relationships':
        return {
            'summary_required': ('Базу даних створено, проте в ній немає зв’язків між '
                                 'таблицями: вимогу щодо зв’язків не виконано.'),
            'summary_neutral': ('У наданій базі даних не виявлено зв’язків між таблицями; '
                                'оцінка спирається на фактичний вміст роботи.'),
            'grade_explanation': ('У базі даних немає жодного зв’язку між таблицями, тому '
                                  'вимогу щодо зв’язків не виконано.'),
            'weakness': 'У базі даних немає зв’язків між таблицями.',
            'weakness_marker': 'немає зв’язків',
            'feedback': ('У базі даних немає зв’язків між таблицями. Створи зв’язки між '
                         'таблицями за ключовими полями.'),
            'feedback_marker': 'немає зв’язків',
            'action': 'Створити зв’язки між таблицями бази даних.',
            'short': 'у базі даних немає зв’язків між таблицями',
            'gr_comment': 'Фактично: у базі даних немає зв’язків між таблицями.',
        }
    if flag == 'implementation_unverified':
        if entry.get('no_evidence'):
            # P0-1: no files at all (comment/link only) — the claim is simply
            # unsupported, not hidden behind a blind format.
            no_evidence_message = (
                'Заявлені елементи (формули/код/зв’язки) не підтверджені жодним '
                'перевіреним файлом; потрібна ручна перевірка.')
            return {
                'summary_required': no_evidence_message,
                'summary_neutral': no_evidence_message,
                'grade_explanation': no_evidence_message,
                'weakness': ('Доказів немає: заявлені елементи (формули/код/зв’язки) не '
                             'підтверджені жодним перевіреним файлом.'),
                'weakness_marker': 'доказів немає',
                'feedback': ('Заявлені елементи (формули/код/зв’язки) не підтверджені '
                             'жодним перевіреним файлом. Додай файл роботи, щоб учитель '
                             'міг перевірити їх.'),
                'feedback_marker': 'не підтверджені',
                'action': ('Додати файл роботи у форматі, який підлягає '
                           'детермінованому аудиту.'),
                'short': 'доказів немає — заявлені елементи не підтверджені файлом',
                'gr_comment': ('Фактично: заявлені елементи не підтверджені жодним '
                               'перевіреним файлом; потрібна ручна перевірка.'),
            }
        return {
            'summary_required': _UNVERIFIED_MESSAGE,
            'summary_neutral': _UNVERIFIED_MESSAGE,
            'grade_explanation': _UNVERIFIED_MESSAGE,
            'weakness': ('Заявлені елементи (формули/код/зв’язки) неможливо перевірити за '
                         'наданим форматом: потрібна ручна перевірка вчителя.'),
            'weakness_marker': 'неможливо перевірити',
            'feedback': ('Заявлені елементи (формули/код/зв’язки) неможливо перевірити за '
                         'наданим форматом. Учитель перевірить їх вручну.'),
            'feedback_marker': 'неможливо перевірити',
            'action': 'Надати файл у форматі, який підлягає детермінованому аудиту, або '
                      'дочекатися ручної перевірки.',
            'short': ('заявлені елементи неможливо перевірити за наданим форматом'),
            'gr_comment': ('Фактично: заявлені елементи неможливо перевірити за наданим '
                           'форматом; потрібна ручна перевірка.'),
        }
    return {
        'summary_required': '', 'summary_neutral': '', 'grade_explanation': '',
        'weakness': '', 'weakness_marker': '', 'feedback': '', 'feedback_marker': '',
        'action': '', 'short': '', 'gr_comment': '',
    }


def _implementation_rule(families):
    terms = [_UNVERIFIED_FAMILY_TERMS[family] for family in families
             if family in _UNVERIFIED_FAMILY_TERMS]
    element = '|'.join(f'(?:{term})' for term in terms) if terms else r'$^'
    return {'element': element, 'claim': element, 'positive': _UNVERIFIED_POSITIVE}


def _entry_cap(flag, entry):
    entry_cap = entry.get('cap') if isinstance(entry, dict) else None
    if entry_cap:
        return int(entry_cap)
    return TECHNICAL_GRADE_CAPS.get(flag)


# ── Main entry point ───────────────────────────────────────────────────────
def apply_technical_evidence_guard(
    result: dict,
    passports: list,
    requirement_text: str,
    suggested_grade: str,
    level: str,
    clean_gr_results: list,
    numeric_gr_grades: list,
    avg_gr_grade,
    summary: str,
    strengths: list,
    weaknesses: list,
    feedback_comment: str,
    teacher_instructions: str = '',
    custom_criteria: str = '',
    evidence_profile=None,
) -> dict:
    """Remove fabricated praise for absent elements and apply evidence ceilings.

    ``result`` is the parsed model JSON. It is mutated in place for
    ``criteria_results``, ``tasks_evaluated`` and ``tasks_completed_count``.
    ``grade_explanation`` is read from ``result`` and returned corrected.
    The remaining values are returned; a repeat call is idempotent.

    ``evidence_profile`` (optional dict from the integration, FIX-2a):
    ``{'expects_artifact': bool, 'has_files': bool, 'has_link': bool,
    'has_comment': bool, 'task_type': str, 'deliverable_type': str}``.
    When the submission has no verified files at all but the task expects a
    technical artifact, a no-evidence ``implementation_unverified`` signal is
    formed (cap 9, criteria unverifiable, never blocked). Without the profile
    (older callers) an empty passport list stays a no-op.
    """
    result = result if isinstance(result, dict) else {}
    additions = {
        'suggested_grade': _as_text(suggested_grade),
        'level': _as_text(level),
        'clean_gr_results': list(clean_gr_results) if isinstance(clean_gr_results, list) else [],
        'numeric_gr_grades': list(numeric_gr_grades) if isinstance(numeric_gr_grades, list) else [],
        'avg_gr_grade': avg_gr_grade,
        'summary': _as_text(summary),
        'strengths': list(strengths) if isinstance(strengths, list) else [],
        'weaknesses': list(weaknesses) if isinstance(weaknesses, list) else [],
        'feedback_comment': _as_text(feedback_comment),
        'grade_explanation': _as_text(result.get('grade_explanation')),
    }
    adjustments = []

    families = _requirement_families(requirement_text)
    verified = _verified_passports(passports)
    signals = _passport_signals(passports, requirement_text, families)
    profile = evidence_profile if isinstance(evidence_profile, dict) else None
    if (profile and profile.get('expects_artifact') and not passports
            and 'implementation_unverified' not in signals):
        # P0-1: a submission without any verified passport cannot prove a
        # technical element. That covers comment-only / link-only work AND a
        # visible file that yields no passport (e.g. an extensionless text/code
        # file — after FIX-3 every ordinary text format has a verified
        # text_document, hidden formats have an unverified stub, so an empty
        # passport list on a technical task means there is no auditable
        # artifact). Form the same unverified signal as for a blind format
        # (cap 9, criteria unverifiable, needs_teacher_review). Essay/oral
        # tasks are protected by expects_artifact=False.
        technical_families = {family for family in families
                              if family in _UNVERIFIED_APPLICABLE_FAMILIES}
        if technical_families:
            signals['implementation_unverified'] = [{
                'file': '',
                'kind': '',
                'facts': {},
                'families': sorted(technical_families),
                'no_evidence': True,
            }]
    claim_signals = _claim_signals(result, additions, verified, families)
    for flag, entries in claim_signals.items():
        signals.setdefault(flag, []).extend(entries)

    flags = [flag for flag in _FLAG_PRIORITY if flag in signals]
    if not flags and not families:
        # No verified absence evidence and no requirement context: nothing to do.
        return dict(additions, adjustments=[], applied=False,
                    active_flags=[], waived_flags=[])
    if not flags:
        # The requirement is known but no file proves (or disproves) anything.
        return dict(additions, adjustments=[], applied=False,
                    active_flags=[], waived_flags=[])

    requirement = _norm(requirement_text)
    required_flags = [flag for flag in flags
                      if _flag_required(flag, families, requirement_text)]
    waiver_text = ' '.join(filter(None, [_as_text(teacher_instructions),
                                        _as_text(custom_criteria)]))
    waived = set(_waived_flags(flags, waiver_text))
    waived_flags = [flag for flag in flags if flag in waived]

    # Record the explicit teacher waiver once (the second call must be a no-op).
    waiver_key = 'technical_evidence_waived_flags'
    already_waived = set(result.get(waiver_key) or [])
    for flag in waived_flags:
        if flag in required_flags and flag not in already_waived:
            _add_adjustment(adjustments, 'waiver', flag, 'cap_waived_by_teacher',
                            'technical_evidence_waived_by_teacher')
            already_waived.add(flag)
    if already_waived:
        marker = sorted(already_waived)
        if result.get(waiver_key) != marker:
            result[waiver_key] = marker

    rules = dict(_EVIDENCE_RULES)
    unverified_families = set()
    for entry in signals.get('implementation_unverified') or []:
        unverified_families.update(entry.get('families') or [])
    rules['implementation_unverified'] = _implementation_rule(
        unverified_families or families)

    texts = {}
    for flag in flags:
        entry = dict(signals[flag][0]) if signals.get(flag) else {}
        entry['requirement'] = requirement
        for family in entry.get('families') or []:
            entry['requirement'] = (entry['requirement'] + ' ' +
                                    _UNVERIFIED_FAMILY_TERMS.get(family, '')).strip()
        texts[flag] = _fact_texts(flag, entry, required=flag in required_flags)

    # Specific ceilings are meaningful only when the teacher's requirement
    # really asked for the element; praise removal below is unconditional.
    #
    # P1-4: a teacher-authored custom scale (e.g. 4/4/4 instead of the NUS
    # 12-point scale) must not be silently cut to 6 by the technical guard.
    # When custom_criteria exists and does not itself demand the evidence
    # family, the ceiling becomes the general "not Високий" 9. If the custom
    # criteria explicitly demand the family, the stricter ceiling stays.
    custom_text = _as_text(custom_criteria)
    custom_families = _requirement_families(custom_text) if custom_text else set()

    def _effective_cap(flag):
        value = _entry_cap(flag, signals[flag][0]) or 99
        if custom_text and flag not in waived:
            family = _FLAG_FAMILIES.get(flag)
            if (family and family not in custom_families
                    and value < _GENERIC_CEILING):
                return _GENERIC_CEILING
        return value

    cap_flags = [flag for flag in required_flags if flag not in waived]
    base_cap = min(_effective_cap(flag) for flag in cap_flags) if cap_flags else None
    if base_cap == 99:
        base_cap = None
    cap_reason = ('technical_evidence_ceiling:' + ','.join(cap_flags)
                  if cap_flags else '')

    # ── 1. Strengths: remove fabricated praise of a missing element ────────
    clean_strengths, strengths_changed = _clean_list(additions['strengths'], flags, rules)
    if strengths_changed:
        _add_adjustment(adjustments, 'strengths',
                        '; '.join(additions['strengths']), '; '.join(clean_strengths),
                        'technical_evidence: fabricated_praise_removed')
    additions['strengths'] = clean_strengths

    # ── 2. Clean summary / explanation / feedback sentence by sentence ─────
    clean_summary, summary_removed = _clean_sentences(additions['summary'], flags, rules)
    clean_explanation, explanation_removed = _clean_sentences(
        additions['grade_explanation'], flags, rules)
    clean_feedback, feedback_removed = _clean_sentences(
        additions['feedback_comment'], flags, rules)

    # ── 3. Criteria: proven absence → missing, unverifiable → unverifiable ─
    criteria_rows = result.get('criteria_results')
    criteria_changed = False
    if isinstance(criteria_rows, list):
        proven = [flag for flag in flags if flag in _PROVEN_MISSING_FLAGS]
        downgrade_statuses = ('completed', 'done', 'виконано', 'так', 'partial',
                              'частково', 'unverifiable', 'неперевірено')
        for row in criteria_rows:
            if not isinstance(row, dict):
                continue
            status = _norm(row.get('status'))
            claim = ' '.join(_as_text(row.get(key)) for key in
                             ('criterion', 'title', 'evidence', 'comment'))
            # The criterion/title alone identifies the row. The evidence is
            # deliberately excluded: the earlier downgrade_unsupported_task_
            # claims guard replaces evidence with a negative sentence ("не
            # знайдено підтвердження функції RANK"), and a negation marker in
            # that sentence used to hide a proven absence from this guard.
            identity = ' '.join(_as_text(row.get(key)) for key in
                                ('criterion', 'title'))
            matched = _element_flag(identity, proven, rules) if proven else None
            if (not matched and proven
                    and status in ('unverifiable', 'неперевірено')):
                # Fallback only for rows already marked unverifiable: the
                # passport proved the element absent, so a negative evidence
                # sentence is a confirmation, not a contradiction.
                matched = _element_flag_ignoring_negation(
                    _as_text(row.get('evidence')), proven, rules)
            if matched and status in downgrade_statuses:
                row['status'] = 'missing'
                row['evidence'] = f"Фактично: {texts[matched]['short']}."
                row['recommendation'] = texts[matched]['action']
                row['technical_evidence_flag'] = matched
                criteria_changed = True
                _add_adjustment(adjustments, 'criteria_results',
                                _as_text(row.get('criterion')) or 'criterion',
                                f'missing ({matched})',
                                'technical_evidence:criterion_missing')
                continue
            if ('implementation_unverified' in flags
                    and status in downgrade_statuses):
                unverified_match = _element_flag(claim, ['implementation_unverified'], rules)
                if unverified_match:
                    row['status'] = 'unverifiable'
                    # P1-1: the format-gap marker lets assessment_policy keep a
                    # numeric capped grade (cap 9) instead of blocking the whole
                    # work because "too many" rows are unverifiable.
                    row['format_gap'] = True
                    row['evidence'] = f"Фактично: {texts['implementation_unverified']['short']}."
                    row['recommendation'] = texts['implementation_unverified']['action']
                    row['technical_evidence_flag'] = 'implementation_unverified'
                    criteria_changed = True
                    _add_adjustment(adjustments, 'criteria_results',
                                    _as_text(row.get('criterion')) or 'criterion',
                                    'unverifiable (implementation_unverified)',
                                    'technical_evidence:criterion_unverifiable')
                    continue
            if status != 'missing':
                for key in ('evidence', 'comment'):
                    before_text = _as_text(row.get(key))
                    cleaned, removed = _clean_sentences(before_text, flags, rules)
                    if removed:
                        row[key] = cleaned
                        criteria_changed = True
                        _add_adjustment(adjustments, f'criteria_results.{key}',
                                        before_text, cleaned,
                                        'technical_evidence:fabricated_praise_removed')

    # ── 4. Tasks: completed → not_completed when the element is proven absent
    task_rows = result.get('tasks_evaluated')
    tasks_changed = False
    if isinstance(task_rows, list) and task_rows:
        proven = [flag for flag in flags if flag in _PROVEN_MISSING_FLAGS]
        for task in task_rows:
            if not isinstance(task, dict):
                continue
            status = _norm(task.get('status'))
            claim = ' '.join(_as_text(task.get(key)) for key in
                             ('task_title', 'title', 'comment'))
            if status in ('completed', 'done', 'виконано', 'так'):
                matched = _element_flag(claim, proven, rules) if proven else None
                if matched:
                    task['status'] = 'not_completed'
                    factual_comment = f"Фактично: {texts[matched]['short']}."
                    if _norm(factual_comment) not in _norm(task.get('comment')):
                        task['comment'] = ' '.join(
                            filter(None, [_as_text(task.get('comment')), factual_comment]))
                    task['technical_evidence_flag'] = matched
                    tasks_changed = True
                    _add_adjustment(adjustments, 'tasks_evaluated',
                                    _as_text(task.get('task_title')) or 'task',
                                    f'not_completed ({matched})',
                                    'technical_evidence:task_not_completed')
                    continue
            comment = task.get('comment')
            cleaned, removed = _clean_sentences(comment, flags, rules)
            if removed:
                task['comment'] = cleaned
                tasks_changed = True
                _add_adjustment(adjustments, 'tasks_evaluated.comment',
                                _as_text(comment), cleaned,
                                'technical_evidence:fabricated_praise_removed')
        completed_count = sum(
            1 for task in task_rows
            if isinstance(task, dict)
            and _norm(task.get('status')) in ('completed', 'done', 'виконано', 'так'))
        before_count = result.get('tasks_completed_count')
        if before_count != completed_count:
            result['tasks_completed_count'] = completed_count
            _add_adjustment(adjustments, 'tasks_completed_count', before_count,
                            completed_count, 'technical_evidence:tasks_completed_count')
        elif 'tasks_completed_count' not in result:
            result['tasks_completed_count'] = completed_count

    # ── 5. GR comments: the same claim filter as the main fields ───────────
    gr_comment_changed = False
    gr_rows = additions['clean_gr_results']
    for row in gr_rows:
        if not isinstance(row, dict):
            continue
        comment = row.get('comment')
        if not _as_text(comment):
            continue
        cleaned, removed = _clean_sentences(comment, flags, rules)
        if not removed:
            continue
        if not cleaned:
            cleaned = texts[flags[0]]['gr_comment']
        row['comment'] = cleaned
        gr_comment_changed = True
        _add_adjustment(adjustments, 'gr_comment', _as_text(comment), cleaned,
                        'technical_evidence:fabricated_praise_removed')

    claim_removed = bool(
        strengths_changed or summary_removed or explanation_removed
        or feedback_removed or criteria_changed or tasks_changed or gr_comment_changed)

    # ── 6. General consistency ceiling: never 10-12 with disproved evidence ─
    cap = base_cap
    if cap is None and claim_removed and any(flag not in waived for flag in flags):
        cap = _GENERIC_CEILING
        cap_reason = 'technical_evidence_general_ceiling'

    # ── 7. Rebuild summary: factual conclusion instead of removed praise ──
    ordered = [flag for flag in _FLAG_PRIORITY if flag in flags]
    primary = next((flag for flag in ordered if flag in required_flags), ordered[0])
    primary_text = (texts[primary]['summary_required'] if primary in required_flags
                    else texts[primary]['summary_neutral'])
    generic_success = any(marker in _norm(additions['summary'])
                          for marker in _GENERIC_SUCCESS_MARKERS)
    if summary_removed:
        new_summary = primary_text
    elif cap is not None and (not additions['summary'] or generic_success):
        new_summary = primary_text
    else:
        new_summary = additions['summary']
    if new_summary != additions['summary']:
        _add_adjustment(adjustments, 'summary', additions['summary'], new_summary,
                        f'technical_evidence:{primary}')
        additions['summary'] = new_summary

    # ── 8. Grade explanation: remove praise, append factual reasoning ─────
    missing_explanations = [texts[flag]['grade_explanation'] for flag in flags
                            if texts[flag]['grade_explanation']
                            and _norm(texts[flag]['grade_explanation'])
                            not in _norm(clean_explanation)]
    if explanation_removed or not clean_explanation:
        new_explanation = ' '.join(
            filter(None, [clean_explanation] + missing_explanations)).strip()
    elif cap is not None and missing_explanations:
        new_explanation = ' '.join(
            filter(None, [clean_explanation] + missing_explanations)).strip()
    else:
        new_explanation = additions['grade_explanation']
    if new_explanation != additions['grade_explanation']:
        _add_adjustment(adjustments, 'grade_explanation', additions['grade_explanation'],
                        new_explanation, f'technical_evidence:{primary}')
    additions['grade_explanation'] = new_explanation

    # ── 9. Weaknesses: add factual remarks once, never filter corrections ─
    new_weaknesses = list(additions['weaknesses'])
    for flag in flags:
        marker = _norm(texts[flag]['weakness_marker'])
        already = any(marker and marker in _norm(item) for item in new_weaknesses)
        if not already:
            new_weaknesses.append(texts[flag]['weakness'])
    if new_weaknesses != additions['weaknesses']:
        _add_adjustment(adjustments, 'weaknesses',
                        '; '.join(additions['weaknesses']),
                        '; '.join(new_weaknesses),
                        f'technical_evidence:{primary}')
        additions['weaknesses'] = new_weaknesses

    # ── 10. Student feedback: factual explanation without praise ──────────
    base_feedback = clean_feedback if feedback_removed else additions['feedback_comment']
    missing_feedback = [texts[flag]['feedback'] for flag in flags
                        if texts[flag]['feedback']
                        and _norm(texts[flag]['feedback_marker'])
                        not in _norm(base_feedback)]
    new_feedback = ' '.join(
        filter(None, [_as_text(base_feedback).strip()] + missing_feedback)).strip()
    if new_feedback != additions['feedback_comment']:
        _add_adjustment(adjustments, 'feedback_comment', additions['feedback_comment'],
                        new_feedback, f'technical_evidence:{primary}')
        additions['feedback_comment'] = new_feedback

    # ── 11. Grade ceilings — every grade source, not only the suggested one ─
    if cap is not None:
        before_grade = _as_text(additions['suggested_grade'])
        after_grade = _cap_grade(before_grade, cap)
        if after_grade != before_grade:
            additions['suggested_grade'] = after_grade
            _add_adjustment(adjustments, 'suggested_grade', before_grade, after_grade,
                            cap_reason)
            before_level = additions['level']
            additions['level'] = _level_for_grade(after_grade)
            _add_adjustment(adjustments, 'level', before_level, additions['level'],
                            cap_reason)

        for row in additions['clean_gr_results']:
            if not isinstance(row, dict):
                continue
            if _norm(row.get('status')) in ('not_applicable', 'not applicable', 'n/a',
                                            'не застосовується'):
                continue
            before = row.get('grade')
            after = _cap_grade(before, cap)
            if after != _as_text(before):
                row['grade'] = after
                row['level'] = _level_for_grade(after)
                _add_adjustment(adjustments, 'clean_gr_results', before, after,
                                cap_reason)

        numeric_before = list(additions['numeric_gr_grades'])
        numeric_after = []
        for value in numeric_before:
            try:
                number = float(value)
            except (TypeError, ValueError):
                numeric_after.append(value)
                continue
            numeric_after.append(min(number, float(cap)))
        if len(numeric_after) == len(numeric_before) and any(
                isinstance(before, (int, float)) and isinstance(after, (int, float))
                and after < before for before, after in zip(numeric_before, numeric_after)):
            additions['numeric_gr_grades'] = numeric_after
            _add_adjustment(adjustments, 'numeric_gr_grades', numeric_before,
                            numeric_after, cap_reason)

        # Always enforce the ceiling on the average, even when the suggested
        # grade was already below the cap: the final mark may become the GR
        # average, which would silently lose the ceiling otherwise.
        before_avg = additions['avg_gr_grade']
        valid_numeric = _cap_numeric_list(additions['numeric_gr_grades'])
        if valid_numeric:
            computed = int(min(12, max(1, math.ceil(
                sum(valid_numeric) / len(valid_numeric)))))
            after_avg = min(computed, int(cap))
        elif before_avg is not None:
            capped_avg = _cap_grade(before_avg, cap)
            after_avg = int(capped_avg) if capped_avg.isdigit() else before_avg
        else:
            after_avg = before_avg
        if after_avg != before_avg:
            additions['avg_gr_grade'] = after_avg
            _add_adjustment(adjustments, 'avg_gr_grade', before_avg, after_avg,
                            cap_reason)

    # The returned explanation and the mutated result JSON must stay in sync
    # (the integration writes the returned value back into result_json as well).
    result['grade_explanation'] = additions['grade_explanation']

    return dict(additions, adjustments=adjustments, applied=bool(adjustments),
                active_flags=flags, waived_flags=waived_flags)
