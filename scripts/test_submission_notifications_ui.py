"""Exercise real notice delivery, settings, audio and layouts in headless Chrome.

Uses an isolated temporary database and synthetic users; saves screenshots to
QA_ARTIFACTS (default /tmp/schoolnet-notifications-ui).
"""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from test_cloud_import_ui import CDP


INSTRUMENT = r"""
// Docker --network none has loopback but no adapter, so Chromium reports
// offline. The fixture uses only loopback; simulate its online state explicitly.
Object.defineProperty(navigator, 'onLine', {configurable: true, get: () => !window.noticeOffline});
window.noticePolls = [];
window.noticeFrequencies = [];
const originalFetch = window.fetch;
window.fetch = (...args) => {
  if (String(args[0]).includes('/api/submission-notifications/')) window.noticePolls.push(Date.now());
  return originalFetch(...args);
};
const NativeAudio = window.AudioContext;
if (NativeAudio) window.AudioContext = class extends NativeAudio {
  createOscillator() {
    const node = super.createOscillator();
    const start = node.start.bind(node);
    node.start = (...args) => { window.noticeFrequencies.push(node.frequency.value); return start(...args); };
    return node;
  }
};
if (location.search.includes('noaudio=1')) window.AudioContext = window.webkitAudioContext = undefined;
"""


def main():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
    os.environ['ROSTER_PEERS'] = '[]'
    chrome = os.environ.get('CHROME_BINARY') or shutil.which('google-chrome') or shutil.which('chromium')
    if not chrome:
        raise SystemExit('Chrome/Chromium is required.')
    artifacts = Path(os.environ.get('QA_ARTIFACTS', '/tmp/schoolnet-notifications-ui'))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='schoolnet-notices-ui-') as temporary:
        from django.conf import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
            'NAME': str(Path(temporary) / 'test.sqlite3'),
            'OPTIONS': {'timeout': 30, 'transaction_mode': 'IMMEDIATE'}}}
        import django
        django.setup()
        from django.contrib.auth.models import User
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.test import Client
        from django.utils import timezone
        import feed.middleware
        from feed.models import Assignment, ClassGroup, School, Student, Subject, Teacher
        from feed.submission_notifications import SubmissionNotificationSettings

        call_command('migrate', verbosity=0, interactive=False)
        feed.middleware._safe_log = lambda *args, **kwargs: None
        user = User.objects.create_superuser('synthetic-notice-ui', password='synthetic-only')
        teacher = Teacher.objects.create(user=user, full_name='Тестовий Учитель')
        School.objects.create(name='Тестова школа', admin=user)
        feed.middleware.set_has_admin(True)
        group = ClassGroup.objects.create(name='7-А')
        other_group = ClassGroup.objects.create(name='8-Б')
        student = Student.objects.create(last_name='Шевченко', first_name='Олександра', class_group=group)
        subject = Subject.objects.create(name='Інформатика')
        teacher.classes.add(group, other_group)
        assignment = Assignment.objects.create(teacher=teacher, subject=subject,
            title='Практична робота: створення навчальної презентації з дуже довгою назвою ' * 2,
            status='published', published_at=timezone.now())
        assignment.classes.add(group, other_group)

        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), handler_class=Quiet)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        profile = Path(temporary) / 'chrome'
        browser = subprocess.Popen([chrome, '--headless', '--no-sandbox', '--disable-gpu', '--no-first-run',
            '--disable-background-networking', '--remote-debugging-port=0', '--user-data-dir=' + str(profile),
            'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            port_file = profile / 'DevToolsActivePort'
            for _ in range(200):
                if port_file.exists():
                    break
                time.sleep(.05)
            port = port_file.read_text().splitlines()[0]
            with urlopen(f'http://127.0.0.1:{port}/json/list') as response:
                target = next(item for item in json.load(response) if item['type'] == 'page')
            cdp = CDP(target['webSocketDebuggerUrl'])
            cdp.call('Page.enable'); cdp.call('Runtime.enable'); cdp.call('Network.enable')
            cdp.call('Page.addScriptToEvaluateOnNewDocument', {'source': INSTRUMENT})
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1000, 'deviceScaleFactor': 1, 'mobile': False})
            client = Client(); client.force_login(user)
            cdp.call('Network.setCookie', {'name': settings.SESSION_COOKIE_NAME,
                'value': client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': base})

            def navigate(path):
                cdp.call('Page.navigate', {'url': base + path})
                cdp.until("location.href===" + json.dumps(base + path) + " && document.readyState==='complete' && !!document.querySelector('#submission-notifications')")
                cdp.until('window.noticePolls.length>0')

            def submit(pupil=student):
                response = Client().post(f'/assignment/{assignment.pk}/submit/', {
                    'student_id': pupil.pk, 'full_name': pupil.get_full_name(),
                    'class_group': pupil.class_group_id, 'comment_student': 'Готова робота',
                })
                assert response.status_code == 302, response.content[:1000]

            def click(selector):
                point = cdp.evaluate(f"(() => {{ const e=document.querySelector({json.dumps(selector)}); e.scrollIntoView({{block:'center'}}); const r=e.getBoundingClientRect(); return {{x:r.x+r.width/2,y:r.y+r.height/2}}; }})()")
                cdp.call('Input.dispatchMouseEvent', {'type': 'mousePressed', 'button': 'left', 'clickCount': 1, **point})
                cdp.call('Input.dispatchMouseEvent', {'type': 'mouseReleased', 'button': 'left', 'clickCount': 1, **point})

            def screenshot(name):
                time.sleep(.25)  # Capture the settled card after its entrance animation.
                result = cdp.call('Page.captureScreenshot', {'format': 'png'})
                (artifacts / name).write_bytes(base64.b64decode(result['data']))

            def cards():
                return cdp.evaluate("document.querySelectorAll('.submission-notice').length")

            navigate('/teacher/settings/?tab=environment&section=notifications')
            cdp.evaluate("document.querySelector('#id_teacher_enabled').checked=true; document.querySelector('#id_students_enabled').checked=true; document.querySelector('#id_teacher_sound').checked=true; document.querySelector('#id_students_sound').checked=true; document.querySelector('#id_duration_seconds').value='5'")
            click('#submission-notification-form button[type=submit]')
            cdp.until("location.hash==='#submission-notification-settings' && document.readyState==='complete'")
            cdp.until('window.noticePolls.length>0')
            prefs = SubmissionNotificationSettings.objects.get(teacher=teacher)
            assert prefs.teacher_enabled and prefs.students_sound and prefs.duration_seconds == 5
            # Real user gesture unlocks WebAudio; the example exercises the same card/sound path.
            click('[data-submission-preview="new"]')
            cdp.until("document.querySelector('.submission-notice') && window.noticeFrequencies.length===2")
            assert cdp.evaluate('JSON.stringify(window.noticeFrequencies)') == '[523,784]'
            assert cdp.evaluate("document.querySelector('.submission-notice-status').textContent==='Роботу здано'")
            screenshot('teacher-light-preview.png')
            click('.submission-notice-close')
            cdp.evaluate('window.noticeFrequencies=[]')
            submit()
            cdp.until("document.querySelector('.submission-notice-main[href]')!==null", timeout=9)
            assert cdp.evaluate("document.querySelector('.submission-notice-name').textContent.includes('Шевченко Олександра')")
            assert cdp.evaluate("document.querySelector('.submission-notice-status').textContent==='Роботу здано'")
            assert cdp.evaluate('JSON.stringify(window.noticeFrequencies)') == '[523,784]'
            screenshot('teacher-light-submitted.png')
            cdp.until("document.querySelectorAll('.submission-notice').length===0", timeout=7)
            print('PASS: settings persist, text delivery, first signal and five-second timeout', flush=True)

            prefs.until_dismissed = True; prefs.save()
            submit()
            cdp.until("document.querySelector('.submission-notice.is-resubmission')!==null", timeout=9)
            assert cdp.evaluate('JSON.stringify(window.noticeFrequencies.slice(-3))') == '[660,523,440]'
            assert cdp.evaluate("document.querySelector('.submission-notice-status').textContent.includes('Роботу здано повторно')")
            screenshot('teacher-light-resubmitted.png')
            time.sleep(5.3)
            assert cards() == 1, 'Sticky card expired'
            click('.submission-notice-main')
            cdp.until("location.pathname.startsWith('/teacher/view-file/') && document.readyState==='complete'")
            assert cards() == 0
            print('PASS: repeat signal, persistent card and teacher review navigation', flush=True)

            prefs.students_click_opens_task = False
            prefs.students_all_classes = False
            prefs.students_background_active = False
            prefs.save()
            cdp.call('Page.navigate', {'url': base + '/teacher/logout/'})
            cdp.until("location.pathname==='/' && document.readyState==='complete' && document.querySelector('#submission-notifications')?.dataset.viewer==='student'")
            navigate(f'/assignment/{assignment.pk}/?class={group.pk}')
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
            cdp.evaluate("document.documentElement.dataset.theme='dark'; localStorage.setItem('sn-theme','dark')")
            assert cdp.evaluate("document.querySelector('#submission-notifications').dataset.viewer==='student'")
            cdp.until(f"JSON.parse(sessionStorage.getItem('schoolnet-submission-notices:student:{group.pk}')||'null')?.cursor!==undefined")
            # A real gesture after navigation is required by the browser audio policy.
            click('h1')
            submit()
            cdp.until("document.querySelector('.submission-notice')!==null", timeout=9)
            assert cdp.evaluate("!document.querySelector('.submission-notice-main[href]')")
            assert cdp.evaluate("(() => { const r=document.querySelector('.submission-notice').getBoundingClientRect(); return r.left>=0 && r.right<=innerWidth && r.bottom<=innerHeight; })()")
            assert cdp.evaluate('JSON.stringify(window.noticeFrequencies.slice(-3))') == '[660,523,440]'
            screenshot('student-dark-mobile.png')
            current = cdp.evaluate('location.href')
            click('.submission-notice-main')
            assert cards() == 0 and cdp.evaluate('location.href') == current
            print('PASS: student mobile/dark layout, independent audio and click dismissal', flush=True)

            # Test student click opening assignment
            prefs.students_click_opens_task = True; prefs.save()
            submit()
            cdp.until("document.querySelector('.submission-notice-main[href]')!==null", timeout=9)
            assert cdp.evaluate(f"document.querySelector('.submission-notice-main').getAttribute('href')==='/assignment/{assignment.pk}/'")
            click('.submission-notice-main')
            cdp.until(f"location.pathname==='/assignment/{assignment.pk}/' && document.readyState==='complete'")
            assert cards() == 0
            print('PASS: student click opens task navigation', flush=True)
            prefs.students_click_opens_task = False; prefs.save()
            navigate(f'/assignment/{assignment.pk}/?class={group.pk}')

            # Queue is bounded in the DOM and survives page navigation without replaying sound.
            for _ in range(6):
                submit()
            cdp.until("document.querySelector('.submission-notifications-clear').textContent.includes('Ще 3')", timeout=9)
            assert cards() == 3
            cdp.call('Page.reload')
            cdp.until("document.readyState==='complete' && document.querySelectorAll('.submission-notice').length===3")
            assert cdp.evaluate('window.noticeFrequencies.length') == 0, 'Reload replayed audio'
            screenshot('student-dark-queue.png')
            click('.submission-notifications-clear')
            assert cards() == 0

            # Simulate document visibility to verify the controller stops and resumes polling.
            cdp.evaluate("Object.defineProperty(document,'hidden',{configurable:true,get:()=>window.noticeHidden}); window.noticeHidden=true; document.dispatchEvent(new Event('visibilitychange')); window.hiddenPollCount=window.noticePolls.length")
            submit()
            time.sleep(5.5)
            assert cards() == 0
            assert cdp.evaluate('window.noticePolls.length===window.hiddenPollCount')
            cdp.evaluate("window.noticeHidden=false; document.dispatchEvent(new Event('visibilitychange'))")
            cdp.until("document.querySelector('.submission-notice')!==null")
            click('.submission-notice-close')
            print('PASS: queue, reload deduplication and hidden-tab pause/resume', flush=True)

            prefs.students_enabled = False; prefs.save()
            submit()
            time.sleep(5.5)
            assert cards() == 0, 'Disabled student notices still arrived'
            outsider = Student.objects.create(first_name='Іван', last_name='Коваль', class_group=other_group)
            prefs.students_enabled = True; prefs.students_sound = False; prefs.show_class_badge = True; prefs.save()
            navigate(f'/assignment/{assignment.pk}/?class={group.pk}&noaudio=1')
            submit(outsider)
            time.sleep(5.5)
            assert cards() == 0, 'Notice from another class leaked'
            submit()
            cdp.until("document.querySelector('.submission-notice')!==null", timeout=9)
            assert cdp.evaluate('window.noticeFrequencies.length') == 0
            navigate(f'/?class={group.pk}')
            cdp.evaluate(f"applyFilter('{other_group.pk}',null)")
            cdp.until(f"JSON.parse(sessionStorage.getItem('schoolnet-submission-notices:student:{other_group.pk}')||'null')?.cursor!==undefined")
            submit()
            time.sleep(5.5)
            assert cards() == 0, 'AJAX filter retained the previous class audience'
            submit(outsider)
            cdp.until("document.querySelector('.submission-notice-class')?.textContent==='8-Б'", timeout=9)
            cdp.evaluate("applyFilter('all',null); window.allClassPolls=window.noticePolls.length")
            time.sleep(5.5)
            assert cards() == 0 and cdp.evaluate('window.noticePolls.length===window.allClassPolls')
            assert not cdp.errors, cdp.errors
            print('PASS: live disable, class isolation, AJAX class switching, text without AudioContext; no JS exceptions', flush=True)
            print('Artifacts: ' + str(artifacts), flush=True)
        finally:
            browser.terminate(); browser.wait(timeout=10)
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
