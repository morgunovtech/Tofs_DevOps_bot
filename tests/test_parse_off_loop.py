"""HTML is parsed outside the event loop: while a 2 MB page is analysed,
the availability tick, polling and /health keep running."""

import asyncio
import time

from aiohttp import web
from aiohttp.test_utils import TestServer

from db.database import get_active_incidents, save_incident
from monitors import links_checker, pagemeta, parse_worker, seo_checker
from monitors.sitemap import SitemapScan


def _big_page(target: int = 2 * 1024 * 1024) -> str:
    head = ("<html lang='ru'><head><title>Studio home page title</title>"
            "<meta name='description' content='A description that is long enough to pass the fifty chars check.'>"
            "</head><body><h1>Каталог</h1>")
    row = "<div class='item'><a href='/catalog/item-{i}'>Товар {i}</a> <span>цена {i} ₽</span></div>\n"
    parts, size, i = [head], len(head), 0
    while size < target:
        line = row.format(i=i)
        parts.append(line)
        size += len(line.encode())
        i += 1
    parts.append("</body></html>")
    return "".join(parts)


async def _worst_loop_delay(coro):
    """(the longest the event loop was late for a 10 ms timer, coro's result)."""
    delays: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            t = time.perf_counter()
            await asyncio.sleep(0.01)
            delays.append(time.perf_counter() - t - 0.01)

    task = asyncio.create_task(ticker())
    try:
        result = await coro
    finally:
        stop.set()
        await task
    return max(delays), result


async def _unread():
    return SitemapScan()


async def test_seo_audit_of_a_2mb_page_does_not_stall_the_loop(db, cfg, monkeypatch):
    page = _big_page()
    plain_home_requests = 0

    async def home(request):
        nonlocal plain_home_requests
        if "Bot/" not in request.headers.get("User-Agent", ""):
            plain_home_requests += 1
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", home)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(seo_checker, "scan_sitemap", lambda session, base: _unread())
    cfg(seo_pages_sample=1)
    try:
        delay, r = await _worst_loop_delay(
            seo_checker.check_seo(f"http://127.0.0.1:{server.port}", manage=False))
    finally:
        await server.close()
        await parse_worker.close()
    assert r.pages_checked == 1                 # the page really was analysed
    assert r.no_js_chars and r.no_js_chars > 100_000
    assert delay < 0.3, f"event loop stood still for {delay:.2f} s"
    assert plain_home_requests == 1             # the probe's answer is the homepage sample


async def test_seo_problems_come_back_whole_from_the_child():
    html = ("<html><head><meta name='robots' content='noindex'></head>"
            "<body><h1>x</h1><p>текст</p></body></html>")
    try:
        problems, text_len = await parse_worker.seo_page(html, "https://ex.test/", {"X-Robots-Tag": "noindex"},
                                                         is_home=True)
    finally:
        await parse_worker.close()
    expected, expected_len = seo_checker.analyze_page(html, "https://ex.test/", {"X-Robots-Tag": "noindex"},
                                                      is_home=True)
    assert problems == expected and text_len == expected_len
    # is_home survived the hop: both noindex findings are 🔴, as on the homepage
    assert {p.code: p.severity for p in problems}["noindex_meta"] == "critical"
    assert {p.code: p.severity for p in problems}["noindex_header"] == "critical"


async def test_a_homepage_the_parser_cannot_read_leaves_the_audit_incomplete(db, cfg, monkeypatch):
    """The child's failure is a page not read, not a crash: the audit goes
    on, but without the homepage it has no verdict and closes nothing."""
    async def home(_):
        return web.Response(text="<html><body><h1>Кофе</h1></body></html>", content_type="text/html")

    async def robots(_):
        return web.Response(text="User-agent: *\nAllow: /\n")

    async def broken(*args, **kwargs):
        raise parse_worker.ParseError("seo_page: RecursionError")

    app = web.Application()
    app.router.add_get("/", home)
    app.router.add_get("/robots.txt", robots)
    monkeypatch.setattr(seo_checker, "scan_sitemap", lambda session, base: _unread())
    monkeypatch.setattr(parse_worker, "seo_page", broken)
    cfg(seo_pages_sample=1)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(url)
        await save_incident(sid, "seo", "закрыт от поиска — robots.txt", severity="critical")
        r = await seo_checker.check_seo(url)
    assert r.pages_checked == 0 and not r.recovered
    assert [i["check_type"] for i in await get_active_incidents()] == ["seo"]


async def test_links_of_a_2mb_page_are_extracted_off_the_loop():
    page = _big_page()
    try:
        delay, links = await _worst_loop_delay(parse_worker.links(page, "https://example.com/"))
    finally:
        await parse_worker.close()
    assert links == links_checker.extract_links(page, "https://example.com/")
    assert delay < 0.3, f"event loop stood still for {delay:.2f} s"


async def test_a_page_the_parser_cannot_read_is_a_transient_crawl_error(db, monkeypatch):
    async def home(_):
        return web.Response(text="<a href='/x'>x</a>", content_type="text/html")

    async def broken(*args):
        raise parse_worker.ParseError("links: TimeoutError")

    app = web.Application()
    app.router.add_get("/", home)
    monkeypatch.setattr(parse_worker, "links", broken)
    async with TestServer(app) as server:
        r = await links_checker.check_links(str(server.make_url("")).rstrip("/"))
    assert r.status == "error" and r.transient and r.error == "Could not parse page"
    assert await get_active_incidents() == []


async def test_a_parse_that_overruns_is_killed_and_the_next_one_works(monkeypatch):
    monkeypatch.setattr(parse_worker, "TIMEOUT_SEC", 0.05)
    try:
        try:
            await parse_worker.links(_big_page(), "https://example.com/")
        except parse_worker.ParseError:
            pass
        else:
            raise AssertionError("a 2 MB parse finished within 50 ms")
        assert parse_worker._child.proc is None          # killed, not left half-answered
        monkeypatch.setattr(parse_worker, "TIMEOUT_SEC", 30)
        assert await parse_worker.links("<a href='/x'>x</a>", "https://example.com/") == ["https://example.com/x"]
    finally:
        await parse_worker.close()


async def test_a_page_that_breaks_the_parser_is_an_error_not_a_dead_child():
    """An exception inside analyze_page comes back as ParseError and the
    same child answers the next request."""
    try:
        try:
            await parse_worker._child.call("no_such_op")
        except parse_worker.ParseError as e:
            assert "KeyError" in str(e)
        proc = parse_worker._child.proc
        assert proc is not None and proc.returncode is None
        assert await parse_worker.phrase("<h1>Кофейня у дома</h1>") == "Кофейня у дома"
        assert parse_worker._child.proc is proc
    finally:
        await parse_worker.close()


async def test_an_idle_child_exits_and_a_new_one_starts_on_demand(monkeypatch):
    monkeypatch.setattr(parse_worker, "IDLE_SEC", 0.05)
    assert await parse_worker.phrase("<h1>Кофейня у дома</h1>") == "Кофейня у дома"
    proc = parse_worker._child.proc
    await asyncio.wait_for(proc.wait(), 5)            # gone without being asked
    assert await parse_worker.phrase("<title>Кофейня у дома</title>") == "Кофейня у дома"
    assert parse_worker._child.proc is not proc


def test_pick_phrase_is_what_suggest_keyword_used_to_compute():
    assert pagemeta.pick_phrase("<html><head><title>Студия Морг</title></head><body><h1>Студия Морг</h1>"
                                "</body></html>") == "Студия Морг"
    assert pagemeta.pick_phrase("<title>Untitled</title>") is None
