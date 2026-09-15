from datetime import datetime

from services import maintenance
from services.maintenance import fmt_window, window_active


def _at(weekday: int, hour: int, minute: int = 0) -> datetime:
    # 2026-09-14 is a Monday (weekday 0)
    return datetime(2026, 9, 14 + weekday, hour, minute)


def test_day_window():
    w = {"days": [0, 1, 2, 3, 4], "start_min": 120, "end_min": 240}
    assert window_active(w, _at(0, 3))
    assert not window_active(w, _at(0, 5))
    assert not window_active(w, _at(5, 3))      # Saturday, weekdays only


def test_overnight_window_belongs_to_start_day():
    w = {"days": [4], "start_min": 23 * 60, "end_min": 6 * 60}   # Friday 23:00–06:00
    assert window_active(w, _at(4, 23, 30))     # Friday night
    assert window_active(w, _at(5, 2))          # Saturday early morning = same window
    assert not window_active(w, _at(3, 2))      # Thursday 02:00 → belongs to Wednesday
    assert not window_active(w, _at(4, 12))


def test_fmt_window():
    assert fmt_window({"days": [], "start_min": 120, "end_min": 240}) == "ежедневно · 02:00–04:00"
    assert fmt_window({"days": [5, 6], "start_min": 0, "end_min": 30}).startswith("выходные")


async def test_windows_and_pause_roundtrip(db):
    wid = await maintenance.add_window(None, [0, 1, 2, 3, 4], 120, 240)
    assert await maintenance.add_window(None, [4, 3, 2, 1, 0], 120, 240) == wid  # dedup
    assert len(maintenance.windows()) == 1
    await maintenance.load()
    assert maintenance.windows()[0]["id"] == wid
    assert await maintenance.remove_window(wid)
    assert not await maintenance.remove_window(wid)

    await maintenance.pause_site(5, 30)
    assert await maintenance.is_paused(5)
    await maintenance.pause_site(5, None)
    assert not await maintenance.is_paused(5)
