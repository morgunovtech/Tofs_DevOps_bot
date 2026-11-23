"""🛠 Тонкие настройки: the .env dials an owner changes more than once a
year, set from the chat. A value chosen here wins over .env and survives a
restart (services.settings); «↩︎ Как в .env» gives the server's value back.
Every dial's screen offers a few values — what the screen offers is what a
button may set — and says where the current one comes from. What only the
server can decide (addresses, containers, retention) is listed read-only
at the end, with the variable to edit."""

from collections.abc import Callable
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from handlers.common import ack, back_button, bottom, cancel_kb, mark, render
from reports.scheduler import reschedule_links
from services import settings
from utils.i18n import fmt_duration, t
from utils.text import esc
from utils.urls import host_of, proxy_label

router = Router(name="tune")


class TuneForm(StatesGroup):
    title = State()


@dataclass(frozen=True)
class Row:
    key: str                           # settings.DIALS key, the callback's second part
    icon: str
    title: str                         # Russian, translated where drawn
    presets: tuple[int, ...]           # the values the buttons offer
    words: Callable[[int], str]        # value → words («5 мин», «выкл»), translated
    why: str                           # one sentence on the dial's screen


def _off_or(words: Callable[[int], str]) -> Callable[[int], str]:
    return lambda v: t("выкл") if v == 0 else words(v)


def _minutes(v: int) -> str:
    return t("{v} мин").format(v=v)


def _seconds_of_ms(v: int) -> str:
    s = v / 1000
    return t("{v} с").format(v=int(s) if s == int(s) else s)


def _days(v: int) -> str:
    return t("за {v} дн.").format(v=v)


def _pages(v: int) -> str:
    return t("{v} стр.").format(v=v)


# The screen's order. Every key is one of settings.DIALS.
ROWS = (
    Row("check_interval", "⏱", "Проверка сайтов", (1, 2, 5, 10, 15, 30, 60), lambda v: t("раз в {v}").format(v=_minutes(v)),
        "Для сайтов, у которых интервал не задан в их настройках. Чаще — раньше узнаю о падении, но больше запросов к сайту."),
    Row("slow_ms", "🐢", "Порог «медленно»", (1000, 2000, 3000, 5000, 10000), lambda v: t("от {v}").format(v=_seconds_of_ms(v)),
        "Ответ дольше — заметка «медленно отвечает» в сводке и на карточке. У сайта может быть свой порог в его настройках."),
    Row("escalation_repeat", "🔁", "Напоминание о лежащем сайте", (0, 15, 30, 60, 120),
        _off_or(lambda v: t("через {v}").format(v=fmt_duration(v))),
        "Первое напоминание, пока сайт лежит; дальше каждый раз вдвое реже, не чаще раза в 4 ч. «Выкл» — одна тревога и одно «снова работает»."),
    Row("ssl_renew_warn", "🔒", "Автопродление сертификата", (0, 5, 7, 10, 14, 21), _off_or(_days),
        "Тревога, если так близко к сроку сертификат ещё не заменился — автопродление, похоже, сломалось."),
    Row("disk_alert_pct", "💽", "Тревога о диске", (70, 80, 85, 90, 95), lambda v: t("от {v} %").format(v=v),
        "Диск сервера, где я работаю, заполнен сильнее — 🟠 (от 95 % — 🔴); на том же пороге чищу docker, если очистка включена."),
    Row("links_hours", "🔗", "Обход ссылок", (0, 1, 6, 12, 24, 168), _off_or(lambda v: t("раз в {v}").format(v=fmt_duration(v * 60))),
        "Как часто обходить ссылки с главной и страницы из карты сайта. «Выкл» — не обходить."),
    Row("deep_sample", "🕳", "Глубокая проверка 5xx", (0, 5, 10, 20, 50), _off_or(_pages),
        "Сколько страниц из карты сайта открывать раз в час в поисках ошибок сервера глубже главной."),
    Row("seo_pages", "🔍", "Поиск и ИИ: страниц", (1, 3, 5, 10, 20), _pages,
        "Сколько страниц кроме главной смотреть в ежедневной проверке поиска и ИИ-ассистентов."),
    Row("seo_min_text", "🔍", "Поиск и ИИ: мало текста", (0, 200, 400, 800, 1500), _off_or(lambda v: t("от {v} зн.").format(v=v)),
        "Страница с текстом короче — находка «мало текста» в проверке поиска. «Выкл» — не проверять."),
)
_BY_KEY = {r.key: r for r in ROWS}
_LANG_WORDS = {"ru": "русский", "en": "английский"}
BACK = ("← Тонкие настройки", "menu_tune")


def _source(from_env: bool, env: str, env_value) -> str:
    """«из .env (X=5)» or «задано здесь (в .env X=5)» — the owner must see
    that a tap here overrides the server's value."""
    env_pair = f"{env}={env_value}" if env_value != "" else f"{env} {t('пусто')}"
    return (t("из .env ({env})") if from_env else t("задано здесь (в .env {env})")).format(env=env_pair)


def _dial_line(row: Row) -> str:
    """One line of the hub: icon, title, the value, where it comes from."""
    where = t("из .env") if settings.dial_from_env(row.key) else t("задано здесь")
    return f"{row.icon} {t(row.title)}: {row.words(settings.dial(row.key))} — {where}"


def _status_page_words() -> str:
    lang = t("по-русски") if settings.status_page_lang() == "ru" else t("по-английски")
    title = settings.status_page_title()
    return lang + (t(", название «{title}»").format(title=esc(title)) if title else t(", без своего названия"))


def _server_only() -> list[str]:
    """What stays in .env, with its value as the bot sees it."""
    containers = ", ".join(config.autorestart_containers)
    shots = host_of(config.screenshot_template) if config.screenshot_template != "off" else t("выкл")
    return [
        t("• внутренние адреса: {v} — ALLOW_PRIVATE_TARGETS").format(
            v=t("разрешены") if config.allow_private_targets else t("только публичные")),
        t("• перезапуск контейнеров: {v} — AUTORESTART_CONTAINERS").format(v=esc(containers) if containers else t("нет")),
        t("• история проверок: {days} дн., ночных копий: {keep} — RETENTION_DAYS, DB_BACKUP_KEEP").format(
            days=config.retention_days, keep=config.db_backup_keep),
        t("• снимки страниц: {v} — SCREENSHOT_TEMPLATE").format(v=esc(shots)),
        t("• проверка уязвимостей и деплоя: {v} — DEPENDENCY_WATCH").format(
            v=t("вкл") if config.dependency_watch else t("выкл")),
        t("• прокси для Telegram: {v} — TELEGRAM_PROXY").format(
            v=esc(proxy_label(config.telegram_proxy)) if config.telegram_proxy else t("нет")),
    ]


def hub_summary() -> str:
    """One line for «⚙️ Настройки»: the three dials people ask about, and
    whether anything here differs from the server."""
    changed = sum(1 for key in settings.DIALS if not settings.dial_from_env(key))
    changed += sum(1 for key in ("status_page_title", "status_page_lang", "lang") if not settings.text_from_env(key))
    return (t("проверка раз в {m} мин · «медленно» от {s} · напоминание {r}").format(
        m=settings.check_interval_minutes(), s=_seconds_of_ms(settings.slow_response_ms()),
        r=_off_or(lambda v: t("через {v}").format(v=fmt_duration(v)))(settings.escalation_repeat_min()))
        + (t(" · задано здесь: {n}").format(n=changed) if changed else t(" · всё из .env")))


def tune_text() -> str:
    lines = [t("🛠 Тонкие настройки"), "",
             t("Что раньше менялось только в .env на сервере. Значение, выбранное здесь, главнее .env и переживает перезапуск; «↩︎ Как в .env» возвращает серверное."), ""]
    lines += [_dial_line(r) for r in ROWS]
    lines.append(t("🌐 Статус-страница: {words} — {where}").format(
        words=_status_page_words(),
        where=t("из .env") if settings.text_from_env("status_page_lang") and settings.text_from_env("status_page_title")
        else t("задано здесь")))
    lines.append(t("🗣 Язык бота: {lang} — {where}").format(
        lang=t(_LANG_WORDS[settings.lang()]), where=t("из .env") if settings.text_from_env("lang") else t("задано здесь")))
    lines += ["", t("Только на сервере (.env):"), *_server_only(), "", t("Тапни, чтобы изменить:")]
    return "\n".join(lines)


def tune_kb() -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(text=f"{r.icon} {t(r.title)}", callback_data=f"tune:{r.key}") for r in ROWS]
    buttons += [InlineKeyboardButton(text=t("🌐 Статус-страница"), callback_data="tune:status_page"),
                InlineKeyboardButton(text=t("🗣 Язык бота"), callback_data="tune:lang")]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_tune")
async def cb_tune(call: CallbackQuery):
    await ack(call)
    await render(call, tune_text(), tune_kb())


# ── One dial ─────────────────────────────────────────────────────────────────

def dial_screen(row: Row) -> tuple[str, InlineKeyboardMarkup]:
    d, value = settings.DIALS[row.key], settings.dial(row.key)
    text = "\n".join([
        f"{row.icon} {t(row.title)}: <b>{row.words(value)}</b> — {_source(settings.dial_from_env(row.key), d.env, settings.dial_default(row.key))}.",
        "", t(row.why), "", t("Тапни, чтобы изменить:")])
    buttons = [InlineKeyboardButton(text=mark(v == value, row.words(v)), callback_data=f"tune:{row.key}:{v}")
               for v in row.presets]
    rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
    rows.append([InlineKeyboardButton(text=t("↩︎ Как в .env ({v})").format(v=row.words(settings.dial_default(row.key))),
                                      callback_data=f"tune:{row.key}:env")])
    rows.append([InlineKeyboardButton(text=t(BACK[0]), callback_data=BACK[1])])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.func(lambda d: (d or "").startswith("tune:") and (d or "").split(":")[1] in _BY_KEY))
async def cb_dial(call: CallbackQuery):
    """tune:<key> draws the dial; tune:<key>:<value> sets one of the offered
    values, tune:<key>:env drops the chat's value. Anything else — a forged
    or stale button — changes nothing."""
    parts = call.data.split(":")
    row = _BY_KEY[parts[1]]
    if len(parts) == 2:
        await ack(call)
        await render(call, *dial_screen(row))
        return
    if len(parts) != 3 or (parts[2] != "env" and parts[2] not in {str(v) for v in row.presets}):
        await ack(call, t("Не понял"), show_alert=True)
        return
    if not await ack(call, t("Сохранено ✅")):   # a tap from the queue after a restart sets nothing
        return
    await settings.set_dial(row.key, None if parts[2] == "env" else int(parts[2]))
    if row.key == "links_hours" and not reschedule_links():
        await call.message.answer(t("Сохранено, но расписание не обновилось — подробности в логе"))
    await render(call, *dial_screen(row))


# ── The status page: its language and title ──────────────────────────────────

def status_page_screen() -> tuple[str, InlineKeyboardMarkup]:
    lang, title = settings.status_page_lang(), settings.status_page_title()
    text = "\n".join([
        t("🌐 Статус-страница: {words}.").format(words=_status_page_words()),
        t("Язык — {src}; название — {src2}.").format(
            src=_source(settings.text_from_env("status_page_lang"), "STATUS_PAGE_LANG", config.status_page_lang),
            src2=_source(settings.text_from_env("status_page_title"), "STATUS_PAGE_TITLE", config.status_page_title)),
        "", t("Язык страницы, её JSON-текстов и бейджей — для посетителей; язык самого бота — «🗣 Язык бота». Название показывается над баннером и во вкладке браузера."),
        "", t("Тапни, чтобы изменить:")])
    rows = [[InlineKeyboardButton(text=mark(lang == "ru", t("🌐 По-русски")), callback_data="tune:sp_lang:ru"),
             InlineKeyboardButton(text=mark(lang == "en", t("🌐 По-английски")), callback_data="tune:sp_lang:en")],
            [InlineKeyboardButton(text=t("✏️ Название: «{title}»").format(title=title)[:60] if title else t("✏️ Задать название"),
                                  callback_data="tune:sp_title")],
            [InlineKeyboardButton(text=t("↩︎ Как в .env"), callback_data="tune:sp_env")],
            [InlineKeyboardButton(text=t(BACK[0]), callback_data=BACK[1])]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.in_({"tune:status_page", "tune:sp_lang:ru", "tune:sp_lang:en", "tune:sp_env"}))
async def cb_status_page(call: CallbackQuery):
    if call.data == "tune:status_page":
        await ack(call)
    elif not await ack(call, t("Сохранено ✅")):
        return
    elif call.data == "tune:sp_env":
        await settings.set_status_page_lang(None)
        await settings.set_status_page_title(None)
    else:
        await settings.set_status_page_lang(call.data.rsplit(":", 1)[1])
    await render(call, *status_page_screen())


@router.callback_query(F.data == "tune:sp_title")
async def cb_status_page_title(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(TuneForm.title)
    await render(call, t("Пришли название статус-страницы одним сообщением — до {n} знаков, например «Acme — статус сервисов». Прислать <code>-</code> — без своего названия.").format(n=settings.STATUS_PAGE_TITLE_MAX),
                 cancel_kb("tune:status_page"))


@router.message(TuneForm.title)
async def msg_status_page_title(message: Message, state: FSMContext):
    raw = (message.text or "").strip()
    if not raw or not await settings.set_status_page_title("" if raw == "-" else raw):
        await message.answer(t("Нужна одна строка до {n} знаков. Попробуй ещё раз:").format(n=settings.STATUS_PAGE_TITLE_MAX),
                             reply_markup=cancel_kb("tune:status_page"))
        return
    await state.clear()
    text, kb = status_page_screen()
    await message.answer(t("✅ Сохранил.") + "\n\n" + text, reply_markup=kb)


# ── The bot's own language ───────────────────────────────────────────────────

def lang_screen() -> tuple[str, InlineKeyboardMarkup]:
    lang = settings.lang()
    text = "\n".join([
        t("🗣 Язык бота: <b>{lang}</b> — {src}.").format(
            lang=t(_LANG_WORDS[lang]), src=_source(settings.text_from_env("lang"), "BOT_LANG", config.lang)),
        "", t("Каждый экран, тревога и отчёт. Язык статус-страницы и виджета для посетителей — отдельно, «🌐 Статус-страница»."),
        "", t("Тапни, чтобы изменить:")])
    rows = [[InlineKeyboardButton(text=mark(lang == "ru", t("🗣 Русский")), callback_data="tune:lang:ru"),
             InlineKeyboardButton(text=mark(lang == "en", "🗣 English"), callback_data="tune:lang:en")],
            [InlineKeyboardButton(text=t("↩︎ Как в .env ({v})").format(v=config.lang), callback_data="tune:lang:env")],
            [InlineKeyboardButton(text=t(BACK[0]), callback_data=BACK[1])]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.in_({"tune:lang", "tune:lang:ru", "tune:lang:en", "tune:lang:env"}))
async def cb_lang(call: CallbackQuery):
    """The switch is immediate (utils.i18n). The three buttons under the
    input field keep their old words until re-sent, so they are."""
    if call.data == "tune:lang":
        await ack(call)
        await render(call, *lang_screen())
        return
    if not await ack(call, t("Сохранено ✅")):
        return
    code = call.data.rsplit(":", 1)[1]
    before = settings.lang()
    await settings.set_lang(None if code == "env" else code)
    await render(call, *lang_screen())
    if settings.lang() != before:
        await call.message.answer(t("Язык бота: {lang}. Кнопки под полем ввода обновил.").format(lang=t(_LANG_WORDS[settings.lang()])),
                                  reply_markup=bottom())


def back_to_tune() -> InlineKeyboardMarkup:
    return back_button(*BACK)
