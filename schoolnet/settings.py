"""
Налаштування Django для проєкту SchoolNet.
Локальна шкільна мікро-соціальна мережа — офлайн-режим, SQLite, без CDN.
"""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

# ─── Безпека ───────────────────────────────────────────────────────────────────
SECRET_KEY = 'schoolnet-local-secret-key-change-in-production-2026'
DEBUG = True
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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'feed.middleware.OnlineClientsMiddleware',
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

# ─── База даних (SQLite — ідеально для офлайн-розгортання) ─────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'schoolnet.sqlite3',
    }
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

# ─── Медіа-файли (завантажені вчителем) ────────────────────────────────────────
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Сесії ─────────────────────────────────────────────────────────────────────
SESSION_COOKIE_AGE = 86400 * 30  # 30 днів
SESSION_SAVE_EVERY_REQUEST = True

# ─── Максимальний розмір завантаження файлів (50 МБ) ───────────────────────────
DATA_UPLOAD_MAX_MEMORY_SIZE = 52428800
FILE_UPLOAD_MAX_MEMORY_SIZE = 52428800
