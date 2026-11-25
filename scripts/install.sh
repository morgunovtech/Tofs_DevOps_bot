#!/bin/sh
# TofsDevOps on your own server in one command:
#
#   curl -fsSL https://raw.githubusercontent.com/morgunovtech/Tofs_DevOps_bot/main/scripts/install.sh | sh
#
# It makes a folder (~/tofs-devops, or $TOFS_DIR), asks for the bot token,
# writes the two files the README describes (.env and docker-compose.yml) and
# starts the published image. Run it again to update: the token and the
# database stay, the newest image is pulled. Nothing is installed outside
# that folder and Docker's own volume.
set -eu

DIR="${TOFS_DIR:-$HOME/tofs-devops}"

say() { printf '%s\n' "$*"; }
die() { say "✗ $*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 \
  || die "Docker is not installed. Install it first (https://docs.docker.com/engine/install/), then run this again."
docker compose version >/dev/null 2>&1 \
  || die "This Docker has no 'docker compose'. Install the compose plugin: https://docs.docker.com/compose/install/"
docker info >/dev/null 2>&1 \
  || die "Docker is installed but does not answer. Start it, or run this as a user who may use Docker (often: sudo usermod -aG docker \$USER, then log in again)."

mkdir -p "$DIR"
cd "$DIR"

if [ -s .env ] && grep -q '^TELEGRAM_BOT_TOKEN=..*' .env; then
  say "✓ Found an existing install in $DIR — keeping its token and database, updating the bot."
else
  # The script itself may arrive on stdin (curl | sh), so the question goes to the terminal.
  (: </dev/tty) 2>/dev/null || die "No terminal to ask for the token. Create $DIR/.env with one line, TELEGRAM_BOT_TOKEN=<token from @BotFather>, and run this again."
  say "Create a bot at https://t.me/BotFather (/newbot) and copy the token it gives you."
  printf 'Paste the token and press Enter (it will not be shown): '
  stty -echo </dev/tty 2>/dev/null || true
  IFS= read -r TOKEN </dev/tty || TOKEN=""
  stty echo </dev/tty 2>/dev/null || true
  printf '\n'
  TOKEN=$(printf '%s' "$TOKEN" | tr -d '[:space:]')
  printf '%s' "$TOKEN" | grep -Eq '^[0-9]+:[A-Za-z0-9_-]{30,}$' \
    || die "That does not look like a bot token (digits, a colon, then about 35 letters and digits). Nothing was changed; run this again."
  umask 077
  printf 'TELEGRAM_BOT_TOKEN=%s\n' "$TOKEN" >> .env
  chmod 600 .env
  unset TOKEN
fi

# Our own file is refreshed on every run; one you wrote yourself is left alone.
if [ ! -e docker-compose.yml ] || grep -q '^# Written by scripts/install.sh' docker-compose.yml; then
  cat > docker-compose.yml <<'YAML'
# Written by scripts/install.sh — delete this line to keep your own edits.
services:
  bot:
    image: ghcr.io/morgunovtech/tofs_devops_bot:latest
    restart: always
    init: true
    stop_grace_period: 20s
    env_file: .env
    volumes:
      - bot_data:/app/data
    sysctls:
      - net.ipv4.ping_group_range=10001 10001   # ping:// monitors
volumes:
  bot_data:
YAML
fi

docker compose pull
docker compose up -d

# A token Telegram refuses stops the bot a second after it starts, and Docker
# keeps restarting it: say so here instead of «running».
sleep "${INSTALL_CHECK_WAIT:-15}"
# «running» alone is not enough: between two restarts the container is running too.
STATE=$(docker inspect --format '{{.State.Status}} after {{.RestartCount}} restarts' "$(docker compose ps -a -q | head -n 1)" 2>/dev/null || true)
if [ "$STATE" != "running after 0 restarts" ]; then
  say ""
  say "✗ The bot started and stopped (state: ${STATE:-unknown}). Its last lines:" >&2
  docker compose logs --tail 5 2>&1 | sed 's/[0-9]\{8,\}:[A-Za-z0-9_-]\{30,\}/[token]/g' >&2
  say "  Most often the token is wrong: fix it in $DIR/.env and run this again." >&2
  exit 1
fi

say ""
say "✓ The bot is running. Open it in Telegram and send /start — the first /start becomes the owner."
say "  Folder: $DIR    Logs: docker compose logs -f    Update: run this command again."
