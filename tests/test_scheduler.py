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
from services import gsc, heartbeats, integrations, settings, sitestatus, updates
from utils.text import fmt_duration


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
    assert bot.sent[-1]["text"].startswith("⚠️ «backup» так и не выходит на связь") and await get_state("hb_alerted:backup")
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
            "retention", "db_backup", "morning_report", "evening_report", "weekly_report",
            "alive", "outbox_flush"} <= ids
    scheduler.reschedule_all()   # works on a not-yet-started scheduler too


async def test_morning_digest_says_each_thing_once(bot, db, monkeypatch):
    """A search finding that is an open incident is not repeated in the
    «🔎 Поиск и ИИ» line; a clean week is one phrase; a task is a line of
    its own under the sites, and one that never marked brings its buttons."""
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
    assert "backup" not in text                                     # under the sites, not in the tail
    assert (await scheduler._watch_lines(morning=True))[0] == ("⏳ backup — пока не выходит на связь (жду раз в сутки)", True)
    await set_state("hb_alerted:backup", "1")                       # the watch has said «never marked»
    assert (await scheduler._watch_lines(morning=True))[0] == (
        "⚠️ backup — так и не выходит на связь: подключи или удали, кнопки ниже", False)

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
    # «1 дн назад» alone read like trouble: the expected rhythm says it is on time.
    assert (await scheduler._watch_lines(morning=True))[0] == (
        "✅ backup — на связи, только что (выходит на связь раз в сутки)", True)
    await scheduler.send_morning_report()
    assert bot.sent[-1]["reply_markup"] is None
    await settings.remove_heartbeat_job("backup")


async def test_watchdog_on_watch_is_seen_and_a_deaf_one_is_said_once(bot, db, monkeypatch):
    """«If the bot dies, will anyone know?» — the digest answers it: ✅ while
    the watchdog takes the marks; a blip stays ✅, a quarter of an hour of
    refusals is 🟠 and half an hour one alert; the recovery is said once."""
    assert (await scheduler._watch_lines(morning=True))[-1][0].startswith("🛡 Внешнего сторожа нет")
    assert await scheduler._watch_lines(morning=False) == []        # the evening report does not nag
    await scheduler.run_self_heartbeat()                            # nothing to ping, nothing stored
    assert await get_state("watchdog") is None

    await integrations.set_value("self_heartbeat_url", "https://hc-ping.com/abc")
    answer = [None]

    async def ping(url):
        return answer[0]
    monkeypatch.setattr(heartbeats, "ping_watchdog", ping)
    assert (await heartbeats.watchdog()).icon == "⏳"
    await scheduler.run_self_heartbeat()
    assert (await scheduler._watch_lines(morning=False))[-1] == (
        "✅ Сторож healthchecks.io — на связи: если я упаду, он сообщит", True)

    async def failing_for(minutes):
        rec = json.loads(await get_state("watchdog"))
        rec["fail_since"] = (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()
        await set_state("watchdog", json.dumps(rec))

    answer[0] = "check не найден"
    n = len(bot.sent)
    await scheduler.run_self_heartbeat()
    assert (await heartbeats.watchdog()).icon == "✅"                # one refused mark is a blip
    await failing_for(20)
    line, fine = (await scheduler._watch_lines(morning=False))[-1]
    assert line == ("🟠 Сторож healthchecks.io — не на связи 20 мин (check не найден): "
                    "если я упаду, никто не узнает") and not fine
    await scheduler.run_self_heartbeat()
    assert len(bot.sent) == n                                       # not yet worth a message
    await failing_for(35)
    await scheduler.run_self_heartbeat()
    assert len(bot.sent) == n + 1
    assert bot.sent[-1]["text"].startswith("🟠 Сторож healthchecks.io не на связи уже 35 мин: check не найден")
    await scheduler.run_self_heartbeat()
    assert len(bot.sent) == n + 1                                   # said once
    answer[0] = None
    await scheduler.run_self_heartbeat()
    assert "снова на связи" in bot.sent[-1]["text"] and not await get_state("watchdog_alerted")
    await scheduler.run_self_heartbeat()
    assert len(bot.sent) == n + 2
    await integrations.set_value("self_heartbeat_url", None)


async def test_watchdog_ping_reads_the_answer_not_just_the_code(db):
    """healthchecks.io answers a check that does not exist with HTTP 200 and
    «OK (not found)» — that is not a mark taken."""
    async def handler(request):
        if request.path == "/gone":
            return web.Response(text="OK (not found)")
        if request.path == "/broken":
            return web.Response(status=503, text="nope")
        return web.Response(text="OK")
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    async with TestServer(app) as server:
        ok_url = str(server.make_url("/ok"))
        assert await heartbeats.ping_watchdog(ok_url) is None
        assert await heartbeats.ping_watchdog(str(server.make_url("/gone"))) == "check не найден"
        assert await heartbeats.ping_watchdog(str(server.make_url("/broken"))) == "отвечает ошибкой 503"
    assert await heartbeats.ping_watchdog(ok_url) == "не отвечает"         # server gone


async def test_a_long_absence_is_said_and_the_missed_morning_comes_late(bot, db, monkeypatch):
    """While the bot was gone its silence meant nothing: after a restart
    past a redeploy's minute or two it says when it was gone, and a morning
    report that fell into the gap comes late."""
    sched = scheduler.setup_scheduler()
    monkeypatch.setattr(scheduler, "_scheduler", sched)
    await scheduler.catch_up_after_restart()                         # first run ever: nothing to say
    assert sched.get_job("downtime_note") is None and await get_state("alive_at")

    await set_state("alive_at", (datetime.now(UTC) - timedelta(minutes=3)).isoformat())
    await scheduler.catch_up_after_restart()                         # a redeploy
    assert sched.get_job("downtime_note") is None

    now = scheduler.now_local()
    monkeypatch.setattr(settings, "morning_hour", lambda: now.hour)
    gone = datetime.now(UTC) - timedelta(minutes=now.minute + 90)    # before this hour's «morning»
    await set_state("alive_at", gone.isoformat())
    await scheduler.catch_up_after_restart()
    job = sched.get_job("downtime_note")
    assert job is not None
    assert (sched.get_job("late_morning_report") is not None) == (now.hour < scheduler.LATE_MORNING_UNTIL)

    async with await _site_server(200) as server:
        await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await scheduler._report_downtime(*job.args)
    text = bot.sent[-1]["text"]
    assert text.startswith(f"⏸ Меня не было {fmt_duration(now.minute + 90)} (")
    assert "не следил за сайтами" in text and "✅ Сейчас всё работает: 1 из 1." in text and bot.sent[-1]["silent"]

    async def snapshot(report_type, extras):
        return report_type
    monkeypatch.setattr(scheduler, "_status_snapshot", snapshot)
    await scheduler.send_morning_report(late=True)
    assert bot.sent[-1]["text"] == "morning_late"


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
