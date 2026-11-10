"""«🔧 Я чиню» and maintenance windows postpone, they do not throw away:
the recovery the owner waits for arrives, a problem that started in the
silence and outlived it is said afterwards, one that came and went inside
it stays silent."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, buttons

from db.database import get_active_incidents, get_state, get_states_like
from handlers import alerts
from reports import scheduler
from services import maintenance, notifier
from services.actions import alert_actions_keyboard, deep_alert_keyboard
from utils.text import parse_iso_utc


def _switchable_site():
    """A site whose answer the test flips: state["status"] = 503 / 200."""
    state = {"status": 503}

    async def handler(_):
        return web.Response(status=state["status"], text="<html>hi</html>", content_type="text/html")

    app = web.Application()
    app.router.add_get("/", handler)
    return TestServer(app), state


async def _tick():
    scheduler._next_avail_check.clear()
    await scheduler.run_availability_checks()


async def test_recovery_during_fix_arrives_and_lifts_the_silence(bot, db):
    server, state = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        await _tick()
        assert "не открывается" in bot.sent[-1]["text"]
        await alerts.cb_alert_action(FakeCall(f"act:fix:{sid}"))         # «🔧 Я чиню»
        until = await maintenance.paused_until(sid)
        assert until and timedelta(minutes=59) < until - datetime.now(UTC) <= timedelta(minutes=60)
        inc = (await get_active_incidents())[0]
        ack = parse_iso_utc(await get_state(f"ack:{inc['id']}"))
        assert ack - datetime.now(UTC) <= timedelta(minutes=60)           # was 2 h
        state["status"] = 200
        await _tick()
    assert "снова открывается" in bot.sent[-1]["text"] and "Тишину снял" in bot.sent[-1]["text"]
    assert await maintenance.paused_until(sid) is None


async def test_alert_inside_a_window_is_said_after_it(bot, db, cfg, monkeypatch):
    cfg(escalation_repeat_min=30)
    server, _ = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: True)
        await _tick()
        conn = await db.get_db()                                            # 40 minutes into the window
        await conn.execute("UPDATE incidents SET created_at = datetime('now', '-40 minutes')")
        await conn.commit()
        await scheduler.run_escalation_watch()
        await notifier.release_held()
        assert bot.sent == []                                               # quiet inside the window
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: False)
        await notifier.release_held()
    assert len(bot.sent) == 1
    assert "до сих пор не прошло" in bot.sent[0]["text"] and "не открывается" in bot.sent[0]["text"]
    assert await get_states_like("held:") == {}
    await scheduler.run_escalation_watch()                                 # no second message right away
    assert len(bot.sent) == 1


async def test_escalation_first_after_the_window_still_gives_one_message(bot, db, cfg, monkeypatch):
    """APScheduler does not order the two jobs: whichever runs first, the
    owner hears about the fall exactly once."""
    cfg(escalation_repeat_min=30)
    server, _ = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: True)
        await _tick()
        conn = await db.get_db()
        await conn.execute("UPDATE incidents SET created_at = datetime('now', '-40 minutes')")
        await conn.commit()
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: False)
        await scheduler.run_escalation_watch()                              # escalation wins the race
        await notifier.release_held()
        assert len(bot.sent) == 1 and "до сих пор не прошло" in bot.sent[0]["text"]
        await asyncio.gather(scheduler.run_escalation_watch(), notifier.release_held())
    assert len(bot.sent) == 1


async def test_a_problem_that_ended_inside_the_window_stays_silent(bot, db, monkeypatch):
    server, state = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: True)
        await _tick()
        state["status"] = 200
        await _tick()                                                       # recovery of an unannounced fall
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: False)
        await notifier.release_held()
    assert bot.sent == []
    assert await get_states_like("held:") == {}


async def test_a_pause_set_before_an_unannounced_fall_keeps_its_silence(bot, db):
    """«🔧» from the card, then a fall nobody heard of, then a recovery: no
    ✅ rings, and the pause the owner asked for stays in place."""
    server, state = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        await maintenance.pause_site(sid, maintenance.FIX_MINUTES)
        await _tick()
        assert bot.sent == [] and len(await get_states_like("held:")) == 1
        state["status"] = 200
        await _tick()
    assert bot.sent == []
    assert await maintenance.paused_until(sid) is not None


async def test_a_held_alert_is_said_once_even_when_the_database_refuses_writes(bot, db, monkeypatch, caplog):
    server, _ = _switchable_site()
    async with server:
        sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
        await db.update_site_settings(sid, fail_threshold=1)
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: True)
        await _tick()
        monkeypatch.setattr(maintenance, "maintenance_now", lambda site_id=None: False)
        real_set_state = notifier.set_state

        async def refusing(key, value):
            if key.startswith(("announced:", "held:")):
                raise sqlite3.OperationalError("database or disk is full")
            return await real_set_state(key, value)

        monkeypatch.setattr(notifier, "set_state", refusing)
        for _ in range(3):
            await notifier.release_held()                                   # must not raise, must not repeat
        assert len(bot.sent) == 1
        monkeypatch.setattr(notifier, "set_state", real_set_state)
        await notifier.release_held()                                       # the disk is back: the key goes
    assert len(bot.sent) == 1 and await get_states_like("held:") == {}


async def test_a_plain_message_for_a_paused_site_is_still_dropped(bot):
    await maintenance.pause_site(42, 30)
    assert not await notifier.send("down!", notifier.Priority.CRITICAL, site_id=42)
    assert bot.sent == []


def test_pages_alert_has_no_whole_site_buttons():
    labels = buttons(deep_alert_keyboard("https://example.com", 1, 7))
    assert labels == ["ℹ️ Что делать"]


def test_every_fix_text_names_the_same_length():
    length = maintenance.fix_length()
    assert length in buttons(alert_actions_keyboard("https://example.com", 1, 7))[0]
    assert maintenance.FIX_MINUTES == 60 and length == "час"
