"""The real Dispatcher with the real root router, driven the way Telegram
drives it: raw updates in, Bot API calls out (captured by a mocked session).
Covers routing, AdminFilter on the admin router and the deny router."""

import ast
import pathlib
import time
from datetime import UTC, datetime

from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, GetFile, SendMessage
from aiogram.types import Chat, Message

import handlers
from db.database import save_incident
from services import helpers, runtime


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
    """AdminFilter guards `message` and `callback_query` of the admin
    router only: a handler for any other kind of update there (an
    edited_message, say) would be open to everyone, and Telegram would
    start sending that kind. A new update type needs
    admin.<type>.filter(AdminFilter()) in handlers/__init__.py."""
    def walk(router):
        yield router
        for sub in router.sub_routers:
            yield from walk(sub)
    used = {name for r in walk(handlers.admin) for name, obs in r.observers.items()
            if obs.handlers and name not in ("error", "update")}
    assert used <= {"message", "callback_query"}
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


# ── Nobody but the admin can do anything ─────────────────────────────────────

def _callback_samples() -> list[str]:
    """Every callback_data the code puts on a button or a handler listens
    for, read from the source; the variable parts of an f-string become «1»
    (the id of the first site). A new button is covered without anyone
    adding it here."""
    def text_of(node) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(v.value if isinstance(v, ast.Constant) else "1" for v in node.values)
        return None

    found: set[str | None] = set()
    for folder in ("handlers", "services", "reports"):
        for path in sorted(pathlib.Path(folder).glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if isinstance(node, ast.keyword) and node.arg == "callback_data":
                    found.add(text_of(node.value))
                elif (isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
                      and node.left.attr == "data" and len(node.comparators) == 1):
                    found.add(text_of(node.comparators[0]))                       # F.data == "x"
                elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == "startswith" and isinstance(node.func.value, ast.Attribute)
                      and node.func.value.attr == "data" and node.args):
                    prefix = text_of(node.args[0])                                # F.data.startswith("x")
                    found.add(prefix + "1" if prefix else None)
    found.discard(None)
    return sorted(s for s in found if s)


async def _all_rows(db) -> dict[str, list]:
    conn = await db.get_db()
    tables = [r[0] for r in await (await conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")).fetchall()]
    return {t: sorted((tuple(r) for r in await (await conn.execute(f'SELECT * FROM "{t}"')).fetchall()), key=repr)
            for t in tables}


def _tap(uid: int, data: str, update_id: int, chat_id: int, chat_type: str) -> dict:
    return {"update_id": update_id,
            "callback_query": {"id": str(update_id), "chat_instance": "ci", "data": data,
                               "from": {"id": uid, "is_bot": False, "first_name": "T"},
                               "message": {"message_id": 5, "date": 1700000000, "text": "menu",
                                           "chat": {"id": chat_id, "type": chat_type}}}}


async def test_no_button_does_anything_for_anyone_but_the_admin(bot, db, monkeypatch):
    """Every button of the bot pressed by a stranger in a private chat, by a
    stranger in another group, by a member of the admin group and by a
    helper: an answer to the tap and nothing else — no screen, no message,
    no change in the database."""
    sid = await db.activate_or_create_site("https://ex.test")
    assert sid == 1                                     # «1» in the samples is this site
    await save_incident(sid, "availability", "Site down", "critical")
    assert await helpers.accept(await helpers.create_invite(), 555, "Аня") == "ok"
    samples = _callback_samples()
    assert len(samples) > 100, samples                  # the scan still finds the buttons
    before = await _all_rows(db)
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    roles = [  # (admin chat, admin user, who presses, in which chat, chat type)
        ("777", "777", 999, 999, "private"),
        ("777", "777", 999, -100500, "supergroup"),
        ("-100777", "777", 999, -100777, "supergroup"),
        ("777", "777", 555, 555, "private"),
    ]
    n = 1000
    for admin_chat, admin_user, who, chat_id, chat_type in roles:
        monkeypatch.setattr(runtime, "_chat_id", admin_chat)
        monkeypatch.setattr(runtime, "_user_id", admin_user)
        for data in samples:
            n += 1
            session.calls.clear()
            await DP.feed_raw_update(tg, _tap(who, data, n, chat_id, chat_type))
            extra = [type(m).__name__ for m in session.calls if not isinstance(m, AnswerCallbackQuery)]
            assert extra == [], (who, chat_id, data, extra)
    assert await _all_rows(db) == before
    assert bot.sent == [] and bot.edited == []
    await tg.session.close()


async def test_a_file_from_a_stranger_is_not_even_downloaded(bot, db, monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", "777")
    monkeypatch.setattr(runtime, "_user_id", "777")
    await db.activate_or_create_site("https://ex.test")
    before = await _all_rows(db)
    session = MockedSession()
    tg = Bot(token="123456:TEST", session=session)
    await DP.feed_raw_update(tg, {"update_id": 1, "message": {
        "message_id": 1, "date": 1700000000, "chat": {"id": 999, "type": "private"},
        "from": {"id": 999, "is_bot": False, "first_name": "T"},
        "document": {"file_id": "f", "file_unique_id": "u", "file_name": "bot.db", "file_size": 10}}})
    assert not [m for m in session.calls if isinstance(m, GetFile)]
    assert await _all_rows(db) == before
    await tg.session.close()
