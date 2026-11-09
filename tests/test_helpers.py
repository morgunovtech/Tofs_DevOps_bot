"""👥 Helpers: a second person who gets only what rings."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from aiogram.filters import CommandObject
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from conftest import FakeCall, FakeMessage, FakeState, buttons

from handlers import common, start
from handlers import settings as settings_screen
from services import helpers, notifier
from services.notifier import Priority


def _visitor(user_id: int, name: str = "Аня") -> FakeMessage:
    msg = FakeMessage("/start")
    msg.from_user = SimpleNamespace(id=user_id, full_name=name)
    msg.chat = SimpleNamespace(id=user_id, type="private")
    return msg


async def _join(user_id: int, name: str = "Аня") -> FakeMessage:
    token = await helpers.create_invite()
    msg = _visitor(user_id, name)
    await start.cmd_start_helper(msg, CommandObject(prefix="/", command="start", args=f"helper_{token}"),
                                 FakeState())
    return msg


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


async def test_only_what_rings_reaches_a_helper(bot, db):
    """Text only — the buttons steer the bot, and only the owner may."""
    msg = await _join(555)
    assert msg.texts[-1].startswith("✅ Готово") and bot.sent[-1]["text"].startswith("👥 Аня теперь получает")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="x")]])
    await notifier.send("🔴 a.test не открывается", Priority.CRITICAL, reply_markup=kb)
    to_helper = [m for m in bot.sent if m["chat_id"] == "555"]
    assert len(to_helper) == 1 and to_helper[0]["reply_markup"] is None and not to_helper[0]["silent"]
    await notifier.send("🟠 сертификат через 10 дн.", Priority.NORMAL)
    await notifier.send("🔴 Пробная тревога", Priority.CRITICAL, to_helpers=False)
    assert len([m for m in bot.sent if m["chat_id"] == "555"]) == 1


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
    assert "Ты помощник" in helper.texts[-1]
    await start.cmd_stop(helper)
    assert "Больше не присылаю" in helper.texts[-1] and not await helpers.helpers()
    assert "больше не получает тревоги" in bot.sent[-1]["text"]

    await _join(556, "Боря")
    call = FakeCall("helper_rm:556")
    await settings_screen.cb_helper_remove(call)
    assert not await helpers.helpers() and bot.sent[-1]["chat_id"] == "556"
    assert "сейчас приходят только тебе" in call.message.texts[-1]
