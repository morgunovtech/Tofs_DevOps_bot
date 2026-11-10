"""The search audit's verdicts: the homepage behind a redirect, every form of
noindex, robots.txt read the same on every Python, an audit that read
nothing closes nothing, a vanished sitemap is not «all pages answer»."""

import asyncio
import gzip
import ssl
import time
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall

from db.database import get_active_incidents, save_incident
from handlers import seo as seo_screen
from handlers.seo import headline
from handlers.sites import _pages_lines
from monitors import deep_checker, pagemeta, seo_checker
from monitors.base import DeepResult, SeoResult
from monitors.seo_checker import _key, _page, analyze_page, analyze_robots
from monitors.sitemap import scan_sitemap
from reports import scheduler
from reports.formatter import format_deep_alert
from services import seo_fixes, sitestatus
from services.humanize import describe_error


def _answer(**response):
    """An aiohttp handler that always gives this answer (aiohttp wants async handlers)."""
    async def handler(_request):
        return web.Response(**response)
    return handler


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


def test_the_screen_says_when_there_is_no_verdict():
    assert headline({}, [], []) == "⏳ Аудита ещё не было — нажми «🔄 Проверить снова»."
    # robots.txt silent, the homepage clean: not «the site did not open».
    incomplete = {"status": "error", "incomplete": True, "problems": [], "pages": 1}
    assert headline(incomplete, [], []) == ("⚠️ Часть проверки не удалась (robots.txt или главная не ответили) "
                                            "— вердикта нет; открытые проблемы остаются в «Что сейчас не так».")
    # An incomplete audit with 🟠 findings and an open 🔴: the snapshot keeps
    # «critical», and the screen says «no verdict», not «closed» or «N things».
    groups = [("og_title", "warning", ["нет заголовка для превью"])]
    assert headline({"status": "critical", "incomplete": True}, groups, []).startswith("⚠️ Часть проверки не удалась")
    assert headline({"status": "error", "problems": []}, [], []).startswith("⚠️ Сайт не открывался")
    assert headline({"status": "warning"}, groups, []).startswith("🟠 Сайт открыт для поисковиков, но есть 1 помеха")


async def test_a_failed_live_audit_keeps_the_old_screen_and_says_so(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://seo.test")
    await sitestatus.update(sid, "seo", status="warning", critical=0, improve=1, pages=3, no_js_chars=500,
                            problems=[{"code": "og_image", "severity": "warning", "message": "нет картинки (og:image)"}])

    async def nothing(urls, manage=True):
        return []          # the check itself failed: gather_checks returns no result

    async def no_index(site_id, url):
        return None, None

    monkeypatch.setattr(seo_screen, "check_all_seo", nothing)
    monkeypatch.setattr(seo_screen.index_status, "refresh", no_index)
    call = FakeCall(f"seo_live:{sid}")
    await seo_screen.cb_seo_live(call)
    assert "🟠 Сайт открыт для поисковиков, но есть 1 помеха" in call.message.texts[-2]     # the previous data
    assert call.message.texts[-1] == "⚠️ Проверка не удалась — на экране прошлые данные. Попробуй ещё раз позже."


async def test_the_www_twin_with_a_bad_certificate_is_named(monkeypatch):
    class FailingSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, url, **kwargs):
            raise aiohttp.ClientConnectorCertificateError(
                SimpleNamespace(host="www.ex.test", port=443, is_ssl=True, ssl=True),
                ssl.SSLCertVerificationError("hostname mismatch"))

    monkeypatch.setattr(pagemeta.aiohttp, "ClientSession", FailingSession)
    note = await pagemeta.alt_host_note("https://ex.test/")
    assert note == ("⚠️ www.ex.test открывается с ошибкой сертификата — кто наберёт адрес так, увидит "
                    "предупреждение браузера. Выпусти сертификат и на www.ex.test или настрой редирект на ex.test.")


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
    app.router.add_get("/about", _answer(text=GOOD, content_type="text/html"))
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


async def test_sitemap_of_another_host_a_gzipped_one_and_an_spa_shell():
    state = {"body": b"", "type": "text/xml"}

    async def sitemap(request):
        return web.Response(body=state["body"], content_type=state["type"])

    app = web.Application()
    app.router.add_get("/sitemap.xml", sitemap)
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as session:
            state["body"] = b"<urlset><url><loc>https://old-domain.example/a</loc></url></urlset>"
            scan = await scan_sitemap(session, base)
            assert scan.pages == [] and not scan.missing and scan.foreign_hosts == ["old-domain.example"]
            state["body"] = gzip.compress(f"<urlset><url><loc>{base}/p</loc></url></urlset>".encode())
            state["type"] = "application/x-gzip"
            assert (await scan_sitemap(session, base)).pages == [f"{base}/p"]
            # An SPA answers /sitemap.xml with its index.html: that is «no
            # sitemap», not a map with no pages — and so is an empty <urlset/>.
            state["body"], state["type"] = b"<html><body>app</body></html>", "text/html"
            scan = await scan_sitemap(session, base)
            assert scan.missing and scan.pages == [] and not scan.foreign_hosts
            state["body"], state["type"] = b"<?xml version='1.0'?><urlset/>", "text/xml"
            assert (await scan_sitemap(session, base)).missing


async def test_a_sitemap_of_the_old_domain_is_a_finding(db, cfg, monkeypatch):
    cfg(seo_pages_sample=1)
    monkeypatch.setattr(seo_checker, "plain_http_opens", _no)

    async def sitemap(request):
        return web.Response(text="<urlset><url><loc>https://old-domain.example/a</loc></url></urlset>",
                            content_type="text/xml")

    app = web.Application()
    app.router.add_get("/", _answer(text=GOOD, content_type="text/html"))
    app.router.add_get("/sitemap.xml", sitemap)
    async with TestServer(app) as server:
        r = await seo_checker.check_seo(str(server.make_url("")).rstrip("/"), manage=False)
    found = {p.code: p.message for p in r.problems}
    assert "sitemap_missing" not in found
    assert found["sitemap_foreign"] == "карта сайта ведёт на другой адрес: old-domain.example"


async def test_deep_check_without_sitemap_and_with_silent_pages(bot, db, monkeypatch):
    async def page(request):
        if request.path.startswith("/slow"):
            await asyncio.sleep(2)
        return web.Response(text="ok")

    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(base)
        await save_incident(sid, "deep", "5xx на 4 из 10 проверенных страниц")
        await sitestatus.update(sid, "deep", sampled=10, errors=4, pages=[["/a", 502]])

        async def no_pages(session, url):
            return []

        monkeypatch.setattr(deep_checker, "sitemap_urls", no_pages)
        r = await deep_checker.check_deep(base)
        assert not r.recovered and not [i for i in await get_active_incidents() if i["check_type"] == "deep"]
        deep = (await sitestatus.get(sid))["deep"]
        assert (deep["sampled"], deep["errors"], deep["pages"]) == (0, 0, [])    # nothing was checked

        async def three(session, url):
            return [f"{base}/ok", f"{base}/slow1", f"{base}/slow2"]

        monkeypatch.setattr(deep_checker, "sitemap_urls", three)
        monkeypatch.setattr(deep_checker, "TIMEOUT", aiohttp.ClientTimeout(total=0.5))
        r = await deep_checker.check_deep(base)
        assert r.status == "error" and r.error == "5xx или нет ответа на 2 из 3 проверенных страниц"
        deep = (await sitestatus.get(sid))["deep"]
        assert deep["pages"] == [["/ok", None], ["/slow1", 0], ["/slow2", 0]]

        # Not one page answered: the bot's network or the whole site — no verdict.
        async def two_slow(session, url):
            return [f"{base}/slow1", f"{base}/slow2"]

        monkeypatch.setattr(deep_checker, "sitemap_urls", two_slow)
        r = await deep_checker.check_deep(base)
        assert r.status == "ok" and r.error is None and not r.recovered
        assert (await sitestatus.get(sid))["deep"]["pages"] == deep["pages"]     # the card keeps the last verdict


def test_a_silent_page_is_not_a_healthy_one_on_the_card_and_in_the_alert():
    lines = _pages_lines({"sampled": 3, "errors": 2, "pages": [["/ok", None], ["/slow", 0], ["/broken", 502]]})
    assert lines == ["🟠 Страницы из карты сайта: 2 из 3 отвечают ошибкой сервера или не отвечают",
                     "    🔴 /slow — не ответила", "    🔴 /broken — ошибка 502"]    # the healthy ones are not listed
    assert _pages_lines({"sampled": 2, "errors": 1, "pages": [["/a", None], ["/b", 500]]})[0] == \
        "🟠 Страницы из карты сайта: 1 из 2 отвечают ошибкой сервера"
    assert describe_error("5xx или нет ответа на 2 из 3 проверенных страниц") == \
        "2 из 3 проверенных страниц отдают ошибку или не отвечают"
    assert describe_error("5xx на 3 из 10 проверенных страниц") == "3 из 10 проверенных страниц отдают ошибку"
    r = DeepResult(url="https://ex.test", site_id=1, status="error",
                   error="5xx или нет ответа на 2 из 3 проверенных страниц",
                   errors=[("https://ex.test/slow", 0), ("https://ex.test/broken", 502)])
    text = format_deep_alert(r)
    assert "https://ex.test/slow — не ответила за 15 секунд" in text and "HTTP 0" not in text


def test_fixes_do_not_break_the_site():
    spa = " ".join(seo_fixes.steps("soft404_spa", None, "https://ex.com"))
    assert "НЕ удаляй" in spa and "внутренние страницы" in spa and "/* /404.html 404" in spa
    for hosting in (None, "cloudflare", "netlify", "vercel", "github"):
        plain = " ".join(seo_fixes.steps("soft404", hosting, "https://ex.com"))
        assert "2–3 внутренние страницы" in plain and "Проверить снова" in plain, hosting
    assert "НЕ удаляй" in " ".join(seo_fixes.steps("soft404", None, "https://ex.com"))
    cf = seo_fixes.steps("https_redirect", "cloudflare", "https://ex.com")
    assert any("52" in s and "Full (strict)" in s and "сертификат" in s for s in cf)
    assert not any("поставь режим SSL/TLS" in s for s in cf)
    no_js = " ".join(seo_fixes.steps("no_js", None, "https://ex.com"))
    assert "vike" in no_js and "vite-plugin-ssr" not in no_js and "только в этой вкладке" in no_js
    assert 'lang="en"' in " ".join(seo_fixes.steps("no_lang", None, "https://ex.com"))
    assert seo_fixes.fix("sitemap_foreign").title != "sitemap_foreign"
    assert seo_fixes.links("sitemap_foreign", None, "https://ex.com") == [("🗺 Открыть sitemap.xml", "https://ex.com/sitemap.xml")]


async def test_an_application_in_the_browser_gets_the_spa_steps(db, cfg, monkeypatch):
    """Every address answers 200 with a nearly empty shell: the soft-404
    finding carries the code whose steps keep the catch-all rule."""
    cfg(seo_pages_sample=1)
    monkeypatch.setattr(seo_checker, "plain_http_opens", _no)
    shell = "<html><head><title>App</title></head><body><div id='root'></div></body></html>"
    app = web.Application()
    app.router.add_get("/{tail:.*}", _answer(text=shell, content_type="text/html"))
    async with TestServer(app) as server:
        r = await seo_checker.check_seo(str(server.make_url("")).rstrip("/"), manage=False)
    codes = {p.code for p in r.problems}
    assert "soft404_spa" in codes and "soft404" not in codes and "no_js" in codes
    assert "sitemap_missing" in codes            # the shell at /sitemap.xml is not a sitemap


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
