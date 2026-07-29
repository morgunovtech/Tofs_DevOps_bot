import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


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
        if ":" in pair:
            k, v = pair.rsplit(":", 1)
            try:
                out[k.strip()] = int(v)
            except ValueError:
                pass
    return out


def _quiet_hours(name: str) -> tuple[int, int] | None:
    """Parse "23-8" into (23, 8); None disables quiet hours."""
    raw = os.getenv(name, "").strip()
    if not raw or "-" not in raw:
        return None
    try:
        a, b = raw.split("-", 1)
        start, end = int(a), int(b)
    except ValueError:
        return None
    if 0 <= start <= 23 and 0 <= end <= 23 and start != end:
        return (start, end)
    return None


@dataclass
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_chat_id: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
    # Personal user id for admin checks. Falls back to admin_chat_id, which
    # only works while the admin chat is a private chat (user id == chat id).
    admin_user_id: str = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
    # Only trust X-Forwarded-For when explicitly behind a reverse proxy.
    trust_proxy: bool = _bool("TRUST_PROXY")
    sites: list[str] = field(default_factory=lambda: _csv("SITES"))
    check_interval_minutes: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
    links_check_interval_hours: int = int(os.getenv("LINKS_CHECK_INTERVAL_HOURS", "6"))
    morning_report_hour: int = int(os.getenv("MORNING_REPORT_HOUR", "9"))
    evening_report_hour: int = int(os.getenv("EVENING_REPORT_HOUR", "21"))
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "8080"))
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "change_me")
    db_path: str = os.getenv("DB_PATH", "data/bot.db")

    # ── Dead-man switch (heartbeats) ─────────────────────────────────────────
    # Expected jobs: "backup:1440" = job "backup" must ping at least every
    # 1440 minutes. Ping URL: GET /api/heartbeat/<secret>/<job>
    heartbeat_jobs: dict[str, int] = field(default_factory=lambda: _jobs("HEARTBEAT_JOBS"))
    heartbeat_secret: str = os.getenv(
        "HEARTBEAT_SECRET", os.getenv("WEBHOOK_SECRET", "change_me"))

    # ── Second-opinion check (external vantage point) ────────────────────────
    second_opinion: bool = _bool("SECOND_OPINION", "1")

    # ── Deep 5xx probe (sitemap sampling) ────────────────────────────────────
    deep_check_sample: int = int(os.getenv("DEEP_CHECK_SAMPLE", "10"))

    # ── Cloudflare Pages actions ─────────────────────────────────────────────
    # Deploy hooks per host: "s.morgunov.tech=https://api.cloudflare.com/...".
    deploy_hooks: dict[str, str] = field(default_factory=lambda: _map("DEPLOY_HOOKS"))
    # Fire the deploy hook automatically when a site goes down (once/incident).
    auto_redeploy: bool = _bool("AUTO_REDEPLOY")
    # Token needs Zone.Cache Purge permission; used for the "purge cache" button.
    cf_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cf_zone_id: str = os.getenv("CLOUDFLARE_ZONE_ID", "")

    # ── Bot-host monitoring / remediation ────────────────────────────────────
    docker_socket: str = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")
    # Containers on THIS host to watch & auto-restart (needs docker.sock mount).
    autorestart_containers: list[str] = field(
        default_factory=lambda: _csv("AUTORESTART_CONTAINERS"))
    disk_alert_pct: int = int(os.getenv("DISK_ALERT_PCT", "85"))
    auto_cleanup: bool = _bool("AUTO_CLEANUP", "1")

    # ── SSL renewal watch ────────────────────────────────────────────────────
    # Warn that auto-renewal hasn't happened when this close to expiry.
    ssl_renew_warn_days: int = int(os.getenv("SSL_RENEW_WARN_DAYS", "10"))

    # ── Notifications ────────────────────────────────────────────────────────
    quiet_hours: tuple[int, int] | None = field(
        default_factory=lambda: _quiet_hours("QUIET_HOURS"))
    # Re-alert about unresolved critical incidents every N minutes (0 = off).
    escalation_repeat_min: int = int(os.getenv("ESCALATION_REPEAT_MIN", "30"))

    # ── Weekly report ────────────────────────────────────────────────────────
    weekly_report_hour: int = int(os.getenv("WEEKLY_REPORT_HOUR", "11"))

    # ── Self-maintenance ─────────────────────────────────────────────────────
    retention_days: int = int(os.getenv("RETENTION_DAYS", "30"))
    db_backup_keep: int = int(os.getenv("DB_BACKUP_KEEP", "7"))
    # External watchdog (e.g. healthchecks.io ping URL) — pinged every 5 min.
    self_heartbeat_url: str = os.getenv("SELF_HEARTBEAT_URL", "")

    def get_site_urls(self) -> list[str]:
        return [f"https://{s}" if not s.startswith("http") else s for s in self.sites]


config = Config()
