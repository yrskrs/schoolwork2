"""Small real-provider benchmark on synthetic work in a temporary database.

Configuration is supplied read-only on the server; no production submissions,
users or grades are read or changed. API credentials are never printed.
"""
import argparse
import copy
import io
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    exported = json.loads(args.config.read_text())
    secrets = [connection.get('api_key', '') for connection in exported.get('provider_connections', [])]
    secrets += [exported.get('api_key', ''), exported.get('backup_api_key', '')]

    def sanitized(text):
        for secret in secrets:
            if secret:
                text = text.replace(secret, '[KEY]')
        return text

    with tempfile.TemporaryDirectory(prefix='schoolnet-live-ai-synthetic-') as temporary:
        folder = Path(temporary)
        module = folder / 'live_ai_settings.py'
        module.write_text('from schoolnet.test_settings import *\n'
            f'DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(folder / "trial.sqlite3")!r}, "OPTIONS": {{"timeout": 20, "transaction_mode": "IMMEDIATE"}}}}}}\n'
            f'MEDIA_ROOT = {str(folder / "media")!r}\n'
            f'AI_PREPARATION_LOCK_DIR = {str(folder / "locks")!r}\n'
            'AI_JOBS_EAGER = False\nAI_REQUEST_DEADLINE = 150\n'
            'CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}, "ai_materials": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}\n')
        os.environ.pop('DATABASE_URL', None)
        os.environ.update(DJANGO_SETTINGS_MODULE='live_ai_settings', PYTHON_DOTENV_DISABLED='1',
                          ROSTER_PEERS='[]', ROSTER_SYNC_TOKEN='', JOURNAL_API_BASE_URL='')
        sys.path.insert(0, str(folder))
        import django
        django.setup()
        from django.core.management import call_command
        call_command('migrate', verbosity=0)
        from django.contrib.auth.models import User
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.db import close_old_connections, connections
        from feed.models import School, Teacher, ClassGroup, AICriteriaPreset, Assignment, Submission, AISettings
        from feed.ai_connections import model_queue
        from feed.gemini_service import evaluate_submission_with_gemini
        user = User.objects.create_superuser('synthetic_live_teacher', password='synthetic-only')
        School.objects.create(name='Синтетичний стенд', admin=user)
        teacher = Teacher.objects.create(user=user, full_name='Синтетичний учитель')
        group = ClassGroup.objects.create(name='10-А', grade=10)
        teacher.classes.add(group)
        preset = AICriteriaPreset.objects.create(name='Синтетична розбаловка', evaluation_type='traditional')
        assignment = Assignment.objects.create(teacher=teacher, title='Додавання двох чисел у Python',
            description='Напиши програму Python, що зчитує два цілі числа та виводить їх суму.',
            custom_criteria='Усього 12 балів: правильне зчитування двох цілих чисел — 3; '
                'саме додавання цих чисел — 6; виведення обчисленого результату — 3. '
                'За віднімання замість додавання за другий критерій 0. Додаткових вимог немає.',
            teacher_ai_instructions='Перевір код статично; запуск не вимагається. Не штрафуй за стислість, '
                'оформлення, відсутність пояснень або ШІ. Застосуй лише задані три критерії та їх ваги.',
            status='published', allow_ai_usage=True, default_ai_preset=preset)
        assignment.classes.add(group)
        allowed = {field.name for field in AISettings._meta.fields} - {'id', 'updated_at'}
        values = {key: value for key, value in exported.items() if key in allowed}
        values.update(is_enabled=True, privacy_mode='strict', default_thinking_mode=False)
        settings = AISettings.objects.create(id=1, **values)
        if not settings.unified_model_queue:
            settings.import_legacy_connections()
        configs = settings.get_request_configs()
        profiles = []
        for provider in ('gemini', 'groq', 'openrouter'):
            config = next((row for row in configs if row['provider'] == provider), None)
            if config:
                profile = copy.deepcopy(settings)
                profile.saved_models_list = json.dumps([row for row in model_queue(settings)
                    if row['connection_id'] == config['connection_id'] and row['name'] == config['model']])
                profile.auto_failover_enabled = False
                profiles.append((config, profile))
        if not profiles:
            raise RuntimeError('No configured enabled provider was eligible.')
        jobs = []
        for case, operator, expected in [('correct', '+', '12'), ('incorrect', '-', '6')]:
            for config, profile in profiles:
                sub = Submission.objects.create(assignment=assignment, teacher=teacher, class_group=group,
                    first_name='Синтетичний', last_name='Учасник',
                    file=SimpleUploadedFile(f'{case}.py', f'a = int(input())\nb = int(input())\nprint(a {operator} b)\n'.encode()))
                jobs.append((case, expected, config, profile, sub.pk))

        def assess(job):
            case, expected, config, profile, identity = job
            close_old_connections()
            started = time.monotonic()
            try:
                sub = Submission.objects.select_related('assignment', 'teacher', 'class_group').get(pk=identity)
                result = evaluate_submission_with_gemini(sub, ai_settings=profile)
                grade = str(result.get('suggested_grade', ''))
                return {'case': case, 'provider': config['provider'], 'model': config['model'],
                    'status': result.get('status'), 'grade': grade, 'expected_grade': expected,
                    'rubric_matches': result.get('status') == 'success' and grade == expected,
                    'seconds': round(time.monotonic() - started, 3),
                    'error': sanitized(str(result.get('error') or ''))[:500],
                    'usage': result.get('request_metadata') or {}}
            finally:
                connections.close_all()

        report = {'scope': 'real API calls, synthetic work only; temporary SQLite; production unchanged',
                  'results': [], 'rounds': []}
        first_started = None
        for case in ('correct', 'incorrect'):
            if first_started is not None:
                # Leave a full minute between starts to avoid spending two free
                # input-token windows at once. Provider headers remain authoritative.
                while time.monotonic() - first_started < 65:
                    time.sleep(1)
            started = time.monotonic()
            first_started = started
            print(json.dumps({'stage': 'real-api', 'case': case, 'providers': len(profiles)}), flush=True)
            with ThreadPoolExecutor(max_workers=len(profiles)) as pool:
                rows = list(pool.map(assess, [job for job in jobs if job[0] == case]))
            report['results'].extend(rows)
            report['rounds'].append({'case': case, 'wall_seconds': round(time.monotonic() - started, 3),
                'sum_individual_seconds': round(sum(row['seconds'] for row in rows), 3)})
            args.output.write_text(sanitized(json.dumps(report, ensure_ascii=False, indent=2)) + '\n')
            print(sanitized(json.dumps({'case': case, 'results': rows}, ensure_ascii=False)), flush=True)
        report['all_requests_succeeded'] = all(row['status'] == 'success' for row in report['results'])
        report['rubric_consistent'] = all(row['rubric_matches'] for row in report['results'])
        args.output.write_text(sanitized(json.dumps(report, ensure_ascii=False, indent=2)) + '\n')
        print(json.dumps({'all_requests_succeeded': report['all_requests_succeeded'],
                          'rubric_consistent': report['rubric_consistent']}), flush=True)


if __name__ == '__main__':
    main()
