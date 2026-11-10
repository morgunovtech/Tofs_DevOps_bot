"""The watched phrase: found as a visitor sees it, checked against the page
when it is typed, never «watched» on HEAD, never re-read as a network error."""

import time

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from handlers import site_settings, sites
from monitors.availability import check_availability, phrase_state
from monitors.base import AvailabilityResult, phrase_found
from reports.formatter import format_recovery_alert, incident_line
from services import humanize, sitestatus

PAGE = ("<html><body><a class=logo>Рога&nbsp;и&nbsp;Копыта</a>"
        "<h1>Доставка <b>по&nbsp;всей</b> России</h1><p>Студия &laquo;Морг&raquo;</p>"
        "<script>var t = 'Fatal error';</script></body></html>")


def test_phrase_found_as_a_visitor_sees_it():
    assert phrase_found("Рога и Копыта", PAGE)
    assert phrase_found("доставка по всей россии", PAGE)
    assert phrase_found("Студия «Морг»", PAGE)
    assert phrase_found("Рога и Копыта", "<p>Рога" + chr(0xA0) + "и Копыта</p>")   # a literal U+00A0
    assert phrase_found("Fatal error", PAGE)                                  # raw match still counts
    assert not phrase_found("Корзина", PAGE)
    # A phrase that is nothing but markup has no visible text: source only,
    # or «'' in anything» would find it on an empty page and in healthy JSON.
    assert not phrase_found("<html>", '{"ok": true}')
    assert not phrase_found("<!DOCTYPE html>", "")


def test_phrase_search_stays_linear_on_hostile_html():
    start = time.monotonic()
    assert not phrase_found("Корзина", "<" * 1_000_000)
    assert not phrase_found("Корзина", "<a" + "x" * 1_000_000)
    assert time.monotonic() - start < 1.0


async def _server():
    async def page(request: web.Request):
        return web.Response(text=PAGE, content_type="text/html")
    app = web.Application()
    app.router.add_get("/{tail:.*}", page)
    return TestServer(app)


async def test_monitor_finds_nbsp_phrase_and_marks_the_card(db):
    async with await _server() as server:
        url = str(server.make_url("/"))
        sid = await db.get_or_create_site(url)
        await db.update_site_settings(sid, keyword="Рога и Копыта", keyword_mode="present")
        assert (await check_availability(url, manage=False)).ok
        await db.update_site_settings(sid, keyword="Корзина")
        r = await check_availability(url, manage=False)
        assert r.keyword_failed and (await sitestatus.get(sid))["avail"]["kw"] is True
        site = await db.get_site(sid)
        line = await sites._avail_line(site, await sitestatus.get(sid), page=True)
        assert line.startswith("🔴 Открывается, но показывает не то")
        assert await phrase_state(site, "Корзина") is False
        assert await phrase_state(site, "Доставка по всей России") is True
        assert await phrase_state({**site, "http_method": "HEAD"}, "Корзина") is None


async def test_typed_phrase_is_checked_against_the_page(bot, db):
    async with await _server() as server:
        url = str(server.make_url("/"))
        sid = await db.get_or_create_site(url)

        msg, state = FakeMessage("Корзина"), FakeState(sset_site_id=sid, kw_mode="present")
        await site_settings.msg_site_keyword(msg, state)
        assert (await db.get_site(sid))["keyword"] is None                   # not saved
        assert "💾 Сохранить всё равно" in buttons(msg.reply_markup)
        call = FakeCall(f"sskwf:{sid}")
        await site_settings.cb_site_keyword_force(call, state)
        assert (await db.get_site(sid))["keyword"] == "Корзина"              # the owner insisted

        msg, state = FakeMessage("Рога и Копыта"), FakeState(sset_site_id=sid, kw_mode="present")
        await site_settings.msg_site_keyword(msg, state)
        assert (await db.get_site(sid))["keyword"] == "Рога и Копыта"        # on the page: saved at once


async def test_head_and_phrase_do_not_mix(bot, db):
    sid = await db.get_or_create_site("https://head.test/")
    await db.update_site_settings(sid, http_method="HEAD")
    msg = FakeMessage("Корзина")
    await site_settings.msg_site_keyword(msg, FakeState(sset_site_id=sid, kw_mode="present"))
    assert "HEAD" in msg.texts[-1] and (await db.get_site(sid))["keyword"] is None

    await db.update_site_settings(sid, http_method=None, keyword="Корзина", keyword_mode="present")
    call = FakeCall(f"ssm:{sid}:HEAD")
    await site_settings.cb_site_method(call)
    assert "HEAD" in call.answers[-1] and (await db.get_site(sid))["http_method"] is None
    assert "Запрос: GET" in call.message.texts[-1]          # the screen is drawn again, not left as it was

    await db.update_site_settings(sid, http_method="HEAD")              # e.g. from an old import
    text, _ = await site_settings.sset_screen(await db.get_site(sid))
    assert "не проверяется" in text


async def test_keyword_verdict_is_never_read_as_a_network_error(db):
    raw = "на странице стоп-фраза «SQLSTATE[HY000] [2002] Connection refused»"
    assert humanize.describe_error(raw) == raw
    assert humanize.describe_error(f"Bad content: {raw}") == raw
    assert humanize.describe_error("Connection refused") == "сервер отказал в соединении"
    assert (humanize.incident_headline("availability", "Bad content: на странице нет фразы «Купить»", "shop")
            == "shop показывает не то: на странице нет фразы «Купить»")
    assert (humanize.incident_headline("availability", "Site down: Timeout (10s)", "db:5432", page=False)
            == "db:5432 не отвечает: не ответил за 10 секунд")
    text = format_recovery_alert(AvailabilityResult(
        url="https://shop.test/", site_id=1, status_code=200,
        resolved_incident={"message": "Bad content: на странице нет фразы «Купить»",
                           "created_at": "2026-11-09 10:00:00"}))
    assert "снова всё как надо" in text and "Было не так" in text and "Лежал" not in text
    # «ℹ️ Что делать» reads the incident alone: it must know the method too.
    sid = await db.get_or_create_site("https://shop.test/checkout")
    await db.update_site_settings(sid, http_method="POST")
    inc_id, _ = await db.save_incident(sid, "availability", "Site down: HTTP 500", "critical")
    assert "не отвечает" in incident_line(await db.get_incident(inc_id))
