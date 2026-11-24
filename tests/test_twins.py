"""Item 3: the technical twin (x.pages.dev) of a Pages site, Google's own
choice of the main copy, and the coverage states in words. Cloudflare is
a local server; the twin's pages are stubbed (https on *.pages.dev)."""

from datetime import UTC, datetime

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import buttons

from handlers import seo as seo_screen
from monitors import seo_checker
from reports import scheduler
from services import cloudflare, index_status, integrations

ZONE = "e" * 32
PAGE = "<html><head><title>Пример</title>{head}</head><body><h1>x</h1></body></html>"


@pytest.fixture
async def cf(monkeypatch):
    async def zone(request):
        return web.json_response({"success": True, "errors": [],
                                  "result": {"id": ZONE, "name": "ex.test", "account": {"id": "acc"}}})

    async def projects(request):
        return web.json_response({"success": True, "errors": [], "result_info": {"total_pages": 1},
                                  "result": [{"name": "shop", "subdomain": "shop-1x.pages.dev",
                                              "domains": ["shop.ex.test"]}]})

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE}", zone)
    app.router.add_get("/client/v4/accounts/acc/pages/projects", projects)
    async with TestServer(app) as server:
        monkeypatch.setattr(cloudflare, "API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE)
        yield


async def test_the_twin_of_a_custom_domain(db, cf):
    assert await cloudflare.pages_twin("shop.ex.test") == "shop-1x.pages.dev"
    assert await cloudflare.pages_twin("other.ex.test") is None
    assert await cloudflare.pages_twin("shop-1x.pages.dev") is None


async def test_a_twin_is_open_only_without_noindex_and_without_a_redirect(monkeypatch):
    answers = {}

    async def fetch(session, url, ua=None):
        return answers[url]

    monkeypatch.setattr(seo_checker, "_fetch", fetch)
    twin = "shop-1x.pages.dev"
    pages = ["https://shop.ex.test/", "https://shop.ex.test/notes/1"]
    plain = PAGE.format(head="")
    answers = {f"https://{twin}/": (200, plain, {}, f"https://{twin}/"),
               f"https://{twin}/notes/1": (200, plain, {}, f"https://{twin}/notes/1")}
    p = await seo_checker._twin_open(None, twin, pages)
    assert p.code == "pages_twin" and p.message == ("технический адрес shop-1x.pages.dev открыт поисковикам: та же "
                                                    "страница без noindex и без переадресации на основной адрес")
    # the homepage is closed by its header, a Functions page is not: that page is named
    answers[f"https://{twin}/"] = (200, plain, {"X-Robots-Tag": "noindex"}, f"https://{twin}/")
    p = await seo_checker._twin_open(None, twin, pages)
    assert "shop-1x.pages.dev/notes/1" in p.message
    # closed by meta, and redirected to the real address: nothing to say
    answers[f"https://{twin}/notes/1"] = (200, PAGE.format(head='<meta name="robots" content="noindex">'), {},
                                          f"https://{twin}/notes/1")
    assert await seo_checker._twin_open(None, twin, pages) is None
    answers = {f"https://{twin}/": (200, plain, {}, "https://shop.ex.test/"),
               f"https://{twin}/notes/1": (None, "", {}, f"https://{twin}/notes/1")}
    assert await seo_checker._twin_open(None, twin, pages) is None


def test_coverage_in_words_and_the_canonical_choice():
    assert index_status.coverage_words("Discovered - currently not indexed").startswith(
        "обнаружена, но не проиндексирована — Google знает адрес")
    assert index_status.coverage_words("Crawled - currently not indexed").startswith("просканирована")
    assert index_status.coverage_words("Excluded by ‘noindex’ tag") == "закрыта noindex"
    assert index_status.coverage_words("Something new") == "Something new"
    assert index_status.coverage_words(None) == "?"
    differs = {"google_canonical": "https://shop-1x.pages.dev/", "user_canonical": "https://shop.ex.test/"}
    assert index_status.canonical_differs(differs)
    assert not index_status.canonical_differs({"google_canonical": "https://Shop.ex.test", "user_canonical": "http://shop.ex.test/"})
    assert not index_status.canonical_differs({"google_canonical": "https://shop.ex.test/"})


def test_the_search_screen_says_it_in_words():
    snap = {"google": {"verdict": "NEUTRAL", "coverage": "Discovered - currently not indexed",
                       "google_canonical": "https://shop-1x.pages.dev/", "user_canonical": "https://shop.ex.test/",
                       "at": datetime.now(UTC).isoformat()}}
    lines, absent = seo_screen.index_lines(snap, google_on=True, yandex_on=False)
    assert lines[0].startswith("📇 Google: главной нет в индексе 🔴 (обнаружена, но не проиндексирована")
    assert lines[1] == ("📇 Google: главной копией считает https://shop-1x.pages.dev/, а страница называет "
                        "https://shop.ex.test/ 🟠")
    assert absent == ["Google"]


async def test_google_choosing_another_main_copy_is_said_once(bot, db):
    sid = await db.activate_or_create_site("https://shop.ex.test")
    site = await db.get_site(sid)
    google = {"verdict": "PASS", "google_canonical": "https://shop-1x.pages.dev/", "user_canonical": "https://shop.ex.test/"}
    await scheduler._google_canonical(site, google)
    await scheduler._google_canonical(site, google)
    [msg] = bot.sent
    assert msg["text"].startswith("🟠 Google считает главной копией shop.ex.test другой адрес: https://shop-1x.pages.dev/")
    assert msg["silent"] is True and "↗️ Search Console" in buttons(msg["reply_markup"])
    await scheduler._google_canonical(site, {"verdict": "PASS"})                 # no canonicals: no word
    assert len(bot.sent) == 1
    await scheduler._google_canonical(site, {**google, "google_canonical": "https://shop.ex.test/"})
    assert bot.sent[-1]["text"] == "✅ Google снова считает главной копией shop.ex.test адрес, который называет страница."
