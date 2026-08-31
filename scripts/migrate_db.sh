#!/usr/bin/env bash
# Одноразовий скрипт для міграції даних з SQLite в PostgreSQL
# 
# УВАГА: Цей скрипт передбачає, що у вас є локально робочий python з Django 
# та стара база schoolnet.sqlite3.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo " Міграція даних з SQLite в PostgreSQL"
echo "========================================"

cd "$PROJECT_ROOT"

# Перевіряємо наявність SQLite БД
if [ ! -f "schoolnet.sqlite3" ]; then
    echo "❌ Файл schoolnet.sqlite3 не знайдено! Немає чого мігрувати."
    exit 1
fi

# 1. Дамп даних (запускається через локальний Python, якщо він є, 
# або можна запустити всередині контейнера, якщо туди прокинути SQLite)
echo "📦 Створення дампа існуючих даних..."
if [ -f "venv/bin/python" ]; then
    PYTHON_CMD="venv/bin/python"
else
    PYTHON_CMD="python"
fi

# Тимчасово вкажемо Django використовувати SQLite, щоб зробити дамп
# Встановлюємо DATABASE_URL, щоб load_dotenv не перезаписав його значенням з .env
env DATABASE_URL="sqlite:///$PROJECT_ROOT/schoolnet.sqlite3" $PYTHON_CMD manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission -e sessions -e admin.logentry > datadump.json

echo "✅ Дамп створено (datadump.json)."

echo "🚀 Запуск PostgreSQL та застосунку через Docker..."
docker compose up -d

echo "⏳ Очікування готовності застосунку та БД (10 секунд)..."
sleep 10

echo "🛠️ Застосування міграцій в PostgreSQL..."
docker compose exec app python manage.py migrate

echo "📥 Завантаження даних у PostgreSQL..."
docker compose cp datadump.json app:/app/datadump.json
docker compose exec app python manage.py loaddata datadump.json
docker compose exec app rm /app/datadump.json

echo "✅ Міграція завершена. Можна видалити datadump.json та schoolnet.sqlite3."
echo "========================================"
