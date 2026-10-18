"""Run real browser workflows against an isolated Django database and media folder.

Usage: venv/bin/python scripts/test_redesign_ui.py --artifacts /tmp/redesign-ui
Requires Node, Playwright and Chrome; no school data or external AI is used.
"""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifacts', type=Path, default=Path('/tmp/schoolnet-redesign-ui'))
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--playwright', default=os.environ.get('PLAYWRIGHT_MODULE', 'playwright'))
    parser.add_argument('--chrome', default=shutil.which('google-chrome') or shutil.which('chromium'))
    args = parser.parse_args()
    if not args.node or not args.chrome:
        parser.error('Pass --node and --chrome when they are not on PATH')
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='schoolnet-redesign-db-') as temporary:
        os.environ['DJANGO_SETTINGS_MODULE'] = 'schoolnet.test_settings'
        os.environ['JOURNAL_API_BASE_URL'] = ''
        os.environ['ROSTER_PEERS'] = '[]'
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
        from django.template import engines
        from django.utils import timezone
        from feed.middleware import set_has_admin
        from feed.models import Assignment, AssignmentFile, ClassGroup, School, Student, Subject, Teacher, Submission, BellSchedule, TeacherLessonSchedule, AIRequestLog

        call_command('migrate', verbosity=0, interactive=False)
        user = User.objects.create_superuser('redesign-test', password='local-browser-test-only')
        teacher = Teacher.objects.create(user=user, full_name='Тестовий учитель')
        School.objects.create(name='Школа для перевірки дизайну', admin=user)
        set_has_admin(True)
        groups = [ClassGroup.objects.create(name=name) for name in ('5А', '6А', '7А', '8А', '9А', '9Б', '10А', '10-В', '11А')]
        subject = Subject.objects.create(name='Інформатика')
        teacher.classes.add(*groups)
        teacher.subjects.add(subject)
        from datetime import time
        slot = BellSchedule.objects.create(lesson_number=1, start_time=time(8, 30), end_time=time(9, 15))
        TeacherLessonSchedule.objects.create(teacher=teacher, class_group=groups[0], subject=subject, bell_slot=slot, day_of_week=1)
        students = [Student.objects.create(class_group=group, last_name='Тестовий', first_name='Учень')
                    for group in groups]
        Student.objects.create(class_group=groups[0], last_name='Тестова', first_name='Співавторка')
        assignment = Assignment.objects.create(
            teacher=teacher, subject=subject,
            title='Практична робота: створення навчального проєкту з довгою назвою та перевіркою результатів',
            description='<ol>' + ''.join('<li>Виконай вправу та збережи результат. '
                                        'Перевір правильність оформлення документа.</li>' for _ in range(14)) + '</ol>',
            status=Assignment.STATUS_PUBLISHED, published_at=timezone.now())
        assignment.classes.add(*groups)
        material = AssignmentFile.objects.create(assignment=assignment,
            original_name='Довга назва навчального матеріалу для самостійного опрацювання.txt',
            file=SimpleUploadedFile('teacher-material.txt', 'Навчальний матеріал'.encode()))
        more_materials = []
        for index in (2, 3):
            more_materials.append(AssignmentFile.objects.create(assignment=assignment,
                original_name=f'Навчальний матеріал {index}.txt',
                file=SimpleUploadedFile(f'teacher-material-{index}.txt', f'Матеріал номер {index}'.encode())))
        second = Assignment.objects.create(teacher=teacher, subject=subject, title='Завдання іншого класу',
            description='Друга вправа', status=Assignment.STATUS_PUBLISHED, published_at=timezone.now())
        second.classes.add(groups[1])
        portal_assignment = Assignment.objects.create(teacher=teacher, subject=subject, title='Матеріали для пошуку робіт',
            description='Перевірка пошуку', status=Assignment.STATUS_PUBLISHED, published_at=timezone.now())
        portal_assignment.classes.add(*groups[:2])
        existing_submissions = [Submission.objects.create(assignment=portal_assignment, teacher=teacher,
            class_group=groups[0], student=students[0], last_name='Тестовий', first_name='Учень').pk,
            Submission.objects.create(assignment=portal_assignment, teacher=teacher,
            class_group=groups[1], last_name='Інший', first_name='Користувач').pk]
        AIRequestLog.objects.create(provider='gemini', model_name='test-model', action='evaluation',
                                    prompt_tokens=20, completion_tokens=5, total_tokens=25)
        template_count = 0
        for path in (ROOT / 'templates').rglob('*.html'):
            engines['django'].get_template(str(path.relative_to(ROOT / 'templates')))
            template_count += 1

        class QuietHandler(WSGIRequestHandler):
            def log_message(self, *args):
                pass

        server = make_server('127.0.0.1', 0, StaticFilesHandler(get_wsgi_application()), handler_class=QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        config = {'baseURL': f'http://127.0.0.1:{server.server_port}', 'version': (ROOT / 'VERSION').read_text().strip(), 'assignment': assignment.pk,
                  'classId': groups[0].pk, 'otherClassId': groups[1].pk, 'studentId': students[0].pk,
                  'materialId': material.pk, 'moreMaterialIds': [item.pk for item in more_materials], 'artifacts': str(args.artifacts.resolve()),
                  'playwright': args.playwright, 'chrome': args.chrome, 'templates': template_count}
        configuration = Path(temporary) / 'browser.json'
        configuration.write_text(json.dumps(config))
        try:
            subprocess.run([args.node, str(ROOT / 'scripts/test_redesign_ui.cjs'), str(configuration)], check=True)
            saved = Submission.objects.exclude(pk__in=existing_submissions).get(primary_submission__isnull=True)
            assert saved.student_id == students[0].pk
            assert saved.class_group_id == groups[0].pk
            assert saved.files.count() == 1
            print(f'Validated {template_count} templates; browser upload saved to temporary database/media only.')
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
