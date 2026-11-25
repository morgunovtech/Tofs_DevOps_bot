"""TELEGRAM_PROXY: every call to Telegram through a proxy, for a server that
cannot reach api.telegram.org directly. A bad value stops the start with a
sentence; the password never reaches a log or a screen."""

import logging

import pytest
from aiogram.client.session.aiohttp import AiohttpSession

import main
from utils.urls import api_url_problem, proxy_label, proxy_problem


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


async def test_an_own_bot_api_server_is_used_and_named_in_the_log(cfg, caplog):
    cfg(telegram_proxy="", telegram_api_url="http://bot-api.internal:8081")
    with caplog.at_level(logging.WARNING):
        session = main.telegram_session()
    assert session.api.api_url(token="42:T", method="getMe") == "http://bot-api.internal:8081/bot42:T/getMe"
    assert session.proxy is None
    assert "http://bot-api.internal:8081 (TELEGRAM_API_URL) instead of api.telegram.org" in caplog.text
    await session.close()
    cfg(telegram_proxy="socks5://user:secret@10.0.0.2:1080")           # both at once
    session = main.telegram_session()
    assert session.proxy and session.api.base.startswith("http://bot-api.internal:8081/")
    await session.close()


def test_a_bad_bot_api_address_stops_the_start_with_words(cfg):
    assert api_url_problem("http://localhost:8081") is None and api_url_problem("https://bot-api.example") is None
    for bad in ("localhost:8081", "ftp://host", "http://", "http://host:port", "http://user:pw@host:8081",
                "http://host:8081/bot", "http://host:8081?x=1", "http://[::1"):
        assert api_url_problem(bad), bad
        cfg(telegram_proxy="", telegram_api_url=bad)
        with pytest.raises(main.ConfigError, match="TELEGRAM_API_URL: "):
            main.telegram_session()


def test_a_bad_proxy_stops_the_start_with_words(cfg):
    cfg(telegram_proxy="https://proxy.example:443")
    with pytest.raises(RuntimeError, match="TELEGRAM_PROXY: схема «https» не подходит"):
        main.telegram_session()
