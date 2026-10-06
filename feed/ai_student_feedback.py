"""Short, grade-free feedback at every student display and persistence boundary."""
import json
import re

from django.utils.html import strip_tags

AI_COMMENT_TAG = '🤖 [Рекомендації та відгук ШІ]:'
MAX_FEEDBACK_CHARS = 700
_GRADE = re.compile(
    r'\b(?:оц[іе]нк\w*|оцінен\w*|оцінюван\w*|бал(?:и|ів|а|ом|ами|у)?|розбаловк\w*|grade\w*|score\w*|points?|suggested_grade|gr_results)\b'
    r'|\bГР\s*\d+|\b\d+(?:[.,]\d+)?\s*(?:/|з|із|out of)\s*12\b'
    r'|\b12\s*[-–]?\s*бальн\w*|\b\d+(?:[.,]\d+)?\s+б\.(?=\s|$)', re.IGNORECASE)
_HEADING = re.compile(r'^\s*(?:[📌✅💡💬⚠️🎯📋📊🛠️]+\s*|\*\*[^*]+\*\*)')


def _short_text(value, limit):
    if not isinstance(value, str):
        return ''
    value = strip_tags(value).replace('**', '').strip()
    value = re.sub(r'^\s*(?:[•*\-]|\d+[.)])\s*', '', value)
    sentences = re.split(r'(?<=[.!?;])\s+|\n+', value)
    value = ' '.join(s.strip() for s in sentences if s.strip() and not _GRADE.search(s))
    value = re.sub(r'\s+', ' ', value).strip()
    if re.fullmatch(r'\d+(?:[.,]\d+)?', value):
        return ''
    if len(value) > limit:
        value = value[:limit - 1].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    return value


def _legacy_result(text):
    text = str(text or '').strip()
    if text.startswith('🤖 ['):
        text = text.split('\n', 1)[-1] if '\n' in text else ''
    candidate = text.strip('` \n')
    if candidate.startswith('json\n'):
        candidate = candidate[5:]
    if candidate.startswith('{'):
        try:
            result = json.loads(candidate)
            return result if isinstance(result, dict) else {}
        except (ValueError, TypeError):
            # Never expose broken JSON, technical keys or a salvaged grade.
            return {}
    values = {'summary': [], 'strengths': [], 'revision_advice': [], 'feedback_comment': []}
    section = 'feedback_comment'
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if _HEADING.match(line):
            title = line.replace('**', '').casefold()
            if _GRADE.search(title) or any(word in title for word in ('чому така', 'критері', 'оцінювання', 'групами результат', 'розбалов')):
                section = None
                continue
            if line.startswith('📌') or 'висновок:' in title or 'резюме:' in title:
                section = 'summary'
            elif line.startswith('✅') or 'сильні сторони' in title:
                section = 'strengths'
            elif line.startswith(('💡', '🛠', '⚠')) or any(w in title for w in ('покращити', 'зауваження', 'доробити')):
                section = 'revision_advice'
            else:
                section = 'feedback_comment'
            if '**' in line or re.match(r'^[^:]+:', line):
                line = line.split(':', 1)[-1] if ':' in line else ''
            else:
                line = line.lstrip('📌✅💡💬⚠️🎯📋📊🛠️ ')
        if section and line:
            values[section].append(line)
    return dict(values, summary=' '.join(values['summary']),
                feedback_comment=' '.join(values['feedback_comment']))


def student_feedback_parts(result=None, text=''):
    data = result if isinstance(result, dict) else _legacy_result(text)
    summary = _short_text(data.get('summary'), 180)
    seen = {summary.casefold()} if summary else set()

    def choose(items, count, limit):
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list):
            return []
        output = []
        for item in items:
            value = _short_text(item, limit)
            key = value.casefold().rstrip('.!…')
            if value and key not in {s.rstrip('.!…') for s in seen}:
                output.append(value)
                seen.add(key)
            if len(output) == count:
                break
        return output

    strengths = choose(data.get('strengths'), 1, 120)
    advice = choose(data.get('revision_advice'), 2, 150)
    if not advice:
        advice = choose(data.get('weaknesses'), 2, 150)
    comment = _short_text(data.get('feedback_comment'), 180)
    if not summary and comment:
        summary = comment
    elif not advice and comment:
        advice = choose([comment], 1, 150)
    return {'summary': summary, 'strengths': strengths, 'weaknesses': advice}


def compact_student_feedback(result=None, text=''):
    parts = student_feedback_parts(result, text)
    lines = ([f'📌 {parts["summary"]}'] if parts['summary'] else [])
    lines += [f'✅ {s}' for s in parts['strengths']]
    lines += [f'💡 {s}' for s in parts['weaknesses']]
    return '\n'.join(lines)[:MAX_FEEDBACK_CHARS]


def public_ai_comment(text):
    if str(text or '').lstrip().startswith('🤖 ['):
        feedback = compact_student_feedback(text=text)
        return f'{AI_COMMENT_TAG}\n{feedback}' if feedback else AI_COMMENT_TAG
    return text


def generate_student_praise(grade=None, level=None):
    """
    Pedagogical warm encouragement tailored to the student's effort and grade level.
    """
    raw_str = str(grade or '').strip().lower()
    lvl_str = str(level or '').strip().lower()

    if 'доопрацю' in raw_str or 'доопрацю' in lvl_str:
        return "Молодець, що вчасно здав роботу та зробив першу спробу! Помилки — це частина навчання. Ознайомся з порадами нижче та доопрацюй завдання!"

    val = None
    try:
        val = float(raw_str.replace(',', '.'))
    except (ValueError, TypeError):
        pass

    if val is not None:
        if val >= 10:
            return "Чудова робота! Ти старанно виконав завдання, продемонстрував високий рівень знань та охайність. Так тримати!"
        elif val >= 7:
            return "Гарний результат! Ти добре попрацював, впевнено розібрався в темі та виконав основні вимоги вчителя. Молодець!"
        elif val >= 4:
            return "Добра спроба! Ти доклав зусиль і впорався з важливою частиною роботи. Продовжуй практикуватися і результат буде ще кращим!"
        else:
            return "Молодець, що надіслав роботу! Не засмучуйся — помилки допомагають краще зрозуміти тему. Доопрацюй вказані кроки, і все вийде!"

    if any(k in lvl_str for k in ('висок', 'відмінн', 'high')):
        return "Чудова робота! Ти старанно виконав завдання, продемонстрував високий рівень знань та охайність. Так тримати!"
    if any(k in lvl_str for k in ('достатн', 'добр', 'good')):
        return "Гарний результат! Ти добре попрацював, впевнено розібрався в темі та виконав основні вимоги вчителя. Молодець!"
    if any(k in lvl_str for k in ('середн', 'avg')):
        return "Добра спроба! Ти доклав зусиль і впорався з важливою частиною роботи. Продовжуй практикуватися!"

    return "Молодець, що вчасно здав роботу та проявив активність! Ознайомся з корисними порадами нижче."


def get_student_feedback_sections(result=None, text='', grade=None, level=None):
    """
    Returns structured, friendly, concise feedback sections for the student UI:
    - praise: Похвала учню за роботу
    - grade_reason: Чому така оцінка (коротко)
    - recommendation: Рекомендація учню
    - improvement: Як покращити роботу (список конкретних дій)
    """
    data = result if isinstance(result, dict) else _legacy_result(text)

    # 1. Похвала за роботу
    praise = generate_student_praise(
        grade=grade or data.get('suggested_grade'),
        level=level or data.get('level')
    )

    # 2. Чому така оцінка
    grade_reason = ''
    raw_expl = data.get('grade_explanation') or ''
    if raw_expl and isinstance(raw_expl, str):
        cleaned = strip_tags(raw_expl).replace('**', '').strip()
        sentences = [s.strip() for s in re.split(r'(?<=[.!?;])\s+|\n+', cleaned) if s.strip()]
        grade_reason = ' '.join(sentences[:2])
    if not grade_reason:
        grade_reason = _short_text(data.get('summary'), 220)
    if not grade_reason and text:
        parts = student_feedback_parts(result=result, text=text)
        grade_reason = parts.get('summary') or ''
    if len(grade_reason) > 240:
        grade_reason = grade_reason[:239].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'

    # 3. Рекомендація учню
    recommendation = ''
    raw_comm = data.get('feedback_comment') or ''
    if raw_comm and isinstance(raw_comm, str):
        cleaned_comm = strip_tags(raw_comm).replace('**', '').strip()
        cleaned_comm = re.sub(r'^\s*(?:[•*\-]|\d+[.)])\s*', '', cleaned_comm)
        if cleaned_comm.casefold() != grade_reason.casefold():
            recommendation = cleaned_comm
    if not recommendation and data.get('strengths'):
        st = data.get('strengths')
        st_list = st if isinstance(st, list) else [st]
        for item in st_list:
            clean_item = _short_text(item, 180)
            if clean_item and clean_item.casefold() != grade_reason.casefold():
                recommendation = clean_item
                break
    if not recommendation and text:
        parts = student_feedback_parts(result=result, text=text)
        if parts.get('strengths'):
            recommendation = parts['strengths'][0]
    if len(recommendation) > 220:
        recommendation = recommendation[:219].rsplit(' ', 1)[0].rstrip(' ,;:') + '…'
    if not recommendation:
        recommendation = "Звертай увагу на правильність оформлення та дотримуйся рекомендацій учителя."

    # 4. Як покращити роботу
    advice_items = []
    raw_adv = data.get('revision_advice') or data.get('weaknesses') or []
    if isinstance(raw_adv, str):
        raw_adv = [raw_adv]
    seen_adv = set()
    for item in raw_adv:
        clean_item = _short_text(item, 150)
        key = clean_item.casefold().rstrip('.!…')
        if clean_item and key not in seen_adv and key != grade_reason.casefold() and key != recommendation.casefold():
            advice_items.append(clean_item)
            seen_adv.add(key)
        if len(advice_items) == 3:
            break

    if not advice_items and text:
        parts = student_feedback_parts(result=result, text=text)
        advice_items = parts.get('weaknesses') or []

    if not advice_items:
        raw_grade_str = str(grade or data.get('suggested_grade') or '').strip()
        try:
            val = float(raw_grade_str.replace(',', '.'))
        except (ValueError, TypeError):
            val = None
        if val is not None and val >= 10:
            advice_items = ["Роботу виконано на високому рівні, критичних зауважень немає. Продовжуй у тому ж дусі!"]
        else:
            advice_items = ["Ознайомся з вимогами до наступних завдань для досягнення найвищого результату."]

    return {
        'praise': praise,
        'grade_reason': grade_reason or "Роботу перевірено відповідно до навчальних критеріїв.",
        'recommendation': recommendation,
        'improvement': advice_items,
    }


def parse_teacher_ai_feedback_groups(text='', result=None):
    """
    Розбирає текстовий звіт ШІ для вчителя на структуровані групи для згорнутого перегляду:
    - 📌 Загальний висновок
    - 🎯 Чому така оцінка / Обґрунтування
    - ✅ Сильні сторони роботи
    - 💡 Зауваження та неточності
    - 💬 Рекомендація учню
    - ⚠️ Зауваження до формату файлу
    - 📋 Критерії оцінювання / Вимоги завдання
    - 📝 Зміст звіту ШІ

    Повертає список груп: [{'id': str, 'icon': str, 'title': str, 'badge': str, 'content': str}]
    """
    groups = []

    # 1. Якщо передано структурований результат або в тексті схований JSON
    data = result if isinstance(result, dict) else None
    if not data and text:
        work_text = str(text).strip()
        if (work_text.startswith('{') or '"suggested_grade"' in work_text or '"feedback_comment"' in work_text) and work_text.endswith('}'):
            try:
                data = json.loads(work_text)
            except Exception:
                try:
                    from feed.gemini_service import extract_json_from_text
                    data = extract_json_from_text(work_text)
                except Exception:
                    data = None

    if data and isinstance(data, dict):
        fw = data.get('format_warning')
        if fw and str(fw).strip():
            groups.append({
                'id': 'format_warning',
                'icon': '⚠️',
                'title': 'Зауваження до формату файлу',
                'badge': 'Увага',
                'content': str(fw).strip(),
            })

        summary = data.get('summary')
        if summary and str(summary).strip():
            groups.append({
                'id': 'summary',
                'icon': '📌',
                'title': 'Загальний висновок',
                'badge': '',
                'content': str(summary).strip(),
            })

        grade_expl = data.get('grade_explanation')
        if grade_expl and str(grade_expl).strip():
            groups.append({
                'id': 'grade_reason',
                'icon': '🎯',
                'title': 'Чому така оцінка / Обґрунтування',
                'badge': '',
                'content': str(grade_expl).strip(),
            })

        strengths = data.get('strengths')
        if strengths and isinstance(strengths, list) and len(strengths) > 0:
            count = len(strengths)
            cnt_str = f"{count} " + ("пункт" if count == 1 else "пункти" if count in (2, 3, 4) else "пунктів")
            groups.append({
                'id': 'strengths',
                'icon': '✅',
                'title': 'Сильні сторони роботи',
                'badge': cnt_str,
                'content': "\n".join(f"• {s}" for s in strengths if str(s).strip()),
            })

        weaknesses = data.get('weaknesses')
        if weaknesses and isinstance(weaknesses, list) and len(weaknesses) > 0:
            count = len(weaknesses)
            cnt_str = f"{count} " + ("пункт" if count == 1 else "пункти" if count in (2, 3, 4) else "пунктів")
            groups.append({
                'id': 'weaknesses',
                'icon': '💡',
                'title': 'Зауваження та неточності',
                'badge': cnt_str,
                'content': "\n".join(f"• {w}" for w in weaknesses if str(w).strip()),
            })

        fc = data.get('feedback_comment')
        if fc and str(fc).strip():
            groups.append({
                'id': 'recommendation',
                'icon': '💬',
                'title': 'Рекомендація учню',
                'badge': '',
                'content': str(fc).strip(),
            })

        if groups:
            return groups

    # 2. Парсинг тексту за заголовками секцій
    if not text:
        return []

    raw_text = str(text).strip()
    if not raw_text:
        return []

    # Розділяємо за відомими патернами маркдаун-заголовків або емодзі-заголовків
    pattern = re.compile(
        r'(?:^|\n)(?:'
        r'(#{2,4}\s+([^\n]+))|'
        r'([⚠️📌🎯✅💡💬📋🔍📝⚡])\s*(?:\*\*)?([^\n:*]+)(?:\*\*)?[:\s]*|'
        r'(?:\*\*)([^\n:*]+)(?:\*\*)[:\s]+'
        r')',
        re.MULTILINE
    )

    matches = list(pattern.finditer(raw_text))
    if len(matches) >= 2:
        for idx, m in enumerate(matches):
            header_full = m.group(0).strip()
            # Визначаємо іконку та назву
            h_icon = '📝'
            h_title = ''
            if m.group(1):  # ### Title
                h_title = m.group(2).strip()
            elif m.group(3):  # Icon + Title
                h_icon = m.group(3)
                h_title = m.group(4).strip()
            elif m.group(5):  # **Title:**
                h_title = m.group(5).strip()

            # Нормалізуємо іконку, якщо вона не була визначена
            title_lower = h_title.lower()
            if 'виснов' in title_lower or 'підсум' in title_lower:
                h_icon = '📌'
            elif 'сильн' in title_lower or 'плюс' in title_lower or 'переваг' in title_lower:
                h_icon = '✅'
            elif 'зауважен' in title_lower or 'недолік' in title_lower or 'помилк' in title_lower:
                h_icon = '💡'
            elif 'рекоменд' in title_lower or 'порад' in title_lower:
                h_icon = '💬'
            elif 'формат' in title_lower:
                h_icon = '⚠️'
            elif 'чому' in title_lower or 'обґрунт' in title_lower or 'оцінк' in title_lower:
                h_icon = '🎯'
            elif 'критер' in title_lower:
                h_icon = '📋'

            start_content = m.end()
            end_content = matches[idx + 1].start() if idx + 1 < len(matches) else len(raw_text)
            content_part = raw_text[start_content:end_content].strip()

            # Чистимо бейдж
            bullets = [line for line in content_part.split('\n') if line.strip().startswith(('•', '-', '* '))]
            badge = ''
            if len(bullets) >= 2:
                b_count = len(bullets)
                badge = f"{b_count} " + ("пункти" if b_count in (2, 3, 4) else "пунктів")

            if content_part or h_title:
                groups.append({
                    'id': f"sec_{idx + 1}",
                    'icon': h_icon,
                    'title': h_title or 'Розділ перевірки',
                    'badge': badge,
                    'content': content_part,
                })

        if len(groups) >= 2:
            return groups

    # 3. Якщо секцій немає або лише одна, повертаємо суцільний текст в одній групі
    return [{
        'id': 'general',
        'icon': '📝',
        'title': 'Текстовий звіт перевірки ШІ',
        'badge': '',
        'content': raw_text,
    }]


