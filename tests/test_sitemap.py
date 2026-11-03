import asyncio

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors.seo_checker import _fetch, check_seo
from monitors.sitemap import pick_sample, sitemap_urls


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
            status, html, _ = await _fetch(session, base + "/")
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
