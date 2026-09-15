"""/start (public: claims the admin seat on first run) and /help."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from handlers.common import DENIED, back_button, persistent_keyboard
from handlers.menu import send_main_menu
from services import runtime

router = Router(name="start")

HELP = (
    "🐕 TofsDevOps — личный девопс твоих сайтов.\n\n"
    "Команды:\n"
    "/menu — главное меню (дашборд)\n"
    "/status — быстрый статус сайтов\n"
    "/sites — список сайтов\n"
    "/mute 8h — приглушить некритичные алерты (1h, 30m, 1d)\n"
    "/unmute — снять заглушку\n"
    "/help — эта справка\n\n"
    "Где что в меню:\n"
    "🔎 Проверить сейчас — открываются ли сайты, сертификаты, домены, ссылки\n"
    "🔴 Проблемы — что сломано прямо сейчас и что с этим делать\n"
    "🌍 Мои сайты — добавить или убрать сайт, настройки, «я чиню»\n"
    "📈 Здоровье сайтов — доступность, сертификаты, домены, битые ссылки\n"
    "🔍 Поиск и ИИ — виден ли сайт Google, Яндексу и ИИ-ассистентам\n"
    "⚙️ Настройки — время сводок, тихие часы, плановые работы\n"
    "🔌 Подключения — диагностика, Google, Яндекс, Cloudflare, контроль задач\n\n"
    "Критические алерты (сайт лежит, NS сменились, noindex) пробивают "
    "mute и тихие часы; остальное приходит тихо или утренним дайджестом."
)


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always aborts any pending input flow
    if not runtime.has_admin() and message.from_user:
        await runtime.claim(message.chat.id, message.from_user.id)
        await message.answer("🔑 Ты стал администратором этого бота — все отчёты "
                             "и алерты будут приходить сюда.")
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(
        "👋 Привет! Я TofsDevOps — личный девопс твоих сайтов.\n"
        "Кнопка «📱 Меню» внизу всегда под рукой.\n\n"
        "Назван в честь Тофса — ирландского терьера, который принимает "
        "аптайм близко к сердцу.",
        reply_markup=persistent_keyboard())
    await send_main_menu(message)


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(HELP, reply_markup=back_button())
