"""API checks: an endpoint watched for its answer only — never audited as
a web page, and never a second certificate alert next to its own site."""

import json

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import get_active_incidents, save_incident
from handlers import links, menu, site_settings, sites
from monitors import availability
from monitors.second_opinion import repeatable
from reports import scheduler
from reports.formatter import format_compact_status_report
from services import integrations, sitestatus
from utils.urls import is_api_check, is_api_url, is_web_page, site_label

GOOD_PASS = "https://good-pass.pages.dev"
ANALYZE = "https://good-pass.pages.dev/api/v1/analyze"
WHOAMI = "https://fingerprint-api.xfxrrpkvr5.workers.dev/api/whoami"


def test_the_rule():
    """One rule: not GET/HEAD, or an /api address, or an api.* host."""
    assert is_api_check(ANALYZE) and is_api_check(WHOAMI)
    assert is_api_check("https://api.example.com") and is_api_check("https://example.com/API/v2")
    assert is_api_check("https://example.com", "POST") and is_api_check("https://example.com", "put")
    assert not is_api_check("https://example.com") and not is_api_check("https://example.com", "HEAD")
    assert not is_api_check("https://example.com/apis") and not is_api_check("https://rapid.example.com")
    assert not is_api_check("tcp://example.com:25") and not is_api_check("ping://10.0.0.1")
    assert is_web_page("https://example.com") and not is_web_page(ANALYZE)
    assert not is_web_page("https://example.com", "POST") and not is_web_page("tcp://example.com:25")
    assert is_api_url(ANALYZE) and not is_api_url("https://example.com/")
    # The endpoint must not look like its site; a query (maybe a token) is never shown.
    assert site_label(ANALYZE) == "good-pass.pages.dev/api/v1/analyze"
    assert site_label("https://ex.com/api/x?key=secret") == "ex.com/api/x" and site_label("https://ex.com/") == "ex.com"


def test_add_keeps_the_api_path_and_drops_a_page_path():
    parse = sites.parse_site_input
    assert parse("good-pass.pages.dev/api/v1/analyze") == (ANALYZE, None)
    assert parse("HTTPS://Fingerprint-API.example.com/api/WhoAmI?x=1") == \
        ("https://fingerprint-api.example.com/api/WhoAmI?x=1", None)    # paths are case-sensitive
    assert parse("api.example.com/") == ("https://api.example.com", None)
    assert parse("example.com/blog/post") == ("https://example.com", None)   # a page is its site
    assert parse("example.com/api/a b")[0] is None


def test_second_opinion_only_for_a_request_it_can_repeat():
    assert repeatable({"url": WHOAMI}) and repeatable({"url": "tcp://ex.com:25"})
    assert not repeatable({"url": ANALYZE, "http_method": "POST"})
    assert not repeatable({"url": WHOAMI, "http_headers": json.dumps({"Authorization": "Bearer t"})})


def test_json_body_goes_as_json():
    """aiohttp sends a str as text/plain — good-pass answers that with 400."""
    _, headers, body = availability.request_options({"http_method": "POST", "http_body": '{"password": "x"}'})
    assert headers["Content-Type"] == "application/json" and body == '{"password": "x"}'
    _, headers, _ = availability.request_options({"http_method": "POST", "http_body": "a=1"})
    assert "Content-Type" not in headers
    _, headers, _ = availability.request_options({"http_method": "POST", "http_body": "{}",
                                                  "http_headers": json.dumps({"content-type": "text/x"})})
    assert headers == {"User-Agent": headers["User-Agent"], "content-type": "text/x"}


async def _monitors(db):
    page = await db.activate_or_create_site(GOOD_PASS)
    api = await db.activate_or_create_site(ANALYZE)
    await db.update_site_settings(api, http_method="POST", http_body='{"password":"x"}', keyword="scoreLabel")
    who = await db.activate_or_create_site(WHOAMI)
    await db.activate_or_create_site(WHOAMI.replace("whoami", "scan"))
    posted = await db.activate_or_create_site("https://worker.example.com")   # POST at the root
    await db.update_site_settings(posted, http_method="POST")
    await db.activate_or_create_site("tcp://mail.example.com:25")
    return page, api, who


async def test_page_checks_never_see_an_api_and_host_checks_dedupe(db):
    await _monitors(db)
    assert await db.get_active_page_urls() == [GOOD_PASS]
    # The site covers its endpoint's certificate; the worker host has only API
    # checks — the first of them stands for the host, once.
    assert await db.get_active_host_urls() == [GOOD_PASS, WHOAMI, "https://worker.example.com"]


async def test_scheduled_page_checks_skip_api_and_close_its_old_findings(bot, db, monkeypatch):
    _, api, _ = await _monitors(db)
    await save_incident(api, "links", "2 broken link(s)", "warning")     # from when it was a page
    seen: dict[str, list[str]] = {}

    def capture(name):
        async def fake(urls, manage=True):
            seen.setdefault(name, []).extend(urls)    # the SEO audit calls twice: fresh sites quietly
            return []
        return fake
    for name in ("check_all_links", "check_all_deep", "check_all_seo", "check_all_ssl", "check_all_domains"):
        monkeypatch.setattr(scheduler, name, capture(name))
    await scheduler.run_links_checks()
    await scheduler.run_deep_checks()
    await scheduler.run_seo_checks()
    await scheduler.run_ssl_checks()
    await scheduler.run_domain_checks()
    assert seen["check_all_links"] == seen["check_all_deep"] == seen["check_all_seo"] == [GOOD_PASS]
    assert ANALYZE not in seen["check_all_ssl"] and ANALYZE not in seen["check_all_domains"]
    assert not [i for i in await get_active_incidents() if i["check_type"] == "links"]
    assert bot.sent == []                                       # closed silently: nothing was fixed


async def test_post_api_check_end_to_end(bot, db, monkeypatch):
    seen: list = []

    async def analyze(request: web.Request):
        seen.append((request.method, request.headers.get("Content-Type"), await request.text()))
        if request.method != "POST" or request.content_type != "application/json":
            return web.json_response({"error": "Пароль должен быть строкой."}, status=400)
        return web.json_response({"scoreLabel": "Сильный"})
    app = web.Application()
    app.router.add_route("*", "/api/v1/analyze", analyze)
    asked = []

    async def no_second_opinion(url):
        asked.append(url)
        return True
    monkeypatch.setattr(availability, "second_opinion_up", no_second_opinion)
    await integrations.set_value("second_opinion", "on")
    async with TestServer(app) as server:
        url = str(server.make_url("/api/v1/analyze"))
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, http_method="POST", http_body='{"password":"Tr0ub4dor&3"}',
                                      keyword="scoreLabel", fail_threshold=1)
        r = await availability.check_availability(url)
        assert r.ok and r.api and seen[-1][:2] == ("POST", "application/json")
        await db.update_site_settings(sid, keyword="nope")
        scheduler._next_avail_check.clear()
        await scheduler.run_availability_checks()
    alert = bot.sent[-1]
    assert "отвечает, но показывает не то" in alert["text"] and "в ответе нет фразы «nope»" in alert["text"]
    assert "📸 Как выглядит сайт" not in buttons(alert["reply_markup"])
    assert asked == []                     # a POST cannot be repeated by check-host — not asked


async def test_card_menu_and_report_show_an_api_check_as_one(bot, db):
    page, api, who = await _monitors(db)
    for sid in (page, api, who):
        await sitestatus.update(sid, "avail", status="ok", ms=120, error=None)
    await sitestatus.update(page, "ssl", days_left=10, issuer="LE", not_after=None, error=None)
    await sitestatus.update(api, "ssl", days_left=10, issuer="LE", not_after=None, error=None)   # stale
    await sitestatus.update(api, "seo", status="warning", critical=0, improve=3, problems=[])     # stale

    call = FakeCall(f"check_site:{api}")
    await sites.cb_check_single_site(call)
    card = call.message.texts[-1]
    assert "🔌 API-проверка: POST, в ответе должно быть «scoreLabel»" in card and "Отвечает, быстро" in card
    assert "Сертификат и домен — на карточке good-pass.pages.dev" in card
    assert "Поиск и ИИ" not in card and "Сертификат действует" not in card
    assert "🔎 Поиск и ИИ" not in buttons(call.message.reply_markup)
    icon, state, _ = await menu.site_state(await db.get_site(api))
    assert icon == "✅" and "сертификат" not in state                 # the site's line carries it

    call = FakeCall(f"check_site:{who}")
    await sites.cb_check_single_site(call)
    assert "⏳ Сертификат: ещё не проверял" in call.message.texts[-1]   # nobody else covers that host

    links_call = FakeCall(f"check_links:{api}")
    await links.cb_check_links(links_call)
    assert "API-проверка" in links_call.message.texts[-1]

    text, kb = await site_settings.sset_screen(await db.get_site(api))
    assert "API-проверка" in text and "📸 Как выглядит сайт" not in buttons(kb)
    assert "«scoreLabel» — должно быть в ответе" in text
    picker = FakeCall(f"ssp:k:{api}")
    await site_settings.cb_site_setting_picker(picker, FakeState())
    assert "Фраза в ответе" in picker.message.texts[-1]
    assert "✅ Фраза должна быть в ответе" in buttons(picker.message.reply_markup)

    r_page = availability.AvailabilityResult(url=GOOD_PASS, site_id=page, response_time_ms=100)
    r_api = availability.AvailabilityResult(url=ANALYZE, site_id=api, response_time_ms=100, api=True)
    report = format_compact_status_report([r_page, r_api], [], ssl_results=[], domain_results=[])
    assert "✅ good-pass.pages.dev/api/v1/analyze — в порядке" in report


async def test_adding_an_api_address_says_what_is_watched(bot, db):
    async def h(_):
        return web.json_response({"ip": "1.2.3.4"})
    app = web.Application()
    app.router.add_get("/api/whoami", h)
    async with TestServer(app) as server:
        msg = FakeMessage(f"http://127.0.0.1:{server.port}/api/whoami")
        await sites.msg_site_add(msg, FakeState())
    text = msg.texts[-1]
    assert "✅ Отвечает" in text and "🔌 Это API" in text and "поиск и ИИ проверю" not in text
    assert "📡 Настроить запрос" in buttons(msg.reply_markup)


async def test_switching_to_post_closes_page_findings(bot, db):
    sid = await db.activate_or_create_site("https://worker.example.com")
    await save_incident(sid, "seo", "2 помехи в поиске, напр.: Нет карты сайта", "warning")
    call = FakeCall(f"ssm:{sid}:POST")
    await site_settings.cb_site_method(call)
    assert not await get_active_incidents()
    assert "API-проверка" in call.message.texts[-1]
