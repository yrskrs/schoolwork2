#!/usr/bin/env bash
# Fast-forward code, take a verified snapshot, then replace both application services.
set -euo pipefail
umask 077
update_main() {
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"
TIMEOUT="${SCHOOLNET_DEPLOY_TIMEOUT:-240}"
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || { echo 'Некоректний SCHOOLNET_DEPLOY_TIMEOUT' >&2; exit 1; }
docker compose up --help | grep -q -- '--wait-timeout' || { echo 'Потрібен Docker Compose з --wait-timeout.' >&2; exit 1; }
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    [ -z "$(git status --porcelain)" ] || { echo 'Є локальні зміни. Збережіть їх перед оновленням.' >&2; exit 1; }
    BRANCH=$(git symbolic-ref --quiet --short HEAD) || { echo 'Detached HEAD: оберіть гілку перед оновленням.' >&2; exit 1; }
    python3 "$SCRIPT_DIR/fetch_update.py" --branch "$BRANCH"
    git merge-base --is-ancestor HEAD "origin/$BRANCH" || { echo 'Гілки розійшлися; автоматичне оновлення скасовано.' >&2; exit 1; }
else
    BRANCH=''
fi
# Fail closed: a failed or missing backup must never become a successful deployment.
bash "$SCRIPT_DIR/backup.sh"
if [ -n "$BRANCH" ]; then git merge --ff-only "origin/$BRANCH"; fi
python3 "$SCRIPT_DIR/select_release_image.py" schoolwork2
export VCS_REF="$(git rev-parse HEAD 2>/dev/null || printf local)"
APP_ID=$(docker compose ps -q app)
if [ -n "$APP_ID" ]; then
    IMAGE_ID=$(docker inspect --format '{{.Image}}' "$APP_ID")
    docker image tag "$IMAGE_ID" "schoolwork2-app:before-update-$(date +%Y%m%d-%H%M%S)"
fi
# Build while the existing services are still serving requests.
docker compose build app
# Stop both writers before the app startup performs migrations.
docker compose stop app ai_worker
if ! docker compose up -d --wait --wait-timeout "$TIMEOUT" app ai_worker; then
    echo 'Оновлення не пройшло healthcheck. Перевірте docker compose ps/logs; резервна копія збережена.' >&2
    exit 1
fi
python3 "$SCRIPT_DIR/configure_integration_network.py"
docker compose exec -T app python manage.py check
docker compose exec -T app python manage.py migrate --check
printf 'Оновлення завершене: вебсервіс і ШІ-воркер здорові.\n'
}
update_main "$@"
