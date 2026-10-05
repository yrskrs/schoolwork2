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
