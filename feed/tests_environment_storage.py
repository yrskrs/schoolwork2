import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from lxml import html

from .environment_storage import database_size, format_size, get_storage_snapshot
from .middleware import set_has_admin
from .models import AICriteriaPreset, Assignment, AssignmentFile, ClassGroup, School, Submission, SubmissionFile, Teacher


class DatabaseStorageTests(SimpleTestCase):
    def test_postgres_uses_the_connected_database(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (7340032,)
        database = MagicMock(vendor='postgresql')
        database.cursor.return_value.__enter__.return_value = cursor
        with patch('feed.environment_storage.connections', {'default': database}), patch('feed.environment_storage.transaction.atomic'):
            self.assertEqual(database_size(), 7340032)
        cursor.execute.assert_called_once_with('SELECT pg_database_size(current_database())')

    def test_sqlite_includes_wal_and_shm(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / 'school.sqlite3')
            for suffix, size in (('', 1024), ('-wal', 20), ('-shm', 30)):
                Path(filename + suffix).write_bytes(b'x' * size)
            database = SimpleNamespace(vendor='sqlite', settings_dict={'NAME': filename})
            with patch('feed.environment_storage.connections', {'default': database}):
                self.assertEqual(database_size(), 1074)

    def test_missing_sqlite_is_unavailable_and_memory_uses_no_disk(self):
        database = SimpleNamespace(vendor='sqlite', settings_dict={'NAME': '/nonexistent-school-storage/db'})
        with patch('feed.environment_storage.connections', {'default': database}):
            self.assertIsNone(database_size())
            for name in (':memory:', 'file:memorydb_default?mode=memory&cache=shared'):
                database.settings_dict['NAME'] = name
                self.assertEqual(database_size(), 0)

    def test_database_error_is_not_reported_as_zero(self):
        database = MagicMock(vendor='postgresql')
        database.cursor.side_effect = DatabaseError('unavailable')
        with patch('feed.environment_storage.connections', {'default': database}), patch('feed.environment_storage.transaction.atomic'):
            self.assertIsNone(database_size())

    def test_units_include_empty_storage_and_large_files(self):
        for value, expected in ((None, 'Недоступно'), (0, '0 Б'), (1024, '1.00 КіБ'), (1024**3, '1.00 ГіБ'), (1024**4, '1.00 ТіБ')):
            self.assertEqual(format_size(value), expected)


class EnvironmentStorageTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.media = self.root / 'media'
        self.media.mkdir()
        self.override = self.settings(
            BASE_DIR=self.root, MEDIA_ROOT=self.media, REVIEW_PREVIEW_DIR=self.root / 'cache' / 'previews',
            CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': self.directory.name},
                    'ai_materials': {'BACKEND': 'django.core.cache.backends.filebased.FileBasedCache', 'LOCATION': str(self.root / 'cache')}},
        )
        self.override.enable()
        self.addCleanup(self.override.disable)
        cache.clear()
        set_has_admin(True)
        self.user = User.objects.create_superuser('storage-admin', password='test-only')
        self.teacher = Teacher.objects.create(user=self.user, full_name='Вчитель')
        School.objects.create(name='Школа', admin=self.user)
        self.assignment = Assignment.objects.create(teacher=self.teacher, title='Завдання')
        self.class_group = ClassGroup.objects.create(grade=7, letter='А', name='7-А')
        self.client.force_login(self.user)

    def write(self, relative, size, base=None):
        path = (base or self.media) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x' * size)
        return relative

    def seed_files(self):
        AssignmentFile.objects.create(assignment=self.assignment, file=self.write('assignments/lesson.txt', 100))
        # Duplicated assignments reuse the same physical attachment.
        AssignmentFile.objects.create(assignment=self.assignment, file='assignments/lesson.txt')
        AICriteriaPreset.objects.create(name='Критерії', document_file=self.write('ai_criteria_docs/rules.txt', 20))
        primary = self.write('submissions/first.txt', 200)
        submission = Submission.objects.create(assignment=self.assignment, class_group=self.class_group, first_name='Учень', last_name='Перший', file=primary)
        SubmissionFile.objects.create(submission=submission, file=primary, file_size=99999)
        SubmissionFile.objects.create(submission=submission, file=self.write('submissions/extra.txt', 30), file_size=1)
        Submission.objects.create(assignment=self.assignment, class_group=self.class_group, first_name='Учень', last_name='Другий', file=primary)
        Submission.objects.create(assignment=self.assignment, class_group=self.class_group, first_name='Учень', last_name='Третій', file='submissions/missing.txt')
        self.write('teacher_avatars/photo.jpg', 40)
        self.write('school_archives/backup.zip', 50)
        self.write('submissions/orphan.txt', 10)
        self.write('ai/cache.bin', 60, self.root / 'cache')
        self.write('page.png', 70, self.root / 'cache' / 'previews')
        self.write('app.log', 80, self.root / 'logs')

    def test_real_files_are_split_and_shared_files_are_counted_once(self):
        self.seed_files()
        with patch('feed.environment_storage.database_size', return_value=1000):
            snapshot = get_storage_snapshot()
        for key, expected in {'teacher_files_bytes': 120, 'student_files_bytes': 230, 'other_files_bytes': 100,
                              'media_bytes': 450, 'cache_bytes': 130, 'logs_bytes': 80, 'total_bytes': 1660}.items():
            self.assertEqual(snapshot[key], expected, key)
        self.assertFalse(snapshot['storage_incomplete'])

    def test_hardlinks_and_nested_cache_roots_do_not_inflate_total(self):
        self.write('one.txt', 10)
        os.link(self.media / 'one.txt', self.media / 'two.txt')
        self.write('page.png', 70, self.root / 'cache' / 'previews')
        snapshot = get_storage_snapshot()
        self.assertEqual(snapshot['other_files_bytes'], 10)
        self.assertEqual(snapshot['cache_bytes'], 70)

    def test_empty_storage_is_zero_and_snapshot_is_cached(self):
        with patch('feed.environment_storage.database_size', return_value=0) as size:
            first = get_storage_snapshot()
            self.write('later.txt', 10)
            self.assertEqual(get_storage_snapshot(), first)
            size.assert_called_once()
        self.assertEqual(first['total_size'], '0 Б')

    def test_unknown_database_marks_the_total_as_incomplete(self):
        self.write('file.txt', 10)
        with patch('feed.environment_storage.database_size', return_value=None):
            response = self.client.get(reverse('teacher_settings'), {'tab': 'environment'})
        self.assertContains(response, 'Недоступно')
        self.assertContains(response, '≥ 10 Б')
        self.assertTrue(response.context['env_stats']['storage_warnings'])

    def test_external_symlinks_are_not_followed(self):
        self.write('outside.txt', 1000, self.root)
        (self.media / 'link.txt').symlink_to(self.root / 'outside.txt')
        snapshot = get_storage_snapshot()
        self.assertEqual(snapshot['media_bytes'], 0)
        self.assertTrue(snapshot['storage_incomplete'])

    def test_scan_failure_is_visible(self):
        def inaccessible(*args, **kwargs):
            kwargs['onerror'](PermissionError('denied'))
            return iter(())
        with patch('feed.environment_storage.os.walk', side_effect=inaccessible):
            self.assertTrue(get_storage_snapshot()['storage_incomplete'])

    def test_settings_show_storage_and_current_guide_categories(self):
        self.seed_files()
        response = self.client.get(reverse('teacher_settings'), {'tab': 'environment'})
        self.assertContains(response, 'Матеріали вчителів')
        self.assertContains(response, 'Файли учнів')
        self.assertContains(response, 'Усього даних на диску')
        self.assertContains(response, 'guide-template')
        response = self.client.get(reverse('ui_dialog', args=['guide']))
        page = html.fromstring(response.content)
        dialog = page.get_element_by_id('guide-modal')
        # Every topic belongs inside the scrollable accordion, never above the header.
        self.assertFalse(dialog.xpath('./div/section'))
        for category in ('teacher-settings', 'teacher-ai-settings', 'teacher-api-settings', 'teacher-updates'):
            card = page.get_element_by_id('guide-category-' + category)
            self.assertIn(page.get_element_by_id('guide-accordion-container'), card.iterancestors())
        self.assertContains(response, 'приватні вказівки для ШІ')

    def test_school_storage_is_available_to_teachers_but_not_anonymous_users(self):
        self.user.is_superuser = False
        self.user.save(update_fields=['is_superuser'])
        response = self.client.get(reverse('teacher_settings'), {'tab': 'environment'})
        self.assertContains(response, 'Матеріали вчителів')
        self.assertNotContains(response, 'id="guide-category-teacher-updates"')
        self.client.logout()
        response = self.client.get(reverse('teacher_settings'), {'tab': 'environment'})
        self.assertEqual(response.status_code, 302)
