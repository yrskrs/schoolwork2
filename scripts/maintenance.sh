#!/usr/bin/env bash
# Inherit the lock from an updater/restore job; otherwise acquire it once.
if [[ "${MAINTENANCE_LOCK_HELD:-0}" != 1 ]]; then
    exec 9>"$PROJECT_ROOT/.maintenance.lock"
    flock -n 9 || { echo 'Інша операція обслуговування вже виконується.' >&2; exit 1; }
    export MAINTENANCE_LOCK_HELD=1
fi
