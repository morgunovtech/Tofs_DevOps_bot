"""What visitors and a new owner read: the status page speaks the language
set in STATUS_PAGE_LANG, the widget has no stray hard-coded texts, and the
first /start names the bot's clock with a way to change it. The fakes look
like a message from a private chat (in_admin_chat reads is_bot, sender_chat
and chat.type) — the access check is never weakened for a test."""

import re
from pathlib import Path
from types import SimpleNamespace

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeMessage, FakeState

from db.database import resolve_incident, save_incident
from handlers import settings as settings_screens
from handlers import start
from services import runtime, settings
from web import status_page
from web.webhook import create_app

CYRILLIC = re.compile("[А-Яа-яЁё]")


async def test_status_page_speaks_english_when_asked(bot, db, cfg, monkeypatch):
    cfg(status_page_lang="en", status_page_title="Acme status")
    monkeypatch.setattr(status_page, "_cache", None)        # no page built in another language
    sid = await db.get_or_create_site("https://example.com")
    await db.save_check(sid, "availability", "ok", response_time_ms=120, status_code=200)
    await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await resolve_incident(sid, "availability")                 # a past outage: «1 min»
    await save_incident(sid, "availability", "Site down: HTTP 502", "critical")   # and an open one
    await settings.set_status_page(True)
    try:
        async with TestClient(TestServer(create_app())) as c:
            page = await (await c.get("/status")).text()
            data = await (await c.get("/status.json")).json()
            badge = await (await c.get(f"/status/badge/{sid}.svg")).text()
    finally:
        await settings.set_status_page(False)
    assert '<html lang="en">' in page and "<title>Acme status</title>" in page
    assert '<p class="page-title">Acme status</p>' in page and page.count("<h1") == 1
    assert "Incidents in the last 14 days" in page and "ongoing" in page and "1 min" in page
    assert "last check" in page                              # the freshness mark stays
    assert "MSK" in page                                     # times carry the bot's zone
    assert CYRILLIC.search(page) is None, CYRILLIC.findall(page)
    assert {i["type"] for i in data["incidents"]} == {"Outage"}
    assert "uptime 7d" in badge


async def test_status_page_stays_russian_by_default(bot, db, monkeypatch):
    monkeypatch.setattr(status_page, "_cache", None)
    await db.get_or_create_site("https://example.com")
    await settings.set_status_page(True)
    try:
        async with TestClient(TestServer(create_app())) as c:
            page = await (await c.get("/status")).text()
    finally:
        await settings.set_status_page(False)
    assert '<html lang="ru">' in page and "<title>Статус сервисов</title>" in page
    assert '<p class="page-title">' not in page and "MSK" in page   # no STATUS_PAGE_TITLE — no heading


def test_widget_texts_live_only_in_its_dictionary():
    """Every visible string of the widget is in I18N; ru and en have the
    same keys, so a new text cannot be added in one language only."""
    js = Path("web/feedback-widget.js").read_text("utf-8")
    code = re.sub(r"/\*.*?\*/", "", js, flags=re.S)          # the header comment may stay Russian
    blocks = dict(re.findall(r"\n    (ru|en): \{(.*?)\n    \},", code, flags=re.S))
    assert set(blocks) == {"ru", "en"}
    keys = {lang: set(re.findall(r"(?:^\s+|',\s*)([A-Za-z]+): '", body, flags=re.M))
            for lang, body in blocks.items()}
    assert keys["ru"] == keys["en"] and {"button", "title", "send", "cancel"} <= keys["en"]
    rest = code.replace(blocks["ru"], "")
    assert CYRILLIC.search(rest) is None, CYRILLIC.findall(rest)
    assert "aria-label=" not in code                          # the button's name is its visible text


async def test_first_start_names_the_clock_and_offers_to_change_it(bot, db, monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", None)                 # the seat is free: this /start takes it
    monkeypatch.setattr(runtime, "_user_id", None)
    msg = FakeMessage("/start")
    msg.from_user = SimpleNamespace(id=777, is_bot=False, full_name="T")
    msg.chat, msg.sender_chat = SimpleNamespace(id=777, type="private"), None
    await start.cmd_start(msg, FakeState())
    i = next(n for n, t in enumerate(msg.texts) if t.startswith("Как я работаю"))
    assert "Europe/Moscow" in msg.texts[i] and "🕐 Часовой пояс" in msg.texts[i]
    assert [b.callback_data for row in msg.markups[i].inline_keyboard for b in row] == ["menu_tz"]


def test_timezone_screen_says_where_visitor_speed_is_measured_from(db, cfg):
    cfg(region_speed=True)                                     # the switch run_region_checks reads
    assert "из России" in settings_screens._tz_text()        # Europe/Moscow → a node in Russia
    cfg(region_speed=False)
    assert "из России" not in settings_screens._tz_text()    # no measuring — nothing to explain
