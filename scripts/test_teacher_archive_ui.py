"""Verify teacher menu and ZIP selection in Chrome with isolated temporary data.

Run: venv/bin/python scripts/test_teacher_archive_ui.py --artifacts /tmp/schoolnet-archive-ui
"""
import argparse
import html
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import date, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'

CHECKS = r"""
<script>
window.addEventListener('load', () => setTimeout(async () => {
  const result = document.createElement('pre'); result.id = 'archive-ui-check-result'; document.body.appendChild(result);
  const assert = (ok, message) => { if (!ok) throw new Error(message); };
  const params = new URLSearchParams(location.search);
  const frame = () => new Promise(resolve => setTimeout(resolve, 30));
  try {
    document.documentElement.dataset.theme = params.get('theme');
    applyFontScale(Number(params.get('scale')));
    const button = document.getElementById('user-menu-btn');
    const menu = document.getElementById('user-menu');
    const panel = menu.querySelector('.user-menu-panel');
    button.click();
    await new Promise(resolve => setTimeout(resolve, 200));
    function checkMenu() {
      const bounds = panel.getBoundingClientRect();
      assert(bounds.left >= 7 && bounds.right <= innerWidth - 7, 'Menu fits width: ' + JSON.stringify(bounds));
      assert(bounds.top >= 0 && bounds.bottom <= innerHeight - 7, 'Menu fits height: ' + JSON.stringify(bounds));
      assert(panel.scrollHeight > panel.clientHeight, 'Long menu has its own scrollbar');
      panel.scrollTop = panel.scrollHeight;
      const logout = panel.querySelector('.danger').getBoundingClientRect();
      assert(logout.bottom <= bounds.bottom && logout.top >= bounds.top, 'Logout is reachable at bottom');
    }
    checkMenu();
    panel.querySelector('.font-scale-choice-btn[data-scale="125"]').click();
    await frame();
    assert(menu.classList.contains('open'), 'Font controls keep menu open');
    checkMenu();
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
    assert(!menu.classList.contains('open') && document.activeElement === button, 'Escape closes and restores focus');
    applyFontScale(Number(params.get('scale'))); await frame();
    const form = document.getElementById('assignment-export-form');
    const inputs = Array.from(form.querySelectorAll('input[name="assignments"]'));
    const download = document.getElementById('export-download');
    assert(inputs.length === 6 && new Set(inputs.map(input => input.value)).size === 5, 'Shared task appears in both lesson days');
    assert(download.disabled, 'Empty selection cannot download');
    const group = form.querySelector('.export-day');
    const day = group.querySelector('.export-day-checkbox');
    day.click();
    assert(group.querySelectorAll('input[name="assignments"]:checked').length === 3, 'Whole Tuesday can be selected');
    const shared = inputs.filter(input => input.value === inputs[0].value);
    assert(shared.length === 2 && shared.every(input => input.checked), 'Selecting Tuesday also selects the same task under Monday');
    assert(document.getElementById('export-selected-count').textContent === 'Обрано: 3', 'Shared task is counted once');
    assert(!download.disabled, 'Selection enables download');
    group.querySelector('input[name="assignments"]').click();
    assert(shared.every(input => !input.checked), 'Shared selection is cleared in every day');
    assert(day.indeterminate && !day.checked, 'Partial day selection is shown');
    document.getElementById('export-select-all').click();
    assert(inputs.every(input => input.checked), 'All days can be selected together');
    assert(document.getElementById('export-selected-count').textContent === 'Обрано: 5', 'Selected count is accurate');
    document.getElementById('export-clear-all').click();
    assert(inputs.every(input => !input.checked) && download.disabled, 'Clear removes selection');
    for (const card of document.querySelectorAll('.export-card')) {
      const bounds = card.getBoundingClientRect();
      assert(bounds.left >= 0 && bounds.right <= innerWidth + 1, 'Export card fits width');
      assert(card.scrollWidth <= card.clientWidth + 1, 'Export card has no horizontal overflow');
    }
    assert(window.archiveUiErrors.length === 0, 'No script errors: ' + window.archiveUiErrors.join('; '));
    if (params.get('preview') === 'menu') { button.click(); await frame(); panel.scrollTop = panel.scrollHeight; }
    result.textContent = JSON.stringify({ok: true, width: innerWidth, height: innerHeight, scale: params.get('scale'), theme: params.get('theme')});
    result.style.display = 'none';
  } catch (error) { result.textContent = JSON.stringify({ok: false, error: error.message}); }
  if (window.parent !== window) window.parent.postMessage(JSON.parse(result.textContent), location.origin);
}, 100));
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', default=shutil.which('google-chrome') or shutil.which('chromium'))
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if not args.chrome:
        parser.error('Chrome/Chromium is required')
    import django
    django.setup()
    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.test import Client
    from feed.middleware import set_has_admin
    from feed.models import Assignment, AssignmentScheduleTarget, BellSchedule, ClassGroup, School, Teacher

    call_command('migrate', verbosity=0, interactive=False)
    user = User.objects.create_superuser('archive-ui', password='test-only')
    teacher = Teacher.objects.create(user=user, full_name='Тестовий учитель')
    School.objects.create(name='Тестова школа', admin=user)
    set_has_admin(True)
    group = ClassGroup.objects.create(name='6-Б')
    older = ClassGroup.objects.create(name='10-А')
    slot = BellSchedule.objects.create(lesson_number=2, start_time=time(9, 25), end_time=time(10, 10))
    for index in range(5):
        assignment = Assignment.objects.create(teacher=teacher, title=f'Практична робота {index + 1}: створюємо навчальний проєкт')
        assignment.classes.add(group)
        AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=group, bell_slot=slot,
                                                target_date=date(2026, 10, 13 if index < 3 else 12),
                                                target_day_of_week=2 if index < 3 else 1)
        if index == 0:
            assignment.classes.add(older)
            AssignmentScheduleTarget.objects.create(assignment=assignment, class_group=older, bell_slot=slot,
                                                    target_date=date(2026, 10, 12), target_day_of_week=1)
    client = Client()
    client.force_login(user)
    response = client.get('/teacher/assignments/export-day-zip/')
    assert response.status_code == 200
    markup = response.content.decode().replace('<head>', '<head><script>window.archiveUiErrors=[];window.addEventListener("error", e=>{if(e.message)window.archiveUiErrors.push(e.message)});</script>', 1)
    content = markup.replace('</body>', CHECKS + '</body>').encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == '/teacher/assignments/export-day-zip/':
                body, mime = content, 'text/html; charset=utf-8'
                params = parse_qs(parsed.query)
                if 'frame_width' in params:
                    width = int(params['frame_width'][0])
                    height = int(params['frame_height'][0])
                    inner_url = f"{path}?scale={params['scale'][0]}&theme={params['theme'][0]}&preview={params['preview'][0]}"
                    body = (f'<html><body style="margin:0"><iframe src="{inner_url}" style="border:0;width:{width}px;height:{height}px"></iframe>'
                            '<pre id="archive-ui-check-result" style="display:none"></pre><script>addEventListener("message",event=>{'
                            'if(event.origin===location.origin)document.getElementById("archive-ui-check-result").textContent=JSON.stringify(event.data)})</script></body></html>').encode()
            elif path.startswith('/static/'):
                file = (ROOT / 'static' / unquote(path[8:])).resolve()
                if not file.is_relative_to(ROOT / 'static') or not file.is_file():
                    self.send_error(404)
                    return
                body, mime = file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream'
            else:
                body, mime = b'{}', 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if args.artifacts:
            args.artifacts.mkdir(parents=True, exist_ok=True)
        cases = ((1366, 768, 100, 'light', 'menu'), (1280, 640, 125, 'dark', 'menu'),
                 (1024, 600, 120, 'light', 'export'), (390, 690, 100, 'light', 'export'),
                 (360, 640, 125, 'dark', 'menu'), (640, 360, 125, 'dark', 'menu'))
        for width, height, scale, theme, preview in cases:
            with tempfile.TemporaryDirectory(prefix='archive-ui-chrome-') as profile:
                command = [args.chrome, '--headless', '--no-sandbox', '--disable-gpu', '--no-first-run',
                           '--disable-background-networking', '--force-device-scale-factor=1',
                           '--virtual-time-budget=2500', f'--window-size={max(width, 500)},{height + 160}',
                           f'--user-data-dir={profile}', '--dump-dom']
                if args.artifacts:
                    command.append(f'--screenshot={args.artifacts / f"{preview}-{width}-{scale}-{theme}.png"}')
                command.append(f'http://127.0.0.1:{server.server_port}/teacher/assignments/export-day-zip/?scale={scale}&theme={theme}&preview={preview}&frame_width={width}&frame_height={height}')
                completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
                match = re.search(r'<pre id="archive-ui-check-result"[^>]*>(.*?)</pre>', completed.stdout, re.S)
                if not match:
                    raise AssertionError('Browser did not finish checks: ' + completed.stderr[-2000:])
                result = json.loads(html.unescape(match.group(1)))
                print(result, flush=True)
                if not result['ok']:
                    raise AssertionError(result['error'])
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
