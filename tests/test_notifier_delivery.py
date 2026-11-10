"""How a message survives Telegram's answers that are not «ok»: a group that
became a supergroup, flood control, 5xx, a broken network, an over-long
text, a full outbox and slow helpers. Exceptions are aiogram's own."""

import asyncio
import time

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import OUTBOX_MAX, get_state, outbox_peek, peek_notifications, queue_notification
from services import helpers, notifier, runtime
from services.notifier import Priority
from utils.text import SAFE_LIMIT

M = SendMessage(chat_id=1, text="x")


def bad_request(text="Bad Request: can't parse entities"):
    return TelegramBadRequest(method=M, message=text)


def network_error(cause: Exception):
    e = TelegramNetworkError(method=M, message=f"{type(cause).__name__}")
    e.__cause__ = cause
    return e


async def test_a_group_that_became_a_supergroup_is_followed(bot, cfg):
    bot.fail_with = [TelegramMigrateToChat(method=M, message="migrated", migrate_to_chat_id=-1001234567890)]
    assert await notifier.send("🔴 a.test не открывается", Priority.CRITICAL)
    assert bot.sent[-1]["chat_id"] == "-1001234567890" and "a.test" in bot.sent[-1]["text"]
    assert runtime.admin_chat_id() == "-1001234567890" and await outbox_peek() == []
    assert await get_state(runtime.MOVED_KEY) == "777:-1001234567890"

    runtime._chat_id = "777"                       # a restart with the old id still in .env
    await runtime.follow_moved_chat()
    assert runtime.admin_chat_id() == "-1001234567890"


async def test_the_env_id_owner_is_asked_to_update_it(bot, cfg):
    cfg(admin_chat_id="777")
    bot.fail_with = [TelegramMigrateToChat(method=M, message="migrated", migrate_to_chat_id=-100555)]
    await notifier.send("🟠 сертификат", Priority.NORMAL)
    assert any("TELEGRAM_ADMIN_CHAT_ID" in m["text"] and "-100555" in m["text"] for m in bot.sent)


async def test_a_failed_plain_retry_keeps_the_alert(bot, monkeypatch):
    monkeypatch.setattr(notifier, "_no_wait_until", 0.0)
    for second in (TelegramRetryAfter(method=M, message="Flood control", retry_after=120),
                   TelegramServerError(method=M, message="Internal Server Error"),
                   network_error(OSError("Network is unreachable"))):
        bot.fail_with = [bad_request(), second]
        assert await notifier.send("🔴 <b>a.test", Priority.CRITICAL) is True
    assert [r["text"] for r in await outbox_peek()] == ["🔴 <b>a.test"] * 3


async def test_text_refused_twice_is_dropped_not_retried_forever(bot, caplog):
    """Already true on the old code; guards against a new _deliver that would
    start keeping such messages in the outbox — the log line is new."""
    bot.fail_with = [bad_request(), bad_request("Bad Request: chat not found")]
    assert await notifier.send("🔴 x", Priority.CRITICAL) is False
    assert await outbox_peek() == []
    assert "Telegram will never take this message" in caplog.text


async def test_short_flood_control_is_waited_out_once_a_minute(bot, monkeypatch):
    monkeypatch.setattr(notifier, "_no_wait_until", 0.0)
    bot.fail_with = [TelegramRetryAfter(method=M, message="Flood control", retry_after=1)]
    started = time.monotonic()
    sent = await notifier.send("🔴 b.test", Priority.CRITICAL)
    assert sent is not True and bot.sent[-1]["text"] == "🔴 b.test"      # delivered now, not queued
    assert time.monotonic() - started >= 1

    bot.fail_with = [TelegramRetryAfter(method=M, message="Flood control", retry_after=1)]
    started = time.monotonic()
    assert await notifier.send("🔴 c.test", Priority.CRITICAL) is True   # straight to the outbox
    assert time.monotonic() - started < 0.5 and (await outbox_peek())[-1]["text"] == "🔴 c.test"


async def test_an_answer_that_never_came_is_kept_with_a_warning(bot):
    bot.fail_with = [network_error(TimeoutError())]
    assert await notifier.send("🔴 c.test не открывается", Priority.CRITICAL) is True
    row = (await outbox_peek())[0]
    assert row["text"].startswith("⚠️ Если это сообщение уже было — это повтор")
    await notifier.flush_outbox()
    assert bot.sent[-1]["text"].startswith("⏳ Задержалось с ") and "c.test" in bot.sent[-1]["text"]


async def test_too_long_alert_is_clipped_not_lost(bot):
    assert await notifier.send("🔴 " + "x" * 5000, Priority.CRITICAL)
    text = bot.sent[-1]["text"]
    assert len(text) <= SAFE_LIMIT + 20 and text.endswith("(обрезано)")


async def test_critical_survives_a_full_outbox_and_the_loss_is_said(bot, monkeypatch):
    monkeypatch.setattr(notifier, "FLUSH_PACE_SEC", 0)
    bot.fail = True
    await notifier.send("🔴 NS у a.test сменились", Priority.CRITICAL)
    for i in range(OUTBOX_MAX + 5):
        await notifier.send(f"🟠 m{i}", Priority.DIGEST)
    rows = await outbox_peek()
    assert len(rows) == OUTBOX_MAX and rows[0]["text"] == "🔴 NS у a.test сменились"
    bot.fail = False
    await notifier.flush_outbox()
    assert await outbox_peek() == []
    assert "6 из них не поместились" in bot.sent[-1]["text"]


async def test_owner_first_then_helpers_together(bot, monkeypatch):
    for chat in ("201", "202"):
        await helpers._save([*await helpers.helpers(), {"chat_id": chat, "name": chat}])
    seen_outbox: list[int] = []
    original = bot.send_message

    async def telegram(chat_id, text, **kwargs):
        if chat_id == "777":
            raise TelegramServerError(method=M, message="Bad Gateway")      # the owner's copy waits
        seen_outbox.append(len(await outbox_peek()))
        await asyncio.sleep(0.5)                                            # each helper is slow
        return await original(chat_id, text, **kwargs)

    monkeypatch.setattr(bot, "send_message", telegram)
    started = time.monotonic()
    assert await notifier.send("🔴 d.test", Priority.CRITICAL, to_helpers=True) is True
    assert time.monotonic() - started < 0.9                                 # in parallel, not one by one
    assert seen_outbox == [1, 1]                                            # the owner's copy was kept first
    assert sorted(m["chat_id"] for m in bot.sent) == ["201", "202"]


async def test_a_queued_message_telegram_never_takes_does_not_jam_the_queue(bot, monkeypatch):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="x")]])
    await queue_notification("⚠️ с кнопкой", kb.model_dump_json(exclude_none=True))
    await queue_notification("🟠 обычное")
    original = bot.send_message

    async def telegram(chat_id, text, reply_markup=None, **kwargs):
        if "⚠️ с кнопкой" in text:                      # below its «🕒 ЧЧ:ММ» line
            raise bad_request("Bad Request: BUTTON_DATA_INVALID")
        return await original(chat_id, text, reply_markup=reply_markup, **kwargs)

    monkeypatch.setattr(bot, "send_message", telegram)
    await notifier.flush_queue()
    assert await peek_notifications() == []
    texts = [m["text"] for m in bot.sent]
    assert any("так и не принял" in t for t in texts) and any("🟠 обычное" in t for t in texts)
