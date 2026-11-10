"""The link crawl: a ceiling per crawl, two connections per foreign host,
links resolved like a browser, DNS verdicts by error code, every
certificate checked; DNS and TLS of slow strangers do not take the thread
pool where the availability checks resolve their sites."""

import asyncio
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import dns.asyncresolver
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from handlers import links
from monitors import dns_checker, ssl_checker
from monitors.availability import check_availability
from monitors.base import LinkCheck, LinksResult
from monitors.links_checker import MAX_HOSTS, check_links, extract_links, pick_window


def _app(routes: dict) -> web.Application:
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_get(path, handler)
    return app


def _page(html: str):
    async def handler(_):
        return web.Response(text=html, content_type="text/html")
    return handler


async def test_a_huge_page_costs_300_requests_two_at_a_time(db):
    """A thousand links to a stranger's host: 300 per crawl, two in flight,
    the next crawl takes the next 300. The host is foreign to the site
    (127.0.0.1 against localhost) — own links are not throttled."""
    seen, live = [], {"now": 0, "peak": 0}

    async def far(request):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.005)
        live["now"] -= 1
        seen.append(request.path)
        return web.Response(text="ok")

    async with TestServer(_app({"/{n}": far}), host="127.0.0.1") as far_end:
        html = "".join(f'<a href="http://127.0.0.1:{far_end.port}/{i}">{i}</a>' for i in range(1000))
        async with TestServer(_app({"/": _page(html)}), host="localhost") as site:
            url = str(site.make_url("/"))
            r = await check_links(url)
            first = set(seen)
            seen.clear()
            await check_links(url)                                  # the next crawl, the next window
    assert (r.total_links, r.checked, len(first)) == (1000, 300, 300)
    assert live["peak"] <= 2 and len(seen) == 300 and not first & set(seen)


async def test_own_links_are_not_throttled(db):
    """The site's own pages are the owner's: up to eight at once, as before."""
    live = {"now": 0, "peak": 0}

    async def own(_):
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.05)
        live["now"] -= 1
        return web.Response(text="ok")

    html = "".join(f'<a href="/p/{i}">{i}</a>' for i in range(24))
    async with TestServer(_app({"/": _page(html), "/p/{n}": own}), host="localhost") as site:
        r = await check_links(str(site.make_url("/")))
    assert r.checked == 24 and not r.broken and live["peak"] > 2


async def test_a_manual_crawl_does_not_wait_for_a_big_crawl_of_another_site(db):
    """«🔗 Ссылки» of a small site, tapped while the scheduled crawl of a big
    one is busy with a slow stranger: the shared slots are not all taken by
    one crawl (two per host), and a site's own links need none."""
    async def slow(_):
        await asyncio.sleep(0.3)
        return web.Response(text="ok")

    async with TestServer(_app({"/{n}": slow}), host="127.0.0.1") as far_end:
        big_html = "".join(f'<a href="http://127.0.0.1:{far_end.port}/{i}">{i}</a>' for i in range(30))
        small_html = "".join(f'<a href="/p/{i}">{i}</a>' for i in range(5))
        async with TestServer(_app({"/": _page(big_html)}), host="localhost") as big, \
                TestServer(_app({"/": _page(small_html), "/p/{n}": _page("ok")}), host="localhost") as small:
            crawl = asyncio.create_task(check_links(str(big.make_url("/"))))
            await asyncio.sleep(0.3)
            start = time.monotonic()
            r = await check_links(str(small.make_url("/")), manage=False)
            took = time.monotonic() - start
            await crawl
    assert r.checked == 5 and not r.broken and took < 2


def test_links_resolve_like_a_browser():
    base = "https://ex.test/en/"
    assert extract_links('<a href="about/">', base) == ["https://ex.test/en/about/"]
    assert extract_links('<a href="/en/contacts/ ">', base) == ["https://ex.test/en/contacts/"]
    assert extract_links('<base href="/docs/"><a href="intro.html">', base) == ["https://ex.test/docs/intro.html"]
    assert extract_links('<a href="/doc#install"><a href="/doc#faq">', base) == ["https://ex.test/doc"]
    # A link with a typo in its address stays a link — a dead end for the visitor.
    assert extract_links('<a href="https://example.com]/post">', base) == ["https://example.com]/post"]


def test_window_caps_foreign_hosts_and_moves_on():
    links = [f"https://h{i}.example/" for i in range(60)]
    first, offset = pick_window(links, "https://site.example/", 0)
    assert len(first) == MAX_HOSTS and offset == MAX_HOSTS          # hosts 51–60 wait for the next crawl
    second, _ = pick_window(links, "https://site.example/", offset)
    assert set(links) - set(first) <= set(second)


async def test_a_broken_link_outside_the_window_keeps_the_problem_open(db):
    """400 links, /p/250 answers 404. The second crawl's window (300..399,
    0..199) does not reach it — but a link once found broken is rechecked
    in every crawl, and the problem closes only after a full round without
    a broken link: no «✅ ссылки в порядке» in between, no second alert when
    the window comes round again."""
    async def page(request):
        if request.match_info["n"] == "250":
            raise web.HTTPNotFound()
        return web.Response(text="ok")

    html = "".join(f'<a href="/p/{i:03d}">{i}</a>' for i in range(400))      # sorted = numbered
    async with TestServer(_app({"/": _page(html), "/p/{n}": page}), host="localhost") as site:
        url = str(site.make_url("/"))
        first = await check_links(url)
        assert [b.url.rsplit("/", 1)[1] for b in first.broken] == ["250"] and first.incident_new
        second = await check_links(url)
        assert [b.url.rsplit("/", 1)[1] for b in second.broken] == ["250"]
        assert second.checked == 301 and not second.recovered and not second.incident_new
        third = await check_links(url)
        assert [b.url.rsplit("/", 1)[1] for b in third.broken] == ["250"] and not third.incident_new
        assert [i["check_type"] for i in await db.get_active_incidents()] == ["links"]


async def test_the_problem_closes_after_a_clean_round(db):
    """The link is fixed: the crawl that no longer sees it does not say
    «fixed» yet — the rest of the page has not been looked at since; the
    one that completes the round does."""
    broken = {"on": True}

    async def page(request):
        if request.match_info["n"] == "250" and broken["on"]:
            raise web.HTTPNotFound()
        return web.Response(text="ok")

    html = "".join(f'<a href="/p/{i:03d}">{i}</a>' for i in range(400))
    async with TestServer(_app({"/": _page(html), "/p/{n}": page}), host="localhost") as site:
        url = str(site.make_url("/"))
        assert (await check_links(url)).incident_new
        broken["on"] = False
        clean = await check_links(url)                                   # window 300..399 + 0..199, /p/250 rechecked
        assert clean.checked == 301 and not clean.broken and not clean.recovered
        assert await db.get_active_incidents()
        closed = await check_links(url)                                  # window 200..399 + 0..99: the round is full
        assert closed.recovered and await db.get_active_incidents() == []


async def test_relative_links_start_from_the_page_after_a_redirect(db):
    async def home(_):
        raise web.HTTPFound("/en/")

    routes = {"/": home, "/en/": _page('<a href="about/">a</a>'), "/en/about/": _page("ok")}
    async with TestServer(_app(routes), host="localhost") as site:
        r = await check_links(str(site.make_url("/")))
    assert r.checked == 1 and not r.broken and not r.pending


async def test_a_dns_hiccup_waits_a_dead_name_does_not(db, monkeypatch):
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host == "flaky.example.net":
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        if host == "gone.example.net":
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    html = '<a href="https://flaky.example.net/x">1</a><a href="https://gone.example.net/y">2</a>'
    async with TestServer(_app({"/": _page(html)}), host="localhost") as site:
        r = await check_links(str(site.make_url("/")))
    assert [c.url for c in r.broken] == ["https://gone.example.net/y"]
    assert [c.url for c in r.pending] == ["https://flaky.example.net/x"]


def _self_signed(tmp_path) -> ssl.SSLContext:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "key.pem").write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(tmp_path / "cert.pem", tmp_path / "key.pem")
    return ctx


async def test_a_link_to_a_bad_certificate_is_broken_at_once(db, tmp_path):
    tls = TestServer(_app({"/": _page("ok")}), host="127.0.0.1")       # another site than localhost
    await tls.start_server(ssl=_self_signed(tmp_path))
    try:
        async with TestServer(_app({"/": _page(f'<a href="{tls.make_url("/")}">x</a>')}), host="localhost") as site:
            r = await check_links(str(site.make_url("/")))
    finally:
        await tls.close()
    assert len(r.broken) == 1 and "certificate" in r.broken[0].error.lower()


async def test_a_dead_503_is_a_problem_a_response_with_the_challenge_header_is_not(db):
    async def down(_):
        return web.Response(status=503, text="maintenance")

    async def challenge(_):
        return web.Response(status=503, text="checking your browser", headers={"cf-mitigated": "challenge"})

    routes = {"/": _page('<a href="/down">1</a><a href="/challenge">2</a>'), "/down": down, "/challenge": challenge}
    async with TestServer(_app(routes), host="localhost") as site:
        r = await check_links(str(site.make_url("/")))
    assert [c.url.rsplit("/", 1)[1] for c in r.pending] == ["down"] and not r.broken


async def test_silent_dns_of_links_does_not_stall_availability(db, monkeypatch):
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if str(host).endswith(".slow.example"):
            time.sleep(1.5)                                        # a DNS server that does not answer
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(2))               # the pool of a 1-vCPU server is about this
    html = "".join(f'<a href="https://h{i}.slow.example/">{i}</a>' for i in range(6))
    async with TestServer(_app({"/": _page(html)}), host="localhost") as crawled, \
            TestServer(_app({"/": _page("ok")}), host="localhost") as healthy:
        healthy_url = str(healthy.make_url("")).rstrip("/")
        await db.activate_or_create_site(healthy_url)
        crawl = asyncio.create_task(check_links(str(crawled.make_url("/"))))
        await asyncio.sleep(0.3)
        start = time.monotonic()
        r = await check_availability(healthy_url, manage=False)
        took = time.monotonic() - start
        await crawl
    assert r.ok and took < 1.0


async def test_certificate_check_does_not_hold_a_thread(db, monkeypatch):
    monkeypatch.setattr(ssl_checker, "HANDSHAKE_TIMEOUT", 1)
    stop = asyncio.Event()

    async def stall(reader, writer):                               # takes TCP, never answers TLS
        await stop.wait()
        writer.close()

    server = await asyncio.start_server(stall, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(1))
    check = asyncio.create_task(ssl_checker.check_ssl(f"https://127.0.0.1:{port}"))
    await asyncio.sleep(0.2)
    start = time.monotonic()
    await loop.run_in_executor(None, lambda: None)                 # what a site's name lookup needs
    took = time.monotonic() - start
    r = await check
    stop.set()
    server.close()
    await server.wait_closed()
    assert took < 0.5 and r.transient


async def test_dns_lookups_do_not_hold_a_thread(monkeypatch):
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))                                  # takes queries, never answers

    def resolver():
        r = dns.asyncresolver.Resolver(configure=False)
        r.nameservers, r.port, r.lifetime = ["127.0.0.1"], silent.getsockname()[1], 1.0
        return r

    monkeypatch.setattr(dns_checker, "_resolver", resolver)
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(1))
    lookups = asyncio.gather(*(dns_checker.resolve(f"h{i}.example", "A") for i in range(4)))
    await asyncio.sleep(0.1)
    start = time.monotonic()
    await loop.run_in_executor(None, lambda: None)
    took = time.monotonic() - start
    assert await lookups == [None] * 4 and took < 0.5
    silent.close()


async def test_links_screen_does_not_say_all_open_when_some_wait(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")

    async def crawl(url, manage=True):
        return LinksResult(url=url, site_id=sid, total_links=12, checked=12,
                           pending=[LinkCheck("https://a.test/", None, False, "timeout")])

    monkeypatch.setattr(links, "check_links", crawl)
    call = FakeCall(f"check_links:{sid}")
    await links.cb_check_links(call)
    assert call.message.texts[-1].startswith("🟡") and "все" not in call.message.texts[-1]


async def test_links_screen_says_how_many_of_the_page_it_checked(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")

    async def crawl(url, manage=True):
        return LinksResult(url=url, site_id=sid, total_links=1000, checked=300)

    monkeypatch.setattr(links, "check_links", crawl)
    call = FakeCall(f"check_links:{sid}")
    await links.cb_check_links(call)
    text = call.message.texts[-1]
    assert text.startswith("✅") and "(300)" in text and "На странице 1000 ссылок — за раз проверяю 300" in text
