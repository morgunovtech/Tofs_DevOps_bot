"""⚙️ Settings: the hub for everything that is not «is my site OK» —
digests and quiet hours, mute, maintenance windows, status page,
connections, task control, feedback, export/import, test alert."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render
from handlers.menu import mute_until_local
from reports.scheduler import reschedule_report_jobs
from services import maintenance, public_urls, settings
from utils.text import esc

router = Router(name="settings")


async def hub_text() -> str:
    evening, quiet = settings.evening_hour(), settings.quiet_hours()
    mute = await mute_until_local()
    sp = f"вкл · {public_urls.status_page_url()}" if settings.status_page_enabled() else "выключена"
    return ("⚙️ Настройки\n\n"
            f"🌅 Утренняя сводка {settings.morning_hour()}:00 · "
            f"🌙 вечерняя {'выкл' if evening is None else f'{evening}:00'} · "
            f"📊 недельная вс {settings.weekly_hour()}:00\n"
            f"🔕 Тихие часы: {'выключены' if not quiet else f'{quiet[0]}–{quiet[1]}'} · "
            f"{'🔕 тихо до ' + mute if mute else '🔔 алерты включены'}\n"
            f"🕐 Плановые работы: {len(maintenance.windows()) or 'нет'} · "
            f"🌐 Статус-страница: {esc(sp)}")


def hub_kb() -> InlineKeyboardMarkup:
    sp_on = settings.status_page_enabled()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌅 Сводки и тихие часы", callback_data="menu_reports"),
         InlineKeyboardButton(text="🔕 Заглушить на время", callback_data="menu_mute")],
        [InlineKeyboardButton(text="🕐 Плановые работы", callback_data="menu_maint"),
         InlineKeyboardButton(text="🌐 Статус: выключить" if sp_on else "🌐 Статус: включить",
                              callback_data=f"set_sp:{'off' if sp_on else 'on'}")],
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
    return InlineKeyboardMarkup(inline_keyboard=[
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
            f"ждёт до утра, «сайт лежит» приходит всегда.\n\nТапни, чтобы изменить:")


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
    else:
        await ack(call, "Не понял", show_alert=True)
        return
    reschedule_report_jobs()  # apply new hours to the running scheduler now
    await ack(call, "Сохранено ✅")
    try:
        if kind == "set_sp":
            await show_hub(call)
        else:
            await render(call, _reports_text(), _reports_kb())
    except TelegramBadRequest:
        pass
