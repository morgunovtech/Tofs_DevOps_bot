"""Scheduler jobs run end-to-end against a local HTTP server and the test
DB — the paths tests of pure functions cannot reach."""

import json
from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents, get_state, save_incident, set_state
from monitors import host_checker
from monitors.base import SslInfo, SslResult
from reports import scheduler
from services import gsc, settings, sitestatus, updates


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
        assert bot.sent and "не открывается" in bot.sent[-1]["text"] and bot.sent[-1]["silent"] is False
        labels = [b.text for row in bot.sent[-1]["reply_markup"].inline_keyboard for b in row]
        assert "ℹ️ Что делать" in labels and "🔧 Я чиню, час тишины" in labels
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
        assert "снова открывается" in bot.sent[-1]["text"]


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
    assert "снова открывается быстро" in bot.sent[-1]["text"]
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
    assert "Лежит уже" in bot.sent[-1]["text"] and "Я чиню" in bot.sent[-1]["text"]
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
    # Never marked = not connected: no «крон упал», and both ways out are one tap.
    assert bot.sent[-1]["text"].startswith("⚠️ Задача «backup» ещё ни разу не отметилась") and await get_state("hb_alerted:backup")
    assert "крон" not in bot.sent[-1]["text"]
    buttons = [b.callback_data for row in bot.sent[-1]["reply_markup"].inline_keyboard for b in row]
    assert buttons == ["hb_how:backup", "hb_rm:backup"]
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
    assert any("Итоги недели" in t and "за месяц" in t for t in texts)
    assert any("Как быстро открывались" in t and "<pre>" in t for t in texts)
    assert any(m.get("document") is not None for m in bot.sent)   # DB copy sent


async def test_ssl_alert_rings_only_at_the_critical_level(bot, db, monkeypatch):
    """The icon in the text and the way it arrives must agree: 🔴 rings,
    🟠 is silent. A certificate that renewed by itself is not a message."""
    sid = await db.activate_or_create_site("https://ex.test")

    async def fake(urls, manage=True):
        return [result]

    monkeypatch.setattr(scheduler, "check_all_ssl", fake)
    result = SslResult(url="https://ex.test", site_id=sid, status="warning", error="SSL expires in 10 days",
                       ssl_info=SslInfo("ex.test", "LE", "", "", 10, "1"), incident_new=True, incident_id=1)
    await scheduler.run_ssl_checks()
    assert bot.sent[-1]["text"].startswith("🟠 ex.test") and bot.sent[-1]["silent"] is True
    result = SslResult(url="https://ex.test", site_id=sid, status="critical", error="SSL expires in 1 days!",
                       ssl_info=SslInfo("ex.test", "LE", "", "", 1, "1"), incident_new=True, incident_id=1)
    await scheduler.run_ssl_checks()
    assert bot.sent[-1]["text"].startswith("🔴 ex.test") and bot.sent[-1]["silent"] is False
    n = len(bot.sent)
    result = SslResult(url="https://ex.test", site_id=sid, ssl_info=SslInfo("ex.test", "LE", "", "", 80, "2"),
                       renewed=True)
    await scheduler.run_ssl_checks()
    assert len(bot.sent) == n                                        # normal operation: silence


async def test_index_checks_write_the_snapshot_and_alert_on_change(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    answer = {"verdict": "PASS", "coverage": "Submitted and indexed", "last_crawl": "2026-09-20T10:00:00Z"}

    async def inspect(url):
        return answer

    monkeypatch.setattr(gsc, "available", lambda: True)
    monkeypatch.setattr(gsc, "inspect_url", inspect)
    n = len(bot.sent)
    await scheduler.run_index_checks()
    google = (await sitestatus.get(sid)).get("google")
    assert google["verdict"] == "PASS" and google["at"] and len(bot.sent) == n    # first PASS: nothing to say
    answer = {"verdict": "FAIL", "coverage": "Crawled - currently not indexed", "last_crawl": None}
    await scheduler.run_index_checks()
    assert bot.sent[-1]["text"].startswith("🔴 Google убрал ex.test из поиска") and bot.sent[-1]["silent"] is False
    assert "«🔎 Поиск и ИИ»" in bot.sent[-1]["text"]
    assert (await sitestatus.get(sid))["google"]["verdict"] == "FAIL"
    n = len(bot.sent)
    await scheduler.run_index_checks()                               # same state → no repeat
    assert len(bot.sent) == n


async def test_local_network_note_is_said_once_per_episode(bot, db):
    sid = await db.activate_or_create_site("https://ext.test")
    scheduler._ext_ok_noted.clear()
    await scheduler._note_local_network_problem("https://ext.test", sid)
    n = len(bot.sent)
    assert "не открылся с моего сервера" in bot.sent[-1]["text"]
    await scheduler._note_local_network_problem("https://ext.test", sid)
    assert len(bot.sent) == n
    scheduler._ext_ok_noted.discard(sid)                             # what an OK check does
    await scheduler._note_local_network_problem("https://ext.test", sid)
    assert len(bot.sent) == n + 1


async def test_host_checks_disk_thresholds(bot, db, cfg, monkeypatch):
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (96, 38.4, 40.0))
    cfg(auto_cleanup=False)
    await scheduler.run_host_checks()
    assert "заполнен на 96%" in bot.sent[-1]["text"] and bot.sent[-1]["silent"] is False
    assert bot.sent[-1]["text"].startswith("🔴")                    # ≥95% rings, and looks like it
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


async def test_morning_digest_says_each_thing_once(bot, db, monkeypatch):
    """A search finding that is an open incident is not repeated in the
    «🔎 Поиск и ИИ» line; a clean week is one phrase; a task that never
    marked says what to do and brings a button for it."""
    a = await db.activate_or_create_site("https://a.test")
    b = await db.activate_or_create_site("https://b.test")
    for sid in (a, b):
        await db.save_check(sid, "availability", "ok", response_time_ms=100)
    await db.save_check(a, "seo", "warning", details="2 проблемы")
    await save_incident(a, "seo", "2 помехи в поиске, напр.: Нет карты сайта (sitemap.xml)", "warning")
    await db.save_check(b, "seo", "ok")
    await settings.add_heartbeat_job("backup", 1440)
    extras = await scheduler._morning_extras([], [], await get_active_incidents())
    text = "\n".join(extras)
    assert "📈 За неделю: все сайты без сбоев" in text
    assert "Поиск и ИИ" not in text                                 # a.test is in «Открытых проблем»
    assert "⏰ backup: ⚠️ ни разу не отметилась — подключи её или удали: кнопки ниже" in text

    await db.save_check(b, "availability", "error", details="Timeout (15s)")
    await db.save_check(b, "seo", "error", details="site unreachable, audit skipped")
    text = "\n".join(await scheduler._morning_extras([], [], await get_active_incidents()))
    assert "📈 За неделю:\n• b.test — недоступен" in text and "\n• остальные — без сбоев" in text
    assert "🔎 Поиск и ИИ: b.test ⚠️" in text and "a.test" not in text.split("Поиск и ИИ")[1]

    async def snapshot(report_type, extras):
        return "🌅 Доброе утро"

    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    await scheduler.send_morning_report()
    buttons = [btn.callback_data for row in bot.sent[-1]["reply_markup"].inline_keyboard for btn in row]
    assert buttons == ["hb_how:backup", "hb_rm:backup"]
    await db.heartbeat_ping("backup")
    text = "\n".join(await scheduler._morning_extras([], [], await get_active_incidents()))
    assert "⏰ Задачи в срок: backup (" in text and "ни разу не отметилась" not in text
    await scheduler.send_morning_report()
    assert bot.sent[-1]["reply_markup"] is None
    await settings.remove_heartbeat_job("backup")


async def test_an_update_reruns_the_seo_audit_soon(bot, db, cfg, monkeypatch):
    """A new build that fixes a check should not leave yesterday's findings
    until 07:30: the start after an update queues one SEO audit."""
    assert await updates.report_startup_changes() is False          # first boot: nothing to compare
    assert await updates.report_startup_changes() is False          # same build
    snap = json.loads(await get_state("deps:snapshot"))
    snap["commit"] = "c370117c0c25"
    await set_state("deps:snapshot", json.dumps(snap))
    cfg(app_commit="a03ef0ccbc5c")
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    if await updates.report_startup_changes():
        scheduler.rerun_seo_after_update()
    job = sched.get_job("seo_after_update")
    assert job is not None and job.func is scheduler.run_seo_checks
