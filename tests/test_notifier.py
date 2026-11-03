from datetime import timedelta

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import peek_notifications, queue_notification
from services import maintenance, notifier, settings
from services.notifier import Priority


async def test_priorities_vs_mute(bot):
    assert await notifier.send("normal", Priority.NORMAL)
    assert bot.sent[-1]["silent"] is True
    assert await notifier.send("critical", Priority.CRITICAL)
    assert bot.sent[-1]["silent"] is False

    await notifier.set_mute(timedelta(hours=1))
    assert await notifier.is_muted()
    n = len(bot.sent)
    assert await notifier.send("queued", Priority.NORMAL)          # deferred, not lost
    assert await notifier.send("digest too", Priority.DIGEST)
    assert len(bot.sent) == n
    assert [r["text"] for r in await peek_notifications()] == ["queued", "digest too"]
    assert await notifier.send("rings", Priority.CRITICAL)         # bypasses mute
    assert bot.sent[-1]["text"] == "rings"
    await notifier.set_mute(None)


async def test_quiet_hours_defer_normal_but_not_digest(bot, monkeypatch):
    monkeypatch.setattr(notifier, "in_quiet_hours", lambda hour=None: True)
    n = len(bot.sent)
    assert await notifier.send("night alert", Priority.NORMAL)
    assert len(bot.sent) == n                                      # queued
    assert await notifier.send("morning report", Priority.DIGEST)
    assert bot.sent[-1]["text"] == "morning report"


async def test_paused_site_drops_even_critical(bot):
    await maintenance.pause_site(42, 30)
    n = len(bot.sent)
    assert not await notifier.send("down!", Priority.CRITICAL, site_id=42)
    assert len(bot.sent) == n
    assert await notifier.send("other site", Priority.CRITICAL, site_id=43)


async def test_flush_queue_keeps_undelivered_rows(bot, monkeypatch):
    for i in range(30):
        await queue_notification(f"alert {i} " + "x" * 300)     # ~9.3k chars → 3 messages
    calls = {"n": 0}
    original = bot.send_message

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("telegram hiccup")
        return await original(*args, **kwargs)

    monkeypatch.setattr(bot, "send_message", flaky)
    await notifier.flush_queue()
    left = await peek_notifications()
    assert 0 < len(left) < 30                                     # first chunk delivered & deleted
    assert bot.sent[0]["text"].startswith("🌙 Накопилось")
    await notifier.flush_queue()                                  # second run delivers the rest
    assert await peek_notifications() == []


async def test_priority_follows_the_level(bot, monkeypatch):
    """🔴 rings, 🟠 is silent — and «будить только когда сайт лёг» turns a
    🔴 that is not a downed site into a silent one, as documented."""
    assert notifier.priority_for("critical") is Priority.CRITICAL
    assert notifier.priority_for("warning") is Priority.NORMAL and notifier.priority_for(None) is Priority.NORMAL
    await notifier.send("🔴 cert", notifier.priority_for("critical"))
    assert bot.sent[-1]["silent"] is False
    await notifier.send("🟠 cert", notifier.priority_for("warning"))
    assert bot.sent[-1]["silent"] is True
    monkeypatch.setattr(settings, "ring_only_down", lambda: True)
    await notifier.send("🔴 cert", notifier.priority_for("critical"))
    assert bot.sent[-1]["silent"] is True
    await notifier.send("🔴 down", Priority.CRITICAL, always_ring=True)
    assert bot.sent[-1]["silent"] is False


def test_in_quiet_hours_windows(monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours", lambda: (23, 8))
    assert notifier.in_quiet_hours(hour=23) and notifier.in_quiet_hours(hour=3)
    assert not notifier.in_quiet_hours(hour=8) and not notifier.in_quiet_hours(hour=12)
    monkeypatch.setattr(settings, "quiet_hours", lambda: None)
    assert not notifier.in_quiet_hours(hour=3)


async def test_deferred_message_keeps_its_buttons(bot):
    """«Задача есть — нажми «Подключить»» arrived at night without the button."""
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Подключить backup",
                                                                      callback_data="hb_how:backup")]])
    await notifier.set_mute(timedelta(hours=1))
    assert await notifier.send("plain one") is True
    assert await notifier.send("⚠️ Задача «backup»", reply_markup=kb) is True
    await notifier.set_mute(None)
    await notifier.flush_queue()
    assert bot.sent[-2]["text"].startswith("🌙 Накопилось") and bot.sent[-2]["reply_markup"] is None
    assert bot.sent[-1]["text"] == "⚠️ Задача «backup»"
    assert bot.sent[-1]["reply_markup"].inline_keyboard[0][0].callback_data == "hb_how:backup"
    assert await peek_notifications() == []


async def test_a_batch_telegram_rejects_does_not_jam_the_queue(bot, monkeypatch):
    await queue_notification("<b>broken")
    original = bot.send_message

    async def picky(*args, **kwargs):
        if "parse_mode" not in kwargs:
            raise TelegramBadRequest(SendMessage(chat_id=1, text="x"), "can't parse entities")
        return await original(*args, **kwargs)

    monkeypatch.setattr(bot, "send_message", picky)
    await notifier.flush_queue()
    assert await peek_notifications() == [] and "<b>broken" in bot.sent[-1]["text"]
