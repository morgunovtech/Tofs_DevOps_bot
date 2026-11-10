"""/start (public: claims the admin seat on first run; with a helper
invite, makes a helper), /stop for helpers, and /help."""

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import config
from db.database import set_state
from handlers.common import BOTTOM, DENIED, HELPER_NOTE, back_button, refusal
from handlers.filters import in_admin_chat
from handlers.menu import send_main_menu
from services import helpers, notifier, railway, runtime, settings
from utils.text import esc

logger = logging.getLogger(__name__)
router = Router(name="start")

# Telegram's clock and ours may differ by a few seconds.
CLAIM_CLOCK_SLACK_SEC = 5
PRIVATE_ONLY = ("Владельцем можно стать только в личном чате со мной: открой его и нажми Start. "
                "Группа для тревог задаётся переменными TELEGRAM_ADMIN_CHAT_ID и TELEGRAM_ADMIN_USER_ID.")
TRY_AGAIN = "Я только что запустился — нажми /start ещё раз."

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
    "Нужно, чтобы я помолчал: /mute 8h — приходят только звонящие: сайт лёг, а при «🔔 Будить: всё "
    "критичное» ещё снова работает, смена настроек домена, сайт закрыт от поисковиков; остальное — когда "
    "тишина кончится. Снова обо всём — /unmute. Будить только когда сайт лёг — ⚙️ Настройки → 🔔 Когда писать."
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
            f"• {_down_line()}")


def _down_line() -> str:
    """What the escalation really does (reports.scheduler.run_escalation_watch):
    reminders at a growing pause, none at all with 0 repeats."""
    repeat = config.escalation_repeat_min
    return ("Если сайт лёг, напишу в любое время"
            + (f" и напомню через {repeat} мин, потом всё реже (не чаще раза в 4 ч), пока не поднимется."
               if repeat else "."))


_HELPER_ANSWERS = {
    "ok": "✅ Готово: теперь, если сайт ляжет, я позвоню и тебе — и напишу, когда он снова заработает. "
          "Больше ничего присылать не буду, управляет ботом владелец. Перестать — /stop.",
    "already": "Ты уже помощник — «сайт лёг» и «снова работает» приходят и тебе. Перестать — /stop.",
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
        await notifier.send(f"👥 {esc(name)} теперь получает «сайт лёг» и «снова работает». "
                            "Убрать — ⚙️ Настройки → 👥 Помощники.")


@router.message(F.migrate_from_chat_id)
async def on_group_upgraded(message: Message):
    """Telegram turned the admin group into a supergroup: follow it at once,
    not at the next alert — commands from the new chat count from now on."""
    if str(message.migrate_from_chat_id) == runtime.admin_chat_id():
        await runtime.move_admin_chat(message.chat.id)
        if runtime.env_chat_outdated():
            await message.answer(f"Группа стала супергруппой — теперь пишу сюда. В .env замени "
                                 f"TELEGRAM_ADMIN_CHAT_ID на <code>{message.chat.id}</code>.")


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
    await notifier.send(f"👥 {esc(gone['name'])} больше не получает «сайт лёг» и «снова работает».")


def _claim_refusal(message: Message) -> str | None:
    """Why this /start may not take the free admin seat, or None."""
    user = message.from_user
    if message.chat.type != "private" or message.sender_chat is not None or user.is_bot:
        return PRIVATE_ONLY
    age = runtime.STARTED_AT - message.date.timestamp()
    if age > CLAIM_CLOCK_SLACK_SEC:
        # Queued while the bot was down: the owner may not even know the seat is free.
        logger.warning("Ignoring a /start sent %.0f s before this process started — if this "
                       "repeats, check the server clock", age)
        return TRY_AGAIN
    return None


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always aborts any pending input flow
    if not runtime.has_admin() and message.from_user:
        refusal_text = _claim_refusal(message)
        if refusal_text:
            await message.answer(refusal_text)
            return
        if await runtime.claim(message.chat.id, message.from_user.id):
            await railway.note_claim()   # the trial clock: the day-25 reminder counts from here
            await message.answer("🔑 Ты стал администратором этого бота — все отчёты "
                                 "и тревоги будут приходить сюда.")
            if runtime.db_on_persistent_disk() is False:   # now, before the first site is typed in
                await message.answer(runtime.NO_VOLUME)
    if not in_admin_chat(message):
        await message.answer(HELPER_NOTE if await helpers.is_helper(message.chat.id) else refusal(message))
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
    if not in_admin_chat(message):
        await message.answer(HELPER_NOTE if await helpers.is_helper(message.chat.id) else refusal(message))
        return
    await message.answer(HELP, reply_markup=back_button())
