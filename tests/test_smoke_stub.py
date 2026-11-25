"""tests/smoke/telegram_stub.py — the stand-in for Telegram the CI start
check points the real image at — answers as aiogram expects and refuses a
token it was not given, so that check cannot pass with the token check off."""

import importlib.util
import pathlib
import threading
from http.server import ThreadingHTTPServer

import pytest
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramUnauthorizedError

SMOKE = pathlib.Path(__file__).resolve().parent / "smoke"
spec = importlib.util.spec_from_file_location("telegram_stub", SMOKE / "telegram_stub.py")
stub = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stub)


async def test_the_stub_accepts_its_token_and_refuses_any_other(monkeypatch):
    monkeypatch.setattr(stub, "LONG_POLL_SEC", 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub.make_handler("123456:ci-smoke-token"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api = TelegramAPIServer.from_base(f"http://127.0.0.1:{server.server_address[1]}")
    try:
        bot = Bot("123456:ci-smoke-token", session=AiohttpSession(api=api))
        assert (await bot.me()).username == "ci_smoke_bot"
        assert await bot.delete_my_commands() is True
        assert await bot.get_updates(timeout=1) == []
        await bot.session.close()
        stranger = Bot("123456:not-the-token", session=AiohttpSession(api=api))
        with pytest.raises(TelegramUnauthorizedError):
            await stranger.me()
        await stranger.session.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_start_check_is_runnable():
    script = SMOKE / "start_image.sh"
    assert script.stat().st_mode & 0o111, "tests/smoke/start_image.sh lost its executable bit (CI runs it directly)"
