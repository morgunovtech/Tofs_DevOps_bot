from monitors.seo_checker import analyze_page, analyze_robots

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
    problems, infos, text_len = analyze_page(html, "https://example.com/page",
                                             {"X-Robots-Tag": "noindex"})
    codes = [p.code for p in problems]
    assert codes.count("noindex_header") == 1 and codes.count("noindex_meta") == 1
    assert {"no_title", "no_description", "jsonld_invalid"} <= set(codes)
    assert all(p.message.startswith("/page: ") for p in problems if p.code != "noindex_header")
    assert text_len == len("Hello world text here")
    assert any(i.code == "no_canonical" for i in infos) and all(i.severity == "info" for i in infos)


def test_analyze_page_clean():
    html = """<html lang="ru"><head><title>Good title for the page</title>
    <meta name="description" content="A description that is long enough to satisfy the fifty char rule.">
    <link rel="canonical" href="https://example.com/"><meta property="og:title" content="x">
    <meta property="og:image" content="y"></head><body><h1>Hi</h1></body></html>"""
    problems, infos, _ = analyze_page(html, "https://example.com/")
    assert problems == [] and infos == []
