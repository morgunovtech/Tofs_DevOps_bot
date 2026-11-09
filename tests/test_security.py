"""Basic security: the probes' verdicts (pure), the job end to end with a
fake web, the card, the 🛡 screen and the public page keeping quiet."""

from conftest import FakeCall, buttons

from db.database import get_active_incidents, set_state
from handlers import incidents as incidents_screen
from handlers import security as security_screen
from handlers.sites import card_lines
from monitors import security_checker as sc
from reports import scheduler
from services import humanize
from web import status_page

GOOD_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Content-Type": "text/html"}


def test_service_files_are_told_apart_from_a_single_page_app():
    assert sc.looks_like_git_head("ref: refs/heads/main\n")
    assert sc.looks_like_git_head("4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    assert not sc.looks_like_git_head("<!doctype html><html>…")
    assert sc.looks_like_env("DATABASE_URL=postgres://x\nDEBUG=1\n", "text/plain")
    assert sc.looks_like_env("APP_ENV=prod\nPORT=3000\nNODE_ENV=production\n", "application/octet-stream")
    assert not sc.looks_like_env("DEBUG=1\n", "text/plain")                       # one harmless line
    assert not sc.looks_like_env("<html><body>API_KEY=</body></html>", "text/html")  # the site's own page


def test_missing_headers():
    assert sc.missing_headers(GOOD_HEADERS) == []
    assert sc.missing_headers({}) == [n for n, _, _ in sc.HEADERS]
    weak = {**GOOD_HEADERS, "Strict-Transport-Security": "max-age=0"}
    assert sc.missing_headers(weak) == ["Strict-Transport-Security"]
    csp = {k: v for k, v in GOOD_HEADERS.items() if k != "X-Frame-Options"}
    assert sc.missing_headers({**csp, "Content-Security-Policy": "frame-ancestors 'self'"}) == []


def test_the_ready_text_fits_the_hosting():
    what, block, note = sc.headers_block("cloudflare", ["X-Frame-Options", "Referrer-Policy"])
    assert "_headers" in what and block == "/*\n  X-Frame-Options: SAMEORIGIN\n  Referrer-Policy: strict-origin-when-cross-origin"
    assert "Transform Rules" in note
    what, block, _ = sc.headers_block("vercel", ["X-Content-Type-Options"])
    assert "vercel.json" in what and '"key": "X-Content-Type-Options"' in block
    assert sc.headers_block(None, ["Referrer-Policy"])[1] == "Referrer-Policy: strict-origin-when-cross-origin"
    # GitHub Pages cannot set headers: nothing to demand of the owner.
    assert sc.assess("https://ex.test", "github", False, False, False, ["Referrer-Policy"]) == []
    assert "Enforce HTTPS" in sc.assess("https://ex.test", "github", False, False, True, [])[0].fix


class FakeWeb:
    def __init__(self, monkeypatch):
        self.pages = {"https://ex.test": (200, dict(GOOD_HEADERS), "<html>hi</html>"),
                      "https://ex.test/.git/HEAD": (200, {"Content-Type": "text/html"}, "<html>spa</html>"),
                      "https://ex.test/.env": (404, {}, ""),
                      "http://ex.test/": (301, {"Location": "https://ex.test/"}, "")}
        monkeypatch.setattr(sc, "_get", self.get)

    async def get(self, session, url, redirects=False, limit=4096):
        return self.pages.get(url)


async def test_http_that_ends_on_https_is_fine(monkeypatch):
    web = FakeWeb(monkeypatch)
    assert await sc.plain_http_opens(None, "https://ex.test") is False
    web.pages["http://ex.test/"] = (302, {"Location": "http://www.ex.test/"}, "")
    web.pages["http://www.ex.test/"] = (301, {"Location": "https://www.ex.test/"}, "")
    assert await sc.plain_http_opens(None, "https://ex.test") is False
    web.pages["http://www.ex.test/"] = (200, {}, "<html>")
    assert await sc.plain_http_opens(None, "https://ex.test") is True
    del web.pages["http://ex.test/"]                                # port 80 closed: nothing to intercept
    assert await sc.plain_http_opens(None, "https://ex.test") is False


async def test_security_job_end_to_end(bot, db, monkeypatch):
    """An open .git rings; missing headers come quietly with the ready
    _headers file; both say when they are fixed; the public page is silent."""
    web = FakeWeb(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await set_state(f"hosting:{sid}", "cloudflare")
    await scheduler.run_security_checks()
    assert bot.sent == [] and await get_active_incidents() == []   # a single-page app is not a leak

    web.pages["https://ex.test/.git/HEAD"] = (200, {"Content-Type": "text/plain"}, "ref: refs/heads/main\n")
    web.pages["https://ex.test"] = (200, {"Content-Type": "text/html"}, "<html>hi</html>")
    await scheduler.run_security_checks()
    texts = {m["text"].split("\n")[0]: m for m in bot.sent}
    alarm = texts["🔴 На ex.test наружу видны служебные файлы — их может скачать любой"]
    assert alarm["silent"] is False and "Наружу виден .git" in alarm["text"]
    quiet = texts["🟠 ex.test: защиту сайта стоит усилить"]
    assert quiet["silent"] is True and "<pre>/*\n  Strict-Transport-Security: max-age=31536000" in quiet["text"]
    assert "🛡 Безопасность" in buttons(quiet["reply_markup"])
    assert {i["check_type"]: i["message"] for i in await get_active_incidents()} == {
        "exposed": "наружу виден .git",
        "security": "безопасность — не хватает 4 защитных заголовков из 4"}
    assert (await status_page.build_payload())["incidents"] == []

    n = len(bot.sent)
    await scheduler.run_security_checks()
    assert len(bot.sent) == n                                      # said once

    web.pages["https://ex.test/.git/HEAD"] = (404, {}, "")
    web.pages["https://ex.test"] = (200, dict(GOOD_HEADERS), "<html>hi</html>")
    await scheduler.run_security_checks()
    said = [m["text"] for m in bot.sent[n:]]
    assert any(t.startswith("✅ На ex.test служебные файлы больше не видны") for t in said)
    assert "✅ ex.test: защита сайта в порядке." in said
    assert await get_active_incidents() == []


async def test_card_screen_and_the_check_from_the_screen(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    web.pages["https://ex.test"] = (200, {**GOOD_HEADERS, "X-Frame-Options": ""}, "<html>hi</html>")
    await sc.check_security("https://ex.test", manage=False)
    assert await get_active_incidents() == []                      # the alert belongs to the scheduled check

    lines = await card_lines(await db.get_site(sid))
    assert "🟠 Безопасность: 1 помеха" in lines
    assert "    1. 🟠 Не хватает 1 защитного заголовка из 4" in lines

    call = FakeCall(f"sec:{sid}")
    await security_screen.cb_security(call)
    text = call.message.texts[-1]
    assert text.startswith("🛡 Безопасность ex.test")
    assert "✅ .git и .env снаружи не видны" in text and "✅ По http:// сайт перенаправляет на https://" in text
    assert "🟠 Защитные заголовки: нет 1 из 4 — X-Frame-Options" in text
    assert "ℹ️ CSP нет" in text and "<code>X-Frame-Options: SAMEORIGIN</code>" in text

    await scheduler.run_security_checks()
    [inc] = await get_active_incidents()
    assert humanize.explain(inc["check_type"], inc["message"]).key == "security"
    assert "🛡 Безопасность" in buttons(await incidents_screen.explain_keyboard(inc))
    web.pages["https://ex.test"] = (200, dict(GOOD_HEADERS), "<html>hi</html>")
    await security_screen.cb_security_check(FakeCall(f"sec_check:{sid}"))
    assert await get_active_incidents() == []
    assert security_screen.security_lines((await sc.sitestatus.get(sid))["security"]) == \
        ["✅ Безопасность: только https, защитные заголовки на месте, .git и .env снаружи не видны"]


async def test_a_site_that_does_not_answer_keeps_the_last_result(bot, db, monkeypatch):
    web = FakeWeb(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    web.pages["https://ex.test/.env"] = (200, {"Content-Type": "text/plain"}, "SECRET_KEY=x\n")
    await scheduler.run_security_checks()
    assert [i["check_type"] for i in await get_active_incidents()] == ["exposed"]
    web.pages["https://ex.test"] = (503, {}, "")
    web.pages["https://ex.test/.env"] = (404, {}, "")
    await scheduler.run_security_checks()
    assert [i["check_type"] for i in await get_active_incidents()] == ["exposed"]
