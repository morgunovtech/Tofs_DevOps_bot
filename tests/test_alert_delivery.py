"""An incident's alert arrives at least once: a failed or cut-off send is
re-sent, the tick never waits for Telegram or the deploy hook, and the
hook reports into the alert it follows."""

import asyncio
import sqlite3
import time

from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents, resolve_incident_by_id, save_incident
from db.migrations import MIGRATIONS, m8_incident_alerted_at
from monitors.base import CheckResult, SslInfo, SslResult
from monitors.ladder import apply_ladder
from reports import scheduler
from services import integrations, maintenance, notifier


async def _age(db, incident_id: int, minutes: int = 3):
    """Pretend the incident was opened `minutes` ago."""
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', ?) WHERE id = ?",
                       (f"-{minutes} minutes", incident_id))
    await conn.commit()


async def _alerted(incident_id: int) -> bool:
    return any(i["id"] == incident_id and i["alerted_at"] for i in await get_active_incidents())


def _server(status: int = 503, hook_status: int = 200, hook_calls: list | None = None):
    async def site(_):
        return web.Response(status=status, text="down")

    async def hook(_):
        if hook_calls is not None:
            hook_calls.append(time.monotonic())
        return web.Response(status=hook_status)
    app = web.Application()
    app.router.add_get("/", site)
    app.router.add_post("/hook", hook)
    return TestServer(app)


async def test_failed_send_is_redelivered_once(bot, db, monkeypatch):
    async with _server() as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        real_send = notifier.send
        calls = []

        async def flaky_send(*a, **k):
            calls.append(a[0])
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            return await real_send(*a, **k)
        monkeypatch.setattr(notifier, "send", flaky_send)
        await scheduler.run_availability_checks()
        await scheduler.drain_alert_worker()
    [inc] = [i for i in await get_active_incidents() if i["site_id"] == sid]
    assert bot.sent == [] and not await _alerted(inc["id"])      # the first try was lost
    await scheduler.deliver_missed_alerts()
    assert bot.sent == []                                          # younger than 2 min: its own job may still send
    await _age(db, inc["id"])
    await scheduler.deliver_missed_alerts()
    assert len(bot.sent) == 1 and "не ушла вовремя" in bot.sent[0]["text"] and bot.sent[0]["silent"] is False
    assert await _alerted(inc["id"])
    await scheduler.deliver_missed_alerts()
    assert len(bot.sent) == 1                                      # once, not every minute


async def test_alert_lost_to_a_restart_comes_after_it(bot, db):
    """The process died after the check wrote the incident and before the
    alert went out: the next process sends it."""
    sid = await db.activate_or_create_site("https://down.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await _age(db, inc_id)
    await scheduler.deliver_missed_alerts()
    labels = [b.text for row in bot.sent[-1]["reply_markup"].inline_keyboard for b in row]
    assert "down.test" in bot.sent[-1]["text"] and f"🔧 Я чиню, {maintenance.fix_length()} тишины" in labels
    assert await _alerted(inc_id)


async def test_one_site_failing_does_not_cost_the_others_their_alert(bot, db, monkeypatch):
    a = await db.activate_or_create_site("https://a.test")
    b = await db.activate_or_create_site("https://b.test")
    inc_a, _ = await save_incident(a, "ssl", "SSL expires in 6 days", "warning")
    inc_b, _ = await save_incident(b, "ssl", "SSL expires in 6 days", "warning")

    async def fake(urls, manage=True):
        info = SslInfo("x", "LE", "", "", 6, "1")
        return [SslResult(url="https://a.test", site_id=a, status="warning", error="SSL expires in 6 days",
                          ssl_info=info, incident_new=True, incident_id=inc_a),
                SslResult(url="https://b.test", site_id=b, status="warning", error="SSL expires in 6 days",
                          ssl_info=info, incident_new=True, incident_id=inc_b)]
    monkeypatch.setattr(scheduler, "check_all_ssl", fake)
    real_send = notifier.send

    async def send(text, *args, **kwargs):
        if kwargs.get("site_id") == a:
            raise RuntimeError("disk I/O error")
        return await real_send(text, *args, **kwargs)
    monkeypatch.setattr(notifier, "send", send)
    await scheduler.run_ssl_checks()
    assert [m["text"].split(":")[0] for m in bot.sent] == ["🟠 b.test"]
    assert await _alerted(inc_b) and not await _alerted(inc_a)
    monkeypatch.setattr(notifier, "send", real_send)
    await _age(db, inc_a)
    await scheduler.deliver_missed_alerts()
    assert "a.test" in bot.sent[-1]["text"] and await _alerted(inc_a)


async def test_tick_does_not_wait_for_telegram_or_the_hook(bot, db, cfg, monkeypatch):
    cfg(auto_redeploy=True)
    hook_calls: list[float] = []
    sent_at: list[float] = []

    async def slow_send(chat_id, text, reply_markup=None, disable_notification=False, parse_mode=None, **kw):
        await asyncio.sleep(2)                          # Telegram answers slowly
        sent_at.append(time.monotonic())
        bot.sent.append({"text": text, "reply_markup": reply_markup, "silent": disable_notification})
        return type("M", (), {"message_id": len(bot.sent)})()
    monkeypatch.setattr(bot, "send_message", slow_send)
    async with _server(hook_status=404, hook_calls=hook_calls) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        await integrations.set_deploy_hook("127.0.0.1", str(server.make_url("/hook")))
        started = time.monotonic()
        await scheduler.run_availability_checks()
        tick = time.monotonic() - started
        sent_before_hook = len(bot.sent)
        await scheduler.drain_alert_worker()
    assert tick < 1.5, tick                            # the 2 s Telegram call is not the tick's
    assert sent_before_hook == 0 and len(hook_calls) == 1
    assert "не открывается" in bot.sent[0]["text"] and sent_at[0] < hook_calls[0]   # alert first, then the hook
    edited = bot.edited[-1]["text"]
    assert "🤖 Пробовал пересобрать сайт — не вышло." in edited and "Уже сделал сам" not in edited
    assert "удалили" in edited                          # 404: the hook is gone, say what to do


async def test_paused_site_alert_waits_for_the_end_of_the_pause(bot, db):
    sid = await db.activate_or_create_site("https://paused.test")
    await maintenance.pause_site(sid, 60)
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    await _age(db, inc_id)
    await scheduler.deliver_missed_alerts()
    assert bot.sent == [] and not await _alerted(inc_id)     # owed, not dropped: held for the end of the pause
    await maintenance.pause_site(sid, None)
    await scheduler.deliver_missed_alerts()                  # leaves a held alert to release_held
    await notifier.release_held()
    await scheduler.deliver_missed_alerts()                  # and does not repeat it afterwards
    assert len(bot.sent) == 1 and "paused.test" in bot.sent[0]["text"] and await _alerted(inc_id)


async def test_quiet_seo_finding_is_not_turned_into_an_alert(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "seo", "no sitemap", "warning")
    await _age(db, inc_id)
    await scheduler.deliver_missed_alerts()
    assert bot.sent == [] and await _alerted(inc_id)


async def test_refused_db_write_costs_neither_the_alert_nor_a_resend_storm(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ro.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    await _age(db, inc_id)

    async def refuse(_incident_id):
        raise sqlite3.OperationalError("attempt to write a readonly database")
    monkeypatch.setattr(scheduler, "mark_incident_alerted", refuse)
    monkeypatch.setattr(scheduler, "clear_incident_alerted", refuse)
    monkeypatch.setattr(notifier, "mark_incident_alerted", refuse)
    await scheduler.deliver_missed_alerts()
    await scheduler.deliver_missed_alerts()
    assert len(bot.sent) == 1 and "ro.test" in bot.sent[0]["text"]


async def test_update_does_not_resend_alerts_of_incidents_opened_before_it(bot, db):
    sid = await db.activate_or_create_site("https://old.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET alerted_at = NULL, created_at = datetime('now', '-1 hour')")
    # The database as the version before incidents.alerted_at left it: that migration still ahead.
    await conn.execute(f"PRAGMA user_version = {MIGRATIONS.index(m8_incident_alerted_at)}")
    await conn.commit()
    await db.init_db()                                    # the update runs the migration
    assert (await (await conn.execute("PRAGMA user_version")).fetchone())[0] == len(MIGRATIONS)
    await scheduler.deliver_missed_alerts()
    assert bot.sent == [] and await _alerted(inc_id)


async def test_a_ladder_step_reopened_after_close_is_not_a_lost_alert(bot, db):
    """«✅ Закрыть» on an SSL finding, the next check reopens the same step
    silently (by design) — the re-send must not call that a lost alert."""
    sid = await db.activate_or_create_site("https://ssl.test")
    r1 = CheckResult(url="https://ssl.test", site_id=sid, status="warning", error="SSL expires in 6 days")
    await apply_ladder(r1, "ssl", 6, [1, 3, 7, 14])
    assert r1.incident_new
    await resolve_incident_by_id(r1.incident_id)                 # the owner closes it
    r2 = CheckResult(url="https://ssl.test", site_id=sid, status="warning", error="SSL expires in 6 days")
    await apply_ladder(r2, "ssl", 6, [1, 3, 7, 14])
    assert not r2.incident_new and r2.incident_id != r1.incident_id
    await _age(db, r2.incident_id)
    await scheduler.deliver_missed_alerts()
    assert bot.sent == [] and await _alerted(r2.incident_id)
