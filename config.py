"""Environment configuration. Frozen: runtime state (admin identity,
generated secrets, UI overrides) lives in the services layer, not here."""

import logging
import os
import re
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

from utils.parse import parse_hour_range
from utils.validate import HOURS

load_dotenv()
logger = logging.getLogger(__name__)

_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off"}


def _bool(name: str, default: str = "0") -> bool:
    """1/true/yes/on or 0/false/no/off; unset → default. Any other word →
    warning and default («TRUST_PROXY=ture» must not silently mean «no»).
    An empty value keeps its old meaning «off» — turning AUTO_REDEPLOY or
    SECOND_OPINION on behind someone's back is worse — but it is said."""
    raw = os.getenv(name)
    if raw is None:
        return default in _TRUE
    raw = raw.strip().lower()
    if raw in _TRUE | _FALSE:
        return raw in _TRUE
    if not raw:
        if default in _TRUE:
            logger.warning("%s is empty — treated as off; write %s=1 or %s=0", name, name, name)
        return False
    logger.warning("Invalid %s=%r — using default %s", name, raw, default)
    return default in _TRUE


def _int(name: str, default: int, lo: int | None = None, hi: int | None = None) -> int:
    """Env int that survives empty, garbage and out-of-range values instead of
    crashing boot or the scheduler: a warning names the variable."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r — using default %s", name, raw, default)
        return default
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        logger.warning("%s=%s is out of range %s..%s — using default %s", name, value, lo, hi, default)
        return default
    return value


def _port(name: str, default: int) -> int:
    """A wrong port stops the start with a sentence: a silent default would
    make the server listen where neither HEALTHCHECK nor the owner looks."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 65535):
        logger.error("%s=%r is not a port (1..65535) — fix it in .env; the default is %s", name, raw, default)
        raise SystemExit(2)
    return int(raw)


def _csv(name: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, "").split(",") if s.strip()]


def _hooks(name: str) -> dict[str, str]:
    """Parse "host=https://…,host2=https://…": a pair without «=», with an
    empty side or with a URL that is not https is skipped with a warning
    (the chat wizard accepts only https hooks too)."""
    out: dict[str, str] = {}
    for pair in _csv(name):
        k, sep, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or not k or not v.startswith("https://"):
            # The hook URL is a secret: name the host, never the value.
            logger.warning("Ignoring a %s entry for host %r — want host=https://…", name, k or "?")
            continue
        out[k] = v
    return out


# The dead-man switch URL ends in the job name: no «/», ASCII, short
# enough for the bot's 64-byte buttons. Wider than the chat's own rule
# (utils.validate.job_name) so that names already in .env keep working.
_ENV_JOB = re.compile(r"[A-Za-z0-9_-]{1,50}")


def _jobs(name: str) -> dict[str, int]:
    """Parse "backup:1440,certs:10080" (job:interval_minutes) into a dict."""
    out: dict[str, int] = {}
    for pair in _csv(name):
        k, _, v = pair.rpartition(":")
        k = k.strip()
        try:
            minutes = int(v)
        except ValueError:
            minutes = 0
        if not _ENV_JOB.fullmatch(k) or minutes <= 0:
            # A silently dropped entry would disarm the dead-man switch
            # without a trace — say it loudly in the logs.
            logger.warning("Ignoring %s entry %r (want name:minutes — name of A-Z a-z 0-9 - _ up to 50, "
                           "minutes above 0)", name, pair)
            continue
        out[k] = minutes
    return out


def _quiet(name: str) -> tuple[int, int] | None:
    raw = os.getenv(name, "").strip()
    parsed = parse_hour_range(raw)
    if raw and parsed is None:
        logger.warning("Invalid %s=%r — want two different hours like 23-8; quiet hours are off", name, raw)
    return parsed


def _url(name: str, schemes: tuple[str, ...]) -> str:
    raw = os.getenv(name, "").strip()
    if raw and not raw.startswith(schemes):
        # Such URLs carry tokens (hc-ping): the value stays out of the log.
        logger.warning("Ignoring %s — it must start with %s", name, " or ".join(schemes))
        return ""
    return raw


# Values of a URL template that mean «this feature is off».
TEMPLATE_OFF = ("off", "0", "no", "none", "false")


def _template(name: str, default: str) -> str:
    """A URL template with a {url} field (other braces are a provider's own
    and stay as they are), or an «off» word that switches the feature off."""
    raw = os.getenv(name, "").strip() or default
    if raw.lower() in TEMPLATE_OFF:
        return "off"
    if "{url}" not in raw:
        logger.warning("Invalid %s — it needs {url} (or «off»); using the default", name)
        return default
    return raw


def _match(name: str, pattern: str, hint: str) -> str:
    raw = os.getenv(name, "").strip()
    if raw and not re.fullmatch(pattern, raw):
        logger.warning("Ignoring %s — %s", name, hint)
        return ""
    return raw


def _slug(name: str) -> str:
    """The status page's secret path. Kept as given — dropping it would open
    the page at bare /status — but a slug that cannot work or is easy to
    guess is said at the start."""
    raw = os.getenv(name, "").strip().strip("/")
    if raw and not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", raw):
        logger.warning("%s should be 8–64 characters of A-Z a-z 0-9 - _ : a shorter one is easy to guess, "
                       "and «/», spaces or «?» make the link answer 404", name)
    return raw


def _tg_id(name: str) -> str:
    """A Telegram user or chat id: digits with an optional minus. Anything else
    (a typo, @name, a stray space) is ignored with an error in the log — kept,
    it would silently make nobody the admin."""
    raw = os.getenv(name, "").strip()
    if raw and not re.fullmatch(r"-?[0-9]{1,20}", raw):
        logger.error("Ignoring %s=%r: it must be a numeric Telegram id (@userinfobot shows yours)", name, raw)
        return ""
    return raw


def _choice(name: str, allowed: tuple[str, ...], default: str) -> str:
    """One of a few fixed words; anything else is reported and replaced."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in allowed:
        return raw
    logger.warning("Invalid %s=%r — using %s (allowed: %s)", name, raw, default, ", ".join(allowed))
    return default


def _timezone(name: str, default: str = "Europe/Moscow") -> str:
    raw = os.getenv(name, "").strip() or default
    try:
        ZoneInfo(raw)
        return raw
    except (ZoneInfoNotFoundError, ValueError):
        # ValueError: keys like /etc/localtime, Europe/Moscow/ or ../x.
        logger.warning("Unknown %s=%r — using %s", name, raw, default)
        return default


DEFAULT_NET_CANARIES = ("1.1.1.1,8.8.8.8,9.9.9.9,2606:4700:4700::1111,2001:4860:4860::8888,"
                        "one.one.one.one,dns.google,dns.quad9.net")


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    # Optional: without these the first user to /start becomes admin
    # (persisted in the DB, see services.runtime).
    admin_chat_id: str = _tg_id("TELEGRAM_ADMIN_CHAT_ID")
    admin_user_id: str = _tg_id("TELEGRAM_ADMIN_USER_ID")
    # ── Own-network self-check (monitors.netcheck) ──────────────────────────
    # An IP gets a TCP connect on :443, a name a DNS lookup. Unset → the
    # defaults; set but empty → the self-check is off.
    net_canaries: tuple[str, ...] = field(default_factory=lambda: tuple(
        s.strip() for s in os.getenv("NET_CANARIES", DEFAULT_NET_CANARIES).split(",") if s.strip()))
    # Only trust X-Forwarded-For when explicitly behind a reverse proxy.
    # TRUSTED_PROXIES (optional) narrows it further: the header counts only
    # when the peer is in one of these networks (the proxy as the bot sees
    # it: docker networks, localhost, a CDN's published ranges), and the
    # visitor is the first address right-to-left that is not one of them.
    # Empty: the header is trusted from any peer — only its last IP-shaped entry.
    trust_proxy: bool = _bool("TRUST_PROXY")
    trusted_proxies: tuple[str, ...] = field(default_factory=lambda: tuple(_csv("TRUSTED_PROXIES")))
    # Optional: every call to Telegram goes through this proxy
    # (socks5://user:pass@host:1080 or http://host:3128). For a server where
    # api.telegram.org is not reachable directly — a Russian VPS, for one.
    # Checks of the sites themselves never use it.
    telegram_proxy: str = os.getenv("TELEGRAM_PROXY", "").strip()
    # First-run seed only; afterwards the DB is the source of truth.
    sites: tuple[str, ...] = field(default_factory=lambda: tuple(_csv("SITES")))
    check_interval_minutes: int = _int("CHECK_INTERVAL_MINUTES", 5, 1, 1440)
    links_check_interval_hours: int = _int("LINKS_CHECK_INTERVAL_HOURS", 6, 0, 168)   # 0 = no link crawl
    morning_report_hour: int = _int("MORNING_REPORT_HOUR", 9, *HOURS)
    evening_report_hour: int = _int("EVENING_REPORT_HOUR", 21, *HOURS)
    weekly_report_hour: int = _int("WEEKLY_REPORT_HOUR", 11, *HOURS)
    timezone: str = _timezone("TIMEZONE")
    webhook_port: int = _port("WEBHOOK_PORT", 8080)
    # Raw env values; services.secrets replaces empty/placeholder values
    # with generated ones persisted in the DB.
    webhook_secret_env: str = os.getenv("WEBHOOK_SECRET", "").strip()
    heartbeat_secret_env: str = os.getenv("HEARTBEAT_SECRET", "").strip()
    db_path: str = os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db"
    # Public address of the web server (https://bot.example.com) — used to
    # render ready-to-paste heartbeat/status URLs.
    public_base_url: str = _url("PUBLIC_BASE_URL", ("https://", "http://")).rstrip("/")
    # Git commit of the running build, when the platform exposes it
    # (Railway sets RAILWAY_GIT_COMMIT_SHA; docker builds can pass APP_COMMIT).
    app_commit: str = (os.getenv("APP_COMMIT") or os.getenv("RAILWAY_GIT_COMMIT_SHA") or "").strip()[:12]
    # Railway injects these: the public domain becomes the default
    # PUBLIC_BASE_URL, and host-only features (docker socket) hide themselves.
    railway_public_domain: str = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()
    on_railway: bool = bool(os.getenv("RAILWAY_ENVIRONMENT", "").strip())

    # ── Dead-man switch (heartbeats) ─────────────────────────────────────────
    heartbeat_jobs: dict[str, int] = field(default_factory=lambda: _jobs("HEARTBEAT_JOBS"))

    # ── Second-opinion check (external vantage point) ────────────────────────
    second_opinion: bool = _bool("SECOND_OPINION", "1")
    # Every 6 hours the address of each site goes to check-host.net for the
    # «из России, 0.8 с» line of the card. Its own opt-in, off by default.
    region_speed: bool = _bool("REGION_SPEED", "0")

    # ── Deep 5xx probe (sitemap sampling) ────────────────────────────────────
    deep_check_sample: int = _int("DEEP_CHECK_SAMPLE", 10, 0, 100)

    # ── Index status APIs (optional) ─────────────────────────────────────────
    gsc_service_account_file: str = os.getenv("GSC_SERVICE_ACCOUNT_FILE", "").strip()
    gsc_property: str = os.getenv("GSC_PROPERTY", "").strip()
    yandex_webmaster_token: str = os.getenv("YANDEX_WEBMASTER_TOKEN", "").strip()

    # ── Real visitors' speed from Chrome (Google CrUX API, optional) ─────────
    crux_api_key: str = os.getenv("CRUX_API_KEY", "").strip()

    # ── GitHub hygiene in the weekly report (optional) ───────────────────────
    # Read-only token; the owner defaults to the token's own account.
    github_token: str = os.getenv("GITHUB_TOKEN", "").strip()
    github_owner: str = os.getenv("GITHUB_OWNER", "").strip().lstrip("@")

    # ── SEO/GEO monitor ──────────────────────────────────────────────────────
    seo_pages_sample: int = _int("SEO_PAGES_SAMPLE", 5, 1, 50)
    seo_min_text_chars: int = _int("SEO_MIN_TEXT_CHARS", 400, 0, 100_000)

    # ── Cloudflare Pages actions ─────────────────────────────────────────────
    deploy_hooks: dict[str, str] = field(default_factory=lambda: _hooks("DEPLOY_HOOKS"))
    # On by default: a deploy hook that exists is a fix that exists.
    auto_redeploy: bool = _bool("AUTO_REDEPLOY", "1")
    cf_api_token: str = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
    cf_zone_id: str = _match("CLOUDFLARE_ZONE_ID", r"[0-9a-f]{32}", "a zone id is 32 hex characters")

    # ── Bot-host monitoring / remediation ────────────────────────────────────
    docker_socket: str = os.getenv("DOCKER_SOCKET", "").strip() or "/var/run/docker.sock"
    # Set by entrypoint.sh, not by the owner: the mounted socket belongs to
    # group root and ALLOW_ROOT_GROUP_SOCKET=1 is not in .env, so the bot has
    # no access to it — «🔌 Подключения» and the 🟠 note name that fix.
    docker_socket_root_group: bool = _bool("DOCKER_SOCKET_ROOT_GROUP", "0")
    autorestart_containers: tuple[str, ...] = field(
        default_factory=lambda: tuple(_csv("AUTORESTART_CONTAINERS")))
    disk_alert_pct: int = _int("DISK_ALERT_PCT", 85, 50, 99)   # below 50 prune would fire on a near-empty disk
    # Off by default: removes dangling docker images and old build cache on the
    # whole host when the disk is full (services.docker_api.prune).
    auto_cleanup: bool = _bool("AUTO_CLEANUP", "0")

    # ── Where checks may connect (utils.net) ─────────────────────────────────
    # 1: a monitor the owner types may be an internal address (a NAS, a LAN
    # service) — as before; so may its own name when the DNS points it inside.
    # 0: only public addresses, even typed ones — the strict mode for watching
    # other people's sites. Links, nested sitemaps, the redirects of public
    # sites and the screenshot service's image never lead inside either way.
    allow_private_targets: bool = _bool("ALLOW_PRIVATE_TARGETS", "1")

    # ── SSL renewal watch ────────────────────────────────────────────────────
    ssl_renew_warn_days: int = _int("SSL_RENEW_WARN_DAYS", 10, 0, 60)

    # ── Notifications ────────────────────────────────────────────────────────
    quiet_hours: tuple[int, int] | None = field(default_factory=lambda: _quiet("QUIET_HOURS"))
    escalation_repeat_min: int = _int("ESCALATION_REPEAT_MIN", 30, 0, 1440)   # 0 = no repeats
    slow_response_ms: int = _int("SLOW_RESPONSE_MS", 3000, 100, 120_000)

    # ── Page screenshots ─────────────────────────────────────────────────────
    screenshot_template: str = _template(
        "SCREENSHOT_TEMPLATE",
        "https://api.microlink.io/?url={url}&screenshot=true&meta=false"
        "&waitForTimeout=3500")

    # ── Public status page ───────────────────────────────────────────────────
    status_page: bool = _bool("STATUS_PAGE")
    status_page_slug: str = _slug("STATUS_PAGE_SLUG")
    # Language of what visitors see (status page, its JSON texts, badges): ru | en.
    status_page_lang: str = _choice("STATUS_PAGE_LANG", ("ru", "en"), "ru")
    # Name shown above the banner and in the browser tab; empty — a generic one.
    status_page_title: str = os.getenv("STATUS_PAGE_TITLE", "").strip()

    # ── Self-maintenance ─────────────────────────────────────────────────────
    retention_days: int = _int("RETENTION_DAYS", 30, 1, 3650)
    db_backup_keep: int = _int("DB_BACKUP_KEEP", 7, 1, 365)
    self_heartbeat_url: str = _url("SELF_HEARTBEAT_URL", ("https://", "http://"))
    # Weekly OSV vulnerability check of every installed package, plus the hourly
    # GitHub deploy watch (services.updates; it reads GITHUB_REPOSITORY itself) —
    # one switch for both.
    dependency_watch: bool = _bool("DEPENDENCY_WATCH", "1")

    def seed_site_urls(self) -> list[str]:
        # startswith(("http://", ...)) not "http" — a domain like httpbin.org
        # must still get a scheme prepended.
        return [s if s.startswith(("http://", "https://", "tcp://", "ping://"))
                else f"https://{s}" for s in self.sites]


config = Config()
