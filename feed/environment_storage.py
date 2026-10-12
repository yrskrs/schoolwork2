"""Read-only storage snapshot for the school settings page."""
import hashlib
import logging
import os
import stat
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, connections, transaction
from django.utils import timezone

from .models import AICriteriaPreset, AssignmentFile, Submission, SubmissionFile

logger = logging.getLogger(__name__)
SNAPSHOT_TTL = 60


def format_size(size):
    if size is None:
        return 'Недоступно'
    value = float(size)
    for unit in ('Б', 'КіБ', 'МіБ', 'ГіБ', 'ТіБ'):
        if value < 1024 or unit == 'ТіБ':
            return f'{int(value)} {unit}' if unit == 'Б' else f'{value:.2f} {unit}'
        value /= 1024


def database_size(alias='default'):
    """PostgreSQL relation storage, or SQLite files including WAL/SHM."""
    database = connections[alias]
    try:
        if database.vendor == 'postgresql':
            with transaction.atomic(using=alias), database.cursor() as cursor:
                cursor.execute('SELECT pg_database_size(current_database())')
                return int(cursor.fetchone()[0])
        if database.vendor == 'sqlite':
            name = str(database.settings_dict['NAME'])
            if name == ':memory:' or 'mode=memory' in name:
                return 0  # An in-memory test DB does not occupy disk space.
            # Stat the main file explicitly: a missing database is not an empty one.
            size = os.stat(name).st_size
            for suffix in ('-wal', '-shm'):
                try:
                    size += os.stat(name + suffix).st_size
                except FileNotFoundError:
                    pass
            return size
    except (OSError, DatabaseError, TypeError, ValueError) as error:
        logger.warning('Cannot measure %s database storage (%s)', database.vendor, type(error).__name__)
    return None


def storage_roots():
    roots = [('media', Path(settings.MEDIA_ROOT))]
    for config in settings.CACHES.values():
        if config.get('BACKEND') == 'django.core.cache.backends.filebased.FileBasedCache':
            roots.append(('cache', Path(config['LOCATION'])))
    roots.extend([
        ('cache', Path(settings.REVIEW_PREVIEW_DIR)),
        ('logs', Path(settings.BASE_DIR) / 'logs'),
    ])
    return roots


def referenced_files():
    teacher = set(AssignmentFile.objects.order_by().values_list('file', flat=True).iterator())
    teacher.update(AICriteriaPreset.objects.order_by().values_list('document_file', flat=True).iterator())
    student = set(Submission.objects.order_by().values_list('file', flat=True).iterator())
    student.update(SubmissionFile.objects.order_by().values_list('file', flat=True).iterator())
    return teacher - {'', None}, student - {'', None}


def get_storage_snapshot():
    roots = storage_roots()
    db_config = connections['default'].settings_dict
    identity = repr(([(kind, str(root)) for kind, root in roots],
                     db_config.get('ENGINE'), db_config.get('NAME'), db_config.get('HOST'), db_config.get('PORT')))
    cache_key = 'environment-storage:' + hashlib.sha256(identity.encode()).hexdigest()
    snapshot = cache.get(cache_key)
    if snapshot is not None:
        return snapshot

    teacher_names, student_names = referenced_files()
    sizes = dict.fromkeys(('teacher_files', 'student_files', 'other_files', 'cache', 'logs'), 0)
    warnings = []
    seen = set()

    def scan_error(error):
        if 'Частину каталогів не вдалося прочитати; показано доступний обсяг.' not in warnings:
            warnings.append('Частину каталогів не вдалося прочитати; показано доступний обсяг.')
        logger.warning('Cannot scan school storage (%s)', type(error).__name__)

    for kind, root in roots:
        try:
            root.stat()
        except FileNotFoundError:
            continue  # Cache/log directories are created on first use.
        except OSError as error:
            scan_error(error)
            continue
        for directory, directories, filenames in os.walk(root, onerror=scan_error, followlinks=False):
            # Never follow an upload symlink outside the configured storage roots.
            for dirname in directories[:]:
                if (Path(directory) / dirname).is_symlink():
                    directories.remove(dirname)
                    scan_error(OSError('symlink directory'))
            for filename in filenames:
                path = Path(directory) / filename
                try:
                    info = path.lstat()
                except FileNotFoundError:
                    continue  # A concurrent deletion is safe to skip.
                except OSError as error:
                    scan_error(error)
                    continue
                if stat.S_ISLNK(info.st_mode):
                    scan_error(OSError('symlink file'))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                identity = (info.st_dev, info.st_ino)
                if identity in seen:
                    continue  # Shared submissions, hard links and overlapping roots.
                seen.add(identity)
                category = kind
                if kind == 'media':
                    name = path.relative_to(root).as_posix()
                    category = ('student_files' if name in student_names else
                                'teacher_files' if name in teacher_names else 'other_files')
                sizes[category] += info.st_size

    db_bytes = database_size()
    if db_bytes is None:
        warnings.append('Розмір БД недоступний; загальний обсяг не включає БД.')
    sizes['db'] = db_bytes
    sizes['media'] = sum(sizes[key] for key in ('teacher_files', 'student_files', 'other_files'))
    sizes['total'] = (db_bytes or 0) + sizes['media'] + sizes['cache'] + sizes['logs']
    snapshot = {key + '_size': format_size(size) for key, size in sizes.items()}
    snapshot.update({key + '_bytes': size for key, size in sizes.items()})
    snapshot.update(storage_warnings=warnings, storage_incomplete=bool(warnings), storage_updated_at=timezone.now())
    cache.set(cache_key, snapshot, SNAPSHOT_TTL)
    return snapshot
