"""⚠️ Incidents: list, close one, clear all, snooze escalation."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_active_incidents, resolve_all_incidents, resolve_incident_by_id
from handlers.common import back_button, render
from utils.clock import fmt_local
from utils.text import esc
from utils.urls import short_host

router = Router(name="incidents")


async def render_incidents(call: CallbackQuery):
    incidents = await get_active_incidents()
    if not incidents:
        await render(call, "✅ Активных инцидентов нет — всё работает нормально!", back_button())
        return
    lines = [f"⚠️ Активные инциденты ({len(incidents)}):\n"]
    rows = []
    for inc in incidents:
        sev = "🔴" if inc["severity"] == "critical" else "⚠️"
        lines.append(f"{sev} {esc(inc['url'])}\n   Тип: {esc(inc['check_type'])}\n"
                     f"   Проблема: {esc(inc['message'])}\n   С: {fmt_local(inc['created_at'])}")
        if len(rows) < 6:
            row = [InlineKeyboardButton(
                text=f"✓ Закрыть: {short_host(inc['url'])} [{inc['check_type']}]",
                callback_data=f"inc_close:{inc['id']}")]
            if inc["check_type"] == "availability" and inc["severity"] == "critical":
                row.append(InlineKeyboardButton(text="👀 2 ч", callback_data=f"ack:{inc['id']}:120"))
            rows.append(row)
    rows.append([InlineKeyboardButton(text="🗑 Сбросить все (если устарели)",
                                      callback_data="incidents_clear")])
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu_incidents")
async def cb_incidents(call: CallbackQuery):
    await call.answer()
    await render_incidents(call)


@router.callback_query(F.data == "incidents_clear")
async def cb_incidents_clear(call: CallbackQuery):
    await call.answer()
    n = await resolve_all_incidents()
    await render(call, f"🗑 Сброшено инцидентов: {n}\n"
                       "При следующей проверке те, что реальны, откроются заново.", back_button())


@router.callback_query(F.data.startswith("inc_close:"))
async def cb_incident_close(call: CallbackQuery):
    arg = call.data.split(":", 1)[1]
    if arg.isdigit() and await resolve_incident_by_id(int(arg)):
        await call.answer("Закрыт ✅")
    else:
        await call.answer("Уже закрыт")
    await render_incidents(call)
