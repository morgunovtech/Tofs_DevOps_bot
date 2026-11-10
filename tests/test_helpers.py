"""👥 Helpers: a second person who gets only «сайт лёг» and «снова работает»."""

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from handlers import common, start
from handlers import settings as settings_screen
from monitors.base import AvailabilityResult, CheckResult
from reports import scheduler
from services import helpers, maintenance, notifier
from services import settings as settings_service
from services.notifier import Priority


def _visitor(user_id: int, name: str = "Аня") -> FakeMessage:
    msg = FakeMessage("/start")
    msg.from_user = SimpleNamespace(id=user_id, full_name=name, is_bot=False)
    msg.chat = SimpleNamespace(id=user_id, type="private")
    return msg


async def _join(user_id: int, name: str = "Аня") -> FakeMessage:
    token = await helpers.create_invite()
    msg = _visitor(user_id, name)
    await start.cmd_start_helper(msg, CommandObject(prefix="/", command="start", args=f"helper_{token}"),
                                 FakeState())
    return msg


def _copies(bot, chat_id: str = "555") -> list[dict]:
    return [m for m in bot.sent if m["chat_id"] == chat_id]


async def test_invite_is_one_off_and_lives_a_day(bot, db):
    token = await helpers.create_invite()
    assert await helpers.accept("wrong", 1, "X") == "invalid"
    assert await helpers.accept(token, 1, "Аня") == "ok"
    assert await helpers.accept(token, 2, "Боря") == "invalid"          # used
    token = await helpers.create_invite()
    assert await helpers.accept(token, 1, "Аня") == "already"
    rec = json.loads(await db.get_state("helper_invite"))
    rec["until"] = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    await db.set_state("helper_invite", json.dumps(rec))
    assert await helpers.accept(token, 2, "Боря") == "invalid"          # expired
    for i in (2, 3):
        assert await helpers.accept(await helpers.create_invite(), i, f"h{i}") == "ok"
    assert await helpers.accept(await helpers.create_invite(), 4, "h4") == "full"
    assert (await helpers.remove(2))["name"] == "h2" and await helpers.remove(2) is None


async def test_only_down_and_up_reach_a_helper(bot, db):
    """Text only — the buttons steer the bot, and only the owner may. What
    reaches a helper is chosen by the caller (to_helpers=True), not by «it rings»."""
    msg = await _join(555)
    assert msg.texts[-1].startswith("✅ Готово") and bot.sent[-1]["text"].startswith("👥 Аня теперь получает")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="x")]])
    await notifier.send("🔴 a.test не открывается", Priority.CRITICAL, reply_markup=kb, always_ring=True,
                        to_helpers=True)
    to_helper = _copies(bot)
    assert len(to_helper) == 1 and to_helper[0]["reply_markup"] is None and not to_helper[0]["silent"]
    assert to_helper[0]["text"].startswith("🔴 a.test") and "копия для помощника" in to_helper[0]["text"]
    await notifier.send("🔴 На a.test наружу видны служебные файлы: https://a.test/.env", Priority.CRITICAL)
    await notifier.send("🟠 сертификат через 10 дн.", Priority.NORMAL)
    await notifier.send("🔴 Пробная тревога", Priority.CRITICAL, to_helpers=False)
    await notifier.send("🟠 не звонит, хоть и помечено", Priority.NORMAL, to_helpers=True)
    assert len(_copies(bot)) == 1


async def test_a_security_alert_never_reaches_a_helper(bot, db):
    """CR-214: a CRITICAL alert of any other kind (here «наружу виден .env»
    through the generic transition path) is the owner's business only."""
    await _join(555)
    n = len(bot.sent)                                        # «👥 Аня теперь получает…» to the owner
    sid = await db.get_or_create_site("https://a.test")
    r = CheckResult(url="https://a.test", site_id=sid, status="critical", incident_new=True)
    await scheduler._notify_transitions([r], lambda r: "🔴 На a.test наружу виден .env: https://a.test/.env",
                                        lambda r: "✅", priority=lambda r: Priority.CRITICAL)
    assert [m["chat_id"] for m in bot.sent[n:]] == ["777"] and not bot.sent[n]["silent"]


async def test_the_helpers_copy_names_the_site_without_its_path(bot, db, monkeypatch):
    """An API monitor's path carries a token: the owner sees it, the helper
    sees the public name — the host and «API»; a tcp monitor or an internal
    address is not named at all."""
    await _join(555)
    bot.sent.clear()
    monkeypatch.setattr(scheduler, "_after_availability_alert", lambda *a, **k: None)
    url = "https://api.test/bot0:FAKE-TOKEN/getMe"
    sid = await db.get_or_create_site(url)
    r = AvailabilityResult(url=url, site_id=sid, status="critical", error="HTTP 500", incident_new=True, api=True)
    await scheduler._send_availability_alert(r, {"id": sid, "url": url, "http_method": "GET"})
    owner, (copy,) = bot.sent[0], _copies(bot)
    assert "FAKE-TOKEN" in owner["text"] and "FAKE-TOKEN" not in copy["text"]
    assert copy["text"].startswith("🔴 api.test · API не отвечает: ") and "ошибкой 500" in copy["text"]
    r = AvailabilityResult(url=url, site_id=sid, status="ok", recovered=True, api=True,
                           resolved_incident={"message": "Site down: HTTP 500"})
    await scheduler._send_recovery(r)
    assert "FAKE-TOKEN" in bot.sent[-2]["text"] and bot.sent[-2]["chat_id"] == "777"
    assert _copies(bot)[-1]["text"].startswith("✅ api.test · API снова отвечает")
    assert scheduler.helper_label("tcp://10.0.0.5:5432") == "Один из серверов"
    assert scheduler.helper_label("http://192.168.1.10:8080/admin") == "Один из сайтов"
    assert scheduler.helper_label("https://shop.test/health", "POST") == "shop.test · API"


async def test_helper_hears_the_recovery_even_when_only_the_fall_rings(bot, db, monkeypatch):
    """«🔔 Будить: только «сайт лёг»» and quiet hours are the owner's: the
    helper still gets «снова открывается» — silently, right away."""
    await _join(555)
    monkeypatch.setattr(settings_service, "ring_only_down", lambda: True)
    monkeypatch.setattr(notifier, "in_quiet_hours", lambda hour=None: True)
    status = {"code": 500}

    async def page(_):
        return web.Response(status=status["code"], text="<html>hello</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/", page)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        scheduler._next_avail_check.clear()
        await scheduler.run_availability_checks()                # down
        await scheduler.drain_alert_worker()
        status["code"] = 200
        for _ in range(2):                                       # up after two planned successes
            scheduler._next_avail_check.clear()
            await scheduler.run_availability_checks()
            await scheduler.drain_alert_worker()
    copies = _copies(bot)
    assert [("не открывается" in m["text"], m["silent"]) for m in copies] == [(True, False), (False, True)]
    assert "снова открывается" in copies[1]["text"] and copies[1]["text"].endswith("кнопки — у него.")
    # The owner's ✅ waited for the quiet hours to end; the helper's did not.
    assert not any("снова открывается" in m["text"] for m in bot.sent if m["chat_id"] == "777")


async def test_a_held_alert_keeps_the_helpers_copy(bot, db, monkeypatch):
    """A fall inside «🔧 Я чиню» is said once the pause ends — to the helper too."""
    await _join(555)
    bot.sent.clear()
    sid = await db.get_or_create_site("https://a.test")
    inc_id, _ = await db.save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    await maintenance.pause_site(sid, 60)
    await notifier.send("🔴 a.test не открывается", Priority.CRITICAL, site_id=sid, always_ring=True,
                        to_helpers=True, helper_text="🔴 a.test (для помощника)", announces=inc_id)
    assert not bot.sent
    await maintenance.pause_site(sid, None)
    await notifier.release_held()
    (copy,) = _copies(bot)
    assert "для помощника)" in copy["text"] and "🔕 Это случилось" in copy["text"]


async def test_one_link_one_helper_when_two_open_it_at_once(bot, db):
    token = await helpers.create_invite()
    results = await asyncio.gather(helpers.accept(token, 1, "Аня"), helpers.accept(token, 2, "Боря"))
    assert sorted(results) == ["invalid", "ok"]
    assert len(await helpers.helpers()) == 1


async def test_a_strange_invite_payload_is_just_invalid(bot, db):
    await helpers.create_invite()
    assert await helpers.accept("ж", 5, "X") == "invalid"
    assert await helpers.accept("", 5, "X") == "invalid"
    await db.set_state("helper_invite", "[1, 2]")
    assert await helpers.accept("abc", 5, "X") == "invalid"


async def test_a_helper_who_types_start_or_help_is_told_who_he_is(bot, db):
    await _join(555)
    msg = _visitor(555)
    await start.cmd_start(msg, FakeState())
    assert msg.texts[-1] == common.HELPER_NOTE and "«сайт лёг» и «снова работает»" in common.HELPER_NOTE
    msg = _visitor(555)
    msg.text = "/help"
    await start.cmd_help(msg)
    assert msg.texts[-1] == common.HELPER_NOTE
    stranger = _visitor(999)
    await start.cmd_help(stranger)
    assert stranger.texts[-1] == common.DENIED


async def test_a_second_look_at_the_invite_keeps_the_link(bot, db):
    def link(call):
        return re.search(r"helper_[A-Za-z0-9_-]+", call.message.texts[-1]).group(0)
    first, second = FakeCall("helper_invite"), FakeCall("helper_invite")
    await settings_screen.cb_helper_invite(first)
    await settings_screen.cb_helper_invite(second)
    assert link(first) == link(second)
    assert "живёт сутки — до " in second.message.texts[-1]
    assert "🔄 Новая ссылка (прежняя перестанет работать)" in buttons(second.message.reply_markup)
    fresh = FakeCall("helper_invite_new")
    await settings_screen.cb_helper_invite_new(fresh)
    assert link(fresh) != link(first)
    assert await helpers.accept(link(first)[len("helper_"):], 5, "X") == "invalid"
    assert await helpers.accept(link(fresh)[len("helper_"):], 5, "X") == "ok"


async def test_owner_manages_helpers_and_a_helper_can_leave(bot, db):
    call = FakeCall("menu_helpers")
    await settings_screen.cb_helpers(call)
    assert "сейчас приходят только тебе" in call.message.texts[-1]
    assert buttons(call.message.reply_markup) == ["🔗 Пригласить помощника", "← Настройки"]
    call = FakeCall("helper_invite")
    await settings_screen.cb_helper_invite(call)
    assert "start helper_" in call.message.texts[-1] and "один раз и живёт сутки" in call.message.texts[-1]

    owner = _visitor(777, "Я")
    await start.cmd_start_helper(owner, CommandObject(prefix="/", command="start", args="helper_x"), FakeState())
    assert "перешли ссылку" in owner.texts[-1]                       # the owner does not become a helper

    await _join(555)
    helper = _visitor(555)
    helper.text = "/menu"
    await common.deny_command(helper)
    assert helper.texts[-1] == common.HELPER_NOTE
    await start.cmd_stop(helper)
    assert "Больше не присылаю" in helper.texts[-1] and not await helpers.helpers()
    assert "больше не получает" in bot.sent[-1]["text"]

    await _join(556, "Боря")
    call = FakeCall("helper_rm:556")
    await settings_screen.cb_helper_remove(call)
    assert await helpers.helpers() and call.message.texts[-1].startswith("Убрать Боря?")   # nothing yet
    assert buttons(call.message.reply_markup) == ["🗑 Да, убрать", "Отмена"]
    call = FakeCall("helper_rm_ok:556")
    await settings_screen.cb_helper_remove_ok(call)
    assert not await helpers.helpers() and bot.sent[-1]["chat_id"] == "556"
    assert call.message.texts[-1].startswith("Убрал: Боря.")
    assert "сейчас приходят только тебе" in call.message.texts[-1]


def test_help_promises_what_the_code_does(cfg):
    assert "только о том, что сайт лёг" not in start.HELP
    assert "приходят только звонящие" in start.HELP
    cfg(escalation_repeat_min=0)
    assert "напомню" not in start.rules_of_the_game()
    cfg(escalation_repeat_min=30)
    assert "через 30 мин, потом всё реже" in start.rules_of_the_game()
