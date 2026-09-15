"""Bot screens rendered against the test DB with fake Telegram objects —
catches attribute errors in handlers without a Telegram connection."""

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import save_feedback, save_incident
from handlers import feedback, heartbeats, incidents, maintenance, menu, seo, settings, site_settings, sites
from services import integrations, recommend, sitestatus
from services import maintenance as maint_service
from services import settings as settings_service


async def test_main_menu_header_states(bot, db):
    header, kb = await menu.build_main_menu()
    assert "Сайтов пока нет" in header
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await save_incident(sid, "ssl", "expiring", "warning")
    await maint_service.pause_site(sid, 30)
    header, kb = await menu.build_main_menu()
    first = header.split("\n")[0]
    assert first.startswith("✅ 1/1 открывается · 🔴 проблем: 1") and "чинится" in header
    assert "🔴 ex.test: expiring" in header                      # the problem is right on the main screen
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "🔴 Проблемы (1)" in labels and "🌍 Сайты" in labels and "⚙️ Настройки" in labels
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
        await sites.cb_check_single_site(call)            # instant: from the snapshot the add flow wrote
        assert url in call.message.texts[-1] and "Открывается, быстро" in call.message.texts[-1]
        assert "Домен: ещё не проверял" in call.message.texts[-1]
        call = FakeCall(f"check_site_live:{sid}")
        await sites.cb_check_site_live(call)              # live: domain gets checked
        assert "Домен: это IP-адрес" in call.message.texts[-1]          # one screen, no pointer elsewhere
        await sitestatus.update(sid, "links", status="ok", internal=0, external=0)
        await sitestatus.update(sid, "seo", status="warning", critical=0, improve=2)
        call = FakeCall(f"check_site:{sid}")
        await sites.cb_check_single_site(call)
        assert "Ссылки: все работают" in call.message.texts[-1]
        assert "есть что улучшить" in call.message.texts[-1] and "За неделю" in call.message.texts[-1]

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
    assert "недельная вс 11:00" in call.message.texts[-1]
    labels = [b.text for row in call.message.reply_markup.inline_keyboard for b in row]
    assert "🩺 Диагностика и подключения" in labels and "⏰ Контроль задач" in labels
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


async def test_main_menu_header_is_short_when_all_is_well(bot, db):
    for i in range(3):
        sid = await db.activate_or_create_site(f"https://ok{i}.test")
        await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    header, kb = await menu.build_main_menu()
    lines = header.split("\n")
    assert lines[0].startswith("✅ 3/3 в порядке") and len(lines) == 4
    assert all(line.endswith("· быстро") for line in lines[1:])
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert labels == ["🌍 Сайты", "🔎 Проверить всё сейчас", "⚙️ Настройки"]   # no «Проблемы» on a good day


async def test_seo_screen_from_snapshot_with_fix_steps(bot, db):
    sid = await db.activate_or_create_site("https://seo.test")
    await sitestatus.set_field(sid, "hosting", "cloudflare")
    await sitestatus.update(
        sid, "seo", status="warning", critical=0, improve=4, pages=3, no_js_chars=60,
        problems=[{"code": "no_js", "severity": "warning", "message": "без JavaScript на главной всего 60 символов текста"},
                  {"code": "soft404", "severity": "warning", "message": "несуществующий адрес ответил «всё хорошо» (HTTP 200)"},
                  {"code": "no_title", "severity": "warning", "message": "/about: нет заголовка <title>"},
                  {"code": "no_title", "severity": "warning", "message": "/blog: нет заголовка <title>"}],
        infos=[{"code": "og_image", "severity": "info", "message": "нет картинки для превью в мессенджерах (og:image)"}])
    call = FakeCall(f"seo_site:{sid}")
    await seo.cb_seo(call)
    text = call.message.texts[-1]
    assert len(call.message.texts) == 1                              # instant: no «Смотрю…» progress screen
    assert "В поиске виден. 3 помехи" in text and "видят почти пустую страницу — 60 символов" in text
    assert "1. 🤖" in text and "2. 🗑" in text and "3. 🏷" in text and "&lt;title&gt;" in text
    assert "/about: " in text and "/blog: " in text                   # both pages under one numbered item
    labels = buttons(call.message.reply_markup)
    assert labels[0].startswith("1. 💡") and labels[2] == "3. 💡 Добавить заголовок страницы"
    assert "💡 Мелочи: что с ними делать" in labels and "🔄 Проверить снова" in labels

    call = FakeCall(f"seo_fix:{sid}:soft404")
    await seo.cb_seo_fix(call)
    text = call.message.texts[-1]
    assert "Чем грозит" in text and "_redirects" in text and "Шаги для Cloudflare" in text and "Что я увидел" in text
    labels = buttons(call.message.reply_markup)
    assert "🔗 Открыть панель Cloudflare" in labels and "🔗 Открыть несуществующую страницу" in labels

    call = FakeCall(f"seo_minor:{sid}")
    await seo.cb_seo_minor(call)
    assert "og:image" in call.message.texts[-1] and "1200×630" in call.message.texts[-1]

    lines = await sites.card_lines(await db.get_site(sid))
    assert any("есть что улучшить (4)" in line for line in lines)


async def test_timezone_ring_and_status_page_screens(bot, db):
    call = FakeCall("menu_tz")
    await settings.cb_tz(call)
    assert "Часовой пояс: <b>Europe/Moscow</b>" in call.message.texts[-1]
    call = FakeCall("set_tz:4")                                       # Новосибирск
    await settings.cb_settings_pick(call)
    assert settings_service.timezone() == "Asia/Novosibirsk" and "Asia/Novosibirsk" in call.message.texts[-1]
    state = FakeState()
    msg = FakeMessage("Europe/Prague")
    await settings.msg_tz(msg, state)
    assert settings_service.timezone() == "Europe/Prague" and state.state is None
    msg = FakeMessage("Mars/Olympus")
    await settings.msg_tz(msg, FakeState())
    assert "не знаю" in msg.texts[-1] and settings_service.timezone() == "Europe/Prague"
    await settings.cb_settings_pick(FakeCall("set_tz:env"))
    assert settings_service.timezone() == "Europe/Moscow"
    assert "Часовой пояс: Europe/Moscow" in await settings.hub_text()

    call = FakeCall("set_ring:on")
    await settings.cb_settings_pick(call)
    assert settings_service.ring_only_down() and "только когда сайт лёг" in call.message.texts[-1]
    await settings.cb_settings_pick(FakeCall("set_ring:off"))
    assert not settings_service.ring_only_down()

    call = FakeCall("menu_statuspage")
    await settings.cb_status_page(call)
    assert "Выключена" in call.message.texts[-1]
    call = FakeCall("set_sp:on")
    await settings.cb_settings_pick(call)
    assert "Включена: https://bot.example.test/status" in call.message.texts[-1]
    call = FakeCall("sp_secret:on")
    await settings.cb_status_page_secret(call)
    slug = integrations.status_page_slug()
    assert slug and slug in call.message.texts[-1] and "Секретная ссылка включена" in call.message.texts[-1]
    await settings.cb_status_page_secret(FakeCall("sp_secret:off"))
    assert not integrations.status_page_slug()
    await settings.cb_settings_pick(FakeCall("set_sp:off"))


async def test_declined_keyword_is_not_suggested_again(bot, db):
    sid = await db.activate_or_create_site("https://kw.test")
    assert "keyword" in [c[0] for c in await recommend._candidates()]
    call = FakeCall(f"kwno:{sid}")
    await sites.cb_keyword_decline(call, FakeState())
    assert any(a and a.startswith("Ок") for a in call.answers) and call.message.texts   # back on the main screen
    assert "keyword" not in [c[0] for c in await recommend._candidates()]
