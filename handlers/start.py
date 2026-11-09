"""/start (public: claims the admin seat on first run; with a helper
invite, makes a helper), /stop for helpers, and /help."""

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import config
from db.database import set_state
from handlers.common import BOTTOM, DENIED, back_button
from handlers.menu import send_main_menu
from services import helpers, notifier, railway, runtime, settings
from utils.text import esc

router = Router(name="start")

# Set once the owner has the bottom buttons (main.py announces them otherwise).
BOTTOM_SHOWN = "ui:bottom_keyboard"

HELP = (
    "🐕 TofsDevOps следит за твоими сайтами и пишет, только когда что-то сломалось.\n\n"
    "Что значат значки:\n"
    "🔴 — нужно действие сейчас: сайт не открывается, кто-то сменил настройки домена, сайт закрыли "
    "от поисковиков. Звоню даже ночью и в тишину.\n"
    "🟠 — нужно действие, но не сегодня: например, сертификат скоро закончится. Приходит без звука.\n"
    "✅ — всё в порядке или снова работает.\n"
    "⚠️ — не смог проверить: сам попробую ещё раз.\n"
    "⏳ — жду первой проверки или первой связи.\n\n"
    "Где что:\n"
    "🏠 Главная (кнопка под полем ввода) — всё на одном экране, по блокам: сайты, соцсети, боты и "
    "системы. Кнопка блока открывает всё о нём, там же «➕ Добавить».\n"
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


_HELPER_ANSWERS = {
    "ok": "✅ Готово: теперь, если сайт ляжет, я позвоню и тебе — и напишу, когда он снова заработает. "
          "Больше ничего присылать не буду, управляет ботом владелец. Перестать — /stop.",
    "already": "Ты уже помощник — тревоги 🔴 приходят и тебе. Перестать — /stop.",
    "full": "У владельца уже три помощника — больше нельзя. Попроси его убрать кого-нибудь.",
    "invalid": "Ссылка не сработала: она одноразовая и живёт сутки. Попроси у владельца новую.",
}


@router.message(CommandStart(deep_link=True, magic=F.args.startswith(helpers.PAYLOAD_PREFIX)))
async def cmd_start_helper(message: Message, command: CommandObject, state: FSMContext):
    """An invite link from «⚙️ Настройки → 👥 Помощники». Never claims the
    admin seat, whatever happens."""
    await state.clear()
    if runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Это приглашение для помощника — перешли ссылку тому, кто подстрахует тебя.")
        return
    if message.chat.type != "private":
        await message.answer("Открой эту ссылку в личном чате со мной.")
        return
    name = message.from_user.full_name if message.from_user else ""
    result = await helpers.accept(command.args[len(helpers.PAYLOAD_PREFIX):], message.chat.id, name)
    await message.answer(_HELPER_ANSWERS[result])
    if result == "ok":
        await notifier.send(f"👥 {esc(name)} теперь получает тревоги 🔴. Убрать — ⚙️ Настройки → 👥 Помощники.")


@router.message(Command("stop"))
async def cmd_stop(message: Message):
    if runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer("Ты владелец. Чтобы я помолчал — /mute 8h, тревога «сайт лёг» всё равно придёт.")
        return
    gone = await helpers.remove(message.chat.id)
    if not gone:
        await message.answer(DENIED)
        return
    await message.answer("Больше не присылаю тревоги. Вернуться можно по новой ссылке от владельца.")
    await notifier.send(f"👥 {esc(gone['name'])} больше не получает тревоги 🔴.")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always aborts any pending input flow
    if not runtime.has_admin() and message.from_user:
        await runtime.claim(message.chat.id, message.from_user.id)
        await railway.note_claim()   # the trial clock: the day-25 reminder counts from here
        await message.answer("🔑 Ты стал администратором этого бота — все отчёты "
                             "и тревоги будут приходить сюда.")
        if runtime.db_on_persistent_disk() is False:   # now, before the first site is typed in
            await message.answer(runtime.NO_VOLUME)
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(
        "👋 Привет! Я TofsDevOps — слежу за твоими сайтами, чтобы тебе не пришлось.\n"
        "Всё под рукой — кнопки под полем ввода: 🏠 Главная, 📖 Что я умею и ❓ Значки.\n\n"
        "Назван в честь Тофса — ирландского терьера, который принимает "
        "аптайм близко к сердцу.",
        reply_markup=BOTTOM)
    await set_state(BOTTOM_SHOWN, "1")   # nothing left for main.py to announce
    await message.answer(rules_of_the_game())
    await send_main_menu(message)


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not runtime.is_admin(message.from_user.id if message.from_user else None):
        await message.answer(DENIED)
        return
    await message.answer(HELP, reply_markup=back_button())
