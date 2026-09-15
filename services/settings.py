"""UI-editable overrides on top of .env defaults, cached in memory and
written through to bot_state. Sync getters so schedule triggers and
quiet-hours checks never touch the DB."""

import json
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import config
from db.database import get_state, set_state
from utils import clock
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
    status_page: bool | None = None      # None → .env
    timezone: str | None = None          # IANA key; None → .env
    ring_only_down: bool = False         # ring only for «site down»; the rest waits quietly


_o = Overrides()


def _int_or_none(raw: str | None) -> int | None:
    return int(raw) if raw and raw.isdigit() else None


async def load():
    """Populate the cache from bot_state; call once at startup."""
    global _o
    o = Overrides()
    o.morning_hour = _int_or_none(await get_state("cfg:morning_hour"))
    v = await get_state("cfg:evening_hour")
    o.evening_off = v == "off"
    o.evening_hour = _int_or_none(v)
    o.weekly_hour = _int_or_none(await get_state("cfg:weekly_hour"))
    v = await get_state("cfg:quiet_hours")
    o.quiet_off = v == "off"
    o.quiet = parse_hour_range(v) if v and v != "off" else None
    try:
        jobs = json.loads(await get_state("cfg:hb_jobs") or "{}")
        o.hb_jobs = {str(k): int(v) for k, v in jobs.items()} if isinstance(jobs, dict) else {}
    except (ValueError, TypeError):
        o.hb_jobs = {}
    v = await get_state("cfg:status_page")
    o.status_page = {"on": True, "off": False}.get(v or "")
    o.timezone = valid_timezone(await get_state("cfg:timezone"))
    clock.set_timezone(o.timezone)
    o.ring_only_down = (await get_state("cfg:ring_only_down")) == "1"
    _o = o


def valid_timezone(key: str | None) -> str | None:
    if not key:
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
    """env-defined jobs merged with UI-added ones (UI wins on name clash)."""
    return {**config.heartbeat_jobs, **_o.hb_jobs}


def ui_heartbeat_jobs() -> dict[str, int]:
    """Only the jobs added via the bot UI (deletable there, unlike env ones)."""
    return dict(_o.hb_jobs)


def status_page_enabled() -> bool:
    return _o.status_page if _o.status_page is not None else config.status_page


# ── Setters (write-through) ──────────────────────────────────────────────────

async def set_morning_hour(hour: int):
    _o.morning_hour = hour
    await set_state("cfg:morning_hour", str(hour))


async def set_evening_hour(hour: int | None):
    _o.evening_off = hour is None
    _o.evening_hour = hour
    await set_state("cfg:evening_hour", "off" if hour is None else str(hour))


async def set_weekly_hour(hour: int):
    _o.weekly_hour = hour
    await set_state("cfg:weekly_hour", str(hour))


async def set_quiet_hours(raw: str | None):
    """raw: '23-8' style or None to disable."""
    _o.quiet_off = raw is None
    _o.quiet = parse_hour_range(raw) if raw else None
    await set_state("cfg:quiet_hours", raw if raw else "off")


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


async def add_heartbeat_job(name: str, interval_min: int):
    _o.hb_jobs[name] = interval_min
    await set_state("cfg:hb_jobs", json.dumps(_o.hb_jobs))


async def remove_heartbeat_job(name: str) -> bool:
    if name not in _o.hb_jobs:
        return False
    del _o.hb_jobs[name]
    await set_state("cfg:hb_jobs", json.dumps(_o.hb_jobs))
    # Clear the "went silent" flag so a re-added job starts clean.
    await set_state(f"hb_alerted:{name}", None)
    return True
