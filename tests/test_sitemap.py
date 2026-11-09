import asyncio
import json
from datetime import UTC, datetime, timedelta

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors import deep_checker
from monitors.base import LinkCheck, LinksResult
from monitors.links_checker import check_links, is_definite
from monitors.seo_checker import _fetch, check_seo
from monitors.sitemap import pick_sample, sitemap_urls
from reports.scheduler import _links_recovered
from services import sitestatus


def test_pick_sample():
    assert pick_sample(list(range(10)), 3) == [0, 3, 6]
    assert pick_sample([1, 2], 5) == [1, 2]
    assert pick_sample([1, 2], 0) == []


async def test_sitemap_index_is_followed_and_unescaped():
    async def index(_):
        return web.Response(text="""<sitemapindex><sitemap><loc>{base}/a.xml</loc></sitemap>
            <sitemap><loc>{base}/b.xml</loc></sitemap></sitemapindex>""".replace("{base}", base))

    async def a(_):
        return web.Response(text=f"<urlset><url><loc>{base}/p?x=1&amp;y=2</loc></url></urlset>")

    async def b(_):
        return web.Response(text=f"<urlset><url><loc>{base}/q</loc></url>"
                                 "<url><loc>https://other.example/z</loc></url></urlset>")

    app = web.Application()
    app.router.add_get("/sitemap.xml", index)
    app.router.add_get("/a.xml", a)
    app.router.add_get("/b.xml", b)
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as session:
            urls = await sitemap_urls(session, base)
    assert urls == [f"{base}/p?x=1&y=2", f"{base}/q"]   # nested locs unescaped, foreign host dropped


async def test_a_body_that_arrives_in_pieces_is_read_whole():
    """Production saw «нет карты сайта» and «нет h1» on sites that had both:
    StreamReader.read(n) returned only the first chunk of a page. Here the
    server sends the sitemap and the page in slow pieces."""
    async def slow(request, body: str):
        resp = web.StreamResponse()
        await resp.prepare(request)
        for i in range(0, len(body), 40):
            await resp.write(body[i:i + 40].encode())
            await asyncio.sleep(0.01)
        await resp.write_eof()
        return resp

    urls = "".join(f"<url><loc>{{base}}/p{i}</loc></url>" for i in range(5))
    page = "<html><body>" + "<p>текст</p>" * 30 + "<h1>Заголовок</h1></body></html>"
    app = web.Application()
    app.router.add_get("/sitemap.xml", lambda r: slow(r, f"<urlset>{urls}</urlset>".replace("{base}", base)))
    app.router.add_get("/", lambda r: slow(r, page))
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")
        async with aiohttp.ClientSession() as session:
            assert await sitemap_urls(session, base) == [f"{base}/p{i}" for i in range(5)]
            status, html, _, _ = await _fetch(session, base + "/")
    assert status == 200 and html.endswith("<h1>Заголовок</h1></body></html>")


async def test_sitemap_is_missing_only_when_the_site_says_so(db):
    """A 404 is an answer; a 503 or a timeout is not — the audit must not
    report «нет карты сайта» because the server hiccuped."""
    answer = {"status": 503}

    async def sitemap(_):
        return web.Response(status=answer["status"])

    app = web.Application()
    app.router.add_get("/", lambda r: web.Response(text="<html><head><title>Главная страница</title></head>"
                                                        "<body><h1>Привет</h1></body></html>",
                                                   content_type="text/html"))
    app.router.add_get("/sitemap.xml", sitemap)
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")
        codes = [p.code for p in (await check_seo(base, manage=False)).problems]
        assert "sitemap_missing" not in codes
        answer["status"] = 404
        codes = [p.code for p in (await check_seo(base, manage=False)).problems]
        assert "sitemap_missing" in codes


async def test_links_are_judged_by_how_sure_the_answer_is(db):
    """A 404 is a problem at once, own site or not; a 502 only when it still
    fails three hours after the first time — a restart minutes later is not
    «later»; a bot block (403) never."""
    other = web.Application()
    other.router.add_get("/gone", lambda r: web.Response(status=404))
    other.router.add_get("/flaky", lambda r: web.Response(status=502))
    other.router.add_get("/blocked", lambda r: web.Response(status=403))
    async with TestServer(other) as ext:
        ext_base = str(ext.make_url("")).rstrip("/").replace("127.0.0.1", "localhost")
        page = (f'<html><body><a href="{ext_base}/gone">a</a><a href="{ext_base}/flaky">b</a>'
                f'<a href="{ext_base}/blocked">c</a><link rel="preconnect" href="{ext_base}/"></body></html>')
        site = web.Application()
        site.router.add_get("/", lambda r: web.Response(text=page, content_type="text/html"))
        async with TestServer(site) as own:
            base = str(own.make_url("")).rstrip("/")
            gone, flaky = f"{ext_base}/gone", f"{ext_base}/flaky"
            first = await check_links(base)
            assert [b.url for b in first.broken] == [gone]
            assert [p.url for p in first.pending] == [flaky] and first.incident_new
            # Minutes later — a menu tap, a crawl after a restart — is not
            # «later»: the 502 is still only a suspect.
            ui = await check_links(base, manage=False)
            assert [b.url for b in ui.broken] == [gone] and [p.url for p in ui.pending] == [flaky]
            again = await check_links(base)
            assert [p.url for p in again.pending] == [flaky]
            key = f"links_suspect:{first.site_id}"
            state = json.loads(await db.get_state(key))
            state[flaky] = (datetime.now(UTC) - timedelta(hours=3, minutes=1)).isoformat()
            await db.set_state(key, json.dumps(state))
            second = await check_links(base)
            assert sorted(b.url for b in second.broken) == [flaky, gone]
            assert second.pending == []
            # The list kept before 11.2026 had no times: its links start
            # the clock now instead of counting as confirmed.
            await db.set_state(key, json.dumps([flaky]))
            third = await check_links(base)
            assert [p.url for p in third.pending] == [flaky]
            assert flaky in json.loads(await db.get_state(key))
    assert is_definite(LinkCheck("https://x", None, False, error="[SSL: CERTIFICATE_VERIFY_FAILED] certificate"))
    assert not is_definite(LinkCheck("https://x", None, False, error="timeout"))


def test_closing_a_links_alert_does_not_claim_a_failing_link_works():
    r = LinksResult(url="https://s.example", site_id=1)
    assert _links_recovered(r) == "✅ На s.example все ссылки снова работают."
    r.pending = [LinkCheck("https://github.com/x/y", 504, False)]
    text = _links_recovered(r)
    assert "снимаю тревогу" in text and "1 ссылка сейчас не открывается" in text and "временный сбой" in text



async def test_deep_check_keeps_each_page_with_its_mark(db, monkeypatch):
    """The card lists the pages it checked, each with ✅ or 🔴 — so the
    snapshot keeps their paths and errors, not only a count."""
    async def page(request):
        return web.Response(status=502 if request.path == "/broken" else 200, text="ok")

    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    async with TestServer(app) as server:
        base = str(server.make_url("")).rstrip("/")            # http://127.0.0.1:port
        other = f"http://localhost:{server.port}/x"             # another host, still this test server

        async def fake_sitemap(session, url):
            return [f"{base}/", f"{base}/broken", f"{base}/about?lang=en", other]

        monkeypatch.setattr(deep_checker, "sitemap_urls", fake_sitemap)
        r = await deep_checker.check_deep(base)
    deep = (await sitestatus.get(r.site_id))["deep"]
    assert deep["sampled"] == 4 and deep["errors"] == 1
    assert deep["pages"] == [["/", None], ["/broken", 502], ["/about?lang=en", None], [other, None]]
