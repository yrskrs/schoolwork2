"""Render a human-readable expected-results report from the local import log."""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = Path('/tmp/ai_lab_coursework_import.log')
OUT = ROOT / 'docs' / 'audits'
ARTIFACT_DIR = Path('/home/mlinux/.gemini/antigravity/brain/deb2f2b5-6374-4c03-b9da-d233ac77c52c')


def rows_from_log():
    line = next(item for item in LOG.read_text(encoding='utf-8').splitlines()
                if item.startswith('AI_LAB_COURSEWORK_ROWS='))
    rows = json.loads(line.split('=', 1)[1])
    # Persist shared json
    shared_path = OUT / 'artifacts' / 'ai_lab_coursework_import.json'
    shared_path.parent.mkdir(parents=True, exist_ok=True)
    shared_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return rows


def main():
    rows = rows_from_log()
    assert len(rows) == 175, f"Expected 175 rows, got {len(rows)}"
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['class_name']].append(row)

    # Unique assignments map
    assignments = {}
    for r in rows:
        key = (r['class_name'], r['lesson'])
        if key not in assignments:
            assignments[key] = {
                'class_name': r['class_name'],
                'lesson': r['lesson'],
                'topic': r['topic'],
                'teacher_attachment': r.get('teacher_attachment', ''),
                'assigned_task': r.get('assigned_task', 'Вся практична робота'),
                'work_type': r.get('work_type', ''),
                'assignment_id': r.get('assignment_id'),
            }

    rich_count = sum(1 for a in assignments.values() if a['teacher_attachment'])

    md = [
        '# Очікувані результати навчального набору AI-Lab (Інформатика 5–11 класи)',
        '',
        'Дата оновлення: 09.11.2026. Набір містить 35 оригінальних навчальних завдань з інформатики та 175 реалістично виконаних учнівських робіт: по 5 завдань і 25 зданих робіт для кожного класу від 5-А до 11-А.',
        '',
        'Усі роботи в системі SchoolNet навмисно залишені **неоціненими** (поля оцінки вчителя та ШІ порожні), щоб слугувати контрольним еталоном для тестування автоматизованого оцінювання штучним інтелектом.',
        '',
        '## Підтримка форматів матеріалів та фокусування завдань',
        '',
        f'У наборі реалізовано **{rich_count} завдань із прикріпленими файлами вчителя** у різних форматах, що відповідають реальній шкільній практиці:',
        '- **Документи Word (.docx)**: методичні рекомендації та інструкційні картки (5 завдань: 5-А ур. 3, 8-А ур. 1, 9-А ур. 3, 10-А ур. 1, 11-А ур. 1).',
        '- **Електронні таблиці Excel (.xlsx)**: шаблони вхідних даних та багаторівневі розрахункові відомості (3 завдання: 6-А ур. 3, 7-А ур. 2, 9-А ур. 2).',
        '- **Презентації PowerPoint (.pptx)**: навчальні слайди з вимогами та структурою проєктів (2 завдання: 6-А ур. 2, 7-А ур. 4).',
        '- **Графічні схеми та макети (.png)**: блок-схеми лінійних алгоритмів та адаптивні вебмакети (2 завдання: 5-А ур. 4, 8-А ур. 3).',
        '',
        '> [!IMPORTANT]',
        '> **Фокусування завдання з багатозадачного документа (Task Scoping):**',
        '> У прикріплених файлах вчителя міститься кілька завдань (наприклад: *Завдання 1: оглядове*, *Завдання 2: обов’язкове*, *Завдання 3: додаткове*).',
        '> У полі опису вчитель чітко обмежує обсяг (наприклад: *«Обов’язково виконайте лише завдання 2... інші вправи документа є оглядовими»*).',
        '> Механізм AI-контексту (`parse_teacher_specific_task_numbers` та `focus_assigned_material`) автоматично виділяє саме задане завдання, запобігаючи невірній оцінці за невиконані оглядові частини.',
        '',
        '## Розподіл теоретичних оцінок у наборі',
        '',
        '| Бал | Кількість робіт | Відсоток від вибірки |',
        '| ---: | ---: | ---: |',
        f"| 4 | {sum(r['expected_mark'] == 4 for r in rows)} | 20.0% |",
        f"| 5 | {sum(r['expected_mark'] == 5 for r in rows)} | 20.0% |",
        f"| 7 | {sum(r['expected_mark'] == 7 for r in rows)} | 20.0% |",
        f"| 10 | {sum(r['expected_mark'] == 10 for r in rows)} | 20.0% |",
        f"| 12 | {sum(r['expected_mark'] == 12 for r in rows)} | 20.0% |",
        '| **Разом** | **175** | **100.0%** |',
        '',
        '## Критерії теоретичного балу (НУШ та загальні)',
        '',
        '| Рівень | Бал | Педагогічна характеристика учнівської роботи |',
        '| :--- | ---: | :--- |',
        '| **Високий** | **12** | Зразкове, бездоганне виконання всіх вимог завдання. Учень демонструє глибоке розуміння теми, наводить розгорнуті коментарі, враховує граничні випадки, код, таблиця чи верстка оформлені охайно за стандартами (PEP8, формули Excel, семантичний HTML5, структура Word). |',
        '| **Високий** | **10** | Відмінне виконання. Усі основні й другорядні завдання виконані правильно, логіка вірна. Є незначні прогалини в коментарях або спрощене оформлення. |',
        '| **Достатній** | **7** | Тверде володіння базовим матеріалом. Робота працює для типових випадків, але є неточності, пропущено крайові випадки або 1 другорядну вимогу, стислі пояснення. |',
        '| **Середній** | **5** | Часткове виконання (близько 40-50%). Наявні помітні логічні або синтаксичні помилки, неповний код або непрацюючі формули/конструкції, поверхневі відповіді. |',
        '| **Початковий** | **4** | Мінімальна спроба виконання (20-30%). Код не запускається або містить грубі синтаксичні помилки, файл містить лише 1-2 уривчастих рядки, базові навички не продемонстровані. |',
        '',
        '## Зведений перелік завдань з інформатики (35 уроків)',
        '',
        '| Клас | Урок | Тема завдання | Прикріплений файл вчителя | Задана частина завдання | Формат учнівських робіт |',
        '| :---: | :---: | :--- | :--- | :--- | :--- |'
    ]

    for key in sorted(assignments, key=lambda x: (int(x[0].split('-')[0]), x[1])):
        a = assignments[key]
        teacher_file = f"`{a['teacher_attachment']}`" if a['teacher_attachment'] else '*(текст в описі)*'
        md.append(
            f"| {a['class_name']} | {a['lesson']} | {a['topic']} | {teacher_file} | {a['assigned_task']} | {a['work_type']} |"
        )
    md.append('')

    md.append('## Таблиці очікуваних результатів за класами та учнями')
    md.append('')

    for class_name in sorted(grouped, key=lambda x: int(x.split('-')[0])):
        class_rows = sorted(grouped[class_name], key=lambda row: (row['lesson'], row['student']))
        md += [
            f'### Клас {class_name}',
            '',
            '| Урок | Тема завдання | Файл вчителя / Завдання | Учень / учениця | Файл роботи | Очікуваний бал | Рівень | Педагогічне обґрунтування |',
            '| :---: | :--- | :--- | :--- | :--- | :---: | :---: | :--- |'
        ]
        for row in class_rows:
            rationale = row.get('rationale') or '; '.join(row.get('gaps', [])) or 'Вимоги виконано зразково.'
            t_info = f"`{row.get('teacher_attachment')}`<br>*{row.get('assigned_task', '')}*" if row.get('teacher_attachment') else "*(в описі)*"
            md.append(
                f"| {row['lesson']} | {row['topic']} | {t_info} | {row['student']} | `{row.get('filename', '')}` | "
                f"**{row['expected_mark']}** | {row['level']} | {rationale} |"
            )
        md.append('')

    OUT.mkdir(parents=True, exist_ok=True)
    report_file = OUT / 'AI_LAB_COURSEWORK_EXPECTED_RESULTS_2026-11-09.md'
    full_md = '\n'.join(md) + '\n'
    report_file.write_text(full_md, encoding='utf-8')

    fields = [
        'class_name', 'lesson', 'topic', 'teacher_attachment', 'assigned_task',
        'student', 'work_type', 'filename', 'expected_mark', 'level', 'rationale',
        'gaps', 'source', 'submission_id', 'assignment_id'
    ]
    csv_file = OUT / 'AI_LAB_COURSEWORK_EXPECTED_RESULTS_2026-11-09.csv'
    with csv_file.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            copy = dict(row)
            if isinstance(copy.get('gaps'), list):
                copy['gaps'] = ' | '.join(copy['gaps'])
            writer.writerow({k: copy.get(k, '') for k in fields})

    summary = {
        'assignments': 35,
        'rich_assignments': rich_count,
        'submissions': 175,
        'ungraded_expected': True,
        'classes': {name: len(items) for name, items in grouped.items()},
        'marks': {str(mark): sum(item['expected_mark'] == mark for item in rows) for mark in (4, 5, 7, 10, 12)}
    }
    json_summary = OUT / 'artifacts' / 'ai_lab_coursework_summary_2026-11-09.json'
    json_summary.parent.mkdir(parents=True, exist_ok=True)
    json_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    if ARTIFACT_DIR.exists():
        (ARTIFACT_DIR / 'AI_LAB_COURSEWORK_EXPECTED_RESULTS.md').write_text(full_md, encoding='utf-8')
        (ARTIFACT_DIR / 'coursework_expected_results.md').write_text(full_md, encoding='utf-8')

    print(f"Звіт успішно сформовано: {report_file}")
    print(f"CSV збережено: {csv_file}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
