import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


def _int(name: str, default: int) -> int:
    """Env int that survives empty/garbage values instead of crashing boot."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        import logging
        logging.getLogger(__name__).warning(
            "Invalid %s=%r — using default %s", name, raw, default)
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
        k, sep, v = pair.rpartition(":")
        try:
            out[k.strip()] = int(v)
        except ValueError:
            # A silently dropped entry would disarm the dead-man switch
            # without a trace — say it loudly in the logs.
            import logging
            logging.getLogger(__name__).warning(
                "Ignoring malformed %s entry: %r (want name:minutes)", name, pair)
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
    check_interval_minutes: int = _int("CHECK_INTERVAL_MINUTES", 5)
    links_check_interval_hours: int = _int("LINKS_CHECK_INTERVAL_HOURS", 6)
    morning_report_hour: int = _int("MORNING_REPORT_HOUR", 9)
    evening_report_hour: int = _int("EVENING_REPORT_HOUR", 21)
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")
    webhook_port: int = _int("WEBHOOK_PORT", 8080)
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "change_me")
    db_path: str = os.getenv("DB_PATH", "data/bot.db")
    # Public address of the webhook server (https://bot.example.com or
    # http://1.2.3.4:8080) — used to render ready-to-paste heartbeat URLs.
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    # ── Dead-man switch (heartbeats) ─────────────────────────────────────────
    # Expected jobs: "backup:1440" = job "backup" must ping at least every
    # 1440 minutes. Ping URL: GET /api/heartbeat/<secret>/<job>
    heartbeat_jobs: dict[str, int] = field(default_factory=lambda: _jobs("HEARTBEAT_JOBS"))
    # `or`-fallback (not getenv default): .env.example ships the var EMPTY,
    # and an empty secret would silently break every heartbeat URL.
    heartbeat_secret: str = (os.getenv("HEARTBEAT_SECRET") or
                             os.getenv("WEBHOOK_SECRET") or "change_me")

    # ── Second-opinion check (external vantage point) ────────────────────────
    second_opinion: bool = _bool("SECOND_OPINION", "1")

    # ── Deep 5xx probe (sitemap sampling) ────────────────────────────────────
    deep_check_sample: int = _int("DEEP_CHECK_SAMPLE", 10)

    # ── Index status APIs (optional) ─────────────────────────────────────────
    # Google Search Console: service-account JSON key + property name
    # (domain property "sc-domain:example.com" covers all subdomains).
    gsc_service_account_file: str = os.getenv("GSC_SERVICE_ACCOUNT_FILE", "")
    gsc_property: str = os.getenv("GSC_PROPERTY", "")
    # Yandex.Webmaster: OAuth token with webmaster:read scope.
    yandex_webmaster_token: str = os.getenv("YANDEX_WEBMASTER_TOKEN", "")

    # ── SEO/GEO monitor ──────────────────────────────────────────────────────
    # Pages per site to inspect (homepage + sitemap sample).
    seo_pages_sample: int = _int("SEO_PAGES_SAMPLE", 5)
    # Below this many no-JS text chars on the homepage, AI crawlers are
    # effectively looking at a blank page.
    seo_min_text_chars: int = _int("SEO_MIN_TEXT_CHARS", 400)

    # ── Cloudflare Pages actions ─────────────────────────────────────────────
    # Deploy hooks per host: "example.com=https://api.cloudflare.com/...".
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
    disk_alert_pct: int = _int("DISK_ALERT_PCT", 85)
    auto_cleanup: bool = _bool("AUTO_CLEANUP", "1")

    # ── SSL renewal watch ────────────────────────────────────────────────────
    # Warn that auto-renewal hasn't happened when this close to expiry.
    ssl_renew_warn_days: int = _int("SSL_RENEW_WARN_DAYS", 10)

    # ── Notifications ────────────────────────────────────────────────────────
    quiet_hours: tuple[int, int] | None = field(
        default_factory=lambda: _quiet_hours("QUIET_HOURS"))
    # Re-alert about unresolved critical incidents every N minutes (0 = off).
    escalation_repeat_min: int = _int("ESCALATION_REPEAT_MIN", 30)

    # ── Weekly report ────────────────────────────────────────────────────────
    weekly_report_hour: int = _int("WEEKLY_REPORT_HOUR", 11)

    # ── Page screenshots ─────────────────────────────────────────────────────
    # URL template of a rendering service; {url} is the page. Default is
    # microlink.io (keyless, ~50 req/day). waitForTimeout gives JS entry
    # animations time to finish — without it, animated pages render blank.
    screenshot_template: str = os.getenv(
        "SCREENSHOT_TEMPLATE",
        "https://api.microlink.io/?url={url}&screenshot=true&meta=false"
        "&waitForTimeout=3500")

    # ── Public status page ───────────────────────────────────────────────────
    # Serve a public HTML status page at /status (default off — it reveals
    # the list of monitored sites to anyone who finds the URL).
    status_page: bool = _bool("STATUS_PAGE")
    # Optional secret path segment: when set, the page lives at
    # /status/<slug> and bare /status returns 404 — "private" status page
    # you can share as a link without exposing it to drive-by scanners.
    status_page_slug: str = os.getenv("STATUS_PAGE_SLUG", "").strip().strip("/")

    # ── Self-maintenance ─────────────────────────────────────────────────────
    retention_days: int = _int("RETENTION_DAYS", 30)
    db_backup_keep: int = _int("DB_BACKUP_KEEP", 7)
    # External watchdog (e.g. healthchecks.io ping URL) — pinged every 5 min.
    self_heartbeat_url: str = os.getenv("SELF_HEARTBEAT_URL", "")

    def get_site_urls(self) -> list[str]:
        # startswith(("http://", ...)) not "http" — a domain like httpbin.org
        # must still get a scheme prepended.
        return [s if s.startswith(("http://", "https://")) else f"https://{s}"
                for s in self.sites]


config = Config()
