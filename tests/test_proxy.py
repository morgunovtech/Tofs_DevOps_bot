"""TELEGRAM_PROXY: every call to Telegram through a proxy, for a server that
cannot reach api.telegram.org directly. A bad value stops the start with a
sentence; the password never reaches a log or a screen."""

import pytest
from aiogram.client.session.aiohttp import AiohttpSession

import main
from utils.urls import proxy_label, proxy_problem


def test_proxy_values():
    assert proxy_problem("socks5://user:secret@10.0.0.2:1080") is None
    assert proxy_problem("socks4://proxy.example:1080") is None
    assert proxy_problem("http://proxy.example:3128") is None
    assert "схема «https»" in proxy_problem("https://proxy.example:443")   # aiohttp-socks has no https://
    assert "схема «socks5h»" in proxy_problem("socks5h://proxy.example:1080")
    assert "не подходит" in proxy_problem("proxy.example:1080")             # no scheme at all
    assert proxy_problem("socks5://proxy.example") == "нет порта — нужен вид socks5://host:1080"
    assert proxy_problem("socks5://proxy.example:abc") == "порт не число — нужен вид socks5://host:1080"
    assert proxy_problem("socks5://:1080").startswith("нет адреса сервера")


def test_the_label_never_shows_the_password():
    assert proxy_label("socks5://user:secret@10.0.0.2:1080") == "socks5://10.0.0.2:1080"


def test_no_proxy_means_the_default_session(cfg):
    cfg(telegram_proxy="")
    assert main.telegram_session() is None


async def test_proxy_session(cfg):
    cfg(telegram_proxy="socks5://user:secret@10.0.0.2:1080")
    session = main.telegram_session()
    assert isinstance(session, AiohttpSession) and session.proxy == "socks5://user:secret@10.0.0.2:1080"
    await session.close()


def test_a_bad_proxy_stops_the_start_with_words(cfg):
    cfg(telegram_proxy="https://proxy.example:443")
    with pytest.raises(RuntimeError, match="TELEGRAM_PROXY: схема «https» не подходит"):
        main.telegram_session()
