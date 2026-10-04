#!/usr/bin/env bash
# Verify first; pause BOTH writers; restore SQL atomically; install staged uploads.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
TARGET_BACKUP='' AUTO_CONFIRM=0 VERIFY_ONLY=0 LEGACY=()
for arg in "$@"; do
    case "$arg" in
        -y|--yes) AUTO_CONFIRM=1;;
        --verify-only) VERIFY_ONLY=1;;
        --allow-legacy) LEGACY=(--allow-legacy);;
        -*) echo "Невідомий параметр: $arg" >&2; exit 1;;
        *) [ -z "$TARGET_BACKUP" ] || { echo 'Вкажіть лише один каталог копії.' >&2; exit 1; }; TARGET_BACKUP="$arg";;
    esac
done
[ -n "$TARGET_BACKUP" ] && [ -d "$TARGET_BACKUP" ] || { echo 'Використання: scripts/restore.sh КАТАЛОГ [--yes] [--verify-only] [--allow-legacy]' >&2; exit 1; }
# Resolve before changing cwd, so paths from another directory work.
TARGET_BACKUP="$(cd "$TARGET_BACKUP" && pwd)"
python3 "$SCRIPT_DIR/backup_tools.py" validate "$TARGET_BACKUP" "${LEGACY[@]}"
gzip -t "$TARGET_BACKUP/media.tar.gz"
cd "$PROJECT_ROOT"
docker compose run --rm --no-deps -T --entrypoint pg_restore postgres --list < "$TARGET_BACKUP/database.dump" > /dev/null
if [ "$VERIFY_ONLY" -eq 1 ]; then echo 'Копія пройшла перевірку; дані не змінено.'; exit 0; fi
if [ "$AUTO_CONFIRM" -ne 1 ]; then
    read -r -p 'Поточні БД та медіа будуть замінені. Продовжити? (y/N): ' REPLY || REPLY='n'
    [[ "$REPLY" =~ ^[Yy]$ ]] || { echo 'Відновлення скасовано.'; exit 0; }
fi
TIMEOUT="${SCHOOLNET_DEPLOY_TIMEOUT:-240}"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo 'Некоректний SCHOOLNET_DEPLOY_TIMEOUT' >&2; exit 1; }
docker compose up -d --wait --wait-timeout "$TIMEOUT" postgres
# Decode the entire dump BEFORE deleting anything. No secrets are read on the host.
SQL_FILE=$(mktemp)
trap 'rm -f "$SQL_FILE"' EXIT
docker compose exec -T postgres pg_restore --no-owner --no-privileges --file=- < "$TARGET_BACKUP/database.dump" > "$SQL_FILE"
# Capture the current state for recovery even when restoring an older copy.
SCHOOLNET_BACKUP_KEEP_STOPPED=1 bash "$SCRIPT_DIR/backup.sh"
docker compose stop app ai_worker
on_exit() {
    status=$?
    rm -f "$SQL_FILE"
    if [ "$status" -ne 0 ]; then
        docker compose stop app ai_worker || true
        echo 'Відновлення перервано. Вебсервіс і ШІ-воркер залишено зупиненими; поточна копія збережена в backups. Виправте причину або відновіть цю копію.' >&2
    fi
}
trap on_exit EXIT
STAGED=$(docker compose run --rm --no-deps -T \
    -v "$TARGET_BACKUP:/backup:ro" -v "$SCRIPT_DIR/backup_tools.py:/restore_tools.py:ro" \
    --entrypoint python app /restore_tools.py stage /backup/media.tar.gz /app/media)
# DROP and all restored objects/data belong to one transaction. Any SQL error rolls it back.
{ printf 'DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;\n'; cat "$SQL_FILE"; } |
    docker compose exec -T postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" --single-transaction -v ON_ERROR_STOP=1' > /dev/null
docker compose run --rm --no-deps -T -v "$SCRIPT_DIR/backup_tools.py:/restore_tools.py:ro" \
    --entrypoint python app /restore_tools.py install /app/media "$STAGED"
# Conversion caches must not refer to files from the previous database.
docker compose run --rm --no-deps -T --entrypoint python app manage.py shell -c "from django.core.cache import caches; caches['ai_materials'].clear()" > /dev/null
if ! docker compose up -d --wait --wait-timeout "$TIMEOUT" app ai_worker; then
    docker compose stop app ai_worker
    exit 1
fi
docker compose exec -T app python manage.py check
docker compose exec -T app python manage.py migrate --check
trap - EXIT
rm -f "$SQL_FILE"
echo 'Відновлення завершене: БД, медіа, вебсервіс та ШІ-воркер перевірено.'
