"""«🔌 Подключения»: a destructive tap asks first and «Отключено» is true even
for a value from .env; «не достучался» is not «не принял»; Cloudflare is
checked before «✅ сохранён»; the screen does not wait for probes in a row."""

from aiogram import Bot
from aiogram.methods import EditMessageText
from conftest import FakeMessage, FakeState
from test_dispatcher import DP, MockedSession, _callback

from handlers import setup
from services import github, integrations, secrets, settings, yandex_webmaster
from services.actions import deploy_hook_for
from services.integrations import Probe


async def _const(value):
    return value


async def _screens(tg, session, data, update_id):
    session.calls.clear()
    await DP.feed_raw_update(tg, _callback(777, data, update_id))
    return [m for m in session.calls if isinstance(m, EditMessageText)]


async def test_off_asks_first_and_switches_off_a_value_from_env(bot, db, cfg, monkeypatch):
    cfg(yandex_webmaster_token="env-token-" + "x" * 20, deploy_hooks={"example.com": "https://hooks.test/env"})
    await integrations.load()
    monkeypatch.setattr(yandex_webmaster, "check_token", lambda: _const(Probe.OK))
    monkeypatch.setattr(setup, "_yandex_probe", lambda: _const(1))
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    guide = await _screens(tg, session, "setup:yx", 901)
    assert "setup:yx_off" in [b.callback_data for row in guide[-1].reply_markup.inline_keyboard for b in row]
    asked = await _screens(tg, session, "setup:yx_off", 902)
    assert yandex_webmaster.available() and ".env" in asked[-1].text            # one tap: only the question
    await _screens(tg, session, "setup:yx_off:ok", 903)
    assert not yandex_webmaster.available() and integrations.source("yandex_token") == "off"
    await integrations.load()
    assert not yandex_webmaster.available()                                      # survives a restart
    assert "yandex_token" not in integrations.stored()                           # a tombstone is not a value
    guide = await _screens(tg, session, "setup:yx", 904)
    datas = [b.callback_data for row in guide[-1].reply_markup.inline_keyboard for b in row]
    assert "setup:env:yx" in datas and "setup:yx_off" not in datas
    await _screens(tg, session, "setup:cf_off:ok", 905)
    assert deploy_hook_for("https://example.com/") is None                       # the .env hook too
    await _screens(tg, session, "setup:env:yx", 906)                             # «↩️ Брать из .env»
    assert yandex_webmaster.available() and integrations.source("yandex_token") == "env"
    await tg.session.close()


async def test_a_new_heartbeat_secret_asks_and_gives_every_new_address(bot, db, cfg):
    cfg(heartbeat_secret_env="")
    await settings.add_heartbeat_job("backup", 1440)
    await settings.add_heartbeat_job("mailer", 60)
    before = secrets.heartbeat_secret()
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    asked = await _screens(tg, session, "setup:hb_regen", 911)
    assert secrets.heartbeat_secret() == before and "backup" in asked[-1].text and "mailer" in asked[-1].text
    done = await _screens(tg, session, "setup:hb_regen:ok", 912)
    assert secrets.heartbeat_secret() != before
    # A new address for each task — the job's own token, never the shared secret.
    assert secrets.heartbeat_token("backup") in done[-1].text and secrets.heartbeat_token("mailer") in done[-1].text
    assert secrets.heartbeat_secret() not in done[-1].text
    await tg.session.close()


async def test_an_unreachable_api_is_not_called_a_rejected_token(bot, db, monkeypatch):
    token = "github_pat_" + "a" * 40
    monkeypatch.setattr(github, "check_token", lambda t: _const((Probe.UNREACHABLE, None)))
    state = FakeState()
    await state.set_state(setup.SetupForm.gh_token)
    msg = FakeMessage(token)
    await setup.msg_gh_token(msg, state)
    assert "не ответил" in msg.texts[-1] and "не принял" not in msg.texts[-1]
    assert integrations.github_token() == token                                  # saved, checked later

    monkeypatch.setattr(github, "check_token", lambda t: _const((Probe.REJECTED, None)))
    state = FakeState()
    await state.set_state(setup.SetupForm.gh_token)
    msg = FakeMessage("github_pat_" + "b" * 40)
    await setup.msg_gh_token(msg, state)
    assert "не принял" in msg.texts[-1] and state.state == setup.SetupForm.gh_token   # send another one
    assert integrations.github_token() == token
    await integrations.set_value("github_token", None)


async def test_a_rejected_yandex_token_keeps_the_step_and_the_old_switch_off(bot, db, cfg, monkeypatch):
    cfg(yandex_webmaster_token="env-token-" + "x" * 20)
    await integrations.load()
    await integrations.switch_off("yandex_token")
    monkeypatch.setattr(yandex_webmaster, "check_token", lambda: _const(Probe.REJECTED))
    state = FakeState()
    await state.set_state(setup.SetupForm.yx_token)
    msg = FakeMessage("bad-" + "y" * 30)
    await setup.msg_yx_token(msg, state)
    assert "не принял" in msg.texts[-1] and state.state == setup.SetupForm.yx_token
    assert integrations.source("yandex_token") == "off" and not yandex_webmaster.available()
    monkeypatch.setattr(yandex_webmaster, "check_token", lambda: _const(Probe.UNREACHABLE))
    msg = FakeMessage("good-" + "y" * 30)
    await setup.msg_yx_token(msg, state)
    assert "не ответил" in msg.texts[-1] and state.state is None
    assert integrations.yandex_token().startswith("good")                       # saved unchecked


async def test_a_watchdog_that_does_not_answer_keeps_the_step_open(bot, db, monkeypatch):
    async def fake_ping(url):
        return "check не найден"
    monkeypatch.setattr(setup.heartbeats, "ping_watchdog", fake_ping)
    state = FakeState()
    await state.set_state(setup.SetupForm.watchdog_url)
    msg = FakeMessage("https://hc-ping.com/bad-link")
    await setup.msg_watchdog_url(msg, state)
    assert "Сторож не на связи" in msg.texts[-1] and state.state == setup.SetupForm.watchdog_url
    assert not integrations.self_heartbeat_url()


async def test_cloudflare_is_checked_before_it_is_saved(bot, db, monkeypatch):
    await db.activate_or_create_site("https://www.example.com")
    msg = FakeMessage("example.com https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/abc")
    await setup.msg_cf_hook(msg, FakeState())
    assert deploy_hook_for("https://www.example.com/") is None and "www.example.com" in msg.texts[-1]

    monkeypatch.setattr(setup, "cf_token_status", lambda token: _const(Probe.REJECTED))
    state = FakeState()
    await state.set_state(setup.SetupForm.cf_token)
    msg = FakeMessage("t" * 40)
    await setup.msg_cf_token(msg, state)
    assert not integrations.cf_api_token() and "не принял" in msg.texts[-1]
    assert state.state == setup.SetupForm.cf_token
    monkeypatch.setattr(setup, "cf_token_status", lambda token: _const(Probe.UNREACHABLE))
    msg = FakeMessage("u" * 40)
    await setup.msg_cf_token(msg, state)
    assert integrations.cf_api_token() == "u" * 40 and "подтвердить не смог" in msg.texts[-1]
    assert state.state is None
