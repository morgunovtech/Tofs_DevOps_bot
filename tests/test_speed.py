"""Real visitors' speed from Google's Chrome data: the numbers, the daily
verdict, the card, the 👥 screen and the key wizard — and nothing to install
on the site."""

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import get_active_incidents, get_db
from handlers import setup
from handlers import speed as speed_screen
from handlers.sites import card_lines
from reports import scheduler
from services import crux, integrations, sitestatus
from services.integrations import Probe
from web.webhook import create_app


def test_words_and_levels():
    assert crux.describe({"lcp": 1800, "inp": 120, "cls": 0.02}) == \
        "страница появляется за 1.8 с, кнопки откликаются за 0.12 с, вёрстка не прыгает"
    assert crux.describe({"lcp": 3100, "inp": 650, "cls": 0.3}) == (
        "страница появляется за 3.1 с — можно быстрее, кнопки откликаются за 0.65 с — медленно, "
        "вёрстка сильно прыгает при загрузке")
    assert crux.slow_parts({"lcp": 5200, "inp": 100, "cls": 0.3}) == \
        ["страница появляется за 5.2 с", "вёрстка сильно прыгает при загрузке"]
    assert crux.verdict_icon({"lcp": 1000, "inp": None, "cls": 0}) == "✅"
    assert crux.verdict_icon({"lcp": 3000, "inp": 100, "cls": 0}) == "👥"
    assert crux.verdict_icon({"lcp": 5000}) == "🟠"
    assert crux.has_data({"lcp": 1, "at": "x"}) and not crux.has_data({"none": True, "at": "x"})
    assert not crux.has_data(None)


def test_chrome_record_is_read():
    record = {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2210}},
                          "interaction_to_next_paint": {"percentiles": {"p75": 160}},
                          "cumulative_layout_shift": {"percentiles": {"p75": "0.04"}}}}
    assert crux.parse(record) == {"lcp": 2210, "inp": 160, "cls": 0.04, "ttfb": None}


async def _speed_line(db, sid: int) -> str | None:
    return next((line for line in await card_lines(await db.get_site(sid)) if "посетител" in line), None)


def _google(monkeypatch, answers: dict):
    """A key is set; Google answers per origin from `answers`. Returns the asked origins."""
    asked = []

    async def fake_query(origin, key=None):
        asked.append(origin)
        return answers[origin]

    monkeypatch.setattr(integrations, "crux_key", lambda: "AIza-test")
    monkeypatch.setattr(crux, "query", fake_query)
    return asked


async def test_without_a_key_nothing_is_asked_or_shown(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    asked = []

    async def fake_query(origin, key=None):
        asked.append(origin)
        return {}

    monkeypatch.setattr(crux, "query", fake_query)
    await scheduler.run_visitor_checks()
    assert asked == [] and bot.sent == []
    assert await _speed_line(db, sid) is None                     # the card does not nag


async def test_slow_for_visitors_is_said_quietly_and_its_end_too(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    answers = {"https://ex.test": {"lcp": 5200, "inp": 80, "cls": 0.01, "ttfb": 300}}
    _google(monkeypatch, answers)
    await scheduler.run_visitor_checks()
    msg = bot.sent[-1]
    assert msg["text"].startswith("🟠 ex.test: медленно у настоящих посетителей") and msg["silent"] is True
    assert "страница появляется за 5.2 с — медленно" in msg["text"]
    assert "посетители Chrome за 28 дней, данные Google" in msg["text"]
    assert {"↗️ PageSpeed Insights", "👥 Скорость у посетителей"} <= set(buttons(msg["reply_markup"]))
    [inc] = await get_active_incidents()
    assert inc["message"] == "у посетителей страница появляется за 5.2 с"
    n = len(bot.sent)
    await scheduler.run_visitor_checks()
    assert len(bot.sent) == n                                      # said once

    answers["https://ex.test"] = {"lcp": 1500, "inp": 80, "cls": 0.01, "ttfb": 300}
    await scheduler.run_visitor_checks()
    assert bot.sent[-1]["text"].startswith("✅ ex.test: у посетителей снова не медленно — страница появляется за 1.5 с")
    assert await get_active_incidents() == []
    assert await _speed_line(db, sid) == (
        "✅ У посетителей: страница появляется за 1.5 с, кнопки откликаются за 0.08 с, вёрстка не прыгает · "
        "посетители Chrome за 28 дней, данные Google")


async def test_one_question_per_origin_and_too_little_data(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    other = await db.activate_or_create_site("https://ex.test/blog")
    answers = {"https://ex.test": {"lcp": 2100, "inp": 150, "cls": 0.02, "ttfb": 500}}
    asked = _google(monkeypatch, answers)
    await scheduler.run_visitor_checks()
    assert asked == ["https://ex.test"]                            # one question per origin
    assert (await sitestatus.get(other))["crux"]["lcp"] == 2100

    answers["https://ex.test"] = {}                                # Google: not enough Chrome visitors
    await scheduler.run_visitor_checks()
    assert (await sitestatus.get(sid))["crux"]["none"] is True
    assert "у Google пока мало данных" in await _speed_line(db, sid)
    assert bot.sent == []


async def test_speed_screen(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    call = FakeCall(f"spd:{sid}")
    await speed_screen.cb_speed(call)
    text = call.message.texts[-1]
    assert text.startswith("👥 Скорость у посетителей — ex.test")
    assert "Не подключено" in text and "на сайт ничего ставить не нужно" in text and "<script" not in text
    assert "↗️ PageSpeed Insights" in buttons(call.message.reply_markup)

    monkeypatch.setattr(integrations, "crux_key", lambda: "AIza-test")
    await sitestatus.update(sid, "crux", lcp=2100, inp=650, cls=0.02, ttfb=400)
    await speed_screen.cb_speed(call)
    text = call.message.texts[-1]
    assert "✅ Страница появляется за 2.1 с" in text and "🟠 Кнопки откликаются за 0.65 с — медленно" in text


async def test_own_measurement_is_gone(db):
    async with TestClient(TestServer(create_app())) as c:
        assert (await c.get("/rum.js")).status == 404
    cursor = await (await get_db()).execute("SELECT name FROM sqlite_master WHERE name = 'rum_samples'")
    assert await cursor.fetchone() is None


async def _probe(key):
    return Probe.OK if key.startswith("AIzaGood") else Probe.REJECTED


async def test_google_key_wizard(bot, db, monkeypatch):
    monkeypatch.setattr(crux, "check_key", lambda key: _probe(key))
    msg = FakeMessage("short")
    await setup.msg_crux_key(msg, FakeState())
    assert "Не похоже" in msg.texts[-1]
    msg = FakeMessage("AIzaBad" + "x" * 32)
    await setup.msg_crux_key(msg, FakeState())
    assert "не принял" in msg.texts[-1] and not crux.available()
    msg = FakeMessage("AIzaGood" + "y" * 31)
    await setup.msg_crux_key(msg, FakeState())
    assert msg.texts[-1].startswith("✅ Данные Google о скорости подключены") and crux.available()
    call = FakeCall("setup:crux")
    await setup.cb_guide_crux(call)
    text = call.message.texts[-1]
    assert "Chrome UX Report API" in text and "На сайты ничего ставить не нужно" in text
    assert "🗑 Отключить" in buttons(call.message.reply_markup)
