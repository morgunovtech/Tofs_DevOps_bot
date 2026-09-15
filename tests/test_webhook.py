from aiohttp.test_utils import TestClient, TestServer

from db.database import get_heartbeats, get_state, set_state
from services import settings
from web import webhook
from web.webhook import create_app

SECRET = "unit-test-webhook-secret"
HB = "unit-test-heartbeat-secret"


async def _client():
    return TestClient(TestServer(create_app()))


async def test_feedback_flow(bot):
    async with await _client() as c:
        r = await c.post("/api/feedback", json={"message": "hi"}, headers={"X-Webhook-Secret": "nope"})
        assert r.status == 403
        r = await c.post("/api/feedback", json={"message": "  "}, headers={"X-Webhook-Secret": SECRET})
        assert r.status == 400
        r = await c.post("/api/feedback", headers={"X-Webhook-Secret": SECRET},
                         json={"message": "Опечатка: «превет»", "site_url": "https://s.test",
                               "page_url": "https://s.test/about"})
        assert r.status == 201 and (await r.json())["ok"]
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert bot.sent[-1]["text"].startswith("📩 Сообщение с сайта")
        assert "https://s.test/about" in bot.sent[-1]["text"]
        assert "превет" in bot.sent[-1]["text"]
        r = await c.options("/api/feedback")
        assert r.status == 204 and "X-Webhook-Secret" in r.headers["Access-Control-Allow-Headers"]


async def test_feedback_rate_limit_and_forwarded_for(bot, cfg, monkeypatch):
    monkeypatch.setattr(webhook, "_ip_hits", webhook.defaultdict(webhook.deque))
    monkeypatch.setattr(webhook, "_global_hits", webhook.deque())
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
        assert r.status == 201


async def test_heartbeat_secrets(bot):
    async with await _client() as c:
        assert (await c.get("/api/heartbeat/wrong/backup")).status == 403
        assert (await c.get(f"/api/heartbeat/{HB}/backup")).status == 200
        assert "backup" in await get_heartbeats()
        # Legacy pings with the (public) webhook secret still count, with one warning.
        n = len(bot.sent)
        assert (await c.get(f"/api/heartbeat/{SECRET}/legacy")).status == 200
        assert (await c.get(f"/api/heartbeat/{SECRET}/legacy")).status == 200
        assert len(bot.sent) == n + 1 and "старым секретом" in bot.sent[-1]["text"]
        assert HB in bot.sent[-1]["text"]
        # Recovery announcement clears the alerted flag.
        await set_state("hb_alerted:backup", "1")
        assert (await c.get(f"/api/heartbeat/{HB}/backup")).status == 200
        assert await get_state("hb_alerted:backup") is None
        assert "снова подаёт" in bot.sent[-1]["text"]


async def test_status_page_and_json_and_badge(bot, db):
    sid = await db.get_or_create_site("https://example.com")
    await db.save_check(sid, "availability", "ok", response_time_ms=120, status_code=200)
    async with await _client() as c:
        assert (await c.get("/status")).status == 404          # disabled by default
        await settings.set_status_page(True)
        r = await c.get("/status")
        assert r.status == 200 and "example.com" in await r.text()
        assert r.headers["X-Robots-Tag"].startswith("noindex")
        r = await c.get("/status.json")
        data = await r.json()
        assert data["status"] == "up" and data["sites"][0]["label"] == "example.com"
        assert {"up24", "up7d", "up30d", "up90d"} <= set(data["sites"][0])
        r = await c.get(f"/status/badge/{sid}.svg")
        assert r.status == 200 and "100.0%" in await r.text()
        assert (await c.get("/status/badge/999.svg")).status == 404
        assert (await c.get("/status/badge/abc.svg")).status == 404
        assert (await c.get("/health")).status == 200
        r = await c.get("/feedback-widget.js")
        assert r.status == 200 and "DevOpsFeedback" in await r.text()
        await settings.set_status_page(False)


async def test_status_page_slug(bot, db, cfg):
    cfg(status_page_slug="s3cret")
    await settings.set_status_page(True)
    async with await _client() as c:
        assert (await c.get("/status")).status == 404
        assert (await c.get("/status/wrong")).status == 404
        assert (await c.get("/status/s3cret")).status == 200
        assert (await c.get("/status/s3cret.json")).status == 200
    await settings.set_status_page(False)
