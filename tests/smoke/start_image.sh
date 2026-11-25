#!/bin/sh
# Starts the built image for real and waits for GET /health → 200 — what CI's
# «the image starts and answers /health» step runs, and what a person can run
# after `docker build --tag devops-bot-ci .`:
#
#     tests/smoke/start_image.sh devops-bot-ci [timeout-seconds]
#
# No real token and no Telegram: the bot is pointed (TELEGRAM_API_URL) at
# tests/smoke/telegram_stub.py, which runs in a second container of the same
# image, on a Docker network marked --internal — nothing on it can reach the
# internet. The bot starts as in production: entrypoint → uid 10001 →
# database → web server → getMe → polling. It gets PORT and no WEBHOOK_PORT,
# as on a hosting platform. Passes when, within the timeout,
#   - GET /health from another container answers 200,
#   - the log says «Starting polling...» (getMe accepted the token),
#   - the image's own HEALTHCHECK command agrees,
# and a token the stub does not know still stops the start with exit code 2.
# Anything else: the containers' logs, exit code 1.
set -eu

IMAGE="${1:?usage: start_image.sh <image> [timeout-seconds]}"
TIMEOUT="${2:-90}"
HERE="$(cd "$(dirname "$0")" && pwd)"
NET="bot-smoke-$$"
STUB="$NET-telegram"
BOT="$NET-bot"
TOKEN="123456:ci-smoke-token"        # the shape aiogram asks for; no bot has it
PORT=18080

cleanup() {
    docker rm --force "$BOT" "$STUB" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "FAILED: $1" >&2
    echo "──── bot log ────" >&2
    docker logs "$BOT" >&2 2>&1 || true
    echo "──── Telegram stub log ────" >&2
    docker logs "$STUB" >&2 2>&1 || true
    exit 1
}

docker network create --internal "$NET" >/dev/null
docker create --name "$STUB" --network "$NET" --user 10001:10001 --entrypoint python \
    "$IMAGE" -u /tmp/telegram_stub.py "$TOKEN" 8081 >/dev/null
docker cp "$HERE/telegram_stub.py" "$STUB:/tmp/telegram_stub.py" >/dev/null
docker start "$STUB" >/dev/null

docker run --detach --name "$BOT" --network "$NET" \
    --env TELEGRAM_BOT_TOKEN="$TOKEN" --env TELEGRAM_API_URL="http://$STUB:8081" --env PORT="$PORT" \
    "$IMAGE" >/dev/null

# GET /health from the stub's container: over the network, not on localhost.
GET="import sys, urllib.request
try:
    print(urllib.request.urlopen(sys.argv[1], timeout=3).status)
except Exception as e:
    print(getattr(e, 'code', type(e).__name__))"

status="none"
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
    [ "$(docker inspect --format '{{.State.Running}}' "$BOT" 2>/dev/null)" = "true" ] \
        || fail "the bot stopped (exit code $(docker inspect --format '{{.State.ExitCode}}' "$BOT" 2>/dev/null || echo '?'))"
    status="$(docker exec "$STUB" python -c "$GET" "http://$BOT:$PORT/health" 2>/dev/null || echo "none")"
    if [ "$status" = "200" ] && docker logs "$BOT" 2>&1 | grep -q "Starting polling"; then
        break
    fi
    [ "$(date +%s)" -lt "$deadline" ] || fail "no «GET /health → 200» with polling started in ${TIMEOUT} s (last answer: $status)"
    sleep 1
done
docker exec "$BOT" python -m web.healthcheck || fail "/health answers 200, but the image's HEALTHCHECK command does not see it"
echo "ok: GET /health → 200 on PORT=$PORT, polling started, HEALTHCHECK agrees"

# The stub is not a way around the token check: another token is refused
# (401, as Telegram answers) and the start ends with the setting's exit code.
code=0
docker run --rm --network "$NET" --env TELEGRAM_BOT_TOKEN="123456:not-the-token" \
    --env TELEGRAM_API_URL="http://$STUB:8081" "$IMAGE" >/dev/null 2>&1 || code=$?
[ "$code" = 2 ] || fail "a token the server refuses must stop the start with exit code 2, got $code"
echo "ok: a refused token stops the start (exit code 2)"
