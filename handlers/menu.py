"""The main screen answers «is everything OK?» by itself: one line per
site from the last checks, problems listed inline when there are any, and
three buttons — sites, check now, settings. A fourth («🔴 Проблемы»)
appears only while something is broken."""

import re
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import (
    get_active_http_site_urls,
    get_active_incidents,
    get_active_site_urls,
    get_all_sites,
    get_last_check,
)
from handlers.common import ack, back_button, render
from monitors.availability import check_all
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.ssl_checker import check_all_ssl
from reports.formatter import format_compact_status_report, incident_line
from services import humanize, maintenance, notifier
from utils.clock import to_local, utcnow
from utils.text import clip, esc, fmt_duration, parse_sqlite_utc, plural
from utils.urls import is_http_url, site_label

router = Router(name="menu")

_DAYS_LEFT = re.compile(r"(-?\d+) days left")


async def mute_until_local() -> str | None:
    deadline = await notifier.mute_until()
    if not deadline:
        return None
    fmt = "%H:%M" if deadline - utcnow() < timedelta(hours=20) else "%d.%m %H:%M"
    return to_local(deadline).strftime(fmt)


async def site_state(site: dict) -> tuple[str, str, datetime | None]:
    """(icon, short state, checked_at) for one site from the last stored
    checks — no network, the menu must open instantly."""
    last = await get_last_check(site["id"], "availability")
    if not last:
        return "⏳", "ждёт первой проверки", None
    checked = parse_sqlite_utc(last["checked_at"])
    if last["status"] != "ok":
        return "🔴", humanize.describe_error(last.get("details")), checked
    notes = []
    if is_http_url(site["url"]):
        for kind, word, limit in (("ssl", "сертификат", 14), ("domain", "домен", 30)):
            row = await get_last_check(site["id"], kind)
            m = _DAYS_LEFT.search(row.get("details") or "") if row else None
            if m and int(m.group(1)) <= limit:
                days = int(m.group(1))
                notes.append(f"{word} {'истёк' if days < 0 else f'через {days} дн.'}")
    if notes:
        return "⚠️", ", ".join(notes), checked
    if await maintenance.is_paused(site["id"]):
        return "🔧", "чинится, " + humanize.speed(last.get("response_time_ms")).split(" (")[0], checked
    return "✅", humanize.speed(last.get("response_time_ms")).split(" (")[0], checked


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    sites = await get_all_sites()
    if not sites:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")]])
        return ("👋 Сайтов пока нет. Добавь первый — и я начну проверять его каждые "
                f"{config.check_interval_minutes} мин и напишу, только если что-то сломается.", kb)

    states = [(s, *await site_state(s)) for s in sites]
    checked = [c for *_, c in states if c]
    ok = sum(1 for _, icon, *_ in states if icon in ("✅", "🔧"))
    total = sum(1 for _, icon, *_ in states if icon != "⏳")
    incidents = await get_active_incidents()

    if not total:
        headline = "⏳ Первая проверка через минуту"
    elif incidents:
        headline = (f"{'✅' if ok == total else '⚠️'} {ok}/{total} "
                    f"{plural(total, 'открывается', 'открываются', 'открываются')} · 🔴 проблем: {len(incidents)}")
    else:
        headline = f"{'✅' if ok == total else '⚠️'} {ok}/{total} в порядке"
    if total and checked:
        if True:
            ago = (datetime.now(UTC) - max(checked)).total_seconds() / 60
            headline += f" · проверял {fmt_duration(ago)} назад" if ago >= 1 else " · проверял только что"
    lines = [headline]
    for s, icon, state, _ in states[:10]:
        lines.append(f"{icon} {esc(site_label(s['url']))} · {esc(state)}")
    if len(states) > 10:
        lines.append(f"… и ещё {len(states) - 10}")

    if incidents:
        lines.append("")
        lines += [f"🔴 {esc(incident_line(inc))}" for inc in incidents[:3]]
        if len(incidents) > 3:
            lines.append(f"… и ещё {len(incidents) - 3}")
    chips = []
    mute_until = await mute_until_local()
    if mute_until:
        chips.append(f"🔕 тихо до {mute_until}")
    if maintenance.maintenance_now(None):
        chips.append("🕐 плановые работы")
    if notifier.in_quiet_hours():
        chips.append("🌙 тихие часы")
    if chips:
        lines += ["", " · ".join(chips)]

    rows = [[InlineKeyboardButton(text="🌍 Сайты", callback_data="menu_sites")]]
    if incidents:
        rows.append([InlineKeyboardButton(text=f"🔴 Проблемы ({len(incidents)})", callback_data="menu_incidents")])
    rows.append([InlineKeyboardButton(text="🔎 Проверить всё сейчас", callback_data="run_full_check")])
    rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def send_main_menu(target: Message | CallbackQuery):
    header, kb = await build_main_menu()
    await render(target, header, kb)


@router.message(Command("menu"))
@router.message(F.text == "📱 Меню")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()  # a menu tap always aborts any pending input flow
    await send_main_menu(message)


# Old buttons from earlier layouts still land somewhere sensible.
_LEGACY_MAIN = {"menu_main", "menu_more", "menu_health", "menu_connections",
                "menu_uptime", "menu_ssl", "menu_domains", "menu_links", "menu_seo", "menu_status"}


@router.callback_query(F.data.in_(_LEGACY_MAIN))
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await send_main_menu(call)


@router.callback_query(F.data == "fsm_cancel")
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext):
    await ack(call, "Отменено")
    await state.clear()
    await send_main_menu(call)


# ── Check everything now ─────────────────────────────────────────────────────

_NO_SITES = "Сайтов пока нет — добавь через 📱 Меню → «🌍 Сайты»."


@router.message(Command("status"))
@router.message(F.text == "📊 Статус")
async def cmd_status(message: Message, state: FSMContext):
    await state.clear()
    urls = await get_active_site_urls()
    if not urls:
        await message.answer(_NO_SITES)
        return
    progress = await message.answer("▱▱▱ Проверяю...")
    report = format_compact_status_report(await check_all(urls, manage=False),
                                          await get_active_incidents())
    await progress.edit_text(clip(report), reply_markup=back_button())


@router.message(Command("sites"))
async def cmd_sites(message: Message):
    urls = await get_active_site_urls()
    if not urls:
        await message.answer(_NO_SITES)
        return
    await message.answer("🌍 Слежу за:\n" + "\n".join(f"  • {esc(u)}" for u in urls))


@router.callback_query(F.data == "run_full_check")
async def cb_check_now(call: CallbackQuery):
    """Availability first (fast), then certificates, domains and links are
    folded into the same message as they arrive."""
    await ack(call)
    urls = await get_active_site_urls()
    if not urls:
        await render(call, "Сайтов пока нет — сначала добавь хотя бы один.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")],
                             [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]]))
        return
    http_urls = await get_active_http_site_urls()
    # manage=False everywhere: interactive checks are read-only observers.
    await render(call, "▱▱▱ Проверяю, открываются ли сайты...")
    availability = await check_all(urls, manage=False)
    incidents = await get_active_incidents()
    quick = format_compact_status_report(availability, incidents)
    if not http_urls:
        await render(call, quick, back_button())
        return
    await render(call, quick + "\n\n▰▱▱ Проверяю сертификаты и домены...")
    ssl_results = await check_all_ssl(http_urls, manage=False)
    domain_results = await check_all_domains(http_urls, manage=False)
    report = format_compact_status_report(availability, incidents, ssl_results, domain_results)
    await render(call, report + "\n\n▰▰▱ Проверяю ссылки на страницах...")
    links_results = await check_all_links(http_urls, manage=False)
    internal = sum(len(r.broken_internal) for r in links_results)
    external = sum(len(r.broken_external) for r in links_results)
    failed = sum(1 for r in links_results if r.status == "error")
    if failed:
        report += f"\n\n⚠️ Ссылки: не смог проверить {failed} из {len(links_results)} сайтов"
    elif internal:
        report += (f"\n\n⚠️ {internal} {plural(internal, 'ссылка ведёт', 'ссылки ведут', 'ссылок ведут')} "
                   f"в никуда — подробности в карточке сайта («🌍 Сайты» → сайт → «🔗 Ссылки»)")
    elif external:
        report += f"\n\n✅ Свои ссылки в порядке (не открываются {external} чужих — обычно не важно)"
    else:
        report += "\n\n✅ Все ссылки в порядке"
    await render(call, report, back_button())
