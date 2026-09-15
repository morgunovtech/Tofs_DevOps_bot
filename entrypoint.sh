#!/bin/sh
# Run as the unprivileged `bot` user. Volumes (Railway, docker named volumes)
# are usually mounted root-owned, so fix that first while we still can.
set -e
DATA_DIR="$(dirname "${DB_PATH:-/app/data/bot.db}")"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R bot:bot "$DATA_DIR" 2>/dev/null || true
    exec setpriv --reuid=bot --regid=bot --init-groups "$@"
fi
exec "$@"
