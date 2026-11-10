"""The search audit's verdicts: the homepage behind a redirect, every form of
noindex, robots.txt read the same on every Python, an audit that read
nothing closes nothing, a vanished sitemap is not «all pages answer»."""

import time

from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents
from monitors import seo_checker
from monitors.base import SeoResult
from monitors.seo_checker import _key, _page, analyze_page, analyze_robots
from reports import scheduler
from services import sitestatus

GOOD = ("<html lang='ru'><head><title>Магазин кофе в Ташкенте</title>"
        "<meta name='description' content='Свежая обжарка каждый день, доставка по городу за час.'>"
        "</head><body><h1>Кофе</h1>" + "<p>текст страницы</p>" * 60 + "</body></html>")
NOINDEX = GOOD.replace("<head>", "<head><meta name='robots' content='noindex'>")
CLEAN = GOOD.replace("<head>", "<head><meta property='og:title' content='Кофе'>"
                               "<meta property='og:image' content='https://example.com/p.jpg'>")


def _noindex_level(html: str, headers: dict | None = None) -> str | None:
    problems, _ = analyze_page(html, "https://example.com/", headers)
    levels = [p.severity for p in problems if p.code.startswith("noindex")]
    return levels[0] if levels else None


def test_every_form_of_noindex_and_only_ours():
    assert _noindex_level("<META NAME='ROBOTS' CONTENT='NOINDEX, NOFOLLOW'>") == "critical"
    assert _noindex_level("<meta name='robots' content='index, follow'><meta name='robots' content='noindex'>")
    assert _noindex_level("<meta name='googlebot' content='noindex'>") == "critical"
    assert _noindex_level("<meta name='yandex' content='noindex'>") == "critical"
    assert _noindex_level("<meta name='robots' content='none'>") == "critical"
    assert _noindex_level("<meta name='robots' content='noindex nofollow'>") == "critical"
    assert _noindex_level("", {"X-Robots-Tag": "noindex, max-image-preview:large"}) == "critical"
    assert _noindex_level("", {"X-Robots-Tag": "noindex, max-snippet:0"}) == "critical"
    assert _noindex_level("", {"X-Robots-Tag": "googlebot: noindex"}) == "critical"
    assert _noindex_level("", {"X-Robots-Tag": "GPTBot: noindex"}) is None
    assert _noindex_level("", {"X-Robots-Tag": "googlebot: nofollow\notherbot: noindex, nofollow"}) is None
    assert _noindex_level("", {"X-Robots-Tag": "unavailable_after: 25 Jun 2030 15:00:00 PST"}) is None
    # An inner page's noindex is a 🟠; the homepage asked for, wherever it
    # landed, is a 🔴 and is named as the homepage.
    problems, _ = analyze_page(NOINDEX, "https://example.com/en/")
    assert [(p.code, p.severity) for p in problems if p.code == "noindex_meta"] == [("noindex_meta", "warning")]
    problems, _ = analyze_page(NOINDEX, "https://example.com/en/", is_home=True)
    noindex = [p for p in problems if p.code == "noindex_meta"]
    assert noindex[0].severity == "critical" and noindex[0].message.startswith("главная (→ /en/): ")
    assert "og_title" in {p.code for p in problems}            # the homepage checks follow the request
    problems, _ = analyze_page("<meta name='Description' content='x'>", "https://example.com/")
    assert "no_description" not in {p.code for p in problems}


def test_robots_txt_is_read_like_google_on_every_python():
    def codes(text: str) -> list[str]:
        return [p.code for p in analyze_robots(text, "https://example.com")[0]]
    assert codes("User-agent: *\nDisallow: /*") == ["robots_search", "robots_ai"]
    assert codes("User-agent: *\nDisallow: /$") == ["robots_search", "robots_ai"]
    assert codes("User-agent: *\nAllow: /$\nDisallow: /") == []
    assert codes("User-agent: *\nDisallow: /\nAllow: /") == []
    assert codes("User-agent: bot\nDisallow: /") == []
    assert codes("User-agent: *\nDisallow: /admin/") == []
    assert codes("User-agent: *\nDisallow:\nUser-agent: GPTBot\nDisallow: /") == ["robots_ai"]
    assert codes("﻿User-agent: *\nDisallow: /") == ["robots_search", "robots_ai"]   # a BOM is not a name
    assert codes("User-agent: Googlebot\nUser-agent: Yandex\nDisallow: /") == ["robots_search"]
    assert codes("User-agent: YandexBot\nAllow: /\nUser-agent: Yandex\nDisallow: /") == []
    problems, _ = analyze_robots("User-agent: Yandex\nDisallow: /", "https://example.com")
    assert problems[0].message.startswith("robots.txt запрещает YandexBot —")
    problems, _ = analyze_robots("User-agent: Googlebot\nCrawl-delay: ²\nDisallow: /", "https://example.com")
    assert problems[0].message.startswith("robots.txt запрещает Googlebot —")
    _, sitemaps = analyze_robots("Sitemap: https://example.com/sitemap.xml # the map", "https://example.com")
    assert sitemaps == ["https://example.com/sitemap.xml"]
    # A hostile file from the watched site — 4000 agents sharing 4000 rules —
    # is read in linear time, not as 16 million tuples.
    hostile = "\n".join(f"User-agent: bot{i}" for i in range(4000)) + "\nUser-agent: *\n" \
        + "\n".join(f"Disallow: /p{i}" for i in range(4000))
    started = time.perf_counter()
    assert codes(hostile) == []
    assert time.perf_counter() - started < 2


def test_page_labels_and_keys():
    assert _page("/%D0%BE-%D0%BD%D0%B0%D1%81") == "/о-нас"
    assert len(_page("/" + "x" * 200)) == 60
    assert _key("https://www.ex.test/") == _key("http://ex.test") == ("ex.test", "/")


async def _audit_site(state: dict):
    async def home(request):
        raise web.HTTPFound("/en/")

    async def en(request):
        return web.Response(text=state["en"], content_type="text/html")

    async def robots(request):
        return web.Response(status=state.get("robots", 404))

    async def sitemap(request):
        base = f"http://{request.host}"
        return web.Response(text=f"<urlset><url><loc>{base}/en/</loc></url>"
                                 f"<url><loc>{base}/about</loc></url></urlset>", content_type="text/xml")

    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/en/", en)
    app.router.add_get("/about", lambda r: web.Response(text=GOOD, content_type="text/html"))
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/sitemap.xml", sitemap)
    return TestServer(app)


async def _no(*args):
    return False


async def _seo_incidents() -> list[str]:
    return [i["severity"] for i in await get_active_incidents() if i["check_type"] == "seo"]


async def test_homepage_behind_a_redirect_and_an_audit_that_read_nothing(db, cfg, monkeypatch):
    cfg(seo_pages_sample=3)
    monkeypatch.setattr(seo_checker, "plain_http_opens", _no)
    state = {"en": NOINDEX}
    async with await _audit_site(state) as server:
        base = str(server.make_url("")).rstrip("/")
        r = await seo_checker.check_seo(base)
        noindex = [p for p in r.problems if p.code == "noindex_meta"]
        assert [p.severity for p in noindex] == ["critical"]          # once, and 🔴: it IS the homepage
        assert noindex[0].message.startswith("главная (→ /en/)") and r.incident_new and r.pages_checked == 2

        real_fetch = seo_checker._fetch

        async def robots_silent(session, url, ua=seo_checker.USER_AGENT):
            if url.endswith("/robots.txt"):
                return None, "", {}, url
            return await real_fetch(session, url, ua)

        state["en"] = GOOD
        monkeypatch.setattr(seo_checker, "_fetch", robots_silent)
        r = await seo_checker.check_seo(base)
        assert not r.recovered and not r.downgraded                      # no verdict — nothing closes
        assert await _seo_incidents() == ["critical"]
        seo = (await sitestatus.get(r.site_id))["seo"]
        assert seo["incomplete"] and seo["status"] == "critical"          # the snapshot never says less than the open 🔴

        monkeypatch.setattr(seo_checker, "_fetch", real_fetch)
        r = await seo_checker.check_seo(base)
        assert r.downgraded and not r.has_critical                       # 🔴 → 🟠: «запрет снят»
        assert await _seo_incidents() == ["warning"]
        assert not (await sitestatus.get(r.site_id))["seo"]["incomplete"]


async def test_an_incomplete_audit_still_opens_what_it_saw(db, cfg, monkeypatch):
    """robots.txt answers 503 and no incident is open: the 🟠 finding about
    robots.txt itself opens one — incomplete means «cannot lower», not «mute»."""
    cfg(seo_pages_sample=1)
    monkeypatch.setattr(seo_checker, "plain_http_opens", _no)
    state = {"en": GOOD, "robots": 503}
    async with await _audit_site(state) as server:
        base = str(server.make_url("")).rstrip("/")
        r = await seo_checker.check_seo(base)
        assert "robots_http_error" in {p.code for p in r.problems}
        assert r.incident_new and r.severity == "warning" and await _seo_incidents() == ["warning"]
        assert (await sitestatus.get(r.site_id))["seo"]["incomplete"]
        # A clean homepage with a silent robots.txt is not «OK»: no verdict.
        await db.resolve_incident(r.site_id, "seo")
        state.update(robots=404, en=CLEAN)
        real_fetch = seo_checker._fetch

        async def robots_silent(session, url, ua=seo_checker.USER_AGENT):
            if url.endswith("/robots.txt"):
                return None, "", {}, url
            return await real_fetch(session, url, ua)

        monkeypatch.setattr(seo_checker, "_fetch", robots_silent)
        r = await seo_checker.check_seo(base)
        assert r.status == "error" and r.transient and not r.recovered
        assert "incomplete (robots.txt read: False, homepage read: True)" in \
            (await db.get_last_check(r.site_id, "seo"))["details"]


async def test_the_lifted_ban_is_said_once_and_quietly(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    conn = await db.get_db()
    await conn.execute("UPDATE sites SET added_at = datetime('now', '-3 days') WHERE id = ?", (sid,))
    await conn.commit()
    result = SeoResult(url="https://ex.test", site_id=sid, status="warning", downgraded=True)

    async def fake_check_all_seo(urls, manage=True):
        return [result] if urls else []

    monkeypatch.setattr(scheduler, "check_all_seo", fake_check_all_seo)
    await scheduler.run_seo_checks()
    texts = [m["text"] for m in bot.sent]
    assert texts == ["✅ ex.test: запрет для поисковиков снят — сайт снова открыт для поиска. "
                     "Мелкие помехи остались, они в «🔎 Поиск и ИИ»."]
    assert bot.sent[0]["silent"]
