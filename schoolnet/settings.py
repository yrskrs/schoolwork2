"""
Налаштування Django для проєкту SchoolNet.
Локальна шкільна мікро-соціальна мережа — офлайн-режим, SQLite, без CDN.
"""

from pathlib import Path
import os
import sys
import dj_database_url
from dotenv import load_dotenv

# Захист від порожніх системних каталогів numpy у Python 3.14 (namespace packages без модуля)
try:
    import numpy
    if not hasattr(numpy, '__file__') or not hasattr(numpy, '__version__'):
        sys.modules['numpy'] = None
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# Завантажуємо змінні середовища з .env, якщо файл існує
load_dotenv(BASE_DIR / '.env')

# ─── Безпека ───────────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', 'schoolnet-local-secret-key-change-in-production-2026')
DEBUG = os.environ.get('DEBUG', 'True') == 'True'
ALLOWED_HOSTS = ['*']  # Локальна мережа — дозволяємо всі хости
# Enable only behind a trusted HTTPS reverse proxy such as the local Funnel.
if os.environ.get('SCHOOLNET_TRUST_PROXY_HTTPS', '').lower() in ('1', 'true'):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get('CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]

# ─── Застосунки ────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'feed',  # Основний застосунок
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'feed.middleware.PageCompressionMiddleware',
    'feed.https_hardening.HTTPSHardeningMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'schoolnet.middleware.LegacySessionCookieMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'feed.login_throttle_middleware.AdminLoginThrottleMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'feed.access_middleware.PublicAccessMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'feed.middleware.OnlineClientsMiddleware',
    'feed.middleware.FirstRunSetupMiddleware',
]

ROOT_URLCONF = 'schoolnet.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],  # Глобальна директорія шаблонів
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'feed.context_processors.teacher_stats_context',
            ],

        },
    },
]

WSGI_APPLICATION = 'schoolnet.wsgi.application'

# ─── База даних ────────────────────────────────────────────────────────────────
# Використовуємо DATABASE_URL з .env, або за замовчуванням локальний SQLite
db_url_env = os.environ.get('DATABASE_URL')
if db_url_env and '@postgres' in db_url_env:
    import socket
    try:
        socket.gethostbyname('postgres')
    except (socket.gaierror, OSError):
        db_url_env = db_url_env.replace('@postgres:5432', '@127.0.0.1:5433').replace('@postgres', '@127.0.0.1:5433')

DATABASES = {
    'default': dj_database_url.parse(
        db_url_env,
        conn_max_age=600,
        conn_health_checks=True,
    ) if db_url_env else dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'schoolnet.sqlite3'}",
        conn_max_age=600,
        conn_health_checks=True,
    )
}
if DATABASES['default'].get('ENGINE') == 'django.db.backends.sqlite3':
    # Acquire SQLite write transactions up front; deferred read-to-write
    # upgrades can deadlock otherwise when several AI subprocesses commit.
    DATABASES['default'].setdefault('OPTIONS', {}).update(timeout=20, transaction_mode='IMMEDIATE')

# ─── Пароль ────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
     'OPTIONS': {'min_length': 10}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ─── Локалізація ────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'uk'
TIME_ZONE = 'Europe/Kyiv'
USE_I18N = True
USE_TZ = True

# ─── Статичні файли (CSS, JS — без CDN) ────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# In production serve the collected tree so WhiteNoise can use the .br/.gz
# variants created by CompressedStaticFilesStorage. In development keep
# finders so edits are visible without collectstatic.
WHITENOISE_USE_FINDERS = DEBUG
# Precompress public assets during the existing collectstatic step; keep URLs stable.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage'},
}

# ─── Медіа-файли (завантажені вчителем) ────────────────────────────────────────
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Сесії ─────────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 86400 * 30  # 30 днів
SESSION_COOKIE_NAME = 'schoolwork2_sessionid'
CSRF_COOKIE_NAME = 'schoolwork2_csrftoken'
SESSION_SAVE_EVERY_REQUEST = True

# ─── Завантаження без обмеження розміру файлів ────────────────────────────────
# DATA_UPLOAD_MAX_MEMORY_SIZE limits non-file form fields only.
# Larger uploads stream to temporary files instead of occupying RAM.
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024


# Durable AI jobs; unit tests use the same executor without an external worker.
AI_JOBS_EAGER = True if 'test' in sys.argv else os.environ.get('AI_JOBS_EAGER', 'False') == 'True'
AI_JOB_TIMEOUT = int(os.environ.get('AI_JOB_TIMEOUT', '600'))
AI_WORKER_CONCURRENCY = max(1, min(8, int(os.environ.get('AI_WORKER_CONCURRENCY', '3'))))
AI_SNAPSHOT_RETENTION_DAYS = max(1, int(os.environ.get('AI_SNAPSHOT_RETENTION_DAYS', '14')))
AI_BATCH_CONCURRENCY = max(1, min(5, int(os.environ.get('AI_BATCH_CONCURRENCY', '3'))))
AI_PIN_MAX_WAITS = max(1, int(os.environ.get('AI_PIN_MAX_WAITS', '3')))
AI_FILE_API_MAX_FILES = max(1, int(os.environ.get('AI_FILE_API_MAX_FILES', '4')))
AI_FILE_API_MAX_FILE_MB = max(1, int(os.environ.get('AI_FILE_API_MAX_FILE_MB', '200')))
AI_FILE_API_MAX_TOTAL_MB = max(1, int(os.environ.get('AI_FILE_API_MAX_TOTAL_MB', '400')))
AI_STUDENT_RESERVED = max(0, min(AI_WORKER_CONCURRENCY - 1, int(os.environ.get('AI_STUDENT_RESERVED', '1'))))
AI_PREP_CONCURRENCY = max(1, min(2, int(os.environ.get('AI_PREP_CONCURRENCY', '1'))))
AI_REQUEST_DEADLINE = max(30, min(AI_JOB_TIMEOUT - 10, int(os.environ.get('AI_REQUEST_DEADLINE', '150'))))
AI_PRIVACY_ALLOWED_PROVIDERS = [p.strip() for p in os.environ.get('AI_PRIVACY_ALLOWED_PROVIDERS',
    'gemini,openai,deepseek,groq,openrouter,cloudflare,custom').split(',') if p.strip()]

# Reversible viewer optimization; no database migrations or worker changes.
REVIEW_ASYNC = os.environ.get('SCHOOLNET_REVIEW_ASYNC', 'True') == 'True'
CODE_RUNNER_URL = os.environ.get('CODE_RUNNER_URL', '')
CLOUD_IMPORT_ROOT = Path(os.environ.get('CLOUD_IMPORT_ROOT', str(Path(os.environ.get('AI_CACHE_DIR', '/tmp/schoolnet-ai-cache')).parent / 'schoolnet-cloud-imports')))
CLOUD_IMPORT_MAX_BYTES = int(os.environ.get('CLOUD_IMPORT_MAX_BYTES', str(1024**3)))
CLOUD_IMPORT_TIMEOUT = int(os.environ.get('CLOUD_IMPORT_TIMEOUT', '300'))
CLOUD_NEXTCLOUD_CA_FILE = os.environ.get('CLOUD_NEXTCLOUD_CA_FILE', '')
CODE_RUNNER_TOKEN = os.environ.get('CODE_RUNNER_TOKEN') or SECRET_KEY
REVIEW_PREVIEW_DIR = os.environ.get('REVIEW_PREVIEW_DIR',
    str(Path(os.environ.get('AI_CACHE_DIR', '/tmp/schoolnet-ai-cache')).parent / 'schoolnet-review-previews'))

# Private cache, shared by web workers and the AI worker; never served as media.
CACHES = {
    'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'},
    'ai_materials': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache' if 'test' in sys.argv else 'django.core.cache.backends.filebased.FileBasedCache',
        'LOCATION': os.environ.get('AI_CACHE_DIR', '/tmp/schoolnet-ai-cache'),
        'TIMEOUT': 604800,
        'OPTIONS': {'MAX_ENTRIES': int(os.environ.get('AI_CACHE_MAX_ENTRIES', '2000'))},
    },
}

# Server-only, optional peer-to-peer roster integration.
import json as _roster_json
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
HTTPS_HSTS_MAX_AGE = int(os.environ.get('HTTPS_HSTS_MAX_AGE', '86400'))
JOURNAL_API_BASE_URL = os.getenv('JOURNAL_API_BASE_URL', '').rstrip('/')
ROSTER_BACKEND = 'feed.roster_backend'
ROSTER_SYNC_TOKEN = os.getenv("ROSTER_SYNC_TOKEN", "")
ROSTER_PEERS = _roster_json.loads(os.getenv("ROSTER_PEERS", "[]"))
ROSTER_SYNC_INTERVAL = int(os.getenv("ROSTER_SYNC_INTERVAL", "30"))
ROSTER_SCHOOL_ID = int(os.getenv("ROSTER_SCHOOL_ID", "0"))
ROSTER_YEAR_ID = int(os.getenv("ROSTER_YEAR_ID", "0"))

# Optional: only set when the web process can access a local Tailscale daemon.
SCHOOLNET_TAILSCALE_BINARY = os.environ.get('SCHOOLNET_TAILSCALE_BINARY', '')
SCHOOLNET_TAILSCALE_TARGET_PORT = int(os.environ.get('SCHOOLNET_TAILSCALE_TARGET_PORT', os.environ.get('APP_PORT', '8000')))
