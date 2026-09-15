"""Who the admin is. Env wins; otherwise the first user to /start claims the
bot and the claim is persisted in bot_state so it survives restarts."""

import logging

from config import config
from db.database import get_state, set_state

logger = logging.getLogger(__name__)

_chat_id: str | None = config.admin_chat_id or None
_user_id: str | None = config.admin_user_id or None
_bot_username: str = ""


def set_bot_username(username: str | None):
    global _bot_username
    _bot_username = username or ""


def bot_username() -> str:
    return _bot_username


async def load():
    global _chat_id, _user_id
    if _chat_id:
        return
    saved = await get_state("admin_chat_id")
    if saved:
        _chat_id = saved
        _user_id = await get_state("admin_user_id") or saved
        logger.info("Admin restored from DB.")
    else:
        logger.warning("No admin configured — the first user to /start becomes admin.")


def admin_chat_id() -> str | None:
    return _chat_id


def has_admin() -> bool:
    return bool(_chat_id)


def is_admin(user_id: int | str | None) -> bool:
    """admin_chat_id fallback only works for private chats (user id == chat
    id); set TELEGRAM_ADMIN_USER_ID when the admin chat is a group."""
    if user_id is None or not _chat_id:
        return False
    return str(user_id) == (_user_id or _chat_id)


async def claim(chat_id: int, user_id: int) -> bool:
    """First-run: the first /start becomes admin. False if already claimed."""
    global _chat_id, _user_id
    if _chat_id:
        return False
    _chat_id, _user_id = str(chat_id), str(user_id)
    await set_state("admin_chat_id", _chat_id)
    await set_state("admin_user_id", _user_id)
    logger.info("Admin claimed by user %s in chat %s", user_id, chat_id)
    return True
