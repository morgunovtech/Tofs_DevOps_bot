# 3.14, not 3.15: aiogram 3.31 allows Python below 3.15 only, and the
# pydantic-core of pydantic 2.13 has no 3.15 wheels (the 3.15 bump of
# 01.11.2026 failed to deploy) — move on once an aiogram release allows both
# Python 3.15 and pydantic >= 2.14. The OS suffix is explicit: a bare
# «3.14-slim» changes OS with the next Debian stable. The digest pins the
# exact image CI tested and ran; Dependabot (docker) proposes the next one
# as a PR.
FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

# iputils-ping powers ping:// monitors. Debian's ping has no setuid and no
# capabilities: it opens an ICMP datagram socket, which the kernel allows
# only to groups in net.ipv4.ping_group_range — hence the fixed GID 10001
# (docker-compose.yml opens the range for it). Everything Python ships as
# wheels, so no compiler is needed.
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 bot \
    && useradd --system --uid 10001 --gid 10001 --home /app --shell /usr/sbin/nologin bot

WORKDIR /app

# Install Python deps first (better layer caching).
COPY requirements.txt .
# Exactly the locked versions, each checked against its hash; -B keeps pip's
# own bytecode out of the layer.
RUN python -B -m pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt

COPY . .
# The code stays root's: the bot (uid 10001) must not be able to rewrite what
# root runs at the next start (entrypoint.sh, main.py). It owns only its data
# directory. Bytecode of everything the bot imports (stdlib + /app) is
# written now, as root: the bot cannot write __pycache__ here. `import main`
# doubles as a smoke test of the image (every module and template in place;
# it needs no token, no .env and no network).
RUN python -c "import main" && python -m compileall -q /app \
    && mkdir -p /app/data && chown bot:bot /app/data \
    && chmod 0755 /app/entrypoint.sh

# Git commit shown in 🩺 Диагностика and "🔄 Бот обновился" (Railway sets
# RAILWAY_GIT_COMMIT_SHA on its own; docker builds can pass --build-arg).
ARG APP_COMMIT=""
ENV APP_COMMIT=$APP_COMMIT

# web/healthcheck.py: the port parsed as the bot parses it, no http_proxy.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD ["python", "-m", "web.healthcheck"]

# entrypoint fixes ownership of the data volume (platforms mount it as root)
# and drops privileges to `bot` before starting Python.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
