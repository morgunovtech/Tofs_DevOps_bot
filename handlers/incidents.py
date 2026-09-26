"""Problems: what is open right now, in plain words, each with its level
(🔴 act now · 🟠 act, but not today) and a way out — «ℹ️ Что делать»
opens what it means, what it usually is and the steps."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_active_incidents, get_incident, resolve_all_incidents, resolve_incident_by_id
from handlers.common import ack, back_button, render
from reports.formatter import incident_line
from services import humanize, sitestatus
from utils.clock import fmt_local
from utils.text import esc

router = Router(name="incidents")


async def render_incidents(call: CallbackQuery):
    incidents = await get_active_incidents()
    if not incidents:
        await render(call, "✅ Сейчас всё работает — открытых проблем нет.", back_button())
        return
    worst = humanize.worst_severity(i["severity"] for i in incidents)
    lines = [f"{humanize.level_icon(worst)} Что сейчас не так ({len(incidents)}):\n"]
    rows = []
    for inc in incidents:
        lines.append(f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}\n"
                     f"   <i>с {fmt_local(inc['created_at'])}</i>")
        if len(rows) < 6:
            row = [InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{inc['id']}")]
            if inc["check_type"] == "availability":
                row.append(InlineKeyboardButton(text="🔧 Я чиню", callback_data=f"act:fix:{inc['site_id']}"))
            row.append(InlineKeyboardButton(text="✓ Закрыть", callback_data=f"inc_close:{inc['id']}"))
            rows.append(row)
    lines.append("\n🔴 — нужно действие сейчас, 🟠 — не сегодня. «Закрыть» убирает проблему из списка; "
                 "если она настоящая, следующая проверка откроет её снова.")
    rows.append([InlineKeyboardButton(text="🗑 Закрыть все", callback_data="incidents_clear")])
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def explain_text(inc: dict) -> str:
    """«ℹ️ Что делать» for one incident: what it means for visitors, what it
    usually is, the steps — from services.humanize, by check type and level."""
    expl = humanize.explain(inc["check_type"], inc.get("message"))
    when = (f"закрыта {fmt_local(inc.get('resolved_at'))}" if inc.get("resolved")
            else f"с {fmt_local(inc['created_at'])}")
    lines = [f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}", f"<i>{when}</i>", "",
             esc(expl.meaning), "", f"Что это обычно значит: {esc(expl.cause)}.", "",
             esc(humanize.steps_block(expl))]
    if inc.get("resolved"):
        lines += ["", "<i>Эта проблема уже закрыта — шаги на случай, если повторится.</i>"]
    return "\n".join(lines)


async def explain_keyboard(inc: dict) -> InlineKeyboardMarkup:
    """The screen that fixes it, the registrar's panel, closing — by type."""
    sid, kind = inc["site_id"], inc["check_type"]
    rows: list[list[InlineKeyboardButton]] = []
    if kind == "seo":
        rows.append([InlineKeyboardButton(text="🔎 Поиск и ИИ", callback_data=f"seo_site:{sid}")])
    elif kind == "links":
        rows.append([InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"check_links:{sid}")])
    elif kind == "availability":
        rows.append([InlineKeyboardButton(text="🔧 Я чиню, час тишины", callback_data=f"act:fix:{sid}"),
                     InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data=f"act:recheck:{sid}")])
    elif kind == "domain":
        registrar = ((await sitestatus.get(sid)).get("domain") or {}).get("registrar")
        url = humanize.registrar_url(registrar)
        if url:
            rows.append([InlineKeyboardButton(text=f"🔗 Открыть панель {registrar}", url=url)])
    last = [InlineKeyboardButton(text="🌍 К сайту", callback_data=f"check_site:{sid}")]
    if not inc.get("resolved"):
        last.append(InlineKeyboardButton(text="✓ Закрыть", callback_data=f"inc_close:{inc['id']}"))
    rows.append(last)
    rows.append([InlineKeyboardButton(text="← Проблемы", callback_data="menu_incidents")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_incidents")
async def cb_incidents(call: CallbackQuery):
    await ack(call)
    await render_incidents(call)


@router.callback_query(F.data.startswith("inc_explain:"))
async def cb_incident_explain(call: CallbackQuery):
    """The button lives on alerts too, so it must work for a closed incident
    and for a message the bot can no longer edit (then it answers below)."""
    arg = call.data.split(":", 1)[1]
    inc = await get_incident(int(arg)) if arg.isdigit() else None
    if not inc:
        await ack(call, "Этой проблемы уже нет в списке", show_alert=True)
        return
    await ack(call)
    text, kb = explain_text(inc), await explain_keyboard(inc)
    markup = getattr(call.message, "reply_markup", None)
    on_alert = bool(markup) and any((b.callback_data or "").startswith("act:")
                                    for row in markup.inline_keyboard for b in row)
    if on_alert:
        await call.message.answer(text, reply_markup=kb)   # keep the alert text and its buttons intact
    else:
        await render(call, text, kb)


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
