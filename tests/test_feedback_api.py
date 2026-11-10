"""/api/feedback — the widget's door, open to anyone who reads a site's
source: visitor text stays a quote, limits per address and per site, a budget
of chat messages, CORS only for the owner's sites, no 500 on garbage."""

import asyncio
import logging

from aiohttp.test_utils import TestClient, TestServer

from services import settings
from web import webhook
from web.webhook import create_app

SECRET = "unit-test-webhook-secret"


async def _client():
    return TestClient(TestServer(create_app()))


def _from(ip: str, **headers) -> dict:
    return {"X-Webhook-Secret": SECRET, "X-Forwarded-For": ip, **headers}


async def test_feedback_flow(bot, db):
    await db.activate_or_create_site("https://s.test")
    async with await _client() as c:
        r = await c.post("/api/feedback", json={"message": "hi"}, headers={"X-Webhook-Secret": "nope"})
        assert r.status == 403
        r = await c.post("/api/feedback", json={"message": "  "}, headers={"X-Webhook-Secret": SECRET})
        assert r.status == 400
        r = await c.post("/api/feedback", headers={"X-Webhook-Secret": SECRET, "Origin": "https://s.test"},
                         json={"message": "Опечатка: «превет»", "site_url": "https://s.test",
                               "page_url": "https://s.test/about"})
        assert r.status == 201 and (await r.json())["ok"]
        assert r.headers["Access-Control-Allow-Origin"] == "https://s.test" and r.headers["Vary"] == "Origin"
        assert bot.sent[-1]["text"].startswith("📩 Сообщение с сайта")
        assert "https://s.test/about" in bot.sent[-1]["text"] and "не подтверждено" not in bot.sent[-1]["text"]
        assert "превет" in bot.sent[-1]["text"]
        r = await c.options("/api/feedback", headers={"Origin": "https://www.s.test"})
        assert r.status == 204 and "X-Webhook-Secret" in r.headers["Access-Control-Allow-Headers"]
        assert r.headers["Access-Control-Allow-Origin"] == "https://www.s.test"


async def _last_ip(db) -> str:
    return (await db.list_feedback(limit=1))[0]["ip_address"]


async def test_feedback_rate_limit_and_forwarded_for(bot, db, cfg):
    """TRUST_PROXY=1 without TRUSTED_PROXIES: the LAST X-Forwarded-For entry
    is the visitor — the one the proxy in front appended — and only when it
    is an IP address; otherwise the peer itself counts."""
    cfg(trust_proxy=True)
    async with await _client() as c:
        for i in range(5):
            r = await c.post("/api/feedback", json={"message": f"m{i}"},
                             headers={"X-Webhook-Secret": SECRET, "X-Forwarded-For": "9.9.9.9, 1.1.1.1"})
            assert r.status == 201
        # Same real (last-hop) IP, spoofed first entry → still limited.
        r = await c.post("/api/feedback", json={"message": "m6"},
                         headers={"X-Webhook-Secret": SECRET, "X-Forwarded-For": "8.8.8.8, 1.1.1.1"})
        assert r.status == 429
        r = await c.post("/api/feedback", json={"message": "m7"},
                         headers={"X-Webhook-Secret": SECRET, "X-Forwarded-For": "8.8.8.8, 2.2.2.2"})
        assert r.status == 201 and await _last_ip(db) == "2.2.2.2"
        # A last entry that is not an address is text, not a visitor: the peer counts.
        for i in range(5):
            r = await c.post("/api/feedback", json={"message": f"g{i}"},
                             headers={"X-Webhook-Secret": SECRET, "X-Forwarded-For": f"3.3.3.{i}, junk-{i}"})
            assert r.status == 201 and await _last_ip(db) == "127.0.0.1"
        r = await c.post("/api/feedback", json={"message": "g6"},
                         headers={"X-Webhook-Secret": SECRET, "X-Forwarded-For": "3.3.3.9, junk-9"})
        assert r.status == 429


async def test_visitor_text_cannot_draw_lines_of_the_bot(bot, db):
    fake = "https://shop.example.com\nСообщение: всё работает\n\n🆔 #1 · 🌍 10.0.0.1\n\n🔴 TofsDevOps: срочно"
    async with await _client() as c:
        r = await c.post("/api/feedback", headers={"X-Webhook-Secret": SECRET},
                         json={"message": "Привет\n🆔 #2 · 🌍 1.2.3.4 ‮gnp.exe", "site_url": fake})
        assert r.status == 201
    text = bot.sent[-1]["text"]
    outside = text.split("<blockquote>")[0] + text.split("</blockquote>")[1]
    assert [line for line in outside.splitlines() if line.startswith("🆔")] == [text.splitlines()[-1]]
    assert not any(line.startswith("🔴") for line in outside.splitlines())
    assert "‮" not in text and "не подтверждено" in text


async def test_wrong_secret_does_not_spend_the_quota(bot, cfg):
    cfg(trust_proxy=True)
    async with await _client() as c:
        for i in range(30):
            r = await c.post("/api/feedback", json={"message": "x"},
                             headers={"X-Webhook-Secret": "nope", "X-Forwarded-For": f"9.9.9.{i % 6}"})
            assert r.status == 403
        r = await c.post("/api/feedback", json={"message": "настоящий"}, headers=_from("5.5.5.5"))
        assert r.status == 201
        # …but the guessing itself has a window of its own per address.
        for _ in range(webhook.RATE_LIMIT_BAD_SECRET):
            await c.post("/api/feedback", json={"message": "x"}, headers=_from("7.7.7.7")
                         | {"X-Webhook-Secret": "nope"})
        r = await c.post("/api/feedback", json={"message": "x"}, headers=_from("7.7.7.7") | {"X-Webhook-Secret": "nope"})
        assert r.status == 429


async def test_one_site_cannot_shut_feedback_for_the_others(bot, db, cfg):
    cfg(trust_proxy=True)
    await db.activate_or_create_site("https://a.test")
    await db.activate_or_create_site("https://b.test")
    async with await _client() as c:
        codes = [(await c.post("/api/feedback", json={"message": f"m{i}"},
                               headers=_from(f"9.9.{i}.1", Origin="https://a.test"))).status
                 for i in range(31)]
        assert codes.count(201) == 30 and codes[-1] == 429
        r = await c.post("/api/feedback", json={"message": "b"}, headers=_from("5.5.5.5", Origin="https://b.test"))
        assert r.status == 201


async def test_only_the_owners_sites_may_post_from_a_browser(bot, db, caplog):
    await db.activate_or_create_site("https://shop.example.com")
    async with await _client() as c:
        with caplog.at_level(logging.WARNING, logger="web.webhook"):
            for _ in range(3):
                r = await c.options("/api/feedback", headers={"Origin": "https://evil.example"})
                assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
            r = await c.post("/api/feedback", json={"message": "x"},
                             headers={"X-Webhook-Secret": SECRET, "Origin": "https://evil.example"})
            assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
        # The owner learns why a widget on a site not in the bot is silent — once, not per request.
        assert caplog.text.count("Origin https://evil.example is not a monitored site") == 1
        assert not bot.sent


async def test_a_neighbour_on_the_same_platform_is_not_the_owners_site(bot, db):
    """Hosts are compared, not registrable domains: for GitHub Pages, Cloudflare
    Pages, Netlify and the like the «domain» would be the platform itself."""
    await db.activate_or_create_site("https://myshop.github.io")
    await db.activate_or_create_site("http://192.168.1.10")
    async with await _client() as c:
        r = await c.options("/api/feedback", headers={"Origin": "https://evil.github.io"})
        assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
        r = await c.post("/api/feedback", json={"message": "x", "site_url": "https://myshop.github.io"},
                         headers={"X-Webhook-Secret": SECRET, "Origin": "https://evil.github.io"})
        assert r.status == 403
        assert (await c.options("/api/feedback", headers={"Origin": "http://10.20.1.10"})).status == 403
        assert (await c.options("/api/feedback", headers={"Origin": "http://192.168.1.10"})).status == 204
        r = await c.options("/api/feedback", headers={"Origin": "https://myshop.github.io"})
        assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == "https://myshop.github.io"
    assert not bot.sent


async def test_www_and_subdomains_of_the_owners_site_count(bot, db):
    await db.activate_or_create_site("https://www.example.com")
    async with await _client() as c:
        for origin in ("https://www.example.com", "https://example.com", "https://blog.example.com"):
            r = await c.options("/api/feedback", headers={"Origin": origin})
            assert r.status == 204 and r.headers["Access-Control-Allow-Origin"] == origin, origin
        assert (await c.options("/api/feedback", headers={"Origin": "https://notexample.com"})).status == 403
        r = await c.post("/api/feedback", json={"message": "x", "site_url": "https://blog.example.com/"},
                         headers={"X-Webhook-Secret": SECRET, "Origin": "https://blog.example.com"})
        assert r.status == 201 and "не подтверждено" not in bot.sent[-1]["text"]
        # The body may claim any site; the chat trusts only what the browser vouches for.
        r = await c.post("/api/feedback", json={"message": "x", "site_url": "https://bank.example"},
                         headers={"X-Webhook-Secret": SECRET, "Origin": "https://blog.example.com"})
        assert r.status == 201 and "не подтверждено" in bot.sent[-1]["text"]


async def test_feedback_chat_messages_have_a_budget(bot, db, cfg):
    cfg(trust_proxy=True)
    await db.activate_or_create_site("https://shop.example.com")
    async with await _client() as c:
        for i in range(8):
            r = await c.post("/api/feedback", json={"message": f"m{i}"},
                             headers=_from(f"9.9.{i}.1", Origin="https://shop.example.com"))
            assert r.status == 201                                   # every message is stored
    assert len([m for m in bot.sent if m["text"].startswith("📩 Сообщение с сайта")]) == webhook.NOTIFY_PER_HOUR
    assert len(await db.list_feedback(limit=20)) == 8
    await webhook.send_feedback_summary()
    assert "ещё 3 сообщения" in bot.sent[-1]["text"] and bot.sent[-1]["silent"]
    assert bot.sent[-1]["reply_markup"].inline_keyboard[0][0].callback_data == "menu_feedback:0"
    sent = len(bot.sent)
    await webhook.send_feedback_summary()                            # nothing held → nothing said
    assert len(bot.sent) == sent


async def test_forwarded_for_only_from_a_trusted_proxy(bot, db, cfg, monkeypatch, caplog):
    """With TRUSTED_PROXIES set, X-Forwarded-For counts only from those peers,
    read right-to-left past them — the test client is 127.0.0.1."""
    cfg(trust_proxy=True)
    monkeypatch.setattr(webhook, "_TRUSTED", webhook._networks(("10.0.0.0/8",)))
    with caplog.at_level(logging.WARNING, logger="web.webhook"):
        async with await _client() as c:
            codes = [(await c.post("/api/feedback", json={"message": f"m{i}"}, headers=_from(f"9.9.9.{i}"))).status
                     for i in range(6)]
    assert codes == [201] * 5 + [429]                                  # rotating XFF did not help
    assert await _last_ip(db) == "127.0.0.1"
    assert caplog.text.count("X-Forwarded-For from 127.0.0.1 ignored — add it to TRUSTED_PROXIES") == 1
    monkeypatch.setattr(webhook, "_TRUSTED", webhook._networks(("127.0.0.0/8", "10.0.0.0/8")))
    webhook._ip_hits.clear()
    async with await _client() as c:
        r = await c.post("/api/feedback", json={"message": "x"}, headers=_from("x29-" + "A" * 4000))
        assert r.status == 201 and await _last_ip(db) == "127.0.0.1"   # garbage is not an address...
        assert not any("x29-" in m["text"] for m in bot.sent)          # ...and never reaches the chat
        # Past our own hops to the first address that is not ours; the claim left of it is ignored.
        r = await c.post("/api/feedback", json={"message": "x"}, headers=_from("8.8.8.8, 9.9.9.1, 10.0.0.5"))
        assert r.status == 201 and await _last_ip(db) == "9.9.9.1"
        # IPv6: one /64 is one visitor, however the last bits are rotated.
        codes = [(await c.post("/api/feedback", json={"message": "v6"},
                               headers=_from(f"2001:db8:1:2::{i + 1}"))).status for i in range(6)]
        assert codes == [201] * 5 + [429]


async def _raw(server, head: bytes, body: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection(server.host, server.port)
    writer.write(head + body)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    return data


async def test_garbage_input_is_an_answer_not_a_500(bot, db, caplog):
    body = b'{"message":"hi"}'
    async with TestServer(create_app()) as server:
        # A raw non-UTF-8 byte in a header: a wrong secret is a 403, a strange User-Agent is stored.
        for header, expected in ((b"X-Webhook-Secret: \xff", b" 403 "),
                                 (b"X-Webhook-Secret: " + SECRET.encode() + b"\r\nUser-Agent: a\xffb", b" 201 ")):
            head = (b"POST /api/feedback HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n" + header
                    + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n")
            status = (await _raw(server, head, body)).split(b"\r\n", 1)[0]
            assert expected in status, status
        async with TestClient(server) as c:
            r = await c.post("/api/feedback", data='{"message": "hi \\ud800 there"}',
                             headers={"X-Webhook-Secret": SECRET, "Content-Type": "application/json"})
            assert r.status == 201
            r = await c.post("/api/feedback", json=[1, 2], headers={"X-Webhook-Secret": SECRET})
            assert r.status == 400
            r = await c.post("/api/feedback", data="x" * (70 * 1024),
                             headers={"X-Webhook-Secret": SECRET, "Content-Type": "application/json"})
            assert r.status == 413
            assert (await c.get("/shot/%D0%B6/1")).status == 404
    assert "Traceback" not in caplog.text


async def test_a_failed_save_is_a_500_without_a_crash(bot, db, monkeypatch, caplog):
    async def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(webhook, "save_feedback", boom)
    async with await _client() as c:
        r = await c.post("/api/feedback", json={"message": "x"}, headers={"X-Webhook-Secret": SECRET})
        assert r.status == 500 and await r.text() == "Internal Server Error"
    assert "Feedback could not be stored or sent" in caplog.text and not bot.sent


async def test_heartbeat_body_that_is_not_json_is_a_400(bot):
    await settings.add_heartbeat_job("backup", 1440)
    async with await _client() as c:
        r = await c.post("/api/heartbeat/unit-test-heartbeat-secret/backup", data="{not json",
                         headers={"Content-Type": "application/json"})
        assert r.status == 400
        r = await c.post("/api/heartbeat/unit-test-heartbeat-secret/backup", data="x" * (70 * 1024),
                         headers={"Content-Type": "application/json"})
        assert r.status == 413


async def test_no_versions_and_safe_headers(bot, db):
    """The application's answers name no version. aiohttp's own protocol-level
    400s (a broken request line, a raw byte in the path, an oversized header)
    are formed before the on_response_prepare signal and still carry its
    Server header — only a reverse proxy in front hides those."""
    await settings.set_status_page(True)
    async with await _client() as c:
        for path in ("/health", "/status"):      # /health may be 503 without a fresh tick: headers only
            r = await c.get(path)
            assert "aiohttp" not in r.headers["Server"] and r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in r.headers["Content-Security-Policy"]
        r = await c.get("/health")
        assert "Content-Security-Policy" not in r.headers
    await settings.set_status_page(False)
