"""The feedback widget as a site sees it: the snippet the bot gives out,
the file under its versioned address, what is kept from a message."""

import base64
import hashlib

from aiohttp.test_utils import TestClient, TestServer
from conftest import FakeCall

from db.database import list_feedback
from handlers import feedback
from web import webhook, widget

SECRET = "unit-test-webhook-secret"


async def test_snippet_loads_the_widget_after_the_page_and_pins_it(bot, db):
    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    text = call.message.texts[-1]
    assert f"feedback-widget.{widget.VERSION}.js" in text
    assert widget.INTEGRITY in text and "crossOrigin='anonymous'" in text
    assert "addEventListener('load',l)" in text and "readyState==='complete'" in text
    assert "&lt;script src=" not in text          # the old blocking tag is gone


async def test_versioned_widget_file_matches_its_integrity(bot):
    async with TestClient(TestServer(webhook.create_app())) as c:
        r = await c.get(f"/feedback-widget.{widget.VERSION}.js")
        body = await r.read()
        assert r.status == 200 and "immutable" in r.headers["Cache-Control"]
        assert r.headers["Access-Control-Allow-Origin"] == "*"
        assert r.content_type == "application/javascript"
        # A cross-origin <script integrity crossorigin="anonymous"> load: the
        # hardening meant for HTML pages must not touch this answer.
        assert "Cross-Origin-Resource-Policy" not in r.headers
        assert "Content-Security-Policy" not in r.headers
        assert "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode() == widget.INTEGRITY
        assert (await c.get("/feedback-widget.000000000000.js")).status == 404
        old = await c.get("/feedback-widget.js")      # snippets already on sites keep working
        assert old.status == 200 and await old.read() == body


async def test_feedback_keeps_the_page_but_not_its_query_or_user_agent(bot):
    async with TestClient(TestServer(webhook.create_app())) as c:
        r = await c.post("/api/feedback", headers={"X-Webhook-Secret": SECRET, "User-Agent": "UA-test"},
                         json={"message": "Корзина не работает", "site_url": "https://s.test",
                               "page_url": "https://s.test/reset?token=RESET-123&e=a%40b.test#step2"})
        assert r.status == 201
        r = await c.post("/api/feedback", headers={"X-Webhook-Secret": SECRET},
                         json={"message": "SPA", "site_url": "https://s.test",
                               "page_url": "https://s.test/#/cart", "full_url": True})
        assert r.status == 201
    newest, first = await list_feedback(2)
    assert first["page_url"] == "https://s.test/reset" and first["user_agent"] == ""
    assert all("RESET-123" not in m["text"] for m in bot.sent)
    assert newest["page_url"] == "https://s.test/#/cart"


def test_query_is_dropped_even_from_an_address_urlsplit_refuses():
    assert webhook._without_query("https://s.test/a?x=1#f") == "https://s.test/a"
    assert webhook._without_query("https://[bad/reset?token=1#f") == "https://[bad/reset"


async def test_no_public_base_url_warning_when_railway_gives_the_address(bot, db, cfg):
    cfg(public_base_url="", railway_public_domain="bot-prod.up.railway.app")
    call = FakeCall("menu_feedback:0")
    await feedback.cb_feedback(call)
    text = call.message.texts[-1]
    assert "Задай PUBLIC_BASE_URL" not in text
    assert "https://bot-prod.up.railway.app/feedback-widget." in text


async def test_owner_hears_once_when_the_pinned_widget_changed(bot, monkeypatch):
    async with TestClient(TestServer(webhook.create_app())) as c:
        assert (await c.get(f"/feedback-widget.{widget.VERSION}.js")).status == 200
    await webhook.widget_changed_notice()                  # same file: nothing to say
    monkeypatch.setattr(widget, "VERSION", "0123456789ab")  # the next release changed the file
    await webhook.widget_changed_notice()
    await webhook.widget_changed_notice()
    said = [m["text"] for m in bot.sent if "Виджет обратной связи обновился" in m["text"]]
    assert len(said) == 1


def test_widget_source_keeps_its_contract():
    """String checks of web/feedback-widget.js; the browser behaviour itself
    is checked by hand (the plan has the steps)."""
    js = widget.BODY.decode()
    assert "__ready" in js                    # a second copy on the page does nothing
    assert "DOMContentLoaded" in js           # a tag in <head> waits for <body>
    assert "currentScript" in js              # settings from data-* attributes (strict CSP)
    assert "attachShadow" in js and "adoptedStyleSheets" in js
    assert "AbortController" in js            # a hung bot does not freeze the form
    assert "location.origin + location.pathname" in js
    assert "${TITLE}" not in js and "aria-label=\"' + TITLE" not in js   # the title goes in as text
