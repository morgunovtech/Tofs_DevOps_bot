"""⚙️ Global settings: report hours (morning, evening, weekly), quiet hours,
public status page."""

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render
from reports.scheduler import reschedule_report_jobs
from services import maintenance, public_urls, settings
from utils.text import esc

router = Router(name="settings")


def _kb() -> InlineKeyboardMarkup:
    sp_on = settings.status_page_enabled()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌅 {h}:00", callback_data=f"set_m:{h}") for h in (7, 8, 9, 10)],
        [InlineKeyboardButton(text=f"🌙 {h}:00", callback_data=f"set_e:{h}") for h in (20, 21, 22)]
        + [InlineKeyboardButton(text="🌙 выкл", callback_data="set_e:off")],
        [InlineKeyboardButton(text=f"📊 вс {h}:00", callback_data=f"set_w:{h}") for h in (10, 11, 12, 18)],
        [InlineKeyboardButton(text="🔕 23–8", callback_data="set_q:23-8"),
         InlineKeyboardButton(text="🔕 0–7", callback_data="set_q:0-7"),
         InlineKeyboardButton(text="🔕 выкл", callback_data="set_q:off")],
        [InlineKeyboardButton(text="🔧 Тех. окна", callback_data="menu_maint"),
         InlineKeyboardButton(text="🌐 Статус: выключить" if sp_on else "🌐 Статус: включить",
                              callback_data=f"set_sp:{'off' if sp_on else 'on'}")],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_more")]])


def _text() -> str:
    evening, quiet = settings.evening_hour(), settings.quiet_hours()
    sp = (f"вкл · {public_urls.status_page_url()}" if settings.status_page_enabled() else "выключена")
    return ("⚙️ Настройки\n\n"
            f"🌅 Утренний отчёт: {settings.morning_hour()}:00\n"
            f"🌙 Вечерний отчёт: {'выключен' if evening is None else f'{evening}:00 (только при инцидентах)'}\n"
            f"📊 Недельный отчёт: воскресенье {settings.weekly_hour()}:00\n"
            f"🔕 Тихие часы: {'выключены' if not quiet else f'{quiet[0]}–{quiet[1]}'}\n"
            f"🔧 Тех. окна: {len(maintenance.windows()) or 'нет'}\n"
            f"🌐 Статус-страница: {esc(sp)}\n\nТапни, чтобы изменить:")


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    await ack(call)
    await render(call, _text(), _kb())


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
        await render(call, _text(), _kb())
    except TelegramBadRequest:
        pass
