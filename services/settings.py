"""
Runtime settings: UI-editable overrides stored in bot_state on top of the
.env defaults. `load()` fills the in-memory cache at startup; setters write
through to the DB and the cache, so sync code paths (schedule triggers,
quiet-hours checks) never need to touch the DB.

Also home of per-site pause ("maintenance mode"): checks keep running, but
availability-style alerts and escalation stay silent until the deadline.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import pytz

from config import config
from db.database import get_state, set_state

logger = logging.getLogger(__name__)

# In-memory cache of overrides; None means "no override, use .env default".
_cache: dict = {
    "morning_hour": None,     # int
    "evening_hour": None,     # int | "off"
    "quiet_hours": None,      # "23-8" | "off"
    "hb_jobs": {},            # {name: interval_min} added via UI
    "status_page": None,      # "on" | "off" (None = .env default)
    # Recurring maintenance windows added via UI:
    # [{"id": 1, "site_id": None|int, "days": [0..6] ([] = daily),
    #   "start_min": 120, "end_min": 240}, …]  (minutes since local midnight)
    "maint_windows": [],
}


def _parse_quiet(raw: str) -> tuple[int, int] | None:
    try:
        a, b = raw.split("-", 1)
        start, end = int(a), int(b)
    except ValueError:
        return None
    if 0 <= start <= 23 and 0 <= end <= 23 and start != end:
        return (start, end)
    return None


async def load():
    """Populate the cache from bot_state; call once at startup."""
    v = await get_state("cfg:morning_hour")
    _cache["morning_hour"] = int(v) if v and v.isdigit() else None
    v = await get_state("cfg:evening_hour")
    _cache["evening_hour"] = ("off" if v == "off"
                              else int(v) if v and v.isdigit() else None)
    _cache["quiet_hours"] = await get_state("cfg:quiet_hours")
    v = await get_state("cfg:hb_jobs")
    try:
        _cache["hb_jobs"] = json.loads(v) if v else {}
    except ValueError:
        _cache["hb_jobs"] = {}
    v = await get_state("cfg:status_page")
    _cache["status_page"] = v if v in ("on", "off") else None
    v = await get_state("cfg:maint_windows")
    try:
        windows = json.loads(v) if v else []
        _cache["maint_windows"] = windows if isinstance(windows, list) else []
    except ValueError:
        _cache["maint_windows"] = []


# ── Effective values (override → env default) ────────────────────────────────

def morning_hour() -> int:
    return (_cache["morning_hour"] if _cache["morning_hour"] is not None
            else config.morning_report_hour)


def evening_hour() -> int | None:
    """None = evening report disabled."""
    v = _cache["evening_hour"]
    if v == "off":
        return None
    return v if v is not None else config.evening_report_hour


def quiet_hours() -> tuple[int, int] | None:
    raw = _cache["quiet_hours"]
    if raw == "off":
        return None
    if raw:
        parsed = _parse_quiet(raw)
        if parsed:
            return parsed
    return config.quiet_hours


def heartbeat_jobs() -> dict[str, int]:
    """env-defined jobs merged with UI-added ones (UI wins on name clash)."""
    return {**config.heartbeat_jobs, **_cache["hb_jobs"]}


# ── Setters (write-through) ──────────────────────────────────────────────────

async def set_morning_hour(hour: int):
    _cache["morning_hour"] = hour
    await set_state("cfg:morning_hour", str(hour))


async def set_evening_hour(hour: int | None):
    _cache["evening_hour"] = "off" if hour is None else hour
    await set_state("cfg:evening_hour", "off" if hour is None else str(hour))


async def set_quiet_hours(raw: str | None):
    """raw: '23-8' style or None to disable."""
    _cache["quiet_hours"] = raw if raw else "off"
    await set_state("cfg:quiet_hours", raw if raw else "off")


async def add_heartbeat_job(name: str, interval_min: int):
    _cache["hb_jobs"][name] = interval_min
    await set_state("cfg:hb_jobs", json.dumps(_cache["hb_jobs"]))


async def remove_heartbeat_job(name: str) -> bool:
    if name not in _cache["hb_jobs"]:
        return False
    del _cache["hb_jobs"][name]
    await set_state("cfg:hb_jobs", json.dumps(_cache["hb_jobs"]))
    # Clear the "went silent" flag so a re-added job starts clean.
    await set_state(f"hb_alerted:{name}", None)
    return True


# ── Recurring maintenance windows ────────────────────────────────────────────
# During an active window a site behaves exactly like a paused one: checks
# and incident bookkeeping continue, alerts and escalation stay silent.

MAX_MAINT_WINDOWS = 10


def _now_local() -> datetime:
    return datetime.now(pytz.timezone(config.timezone))


def maintenance_windows() -> list[dict]:
    return list(_cache["maint_windows"])


async def add_maintenance_window(site_id: int | None, days: list[int],
                                 start_min: int, end_min: int) -> int:
    """Returns the window's id (existing one when an identical window is
    already there — a double-tapped preset must not create duplicates).
    days=[] means every day."""
    windows = _cache["maint_windows"]
    days = sorted(set(days))
    for w in windows:
        if (w.get("site_id") == site_id and (w.get("days") or []) == days
                and w.get("start_min") == start_min
                and w.get("end_min") == end_min):
            return w["id"]
    new_id = max((w.get("id", 0) for w in windows), default=0) + 1
    windows.append({
        "id": new_id,
        "site_id": site_id,
        "days": days,
        "start_min": start_min,
        "end_min": end_min,
    })
    await set_state("cfg:maint_windows", json.dumps(windows))
    return new_id


async def remove_windows_for_site(site_id: int) -> int:
    """Drop every window scoped to a removed site (all-sites windows stay).
    Returns how many were removed."""
    windows = _cache["maint_windows"]
    kept = [w for w in windows if w.get("site_id") != site_id]
    removed = len(windows) - len(kept)
    if removed:
        _cache["maint_windows"] = kept
        await set_state("cfg:maint_windows", json.dumps(kept))
    return removed


async def remove_maintenance_window(win_id: int) -> bool:
    windows = _cache["maint_windows"]
    kept = [w for w in windows if w.get("id") != win_id]
    if len(kept) == len(windows):
        return False
    _cache["maint_windows"] = kept
    await set_state("cfg:maint_windows", json.dumps(kept))
    return True


def _window_active(w: dict, now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    days = w.get("days") or []  # [] = every day
    start, end = w.get("start_min"), w.get("end_min")
    if not isinstance(start, int) or not isinstance(end, int) or start == end:
        return False
    if start < end:
        return (not days or now.weekday() in days) and start <= minutes < end
    # Overnight window (e.g. 23:00–06:00): [start, midnight) belongs to the
    # window's start day, [midnight, end) to the following day.
    if minutes >= start:
        return not days or now.weekday() in days
    if minutes < end:
        return not days or (now.weekday() - 1) % 7 in days
    return False


def window_active_now(w: dict) -> bool:
    """Is this specific window active right now? (list-screen indicator)"""
    return _window_active(w, _now_local())


def maintenance_now(site_id: int | None = None) -> bool:
    """Is a maintenance window active for this site right now?
    site_id=None asks "for any site at all" (menu header indicator)."""
    now = _now_local()
    for w in _cache["maint_windows"]:
        scope = w.get("site_id")
        if site_id is not None and scope is not None and scope != site_id:
            continue
        if _window_active(w, now):
            return True
    return False


def fmt_window(w: dict) -> str:
    """'будни · 02:00–04:00' — human-readable window description."""
    day_names = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    days = w.get("days") or []
    if not days:
        days_txt = "ежедневно"
    elif days == [0, 1, 2, 3, 4]:
        days_txt = "будни"
    elif days == [5, 6]:
        days_txt = "выходные"
    else:
        days_txt = " ".join(day_names[d] for d in days if 0 <= d <= 6)
    s, e = w["start_min"], w["end_min"]
    return (f"{days_txt} · {s // 60:02d}:{s % 60:02d}–"
            f"{e // 60:02d}:{e % 60:02d}")


# ── Public status page ───────────────────────────────────────────────────────

def status_page_enabled() -> bool:
    v = _cache["status_page"]
    if v == "on":
        return True
    if v == "off":
        return False
    return config.status_page


async def set_status_page(on: bool):
    _cache["status_page"] = "on" if on else "off"
    await set_state("cfg:status_page", _cache["status_page"])


def status_page_url() -> str:
    base = config.public_base_url or f"http://YOUR_SERVER:{config.webhook_port}"
    path = "/status"
    if config.status_page_slug:
        path += f"/{config.status_page_slug}"
    return base + path


def badge_url(site_id: int) -> str:
    """Shields-style SVG uptime badge for embedding in a README."""
    base = config.public_base_url or f"http://YOUR_SERVER:{config.webhook_port}"
    path = "/status"
    if config.status_page_slug:
        path += f"/{config.status_page_slug}"
    return f"{base}{path}/badge/{site_id}.svg"


# ── Per-site pause (maintenance mode) ────────────────────────────────────────

async def pause_site(site_id: int, minutes: int | None):
    """minutes=None lifts the pause."""
    if minutes is None:
        await set_state(f"paused:{site_id}", None)
    else:
        deadline = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await set_state(f"paused:{site_id}", deadline.isoformat())


async def paused_until(site_id: int) -> datetime | None:
    raw = await get_state(f"paused:{site_id}")
    if not raw:
        return None
    try:
        deadline = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline <= datetime.now(timezone.utc):
        await set_state(f"paused:{site_id}", None)  # expired — clean up
        return None
    return deadline


async def is_paused(site_id: int) -> bool:
    """Manual pause OR an active recurring maintenance window — every
    alert-silencing code path asks this one question."""
    if maintenance_now(site_id):
        return True
    return await paused_until(site_id) is not None


def heartbeat_url(job: str) -> str:
    """Ready-to-paste ping URL; falls back to a placeholder host when
    PUBLIC_BASE_URL is not configured."""
    base = config.public_base_url or f"http://YOUR_SERVER:{config.webhook_port}"
    return f"{base}/api/heartbeat/{config.heartbeat_secret}/{job}"


def ui_heartbeat_jobs() -> dict[str, int]:
    """Only the jobs added via the bot UI (deletable there, unlike env ones)."""
    return dict(_cache["hb_jobs"])
