#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
lab_env=.env.ai-lab.local
if [[ ! -f "$lab_env" ]]; then
  python3 - <<'PY'
import os, secrets
path = '.env.ai-lab.local'
with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
    f.write('LAB_PORT=8010\nDEBUG=True\n')
    for key in ('SECRET_KEY', 'LAB_DB_PASSWORD', 'LAB_RUNNER_TOKEN', 'LAB_TEACHER_PASSWORD'):
        f.write(f'{key}={secrets.token_hex(24)}\n')
PY
fi
compose() {
  local args=(docker compose --env-file "$lab_env" -f compose.ai-lab.yaml "$@")
  if docker info >/dev/null 2>&1; then
    "${args[@]}"
  elif id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    local command
    printf -v command '%q ' "${args[@]}"
    sg docker -c "$command"
  else
    echo 'Немає доступу до Docker. Потрібен запущений Docker та членство користувача у групі docker.' >&2
    return 1
  fi
}
verify() {
  compose exec -T app python manage.py shell <<'PY'
import os
from django.test import Client
from feed.models import ClassGroup, Student, Teacher
teacher = Teacher.objects.get(user__username='teacher_lab')
groups = teacher.classes.all()
assert groups.count() >= 7
assert Student.objects.filter(class_group__in=groups).count() >= 35
client = Client()
assert client.login(username='teacher_lab', password=os.environ['LAB_TEACHER_PASSWORD'])
for path in ('/teacher/', '/teacher/assignment/create/', '/teacher/settings/'):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
print('Перевірено: вхід, кабінет, створення завдання, налаштування, 7 класів та 35 учнів.')
PY
}
case "${1:-up}" in
  init) echo "Локальні секрети підготовлено: $lab_env" ;;
  up)
    command -v docker >/dev/null || { echo 'Docker не встановлено.' >&2; exit 1; }
    # Nested named-volume mountpoints must exist inside the read-only source bind.
    mkdir -p media logs staticfiles
    compose build app code_runner
    compose up -d --wait --wait-timeout 300
    compose exec -T app python manage.py seed_ai_lab
    compose exec -T app python manage.py check
    verify
    compose ps
    echo 'Сайт: http://127.0.0.1:8010/teacher/login/; доступ: scripts/ai-lab.sh credentials'
    ;;
  stop) compose stop ;;
  restart) compose restart app ai_worker ;;
  status) compose ps ;;
  verify) verify ;;
  logs) compose logs --tail=100 app ai_worker ;;
  credentials)
    python3 - <<'PY'
from pathlib import Path
values = dict(line.split('=', 1) for line in Path('.env.ai-lab.local').read_text().splitlines() if '=' in line)
print(f"Адреса: http://127.0.0.1:{values['LAB_PORT']}/teacher/login/")
print('Логін: teacher_lab')
print('Пароль: ' + values['LAB_TEACHER_PASSWORD'])
PY
    ;;
  test) compose exec -T app python manage.py test feed.tests_ai_assessment_v4 feed.tests_ai_payload --settings=schoolnet.test_settings --noinput ;;
  *) echo 'Команди: init | up | stop | restart | status | verify | logs | credentials | test' >&2; exit 2 ;;
esac
