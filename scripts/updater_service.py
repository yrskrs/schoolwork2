#!/usr/bin/env python3
"""
Autonomous updater service for SchoolNet (schoolwork2).
Executes updates in an isolated Docker container, reports progress,
maintains changelog diffs between versions, and performs automatic rollbacks.
"""
import http.server
import json
import os
from pathlib import Path
import re
import socketserver
import subprocess
import threading
import time
import urllib.error
import urllib.request

ROOT = Path(os.environ.get('SCHOOLWORK_ROOT', Path(__file__).resolve().parent.parent))
STATUS_FILE = ROOT / 'backups' / 'update_status.json'
LOCK_FILE = ROOT / '.updater.lock'

CURRENT_STATE = {
    'state': 'idle',  # 'idle', 'running', 'success', 'error'
    'step': 0,
    'step_name': 'Готово до роботи',
    'progress': 0,
    'logs': [],
    'error': None,
    'rolled_back': False,
    'target_version': None,
    'current_version': None,
    'backup_file': None,
    'started_at': None,
    'updated_at': None,
    'releases_between': [],
}

STATE_LOCK = threading.Lock()


def get_current_version():
    version_file = ROOT / 'VERSION'
    if version_file.exists():
        return version_file.read_text(encoding='utf-8').strip()
    return '4.4.5'


def save_status():
    try:
        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATUS_FILE, 'w', encoding='utf-8') as f:
            json.dump(CURRENT_STATE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Failed to write status file: {e}")


def update_state(**kwargs):
    with STATE_LOCK:
        CURRENT_STATE.update(kwargs)
        CURRENT_STATE['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_status()


def append_log(line):
    clean_line = line.strip()
    if not clean_line:
        return
    with STATE_LOCK:
        CURRENT_STATE['logs'].append(clean_line)
        if len(CURRENT_STATE['logs']) > 300:
            CURRENT_STATE['logs'] = CURRENT_STATE['logs'][-300:]
        CURRENT_STATE['updated_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        save_status()


def _version_tuple(v_str):
    try:
        parts = [int(p) for p in re.findall(r'\d+', str(v_str))]
        return tuple(parts) if parts else (0,)
    except Exception:
        return (0,)


def parse_changelog_markdown(text):
    """Fallback parser for markdown CHANGELOG.md."""
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
    Returns all releases between current_ver and target_ver (inclusive of target_ver, exclusive of current_ver),
    sorted in descending order (latest version first).
    """
    curr_t = _version_tuple(current_ver)
    target_t = _version_tuple(target_ver)
    matching = []
    for rel in all_releases:
        v_t = _version_tuple(rel.get('version', ''))
        if curr_t < v_t <= target_t:
            matching.append(rel)
    matching.sort(key=lambda r: _version_tuple(r.get('version', '')), reverse=True)
    return matching


def check_github_updates():
    """
    Checks GitHub for the latest version and computes the full list of releases between
    the currently installed version and the latest remote version.
    """
    current_ver = get_current_version()
    repo_url = os.environ.get('GITHUB_REPO', 'yrskrs/schoolwork2')
    version_url = f"https://raw.githubusercontent.com/{repo_url}/main/VERSION"
    releases_url = f"https://raw.githubusercontent.com/{repo_url}/main/RELEASES.json"
    changelog_url = f"https://raw.githubusercontent.com/{repo_url}/main/CHANGELOG.md"

    headers = {'User-Agent': 'SchoolNet-Updater/1.0'}
    remote_ver = None
    all_releases = []

    try:
        # 1. Fetch remote VERSION
        req_v = urllib.request.Request(version_url, headers=headers)
        with urllib.request.urlopen(req_v, timeout=7) as resp:
            remote_ver = resp.read().decode('utf-8').strip()

        # 2. Try fetching RELEASES.json first
        try:
            req_r = urllib.request.Request(releases_url, headers=headers)
            with urllib.request.urlopen(req_r, timeout=7) as resp_rel:
                all_releases = json.loads(resp_rel.read().decode('utf-8'))
        except Exception:
            # 3. Fallback to CHANGELOG.md if RELEASES.json is unavailable
            try:
                req_c = urllib.request.Request(changelog_url, headers=headers)
                with urllib.request.urlopen(req_c, timeout=7) as resp_c:
                    md_text = resp_c.read().decode('utf-8')
                    all_releases = parse_changelog_markdown(md_text)
            except Exception:
                pass

        # If both remote files failed, check local RELEASES.json / CHANGELOG.md
        if not all_releases:
            local_rel_file = ROOT / 'RELEASES.json'
            local_cl_file = ROOT / 'CHANGELOG.md'
            if local_rel_file.exists():
                try:
                    all_releases = json.loads(local_rel_file.read_text(encoding='utf-8'))
                except Exception:
                    pass
            elif local_cl_file.exists():
                try:
                    all_releases = parse_changelog_markdown(local_cl_file.read_text(encoding='utf-8'))
                except Exception:
                    pass

        has_update = _version_tuple(remote_ver) > _version_tuple(current_ver)
        releases_between = filter_releases_between(all_releases, current_ver, remote_ver) if has_update else []

        # If has_update but releases_between is empty, generate a fallback entry
        if has_update and not releases_between:
            releases_between = [{
                'version': remote_ver,
                'date': time.strftime('%Y-%m-%d'),
                'title': f'Оновлення до версії {remote_ver}',
                'changes': ['Планове оновлення компонентів SchoolNet']
            }]

        return {
            'ok': True,
            'current_version': current_ver,
            'latest_version': remote_ver,
            'has_update': has_update,
            'releases_between': releases_between,
            'target_release': releases_between[0] if releases_between else None,
            'releases_count': len(releases_between)
        }
    except Exception as exc:
        return {
            'ok': False,
            'current_version': current_ver,
            'latest_version': current_ver,
            'has_update': False,
            'releases_between': [],
            'error': f"Не вдалося зв'язатися з GitHub: {str(exc)}"
        }


def execute_update_job(target_ref='origin/main', target_ver=None):
    """Background update execution with rollback on failure."""
    current_ver = get_current_version()
    update_state(
        state='running',
        step=1,
        step_name='Отримання коду з GitHub',
        progress=10,
        logs=[],
        error=None,
        rolled_back=False,
        target_version=target_ver or 'останньої версії',
        current_version=current_ver,
        started_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    )
    append_log(f"Розпочато процес оновлення з {current_ver} до {target_ver or target_ref}...")

    backup_path = None
    try:
        # Step 1: Fetch from GitHub
        append_log("Крок 1/5: Завантаження коду з GitHub...")
        fetch_proc = subprocess.run(
            ['python3', str(ROOT / 'scripts' / 'fetch_update.py')],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90
        )
        if fetch_proc.returncode != 0:
            err_msg = fetch_proc.stderr or fetch_proc.stdout or 'Помилка fetch_update'
            raise RuntimeError(f"Не вдалося отримати код із GitHub: {err_msg.strip()}")
        append_log("Код успішно завантажено з GitHub.")

        # Step 2: Target revision & candidate build in separate worktree
        update_state(step=2, step_name='Збірка образу кандидата у тимчасовому оточенні', progress=30)
        append_log(f"Крок 2/5: Визначення цільової ревізії {target_ref}...")

        target_commit = subprocess.check_output(
            ['git', 'rev-parse', '--verify', f"{target_ref}^{{commit}}"],
            cwd=str(ROOT),
            text=True
        ).strip()
        current_commit = subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(ROOT),
            text=True
        ).strip()
        append_log(f"Поточний коміт: {current_commit[:10]}, Цільовий коміт: {target_commit[:10]}")

        candidate_image = f"schoolwork2-candidate:{target_commit[:12]}"
        build_workdir = Path(f"/tmp/schoolwork2-build.{target_commit[:8]}")
        if build_workdir.exists():
            subprocess.run(['rm', '-rf', str(build_workdir)], check=False)
        subprocess.run(['git', 'worktree', 'add', '--detach', str(build_workdir), target_commit], cwd=str(ROOT), check=True)

        try:
            append_log(f"Збірка Docker-образу кандидата {candidate_image}...")
            build_proc = subprocess.run(
                ['docker', 'build', '--build-arg', f'VCS_REF={target_commit}', '-t', candidate_image, str(build_workdir)],
                capture_output=True,
                text=True,
                timeout=600
            )
            if build_proc.returncode != 0:
                raise RuntimeError(f"Помилка збірки Docker-образу: {build_proc.stderr or build_proc.stdout}")
            append_log("Docker-образ кандидата успішно зібрано.")
        finally:
            subprocess.run(['git', 'worktree', 'remove', '--force', str(build_workdir)], cwd=str(ROOT), check=False)

        # Step 3: Consistent snapshot / backup
        update_state(step=3, step_name='Створення резервної копії перед оновленням', progress=55)
        append_log("Крок 3/5: Створення повного знімка бази даних, файлів та образу...")
        backup_proc = subprocess.run(
            ['bash', str(ROOT / 'scripts' / 'backup.sh')],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=240
        )
        if backup_proc.returncode != 0:
            raise RuntimeError(f"Помилка створення резервної копії: {backup_proc.stderr or backup_proc.stdout}")
        
        # Extract backup path from backup script output
        backup_path = None
        for line in backup_proc.stdout.strip().splitlines():
            if 'Резервна копія перевірена:' in line:
                backup_path = line.split('Резервна копія перевірена:', 1)[1].strip()
            elif line.strip().startswith('/') and 'backups/' in line:
                backup_path = line.strip()

        if not backup_path:
            # Fallback to the latest directory in backups
            backups_dir = ROOT / 'backups'
            subdirs = [d for d in backups_dir.iterdir() if d.is_dir() and not d.name.startswith('.')]
            if subdirs:
                backup_path = str(max(subdirs, key=lambda d: d.stat().st_mtime))

        append_log(f"Резервну копію збережено: {Path(backup_path).name if backup_path else 'успішно'}")
        update_state(backup_file=backup_path)

        # Step 4: Merge, update image configuration, migrate & restart
        update_state(step=4, step_name='Застосування міграцій та перезапуск сервісів', progress=75)
        append_log("Крок 4/5: Оновлення ревізії Git та конфігурації образу...")

        merge_proc = subprocess.run(
            ['git', 'merge', '--ff-only', target_commit],
            cwd=str(ROOT),
            capture_output=True,
            text=True
        )
        if merge_proc.returncode != 0:
            raise RuntimeError(f"Не вдалося виконати швидке злиття (fast-forward merge): {merge_proc.stderr or merge_proc.stdout}")

        # Update .env image tag
        env_file = ROOT / '.env'
        if env_file.exists():
            content = env_file.read_text(encoding='utf-8')
            if 'SCHOOLWORK_IMAGE=' in content:
                content = re.sub(r'SCHOOLWORK_IMAGE=[^\n]*', f'SCHOOLWORK_IMAGE={candidate_image}', content)
                env_file.write_text(content, encoding='utf-8')

        append_log("Збір статичних файлів (collectstatic)...")
        subprocess.run(
            ['docker', 'compose', 'run', '--rm', '--no-deps', '-T', 'app', 'python', 'manage.py', 'collectstatic', '--noinput'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True
        )

        append_log("Застосування міграцій бази даних...")
        subprocess.run(
            ['docker', 'compose', 'run', '--rm', '--no-deps', '-T', 'app', 'python', 'manage.py', 'migrate', '--noinput'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True
        )

        append_log("Перезапуск сервісів...")
        subprocess.run(
            ['docker', 'compose', 'stop', 'app', 'ai_worker'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False
        )
        subprocess.run(
            ['docker', 'compose', 'up', '-d', '--wait', '--wait-timeout', '180', 'app', 'ai_worker'],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True
        )

        # Step 5: Health check
        update_state(step=5, step_name='Перевірка працездатності системи (/healthz)', progress=90)
        append_log("Крок 5/5: Перевірка працездатності оновленої системи...")

        healthy = False
        new_version = get_current_version()
        test_urls = [
            'http://schoolnet_app:8000/healthz/',
            'http://app:8000/healthz/',
            'http://127.0.0.1:8000/healthz/',
            'http://schoolnet_app:8000/teacher/login/',
            'http://app:8000/teacher/login/',
        ]

        for attempt in range(20):
            time.sleep(2)
            for test_url in test_urls:
                try:
                    with urllib.request.urlopen(test_url, timeout=4) as resp:
                        if resp.status == 200:
                            healthy = True
                            break
                except Exception:
                    pass
            if healthy:
                break

        if not healthy:
            raise RuntimeError("Сервіс не пройшов перевірку справності (healthcheck) після перезапуску.")

        append_log(f"Оновлення успішно завершено! Нова версія: {new_version}")
        update_state(
            state='success',
            step=5,
            step_name='Оновлення успішно завершено',
            progress=100,
            target_version=new_version,
            current_version=new_version
        )

    except Exception as exc:
        err_msg = str(exc)
        append_log(f"ПОМИЛКА ОНОВЛЕННЯ: {err_msg}")
        update_state(
            state='error',
            error=err_msg,
            step_name='Помилка під час оновлення'
        )

        if backup_path and Path(backup_path).exists():
            append_log(f"АВТОМАТИЧНЕ ВІДНОВЛЕННЯ: Повернення до резервної копії {Path(backup_path).name}...")
            try:
                restore_proc = subprocess.run(
                    ['bash', str(ROOT / 'scripts' / 'restore.sh'), backup_path, '--yes', '--no-safety-backup'],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if restore_proc.returncode == 0:
                    append_log("Систему успішно відновлено до попередньої стабільної версії з резервної копії.")
                    update_state(rolled_back=True)
                else:
                    append_log(f"Помилка автоматичного відновлення: {restore_proc.stderr or restore_proc.stdout}")
            except Exception as r_exc:
                append_log(f"Критична помилка при відновленні: {r_exc}")


class UpdaterHTTPHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ('/', '/health', '/healthz'):
            self._send_json(200, {'status': 'ok', 'service': 'updater', 'version': get_current_version()})
        elif self.path in ('/status', '/api/status'):
            with STATE_LOCK:
                st = dict(CURRENT_STATE)
            self._send_json(200, st)
        elif self.path in ('/check', '/api/check'):
            result = check_github_updates()
            self._send_json(200, result)
        else:
            self._send_json(404, {'error': 'Маршрут не знайдено'})

    def do_POST(self):
        content_len = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_len).decode('utf-8') if content_len > 0 else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if self.path in ('/check', '/api/check'):
            result = check_github_updates()
            self._send_json(200, result)

        elif self.path in ('/update', '/api/update'):
            with STATE_LOCK:
                if CURRENT_STATE.get('state') == 'running':
                    self._send_json(409, {'error': 'Оновлення вже виконується'})
                    return
            target_ref = data.get('ref', 'origin/main')
            target_ver = data.get('version')
            thread = threading.Thread(target=execute_update_job, args=(target_ref, target_ver), daemon=True)
            thread.start()
            self._send_json(200, {'ok': True, 'message': 'Оновлення розпочато', 'target_version': target_ver})

        elif self.path in ('/rollback', '/api/rollback'):
            backup = data.get('backup_file') or CURRENT_STATE.get('backup_file')
            if not backup or not Path(backup).exists():
                self._send_json(400, {'error': 'Файл резервної копії не знайдено'})
                return
            def run_rollback():
                append_log(f"Ручний запуск відновлення з {backup}...")
                proc = subprocess.run(
                    ['bash', str(ROOT / 'scripts' / 'restore.sh'), backup, '--yes', '--no-safety-backup'],
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True
                )
                if proc.returncode == 0:
                    append_log("Відновлення успішно завершено.")
                    update_state(state='idle', rolled_back=True, error=None)
                else:
                    append_log(f"Помилка відновлення: {proc.stderr or proc.stdout}")
            threading.Thread(target=run_rollback, daemon=True).start()
            self._send_json(200, {'ok': True, 'message': 'Відновлення запущено'})
        else:
            self._send_json(404, {'error': 'Маршрут не знайдено'})

    def _send_json(self, status, payload):
        text = json.dumps(payload, ensure_ascii=False)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(text.encode('utf-8'))

    def log_message(self, format, *args):
        pass


def run_server(port=8001):
    try:
        subprocess.run(['git', 'config', '--global', '--add', 'safe.directory', '*'], check=False)
    except Exception:
        pass
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('0.0.0.0', port), UpdaterHTTPHandler) as httpd:
        print(f"SchoolNet Updater service listening on 0.0.0.0:{port}...")
        save_status()
        httpd.serve_forever()


if __name__ == '__main__':
    port = int(os.environ.get('UPDATER_PORT', 8001))
    run_server(port)
