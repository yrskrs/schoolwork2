"""Authorization must use the same resolved path as the file server."""
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth.models import AnonymousUser, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, TestCase, override_settings

from .file_serving import public_media
from .models import Assignment, AssignmentFile, Subject, Teacher


class MediaPathAuditTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory(prefix='synthetic-media-audit-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        override = override_settings(MEDIA_ROOT=directory.name)
        override.enable()
        self.addCleanup(override.disable)
        self.factory = RequestFactory()
        self.user = User.objects.create_user('synthetic_media_owner')
        teacher = Teacher.objects.create(user=self.user, full_name='Synthetic Teacher')
        self.assignment = Assignment.objects.create(teacher=teacher,
            subject=Subject.objects.create(name='Synthetic subject'), title='Synthetic draft', status='draft')

    def request(self, **headers):
        request = self.factory.get('/media/audit', **headers)
        request.user = AnonymousUser()
        return request

    def test_normalized_paths_cannot_expose_archives_or_logs(self):
        for folder in ('school_archives', 'logs_archives'):
            target = self.root / folder / 'synthetic.txt'
            target.parent.mkdir()
            target.write_text('synthetic private archive')
            for path in (f'{folder}/synthetic.txt', f'other/../{folder}/synthetic.txt',
                         f'./{folder}/synthetic.txt'):
                with self.subTest(path=path), self.assertRaises(Http404):
                    public_media(self.request(), path)

    def test_normalized_paths_cannot_expose_draft_materials(self):
        material = AssignmentFile.objects.create(assignment=self.assignment,
            file=SimpleUploadedFile('synthetic.txt', b'synthetic draft'), original_name='synthetic.txt')
        for path in (material.file.name, 'other/../' + material.file.name, './' + material.file.name):
            with self.subTest(path=path), self.assertRaises(Http404):
                public_media(self.request(), path)
        request = self.request()
        request.user = self.user
        response = public_media(request, 'other/../' + material.file.name)
        self.assertEqual(b''.join(response.streaming_content), b'synthetic draft')
        # This response uses RequestFactory, so close the stream directly rather
        # than emitting request_finished inside TestCase's database transaction.
        response.file_to_stream.close()

    def test_symlink_to_protected_archive_uses_target_permissions(self):
        target = self.root / 'school_archives' / 'synthetic.txt'
        target.parent.mkdir()
        target.write_text('synthetic private archive')
        (self.root / 'public-alias.txt').symlink_to(target)
        with self.assertRaises(Http404):
            public_media(self.request(), 'public-alias.txt')

    def test_oversized_range_returns_416_instead_of_server_error(self):
        (self.root / 'synthetic.txt').write_text('synthetic')
        response = public_media(self.request(HTTP_RANGE='bytes=' + '9' * 5000 + '-'), 'synthetic.txt')
        self.assertEqual(response.status_code, 416)
        self.assertEqual(response['Content-Range'], 'bytes */9')
