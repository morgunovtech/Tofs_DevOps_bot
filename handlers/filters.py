from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from services import runtime


class AdminFilter(BaseFilter):
    """Only the admin may talk to the bot; applied once, on the admin router."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return runtime.is_admin(event.from_user.id if event.from_user else None)
