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

import pytest  # noqa: E402

from config import config  # noqa: E402
from db import database  # noqa: E402
from services import maintenance, notifier, runtime, secrets, settings  # noqa: E402


class FakeBot:
    """Records what the bot would have sent."""

    def __init__(self):
        self.sent: list[dict] = []
        self.fail = False

    async def send_message(self, chat_id, text, reply_markup=None, disable_notification=False):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup,
                          "silent": disable_notification})

    async def send_document(self, chat_id, document, caption=None, disable_notification=False):
        self.sent.append({"chat_id": chat_id, "document": document, "caption": caption})


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
