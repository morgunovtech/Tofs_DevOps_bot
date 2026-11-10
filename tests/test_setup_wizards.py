"""«🔌 Подключения»: a destructive tap asks first and «Отключено» is true even
for a value from .env; «не достучался» is not «не принял»; Cloudflare is
checked before «✅ сохранён»; the screen does not wait for probes in a row."""

import asyncio
import time

from aiogram import Bot
from aiogram.methods import EditMessageText
from conftest import FakeCall, FakeMessage, FakeState, buttons
from test_dispatcher import DP, MockedSession, _callback

from handlers import setup, speed
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


async def test_the_checklist_asks_probes_together_and_more_asks_none(bot, db, cfg, monkeypatch):
    cfg(yandex_webmaster_token="y" * 30, github_token="g" * 40)
    await integrations.load()
    calls = []

    async def slow(result):
        calls.append(result)
        await asyncio.sleep(0.3)
        return result

    monkeypatch.setattr(setup, "_web_ok", lambda: slow(True))
    monkeypatch.setattr(yandex_webmaster, "check_token", lambda: slow(Probe.OK))
    monkeypatch.setattr(github, "check_token", lambda token=None: slow((Probe.OK, "me")))
    setup.forget_probes()
    started = time.monotonic()
    items = {i.key: i for i in await setup.build_items()}
    assert time.monotonic() - started < 0.6 and items["github"].status == "ok" and items["yandex"].status == "ok"
    assert len(calls) == 3
    calls.clear()
    await setup.cb_more(FakeCall("setup:more"))
    assert calls == []
    items = {i.key: i for i in await setup.build_items()}            # the answers of a minute ago
    assert calls == [] and items["github"].status == "ok"
    cfg(yandex_webmaster_token="", github_token="")
    await integrations.load()


async def test_a_slow_or_silent_api_is_not_an_expired_token(bot, db, cfg, monkeypatch):
    cfg(yandex_webmaster_token="y" * 30, github_token="g" * 40)
    await integrations.load()
    monkeypatch.setattr(setup, "PROBE_DEADLINE_S", 0.05)

    async def late():
        await asyncio.sleep(0.3)
        return Probe.OK

    async def _const_tuple():
        return (Probe.UNREACHABLE, None)

    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    monkeypatch.setattr(yandex_webmaster, "check_token", late)
    monkeypatch.setattr(github, "check_token", lambda token=None: _const_tuple())
    items = {i.key: i for i in await setup.build_items()}
    for key in ("yandex", "github"):
        assert "истёк" not in items[key].detail and "не принимает" not in items[key].detail
        assert items[key].button is None and "не ответил" in items[key].detail
    setup.forget_probes()
    monkeypatch.setattr(yandex_webmaster, "check_token", lambda: _const(Probe.REJECTED))
    items = {i.key: i for i in await setup.build_items()}
    assert items["yandex"].status == "warn" and "не принял" in items["yandex"].detail
    assert items["yandex"].button == ("🔎 Яндекс: проверить", "setup:yx")
    cfg(yandex_webmaster_token="", github_token="")
    await integrations.load()


async def test_the_test_alert_is_sent_once_however_many_taps(bot, db, monkeypatch):
    monkeypatch.setattr(setup, "RING_DELAY_S", 30)
    first, second = FakeCall("ring:test"), FakeCall("ring:test")
    await setup.cb_test_alert(first)
    await setup.cb_test_alert(second)
    assert len(setup._ring_tasks) == 1 and "уже в пути" in second.answers[-1]
    for task in list(setup._ring_tasks):
        task.cancel()
    await asyncio.sleep(0)


async def test_a_working_integration_has_its_button(bot, db, cfg, monkeypatch):
    cfg(github_token="g" * 40)
    await integrations.load()
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    monkeypatch.setattr(github, "check_token", lambda token=None: _const((Probe.OK, "me")))
    setup.forget_probes()
    _, kb = await setup.render_checklist()
    pairs = [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row]
    assert ("⚙️ GitHub", "setup:gh") in pairs and ("🔄 Проверить снова", "setup:recheck") in pairs
    cfg(github_token="")
    await integrations.load()


async def test_speed_screen_without_a_key_offers_to_connect(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    _, kb = await speed.speed_page(await db.get_site(sid))
    assert "setup:crux" in [b.callback_data for row in kb.inline_keyboard for b in row]


async def test_guides_name_buttons_that_exist(bot, db):
    call = FakeCall("setup:url")
    await setup.cb_guide_url(call)
    assert "«🔄 Проверить снова»" in call.message.texts[-1] and "🔄 Проверить снова" in buttons(call.message.reply_markup)
    call = FakeCall("setup:docker")
    await setup.cb_guide_docker(call)
    assert "«🔄 Проверить снова»" in call.message.texts[-1] and "🔄 Проверить снова" in buttons(call.message.reply_markup)
