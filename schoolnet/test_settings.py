"""Safe local tests: no production database or uploaded files are touched."""
import atexit
import os
import shutil
import tempfile

from .settings import *  # noqa: F403

if os.environ.get('SCHOOLNET_TEST_POSTGRES') == '1':
    DATABASES['default']['TEST'] = {'NAME': 'test_schoolnet_audit_20261003'}
else:
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
MEDIA_ROOT = tempfile.mkdtemp(prefix='schoolnet-tests-media-')
atexit.register(shutil.rmtree, MEDIA_ROOT, ignore_errors=True)
SECRET_KEY = 'schoolnet-tests-only-secret-key-with-at-least-fifty-characters'
DEBUG = False
AI_JOBS_EAGER = True
CACHES = {name: {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'test-' + name}
          for name in ('default', 'ai_materials')}
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'

PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
