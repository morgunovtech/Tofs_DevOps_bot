"""Scheduler jobs run end-to-end against a local HTTP server and the test
DB — the paths tests of pure functions cannot reach."""

from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents, get_state, save_incident, set_state
from monitors import host_checker
from reports import scheduler
from services import settings


async def _site_server(status: int = 200):
    async def handler(_):
        return web.Response(status=status, text="<html>hello</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/", handler)
    return TestServer(app)


async def test_availability_job_alerts_and_recovers(bot, db):
    async with await _site_server(500) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        scheduler._next_avail_check.clear()
        await scheduler.run_availability_checks()
        assert bot.sent and "НЕДОСТУПЕН" in bot.sent[-1]["text"] and bot.sent[-1]["silent"] is False
        assert bot.sent[-1]["reply_markup"] is not None
        # Not due again within the interval → no second alert.
        n = len(bot.sent)
        await scheduler.run_availability_checks()
        assert len(bot.sent) == n
    async with await _site_server(200) as server2:
        # Same URL cannot be reused (port changes), so simulate recovery on a new site.
        url2 = str(server2.make_url("")).rstrip("/")
        sid2 = await db.activate_or_create_site(url2)
        await save_incident(sid2, "availability", "Site down: HTTP 500", "critical")
        scheduler._next_avail_check.clear()
        await scheduler.run_availability_checks()
        assert "ВОССТАНОВЛЕН" in bot.sent[-1]["text"]


async def test_slow_streak_opens_performance_incident(bot, db):
    r = type("R", (), {})()
    r.site_id, r.url, r.response_time_ms, r.ok = 1, "https://slow.test", 5000, True
    await db.get_or_create_site("https://slow.test")
    scheduler._slow_streak.clear()
    for _ in range(scheduler.SLOW_RESPONSE_STREAK):
        await scheduler._track_slow(r, slow_ms=3000)
    assert any("медленно" in m["text"] for m in bot.sent)
    assert len([i for i in await get_active_incidents() if i["check_type"] == "performance"]) == 1
    r.response_time_ms = 100
    await scheduler._track_slow(r, slow_ms=3000)
    assert "восстановилась" in bot.sent[-1]["text"]
    assert not [i for i in await get_active_incidents() if i["check_type"] == "performance"]


async def test_escalation_respects_ack(bot, db, cfg):
    cfg(escalation_repeat_min=1)
    sid = await db.get_or_create_site("https://down.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-10 minutes') WHERE id = ?",
                       (inc_id,))
    await conn.commit()
    await scheduler.run_escalation_watch()
    assert "ВСЁ ЕЩЁ НЕ РЕШЕНО" in bot.sent[-1]["text"]
    n = len(bot.sent)
    await scheduler.run_escalation_watch()                 # within repeat window
    assert len(bot.sent) == n
    await set_state(f"escalated:{inc_id}", (datetime.now(UTC) - timedelta(hours=1)).isoformat())
    await set_state(f"ack:{inc_id}", (datetime.now(UTC) + timedelta(hours=1)).isoformat())
    await scheduler.run_escalation_watch()                 # acknowledged → silent
    assert len(bot.sent) == n


async def test_heartbeat_watch_and_recovery_flag(bot, db, monkeypatch):
    await settings.add_heartbeat_job("backup", 60)
    monkeypatch.setattr(scheduler, "_started_at", datetime.now(UTC) - timedelta(hours=2))
    await scheduler.run_heartbeat_watch()
    assert "молчит" in bot.sent[-1]["text"] and await get_state("hb_alerted:backup")
    await db.heartbeat_ping("backup")
    await scheduler.run_heartbeat_watch()
    assert await get_state("hb_alerted:backup") is None
    await settings.remove_heartbeat_job("backup")


async def test_reports_do_not_crash_on_empty_and_populated_db(bot, db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))   # backups go next to the DB — keep them out of the repo
    await scheduler.send_morning_report()
    assert "Доброе утро" in bot.sent[-1]["text"]
    await scheduler.send_evening_report()                  # no incidents → nothing
    sid = await db.get_or_create_site("https://ex.test")
    await db.save_check(sid, "availability", "ok", response_time_ms=100)
    await save_incident(sid, "links", "2 broken links")
    await scheduler.send_evening_report()
    assert "Вечер" in bot.sent[-1]["text"]
    await scheduler.send_weekly_report()
    texts = [m.get("text", "") for m in bot.sent]
    assert any("Итоги недели" in t and "90д" in t for t in texts)
    assert any("Среднее время ответа по дням" in t and "<pre>" in t for t in texts)
    assert any(m.get("document") is not None for m in bot.sent)   # DB copy sent


async def test_host_checks_disk_thresholds(bot, db, cfg, monkeypatch):
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (96, 38.4, 40.0))
    cfg(auto_cleanup=False)
    await scheduler.run_host_checks()
    assert "заполнен на 96%" in bot.sent[-1]["text"] and bot.sent[-1]["silent"] is False
    n = len(bot.sent)
    await scheduler.run_host_checks()                      # already alerted
    assert len(bot.sent) == n
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (40, 16.0, 40.0))
    await scheduler.run_host_checks()
    assert "в норме" in bot.sent[-1]["text"]


def test_setup_scheduler_registers_jobs(db):
    sched = scheduler.setup_scheduler()
    ids = {j.id for j in sched.get_jobs()}
    assert {"availability_checks", "ssl_checks", "domain_checks", "seo_checks", "links_checks",
            "dns_checks", "deep_checks", "heartbeat_watch", "escalation_watch", "quiet_flush",
            "retention", "db_backup", "morning_report", "evening_report", "weekly_report"} <= ids
    scheduler.reschedule_report_jobs()   # works on a not-yet-started scheduler too
