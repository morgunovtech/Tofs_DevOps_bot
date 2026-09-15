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
    messages = {p.severity: p.message for p in problems}
    assert "critical" in messages and "Googlebot" in messages["critical"]
    assert any("GPTBot" in p.message and p.severity == "warning" for p in problems)
    assert sitemaps == ["https://example.com/sitemap.xml"]


def test_analyze_page_flags_noindex_and_missing_meta():
    html = """<html><head><meta name="robots" content="noindex">
    <script type="application/ld+json">{not json}</script></head>
    <body><p>Hello world text here</p><script>var x = 1;</script></body></html>"""
    problems, infos, text_len = analyze_page(html, "https://example.com/page",
                                             {"X-Robots-Tag": "noindex"})
    kinds = [p.message for p in problems]
    assert sum("noindex" in m for m in kinds) == 2
    assert any("нет <title>" in m for m in kinds)
    assert any("description" in m for m in kinds)
    assert any("JSON-LD" in m for m in kinds)
    assert text_len == len("Hello world text here")
    assert any("canonical" in i for i in infos)


def test_analyze_page_clean():
    html = """<html lang="ru"><head><title>Good title for the page</title>
    <meta name="description" content="A description that is long enough to satisfy the fifty char rule.">
    <link rel="canonical" href="https://example.com/"><meta property="og:title" content="x">
    <meta property="og:image" content="y"></head><body><h1>Hi</h1></body></html>"""
    problems, infos, _ = analyze_page(html, "https://example.com/")
    assert problems == [] and infos == []
