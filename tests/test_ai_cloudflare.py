"""Item 7: whether Cloudflare lets AI assistants in is read from the zone's
«Block AI bots» (bot_management.ai_bots_protection), not guessed — and the
«🔎 Поиск и ИИ» screen says what was read. Cloudflare is a local server."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import buttons

from handlers import seo as seo_screen
from services import cloudflare, integrations

ZONE = "d" * 32


@pytest.fixture
async def cf(monkeypatch):
    state = {"value": "block", "status": 200, "asked": 0}

    async def zone(request):
        return web.json_response({"success": True, "errors": [],
                                  "result": {"id": ZONE, "name": "ex.test", "account": {"id": "acc"}}})

    async def bots(request):
        state["asked"] += 1
        if state["status"] != 200:
            return web.json_response({"success": False, "errors": [{"message": "forbidden"}]}, status=state["status"])
        return web.json_response({"success": True, "errors": [],
                                  "result": {"ai_bots_protection": state["value"], "fight_mode": False}})

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE}", zone)
    app.router.add_get(f"/client/v4/zones/{ZONE}/bot_management", bots)
    async with TestServer(app) as server:
        monkeypatch.setattr(cloudflare, "API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE)
        yield state


async def test_the_setting_is_read_once_per_zone_and_only_for_its_hosts(db, cf):
    assert await cloudflare.ai_bots("shop.ex.test") == "block"
    assert await cloudflare.ai_bots("ex.test") == "block" and cf["asked"] == 1      # one read per zone
    assert await cloudflare.ai_bots("other.test") is None                         # not this zone
    cloudflare.forget()
    cf["value"] = "disabled"
    assert await cloudflare.ai_bots("shop.ex.test") == "open"
    cloudflare.forget()
    cf["value"] = "only_on_ad_pages"
    assert await cloudflare.ai_bots("shop.ex.test") == "open"
    cloudflare.forget()
    cf["status"] = 403                                                            # no Bot Management Read
    assert await cloudflare.ai_bots("shop.ex.test") is None


async def test_no_token_no_verdict(db):
    assert await cloudflare.ai_bots("shop.ex.test") is None


def _snap(cf_ai=None, problems=()):
    return {"hosting": "cloudflare",
            "seo": {"status": "warning" if problems else "ok", "pages": 3, "no_js_chars": 900,
                    "problems": list(problems), "cf_ai": cf_ai}}


def test_the_screen_says_what_was_read():
    unknown = seo_screen.seo_text("https://shop.ex.test", _snap())
    assert "✅ Сайт открыт для поисковиков, помех нет." in unknown
    assert "💡 Проверить в Cloudflare" in buttons(seo_screen.seo_keyboard(1, _snap()["seo"], hosting="cloudflare"))

    open_ = seo_screen.seo_text("https://shop.ex.test", _snap("open"))
    assert "✅ Сайт открыт для поисковиков и ИИ-ассистентов, помех нет." in open_
    assert "видно только в его панели" not in open_
    assert "💡 Проверить в Cloudflare" not in buttons(seo_screen.seo_keyboard(1, _snap("open")["seo"], hosting="cloudflare"))

    problem = {"code": "ai_cf_blocked", "severity": "warning",
               "message": "в Cloudflare включено «Block AI bots» — ИИ-ассистенты получают отказ"}
    blocked = seo_screen.seo_text("https://shop.ex.test", _snap("block", [problem]))
    assert "🤖 ИИ-ассистенты: Cloudflare их не пускает — в зоне включено «Block AI bots»." in blocked
    assert "Cloudflare не пускает ИИ-ассистентов" in blocked
    kb = buttons(seo_screen.seo_keyboard(1, _snap("block", [problem])["seo"], hosting="cloudflare"))
    assert "1. 💡 Пустить ИИ-ассистентов в Cloudflare" in kb and "💡 Проверить в Cloudflare" not in kb
