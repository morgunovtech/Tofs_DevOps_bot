"""Item 4: Bing Webmaster — the key's live check, the daily state of the
watched sites (verification, pages in the index, crawl issues, sitemaps)
said on a change only, the Sunday clicks and impressions. Bing is a local
server answering in its JSON shape («d», «/Date(…)/»)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeMessage, FakeState, buttons
from test_i18n import screen_english

from handlers import setup as setup_screen
from reports import scheduler
from reports.weekly import search_metrics_lines
from services import bing, integrations
from utils import i18n

KEY = "test" * 8                      # a fake key of the right shape


def bdate(d: date) -> str:
    return f"/Date({int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp() * 1000)}-0700)/"


@pytest.fixture
async def bing_api(monkeypatch):
    today = datetime.now(UTC).date()
    state = {
        "sites": [{"__type": "Site:#Microsoft.Bing.Webmaster.Api", "Url": "https://shop.ex.test/", "IsVerified": True},
                  {"Url": "https://other.test/", "IsVerified": True}],
        "in_index": 40, "issues": [], "feeds": [{"Url": "https://shop.ex.test/sitemap.xml", "Status": "Success"}],
        "traffic": [{"Date": bdate(today - timedelta(days=i)), "Clicks": 3 if i <= 7 else 2, "Impressions": 50}
                    for i in range(1, 15)],
        "refuse": False, "asked": []}

    async def handler(request):
        method = request.match_info["method"]
        state["asked"].append((method, request.query.get("apikey")))
        if state["refuse"] or request.query.get("apikey") != KEY:
            return web.json_response({"ErrorCode": "InvalidApiKey", "Message": "Invalid API key"}, status=400)
        d = {"GetUserSites": state["sites"],
             "GetCrawlStats": [{"Date": bdate(today - timedelta(days=2)), "InIndex": 39},
                               {"Date": bdate(today - timedelta(days=1)), "InIndex": state["in_index"]}],
             "GetCrawlIssues": state["issues"], "GetFeeds": state["feeds"],
             "GetRankAndTrafficStats": state["traffic"]}[method]
        return web.json_response({"d": d})

    app = web.Application()
    app.router.add_get("/webmaster/api.svc/json/{method}", handler)
    async with TestServer(app) as server:
        monkeypatch.setattr(bing, "BASE", str(server.make_url("/webmaster/api.svc/json")))
        yield state


def test_dates_and_weeks():
    assert bing.parse_date("/Date(1315349995266-0700)/") == datetime.fromtimestamp(1315349995.266, UTC)
    assert bing.parse_date("2026-11-24") is None
    today = date(2026, 11, 24)
    rows = [{"Date": bdate(today - timedelta(days=i)), "Clicks": i, "Impressions": 10} for i in range(1, 15)]
    cur, prev = bing.week_totals(rows, today)
    assert cur == {"clicks": sum(range(1, 8)), "impressions": 70} and prev == {"clicks": sum(range(8, 15)), "impressions": 70}
    assert bing.week_totals([], today) is None


async def test_the_wizard_checks_the_key_live(bot, db, bing_api):
    msg = FakeMessage(KEY)
    await setup_screen.msg_bing_key(msg, FakeState())
    assert msg.texts[-1] == "✅ Bing Webmaster подключён: сайтов в аккаунте 2."
    assert integrations.bing_key() == KEY and msg.deleted                    # the message with the key is gone
    bing_api["refuse"] = True
    msg = FakeMessage("f" * 32)
    await setup_screen.msg_bing_key(msg, FakeState())
    assert msg.texts[-1].startswith("❌ Bing не принял ключ")
    assert integrations.bing_key() == KEY                                    # the old one stays


async def test_a_change_is_said_once_and_its_end_too(bot, db, bing_api):
    await integrations.set_value("bing_key", KEY)
    sid = await db.activate_or_create_site("https://shop.ex.test")
    await scheduler.run_index_checks()
    assert bot.sent == []                                                     # the first look: a baseline
    bing_api["in_index"] = 0
    bing_api["issues"] = [{"Url": "https://shop.ex.test/a", "HttpCode": 404, "Issues": 2, "InLinks": 1}]
    bing_api["feeds"][0]["Status"] = "Failed"
    await scheduler.run_index_checks()
    said = [m["text"].split("\n")[0] for m in bot.sent]
    assert said == ["🟠 Bing больше не держит в индексе ни одной страницы shop.ex.test (было 40) — по нему ищут "
                    "ChatGPT и Copilot.",
                    "🟠 Bing: 1 адрес shop.ex.test с ошибками обхода.",
                    "🟠 Bing не может прочитать sitemap shop.ex.test: https://shop.ex.test/sitemap.xml"]
    assert all(m["silent"] for m in bot.sent) and "↗️ Bing Webmaster" in buttons(bot.sent[0]["reply_markup"])
    await scheduler.run_index_checks()
    assert len(bot.sent) == 3                                                 # said once
    bing_api.update(in_index=25, issues=[])
    bing_api["feeds"][0]["Status"] = "Success"
    await scheduler.run_index_checks()
    assert [m["text"] for m in bot.sent[3:]] == ["✅ Bing снова держит страницы shop.ex.test в индексе: 25.",
                                                  "✅ Bing больше не видит ошибок обхода на shop.ex.test.",
                                                  "✅ Bing снова читает sitemap shop.ex.test."]
    bing_api["sites"][0]["IsVerified"] = False
    await scheduler.run_index_checks()
    assert bot.sent[-1]["text"].startswith("🟠 Bing Webmaster: shop.ex.test не подтверждён")
    snap = (await scheduler.sitestatus.get(sid))["bing"]
    assert snap["verified"] is False


async def test_a_refused_key_is_no_verdict(bot, db, bing_api):
    await integrations.set_value("bing_key", KEY)
    await db.activate_or_create_site("https://shop.ex.test")
    bing_api["refuse"] = True
    await scheduler.run_index_checks()
    assert bot.sent == []
    h = await integrations.health("bing")
    assert h["refused"] and "InvalidApiKey" in h["reason"]


async def test_the_sunday_lines(db, bing_api):
    await integrations.set_value("bing_key", KEY)
    lines = await search_metrics_lines(["https://shop.ex.test"])
    assert lines == ["🔎 Bing (неделя к неделе):", "  shop.ex.test: 21 клик (+50%), 350 показов (+0%)"]
    bing_api["refuse"] = True
    await integrations.record_result("bing", False, "ключ не принят (InvalidApiKey)", refused=True)
    lines = await search_metrics_lines(["https://shop.ex.test"])
    assert lines == ["⚠️ Bing Webmaster: не смог получить данные — ключ не принят (InvalidApiKey)"]


async def test_in_english(bot, db, bing_api):
    await integrations.set_value("bing_key", KEY)
    await db.activate_or_create_site("https://shop.ex.test")
    await scheduler.run_index_checks()
    bing_api["in_index"] = 0
    bing_api["sites"][0]["IsVerified"] = False
    i18n.set_language("en")
    try:
        await scheduler.run_index_checks()
        lines = await search_metrics_lines(["https://shop.ex.test"])
    finally:
        i18n.set_language("ru")
    assert bot.sent
    for m in bot.sent:
        screen_english(m["text"], m["reply_markup"])
    screen_english("\n".join(lines))
