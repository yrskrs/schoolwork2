"""Exercise real worker subprocesses against a loopback-only synthetic provider.

Uses a temporary database/media/cache/settings module. Never reads .env secrets
or real submissions. Run with the project's Python dependencies installed.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    counters = {'active': 0, 'peak': 0, 'requests': 0, 'redacted': True, 'models': set(), 'rate_limits': 0}
    lock = threading.Lock()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            wire = json.dumps(payload, ensure_ascii=False)
            with lock:
                counters['active'] += 1
                counters['peak'] = max(counters['peak'], counters['active'])
                counters['requests'] += 1
                counters['models'].add(payload.get('model'))
                counters['redacted'] &= all(marker not in wire for marker in
                    ('PrivacySurname', 'PrivacyGiven', 'student@example.org', 'private-access-token'))
                rate_limit = payload.get('model') == 'model-0' and counters['rate_limits'] == 0
                if rate_limit:
                    counters['rate_limits'] += 1
            if rate_limit:
                body = json.dumps({'error': {'message': 'Synthetic rate limit'}}).encode()
                self.send_response(429)
                self.send_header('Retry-After', '1')
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                with lock:
                    counters['active'] -= 1
                return
            time.sleep(.3)
            result = {'suggested_grade': '9', 'level': 'Достатній', 'summary': 'Розв’язок правильний.',
                      'strengths': ['Правильно виконано обчислення.'], 'weaknesses': [],
                      'feedback_comment': 'Перевір пояснення кроків.', 'grade_explanation': 'Обчислення правильне.',
                      'criteria_results': [{'criterion': 'Обчислення', 'status': 'completed', 'evidence': '2+3=5'}],
                      'gr_results': []}
            body = json.dumps({'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)},
                                           'finish_reason': 'stop'}],
                               'usage': {'prompt_tokens': 700, 'completion_tokens': 150, 'total_tokens': 850}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            with lock:
                counters['active'] -= 1

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='schoolwork-ai-runtime-') as folder:
            temp = Path(folder)
            settings_file = temp / 'synthetic_ai_settings.py'
            settings_file.write_text(
                'from schoolnet.test_settings import *\n'
                f'DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(temp / "test.sqlite3")!r}, "OPTIONS": {{"timeout": 20, "transaction_mode": "IMMEDIATE"}}}}}}\n'
                f'MEDIA_ROOT = {str(temp / "media")!r}\n'
                f'AI_PREPARATION_LOCK_DIR = {str(temp / "locks")!r}\n'
                'AI_JOBS_EAGER = False\nAI_WORKER_CONCURRENCY = 3\nAI_STUDENT_RESERVED = 1\n'
                'AI_PRIVACY_ALLOWED_PROVIDERS = ["custom"]\n'
                'AI_JOB_TIMEOUT = 60\nAI_REQUEST_DEADLINE = 45\n'
                f'CACHES = {{"default": {{"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}, "ai_materials": {{"BACKEND": "django.core.cache.backends.filebased.FileBasedCache", "LOCATION": {str(temp / "cache")!r}}}}}\n')
            os.environ['DJANGO_SETTINGS_MODULE'] = 'synthetic_ai_settings'
            os.environ.pop('DATABASE_URL', None)
            os.environ.update(PYTHON_DOTENV_DISABLED='1', ROSTER_PEERS='[]',
                              ROSTER_SYNC_TOKEN='', JOURNAL_API_BASE_URL='')
            sys.path[:0] = [str(temp), str(ROOT)]
            import django
            django.setup()
            from django.core.management import call_command
            call_command('migrate', verbosity=0)
            from django.contrib.auth.models import User
            from django.core.files.uploadedfile import SimpleUploadedFile
            from feed.models import Teacher, School, ClassGroup, Assignment, AICriteriaPreset, Submission, AISettings, AIJob
            from feed.ai_jobs import enqueue_submission_job
            from feed.ai_connections import save_connection
            from feed.middleware import set_has_admin
            set_has_admin(True)
            user = User.objects.create_superuser('synthetic_teacher', password='synthetic-only')
            School.objects.create(name='Synthetic', admin=user)
            teacher = Teacher.objects.create(user=user, full_name='Synthetic Teacher')
            group = ClassGroup.objects.create(name='7-А', grade=7)
            teacher.classes.add(group)
            preset = AICriteriaPreset.objects.create(name='Synthetic', evaluation_type='traditional')
            assignment = Assignment.objects.create(teacher=teacher, title='Обчислення', description='Обчисли 2+3 і поясни.',
                status='published', default_ai_preset=preset, allow_student_ai_check=True, allow_ai_usage=True)
            assignment.classes.add(group)
            settings = AISettings.objects.create(id=1, unified_model_queue=True, is_enabled=True, saved_models_list='[]')
            for index in range(3):
                settings = save_connection(settings.pk, {'connection_provider': 'custom', 'connection_label': f'Synthetic {index}',
                    'connection_url': f'http://127.0.0.1:{server.server_port}/v1', 'connection_model': f'model-{index}',
                    'quota_group': f'independent-synthetic-{index}', 'quota_concurrency': '1', 'quota_rpm': '100'})
            for index in range(9):
                sub = Submission.objects.create(teacher=teacher, assignment=assignment, class_group=group,
                    first_name='PrivacyGiven', last_name='PrivacySurname',
                    # Separate synthetic identities preserve one self-check per pupil.
                    comment_student='2+3=5. student@example.org',
                    file=SimpleUploadedFile(f'PrivacySurname_{index}.txt', b'2+3=5'))
                kind = 'student_check' if index < 3 else 'batch_check'
                if kind == 'student_check':
                    # The no-account identity is based on names; use a different
                    # class for each synthetic pupil without changing sent PII.
                    sub.last_name = f'PrivacySurname{index}'
                    sub.save(update_fields=['last_name'])
                enqueue_submission_job(sub, kind, user=user)
            env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(temp), str(ROOT)]), PYTHONUNBUFFERED='1')
            worker_log = temp / 'worker.log'
            started = time.monotonic()
            with worker_log.open('w') as log:
                worker = subprocess.Popen([sys.executable, str(ROOT / 'manage.py'), 'process_ai_jobs'],
                    cwd=ROOT, env=env, stdout=log, stderr=log)
                try:
                    deadline = started + 90
                    while AIJob.objects.filter(status__in=('queued', 'running')).exists():
                        if worker.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError('Synthetic worker did not complete: ' + worker_log.read_text()[-3000:])
                        time.sleep(.1)
                finally:
                    worker.terminate()
                    worker.wait(timeout=10)
            statuses = list(AIJob.objects.values_list('status', flat=True))
            assert statuses == ['succeeded'] * 9, (statuses, worker_log.read_text()[-3000:])
            assert counters['peak'] >= 2, counters
            assert counters['redacted'], counters
            assert len(counters['models']) >= 2, counters
            student_jobs = AIJob.objects.filter(kind='student_check')
            assert all(job.reservations.filter(used=True).exists() for job in student_jobs)
            assert all(job.metrics.get('http_ms') and job.metrics.get('process_startup_ms') for job in AIJob.objects.all())
            summary = {'jobs_succeeded': 9, 'http_requests': counters['requests'], 'peak_parallel_http': counters['peak'],
                'received_429_recovered': counters['rate_limits'] == 1,
                'models_used': sorted(counters['models']), 'known_identifiers_removed': counters['redacted'],
                'elapsed_seconds': round(time.monotonic() - started, 3),
                'scope': 'synthetic loopback HTTP; temporary SQLite; real worker subprocesses; no production data'}
            text = json.dumps(summary, ensure_ascii=False, indent=2)
            print(text)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(text + '\n')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
