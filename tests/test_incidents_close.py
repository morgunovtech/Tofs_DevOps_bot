"""«✓ Закрыть» and «🗑 Закрыть все» do what the screen says: a site that is
still down stays open (no new ringing alert, no new rebuild), «Закрыть все»
asks first, a closed «медленно» or certificate warning comes back if it is
still true, every problem has a button, and a site removed during its check
leaves no incident behind."""

import asyncio
import time

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, buttons

from db.database import save_check, save_incident
from handlers import incidents
from monitors.availability import check_availability
from monitors.base import AvailabilityResult, SslResult
from monitors.ladder import apply_ladder
from monitors.ssl_checker import SSL_THRESHOLDS
from reports import scheduler
from services import automation, integrations
from services.actions import RedeployResult


async def _down(_):
    return web.Response(status=503)


async def test_a_site_that_is_still_down_is_not_closed_and_does_not_ring_again(bot, db, cfg, monkeypatch):
    redeploys = []

    async def redeploy(url):
        redeploys.append(url)
        return RedeployResult(True, "пересборка запущена")
    cfg(auto_redeploy=True)
    await integrations.set_deploy_hook("127.0.0.1", "https://hook.example.com/x")
    monkeypatch.setattr(automation, "trigger_redeploy", redeploy)
    app = web.Application()
    app.router.add_get("/", _down)
    async with TestServer(app) as server:
        url = f"http://127.0.0.1:{server.port}"
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)    # the next tick reaches the incident at once
        inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 503", "critical")

        call = FakeCall(f"inc_close:{inc_id}")
        await incidents.cb_incident_close(call)
        assert not (await db.get_incident(inc_id))["resolved"]
        assert call.message.texts[-1].startswith("🔴 Не закрываю: 127.0.0.1 всё ещё не открывается")
        assert "🔧 Я чиню, час тишины" in buttons(call.message.reply_markup)

        scheduler.reset_site_schedule(sid)
        await scheduler.run_availability_checks()                   # the next tick: still down
        await scheduler.drain_alert_worker()
    assert [i["id"] for i in await db.get_active_incidents()] == [inc_id]
    assert bot.sent == [] and redeploys == []                        # no second «сайт лёг», no rebuild


async def test_a_site_that_is_up_again_is_closed(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://back.example.com")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")

    async def up(url, manage=True):
        return AvailabilityResult(url=url, site_id=sid, response_time_ms=80)
    monkeypatch.setattr(incidents, "check_availability", up)
    call = FakeCall(f"inc_close:{inc_id}")
    await incidents.cb_incident_close(call)
    assert (await db.get_incident(inc_id))["resolved"]
    assert call.message.texts[-1].startswith("✅ Закрыл: back.example.com открывается.")


async def test_close_all_asks_first_and_keeps_what_is_still_down(bot, db, monkeypatch):
    up = await db.activate_or_create_site("https://up.example.com")
    down = await db.activate_or_create_site("https://down.example.com")
    await save_incident(up, "availability", "Site down: HTTP 502", "critical")
    still, _ = await save_incident(down, "availability", "Site down: HTTP 502", "critical")
    await save_incident(down, "seo", "1 помеха в поиске", "warning")

    async def check_all(urls, manage=True):
        return [AvailabilityResult(url=u, site_id=0, status="ok" if "//up." in u else "error") for u in urls]
    monkeypatch.setattr(incidents, "check_all", check_all)

    call = FakeCall("incidents_clear")
    await incidents.cb_incidents_clear(call)
    assert call.message.texts[-1].startswith("Закрыть все открытые проблемы (3)?")
    yes = call.message.reply_markup.inline_keyboard[0][0].callback_data
    assert yes.startswith("incidents_clear:ok:") and len(await db.get_active_incidents()) == 3

    stale = FakeCall(f"incidents_clear:ok:{int(time.time()) - 3600}")   # a tap from before a restart
    await incidents.cb_incidents_clear_confirmed(stale)
    assert "устарело" in stale.answers[-1] and len(await db.get_active_incidents()) == 3

    call = FakeCall(yes)
    await incidents.cb_incidents_clear_confirmed(call)
    assert [i["id"] for i in await db.get_active_incidents()] == [still]
    assert call.message.texts[-1].startswith("🗑 Закрыл 2. Не закрыл — всё ещё не открываются: down.example.com.")


async def test_a_slow_site_closed_by_hand_is_reported_again(bot, db):
    url = "https://slow.example.com"
    sid = await db.activate_or_create_site(url)
    scheduler.forget_slow_streak(sid)                                  # module state from other tests
    slow = AvailabilityResult(url=url, site_id=sid, response_time_ms=5000)
    for _ in range(scheduler.SLOW_RESPONSE_STREAK):
        await scheduler._track_slow(slow, 3000)
    [inc] = await db.get_active_incidents()
    await incidents.cb_incident_close(FakeCall(f"inc_close:{inc['id']}"))
    assert await db.get_active_incidents() == []
    for _ in range(scheduler.SLOW_RESPONSE_STREAK):
        await scheduler._track_slow(slow, 3000)
    assert [i["check_type"] for i in await db.get_active_incidents()] == ["performance"]


async def test_a_certificate_warning_closed_by_hand_is_said_again_by_the_next_check(bot, db):
    url = "https://cert.example.com"
    sid = await db.activate_or_create_site(url)
    first = SslResult(url=url, site_id=sid, status="warning", error="SSL expires in 10 days")
    await apply_ladder(first, "ssl", 10, SSL_THRESHOLDS)
    assert first.incident_new
    await incidents.cb_incident_close(FakeCall(f"inc_close:{first.incident_id}"))
    again = SslResult(url=url, site_id=sid, status="warning", error="SSL expires in 9 days")
    await apply_ladder(again, "ssl", 9, SSL_THRESHOLDS)
    assert again.incident_new                                          # it used to re-open silently


async def test_every_problem_on_the_list_has_a_button_and_opens_in_place(bot, db):
    sid = await db.activate_or_create_site("https://many.example.com")
    kinds = ["availability", "ssl", "domain", "deep", "links", "seo", "visitors", "performance"]
    for kind in kinds:
        await save_incident(sid, kind, f"{kind} problem", "warning")
    call = FakeCall("menu_incidents")
    await incidents.cb_incidents(call)
    assert buttons(call.message.reply_markup)[:len(kinds)] == [f"ℹ️ {n}" for n in range(1, len(kinds) + 1)]
    data = [b.callback_data for row in call.message.reply_markup.inline_keyboard for b in row]
    assert all(d.startswith("inc_explain:") and d.endswith(":l") for d in data[:len(kinds)])

    # A list that also carries an «act:» button (an older layout) is still a list, not an alert.
    tap = FakeCall(data[1])
    tap.message.reply_markup = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔧 Я чиню", callback_data=f"act:fix:{sid}"),
        InlineKeyboardButton(text="ℹ️ 2", callback_data=data[1])]])

    async def no_new_message(*args, **kwargs):
        raise AssertionError("«ℹ️» on the list must open in place, not as a new message")
    tap.message.answer = no_new_message
    await incidents.cb_incident_explain(tap)
    assert "← Проблемы" in buttons(tap.message.reply_markup)


async def test_removing_a_site_during_its_check_leaves_no_incident(bot, db):
    async def slow_503(_):
        await asyncio.sleep(0.5)
        return web.Response(status=503)
    app = web.Application()
    app.router.add_get("/", slow_503)
    async with TestServer(app) as server:
        url = f"http://127.0.0.1:{server.port}"
        sid = await db.activate_or_create_site(url)
        await save_check(sid, "availability", "error", details="HTTP 503")   # the first failure of the series
        running = asyncio.create_task(check_availability(url))
        await asyncio.sleep(0.2)
        await db.deactivate_site(sid)                                      # «🗑 Да, убрать» while it runs
        r = await running
    assert not r.incident_new and r.incident_id is None
    assert await db.get_active_incidents() == []
    assert await save_incident(sid, "availability", "Site down: HTTP 503", "critical") == (None, False)
    await scheduler.run_escalation_watch()
    assert bot.sent == []


async def test_an_incident_of_a_removed_site_is_neither_listed_nor_escalated(bot, db, monkeypatch):
    """A row an older version left open after the site was removed."""
    sid = await db.activate_or_create_site("https://gone.example.com")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE sites SET active = 0 WHERE id = ?", (sid,))
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-3 hours') WHERE id = ?", (inc_id,))
    await conn.commit()
    assert await db.get_active_incidents() == []
    await scheduler.run_escalation_watch()
    assert bot.sent == []
    call = FakeCall("menu_incidents")
    await incidents.cb_incidents(call)
    assert call.message.texts[-1].startswith("✅ Сейчас всё работает")
