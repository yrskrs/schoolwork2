#!/usr/bin/env bash
# Скрипт оновлення застосунку SchoolNet
# Забезпечує збереження даних при пересозданні контейнерів.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================"
echo " Оновлення SchoolNet"
echo "========================================"

cd "$PROJECT_ROOT"

# 1. Створення бекапу перед оновленням
echo "🔄 [1/4] Створення резервної копії перед оновленням..."
bash ./scripts/backup.sh || {
    echo "⚠️ Не вдалося створити бекап! Перевірте чи запущені контейнери."
    read -p "Продовжити оновлення без бекапу? (y/n) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Оновлення скасовано."
        exit 1
    fi
}

# 2. Оновлення коду (git pull, якщо використовується git)
# echo "🔄 [2/4] Отримання нового коду..."
# git pull

# 3. Перезбирання контейнера та запуск
echo "🔄 [3/4] Перезбирання Docker образу та запуск..."
docker compose build app
docker compose up -d

# 4. Виконання міграцій БД та збір статики
echo "🔄 [4/4] Застосування міграцій БД та збір статики..."
echo "Очікування готовності БД..."
sleep 5
docker compose exec -T app python manage.py migrate --noinput
docker compose exec -T app python manage.py collectstatic --noinput

echo "✅ Оновлення успішно завершено!"
echo "========================================"
