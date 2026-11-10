"""The public status page: what it publishes, what state it shows, how it
behaves under a burst of hits, and how it gets published at all."""

import asyncio
from datetime import UTC, datetime, timedelta

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall, buttons

from db.database import save_incident, set_state
from handlers import settings as settings_screen
from handlers import setup
from services import integrations, public_urls, settings
from web import status_page
from web.webhook import create_app


async def _fresh_page():
    return await status_page.build_payload()


async def test_paths_ports_and_internal_addresses_are_never_published(bot, db):
    for url in ("https://api.telegram.org/bot0:FAKE-TOKEN/getMe", "https://shop.example.com/catalog",
                "tcp://10.0.0.5:5432", "tcp://db.example.com:5432", "ping://192.168.1.1", "https://nas.local:5000"):
        sid = await db.activate_or_create_site(url)
        await db.save_check(sid, "availability", "ok", response_time_ms=100, status_code=200)
    p = await _fresh_page()
    assert sorted(s["label"] for s in p["sites"]) == ["api.telegram.org · API", "shop.example.com"]
    page = status_page.render(p) + str(await status_page.status_json())
    for secret in ("FAKE-TOKEN", "10.0.0.5", "192.168", "nas.local", "5432", "/catalog", "db.example.com"):
        assert secret not in page


async def test_one_failed_check_is_not_an_outage(bot, db):
    sid = await db.activate_or_create_site("https://shop.example.com")
    await db.save_check(sid, "availability", "error", status_code=500)
    p = await _fresh_page()
    assert p["sites"][0]["state"] == "checking" and status_page._status(p) == "up"
    assert "перепроверяю" in status_page.render(p)


async def test_open_incidents_decide_down_and_degraded(bot, db):
    down = await db.activate_or_create_site("https://down.example.com")
    slow = await db.activate_or_create_site("https://slow.example.com")
    for sid, status in ((down, "error"), (slow, "ok")):
        await db.save_check(sid, "availability", status, response_time_ms=4900)
    await save_incident(down, "availability", "Site down: HTTP 502", "critical")
    await save_incident(slow, "performance", "медленно", "warning")
    p = await _fresh_page()
    assert [(s["label"], s["state"]) for s in p["sites"]] == [("down.example.com", "down"),
                                                              ("slow.example.com", "degraded")]
    assert p["sites"][1]["problems"] == ["Медленные ответы"]
    html = status_page.render(p)
    assert "Часть систем недоступна" in html and "недоступен с " in html
    assert (await status_page.status_json())["status"] == "down"


async def test_stopped_checks_are_not_shown_as_all_green(bot, db):
    sid = await db.activate_or_create_site("https://shop.example.com")
    await db.save_check(sid, "availability", "ok", response_time_ms=100)
    conn = await db.get_db()
    await conn.execute("UPDATE checks SET checked_at = datetime('now', '-6 hours')")
    await conn.commit()
    p = await _fresh_page()
    assert p["sites"][0]["state"] == "stale" and status_page._status(p) == "stale"
    assert "Нет свежих данных" in status_page.render(p)
    await conn.execute("UPDATE checks SET checked_at = datetime('now')")
    await conn.commit()
    await set_state("alive_at", (datetime.now(UTC) - timedelta(minutes=10)).isoformat())   # the scheduler stopped
    assert (await _fresh_page())["sites"][0]["state"] == "stale"


async def test_the_bots_own_network_outage_is_not_the_sites_fault(bot, db):
    """A check the bot could not make (status «unknown», PLAN-17) is «нет
    свежих данных» on the site and no green banner — never «недоступен»."""
    sid = await db.activate_or_create_site("https://shop.example.com")
    await db.save_check(sid, "availability", "unknown")
    p = await _fresh_page()
    assert p["sites"][0]["state"] == "nodata" and p["sites"][0]["ms"] is None
    assert status_page._status(p) == "stale" and "Нет свежих данных" in status_page.render(p)


async def test_no_data_is_not_zero_percent(bot, db):
    await db.activate_or_create_site("https://new.example.com")
    p = await _fresh_page()
    assert p["sites"][0]["up24"] is None and p["sites"][0]["state"] == "pending"
    html = status_page.render(p)
    assert '<span class="na">24ч: —</span>' in html and "Идёт первая проверка" in html
    assert '<h1 class="banner' in html and "@media (max-width: 560px)" in html
    assert ".site-head { flex-wrap: wrap; }" in html


async def test_a_burst_of_hits_builds_the_page_once(bot, db, monkeypatch):
    builds = []
    real = status_page.build_payload

    async def counted():
        builds.append(1)
        await asyncio.sleep(0.05)
        return await real()
    monkeypatch.setattr(status_page, "build_payload", counted)
    await asyncio.gather(*(status_page.payload() for _ in range(100)))
    assert len(builds) == 1


async def test_only_known_incident_types_are_published_with_machine_fields(bot, db):
    sid = await db.activate_or_create_site("https://shop.example.com")
    await db.save_check(sid, "availability", "ok")
    for check_type in ("exposed", "dns", "availability"):
        await save_incident(sid, check_type, "x", "critical")
    data = await status_page.status_json()
    assert data["version"] == 1 and [i["type_code"] for i in data["incidents"]] == ["availability"]
    assert data["incidents"][0]["started_iso"].endswith("+00:00")
    assert data["sites"][0]["checked_at_iso"].endswith("+00:00")


async def test_the_badge_has_its_own_address_without_the_page_slug(bot, db, cfg):
    cfg(status_page_slug="s3cret-slug")
    await settings.set_status_page(True)
    sid = await db.activate_or_create_site("https://shop.example.com")
    await db.save_check(sid, "availability", "ok")
    url = public_urls.badge_url(sid)
    assert "s3cret-slug" not in url and "/status" not in url
    async with TestClient(TestServer(create_app())) as c:
        r = await c.get(url.removeprefix(public_urls.base_url()))
        assert r.status == 200 and 'fill="#1a1a1a">100.0%' in await r.text()   # dark text on green
        assert (await c.get(f"/badge/{sid}-{'0' * 16}.svg")).status == 404
        await settings.set_status_page(False)
        assert (await c.get(url.removeprefix(public_urls.base_url()))).status == 404   # page off — badge off
    # Dark text on every fill: white on the red one reads at 3.6:1, below AA.
    for pct in (99.5, 97.0, 42.0, None):
        assert 'fill="#1a1a1a">' in status_page.badge_svg(pct)
    assert 'fill="#e05d44"' in status_page.badge_svg(42.0)


async def test_publishing_asks_first_and_the_secret_link_is_not_reissued_by_accident(bot, db):
    await db.activate_or_create_site("https://shop.example.com")
    call = FakeCall("set_sp:on")
    await settings_screen.cb_settings_pick(call)
    assert not settings.status_page_enabled() and "Её увидит любой" in call.message.texts[-1]
    assert "shop.example.com" in call.message.texts[-1]
    assert "🔑 Включить с секретной ссылкой" in buttons(call.message.reply_markup)
    await settings_screen.cb_status_page_publish(FakeCall("sp_pub:secret"))
    slug = integrations.status_page_slug()
    assert settings.status_page_enabled() and slug
    await settings_screen.cb_status_page_secret(FakeCall("sp_secret:on"))       # an old button
    assert integrations.status_page_slug() == slug
    call = FakeCall("sp_secret:new")
    await settings_screen.cb_status_page_secret(call)
    assert integrations.status_page_slug() == slug and "перестанет работать" in call.message.texts[-1]
    await settings_screen.cb_status_page_secret(FakeCall("sp_secret:new_ok"))
    assert integrations.status_page_slug() not in (None, "", slug)
    await settings_screen.cb_status_page_secret(FakeCall("sp_secret:off"))
    assert integrations.status_page_slug()                                       # asked, not done
    await settings_screen.cb_status_page_secret(FakeCall("sp_secret:off_ok"))
    assert not integrations.status_page_slug()
    await settings.set_status_page(False)


async def test_an_open_page_drops_the_old_slug_and_the_wizard_door_leads_back(bot, db):
    """«Включить открытой» after a secret link was once issued: the slug goes,
    or /status would answer «не найдено» against the screen's words. From
    «🔌 Подключения» the screen after publishing returns there."""
    await integrations.set_value("status_page_slug", "old-secret")
    call = FakeCall("setup:sp_on")
    await setup.cb_status_page_on(call)
    assert not settings.status_page_enabled() and "Её увидит любой" in call.message.texts[-1]
    assert "← Назад" in buttons(call.message.reply_markup)
    call = FakeCall("sp_pub:open:setup")
    await settings_screen.cb_status_page_publish(call)
    assert settings.status_page_enabled() and not integrations.status_page_slug()
    assert "Включена: https://bot.example.test/status" in call.message.texts[-1]
    assert "← К подключениям" in buttons(call.message.reply_markup)
    await settings.set_status_page(False)
