"""Shared UI helpers: rendering a screen to either a message or a callback,
keyboards, the running progress bar, the error handler and the ⛔ router."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from db.database import get_all_sites, get_site
from handlers.filters import AdminFilter
from utils.text import clip
from utils.urls import is_http_url, site_label

logger = logging.getLogger(__name__)

DENIED = "⛔ Доступ только для администратора."
MAX_SITES = 20


def back_button(text: str = "← Главное меню", callback: str = "menu_main") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=callback)]])


def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])


def persistent_keyboard() -> ReplyKeyboardMarkup:
    """Always-visible bottom keyboard so the menu is one tap away."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Меню"), KeyboardButton(text="📊 Статус")]],
        resize_keyboard=True, is_persistent=True,
        input_field_placeholder="Тапни «📱 Меню» или введи команду")


async def render(target: Message | CallbackQuery, text: str,
                 kb: InlineKeyboardMarkup | None = None) -> None:
    """Show a screen: edit in place for a button tap, send for a command.
    A re-render with identical content ("message is not modified") is not
    an error — the screen is already right."""
    text = clip(text)
    editable = getattr(target, "message", None)   # CallbackQuery → its message
    if editable is not None:
        try:
            await editable.edit_text(text, reply_markup=kb)
        except TelegramBadRequest as e:
            if "not modified" not in str(e):
                raise
    else:
        await target.answer(text, reply_markup=kb)


async def sites_keyboard(prefix: str, *, icon: str = "🔍", manage: bool = False,
                         http_only: bool = False, back: str = "menu_main") -> InlineKeyboardMarkup:
    """One button per active site; callback_data carries the DB id."""
    sites = await get_all_sites()
    if http_only:
        sites = [s for s in sites if is_http_url(s["url"])]
    rows = [[InlineKeyboardButton(text=f"{icon} {site_label(s['url'])}",
                                  callback_data=f"{prefix}:{s['id']}")] for s in sites]
    if manage:
        row = [InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")]
        if sites:
            row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="site_del"))
        rows.append(row)
        rows.append([InlineKeyboardButton(text="📤 Экспорт", callback_data="site_export"),
                     InlineKeyboardButton(text="📥 Импорт", callback_data="site_import")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def ack(call: CallbackQuery, text: str | None = None, show_alert: bool = False) -> None:
    """Answer a button tap. A tap that reaches us after a restart is older
    than Telegram's answer window and gets 'query is too old' — the screen
    should still render, so that error is swallowed here."""
    try:
        await call.answer(text, show_alert=show_alert)
    except TelegramBadRequest as e:
        if "query is too old" not in str(e) and "query ID is invalid" not in str(e):
            raise


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
        if call and (call.data or "").startswith(("act:", "ack:")):
            # Action buttons live ON alert messages — never overwrite the
            # alert text with an error screen; reply separately instead.
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


# ── Non-admins ───────────────────────────────────────────────────────────────

deny_router = Router(name="deny")


@deny_router.message(F.text.startswith("/"), ~AdminFilter())
async def deny_command(message: Message):
    await message.answer(DENIED)


@deny_router.callback_query(~AdminFilter())
async def deny_callback(call: CallbackQuery):
    await ack(call, "⛔ Доступ запрещён", show_alert=True)
