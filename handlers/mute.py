"""/mute, /unmute and the 🔕 inline menu."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.common import ack, back_button, render
from services import notifier
from utils.clock import to_local
from utils.parse import parse_duration

router = Router(name="mute")

_USAGE = ("Использование: /mute &lt;время&gt;\n"
          "Примеры: /mute 1h, /mute 8h, /mute 30m, /mute 1d\nПо умолчанию: 8h")


async def _mute(arg: str) -> str | None:
    duration = parse_duration(arg)
    if not duration:
        return None
    deadline = await notifier.set_mute(duration)
    return (f"🔕 Алерты приглушены до {to_local(deadline).strftime('%d.%m %H:%M')}.\n"
            f"Критические алерты (сайт лежит) всё равно придут.\nСнять: /unmute")


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    text = await _mute(parts[1] if len(parts) > 1 else "8h")
    await message.answer(text or _USAGE)


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    await notifier.set_mute(None)
    await message.answer("🔔 Алерты снова включены.")


def _mute_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1 час", callback_data="mute:1h"),
         InlineKeyboardButton(text="4 часа", callback_data="mute:4h"),
         InlineKeyboardButton(text="8 часов", callback_data="mute:8h")],
        [InlineKeyboardButton(text="1 день", callback_data="mute:1d"),
         InlineKeyboardButton(text="🔔 Снять тишину", callback_data="mute:off")],
        [InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")]])


@router.callback_query(F.data == "menu_mute")
async def cb_mute_menu(call: CallbackQuery):
    await ack(call)
    deadline = await notifier.mute_until()
    status = (f"🔕 Сейчас приглушено до {to_local(deadline).strftime('%d.%m %H:%M')}\n\n"
              if deadline else "")
    await render(call, f"{status}На сколько приглушить алерты?\n"
                       "(критические — «сайт лежит» — всё равно придут)", _mute_menu())


@router.callback_query(F.data.startswith("mute:"))
async def cb_mute_pick(call: CallbackQuery):
    await ack(call)
    arg = call.data.split(":", 1)[1]
    if arg == "off":
        await notifier.set_mute(None)
        await render(call, "🔔 Алерты снова включены.", back_button())
        return
    await render(call, (await _mute(arg)) or "Не понял длительность.", back_button())
