import os

from aiohttp.test_utils import TestClient, TestServer

from db.database import get_heartbeats, get_state, set_state
from services import heartbeats, screenshots, secrets, settings
from web.webhook import create_app

SECRET = "unit-test-webhook-secret"
HB = "unit-test-heartbeat-secret"


async def _client():
    return TestClient(TestServer(create_app()))


async def test_heartbeat_secrets(bot):
    await settings.add_heartbeat_job("backup", 1440)
    await settings.add_heartbeat_job("backup2", 1440)
    token = secrets.heartbeat_token("backup")
    async with await _client() as c:
        assert (await c.get("/api/heartbeat/wrong/backup")).status == 403
        assert (await c.get(f"/api/heartbeat/{token}/backup")).status == 200
        assert "backup" in await get_heartbeats()
        # The webhook secret is public (it is in every site's widget code):
        # it must not keep a job alive.
        assert (await c.get(f"/api/heartbeat/{SECRET}/backup2")).status == 403
        assert "backup2" not in await get_heartbeats()
        # Recovery announcement clears the alerted flag.
        await set_state("hb_alerted:backup", "1")
        assert (await c.get(f"/api/heartbeat/{token}/backup")).status == 200
        assert await get_state("hb_alerted:backup") is None
        assert "снова на связи" in bot.sent[-1]["text"]


SOCIAL = {"title": "📣 Соцсети", "open_url": "https://t.me/social_tracker_bot",
          "items": [{"name": "Telegram-канал", "level": "ok", "text": "12 340, +18 за сутки"},
                    {"name": "LinkedIn", "level": "warning", "text": "cookie истекает через 3 дн."}]}


async def test_a_bot_sends_what_it_sees_with_its_mark(bot, db):
    """One screen for sites, bots and what they watch: SocialTracker POSTs a
    line per channel with its mark. A broken payload is refused loudly and
    is not a mark; «heartbeat»: false updates the lines only."""
    await settings.add_heartbeat_job("socialtracker", 1440)
    async with await _client() as c:
        r = await c.post(f"/api/heartbeat/{HB}/socialtracker", json={"items": [{"level": "ok"}]})
        assert r.status == 400 and "name is required" in await r.text()
        assert "socialtracker" not in await get_heartbeats()
        assert (await c.post(f"/api/heartbeat/{HB}/socialtracker", json={"items": [{"name": "x", "level": "fine"}]})).status == 400
        assert (await c.post(f"/api/heartbeat/{HB}/socialtracker", json={**SOCIAL, "open_url": "http://x"})).status == 400
        assert (await c.post("/api/heartbeat/wrong/socialtracker", json=SOCIAL)).status == 403
        assert (await c.post(f"/api/heartbeat/{HB}/socialtracker", json=SOCIAL)).status == 200
        assert "socialtracker" in await get_heartbeats()
        status = await heartbeats.job_status("socialtracker")
        assert status["title"] == "📣 Соцсети" and [i["level"] for i in status["items"]] == ["ok", "warning"]
        conn = await db.get_db()
        await conn.execute("UPDATE heartbeats SET last_ping = '2000-01-01 00:00:00' WHERE job = 'socialtracker'")
        await conn.commit()
        quiet = {**SOCIAL, "heartbeat": False, "items": [{"name": "Telegram-канал", "level": "critical", "text": "канал недоступен"}]}
        assert (await c.post(f"/api/heartbeat/{HB}/socialtracker", json=quiet)).status == 200
        assert (await get_heartbeats())["socialtracker"].startswith("2000")      # not a mark
        assert (await heartbeats.job_status("socialtracker"))["items"][0]["level"] == "critical"
        # A job nobody watches keeps no status; a plain GET is still a mark.
        assert (await c.post(f"/api/heartbeat/{HB}/stranger", json=SOCIAL)).status == 404
        assert await heartbeats.job_status("stranger") is None and "stranger" not in await get_heartbeats()
        assert (await c.get(f"/api/heartbeat/{HB}/socialtracker")).status == 200
    await settings.remove_heartbeat_job("socialtracker")


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


async def test_card_picture_is_served_on_a_signed_address(bot, db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    sid = await db.activate_or_create_site("https://ex.test")
    path = screenshots.shot_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\x89PNG fake")
    good = screenshots._sig(sid)
    async with await _client() as c:
        r = await c.get(f"/shot/{good}/{sid}")
        assert r.status == 200 and r.content_type == "image/png" and await r.read() == b"\x89PNG fake"
        assert (await c.get(f"/shot/{'0' * 16}/{sid}")).status == 404
        assert (await c.get(f"/shot/{good}/{sid + 1}")).status == 404
