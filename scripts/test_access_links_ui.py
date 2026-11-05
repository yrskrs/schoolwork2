"""Real Chrome/HTTPS access-link QA. Uses only temporary DB, media and browser profile."""
import base64
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from urllib.request import urlopen
from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer
from socketserver import ThreadingMixIn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.test_cloud_import_ui import CDP


def main():
    import dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
    os.environ['JOURNAL_API_BASE_URL'] = ''
    os.environ['ROSTER_PEERS'] = '[]'
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    assert chrome, 'Chrome/Chromium is required'
    artifacts = Path(os.environ.get('QA_ARTIFACTS', '/tmp/schoolnet-access-ui'))
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='schoolnet-access-ui-') as temporary:
        from django.conf import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(Path(temporary) / 'qa.sqlite3'), 'OPTIONS': {'timeout': 30}}}
        import django
        django.setup()
        from django.contrib.auth.models import User
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.test import Client
        from django.urls import reverse
        from django.utils import timezone
        from feed.models import Teacher, ClassGroup, Student, Assignment, AccessLink, TailscaleConnection, Submission, Subject
        call_command('migrate', verbosity=0, interactive=False)
        user = User.objects.create_superuser('qa', password='synthetic-browser-password')
        teacher = Teacher.objects.create(user=user, full_name='Тестовий учитель')
        group = ClassGroup.objects.create(name='7-А')
        teacher.classes.add(group)
        pupil = Student.objects.create(class_group=group, last_name='Петренко', first_name='Олена')
        peer = Student.objects.create(class_group=group, last_name='Іваненко', first_name='Іван')
        subject = Subject.objects.create(name='Інформатика'); teacher.subjects.add(subject)
        assignment = Assignment.objects.create(teacher=teacher, subject=subject, title='Навчальне завдання', status='published', published_at=timezone.now())
        assignment.classes.add(group)
        client = Client(); client.force_login(user)
        application = get_wsgi_application()
        host = 'school.tail123.ts.net'

        def funnel_simulator(environ, start_response):
            environ['wsgi.url_scheme'] = 'https'
            # Reproduce Tailscale's boundary headers; no live daemon or account is touched.
            if environ.get('HTTP_HOST', '').startswith(host):
                environ['HTTP_TAILSCALE_FUNNEL_REQUEST'] = '?1'
                environ['HTTP_X_FORWARDED_PROTO'] = 'https'
                environ['HTTP_X_FORWARDED_FOR'] = '203.0.113.42'
            return application(environ, start_response)

        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        certificate, key = Path(temporary) / 'cert.pem', Path(temporary) / 'key.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key),
                        '-out', str(certificate), '-days', '1', '-subj', '/CN=' + host], check=True, capture_output=True)
        class ThreadedServer(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        server = make_server('127.0.0.1', 0, funnel_simulator, handler_class=Quiet, server_class=ThreadedServer)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        local = f'https://127.0.0.1:{server.server_port}'
        public = f'https://{host}:{server.server_port}'
        profile = Path(temporary) / 'chrome'
        browser = subprocess.Popen([chrome, '--headless', '--no-sandbox', '--disable-gpu', '--no-first-run',
            '--disable-background-networking', '--ignore-certificate-errors', '--no-proxy-server',
            '--host-resolver-rules=MAP ' + host + ' 127.0.0.1', '--remote-debugging-port=0',
            '--user-data-dir=' + str(profile), 'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1100, 'deviceScaleFactor': 1, 'mobile': False})
            cdp.call('Network.setCookie', {'name': settings.SESSION_COOKIE_NAME,
                'value': client.cookies[settings.SESSION_COOKIE_NAME].value, 'url': local})

            def navigate(url, condition):
                cdp.call('Page.navigate', {'url': url}); cdp.until(condition)

            def screenshot(name):
                cdp.evaluate("document.activeElement?.blur()")
                image = cdp.call('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': False})
                (artifacts / name).write_bytes(base64.b64decode(image['data']))

            navigate(local + '/teacher/settings/tailscale/', "document.querySelector('h1')?.textContent==='Tailscale'")
            assert cdp.evaluate("document.body?.innerText.includes('Не підключено')")
            cdp.evaluate("document.querySelector('#id_enabled').checked=true;document.querySelector('#id_public_url').value='https://school.tail123.ts.net';document.querySelector('#id_public_url').form.querySelector('button[type=submit]').click()")
            cdp.until("document.body?.innerText.includes('Підключення увімкнено')")
            assert TailscaleConnection.objects.get(pk=1).enabled
            cdp.evaluate("document.querySelector('button[value=status]').click()")
            cdp.until("document.querySelector('[role=alert]')?.textContent.includes('не підключено')")
            screenshot('tailscale-desktop.png')

            navigate(local + '/teacher/access-links/', "!!document.querySelector('#access-link-form')")
            for kind, name, field, value in [('site', 'Спільний доступ', None, None), ('class', '7-А — урок', 'class_group', group.pk),
                                           ('student', 'Олена — персонально', 'student', pupil.pk), ('assignment', 'Одна робота', 'assignment', assignment.pk)]:
                cdp.evaluate(f"document.querySelector('#id_name').value={json.dumps(name)};document.querySelector('#id_kind').value={json.dumps(kind)};document.querySelector('#id_kind').dispatchEvent(new Event('change'))")
                if field:
                    assert cdp.evaluate(f"!document.querySelector('[data-link-field={field}]').hidden")
                    cdp.evaluate(f"document.querySelector('#id_{field}').value='{value}'")
                cdp.evaluate("document.querySelector('#access-link-form button[type=submit]').click()")
                cdp.until(f"[...document.querySelectorAll('.access-link-card h2')].some(e=>e.textContent==={json.dumps(name)})")
            assert AccessLink.objects.count() == 4
            point = cdp.evaluate("(()=>{const e=document.querySelector('[data-copy]');e.scrollIntoView({block:'center'});const r=e.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
            cdp.call('Input.dispatchMouseEvent', {'type':'mousePressed', 'button':'left', 'clickCount':1, **point})
            cdp.call('Input.dispatchMouseEvent', {'type':'mouseReleased', 'button':'left', 'clickCount':1, **point})
            cdp.until("document.querySelector('#access-copy-status').textContent.includes('скопійовано')")
            cdp.evaluate("document.querySelector('.access-qr').open=true")
            cdp.until("document.querySelector('.access-qr img').complete && document.querySelector('.access-qr img').naturalWidth>0")
            screenshot('access-links-desktop.png')
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': True})
            cdp.until("document.documentElement.clientWidth===390")
            assert cdp.evaluate('document.documentElement.scrollWidth<=document.documentElement.clientWidth+1'), 'Mobile horizontal overflow'
            screenshot('access-links-mobile.png')
            cdp.call('Emulation.setDeviceMetricsOverride', {'width': 1440, 'height': 1100, 'deviceScaleFactor': 1, 'mobile': False})
            cdp.evaluate("document.querySelector('#id_allow_without_link').checked=true;document.querySelector('#public-access-mode-form button').click()")
            cdp.until("document.querySelector('#id_allow_without_link')?.checked===true && document.body?.innerText.includes('Відкритий доступ')")
            navigate(public + '/', "document.body?.innerText.includes('Навчальне завдання')")
            assert 'schoolnet_public_access' not in cdp.evaluate('document.cookie')
            navigate(local + '/teacher/access-links/', "!!document.querySelector('#public-access-mode-form')")
            cdp.evaluate("document.querySelector('#id_allow_without_link').checked=false;document.querySelector('#public-access-mode-form button').click()")
            cdp.until("document.querySelector('#id_allow_without_link')?.checked===false")
            navigate(public + '/', "document.body?.innerText.includes('Потрібне посилання')")
            personal = AccessLink.objects.get(kind='student')
            navigate(public + '/', "document.body?.innerText.includes('Потрібне посилання')")
            navigate(public + reverse('access_enter', args=[personal.token]), "!!document.querySelector('.access-open')")
            assert personal.visits == 0
            cdp.evaluate("document.querySelector('.access-open').click()")
            cdp.until("location.pathname==='/' && document.body?.innerText.includes('Навчальне завдання')")
            screenshot('student-feed.png')
            navigate(public + reverse('submit_assignment', args=[assignment.pk]), "!!document.querySelector('#id_full_name')")
            cdp.until("document.readyState==='complete'")
            assert cdp.evaluate("document.querySelector('#id_full_name').disabled && document.querySelector('#id_full_name').value==='Петренко Олена'")
            assert cdp.evaluate("document.querySelector('#id_class_group').disabled")
            assert cdp.evaluate("getComputedStyle(document.querySelector('#student-manual-input-wrapper')).display!=='none'")
            screenshot('personal-submission.png')
            cdp.evaluate("document.querySelector('#id_comment_student').value='Відповідь із браузера';document.querySelector('#id_comment_student').dispatchEvent(new Event('input',{bubbles:true}))")
            cdp.evaluate("document.querySelector('#id_full_name').form.requestSubmit()")
            cdp.until("location.pathname.includes('/submit/success/')")
            saved = Submission.objects.latest('pk')
            assert saved.student_id == pupil.pk and saved.class_group_id == group.pk
            personal.refresh_from_db(); assert personal.visits == 1 and personal.browsers.count() == 1
            # Revoke through the teacher UI using its independent LAN session.
            navigate(local + '/teacher/access-links/', "!!document.querySelector('#access-link-form')")
            cdp.evaluate(f"document.querySelector('input[name=link_id][value=\"{personal.pk}\"]').form.querySelector('button[value=revoke]').click()")
            cdp.until(f"[...document.querySelectorAll('.access-link-card')].some(e=>e.querySelector('h2').textContent==='Олена — персонально' && e.innerText.includes('Скасоване'))")
            navigate(public + '/', "document.body?.innerText.includes('Потрібне посилання')")
            screenshot('revoked-access.png')
            personal.refresh_from_db(); assert personal.revoked_at is not None
            result = {'passed': True, 'links_created': 4, 'real_browser_submission': saved.pk,
                      'revocation': True, 'open_mode_on_off': True, 'mobile_width': 390, 'screenshots': sorted(p.name for p in artifacts.glob('*.png')),
                      'js_errors': cdp.errors}
            (artifacts / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False))
            cdp.context.__exit__(None, None, None)
        finally:
            browser.terminate(); browser.wait(timeout=15)
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
