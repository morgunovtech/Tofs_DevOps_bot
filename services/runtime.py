"""Who the admin is, and whether that — with everything else — survives a
restart. Env wins; otherwise the first user to /start claims the bot and
the claim is persisted in bot_state so it survives restarts."""

import logging
import os
import time

from config import config
from db.database import count_sites_total, get_state, set_state

logger = logging.getLogger(__name__)

# Said at the claim, at every start and in the diagnostics: without a volume
# Railway wipes the database on each deploy, and weekly auto-updates mean
# the bot forgets the sites — and its owner — every week, without a word.
NO_VOLUME = ("🟠 Моя база не на постоянном диске Railway: при каждом обновлении я забываю сайты, "
             "настройки и тебя как хозяина. Подключи диск: в Railway правый клик по сервису → "
             "Attach Volume → Mount Path <code>/app/data</code>, и переменная "
             "<code>DB_PATH=/app/data/bot.db</code>.")


def db_on_persistent_disk() -> bool | None:
    """None off Railway — on a VPS the docker volume is the operator's call.
    On Railway: the database file must sit on the attached volume (Railway
    names its mount in RAILWAY_VOLUME_MOUNT_PATH) or on any other mount."""
    if not config.on_railway:
        return None
    path = os.path.realpath(config.db_path)
    mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip().rstrip("/")
    if mount and path.startswith(mount + "/"):
        return True
    d = os.path.dirname(path)
    while d and d != "/":
        if os.path.ismount(d):
            return True
        d = os.path.dirname(d)
    return False

_chat_id: str | None = config.admin_chat_id or config.admin_user_id or None
_user_id: str | None = config.admin_user_id or None
# A /start older than this process was queued by Telegram while the bot was
# down; it must not take a free seat ahead of the owner (handlers/start.py).
STARTED_AT = time.time()
_bot_username: str = ""


def set_bot_username(username: str | None):
    global _bot_username
    _bot_username = username or ""


def bot_username() -> str:
    return _bot_username


# "<old id>:<new id>": Telegram turned the admin group into a supergroup.
MOVED_KEY = "admin_chat_moved"


async def load():
    await _load_seat()
    await follow_moved_chat()


async def follow_moved_chat():
    """The admin group became a supergroup earlier: use the new id even while
    the old one is still in TELEGRAM_ADMIN_CHAT_ID (one hop is all there is)."""
    global _chat_id
    old, _, new = (await get_state(MOVED_KEY) or "").partition(":")
    if _chat_id and new and old == _chat_id:
        logger.warning("The admin group %s is supergroup %s now — update TELEGRAM_ADMIN_CHAT_ID", old, new)
        _chat_id = new


async def move_admin_chat(new_id: int | str) -> bool:
    """Follow the admin group into its supergroup. True when the seat moved."""
    global _chat_id
    old, new = _chat_id, str(new_id)
    if not old or new == old:
        return False
    _chat_id = new
    await set_state(MOVED_KEY, f"{old}:{new}")
    if await get_state("admin_chat_id") == old:
        await set_state("admin_chat_id", new)
    logger.warning("The admin group %s became supergroup %s — following it", old, new)
    return True


def env_chat_outdated() -> bool:
    """TELEGRAM_ADMIN_CHAT_ID still names the group we have moved away from."""
    return bool(config.admin_chat_id) and config.admin_chat_id != _chat_id


async def _load_seat():
    global _chat_id, _user_id
    if config.admin_chat_id or config.admin_user_id:
        # Env wins: a stranger's /start, a lost database or a restored copy
        # cannot hand the bot over.
        _chat_id = config.admin_chat_id or config.admin_user_id
        _user_id = config.admin_user_id or None
        if _chat_id.startswith("-") and not _user_id:
            logger.error("TELEGRAM_ADMIN_CHAT_ID is a group, but TELEGRAM_ADMIN_USER_ID is empty — "
                         "nobody can control the bot. Set your numeric user id (@userinfobot).")
        return
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


def nobody_steers() -> bool:
    """A group chat from env without TELEGRAM_ADMIN_USER_ID: is_admin() is
    false for everyone, the owner included."""
    return bool(_chat_id) and _chat_id.startswith("-") and not _user_id


async def warn_if_seat_free():
    """Every 30 min while nobody holds the seat but sites are watched: the bot
    has no one to tell, so it says it where the operator looks — the log.
    Alerts are not redirected: after a claim they would go to the claimer."""
    if not _chat_id and await count_sites_total():
        logger.error("Nobody holds the admin seat while sites are watched — alerts are dropped. "
                     "Send /start to the bot in a private chat or set TELEGRAM_ADMIN_USER_ID.")


def has_admin() -> bool:
    return bool(_chat_id)


def is_admin(user_id: int | str | None) -> bool:
    """admin_chat_id fallback only works for private chats (user id == chat
    id); set TELEGRAM_ADMIN_USER_ID when the admin chat is a group."""
    if user_id is None or not _chat_id:
        return False
    return str(user_id) == (_user_id or _chat_id)


async def persist():
    """Write the current admin seat back: a restored database may lack it,
    and after a restart the seat would be free for anyone's /start."""
    if _chat_id:
        await set_state("admin_chat_id", _chat_id)
        await set_state("admin_user_id", _user_id or _chat_id)


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
