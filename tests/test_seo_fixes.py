from monitors.seo_checker import AI_BOTS, SEARCH_BOTS  # noqa: F401  (import guards the module)
from services import seo_fixes


def test_every_fix_has_headline_cost_and_steps():
    for code, f in seo_fixes.FIXES.items():
        assert f.code == code and f.title and f.icon and f.steps, code
        for hosting, steps in f.by_hosting.items():
            assert steps and hosting in ("cloudflare", "vercel", "netlify", "github"), (code, hosting)


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
