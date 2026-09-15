"""Stage 3 automation: hosting detection, registrar links, alert
follow-ups, keyword suggestion, www twin, quiet first SEO day, weekly tip."""

from datetime import timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall

from db.database import get_state
from handlers import sites
from monitors import pagemeta
from monitors.availability import check_availability
from reports import scheduler
from services import hosting, humanize, notifier, recommend, settings
from services.actions import alert_actions_keyboard, domain_keyboard
from services.notifier import Priority


def test_detect_hosting():
    assert hosting.detect_hosting({"Server": "cloudflare", "CF-RAY": "abc"}) == "cloudflare"
    assert hosting.detect_hosting({"x-vercel-id": "iad1::x"}) == "vercel"
    assert hosting.detect_hosting({"X-NF-Request-ID": "1"}) == "netlify"
    assert hosting.detect_hosting({"Server": "GitHub.com"}) == "github"
    assert hosting.detect_hosting({"x-railway-request-id": "1", "server": "railway-edge"}) == "railway"
    assert hosting.detect_hosting({"Via": "1.1 vegur"}) == "heroku"
    assert hosting.detect_hosting({"Server": "nginx/1.25"}) is None
    assert hosting.detect_hosting(None) is None
    assert hosting.panel_url("vercel").startswith("https://vercel.com") and hosting.label("netlify") == "Netlify"


def test_registrar_links_and_keyboards():
    assert humanize.registrar_url("Cloudflare, Inc.").startswith("https://dash.cloudflare.com")
    assert humanize.registrar_url("REGRU-RU").startswith("https://www.reg.ru")
    assert humanize.registrar_url("NameCheap, Inc.").startswith("https://ap.www.namecheap.com")
    assert humanize.registrar_url("Unknown") is None and humanize.registrar_url("") is None
    kb = alert_actions_keyboard("https://ex.com", 1, 7, hosting="cloudflare")
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "🔧 Я чиню, час тишины" in labels and "ℹ️ Что делать" in labels
    assert any("панель Cloudflare" in lbl for lbl in labels)
    assert kb.inline_keyboard[-1][0].url == "https://dash.cloudflare.com/"
    assert domain_keyboard("GoDaddy.com, LLC").inline_keyboard[0][0].url.startswith("https://dcc.godaddy.com")
    assert domain_keyboard("Some Local Registrar") is None


async def test_alert_followups_edit_the_same_message(bot, db, monkeypatch):
    class FakeSent:
        message_id = 4242

    async def fake_send(*a, **k):
        return FakeSent()

    edits = []

    async def fake_edit(message_id, text, reply_markup=None):
        edits.append((message_id, text))
        return True

    monkeypatch.setattr(notifier, "send", fake_send)
    monkeypatch.setattr(notifier, "edit", fake_edit)
    monkeypatch.setattr(scheduler, "_scheduler", None)          # no APScheduler in tests
    # Message class check in the scheduler is on aiogram's Message; bypass via direct call:
    async def h(_):
        return web.Response(status=503, text="down")
    app = web.Application()
    app.router.add_get("/", h)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.get_or_create_site(url)
        kb = alert_actions_keyboard(url, sid, None)
        await scheduler._followup(url, sid, 4242, "🔴 alert", kb, 2)
        assert edits and "всё ещё не открывается" in edits[-1][1] and "ошибкой 503" in edits[-1][1]
    async with TestServer(app) as server:
        pass
    app2 = web.Application()
    app2.router.add_get("/", lambda _: web.Response(text="ok"))
    async with TestServer(app2) as server:
        url2 = str(server.make_url("")).rstrip("/")
        sid2 = await db.get_or_create_site(url2)
        await scheduler._followup(url2, sid2, 4242, "🔴 alert", None, 5)
        assert "уже открывается" in edits[-1][1]


async def test_hosting_is_remembered_per_site(db):
    async def h(_):
        return web.Response(text="ok", headers={"x-vercel-id": "iad1::abc"})
    app = web.Application()
    app.router.add_get("/", h)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        r = await check_availability(url, manage=False)
        assert r.hosting == "vercel" and await get_state(f"hosting:{r.site_id}") == "vercel"


async def test_keyword_suggestion_and_www_note():
    async def page(_):
        return web.Response(text="<html><head><title>Студия Морг — сайты</title></head>"
                                 "<body><h1>Студия Морг</h1></body></html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/", page)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        assert await pagemeta.suggest_keyword(url) == "Студия Морг"
        assert await pagemeta.alt_host_note(url) is None              # IP host → nothing to say
    assert await pagemeta.suggest_keyword("http://127.0.0.1:1/") is None


async def test_keyword_accept_flow(bot, db):
    sid = await db.activate_or_create_site("https://kw.test")
    await db.set_state(f"kw_suggest:{sid}", "Привет мир")
    call = FakeCall(f"kwok:{sid}")
    await sites.cb_keyword_accept(call)
    site = await db.get_site(sid)
    assert site["keyword"] == "Привет мир" and site["keyword_mode"] == "present"
    assert "Слежу" in call.message.texts[-1]
    call = FakeCall(f"kwok:{sid}")                                      # suggestion consumed
    await sites.cb_keyword_accept(call)
    assert call.answers[-1] and "устарела" in call.answers[-1]


async def test_seo_grace_skips_alerts_for_fresh_sites(bot, db, monkeypatch):
    fresh_url, old_url = "https://fresh.test", "https://old.test"
    await db.activate_or_create_site(fresh_url)
    old_id = await db.activate_or_create_site(old_url)
    conn = await db.get_db()
    await conn.execute("UPDATE sites SET added_at = datetime('now', '-3 days') WHERE id = ?", (old_id,))
    await conn.commit()
    calls = []

    async def fake_check_all_seo(urls, manage=True):
        calls.append((sorted(urls), manage))
        return []

    monkeypatch.setattr(scheduler, "check_all_seo", fake_check_all_seo)
    await scheduler.run_seo_checks()
    assert ([fresh_url], False) in calls and ([old_url], True) in calls


async def test_weekly_recommendation_rotates(bot, db):
    await db.activate_or_create_site("https://ex.test")
    first = await recommend.weekly_recommendation()
    assert first and first[0].startswith("💡 Одна идея на неделю") and first[1].inline_keyboard
    second = await recommend.weekly_recommendation()
    assert second and second[0] != first[0]                               # not the same twice in a row
    await settings.set_status_page(True)
    texts = {(await recommend.weekly_recommendation())[0] for _ in range(6)}
    assert not any("статус-страниц" in t for t in texts)
    await settings.set_status_page(False)


async def test_send_returns_message_like_and_edit(bot):
    result = await notifier.send("hi", Priority.NORMAL)
    assert getattr(result, "message_id", None) == 1
    assert await notifier.edit(1, "hi, edited") and bot.edited[-1]["text"] == "hi, edited"
    await notifier.set_mute(timedelta(hours=1))
    assert await notifier.send("queued", Priority.NORMAL) is True    # queued, still truthy
    await notifier.set_mute(None)
