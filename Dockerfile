FROM python:3.13-slim

# iputils-ping powers ping:// monitors (ICMP needs the binary); everything
# Python ships as wheels, so no compiler is needed.
RUN apt-get update && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home /app --shell /usr/sbin/nologin bot

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p /app/data && chown -R bot:bot /app && chmod +x /app/entrypoint.sh

# Git commit shown in 🩺 Диагностика and "🔄 Бот обновился" (Railway sets
# RAILWAY_GIT_COMMIT_SHA on its own; docker builds can pass --build-arg).
ARG APP_COMMIT=""
ENV APP_COMMIT=$APP_COMMIT

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('WEBHOOK_PORT','8080'), timeout=4).status == 200 else 1)"

# entrypoint fixes ownership of the data volume (platforms mount it as root)
# and drops privileges to `bot` before starting Python.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["python", "main.py"]
