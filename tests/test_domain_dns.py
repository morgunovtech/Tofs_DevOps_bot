"""Domain expiry and DNS watch: whose domain it is, what a failed lookup
keeps, a rotating pool is not a move, a silent zone is said once."""

import asyncio
import ssl
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from db.database import get_active_incidents, get_dns_state, set_dns_state
from handlers import sites as sites_screen
from monitors import dns_checker, domain_checker, rdap, ssl_checker
from monitors.base import DomainInfo, SslInfo
from reports import scheduler
from services import sitestatus
from utils.urls import owned_domain, registrable_domain, same_site


def _info(days: int | None) -> DomainInfo:
    exp = (datetime.now(UTC) + timedelta(days=days, hours=1)).isoformat() if days is not None else None
    return DomainInfo(domain="ex.test", registrar="REGRU-RU", creation_date=None, expiration_date=exp,
                      days_left=days, name_servers=[])


# ── Whose domain ─────────────────────────────────────────────────────────────

def test_registrable_domain_knows_country_second_levels():
    assert registrable_domain("shop.example.com.kz") == "example.com.kz"
    assert registrable_domain("example.org.ua") == "example.org.ua"
    assert registrable_domain("www.example.com.by") == "example.com.by"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("nas.synology.me") == "synology.me"
    assert registrable_domain("192.168.1.10") == "192.168.1.10"
    assert registrable_domain("2001:db8::1") == "2001:db8::1"
    assert not same_site("https://a.com.kz", "https://b.com.kz")


def test_owned_domain_skips_addresses_platforms_and_bare_names():
    assert owned_domain("shop.example.com.kz") == "example.com.kz"
    assert owned_domain("nas.synology.me") is None and owned_domain("user.github.io") is None
    assert owned_domain("192.168.1.10") is None and owned_domain("nas") is None
    assert domain_checker.no_domain_reason("www.ex.test") is None
    assert domain_checker.no_domain_reason("10.0.0.1") == "это IP-адрес, домена нет"
    assert "платформы synology.me" in domain_checker.no_domain_reason("nas.synology.me")


def test_domain_groups_put_the_carrier_first():
    groups = domain_checker.domain_groups(["https://app.ex.test", "https://www.ex.test", "https://ex.test",
                                           "https://shop.other.test", "https://www.other.test",
                                           "https://me.pages.dev", "http://10.0.0.1:8080"])
    assert groups == {"ex.test": ["https://ex.test", "https://www.ex.test", "https://app.ex.test"],
                      "other.test": ["https://www.other.test", "https://shop.other.test"]}


# ── Domain expiry ────────────────────────────────────────────────────────────

async def test_domain_status_by_days(bot, db, monkeypatch):
    await db.activate_or_create_site("https://ex.test")
    got = []
    for days in (31, 30, 8, 7, 0, -1):
        monkeypatch.setattr(rdap, "lookup", lambda d, days=days: asyncio.sleep(0, _info(days)))
        got.append((await domain_checker.check_domain("https://ex.test", manage=False)).status)
    assert got == ["ok", "warning", "warning", "critical", "critical", "error"]
    monkeypatch.setattr(rdap, "lookup", lambda d: asyncio.sleep(0, _info(None)))
    assert (await domain_checker.check_domain("https://ex.test")).unsupported


async def test_a_failed_lookup_keeps_the_known_expiry(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    monkeypatch.setattr(rdap, "lookup", lambda d: asyncio.sleep(0, _info(5)))
    await domain_checker.check_domain("https://ex.test")
    assert [i["check_type"] for i in await get_active_incidents()] == ["domain"]

    async def rate_limited(domain):
        raise rdap.DomainLookupError("RDAP rate limit (429)")
    monkeypatch.setattr(rdap, "lookup", rate_limited)
    r = await domain_checker.check_domain("https://ex.test", manage=False)
    snap = (await sitestatus.get(sid))["domain"]
    assert r.transient and snap["days_left"] == 5 and snap["registrar"] == "REGRU-RU"
    assert len(await get_active_incidents()) == 1                   # the incident stays as it is


async def test_a_failed_first_lookup_is_said_as_such(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")

    async def down(domain):
        raise rdap.DomainLookupError("WHOIS whois.tcinet.ru: timed out")
    monkeypatch.setattr(rdap, "lookup", down)
    r = await domain_checker.check_domain("https://ex.test")
    snap = (await sitestatus.get(sid))["domain"]
    assert r.transient and snap["days_left"] is None and snap["transient"] and "timed out" in snap["error"]


def test_whois_without_a_record_is_a_failure_not_unsupported():
    for text in ("You have exceeded allowed connection rate", "", "No entries found for the selected source"):
        with pytest.raises(rdap.DomainLookupError):
            rdap.parse_whois(text, "ex.ru")
    with pytest.raises(rdap.DomainLookupError, match="не найден"):
        rdap.parse_whois("% TCI\nNo entries found for the selected source(s).\n", "ex.ru")


async def test_every_site_of_a_domain_gets_its_expiry(bot, db, monkeypatch):
    app = await db.activate_or_create_site("https://app.ex.test")       # older, but not the carrier
    bare = await db.activate_or_create_site("https://ex.test")
    asked = []

    async def lookup(domain):
        asked.append(domain)
        return _info(12)
    monkeypatch.setattr(rdap, "lookup", lookup)
    await domain_checker.check_all_domains(["https://app.ex.test", "https://ex.test"])
    assert asked == ["ex.test"]
    assert (await sitestatus.get(app))["domain"]["days_left"] == 12
    assert (await sitestatus.get(bare))["domain"]["days_left"] == 12
    # The ladder rang on the carrier only.
    assert [i["site_id"] for i in await get_active_incidents()] == [bare]


async def test_a_platform_address_has_no_domain_to_watch(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://user.github.io")
    asked = []
    monkeypatch.setattr(rdap, "lookup", lambda d: asked.append(d) or asyncio.sleep(0, _info(3)))
    await domain_checker.check_all_domains(["https://user.github.io"])
    snap = (await sitestatus.get(sid))["domain"]
    assert asked == [] and snap["unsupported"] and "платформы github.io" in snap["error"]
    assert "платформы github.io" in sites_screen._domain_lines(snap, api=False)[0]


async def test_a_domain_incident_moves_with_its_carrier(bot, db, monkeypatch):
    """The incident lives on the carrier; when a bare site appears, the old
    carrier's finding is closed instead of hanging forever."""
    app = await db.activate_or_create_site("https://app.ex.test")
    monkeypatch.setattr(rdap, "lookup", lambda d: asyncio.sleep(0, _info(5)))
    await scheduler.run_domain_checks()
    assert [i["site_id"] for i in await get_active_incidents()] == [app]
    bare = await db.activate_or_create_site("https://ex.test")
    scheduler._in_flight.clear()
    await scheduler.run_domain_checks()
    assert [i["site_id"] for i in await get_active_incidents()] == [bare]


# ── RDAP ─────────────────────────────────────────────────────────────────────

async def test_bootstrap_is_downloaded_once_and_a_failure_is_remembered(monkeypatch):
    hits = []
    status = {"code": 200}

    async def bootstrap(request):
        hits.append(1)
        await asyncio.sleep(0.05)
        if status["code"] != 200:
            return web.Response(status=status["code"])
        return web.json_response({"services": [[["test"], ["https://rdap.example/"]]]})
    app = web.Application()
    app.router.add_get("/dns.json", bootstrap)
    async with TestServer(app) as srv:
        monkeypatch.setattr(rdap, "IANA_BOOTSTRAP", str(srv.make_url("/dns.json")))
        monkeypatch.setattr(rdap, "_bootstrap_lock", asyncio.Lock())
        for name, value in (("_bootstrap", {}), ("_bootstrap_at", 0.0), ("_bootstrap_failed_at", None)):
            monkeypatch.setattr(rdap, name, value)
        async with aiohttp.ClientSession() as session:
            tables = await asyncio.gather(*(rdap._bootstrap_servers(session) for _ in range(10)))
            assert len(hits) == 1 and all(t == {"test": ["https://rdap.example/"]} for t in tables)
            monkeypatch.setattr(rdap, "_bootstrap", {})
            status["code"] = 503
            for _ in range(3):
                assert await rdap._bootstrap_servers(session) == {}
            assert len(hits) == 2                                   # the failure is not retried at once


def test_endless_nesting_of_entities_is_not_a_crash():
    deep: dict = {"roles": ["registrant"]}
    for _ in range(2000):
        deep = {"roles": ["registrant"], "entities": [deep]}
    assert rdap._registrar_name([deep]) == ""


async def test_an_answer_that_is_not_a_record_is_a_failure(monkeypatch):
    async def domain(request):
        return web.json_response(["not", "a", "record"])
    app = web.Application()
    app.router.add_get("/domain/{name}", domain)
    async with TestServer(app) as srv:
        base = str(srv.make_url("/"))
        monkeypatch.setattr(rdap, "_bootstrap", {"test": [base]})
        monkeypatch.setattr(rdap, "_bootstrap_at", 1e12)
        monkeypatch.setattr(rdap, "RDAP_ORG", base + "domain/{domain}")
        async with aiohttp.ClientSession() as session:
            with pytest.raises(rdap.DomainLookupError, match="неожиданный ответ"):
                await rdap.rdap_lookup("ex.test", session)


# ── DNS ──────────────────────────────────────────────────────────────────────

class FakeZone:
    def __init__(self, monkeypatch):
        self.answers: dict[tuple[str, str], list[str] | None] = {
            ("ex.test", "A"): ["1.1.1.1", "1.1.1.2"], ("ex.test", "NS"): ["ns1.ex.test", "ns2.ex.test"],
            ("ex.test", "MX"): ["mx.ex.test"]}
        self.asked: list[str] = []
        monkeypatch.setattr(dns_checker, "resolve", self.resolve)
        monkeypatch.setattr(dns_checker, "_failures", {})

    async def resolve(self, name, rtype):
        self.asked.append(name)
        return self.answers.get((name, rtype), [])


async def test_dns_watches_only_names_of_the_owner(bot, db, monkeypatch):
    zone = FakeZone(monkeypatch)
    await dns_checker.check_all_dns(["https://192.168.1.10:8443", "https://user.github.io", "https://ex.test"])
    assert set(zone.asked) == {"ex.test"}


async def test_a_rotating_pool_is_not_a_move_but_a_new_host_is(bot, db, monkeypatch):
    zone = FakeZone(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    await scheduler.run_dns_checks()                                 # the baseline
    for answer in (["1.1.1.2", "1.1.1.3"], ["1.1.1.3", "1.1.1.4"], ["1.1.1.4", "1.1.1.1"]):
        zone.answers[("ex.test", "A")] = answer
        await scheduler.run_dns_checks()
    assert bot.sent == []
    zone.answers[("ex.test", "A")] = ["9.9.9.9"]                     # moved to another hosting
    await scheduler.run_dns_checks()
    assert bot.sent == []                                            # one answer is not a move yet
    await scheduler.run_dns_checks()
    assert len(bot.sent) == 1 and "запись A" in bot.sent[0]["text"]
    assert await get_dns_state("ex.test", "A") == "9.9.9.9"


async def test_a_record_that_vanished_is_said_at_once(bot, db, monkeypatch):
    zone = FakeZone(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    await scheduler.run_dns_checks()
    zone.answers[("ex.test", "A")] = []
    await scheduler.run_dns_checks()
    assert len(bot.sent) == 1 and "Стало: —" in bot.sent[0]["text"]


async def test_dns_that_stops_answering_is_said_once(bot, db, monkeypatch):
    zone = FakeZone(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    await set_dns_state("ex.test", "NS", "ns1.ex.test, ns2.ex.test")
    zone.answers[("ex.test", "NS")] = None                           # SERVFAIL hour after hour
    for _ in range(4):
        await scheduler.run_dns_checks()
    said = [m["text"] for m in bot.sent]
    assert len(said) == 1 and "DNS не отвечает на запрос NS" in said[0] and said[0].startswith("🔴")
    assert "ns1.ex.test, ns2.ex.test" in said[0]
    assert await get_dns_state("ex.test", "NS") == "ns1.ex.test, ns2.ex.test"   # the baseline stays
    assert await sites_screen._dns_lines({"url": "https://www.ex.test"}) == \
        ["🔴 DNS: NS-серверы ex.test не отвечают уже 4 ч"]
    zone.answers[("ex.test", "NS")] = ["ns1.ex.test", "ns2.ex.test"]   # back: the count resets
    await scheduler.run_dns_checks()
    assert dns_checker.failing("ex.test", "NS") == 0 and len(bot.sent) == 1
    assert (await sites_screen._dns_lines({"url": "https://ex.test"}))[0].startswith("✅ DNS не менялись")
    assert await sites_screen._dns_lines({"url": "https://user.github.io"}) == []


async def test_when_nothing_resolves_it_is_the_bots_network(bot, db, monkeypatch):
    FakeZone(monkeypatch)
    await db.activate_or_create_site("https://ex.test")
    await set_dns_state("ex.test", "NS", "ns1.ex.test, ns2.ex.test")

    async def down(name, rtype):                                     # the bot's resolver is down
        return None
    monkeypatch.setattr(dns_checker, "resolve", down)
    for _ in range(4):
        await scheduler.run_dns_checks()
    assert bot.sent == []


# ── SSL thresholds ───────────────────────────────────────────────────────────

async def test_ssl_status_by_days(bot, db, monkeypatch):
    await db.activate_or_create_site("https://ex.test")
    got = []
    for days in (15, 14, 4, 3, 0, -1):
        info = SslInfo("ex.test", "R3", "", "", days, "01")

        async def fetch(h, p, info=info):
            return info
        monkeypatch.setattr(ssl_checker, "_fetch_cert", fetch)
        got.append((await ssl_checker.check_ssl("https://ex.test", manage=False)).status)
    assert got == ["ok", "warning", "warning", "critical", "critical", "error"]


async def test_expired_cert_handshake_is_expired_not_network(bot, db, monkeypatch):
    await db.activate_or_create_site("https://ex.test")

    async def expired(host, port):
        e = ssl.SSLCertVerificationError("certificate verify failed")
        e.verify_message = "certificate has expired"
        raise e
    monkeypatch.setattr(ssl_checker, "_fetch_cert", expired)
    r = await ssl_checker.check_ssl("https://ex.test")
    assert (r.status, r.error, r.transient) == ("error", "SSL certificate has expired", False)


async def test_fetch_cert_reads_a_real_certificate(tmp_path, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=2, hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert_file, key_file)
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0, ssl=server_ctx)
    port = server.sockets[0].getsockname()[1]
    trusting = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: trusting(cafile=str(cert_file)))
    async with server:
        info = await ssl_checker._fetch_cert("localhost", port)
    assert info.days_left == 2 and info.common_name == "localhost"
