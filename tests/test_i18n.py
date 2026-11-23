"""BOT_LANG=en: every screen, alert and report renders without a Cyrillic
letter. Russian is the source and the lookup key (utils/i18n.py), so a key
that drifted from its English entry falls back to Russian and shows up here.
Russian itself is covered by every other test: with BOT_LANG=ru t() returns
its argument."""

import dataclasses
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from conftest import FakeMessage, FakeState, buttons
from test_docs import _config_with

import main
from handlers import about, blocks, common, menu, start
from services import maintenance, notifier, sitestatus
from utils import i18n
from utils.i18n import fmt_duration, plural, t

CYRILLIC = re.compile("[А-Яа-яЁё]")


@pytest.fixture(autouse=True)
def english():
    i18n.set_language("en")
    yield
    i18n.set_language("ru")


def english_only(text: str | None) -> None:
    assert text is not None
    hit = CYRILLIC.search(text)
    assert hit is None, f"Russian leaked at …{text[max(0, hit.start() - 40):hit.start() + 60]}…"


def screen_english(text: str, kb=None) -> None:
    english_only(text)
    for label in buttons(kb) if kb is not None else []:
        english_only(label)


def test_layer():
    assert t("🏠 Главная") == "🏠 Home"
    assert t("нет такого ключа — остаётся русским") == "нет такого ключа — остаётся русским"
    assert plural(1, "проблема", "проблемы", "проблем") == "problem"
    assert plural(21, "проблема", "проблемы", "проблем") == "problems"
    assert plural(2, "нет такого ключа", "x", "no key: the Russian many form") == "no key: the Russian many form"
    assert fmt_duration(185) == "3 h 5 min" and fmt_duration(2 * 1440 + 180) == "2 d 3 h"
    i18n.set_language("ru")
    assert t("🏠 Главная") == "🏠 Главная"
    assert plural(22, "проблема", "проблемы", "проблем") == "проблемы"
    assert fmt_duration(185) == "3 ч 5 мин"
    with pytest.raises(ValueError):
        i18n.set_language("de")


def test_config_lang_and_the_timezone_default():
    """BOT_LANG=en moves the default TIMEZONE to UTC (BOT_PRINCIPLES §5); an
    explicit TIMEZONE wins; a wrong language falls back to Russian."""
    assert _config_with({})["lang"] == "ru" and _config_with({})["timezone"] == "Europe/Moscow"
    en = _config_with({"BOT_LANG": "EN"})
    assert en["lang"] == "en" and en["timezone"] == "UTC"
    assert _config_with({"BOT_LANG": "en", "TIMEZONE": "Europe/Berlin"})["timezone"] == "Europe/Berlin"
    assert _config_with({"BOT_LANG": "fr"})["lang"] == "ru"


def test_bottom_buttons_and_common_texts():
    labels = [b.text for row in common.bottom().keyboard for b in row]
    assert labels == ["🏠 Home", "📖 What I do", "❓ Icons"]
    for text in (common.DENIED, common.HELPER_NOTE, common.ELSEWHERE, common.NO_OWNER, common.HEARTBEAT_SECRET_NOTE,
                 common.NOT_DELETED, common.STALE_BUTTON, common.NOT_UNDERSTOOD, common.STALE_TAP,
                 start.PRIVATE_ONLY, start.TRY_AGAIN, start.HELP, start.BOTTOM_NOTE, *start._HELPER_ANSWERS.values()):
        english_only(t(text))
    assert buttons(common.back_button()) == ["🏠 Home"]
    assert buttons(common.back_button("← Что я умею", "about:main")) == ["← What I do"]
    assert buttons(common.cancel_kb()) == ["✖️ Cancel"]
    assert buttons(start.tz_button()) == ["🕐 Change the time zone"]


async def test_start_and_help(bot, db, monkeypatch):
    msg = FakeMessage("/start")
    msg.from_user, msg.chat = SimpleNamespace(id=777, is_bot=False), SimpleNamespace(id=777, type="private")
    await start.cmd_start(msg, FakeState())
    for text, kb in zip(msg.texts, msg.markups, strict=True):
        screen_english(text, kb if kb is not None and hasattr(kb, "inline_keyboard") else None)
    english_only(start.rules_of_the_game())
    monkeypatch.setattr(start, "config", dataclasses.replace(start.config, escalation_repeat_min=0))
    english_only(start._down_line())
    help_msg = FakeMessage("/help")
    help_msg.from_user, help_msg.chat = msg.from_user, msg.chat
    await start.cmd_help(help_msg)
    screen_english(help_msg.texts[-1], help_msg.markups[-1])
    stop_msg = FakeMessage("/stop")
    stop_msg.from_user, stop_msg.chat = msg.from_user, msg.chat
    await start.cmd_stop(stop_msg)
    english_only(stop_msg.texts[-1])
    bot.sent.clear()
    await main._show_bottom_buttons(bot)   # a state without the bottom-buttons mark
    await db.set_state(start.BOTTOM_SHOWN, None)
    await main._show_bottom_buttons(bot)
    english_only(bot.sent[-1]["text"])


async def test_main_screen(bot, db):
    screen_english(*await menu.build_main_menu())                       # nothing watched yet
    sid = await db.activate_or_create_site("https://ex.test")
    screen_english(*await menu.build_main_menu())                       # before the first check
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    text, kb = await menu.build_main_menu()
    assert text.startswith("✅ All is well · checked at ") and "✅ ex.test · 0.09 s" in text
    screen_english(text, kb)
    await maintenance.pause_site(sid, 30)
    await notifier.set_mute(timedelta(hours=1))
    text, kb = await menu.build_main_menu()
    assert "🔧 ex.test · being fixed, 0.09 s" in text and "Silence until " in text
    screen_english(text, kb)
    await maintenance.pause_site(sid, None)
    await notifier.set_mute(None)
    for i in range(menu.MAIN_SITES + 1):                                # the fold: «… and N more»
        await db.activate_or_create_site(f"https://site{i}.test")
    text, kb = await menu.build_main_menu()
    assert "… and 2 more" in text
    screen_english(text, kb)
    english_only(menu.headline(["🟠", "🔴"], menu.checked_at(None)))
    english_only(menu._due("сертификат", 5))
    english_only(menu._due("домен", -1))
    for icon, name, state in await menu.system_lines(None):
        english_only(f"{icon} {name} {state}")
    for part in blocks.backup_state():
        english_only(part)


async def test_about(db):
    screen_english(about.features_text(), about.features_keyboard())
    screen_english(*await about.modules_page())
