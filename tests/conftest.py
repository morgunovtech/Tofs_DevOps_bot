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
})

from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

from config import config  # noqa: E402
from db import database  # noqa: E402
from services import integrations, maintenance, notifier, runtime, secrets, settings  # noqa: E402


class FakeBot:
    """Records what the bot would have sent; send_message returns a
    message-like object with a message_id, as aiogram's Bot does."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None, disable_notification=False):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup,
                          "silent": disable_notification})
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
        self.chat = SimpleNamespace(id=1)
        self.reply_markup = None

    async def edit_text(self, text, reply_markup=None):
        self.texts.append(text)
        self.reply_markup = reply_markup

    async def answer(self, text, reply_markup=None):
        self.texts.append(text)
        self.reply_markup = reply_markup
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
