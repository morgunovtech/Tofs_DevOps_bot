"""The setup checklist: grouping, buttons, one-tap fixes and the wizards."""

import json

from conftest import FakeCall, FakeMessage, FakeState, buttons

from handlers import setup
from services import (
    docker_api,
    gsc,
    integrations,
    public_urls,
    runtime,
    secrets,
    settings,
    yandex_webmaster,
)
from services.actions import deploy_hook_for


async def _items(monkeypatch, web_ok=True):
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(web_ok))
    return await setup.build_items()


async def _const(v):
    return v


def _by_key(items):
    return {i.key: i for i in items}


async def test_checklist_groups_and_buttons(bot, db, cfg, monkeypatch):
    runtime.set_bot_username("mrgdvpbot")
    cfg(on_railway=True, railway_public_domain="devops.up.railway.app", public_base_url="",
        self_heartbeat_url="")
    items = _by_key(await _items(monkeypatch))
    assert items["telegram"].status == "ok" and "mrgdvpbot" in items["telegram"].title
    assert items["url"].status == "ok" and "Railway" in items["url"].title
    assert items["sites"].status == "warn" and items["sites"].button[1] == "site_add"
    assert items["status_page"].status == "off" and items["status_page"].button[1] == "setup:sp_on"
    assert items["gsc"].status == "off" and items["yandex"].status == "off"
    assert "docker" not in items                       # not applicable on Railway
    assert items["watchdog"].status == "off"

    call = FakeCall("menu_diag")
    await setup.cb_checklist(call, FakeState())
    text = call.message.texts[-1]
    assert "✅ Работает:" in text and "⚠️ Требует внимания:" in text and "➕ Можно подключить" in text
    labels = buttons(call.message.reply_markup)
    assert "➕ Добавить сайт" in labels and "🌐 Включить статус-страницу" in labels
    assert "🔁 Проверить снова" in labels and "🔔 Тест алерта" in labels
    assert "Python" in text and "<i>" in text


async def test_public_url_warning_and_docker_on_self_host(bot, db, cfg, monkeypatch):
    cfg(on_railway=False, railway_public_domain="", public_base_url="")
    # GitHub runners ship a docker socket — pin the probe so the test means the same everywhere.
    monkeypatch.setattr(docker_api, "docker_available", lambda: False)
    items = _by_key(await _items(monkeypatch, web_ok=False))
    assert items["url"].status == "warn" and items["url"].button[1] == "setup:url"
    assert items["web"].status == "warn"
    assert items["docker"].status == "off" and items["docker"].button[1] == "setup:docker"
    monkeypatch.setattr(docker_api, "docker_available", lambda: True)
    assert _by_key(await _items(monkeypatch))["docker"].status == "ok"
    assert not public_urls.is_public()
    call = FakeCall("setup:url")
    await setup.cb_guide_url(call)
    assert "PUBLIC_BASE_URL" in call.message.texts[-1]


async def test_one_tap_fixes(bot, db, cfg):
    cfg(heartbeat_secret_env="")
    call = FakeCall("setup:sp_on")
    await setup.cb_status_page_on(call)
    assert settings.status_page_enabled() and "/status" in call.message.texts[-1]
    await settings.set_status_page(False)

    call = FakeCall("setup:so:off")
    await setup.cb_second_opinion(call)
    assert not integrations.second_opinion_enabled()
    call = FakeCall("setup:so:on")
    await setup.cb_second_opinion(call)
    assert integrations.second_opinion_enabled()

    before = secrets.heartbeat_secret()
    call = FakeCall("setup:hb_regen")
    await setup.cb_regen_heartbeat(call)
    assert secrets.heartbeat_secret() != before
    assert secrets.heartbeat_secret() in call.message.texts[-1]
    assert "HEARTBEAT_SECRET" not in call.message.texts[-1]   # env var not set → no warning


async def test_yandex_wizard_validates_live(bot, db, monkeypatch):
    calls = []

    async def fake_summaries():
        calls.append(integrations.yandex_token())
        return {"a.example": {}, "b.example": {}} if integrations.yandex_token().startswith("good") else None

    monkeypatch.setattr(yandex_webmaster, "get_summaries", fake_summaries)
    msg = FakeMessage("short")
    await setup.msg_yx_token(msg, FakeState())
    assert "Не похоже" in msg.texts[-1]

    msg = FakeMessage("bad-" + "x" * 30)
    await setup.msg_yx_token(msg, FakeState())
    assert "не принял" in msg.texts[-1] and not yandex_webmaster.available()

    msg = FakeMessage("good-" + "y" * 30)
    await setup.msg_yx_token(msg, FakeState())
    assert "хостов 2" in msg.texts[-1] and yandex_webmaster.available()
    assert integrations.source("yandex_token") == "bot"
    await integrations.load()                                   # persisted
    assert integrations.yandex_token().startswith("good")

    call = FakeCall("setup:yx_off")
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    await setup.cb_yx_off(call)
    assert not yandex_webmaster.available()


async def test_gsc_wizard(bot, db, monkeypatch):
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    probe = {"ok": False}

    async def fake_totals(start, end, page_prefix=None):
        return {"clicks": 1} if probe["ok"] else None

    monkeypatch.setattr(gsc, "search_totals", fake_totals)
    msg = FakeMessage("example.com")
    await setup.msg_gsc_prop(msg, FakeState())
    assert "Формат" in msg.texts[-1]
    msg = FakeMessage("sc-domain:example.com")
    await setup.msg_gsc_prop(msg, FakeState())
    assert integrations.gsc_property() == "sc-domain:example.com" and not gsc.available()

    msg = FakeMessage('{"type": "service_account"}')
    await setup.msg_gsc_key(msg, FakeState())
    assert "не JSON-ключ" in msg.texts[-1]
    key = json.dumps({"client_email": "svc@proj.iam", "private_key": "-----BEGIN PRIVATE KEY-----"})
    msg = FakeMessage(key)
    await setup.msg_gsc_key(msg, FakeState())
    assert gsc.available() and "svc@proj.iam" in msg.texts[-1] and "шаг 3" in msg.texts[-1]
    probe["ok"] = True
    msg = FakeMessage(key)
    await setup.msg_gsc_key(msg, FakeState())
    assert "API отвечает" in msg.texts[-1]
    items = _by_key(await setup.build_items())
    assert items["gsc"].status == "ok"

    call = FakeCall("setup:gsc_off")
    await setup.cb_gsc_off(call)
    assert not gsc.available()


async def test_cloudflare_wizard(bot, db, monkeypatch):
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    msg = FakeMessage("example.com http://not-https")
    await setup.msg_cf_hook(msg, FakeState())
    assert "Формат" in msg.texts[-1]
    msg = FakeMessage("Example.com https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/abc")
    await setup.msg_cf_hook(msg, FakeState())
    assert deploy_hook_for("https://example.com/") .endswith("/abc")
    assert deploy_hook_for("tcp://example.com:25") is None

    msg = FakeMessage("zone")
    await setup.msg_cf_zone(msg, FakeState())
    assert "32 hex" in msg.texts[-1]
    msg = FakeMessage("0123456789abcdef0123456789abcdef")
    await setup.msg_cf_zone(msg, FakeState())
    msg = FakeMessage("t" * 40)
    await setup.msg_cf_token(msg, FakeState())
    assert integrations.cf_purge_configured()
    items = _by_key(await setup.build_items())
    assert items["cloudflare"].status == "ok"

    call = FakeCall("setup:cf_off")
    await setup.cb_cf_off(call)
    assert not integrations.deploy_hooks() and not integrations.cf_purge_configured()


async def test_env_fallback_and_source(db, cfg):
    cfg(yandex_webmaster_token="env-token", cf_api_token="", cf_zone_id="")
    await integrations.load()
    assert integrations.yandex_token() == "env-token" and integrations.source("yandex_token") == "env"
    await integrations.set_value("yandex_token", "bot-token")
    assert integrations.yandex_token() == "bot-token" and integrations.source("yandex_token") == "bot"
    await integrations.set_value("yandex_token", None)
    assert integrations.yandex_token() == "env-token"
    assert integrations.source("cf_api_token") is None
