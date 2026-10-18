#!/usr/bin/env bash
# Inherit the lock from an updater/restore job; otherwise acquire it once.
if [[ "${MAINTENANCE_LOCK_HELD:-0}" != 1 ]]; then
    # flock on Linux works with a read-only descriptor. Existing locks may
    # belong to the updater container's UID; do not truncate or replace them.
    if [[ ! -e "$PROJECT_ROOT/.maintenance.lock" ]]; then
        (umask 022; set -o noclobber; : > "$PROJECT_ROOT/.maintenance.lock") 2>/dev/null || [[ -f "$PROJECT_ROOT/.maintenance.lock" ]]
    fi
    exec 9<"$PROJECT_ROOT/.maintenance.lock"
    flock -n 9 || { echo 'Інша операція обслуговування вже виконується.' >&2; exit 1; }
    export MAINTENANCE_LOCK_HELD=1
fi
