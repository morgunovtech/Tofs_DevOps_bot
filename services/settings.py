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

from config import config
from db.database import get_state, set_state

logger = logging.getLogger(__name__)

# In-memory cache of overrides; None means "no override, use .env default".
_cache: dict = {
    "morning_hour": None,     # int
    "evening_hour": None,     # int | "off"
    "quiet_hours": None,      # "23-8" | "off"
    "hb_jobs": {},            # {name: interval_min} added via UI
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
    return await paused_until(site_id) is not None


def heartbeat_url(job: str) -> str:
    """Ready-to-paste ping URL; falls back to a placeholder host when
    PUBLIC_BASE_URL is not configured."""
    base = config.public_base_url or f"http://YOUR_SERVER:{config.webhook_port}"
    return f"{base}/api/heartbeat/{config.heartbeat_secret}/{job}"


def ui_heartbeat_jobs() -> dict[str, int]:
    """Only the jobs added via the bot UI (deletable there, unlike env ones)."""
    return dict(_cache["hb_jobs"])
