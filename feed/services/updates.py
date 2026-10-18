from scripts.update_metadata import check_remote, local_release
import json
import logging
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from django.conf import settings

logger = logging.getLogger(__name__)

UPDATER_HOST = os.environ.get('UPDATER_HOST', 'updater')
UPDATER_PORT = int(os.environ.get('UPDATER_PORT', 8001))
UPDATER_URL = f"http://{UPDATER_HOST}:{UPDATER_PORT}"
STATUS_FILE = Path(settings.BASE_DIR) / 'backups' / 'update_status.json'


def get_current_local_version():
    """Returns currently installed version of SchoolNet."""
    version_file = Path(settings.BASE_DIR) / 'VERSION'
    if version_file.exists():
        return version_file.read_text(encoding='utf-8').strip()
    try:
        from feed.changelog import SITE_VERSION
        return SITE_VERSION
    except Exception:
        return '4.4.5'


def parse_version_tuple(version_str):
    """Converts version string into integer tuple for comparison."""
    try:
        parts = [int(p) for p in re.findall(r'\d+', str(version_str))]
        return tuple(parts) if parts else (0,)
    except Exception:
        return (0,)


def parse_changelog_markdown(text):
    """Parses markdown CHANGELOG.md into structured release list."""
    releases = []
    current = None
    for line in text.splitlines():
        line = line.strip()
        header_match = re.match(r'^##\s+\[?v?(\d+\.\d+(?:\.\d+)*)\]?(.*)$', line)
        if header_match:
            if current:
                releases.append(current)
            ver = header_match.group(1)
            rest = header_match.group(2).strip()
            rest = re.sub(r'^[-–—:]\s*', '', rest)
            parts = re.split(r'\s+[-–—:]\s+', rest, maxsplit=1)
            if len(parts) == 2:
                date_str, title = parts[0].strip(), parts[1].strip()
            elif len(parts) == 1:
                date_str, title = '', parts[0].strip()
            else:
                date_str, title = '', ''
            current = {
                'version': ver,
                'date': date_str,
                'title': title or f'Версія {ver}',
                'changes': []
            }
        elif current and (line.startswith('- ') or line.startswith('* ')):
            change_text = line[2:].strip()
            if change_text:
                current['changes'].append(change_text)
    if current:
        releases.append(current)
    return releases


def filter_releases_between(all_releases, current_ver, target_ver):
    """
    Returns all releases strictly newer than current_ver up to target_ver,
    sorted with the newest version first.
    """
    curr_t = parse_version_tuple(current_ver)
    target_t = parse_version_tuple(target_ver)
    matching = []
    for rel in all_releases:
        v_t = parse_version_tuple(rel.get('version', ''))
        if curr_t < v_t <= target_t:
            matching.append(rel)
    matching.sort(key=lambda r: parse_version_tuple(r.get('version', '')), reverse=True)
    return matching


def check_updates_available():
    current_ver = get_current_local_version()
    payload = json.dumps({'current_version': current_ver}).encode('utf-8')
    try:
        req = urllib.request.Request(f"{UPDATER_URL}/api/check", data=payload,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=35) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict) and 'ok' in data:
            return data
    except Exception as exc:
        logger.debug("Updater check unavailable: %s", exc)
    return check_remote(Path(settings.BASE_DIR), os.environ.get('GITHUB_REPO', 'yrskrs/schoolwork2'), current_ver)


def get_current_release():
    return local_release(Path(settings.BASE_DIR), get_current_local_version())


def get_update_status():
    """Retrieve current update progress from updater service or local status file."""
    installed_version = get_current_local_version()
    # Try updater daemon
    try:
        req = urllib.request.Request(f"{UPDATER_URL}/api/status")
        with urllib.request.urlopen(req, timeout=2) as resp:
            status = json.loads(resp.read().decode('utf-8'))
            if isinstance(status, dict):
                return {**status, 'installed_version': installed_version}
    except Exception:
        pass

    # Fallback to status file in shared backups volume
    if STATUS_FILE.exists():
        try:
            status = json.loads(STATUS_FILE.read_text(encoding='utf-8'))
            if isinstance(status, dict):
                return {**status, 'installed_version': installed_version}
        except Exception:
            pass

    return {
        'state': 'idle',
        'step': 0,
        'step_name': 'Готово до роботи',
        'progress': 0,
        'logs': [],
        'error': None,
        'rolled_back': False,
        'current_version': get_current_local_version(),
        'installed_version': installed_version,
        'releases_between': []
    }


def start_update(target_ref='origin/main', target_ver=None):
    """Trigger the update process in the updater service."""
    payload = json.dumps({'ref': target_ref, 'version': target_ver}).encode('utf-8')
    req = urllib.request.Request(
        f"{UPDATER_URL}/api/update",
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as he:
        body = he.read().decode('utf-8')
        try:
            return json.loads(body)
        except Exception:
            return {'error': f"HTTP {he.code}: {body}"}
    except Exception as exc:
        return {'error': f"Сервіс оновлення недоступний: {str(exc)}"}


def start_rollback(backup_file=None):
    """Trigger rollback to previous backup."""
    payload = json.dumps({'backup_file': backup_file}).encode('utf-8')
    req = urllib.request.Request(
        f"{UPDATER_URL}/api/rollback",
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except Exception as exc:
        return {'error': f"Сервіс оновлення недоступний: {str(exc)}"}
