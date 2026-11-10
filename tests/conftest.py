"""Test environment: env vars are set BEFORE the project's config module is
imported, so every test sees the same deterministic configuration."""

import os

os.environ.update({
    "TELEGRAM_BOT_TOKEN": "123456:TEST",
    "TELEGRAM_ADMIN_CHAT_ID": "",
    "TELEGRAM_ADMIN_USER_ID": "",
    "WEBHOOK_SECRET": "unit-test-webhook-secret",
    "HEARTBEAT_SECRET": "unit-test-heartbeat-secret",
    "SECOND_OPINION": "0",
    "NET_CANARIES": "",
    "QUIET_HOURS": "",
    "DEPENDENCY_WATCH": "0",
    "TIMEZONE": "Europe/Moscow",
    "PUBLIC_BASE_URL": "https://bot.example.test",
    "STATUS_PAGE": "0",
    "STATUS_PAGE_SLUG": "",
    # A developer's .env may say STATUS_PAGE_LANG=en: the tests expect the default.
    "STATUS_PAGE_LANG": "ru",
    "STATUS_PAGE_TITLE": "",
    "SITES": "",
    "HEARTBEAT_JOBS": "",
    "TRUST_PROXY": "0",
    # The screenshot service is a closed port on loopback: nothing leaves the
    # machine and the client gets a refusal at once. A test that needs the
    # real client against a mock sets cfg(screenshot_template=<TestServer>).
    "SCREENSHOT_TEMPLATE": "http://127.0.0.1:9/shot?url={url}",
    # A developer's shell often exports a GitHub token for gh/git.
    "GITHUB_TOKEN": "",
    "GITHUB_OWNER": "",
})

import asyncio  # noqa: E402
import errno  # noqa: E402
import html  # noqa: E402
import ipaddress  # noqa: E402
import re  # noqa: E402
import socket  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from aiogram.exceptions import TelegramBadRequest  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402

from config import config  # noqa: E402
from db import database  # noqa: E402
from handlers import alerts as alert_handlers  # noqa: E402
from handlers import common  # noqa: E402
from handlers import sites as site_handlers  # noqa: E402
from monitors import availability, dns_checker, links_checker, netcheck, parse_worker, rdap  # noqa: E402
from reports import scheduler  # noqa: E402
from services import (  # noqa: E402
    automation,
    gsc,
    health,
    helpers,
    integrations,
    maintenance,
    notifier,
    runtime,
    screenshots,
    secrets,
    settings,
    sitestatus,
    updates,
)
from web import status_page, webhook  # noqa: E402

# ── No network in tests ──────────────────────────────────────────────────────
# Tests talk only to aiohttp TestServer on 127.0.0.1. Any other address or a
# DNS lookup is refused (nothing leaves the machine) and remembered, and the
# test that tried it fails — even when the code under test swallowed the error.
_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_SENDTO = socket.socket.sendto
_REAL_GETADDRINFO = socket.getaddrinfo
_net_attempts: list[str] = []


def _is_loopback(host) -> bool:
    if host in ("localhost", "localhost.localdomain"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _guard(address, kind: str) -> None:
    if not (isinstance(address, tuple) and len(address) >= 2):
        return                              # a unix socket path is local
    host, port = address[0], address[1]
    if not _is_loopback(host):
        _net_attempts.append(f"{kind} {host}:{port}")
        raise OSError(errno.ENETUNREACH, f"tests must not use the network: {host}:{port}")
    if port in (80, 443):                   # never a TestServer: answer as a closed port would
        raise ConnectionRefusedError(errno.ECONNREFUSED, f"tests: {host}:{port} is closed")


def _connect(self, address):
    _guard(address, "connect")
    return _REAL_CONNECT(self, address)


def _connect_ex(self, address):
    _guard(address, "connect")
    return _REAL_CONNECT_EX(self, address)


def _sendto(self, data, *args):
    _guard(args[-1], "sendto")
    return _REAL_SENDTO(self, data, *args)


def _getaddrinfo(host, *args, **kwargs):
    if host is not None and not _is_loopback(host):
        try:
            ipaddress.ip_address(str(host).split("%")[0])
        except ValueError:
            _net_attempts.append(f"dns {host}")
            raise socket.gaierror(socket.EAI_NONAME, f"tests must not use DNS: {host}") from None
    return _REAL_GETADDRINFO(host, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex)
    monkeypatch.setattr(socket.socket, "sendto", _sendto)
    monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
    _net_attempts.clear()
    yield
    if _net_attempts:
        pytest.fail("the test tried to reach the network: " + ", ".join(sorted(set(_net_attempts)))
                    + " — stub the call (monkeypatch) or use aiohttp TestServer", pytrace=False)


@pytest.fixture(autouse=True)
def _screenshot_service_is_closed(monkeypatch):
    """The screenshot service of the tests (SCREENSHOT_TEMPLATE above) is a
    closed port: the real client runs, is refused at once and does not wait
    between its retries."""
    monkeypatch.setattr(screenshots, "RETRY_DELAY_SEC", 0)


# ── Telegram's rules for what the fakes below receive ────────────────────────
# Telegram refuses a message whose HTML it cannot parse, a text over 4096
# characters (a caption over 1024), callback_data outside 1..64 bytes and a
# tap answer over 200 characters. The fakes refuse the same way and remember
# it; a test fails on any refusal unless it is marked telegram_rejects (it
# checks how the bot copes with a refusal on purpose).
_DEFAULT = "HTML"              # parse_mode not given: the bot-wide default (main.py), HTML
_TG_TAGS = frozenset({"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span",
                      "tg-spoiler", "a", "tg-emoji", "code", "pre", "blockquote"})
_TG_TAG = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9-]*)(\s[^<>]*)?>")
_tg_refusals: list[str] = []


def _html_problem(text: str) -> str | None:
    stack, i = [], 0
    while (j := text.find("<", i)) >= 0:
        m = _TG_TAG.match(text, j)
        name = m.group(2).lower() if m else ""
        if name not in _TG_TAGS:
            return f"can't parse entities: unsupported start tag at {text[j:j + 24]!r}"
        if m.group(1):
            if not stack or stack.pop() != name:
                return f"can't parse entities: unexpected end tag </{name}>"
        else:
            stack.append(name)
        i = m.end()
    return f"can't parse entities: unclosed <{stack[-1]}>" if stack else None


def telegram_check(text: str | None = None, parse_mode=_DEFAULT, markup=None,
                   limit: int = 4096) -> str | None:
    """Raise TelegramBadRequest where Telegram would; return the parse mode
    the text went with ("HTML" or None) for the record."""
    mode = getattr(parse_mode, "value", parse_mode)          # ParseMode.HTML or "HTML"
    html_mode = str(mode or "").upper() == "HTML"
    problem = None
    if text is not None:
        problem = _html_problem(text) if html_mode else None
        visible = html.unescape(_TG_TAG.sub("", text)) if html_mode else text
        if problem is None and len(visible) > limit:
            problem = f"message is too long ({len(visible)} > {limit})"
    for row in getattr(markup, "inline_keyboard", None) or []:
        for button in row:
            data = button.callback_data
            if data is not None and not 1 <= len(data.encode()) <= 64:
                problem = problem or f"callback_data must be 1..64 bytes: {data!r}"
    if problem:
        _refuse(problem)
    return "HTML" if html_mode else None


def _refuse(problem: str):
    _tg_refusals.append(problem)
    raise TelegramBadRequest(method=SendMessage(chat_id=0, text="-"), message=f"Bad Request: {problem}")


@pytest.fixture(autouse=True)
def _telegram_rules(request):
    _tg_refusals.clear()
    yield
    if _tg_refusals and not request.node.get_closest_marker("telegram_rejects"):
        pytest.fail("Telegram would refuse: " + "; ".join(_tg_refusals), pytrace=False)


def pytest_configure(config):
    config.addinivalue_line("markers", "telegram_rejects: the test makes Telegram refuse a message on purpose")


class FakeBot:
    """Records what the bot would have sent; send_message returns a
    message-like object with a message_id, as aiogram's Bot does, and
    refuses what Telegram would refuse (see telegram_check)."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.fail = False
        self.fail_with: list[Exception] = []    # raised one per call, in order, before `fail`

    async def send_message(self, chat_id, text, reply_markup=None, disable_notification=False,
                           parse_mode=_DEFAULT, request_timeout=None):
        if self.fail_with:
            raise self.fail_with.pop(0)
        if self.fail:
            raise RuntimeError("telegram down")
        mode = telegram_check(text, parse_mode, reply_markup)
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup,
                          "silent": disable_notification, "parse_mode": mode})
        return SimpleNamespace(message_id=len(self.sent), text=text)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None,
                                parse_mode=_DEFAULT):
        mode = telegram_check(text, parse_mode, reply_markup)
        self.edited.append({"message_id": message_id, "text": text, "reply_markup": reply_markup,
                            "parse_mode": mode})
        return SimpleNamespace(message_id=message_id, text=text)

    async def send_document(self, chat_id, document, caption=None, disable_notification=False):
        telegram_check(caption, limit=1024)
        self.sent.append({"chat_id": chat_id, "document": document, "caption": caption})


class FakeMessage:
    """Stand-in for aiogram Message: records everything sent or edited."""

    def __init__(self, text: str = ""):
        self.text = text
        self.document = None
        self.texts: list[str] = []
        self.markups: list = []      # the keyboard of each text, in step with self.texts
        self.documents: list = []    # captions of the files sent
        self.previews: list = []     # the picture drawn above each text, or None
        self.preview_options: list = []   # the LinkPreviewOptions object as sent
        self.chat = SimpleNamespace(id=1, type="private")
        self.message_id = 1
        self.deleted = False
        self.sender_chat = None
        self.date = datetime.now(UTC)
        self.reply_markup = None

    async def edit_text(self, text, reply_markup=None, **kwargs):
        telegram_check(text, kwargs.get("parse_mode", _DEFAULT), reply_markup)
        self._preview(kwargs.get("link_preview_options"))
        self.texts.append(text)
        self.markups.append(reply_markup)
        self.reply_markup = reply_markup

    async def answer(self, text, reply_markup=None, **kwargs):
        telegram_check(text, kwargs.get("parse_mode", _DEFAULT), reply_markup)
        self._preview(kwargs.get("link_preview_options"))
        self.texts.append(text)
        self.markups.append(reply_markup)
        self.reply_markup = reply_markup
        return self

    def _preview(self, options):
        self.preview_options.append(options)
        self.previews.append(options.url if options is not None else None)

    async def edit_reply_markup(self, reply_markup=None):
        telegram_check(markup=reply_markup)
        self.reply_markup = reply_markup

    async def delete(self):
        self.deleted = True
        return True

    async def answer_document(self, document, caption=None):
        telegram_check(caption, limit=1024)
        self.documents.append(caption)
        return self


class FakeCall:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeMessage()
        self.from_user = SimpleNamespace(id=777)
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        if text and len(text) > 200:
            _refuse(f"a tap answer is {len(text)} > 200 characters")
        self.answers.append(text)


class FakeState:
    def __init__(self, **data):
        self.state = None
        self.data = dict(data)

    async def clear(self):
        self.state, self.data = None, {}

    async def set_state(self, s):
        self.state = s

    async def get_state(self):
        """Like FSMContext: the state's name, not the State object."""
        return getattr(self.state, "state", self.state)

    async def update_data(self, **kw):
        self.data.update(kw)

    async def get_data(self):
        return dict(self.data)


def buttons(kb) -> list[str]:
    """Flat list of button labels of an inline keyboard."""
    return [b.text for row in kb.inline_keyboard for b in row]


@pytest.fixture(autouse=True)
def _fresh_module_state(monkeypatch):
    """Module-level caches, rate limits, schedules and asyncio locks start
    clean in every test: results must not depend on the order of the tests."""
    monkeypatch.setattr(status_page, "_cache", None)
    monkeypatch.setattr(status_page, "_lock", asyncio.Lock())
    status_page._badge_cache.clear()
    for window in (webhook._ip_hits, webhook._site_hits, webhook._bad_hits, webhook._notified,
                   webhook._held, webhook._warned):
        window.clear()
    monkeypatch.setattr(webhook, "_origins", None)
    webhook._widget_noted.clear()
    monkeypatch.setattr(runtime, "_bot_username", "")
    scheduler._next_avail_check.clear()
    scheduler._failed_jobs.clear()
    scheduler._slow_streak.clear()
    scheduler._fast_streak.clear()
    scheduler._ext_ok_noted.clear()
    notifier._released.clear()
    availability._recent.clear()
    availability._down_without_db.clear()
    notifier._unsent.clear()
    notifier._delivered_rows.clear()
    alert_handlers._busy.clear()
    common._denied.clear()
    site_handlers._live_running.clear()
    monkeypatch.setattr(scheduler, "_disk_level_unsaved", None)
    for state in (scheduler._in_flight, scheduler._busy, scheduler._sent_unrecorded):
        state.clear()
    monkeypatch.setattr(scheduler, "_queue", None)
    monkeypatch.setattr(scheduler, "_worker", None)
    health.reset()
    monkeypatch.setattr(scheduler, "_share_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_conn_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_write_lock", asyncio.Lock())
    monkeypatch.setattr(sitestatus, "_lock", asyncio.Lock())
    monkeypatch.setattr(netcheck, "_lock", asyncio.Lock())
    monkeypatch.setattr(automation, "_lock", asyncio.Lock())
    monkeypatch.setattr(helpers, "_lock", asyncio.Lock())
    netcheck.reset()
    scheduler._cannot_check_noted.clear()
    screenshots._inflight.clear()
    monkeypatch.setattr(links_checker, "_loop_state", None)
    monkeypatch.setattr(dns_checker, "_slots", None)
    monkeypatch.setattr(dns_checker, "_failures", {})
    monkeypatch.setattr(rdap, "_bootstrap_lock", asyncio.Lock())
    monkeypatch.setattr(rdap, "_bootstrap_failed_at", None)
    gsc.reset_token_cache()
    monkeypatch.setattr(integrations, "_gsc_key_problem", None)
    monkeypatch.setattr(updates, "_no_repo_said", False)


@pytest.fixture
async def db(tmp_path):
    """Fresh SQLite DB per test with all migrations applied and the
    service caches loaded."""
    await database.close_db()
    database.configure(str(tmp_path / "test.db"))
    await database.init_db()
    await secrets.load()
    await settings.load()
    await maintenance.load()
    await integrations.load()
    yield database
    await database.close_db()


@pytest.fixture
def bot(db, monkeypatch):
    fake = FakeBot()
    notifier.configure(fake)
    monkeypatch.setattr(runtime, "_chat_id", "777")
    monkeypatch.setattr(runtime, "_user_id", "777")
    return fake


@pytest.fixture
def cfg():
    """Override fields of the frozen config for one test."""
    saved = {}

    def _set(**overrides):
        for k, v in overrides.items():
            saved.setdefault(k, getattr(config, k))
            object.__setattr__(config, k, v)
    yield _set
    for k, v in saved.items():
        object.__setattr__(config, k, v)


@pytest.fixture(autouse=True)
async def _parse_child_stopped():
    """A test that parsed HTML leaves no child process behind."""
    yield
    await parse_worker.close()
