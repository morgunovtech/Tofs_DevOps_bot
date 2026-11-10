from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from services import runtime


def in_admin_chat(event: Message | CallbackQuery) -> bool:
    """The admin — in the admin chat or in their private chat with the bot.
    Screens with secrets must not be drawn into any other chat the admin
    happens to type in, and a message sent on behalf of a chat carries a
    stand-in user, never the admin."""
    user = event.from_user
    if user is None or user.is_bot or not runtime.is_admin(user.id):
        return False
    if isinstance(event, CallbackQuery):
        msg = event.message
        if msg is None:          # a button under an inline-mode message: no chat to check
            return False
    elif event.sender_chat is not None:
        return False
    else:
        msg = event
    return msg.chat.type == "private" or str(msg.chat.id) == runtime.admin_chat_id()


class AdminFilter(BaseFilter):
    """Only the admin may talk to the bot; applied once, on the admin router."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return in_admin_chat(event)
