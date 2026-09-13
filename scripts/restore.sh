#!/usr/bin/env bash
# ==============================================================================
# Скрипт відновлення бази даних та медіа-файлів із резервної копії SchoolNet
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

AUTO_CONFIRM=0
TARGET_BACKUP=""

for arg in "$@"; do
    case "$arg" in
        -y|--yes)
            AUTO_CONFIRM=1
            ;;
        *)
            if [ -z "$TARGET_BACKUP" ]; then
                TARGET_BACKUP="$arg"
            fi
            ;;
    esac
done

if [ -z "$TARGET_BACKUP" ]; then
    echo "❌ [ПОМИЛКА] Вкажіть шлях до папки з бекапом!"
    echo "Використання: ./scripts/restore.sh backups/2026-09-13_18-00-00 [--yes]"
    exit 1
fi

if [ ! -d "$TARGET_BACKUP" ]; then
    echo "❌ [ПОМИЛКА] Папка $TARGET_BACKUP не існує!"
    exit 1
fi

echo "=================================================================="
echo " Відновлення SchoolNet з резервної копії"
echo " Каталог бекапу: $TARGET_BACKUP"
echo " УВАГА: Всі поточні дані бази та медіа-файли будуть перезаписані!"
echo "=================================================================="

if [ "$AUTO_CONFIRM" -ne 1 ]; then
    read -p "Ви дійсно бажаєте продовжити відновлення? (y/N): " -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Операцію відновлення скасовано користувачем."
        exit 0
    fi
fi

cd "$PROJECT_ROOT"

# Зчитування параметрів PostgreSQL із .env (якщо файл існує)
ENV_PG_USER=""
ENV_PG_DB=""
if [ -f "$PROJECT_ROOT/.env" ]; then
    ENV_PG_USER=$(grep -E '^[[:space:]]*POSTGRES_USER=' "$PROJECT_ROOT/.env" | head -n1 | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'" | tr -d ' ' || true)
    ENV_PG_DB=$(grep -E '^[[:space:]]*POSTGRES_DB=' "$PROJECT_ROOT/.env" | head -n1 | cut -d '=' -f2- | tr -d '\r\n"' | tr -d "'" | tr -d ' ' || true)
fi
DB_USER="${POSTGRES_USER:-${ENV_PG_USER:-schoolnet_user}}"
DB_NAME="${POSTGRES_DB:-${ENV_PG_DB:-schoolnet_db}}"

# 1. Зупинка контейнера застосунку, щоб уникнути блокувань та нових записів
echo "🛑 [1/4] Зупинка контейнера застосунку..."
docker compose stop app 2>/dev/null || true

# Перевірка роботи PostgreSQL
if ! docker compose ps --status running --services 2>/dev/null | grep -q "postgres"; then
    echo "⏳ Запуск контейнера PostgreSQL для відновлення..."
    docker compose up -d postgres
fi

echo "⏳ Очікування готовності PostgreSQL..."
READY=0
for i in {1..30}; do
    if docker compose exec -T postgres pg_isready -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done
if [ "$READY" -ne 1 ]; then
    echo "❌ [ПОМИЛКА] PostgreSQL недоступний для відновлення!"
    exit 1
fi

# 2. Відновлення бази даних
if [ -f "$TARGET_BACKUP/database.dump" ]; then
    echo "🗄️  [2/4] Відновлення бази даних PostgreSQL ($DB_NAME)..."
    
    echo "   -> Очищення старої схеми public..."
    docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -v "ON_ERROR_STOP=1" -c \
      "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO \"$DB_USER\"; GRANT ALL ON SCHEMA public TO public;"
    
    echo "   -> Завантаження структури та даних з database.dump..."
    cat "$TARGET_BACKUP/database.dump" | docker compose exec -T postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner --no-privileges || true
    echo "   -> Базу даних успішно відновлено."
else
    echo "⚠️ [2/4] Файл database.dump не знайдено, пропуск відновлення БД."
fi

# 3. Відновлення медіа файлів
if [ -f "$TARGET_BACKUP/media.tar.gz" ]; then
    echo "📂 [3/4] Відновлення медіа файлів..."
    MEDIA_VOLUME=$(docker inspect schoolnet_app --format '{{range .Mounts}}{{if eq .Destination "/app/media"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)
    if [ -z "$MEDIA_VOLUME" ]; then
        MEDIA_VOLUME=$(docker volume ls -q --filter "name=app_media" 2>/dev/null | head -n 1 || true)
    fi
    if [ -z "$MEDIA_VOLUME" ]; then
        PROJ_NAME=$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
        MEDIA_VOLUME="${COMPOSE_PROJECT_NAME:-$PROJ_NAME}_app_media"
    fi

    # Створюємо volume, якщо він ще не існував
    if ! docker volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1; then
        docker volume create "$MEDIA_VOLUME" >/dev/null
    fi

    echo "   -> Очищення та розпакування архіву у том [$MEDIA_VOLUME]..."
    docker run --rm \
      -v "$MEDIA_VOLUME":/target \
      -v "$(realpath "$TARGET_BACKUP")":/backup:ro \
      alpine sh -c "rm -rf /target/* && tar -xzf /backup/media.tar.gz -C /target"
    echo "   -> Медіа-файли успішно відновлено."
else
    echo "⚠️ [3/4] Файл media.tar.gz не знайдено, пропуск відновлення медіа."
fi

# 4. Запуск сервісу
echo "🚀 [4/4] Запуск контейнера застосунку..."
docker compose up -d app

echo "⏳ Застосування міграцій (якщо потрібні)..."
sleep 3
docker compose exec -T app python manage.py migrate --noinput 2>/dev/null || true

echo "=================================================================="
echo "✅ Відновлення успішно завершено!"
echo "=================================================================="
