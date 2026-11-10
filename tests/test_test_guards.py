"""The guards of the test suite itself (tests/conftest.py): no test can
reach the network, and the Telegram fakes refuse what Telegram refuses."""

import socket

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from conftest import FakeBot, FakeCall, FakeMessage, _net_attempts, _tg_refusals


def test_the_network_is_closed_to_tests():
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(OSError):
        socket.create_connection(("192.0.2.1", 80), timeout=1)
    assert _net_attempts == ["dns example.com", "connect 192.0.2.1:80"]
    _net_attempts.clear()            # these two attempts were the point of the test


async def test_the_fakes_refuse_what_telegram_refuses():
    bot = FakeBot()
    with pytest.raises(TelegramBadRequest, match="can't parse entities"):
        await bot.send_message(1, "<b>bold")
    with pytest.raises(TelegramBadRequest, match="can't parse entities"):
        await FakeMessage().answer("a <script> tag")
    with pytest.raises(TelegramBadRequest, match="too long"):
        await bot.send_message(1, "x" * 4097)
    await bot.send_message(1, "<b>bold", parse_mode=None)          # plain text: nothing to parse
    assert bot.sent[-1]["parse_mode"] is None
    await bot.send_message(1, "<b>ok</b> &lt;b&gt;")
    assert bot.sent[-1]["parse_mode"] == "HTML"
    with pytest.raises(TelegramBadRequest):
        await bot.send_message(1, "<b>bold", parse_mode=ParseMode.HTML)  # the enum is HTML too
    long_data = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="x", callback_data="y" * 65)]])
    with pytest.raises(TelegramBadRequest, match="callback_data"):
        await bot.send_message(1, "ok", reply_markup=long_data)
    with pytest.raises(TelegramBadRequest, match="200"):
        await FakeCall("x").answer("x" * 201)
    assert len(_tg_refusals) == 6
    _tg_refusals.clear()             # these refusals were the point of the test
