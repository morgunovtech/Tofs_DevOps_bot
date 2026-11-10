"""What leaves the bot for third parties (check-host.net, the screenshot
service, Google CrUX, RDAP): never an internal address — and every such
channel can be switched off from the chat."""

import asyncio
from datetime import timedelta

import aiohttp
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, buttons

from handlers import setup
from monitors import availability, domain_checker, rdap, region, second_opinion
from reports import scheduler
from services import crux, integrations, screenshots, sitestatus
from utils import net
from utils.net import is_lan_host, is_private_target
from utils.urls import host_of

INTERNAL = ["http://192.168.1.10:8123", "https://10.0.0.5", "https://nas.local:5000",
            "https://homeassistant.local", "https://grafana.home.arpa", "http://nas:5000",
            "https://pihole.lan", "https://corp.internal", "tcp://10.0.0.5:5432", "ping://192.168.1.1",
            "http://[fd00::1]/", "http://100.64.0.1/", "http://127.0.0.1:8080/",
            "https://grafana.corp", "https://wiki.intranet", "https://nas.home",
            "http://[::127.0.0.1]/", "http://[64:ff9b::c0a8:10a]/", "http://224.0.0.1/", "http://0x7f.1/"]
PUBLIC = ["https://shop.example.com", "https://api.example.com/v1/health", "tcp://mail.example.com:25",
          "ping://8.8.8.8", "https://ex.test"]


def test_one_rule_for_internal_addresses():
    """One predicate for every third party — the same one the outbound
    connector (utils.net) judges hosts by."""
    assert [u for u in INTERNAL if not is_private_target(u)] == []
    assert [u for u in PUBLIC if is_private_target(u)] == []
    assert [u for u in INTERNAL + PUBLIC if is_private_target(u) != is_lan_host(host_of(u))] == []


async def test_a_name_that_resolves_into_the_lan_is_private(monkeypatch):
    async def getaddrinfo(host, port, **kw):
        if host == "nas.home.example.com":
            return [(2, 1, 6, "", ("192.168.1.10", 0))]
        if host == "shop.example.com":
            return [(2, 1, 6, "", ("93.184.216.34", 0)), (10, 1, 6, "", ("2606:2800:220:1::1", 0, 0, 0))]
        raise OSError("Name or service not known")
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    assert await net.resolves_private("https://nas.home.example.com")
    assert not await net.resolves_private("https://shop.example.com")
    assert not await net.resolves_private("https://does-not-resolve.invalid")   # the service judges it
    assert await net.resolves_private("nonsense")                              # no host at all


async def _recorder(seen: list, status: int = 200, body: bytes = b"\x89PNG" + b"0" * 3000,
                    ctype: str = "image/png"):
    async def handler(request):
        seen.append(request.query.get("url") or request.query.get("host") or request.path)
        return web.Response(status=status, body=body, content_type=ctype)
    app = web.Application()
    app.router.add_get("/{tail:.*}", handler)
    return TestServer(app)


async def test_screenshot_service_never_sees_internal_addresses(bot, db, cfg, tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(screenshots, "RETRY_DELAY_SEC", 0)
    async with await _recorder(seen) as server:
        cfg(db_path=str(tmp_path / "bot.db"), public_base_url="https://bot.example.test",
            screenshot_template=str(server.make_url("/shot")) + "?url={url}")
        for url in ["https://shop.example.com", "http://192.168.1.10:8123", "https://nas.local:5000"]:
            await db.activate_or_create_site(url)
        await scheduler.run_card_pictures()
        image, reason = await screenshots.fetch_screenshot("https://nas.local:5000")   # the «📸» button
    assert seen == ["https://shop.example.com"]
    assert image is None and "внутренний адрес" in reason


async def test_failed_screenshot_waits_hours_not_the_next_look(bot, db, cfg, tmp_path, monkeypatch):
    seen: list = []
    monkeypatch.setattr(screenshots, "RETRY_DELAY_SEC", 0)
    async with await _recorder(seen, status=400, body=b'{"status": "fail"}', ctype="application/json") as server:
        cfg(db_path=str(tmp_path / "bot.db"), public_base_url="https://bot.example.test",
            screenshot_template=str(server.make_url("/shot")) + "?url={url}")
        sid = await db.activate_or_create_site("https://shop.example.com")
        assert await screenshots.is_due(sid, "https://shop.example.com")
        assert not await screenshots.refresh(sid, "https://shop.example.com")
        assert len(seen) == 1                                   # a definite «no» is not asked 4 times
        assert not await screenshots.is_due(sid, "https://shop.example.com")   # the next look: no new series
        assert (await sitestatus.get(sid))["shot"]["ok"] is False
        monkeypatch.setattr(screenshots, "FAILED_RETRY_AFTER", timedelta(seconds=-1))   # «six hours later»
        assert await screenshots.is_due(sid, "https://shop.example.com")


async def test_one_picture_at_a_time_and_the_old_picture_keeps_its_date(bot, db, cfg, tmp_path, monkeypatch):
    """A second refresh of the same site while one runs is refused; a failed
    refresh keeps the moment of the picture that is still on disk, so the
    card's link (its version) does not change for a picture that did not."""
    cfg(db_path=str(tmp_path / "bot.db"), public_base_url="https://bot.example.test")
    sid = await db.activate_or_create_site("https://shop.example.com")
    gate = asyncio.Event()

    async def slow(url):
        await gate.wait()
        return b"\x89PNG" + b"0" * 3000, None
    monkeypatch.setattr(screenshots, "fetch_screenshot", slow)
    first = asyncio.ensure_future(screenshots.refresh(sid, "https://shop.example.com"))
    await asyncio.sleep(0)
    assert not await screenshots.refresh(sid, "https://shop.example.com")    # busy: no second attempt
    gate.set()
    assert await first
    link = await screenshots.card_picture_url(sid)
    taken = (await sitestatus.get(sid))["shot"]["pic_at"]

    async def failing(url):
        return None, "сервис снимков не ответил"
    monkeypatch.setattr(screenshots, "fetch_screenshot", failing)
    assert not await screenshots.refresh(sid, "https://shop.example.com")
    shot = (await sitestatus.get(sid))["shot"]
    assert shot["ok"] is False and shot["pic_at"] == taken and "не ответил" in shot["reason"]
    assert await screenshots.card_picture_url(sid) == link


async def test_screenshots_off_and_a_template_with_other_braces(bot, db, cfg, tmp_path, monkeypatch):
    cfg(db_path=str(tmp_path / "bot.db"), screenshot_template="off")
    sid = await db.activate_or_create_site("https://shop.example.com")
    assert not await screenshots.is_due(sid, "https://shop.example.com")
    image, reason = await screenshots.fetch_screenshot("https://shop.example.com")
    assert image is None and "выключены" in reason
    seen: list = []
    async with await _recorder(seen) as server:
        cfg(screenshot_template=str(server.make_url("/shot")) + "?w={width}&url={url}")
        image, reason = await screenshots.fetch_screenshot("https://shop.example.com")
    assert image and reason is None and seen == ["https://shop.example.com"]


async def test_no_screenshots_when_the_card_cannot_show_them(bot, db, cfg):
    cfg(public_base_url="", railway_public_domain="")
    sid = await db.activate_or_create_site("https://shop.example.com")
    assert not await screenshots.is_due(sid, "https://shop.example.com")


async def test_check_host_never_sees_internal_addresses(monkeypatch):
    seen: list = []
    async with await _recorder(seen, body=b'{"request_id": ""}', ctype="application/json") as server:
        monkeypatch.setattr(second_opinion, "API", str(server.make_url("")).rstrip("/"))
        monkeypatch.setattr(region, "API", str(server.make_url("")).rstrip("/"))
        for url in INTERNAL:
            assert await second_opinion.second_opinion_up(url) is None
            assert await region.measure(url, "ru") is None

        async def resolves_inside(url):
            return True                     # nas.home.example.com → 192.168.1.10 from the bot
        monkeypatch.setattr(second_opinion, "resolves_private", resolves_inside)
        assert await second_opinion.second_opinion_up("https://nas.home.example.com") is None
        assert (await second_opinion.second_opinion("https://nas.home.example.com", "ru")).up is None
    assert seen == []


async def test_the_alert_path_keeps_a_lan_resolving_name_at_home(db, monkeypatch):
    """The decision is made where check_availability asks (second_opinion →
    Verdict), not only in the mail wrapper: a public-looking name that
    resolves to a LAN address here gives the mock check-host no request."""
    seen: list = []
    resolved: list = []

    async def resolves_inside(url):
        resolved.append(url)
        return True
    monkeypatch.setattr(second_opinion, "resolves_private", resolves_inside)
    monkeypatch.setattr(availability.integrations, "second_opinion_enabled", lambda: True)
    monkeypatch.setattr(availability.settings, "timezone", lambda: "Europe/Moscow")
    async with await _recorder(seen, body=b'{"request_id": "r1"}', ctype="application/json") as server:
        monkeypatch.setattr(second_opinion, "API", str(server.make_url("")).rstrip("/"))
        url = "http://nas.home.example.com:1/"        # the name does not resolve in the test box: down
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        r = await availability.check_availability(url)
    assert r.status == "error" and r.incident_new and r.external_ok is None
    assert resolved == [url] and seen == []


async def test_region_speed_sends_only_the_site_address(monkeypatch):
    seen: list = []
    async with await _recorder(seen, body=b'{"request_id": ""}', ctype="application/json") as server:
        monkeypatch.setattr(region, "API", str(server.make_url("")).rstrip("/"))
        await region.measure("https://shop.example.com/cart?token=abc", "ru")
        await region.measure("https://shop.example.com:8443/cart/", "ru")
    assert seen and set(seen) == {"https://shop.example.com/", "https://shop.example.com:8443/"}


async def test_rdap_is_not_asked_about_internal_names(bot, db, monkeypatch):
    asked: list = []

    async def lookup(domain):
        asked.append(domain)
        raise rdap.DomainLookupError("must not be called")
    monkeypatch.setattr(rdap, "lookup", lookup)
    r = await domain_checker.check_domain("https://homeassistant.local")
    assert asked == [] and r.unsupported and "внутренний адрес" in r.error
    assert (await sitestatus.get(r.site_id))["domain"]["unsupported"] is True

    seen: list = []
    async with await _recorder(seen, status=404, body=b"{}", ctype="application/json") as server:
        base = str(server.make_url("/"))
        monkeypatch.setattr(rdap, "_bootstrap", {"tech": [base]})
        monkeypatch.setattr(rdap, "_bootstrap_at", 1e12)
        monkeypatch.setattr(rdap, "RDAP_ORG", base + "domain/{domain}")
        async with aiohttp.ClientSession() as session:
            with pytest.raises(rdap.RdapUnsupported):
                await rdap.rdap_lookup("intranet.corpzone", session)
            assert seen == []                   # a zone IANA does not list: no request at all
            # The bootstrap itself could not be fetched: rdap.org is still the fallback.
            # IANA is pointed at a closed port — on CI the real download would succeed.
            monkeypatch.setattr(rdap, "_bootstrap", {})
            monkeypatch.setattr(rdap, "IANA_BOOTSTRAP", "http://127.0.0.1:1/dns.json")
            with pytest.raises(rdap.DomainLookupError):
                await rdap.rdap_lookup("shop.example", session)
    assert seen == ["/domain/shop.example"]


async def test_crux_is_not_asked_about_internal_addresses(bot, db, monkeypatch):
    asked: list = []

    async def query(origin, key=None):
        asked.append(origin)
        return {}
    monkeypatch.setattr(integrations, "crux_key", lambda: "AIza-test")
    monkeypatch.setattr(crux, "query", query)
    await db.activate_or_create_site("https://nas.local:5000")
    await db.activate_or_create_site("https://shop.example.com")
    await scheduler.run_visitor_checks()
    assert asked == ["https://shop.example.com"]


async def test_second_opinion_can_be_switched_off_from_the_chat(bot, db, cfg, monkeypatch):
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    cfg(second_opinion=False)
    await setup.cb_second_opinion(FakeCall("setup:so:on"))
    assert integrations.second_opinion_enabled()
    call = FakeCall("setup:check")
    await setup.show_checklist(call)
    assert "🌐 Вторая точка: выключить" in buttons(call.message.reply_markup)
    call = FakeCall("setup:so:off")
    await setup.cb_second_opinion(call)
    assert not integrations.second_opinion_enabled()
    assert "second_opinion" not in integrations.stored()       # .env (SECOND_OPINION=0) is the master again
    assert "из .env (SECOND_OPINION=0)" in call.message.texts[-1]

    cfg(second_opinion=True)
    call = FakeCall("setup:so:off")
    await setup.cb_second_opinion(call)
    assert not integrations.second_opinion_enabled()
    assert "задано здесь, в чате (в .env SECOND_OPINION=1)" in call.message.texts[-1]
    await integrations.set_value("second_opinion", None)


async def test_the_switched_off_service_is_offered_as_a_screen_not_a_tap(bot, db, cfg, monkeypatch):
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    cfg(second_opinion=False)
    call = FakeCall("setup:more")
    await setup.cb_more(call)
    assert "🌐 Вторая точка" in buttons(call.message.reply_markup)
    assert "🌐 Включить вторую точку" not in buttons(call.message.reply_markup)
    call = FakeCall("setup:so")
    await setup.cb_second_opinion_screen(call)
    text = call.message.texts[-1]
    assert text.startswith("🌐 Вторая точка проверки — бесплатный сервис check-host.net")
    assert "Внутренние адреса" in text and "Перепроверка: выключена" in text
    assert buttons(call.message.reply_markup)[:2] == ["🌐 Перепроверку включить", "⏱ Скорость из региона включить"]


async def test_region_speed_is_its_own_switch_and_a_stale_tap_does_not_turn_it_on(bot, db, cfg):
    cfg(region_speed=False)
    assert not integrations.region_speed_enabled()
    call = FakeCall("setup:rs:on")
    await setup.cb_second_opinion(call)
    assert integrations.region_speed_enabled() and "Скорость из региона: включена" in call.message.texts[-1]
    assert "⏱ Скорость из региона выключить" in buttons(call.message.reply_markup)
    await setup.cb_second_opinion(FakeCall("setup:rs:off"))
    assert not integrations.region_speed_enabled() and "region_speed" not in integrations.stored()

    class StaleCall(FakeCall):
        async def answer(self, text=None, show_alert=False):
            raise TelegramBadRequest(method=None, message="Bad Request: query is too old")
    call = StaleCall("setup:rs:on")
    await setup.cb_second_opinion(call)
    assert not integrations.region_speed_enabled() and call.message.texts[-1].startswith("⌛")
    call = StaleCall("setup:so:off")        # switching OFF needs no fresh tap
    await integrations.set_value("second_opinion", "on")
    await setup.cb_second_opinion(call)
    assert not integrations.second_opinion_enabled() and "Перепроверка: выключена" in call.message.texts[-1]


async def _const(v):
    return v
