#!/usr/bin/env bash
# Consistent snapshot of PostgreSQL and uploads. Pause writers briefly and resume them.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
mkdir -p "$PROJECT_ROOT/backups"
BACKUP_PATH=$(mktemp -d "$PROJECT_ROOT/backups/$(date +%Y-%m-%d_%H-%M-%S)_XXXXXX")
WRITERS=()
BACKUP_COMPLETE=0
while IFS= read -r service; do
    case "$service" in app|ai_worker) WRITERS+=("$service");; esac
done < <(docker compose ps --status running --services)
resume_writers() {
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
docker compose exec -T postgres sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c' > "$BACKUP_PATH/database.dump.partial"
docker compose run --rm --no-deps -T --entrypoint tar app -czf - -C /app/media . > "$BACKUP_PATH/media.tar.gz.partial"
docker compose exec -T postgres pg_restore --list < "$BACKUP_PATH/database.dump.partial" > /dev/null
gzip -t "$BACKUP_PATH/media.tar.gz.partial"
mv "$BACKUP_PATH/database.dump.partial" "$BACKUP_PATH/database.dump"
mv "$BACKUP_PATH/media.tar.gz.partial" "$BACKUP_PATH/media.tar.gz"
git rev-parse HEAD > "$BACKUP_PATH/code-commit.txt"
docker compose images -q app > "$BACKUP_PATH/docker-image.txt"
(cd "$BACKUP_PATH" && sha256sum database.dump media.tar.gz > SHA256SUMS)
BACKUP_COMPLETE=1
printf 'Резервна копія перевірена: %s\n' "$BACKUP_PATH"
