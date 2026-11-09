"""Railway's 30-day trial: the bot cannot read the plan, so it asks — among
the first-site steps, once around day 25, and in «🔌 Подключения» — and
never again after «✅ Уже на Hobby». Off Railway it says nothing."""

from datetime import timedelta

from conftest import FakeCall, buttons

from handlers import setup
from services import railway
from utils.clock import utcnow


async def _const(v):
    return v


async def _claimed_days_ago(db, days: int):
    await db.set_state(railway.SINCE, (utcnow() - timedelta(days=days)).isoformat(timespec="seconds"))


async def test_off_railway_nothing_is_said(bot, db, cfg, monkeypatch):
    cfg(on_railway=False)
    await railway.note_claim()
    assert await db.get_state(railway.SINCE) is None
    assert not await railway.asks()
    await db.set_state("ring_ok", "1")
    text, _ = await setup.last_steps()
    assert "Railway" not in text
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    assert "railway" not in {i.key for i in await setup.build_items()}


async def test_first_site_steps_ask_about_the_plan(bot, db, cfg):
    cfg(on_railway=True)
    text, kb = await setup.last_steps()
    assert text.startswith("Ещё три шага") and "💳 Тариф Railway" in text and "$1 в месяц" in text
    assert buttons(kb)[-2:] == ["💳 Тарифы Railway", "✅ Уже на Hobby"]
    assert kb.inline_keyboard[-1][0].url == railway.PRICING_URL

    call = FakeCall("railway:paid:onb")
    await setup.cb_railway_paid(call)
    assert await railway.is_paid() and call.answers[-1] == "Понял, больше не напоминаю"
    assert call.message.texts[-1].startswith("Ещё два шага") and "Railway" not in call.message.texts[-1]
    assert "✅ Уже на Hobby" not in buttons(call.message.reply_markup)


async def test_one_reminder_around_day_25(bot, db, cfg):
    cfg(on_railway=True)
    await railway.note_claim()
    assert await railway.days_since_claim() == 0
    assert not await railway.remind_if_due()                       # day 0: too early

    await _claimed_days_ago(db, 26)
    assert await railway.remind_if_due()
    msg = bot.sent[-1]
    assert "примерно через 4 дн." in msg["text"] and msg["silent"] is True
    assert buttons(msg["reply_markup"]) == ["💳 Тарифы Railway", "✅ Уже на Hobby"]
    assert not await railway.remind_if_due()                       # once

    await db.set_state(railway.REMINDED, None)
    await _claimed_days_ago(db, 31)
    assert not await railway.remind_if_due()                       # still alive past the trial: it pays

    await _claimed_days_ago(db, 29)
    await railway.set_paid()
    assert not await railway.remind_if_due()                       # «✅ Уже на Hobby» ends it


async def test_an_install_from_before_has_no_date_and_no_reminder(bot, db, cfg):
    cfg(on_railway=True)
    assert await railway.days_since_claim() is None
    assert not await railway.remind_if_due()


async def test_connections_screen_warns_only_inside_the_trial(bot, db, cfg, monkeypatch):
    cfg(on_railway=True)
    monkeypatch.setattr(setup, "_web_ok", lambda: _const(True))
    assert "railway" not in {i.key for i in await setup.build_items()}   # no date: an old install

    await _claimed_days_ago(db, 3)
    item = next(i for i in await setup.build_items() if i.key == "railway")
    assert item.status == "warn" and item.button == ("💳 Railway: тариф", "setup:railway")

    call = FakeCall("setup:railway")
    await setup.cb_guide_railway(call)
    assert call.message.texts[-1].startswith("💳 Тариф Railway") and "Docker" in call.message.texts[-1]
    assert buttons(call.message.reply_markup)[:2] == ["💳 Тарифы Railway", "✅ Уже на Hobby"]

    await setup.cb_railway_paid(FakeCall("railway:paid:setup"))
    item = next(i for i in await setup.build_items() if i.key == "railway")
    assert item.status == "ok" and item.title == "Railway Hobby"


async def test_the_reminder_button_answers_in_place(bot, db, cfg):
    cfg(on_railway=True)
    call = FakeCall("railway:paid")
    await setup.cb_railway_paid(call)
    assert call.message.texts[-1].startswith("✅ Понял: аккаунт Railway на Hobby")
