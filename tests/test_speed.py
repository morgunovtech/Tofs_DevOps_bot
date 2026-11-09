"""Real visitors' speed: the beacon endpoint, the numbers, Google's data,
the daily verdict, the card, the 👥 screen and the key wizard."""

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import add_rum_sample, get_active_incidents, get_rum_samples
from handlers import setup
from handlers import speed as speed_screen
from handlers.sites import card_lines
from reports import scheduler
from services import crux, integrations, rum, sitestatus
from web import webhook
from web.webhook import create_app


def test_only_believable_numbers_are_kept():
    assert rum.clean({"lcp": 1800, "inp": 120, "cls": 0.05, "ttfb": 300, "mobile": 1}) == \
        {"lcp": 1800, "inp": 120, "cls": 0.05, "ttfb": 300, "mobile": True}
    assert rum.clean({"lcp": 1800, "inp": None, "cls": 0}) == \
        {"lcp": 1800, "inp": None, "cls": 0.0, "ttfb": None, "mobile": False}
    for junk in ({"lcp": -1}, {"lcp": "1800"}, {"lcp": True}, {"lcp": 10 ** 7}, {"cls": 50, "lcp": 1},
                 {"inp": None, "cls": None}, [], "x"):
        assert rum.clean(junk) is None, junk


def test_words_and_levels():
    assert rum.describe({"lcp": 1800, "inp": 120, "cls": 0.02}) == \
        "страница появляется за 1.8 с, кнопки откликаются за 0.12 с, вёрстка не прыгает"
    assert rum.describe({"lcp": 3100, "inp": 650, "cls": 0.3}) == (
        "страница появляется за 3.1 с — можно быстрее, кнопки откликаются за 0.65 с — медленно, "
        "вёрстка сильно прыгает при загрузке")
    assert rum.slow_parts({"lcp": 5200, "inp": 100, "cls": 0.3}) == \
        ["страница появляется за 5.2 с", "вёрстка сильно прыгает при загрузке"]
    assert rum.verdict_icon({"lcp": 1000, "inp": None, "cls": 0}) == "✅"
    assert rum.verdict_icon({"lcp": 3000, "inp": 100, "cls": 0}) == "👥"
    assert rum.verdict_icon({"lcp": 5000}) == "🟠"
    assert rum.p75([1, 2, 3, 4]) == 4 and rum.p75([5]) == 5 and rum.p75([]) is None


async def test_beacon_endpoint(bot, db, monkeypatch):
    monkeypatch.setattr(webhook, "_rum_ip", webhook.defaultdict(webhook.deque))
    monkeypatch.setattr(webhook, "_rum_day", {})
    sid = await db.activate_or_create_site("https://ex.test")
    path = f"/rum/{rum.sig(sid)}/{sid}"
    body = '{"lcp": 1800, "inp": 90, "cls": 0.01, "ttfb": 200, "mobile": 1}'
    async with TestClient(TestServer(create_app())) as c:
        r = await c.get("/rum.js")
        assert r.status == 200 and "sendBeacon" in await r.text() and r.headers["Access-Control-Allow-Origin"] == "*"
        assert (await c.post(path, data=body, headers={"Origin": "https://www.ex.test"})).status == 204
        assert (await c.post(f"/rum/{'0' * 16}/{sid}", data=body, headers={"Origin": "https://ex.test"})).status == 404
        assert (await c.post(path, data=body, headers={"Origin": "https://evil.test"})).status == 403
        assert (await c.post(path, data=body)).status == 403                        # no Origin: not a page
        assert (await c.post(path, data="{oops", headers={"Origin": "https://ex.test"})).status == 400
        assert (await c.post(path, data="x" * 2000, headers={"Origin": "https://ex.test"})).status == 413
        assert (await c.options(path)).status == 204
        for _ in range(webhook.RUM_PER_IP_PER_MIN):
            await c.post(path, data=body, headers={"Origin": "https://ex.test"})
        assert (await c.post(path, data=body, headers={"Origin": "https://ex.test"})).status == 429
    rows = await get_rum_samples(sid)
    assert rows[0]["lcp"] == 1800 and rows[0]["mobile"] == 1
    # Every well-addressed beacon counts towards the limit, the malformed two included.
    assert len(rows) == webhook.RUM_PER_IP_PER_MIN - 2


def test_chrome_record_is_read():
    record = {"metrics": {"largest_contentful_paint": {"percentiles": {"p75": 2210}},
                          "interaction_to_next_paint": {"percentiles": {"p75": 160}},
                          "cumulative_layout_shift": {"percentiles": {"p75": "0.04"}}}}
    assert crux.parse(record) == {"lcp": 2210, "inp": 160, "cls": 0.04, "ttfb": None}


async def _speed_line(db, sid: int) -> str:
    return next(line for line in await card_lines(await db.get_site(sid)) if "посетител" in line)


async def _views(sid: int, n: int, lcp: int, inp: int = 80, cls: float = 0.01):
    for _ in range(n):
        await add_rum_sample(sid, lcp, inp, cls, 300, False)


async def test_slow_for_visitors_is_said_quietly_and_its_end_too(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    await _views(sid, 10, 5200)
    await scheduler.run_visitor_checks()
    assert bot.sent == []                                          # ten views prove nothing yet
    assert await _speed_line(db, sid) == \
        "👥 У посетителей: пока 10 просмотров — чтобы судить, нужно хотя бы 30"

    await _views(sid, 30, 5200)
    await scheduler.run_visitor_checks()
    msg = bot.sent[-1]
    assert msg["text"].startswith("🟠 ex.test: медленно у настоящих посетителей") and msg["silent"] is True
    assert "страница появляется за 5.2 с — медленно" in msg["text"] and "40 просмотров" in msg["text"]
    assert {"↗️ PageSpeed Insights", "👥 Скорость у посетителей"} <= set(buttons(msg["reply_markup"]))
    [inc] = await get_active_incidents()
    assert inc["message"] == "у посетителей страница появляется за 5.2 с"
    n = len(bot.sent)
    await scheduler.run_visitor_checks()
    assert len(bot.sent) == n                                      # said once

    await _views(sid, 200, 1500)
    await scheduler.run_visitor_checks()
    assert bot.sent[-1]["text"].startswith("✅ ex.test: у посетителей снова не медленно — страница появляется за 1.5 с")
    assert await get_active_incidents() == []
    line = await _speed_line(db, sid)
    assert line == ("✅ У посетителей: страница появляется за 1.5 с, кнопки откликаются за 0.08 с, вёрстка не "
                    "прыгает · 240 просмотров за 28 дней, твой замер")


async def test_google_data_when_there_is_no_own_measurement(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    other = await db.activate_or_create_site("https://ex.test/blog")
    asked = []

    async def fake_query(origin, key=None):
        asked.append(origin)
        return {"lcp": 2100, "inp": 150, "cls": 0.02, "ttfb": 500}

    monkeypatch.setattr(integrations, "crux_key", lambda: "AIza-test")
    monkeypatch.setattr(crux, "query", fake_query)
    await scheduler.run_visitor_checks()
    assert asked == ["https://ex.test"]                            # one question per origin
    assert (await sitestatus.get(other))["crux"]["lcp"] == 2100
    line = await _speed_line(db, sid)
    assert line.startswith("✅ У посетителей: страница появляется за 2.1 с") and line.endswith("данные Google")

    async def too_few(origin, key=None):
        return {}

    monkeypatch.setattr(crux, "query", too_few)
    await scheduler.run_visitor_checks()
    assert (await sitestatus.get(sid))["crux"]["none"] is True
    assert "у Google пока мало данных" in await _speed_line(db, sid)


async def test_speed_screen_gives_the_one_line(bot, db, cfg):
    cfg(public_base_url="https://bot.test")
    sid = await db.activate_or_create_site("https://ex.test")
    call = FakeCall(f"spd:{sid}")
    await speed_screen.cb_speed(call)
    text = call.message.texts[-1]
    assert text.startswith("👥 Скорость у посетителей — ex.test")
    assert (f"<code>&lt;script src=\"https://bot.test/rum.js\" data-site=\"{rum.sig(sid)}.{sid}\" defer&gt;"
            f"&lt;/script&gt;</code>") in text
    assert "не ставит cookie" in text and "Не подключено: 🔌 Подключения" in text
    assert "↗️ PageSpeed Insights" in buttons(call.message.reply_markup) and "CSP" not in text
    # A strict CSP (the owner's own site has one) would block the script: say what to add.
    await sitestatus.update(sid, "security", findings=[], csp=True)
    await speed_screen.cb_speed(call)
    assert "Допиши адрес бота <code>https://bot.test</code> в script-src и connect-src" in call.message.texts[-1]


async def test_google_key_wizard(bot, db, monkeypatch):
    async def works(key):
        return key.startswith("AIzaGood")

    monkeypatch.setattr(crux, "key_works", works)
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
    assert "Chrome UX Report API" in call.message.texts[-1] and "🗑 Отключить" in buttons(call.message.reply_markup)
