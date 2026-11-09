"""/start (public: claims the admin seat on first run) and /help."""

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import config
from handlers.common import DENIED, back_button, persistent_keyboard
from handlers.menu import send_main_menu
from services import runtime, settings

router = Router(name="start")

HELP = (
    "🐕 TofsDevOps следит за твоими сайтами и пишет, только когда что-то сломалось.\n\n"
    "Что значат значки:\n"
    "🔴 — нужно действие сейчас: сайт не открывается, кто-то сменил настройки домена, сайт закрыли "
    "от поисковиков. Звоню даже ночью и в тишину.\n"
    "🟠 — нужно действие, но не сегодня: например, сертификат скоро закончится. Приходит без звука.\n"
    "✅ — всё в порядке или снова работает.\n"
    "⚠️ — не смог проверить: сам попробую ещё раз.\n"
    "⏳ — жду первой проверки или первой отметки.\n\n"
    "Где что:\n"
    "📱 Меню — всё на одном экране: строка и кнопка на каждый сайт, бота и задачу.\n"
    "➕ Добавить — сайт, другой бот или задача по расписанию (например, бэкап).\n"
    "⚙️ Настройки — во сколько присылать сводку, ночная тишина, пробная тревога.\n\n"
    "Нужно, чтобы я помолчал: /mute 8h — пишу только о том, что сайт лёг. Снова обо всём — /unmute."
)


def rules_of_the_game() -> str:
    """One message that removes the novice's main fear: «will it spam me?»"""
    quiet = settings.quiet_hours()
    quiet_line = (f"Ночью с {quiet[0]}:00 до {quiet[1]}:00 не бужу — некритичное подожду до утра."
                  if quiet else "Тихих часов нет — можно включить в ⚙️ Настройках.")
    return ("Как я работаю:\n"
            f"• Проверяю каждый сайт каждые {config.check_interval_minutes} мин.\n"
            "• Пишу только если что-то сломалось, и сразу говорю, что делать.\n"
            f"• Каждое утро в {settings.morning_hour()}:00 одно короткое «всё в порядке».\n"
            f"• {quiet_line}\n"
            "• Если сайт лёг, напишу в любое время и повторю через полчаса, пока не поднимется.")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always aborts any pending input flow
    if not runtime.has_admin() and message.from_user:
        await runtime.claim(message.chat.id, message.from_user.id)
        await message.answer("🔑 Ты стал администратором этого бота — все отчёты "
                             "и тревоги будут приходить сюда.")
        if runtime.db_on_persistent_disk() is False:   # now, before the first site is typed in
            await message.answer(runtime.NO_VOLUME)
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(
        "👋 Привет! Я TofsDevOps — слежу за твоими сайтами, чтобы тебе не пришлось.\n"
        "Кнопка «📱 Меню» внизу всегда под рукой.\n\n"
        "Назван в честь Тофса — ирландского терьера, который принимает "
        "аптайм близко к сердцу.",
        reply_markup=persistent_keyboard())
    await message.answer(rules_of_the_game())
    await send_main_menu(message)


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(HELP, reply_markup=back_button())
