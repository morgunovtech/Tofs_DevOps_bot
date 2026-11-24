"""/mute, /unmute and the 🔕 inline menu."""

from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.common import ack, render
from services import notifier
from utils.clock import to_local
from utils.i18n import t
from utils.parse import parse_duration

router = Router(name="mute")

_USAGE = ("Использование: /mute &lt;время&gt;\n"
          "Примеры: /mute 1h, /mute 8h, /mute 30m, /mute 1d\nПо умолчанию: 8h")


MAX_MUTE = timedelta(days=7)


async def _mute(arg: str) -> str | None:
    duration = parse_duration(arg)
    if duration and duration > MAX_MUTE:
        return t("Максимум — 7 дней: дольше тишина уже не пауза, а выключенный мониторинг.")
    if not duration:
        return None
    deadline = await notifier.set_mute(duration)
    return (t("🔕 Молчу до {time}.\n{what_rings_through_mute}\nСнова писать обо всём: /unmute").format(time=to_local(deadline).strftime('%d.%m %H:%M'), what_rings_through_mute=notifier.what_rings_through_mute()))


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    parts = (message.text or "").split(maxsplit=1)
    text = await _mute(parts[1] if len(parts) > 1 else "8h")
    await message.answer(text or t(_USAGE))


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    await notifier.set_mute(None)
    await message.answer(t("🔔 Снова пишу обо всём."))


def _mute_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("1 час"), callback_data="mute:1h"),
         InlineKeyboardButton(text=t("4 часа"), callback_data="mute:4h"),
         InlineKeyboardButton(text=t("8 часов"), callback_data="mute:8h")],
        [InlineKeyboardButton(text=t("1 день"), callback_data="mute:1d"),
         InlineKeyboardButton(text=t("3 дня"), callback_data="mute:3d"),
         InlineKeyboardButton(text=t("7 дней"), callback_data="mute:7d")],
        [InlineKeyboardButton(text=t("🔔 Снять тишину"), callback_data="mute:off")],
        _back()])


@router.callback_query(F.data == "menu_mute")
async def cb_mute_menu(call: CallbackQuery):
    await ack(call)
    deadline = await notifier.mute_until()
    status = (t("🔕 Сейчас приглушено до {time}").format(time=to_local(deadline).strftime('%d.%m %H:%M')) + "\n\n"
              if deadline else "")
    await render(call, t("{status}На сколько замолчать?\n{what_rings_through_mute}").format(status=status, what_rings_through_mute=notifier.what_rings_through_mute()), _mute_menu())


def _back() -> list[InlineKeyboardButton]:
    """The way back: the silence screen this one belongs to, and home — the
    main screen has its own «🔕 Помолчать…» button."""
    return [InlineKeyboardButton(text=t("← Тишина"), callback_data="menu_quiet"),
            InlineKeyboardButton(text=t("🏠 Главная"), callback_data="menu_main")]


@router.callback_query(F.data.startswith("mute:"))
async def cb_mute_pick(call: CallbackQuery):
    """After a pause: the way to lift it and the way back; after lifting — the way back."""
    await ack(call)
    arg = call.data.split(":", 1)[1]
    if arg == "off":
        await notifier.set_mute(None)
        await render(call, t("🔔 Снова пишу обо всём."), InlineKeyboardMarkup(inline_keyboard=[_back()]))
        return
    text = await _mute(arg)
    if not text:
        await render(call, t("Не понял длительность."), InlineKeyboardMarkup(inline_keyboard=[_back()]))
        return
    await render(call, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("🔔 Снять тишину"), callback_data="mute:off")], _back()]))
