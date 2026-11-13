"""Серверний diff для історії генерацій опису завдання.

Повертає JSON-сумісну структуру без HTML: UI рендерить її через textContent.
Для замін у межах рядка додаються посегментні позначки змін (intra-line),
щоб учитель бачив не лише «рядок змінився», а й що саме в ньому змінилося.
"""
import difflib
import re

MAX_LINES = 2000
MAX_INTRALINE_LINES = 40

_TOKEN_RE = re.compile(r'\S+|\s+')


def _line_list(text):
    value = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    if not value.strip():
        return []
    return value.split('\n')


def _segments(old_line, new_line):
    """Посегментний diff двох рядків для intra-line підсвітки."""
    old_tokens = _TOKEN_RE.findall(old_line)
    new_tokens = _TOKEN_RE.findall(new_line)
    matcher = difflib.SequenceMatcher(None, old_tokens, new_tokens)

    def side(tokens, other, wanted):
        result = []
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == 'equal':
                result.append({'text': ''.join(tokens[i1:i2]), 'changed': False})
            elif op == wanted:
                result.append({'text': ''.join(tokens[i1:i2]), 'changed': True})
            elif op == 'replace' and wanted == 'old':
                result.append({'text': ''.join(tokens[i1:i2]), 'changed': True})
            elif op == 'replace' and wanted == 'new':
                result.append({'text': ''.join(tokens[j1:j2]), 'changed': True})
            elif op == 'delete' and wanted == 'old':
                result.append({'text': ''.join(tokens[i1:i2]), 'changed': True})
            elif op == 'insert' and wanted == 'new':
                result.append({'text': ''.join(tokens[j1:j2]), 'changed': True})
        # Прибираємо порожні сегменти й зливаємо сусідні з однаковим прапорцем.
        merged = []
        for item in result:
            if not item['text']:
                continue
            if merged and merged[-1]['changed'] == item['changed']:
                merged[-1]['text'] += item['text']
            else:
                merged.append(dict(item))
        return merged

    return side(old_tokens, new_tokens, 'old'), side(new_tokens, old_tokens, 'new')


def build_line_diff(old_text, new_text, max_lines=MAX_LINES):
    """Повертає {'ops': [...], 'truncated': bool}.

    ops: {'type': ' '|'-'|'+', 'text': str, 'segments': [{'text','changed'}]|None}
    """
    old_lines = _line_list(old_text)
    new_lines = _line_list(new_text)
    truncated = len(old_lines) > max_lines or len(new_lines) > max_lines
    if truncated:
        old_lines = old_lines[:max_lines]
        new_lines = new_lines[:max_lines]

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    ops = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            for line in old_lines[i1:i2]:
                ops.append({'type': ' ', 'text': line, 'segments': None})
        elif op == 'delete':
            for line in old_lines[i1:i2]:
                ops.append({'type': '-', 'text': line, 'segments': None})
        elif op == 'insert':
            for line in new_lines[j1:j2]:
                ops.append({'type': '+', 'text': line, 'segments': None})
        else:  # replace
            old_block = old_lines[i1:i2]
            new_block = new_lines[j1:j2]
            if len(old_block) <= MAX_INTRALINE_LINES and len(new_block) <= MAX_INTRALINE_LINES:
                for old_line, new_line in zip(old_block, new_block):
                    old_segments, new_segments = _segments(old_line, new_line)
                    ops.append({'type': '-', 'text': old_line, 'segments': old_segments})
                    ops.append({'type': '+', 'text': new_line, 'segments': new_segments})
                # Якщо блоки різної довжини — решта рядків без посегментної підсвітки.
                for line in old_block[len(new_block):]:
                    ops.append({'type': '-', 'text': line, 'segments': None})
                for line in new_block[len(old_block):]:
                    ops.append({'type': '+', 'text': line, 'segments': None})
            else:
                for line in old_block:
                    ops.append({'type': '-', 'text': line, 'segments': None})
                for line in new_block:
                    ops.append({'type': '+', 'text': line, 'segments': None})
    return {'ops': ops, 'truncated': truncated}
