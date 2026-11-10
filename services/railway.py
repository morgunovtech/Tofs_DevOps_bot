"""Railway's trial ends without a word. A new account gets 30 days and a
one-time $5 (no card); after that it drops to the Free plan with $1 of
credit a month, and the volume of a trial account is deleted 30 days after
its credits run out (docs.railway.com/pricing/free-trial).

The bot needs more than $1: memory alone is $10 per GB per month on
Railway, and this process holds ~190 MB once its modules are loaded
(measured with ru_maxrss in 11.2026) — about $2 a month before any CPU. So
on a trial account the bot would stop on day 30 and nobody would be told,
the one failure a monitor must never have.

The bot cannot read the plan, so it asks: once among the first-site steps,
once more around day 25 after the owner claimed it, and never again after
«✅ Уже на Hobby». The same line sits in «🔌 Подключения»."""

from datetime import datetime

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from db.database import get_state, set_state
from services import notifier
from services.notifier import Priority
from utils.clock import utcnow

PRICING_URL = "https://railway.com/pricing"
TRIAL_DAYS = 30
REMIND_AT_DAY = 25

SINCE = "railway:since"        # when the owner claimed the bot on Railway (UTC, ISO)
PAID = "railway:paid"          # the owner said the account is on Hobby
REMINDED = "railway:reminded"  # the day-25 reminder went out

STEP = ("💳 Тариф Railway — пробный период длится 30 дней с регистрации. Потом Railway переводит "
        "аккаунт на бесплатный план с $1 в месяц, а мне нужно около $2: я остановлюсь, и никто не "
        "напишет. Ещё через 30 дней Railway удалит диск с базой. Перейди на Hobby — $5 в месяц, "
        "нужна карта, которой можно платить за рубежом (российские не подойдут).")

GUIDE = ("💳 Тариф Railway\n\n"
         "Пробный период — 30 дней и $5 с регистрации, без карты. Потом аккаунт переходит на "
         "бесплатный план: $1 в месяц. Мне нужно около $2 — я работаю круглосуточно, — так что на "
         "бесплатном плане я остановлюсь, и никто не напишет. Диск с базой Railway удаляет через "
         "30 дней после конца пробного периода.\n\n"
         "Чтобы этого не было: тарифы Railway → <b>Hobby</b>, $5 в месяц, в них входит $5 расхода. "
         "Нужна карта, которой можно платить за рубежом: российские не подойдут.\n\n"
         "Не хочешь платить — меня можно бесплатно поставить на свой сервер или домашний компьютер "
         "через Docker: инструкция в README репозитория.")


def keyboard(paid_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="💳 Тарифы Railway", url=PRICING_URL),
        InlineKeyboardButton(text="✅ Уже на Hobby", callback_data=paid_cb)]])


def reminder_text(days_left: int) -> str:
    days = "завтра" if days_left <= 1 else f"примерно через {days_left} дн."
    return ("💳 Пробный период Railway заканчивается " + days + " — он длится 30 дней с регистрации, "
            "считаю от дня, когда ты меня запустил. Если аккаунт ещё на пробном, перейди на Hobby "
            "($5 в месяц): на бесплатном плане я остановлюсь, и никто не напишет, а ещё через 30 дней "
            "Railway удалит базу с сайтами.\n\n"
            "Если уже на Hobby — нажми «✅ Уже на Hobby», больше не напомню.")


async def note_claim():
    """The owner's /start is the closest thing to «the account was created»
    the bot can see. Existing installs never get a date — and no reminder:
    they have either paid or outlived the trial."""
    if config.on_railway and not await get_state(SINCE):
        await set_state(SINCE, utcnow().isoformat(timespec="seconds"))


async def is_paid() -> bool:
    return bool(await get_state(PAID))


async def set_paid():
    await set_state(PAID, "1")


async def asks() -> bool:
    """Whether a Railway line belongs on the screens at all."""
    return config.on_railway and not await is_paid()


async def days_since_claim(now: datetime | None = None) -> int | None:
    raw = await get_state(SINCE)
    if not raw:
        return None
    try:
        since = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return ((now or utcnow()) - since).days


async def remind_if_due(now: datetime | None = None) -> bool:
    """One reminder between day 25 and day 30; True when it went out."""
    if not await asks() or await get_state(REMINDED):
        return False
    days = await days_since_claim(now)
    if days is None or not REMIND_AT_DAY <= days < TRIAL_DAYS:
        return False
    sent = await notifier.send(reminder_text(TRIAL_DAYS - days), Priority.NORMAL,
                               reply_markup=keyboard("railway:paid"))
    if sent:
        await set_state(REMINDED, "1")
    return bool(sent)
