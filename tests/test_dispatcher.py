"""The real Dispatcher with the real root router, driven the way Telegram
drives it: raw updates in, Bot API calls out (captured by a mocked session).
Covers routing, AdminFilter on the admin router and the deny router."""

import time
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, Message

import handlers
from services import runtime


class MockedSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=len(self.calls), date=datetime.now(UTC),
                           chat=Chat(id=int(method.chat_id), type="private"), text=method.text)
        return True

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        yield b""


DP = Dispatcher()
DP.include_router(handlers.router)


def _message(uid: int, text: str, update_id: int) -> dict:
    return {"update_id": update_id,
            "message": {"message_id": update_id, "date": 1700000000,
                        "chat": {"id": uid, "type": "private"},
                        "from": {"id": uid, "is_bot": False, "first_name": "T"}, "text": text}}


def _callback(uid: int, data: str, update_id: int) -> dict:
    return {"update_id": update_id,
            "callback_query": {"id": str(update_id), "chat_instance": "ci", "data": data,
                               "from": {"id": uid, "is_bot": False, "first_name": "T"},
                               "message": {"message_id": 5, "date": 1700000000, "text": "menu",
                                           "chat": {"id": uid, "type": "private"}}}}


async def test_admin_is_routed_and_strangers_are_denied(bot, db, monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", "777")
    monkeypatch.setattr(runtime, "_user_id", "777")
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)

    await DP.feed_raw_update(tg, _message(777, "/start", 1))
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert any("Привет" in m.text for m in sent)
    assert any("Пока ни за чем не слежу" in m.text for m in sent)   # the main menu came right after

    session.calls.clear()
    await DP.feed_raw_update(tg, _message(999, "/start", 2))
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert sent and all("⛔" in m.text for m in sent)

    session.calls.clear()
    await DP.feed_raw_update(tg, _callback(999, "menu_settings", 3))
    assert any(isinstance(m, AnswerCallbackQuery) and m.show_alert for m in session.calls)
    assert not [m for m in session.calls if isinstance(m, (SendMessage, EditMessageText))]

    session.calls.clear()
    await DP.feed_raw_update(tg, _callback(777, "menu_settings", 4))
    edits = [m for m in session.calls if isinstance(m, EditMessageText)]
    assert edits and "⚙️ Настройки" in edits[-1].text

    session.calls.clear()
    await DP.feed_raw_update(tg, _message(777, "/menu", 5))
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert sent and "Пока ни за чем не слежу" in sent[-1].text
    await tg.session.close()


# ── The admin seat ───────────────────────────────────────────────────────────

GROUP = {"id": -100, "type": "supergroup", "title": "Work"}


def _raw(uid: int, text: str, update_id: int, chat: dict | None = None, date: int | None = None,
         **extra) -> dict:
    """A message update; private chat with the sender and «now» unless told otherwise."""
    return {"update_id": update_id,
            "message": {"message_id": update_id, "date": date or int(time.time()),
                        "chat": chat or {"id": uid, "type": "private"},
                        "from": {"id": uid, "is_bot": False, "first_name": "T"}, "text": text, **extra}}


def _sent(session) -> list[SendMessage]:
    return [m for m in session.calls if isinstance(m, SendMessage)]


async def test_the_free_seat_is_taken_only_in_a_private_chat_and_only_now(db, monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", None)
    monkeypatch.setattr(runtime, "_user_id", None)
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    now = int(time.time())

    await DP.feed_raw_update(tg, _raw(555, "/start", 1, chat=GROUP))                 # a group member
    assert not runtime.has_admin() and "личном чате" in _sent(session)[-1].text
    await DP.feed_raw_update(tg, _raw(1087968824, "/start", 2, chat=GROUP, sender_chat=GROUP))
    assert not runtime.has_admin()                                                   # «on behalf of the group»
    monkeypatch.setattr(runtime, "STARTED_AT", now)
    await DP.feed_raw_update(tg, _raw(999, "/start", 3, date=now - 120))             # waited in the queue
    assert not runtime.has_admin() and "ещё раз" in _sent(session)[-1].text

    await DP.feed_raw_update(tg, _raw(777, "/start", 4, date=now))                   # the owner, now
    assert runtime.is_admin(777) and any("🔑" in m.text for m in _sent(session))
    await tg.session.close()


async def test_env_user_id_owns_the_seat_whatever_the_database_says(bot, db, cfg):
    await db.set_state("admin_chat_id", "222")      # a stranger's claim — or a restored copy's
    await db.set_state("admin_user_id", "222")
    cfg(admin_user_id="111", admin_chat_id="")
    await runtime.load()
    assert runtime.is_admin(111) and not runtime.is_admin(222) and runtime.admin_chat_id() == "111"

    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    await DP.feed_raw_update(tg, _raw(222, "/start", 1))
    assert _sent(session) and all("⛔" in m.text for m in _sent(session))
    assert runtime.is_admin(111) and not runtime.is_admin(222)
    await tg.session.close()


async def test_admin_screens_are_not_drawn_into_a_foreign_group(bot, db):
    session = MockedSession()                      # the admin is 777, in a private chat
    tg = Bot(token="123456:TEST", session=session)
    await DP.feed_raw_update(tg, _raw(777, "/menu", 1, chat=GROUP))
    await DP.feed_raw_update(tg, _raw(777, "/start", 2, chat=GROUP))
    texts = [m.text for m in _sent(session)]
    assert len(texts) == 2 and all("личный чат" in t for t in texts)

    session.calls.clear()
    tap = _callback(777, "menu_settings", 3)
    tap["callback_query"]["message"]["chat"] = GROUP
    await DP.feed_raw_update(tg, tap)
    assert not [m for m in session.calls if isinstance(m, (SendMessage, EditMessageText))]
    await tg.session.close()


async def test_a_group_without_an_owner_id_says_what_to_set(bot, db, cfg):
    cfg(admin_chat_id="-100500", admin_user_id="")
    await runtime.load()
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    chat = {"id": -100500, "type": "supergroup", "title": "W"}
    for n, cmd in enumerate(("/start", "/help", "/menu"), start=1):
        await DP.feed_raw_update(tg, _raw(777, cmd, n, chat=chat))
    texts = [m.text for m in _sent(session)]
    assert len(texts) == 3 and all("TELEGRAM_ADMIN_USER_ID" in t for t in texts)
    await tg.session.close()


def test_admin_router_covers_every_update_type():
    # A new update type needs admin.<type>.filter(AdminFilter()) in handlers/__init__.py.
    assert DP.resolve_used_update_types() == ["callback_query", "message"]


async def test_the_upgrade_service_message_moves_the_admin_group(bot, db, monkeypatch):
    """Telegram's own note «this group is now a supergroup» moves the seat at
    once — commands from the new chat count before the next alert."""
    monkeypatch.setattr(runtime, "_chat_id", "-4001")
    monkeypatch.setattr(runtime, "_user_id", "777")
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    await DP.feed_raw_update(tg, {"update_id": 1, "message": {
        "message_id": 1, "date": 1700000000, "migrate_from_chat_id": -4001,
        "chat": {"id": -1001234567890, "type": "supergroup", "title": "Work"}}})
    assert runtime.admin_chat_id() == "-1001234567890"
    await DP.feed_raw_update(tg, {"update_id": 2, "message": {      # someone else's group: ignored
        "message_id": 2, "date": 1700000000, "migrate_from_chat_id": -999,
        "chat": {"id": -1009999, "type": "supergroup", "title": "Other"}}})
    assert runtime.admin_chat_id() == "-1001234567890"
    await tg.session.close()
