#!/usr/bin/env bash
# Local OAuth access to SchoolWork; does not expose a new public server port.
set -euo pipefail
LOCAL_PORT=18080
SSH_SERVER=yrsadmin@192.168.1.13
BASE_URL="http://localhost:$LOCAL_PORT"
check_schoolwork() {
    python3 - "$BASE_URL" <<'PY'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(sys.argv[1] + '/healthz/', timeout=4) as response:
        data = json.load(response)
    sys.exit(0 if data.get('status') == 'ok' and str(data.get('version', '')).startswith('4.') else 1)
except Exception:
    sys.exit(1)
PY
}
if ! check_schoolwork; then
    ssh -F /dev/null -o BatchMode=yes -o ConnectTimeout=10 \
        -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 -fNT \
        -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:8080" "$SSH_SERVER"
    check_schoolwork || { echo 'Тунель запущено, але SchoolWork не відповідає.' >&2; exit 1; }
fi
printf 'SchoolWork: %s/teacher/backups\n' "$BASE_URL"
printf 'Відкрийте цю адресу у звичайному браузері на цьому комп’ютері та ввійдіть у SchoolWork.\n'
printf 'Тунель потрібен лише для входу в Google, а не для наступних резервних копій.\n'
