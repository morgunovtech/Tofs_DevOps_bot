"""Secrets stay where they belong: not in log lines, not in texts the owner
or the helpers see, not at check-host, not in a repository, not in the chat
history; a regenerated secret stays regenerated; /shot has its own key."""

import io
import logging
import os
import pathlib
import subprocess
import sys

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeMessage, FakeState

import main
from db import database
from handlers import common, setup, sites
from monitors.availability import check_availability
from monitors.base import AvailabilityResult, SslResult
from monitors.second_opinion import endpoint_for, repeatable
from services import actions, crux, heartbeats, integrations, screenshots, secrets, settings
from utils.redact import RedactingFormatter, redact
from web.webhook import create_app

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOKEN = "123456:" + "T" * 30            # shaped like a bot token, not a real one


def test_redact_masks_tokens_keys_and_passwords():
    line = (f"404, url='https://api.telegram.org/file/bot{TOKEN}/documents/key.json' "
            "then https://x.test/v1?key=AIzaSECRET&q=1 and socks5://user:pa55@proxy.test:1080 "
            "and https://hc-ping.com/known-secret-uuid")
    out = redact(line, ["known-secret-uuid"])
    assert TOKEN.split(":")[1] not in out and "AIzaSECRET" not in out and "pa55" not in out
    assert "known-secret-uuid" not in out
    assert "documents/key.json" in out and "q=1" in out and "proxy.test:1080" in out


def test_formatter_masks_the_traceback_too():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(RedactingFormatter("%(levelname)s %(message)s", lambda: ["hook-secret-part"]))
    log = logging.getLogger("test.redact")
    log.addHandler(handler)
    log.propagate = False
    try:
        try:
            raise RuntimeError(f"url='https://h.test/file/bot{TOKEN}/x' and /deploy_hooks/hook-secret-part")
        except RuntimeError:
            log.exception("Handler error")
    finally:
        log.removeHandler(handler)
    text = stream.getvalue()
    assert "Traceback" in text and "RuntimeError" in text
    assert TOKEN.split(":")[1] not in text and "hook-secret-part" not in text


def test_main_logs_through_the_mask(tmp_path):
    code = ("import logging, main\n"
            f"logging.getLogger('aiohttp.client').error(\"404, url='https://h.test/file/bot{TOKEN}/doc.json'\")\n")
    env = {**os.environ, "TELEGRAM_BOT_TOKEN": TOKEN, "DB_PATH": str(tmp_path / "bot.db"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "doc.json" in out.stderr
    assert TOKEN.split(":")[1] not in out.stderr


async def test_crux_key_goes_in_a_header_and_never_into_the_log(db, monkeypatch, caplog):
    seen = []

    async def handler(request):
        seen.append({"query": request.query_string, "key": request.headers.get("X-Goog-Api-Key")})
        return web.Response(text="<html>proxy page</html>", content_type="text/html")
    app = web.Application()
    app.router.add_post("/v1/records:queryRecord", handler)
    async with TestServer(app) as server:
        monkeypatch.setattr(crux, "API", str(server.make_url("/v1/records:queryRecord")))
        with caplog.at_level(logging.WARNING):
            assert await crux.query("https://example.com", key="AIzaFAKEKEY0123456789") is None
    assert seen == [{"query": "", "key": "AIzaFAKEKEY0123456789"}]
    assert "AIzaFAKEKEY0123456789" not in caplog.text


async def test_a_broken_deploy_hook_does_not_show_its_secret(db, monkeypatch, caplog):
    hook = "api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/SECRETPART"   # «https://» forgotten
    monkeypatch.setattr(integrations, "deploy_hooks", lambda: {"example.com": hook})
    with caplog.at_level(logging.WARNING):
        res = await actions.trigger_redeploy("https://example.com")
    assert not res.ok and "SECRETPART" not in res.text and "SECRETPART" not in caplog.text


def test_an_address_with_a_token_never_goes_to_check_host():
    assert not repeatable({"url": "https://api.example.com/v1/health?token=abc"})
    assert endpoint_for("https://api.example.com/v1/health?token=abc") is None
    assert endpoint_for("https://user:pass@api.example.com/health") is None
    assert endpoint_for("https://example.com/") == ("check-http", "https://example.com/")


async def test_a_redirect_loop_does_not_put_the_token_into_the_error(db):
    async def loop(request):
        raise web.HTTPFound(f"/api/health?token=SECRET{len(request.path_qs)}")
    app = web.Application()
    app.router.add_get("/api/health", loop)
    async with TestServer(app) as server:
        r = await check_availability(str(server.make_url("/api/health?token=SECRET")), manage=False)
    assert r.status == "error" and "SECRET" not in (r.error or "")


async def test_watchdog_failure_logs_the_host_not_the_address(db, caplog):
    async def loop(_):
        raise web.HTTPFound("/ping/secret-uuid-0001")
    app = web.Application()
    app.router.add_get("/{tail:.*}", loop)
    async with TestServer(app) as server:
        with caplog.at_level(logging.WARNING):
            assert await heartbeats.ping_watchdog(str(server.make_url("/ping/secret-uuid-0001"))) == "не отвечает"
    assert "secret-uuid-0001" not in caplog.text


async def test_equal_env_secrets_never_open_the_heartbeat_to_the_widget_secret(bot, db, cfg):
    cfg(webhook_secret_env="same-value-for-both", heartbeat_secret_env="same-value-for-both")
    await secrets.load()
    assert secrets.heartbeat_secret() != secrets.webhook_secret()
    assert secrets.ignored_env() == ["HEARTBEAT_SECRET"]
    await settings.add_heartbeat_job("backup", 1440)
    async with TestClient(TestServer(create_app())) as c:
        assert (await c.get("/api/heartbeat/same-value-for-both/backup")).status == 403
        assert (await c.get(f"/api/heartbeat/{secrets.heartbeat_token('backup')}/backup")).status == 200
        assert (await c.get(f"/api/heartbeat/{secrets.heartbeat_secret()}/backup")).status == 200   # the old address


async def test_a_regenerated_secret_survives_a_restart(db, cfg):
    cfg(heartbeat_secret_env="old-leaked-heartbeat-secret")
    await secrets.load()
    assert secrets.heartbeat_secret() == "old-leaked-heartbeat-secret"
    new = await secrets.regenerate("heartbeat")
    await secrets.load()                                       # what a restart does
    assert secrets.heartbeat_secret() == new
    assert "HEARTBEAT_SECRET" in secrets.ignored_env()


async def test_a_regenerated_secret_survives_a_restore_from_a_copy(db, cfg, tmp_path):
    """The revocation mark and the /shot key live in bot_state: a restored
    copy without them (a shareable copy) must not bring the leaked env value back."""
    cfg(heartbeat_secret_env="old-leaked-heartbeat-secret")
    await db.activate_or_create_site("https://a.test")
    copy = tmp_path / "share.db"
    await database.shareable_copy(str(copy))                   # made before the rotation: no secret:* rows
    await secrets.load()
    new = await secrets.regenerate("heartbeat")
    shots_before = secrets.shots_key()
    await database.restore_from_file(str(copy))
    await secrets.load()
    assert secrets.heartbeat_secret() == new and secrets.shots_key() == shots_before


async def test_shot_signatures_do_not_depend_on_the_heartbeat_secret(db):
    before = screenshots._sig(1)
    await secrets.regenerate("heartbeat")
    assert screenshots._sig(1) == before and secrets.shots_key() != secrets.heartbeat_secret()


async def test_a_mistyped_secret_is_deleted_too(bot, db):
    message = FakeMessage("OAuth y0_not-a-token-format")
    await setup.msg_yx_token(message, FakeState())
    assert message.deleted and "Не похоже на токен" in message.texts[-1]


async def test_an_address_with_a_password_is_deleted_from_the_chat(bot, db, monkeypatch):
    async def quiet_check(url, manage=True):
        return AvailabilityResult(url=url, site_id=1, status="ok", response_time_ms=10)

    async def no_cert(url, manage=True):
        return SslResult(url=url, site_id=1)          # api.example.test is not a real host

    async def nothing(*args, **kwargs):
        return None
    monkeypatch.setattr(sites, "check_availability", quiet_check)
    monkeypatch.setattr(sites, "check_ssl", no_cert)
    for name in ("check_domain", "alt_host_note", "suggest_keyword"):
        monkeypatch.setattr(sites, name, nothing)
    message = FakeMessage("https://user:pass@api.example.test/api/health")
    await sites.msg_site_add(message, FakeState())
    assert message.deleted


async def test_the_owner_hears_when_a_secret_could_not_be_deleted(bot, db):
    message = FakeMessage("x" * 40)

    async def refuse():
        raise TelegramBadRequest(DeleteMessage(chat_id=1, message_id=1), "message can't be deleted")
    message.delete = refuse
    assert await common.forget_secret(message) is False
    assert message.texts[-1] == common.NOT_DELETED


def test_a_token_in_the_path_never_goes_to_check_host():
    """Telegram keeps the bot token in the PATH, webhook services their key:
    a query-only rule sent the whole address to an outside service."""
    # Shaped like a bot token (9 digits, a colon, 35 characters), assembled
    # here so that no token-shaped literal sits in the repository.
    bot_api = "https://api.telegram.org/bot" + "1" * 9 + ":AAF" + "x" * 32 + "/getMe"
    assert not repeatable({"url": bot_api}) and endpoint_for(bot_api) is None
    hook = "https://api.example.com/hooks/x7Kq9mPz2LwQ4rT8vB1nC6yH"
    assert not repeatable({"url": hook}) and endpoint_for(hook) is None
    uuid = "https://api.example.com/v1/3f2504e0-4f89-11d3-9a0c-0305e82c3301/status"
    assert endpoint_for(uuid) is None
    plain = "https://api.example.com/v1/health/deep-check"           # words and a version: no key
    assert repeatable({"url": plain}) and endpoint_for(plain) == ("check-http", plain)


def test_redact_knows_a_key_under_any_name():
    """access_key (apiflash), auth_token, x_api_key: the parameter's name ends
    in the word, and the value is a secret whatever stands before it."""
    line = ("https://api.apiflash.com/v1/urltoimage?access_key=ABCDEF1234567890&url=x "
            "https://h.test/?auth_token=SECRET12345678 https://h.test/?x_api_key=ABCDEFGH1234 "
            "https://h.test/?design=modern&page=2")
    out = redact(line)
    for secret in ("ABCDEF1234567890", "SECRET12345678", "ABCDEFGH1234"):
        assert secret not in out
    assert "design=modern" in out and "page=2" in out and "url=x" in out


def test_the_screenshot_providers_key_is_among_the_masked_values(cfg):
    cfg(screenshot_template="https://api.shots.test/?token_value=KEY0123456789&url={url}&full=true")
    assert "KEY0123456789" in main.secret_values()
    assert "{url}" not in main.secret_values()           # the field is no secret; short switches are filtered by redact
