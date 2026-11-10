"""Problems: what is open right now, in plain words, each with its level
(🔴 act now · 🟠 act, but not today) and a way out — «ℹ️ Что делать»
opens what it means, what it usually is and the steps."""

import time

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_active_incidents, get_incident, get_site, resolve_incident_by_id, set_state
from handlers.common import STALE_TAP, ack, back_button, render
from monitors.availability import check_all, check_availability
from reports.formatter import incident_line
from reports.scheduler import ACK_UNTIL_UP, forget_slow_streak
from services import crux, humanize, sitestatus
from utils.clock import fmt_local
from utils.text import esc
from utils.urls import site_label

router = Router(name="incidents")

# «🗑 Да, закрыть все» older than this is not carried out: a tap that waited
# in Telegram's queue while the bot was down must not close anything.
CONFIRM_TTL_SEC = 600


async def render_incidents(call: CallbackQuery, note: str = ""):
    """The list: every problem numbered, a «ℹ️ N» button for each (four in a
    row) — «🔧 Я чиню» and «✓ Закрыть» live on its «ℹ️» screen."""
    incidents = await get_active_incidents()
    head = f"{note}\n\n" if note else ""
    if not incidents:
        await render(call, f"{head}✅ Сейчас всё работает — открытых проблем нет.", back_button())
        return
    worst = humanize.worst_severity(i["severity"] for i in incidents)
    lines = [f"{head}{humanize.level_icon(worst)} Что сейчас не так ({len(incidents)}):\n"]
    numbered = []
    for n, inc in enumerate(incidents, 1):
        lines.append(f"{n}. {humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}\n"
                     f"   <i>с {fmt_local(inc.get('started_at') or inc['created_at'])}</i>")
        # «:l» — a button of this list, never mistaken for one on an alert.
        numbered.append(InlineKeyboardButton(text=f"ℹ️ {n}", callback_data=f"inc_explain:{inc['id']}:l"))
    lines.append("\nНомер — что это значит и что делать; там же «🔧 Я чиню» и «✓ Закрыть». "
                 "🔴 — нужно действие сейчас, 🟠 — не сегодня.")
    rows = [numbered[i:i + 4] for i in range(0, len(numbered), 4)]
    rows.append([InlineKeyboardButton(text="🗑 Закрыть все", callback_data="incidents_clear")])
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def explain_text(inc: dict) -> str:
    """«ℹ️ Что делать» for one incident: what it means for visitors, what it
    usually is, the steps — from services.humanize, by check type and level."""
    expl = humanize.explain(inc["check_type"], inc.get("message"))
    when = (f"закрыта {fmt_local(inc.get('resolved_at'))}" if inc.get("resolved")
            else f"с {fmt_local(inc.get('started_at') or inc['created_at'])}")
    head = (f"✅ Было: {esc(incident_line(inc))}" if inc.get("resolved")
            else f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}")
    lines = [head, f"<i>{when}</i>", "",
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
    elif kind == "availability" and not inc.get("resolved"):     # «🔧» would pause a working site
        rows.append([InlineKeyboardButton(text="🔧 Я чиню, час тишины", callback_data=f"act:fix:{sid}"),
                     InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data=f"act:recheck:{sid}")])
    elif kind in ("mail", "mail_auth"):
        rows.append([InlineKeyboardButton(text="✉️ Почта", callback_data=f"mail:{sid}")])
    elif kind in ("exposed", "security"):
        rows.append([InlineKeyboardButton(text="🛡 Безопасность", callback_data=f"sec:{sid}")])
    elif kind == "visitors":
        rows.append([InlineKeyboardButton(text="👥 Скорость у посетителей", callback_data=f"spd:{sid}"),
                     InlineKeyboardButton(text="↗️ PageSpeed", url=crux.pagespeed_url(inc["url"]))])
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
    parts = call.data.split(":")
    arg = parts[1] if len(parts) > 1 else ""
    inc = await get_incident(int(arg)) if arg.isdigit() else None
    if not inc:
        await ack(call, "Этой проблемы уже нет в списке", show_alert=True)
        return
    await ack(call)
    text, kb = explain_text(inc), await explain_keyboard(inc)
    # «:a» marks a button on an alert, «:l» one on the problems list; alerts
    # sent before the markers are recognised by their «act:» buttons.
    markup = getattr(call.message, "reply_markup", None)
    on_alert = parts[-1] == "a" or (parts[-1] != "l" and bool(markup) and any(
        (b.callback_data or "").startswith("act:") for row in markup.inline_keyboard for b in row))
    if on_alert:
        await call.message.answer(text, reply_markup=kb)   # keep the alert text and its buttons intact
    else:
        await render(call, text, kb)


async def _close_by_hand(inc: dict) -> bool:
    """Close one incident from the screen. Its SSL/domain ladder starts over
    (resolve_incident_by_id) and a «медленно» streak is counted anew, so a
    problem that is still there is said again by its next check."""
    if not await resolve_incident_by_id(inc["id"]):
        return False
    if inc["check_type"] == "performance":
        forget_slow_streak(inc["site_id"])
    return True


@router.callback_query(F.data == "incidents_clear")
async def cb_incidents_clear(call: CallbackQuery):
    """«🗑 Закрыть все» asks first: it sits one tap away from the «ℹ️» buttons."""
    await ack(call)
    n = len(await get_active_incidents())
    if not n:
        await render_incidents(call)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, закрыть все", callback_data=f"incidents_clear:ok:{int(time.time())}")],
        [InlineKeyboardButton(text="← Отмена", callback_data="menu_incidents")]])
    await render(call, f"Закрыть все открытые проблемы ({n})?\n\nЛежащие сайты я сначала проверю: что всё ещё "
                       f"не открывается, останется в списке. Остальное, если оно настоящее, следующая проверка "
                       f"откроет снова — 🔴 со звонком.", kb)


@router.callback_query(F.data.startswith("incidents_clear:ok:"))
async def cb_incidents_clear_confirmed(call: CallbackQuery):
    """«🗑 Да, закрыть все»: the sites that are down are checked live first
    and the ones still down stay on the list — closing them would only bring
    the same alert back, with a ring and one more rebuild."""
    stamp = call.data.rsplit(":", 1)[1]
    if not stamp.isdigit() or time.time() - int(stamp) > CONFIRM_TTL_SEC:
        await ack(call, "Подтверждение устарело — открой список и нажми ещё раз", show_alert=True)
        return
    if not await ack(call, "Проверяю лежащие сайты…"):
        await call.message.answer(STALE_TAP)
        return
    incidents = await get_active_incidents()
    down_urls = sorted({i["url"] for i in incidents if i["check_type"] == "availability"})
    still_down = {r.url for r in await check_all(down_urls, manage=False) if not r.ok} if down_urls else set()
    closed = 0
    for inc in incidents:
        if inc["check_type"] == "availability" and inc["url"] in still_down:
            continue
        if await _close_by_hand(inc):
            closed += 1
    note = f"🗑 Закрыл {closed}."
    if still_down:
        note += (" Не закрыл — всё ещё не открываются: "
                 + ", ".join(esc(site_label(u)) for u in sorted(still_down)) + ".")
    await render_incidents(call, note)


@router.callback_query(F.data.startswith("inc_close:"))
async def cb_incident_close(call: CallbackQuery):
    """«✓ Закрыть». A site that is still down is not closed: its next check
    would open it again at once — with a ring and, with AUTO_REDEPLOY, one
    more rebuild. «🔧 Я чиню» is the way to make the bot keep quiet."""
    arg = call.data.split(":", 1)[1]
    inc = await get_incident(int(arg)) if arg.isdigit() else None
    if not inc or inc["resolved"]:
        await ack(call, "Уже закрыта")
        await render_incidents(call)
        return
    site = await get_site(inc["site_id"])
    live = inc["check_type"] == "availability" and bool(site and site["active"])
    if not await ack(call, "Проверяю, открывается ли…" if live else "Закрыл ✅"):
        await call.message.answer(STALE_TAP)
        return
    note = ""
    if live:
        r = await check_availability(inc["url"], manage=False)
        if not r.ok:
            await render(call, f"🔴 Не закрываю: {esc(site_label(inc['url']))} всё ещё не открывается "
                               f"({esc(humanize.describe_error(r.error))}). Закрою сам, когда поднимется; "
                               f"чинишь — нажми «🔧 Я чиню», и я замолчу.\n\n{explain_text(inc)}",
                         await explain_keyboard(inc))
            return
        note = f"✅ Закрыл: {esc(site_label(inc['url']))} открывается."
    await _close_by_hand(inc)
    await render_incidents(call, note)


@router.callback_query(F.data.startswith("inc_quiet:"))
async def cb_incident_quiet(call: CallbackQuery):
    """«👀 Знаю, не напоминать» under an escalation: no more reminders about
    this incident; the «✅ поднялся» still comes. The site is NOT paused."""
    arg = call.data.split(":")[1]
    inc = await get_incident(int(arg)) if arg.isdigit() else None
    if not inc or inc.get("resolved"):
        await ack(call, "Эта проблема уже закрыта", show_alert=True)
        return
    if not await ack(call, "Не напоминаю"):
        await call.message.answer(STALE_TAP)
        return
    await set_state(f"ack:{inc['id']}", ACK_UNTIL_UP)
    await call.message.answer(f"👀 Понял: больше не напоминаю про {esc(incident_line(inc))}. "
                              f"Когда поднимется — напишу.")
