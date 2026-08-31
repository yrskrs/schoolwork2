#!/usr/bin/env bash
# Скрипт створення резервної копії PostgreSQL та медіа-файлів

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_ROOT/backups"

TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")
CURRENT_BACKUP_DIR="$BACKUP_DIR/$TIMESTAMP"

echo "========================================"
echo " Початок резервного копіювання SchoolNet"
echo " Час: $TIMESTAMP"
echo "========================================"

mkdir -p "$CURRENT_BACKUP_DIR"
cd "$PROJECT_ROOT"

# 1. Створення дампу бази даних
echo "🗄️  Створення дампу бази даних PostgreSQL..."
docker compose exec -T postgres pg_dump -U schoolnet_user -d schoolnet_db -F c > "$CURRENT_BACKUP_DIR/database.dump"

# 2. Архівування медіа файлів (з volume)
# Щоб зробити бекап volume, ми запускаємо тимчасовий alpine контейнер
echo "📂 Архівування медіа файлів..."
docker run --rm \
  -v schoolwork2_app_media:/source \
  -v "$CURRENT_BACKUP_DIR":/backup \
  alpine tar -czf /backup/media.tar.gz -C /source .

echo "✅ Резервна копія успішно створена: $CURRENT_BACKUP_DIR"
echo "========================================"
