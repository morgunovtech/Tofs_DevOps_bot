"""Addresses from other people's content (links, nested sitemaps, redirects
of a public site, the screenshot service's image URL) never lead into the
bot's own network; an API check's key does not travel to another origin."""

import ipaddress
import socket

import aiohttp
import pytest
from aiohttp import web
from aiohttp.abc import AbstractResolver
from aiohttp.test_utils import TestServer

from handlers.sites import PRIVATE_OFF, parse_site_input
from monitors import availability
from monitors.availability import check_availability
from monitors.links_checker import check_links
from monitors.sitemap import sitemap_urls
from services import screenshots
from services.humanize import describe_error
from utils import net
from utils.urls import host_of

# One list for every «is it internal» predicate of the project.
INTERNAL = ["127.0.0.1", "0.0.0.0", "10.0.0.5", "172.17.0.1", "192.168.1.1", "169.254.169.254",
            "100.100.100.200", "224.0.0.1", "[::1]", "[fd00:ec2::254]", "[fe80::1]", "[::ffff:127.0.0.1]",
            "[64:ff9b::7f00:1]", "[64:ff9b::c0a8:10a]", "[2002:7f00:1::1]", "[::127.0.0.1]",
            "0x7f.1", "127.1", "0177.0.0.1", "2130706433",
            "localhost", "router", "nas.local", "pc.localhost", "srv.localdomain", "nas.lan", "nas.home",
            "printer.home.arpa", "db.internal", "wiki.intranet", "grafana.corp"]
PUBLIC = ["93.184.215.14", "1.1.1.1", "[2606:4700::1111]", "[::ffff:93.184.215.14]", "[64:ff9b::5db8:d70e]",
          "example.com", "www.example.co.uk", "home.example.com", "corp.example.org", "beef.cafe"]


def test_public_ip_predicate():
    public = ["93.184.215.14", "1.1.1.1", "2606:4700::1111", "::ffff:93.184.215.14", "64:ff9b::5db8:d70e"]
    internal = ["127.0.0.1", "0.0.0.0", "10.0.0.5", "172.17.0.1", "192.168.1.1", "169.254.169.254",
                "100.100.100.200", "224.0.0.1", "::1", "fd00:ec2::254", "fe80::1", "::ffff:127.0.0.1",
                "64:ff9b::7f00:1", "2002:7f00:1::1", "::127.0.0.1"]
    assert all(net.is_public_ip(ipaddress.ip_address(a)) for a in public)
    assert not any(net.is_public_ip(ipaddress.ip_address(a)) for a in internal)


def test_both_predicates_agree_on_what_is_internal():
    """is_lan_host (a host) and is_private_target (a URL, PLAN-29 reuses it)
    give one answer for the same list — hex, octal and decimal IPv4 included."""
    for host in INTERNAL:
        assert net.is_lan_host(host) and net.is_private_target(f"http://{host}/"), host
    for host in PUBLIC:
        assert not net.is_lan_host(host) and not net.is_private_target(f"https://{host}/"), host
    assert net.is_private_target("not a url") and net.is_private_target("")
    assert net.normalize_ipv4("0x7f.1") == "127.0.0.1" and net.normalize_ipv4("example.com") is None


def test_site_input_normalizes_ipv4_and_obeys_the_switch(cfg):
    assert parse_site_input("0x7f.1") == ("https://127.0.0.1", None)
    assert parse_site_input("tcp://2130706433:6379") == ("tcp://127.0.0.1:6379", None)
    assert parse_site_input("0.0.0.0")[0] is None
    assert parse_site_input("example.com") == ("https://example.com", None)
    assert parse_site_input("10.0.0.5/api/health") == ("https://10.0.0.5/api/health", None)
    cfg(allow_private_targets=False)
    assert parse_site_input("10.0.0.5/api/health") == (None, PRIVATE_OFF)
    assert parse_site_input("ping://nas.local") == (None, PRIVATE_OFF)
    assert parse_site_input("tcp://2130706433:6379") == (None, PRIVATE_OFF)
    assert parse_site_input("example.com") == ("https://example.com", None)


def test_the_refusal_stays_readable():
    raw = "Cannot connect to host 127.0.0.1:8080 ssl:default [адрес во внутренней сети — туда не хожу]"
    assert describe_error(raw) == net.PRIVATE_REFUSED
    assert describe_error(f"Site down: {raw}") == net.PRIVATE_REFUSED
    away = "API перенаправляет на evil.example — ключ туда не отправляю"
    assert describe_error(away) == away


@pytest.fixture
async def internal():
    """A service in the bot's LAN that must never be reached; records hits."""
    hits = []

    async def any_path(request):
        hits.append(f"{request.method} {request.path_qs}")
        return web.Response(text="internal", content_type="text/html")

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", any_path)
    async with TestServer(app, host="127.0.0.1") as server:
        yield server.port, hits


@pytest.fixture
def public_site(monkeypatch):
    """The monitored site runs on localhost in tests; treat it as a public one."""
    monkeypatch.setattr(net, "is_lan_host", lambda host: False)


async def _site(routes, host="localhost"):
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_route("*", path, handler)
    return TestServer(app, host=host)


async def test_the_guard_hooks_aiohttp_resolution(internal):
    """The guard lives in aiohttp's internal _resolve_host (pinned 3.14.5):
    an upgrade that stops calling it would let this request through."""
    port, hits = internal
    with pytest.raises(net.PrivateAddressError):
        await net.public_connector()._resolve_host("127.0.0.1", port)
    async with aiohttp.ClientSession(connector=net.public_connector()) as session:
        with pytest.raises(aiohttp.ClientConnectorError) as failed:
            await session.get(f"http://127.0.0.1:{port}/")
    assert net.refused_private(failed.value) and hits == []
    assert net.PRIVATE_REFUSED in str(failed.value)
    # The LAN mode: a monitor the owner typed inside the LAN reaches the LAN.
    async with aiohttp.ClientSession(connector=net.owner_connector(f"http://127.0.0.1:{port}")) as session:
        async with session.get(f"http://127.0.0.1:{port}/own") as resp:
            assert resp.status == 200
    assert hits == ["GET /own"]


async def test_links_of_a_public_site_never_reach_the_lan(db, internal, public_site):
    port, hits = internal

    async def page(request):
        return web.Response(content_type="text/html", text=(
            f'<a href="http://127.0.0.1:{port}/admin?do=1">x</a><a href="/ok">ok</a>'))

    async with await _site({"/": page, "/ok": lambda r: web.Response(text="ok")}) as site:
        r = await check_links(str(site.make_url("/")))
    assert hits == [] and r.total_links == 2 and not r.broken and not r.pending


async def test_a_public_site_redirecting_into_the_lan_is_an_error(db, internal, public_site):
    port, hits = internal

    async def home(_):
        raise web.HTTPFound(f"http://127.0.0.1:{port}/")

    async with await _site({"/": home}) as site:
        r = await check_availability(str(site.make_url("")).rstrip("/"), manage=False)
    assert hits == [] and r.status == "error" and r.error == net.PRIVATE_REFUSED and not r.transient


async def test_the_monitors_name_on_another_port_is_not_trusted(db, internal, public_site):
    """A public-looking monitor whose DNS answers with the bot's host may be
    checked on its own port (ALLOW_PRIVATE_TARGETS=1), not scan the others."""
    port, hits = internal

    async def home(_):
        raise web.HTTPFound(f"http://localhost:{port}/admin")

    async with await _site({"/": home}) as site:
        r = await check_availability(str(site.make_url("")).rstrip("/"), manage=False)
    assert hits == [] and r.status == "error" and r.error == net.PRIVATE_REFUSED


class _SplitHorizon(AbstractResolver):
    """The home router's DNS: the owner's names answer with the LAN address."""

    def __init__(self, names: set[str]):
        self.names = names

    async def resolve(self, host, port=0, family=socket.AF_INET):
        assert host in self.names, host
        return [{"hostname": host, "host": "127.0.0.1", "port": port,
                 "family": socket.AF_INET, "proto": 0, "flags": 0}]

    async def close(self):
        pass


async def test_split_horizon_redirect_to_the_sites_own_domain_is_fine(db, monkeypatch):
    """example.com → www.example.com, both resolving to the LAN: the owner's
    site behind the home router, not an attack. Another domain of the same
    answer stays refused."""
    resolver = _SplitHorizon({"split-demo.test", "www.split-demo.test", "other-demo.test"})
    monkeypatch.setattr(availability, "owner_connector",
                        lambda url, **kw: net.owner_connector(url, resolver=resolver, **kw))
    hits = []

    async def home(request):
        hits.append(request.host)
        raise web.HTTPFound(f"http://www.split-demo.test:{request.url.port}/ok")

    async def away(request):
        raise web.HTTPFound(f"http://other-demo.test:{request.url.port}/ok")

    async def ok(request):
        hits.append(request.host)
        return web.Response(text="home sweet home")

    async with await _site({"/": home, "/away": away, "/ok": ok}, host="127.0.0.1") as site:
        r = await check_availability(f"http://split-demo.test:{site.port}", manage=False)
        assert r.status == "ok" and r.status_code == 200 and r.error is None
        assert hits == [f"split-demo.test:{site.port}", f"www.split-demo.test:{site.port}"]
        r = await check_availability(f"http://split-demo.test:{site.port}/away", manage=False)
        assert r.status == "error" and r.error == net.PRIVATE_REFUSED
    assert len(hits) == 2


async def test_api_check_keeps_its_key_at_home(db, internal):
    port, hits = internal

    async def api(_):
        raise web.HTTPTemporaryRedirect(f"http://localhost:{port}/steal")      # another origin

    async with await _site({"/api/x": api}) as site:
        url = str(site.make_url("/api/x"))
        sid = await db.get_or_create_site(url)
        await db.update_site_settings(sid, http_method="POST", http_headers='{"X-Api-Key": "k"}',
                                      http_body='{"password": "p"}')
        r = await check_availability(url, manage=False)
    assert hits == [] and r.status == "error" and not r.transient
    assert r.error == f"API перенаправляет на localhost:{port} — ключ туда не отправляю"


async def test_api_check_follows_redirects_at_home(db):
    """A redirect within the same origin («/health» → «/health/», http →
    https) is followed with the key, as before: no false «нет фразы»."""
    seen = []

    async def health(request):
        seen.append((request.method, request.path, request.headers.get("X-Api-Key")))
        raise web.HTTPPermanentRedirect("/api/health/")

    async def health_slash(request):
        seen.append((request.method, request.path, request.headers.get("X-Api-Key")))
        return web.json_response({"status": "healthy", "got": await request.text()})

    async with await _site({"/api/health": health, "/api/health/": health_slash}) as site:
        url = str(site.make_url("/api/health"))
        sid = await db.get_or_create_site(url)
        await db.update_site_settings(sid, http_method="POST", http_headers='{"X-Api-Key": "k"}',
                                      http_body='{"a": 1}', keyword="healthy")
        r = await check_availability(url, manage=False)
    assert r.status == "ok" and r.status_code == 200 and r.error is None
    assert seen == [("POST", "/api/health", "k"), ("POST", "/api/health/", "k")]


def test_what_stays_home():
    home = availability._stays_home
    assert home("https://api.example.com/x", "https://api.example.com/x/")
    assert home("http://api.example.com/x", "https://api.example.com/x")        # the usual upgrade
    assert home("http://api.example.com:8080/x", "http://api.example.com:8080/y")
    assert not home("https://api.example.com/x", "http://api.example.com/x")    # a downgrade
    assert not home("https://api.example.com/x", "https://www.example.com/x")   # another host
    assert not home("http://api.example.com:8080/x", "http://api.example.com:8081/y")
    assert not home("https://api.example.com/x", "ftp://api.example.com/x")
    assert not home("https://api.example.com/x", "https://api.example.com:abc/x")


async def test_nested_sitemaps_of_other_domains_are_not_fetched(internal):
    port, hits = internal

    async def index(request):
        return web.Response(text=f"<sitemapindex><sitemap><loc>http://{request.host}/a.xml</loc></sitemap>"
                                 f"<sitemap><loc>http://127.0.0.1:{port}/private.xml</loc></sitemap>"
                                 f"</sitemapindex>")

    async def a(request):
        return web.Response(text=f"<urlset><url><loc>http://{request.host}/p</loc></url></urlset>")

    async with await _site({"/sitemap.xml": index, "/a.xml": a}) as site:
        base = str(site.make_url("")).rstrip("/")
        async with aiohttp.ClientSession(connector=net.owner_connector(base)) as session:
            pages = await sitemap_urls(session, base)
    assert hits == [] and pages == [f"{base}/p"]


async def test_screenshot_image_url_into_the_lan_is_refused(db, cfg, internal, public_site, monkeypatch):
    port, hits = internal
    monkeypatch.setattr(screenshots, "RETRIES", 1)

    async def provider(_):
        return web.json_response({"status": "success",
                                  "data": {"screenshot": {"url": f"http://127.0.0.1:{port}/admin.png"}}})

    async with await _site({"/": provider}) as service:
        cfg(screenshot_template=str(service.make_url("/")) + "?url={url}")
        image, _ = await screenshots.fetch_screenshot("https://example.com")
    assert image is None and hits == []


def test_localhost_is_not_the_same_site_as_its_address():
    """The test sites live on localhost and the «internal» server on
    127.0.0.1: the trust of one must not spill over to the other."""
    assert host_of("http://localhost:1/") == "localhost"
    assert not net._same_domain("localhost", "127.0.0.1") and net._same_domain("www.example.com", "example.com")
    assert not net._same_domain("10.0.0.1", "10.0.0.2")
