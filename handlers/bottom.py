"""The three buttons under the input field (handlers.common.bottom). A tap
works in any state: it aborts a pending input instead of being read as one —
this router goes first. The labels are compared in the chat language at
the time of the tap, not at import."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers import about
from handlers.common import ABOUT, HOME, ICONS, back_button
from handlers.menu import send_main_menu
from handlers.start import HELP
from utils.i18n import t

router = Router(name="bottom")


@router.message(F.text.func(lambda s: s == t(HOME)))
async def on_home(message: Message, state: FSMContext):
    await state.clear()
    await send_main_menu(message)


@router.message(F.text.func(lambda s: s == t(ABOUT)))
async def on_about(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(about.features_text(), reply_markup=about.features_keyboard())


@router.message(F.text.func(lambda s: s == t(ICONS)))
async def on_icons(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t(HELP), reply_markup=back_button())
