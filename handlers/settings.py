"""⚙️ Settings: the hub for everything that is not «is my site OK» —
digests and quiet hours, mute, maintenance windows, status page,
connections, task control, feedback, export/import, test alert."""

import secrets as _secrets

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from handlers.common import ack, back_button, cancel_kb, render
from handlers.menu import mute_until_local
from reports.scheduler import reschedule_all
from services import integrations, maintenance, public_urls, settings
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
    evening, quiet = settings.evening_hour(), settings.quiet_hours()
    mute = await mute_until_local()
    sp = f"вкл · {public_urls.status_page_url()}" if settings.status_page_enabled() else "выключена"
    ring = "только «сайт лёг»" if settings.ring_only_down() else "всё критичное"
    return ("⚙️ Настройки\n\n"
            f"🌅 Утренняя сводка {settings.morning_hour()}:00 · "
            f"🌙 вечерняя {'выкл' if evening is None else f'{evening}:00'} · "
            f"📊 недельная вс {settings.weekly_hour()}:00\n"
            f"🔕 Тихие часы: {'выключены' if not quiet else f'{quiet[0]}–{quiet[1]}'} · "
            f"{'🔕 тихо до ' + mute if mute else '🔔 алерты включены'} · будить: {ring}\n"
            f"🕐 Часовой пояс: {esc(settings.timezone())}, сейчас {now_local().strftime('%H:%M')}\n"
            f"🗓 Плановые работы: {len(maintenance.windows()) or 'нет'} · "
            f"🌐 Статус-страница: {esc(sp)}")


def hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 Сводки и тихие часы", callback_data="menu_reports"),
         InlineKeyboardButton(text="🔕 Заглушить на время", callback_data="menu_mute")],
        [InlineKeyboardButton(text="🕐 Часовой пояс", callback_data="menu_tz"),
         InlineKeyboardButton(text="🗓 Плановые работы", callback_data="menu_maint")],
        [InlineKeyboardButton(text="🌐 Статус-страница", callback_data="menu_statuspage")],
        [InlineKeyboardButton(text="🩺 Диагностика и подключения", callback_data="menu_diag"),
         InlineKeyboardButton(text="⏰ Контроль задач", callback_data="menu_hb")],
        [InlineKeyboardButton(text="📩 Обратная связь", callback_data="menu_feedback:0"),
         InlineKeyboardButton(text="🔔 Тест алерта", callback_data="menu_testalert")],
        [InlineKeyboardButton(text="📤 Экспорт сайтов", callback_data="site_export"),
         InlineKeyboardButton(text="📥 Импорт", callback_data="site_import")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]])


async def show_hub(target):
    await render(target, await hub_text(), hub_kb())


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    await ack(call)
    await show_hub(call)


# ── Digests and quiet hours ──────────────────────────────────────────────────

def _reports_kb() -> InlineKeyboardMarkup:
    ring_on = settings.ring_only_down()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Будить: только «сайт лёг»" if ring_on else "🔔 Будить: всё критичное",
                              callback_data=f"set_ring:{'off' if ring_on else 'on'}")],
        [InlineKeyboardButton(text=f"🌅 {h}:00", callback_data=f"set_m:{h}") for h in (7, 8, 9, 10)],
        [InlineKeyboardButton(text=f"🌙 {h}:00", callback_data=f"set_e:{h}") for h in (20, 21, 22)]
        + [InlineKeyboardButton(text="🌙 выкл", callback_data="set_e:off")],
        [InlineKeyboardButton(text=f"📊 вс {h}:00", callback_data=f"set_w:{h}") for h in (10, 11, 12, 18)],
        [InlineKeyboardButton(text="🔕 23–8", callback_data="set_q:23-8"),
         InlineKeyboardButton(text="🔕 0–7", callback_data="set_q:0-7"),
         InlineKeyboardButton(text="🔕 выкл", callback_data="set_q:off")],
        [InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")]])


def _reports_text() -> str:
    evening, quiet = settings.evening_hour(), settings.quiet_hours()
    return ("🌅 Сводки и тихие часы\n\n"
            f"🌅 Утренняя сводка: {settings.morning_hour()}:00 — одно короткое «всё в порядке» "
            f"или список того, что не так.\n"
            f"🌙 Вечерняя: {'выключена' if evening is None else f'{evening}:00, только если есть проблемы'}.\n"
            f"📊 Недельная: воскресенье {settings.weekly_hour()}:00 — простой за неделю и месяц, "
            f"график, одна идея.\n"
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
    if kind == "set_m" and value.isdigit():
        await settings.set_morning_hour(int(value))
    elif kind == "set_e" and (value == "off" or value.isdigit()):
        await settings.set_evening_hour(None if value == "off" else int(value))
    elif kind == "set_w" and value.isdigit():
        await settings.set_weekly_hour(int(value))
    elif kind == "set_q" and (value == "off" or "-" in value):
        await settings.set_quiet_hours(None if value == "off" else value)
    elif kind == "set_sp" and value in ("on", "off"):
        await settings.set_status_page(value == "on")
    elif kind == "set_ring" and value in ("on", "off"):
        await settings.set_ring_only_down(value == "on")
    elif kind == "set_tz" and value.isdigit() and int(value) < len(TIMEZONES):
        await settings.set_timezone(TIMEZONES[int(value)][1])
    elif kind == "set_tz" and value == "env":
        await settings.set_timezone(None)
    else:
        await ack(call, "Не понял", show_alert=True)
        return
    reschedule_all()  # apply new hours / timezone to the running scheduler now
    await ack(call, "Сохранено ✅")
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
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    await render(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu_statuspage")
async def cb_status_page(call: CallbackQuery):
    await ack(call)
    await show_status_page(call)


@router.callback_query(F.data.startswith("sp_secret:"))
async def cb_status_page_secret(call: CallbackQuery):
    on = call.data.endswith(":on")
    await integrations.set_value("status_page_slug", _secrets.token_urlsafe(8) if on else None)
    await ack(call, "Готово")
    await show_status_page(call)
