# 3.14, not 3.15: aiogram 3.31 caps Python below 3.15 and the image does
# not build there (the 3.15 bump of 01.11.2026 failed to deploy). CI builds
# this image, so the next bump goes in only once it builds.
FROM python:3.14-slim

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

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# The code stays root's: the bot (uid 10001) must not be able to rewrite what
# root runs at the next start (entrypoint.sh, main.py). It owns only its data
# directory. Bytecode is compiled now: the bot cannot write __pycache__ here.
RUN python -m compileall -q /app \
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
