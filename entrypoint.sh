#!/bin/sh
# Run as the unprivileged `bot` user. Volumes (Railway, docker named volumes)
# are usually mounted root-owned, so give the bot its data directory first,
# while we still can — and nothing else: the code stays root's.
set -e
if [ "$(id -u)" = "0" ]; then
    DATA_DIR="$(dirname "${DB_PATH:-/app/data/bot.db}")"
    case "$DATA_DIR" in /*) ;; *) DATA_DIR="/app/$DATA_DIR" ;; esac   # relative to WORKDIR /app
    case "$DATA_DIR" in
        */..|*/../*) DATA_DIR="" ;;                                   # no climbing out
        /app/data|/app/data/*|/data|/data/*) ;;
        *) [ -n "${RAILWAY_VOLUME_MOUNT_PATH:-}" ] && [ "$DATA_DIR" = "${RAILWAY_VOLUME_MOUNT_PATH%/}" ] || DATA_DIR="" ;;
    esac
    if [ -n "$DATA_DIR" ]; then
        mkdir -p "$DATA_DIR"
        # Only what is not the bot's yet, on this filesystem only: directories,
        # files with a single link (never a hard link to a system file) and
        # symlinks themselves (-h), never sockets or devices.
        find "$DATA_DIR" -xdev ! -user bot \( -type d -o -type l -o \( -type f -links 1 \) \) \
            -exec chown -h bot:bot {} + \
            || echo "entrypoint: could not give $DATA_DIR to the bot — writing the database may fail" >&2
    else
        echo "entrypoint: DB_PATH=${DB_PATH:-} is not under /app/data, /data or RAILWAY_VOLUME_MOUNT_PATH —" \
             "its directory keeps its owner" >&2
    fi
    # --init-groups would drop the docker group: keep the socket's group so
    # container auto-restart and disk cleanup work when the socket is mounted.
    # An empty DOCKER_SOCKET means the default path, as in config.py.
    SOCK="${DOCKER_SOCKET:-/var/run/docker.sock}"
    if [ -S "$SOCK" ]; then
        GID="$(stat -c %g "$SOCK" 2>/dev/null || true)"
        if [ -z "$GID" ]; then
            echo "entrypoint: cannot read the group of $SOCK — starting without Docker access" >&2
        elif [ "$GID" = 0 ] && [ "${ALLOW_ROOT_GROUP_SOCKET:-0}" != 1 ]; then
            echo "entrypoint: $SOCK belongs to group root — the bot does not get group 0, Docker features" \
                 "stay off (a socket proxy or ALLOW_ROOT_GROUP_SOCKET=1, see docs/SETUP.md)" >&2
            # Tell the bot why, so «🔌 Подключения» and the 🟠 note name the fix
            # instead of advising `docker compose up -d` (config.docker_socket_root_group).
            export DOCKER_SOCKET_ROOT_GROUP=1
        else
            exec setpriv --reuid=bot --regid=bot --groups "$GID" "$@"
        fi
    fi
    exec setpriv --reuid=bot --regid=bot --init-groups "$@"
fi
exec "$@"
