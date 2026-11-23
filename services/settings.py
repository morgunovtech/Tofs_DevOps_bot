"""UI-editable overrides on top of .env defaults, cached in memory and
written through to bot_state. Sync getters so schedule triggers and
quiet-hours checks never touch the DB.

The dials of «🛠 Тонкие настройки» (DIALS, the status page's language and
title, the bot's language) are the .env knobs an owner changes more than
once a year: every reader asks the getter here — settings.check_interval_minutes(),
never config.check_interval_minutes — so a tap in the chat reaches the
scheduler, the monitors and the status page at once, with no restart."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import config
from db import database
from db.database import delete_heartbeat, get_state, set_state
from utils import clock, i18n, validate
from utils.parse import parse_hour_range


@dataclass
class Overrides:
    morning_hour: int | None = None      # None → .env
    evening_hour: int | None = None      # None → .env (unless evening_off)
    evening_off: bool = False
    weekly_hour: int | None = None
    quiet: tuple[int, int] | None = None  # None → .env (unless quiet_off)
    quiet_off: bool = False
    hb_jobs: dict[str, int] = field(default_factory=dict)  # added via UI
    hb_hidden: set[str] = field(default_factory=set)       # .env jobs removed in the bot
    status_page: bool | None = None      # None → .env
    timezone: str | None = None          # IANA key; None → .env
    ring_only_down: bool = False         # ring only for «site down»; the rest waits quietly
    weekly_backup: bool = True           # Sunday: a copy of the DB (without tokens) to the owner's private chat
    digest_full: bool = False            # morning digest: a line per site («🌅 Каждый сайт») instead of the short view
    dials: dict[str, int] = field(default_factory=dict)   # «🛠 Тонкие настройки»: DIALS keys set in the chat
    status_page_title: str | None = None  # None → .env; "" → the generic title
    status_page_lang: str | None = None   # ru | en; None → .env
    lang: str | None = None               # the bot's own language; None → .env (BOT_LANG)


@dataclass(frozen=True)
class Dial:
    """A number that used to be set only in .env: its bot_state key, the
    .env variable it stands in for, the bound every door applies and the
    config attribute with the .env value."""
    key: str
    env: str
    bounds: tuple[int, int]
    attr: str


# What «🛠 Тонкие настройки» may change. The order is the screen's.
DIALS: dict[str, Dial] = {d.key: d for d in (
    Dial("check_interval", "CHECK_INTERVAL_MINUTES", validate.CHECK_INTERVAL_MIN, "check_interval_minutes"),
    Dial("slow_ms", "SLOW_RESPONSE_MS", validate.SLOW_MS, "slow_response_ms"),
    Dial("escalation_repeat", "ESCALATION_REPEAT_MIN", validate.ESCALATION_REPEAT_MIN, "escalation_repeat_min"),
    Dial("ssl_renew_warn", "SSL_RENEW_WARN_DAYS", validate.SSL_RENEW_WARN_DAYS, "ssl_renew_warn_days"),
    Dial("disk_alert_pct", "DISK_ALERT_PCT", validate.DISK_ALERT_PCT, "disk_alert_pct"),
    Dial("links_hours", "LINKS_CHECK_INTERVAL_HOURS", validate.LINKS_INTERVAL_HOURS, "links_check_interval_hours"),
    Dial("deep_sample", "DEEP_CHECK_SAMPLE", validate.DEEP_SAMPLE, "deep_check_sample"),
    Dial("seo_pages", "SEO_PAGES_SAMPLE", validate.SEO_PAGES, "seo_pages_sample"),
    Dial("seo_min_text", "SEO_MIN_TEXT_CHARS", validate.SEO_MIN_TEXT, "seo_min_text_chars"),
)}
STATUS_PAGE_TITLE_MAX = 60


_o = Overrides()


async def load():
    """Populate the cache from bot_state; call once at startup."""
    global _o
    o = Overrides()
    # A saved 24 or 99 (an old bug, a restored file) means «not set», not a
    # scheduler that refuses to start.
    o.morning_hour = validate.hour(await get_state("cfg:morning_hour"))
    v = await get_state("cfg:evening_hour")
    o.evening_off = v == "off"
    o.evening_hour = validate.hour(v)
    o.weekly_hour = validate.hour(await get_state("cfg:weekly_hour"))
    v = await get_state("cfg:quiet_hours")
    o.quiet_off = v == "off"
    o.quiet = parse_hour_range(v) if v and v != "off" else None
    try:
        jobs = json.loads(await get_state("cfg:hb_jobs") or "{}")
        o.hb_jobs = {str(k): int(v) for k, v in jobs.items()} if isinstance(jobs, dict) else {}
    except (ValueError, TypeError):
        o.hb_jobs = {}
    try:
        hidden = json.loads(await get_state("cfg:hb_hidden") or "[]")
        o.hb_hidden = {str(n) for n in hidden} if isinstance(hidden, list) else set()
    except ValueError:
        o.hb_hidden = set()
    v = await get_state("cfg:status_page")
    o.status_page = {"on": True, "off": False}.get(v or "")
    o.timezone = valid_timezone(await get_state("cfg:timezone"))
    clock.set_timezone(o.timezone)
    o.ring_only_down = (await get_state("cfg:ring_only_down")) == "1"
    o.weekly_backup = (await get_state("cfg:weekly_backup")) != "off"
    o.digest_full = (await get_state("cfg:digest_full")) == "1"
    for d in DIALS.values():
        # A value outside the bound (a restored copy, an older build) is «not set».
        v = validate.int_in(await get_state(f"cfg:{d.key}"), *d.bounds)
        if v is not None:
            o.dials[d.key] = v
    v = await get_state("cfg:status_page_title")
    o.status_page_title = v if isinstance(v, str) and len(v) <= STATUS_PAGE_TITLE_MAX else None
    v = await get_state("cfg:status_page_lang")
    o.status_page_lang = v if v in ("ru", "en") else None
    v = await get_state("cfg:lang")
    o.lang = v if v in i18n.LANGUAGES else None
    _o = o
    database.set_default_interval_min(o.dials.get("check_interval"))
    if o.lang:   # config.py set the .env language at import; only a choice made here changes it
        i18n.set_language(o.lang)


def valid_timezone(key: str | None) -> str | None:
    if not key or not isinstance(key, str):     # an import may carry «timezone: 5»
        return None
    try:
        ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        return None
    return key


def timezone() -> str:
    return _o.timezone or config.timezone


def ring_only_down() -> bool:
    return _o.ring_only_down


def weekly_backup() -> bool:
    """Sunday copy of the database to the owner's private chat — on by default."""
    return _o.weekly_backup


def digest_full() -> bool:
    """Morning digest with a line per site; off by default — the short view
    says only what is wrong and how many sites are fine."""
    return _o.digest_full


# ── Effective values (override → env default) ────────────────────────────────

def morning_hour() -> int:
    return _o.morning_hour if _o.morning_hour is not None else config.morning_report_hour


def evening_hour() -> int | None:
    """None = evening report disabled."""
    if _o.evening_off:
        return None
    return _o.evening_hour if _o.evening_hour is not None else config.evening_report_hour


def weekly_hour() -> int:
    return _o.weekly_hour if _o.weekly_hour is not None else config.weekly_report_hour


def quiet_hours() -> tuple[int, int] | None:
    if _o.quiet_off:
        return None
    return _o.quiet if _o.quiet is not None else config.quiet_hours


def heartbeat_jobs() -> dict[str, int]:
    """env-defined jobs merged with UI-added ones (UI wins on name clash),
    minus the env jobs removed in the bot."""
    env = {k: v for k, v in config.heartbeat_jobs.items() if k not in _o.hb_hidden}
    return {**env, **_o.hb_jobs}


def ui_heartbeat_jobs() -> dict[str, int]:
    """Only the jobs added via the bot UI (what the export carries)."""
    return dict(_o.hb_jobs)


def heartbeat_job_from_env(name: str) -> bool:
    """Defined in HEARTBEAT_JOBS on the server rather than added in the bot."""
    return name in config.heartbeat_jobs and name not in _o.hb_jobs


def status_page_enabled() -> bool:
    return _o.status_page if _o.status_page is not None else config.status_page


# ── Dials («🛠 Тонкие настройки»): the chat's value, else .env ───────────────

def dial(key: str) -> int:
    d = DIALS[key]
    return _o.dials.get(key, getattr(config, d.attr))


def dial_from_env(key: str) -> bool:
    """True while the .env value is in force — nothing was chosen in the chat."""
    return key not in _o.dials


def dial_default(key: str) -> int:
    """The .env value (or config's default) the dial falls back to."""
    return getattr(config, DIALS[key].attr)


def check_interval_minutes() -> int:
    """Minutes between two checks of a site that has no interval of its own."""
    return dial("check_interval")


def slow_response_ms() -> int:
    return dial("slow_ms")


def escalation_repeat_min() -> int:
    """The first reminder about a down site; 0 = no reminders."""
    return dial("escalation_repeat")


def ssl_renew_warn_days() -> int:
    return dial("ssl_renew_warn")


def disk_alert_pct() -> int:
    return dial("disk_alert_pct")


def links_check_interval_hours() -> int:
    """Hours between two link crawls; 0 = no crawl."""
    return dial("links_hours")


def deep_check_sample() -> int:
    return dial("deep_sample")


def seo_pages_sample() -> int:
    return dial("seo_pages")


def seo_min_text_chars() -> int:
    return dial("seo_min_text")


def status_page_title() -> str:
    return _o.status_page_title if _o.status_page_title is not None else config.status_page_title


def status_page_lang() -> str:
    return _o.status_page_lang or config.status_page_lang


def lang() -> str:
    """The bot's own language — the chat's choice, else BOT_LANG."""
    return _o.lang or config.lang


def text_from_env(key: str) -> bool:
    """For the three non-numeric dials: True while .env is in force."""
    return {"status_page_title": _o.status_page_title, "status_page_lang": _o.status_page_lang, "lang": _o.lang}[key] is None


# ── Setters (write-through) ──────────────────────────────────────────────────

async def set_morning_hour(hour: int) -> bool:
    """False, and nothing saved, for anything but an hour 0..23."""
    h = validate.hour(hour)
    if h is None:
        return False
    _o.morning_hour = h
    await set_state("cfg:morning_hour", str(h))
    return True


async def set_evening_hour(hour: int | None) -> bool:
    h = None if hour is None else validate.hour(hour)
    if hour is not None and h is None:
        return False
    _o.evening_off = h is None
    _o.evening_hour = h
    await set_state("cfg:evening_hour", "off" if h is None else str(h))
    return True


async def set_weekly_hour(hour: int) -> bool:
    h = validate.hour(hour)
    if h is None:
        return False
    _o.weekly_hour = h
    await set_state("cfg:weekly_hour", str(h))
    return True


async def set_quiet_hours(raw: str | None) -> bool:
    """raw: '23-8' style or None to disable. False for anything else."""
    if raw is not None:
        raw = validate.quiet_hours(raw)
        if raw is None:
            return False
    _o.quiet_off = raw is None
    _o.quiet = parse_hour_range(raw) if raw else None
    await set_state("cfg:quiet_hours", raw if raw else "off")
    return True


async def set_status_page(on: bool):
    _o.status_page = on
    await set_state("cfg:status_page", "on" if on else "off")


async def set_timezone(key: str | None) -> bool:
    """Validated IANA key or None to fall back to .env. False if unknown."""
    if key is not None and not valid_timezone(key):
        return False
    _o.timezone = key
    clock.set_timezone(key)
    await set_state("cfg:timezone", key)
    return True


async def set_ring_only_down(on: bool):
    _o.ring_only_down = on
    await set_state("cfg:ring_only_down", "1" if on else "0")


async def set_weekly_backup(on: bool):
    _o.weekly_backup = on
    await set_state("cfg:weekly_backup", "on" if on else "off")


async def set_digest_full(on: bool):
    _o.digest_full = on
    await set_state("cfg:digest_full", "1" if on else "0")


async def set_dial(key: str, value: int | None) -> bool:
    """A number within the dial's bound, or None to fall back to .env.
    False, and nothing saved, for anything else."""
    d = DIALS.get(key)
    if d is None:
        return False
    if value is None:
        _o.dials.pop(key, None)
    else:
        v = validate.int_in(value, *d.bounds)
        if v is None:
            return False
        _o.dials[key] = v
    await set_state(f"cfg:{key}", None if value is None else str(_o.dials[key]))
    if key == "check_interval":   # the availability windows in the DB read it without importing this module
        database.set_default_interval_min(_o.dials.get(key))
    return True


async def set_status_page_title(title: str | None) -> bool:
    """One line up to STATUS_PAGE_TITLE_MAX characters; "" — the generic
    title; None — as in .env."""
    if title is not None:
        title = " ".join(title.split())
        if len(title) > STATUS_PAGE_TITLE_MAX:
            return False
    _o.status_page_title = title
    await set_state("cfg:status_page_title", title)
    return True


async def set_status_page_lang(code: str | None) -> bool:
    if code is not None and code not in ("ru", "en"):
        return False
    _o.status_page_lang = code
    await set_state("cfg:status_page_lang", code)
    return True


async def set_lang(code: str | None) -> bool:
    """The bot's language from the chat; None — as BOT_LANG says. Every
    screen, alert and report switches at once (utils.i18n)."""
    if code is not None and code not in i18n.LANGUAGES:
        return False
    _o.lang = code
    await set_state("cfg:lang", code)
    i18n.set_language(code or config.lang)
    return True


MAX_HB_JOBS = 12
valid_job_name = validate.job_name      # handlers.heartbeats asks settings, the rule lives in utils.validate


async def add_heartbeat_job(name: str, interval_min: int) -> bool:
    """False for an invalid name or interval, or when the list is full."""
    if (not valid_job_name(name) or interval_min <= 0
            or (name not in _o.hb_jobs and len(_o.hb_jobs) >= MAX_HB_JOBS)):
        return False
    if name not in heartbeat_jobs():
        # Its silence is counted from here (services.heartbeats), not from the bot's start.
        await set_state(f"hb_added:{name}", datetime.now(UTC).isoformat(timespec="seconds"))
    _o.hb_jobs[name] = interval_min
    await set_state("cfg:hb_jobs", json.dumps(_o.hb_jobs))
    if name in _o.hb_hidden:   # added again on purpose
        _o.hb_hidden.discard(name)
        await set_state("cfg:hb_hidden", json.dumps(sorted(_o.hb_hidden)) if _o.hb_hidden else None)
    return True


async def remove_heartbeat_job(name: str) -> bool:
    """Stop waiting for a job, wherever it was defined. One from
    HEARTBEAT_JOBS stays in the server's variable but is hidden here — a
    person who never set up a server variable must not need to find it."""
    if name not in heartbeat_jobs():
        return False
    if name in _o.hb_jobs:
        del _o.hb_jobs[name]
        await set_state("cfg:hb_jobs", json.dumps(_o.hb_jobs))
    if name in config.heartbeat_jobs:   # else the server's copy would come back
        _o.hb_hidden.add(name)
        await set_state("cfg:hb_hidden", json.dumps(sorted(_o.hb_hidden)))
    # Everything the job left behind: a job added again under the same name
    # starts clean — no inherited mark, «went silent» flag or status lines.
    await delete_heartbeat(name)
    for key in ("hb_alerted", "hb_status", "hb_added", "hb_legacy", "hb_legacy_noted"):
        await set_state(f"{key}:{name}", None)
    return True
