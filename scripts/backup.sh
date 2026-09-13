#!/usr/bin/env bash
# ==============================================================================
# Скрипт створення резервної копії PostgreSQL та медіа-файлів SchoolNet
# Забезпечує безпечне створення копії без зупинки роботи сервісу
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_ROOT/backups"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
CURRENT_BACKUP_DIR="$BACKUP_DIR/$TIMESTAMP"

echo "========================================"
echo " Початок резервного копіювання SchoolNet"
echo " Час: $TIMESTAMP"
echo "========================================"

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

mkdir -p "$CURRENT_BACKUP_DIR"

# Перевірка чи запущений контейнер postgres
if ! docker compose ps --status running --services 2>/dev/null | grep -q "postgres"; then
    echo "⏳ Контейнер PostgreSQL не запущений. Запуск служби postgres..."
    docker compose up -d postgres
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
        echo "❌ [ПОМИЛКА] Не вдалося дочекатися готовності PostgreSQL."
        rm -rf "$CURRENT_BACKUP_DIR"
        exit 1
    fi
fi

# 1. Створення дампу бази даних
echo "🗄️  Створення дампу бази даних PostgreSQL ($DB_NAME)..."
docker compose exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" -F c > "$CURRENT_BACKUP_DIR/database.dump"

if [ ! -s "$CURRENT_BACKUP_DIR/database.dump" ]; then
    echo "❌ [ПОМИЛКА] Дамп бази даних порожній або сталася помилка при експорті!"
    rm -rf "$CURRENT_BACKUP_DIR"
    exit 1
fi
DUMP_SIZE=$(du -h "$CURRENT_BACKUP_DIR/database.dump" | cut -f1)
echo "   -> Дамп БД створено ($DUMP_SIZE)"

# 2. Динамічне визначення Docker-тому для медіа файлів
echo "📂 Пошук тому медіа-файлів..."
MEDIA_VOLUME=$(docker inspect schoolnet_app --format '{{range .Mounts}}{{if eq .Destination "/app/media"}}{{.Name}}{{end}}{{end}}' 2>/dev/null || true)
if [ -z "$MEDIA_VOLUME" ]; then
    MEDIA_VOLUME=$(docker volume ls -q --filter "name=app_media" 2>/dev/null | head -n 1 || true)
fi
if [ -z "$MEDIA_VOLUME" ]; then
    PROJ_NAME=$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')
    MEDIA_VOLUME="${COMPOSE_PROJECT_NAME:-$PROJ_NAME}_app_media"
fi

# 3. Архівування медіа файлів (змонтовано read-only :ro для повної безпеки даних)
if docker volume inspect "$MEDIA_VOLUME" >/dev/null 2>&1; then
    echo "📂 Архівування медіа файлів з тому [$MEDIA_VOLUME] (read-only)..."
    docker run --rm \
      -v "$MEDIA_VOLUME":/source:ro \
      -v "$CURRENT_BACKUP_DIR":/backup \
      alpine tar -czf /backup/media.tar.gz -C /source .
    MEDIA_SIZE=$(du -h "$CURRENT_BACKUP_DIR/media.tar.gz" | cut -f1)
    echo "   -> Архів медіа створено ($MEDIA_SIZE)"
else
    echo "⚠️ Том [$MEDIA_VOLUME] не знайдено в системі Docker. Створюємо порожній media.tar.gz."
    tar -czf "$CURRENT_BACKUP_DIR/media.tar.gz" -T /dev/null
fi

echo "========================================"
echo "✅ Резервна копія успішно збережена в:"
echo "   $CURRENT_BACKUP_DIR"
echo "========================================"
