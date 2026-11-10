"""A storm of alerts: several sites falling (or coming back, or still down at
an escalation) at about the same time are one message; the sites of one
interval are not all checked in the same tick; the jobs that call outside
services start with jitter; a screen under Telegram's flood control waits or
says so."""

import json
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import EditMessageText
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, buttons

from handlers import alerts
from handlers.common import on_handler_error, render
from monitors.base import AvailabilityResult
from reports import scheduler
from services import maintenance


def _server(state: dict) -> web.Application:
    async def page(_):
        return web.Response(status=state["status"], text="<html>ok</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    return app


async def _sites(db, port: int, n: int) -> list[int]:
    return [await db.activate_or_create_site(f"http://127.0.0.1:{port}/s{i}") for i in range(n)]


async def _ticks(ids: list[int], n: int):
    """n scheduled checks of every site, one tick after another; the alerts
    go through the worker, so each tick is drained before the next."""
    for _ in range(n):
        for sid in ids:
            scheduler.reset_site_schedule(sid)
        await scheduler.run_availability_checks()
        await scheduler.drain_alert_worker()


async def test_ten_sites_down_in_one_tick_are_one_message_and_so_is_their_return(bot, db):
    state = {"status": 503}
    async with TestServer(_server(state)) as server:
        ids = await _sites(db, server.port, 10)
        await _ticks(ids, 2)                                          # two failures in a row: the incidents
        incidents = await db.get_active_incidents()
        assert len(incidents) == 10 and all(inc["alerted_at"] for inc in incidents)   # said, not owed
        assert len(bot.sent) == 1                                     # was: ten ringing messages
        alert = bot.sent[0]
        assert alert["text"].startswith("🔴 Не открываются сразу 10 сайтов") and not alert["silent"]
        assert buttons(alert["reply_markup"]) == ["📋 Показать по сайтам", "🔧 Я чиню все — час тишины"]
        await scheduler.deliver_missed_alerts()
        assert len(bot.sent) == 1                                     # nothing is re-sent one by one

        state["status"] = 200
        for _ in range(3):                                            # closing wants two good checks in a row
            await _ticks(ids, 1)
            if not await db.get_active_incidents():
                break
    assert await db.get_active_incidents() == []
    assert len(bot.sent) == 2 and bot.sent[1]["text"].startswith("✅ Снова открываются 10 сайтов")


async def test_sites_that_fall_a_few_minutes_later_join_the_same_message(bot, db):
    async with TestServer(_server({"status": 503})) as server:
        await _ticks(await _sites(db, server.port, 2), 2)            # two sites: said one by one
        assert len(bot.sent) == 2
        more = [await db.activate_or_create_site(f"http://127.0.0.1:{server.port}/m{i}") for i in range(2)]
        await _ticks(more, 2)                                         # two more minutes later: one outage
        assert len(bot.sent) == 3 and bot.sent[2]["text"].startswith("🔴 Не открываются сразу 4 сайта")
        last = await db.activate_or_create_site(f"http://127.0.0.1:{server.port}/last")
        await _ticks([last], 2)                                       # one more: added to that message
    assert len(bot.sent) == 3                                         # no new ring
    assert bot.edited[-1]["message_id"] == 3
    assert bot.edited[-1]["text"].startswith("🔴 Не открываются сразу 5 сайтов")
    assert all(inc["alerted_at"] for inc in await db.get_active_incidents())


async def test_ten_minutes_later_a_falling_site_is_said_on_its_own(bot, db):
    async with TestServer(_server({"status": 503})) as server:
        await _ticks(await _sites(db, server.port, 3), 2)             # one outage: one message
        group = json.loads(await db.get_state("alert_group:open"))
        await db.set_state("alert_group:open", json.dumps(              # ...sent eleven minutes ago
            {**group, "at": (datetime.now(UTC) - timedelta(minutes=11)).isoformat()}))
        late = await db.activate_or_create_site(f"http://127.0.0.1:{server.port}/late")
        await _ticks([late], 2)
    assert len(bot.sent) == 2 and "не открывается" in bot.sent[1]["text"]   # not a second list of the same


async def _until_up(db, ids: list[int]):
    """Scheduled checks of these sites until their incidents close — closing
    wants two good checks in a row."""
    for _ in range(3):
        await _ticks(ids, 1)
        if not {inc["site_id"] for inc in await db.get_active_incidents()} & set(ids):
            return


async def test_sites_coming_back_a_few_minutes_apart_are_one_message_too(bot, db):
    state = {"status": 503}
    async with TestServer(_server(state)) as server:
        ids = await _sites(db, server.port, 5)
        await _ticks(ids, 2)                                          # one outage: one message
        state["status"] = 200
        await _until_up(db, ids[:2])                                  # two are back first: one by one
        await _until_up(db, ids[2:4])                                 # two more: one message for the four
        assert [m["text"].split()[0] for m in bot.sent] == ["🔴", "✅", "✅", "✅"]
        assert bot.sent[3]["text"].startswith("✅ Снова открываются 4 сайта")
        await _until_up(db, ids[4:])                                  # the last one: added to it
    assert len(bot.sent) == 4 and bot.edited[-1]["text"].startswith("✅ Снова открываются 5 сайтов")


async def test_two_sites_down_are_still_said_one_by_one(bot, db):
    async with TestServer(_server({"status": 503})) as server:
        await _ticks(await _sites(db, server.port, 2), 2)
    assert len(bot.sent) == 2 and all("не открывается" in m["text"] for m in bot.sent)


async def test_a_paused_site_is_held_and_left_out_of_the_group(bot, db):
    """A site on «🔧 Я чиню» keeps its own held alert (said after the pause);
    the others are one message without it."""
    async with TestServer(_server({"status": 503})) as server:
        ids = await _sites(db, server.port, 4)
        await maintenance.pause_site(ids[0], 60)
        await _ticks(ids, 2)
        assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🔴 Не открываются сразу 3 сайта")
        assert "/s0" not in bot.sent[0]["text"]
        held = [inc for inc in await db.get_active_incidents() if inc["site_id"] == ids[0]]
        assert len(held) == 1 and not held[0]["alerted_at"]
        assert await db.get_state(f"held:{held[0]['id']}")


async def test_the_group_buttons_list_the_sites_and_quiet_them_all(bot, db):
    async with TestServer(_server({"status": 503})) as server:
        ids = await _sites(db, server.port, 4)
        await _ticks(ids, 2)
    keyboard = bot.sent[0]["reply_markup"]
    show, fix = (row[0].callback_data for row in keyboard.inline_keyboard)

    call = FakeCall(show)
    await alerts.cb_alert_group(call)
    assert call.message.texts[-1].startswith("1. 🔴 127.0.0.1/s")
    assert buttons(call.message.reply_markup) == ["ℹ️ 1", "ℹ️ 2", "ℹ️ 3", "ℹ️ 4"]

    call = FakeCall(fix)
    await alerts.cb_alert_group(call)
    assert call.answers[-1] == "Понял, молчу час"
    assert all([await maintenance.is_paused(sid) for sid in ids])
    for inc in await db.get_active_incidents():
        assert await db.get_state(f"ack:{inc['id']}")                   # no reminders either


async def test_an_escalation_about_several_sites_is_one_message(bot, db):
    for i in range(3):
        sid = await db.activate_or_create_site(f"https://down{i}.example.com")
        inc_id, _ = await db.save_incident(sid, "availability", "Site down: HTTP 502", "critical")
        conn = await db.get_db()
        await conn.execute("UPDATE incidents SET created_at = datetime('now', '-40 minutes') WHERE id = ?",
                           (inc_id,))
        await conn.commit()
    await scheduler.run_escalation_watch()
    assert len(bot.sent) == 1 and bot.sent[0]["text"].startswith("🔴 Всё ещё не открываются 3 сайта:")
    assert buttons(bot.sent[0]["reply_markup"])[0] == "📋 Показать по сайтам"
    await scheduler.run_escalation_watch()                            # not again before the repeat interval
    assert len(bot.sent) == 1


async def test_sites_of_one_interval_are_spread_over_its_minutes(db, monkeypatch):
    async def all_up(urls, manage=True, sites=None):
        return [AvailabilityResult(url=u, site_id=(sites or {}).get(u, {}).get("id", 0), response_time_ms=50)
                for u in urls]
    monkeypatch.setattr(scheduler, "check_all", all_up)
    ids = [await db.activate_or_create_site(f"https://s{i:02d}.example.com") for i in range(20)]
    for sid in ids:
        scheduler.reset_site_schedule(sid)
    now = datetime.now(UTC)
    await scheduler.run_availability_checks()                         # right after a start: all of them, once
    await scheduler.drain_alert_worker()
    # The tick that picks a site up: the first whose 30 s tolerance reaches its due time.
    ticks = {math.ceil(((scheduler._next_avail_check[sid] - now) - timedelta(seconds=30)) / timedelta(minutes=1))
             for sid in ids}
    assert ticks == {1, 2, 3, 4, 5}                                   # every 5 min → spread over 5 ticks
    assert all(timedelta(minutes=1) < scheduler._next_avail_check[sid] - now <= timedelta(minutes=5)
               for sid in ids)
    before = dict(scheduler._next_avail_check)
    await scheduler.run_availability_checks()                         # a tick right after: nothing is due
    assert scheduler._next_avail_check == before


def test_jobs_that_call_outside_services_start_with_jitter():
    specs = scheduler._cron_specs()
    for job in ("region_checks", "card_pictures", "index_checks", "visitor_checks", "domain_checks"):
        assert specs[job]["jitter"] == scheduler.OUTSIDE_JITTER_SEC
    jobs = scheduler.setup_scheduler()
    assert jobs.get_job("region_checks").trigger.jitter == scheduler.OUTSIDE_JITTER_SEC
    assert jobs.get_job("morning_report").trigger.jitter is None      # reports keep their minute
    # The catch-up after a restart reads the planned minute, not a jittered one.
    trigger = jobs.get_job("card_pictures").trigger
    now = datetime.now(UTC)
    due = scheduler._last_due(trigger, now, timedelta(hours=24))
    assert due is not None and due.second == 0 and trigger.jitter == scheduler.OUTSIDE_JITTER_SEC


def _flood(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=EditMessageText(text="x", chat_id=1, message_id=1),
                              message=f"Too Many Requests: retry after {seconds}", retry_after=seconds)


async def test_a_screen_under_flood_control_waits_or_says_so(monkeypatch):
    waited = []

    async def no_sleep(seconds):
        waited.append(seconds)
    monkeypatch.setattr("handlers.common.asyncio.sleep", no_sleep)
    msg = FakeMessage()
    real_edit, failures = msg.edit_text, [_flood(3)]

    async def edit_once_refused(text, reply_markup=None, **kwargs):
        if failures:
            raise failures.pop()
        return await real_edit(text, reply_markup=reply_markup, **kwargs)
    msg.edit_text = edit_once_refused
    await render(SimpleNamespace(message=msg), "🏠 Главная")
    assert waited == [3] and msg.texts == ["🏠 Главная"]                # a short wait is waited out

    call = FakeCall("menu_main")
    await on_handler_error(SimpleNamespace(update=SimpleNamespace(callback_query=call, message=None),
                                           exception=_flood(30)))
    assert call.answers == ["Telegram просит подождать 30 с — нажми ещё раз чуть позже"]
    assert call.message.texts == []                                   # no «❌ Что-то пошло не так» on top


async def test_a_group_follow_up_is_one_edit_for_all_the_sites(bot, db, monkeypatch):
    sent = []

    async def edit(message_id, text, reply_markup=None):
        sent.append((message_id, text))
        return True
    monkeypatch.setattr(scheduler.notifier, "edit", edit)

    async def mixed(urls, manage=True, sites=None):
        assert manage is False                                        # a re-check never opens or closes incidents
        return [AvailabilityResult(url=u, site_id=n, status="ok" if n else "error", error="HTTP 503")
                for n, u in enumerate(urls)]
    monkeypatch.setattr(scheduler, "check_all", mixed)
    await scheduler._group_followup(["https://a.test", "https://b.test"], 7, "🔴 text", None, 2)
    assert sent == [(7, "🔴 text\n\n🔁 Через 2 мин: уже открываются: b.test; всё ещё не открываются: a.test.")]
