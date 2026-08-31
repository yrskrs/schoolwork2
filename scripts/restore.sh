#!/usr/bin/env bash
# Скрипт відновлення бази даних та медіа-файлів із бекапу

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKUP_DIR="$PROJECT_ROOT/backups"

if [ -z "$1" ]; then
    echo "❌ [ПОМИЛКА] Вкажіть шлях до папки з бекапом!"
    echo "Використання: ./scripts/restore.sh backups/2026-08-31_21-00-00"
    exit 1
fi

TARGET_BACKUP="$1"

if [ ! -d "$TARGET_BACKUP" ]; then
    echo "❌ [ПОМИЛКА] Папка $TARGET_BACKUP не існує!"
    exit 1
fi

echo "========================================"
echo " Відновлення SchoolNet з бекапу"
echo " Бекап: $TARGET_BACKUP"
echo " УВАГА: Всі поточні дані будуть замінені!"
echo "========================================"
read -p "Продовжити? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Скасовано."
    exit 1
fi

cd "$PROJECT_ROOT"

# 1. Зупинка застосунку, щоб уникнути конфліктів при відновленні БД
echo "🛑 Зупинка застосунку..."
docker compose stop app

# 2. Відновлення БД
if [ -f "$TARGET_BACKUP/database.dump" ]; then
    echo "🗄️  Відновлення бази даних PostgreSQL..."
    # Видалення існуючих з'єднань та старої БД (опціонально, але pg_restore з ключем -c робить drop)
    cat "$TARGET_BACKUP/database.dump" | docker compose exec -T postgres pg_restore -U schoolnet_user -d schoolnet_db -c --if-exists || true
else
    echo "⚠️ Файл database.dump не знайдено, пропускаємо БД."
fi

# 3. Відновлення файлів
if [ -f "$TARGET_BACKUP/media.tar.gz" ]; then
    echo "📂 Відновлення медіа файлів..."
    # Очищення поточного volume і розпакування архіву
    docker run --rm \
      -v schoolwork2_app_media:/target \
      -v "$(realpath "$TARGET_BACKUP")":/backup \
      alpine sh -c "rm -rf /target/* && tar -xzf /backup/media.tar.gz -C /target"
else
    echo "⚠️ Файл media.tar.gz не знайдено, пропускаємо медіа."
fi

# 4. Запуск
echo "🚀 Запуск застосунку..."
docker compose start app

echo "✅ Відновлення успішно завершено."
echo "========================================"
