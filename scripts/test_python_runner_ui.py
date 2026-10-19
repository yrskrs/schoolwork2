"""Browser checks against synthetic Django data and a separately started test broker.

Run through scripts/test_python_runner.py --ui. NODE_BINARY, PLAYWRIGHT_MODULE
and CHROME_BINARY can override the installed browser tools.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
    os.environ['JOURNAL_API_BASE_URL'] = ''
    os.environ['ROSTER_PEERS'] = '[]'
    with tempfile.TemporaryDirectory(prefix='schoolnet-python-ui-') as temporary:
        from django.conf import settings
        settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                                        'NAME': str(Path(temporary) / 'test.sqlite3')}}
        import django
        django.setup()
        from django.contrib.auth.models import User
        from django.contrib.staticfiles.handlers import StaticFilesHandler
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.core.management import call_command
        from django.core.wsgi import get_wsgi_application
        from django.test import Client
        from feed.middleware import set_has_admin
        import feed.middleware
        feed.middleware._safe_log = lambda *args, **kwargs: None
        from feed.models import Assignment, ClassGroup, School, Submission, SubmissionFile, Teacher

        call_command('migrate', verbosity=0, interactive=False)
        user = User.objects.create_superuser('python-browser-test', password='synthetic-only')
        teacher = Teacher.objects.create(user=user, full_name='Учитель Python')
        School.objects.create(name='Тестова школа', admin=user)
        set_has_admin(True)
        group = ClassGroup.objects.create(name='7-А')
        teacher.classes.add(group)
        assignment = Assignment.objects.create(teacher=teacher, title='Програми Python', status='published')
        assignment.classes.add(group)
        programs = {
            'terminal.py': 'print("READY")\nname=input("Name: ")\nprint("HELLO", name)\nprint("<script>window.runnerXss=1</script>")',
            'desktop.py': '''import tkinter as tk
r=tk.Tk(); r.geometry('400x220+40+40')
b=tk.Button(r, text='Click', command=lambda: print('CLICKED', flush=True)); b.pack()
e=tk.Entry(r); e.pack(); e.bind('<Return>', lambda event: print('ENTRY:', e.get(), flush=True))
r.update()
print('BUTTON:', b.winfo_rootx()+10, b.winfo_rooty()+10, flush=True)
print('ENTRY_POS:', e.winfo_rootx()+10, e.winfo_rooty()+10, flush=True)
r.mainloop()
''',
            'other.py': 'print("NEXT_FILE")',
        }
        ids = {}
        for index, (name, code) in enumerate(programs.items()):
            sub = Submission.objects.create(teacher=teacher, assignment=assignment, class_group=group,
                                            first_name='Учень', last_name=f'Python {index}')
            uploaded = SubmissionFile.objects.create(submission=sub, original_name=name,
                file=SimpleUploadedFile(name, code.encode()))
            ids[name] = {'submission': sub.pk, 'file': uploaded.pk}
        companion = SubmissionFile.objects.create(submission_id=ids['terminal.py']['submission'],
            original_name='other.py', file=SimpleUploadedFile('other.py', b'print("NEXT_FILE")'))
        client = Client()
        client.force_login(user)

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        artifacts = Path('/tmp/schoolnet-python-runner-ui')
        artifacts.mkdir(exist_ok=True)
        config = {'baseURL': f'http://127.0.0.1:{server.server_port}', 'ids': ids,
                  'companion': companion.pk, 'artifacts': str(artifacts),
                  'cookie': client.cookies[settings.SESSION_COOKIE_NAME].value,
                  'cookieName': settings.SESSION_COOKIE_NAME,
                  'chrome': os.getenv('CHROME_BINARY') or shutil.which('google-chrome') or '/opt/google/chrome/chrome',
                  'playwright': os.getenv('PLAYWRIGHT_MODULE', 'playwright')}
        path = Path(temporary) / 'browser.json'
        path.write_text(json.dumps(config))
        try:
            subprocess.run([os.getenv('NODE_BINARY', 'node'), str(ROOT / 'scripts/test_python_runner_ui.cjs'), str(path)], check=True)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
