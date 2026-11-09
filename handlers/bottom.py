"""The three buttons under the input field (handlers.common.BOTTOM). A tap
works in any state: it aborts a pending input instead of being read as one —
this router goes first."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers import about
from handlers.common import ABOUT, HOME, ICONS, back_button
from handlers.menu import send_main_menu
from handlers.start import HELP

router = Router(name="bottom")


@router.message(F.text == HOME)
async def on_home(message: Message, state: FSMContext):
    await state.clear()
    await send_main_menu(message)


@router.message(F.text == ABOUT)
async def on_about(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(about.features_text(), reply_markup=about.features_keyboard())


@router.message(F.text == ICONS)
async def on_icons(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP, reply_markup=back_button())
