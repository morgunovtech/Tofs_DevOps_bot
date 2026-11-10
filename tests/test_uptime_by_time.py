"""Downtime and uptime are measured in time, not in check rows: re-checks
after an alert, a changed interval and the hours the bot was away do not
inflate them; the answer time counts answered checks only; «7 days» is 7
days; «Лежал N» starts at the first failed check."""

from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors.mail_checker import MailResult
from reports import scheduler
from reports.formatter import format_mail_recovery
from services import humanize
from utils.clock import fmt_local


async def _check(db, sid: int, minutes_ago: float, status: str, ms: int | None = None):
    """A stored availability check, minutes_ago before now."""
    conn = await db.get_db()
    await conn.execute(
        """INSERT INTO checks (site_id, check_type, status, response_time_ms, checked_at)
           VALUES (?, 'availability', ?, ?, datetime('now', ?))""",
        (sid, status, ms, f"-{round(minutes_ago * 60)} seconds"))
    await conn.commit()


async def test_three_failures_and_two_rechecks_at_five_minutes_are_fifteen_minutes(db):
    sid = await db.activate_or_create_site("https://shop.example.com")    # every 5 min (the default)
    # Scheduled checks 50, 45, 40 min ago failed; the re-checks 2 and 5 min after
    # the alert (43 and 39:40 min ago) failed too; at 35 min ago it opened.
    for minutes_ago, status in ((60, "ok"), (55, "ok"), (50, "error"), (45, "error"), (43, "error"),
                                (40, "error"), (39 + 40 / 60, "error"), (35, "ok"), (30, "ok")):
        await _check(db, sid, minutes_ago, status, 200 if status == "ok" else None)
    week = await db.get_uptime_over_days(sid, 7)
    assert round(week["down_min"]) == 15                 # rows × interval said 5 × 5 = 25
    assert humanize.downtime(week) == "недоступен ≈15 мин"
    assert round((await db.get_uptime_stats(sid, hours=24))["down_min"]) == 15


async def test_a_shorter_interval_does_not_reweigh_the_past(db):
    sid = await db.activate_or_create_site("https://blog.example.com")
    for minutes_ago in range(300, 99, -5):                             # every 5 min, 10 min down
        await _check(db, sid, minutes_ago, "error" if minutes_ago in (200, 195) else "ok", 100)
    await db.update_site_settings(sid, check_interval_min=1)           # then every minute
    for minutes_ago in range(99, -1, -1):
        await _check(db, sid, minutes_ago, "ok", 100)
    stats = await db.get_uptime_over_days(sid, 7)
    assert round(stats["down_min"]) == 10
    assert 96.5 < stats["uptime_pct"] < 96.8                           # 10 of ~300 min; rows said 98.58


async def test_hours_the_bot_was_away_are_not_downtime(db):
    sid = await db.activate_or_create_site("https://cafe.example.com")
    for minutes_ago, status in ((200, "ok"), (195, "error"), (190, "error"),   # down, then the bot stopped
                                (45, "ok"), (40, "ok")):                         # back 145 min later: up
        await _check(db, sid, minutes_ago, status, 120 if status == "ok" else None)
    week = await db.get_uptime_over_days(sid, 7)
    assert round(week["down_min"]) == 11                                # 5 + 6, not 5 + 145


async def test_a_row_the_bot_could_not_check_counts_neither_way(db):
    """«unknown» (the bot's own network was down) ends the span of the check
    before it and is neither downtime nor a watched minute itself."""
    sid = await db.activate_or_create_site("https://net.example.com")
    for minutes_ago, status in ((20, "error"), (15, "unknown"), (10, "unknown"), (5, "ok"), (0, "ok")):
        await _check(db, sid, minutes_ago, status, 100 if status == "ok" else None)
    stats = await db.get_uptime_stats(sid, hours=24)
    assert stats["total_checks"] == 3 and round(stats["down_min"]) == 5
    assert round((await db.get_uptime_over_days(sid, 7))["down_min"]) == 5


async def test_answer_time_counts_answered_checks_and_seven_days_are_seven(db):
    sid = await db.activate_or_create_site("https://fast.example.com")
    for minutes_ago, status, ms in ((20, "ok", 300), (15, "error", 15000), (10, "ok", 300), (0, "ok", 300)):
        await _check(db, sid, minutes_ago, status, ms)
    assert (await db.get_uptime_stats(sid, hours=24))["avg_response_ms"] == 300   # a timeout is not a speed
    for days_ago in range(1, 8):                                          # one check on each of 7 earlier days
        await _check(db, sid, days_ago * 24 * 60, "ok", 300)
    rows = await db.get_daily_availability(sid, 7)
    assert len(rows) == 7 and rows[-1]["avg_ms"] == 300


async def test_rollup_keeps_the_measured_time_and_old_rollups_are_estimated_as_before(db):
    sid = await db.activate_or_create_site("https://old.example.com")
    base = 40 * 24 * 60                                                  # 40 days ago
    for minutes_ago, status in ((base + 60, "ok"), (base + 50, "error"), (base + 45, "error"),
                                (base + 43, "error"), (base + 40, "error"), (base + 39 + 40 / 60, "error"),
                                (base + 35, "ok")):
        await _check(db, sid, minutes_ago, status)
    before = (await db.get_uptime_over_days(sid, 90))["down_min"]
    assert await db.rollup_old_checks(30) == 7
    assert round((await db.get_uptime_over_days(sid, 90))["down_min"]) == round(before) == 15
    conn = await db.get_db()
    await conn.execute(                                                 # rolled up by an older version
        """INSERT INTO checks_daily (site_id, check_type, day, total, ok, sum_ms)
           VALUES (?, 'availability', date('now', '-10 days'), 10, 9, 1000)""", (sid,))
    await conn.commit()
    assert round((await db.get_uptime_over_days(sid, 30))["down_min"]) == 5   # one failed check × 5 min


async def test_the_status_page_windows_match_the_single_queries(db):
    sid = await db.activate_or_create_site("https://win.example.com")
    for days_ago, status in ((0, "ok"), (1, "error"), (10, "ok"), (20, "error"), (50, "ok"), (80, "error")):
        await _check(db, sid, days_ago * 24 * 60 + 30, status, 100 if status == "ok" else None)
    windows = await db.get_uptime_windows(sid, (7, 30, 90))
    for days in (7, 30, 90):
        assert windows[days] == await db.get_uptime_over_days(sid, days)
    assert windows[7]["total_checks"] == 2 and windows[30]["total_checks"] == 4 and windows[90]["total_checks"] == 6


async def _up(_):
    return web.Response(text="<html>ok</html>", content_type="text/html")


async def _down(_):
    return web.Response(status=503)


async def _tick(sid: int):
    scheduler.reset_site_schedule(sid)
    await scheduler.run_availability_checks()
    await scheduler.drain_alert_worker()


async def _tick_until_closed(db, sid: int):
    """Scheduled checks until the incident closes — it may want two good checks in a row."""
    for _ in range(3):
        await _tick(sid)
        if not await db.get_active_incidents():
            return


async def test_the_alert_says_since_when(bot, db):
    app = web.Application()
    app.router.add_get("/", _down)
    async with TestServer(app) as server:
        sid = await db.activate_or_create_site(f"http://127.0.0.1:{server.port}")
        await _tick(sid)                                                 # the first failure
        await _tick(sid)                                                 # the second one: the alert
    [inc] = await db.get_active_incidents()
    assert f"не открывается с {fmt_local(inc['started_at'], '%H:%M')}:" in bot.sent[0]["text"]


async def test_down_since_the_first_failed_check(bot, db):
    app = web.Application()
    app.router.add_get("/", _up)
    async with TestServer(app) as server:
        sid = await db.activate_or_create_site(f"http://127.0.0.1:{server.port}")
        await _check(db, sid, 10, "error")                               # the first failure
        await _check(db, sid, 5, "error")                                # the second: the incident opens
        inc_id, _ = await db.save_incident(sid, "availability", "Site down: HTTP 502", "critical")
        await _tick_until_closed(db, sid)                                # up again
    first_failure = (await db.get_incident(inc_id))["started_at"]       # 10 min ago, not the opening
    assert f"Лежал 10 мин ({fmt_local(first_failure, '%H:%M')}–" in bot.sent[-1]["text"]
    assert "не знаю" not in bot.sent[-1]["text"]


async def test_up_again_while_the_bot_was_away_says_what_it_does_not_know(bot, db):
    app = web.Application()
    app.router.add_get("/", _up)
    async with TestServer(app) as server:
        sid = await db.activate_or_create_site(f"http://127.0.0.1:{server.port}")
        await _check(db, sid, 160, "error")
        await _check(db, sid, 155, "error")
        inc_id, _ = await db.save_incident(sid, "availability", "Site down: HTTP 502", "critical")
        await _tick_until_closed(db, sid)                                # the bot is back 155 min later
    inc = await db.get_incident(inc_id)
    span = f"{fmt_local(inc['started_at'], '%H:%M')}–{fmt_local(inc['resolved_at'], '%H:%M')}"
    text = bot.sent[-1]["text"]
    assert f"Лежал не меньше 5 мин ({span}); когда именно поднялся — не знаю" in text
    # Ends where it was last seen down: the reports and the status page get the lower bound too.
    last_failure = (datetime.now(UTC) - timedelta(minutes=155)).replace(microsecond=0)
    ended = datetime.strptime(inc["resolved_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    assert abs((ended - last_failure).total_seconds()) < 5


async def test_mail_down_since_its_first_failed_check(db):
    sid = await db.activate_or_create_site("https://mail.example.com")
    started = (datetime.now(UTC) - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
    await db.save_incident(sid, "mail", "почта не принимает письма", "critical", started_at=started)
    closed = await db.resolve_incident(sid, "mail")
    text = format_mail_recovery(MailResult(url="https://mail.example.com", site_id=sid, domain="example.com",
                                           kind="mail", recovered=True, resolved_incident=closed))
    assert "(не принимала около 20 мин)" in text
