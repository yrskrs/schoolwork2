import base64
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from runner.dependencies import discover_requirements, validate_bundle
from .middleware import set_has_admin
from .models import Assignment, ClassGroup, School, Submission, SubmissionFile, Teacher
from .python_runner import RunnerError, SALT, broker_request, submission_bundle


class DependencyTests(SimpleTestCase):
    def test_static_discovery_ignores_stdlib_and_local_modules_and_respects_pins(self):
        files = {'main.py': b'import os, tkinter, turtle, helper\nfrom PIL import Image\nimport yaml\n',
                 'helper.py': b'import math\n', 'requirements.txt': b'Pillow==11.3.0\n# note\n'}
        self.assertEqual(discover_requirements(files), ['Pillow==11.3.0', 'PyYAML'])

    def test_discovery_never_executes_code_and_honors_source_encoding(self):
        code = '# coding: cp1251\n# Привіт\nimport pygame\nraise RuntimeError("no")'.encode('cp1251')
        self.assertEqual(discover_requirements({'a.py': code}), ['pygame'])
        self.assertEqual(discover_requirements({'a.py': b'invalid python!'}), [])

    def test_rejects_pip_options_urls_and_directory_traversal(self):
        for value in ('--index-url https://evil.test', '-r other.txt', 'a @ https://evil.test/a.whl',
                      'git+https://evil.test/a', 'thing; python_version > "3"'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                discover_requirements({'requirements.txt': value.encode()})
        for name in ('../a.py', '/a.py', 'a/../../b.py', '.env', 'a\\b.py', 'a//b.py'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_bundle({'entry': name, 'files': [{'name': name, 'content': 'cGFzcw=='}]})

    def test_duplicate_names_and_non_python_entry_are_rejected(self):
        file = {'name': 'a.py', 'content': 'cGFzcw=='}
        with self.assertRaises(ValueError):
            validate_bundle({'entry': 'a.py', 'files': [file, file]})
        with self.assertRaises(ValueError):
            validate_bundle({'entry': 'a.txt', 'files': [{'name': 'a.txt', 'content': 'cGFzcw=='}]})


@override_settings(CODE_RUNNER_URL='http://runner.invalid:8002', CODE_RUNNER_TOKEN='t' * 40)
class PythonReviewTests(TestCase):
    def setUp(self):
        set_has_admin(True)
        user = User.objects.create_user('python-teacher', password='test')
        self.teacher = Teacher.objects.create(user=user, full_name='Учитель Python')
        School.objects.create(name='Тест', admin=user)
        self.group = ClassGroup.objects.create(name='7-А')
        self.teacher.classes.add(self.group)
        assignment = Assignment.objects.create(teacher=self.teacher, title='Python', status='published')
        assignment.classes.add(self.group)
        self.sub = Submission.objects.create(teacher=self.teacher, assignment=assignment,
                                            class_group=self.group, first_name='Учень', last_name='Python')
        self.main = SubmissionFile.objects.create(submission=self.sub, original_name='main.py',
            file=SimpleUploadedFile('main.py', b'from helper import answer\nprint(answer)'))
        self.helper = SubmissionFile.objects.create(submission=self.sub, original_name='helper.py',
            file=SimpleUploadedFile('helper.py', b'answer = 42'))
        self.url = reverse('python_run', args=[self.sub.pk])
        self.client.force_login(user)

    def post(self, data):
        return self.client.post(self.url, json.dumps(data), content_type='application/json')

    def grant(self, **overrides):
        return signing.dumps({'job': 'a' * 32, 'user': self.teacher.user_id,
                              'submission': self.sub.pk, **overrides}, salt=SALT)

    @patch('feed.python_runner.broker_request')
    def test_start_uses_selected_file_and_only_this_submissions_files(self, broker):
        broker.return_value = {'id': 'a' * 32, 'state': 'preparing'}
        response = self.post({'action': 'start', 'file_id': self.helper.pk,
                              'code': 'print("ignore browser-supplied code")'})
        self.assertEqual(response.status_code, 201)
        path, payload = broker.call_args.args
        self.assertEqual(path, '/runs')
        self.assertEqual(payload['entry'], 'helper.py')
        self.assertEqual(payload['owner'], str(self.teacher.user_id))
        self.assertEqual({f['name'] for f in payload['files']}, {'main.py', 'helper.py'})
        self.assertEqual(base64.b64decode(payload['files'][1]['content']), b'answer = 42')
        self.assertEqual(signing.loads(response.json()['token'], salt=SALT)['submission'], self.sub.pk)

    @patch('feed.python_runner.broker_request')
    def test_unauthenticated_other_teacher_and_foreign_file_cannot_start(self, broker):
        self.client.logout()
        self.assertEqual(self.post({'action': 'start'}).status_code, 403)
        user = User.objects.create_user('other-teacher')
        Teacher.objects.create(user=user, full_name='Інший')
        self.client.force_login(user)
        self.assertEqual(self.post({'action': 'start'}).status_code, 404)
        self.client.force_login(self.teacher.user)
        self.assertEqual(self.post({'action': 'start', 'file_id': '99999'}).status_code, 404)
        broker.assert_not_called()

    @patch('feed.python_runner.broker_request')
    def test_tokens_are_bound_to_teacher_and_submission_for_all_actions(self, broker):
        for token in ('invalid', self.grant(user=99999), self.grant(submission=99999)):
            for method in ('status', 'input', 'stop'):
                response = self.client.get(self.url, {'token': token}) if method == 'status' else self.post(
                    {'action': method, 'token': token, 'input': {'type': 'stdin', 'text': 'a'}})
                self.assertEqual(response.status_code, 404)
        broker.assert_not_called()

    @patch('feed.python_runner.broker_request')
    def test_poll_input_and_stop_forward_to_private_service(self, broker):
        broker.return_value = {'state': 'running', 'output': 'hello', 'offset': 5}
        token = self.grant()
        response = self.client.get(self.url, {'token': token, 'cursor': '4', 'frame': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(broker.call_args.args, ('/runs/' + 'a' * 32,
            {'owner': str(self.teacher.user_id), 'cursor': '4', 'frame': '1'}, 'GET'))
        for action in ('input', 'stop'):
            self.assertEqual(self.post({'action': action, 'token': token,
                                        'input': {'type': 'stdin', 'text': 'yes\n'}}).status_code, 200)
            self.assertTrue(broker.call_args.args[0].endswith('/' + action))

    def test_csrf_is_required_for_start_input_and_stop(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.teacher.user)
        for action in ('start', 'input', 'stop'):
            self.assertEqual(client.post(self.url, json.dumps({'action': action, 'token': self.grant()}),
                                         content_type='application/json').status_code, 403)

    @patch('feed.python_runner.broker_request', side_effect=RunnerError('Сервіс недоступний'))
    def test_unavailable_service_does_not_execute_locally_or_change_grade(self, broker):
        self.assertEqual(self.post({'action': 'start'}).status_code, 503)
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.grade)

    def test_play_is_shown_only_for_selected_python_file(self):
        page = self.client.get(reverse('view_file', args=[self.sub.pk]), {'file_id': self.main.pk})
        self.assertContains(page, 'data-python-play')
        text = SubmissionFile.objects.create(submission=self.sub,
            file=SimpleUploadedFile('notes.txt', b'Notes'))
        page = self.client.get(reverse('view_file', args=[self.sub.pk]), {'file_id': text.pk})
        self.assertNotContains(page, 'data-python-play')
        with self.assertRaises(RunnerError):
            submission_bundle(self.sub, text.pk)

    def test_legacy_single_file_is_supported(self):
        legacy = Submission.objects.create(teacher=self.teacher, class_group=self.group,
            first_name='Old', last_name='Python',
            file=SimpleUploadedFile('legacy.py', b'print(42)'))
        self.assertTrue(submission_bundle(legacy, None)['entry'].endswith('.py'))

    @override_settings(CODE_RUNNER_URL='')
    def test_unconfigured_service_fails_closed(self):
        with self.assertRaises(RunnerError):
            broker_request('/runs', {})
