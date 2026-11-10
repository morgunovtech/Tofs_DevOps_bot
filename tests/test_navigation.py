"""Navigation: a list of sites, «Назад»/«Отмена» back to where the step was
opened, a forgotten step does not swallow the next message, long buttons
show progress and do not run twice, destructive taps ask first, strangers
get one «⛔» and leave nothing behind."""

import asyncio

from aiogram import Bot
from aiogram.fsm.storage.base import StorageKey
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, InaccessibleMessage
from conftest import FakeCall, buttons
from test_dispatcher import DP, GROUP, MockedSession, _callback, _message, _raw

from db.database import count_feedback, save_feedback
from handlers import alerts, common, feedback, heartbeats, mute, settings, site_settings, sites, start
from handlers.common import LeanMemoryStorage, with_running_bar
from monitors.base import AvailabilityResult
from services import actions, maintenance, runtime, sitestatus
from services import settings as settings_service


async def _const(value):
    return value


def _key(tg: Bot) -> StorageKey:
    return StorageKey(bot_id=tg.id, chat_id=777, user_id=777)


async def _bot() -> tuple[Bot, MockedSession]:
    """The real Dispatcher with a clean input step for the owner (777): DP is shared by the test modules."""
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    await DP.storage.set_state(_key(tg), None)
    await DP.storage.set_data(_key(tg), {})
    return tg, session


async def test_sites_button_opens_a_list_with_problems_first(bot, db):
    ids = [await db.activate_or_create_site(f"https://s{i:02d}.test") for i in range(12)]
    for sid in ids:
        await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.update(ids[9], "avail", status="error", ms=None, error="HTTP 502")
    call = FakeCall("blk:sites")
    await sites.cb_sites_block(call)
    assert call.message.texts[-1].startswith("🌐 Сайты (12)")
    kb = call.message.reply_markup.inline_keyboard
    assert kb[0][0].text == "🔴 s09.test" and kb[0][0].callback_data == f"check_site:{ids[9]}"
    assert "blk:sites:1" in [b.callback_data for row in kb for b in row]           # 8 per page
    call = FakeCall("blk:sites:1")
    await sites.cb_sites_block(call)
    assert "страница 2 из 2" in call.message.texts[-1]
    assert [b.callback_data for b in call.message.reply_markup.inline_keyboard[-2]] == ["blk:sites:0"]


async def test_the_pause_shows_on_the_card_itself(bot, db, monkeypatch):
    monkeypatch.setattr(sites.screenshots, "is_due", lambda *a: _const(False))
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    call = FakeCall(f"pause:{sid}:60")
    await sites.cb_pause(call)
    assert "🔧 Режим «я чиню»" in call.message.texts[-1]
    assert "✅ Починил, пиши снова" in buttons(call.message.reply_markup)
    await maintenance.pause_site(sid, None)


def test_sitemap_pages_list_only_the_broken_ones():
    healthy = sites._pages_lines({"sampled": 10, "errors": 0, "pages": [["/", None], ["/a", None]]})
    assert healthy == ["✅ Страницы из карты сайта: 10 из 10 отвечают"]
    bad = sites._pages_lines({"sampled": 3, "errors": 1, "pages": [["/", None], ["/a", 502], ["/b", None]]})
    assert bad == ["🟠 Страницы из карты сайта: 1 из 3 отвечают ошибкой сервера", "    🔴 /a — ошибка 502"]


async def test_pickers_opened_from_the_advanced_screen_come_back_to_it(bot, db):
    sid = await db.activate_or_create_site("https://ex.test")
    _, kb = await site_settings.advanced_screen(await db.get_site(sid))
    interval = next(b for row in kb.inline_keyboard for b in row if b.text == "⏱ Интервал")
    call = FakeCall(interval.callback_data)
    await site_settings.cb_site_setting_picker(call, None)
    datas = [b.callback_data for row in call.message.reply_markup.inline_keyboard for b in row]
    assert f"ssetx:{sid}" in datas and f"sset:{sid}" not in datas
    call = FakeCall(f"ssv:i:{sid}:3:x")                                     # a value picked there: back there
    await site_settings.cb_site_setting_value(call)
    assert call.message.texts[-1].startswith("⚙️ Настройки:") and "3 мин" in call.message.texts[-1]
    call = FakeCall(f"ssv:i:{sid}:15")                                      # the simple screen's own button
    await site_settings.cb_site_setting_value(call)
    assert "Проверяю реже" in call.message.texts[-1]


async def test_leaving_a_step_forgets_it(bot, db):
    tg, session = await _bot()
    await DP.feed_raw_update(tg, _callback(777, "setup:gh_token", 801))      # «🔑 Ввести токен» GitHub
    await DP.feed_raw_update(tg, _callback(777, "menu_settings", 802))       # went elsewhere
    assert await DP.storage.get_state(_key(tg)) is None
    session.calls.clear()
    await DP.feed_raw_update(tg, _message(777, "привет", 803))
    texts = [m.text for m in session.calls if isinstance(m, SendMessage)]
    assert texts and not any("токен GitHub" in t for t in texts)
    await tg.session.close()


async def test_the_step_buttons_of_the_phrase_form_keep_it(bot, db):
    """«💾 Сохранить всё равно» lives inside the phrase step and reads its
    data: the step must survive that tap (DropInputOnNavigation.KEEP)."""
    sid = await db.activate_or_create_site("https://ex.test")
    tg, session = await _bot()
    await DP.storage.set_state(_key(tg), site_settings.SiteKeywordForm.keyword)
    await DP.storage.set_data(_key(tg), {"sset_site_id": sid, "kw_mode": "present", "kw_pending": "Привет"})
    await DP.feed_raw_update(tg, _callback(777, f"sskwf:{sid}", 805))
    answers = [m.text for m in session.calls if isinstance(m, AnswerCallbackQuery)]
    assert answers == ["Сохранено ✅"]
    assert (await db.get_site(sid))["keyword"] == "Привет"
    await tg.session.close()


async def test_cancel_returns_to_the_screen_the_step_was_opened_from(bot, db):
    tg, session = await _bot()
    await DP.feed_raw_update(tg, _callback(777, "setup:yx_token", 811))
    prompt = [m for m in session.calls if isinstance(m, EditMessageText)][-1]
    cancel = prompt.reply_markup.inline_keyboard[-1][0]
    assert cancel.text == "✖️ Отмена" and cancel.callback_data == "fsm_cancel:setup:yx"
    session.calls.clear()
    await DP.feed_raw_update(tg, _callback(777, cancel.callback_data, 812))
    edits = [m for m in session.calls if isinstance(m, EditMessageText)]
    assert edits and edits[-1].text.startswith("🔎 Яндекс.Вебмастер")
    assert await DP.storage.get_state(_key(tg)) is None
    session.calls.clear()                                                    # the same old button again
    await DP.feed_raw_update(tg, _callback(777, cancel.callback_data, 813))
    assert any(isinstance(m, AnswerCallbackQuery) and "Нечего отменять" in (m.text or "") for m in session.calls)
    edits = [m for m in session.calls if isinstance(m, EditMessageText)]  # and the same way back
    assert edits and edits[-1].text.startswith("🔎 Яндекс.Вебмастер")
    await tg.session.close()


async def test_after_mute_the_way_back_and_unmute_are_buttons(bot, db):
    call = FakeCall("mute:8h")
    await mute.cb_mute_pick(call)
    assert buttons(call.message.reply_markup) == ["🔔 Снять тишину", "← Когда писать"]
    call = FakeCall("mute:off")
    await mute.cb_mute_pick(call)
    assert buttons(call.message.reply_markup) == ["← Когда писать"]


def test_when_to_write_marks_the_current_values():
    labels = buttons(settings._reports_kb())
    assert f"✅ 🌅 {settings_service.morning_hour()}:00" in labels
    assert "✅ 🔔 Будить: всё критичное" in labels and "🔔 Только «сайт лёг»" in labels


async def test_a_check_from_an_alert_shows_progress_and_does_not_run_twice(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    runs = []

    async def slow_check(url, manage=True):
        runs.append(url)
        await asyncio.sleep(0.05)
        return AvailabilityResult(url=url, site_id=sid, status_code=200, response_time_ms=90)

    monkeypatch.setattr(alerts, "check_availability", slow_check)
    first, second = FakeCall(f"act:recheck:{sid}"), FakeCall(f"act:recheck:{sid}")
    await asyncio.gather(alerts.cb_alert_action(first), alerts.cb_alert_action(second))
    assert len(runs) == 1
    assert first.message.texts[0].startswith("⏳ Проверяю") and first.message.texts[-1].startswith("✅")
    assert any(a and a.startswith("Уже проверяю") for a in second.answers)
    assert not alerts._busy                                                # released for the next tap


async def test_a_live_check_runs_once_at_a_time_and_ends_in_time(bot, db, monkeypatch):
    sid = await db.activate_or_create_site("https://ex.test")
    monkeypatch.setattr(sites.screenshots, "is_due", lambda *a: _const(False))

    async def hanging(url, manage=True):
        await asyncio.sleep(3600)

    monkeypatch.setattr(sites, "check_availability", hanging)
    monkeypatch.setattr(sites, "LIVE_CHECK_SEC", 0.1)
    first, second = FakeCall(f"check_site_live:{sid}"), FakeCall(f"check_site_live:{sid}")
    await asyncio.gather(sites.cb_check_site_live(first), sites.cb_check_site_live(second))
    assert any(a and a.startswith("Уже проверяю") for a in second.answers) and not second.message.texts
    assert any(t.startswith("⌛ Проверка ex.test не уложилась") for t in first.message.texts)
    assert first.message.texts[-1].startswith("<b>ex.test</b>")           # the card, from what is known
    assert not sites._live_running


async def test_progress_on_an_old_message_does_not_cancel_the_check():
    old = InaccessibleMessage(chat=Chat(id=1, type="private"), message_id=1)

    async def work():
        await asyncio.sleep(0.05)
        return "done"

    assert await with_running_bar(old, "x", work(), tick=0.01) == "done"


async def test_admin_text_outside_a_step_gets_an_answer(bot, db, monkeypatch):
    tg, session = await _bot()
    await DP.feed_raw_update(tg, _message(777, "https://example.com", 821))
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert sent and "Не понял" in sent[-1].text and sent[-1].reply_markup == common.BOTTOM
    monkeypatch.setattr(runtime, "_chat_id", str(GROUP["id"]))         # in the admin group: silence
    session.calls.clear()
    await DP.feed_raw_update(tg, _raw(777, "смотрю", 822, chat=GROUP))
    assert not [m for m in session.calls if isinstance(m, SendMessage)]
    await tg.session.close()


async def test_help_names_the_commands_and_start_is_short_for_the_owner(bot, db):
    assert "/menu" in start.HELP and "/help" in start.HELP and "🔧" in start.HELP
    await db.activate_or_create_site("https://seeded.test")
    tg, session = await _bot()
    await DP.feed_raw_update(tg, _message(777, "/start", 851))
    first = [m.text for m in session.calls if isinstance(m, SendMessage)]
    assert any(t.startswith("Ещё два шага") for t in first)                 # sites from SITES: the steps too
    assert any(t == start.BOTTOM_NOTE for t in first)                      # the bottom buttons, once
    session.calls.clear()
    await DP.feed_raw_update(tg, _message(777, "/start", 852))
    again = [m.text for m in session.calls if isinstance(m, SendMessage)]
    assert not any("Привет" in t or "Как я работаю" in t or "Ещё два шага" in t for t in again)
    assert not any(t == start.BOTTOM_NOTE for t in again)
    assert any("seeded.test" in t for t in again)                          # the main screen
    await tg.session.close()


def test_one_label_for_one_thing():
    assert actions.RECHECK == common.RECHECK == "🔄 Проверить снова"
    assert buttons(common.back_button()) == [common.HOME] == ["🏠 Главная"]
    assert buttons(common.cancel_kb()) == [common.CANCEL]
    assert "/menu" not in common.STALE_BUTTON and "🏠 Главная" in common.STALE_BUTTON


async def test_removing_a_task_asks_first(bot, db):
    await settings_service.add_heartbeat_job("backup", 1440)
    tg, session = await _bot()
    await DP.feed_raw_update(tg, _callback(777, "hb_rm:backup", 831))
    assert "backup" in settings_service.heartbeat_jobs()                    # one tap: only the question
    asked = [m for m in session.calls if isinstance(m, SendMessage)][-1]
    yes = [b.callback_data for row in asked.reply_markup.inline_keyboard for b in row][0]
    await DP.feed_raw_update(tg, _callback(777, yes, 832))
    assert "backup" not in settings_service.heartbeat_jobs()
    edits = [m for m in session.calls if isinstance(m, EditMessageText)]
    assert edits and edits[-1].text == "🗑 «backup» больше не жду."           # the question became the answer
    await tg.session.close()


async def test_leaving_a_task_removes_the_question_buttons(bot, db):
    call = FakeCall("noop")
    call.message.reply_markup = common.back_button()
    await heartbeats.cb_noop(call)
    assert call.answers == ["Ок, оставил"] and call.message.reply_markup is None


async def test_visitor_messages_can_be_deleted(bot, db):
    for i in range(7):
        await save_feedback("https://ex.test", "https://ex.test/p", f"spam {i}", "", "1.1.1.1")
    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    delete = next(b for row in call.message.reply_markup.inline_keyboard for b in row if b.text.startswith("🗑"))
    assert delete.text == "🗑 Удалить эти 5"
    ask = FakeCall(delete.callback_data)
    await feedback.cb_feedback_delete(ask)
    assert await count_feedback() == 7                                       # asked, not deleted
    yes = next(b for row in ask.message.reply_markup.inline_keyboard for b in row if b.text.startswith("🗑"))
    done = FakeCall(yes.callback_data)
    await feedback.cb_feedback_delete(done)
    assert await count_feedback() == 2
    assert done.message.texts[-1].startswith("🗑 Удалил: 5\n\n📩 Обратная связь с сайтов (2)")


async def test_a_stranger_gets_one_denial_per_ten_minutes(bot, db):
    common._denied.clear()
    tg, session = await _bot()
    for n in range(3):
        await DP.feed_raw_update(tg, _message(4242, f"/x{n}", 841 + n))
    assert len([m for m in session.calls if isinstance(m, SendMessage)]) == 1
    session.calls.clear()
    for n in range(2):                                                       # a tap: the alert once, then a bare answer
        await DP.feed_raw_update(tg, _callback(4343, "menu_settings", 845 + n))
    answers = [m for m in session.calls if isinstance(m, AnswerCallbackQuery)]
    assert [a.text for a in answers] == ["⛔ Доступ запрещён", None]
    assert not [m for m in session.calls if isinstance(m, SendMessage)]
    session.calls.clear()                                                    # a foreign group: silence
    await DP.feed_raw_update(tg, _raw(4444, "/start", 848, chat=GROUP))
    await DP.feed_raw_update(tg, _raw(4444, "/menu", 849, chat=GROUP))
    assert not [m for m in session.calls if isinstance(m, SendMessage)]
    await tg.session.close()


async def test_reading_a_strangers_state_leaves_no_record():
    storage = LeanMemoryStorage()
    key = StorageKey(bot_id=1, chat_id=5, user_id=5)
    assert await storage.get_state(key) is None and await storage.get_data(key) == {}
    assert await storage.get_value(key, "a", 7) == 7
    assert not storage.storage
    await storage.set_state(key, "Form:step")
    await storage.set_data(key, {"a": 1})
    assert storage.storage and await storage.get_value(key, "a") == 1
    await storage.set_state(key, None)
    await storage.set_data(key, {})
    assert not storage.storage
