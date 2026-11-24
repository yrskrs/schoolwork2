#!/usr/bin/env bash
# Consistent snapshot of PostgreSQL and uploads. Pause writers briefly and resume them.
set -euo pipefail
umask 077
export TZ="${TZ:-Europe/Kyiv}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
source "$SCRIPT_DIR/maintenance.sh"
mkdir -p "$PROJECT_ROOT/backups"
BACKUP_PATH=$(mktemp -d "$PROJECT_ROOT/backups/$(date +%Y-%m-%d_%H-%M-%S)_XXXXXX")
WRITERS=()
BACKUP_COMPLETE=0
while IFS= read -r service; do
    case "$service" in app|ai_worker) WRITERS+=("$service");; esac
done < <(docker compose ps --status running --services)
resume_writers() {
    if [ "$BACKUP_COMPLETE" != 1 ]; then
        rm -rf "$BACKUP_PATH" 2>/dev/null || true
    fi
    if [ "${SCHOOLNET_BACKUP_KEEP_STOPPED:-0}" = 1 ] && [ "$BACKUP_COMPLETE" = 1 ]; then return; fi
    if [ "${#WRITERS[@]}" -gt 0 ]; then
        docker compose start "${WRITERS[@]}"
    fi
}
trap resume_writers EXIT
if [ "${#WRITERS[@]}" -gt 0 ]; then
    docker compose stop "${WRITERS[@]}"
fi
# Use credentials already inside the database container; never print them.
printf 'Створюємо дамп PostgreSQL…\n' >&2
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c' < /dev/null > "$BACKUP_PATH/database.dump.partial"
printf 'Архівуємо /app/media (gzip -1)…\n' >&2
docker compose run --rm --no-deps -T --entrypoint tar app -I 'gzip -1' --hard-dereference -cf - -C /app/media . < /dev/null > "$BACKUP_PATH/media.tar.gz.partial"
printf 'Перевіряємо цілісність дампу PostgreSQL…\n' >&2
docker compose exec -T postgres pg_restore --list < "$BACKUP_PATH/database.dump.partial" > /dev/null
printf 'Перевіряємо цілісність архіву media.tar.gz…\n' >&2
gzip -t "$BACKUP_PATH/media.tar.gz.partial"
mv "$BACKUP_PATH/database.dump.partial" "$BACKUP_PATH/database.dump"
mv "$BACKUP_PATH/media.tar.gz.partial" "$BACKUP_PATH/media.tar.gz"
git rev-parse HEAD > "$BACKUP_PATH/code-commit.txt" 2>/dev/null || echo "unknown" > "$BACKUP_PATH/code-commit.txt"
if [ -n "${BACKUP_IMAGE:-}" ]; then
    printf '%s\n' "$BACKUP_IMAGE" > "$BACKUP_PATH/docker-image.txt"
else
    APP_CONTAINER=$(docker compose ps -aq app)
    docker inspect --format '{{.Image}}' "$APP_CONTAINER" > "$BACKUP_PATH/docker-image.txt"
fi
(cd "$BACKUP_PATH" && sha256sum database.dump media.tar.gz > SHA256SUMS)
BACKUP_COMPLETE=1
printf 'Резервна копія перевірена: %s\n' "$BACKUP_PATH"
