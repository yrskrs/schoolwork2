#!/usr/bin/env python3
"""Validates that repository versioning strictly follows docs/VERSIONING_POLICY.md."""
import ast
from datetime import datetime
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VALID_TAGS = ('[UI]', '[CORE]', '[SYS]', '[SEC]')


def parse_semver(text):
    text = text.strip().lstrip('v')
    match = re.fullmatch(r'^(\d+)\.(\d+)\.(\d+)$', text)
    if not match:
        return None
    return tuple(int(x) for x in match.groups())


def validate_iso_date(date_str):
    if not isinstance(date_str, str) or not re.fullmatch(r'^\d{4}-\d{2}-\d{2}$', date_str.strip()):
        return False
    try:
        datetime.strptime(date_str.strip(), '%Y-%m-%d')
        return True
    except ValueError:
        return False


def main():
    errors = []
    rel_date = None
    md_date = None

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
            changelog_data = None
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            if target.id == 'SITE_VERSION':
                                site_ver = ast.literal_eval(node.value)
                            elif target.id == 'SITE_VERSION_SHORT':
                                site_ver_short = ast.literal_eval(node.value)
                            elif target.id == 'CHANGELOG_DATA':
                                changelog_data = ast.literal_eval(node.value)
                                if changelog_data and isinstance(changelog_data, list):
                                    data_ver = changelog_data[0].get('version')
            if site_ver != raw_version:
                errors.append(f'feed/changelog.py SITE_VERSION ("{site_ver}") != VERSION ("{raw_version}").')
            if site_ver_short != f'v{raw_version}':
                errors.append(f'feed/changelog.py SITE_VERSION_SHORT ("{site_ver_short}") != "v{raw_version}".')
            if data_ver != raw_version:
                errors.append(f'feed/changelog.py CHANGELOG_DATA[0].version ("{data_ver}") != VERSION ("{raw_version}").')
            if changelog_data:
                first_latest = changelog_data[0].get('is_latest')
                if first_latest is not True:
                    errors.append(f'feed/changelog.py CHANGELOG_DATA[0] має містити "is_latest": True (знайдено: {first_latest}).')
                for idx, entry in enumerate(changelog_data[1:], start=1):
                    if entry.get('is_latest'):
                        errors.append(f'feed/changelog.py CHANGELOG_DATA[{idx}] ({entry.get("version")}) не повинен мати "is_latest": True.')
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
                rel_date = latest.get('date', '').strip()
                if not validate_iso_date(rel_date):
                    errors.append(f'RELEASES.json[0].date має бути валідною датою YYYY-MM-DD. Знайдено: "{rel_date}".')
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
            # Підтримуємо обидва формати: "## [X.Y.Z] - YYYY-MM-DD - [TAG] ..." та "## X.Y.Z — YYYY-MM-DD — [TAG] ..."
            m = re.match(r'^##\s+(?:\[v?(\d+\.\d+\.\d+)\]|v?(\d+\.\d+\.\d+))\s+[-—]\s+(\S+)\s+[-—]\s+(.*)$', line.strip())
            if m:
                ver = m.group(1) or m.group(2)
                date_str = m.group(3)
                rest = m.group(4).strip()
                first_header = (ver, date_str, rest)
                break
        if not first_header:
            errors.append('У CHANGELOG.md не знайдено заголовка релізу з форматом ## [X.Y.Z] — YYYY-MM-DD — [TAG] або ## X.Y.Z — YYYY-MM-DD — [TAG].')
        else:
            md_ver, md_date, rest = first_header
            md_date = md_date.strip()
            if md_ver != raw_version:
                errors.append(f'CHANGELOG.md найновіший реліз ("{md_ver}") != VERSION ("{raw_version}").')
            if not validate_iso_date(md_date):
                errors.append(f'CHANGELOG.md дата релізу має бути у форматі YYYY-MM-DD. Знайдено: "{md_date}".')
            if not any(tag in rest for tag in VALID_TAGS):
                errors.append(
                    f'CHANGELOG.md заголовок релізу має містити тег сфери {VALID_TAGS}. Рядок: "{rest}".'
                )

    # 5. Звірка дат RELEASES.json та CHANGELOG.md
    if rel_date and md_date and rel_date != md_date:
        errors.append(f'Дата релізу в RELEASES.json ("{rel_date}") != даті в CHANGELOG.md ("{md_date}").')

    # 6. Перевірка Dockerfile (ARG APP_VERSION)
    dockerfile = ROOT / 'Dockerfile'
    if dockerfile.exists() and raw_version:
        content = dockerfile.read_text(encoding='utf-8')
        m = re.search(r'^\s*ARG\s+APP_VERSION=([^\s]+)', content, re.MULTILINE)
        if m:
            d_ver = m.group(1).strip()
            if d_ver != raw_version:
                errors.append(f'Dockerfile ARG APP_VERSION ("{d_ver}") != VERSION ("{raw_version}").')
        else:
            errors.append('Dockerfile не містить оголошення ARG APP_VERSION.')

    # 7. Перевірка compose.yaml (APP_VERSION та всі теги образу schoolwork2-app)
    compose_yaml = ROOT / 'compose.yaml'
    if compose_yaml.exists() and raw_version:
        content = compose_yaml.read_text(encoding='utf-8')
        if f"APP_VERSION: '{raw_version}'" not in content and f'APP_VERSION: "{raw_version}"' not in content:
            errors.append(f'compose.yaml не містить APP_VERSION: \'{raw_version}\'.')
        tags = re.findall(r'schoolwork2-app:([^\s\}\'\"]+)', content)
        if not tags:
            errors.append(f'compose.yaml не містить тег образу schoolwork2-app:{raw_version}.')
        for tag in tags:
            if tag != raw_version:
                errors.append(f'compose.yaml містить розбіжність тегу образу schoolwork2-app:{tag} != VERSION ("{raw_version}").')

    # 8. Перевірка compose.lan.yaml (тег образу)
    compose_lan = ROOT / 'compose.lan.yaml'
    if compose_lan.exists() and raw_version:
        content = compose_lan.read_text(encoding='utf-8')
        lan_tags = re.findall(r'schoolwork2-app:([^\s\}\'\"]+)', content)
        if not lan_tags:
            errors.append(f'compose.lan.yaml не містить тег образу schoolwork2-app:{raw_version}.')
        for tag in lan_tags:
            if tag != raw_version:
                errors.append(f'compose.lan.yaml містить застарілий тег образу schoolwork2-app:{tag} != VERSION ("{raw_version}").')

    # 9. Перевірка README.md (заголовок та тіло)
    readme_md = ROOT / 'README.md'
    if readme_md.exists() and raw_version:
        content = readme_md.read_text(encoding='utf-8')
        if f'# SchoolNet {raw_version}' not in content:
            errors.append(f'README.md заголовок повинен починатися з "# SchoolNet {raw_version}".')
        if f'Поточний випуск: **{raw_version}' not in content:
            errors.append(f'README.md тіло повинно містити "Поточний випуск: **{raw_version}".')
        if not re.search(r'Готово до встановлення[^:]*:\s*\*\*' + re.escape(raw_version) + r'\*\*', content):
            errors.append(f'README.md тіло повинно містити "Готово до встановлення ...: **{raw_version}**".')

    if errors:
        print('❌ Порушення політики нумерації версій (docs/VERSIONING_POLICY.md):', file=sys.stderr)
        for err in errors:
            print(f'  - {err}', file=sys.stderr)
        sys.exit(1)

    print(f'✅ Політика нумерації дотримана: версія {raw_version} узгоджена в усіх конфігураціях (VERSION, RELEASES.json, feed/changelog.py, CHANGELOG.md, Dockerfile, compose.yaml, compose.lan.yaml, README.md) з обов\'язковим маркуванням.')


if __name__ == '__main__':
    main()
