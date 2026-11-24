"""The three buttons under the input field (handlers.common.bottom). A tap
works in any state: it aborts a pending input instead of being read as one —
this router goes first. The labels are compared in the chat language at
the time of the tap, not at import — and the Russian source is accepted
too: after «🗣 Язык бота» → English the keyboard still on screen is the
old one until the new is sent, and its taps must not fall through."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers import about
from handlers.common import ABOUT, HOME, ICONS, back_button
from handlers.menu import send_main_menu
from handlers.start import HELP
from utils.i18n import t

router = Router(name="bottom")


@router.message(F.text.func(lambda s: s in (HOME, t(HOME))))
async def on_home(message: Message, state: FSMContext):
    await state.clear()
    await send_main_menu(message)


@router.message(F.text.func(lambda s: s in (ABOUT, t(ABOUT))))
async def on_about(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(about.features_text(), reply_markup=about.features_keyboard())


@router.message(F.text.func(lambda s: s in (ICONS, t(ICONS))))
async def on_icons(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t(HELP), reply_markup=back_button())
