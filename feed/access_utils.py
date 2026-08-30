"""
Microsoft Access Database parser (.mdb / .accdb) для SchoolNet.

Підтримує формати:
- Access 2007+ (.accdb) — ACE format
- Access 97-2003 (.mdb) — Jet 3.0/4.0 format

Підходи (в порядку пріоритету):
1. mdbtools (системна утиліта Linux) — найнадійніший варіант
2. Власний pure-Python парсер через struct — fallback без залежностей
"""

import os
import re
import json
import subprocess
import tempfile
from html import escape as html_escape
from typing import Optional, Tuple, List, Dict, Any


# ═══════════════════════════════════════════════════════════════════════════════
# ПЕРЕВІРКА ДОСТУПНОСТІ MDBTOOLS
# ═══════════════════════════════════════════════════════════════════════════════

_MDBTOOLS_AVAILABLE = None


def is_mdbtools_available() -> bool:
    global _MDBTOOLS_AVAILABLE
    if _MDBTOOLS_AVAILABLE is None:
        try:
            result = subprocess.run(
                ['mdb-tables', '--help'],
                capture_output=True,
                timeout=3
            )
            _MDBTOOLS_AVAILABLE = True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            _MDBTOOLS_AVAILABLE = False
    return _MDBTOOLS_AVAILABLE


# ═══════════════════════════════════════════════════════════════════════════════
# ПАРСИНГ ЧЕРЕЗ MDBTOOLS (СИСТЕМНА УТИЛІТА)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_mdb_command(args: list, timeout: int = 10) -> Tuple[str, Optional[str]]:
    """Виконує команду mdbtools і повертає (stdout, error)."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            env={**os.environ, 'MDB_JET3_CHARSET': 'windows-1251', 'LANG': 'uk_UA.UTF-8'}
        )
        stdout = result.stdout.decode('utf-8', errors='replace').strip()
        stderr = result.stderr.decode('utf-8', errors='replace').strip()
        if result.returncode != 0 and not stdout:
            return '', stderr or f"Помилка mdbtools (код {result.returncode})"
        return stdout, None
    except subprocess.TimeoutExpired:
        return '', "Перевищено час очікування при читанні .mdb файлу"
    except FileNotFoundError:
        return '', "mdbtools не встановлено"
    except Exception as e:
        return '', str(e)


def _parse_csv_line(line: str, delimiter: str = ';') -> List[str]:
    import csv, io
    try:
        reader = csv.reader(io.StringIO(line), delimiter=delimiter, quotechar='"')
        return next(reader, [line])
    except Exception:
        return [c.strip('"').strip() for c in line.split(delimiter)]


def _get_tables_mdbtools(file_path: str) -> Tuple[List[str], Optional[str]]:
    stdout, err = _run_mdb_command(['mdb-tables', '-1', file_path])
    if err:
        return [], err
    tables = [t.strip() for t in stdout.splitlines() if t.strip()]
    return tables, None


def _export_table_mdbtools(file_path: str, table: str, max_rows: int = 200) -> Tuple[List[List[str]], List[str], Optional[str]]:
    stdout, err = _run_mdb_command(['mdb-export', '-d', ';', file_path, table], timeout=15)
    if err:
        return [], [], err
    rows = []
    headers = []
    lines = stdout.splitlines()
    for i, line in enumerate(lines[:max_rows + 1]):
        cells = _parse_csv_line(line, delimiter=';')
        if i == 0:
            headers = cells
        else:
            rows.append(cells)
    return rows, headers, None


def parse_access_with_mdbtools(file_path: str, max_rows_per_table: int = 200) -> Tuple[dict, Optional[str]]:
    tables, err = _get_tables_mdbtools(file_path)
    if err:
        return {}, err
    if not tables:
        return {'tables': [], 'schema': ''}, None

    result = {
        'tables': [],
        'file_size_kb': os.path.getsize(file_path) / 1024,
    }
    schema_out, _ = _run_mdb_command(['mdb-schema', file_path])
    result['schema'] = schema_out[:8000] if schema_out else ''

    for table_name in tables[:30]:
        rows, headers, exp_err = _export_table_mdbtools(file_path, table_name, max_rows_per_table)
        result['tables'].append({
            'name': table_name,
            'headers': headers,
            'rows': rows,
            'row_count': len(rows),
            'error': exp_err,
        })

    return result, None


# ═══════════════════════════════════════════════════════════════════════════════
# FALLBACK: PURE-PYTHON BINARY PARSER
# ═══════════════════════════════════════════════════════════════════════════════

def _detect_access_version(file_bytes: bytes) -> str:
    if b'Standard ACE DB' in file_bytes[:100]:
        return 'Access 2007+ (.accdb, ACE)'
    if b'Standard Jet DB' in file_bytes[:100]:
        return 'Access 97-2003 (.mdb, Jet)'
    return 'Microsoft Access Database'


def _extract_strings_from_binary(data: bytes, min_len: int = 5, max_len: int = 200) -> List[str]:
    strings = []
    seen = set()
    # UTF-16LE рядки (Access 2007+ .accdb)
    utf16_pattern = re.compile(rb'(?:[\x20-\x7e\xc0-\xff]\x00){4,}')
    for m in utf16_pattern.finditer(data):
        try:
            s = m.group(0).decode('utf-16-le', errors='replace').strip()
            s = re.sub(r'[\x00-\x1f\x7f]+', ' ', s).strip()
            if len(s) >= min_len and s not in seen:
                if not re.match(r'^[\x00\s!@#$%^&*()\-=+\[\]{}|\\:;"\'<>,./?\d]{3,}$', s):
                    strings.append(s)
                    seen.add(s)
        except Exception:
            pass
    # ANSI / CP1251
    ansi_pattern = re.compile(rb'[\x20-\x7e\xc0-\xff\t\r\n]{' + str(min_len).encode() + rb',}')
    for m in ansi_pattern.finditer(data):
        for enc in ['utf-8', 'cp1251', 'latin-1']:
            try:
                s = m.group(0).decode(enc, errors='replace').strip()
                s = re.sub(r'[\x00-\x1f\x7f]+', ' ', s).strip()
                if (min_len <= len(s) <= max_len and s not in seen
                        and not re.match(r'^[\x00\s!@#$%\d\-=\[\]|\\]{3,}$', s)):
                    strings.append(s)
                    seen.add(s)
                    break
            except Exception:
                pass
    return strings[:500]


def _find_table_names_in_binary(data: bytes) -> List[str]:
    table_names = []
    seen = set()
    utf16_names = re.findall(rb'(?:[\x21-\x7e\xc0-\xff]\x00){2,32}', data)
    for m in utf16_names:
        try:
            s = m.decode('utf-16-le', errors='replace').strip()
            if (2 <= len(s) <= 64
                    and re.match(r'^[\w\u0400-\u04ff \-_]+$', s)
                    and s not in seen
                    and not s.startswith('MSys')):
                table_names.append(s)
                seen.add(s)
        except Exception:
            pass
    return table_names[:20]


def parse_access_fallback(file_path: str) -> Tuple[dict, Optional[str]]:
    try:
        file_size = os.path.getsize(file_path)
        with open(file_path, 'rb') as f:
            data = f.read(min(file_size, 10 * 1024 * 1024))
        version = _detect_access_version(data)
        strings = _extract_strings_from_binary(data)
        possible_tables = _find_table_names_in_binary(data)
        return {
            'version': version,
            'tables': possible_tables,
            'strings': strings,
            'parsed_via': 'fallback_binary',
            'file_size_kb': file_size / 1024,
        }, None
    except Exception as e:
        return {}, f"Помилка читання Access файлу: {str(e)}"


# ═══════════════════════════════════════════════════════════════════════════════
# ГОЛОВНА ФУНКЦІЯ: КОНВЕРТАЦІЯ В HTML ДЛЯ ПЕРЕГЛЯДУ В БРАУЗЕРІ
# ═══════════════════════════════════════════════════════════════════════════════

def convert_access_to_html(file_path: str, max_rows: int = 100) -> Tuple[str, Optional[str]]:
    """
    Конвертує файл Microsoft Access (.mdb / .accdb) у HTML для перегляду в браузері.
    Повертає (html_content: str, error_message: str | None).
    """
    file_name = os.path.basename(file_path)
    file_size_kb = os.path.getsize(file_path) / 1024

    html_parts = []
    html_parts.append(f'''
<div class="access-db-viewer">
  <div style="display:flex;align-items:center;gap:12px;padding:14px 16px;
              background:linear-gradient(135deg,rgba(16,185,129,0.12),rgba(99,102,241,0.1));
              border-radius:var(--radius-md,8px);border:1px solid rgba(16,185,129,0.3);
              margin-bottom:16px;">
    <span style="font-size:32px;">🗄️</span>
    <div>
      <div style="font-weight:800;font-size:16px;color:var(--color-text-primary,#1e293b);">
        Microsoft Access Database
      </div>
      <div style="font-size:12.5px;color:var(--color-text-secondary,#64748b);margin-top:2px;">
        {html_escape(file_name)} &nbsp;&middot;&nbsp; {file_size_kb:.1f} КБ
      </div>
    </div>
  </div>
''')

    if is_mdbtools_available():
        db_data, err = parse_access_with_mdbtools(file_path, max_rows_per_table=max_rows)
        if err:
            html_parts.append(f'<div style="padding:10px;border-radius:6px;background:rgba(239,68,68,0.1);color:#dc2626;border:1px solid rgba(239,68,68,0.3);margin-bottom:12px;">⚠️ {html_escape(err)}</div>')

        tables = db_data.get('tables', [])
        if not tables:
            html_parts.append('<p style="color:var(--color-text-muted,#94a3b8);font-style:italic;">📭 База даних не містить користувацьких таблиць або вони порожні.</p>')
        else:
            html_parts.append(f'<div style="margin-bottom:12px;font-size:13px;color:var(--color-text-secondary,#64748b);">📊 Знайдено таблиць: <strong>{len(tables)}</strong></div>')
            nav_tabs = ' '.join([
                f'<button onclick="switchAccessTable(\'access-tbl-{i}\')" class="btn btn-secondary btn-sm" '
                f'id="access-tab-{i}" style="font-size:11.5px;font-weight:700;margin:2px;">'
                f'📋 {html_escape(t["name"])}</button>'
                for i, t in enumerate(tables)
            ])
            html_parts.append(f'<div style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:16px;">{nav_tabs}</div>')

            for i, table in enumerate(tables):
                display = 'block' if i == 0 else 'none'
                html_parts.append(f'<div id="access-tbl-{i}" class="access-table-panel" style="display:{display};">')
                html_parts.append(f'<h4 style="font-size:14px;font-weight:800;color:var(--color-primary,#6366f1);margin-bottom:10px;">📋 Таблиця: {html_escape(table["name"])}</h4>')

                if table.get('error'):
                    html_parts.append(f'<p style="color:#dc2626;">⚠️ {html_escape(table["error"])}</p>')
                elif not table.get('headers'):
                    html_parts.append('<p style="color:var(--color-text-muted,#94a3b8);">Таблиця порожня або недоступна</p>')
                else:
                    headers = table['headers']
                    rows = table['rows']
                    html_parts.append('<div class="table-responsive" style="overflow-x:auto;">')
                    html_parts.append('<table class="excel-table table table-bordered" style="width:100%;border-collapse:collapse;font-size:13px;">')
                    html_parts.append('<thead><tr style="background:var(--color-bg-secondary,#f8fafc);">')
                    html_parts.append('<th style="text-align:center;padding:6px 8px;font-weight:700;font-size:12px;">#</th>')
                    for col in headers:
                        html_parts.append(f'<th style="padding:6px 10px;font-weight:700;white-space:nowrap;">{html_escape(str(col))}</th>')
                    html_parts.append('</tr></thead><tbody>')

                    for row_idx, row in enumerate(rows[:max_rows], 1):
                        bg = 'background:rgba(99,102,241,0.04);' if row_idx % 2 == 0 else ''
                        html_parts.append(f'<tr style="{bg}"><td style="text-align:center;font-weight:600;color:var(--color-text-muted,#94a3b8);padding:5px 8px;font-size:11px;">{row_idx}</td>')
                        for cell in row:
                            html_parts.append(f'<td style="padding:5px 10px;border:1px solid var(--color-border,#e2e8f0);">{html_escape(str(cell) if cell is not None else "")}</td>')
                        for _ in range(len(headers) - len(row)):
                            html_parts.append('<td></td>')
                        html_parts.append('</tr>')

                    html_parts.append('</tbody></table></div>')
                    count_note = f"Показано перші {max_rows} записів" if len(rows) >= max_rows else f"Всього записів: {len(rows)}"
                    icon = "📌" if len(rows) >= max_rows else "✅"
                    html_parts.append(f'<p style="font-size:11.5px;color:var(--color-text-muted,#94a3b8);margin-top:6px;">{icon} {count_note}</p>')

                html_parts.append('</div>')

        schema = db_data.get('schema', '')
        if schema:
            html_parts.append(f'''
<details style="margin-top:16px;">
  <summary style="cursor:pointer;font-weight:700;font-size:13px;color:var(--color-primary,#6366f1);
                  padding:8px 12px;background:var(--color-bg-secondary,#f8fafc);
                  border-radius:6px;border:1px solid var(--color-border,#e2e8f0);">
    📐 Схема бази даних (SQL DDL)
  </summary>
  <pre style="font-size:11.5px;font-family:monospace;background:var(--color-bg-secondary,#f8fafc);
              border:1px dashed var(--color-border,#e2e8f0);border-radius:6px;
              padding:12px;overflow-x:auto;max-height:320px;white-space:pre-wrap;
              margin-top:6px;">{html_escape(schema[:6000])}</pre>
</details>''')

        html_parts.append('''
<script>
function switchAccessTable(targetId) {
    document.querySelectorAll('.access-table-panel').forEach(function(el) {
        el.style.display = 'none';
    });
    var target = document.getElementById(targetId);
    if (target) target.style.display = 'block';
}
</script>''')
    else:
        db_data, err = parse_access_fallback(file_path)
        if err:
            html_parts.append(f'<p style="color:#dc2626;">⚠️ {html_escape(err)}</p>')
        else:
            version = db_data.get('version', 'Microsoft Access')
            html_parts.append(f'''
<div style="padding:10px 14px;background:rgba(245,158,11,0.1);
            border:1px solid rgba(245,158,11,0.3);border-radius:6px;
            font-size:13px;margin-bottom:12px;">
  ⚠️ <strong>mdbtools не встановлено</strong> — використано резервний режим перегляду.
  Для повного перегляду встановіть: <code>sudo dnf install mdbtools</code>
</div>
<p><strong>Версія:</strong> {html_escape(version)}</p>''')

            tables = db_data.get('tables', [])
            if tables:
                html_parts.append(f'<p><strong>Виявлені таблиці ({len(tables)}):</strong></p><ul>')
                for tname in tables:
                    html_parts.append(f'<li>📋 {html_escape(tname)}</li>')
                html_parts.append('</ul>')

            strings = db_data.get('strings', [])
            if strings:
                html_parts.append(f'''
<details open>
  <summary style="cursor:pointer;font-weight:700;font-size:13px;color:var(--color-primary,#6366f1);
                  padding:8px 12px;background:var(--color-bg-secondary,#f8fafc);
                  border-radius:6px;border:1px solid var(--color-border,#e2e8f0);">
    🔤 Видобуті дані з файлу ({len(strings)} рядків)
  </summary>
  <div style="padding:10px;background:var(--color-bg-secondary,#f8fafc);
              border:1px dashed var(--color-border,#e2e8f0);
              border-radius:6px;margin-top:6px;max-height:300px;overflow-y:auto;">
    <table style="width:100%;font-size:12.5px;border-collapse:collapse;">''')
                for s in strings[:200]:
                    html_parts.append(f'<tr><td style="padding:3px 8px;border-bottom:1px solid var(--color-border,#e2e8f0);">{html_escape(s)}</td></tr>')
                html_parts.append('</table></div></details>')

    html_parts.append('</div>')
    return '\n'.join(html_parts), None


# ═══════════════════════════════════════════════════════════════════════════════
# ТЕКСТОВИЙ ЕКСТРАКТОР ДЛЯ ШІ (GEMINI)
# ═══════════════════════════════════════════════════════════════════════════════

def extract_access_text_for_ai(file_path: str, max_rows_per_table: int = 50) -> str:
    """
    Видобуває структурований текст із Access файлу для відправки до Google Gemini AI.
    """
    file_name = os.path.basename(file_path)
    file_size_kb = os.path.getsize(file_path) / 1024
    ext = os.path.splitext(file_path)[1].lower()
    db_type = "Access 2007+ (.accdb)" if ext == '.accdb' else "Access 97-2003 (.mdb)"

    parts = [f"🗄️ База даних Microsoft {db_type}: «{file_name}» ({file_size_kb:.1f} КБ)"]

    if is_mdbtools_available():
        db_data, err = parse_access_with_mdbtools(file_path, max_rows_per_table)
        if err:
            parts.append(f"⚠️ Помилка читання: {err}")
        else:
            tables = db_data.get('tables', [])
            parts.append(f"\n📊 Кількість таблиць: {len(tables)}")

            schema = db_data.get('schema', '')
            if schema:
                parts.append(f"\n📐 Схема бази даних:\n```sql\n{schema[:3000]}\n```")

            for table in tables[:15]:
                parts.append(f"\n─────────────────────────────────")
                parts.append(f"📋 Таблиця: «{table['name']}» ({table['row_count']} записів)")
                headers = table.get('headers', [])
                rows = table.get('rows', [])
                if headers:
                    parts.append(f"Колонки: {' | '.join(headers)}")
                for i, row in enumerate(rows[:max_rows_per_table], 1):
                    parts.append(f"  {i}. {' | '.join(str(c) for c in row)}")
                if not headers and not rows:
                    parts.append(f"  (Помилка: {table.get('error', 'невідома')})" if table.get('error') else "  (порожня)")
    else:
        db_data, err = parse_access_fallback(file_path)
        if err:
            parts.append(f"⚠️ Помилка: {err}")
        else:
            parts.append(f"\nВерсія: {db_data.get('version', '?')}")
            tables = db_data.get('tables', [])
            if tables:
                parts.append(f"Виявлені таблиці: {', '.join(tables)}")
            strings = db_data.get('strings', [])
            if strings:
                parts.append(f"\nВидобуті дані ({len(strings)} значень):")
                for s in strings[:200]:
                    parts.append(f"  • {s}")
        parts.append("\n⚠️ Для повноцінного аналізу рекомендується встановити mdbtools.")

    return '\n'.join(parts)
