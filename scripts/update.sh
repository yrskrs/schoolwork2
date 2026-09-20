#!/usr/bin/env bash
# ==============================================================================
# Скрипт безпечного оновлення застосунку SchoolNet на бойовому сервері
# Гарантує збереження бази даних (PostgreSQL) та медіа-файлів (учнівських робіт)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=================================================================="
echo "          Безпечне оновлення SchoolNet (Docker)"
echo "=================================================================="
echo "ℹ️  Існуюча база даних (postgres_data) та медіа-файли (app_media)"
echo "   знаходяться у захищених томах Docker і НЕ БУДУТЬ втрачені."
echo "=================================================================="

cd "$PROJECT_ROOT"

# Зчитування параметрів середовища
APP_PORT="8000"
DB_USER="schoolnet_user"
DB_NAME="schoolnet_db"

if [ -f "$PROJECT_ROOT/.env" ]; then
    ENV_PORT=$(grep -E '^[[:space:]]*APP_PORT=' "$PROJECT_ROOT/.env" | head -n1 | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'" | tr -d ' ' || true)
    if [ -n "$ENV_PORT" ]; then APP_PORT="$ENV_PORT"; fi
    
    ENV_USER=$(grep -E '^[[:space:]]*POSTGRES_USER=' "$PROJECT_ROOT/.env" | head -n1 | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'" | tr -d ' ' || true)
    if [ -n "$ENV_USER" ]; then DB_USER="$ENV_USER"; fi
    
    ENV_DB=$(grep -E '^[[:space:]]*POSTGRES_DB=' "$PROJECT_ROOT/.env" | head -n1 | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'" | tr -d ' ' || true)
    if [ -n "$ENV_DB" ]; then DB_NAME="$ENV_DB"; fi
fi

# 1. Автоматичне створення резервної копії перед оновленням
echo ""
echo "📦 [1/5] Створення резервної копії перед оновленням..."
if [ -f "./scripts/backup.sh" ]; then
    if bash ./scripts/backup.sh; then
        echo "✅ Резервну копію успішно створено."
    else
        echo "⚠️  [УВАГА] Не вдалося створити повний бекап!"
        read -p "Бажаєте продовжити оновлення БЕЗ бекапу? (y/N): " -r
        echo ""
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "❌ Оновлення скасовано для захисту даних."
            exit 1
        fi
    fi
else
    echo "⚠️  Скрипт ./scripts/backup.sh не знайдено, пропуск автобекапу."
fi

# 2. Отримання оновлень з GitHub
echo ""
echo "📥 [2/5] Отримання оновлень з репозиторію..."
if [ -d .git ]; then
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    echo "   -> Оновлення гілки [$CURRENT_BRANCH]..."
    git fetch origin "$CURRENT_BRANCH"
    git merge "origin/$CURRENT_BRANCH" || {
        echo "⚠️ Не вдалося автоматично об'єднати зміни (можливий конфлікт)."
        echo "Спробуйте перевірити статус: git status"
        exit 1
    }
else
    echo "ℹ️  Каталог .git відсутній, використовується поточний код."
fi

# 3. Перезбирання контейнера застосунку та запуск
echo ""
echo "🔨 [3/5] Перезбирання образу застосунку та перезапуск служб..."
docker compose build app
docker compose up -d

# Очікуємо поки контейнер app буде у стані running (до 30 сек)
echo "⏳ Очікування запуску контейнера app..."
for i in {1..30}; do
    APP_STATE=$(docker compose ps --status running --services 2>/dev/null || true)
    if echo "$APP_STATE" | grep -q "app"; then
        echo "   -> Контейнер app запущений."
        break
    fi
    sleep 1
done

# 4. Очікування готовності PostgreSQL
echo ""
echo "⏳ [4/5] Очікування готовності бази даних PostgreSQL..."
READY=0
for i in {1..30}; do
    if docker compose exec -T postgres pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done

if [ "$READY" -ne 1 ]; then
    echo "❌ [ПОМИЛКА] База даних PostgreSQL не відповіла за 30 секунд!"
    exit 1
fi
echo "✅ PostgreSQL готовий до виконання міграцій."

# 5. Застосування міграцій та збір статики
echo ""
echo "🚀 [5/5] Застосування міграцій БД та збір статики..."
docker compose exec -T app python manage.py migrate --noinput 2>&1
docker compose exec -T app python manage.py collectstatic --noinput --clear 2>&1 || docker compose exec -T app python manage.py collectstatic --noinput 2>&1

# 6. Фінальна перевірка працездатності (Health Check)
echo ""
echo "🔍 Перевірка працездатності SchoolNet..."
HTTP_STATUS=""
for i in {1..15}; do
    HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${APP_PORT}/" || true)
    if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "302" ]; then
        break
    fi
    sleep 1
done

echo "=================================================================="
if [ "$HTTP_STATUS" = "200" ] || [ "$HTTP_STATUS" = "302" ]; then
    echo "✅ Оновлення успішно завершено! Сервіс працює (HTTP $HTTP_STATUS)."
else
    echo "ℹ️  Оновлення завершено. Код відповіді: $HTTP_STATUS"
fi
echo "🌐 Адреса сервісу: http://localhost:${APP_PORT}/"
echo "=================================================================="
