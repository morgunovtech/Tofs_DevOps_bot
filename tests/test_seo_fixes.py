from monitors.seo_checker import AI_BOTS, SEARCH_BOTS  # noqa: F401  (import guards the module)
from services import seo_fixes


def test_every_fix_has_headline_cost_and_steps():
    for code, f in seo_fixes.FIXES.items():
        assert f.code == code and f.title and f.icon and f.steps, code
        for hosting, steps in f.by_hosting.items():
            assert steps and hosting in ("cloudflare", "vercel", "netlify", "github"), (code, hosting)


def test_every_fix_is_an_action():
    """No «nothing to do» entries: facts of normal operation (robots.txt
    absent, port 80 closed, llms.txt present or not) are not findings."""
    assert not {"robots_missing", "http_closed", "llms_ok", "llms_missing", "no_canonical"} & seo_fixes.FIXES.keys()
    for code, f in seo_fixes.FIXES.items():
        text = " ".join((f.why, *f.steps, *(s for steps in f.by_hosting.values() for s in steps)))
        assert "Ничего делать не нужно" not in text and "не страшно" not in text, code
    for code in ("title_len", "desc_len", "no_h1", "og_title", "og_image", "no_lang"):
        f = seo_fixes.fix(code)
        assert f.short and len(f.steps) >= 2 and "Проверить снова" in f.steps[-1], code


def test_steps_follow_the_hosting_and_fill_placeholders():
    generic = seo_fixes.steps("soft404", None, "https://ex.com/")
    cf = seo_fixes.steps("soft404", "cloudflare", "https://ex.com/")
    unknown = seo_fixes.steps("soft404", "beget", "https://ex.com/")
    assert generic != cf and unknown == generic
    assert any("_redirects" in s for s in cf)
    assert any("https://ex.com/sitemap.xml" in s for s in seo_fixes.steps("sitemap_missing", None, "https://ex.com"))
    assert "{url}" not in " ".join(seo_fixes.steps("robots_missing", None, "https://ex.com"))


def test_links_add_the_panel_only_when_steps_are_hosting_specific():
    plain = seo_fixes.links("soft404", None, "https://ex.com")
    assert plain == [("🔗 Открыть несуществующую страницу", "https://ex.com/takoy-stranicy-net")]
    cf = seo_fixes.links("soft404", "cloudflare", "https://ex.com")
    assert cf[-1][0] == "🔗 Открыть панель Cloudflare"
    assert seo_fixes.links("https_redirect", None, "https://ex.com")[0][1] == "http://ex.com/"


def test_unknown_code_degrades_gracefully():
    f = seo_fixes.fix("something_new")
    assert f.title == "something_new" and seo_fixes.steps("something_new", "vercel", "https://x.io")
    assert seo_fixes.button_label("no_js").startswith("💡 ")
