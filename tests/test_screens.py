"""Bot screens rendered against the test DB with fake Telegram objects —
catches attribute errors in handlers without a Telegram connection."""

import json
from datetime import UTC, datetime, timedelta

from aiohttp import web
from aiohttp.test_utils import TestServer
from conftest import FakeCall, FakeMessage, FakeState, buttons

from db.database import get_active_incidents, save_feedback, save_incident
from handlers import (
    blocks,
    feedback,
    heartbeats,
    incidents,
    links,
    maintenance,
    menu,
    seo,
    settings,
    site_settings,
    sites,
)
from monitors import links_checker
from monitors.base import LinkCheck, SeoResult
from reports import scheduler
from services import heartbeats as heartbeats_service
from services import integrations, recommend, screenshots, sitestatus
from services import maintenance as maint_service
from services import settings as settings_service
from services.actions import heartbeat_keyboard

NOW = datetime.now(UTC).isoformat(timespec="seconds")


async def test_main_menu_header_states(bot, db):
    header, kb = await menu.build_main_menu()
    assert "Пока ни за чем не слежу" in header
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await save_incident(sid, "ssl", "expiring", "warning")
    await maint_service.pause_site(sid, 30)
    header, kb = await menu.build_main_menu()
    first = header.split("\n")[0]
    # The worst level first, the problems counted as the screen shows them, one time for all.
    assert first.startswith("🟠 1 проблема · проверено в ")
    assert "🟠 ex.test · чинится, expiring" in header               # on its site's line, said once
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # «Что не так» first, then a button per block — not one per site.
    assert labels == ["🟠 Что не так", "🌐 Сайты", "🤖 Боты", "⚙️ Настройки"]
    assert kb.inline_keyboard[1][0].callback_data == "blk:sites"
    await maint_service.pause_site(sid, None)
    await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    await sitestatus.update(sid, "avail", status="error", ms=None, error="HTTP 502")
    header, kb = await menu.build_main_menu()
    assert header.startswith("🔴 1 проблема") and "🔴 ex.test · хостинг отвечает ошибкой 502" in header
    assert "🔴 Что не так" in [b.text for row in kb.inline_keyboard for b in row]


async def test_main_menu_site_line_takes_the_worst_level(bot, db):
    """A site that opens but has notes: 🟠 for «act, not today», 🔴 when one
    of the notes is critical — and it still counts as opening."""
    sid = await db.activate_or_create_site("https://notes.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.update(sid, "ssl", days_left=10, issuer="LE", not_after=None, error=None)
    await sitestatus.update(sid, "seo", status="warning", critical=0, improve=3, problems=[])
    icon, state, _ = await menu.site_state(await db.get_site(sid))
    assert icon == "🟠" and state == "сертификат через 10 дн., 3 помехи в поиске"
    header, _ = await menu.build_main_menu()
    assert header.startswith("🟠 1 проблема")
    await sitestatus.update(sid, "seo", status="critical", critical=1, improve=0, problems=[])
    icon, state, _ = await menu.site_state(await db.get_site(sid))
    assert icon == "🔴" and state.endswith("закрыт от поиска")


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
        assert call.message.texts[-1].startswith("<b>127.0.0.1</b>") and "✅ Открывается за " in call.message.texts[-1]
        assert "Домен: ещё не проверял" in call.message.texts[-1]
        call = FakeCall(f"check_site_live:{sid}")
        await sites.cb_check_site_live(call)              # live: domain gets checked
        assert "Домен: это IP-адрес" in call.message.texts[-1]          # one screen, no pointer elsewhere
        await sitestatus.update(sid, "links", status="ok", broken=0, pending=0)
        await sitestatus.update(sid, "seo", status="warning", critical=0, improve=2)
        call = FakeCall(f"check_site:{sid}")
        await sites.cb_check_single_site(call)
        assert "Ссылки: все открываются" in call.message.texts[-1]
        assert "🟠 Поиск и ИИ: 2 помехи" in call.message.texts[-1] and "✅ Без сбоев ни за неделю, ни за месяц" in call.message.texts[-1]
        assert "В поиске" not in call.message.texts[-1]          # the card never claims index status

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

        call = FakeCall(f"pause:{sid}:120")
        await sites.cb_pause(call)
        assert "чинится" in call.message.texts[-1]
        assert await maint_service.is_paused(sid)

        # An export from an older version, sent to the chat, still restores.
        text = await sites._restore_bytes(
            b'{"sites": [{"url": "https://imported.test", "slow_ms": 1234}, {"url": "bad host!"}]}')
        assert "добавлено 1" in text and "Пропущено" in text
        assert (await db.get_site_by_url("https://imported.test"))["slow_ms"] == 1234


async def test_incidents_settings_maintenance_heartbeats_feedback_screens(bot, db):
    sid = await db.get_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "availability", "Site down: <b>", "critical")
    call = FakeCall("menu_incidents")
    await incidents.cb_incidents(call)
    assert "&lt;b&gt;" in call.message.texts[-1] and "не открывается" in call.message.texts[-1]
    assert call.message.texts[-1].startswith("🔴 Что сейчас не так (1)")
    assert any("Я чиню" in b.text for r in call.message.reply_markup.inline_keyboard for b in r)
    assert any("Что делать" in b.text for r in call.message.reply_markup.inline_keyboard for b in r)
    call = FakeCall(f"inc_explain:{inc_id}")                       # the button used to have no handler at all
    await incidents.cb_incident_explain(call)
    text = call.message.texts[-1]
    assert text.startswith("🔴 ex.test не открывается") and "Что делать:" in text and "&lt;b&gt;" in text
    assert "🔧 Я чиню, час тишины" in buttons(call.message.reply_markup)

    call = FakeCall("menu_settings")
    await settings.cb_settings(call)
    text = call.message.texts[-1]
    # Five questions, a line each; no export/import, no separate diagnostics.
    assert text.startswith("⚙️ Настройки\n\n🔔 Когда писать — сводка 9:00")
    assert "👥 Помощники — нет" in text and "🔌 Подключения — " in text and "🌍 Для посетителей — " in text
    labels = [b.text for row in call.message.reply_markup.inline_keyboard for b in row]
    assert labels == ["🔔 Когда писать", "👥 Помощники", "🔌 Подключения", "🌍 Для посетителей",
                      "🕐 Часовой пояс", "🏠 Главная"]
    call = FakeCall("menu_reports")                                   # pause, planned work, the test alert live here
    await settings.cb_reports(call)
    assert {"🔕 Помолчать…", "🗓 Плановые работы", "🔔 Пробная тревога"} <= set(buttons(call.message.reply_markup))
    call = FakeCall("menu_visitors")
    await settings.cb_visitors(call)
    assert buttons(call.message.reply_markup) == ["🌐 Статус-страница", "📩 Сообщения", "← Настройки"]
    call = FakeCall("set_w:18")
    await settings.cb_settings_pick(call)
    assert settings_service.weekly_hour() == 18

    call = FakeCall("mw_time:0:w:120-240")
    await maintenance.cb_mw_time(call)
    assert "Окно добавлено" in call.message.texts[-1] and "будни" in call.message.texts[-1]

    await settings_service.add_heartbeat_job("backup", 1440)
    call = FakeCall("hb_show:backup")
    await heartbeats.cb_hb_show(call)
    text = call.message.texts[-1]
    # One question first; no curl line and no secret until the answer.
    assert text.startswith("⏰ backup\n⏳ Пока не выходит на связь · жду раз в сутки")
    assert text.endswith("Что такое «backup» у тебя?") and "curl" not in text and "secret" not in text
    assert [b.callback_data for row in call.message.reply_markup.inline_keyboard for b in row] == [
        "hb_bot:backup", "hb_cron:backup", "hb_del:backup", "blk:bots"]
    call = FakeCall("hb_bot:backup")
    await heartbeats.cb_hb_bot(call)
    assert "unit-test-heartbeat-secret/backup</code>" in call.message.texts[-1] and "HEALTHCHECK_URL" in call.message.texts[-1]
    call = FakeCall("hb_cron:backup")
    await heartbeats.cb_hb_cron(call)
    text = call.message.texts[-1]
    assert "GitHub Actions" in text and "- run: curl -fsS" in text and "&& curl -fsS" in text
    assert buttons(call.message.reply_markup) == ["← Назад"]
    # From the morning digest: the answer goes below, the digest stays as it was.
    call = FakeCall("hb_how:backup")

    async def no_edit(*a, **kw):
        raise AssertionError("the digest must not be overwritten")

    call.message.edit_text = no_edit
    await heartbeats.cb_hb_show(call)
    assert "Что такое «backup» у тебя?" in call.message.texts[-1]
    # «🗑 Удалить» under the alert: the task goes, its buttons go, the text stays.
    call = FakeCall("hb_rm:backup")
    call.message.text = "⚠️ «backup» так и не выходит на связь"
    call.message.reply_markup = heartbeat_keyboard(["backup"])
    call.message.edit_text = no_edit
    await heartbeats.cb_hb_remove_here(call)
    assert "backup" not in settings_service.heartbeat_jobs() and call.message.reply_markup is None
    assert any(a and a.startswith("Удалил «backup»") for a in call.answers)

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
    # One time for the whole screen: «проверено в», not «2 мин назад» that goes stale in the chat.
    assert lines[0].startswith("✅ Всё в порядке · проверено в ")
    # No count in the block title (the ✅ under it say it); the number, not «быстро».
    assert lines[1:6] == ["", "<b>Сайты</b>", "✅ ok0.test · 0.09 с", "✅ ok1.test · 0.09 с", "✅ ok2.test · 0.09 с"]
    assert "<b>Боты и системы</b>" in header and "резервная копия" in header
    assert not any(e in header for e in ("🌐", "🤖 Боты и", "📣"))      # status icons only
    labels = [b.text for row in kb.inline_keyboard for b in row]
    # A button per block, not per site; no «Что не так» on a good day, no
    # «Проверить всё сейчас» — the screen is never older than a minute or two.
    assert labels == ["🌐 Сайты", "🤖 Боты", "⚙️ Настройки"]
    assert [len(row) for row in kb.inline_keyboard] == [2, 1]


async def test_tasks_and_bots_sit_on_the_main_screen(bot, db):
    """03.11.2026: SocialBot was added to «⏰ Контроль задач» and the owner
    could not find it — the main screen showed sites only."""
    sid = await db.activate_or_create_site("https://ok.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await settings_service.add_heartbeat_job("socialbot", 1440)
    header, kb = await menu.build_main_menu()
    lines = header.split("\n")
    # Not marked yet and not alerted: waiting, not counted, not a problem.
    assert lines[0].startswith("✅ Всё в порядке") and "<b>Боты и системы</b>\n⏳ socialbot · пока не выходит на связь" in header
    call = FakeCall("blk:bots")                                       # its card: one tap from the bots page
    await blocks.cb_bots(call)
    assert "hb_show:socialbot" in [b.callback_data for row in call.message.reply_markup.inline_keyboard for b in row]

    await db.heartbeat_ping("socialbot")
    header, kb = await menu.build_main_menu()
    assert header.startswith("✅ Всё в порядке") and "\n✅ socialbot\n" in header    # on time: just its name

    conn = await db.get_db()
    await conn.execute("UPDATE heartbeats SET last_ping = datetime('now', '-31 hours') WHERE job = 'socialbot'")
    await conn.commit()
    header, kb = await menu.build_main_menu()
    assert header.startswith("🟠 1 проблема") and "🟠 socialbot · молчит 1 дн 7 ч" in header   # late: the time
    call = FakeCall("hb_show:socialbot")
    await heartbeats.cb_hb_show(call)
    assert "🟠 Молчит 1 дн 7 ч, а выходит на связь раз в сутки." in call.message.texts[-1]

    await settings_service.add_heartbeat_job("backup", 60)
    await db.set_state("hb_alerted:backup", NOW)                 # the watch already said «never marked»
    header, _ = await menu.build_main_menu()
    assert header.startswith("🟠 2 проблемы") and "⚠️ backup · так и не выходит на связь" in header

    # Only tasks, no sites: still a dashboard, not the «add your first site» greeting.
    await db.deactivate_site(sid)
    header, kb = await menu.build_main_menu()
    assert "Пока ни за чем не слежу" not in header and "<b>Боты и системы</b>" in header
    assert "<b>Сайты</b>" not in header
    assert buttons(kb) == ["🤖 Боты", "⚙️ Настройки"]


async def test_main_screen_shows_the_watchdog(bot, db, monkeypatch):
    """The watchdog closes the «⏰ Задачи и боты» block; one that does not
    take the marks spoils the headline like a silent task."""
    sid = await db.activate_or_create_site("https://a.test")
    await sitestatus.update(sid, "avail", status="ok", ms=100)
    header, _ = await menu.build_main_menu()
    assert "\nВнешнего сторожа нет · подключи в «🤖 Боты»" in header   # not a problem, not hidden
    assert header.startswith("✅ Всё в порядке")
    await integrations.set_value("self_heartbeat_url", "https://hc-ping.com/abc")
    await heartbeats_service.record_watchdog(None)
    header, _ = await menu.build_main_menu()
    assert header.startswith("✅ Всё в порядке") and "<b>Боты и системы</b>\n✅ сторож healthchecks.io\n" in header
    await db.set_state("watchdog", json.dumps({"ok_at": NOW, "error": "отвечает ошибкой 404",
                                               "fail_since": (datetime.now(UTC) - timedelta(minutes=40)).isoformat()}))
    header, _ = await menu.build_main_menu()
    assert header.startswith("🟠 1 проблема")
    assert "🟠 сторож healthchecks.io · не на связи 40 мин (отвечает ошибкой 404)" in header
    await integrations.set_value("self_heartbeat_url", None)


async def test_add_offers_every_kind_and_old_screens_land_on_main(bot, db):
    call = FakeCall("site_add")
    state = FakeState()
    await sites.cb_site_add(call, state)
    assert "адрес сайта" in call.message.texts[-1]
    assert [b.callback_data for row in call.message.reply_markup.inline_keyboard for b in row] == [
        "site_add_adv", "hb_add", "fsm_cancel"]
    call = FakeCall("hb_add")
    await heartbeats.cb_hb_add(call, state)
    assert state.state == heartbeats.AddHeartbeatForm.name and "socialbot" in call.message.texts[-1]
    msg = FakeMessage("SocialBot")
    await heartbeats.msg_hb_name(msg, state)
    call = FakeCall("hbint:1440")
    await heartbeats.cb_hb_interval(call, state)
    assert settings_service.ui_heartbeat_jobs() == {"socialbot": 1440}
    assert "на главном экране" in call.message.texts[-1]
    call = FakeCall("hb_del:socialbot")
    await heartbeats.cb_hb_del(call)
    assert not settings_service.heartbeat_jobs() and "больше не жду" in call.message.texts[-1]

    for old in ("menu_sites", "menu_hb"):                     # buttons on messages sent before this layout
        call = FakeCall(old)
        await menu.cb_main_menu(call, FakeState())
        assert "Пока ни за чем не слежу" in call.message.texts[-1]


async def test_seo_screen_from_snapshot_with_fix_steps(bot, db):
    sid = await db.activate_or_create_site("https://seo.test")
    await sitestatus.set_field(sid, "hosting", "cloudflare")
    await sitestatus.update(
        sid, "seo", status="warning", critical=0, improve=5, pages=3, no_js_chars=60,
        problems=[{"code": "no_js", "severity": "warning", "message": "без JavaScript на главной всего 60 символов текста"},
                  {"code": "soft404", "severity": "warning", "message": "несуществующий адрес ответил «всё хорошо» (HTTP 200)"},
                  {"code": "no_title", "severity": "warning", "message": "/about: нет заголовка <title>"},
                  {"code": "no_title", "severity": "warning", "message": "/blog: нет заголовка <title>"},
                  {"code": "og_image", "severity": "warning", "message": "нет картинки для превью в мессенджерах (og:image)"}])
    call = FakeCall(f"seo_site:{sid}")
    await seo.cb_seo(call)
    text = call.message.texts[-1]
    assert len(call.message.texts) == 1                              # instant: no «Смотрю…» progress screen
    # Says what was measured («открыт для поисковиков»), never «в поиске виден».
    assert "🟠 Сайт открыт для поисковиков, но есть 4 помехи" in text and "В поиске виден" not in text
    assert "видят почти пустую страницу — 60 символов" in text
    assert "1. 🟠 <b>ИИ-ассистенты" in text and "2. 🟠" in text and "3. 🟠" in text and "&lt;title&gt;" in text
    assert "/about: " in text and "/blog: " in text                   # both pages under one numbered item
    assert "4. 🟠 <b>Нет картинки для превью" in text                # og:image is an action now, not a «мелочь»
    assert "Мелочи" not in text and "проверил 3 страницы" in text
    assert "знают только Google и Яндекс" in text                     # no GSC/Webmaster → say so, don't pretend
    labels = buttons(call.message.reply_markup)
    assert labels[0].startswith("1. 💡") and labels[2] == "3. 💡 Добавить заголовок страницы"
    assert labels[3] == "4. 💡 Добавить картинку для превью"
    assert "🔌 Подключить Google или Яндекс" in labels and "🔄 Проверить снова" in labels
    assert not any("Мелочи" in label for label in labels)

    call = FakeCall(f"seo_fix:{sid}:soft404")
    await seo.cb_seo_fix(call)
    text = call.message.texts[-1]
    assert "Чем грозит" in text and "_redirects" in text and "Шаги для Cloudflare" in text and "Что я увидел" in text
    labels = buttons(call.message.reply_markup)
    assert "🔗 Открыть панель Cloudflare" in labels and "🔗 Открыть несуществующую страницу" in labels

    # The card says it all at once: the count matches the numbered list (a
    # kind of finding on two pages is one item), each item by name.
    lines = await sites.card_lines(await db.get_site(sid))
    assert any(line.startswith("🟠 Поиск и ИИ: 4 помехи") for line in lines)
    assert any(line.startswith("    1. 🟠 ИИ-ассистенты") for line in lines)
    assert any(line.startswith("    4. 🟠 Нет картинки для превью") for line in lines)
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    _, state, _ = await menu.site_state(await db.get_site(sid))
    assert "4 помехи в поиске" in state                                # the main screen counts the same


def _snap(status="ok", problems=(), **extra):
    return {"seo": {"status": status, "critical": sum(p["severity"] == "critical" for p in problems),
                    "improve": sum(p["severity"] != "critical" for p in problems),
                    "problems": list(problems), "pages": 3, "no_js_chars": 500, "at": NOW}, **extra}


def test_seo_headline_asserts_only_what_was_measured():
    """Three cases from the task: no GSC/Webmaster, connected with fresh data,
    connected with old data — plus the negative answers."""
    url = "https://ex.test"
    clean = seo.seo_text(url, _snap())
    assert "✅ Сайт открыт для поисковиков и ИИ-ассистентов, помех нет." in clean
    assert "без JavaScript видно 500 символов" in clean and "🤖" not in clean   # a fact → caption, no icon
    assert "знают только Google и Яндекс" in clean and "📇" not in clean
    assert "остальное (🟠) не шлю" in clean

    fresh = {"verdict": "PASS", "coverage": "Submitted and indexed", "last_crawl": "2026-09-20T10:00:00Z", "at": NOW}
    text = seo.seo_text(url, _snap(google=fresh), google_on=True)
    assert "📇 Google: главная в индексе (обход 20.09)" in text and "знают только" not in text
    assert "✅ Сайт открыт для поисковиков" in text

    gone = dict(fresh, verdict="FAIL", coverage="Crawled - currently not indexed")
    text = seo.seo_text(url, _snap(google=gone), google_on=True)
    assert "🔴 <b>В Google сайта нет</b>. С моей стороны помех нет" in text
    assert "📇 Google: главной нет в индексе 🔴 (Crawled - currently not indexed)" in text

    old = dict(gone, at=(datetime.now(UTC) - timedelta(days=4)).isoformat(timespec="seconds"))
    text = seo.seo_text(url, _snap(google=old), google_on=True)
    assert "✅ Сайт открыт" in text                                  # old data is dated, not asserted
    assert f"📇 Google: по данным на {(datetime.now(UTC) - timedelta(days=4)).strftime('%d.%m')} главная не была в индексе 🔴" in text
    assert "свежих нет" in text

    text = seo.seo_text(url, _snap(), google_on=True, yandex_on=True)
    assert "📇 Google: подключён, данные появятся после утренней проверки" in text
    assert "📇 Яндекс: подключён, данные появятся" in text

    yx = {"searchable_pages": 0, "sqi": 0, "alert_problems": {"FATAL": 1}, "at": NOW}
    text = seo.seo_text(url, _snap(yandex=yx), yandex_on=True)
    assert "🔴 <b>В Яндексе сайта нет</b>" in text
    assert "📇 Яндекс: в поиске 0 страниц 🔴, ИКС 0, 🔴 проблем в Вебмастере: 1" in text
    yx = {"searchable_pages": 12, "sqi": 10, "alert_problems": {"CRITICAL": 2}, "at": NOW}
    text = seo.seo_text(url, _snap(yandex=yx), yandex_on=True)
    assert "📇 Яндекс: 12 стр. в поиске, ИКС 10, 🟠 проблем в Вебмастере: 1" in text

    hindered = [{"code": "sitemap_missing", "severity": "warning", "message": "нет карты сайта (sitemap.xml)"}]
    text = seo.seo_text(url, _snap("warning", hindered, google=gone), google_on=True)
    assert "🔴 <b>В Google сайта нет</b>. Есть 1 помеха — начни с неё." in text

    closed = [{"code": "noindex_meta", "severity": "critical", "message": "главная: в коде стоит meta robots noindex"}]
    text = seo.seo_text(url, _snap("critical", closed, google=fresh), google_on=True)
    assert "🔴 <b>Сайт закрыт от поиска</b>" in text and "📇 Google: главная в индексе (обход 20.09)" in text

    text = seo.seo_text(url, {"seo": {"status": "error", "problems": [], "at": NOW}})
    assert "⚠️ Сайт не открывался, когда я проверял" in text


async def test_explain_button_opens_the_level_matching_explanation(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    inc_id, _ = await save_incident(sid, "seo", "2 помехи в поиске, напр.: Нет карты сайта (sitemap.xml)", "warning")
    call = FakeCall(f"inc_explain:{inc_id}")
    await incidents.cb_incident_explain(call)
    text = call.message.texts[-1]
    assert text.startswith("🟠 ex.test: нет карты сайта (sitemap.xml) и ещё 1 помеха") and "Поисковикам ничего не мешает" in text
    assert "исчезает из Google" not in text                            # warning is explained as a warning
    assert "🔎 Поиск и ИИ" in buttons(call.message.reply_markup) and "✓ Закрыть" in buttons(call.message.reply_markup)
    call = FakeCall("inc_explain:999")
    await incidents.cb_incident_explain(call)
    assert call.answers[-1] == "Этой проблемы уже нет в списке" and not call.message.texts


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
    assert "🕐 Часовой пояс — Europe/Moscow" in await settings.hub_text()

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


async def test_declined_keyword_is_not_suggested_again(bot, db, monkeypatch):
    """The idea names the phrase the bot read itself and takes one tap;
    without a phrase there is no idea at all."""
    sid = await db.activate_or_create_site("https://kw.test")

    async def phrase(url):
        return "Семён Моргунов"

    async def nothing(url):
        return None

    monkeypatch.setattr(recommend, "suggest_keyword", nothing)
    assert "keyword" not in [c[0] for c in await recommend._candidates()]
    monkeypatch.setattr(recommend, "suggest_keyword", phrase)
    idea = next(c for c in await recommend._candidates() if c[0] == "keyword")
    assert "«Семён Моргунов»" in idea[1] and idea[2] == [("✅ Да, следить", f"kwok:{sid}"), ("Не надо", f"kwno:{sid}")]
    call = FakeCall(f"kwok:{sid}")
    await sites.cb_keyword_accept(call)
    assert "Слежу" in call.message.texts[-1] and (await db.get_site_by_url("https://kw.test"))["keyword"] == "Семён Моргунов"
    await db.update_site_settings(sid, keyword="")
    call = FakeCall(f"kwno:{sid}")
    await sites.cb_keyword_decline(call, FakeState())
    assert any(a and a.startswith("Ок") for a in call.answers) and call.message.texts   # back on the main screen
    assert "keyword" not in [c[0] for c in await recommend._candidates()]


async def test_a_clean_live_audit_closes_a_stale_search_finding(bot, db, monkeypatch):
    """3 Nov 2026: the fixed audit said «помех нет» on the screen while the
    false incident from the morning stayed in «Проблемы» until next 07:30."""
    sid = await db.activate_or_create_site("https://ex.test")
    await save_incident(sid, "seo", "1 помеха в поиске, напр.: Нет карты сайта (sitemap.xml)", "warning")
    status = {"value": "warning"}

    async def audit(urls, manage=True):
        assert manage is False                       # still a read-only observer otherwise
        return [SeoResult(url=urls[0], site_id=sid, status=status["value"])]

    async def no_index(site_id, url):
        return None

    monkeypatch.setattr(seo, "check_all_seo", audit)
    monkeypatch.setattr(seo.index_status, "refresh", no_index)
    await seo.cb_seo_live(FakeCall(f"seo_live:{sid}"))
    assert [i["check_type"] for i in await get_active_incidents()] == ["seo"]   # findings remain → stays
    status["value"] = "error"
    await seo.cb_seo_live(FakeCall(f"seo_live:{sid}"))
    assert await get_active_incidents()                                      # could not check → stays
    status["value"] = "ok"
    await seo.cb_seo_live(FakeCall(f"seo_live:{sid}"))
    assert await get_active_incidents() == []


async def test_a_clean_manual_link_check_closes_the_links_alert(bot, db, monkeypatch):
    """03.11.2026: the sites were fixed, «🔗 Ссылки» said all links open, and
    the 🟠 stayed in «Проблемы» until the next six-hourly crawl."""
    sid = await db.activate_or_create_site("https://ex.test")
    page = {"html": '<a href="https://gone.test/x">x</a>'}

    async def fetch(session, url):
        return page["html"]

    async def link(session, url, base):
        return LinkCheck(url, 404 if "gone" in url else 200, "gone" not in url)

    monkeypatch.setattr(links_checker, "_fetch_page", fetch)
    monkeypatch.setattr(links_checker, "_check_link", link)
    await links_checker.check_links("https://ex.test")                       # the crawl opens it
    assert [i["check_type"] for i in await get_active_incidents()] == ["links"]
    await links.cb_check_links(FakeCall(f"check_links:{sid}"))               # still broken → stays
    assert await get_active_incidents()
    page["html"] = '<a href="https://ok.test/y">y</a>'
    await links.cb_check_links(FakeCall(f"check_links:{sid}"))
    assert await get_active_incidents() == []


async def test_a_task_from_the_server_variable_can_be_removed_in_the_bot(bot, db, cfg):
    """HEARTBEAT_JOBS on the server used to mean «убрать можно только там» —
    a dead end for someone who never set that variable consciously."""
    cfg(heartbeat_jobs={"backup": 1440})
    assert "backup" in settings_service.heartbeat_jobs() and settings_service.heartbeat_job_from_env("backup")
    call = FakeCall("hb_del:backup")
    await heartbeats.cb_hb_del(call)
    assert "backup" not in settings_service.heartbeat_jobs()
    assert "больше не жду" in call.message.texts[-1] and "HEARTBEAT_JOBS" in call.message.texts[-1]
    await settings_service.load()                                    # survives a restart
    assert "backup" not in settings_service.heartbeat_jobs()
    await settings_service.add_heartbeat_job("backup", 60)           # added again on purpose
    assert settings_service.heartbeat_jobs()["backup"] == 60
    await settings_service.remove_heartbeat_job("backup")
    assert "backup" not in settings_service.heartbeat_jobs()
    assert heartbeat_keyboard(["backup"]).inline_keyboard[0][1].callback_data == "hb_rm:backup"


async def test_what_a_bot_sees_is_on_the_main_screen_card_and_digest(bot, db):
    """«Соцсети живы, подписчики подписываются» next to the sites: a block
    per reporting bot; a problem line spoils the headline; a silent bot's
    old lines turn into one «не знаю»."""
    sid = await db.activate_or_create_site("https://a.test")
    await sitestatus.update(sid, "avail", status="ok", ms=100)
    await settings_service.add_heartbeat_job("socialtracker", 1440)
    await db.heartbeat_ping("socialtracker")
    await heartbeats_service.save_status("socialtracker", heartbeats_service.parse_status({
        "title": "📣 Соцсети", "open_url": "https://t.me/social_tracker_bot",
        "items": [{"name": "Telegram-канал", "level": "ok", "text": "12 340, +18 за сутки"},
                  {"name": "LinkedIn", "level": "warning", "text": "cookie истекает через 3 дн."}]}))
    header, _ = await menu.build_main_menu()
    assert header.startswith("🟠 1 проблема")
    # The block title as words: another bot's emoji does not reach the screen.
    assert "<b>Соцсети</b>\n✅ Telegram-канал · 12 340, +18 за сутки\n🟠 LinkedIn · cookie истекает через 3 дн." in header
    assert "blk:seen:socialtracker" in [b.callback_data for row in _.inline_keyboard for b in row]
    call = FakeCall("blk:seen:socialtracker")
    await blocks.cb_seen(call)
    text = call.message.texts[-1]
    assert text.startswith("📣 Соцсети · от socialtracker, ") and "🟠 LinkedIn — cookie истекает через 3 дн." in text
    assert call.message.reply_markup.inline_keyboard[0][0].url == "https://t.me/social_tracker_bot"
    call = FakeCall("hb_show:socialtracker")
    await heartbeats.cb_hb_show(call)
    assert "✅ Telegram-канал · 12 340" in call.message.texts[-1]
    assert call.message.reply_markup.inline_keyboard[0][0].url == "https://t.me/social_tracker_bot"
    watch = await scheduler._watch_lines(morning=False)
    assert watch[:4] == [("📣 Соцсети:", True), ("✅ Telegram-канал — 12 340, +18 за сутки", True),
                         ("🟠 LinkedIn — cookie истекает через 3 дн.", False), ("", True)]

    conn = await db.get_db()
    await conn.execute("UPDATE heartbeats SET last_ping = datetime('now', '-3 days') WHERE job = 'socialtracker'")
    await conn.commit()
    header, _ = await menu.build_main_menu()
    assert "<b>Соцсети</b>\n⚠️ socialtracker · молчит — не знаю, что сейчас" in header and "LinkedIn" not in header

    call = FakeCall("hb_del:socialtracker")
    await heartbeats.cb_hb_del(call)
    assert await heartbeats_service.job_status("socialtracker") is None


async def test_sites_block_is_one_card_with_arrows(bot, db):
    """«🌐 Сайты» opens the site that needs a look, and ◀ ▶ flip through
    the rest in the same message — no button per site on the main screen."""
    ids = []
    for name in ("a", "b", "c"):
        sid = await db.activate_or_create_site(f"https://{name}.test")
        await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
        ids.append(sid)
    await sitestatus.update(ids[1], "ssl", days_left=5, issuer="LE", not_after=None, error=None)
    call = FakeCall("blk:sites")
    await sites.cb_sites_block(call)
    text = call.message.texts[-1]
    assert text.startswith("<b>b.test</b>\n🟠 1 проблема · проверено в ")
    nav = [b for row in call.message.reply_markup.inline_keyboard for b in row if "◀" in b.text or "▶" in b.text]
    assert [(b.text, b.callback_data) for b in nav] == [("◀ a.test", f"check_site:{ids[0]}"),
                                                         ("c.test ▶", f"check_site:{ids[2]}")]
    assert buttons(call.message.reply_markup)[-2:] == ["➕ Добавить сайт", "🏠 Главная"]


async def test_bots_page_says_everything_and_hands_out_a_backup(bot, db, cfg, tmp_path):
    cfg(db_path=str(tmp_path / "bot.db"))
    await settings_service.add_heartbeat_job("socialbot", 1440)
    await db.heartbeat_ping("socialbot")
    text, kb = await blocks.bots_page()
    assert text.startswith("🤖 Боты и системы\n\n✅ socialbot — на связи, только что (выходит на связь раз в сутки)")
    assert "🛡 Внешнего сторожа нет" in text and "⏳ Резервная копия базы — первую сделаю сегодня ночью." in text
    assert buttons(kb) == ["⚙️ socialbot", "🛡 Подключить сторожа", "💾 Скачать копию базы",
                           "➕ Бот или задача", "🏠 Главная"]
    call = FakeCall("sys:backup")
    await blocks.cb_backup_now(call)
    assert call.message.documents and "храни её как пароль" in call.message.documents[-1]
    text, _ = await blocks.bots_page()
    assert "✅ Резервная копия базы — " in text and "Делаю каждую ночь" in text


async def test_old_check_everything_button_lands_on_main(bot, db):
    call = FakeCall("run_full_check")
    await menu.cb_main_menu(call, FakeState())
    assert "Пока ни за чем не слежу" in call.message.texts[-1]


async def test_site_card_says_everything_without_extra_buttons(bot, db, cfg, tmp_path, monkeypatch):
    """The card is the whole story: broken links with their reasons, each
    search finding by name, speed over the week, the page itself above the
    text — and one «как исправить» instead of «🔎 Поиск и ИИ» / «🔗 Ссылки»
    / «📸 Как выглядит» buttons."""
    cfg(db_path=str(tmp_path / "bot.db"), public_base_url="https://bot.example")
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    for _ in range(3):
        await db.save_check(sid, "availability", "ok", response_time_ms=120)
    await sitestatus.update(sid, "links", status="warning", broken=1, pending=0, total=24,
                            items=[{"url": "https://github.com/x/gone", "code": 404, "error": None}])
    await sitestatus.update(sid, "seo", status="warning", critical=0, improve=1, pages=1,
                            problems=[{"code": "sitemap_missing", "severity": "warning", "message": "нет sitemap.xml"}])

    async def shot(url):
        return b"\x89PNG fake", None
    monkeypatch.setattr(screenshots, "fetch_screenshot", shot)
    assert await screenshots.refresh(sid, "https://ex.test")

    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    text = call.message.texts[-1]
    assert "🟠 Ссылки: 1 из 24 не открывается" in text and "• https://github.com/x/gone — " in text
    assert "🟠 Поиск и ИИ: 1 помеха" in text and "    1. 🟠 " in text
    labels = buttons(call.message.reply_markup)
    assert labels[0] == "🛠 Как исправить поиск" and "🔄 Перепроверить" in labels
    assert not {"🔎 Поиск и ИИ", "🔗 Ссылки", "📸 Как выглядит сайт"} & set(labels)
    picture = call.message.previews[-1]
    assert picture.startswith("https://bot.example/shot/") and f"/{sid}?v=" in picture
    # Said outright: an unset field takes the bot-wide «no previews» and the
    # picture silently never shows (09.11.2026).
    assert call.message.preview_options[-1].is_disabled is False

    _, kb = await site_settings.sset_screen(await db.get_site(sid))
    assert "📸 Как выглядит сайт" not in buttons(kb)


async def test_recheck_renews_links_and_search_too(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    calls = []

    async def avail(url, manage=True):
        calls.append("avail")
    async def links(url, manage=True):
        calls.append("links")
    async def audit(site):
        calls.append("seo")
    async def nothing(url, manage=True):
        return None
    monkeypatch.setattr(sites, "check_availability", avail)
    monkeypatch.setattr(sites, "check_ssl", nothing)
    monkeypatch.setattr(sites, "check_domain", nothing)
    monkeypatch.setattr(sites, "check_links", links)
    monkeypatch.setattr(sites.seo_screen, "audit_now", audit)
    await sites.cb_check_site_live(FakeCall(f"check_site_live:{sid}"))
    assert calls == ["avail", "links", "seo"]


async def test_site_card_tells_when_it_fell_where_it_lives_and_how_the_rest_is(bot, db):
    """What the bot knew and the card did not say: the last outage with its
    cause, the watched phrase, hosting and the domain's DNS, the pages from
    the sitemap."""
    sid = await db.activate_or_create_site("https://ex.test")
    await db.update_site_settings(sid, keyword="Привет", keyword_mode="present")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.update(sid, "deep", sampled=10, errors=0)
    await db.set_state(f"hosting:{sid}", "cloudflare")
    await db.set_dns_state("ex.test", "NS", "ada.ns.cloudflare.com,bob.ns.cloudflare.com")
    inc_id, _ = await save_incident(sid, "availability", "Site down: HTTP 502", "critical")
    conn = await db.get_db()
    await conn.execute("UPDATE incidents SET created_at = datetime('now', '-2 days'), "
                       "resolved = 1, resolved_at = datetime('now', '-2 days', '+5 minutes') WHERE id = ?", (inc_id,))
    await conn.commit()
    text = "\n".join(await sites.card_lines(await db.get_site(sid)))
    assert "✅ Последний сбой " in text and " — 5 мин, хостинг отвечает ошибкой 502" in text
    assert "✅ Фраза «Привет» на месте" in text
    assert await sites.hosting_words(await db.get_site(sid)) == " · Cloudflare"   # in the card's title
    assert "✅ DNS не менялись с " in text
    assert "✅ Страницы из карты сайта: 10 из 10 отвечают" in text
    # Each page with its own mark, when the check kept them.
    await sitestatus.update(sid, "deep", sampled=3, errors=1, pages=[["/", None], ["/a", 502], ["/b", None]])
    lines = await sites.card_lines(await db.get_site(sid))
    assert lines[:4] == ["🟠 Страницы из карты сайта: 1 из 3 отвечают ошибкой сервера",
                         "    ✅ /", "    🔴 /a — ошибка 502", "    ✅ /b"]              # a problem: at the top


def test_on_cloudflare_the_screen_does_not_claim_what_it_cannot_see():
    """Cloudflare recognises the real AI crawlers by address — a request with
    their signature passing proves nothing there. The headline drops «и
    ИИ-ассистентов», the caption says why, a button leads to the panel."""
    url = "https://ex.test"
    text = seo.seo_text(url, _snap(hosting="cloudflare"))
    assert "✅ Сайт открыт для поисковиков, помех нет." in text and "и ИИ-ассистентов" not in text
    assert "настоящих ботов Cloudflare узнаёт по адресу" in text
    kb = seo.seo_keyboard(1, _snap()["seo"], hosting="cloudflare")
    assert "seo_fix:1:ai_cloudflare" in [b.callback_data for row in kb.inline_keyboard for b in row]

    refused = [{"code": "ai_blocked", "severity": "warning",
                "message": "обычный запрос сайт пропустил, а запрос с подписью GPTBot (HTTP 403) — нет"}]
    text = seo.seo_text(url, _snap("warning", refused, hosting="cloudflare"))
    assert "🤖 ИИ-ассистенты: запрос с их подписью получает отказ — скорее всего, сайт для них закрыт." in text
    assert "узнаёт по адресу" not in text                          # the refusal is already the answer
    kb = seo.seo_keyboard(1, _snap("warning", refused)["seo"], hosting="cloudflare")
    assert "ai_cloudflare" not in " ".join(b.callback_data for row in kb.inline_keyboard for b in row)

    text = seo.seo_text(url, _snap(hosting="vercel"))
    assert "✅ Сайт открыт для поисковиков и ИИ-ассистентов, помех нет." in text   # a block by signature is all there is


async def test_card_says_the_cloudflare_caveat_once(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.set_field(sid, "hosting", "cloudflare")
    await sitestatus.update(sid, "seo", status="ok", critical=0, improve=0, pages=1, problems=[])

    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    assert "✅ Поиск и ИИ: помех нет" in call.message.texts[-1]
    assert "    Пускает ли Cloudflare ИИ-ассистентов, снаружи не видно" in call.message.texts[-1]
    assert "🤖 Проверить в Cloudflare" in buttons(call.message.reply_markup)

    fix = FakeCall(f"seo_fix:{sid}:ai_cloudflare")
    await seo.cb_seo_fix(fix)
    assert "AI Crawl Control" in fix.message.texts[-1]
    assert "🔗 Открыть Cloudflare" in buttons(fix.message.reply_markup)

    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    assert "Пускает ли Cloudflare" not in call.message.texts[-1]
    assert "🤖 Проверить в Cloudflare" not in buttons(call.message.reply_markup)
