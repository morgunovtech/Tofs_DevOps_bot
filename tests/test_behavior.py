"""«Обновление поменяло поведение» (~/BOT_PRINCIPLES.md §6): a value the
code moved while the owner's chat and .env stayed the same is said once,
with the screen to change it back; what the owner changed is not news."""

import json

from conftest import buttons
from test_i18n import screen_english

from handlers import behavior
from services import settings
from utils import i18n


def _callbacks(kb) -> list[str]:
    return [b.callback_data for row in kb.inline_keyboard for b in row]


async def test_the_first_start_only_writes_down(bot, db):
    assert await behavior.report_shift() is False
    assert bot.sent == []
    assert await db.get_state(behavior.SNAPSHOT)


async def test_a_new_default_in_the_code_is_said_once(bot, db, cfg):
    """The digest moves from 09:00 to 08:00 and the «slow» threshold from 3 s
    to 5 s because the code's defaults moved: one quiet message, a button to
    each screen, and the next start is silent again."""
    await behavior.report_shift()
    cfg(morning_report_hour=8, slow_response_ms=5000)
    assert await behavior.report_shift() is True
    [msg] = bot.sent
    assert msg["silent"] is True
    assert msg["text"].startswith("ℹ️ После обновления я работаю иначе, хотя в чате и в .env ничего не менялось:")
    assert "• утренняя сводка: 08:00 (было 09:00)" in msg["text"]
    assert "• порог «медленно»: от 5 с (было от 3 с)" in msg["text"]
    assert buttons(msg["reply_markup"]) == ["🔔 Когда писать", "🛠 Тонкие настройки"]
    assert _callbacks(msg["reply_markup"]) == ["menu_reports", "menu_tune"]
    assert await behavior.report_shift() is False and len(bot.sent) == 1


async def test_a_timezone_that_now_follows_the_language_is_said(bot, db, cfg):
    """The case the rule grew from: the same .env, a timezone default that
    moved with the code."""
    await behavior.report_shift()
    cfg(timezone="UTC")
    await behavior.report_shift()
    assert "• часовой пояс: UTC (было Москва)" in bot.sent[-1]["text"]
    assert _callbacks(bot.sent[-1]["reply_markup"]) == ["menu_tz"]


async def test_what_the_owner_changed_is_not_news(bot, db, cfg, monkeypatch):
    await behavior.report_shift()
    await settings.set_morning_hour(7)                 # in the chat
    monkeypatch.setenv("SLOW_RESPONSE_MS", "5000")     # in .env, the value config read from it
    cfg(slow_response_ms=5000)
    assert await behavior.report_shift() is False
    assert bot.sent == []


async def test_a_message_that_did_not_go_keeps_the_old_snapshot(bot, db, cfg, monkeypatch):
    await behavior.report_shift()
    cfg(evening_report_hour=22)

    async def refused(*args, **kwargs):
        return False

    real = behavior.notifier.send
    monkeypatch.setattr(behavior.notifier, "send", refused)
    assert await behavior.report_shift() is False
    monkeypatch.setattr(behavior.notifier, "send", real)
    assert await behavior.report_shift() is True
    assert "• вечерняя сводка: 22:00 (было 21:00)" in bot.sent[-1]["text"]


async def test_a_setting_new_in_this_build_is_not_compared(bot, db):
    await behavior.report_shift()
    snap = await behavior.snapshot()
    del snap["quiet"]
    snap["morning"]["v"] = 9
    await db.set_state(behavior.SNAPSHOT, json.dumps(snap))
    assert await behavior.report_shift() is False


def test_every_value_can_be_said_in_words():
    for item in behavior.ITEMS:
        value = item.value()
        assert item.words(value), item.key
    assert behavior._range((23, 8)) == "23:00–08:00" and behavior._range(None) == "выкл"


def test_the_message_in_english():
    """Every line the message can hold, in English (tests/test_i18n.py)."""
    i18n.set_language("en")
    try:
        changes = [(item, item.value(), item.value()) for item in behavior.ITEMS]
        text, kb = behavior.shift_message(changes)
    finally:
        i18n.set_language("ru")
    screen_english(text, kb)
    assert text.startswith("ℹ️ Since the update I work differently")
