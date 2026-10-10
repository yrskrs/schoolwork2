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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
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

# ─── Пароль ────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = []  # Спрощено для локального середовища

# ─── Локалізація ────────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'uk'
TIME_ZONE = 'Europe/Kyiv'
USE_I18N = True
USE_TZ = True

# ─── Статичні файли (CSS, JS — без CDN) ────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
WHITENOISE_USE_FINDERS = True

# ─── Медіа-файли (завантажені вчителем) ────────────────────────────────────────
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Сесії ─────────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 86400 * 30  # 30 днів
SESSION_SAVE_EVERY_REQUEST = True

# ─── Завантаження без обмеження розміру файлів ────────────────────────────────
# DATA_UPLOAD_MAX_MEMORY_SIZE limits non-file form fields only.
# Larger uploads stream to temporary files instead of occupying RAM.
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024


# Durable AI jobs; unit tests use the same executor without an external worker.
AI_JOBS_EAGER = os.environ.get('AI_JOBS_EAGER', 'True' if 'test' in sys.argv else 'False') == 'True'
AI_JOB_TIMEOUT = int(os.environ.get('AI_JOB_TIMEOUT', '600'))

# Reversible viewer optimization; no database migrations or worker changes.
REVIEW_ASYNC = os.environ.get('SCHOOLNET_REVIEW_ASYNC', 'True') == 'True'
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
ROSTER_BACKEND = 'feed.roster_backend'
ROSTER_SYNC_TOKEN = os.getenv("ROSTER_SYNC_TOKEN", "")
ROSTER_PEERS = _roster_json.loads(os.getenv("ROSTER_PEERS", "[]"))
ROSTER_SYNC_INTERVAL = int(os.getenv("ROSTER_SYNC_INTERVAL", "30"))
ROSTER_SCHOOL_ID = int(os.getenv("ROSTER_SCHOOL_ID", "0"))
ROSTER_YEAR_ID = int(os.getenv("ROSTER_YEAR_ID", "0"))
