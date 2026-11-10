"""⚙️ Settings: the hub for everything that is not «is it OK» — digests
and quiet hours, mute, timezone, maintenance windows, diagnostics and
connections (the test alert lives there), status page, feedback,
export/import. What is watched — sites, tasks and bots — is on the main
screen, not here."""

import contextlib
import secrets as _secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import count_feedback
from handlers.common import STALE_TAP, ack, back_button, cancel_kb, render
from handlers.menu import mute_until_local
from reports.scheduler import reschedule_all
from services import (
    github,
    gsc,
    helpers,
    integrations,
    notifier,
    public_urls,
    runtime,
    settings,
    yandex_webmaster,
)
from utils.clock import now_local
from utils.text import esc


class TzForm(StatesGroup):
    key = State()


TIMEZONES = [
    ("Москва", "Europe/Moscow"), ("Калининград", "Europe/Kaliningrad"), ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"), ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"), ("Иркутск", "Asia/Irkutsk"), ("Владивосток", "Asia/Vladivostok"),
    ("Киев", "Europe/Kyiv"), ("Минск", "Europe/Minsk"), ("Алматы", "Asia/Almaty"), ("Тбилиси", "Asia/Tbilisi"),
    ("Ереван", "Asia/Yerevan"), ("Берлин", "Europe/Berlin"), ("Лондон", "Europe/London"),
    ("Нью-Йорк", "America/New_York"), ("UTC", "UTC"),
]

router = Router(name="settings")


async def hub_text() -> str:
    """Five questions, each answered in one line: when to write, who else
    hears it, what is connected, what visitors see, which clock."""
    quiet = settings.quiet_hours()
    mute = await mute_until_local()
    ring = "только «сайт лёг»" if settings.ring_only_down() else "всё критичное"
    write = (f"сводка {settings.morning_hour()}:00 · ночью тихо "
             f"{'—' if not quiet else f'{quiet[0]}–{quiet[1]}'} · будить: {ring}"
             + (f" · 🔕 молчу до {mute}" if mute else ""))
    # From what is configured, no probes: the hub must open at once; the
    # «🔌 Подключения» screen checks each one live.
    known = [("сторож", bool(integrations.self_heartbeat_url())), ("Google", gsc.available()),
             ("Яндекс", yandex_webmaster.available()),
             ("Cloudflare", bool(integrations.cf_api_token() or integrations.deploy_hooks())),
             ("GitHub", github.available())]
    on = [name for name, ok in known if ok]
    off = [name for name, ok in known if not ok]
    connections = ((f"{', '.join(on)} ✅" if on else "ничего не подключено")
                   + (f" · можно: {', '.join(off)}" if off else ""))
    sp = "статус-страница вкл." if settings.status_page_enabled() else "статус-страница выкл."
    feedback = await count_feedback()
    visitors = sp + (f" · сообщений с сайтов: {feedback}" if feedback else "")
    helpers_now = ", ".join(h["name"] for h in await helpers.helpers()) or "нет"
    return ("⚙️ Настройки\n\n"
            f"🔔 Когда писать — {esc(write)}\n"
            f"👥 Помощники — {esc(helpers_now)}\n"
            f"🔌 Подключения — {esc(connections)}\n"
            f"🌍 Для посетителей — {esc(visitors)}\n"
            f"🕐 Часовой пояс — {esc(settings.timezone())}, сейчас {now_local().strftime('%H:%M')}")


def hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Когда писать", callback_data="menu_reports"),
         InlineKeyboardButton(text="👥 Помощники", callback_data="menu_helpers")],
        [InlineKeyboardButton(text="🔌 Подключения", callback_data="menu_diag"),
         InlineKeyboardButton(text="🌍 Для посетителей", callback_data="menu_visitors")],
        [InlineKeyboardButton(text="🕐 Часовой пояс", callback_data="menu_tz"),
         InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")]])


async def show_hub(target):
    await render(target, await hub_text(), hub_kb())


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    await ack(call)
    await show_hub(call)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def helpers_screen() -> tuple[str, InlineKeyboardMarkup]:
    items = await helpers.helpers()
    lines = ["👥 Помощники", ""]
    if items:
        lines.append("Тревоги 🔴 — сайт лёг и снова работает — приходят тебе и:")
        lines += [f"• {esc(h['name'])}" for h in items]
    else:
        lines.append("Тревоги 🔴 — сайт лёг и снова работает — сейчас приходят только тебе. Если ты в "
                     "отпуске или сел телефон, о лежащем сайте никто не узнает.")
    lines += ["", "Помощник получает только то, что звонит, без кнопок, и не может управлять ботом. "
                  "Уйти сам может командой /stop."]
    rows = [[InlineKeyboardButton(text=f"🗑 Убрать {h['name']}", callback_data=f"helper_rm:{h['chat_id']}")]
            for h in items]
    if len(items) < helpers.MAX_HELPERS:
        rows.append([InlineKeyboardButton(text="🔗 Пригласить помощника", callback_data="helper_invite")])
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_helpers")
async def cb_helpers(call: CallbackQuery):
    await ack(call)
    await render(call, *await helpers_screen())


@router.callback_query(F.data == "helper_invite")
async def cb_helper_invite(call: CallbackQuery):
    await ack(call)
    payload = helpers.PAYLOAD_PREFIX + await helpers.create_invite()
    me = runtime.bot_username()
    link = f"https://t.me/{me}?start={payload}" if me else f"/start {payload}"
    await render(call, f"🔗 Перешли эту ссылку тому, кто подстрахует тебя:\n\n{esc(link)}\n\n"
                       f"Он откроет её и нажмёт «Start» — и будет получать тревоги 🔴. Ссылка сработает "
                       f"один раз и живёт сутки.", back_button("← Помощники", "menu_helpers"))


@router.callback_query(F.data.startswith("helper_rm:"))
async def cb_helper_remove(call: CallbackQuery):
    if not await ack(call):                     # a tap from the queue after a restart removes nobody
        await call.message.answer(STALE_TAP)
        return
    gone = await helpers.remove(call.data.split(":", 1)[1])
    if gone:
        await notifier.send_to(gone["chat_id"], "Владелец убрал тебя из помощников — тревоги больше не приходят.")
    await render(call, *await helpers_screen())


# ── Digests and quiet hours ──────────────────────────────────────────────────

# What the screen offers is what a button may set: a forged or stale
# «set_m:99» must not reach the scheduler (one bad hour stops every start).
MORNING_HOURS, EVENING_HOURS, WEEKLY_HOURS = (7, 8, 9, 10), (20, 21, 22), (10, 11, 12, 18)
QUIET_CHOICES = ("23-8", "0-7")


def _offered(value: str, hours: tuple[int, ...]) -> int | None:
    return int(value) if value in {str(h) for h in hours} else None


def _reports_kb() -> InlineKeyboardMarkup:
    ring_on = settings.ring_only_down()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Будить: только «сайт лёг»" if ring_on else "🔔 Будить: всё критичное",
                              callback_data=f"set_ring:{'off' if ring_on else 'on'}")],
        [InlineKeyboardButton(text=f"🌅 {h}:00", callback_data=f"set_m:{h}") for h in MORNING_HOURS],
        [InlineKeyboardButton(text=f"🌙 {h}:00", callback_data=f"set_e:{h}") for h in EVENING_HOURS]
        + [InlineKeyboardButton(text="🌙 выкл", callback_data="set_e:off")],
        [InlineKeyboardButton(text=f"📊 вс {h}:00", callback_data=f"set_w:{h}") for h in WEEKLY_HOURS],
        [InlineKeyboardButton(text="🗄 Копия базы по воскресеньям: вкл" if settings.weekly_backup()
                              else "🗄 Копия базы по воскресеньям: выкл",
                              callback_data=f"set_wb:{'off' if settings.weekly_backup() else 'on'}")],
        [InlineKeyboardButton(text=f"🔕 {q.replace('-', '–')}", callback_data=f"set_q:{q}") for q in QUIET_CHOICES]
        + [InlineKeyboardButton(text="🔕 выкл", callback_data="set_q:off")],
        # Everything about «when» in one place: a pause, planned work, and
        # the test that the phone actually rings.
        [InlineKeyboardButton(text="🔕 Помолчать…", callback_data="menu_mute"),
         InlineKeyboardButton(text="🗓 Плановые работы", callback_data="menu_maint")],
        [InlineKeyboardButton(text="🔔 Пробная тревога", callback_data="ring:test")],
        [InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")]])


def _reports_text() -> str:
    evening, quiet = settings.evening_hour(), settings.quiet_hours()
    return ("🔔 Когда писать\n\n"
            f"🌅 Утренняя сводка: {settings.morning_hour()}:00 — одно короткое «всё в порядке» "
            f"или список того, что не так.\n"
            f"🌙 Вечерняя: {'выключена' if evening is None else f'{evening}:00, только если есть проблемы'}.\n"
            f"📊 Недельная: воскресенье {settings.weekly_hour()}:00 — простой за неделю и месяц, "
            f"график, одна идея.\n"
            f"🗄 Копия базы: {'по воскресеньям тебе в личку, без ключей подключений' if settings.weekly_backup() else 'не присылаю'}.\n"
            f"🔕 Тихие часы: {'выключены' if not quiet else f'{quiet[0]}–{quiet[1]}'} — некритичное "
            f"ждёт до утра, «сайт лежит» приходит всегда.\n"
            f"🔔 Будить: {'только когда сайт лёг — остальное, даже критичное, ждёт тихие часы' if settings.ring_only_down() else 'всё критичное: сайт лёг, смена NS-серверов, сайт закрыт от поиска'}.\n\n"
            f"Тапни, чтобы изменить:")


@router.callback_query(F.data == "menu_reports")
async def cb_reports(call: CallbackQuery):
    await ack(call)
    await render(call, _reports_text(), _reports_kb())


@router.callback_query(F.data.startswith("set_"))
async def cb_settings_pick(call: CallbackQuery):
    kind, _, value = call.data.partition(":")
    if kind == "set_sp" and value == "on":
        # Publishing the status page waits for a fresh tap (STALE_TAP rule); switching off is always safe.
        if not await ack(call, "Сохранено ✅"):
            await call.message.answer(STALE_TAP)
            return
        await settings.set_status_page(True)
        with contextlib.suppress(TelegramBadRequest):
            await show_status_page(call)
        return
    if kind == "set_m" and _offered(value, MORNING_HOURS) is not None:
        await settings.set_morning_hour(int(value))
    elif kind == "set_e" and (value == "off" or _offered(value, EVENING_HOURS) is not None):
        await settings.set_evening_hour(None if value == "off" else int(value))
    elif kind == "set_w" and _offered(value, WEEKLY_HOURS) is not None:
        await settings.set_weekly_hour(int(value))
    elif kind == "set_q" and (value == "off" or value in QUIET_CHOICES):
        await settings.set_quiet_hours(None if value == "off" else value)
    elif kind == "set_sp" and value in ("on", "off"):
        await settings.set_status_page(value == "on")
    elif kind == "set_ring" and value in ("on", "off"):
        await settings.set_ring_only_down(value == "on")
    elif kind == "set_wb" and value in ("on", "off"):
        await settings.set_weekly_backup(value == "on")
    elif kind == "set_tz" and value.isdigit() and int(value) < len(TIMEZONES):
        await settings.set_timezone(TIMEZONES[int(value)][1])
    elif kind == "set_tz" and value == "env":
        await settings.set_timezone(None)
    else:
        await ack(call, "Не понял", show_alert=True)
        return
    failed = reschedule_all()  # apply new hours / timezone to the running scheduler now
    await ack(call, "Сохранено, но расписание не обновилось — подробности в логе" if failed else "Сохранено ✅")
    try:
        if kind == "set_sp":
            await show_status_page(call)
        elif kind == "set_tz":
            await render(call, _tz_text(), _tz_kb())
        else:
            await render(call, _reports_text(), _reports_kb())
    except TelegramBadRequest:
        pass


# ── Timezone ─────────────────────────────────────────────────────────────────

def _tz_text() -> str:
    return (f"🕐 Часовой пояс: <b>{esc(settings.timezone())}</b>, сейчас у тебя {now_local().strftime('%H:%M')}.\n"
            "От него зависят время сводок, тихие часы и все времена в сообщениях.\n\nВыбери город или пришли "
            "свой ключ вида <code>Europe/Prague</code>:")


def _tz_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, (name, key) in enumerate(TIMEZONES):
        mark = "✅ " if key == settings.timezone() else ""
        row.append(InlineKeyboardButton(text=f"{mark}{name}", callback_data=f"set_tz:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✏️ Другой", callback_data="tz_custom"),
                 InlineKeyboardButton(text=f"↩︎ Из .env ({config.timezone})", callback_data="set_tz:env")])
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_tz")
async def cb_tz(call: CallbackQuery):
    await ack(call)
    await render(call, _tz_text(), _tz_kb())


@router.callback_query(F.data == "tz_custom")
async def cb_tz_custom(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(TzForm.key)
    await render(call, "Пришли ключ часового пояса из базы IANA, например <code>Europe/Prague</code> "
                       "или <code>Asia/Tashkent</code>.", cancel_kb())


@router.message(TzForm.key)
async def msg_tz(message: Message, state: FSMContext):
    key = (message.text or "").strip()
    if not await settings.set_timezone(key):
        await message.answer("Такого пояса не знаю. Формат: <code>Регион/Город</code>, например "
                             "<code>Europe/Prague</code>.", reply_markup=cancel_kb())
        return
    await state.clear()
    reschedule_all()
    await message.answer(f"✅ Часовой пояс: {esc(key)}, сейчас у тебя {now_local().strftime('%H:%M')}.",
                         reply_markup=back_button("← Настройки", "menu_settings"))


# ── Status page ──────────────────────────────────────────────────────────────

async def show_status_page(target):
    on = settings.status_page_enabled()
    slug_src = integrations.source("status_page_slug")
    lines = ["🌐 Публичная статус-страница", ""]
    if on:
        lines.append(f"Включена: {esc(public_urls.status_page_url())}")
        if not public_urls.is_public():
            lines.append(esc(public_urls.NOT_PUBLIC))
        lines.append("Показывает, открываются ли сайты, простой за сутки/неделю/месяц и историю проблем без "
                     "подробностей. Удобно давать клиентам или ставить в README.")
    else:
        lines.append("Выключена. Включи — и по ссылке будет страница «всё ли работает» с аптаймом.")
    if slug_src == "env":
        lines.append("\n🔑 Секретная ссылка задана в .env (STATUS_PAGE_SLUG).")
    elif slug_src == "bot":
        lines.append("\n🔑 Секретная ссылка включена: без секретного хвоста страница отвечает «не найдено».")
    rows = [[InlineKeyboardButton(text="🌐 Выключить" if on else "🌐 Включить",
                                  callback_data=f"set_sp:{'off' if on else 'on'}")]]
    if slug_src != "env":
        rows.append([InlineKeyboardButton(
            text="🔑 Секретная ссылка: выключить" if slug_src == "bot" else "🔑 Сделать ссылку секретной",
            callback_data=f"sp_secret:{'off' if slug_src == 'bot' else 'on'}")])
    if on and not public_urls.is_public():
        rows.append([InlineKeyboardButton(text="🌍 Как задать адрес", callback_data="setup:url")])
    rows.append([InlineKeyboardButton(text="← Для посетителей", callback_data="menu_visitors")])
    await render(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu_visitors")
async def cb_visitors(call: CallbackQuery):
    """What the bot shows to the people who visit the sites."""
    await ack(call)
    sp = (f"включена: {public_urls.status_page_url()}" if settings.status_page_enabled()
          else "выключена")
    n = await count_feedback()
    await render(call, "🌍 Для посетителей сайтов\n\n"
                       f"🌐 Статус-страница — {esc(sp)}. Публичная страница «всё работает» с историей "
                       f"простоев — её можно дать посетителям.\n"
                       f"📩 Виджет «нашли опечатку?» — сообщения с сайтов приходят сюда"
                       + (f"; получено: {n}." if n else "; пока ни одного."),
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text="🌐 Статус-страница", callback_data="menu_statuspage"),
                      InlineKeyboardButton(text="📩 Сообщения", callback_data="menu_feedback:0")],
                     [InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")]]))


@router.callback_query(F.data == "menu_statuspage")
async def cb_status_page(call: CallbackQuery):
    await ack(call)
    await show_status_page(call)


@router.callback_query(F.data.startswith("sp_secret:"))
async def cb_status_page_secret(call: CallbackQuery):
    on = call.data.endswith(":on")
    if not await ack(call, "Готово"):
        await call.message.answer(STALE_TAP)
        return
    await integrations.set_value("status_page_slug", _secrets.token_urlsafe(8) if on else None)
    await show_status_page(call)
