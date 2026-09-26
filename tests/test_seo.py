from monitors.base import SeoProblem
from monitors.seo_checker import analyze_page, analyze_robots, incident_message

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
    # and a missing canonical is not reported (it would be a guess).
    assert {p.severity for p in problems} == {"critical", "warning"}
    assert "no_canonical" not in codes


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
