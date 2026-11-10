"""Adding and removing a site: names as people write them, the port and
IPv6 forms, a certificate only where there is one, the 20-site limit on
every way in, and removal that leaves nothing behind but history."""

from conftest import FakeCall, FakeMessage, FakeState

from handlers import menu, sites, start
from handlers.common import MAX_SITES
from handlers.sites import parse_site_input
from monitors import availability
from monitors.base import AvailabilityResult, SslResult
from monitors.ladder import apply_ladder
from monitors.ssl_checker import SSL_THRESHOLDS
from services import maintenance, screenshots, sitestatus
from utils.urls import site_label, ssl_applies


def test_international_names_are_stored_as_punycode_and_shown_as_written():
    assert parse_site_input("пример.рф") == ("https://xn--e1afmkfd.xn--p1ai", None)
    assert parse_site_input("https://Почта.Пример.РФ/") == ("https://xn--80a1acny.xn--e1afmkfd.xn--p1ai", None)
    assert parse_site_input("café.example.com") == ("https://xn--caf-dma.example.com", None)
    assert parse_site_input("tcp://почта.пример.рф:25") == ("tcp://xn--80a1acny.xn--e1afmkfd.xn--p1ai:25", None)
    assert parse_site_input("пример..рф")[0] is None
    assert parse_site_input("ab--cd.com") == ("https://ab--cd.com", None)   # ASCII names: DNS rules, as before
    assert site_label("https://xn--e1afmkfd.xn--p1ai") == "пример.рф"
    assert site_label("https://xn--zz.com") == "xn--zz.com"                 # does not decode: shown as stored


def test_a_port_is_dropped_only_when_it_is_the_default_of_its_scheme():
    assert parse_site_input("http://example.com:80") == ("http://example.com", None)
    assert parse_site_input("https://example.com:443") == ("https://example.com", None)
    assert parse_site_input("http://example.com:443") == ("http://example.com:443", None)
    assert parse_site_input("https://example.com:80") == ("https://example.com:80", None)


def test_ipv6_hosts_for_port_and_ping_monitors():
    assert parse_site_input("ping://[2001:DB8::1]") == ("ping://[2001:db8::1]", None)
    assert parse_site_input("tcp://[2001:db8:0:0::1]:22") == ("tcp://[2001:db8::1]:22", None)
    assert parse_site_input("tcp://[2001:db8::1]")[0] is None       # a port monitor needs a port
    assert parse_site_input("ping://2001:db8::1")[0] is None        # without brackets it is not an address
    assert site_label("tcp://[2001:db8::1]:22") == "[2001:db8::1]:22"
    assert site_label("ping://[2001:db8::1]") == "ping 2001:db8::1"


def test_a_private_ipv6_address_follows_the_same_policy_as_ipv4(cfg):
    cfg(allow_private_targets=False)
    assert parse_site_input("ping://[fd00::1]") == (None, sites.PRIVATE_OFF)
    assert parse_site_input("ping://[2606:4700:4700::1111]")[0] == "ping://[2606:4700:4700::1111]"


def test_only_https_has_a_certificate():
    assert ssl_applies("https://example.com") and ssl_applies("HTTPS://example.com:8443")
    assert not ssl_applies("http://example.com") and not ssl_applies("http://example.com:8080")
    assert not ssl_applies("tcp://example.com:443") and not ssl_applies("ping://example.com")


async def test_ping_without_ipv6_on_the_bots_server_is_not_an_outage(db, monkeypatch):
    class Proc:
        returncode = 2

        async def communicate(self):
            return b"ping: 2001:db8::1: Address family for hostname not supported\n", b""

    async def spawn(*args, **kwargs):
        return Proc()
    monkeypatch.setattr(availability.asyncio, "create_subprocess_exec", spawn)
    r = await availability.check_availability("ping://[2001:db8::1]", manage=False)
    assert r.status == "unknown" and r.transient and "нет IPv6" in r.error


async def test_an_http_site_has_no_certificate_anywhere(bot, db, monkeypatch):
    certs = []

    async def avail(url, manage=True):
        site = await db.get_site_by_url(url)
        await sitestatus.update(site["id"], "avail", status="ok", ms=40, error=None)
        return AvailabilityResult(url=url, site_id=site["id"], response_time_ms=40)

    async def cert(url, manage=True):
        certs.append(url)
        return SslResult(url=url, site_id=0)

    async def nothing(*args, **kwargs):
        return None

    async def not_due(site_id, url):
        return False
    for name in ("check_domain", "check_mail", "check_links", "check_security", "alt_host_note",
                 "suggest_keyword"):
        monkeypatch.setattr(sites, name, nothing)
    monkeypatch.setattr(sites, "check_availability", avail)
    monkeypatch.setattr(sites, "check_ssl", cert)
    monkeypatch.setattr(sites.seo_screen, "audit_now", nothing)
    monkeypatch.setattr(screenshots, "is_due", not_due)
    await db.activate_or_create_site("https://first.example.com")   # not the first site: no welcome texts
    await db.set_state(start.LAST_STEPS_SHOWN, "1")                  # … and the last steps were offered already

    msg = FakeMessage("http://plain.example.com:8080")
    await sites.msg_site_add(msg, FakeState())
    assert "под присмотром" in msg.texts[-1] and "Сертификат" not in msg.texts[-1]
    assert "Срок домена" in msg.texts[-1]                            # the domain is still checked
    site = await db.get_site_by_url("http://plain.example.com:8080")
    call = FakeCall(f"check_site_live:{site['id']}")
    await sites.cb_check_site_live(call)
    assert certs == []                                               # neither add nor recheck asked for one
    assert all("сертификат" not in t.lower() for t in call.message.texts)

    # A snapshot left by an older version: the card and the main screen ignore it.
    await sitestatus.update(site["id"], "ssl", days_left=5, issuer="X", not_after=None, error=None)
    assert all("Сертификат" not in line for line in await sites.card_lines(site))
    _, state, _ = await menu.site_state(site)
    assert "сертификат" not in state


async def test_the_twenty_site_limit_holds_on_every_way_in(bot, db, monkeypatch):
    async def avail(url, manage=True):
        site = await db.get_site_by_url(url)
        return AvailabilityResult(url=url, site_id=site["id"], response_time_ms=10)
    monkeypatch.setattr(sites, "check_availability", avail)
    for i in range(MAX_SITES):
        await db.activate_or_create_site(f"tcp://host{i:02d}.example.com:25")

    state = FakeState()
    call = FakeCall("site_add")
    await sites.cb_site_add(call, state)
    assert call.message.texts[-1].startswith(f"Лимит {MAX_SITES} сайтов") and state.state is None

    call = FakeCall("site_add_adv")                                  # «🛠 API, порт или ping» from an older screen
    await sites.cb_site_add_advanced(call, state)
    assert call.message.texts[-1].startswith(f"Лимит {MAX_SITES} сайтов") and state.state is None

    state.state = sites.AddSiteForm.url                              # a form opened before the limit was reached
    msg = FakeMessage("tcp://host20.example.com:25")
    await sites.msg_site_add(msg, state)
    assert msg.texts[-1].startswith(f"Лимит {MAX_SITES} сайтов")
    assert len(await db.get_all_sites()) == MAX_SITES

    msg = FakeMessage("tcp://host00.example.com:25")                 # an address already watched is not a 21st
    await sites.msg_site_add(msg, FakeState())
    assert any("под присмотром" in t for t in msg.texts)
    assert len(await db.get_all_sites()) == MAX_SITES


async def test_removing_a_site_forgets_everything_but_its_history(bot, db):
    url = "https://shop.example.com"
    sid = await db.activate_or_create_site(url)
    await db.update_site_settings(sid, keyword="Old phrase", keyword_mode="present", fail_threshold=5,
                                  http_headers='{"Authorization": "Bearer old"}')
    await maintenance.pause_site(sid, 60)
    await sitestatus.update(sid, "avail", status="ok", ms=10, error=None)
    await db.set_state(f"kw_declined:{sid}", "1")
    await db.set_state(f"hosting:{sid}", "cloudflare")
    await db.save_check(sid, "availability", "ok", response_time_ms=10)
    old = SslResult(url=url, site_id=sid, status="warning", error="SSL expires in 6 days")
    await apply_ladder(old, "ssl", 6, SSL_THRESHOLDS)                # the old episode reached the 7-day step
    assert await db.get_last_alert_threshold(sid, "ssl") == 7
    assert await db.get_active_incidents()

    confirm = FakeCall(f"delsite:{sid}")
    await sites.cb_site_del_confirm(confirm)
    assert "настройки" in confirm.message.texts[-1] and "сбросятся" in confirm.message.texts[-1]

    call = FakeCall(f"delok:{sid}")
    await sites.cb_site_del_do(call)
    assert "больше не под присмотром" in call.message.texts[-1] and "настройки сброшены" in call.message.texts[-1]
    assert await db.get_active_incidents() == []
    assert await db.get_last_check(sid, "availability")             # history stays

    assert await db.activate_or_create_site(url) == sid             # same row: the history stays with it
    site = await db.get_site(sid)
    assert (site["keyword"], site["fail_threshold"], site["http_headers"]) == (None, None, None)
    assert not await maintenance.is_paused(sid)
    assert await sitestatus.get(sid) == {}
    assert await db.get_state(f"kw_declined:{sid}") is None and await db.get_state(f"hosting:{sid}") is None
    new = SslResult(url=url, site_id=sid, status="warning", error="SSL expires in 12 days")
    await apply_ladder(new, "ssl", 12, SSL_THRESHOLDS)               # a new episode starts its own ladder
    assert new.incident_new

    # A site removed by an older version kept its settings: re-adding clears them too.
    await db.deactivate_site(sid)
    conn = await db.get_db()
    await conn.execute("UPDATE sites SET keyword = 'Older' WHERE id = ?", (sid,))
    await conn.commit()
    await db.activate_or_create_site(url)
    assert (await db.get_site(sid))["keyword"] is None
    # Re-adding a site that is already watched keeps what it has.
    await db.update_site_settings(sid, keyword="Kept")
    await db.activate_or_create_site(url)
    assert (await db.get_site(sid))["keyword"] == "Kept"
