"""Shared UI helpers: rendering a screen to either a message or a callback,
keyboards, the running progress bar, the error handler and the ⛔ router."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

from db.database import get_site
from handlers.filters import AdminFilter
from services import helpers, runtime
from utils.text import clip

logger = logging.getLogger(__name__)

DENIED = "⛔ Доступ только для администратора."
ELSEWHERE = ("Здесь я не управляюсь: пиши мне в наш личный чат "
             "(или в группу из TELEGRAM_ADMIN_CHAT_ID).")
NO_OWNER = ("Мной сейчас никто не может управлять: в .env задан TELEGRAM_ADMIN_CHAT_ID группы, "
            "но не задан TELEGRAM_ADMIN_USER_ID. Впиши туда свой числовой id (его покажет @userinfobot) "
            "и перезапусти бота.")
# The heartbeat address is a password: whoever has it can mark in for any task.
HEARTBEAT_SECRET_NOTE = ("🔐 Этот адрес — как пароль: кто его знает, может выходить на связь вместо "
                         "задачи. Не публикуй его и не клади в файлы репозитория.")
NOT_DELETED = ("⚠️ Не смог удалить твоё сообщение с ключом — удали его сам. "
               "В группе для этого мне нужно право администратора «Удаление сообщений».")


async def forget_secret(message: Message) -> bool:
    """A token, key, password or hook URL must not stay in the chat history.
    Called first, before the input is checked: a mistyped secret is still a
    secret. When Telegram refuses (a group where the bot is not an admin),
    the owner is told — silence would leave the key on view."""
    try:
        await message.delete()
        return True
    except TelegramAPIError as e:
        logger.warning("Could not delete a message with a secret: %s", type(e).__name__)
        try:
            await message.answer(NOT_DELETED)
        except TelegramAPIError:
            pass
        return False

# The bot's three doors, always under the input field (no ☰ menu).
HOME, ABOUT, ICONS = "🏠 Главная", "📖 Что я умею", "❓ Значки"
BOTTOM = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=HOME), KeyboardButton(text=ABOUT), KeyboardButton(text=ICONS)]],
    resize_keyboard=True, is_persistent=True)
MAX_SITES = 20


def back_button(text: str = "← Главное меню", callback: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])


async def render(target: Message | CallbackQuery, text: str,
                 kb: InlineKeyboardMarkup | None = None, picture: str | None = None) -> None:
    """Show a screen: edit in place for a button tap, send for a command.
    A re-render with identical content ("message is not modified") is not
    an error — the screen is already right. picture: an image address that
    Telegram draws above the text (link previews are off everywhere else)."""
    text = clip(text)
    # is_disabled=False must be said: aiogram fills an unset field from the
    # bot-wide default, and that default switches every preview off.
    extra = {"link_preview_options": LinkPreviewOptions(is_disabled=False, url=picture, prefer_large_media=True,
                                                         show_above_text=True)} if picture else {}
    editable = getattr(target, "message", None)   # CallbackQuery → its message
    if editable is not None and not hasattr(editable, "edit_text"):
        # InaccessibleMessage (too old to edit): answer below instead.
        await editable.answer(text, reply_markup=kb, **extra)
    elif editable is not None:
        try:
            await editable.edit_text(text, reply_markup=kb, **extra)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise
    else:
        await target.answer(text, reply_markup=kb, **extra)


STALE_TAP = ("⌛ Это нажатие пришло, пока я был выключен или перезапускался, — ничего не сделал. "
             "Если это ещё нужно, нажми кнопку снова.")


async def ack(call: CallbackQuery, text: str | None = None, show_alert: bool = False) -> bool:
    """Answer a button tap. False when Telegram says the tap is too old to
    answer ('query is too old') — it was pressed while the bot was down and
    came from the queue after a restart. A screen may still render then, but
    a handler that changes something (a rebuild, a purge, deleting, a new
    secret) must call ack FIRST and do nothing on False (reply STALE_TAP)."""
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            raise
        return False
    return True


def cb_args(call: CallbackQuery, n: int) -> list[str] | None:
    """Split 'prefix:a:b' into exactly n parts after the prefix, else None."""
    parts = (call.data or "").split(":")
    return parts[1:] if len(parts) == n + 1 else None


async def site_by_cb(arg: str | None) -> dict | None:
    """Resolve a callback site id back to an active site row."""
    if not arg or not arg.isdigit():
        return None
    site = await get_site(int(arg))
    return site if site and site["active"] else None


async def with_running_bar(message: Message, base_text: str, coro, tick: float = 4.0):
    """Await a long coroutine while a runner segment (▰▱▱ → ▱▰▱ → ▱▱▰)
    metronomes on the status message. The task is cancelled if the
    animation itself fails, so nothing is left running as an orphan."""
    task = asyncio.ensure_future(coro)
    frames = ("▰▱▱", "▱▰▱", "▱▱▰", "▱▰▱")
    i = 0
    try:
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=tick)
            except TimeoutError:
                if task.done():   # a TimeoutError from INSIDE the task
                    return task.result()
                i += 1
                try:
                    await message.edit_text(f"{frames[i % len(frames)]} {base_text}")
                except TelegramBadRequest:
                    pass
    except BaseException:
        if not task.done():
            task.cancel()
        raise


# ── Errors ───────────────────────────────────────────────────────────────────
# Registered on the ROOT router (handlers/__init__.py): aiogram looks for
# error handlers in the failing handler's router and its parents, never in
# sibling routers.

async def on_handler_error(event: ErrorEvent):
    """Catch-all so a failed check (lookup timeout etc.) doesn't leave the
    menu stuck on a '▱▱▱ …' message with no keyboard and no way back."""
    update = event.update
    call = update.callback_query if update else None
    if "message is not modified" in str(event.exception):
        if call:
            try:
                await call.answer()
            except Exception:
                pass
        return True
    logger.exception("Handler error: %s", event.exception)
    try:
        data = (call.data or "") if call else ""
        if call and (data.startswith(("act:", "hb_how:", "hb_rm:")) or data.endswith(":a")):
            # Buttons that live ON alert messages and digests — never
            # overwrite that text with an error screen; reply separately.
            await call.message.answer("❌ Действие не удалось. Попробуй ещё раз чуть позже.")
        elif call and call.message:
            await call.message.edit_text(
                "❌ Что-то пошло не так во время проверки.\nПопробуй ещё раз чуть позже.",
                reply_markup=back_button())
        elif update and update.message:
            await update.message.answer("❌ Что-то пошло не так. Попробуй ещё раз чуть позже.")
    except Exception:
        if call:  # message too old to edit (48h+) — at least acknowledge the tap
            try:
                await call.answer("Сообщение устарело — открой /menu", show_alert=True)
            except Exception:
                pass
    return True


# ── Stale buttons ────────────────────────────────────────────────────────────

stale_router = Router(name="stale")


@stale_router.callback_query()
async def stale_callback(call: CallbackQuery):
    """A button no handler knows any more (an old alert, a removed feature):
    say so instead of leaving the spinner hanging."""
    await ack(call, "Кнопка устарела — открой /menu", show_alert=True)


# ── Non-admins ───────────────────────────────────────────────────────────────

deny_router = Router(name="deny")


def _admin_elsewhere(event: Message | CallbackQuery) -> bool:
    """The real admin, but in a chat that is not ours."""
    user = event.from_user
    on_behalf = not isinstance(event, CallbackQuery) and event.sender_chat is not None
    return bool(user and not user.is_bot and not on_behalf and runtime.is_admin(user.id))


def refusal(message: Message) -> str:
    """Why this message gets no screen: the owner is misconfigured, the admin
    is in a foreign chat, or it is simply not the admin."""
    if runtime.nobody_steers() and str(message.chat.id) == runtime.admin_chat_id():
        return NO_OWNER
    return ELSEWHERE if _admin_elsewhere(message) else DENIED


@deny_router.message(F.text.startswith("/"), ~AdminFilter())
async def deny_command(message: Message):
    if await helpers.is_helper(message.chat.id):
        await message.answer("👥 Ты помощник: я присылаю тебе только тревоги 🔴 о сайтах, управляет ботом "
                             "владелец. Перестать — /stop.")
        return
    await message.answer(refusal(message))


@deny_router.callback_query(~AdminFilter())
async def deny_callback(call: CallbackQuery):
    await ack(call, ELSEWHERE if _admin_elsewhere(call) else "⛔ Доступ запрещён", show_alert=True)
