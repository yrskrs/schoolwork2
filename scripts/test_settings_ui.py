"""Exercise rendered settings/help in headless Chrome, using only temporary data.

Run: venv/bin/python scripts/test_settings_ui.py --artifacts /tmp/schoolnet-settings-ui
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'

CHECKS = r"""
<script>
window.addEventListener('load', () => setTimeout(() => {
  const result = document.createElement('pre');
  result.id = 'settings-ui-check-result';
  document.body.appendChild(result);
  const assert = (condition, message) => { if (!condition) throw new Error(message); };
  const byId = id => document.getElementById(id);
  const desiredTheme = new URLSearchParams(location.search).get('theme') || 'light';
  const preview = new URLSearchParams(location.search).get('preview') || 'guide';
  const help = Array.from(document.querySelectorAll('#teacher-page-tools button')).find(button => button.textContent.includes('Довідка налаштувань'));
  const openHelp = () => { byId('teacher-page-tools').open = true; help.focus(); help.click(); };
  const visibleTopic = () => document.querySelector('.guide-card-active').dataset.category;
  try {
    assert(help, 'Settings help button must exist');
    assert(!byId('guide-modal'), 'Help stays deferred until opened');
    for (const [tab, category] of [['profile', 'teacher_settings'], ['environment', 'teacher_settings'], ['ai', 'teacher_ai_settings'], ['api', 'teacher_api_settings']]) {
      switchSettingsTab(tab);
      openHelp();
      assert(byId('guide-modal').style.display === 'flex', 'Help opens for ' + tab);
      assert(visibleTopic() === category, 'Correct topic for ' + tab);
      assert(document.querySelectorAll('.guide-card-open').length === 1, 'Only current topic starts expanded');
      const body = byId('body-guide-' + category.replaceAll('_', '-'));
      assert(body.style.display === 'block', 'Current topic is expanded');
      document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
      assert(byId('guide-modal').style.display === 'none', 'Escape closes help');
      assert(document.activeElement === help, 'Focus returns to help button');
    }
    openHelp();
    const search = byId('guide-search-input');
    search.value = 'невідоматема123'; search.dispatchEvent(new Event('input'));
    assert(byId('guide-no-results').style.display === 'block', 'Empty search result is visible');
    search.value = 'синхронізація'; search.dispatchEvent(new Event('input'));
    assert(byId('guide-category-teacher-api-settings').style.display !== 'none', 'API topic is searchable');
    switchGuideTab('student');
    assert(search.value === '', 'Changing roles clears search');
    assert(byId('guide-pane-teacher').style.display === 'none', 'Teacher topics hidden on student tab');
    switchGuideTab('teacher');
    collapseAllGuideCategories();
    assert(document.querySelectorAll('#guide-pane-teacher .guide-card-open').length === 0, 'Collapse all works');
    expandAllGuideCategories();
    assert(document.querySelectorAll('#guide-pane-teacher .guide-card-open').length > 3, 'Expand all works');
    byId('guide-modal').click();
    assert(byId('guide-modal').style.display === 'none', 'Backdrop closes help');
    history.replaceState(null, '', '/teacher/updates/');
    openGuideModal();
    assert(visibleTopic() === 'teacher_updates', 'Updates page selects its guide');
    closeGuideModal();
    history.replaceState(null, '', '/teacher/settings/?theme=' + desiredTheme);
    switchSettingsTab('environment'); openHelp();
    document.documentElement.setAttribute('data-theme', desiredTheme);
    const dialog = document.querySelector('.guide-modal-dialog');
    const bounds = dialog.getBoundingClientRect();
    assert(bounds.left >= 0 && bounds.right <= innerWidth + 1, 'Dialog fits viewport width');
    assert(bounds.top >= 0 && bounds.bottom <= innerHeight + 1, 'Dialog fits viewport height');
    const accordion = byId('guide-accordion-container');
    assert(accordion.clientHeight > 100, 'Help has a usable scroll area');
    assert(accordion.scrollHeight > accordion.clientHeight, 'Long help scrolls');
    const close = dialog.querySelector('button[onclick="closeGuideModal()"]');
    close.focus();
    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true}));
    assert(document.activeElement === dialog.querySelectorAll('button[onclick="closeGuideModal()"]')[1], 'Focus stays inside dialog');
    assert(window.settingsUiErrors.length === 0, 'No JavaScript errors: ' + window.settingsUiErrors.join('; '));
    if (preview === 'storage') {
      closeGuideModal();
      const grid = document.querySelector('.environment-storage-grid');
      window.scrollTo({top: 0, behavior: 'instant'});
      for (const card of grid.children) {
        const rect = card.getBoundingClientRect();
        assert(rect.left >= 0 && rect.right <= innerWidth + 1, 'Storage card fits viewport width');
        assert(card.scrollWidth <= card.clientWidth + 1, 'Storage card does not overflow');
      }
    }
    result.textContent = JSON.stringify({ok: true, width: innerWidth, height: innerHeight, topic: visibleTopic(), theme: document.documentElement.dataset.theme, preview});
    result.style.display = 'none';
  } catch (error) {
    result.textContent = JSON.stringify({ok: false, error: error.message});
  }
}, 100));
</script>
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--chrome', default=shutil.which('google-chrome') or shutil.which('chromium'))
    parser.add_argument('--artifacts', type=Path)
    args = parser.parse_args()
    if not args.chrome:
        parser.error('Chrome/Chromium is required; pass --chrome /path/to/browser')

    import django
    django.setup()
    from django.contrib.auth.models import User
    from django.core.management import call_command
    from django.test import Client
    from feed.middleware import set_has_admin
    from feed.models import School, Teacher

    call_command('migrate', verbosity=0, interactive=False)
    user = User.objects.create_superuser('ui-test-only', password='temporary-test-only')
    Teacher.objects.create(user=user, full_name='Тестовий учитель')
    School.objects.create(name='Тестова школа', admin=user)
    set_has_admin(True)
    client = Client()
    client.force_login(user)
    pages = {}
    for tab in ('profile', 'environment', 'ai', 'api'):
        response = client.get('/teacher/settings/', {'tab': tab})
        assert response.status_code == 200
        markup = response.content.decode()
        markup = markup.replace('<head>', '<head><script>window.settingsUiErrors=[];window.addEventListener("error",e=>{if(e.message)window.settingsUiErrors.push(e.message)});</script>', 1)
        pages[tab] = markup.replace('</body>', CHECKS + '</body>').encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == '/teacher/settings/':
                content = pages[parse_qs(parsed.query).get('tab', ['profile'])[0]]
                mime = 'text/html; charset=utf-8'
            elif parsed.path.startswith('/static/'):
                path = (ROOT / 'static' / unquote(parsed.path[8:])).resolve()
                if not path.is_relative_to(ROOT / 'static') or not path.is_file():
                    self.send_error(404)
                    return
                content = path.read_bytes()
                mime = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            else:
                content, mime = b'{}', 'application/json'
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        if args.artifacts:
            args.artifacts.mkdir(parents=True, exist_ok=True)
        cases = ((1440, 1000, 'light', 'profile', 'guide'), (500, 900, 'light', 'ai', 'guide'),
                 (500, 900, 'dark', 'api', 'guide'), (1440, 1800, 'light', 'environment', 'storage'),
                 (500, 2200, 'dark', 'environment', 'storage'))
        for width, height, theme, tab, preview in cases:
            with tempfile.TemporaryDirectory(prefix='schoolnet-ui-chrome-') as profile:
                command = [args.chrome, '--headless', '--no-sandbox', '--disable-gpu', '--no-first-run',
                           '--disable-background-networking', '--hide-scrollbars', '--virtual-time-budget=2000',
                           '--force-device-scale-factor=1', f'--window-size={width},{height}',
                           f'--user-data-dir={profile}', '--dump-dom']
                if args.artifacts:
                    command.append(f'--screenshot={args.artifacts / f"{preview}-{width}-{theme}.png"}')
                command.append(f'http://127.0.0.1:{server.server_port}/teacher/settings/?tab={tab}&theme={theme}&preview={preview}')
                completed = subprocess.run(command, capture_output=True, text=True, timeout=40)
                match = re.search(r'<pre id="settings-ui-check-result"[^>]*>(.*?)</pre>', completed.stdout, re.S)
                if not match:
                    raise AssertionError('Browser did not complete checks: ' + completed.stderr[-2000:])
                result = json.loads(html.unescape(match.group(1)))
                print(result, flush=True)
                if not result['ok']:
                    raise AssertionError(result['error'])
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
