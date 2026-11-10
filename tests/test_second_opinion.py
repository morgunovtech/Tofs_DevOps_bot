"""The second opinion from check-host.net: most nodes decide, not one; the
node in the audience's country can veto «up»; a rejected certificate and an
open incident are not asked about; the whole verdict has a budget."""

import asyncio
import datetime
import ssl
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from monitors import availability, second_opinion
from monitors.base import AvailabilityResult
from monitors.second_opinion import decide, endpoint_for, node_up, region_node
from reports.formatter import format_availability_alert

UP = [[1, 0.12, "OK", "200", "1.2.3.4"]]
DOWN = [[0, 3.0, "Connection timed out", None, None]]


def test_endpoint_for():
    assert endpoint_for("https://example.com") == ("check-http", "https://example.com")
    assert endpoint_for("tcp://mail.example.com:25") == ("check-tcp", "mail.example.com:25")
    assert endpoint_for("ping://example.com") == ("check-ping", "example.com")
    assert endpoint_for("ping://10.0.0.1") is None          # private
    assert endpoint_for("https://nas.local") is None
    assert endpoint_for("tcp://example.com") is None        # no port


def test_node_up_shapes():
    assert node_up("check-http", [[1, 0.12, "OK", "200", "1.2.3.4"]]) is True
    assert node_up("check-http", [[0, 0.12, "Timeout", None, "1.2.3.4"]]) is False
    assert node_up("check-tcp", [{"address": "1.2.3.4", "time": 0.03}]) is True
    assert node_up("check-tcp", [{"error": "refused"}]) is False
    assert node_up("check-ping", [[["OK", 0.05, "1.2.3.4"], ["TIMEOUT", None, None]]]) is True
    assert node_up("check-ping", [[["TIMEOUT", None, None]]]) is False
    assert node_up("check-http", "garbage") is None


def test_most_nodes_decide_not_one():
    assert decide(ups=2, answered=3, pending=0) is True
    assert decide(ups=1, answered=3, pending=0) is None      # split: alert, unconfirmed
    assert decide(ups=0, answered=3, pending=0) is False
    assert decide(ups=1, answered=1, pending=2) is None      # silent nodes are no verdict
    assert decide(ups=0, answered=2, pending=1) is None


def test_region_node_name():
    assert region_node("ru") == "ru1.node.check-host.net"
    assert region_node("DE") == "de1.node.check-host.net"
    assert region_node(None) is None and region_node("") is None and region_node("x1") is None


@pytest.fixture
async def checkhost(monkeypatch):
    """A stand-in for check-host.net on 127.0.0.1: /check-<kind> starts a check
    (request r1; with a `node` parameter — the region's request r2),
    /check-result/<id> answers with the next of that request's `results` (the
    last one repeats). Returns the list of started checks' query strings."""
    servers = []
    monkeypatch.setattr(second_opinion, "POLL_PAUSES", (0, 0, 0))

    async def start(results, status=200, delay=0.0, body=None, region_results=None):
        polls = {"r1": 0, "r2": 0}
        per_request = {"r1": results, "r2": region_results or [{}]}
        calls = []

        async def begin(request):
            calls.append(request.path_qs)
            await asyncio.sleep(delay)
            if body is not None:
                return web.Response(status=status, text=body, content_type="text/html")
            if "node" in request.query:
                nodes = {request.query["node"]: ["RU", "Russia", "Moscow"]} if region_results else {}
                return web.json_response({"ok": 1, "request_id": "r2", "nodes": nodes}, status=status)
            return web.json_response({"ok": 1, "request_id": "r1"}, status=status)

        async def result(request):
            rid = request.match_info["rid"]
            seq = per_request[rid]
            i = min(polls[rid], len(seq) - 1)
            polls[rid] += 1
            return web.json_response(seq[i])

        app = web.Application()
        app.router.add_get("/check-result/{rid}", result)
        app.router.add_get("/{kind}", begin)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        monkeypatch.setattr(second_opinion, "API", str(server.make_url("")).rstrip("/"))
        return calls
    yield start
    for server in servers:
        await server.close()


async def test_majority_of_nodes_makes_the_verdict(checkhost):
    calls = await checkhost([{"de1": UP, "us1": UP, "ru1": DOWN}])
    v = await second_opinion.second_opinion("https://shop.example.com")
    assert (v.up, v.ups, v.total, v.region_down) == (True, 2, 3, False)
    assert len(calls) == 1 and "max_nodes=3" in calls[0] and "node=" not in calls[0]
    await checkhost([{"de1": UP, "us1": DOWN, "ru1": DOWN}])
    v = await second_opinion.second_opinion("https://shop.example.com")
    assert (v.up, v.ups, v.total) == (None, 1, 3)           # one node is not «everyone else»
    await checkhost([{"de1": DOWN, "us1": DOWN, "ru1": DOWN}])
    assert await second_opinion.second_opinion_up("https://shop.example.com") is False


async def test_pending_nodes_are_not_a_verdict(checkhost):
    await checkhost([{"de1": None, "us1": None, "ru1": None}])
    assert await second_opinion.second_opinion_up("https://shop.example.com") is None
    await checkhost([{"de1": DOWN, "us1": DOWN, "ru1": None}])
    v = await second_opinion.second_opinion("https://shop.example.com")
    assert (v.up, v.ups, v.total) == (None, 0, 3)           # asked three, two said no, one is silent


async def test_the_regions_node_can_veto_up_but_not_make_it(checkhost):
    three_up = [{"de1": UP, "us1": UP, "nl1": UP}]
    calls = await checkhost(three_up, region_results=[{"ru1.node.check-host.net": DOWN}])
    v = await second_opinion.second_opinion("https://shop.example.com", "ru")
    assert (v.up, v.ups, v.total, v.region_down) == (None, 3, 3, True)
    assert len(calls) == 2 and any("node=ru1.node.check-host.net" in c for c in calls)
    await checkhost(three_up, region_results=[{"ru1.node.check-host.net": UP}])
    v = await second_opinion.second_opinion("https://shop.example.com", "ru")
    assert (v.up, v.region_down) == (True, False)
    await checkhost(three_up, region_results=[{"ru1.node.check-host.net": None}])
    v = await second_opinion.second_opinion("https://shop.example.com", "ru")
    assert (v.up, v.region_down) == (True, False)           # a silent node: the random nodes decide
    await checkhost(three_up)                                # no node in that country at all
    v = await second_opinion.second_opinion("https://shop.example.com", "xx")
    assert (v.up, v.region_down) == (True, False)
    await checkhost([{"de1": DOWN, "us1": DOWN, "nl1": DOWN}],
                    region_results=[{"ru1.node.check-host.net": UP}])
    v = await second_opinion.second_opinion("https://shop.example.com", "ru")
    assert (v.up, v.region_down) == (False, False)          # one node up elsewhere never lifts «down»


@pytest.mark.parametrize("status,body", [(500, None), (200, "<html>busy</html>"), (429, "slow down")])
async def test_service_trouble_fails_open(checkhost, status, body):
    await checkhost([{"de1": UP}], status=status, body=body)
    assert await second_opinion.second_opinion_up("https://shop.example.com") is None


async def test_a_slow_service_gets_a_budget_not_two_minutes(checkhost, monkeypatch):
    monkeypatch.setattr(second_opinion, "BUDGET_SEC", 0.5)
    await checkhost([{"de1": UP}], delay=5)
    started = time.monotonic()
    assert await second_opinion.second_opinion_up("https://shop.example.com") is None
    assert time.monotonic() - started < 2


def _self_signed(tmp_path) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=30))
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    return ctx


async def test_a_certificate_error_alerts_without_asking_outside(db, monkeypatch, tmp_path):
    asked = []

    async def outside(url, region=None):
        asked.append(url)
        return second_opinion.Verdict(True, 3, 3)

    monkeypatch.setattr(availability, "second_opinion", outside)
    monkeypatch.setattr(availability.integrations, "second_opinion_enabled", lambda: True)
    app = web.Application()
    app.router.add_get("/", lambda request: web.Response(text="hello"))
    server = TestServer(app)
    await server.start_server(ssl=_self_signed(tmp_path))
    try:
        url = str(server.make_url("/"))
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        r = await availability.check_availability(url)
    finally:
        await server.close()
    assert r.cert_failed and r.incident_new and asked == []
    text = format_availability_alert(r, second_opinion=True)
    assert "сертификат не прошёл проверку" in text and "его сертификат не принимается" in text
    assert "снаружи" not in text


async def test_no_second_opinion_while_the_incident_is_open(db, monkeypatch):
    asked = []

    async def outside(url, region=None):
        asked.append((url, region))
        return second_opinion.Verdict(False, 0, 3)

    monkeypatch.setattr(availability, "second_opinion", outside)
    monkeypatch.setattr(availability.integrations, "second_opinion_enabled", lambda: True)
    monkeypatch.setattr(availability.settings, "timezone", lambda: "Europe/Moscow")
    sid = await db.activate_or_create_site("http://127.0.0.1:1/")      # nothing listens on :1
    await db.update_site_settings(sid, fail_threshold=1)
    r = await availability.check_availability("http://127.0.0.1:1/")
    assert r.incident_new and asked == [("http://127.0.0.1:1/", "ru")]
    for _ in range(3):
        r = await availability.check_availability("http://127.0.0.1:1/")
        assert r.incident_id and not r.incident_new
    assert len(asked) == 1


async def test_a_region_veto_opens_the_incident_with_the_country_named(db, monkeypatch):
    async def outside(url, region=None):
        return second_opinion.Verdict(None, 3, 3, region_down=True)

    monkeypatch.setattr(availability, "second_opinion", outside)
    monkeypatch.setattr(availability.integrations, "second_opinion_enabled", lambda: True)
    monkeypatch.setattr(availability.settings, "timezone", lambda: "Europe/Moscow")
    sid = await db.activate_or_create_site("http://127.0.0.1:1/")
    await db.update_site_settings(sid, fail_threshold=1)
    r = await availability.check_availability("http://127.0.0.1:1/")
    assert r.incident_new and r.external_region == "ru" and r.external_ok is None
    text = format_availability_alert(r, second_opinion=True)
    assert "снаружи открывается (с 3 из 3 серверов), но не из России" in text
    assert "лежит не везде" not in text


def _down(**fields) -> AvailabilityResult:
    return AvailabilityResult(url="https://shop.test", site_id=1, status="error",
                              error="Cannot connect to host shop.test:443 ssl:default [Connect call failed]",
                              **fields)


def test_alert_counts_the_nodes_honestly():
    # None opened it, one is silent: nothing is known — not «lies somewhere».
    text = format_availability_alert(_down(external_ok=None, external_ups=0, external_total=2), second_opinion=True)
    assert "лежит не везде" not in text and "снаружи проверить не удалось" in text
    text = format_availability_alert(_down(external_ok=None, external_ups=1, external_total=3), second_opinion=True)
    assert "снаружи открывается только с 1 из 3 серверов — похоже, сайт лежит не везде" in text
    # One node asked, one node opened it: no «only with 1 of 1».
    text = format_availability_alert(_down(external_ok=None, external_ups=1, external_total=1), second_opinion=True)
    assert "лежит не везде" not in text and "из 1 серверов" not in text
    text = format_availability_alert(_down(external_ok=None, external_ups=0, external_total=3,
                                           external_region="ru"), second_opinion=True)
    assert "из России тоже не открывается" in text
    text = format_availability_alert(_down(external_ok=False, external_ups=0, external_total=3), second_opinion=True)
    assert "не открывается ни у кого" in text
