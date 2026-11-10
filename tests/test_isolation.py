"""One site must not cost the others their checks and alerts: an exception
while alerting about one site, a malformed stored address, an absurd
interval, a broken sitemap, link, canonical or robots.txt."""

import logging
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

import main
from db.database import get_active_incidents, get_all_sites, get_last_check, save_incident
from monitors import seo_checker, sitemap
from monitors.links_checker import check_links
from reports import scheduler
from services import notifier
from utils.urls import proxy_problem, site_label


def _server(status: int, page: str = "<html>hello</html>") -> TestServer:
    async def handler(_):
        return web.Response(status=status, text=page, content_type="text/html")
    app = web.Application()
    app.router.add_get("/", handler)
    return TestServer(app)


async def test_a_failed_alert_does_not_cost_the_next_site_its_alert(bot, db, monkeypatch, caplog):
    real_send = notifier.send
    calls = []

    async def send_failing_once(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return await real_send(*args, **kwargs)

    monkeypatch.setattr(notifier, "send", send_failing_once)
    async with _server(503) as a, _server(503) as b:
        for server in (a, b):
            sid = await db.activate_or_create_site(str(server.make_url("")).rstrip("/"))
            await db.update_site_settings(sid, fail_threshold=1)
        with caplog.at_level(logging.ERROR, logger="reports.scheduler"):
            await scheduler.run_availability_checks()       # must not raise
            await scheduler.drain_alert_worker()
    alerts = [m for m in bot.sent if "не открывается" in m["text"]]
    assert len(calls) == 2 and len(alerts) == 1             # the second site was told about
    assert "Alert worker" in caplog.text and "failed" in caplog.text


async def test_transitions_loop_survives_one_bad_result(bot, db, caplog):
    first = await db.get_or_create_site("https://a.example")
    second = await db.get_or_create_site("https://b.example")

    class R:
        def __init__(self, url, site_id):
            self.url, self.site_id = url, site_id
            self.incident_new, self.recovered, self.incident_id = True, False, None

    def alert(r):
        if r.site_id == first:
            raise ValueError("cannot format")
        return f"🔴 {r.url} упал"

    with caplog.at_level(logging.ERROR, logger="reports.scheduler"):
        await scheduler._notify_transitions([R("https://a.example", first), R("https://b.example", second)],
                                            alert, lambda r: "✅", keyboard=lambda r: None)
    assert [m["text"] for m in bot.sent] == ["🔴 https://b.example упал"]
    assert "Alert for a.example" in caplog.text


@pytest.mark.parametrize(("url", "label"), [
    ("tcp://db:54321x", "tcp://db:54321x"),
    ("https://[::1", "https://[::1"),
    ("https://user:pw@[::1/x?token=secret", "https://[::1/x"),
    ("https://example.com/", "example.com"),
    ("tcp://db.example:5432", "db.example:5432"),
])
def test_site_label_never_raises(url, label):
    assert site_label(url) == label


def test_proxy_problem_explains_a_broken_address():
    assert "[" in proxy_problem("socks5://[::1")


async def test_sites_from_env_go_through_the_chat_rules(db):
    added = await main.seed_sites(["https://example.com", "tcp://db:5432x", "https://[::1",
                                   "ping://-c999", "https://example.com"]
                                  + [f"https://s{i}.example" for i in range(30)])
    urls = [s["url"] for s in await get_all_sites()]
    assert "https://example.com" in urls
    assert not [u for u in urls if "5432x" in u or "[" in u or "-c999" in u]
    assert len(urls) == len(added) == 20                    # MAX_SITES


async def test_an_absurd_interval_does_not_stop_the_other_sites(bot, db):
    async with _server(200) as ok:
        healthy = await db.activate_or_create_site(str(ok.make_url("")).rstrip("/"))
        broken = await db.get_or_create_site("tcp://127.0.0.1:9")      # a closed local port, no network
        await db.update_site_settings(broken, check_interval_min=10**10)
        await scheduler.run_availability_checks()            # was: OverflowError on every tick
        await scheduler.drain_alert_worker()
    assert await get_last_check(healthy, "availability")


@pytest.mark.parametrize("xml", [
    "<loc>" + " " * 3000,                                  # the old pattern: 7 s here, minutes at 10 KB
    "<loc>" * 8000,
    "<loc><![CDATA[" * 20_000,
])
def test_sitemap_parsing_is_linear(xml):
    start = time.perf_counter()
    sitemap._locs(xml)
    assert time.perf_counter() - start < 0.25


def test_sitemap_values_are_read_as_before():
    xml = ("<urlset><url><LOC>\n  https://e.example/a?x=1&amp;y=2\n</LOC></url>"
           "<url><loc><![CDATA[https://e.example/b]]></loc></url>"
           "<url><loc></loc></url><url><loc>https://e.example/c</loc></url></urlset>")
    assert sitemap._locs(xml) == ["https://e.example/a?x=1&y=2", "https://e.example/b", "https://e.example/c"]


async def test_a_link_with_a_typo_is_reported_and_the_crawl_survives(db):
    page = '<html><a href="https://example.com]/post">typo</a> <a href="/gone">gone</a></html>'

    async def home(_):
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", home)
    async with TestServer(app) as server:
        r = await check_links(str(server.make_url("")).rstrip("/"))
    assert sorted(b.url.rsplit("/", 1)[-1] for b in r.broken) == ["gone", "post"]


def test_a_broken_canonical_or_json_ld_is_a_finding_not_a_crash():
    # Deep AND unterminated: 3.12/3.13 hit RecursionError, 3.14 parses the
    # depth and reports the missing brackets — invalid either way.
    deep = "[" * 100_000
    html = (f'<html><head><link rel="canonical" href="https://example.com]/">'
            f'<script type="application/ld+json">{deep}</script></head></html>')
    codes = {p.code for p in seo_checker.analyze_page(html, "https://example.com/")[0]}
    assert {"canonical_bad", "jsonld_invalid"} <= codes


async def test_an_unparsable_robots_txt_does_not_close_an_open_finding(db, cfg, monkeypatch):
    page = ("<html lang='ru'><head><title>Магазин кофе в Ташкенте</title>"
            "<meta name='description' content='Свежая обжарка каждый день, доставка по городу за час.'>"
            "</head><body><h1>Кофе</h1>" + "<p>текст</p>" * 200 + "</body></html>")

    async def home(_):
        return web.Response(text=page, content_type="text/html")

    async def robots(_):
        return web.Response(text="User-agent: *\nDisallow: /\n")

    def broken_parser(text, base):
        raise ValueError("invalid literal for int() with base 10: '²'")

    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/robots.txt", robots)
    monkeypatch.setattr(seo_checker, "analyze_robots", broken_parser)
    cfg(seo_pages_sample=1)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(url)
        await save_incident(sid, "seo", "закрыт от поиска — robots.txt", severity="critical")
        r = await seo_checker.check_seo(url)
    assert not r.recovered
    assert [i["check_type"] for i in await get_active_incidents()] == ["seo"]
    assert r.pages_checked == 1                              # the audit itself went on
