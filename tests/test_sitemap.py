import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

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
