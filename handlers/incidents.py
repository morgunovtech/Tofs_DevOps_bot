"""🔴 Problems: what is broken right now, in plain words, with a way out."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_active_incidents, resolve_all_incidents, resolve_incident_by_id
from handlers.common import ack, back_button, render
from reports.formatter import incident_line
from utils.clock import fmt_local
from utils.text import esc

router = Router(name="incidents")


async def render_incidents(call: CallbackQuery):
    incidents = await get_active_incidents()
    if not incidents:
        await render(call, "✅ Сейчас всё работает — открытых проблем нет.", back_button())
        return
    lines = [f"🔴 Что сейчас не так ({len(incidents)}):\n"]
    rows = []
    for inc in incidents:
        icon = "🔴" if inc["severity"] == "critical" else "⚠️"
        lines.append(f"{icon} {esc(incident_line(inc))}\n   <i>с {fmt_local(inc['created_at'])}</i>")
        if len(rows) < 6:
            row = [InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{inc['id']}")]
            if inc["check_type"] == "availability":
                row.append(InlineKeyboardButton(text="🔧 Я чиню", callback_data=f"act:fix:{inc['site_id']}"))
            row.append(InlineKeyboardButton(text="✓ Закрыть", callback_data=f"inc_close:{inc['id']}"))
            rows.append(row)
    lines.append("\n«Закрыть» убирает проблему из списка; если она настоящая, следующая проверка откроет её снова.")
    rows.append([InlineKeyboardButton(text="🗑 Закрыть все", callback_data="incidents_clear")])
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu_incidents")
async def cb_incidents(call: CallbackQuery):
    await ack(call)
    await render_incidents(call)


@router.callback_query(F.data == "incidents_clear")
async def cb_incidents_clear(call: CallbackQuery):
    await ack(call)
    n = await resolve_all_incidents()
    await render(call, f"🗑 Закрыл {n}. Если что-то из этого всё ещё сломано, следующая проверка "
                       f"откроет проблему заново.", back_button())


@router.callback_query(F.data.startswith("inc_close:"))
async def cb_incident_close(call: CallbackQuery):
    arg = call.data.split(":", 1)[1]
    if arg.isdigit() and await resolve_incident_by_id(int(arg)):
        await ack(call, "Закрыл ✅")
    else:
        await ack(call, "Уже закрыта")
    await render_incidents(call)
