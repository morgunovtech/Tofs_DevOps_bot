"""The Cloudflare account seen from the bot: the audit log (a dangerous
change rings once, «✅ Это я» quiets its address, the day's list), the
permission a refusal names, and the cause of a short outage (a Pages
deployment or a change close in time). Cloudflare is a local server."""

from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, buttons
from test_i18n import screen_english

from handlers import cloudflare as cf_buttons
from monitors.base import AvailabilityResult
from reports import scheduler
from reports.formatter import format_availability_alert, format_recovery_alert
from services import causes, cf_audit, cloudflare, integrations
from utils import i18n

ZONE = "a" * 32
ACCOUNT = "acc1"


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def entry(id_: str, at: datetime, kind: str, *, product="", rtype="", desc="", ip="203.0.113.5",
          email="owner@ex.test", request=None, response=None, zone="ex.test", result="success") -> dict:
    """One Audit Logs v2 entry, the shape of the API reference."""
    return {"id": id_, "action": {"type": kind, "result": result, "description": desc, "time": iso(at)},
            "actor": {"email": email, "ip_address": ip, "type": "user", "context": "dash"},
            "resource": {"product": product, "type": rtype, "id": "r-" + id_, "request": request, "response": response},
            "zone": {"name": zone, "id": ZONE}}


@pytest.fixture
async def cf(monkeypatch):
    """Cloudflare API v4 on 127.0.0.1. state: the audit entries, the Pages
    projects and deployments, and a status to refuse the audit log with."""
    state = {"audit": [], "audit_status": 200, "projects": [], "deployments": {}, "asked": []}

    async def zone(request):
        return web.json_response({"success": True, "errors": [],
                                  "result": {"id": ZONE, "name": "ex.test", "account": {"id": ACCOUNT}}})

    async def audit(request):
        state["asked"].append(dict(request.query))
        if state["audit_status"] != 200:
            return web.json_response({"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]},
                                     status=state["audit_status"])
        since, before = (cloudflare.parse_time(request.query[k]) for k in ("since", "before"))
        rows = [e for e in state["audit"] if since <= cloudflare.parse_time(e["action"]["time"]) <= before]
        return web.json_response({"success": True, "errors": [], "result": rows, "result_info": {"count": len(rows)}})

    async def projects(request):
        return web.json_response({"success": True, "errors": [], "result": state["projects"],
                                  "result_info": {"page": 1, "total_pages": 1}})

    async def deployments(request):
        return web.json_response({"success": True, "errors": [],
                                  "result": state["deployments"].get(request.match_info["name"], [])})

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE}", zone)
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/logs/audit", audit)
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/pages/projects", projects)
    app.router.add_get(f"/client/v4/accounts/{ACCOUNT}/pages/projects/{{name}}/deployments", deployments)
    async with TestServer(app) as server:
        monkeypatch.setattr(cloudflare, "API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE)
        yield state


def test_dangerous_kinds_are_recognised_and_a_read_is_not_a_change():
    now = datetime.now(UTC)
    kinds = {
        "dns_deleted": entry("1", now, "delete", product="dns", rtype="dns_record", desc="Delete DNS record",
                             request={"name": "shop.ex.test"}),
        "ssl_weak": entry("2", now, "update", product="zone_settings", rtype="ssl", desc="Update zone setting",
                          response={"result": {"id": "ssl", "value": "flexible"}}),
        "redirect_deleted": entry("3", now, "delete", product="rulesets", rtype="rule", desc="Delete rule"),
        "token_created": entry("4", now, "create", product="api_tokens", rtype="token", desc="Create API token",
                               request='{"name": "ci deploy"}'),
        "dnssec": entry("5", now, "update", product="dnssec", desc="Update DNSSEC", response={"status": "disabled"}),
        "login": entry("6", now, "create", product="user", desc="Login"),
    }
    for kind, e in kinds.items():
        c = cf_audit.parse(e)
        assert c is not None and c.kind == kind, (kind, c)
    dns = cf_audit.parse(kinds["dns_deleted"])
    assert dns.detail == "shop.ex.test" and dns.ip == "203.0.113.5" and dns.zone == "ex.test"
    assert cf_audit.parse(kinds["ssl_weak"]).detail == "Flexible"
    assert cf_audit.parse(kinds["token_created"]).detail == "ci deploy"
    harmless = cf_audit.parse(entry("7", now, "update", product="zone_settings", rtype="ssl",
                                    response={"value": "strict"}, desc="Update zone setting"))
    assert harmless.kind == ""
    assert cf_audit.parse(entry("8", now, "view", product="dns", desc="List DNS records")).kind == ""
    assert cf_audit.parse(entry("9", now, "delete", product="dns", result="failure")) is None
    assert cf_audit.parse({"id": "x"}) is None


async def test_the_first_read_learns_then_a_new_address_rings_once(bot, db, cf):
    now = datetime.now(UTC)
    cf["audit"] = [entry("old", now - timedelta(hours=3), "update", product="dns", desc="Update DNS record"),
                   entry("read", now - timedelta(hours=2), "view", product="dns", desc="List DNS records")]
    await cf_audit.run()
    assert bot.sent == []                                            # learning: nothing rings
    assert cf["asked"][0]["since"].endswith("Z")                     # never a «+», which a query reads as a space
    assert cloudflare.parse_time(cf["asked"][0]["since"]) < now - timedelta(hours=23)   # the last day
    assert [x["id"] for x in await cf_audit.recent()] == ["old"]     # the read is not a change

    cf["audit"].append(entry("del", now - timedelta(minutes=1), "delete", product="dns", desc="Delete DNS record",
                             request={"name": "shop.ex.test"}, ip="198.51.100.7", email="who@else.test"))
    cf["audit"].append(entry("tweak", now - timedelta(minutes=1), "update", product="dns", desc="Update DNS record"))
    await cf_audit.run()
    [msg] = bot.sent
    assert msg["text"].startswith("🔴 Cloudflare: удалена запись DNS «shop.ex.test»")
    assert "who@else.test · с адреса 198.51.100.7" in msg["text"]
    assert msg["silent"] is False
    assert buttons(msg["reply_markup"]) == ["✅ Это я", "🔗 Журнал в Cloudflare", "🔗 API-токены"]
    assert msg["reply_markup"].inline_keyboard[1][0].url == f"https://dash.cloudflare.com/{ACCOUNT}/audit-log"
    await cf_audit.run()
    assert len(bot.sent) == 1                                        # read again (overlap): said once

    call = FakeCall(msg["reply_markup"].inline_keyboard[0][0].callback_data)
    await cf_buttons.cb_it_was_me(call)
    assert "198.51.100.7 запомнил" in call.message.texts[-1]
    cf["audit"].append(entry("del2", now, "delete", product="dns", desc="Delete DNS record", ip="198.51.100.7"))
    await cf_audit.run()
    assert len(bot.sent) == 1                                        # a known address: only listed
    assert {x["id"] for x in await cf_audit.recent()} == {"old", "del", "tweak", "del2"}


async def test_a_login_from_a_new_address_rings_and_is_not_a_change(bot, db, cf):
    now = datetime.now(UTC)
    await cf_audit.run()
    cf["audit"].append(entry("in", now, "create", product="user", desc="Login", ip="192.0.2.99"))
    await cf_audit.run()
    assert bot.sent[-1]["text"].startswith("🔴 Cloudflare: вход в аккаунт с нового адреса 192.0.2.99")
    assert await cf_audit.recent() == []


async def test_the_morning_line_and_the_list(bot, db, cf):
    assert await cf_audit.morning_line() is None                     # a quiet day: no line
    now = datetime.now(UTC)
    cf["audit"] = [entry(str(i), now - timedelta(minutes=10 + i), "update", product="dns",
                         desc="Update DNS record", request={"name": f"h{i}.ex.test"}) for i in range(3)]
    await cf_audit.run()
    assert await cf_audit.morning_line() == ("☁️ Cloudflare за сутки: 3 изменения — список под кнопкой "
                                             "«📋 Что изменилось»")
    call = FakeCall("cfa:list")
    await cf_buttons.cb_changes(call)
    text = call.message.texts[-1]
    assert text.startswith("☁️ Что изменилось в Cloudflare за сутки (3):")
    assert "Update DNS record · h0.ex.test · owner@ex.test" in text


async def test_a_refusal_names_the_permission_and_is_said_once(bot, db, cf):
    cf["audit_status"] = 403
    await cf_audit.run()
    h = await integrations.health("cf_audit")
    assert h["refused"] and h["reason"] == "токену не хватает права Account → Account Settings → Read"
    await scheduler.run_integration_health()
    msg = bot.sent[-1]
    assert msg["text"].startswith("🟠 журнал изменений Cloudflare: не работает — токену не хватает права "
                                  "Account → Account Settings → Read.")
    assert buttons(msg["reply_markup"]) == ["🔌 Проверить подключение"]


async def test_the_cause_of_a_short_outage(db, cf):
    start = datetime.now(UTC) - timedelta(minutes=30)
    cf["projects"] = [{"name": "shop", "subdomain": "shop-1x.pages.dev", "domains": ["shop.ex.test"]}]
    cf["deployments"]["shop"] = [
        {"id": "d2", "short_id": "f00dbabe", "created_on": iso(start + timedelta(minutes=6)),
         "deployment_trigger": {"metadata": {"commit_hash": "aaaaaaa1111"}}},           # the fix, not the cause
        {"id": "d1", "short_id": "7991da4f", "created_on": iso(start - timedelta(minutes=3)),
         "deployment_trigger": {"metadata": {"commit_hash": "7991da4cafe"}}}]
    cf["audit"] = [entry("r", start - timedelta(minutes=2), "delete", product="rulesets", desc="Delete rule")]
    await cf_audit.poll()       # the first read: learnt, listed
    words = await causes.near("https://shop.ex.test/", start)
    assert words.startswith("Рядом по времени: деплой Cloudflare Pages 7991da4 в ")
    assert "в Cloudflare: Delete rule" in words
    assert await causes.near("https://other.ex.test/", start + timedelta(hours=2)) is None
    assert await causes.near("https://shop.ex.test/", None) is None


async def test_no_cloudflare_no_line(db):
    assert await causes.near("https://shop.ex.test/", datetime.now(UTC)) is None


def test_the_cause_line_sits_before_the_promise_and_the_tech_line():
    r = AvailabilityResult(url="https://shop.ex.test", site_id=1, status="critical", error="HTTP 502",
                           status_code=502, response_time_ms=120)
    text = format_availability_alert(r, False, nearby="Рядом по времени: деплой Cloudflare Pages 7991da4 в 14:02.")
    lines = text.split("\n")
    i = lines.index("Рядом по времени: деплой Cloudflare Pages 7991da4 в 14:02.")
    assert lines[i + 1].startswith("Продолжаю проверять")
    up = AvailabilityResult(url="https://shop.ex.test", site_id=1, status_code=200, response_time_ms=90,
                            recovered=True, resolved_incident={"message": "Site down: HTTP 502"})
    assert "Рядом по времени: x.\nНичего делать не нужно." in format_recovery_alert(up, nearby="Рядом по времени: x.")


def test_the_alert_and_the_list_in_english():
    now = datetime.now(UTC)
    danger = [cf_audit.parse(entry("1", now, "delete", product="dns", desc="Delete DNS record",
                                   request={"name": "shop.ex.test"})),
              cf_audit.parse(entry("2", now, "create", product="user", desc="Login", ip="192.0.2.1"))]
    i18n.set_language("en")
    try:
        screen_english(cf_audit.alert_text(danger), cf_audit.alert_keyboard("k", ACCOUNT))
        screen_english(cf_audit.alert_text(danger[:1]))
    finally:
        i18n.set_language("ru")
