from aiohttp import web
from aiohttp.test_utils import TestServer

from monitors import seo_checker
from monitors.base import SeoProblem
from monitors.seo_checker import analyze_page, analyze_robots, incident_message
from monitors.sitemap import SitemapScan

ROBOTS = """User-agent: *
Allow: /
User-agent: GPTBot
Disallow: /
User-agent: Googlebot
Disallow: /
Sitemap: https://example.com/sitemap.xml
"""


def test_analyze_robots():
    problems, sitemaps = analyze_robots(ROBOTS, "https://example.com")
    by_code = {p.code: p for p in problems}
    assert by_code["robots_search"].severity == "critical" and "Googlebot" in by_code["robots_search"].message
    assert by_code["robots_ai"].severity == "warning" and "GPTBot" in by_code["robots_ai"].message
    assert sitemaps == ["https://example.com/sitemap.xml"]


def test_analyze_page_flags_noindex_and_missing_meta():
    html = """<html><head><meta name="robots" content="noindex">
    <script type="application/ld+json">{not json}</script></head>
    <body><p>Hello world text here</p><script>var x = 1;</script></body></html>"""
    problems, text_len = analyze_page(html, "https://example.com/page",
                                      {"X-Robots-Tag": "noindex"})
    codes = [p.code for p in problems]
    assert codes.count("noindex_header") == 1 and codes.count("noindex_meta") == 1
    assert {"no_title", "no_description", "jsonld_invalid", "no_h1"} <= set(codes)
    assert all(p.message.startswith("/page: ") for p in problems if p.code != "noindex_header")
    assert text_len == len("Hello world text here")
    # Every finding is either critical or an action for later — no «info» bucket,
    # and a missing canonical is not reported (it would be a guess). noindex on
    # an inner page is often deliberate (/privacy): 🟠, not a 3 a.m. call.
    assert {p.severity for p in problems} == {"warning"}
    assert "no_canonical" not in codes
    home, _ = analyze_page(html, "https://example.com/", {"X-Robots-Tag": "noindex"})
    assert {p.severity for p in home if p.code.startswith("noindex")} == {"critical"}


def test_page_level_findings_are_warnings_with_an_action():
    """title/description length, h1, og:*, lang used to be «info» (praise and
    nags in one bucket). Each is now a 🟠 with a fix, or not reported at all."""
    html = ("<html><head><title>" + "x" * 90 + "</title>"
            "<meta name=\"description\" content=\"short\"></head><body><p>text</p></body></html>")
    problems, _ = analyze_page(html, "https://example.com/")
    by_code = {p.code: p for p in problems}
    assert by_code["title_len"].severity == "warning" and "90 символов" in by_code["title_len"].message
    assert by_code["desc_len"].severity == "warning" and "5 символов" in by_code["desc_len"].message
    assert {"no_h1", "og_title", "og_image", "no_lang"} <= by_code.keys()
    assert all(p.severity == "warning" for p in problems)


def test_analyze_page_clean():
    html = """<html lang="ru"><head><title>Good title for the page</title>
    <meta name="description" content="A description that is long enough to satisfy the fifty char rule.">
    <link rel="canonical" href="https://example.com/"><meta property="og:title" content="x">
    <meta property="og:image" content="y"></head><body><h1>Hi</h1></body></html>"""
    problems, _ = analyze_page(html, "https://example.com/")
    assert problems == []


def test_incident_message_leads_with_the_critical_finding():
    """The incident line carries the level: «закрыт от поиска — …» is what
    humanize.classify reads as critical, whatever came first in the scan."""
    warn = SeoProblem("sitemap_missing", "warning", "нет карты сайта (sitemap.xml)")
    crit = SeoProblem("noindex_meta", "critical", "главная: в коде стоит meta robots noindex")
    assert incident_message([warn, crit]) == "закрыт от поиска — В коде страницы стоит запрет на индексацию"
    assert incident_message([warn, SeoProblem("no_h1", "warning", "x")]) == \
        "2 помехи в поиске, напр.: Нет карты сайта (sitemap.xml)"


async def test_ai_probe_reports_a_signature_refusal_in_its_own_words(db, cfg, monkeypatch):
    """The plain request passes, the signed ones do not: 403 for GPTBot, 402
    (Cloudflare's pay-per-crawl) for ClaudeBot. The finding says «запрос с
    подписью», never that the real bots were seen."""
    page = ("<html lang='ru'><head><title>Магазин кофе в Ташкенте</title>"
            "<meta name='description' content='Свежая обжарка каждый день, доставка по городу за час.'>"
            "</head><body><h1>Кофе</h1>" + "<p>текст страницы</p>" * 60 + "</body></html>")

    async def home(request):
        ua = request.headers.get("User-Agent", "")
        if "GPTBot" in ua:
            return web.Response(status=403)
        if "ClaudeBot" in ua:
            return web.Response(status=402)
        return web.Response(text=page, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", home)
    server = TestServer(app)
    await server.start_server()
    monkeypatch.setattr(seo_checker, "scan_sitemap", lambda session, base: _unread())
    cfg(seo_pages_sample=1)
    try:
        r = await seo_checker.check_seo(f"http://127.0.0.1:{server.port}", manage=False)
    finally:
        await server.close()
    found = {p.code: p.message for p in r.problems}
    assert found["ai_blocked"] == ("обычный запрос сайт пропустил, а запрос с подписью "
                                   "GPTBot (HTTP 403), ClaudeBot (HTTP 402) — нет")
    assert "PerplexityBot" not in found["ai_blocked"]


async def _unread():
    return SitemapScan()
