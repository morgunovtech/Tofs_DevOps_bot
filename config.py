"""Environment configuration. Frozen: runtime state (admin identity,
generated secrets, UI overrides) lives in the services layer, not here."""

import logging
import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from utils.parse import parse_hour_range

load_dotenv()
logger = logging.getLogger(__name__)


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    """Env int that survives empty/garbage values instead of crashing boot."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default


def _csv(name: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, "").split(",") if s.strip()]


def _map(name: str) -> dict[str, str]:
    """Parse "key=value,key2=value2" into a dict."""
    out: dict[str, str] = {}
    for pair in _csv(name):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _jobs(name: str) -> dict[str, int]:
    """Parse "backup:1440,certs:10080" (job:interval_minutes) into a dict."""
    out: dict[str, int] = {}
    for pair in _csv(name):
        k, _, v = pair.rpartition(":")
        try:
            out[k.strip()] = int(v)
        except ValueError:
            # A silently dropped entry would disarm the dead-man switch
            # without a trace — say it loudly in the logs.
            logger.warning("Ignoring malformed %s entry: %r (want name:minutes)", name, pair)
    return out


def _timezone(name: str, default: str = "Europe/Moscow") -> str:
    raw = os.getenv(name, "").strip() or default
    try:
        ZoneInfo(raw)
        return raw
    except ZoneInfoNotFoundError:
        logger.warning("Unknown %s=%r — using %s", name, raw, default)
        return default


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    # Optional: without these the first user to /start becomes admin
    # (persisted in the DB, see services.runtime).
    admin_chat_id: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "").strip()
    admin_user_id: str = os.getenv("TELEGRAM_ADMIN_USER_ID", "").strip()
    # Only trust X-Forwarded-For when explicitly behind a reverse proxy.
    trust_proxy: bool = _bool("TRUST_PROXY")
    # First-run seed only; afterwards the DB is the source of truth.
    sites: tuple[str, ...] = field(default_factory=lambda: tuple(_csv("SITES")))
    check_interval_minutes: int = _int("CHECK_INTERVAL_MINUTES", 5)
    links_check_interval_hours: int = _int("LINKS_CHECK_INTERVAL_HOURS", 6)
    morning_report_hour: int = _int("MORNING_REPORT_HOUR", 9)
    evening_report_hour: int = _int("EVENING_REPORT_HOUR", 21)
    weekly_report_hour: int = _int("WEEKLY_REPORT_HOUR", 11)
    timezone: str = _timezone("TIMEZONE")
    webhook_port: int = _int("WEBHOOK_PORT", 8080)
    # Raw env values; services.secrets replaces empty/placeholder values
    # with generated ones persisted in the DB.
    webhook_secret_env: str = os.getenv("WEBHOOK_SECRET", "").strip()
    heartbeat_secret_env: str = os.getenv("HEARTBEAT_SECRET", "").strip()
    db_path: str = os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db"
    # Public address of the web server (https://bot.example.com) — used to
    # render ready-to-paste heartbeat/status URLs.
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    # Git commit of the running build, when the platform exposes it
    # (Railway sets RAILWAY_GIT_COMMIT_SHA; docker builds can pass APP_COMMIT).
    app_commit: str = (os.getenv("APP_COMMIT") or os.getenv("RAILWAY_GIT_COMMIT_SHA") or "").strip()[:12]

    # ── Dead-man switch (heartbeats) ─────────────────────────────────────────
    heartbeat_jobs: dict[str, int] = field(default_factory=lambda: _jobs("HEARTBEAT_JOBS"))

    # ── Second-opinion check (external vantage point) ────────────────────────
    second_opinion: bool = _bool("SECOND_OPINION", "1")

    # ── Deep 5xx probe (sitemap sampling) ────────────────────────────────────
    deep_check_sample: int = _int("DEEP_CHECK_SAMPLE", 10)

    # ── Index status APIs (optional) ─────────────────────────────────────────
    gsc_service_account_file: str = os.getenv("GSC_SERVICE_ACCOUNT_FILE", "").strip()
    gsc_property: str = os.getenv("GSC_PROPERTY", "").strip()
    yandex_webmaster_token: str = os.getenv("YANDEX_WEBMASTER_TOKEN", "").strip()

    # ── SEO/GEO monitor ──────────────────────────────────────────────────────
    seo_pages_sample: int = _int("SEO_PAGES_SAMPLE", 5)
    seo_min_text_chars: int = _int("SEO_MIN_TEXT_CHARS", 400)

    # ── Cloudflare Pages actions ─────────────────────────────────────────────
    deploy_hooks: dict[str, str] = field(default_factory=lambda: _map("DEPLOY_HOOKS"))
    auto_redeploy: bool = _bool("AUTO_REDEPLOY")
    cf_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    cf_zone_id: str = os.getenv("CLOUDFLARE_ZONE_ID", "").strip()

    # ── Bot-host monitoring / remediation ────────────────────────────────────
    docker_socket: str = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
    autorestart_containers: tuple[str, ...] = field(
        default_factory=lambda: tuple(_csv("AUTORESTART_CONTAINERS")))
    disk_alert_pct: int = _int("DISK_ALERT_PCT", 85)
    auto_cleanup: bool = _bool("AUTO_CLEANUP", "1")

    # ── SSL renewal watch ────────────────────────────────────────────────────
    ssl_renew_warn_days: int = _int("SSL_RENEW_WARN_DAYS", 10)

    # ── Notifications ────────────────────────────────────────────────────────
    quiet_hours: tuple[int, int] | None = field(
        default_factory=lambda: parse_hour_range(os.getenv("QUIET_HOURS", "")))
    escalation_repeat_min: int = _int("ESCALATION_REPEAT_MIN", 30)
    slow_response_ms: int = _int("SLOW_RESPONSE_MS", 3000)

    # ── Page screenshots ─────────────────────────────────────────────────────
    screenshot_template: str = os.getenv(
        "SCREENSHOT_TEMPLATE",
        "https://api.microlink.io/?url={url}&screenshot=true&meta=false"
        "&waitForTimeout=3500")

    # ── Public status page ───────────────────────────────────────────────────
    status_page: bool = _bool("STATUS_PAGE")
    status_page_slug: str = os.getenv("STATUS_PAGE_SLUG", "").strip().strip("/")

    # ── Self-maintenance ─────────────────────────────────────────────────────
    retention_days: int = _int("RETENTION_DAYS", 30)
    db_backup_keep: int = _int("DB_BACKUP_KEEP", 7)
    self_heartbeat_url: str = os.getenv("SELF_HEARTBEAT_URL", "").strip()
    # Weekly check of installed packages against PyPI/OSV (see services.updates).
    dependency_watch: bool = _bool("DEPENDENCY_WATCH", "1")

    def seed_site_urls(self) -> list[str]:
        # startswith(("http://", ...)) not "http" — a domain like httpbin.org
        # must still get a scheme prepended.
        return [s if s.startswith(("http://", "https://", "tcp://", "ping://"))
                else f"https://{s}" for s in self.sites]


config = Config()
