"""What the bot does to production: rebuild, purge, restart, prune — budgets,
the owner's switches and pause, the journal, confirmations, stale taps."""

from datetime import UTC, datetime, timedelta

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery
from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, buttons

import handlers
from db.database import get_active_incidents, get_site, get_state, save_incident
from handlers import alerts, incidents, setup, sites
from handlers import automation as auto_handlers
from handlers import heartbeats as hb_handlers
from monitors import host_checker
from reports import scheduler
from services import actions, automation, docker_api, integrations, maintenance, settings
from utils.text import parse_iso_utc

ZONE_ID = "0123456789abcdef0123456789abcdef"
SITE = "https://shop.example.test"


class StaleCall(FakeCall):
    """A tap that waited in Telegram's queue while the bot was down."""

    async def answer(self, text=None, show_alert=False):
        raise TelegramBadRequest(method=AnswerCallbackQuery(callback_query_id="1"),
                                 message="Bad Request: query is too old and response timeout expired")


@pytest.fixture
async def hook():
    """A deploy hook on 127.0.0.1 that counts the rebuilds it was asked for."""
    posts = []

    async def handle(request):
        posts.append(request.path)
        return web.Response(status=200, text="{}")

    app = web.Application()
    app.router.add_post("/hook", handle)
    async with TestServer(app) as server:
        await integrations.set_deploy_hook("shop.example.test", str(server.make_url("/hook")))
        yield posts


async def _age_journal(minutes: int):
    entries = await automation._journal()
    for e in entries:
        e["at"] = (parse_iso_utc(e["at"]) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    await automation._save(entries)


# ── The bot's own rebuild ────────────────────────────────────────────────────

async def test_auto_rebuild_once_an_hour_per_hook_and_journaled(db, hook):
    home = await db.activate_or_create_site(SITE)
    api = await db.activate_or_create_site(SITE + "/api/health")
    first = await automation.auto_redeploy(SITE, home, 1)
    second = await automation.auto_redeploy(SITE + "/api/health", api, 2)      # the same hook
    assert first.startswith("🤖 Уже сделал сам: 🚀") and len(hook) == 1
    assert "Сам не пересобирал" in second and "не чаще раза в час на один хук" in second
    row = (await automation.recent_actions(1))[0]
    assert (row["kind"], row["label"], row["auto"], row["trigger"]) == ("redeploy", "shop.example.test", 1,
                                                                         "инцидент #1")
    assert row["target"] == automation.redeploy_target(SITE) and row["target"].startswith("hook:")
    assert row["result"].startswith("🚀")
    assert "/hook" not in (await get_state("auto:journal"))          # the hook address is the secret
    await _age_journal(61)
    assert (await automation.auto_redeploy(SITE, home, 3)).startswith("🤖 Уже сделал сам")
    assert len(hook) == 2


async def test_no_auto_rebuild_on_pause_switch_off_or_stop(db, hook):
    sid = await db.activate_or_create_site(SITE)
    await maintenance.pause_site(sid, 60)                        # «🔧 Я чиню»
    assert "на паузе" in await automation.auto_redeploy(SITE, sid)
    await maintenance.pause_site(sid, None)
    await automation.pause(None)                                 # «⏹ До включения»
    assert "остановлены" in await automation.auto_redeploy(SITE, sid)
    await automation.resume()
    await integrations.set_value("auto_redeploy", "off")         # the switch in the chat beats .env
    assert await automation.auto_redeploy(SITE, sid) is None
    assert hook == []


async def test_a_failed_rebuild_is_said_as_such(db, hook, monkeypatch):
    sid = await db.activate_or_create_site(SITE)
    await integrations.set_deploy_hook("shop.example.test", "https://127.0.0.1:9/gone")
    line = await automation.auto_redeploy(SITE, sid, 5)
    assert line.startswith("🤖 Пробовал пересобрать сайт — не вышло.") and "Уже сделал сам" not in line
    assert (await automation.recent_actions(1))[0]["result"].startswith("❌")


async def test_the_tick_rebuilds_through_the_gate_once(bot, db):
    """A new availability incident: the alert first, then the hook — exactly
    once, even when the next tick finds the site still down."""
    hook_calls = []

    async def site(_):
        return web.Response(status=503, text="down")

    async def hook(_):
        hook_calls.append(1)
        return web.Response(status=200)
    app = web.Application()
    app.router.add_get("/", site)
    app.router.add_post("/hook", hook)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        sid = await db.activate_or_create_site(url)
        await db.update_site_settings(sid, fail_threshold=1)
        await integrations.set_deploy_hook("127.0.0.1", str(server.make_url("/hook")))
        await scheduler.run_availability_checks()
        await scheduler.drain_alert_worker()
        assert len(hook_calls) == 1 and "🤖 Уже сделал сам: 🚀" in bot.edited[-1]["text"]
        scheduler._next_avail_check.clear()
        await scheduler.run_availability_checks()                 # still down: the same incident
        await scheduler.drain_alert_worker()
    assert len(hook_calls) == 1
    [row] = await automation.recent_actions()
    assert row["kind"] == "redeploy" and row["trigger"].startswith("инцидент #")


# ── «🚀» / «🧹» under an alert ───────────────────────────────────────────────

async def test_rebuild_button_asks_then_runs_once(bot, db, hook):
    sid = await db.activate_or_create_site(SITE)
    ask = FakeCall(f"act:redeploy:{sid}")
    await alerts.cb_alert_action(ask)
    assert "Пересобрать" in ask.message.texts[-1] and hook == []           # only a question so far
    confirm = ask.message.markups[-1].inline_keyboard[0][0].callback_data
    assert confirm.startswith(f"act:ok:redeploy:{sid}:")
    yes = FakeCall(confirm)
    await alerts.cb_alert_confirmed(yes)
    assert len(hook) == 1 and yes.message.texts[-1].startswith("🚀")
    again = FakeCall(confirm)                                              # a double tap
    await alerts.cb_alert_confirmed(again)
    assert len(hook) == 1 and "второй раз" in again.message.texts[-1]
    row = (await automation.recent_actions(1))[0]
    assert row["auto"] == 0 and row["trigger"] == "кнопка" and row["result"].startswith("🚀")


async def test_old_confirmation_and_queued_tap_do_nothing(bot, db, hook):
    sid = await db.activate_or_create_site(SITE)
    old = int((datetime.now(UTC) - timedelta(minutes=11)).timestamp())
    late = FakeCall(f"act:ok:redeploy:{sid}:{old}")
    await alerts.cb_alert_confirmed(late)
    assert "устарел" in late.message.texts[-1]
    queued = StaleCall(f"act:ok:redeploy:{sid}:{int(datetime.now(UTC).timestamp())}")
    await alerts.cb_alert_confirmed(queued)
    assert "пока я был выключен" in queued.message.texts[-1]
    assert hook == [] and await automation.recent_actions() == []


def test_confirmation_handler_is_registered_before_the_generic_one():
    """act:ok:… must not reach cb_alert_action (it would say «Не понял действие»),
    and the auto:* buttons must have a router behind the admin filter."""
    names = [h.callback.__name__ for h in alerts.router.callback_query.handlers]
    assert names.index("cb_alert_confirmed") < names.index("cb_alert_action")
    assert auto_handlers.router in handlers.admin.sub_routers
    assert handlers.admin.sub_routers.index(auto_handlers.router) < handlers.admin.sub_routers.index(
        handlers.stale_router)


async def test_the_alert_offers_to_stop_the_bot_fixing_by_itself(bot, db, hook):
    kb = actions.alert_actions_keyboard(SITE, 1, 7, hosting="cloudflare")
    labels = buttons(kb)
    assert "⏸ Не чинить самому 2 ч" in labels and "🚀 Пересобрать сайт" in labels
    assert kb.inline_keyboard[-1][0].url == "https://dash.cloudflare.com/"      # the panel stays last
    tap = FakeCall("auto:pause:2:a")
    await auto_handlers.cb_auto_pause(tap)
    assert "Ничего не делаю сам 2 часа" in tap.message.texts[-1] and tap.message.texts[-1] != "🤖 Что я делаю сам"
    assert isinstance(await automation.paused_until(), datetime)
    await integrations.set_value("auto_redeploy", "off")
    assert "⏸ Не чинить самому 2 ч" not in buttons(actions.alert_actions_keyboard(SITE, 1, 7, hosting="cloudflare"))


@pytest.fixture
async def cloudflare(monkeypatch):
    """Cloudflare API v4 on 127.0.0.1: zone example.test; records purge bodies."""
    seen = {"purges": [], "zone": (200, {"success": True, "errors": [], "result": {"name": "example.test"}})}

    async def zone(request):
        status, body = seen["zone"]
        if isinstance(body, str):
            return web.Response(status=status, text=body, content_type="text/html")
        return web.json_response(body, status=status)

    async def purge(request):
        seen["purges"].append(await request.json())
        return web.json_response({"success": True, "errors": [], "result": {"id": ZONE_ID}})

    app = web.Application()
    app.router.add_get(f"/client/v4/zones/{ZONE_ID}", zone)
    app.router.add_post(f"/client/v4/zones/{ZONE_ID}/purge_cache", purge)
    async with TestServer(app) as server:
        monkeypatch.setattr(actions, "CF_API", str(server.make_url("/client/v4")))
        await integrations.set_value("cf_api_token", "t" * 40)
        await integrations.set_value("cf_zone_id", ZONE_ID)
        yield seen


async def test_purge_clears_only_the_host_and_only_in_the_zone(db, cloudflare):
    assert actions.in_cf_zone(SITE, hosting="cloudflare")                  # zone name not learnt yet
    assert not actions.in_cf_zone(SITE, hosting="vercel")
    assert (await actions.purge_cf_cache(SITE)).startswith("🧹")
    assert cloudflare["purges"] == [{"hosts": ["shop.example.test"]}]          # not purge_everything
    assert integrations.cf_zone_name() == "example.test"
    assert "не в зоне" in await actions.purge_cf_cache("https://other-shop.test")
    assert len(cloudflare["purges"]) == 1
    assert "🧹 Сбросить кэш" not in buttons(actions.alert_actions_keyboard("https://other-shop.test", 2))
    assert "🧹 Сбросить кэш" in buttons(actions.alert_actions_keyboard(SITE, 1))
    await integrations.set_value("cf_zone_id", "f" * 32)                   # another zone: its name is unknown again
    assert integrations.cf_zone_name() == ""


async def test_purge_names_the_cloudflare_error(db, cloudflare):
    cloudflare["zone"] = (403, {"success": False, "errors": [{"code": 9109, "message": "Unauthorized"}]})
    text = await actions.purge_cf_cache(SITE)
    assert "не дал прочитать зону: Unauthorized" in text and "не в зоне" not in text
    cloudflare["zone"] = (502, "<html>502 Bad Gateway</html>")
    assert "HTTP 502" in await actions.purge_cf_cache(SITE)
    assert cloudflare["purges"] == []
    await integrations.set_value("cf_zone_id", "not-a-zone-id")
    assert "испорчен" in await actions.purge_cf_cache(SITE)


async def test_purge_button_asks_and_is_journaled(bot, db, cloudflare):
    sid = await db.activate_or_create_site(SITE)
    ask = FakeCall(f"act:purge:{sid}")
    await alerts.cb_alert_action(ask)
    assert "Сбросить кэш Cloudflare для shop.example.test" in ask.message.texts[-1] and cloudflare["purges"] == []
    yes = FakeCall(ask.message.markups[-1].inline_keyboard[0][0].callback_data)
    await alerts.cb_alert_confirmed(yes)
    assert len(cloudflare["purges"]) == 1 and yes.message.texts[-1].startswith("🧹")
    row = (await automation.recent_actions(1))[0]
    assert (row["kind"], row["target"], row["auto"]) == ("purge", "shop.example.test", 0)
    no = FakeCall(f"act:no:{sid}")
    await alerts.cb_alert_action(no)
    assert "ничего не делаю" in no.message.texts[-1]


# ── Restart and prune through the gate ───────────────────────────────────────

async def test_restart_and_prune_budgets(db, cfg):
    cfg(autorestart_containers=("app", "db", "web"), auto_cleanup=True)
    for _ in range(4):                       # no hourly budget: the episode budget of host_checker decides
        row, why = await automation.claim("restart", "app", "unhealthy")
        assert row and why is None
    assert str(host_checker.RESTART_BUDGET) in automation.limit_text("restart")
    assert (await automation.claim("cleanup", "docker", "диск 95%"))[0]
    row, why = await automation.claim("cleanup", "docker", "диск 96%")        # once in 12 hours
    assert row is None and "не чаще раза в 12 ч" in why
    await automation.pause(2)
    assert "остановлены до" in (await automation.claim("restart", "web", "unhealthy"))[1]
    await integrations.set_value("auto_restart", "off")
    await automation.resume()
    assert (await automation.claim("restart", "web", "unhealthy")) == (None, "выключено")
    await integrations.set_value("auto_restart", None)
    assert (await automation.claim("restart", "web", "unhealthy"))[0]
    assert len(await automation.recent_actions(100)) == 6


async def test_a_paused_gate_restarts_nothing_and_the_owner_hears_why(bot, db, cfg, monkeypatch):
    cfg(autorestart_containers=("web",))
    restarts = []

    async def list_containers():
        return [{"id": "ab" * 32, "name": "web", "state": "exited", "status": "Exited (1) 2 minutes ago"}]

    async def restart_container(name):
        restarts.append(name)
        return True
    monkeypatch.setattr(docker_api, "list_containers", list_containers)
    monkeypatch.setattr(docker_api, "restart_container", restart_container)
    monkeypatch.setattr(docker_api, "own_container_id", lambda: "")
    await automation.pause(None)
    events = await host_checker.watch_containers()
    assert restarts == [] and events[0]["skipped"].startswith("все авто-действия остановлены")
    assert await get_state("restarts:web") is None                 # a skipped restart is not one of the episode's
    await scheduler.run_container_watch()
    await scheduler.run_container_watch()
    assert restarts == [] and len(bot.sent) == 1
    assert "Сам больше не перезапускаю: все авто-действия остановлены" in bot.sent[0]["text"]
    await automation.resume()
    await integrations.set_value("auto_restart", "off")             # the switch in the chat, same outcome
    assert (await host_checker.watch_containers())[0]["skipped"] == "выключено" and restarts == []
    await integrations.set_value("auto_restart", None)
    assert (await host_checker.watch_containers())[0]["restarted"] and restarts == ["web"]
    assert (await automation.recent_actions(1))[0]["kind"] == "restart"


async def test_a_spent_cleanup_budget_prunes_nothing(db, cfg, monkeypatch):
    cfg(auto_cleanup=True, disk_alert_pct=85)
    prunes = []

    async def prune():
        prunes.append(1)
        return {"bytes": 1_400_000_000, "layers": 2, "caches": 3}
    monkeypatch.setattr(docker_api, "prune", prune)
    monkeypatch.setattr(host_checker, "disk_usage_pct", lambda path="/": (92, 92.0, 100.0))
    assert (await automation.claim("cleanup", "docker", "test"))[0]       # the budget is spent
    assert (await host_checker.check_disk())["cleaned"] is None and prunes == []
    await _age_journal(13 * 60)
    result = await host_checker.check_disk()
    assert prunes == [1] and result["cleaned_bytes"] == 1_400_000_000
    assert (await automation.recent_actions(1))[0]["result"] == "освободил 1.4 GB"
    await _age_journal(13 * 60)
    await automation.pause(None)
    assert (await host_checker.check_disk())["cleaned"] is None and prunes == [1]
    await automation.resume()
    cfg(auto_cleanup=False)
    await integrations.set_value("auto_cleanup", "on")                     # the chat switch beats AUTO_CLEANUP=0
    await db.set_state("disk_pruned_at", None)
    await host_checker.check_disk()
    assert prunes == [1, 1]


# ── The screen and the switches ──────────────────────────────────────────────

async def test_stop_and_resume_from_the_chat(bot, db, hook):
    await auto_handlers.cb_auto_pause(FakeCall("auto:pause:0"))
    assert await automation.paused_until() == automation.FOREVER
    stale = StaleCall("auto:resume")
    await auto_handlers.cb_auto_resume(stale)                     # switching ON needs a fresh tap
    assert await automation.paused_until() == automation.FOREVER and "выключен" in stale.message.texts[-1]
    menu = FakeCall("auto:resume")
    await auto_handlers.cb_auto_resume(menu)
    assert await automation.paused_until() is None and "🤖 Что я делаю сам" in menu.message.texts[-1]
    assert "🚀 Пересобираю сайт, когда он падает — не чаще раза в час на один хук (проект Pages): вкл" in \
        menu.message.texts[-1]
    off = FakeCall("auto:off:redeploy")
    await auto_handlers.cb_auto_toggle(off)
    assert not integrations.auto_enabled("redeploy") and "🚀 Включить" in buttons(off.message.markups[-1])
    on_stale = StaleCall("auto:on:redeploy")
    await auto_handlers.cb_auto_toggle(on_stale)
    assert not integrations.auto_enabled("redeploy") and "выключен" in on_stale.message.texts[-1]
    await auto_handlers.cb_auto_toggle(FakeCall("auto:on:redeploy"))
    assert integrations.auto_enabled("redeploy")
    await automation.auto_redeploy(SITE, 1, 9)
    screen = FakeCall("auto:menu")
    await auto_handlers.cb_auto_menu(screen)
    assert "Последнее:" in screen.message.texts[-1] and "🚀 shop.example.test, сам: 🚀" in screen.message.texts[-1]


async def test_the_connections_screen_leads_to_the_switches(db, hook):
    assert "🤖 Что бот делает сам" in buttons((await setup.render_checklist())[1])
    assert "🤖 Что бот делает сам" in buttons(setup._cf_kb())
    assert "пересобираю сам: да, не чаще раза в час" in setup._cf_state_line()
    await integrations.set_value("auto_redeploy", "off")
    assert "пересобираю сам: нет" in setup._cf_state_line()


# ── Taps from the queue after a restart ──────────────────────────────────────

async def test_queued_taps_do_not_delete(bot, db):
    await settings.add_heartbeat_job("backup", 60)
    await hb_handlers.cb_hb_remove_here(StaleCall("hb_rm:backup"))
    assert "backup" in settings.heartbeat_jobs()
    await hb_handlers.cb_hb_del(StaleCall("hb_del:backup"))
    assert "backup" in settings.heartbeat_jobs()
    sid = await db.activate_or_create_site(SITE)
    await save_incident(sid, "availability", "Site down: HTTP 502", severity="critical")
    await incidents.cb_incidents_clear(StaleCall("incidents_clear"))
    assert len(await get_active_incidents()) == 1
    [inc] = await get_active_incidents()
    await incidents.cb_incident_close(StaleCall(f"inc_close:{inc['id']}"))
    assert len(await get_active_incidents()) == 1
    gone = StaleCall(f"delok:{sid}")
    await sites.cb_site_del_do(gone)
    assert (await get_site(sid))["active"] and "пока я был выключен" in gone.message.texts[-1]
    await integrations.set_value("yandex_token", "y" * 40)
    await setup.cb_yx_off(StaleCall("setup:yx_off"))
    assert integrations.yandex_token() == "y" * 40
    await setup.cb_status_page_on(StaleCall("setup:sp_on"))
    assert not settings.status_page_enabled()


# ── A key sent after the wizard was lost to a restart ────────────────────────

async def test_a_key_without_its_wizard_is_deleted(db):
    assert setup.looks_like_key("ghp_" + "a1" * 20)
    assert setup.looks_like_key("y0_AgAAAABc3Vp5AAq2kQAAAAEa0Ab9")
    assert setup.looks_like_key("example.com https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/abc")
    assert setup.looks_like_key("https://hc-ping.com/12345678-1234-1234-1234-123456789abc")
    assert setup.looks_like_key('{"type": "service_account", "client_email": "x@y", "private_key": "-----"}')
    assert not setup.looks_like_key("Привет! Что с сайтом shop.example.test?")
    assert not setup.looks_like_key("https://shop.example.test/catalog/")
    assert not setup.looks_like_key("supercalifragilisticexpialidocious")
    msg = FakeMessage("ghp_" + "a1" * 20)
    await setup.msg_key_without_wizard(msg)
    assert msg.deleted and "Мастер прервался после перезапуска" in msg.texts[-1]
