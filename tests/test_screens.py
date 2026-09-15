"""Bot screens rendered against the test DB with fake Telegram objects —
catches attribute errors in handlers without a Telegram connection."""

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState

from db.database import save_feedback, save_incident
from handlers import feedback, heartbeats, incidents, maintenance, menu, settings, site_settings, sites
from services import maintenance as maint_service
from services import settings as settings_service


async def test_main_menu_header_states(bot, db):
    header, kb = await menu.build_main_menu()
    assert "Сайтов пока нет" in header
    sid = await db.activate_or_create_site("https://ex.test")
    await db.save_check(sid, "availability", "ok", response_time_ms=90)
    await save_incident(sid, "ssl", "expiring", "warning")
    await maint_service.pause_site(sid, 30)
    header, kb = await menu.build_main_menu()
    assert "сайт в порядке" in header and "проблем: 1" in header and "чинится: 1" in header
    assert any("Проблемы (1)" in b.text for row in kb.inline_keyboard for b in row)
    await maint_service.pause_site(sid, None)


async def test_site_add_flow_and_detail_screen(bot, db):
    async def h(_):
        return web.Response(text="<html>ok</html>", content_type="text/html")
    app = web.Application()
    app.router.add_get("/", h)
    async with TestServer(app) as server:
        url = str(server.make_url("")).rstrip("/")
        msg = FakeMessage(f"http://127.0.0.1:{server.port}")
        state = FakeState()
        await sites.msg_site_add(msg, state)
        assert any("под присмотром" in t for t in msg.texts) and state.state is None
        assert any("Как я работаю" in t for t in msg.texts)   # first site → rules of the game
        assert url in await db.get_active_site_urls()

        sid = (await db.get_site_by_url(url))["id"]
        call = FakeCall(f"check_site:{sid}")
        await sites.cb_check_single_site(call)
        assert url in call.message.texts[-1] and "Открывается, быстро" in call.message.texts[-1]

        text, kb = await site_settings.sset_screen(await db.get_site(sid))
        assert "Проверяю обычно" in text and any("Для продвинутых" in b.text for r in kb.inline_keyboard for b in r)
        text, kb = await site_settings.advanced_screen(await db.get_site(sid))
        assert "по умолчанию" in text and any("Метод" in b.text for r in kb.inline_keyboard for b in r)
        call = FakeCall(f"ssv:s:{sid}:5000")
        await site_settings.cb_site_setting_value(call)
        assert "5000 мс" in call.message.texts[-1]
        call = FakeCall(f"ssm:{sid}:HEAD")
        await site_settings.cb_site_method(call)
        assert "Запрос: HEAD" in call.message.texts[-1]

        headers_msg = FakeMessage("Authorization: Bearer x")
        state = FakeState(sset_site_id=sid)
        await site_settings.msg_site_headers(headers_msg, state)
        assert "заголовков: 1" in headers_msg.texts[-1]

        call = FakeCall(f"pause:{sid}:morning")
        await sites.cb_pause(call)
        assert "чинится" in call.message.texts[-1]
        assert await maint_service.is_paused(sid)

        call = FakeCall("site_export")
        docs = []
        call.message.answer_document = lambda document, caption=None: _record(docs, document, caption)
        await sites.cb_site_export(call)
        assert docs and b'"url"' in docs[0]

        imp = FakeMessage('{"sites": [{"url": "https://imported.test", "slow_ms": 1234}, {"url": "bad host!"}]}')
        await sites.msg_site_import(imp, FakeState())
        assert "добавлено 1" in imp.texts[-1] and "Пропущено" in imp.texts[-1]
        assert (await db.get_site_by_url("https://imported.test"))["slow_ms"] == 1234


async def _record(store, document, caption):
    store.append(document.data)


async def test_incidents_settings_maintenance_heartbeats_feedback_screens(bot, db):
    sid = await db.get_or_create_site("https://ex.test")
    await save_incident(sid, "availability", "Site down: <b>", "critical")
    call = FakeCall("menu_incidents")
    await incidents.cb_incidents(call)
    assert "&lt;b&gt;" in call.message.texts[-1] and "не открывается" in call.message.texts[-1]
    assert any("Я чиню" in b.text for r in call.message.reply_markup.inline_keyboard for b in r)
    assert any("Что делать" in b.text for r in call.message.reply_markup.inline_keyboard for b in r)

    call = FakeCall("menu_settings")
    await settings.cb_settings(call)
    assert "Недельный отчёт" in call.message.texts[-1]
    call = FakeCall("set_w:18")
    await settings.cb_settings_pick(call)
    assert settings_service.weekly_hour() == 18

    call = FakeCall("mw_time:0:w:120-240")
    await maintenance.cb_mw_time(call)
    assert "Окно добавлено" in call.message.texts[-1] and "будни" in call.message.texts[-1]

    call = FakeCall("menu_hb")
    await heartbeats.cb_hb(call)
    assert "ни одной задачи" in call.message.texts[-1]
    await settings_service.add_heartbeat_job("backup", 1440)
    call = FakeCall("menu_hb")
    await heartbeats.cb_hb(call)
    assert "backup" in call.message.texts[-1] and "ожидаю каждые" in call.message.texts[-1]
    call = FakeCall("hb_show:backup")
    await heartbeats.cb_hb_show(call)
    assert "unit-test-heartbeat-secret" in call.message.texts[-1] and "curl -fsS" in call.message.texts[-1]
    await settings_service.remove_heartbeat_job("backup")

    await save_feedback("https://ex.test", "https://ex.test/p", "typo <here>", "", "1.1.1.1")
    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    text = call.message.texts[-1]
    assert "typo &lt;here&gt;" in text and "data-devops-feedback" in text
    assert "unit-test-webhook-secret" in text
