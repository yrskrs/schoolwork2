#!/bin/sh
# Run on the existing document-server host after restricting public /office/admin.
set -eu
container=${1:-euro-office}
backup_dir=${2:-./font-manager-backup}
umask 077
mkdir -p "$backup_dir"
if [ ! -f "$backup_dir/ds-adminpanel.conf" ]; then
    docker cp "$container:/etc/supervisor/conf.d/ds-adminpanel.conf" "$backup_dir/ds-adminpanel.conf"
fi
docker exec "$container" sh -eu -c '
    conf=/etc/supervisor/conf.d/ds-adminpanel.conf
    test -f "$conf"
    sed -i "s/^autostart=false$/autostart=true/" "$conf"
    supervisorctl reread
    supervisorctl update adminpanel
    supervisorctl status adminpanel
'
# AdminPanel starts asynchronously. Confirm RUNNING and authenticated access
# separately; do not restart converter/docservice or replace document volumes.
