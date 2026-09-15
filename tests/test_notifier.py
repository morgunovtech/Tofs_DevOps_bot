from datetime import timedelta

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


def test_in_quiet_hours_windows(monkeypatch):
    monkeypatch.setattr(settings, "quiet_hours", lambda: (23, 8))
    assert notifier.in_quiet_hours(hour=23) and notifier.in_quiet_hours(hour=3)
    assert not notifier.in_quiet_hours(hour=8) and not notifier.in_quiet_hours(hour=12)
    monkeypatch.setattr(settings, "quiet_hours", lambda: None)
    assert not notifier.in_quiet_hours(hour=3)
