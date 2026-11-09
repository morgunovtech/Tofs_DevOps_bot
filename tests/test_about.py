"""The bottom buttons (no ☰ menu) and «📖 Что я умею»: what the bot does
and what each connection gives — and who needs it."""

import pathlib

from conftest import FakeCall, FakeMessage, FakeState, buttons

import handlers
from handlers import about, bottom
from services import integrations


def test_bottom_buttons_win_over_any_pending_input():
    """A tap on «🏠 Главная» while the bot waits for a site address must open
    the main screen, not be read as the address: its router goes first."""
    assert handlers.admin.sub_routers[0] is bottom.router


async def test_bottom_buttons(bot, db):
    state = FakeState()
    await state.set_state("SiteForm:url")
    msg = FakeMessage("🏠 Главная")
    await bottom.on_home(msg, state)
    assert state.state is None and msg.texts                      # the main screen, input aborted

    msg = FakeMessage("📖 Что я умею")
    await bottom.on_about(msg, FakeState())
    assert msg.texts[-1].startswith("📖 <b>Что я умею — сам, без настроек</b>")
    assert "🔌 Что можно подключить" in buttons(msg.reply_markup)

    msg = FakeMessage("❓ Значки")
    await bottom.on_icons(msg, FakeState())
    assert "Что значат значки" in msg.texts[-1] and "кнопка под полем ввода" in msg.texts[-1]


def test_every_function_is_said_as_what_it_does_and_what_you_get():
    text = about.features_text()
    assert len(text) < 4096
    for group, items in about._features():
        assert group in text
        for what, gain in items:
            assert f"• {what} → <i>{gain}</i>" in text


async def test_connections_say_what_they_give_and_who_needs_them(bot, db, monkeypatch):
    text, kb = await about.modules_page()
    assert len(text) < 4096 and "всё необязательно" in text
    for _, m in about.MODULES:
        assert m.title in text and f"{m.gives[:1].upper()}{m.gives[1:]} → <i>{m.who}</i>." in text
    assert "⚡ Скорость" in buttons(kb) and "<b>⚡ Скорость у посетителей</b>\n" in text

    monkeypatch.setattr(integrations, "crux_key", lambda: "AIza-test")
    call = FakeCall("about:modules")
    await about.cb_modules(call)
    text = call.message.texts[-1]
    assert "<b>⚡ Скорость у посетителей</b> ✅ подключено" in text
    assert "⚡ Скорость" not in buttons(call.message.reply_markup)    # nothing to connect there


def test_every_connect_button_leads_somewhere():
    source = pathlib.Path("handlers/setup.py").read_text()
    for _, m in about.MODULES:
        callback = m.button[1]
        assert f'"{callback}"' in source or f'"{callback.rsplit(":", 1)[0]}:' in source, callback
