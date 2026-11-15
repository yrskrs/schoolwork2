#!/usr/bin/env python3
"""Validates that repository versioning strictly follows docs/VERSIONING_POLICY.md."""
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VALID_TAGS = ('[UI]', '[CORE]', '[SYS]', '[SEC]', '[FEATURE]')


def parse_semver(text):
    text = text.strip().lstrip('v')
    match = re.fullmatch(r'^(\d+)\.(\d+)\.(\d+)$', text)
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def main():
    errors = []

    # 1. Перевірка VERSION
    version_file = ROOT / 'VERSION'
    if not version_file.exists():
        errors.append('Файл VERSION відсутній.')
        raw_version = None
    else:
        raw_version = version_file.read_text(encoding='utf-8').strip()
        parsed = parse_semver(raw_version)
        if not parsed:
            errors.append(f'VERSION має бути у форматі X.Y.Z без літер (знайдено: "{raw_version}").')

    # 2. Перевірка feed/changelog.py
    changelog_py = ROOT / 'feed' / 'changelog.py'
    if not changelog_py.exists():
        errors.append('feed/changelog.py відсутній.')
    else:
        try:
            tree = ast.parse(changelog_py.read_text(encoding='utf-8'))
            site_ver = None
            site_ver_short = None
            data_ver = None
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id == 'SITE_VERSION':
                                site_ver = ast.literal_eval(node.value)
                            elif target.id == 'SITE_VERSION_SHORT':
                                site_ver_short = ast.literal_eval(node.value)
                            elif target.id == 'CHANGELOG_DATA':
                                data = ast.literal_eval(node.value)
                                if data and isinstance(data, list):
                                    data_ver = data[0].get('version')
            if site_ver != raw_version:
                errors.append(f'feed/changelog.py SITE_VERSION ("{site_ver}") != VERSION ("{raw_version}").')
            if site_ver_short != f'v{raw_version}':
                errors.append(f'feed/changelog.py SITE_VERSION_SHORT ("{site_ver_short}") != "v{raw_version}".')
            if data_ver != raw_version:
                errors.append(f'feed/changelog.py CHANGELOG_DATA[0].version ("{data_ver}") != VERSION ("{raw_version}").')
        except Exception as e:
            errors.append(f'Помилка синтаксичного аналізу feed/changelog.py: {e}')

    # 3. Перевірка RELEASES.json
    releases_json = ROOT / 'RELEASES.json'
    if not releases_json.exists():
        errors.append('RELEASES.json відсутній.')
    else:
        try:
            releases = json.loads(releases_json.read_text(encoding='utf-8'))
            if not releases:
                errors.append('RELEASES.json порожній.')
            else:
                latest = releases[0]
                rel_ver = latest.get('version')
                if rel_ver != raw_version:
                    errors.append(f'RELEASES.json[0].version ("{rel_ver}") != VERSION ("{raw_version}").')
                rel_title = latest.get('title', '').strip()
                if not any(rel_title.startswith(tag) for tag in VALID_TAGS):
                    errors.append(
                        f'RELEASES.json[0].title має починатися з одного з обов\'язкових тегів {VALID_TAGS}. Знайдено: "{rel_title}".'
                    )
                # Перевірка попередньої версії (новіша версія має бути більшою)
                if len(releases) > 1 and raw_version:
                    prev_ver = releases[1].get('version')
                    p_curr = parse_semver(raw_version)
                    p_prev = parse_semver(prev_ver) if prev_ver else None
                    if p_curr and p_prev and not (p_curr > p_prev):
                        errors.append(f'Поточна версія {raw_version} не більша за попередню {prev_ver} у RELEASES.json.')
        except Exception as e:
            errors.append(f'Помилка читання RELEASES.json: {e}')

    # 4. Перевірка CHANGELOG.md
    changelog_md = ROOT / 'CHANGELOG.md'
    if not changelog_md.exists():
        errors.append('CHANGELOG.md відсутній.')
    else:
        first_header = None
        for line in changelog_md.read_text(encoding='utf-8').splitlines():
            m = re.match(r'^##\s+\[?v?(\d+\.\d+\.\d+)\]?(.*)$', line.strip())
            if m:
                first_header = (m.group(1), m.group(2).strip())
                break
        if not first_header:
            errors.append('У CHANGELOG.md не знайдено заголовка релізу з форматом ## [X.Y.Z].')
        else:
            md_ver, rest = first_header
            if md_ver != raw_version:
                errors.append(f'CHANGELOG.md найновіший реліз ("{md_ver}") != VERSION ("{raw_version}").')
            if not any(tag in rest for tag in VALID_TAGS):
                errors.append(
                    f'CHANGELOG.md заголовок релізу має містити тег сфери {VALID_TAGS}. Рядок: "{rest}".'
                )

    if errors:
        print('❌ Порушення політики нумерації версій (docs/VERSIONING_POLICY.md):', file=sys.stderr)
        for err in errors:
            print(f'  - {err}', file=sys.stderr)
        sys.exit(1)

    print(f'✅ Політика нумерації дотримана: версія {raw_version} узгоджена в усіх 4 джерелах з обов\'язковим маркуванням.')


if __name__ == '__main__':
    main()
