"""«🛠 Тонкие настройки»: the .env dials set from the chat — the settings
layer every reader asks, the screens, the status page's language and
title, the bot's language."""

from conftest import FakeCall, FakeMessage, FakeState, buttons

from db import database
from handlers import settings as settings_screen
from handlers import tune
from reports import scheduler
from services import settings
from utils import i18n


async def test_a_dial_wins_over_env_and_survives_a_reload(db, cfg):
    cfg(check_interval_minutes=5)
    assert settings.check_interval_minutes() == 5 and settings.dial_from_env("check_interval")
    assert database.default_interval_min() == 5
    assert await settings.set_dial("check_interval", 10)
    assert settings.check_interval_minutes() == 10 and not settings.dial_from_env("check_interval")
    assert database.default_interval_min() == 10            # the availability windows read the same number
    assert database.span_cap_sec(None) == 11 * 60.0
    await settings.load()                                    # a restart
    assert settings.check_interval_minutes() == 10
    assert await settings.set_dial("check_interval", None)   # «↩︎ Как в .env»
    assert settings.check_interval_minutes() == 5 and database.default_interval_min() == 5
    assert settings.dial_default("check_interval") == 5


async def test_every_dial_refuses_what_its_bound_refuses(db):
    for key, d in settings.DIALS.items():
        lo, hi = d.bounds
        assert not await settings.set_dial(key, lo - 1) and not await settings.set_dial(key, hi + 1)
        assert not await settings.set_dial(key, "x") and not await settings.set_dial(key, True)
        assert settings.dial_from_env(key)                   # nothing was saved
        assert await settings.set_dial(key, hi)
        assert settings.dial(key) == hi
        await settings.set_dial(key, None)
    assert not await settings.set_dial("no_such_dial", 1)
    # A value outside the bound in a restored copy is «not set», not a broken scheduler.
    await db.set_state("cfg:disk_alert_pct", "7")
    await settings.load()
    assert settings.disk_alert_pct() == 85 and settings.dial_from_env("disk_alert_pct")


async def test_the_readers_follow_the_chat_value(db, cfg):
    cfg(slow_response_ms=3000, escalation_repeat_min=30, links_check_interval_hours=6)
    await settings.set_dial("slow_ms", 5000)
    assert scheduler._slow_ms({"url": "https://ex.test"}) == 5000
    assert scheduler._slow_ms({"url": "https://ex.test", "slow_ms": 700}) == 700   # a site's own still wins
    await settings.set_dial("escalation_repeat", 0)
    assert settings.escalation_repeat_min() == 0
    await settings.set_dial("links_hours", 0)
    assert scheduler.reschedule_links()                     # no scheduler running: nothing to move, nothing failed
    for key in ("slow_ms", "escalation_repeat", "links_hours"):
        await settings.set_dial(key, None)


async def test_the_links_crawl_follows_the_dial_on_a_running_scheduler(db):
    sch = scheduler.setup_scheduler()
    try:
        assert sch.get_job("links_checks") is not None     # six hours by default
        await settings.set_dial("links_hours", 0)
        assert scheduler.reschedule_links() and sch.get_job("links_checks") is None
        await settings.set_dial("links_hours", 12)
        assert scheduler.reschedule_links() and sch.get_job("links_checks") is not None
        await settings.set_dial("links_hours", 1)
        assert scheduler.reschedule_links() and sch.get_job("links_checks") is not None
    finally:
        await settings.set_dial("links_hours", None)
        scheduler._scheduler = None


async def test_the_screen_lists_every_dial_and_its_source(db):
    call = FakeCall("menu_tune")
    await tune.cb_tune(call)
    text = call.message.texts[-1]
    assert text.startswith("🛠 Тонкие настройки")
    assert "⏱ Проверка сайтов: раз в 5 мин — из .env" in text
    assert "🐢 Порог «медленно»: от 3 с — из .env" in text
    assert "🔁 Напоминание о лежащем сайте: через 30 мин — из .env" in text
    assert "🔗 Обход ссылок: раз в 6 ч — из .env" in text
    assert "🌐 Статус-страница: по-русски, без своего названия — из .env" in text
    assert "🗣 Язык бота: русский — из .env" in text
    assert "Только на сервере (.env):" in text and "ALLOW_PRIVATE_TARGETS" in text and "TELEGRAM_PROXY" in text
    labels = buttons(call.message.reply_markup)
    assert labels[0] == "⏱ Проверка сайтов" and labels[-3:] == ["🌐 Статус-страница", "🗣 Язык бота", "← Настройки"]
    # The hub says at a glance whether anything differs from the server.
    assert "всё из .env" in await settings_screen.hub_text()


async def test_a_dial_screen_offers_values_marks_the_current_and_refuses_the_rest(db):
    call = FakeCall("tune:check_interval")
    await tune.cb_dial(call)
    text = call.message.texts[-1]
    assert text.startswith("⏱ Проверка сайтов: <b>раз в 5 мин</b> — из .env (CHECK_INTERVAL_MINUTES=5).")
    labels = buttons(call.message.reply_markup)
    assert "✅ раз в 5 мин" in labels and "раз в 60 мин" in labels and labels[-2:] == ["↩︎ Как в .env (раз в 5 мин)", "← Тонкие настройки"]

    call = FakeCall("tune:check_interval:10")
    await tune.cb_dial(call)
    assert call.answers == ["Сохранено ✅"] and settings.check_interval_minutes() == 10
    text = call.message.texts[-1]
    assert "<b>раз в 10 мин</b> — задано здесь (в .env CHECK_INTERVAL_MINUTES=5)" in text
    assert "✅ раз в 10 мин" in buttons(call.message.reply_markup)
    assert "задано здесь: 1" in await settings_screen.hub_text()

    for data in ("tune:check_interval:7", "tune:check_interval:abc", "tune:check_interval:10:x"):   # not offered
        call = FakeCall(data)
        await tune.cb_dial(call)
        assert call.answers == ["Не понял"] and not call.message.texts
    assert settings.check_interval_minutes() == 10

    call = FakeCall("tune:check_interval:env")
    await tune.cb_dial(call)
    assert settings.check_interval_minutes() == 5 and settings.dial_from_env("check_interval")

    call = FakeCall("tune:escalation_repeat")                 # «выкл» is a value too
    await tune.cb_dial(call)
    assert buttons(call.message.reply_markup)[0] == "выкл"
    call = FakeCall("tune:escalation_repeat:0")
    await tune.cb_dial(call)
    assert settings.escalation_repeat_min() == 0 and "<b>выкл</b>" in call.message.texts[-1]
    await settings.set_dial("escalation_repeat", None)


async def test_the_status_page_language_and_title_from_the_chat(db, cfg):
    cfg(status_page_lang="ru", status_page_title="")
    call = FakeCall("tune:status_page")
    await tune.cb_status_page(call)
    assert call.message.texts[-1].startswith("🌐 Статус-страница: по-русски, без своего названия.")
    assert buttons(call.message.reply_markup)[:3] == ["✅ 🌐 По-русски", "🌐 По-английски", "✏️ Задать название"]
    call = FakeCall("tune:sp_lang:en")
    await tune.cb_status_page(call)
    assert settings.status_page_lang() == "en" and buttons(call.message.reply_markup)[1] == "✅ 🌐 По-английски"

    call = FakeCall("tune:sp_title")
    state = FakeState()
    await tune.cb_status_page_title(call, state)
    assert state.state == tune.TuneForm.title
    msg = FakeMessage("x" * 61)
    await tune.msg_status_page_title(msg, state)
    assert msg.texts[-1].startswith("Нужна одна строка") and settings.status_page_title() == ""
    msg = FakeMessage("  Acme —  статус  ")
    await tune.msg_status_page_title(msg, state)
    assert settings.status_page_title() == "Acme — статус" and state.state is None
    assert msg.texts[-1].startswith("✅ Сохранил.") and "название «Acme — статус»" in msg.texts[-1]
    assert "✏️ Название: «Acme — статус»" in buttons(msg.markups[-1])
    msg = FakeMessage("-")                                    # no title of its own, whatever .env says
    await tune.msg_status_page_title(msg, FakeState())
    assert settings.status_page_title() == "" and not settings.text_from_env("status_page_title")

    call = FakeCall("tune:sp_env")
    await tune.cb_status_page(call)
    assert settings.status_page_lang() == "ru" and settings.text_from_env("status_page_title")


async def test_the_bot_language_switches_at_once_and_the_bottom_keyboard_is_resent(db):
    try:
        call = FakeCall("tune:lang")
        await tune.cb_lang(call)
        assert call.message.texts[-1].startswith("🗣 Язык бота: <b>русский</b> — из .env (BOT_LANG=ru).")
        assert buttons(call.message.reply_markup)[:2] == ["✅ 🗣 Русский", "🗣 English"]
        call = FakeCall("tune:lang:en")
        await tune.cb_lang(call)
        assert i18n.language() == "en" and settings.lang() == "en"
        assert call.message.texts[-2].startswith("🗣 Bot language: <b>English</b> — set here (in .env BOT_LANG=ru).")
        assert call.message.texts[-1] == "Bot language: English. The buttons under the input field are updated."
        assert call.message.markups[-1].keyboard[0][0].text == "🏠 Home"
        await settings.load()                                 # a restart keeps the choice
        assert i18n.language() == "en"
        call = FakeCall("tune:lang:en")                       # the same again: no second keyboard
        await tune.cb_lang(call)
        assert len(call.message.texts) == 1
        call = FakeCall("tune:lang:env")
        await tune.cb_lang(call)
        assert i18n.language() == "ru" and settings.text_from_env("lang")
        assert call.message.texts[-1].startswith("Язык бота: русский.")
    finally:
        await settings.set_lang(None)
        i18n.set_language("ru")
