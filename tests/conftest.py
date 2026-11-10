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
    "QUIET_HOURS": "",
    "DEPENDENCY_WATCH": "0",
    "TIMEZONE": "Europe/Moscow",
    "PUBLIC_BASE_URL": "https://bot.example.test",
    "STATUS_PAGE": "0",
    "STATUS_PAGE_SLUG": "",
    "SITES": "",
    "HEARTBEAT_JOBS": "",
    "TRUST_PROXY": "0",
    # A developer's shell often exports a GitHub token for gh/git.
    "GITHUB_TOKEN": "",
    "GITHUB_OWNER": "",
})

import asyncio  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from config import config  # noqa: E402
from db import database  # noqa: E402
from monitors import availability  # noqa: E402
from reports import scheduler  # noqa: E402
from services import (  # noqa: E402
    health,
    integrations,
    maintenance,
    notifier,
    runtime,
    secrets,
    settings,
    sitestatus,
)
from web import status_page, webhook  # noqa: E402


class FakeBot:
    """Records what the bot would have sent; send_message returns a
    message-like object with a message_id, as aiogram's Bot does."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.fail = False
        self.fail_with: list[Exception] = []    # raised one per call, in order, before `fail`

    async def send_message(self, chat_id, text, reply_markup=None, disable_notification=False,
                           parse_mode="HTML", request_timeout=None):
        if self.fail_with:
            raise self.fail_with.pop(0)
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup,
                          "silent": disable_notification, "parse_mode": parse_mode})
        return SimpleNamespace(message_id=len(self.sent), text=text)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        self.edited.append({"message_id": message_id, "text": text, "reply_markup": reply_markup})
        return SimpleNamespace(message_id=message_id, text=text)

    async def send_document(self, chat_id, document, caption=None, disable_notification=False):
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
        self.sender_chat = None
        self.date = datetime.now(UTC)
        self.reply_markup = None

    async def edit_text(self, text, reply_markup=None, **kwargs):
        self._preview(kwargs.get("link_preview_options"))
        self.texts.append(text)
        self.markups.append(reply_markup)
        self.reply_markup = reply_markup

    async def answer(self, text, reply_markup=None, **kwargs):
        self._preview(kwargs.get("link_preview_options"))
        self.texts.append(text)
        self.markups.append(reply_markup)
        self.reply_markup = reply_markup
        return self

    def _preview(self, options):
        self.preview_options.append(options)
        self.previews.append(options.url if options is not None else None)

    async def edit_reply_markup(self, reply_markup=None):
        self.reply_markup = reply_markup

    async def answer_document(self, document, caption=None):
        self.documents.append(caption)
        return self


class FakeCall:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeMessage()
        self.from_user = SimpleNamespace(id=777)
        self.answers: list = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)


class FakeState:
    def __init__(self, **data):
        self.state = None
        self.data = dict(data)

    async def clear(self):
        self.state, self.data = None, {}

    async def set_state(self, s):
        self.state = s

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
    status_page._badge_cache.clear()
    webhook._ip_hits.clear()
    webhook._global_hits.clear()
    monkeypatch.setattr(runtime, "_bot_username", "")
    scheduler._next_avail_check.clear()
    scheduler._slow_streak.clear()
    scheduler._ext_ok_noted.clear()
    notifier._released.clear()
    availability._recent.clear()
    availability._down_without_db.clear()
    notifier._unsent.clear()
    notifier._delivered_rows.clear()
    monkeypatch.setattr(scheduler, "_disk_level_unsaved", None)
    health.reset()
    monkeypatch.setattr(database, "_conn_lock", asyncio.Lock())
    monkeypatch.setattr(database, "_write_lock", asyncio.Lock())
    monkeypatch.setattr(sitestatus, "_lock", asyncio.Lock())


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
