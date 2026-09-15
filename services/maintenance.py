"""Silence without blindness: recurring maintenance windows and per-site
pauses. Checks, incidents and statistics keep running; only outgoing alerts
and escalation stay quiet. Every alert-silencing code path asks one
question: is_paused(site_id)."""

import json
from datetime import UTC, datetime, timedelta

from db.database import get_state, set_state
from utils.clock import now_local
from utils.text import parse_iso_utc

MAX_MAINT_WINDOWS = 10

# [{"id": 1, "site_id": None|int, "days": [0..6] ([] = daily),
#   "start_min": 120, "end_min": 240}, …]  (minutes since local midnight)
_windows: list[dict] = []


async def load():
    global _windows
    try:
        raw = json.loads(await get_state("cfg:maint_windows") or "[]")
        _windows = raw if isinstance(raw, list) else []
    except ValueError:
        _windows = []


async def _save():
    await set_state("cfg:maint_windows", json.dumps(_windows))


# ── Recurring windows ────────────────────────────────────────────────────────

def windows() -> list[dict]:
    return list(_windows)


async def add_window(site_id: int | None, days: list[int],
                     start_min: int, end_min: int) -> int:
    """Returns the window's id (the existing one when an identical window is
    already there — a double-tapped preset must not create duplicates)."""
    days = sorted(set(days))
    for w in _windows:
        if (w.get("site_id") == site_id and (w.get("days") or []) == days
                and w.get("start_min") == start_min and w.get("end_min") == end_min):
            return w["id"]
    new_id = max((w.get("id", 0) for w in _windows), default=0) + 1
    _windows.append({"id": new_id, "site_id": site_id, "days": days,
                     "start_min": start_min, "end_min": end_min})
    await _save()
    return new_id


async def remove_window(win_id: int) -> bool:
    global _windows
    kept = [w for w in _windows if w.get("id") != win_id]
    if len(kept) == len(_windows):
        return False
    _windows = kept
    await _save()
    return True


async def remove_windows_for_site(site_id: int) -> int:
    """Drop every window scoped to a removed site (all-sites windows stay)."""
    global _windows
    kept = [w for w in _windows if w.get("site_id") != site_id]
    removed = len(_windows) - len(kept)
    if removed:
        _windows = kept
        await _save()
    return removed


def window_active(w: dict, now: datetime) -> bool:
    minutes = now.hour * 60 + now.minute
    days = w.get("days") or []  # [] = every day
    start, end = w.get("start_min"), w.get("end_min")
    if not isinstance(start, int) or not isinstance(end, int) or start == end:
        return False
    if start < end:
        return (not days or now.weekday() in days) and start <= minutes < end
    # Overnight window (23:00–06:00): [start, midnight) belongs to the
    # window's start day, [midnight, end) to the following day.
    if minutes >= start:
        return not days or now.weekday() in days
    if minutes < end:
        return not days or (now.weekday() - 1) % 7 in days
    return False


def window_active_now(w: dict) -> bool:
    return window_active(w, now_local())


def maintenance_now(site_id: int | None = None) -> bool:
    """site_id=None asks "for any site at all" (menu header indicator)."""
    now = now_local()
    for w in _windows:
        scope = w.get("site_id")
        if site_id is not None and scope is not None and scope != site_id:
            continue
        if window_active(w, now):
            return True
    return False


def fmt_window(w: dict) -> str:
    """'будни · 02:00–04:00'."""
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
    return f"{days_txt} · {s // 60:02d}:{s % 60:02d}–{e // 60:02d}:{e % 60:02d}"


# ── Per-site pause ───────────────────────────────────────────────────────────

async def pause_site(site_id: int, minutes: int | None):
    """minutes=None lifts the pause."""
    if minutes is None:
        await set_state(f"paused:{site_id}", None)
    else:
        deadline = datetime.now(UTC) + timedelta(minutes=minutes)
        await set_state(f"paused:{site_id}", deadline.isoformat())


async def paused_until(site_id: int) -> datetime | None:
    deadline = parse_iso_utc(await get_state(f"paused:{site_id}"))
    if not deadline:
        return None
    if deadline <= datetime.now(UTC):
        await set_state(f"paused:{site_id}", None)  # expired — clean up
        return None
    return deadline


async def is_paused(site_id: int) -> bool:
    """Manual pause OR an active recurring maintenance window."""
    if maintenance_now(site_id):
        return True
    return await paused_until(site_id) is not None
