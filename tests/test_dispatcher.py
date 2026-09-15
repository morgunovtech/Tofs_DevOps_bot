"""The real Dispatcher with the real root router, driven the way Telegram
drives it: raw updates in, Bot API calls out (captured by a mocked session).
Covers routing, AdminFilter on the admin router and the deny router."""

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
    assert any("Сайтов пока нет" in m.text for m in sent)          # the main menu came right after

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
    await DP.feed_raw_update(tg, _message(777, "📱 Меню", 5))
    sent = [m for m in session.calls if isinstance(m, SendMessage)]
    assert sent and "Сайтов пока нет" in sent[-1].text
    await tg.session.close()
