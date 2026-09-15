"""Main menu (a mini-dashboard), the «Ещё» submenu and the read-only
overview screens: full check, status, SSL, domains, uptime."""

from datetime import timedelta

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
    get_recent_response_times,
    get_uptime_over_days,
    get_uptime_stats,
)
from handlers.common import ack, back_button, render
from monitors.availability import check_all
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.ssl_checker import check_all_ssl
from reports.formatter import format_compact_status_report
from services import maintenance, notifier
from utils.clock import to_local, utcnow
from utils.text import clip, esc, fmt_date, sparkline
from utils.urls import short_host, site_label

router = Router(name="menu")


async def mute_until_local() -> str | None:
    """Human-readable local time the mute expires, or None when not muted."""
    deadline = await notifier.mute_until()
    if not deadline:
        return None
    fmt = "%H:%M" if deadline - utcnow() < timedelta(hours=20) else "%d.%m %H:%M"
    return to_local(deadline).strftime(fmt)


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """The header answers "is everything OK?" before any tap; button labels
    carry live state. All data comes from the DB — opening the menu must
    be instant, no network checks here."""
    sites = await get_all_sites()
    if not sites:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="📋 Ещё", callback_data="menu_more")]])
        return ("👋 Сайтов пока нет. Добавь первый — и я начну проверять его "
                f"каждые {config.check_interval_minutes} мин.", kb)
    ok = total = paused = 0
    for s in sites:
        if await maintenance.is_paused(s["id"]):
            paused += 1
        last = await get_last_check(s["id"], "availability")
        if last:
            total += 1
            ok += last["status"] == "ok"
    incidents = await get_active_incidents()
    mute_until = await mute_until_local()

    site_chip = (f"{'✅' if ok == total else '⚠️'} {ok}/{total} сайтов ок" if total
                 else "⏳ ещё нет проверок")
    inc_chip = f"инцидентов: {len(incidents)}" if incidents else "инцидентов нет"
    mute_chip = f"🔕 тихо до {mute_until}" if mute_until else "🔔 алерты вкл"
    header = f"{site_chip} · {inc_chip} · {mute_chip}"
    if paused:
        header += f" · ⏸ на паузе: {paused}"
    if maintenance.maintenance_now(None):
        header += " · 🔧 тех. окно"
    if notifier.in_quiet_hours():
        header += " · 🌙 тихие часы"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Полная проверка", callback_data="run_full_check")],
        [InlineKeyboardButton(text="📊 Статус", callback_data="menu_status"),
         InlineKeyboardButton(text=f"⚠️ Инциденты ({len(incidents)})" if incidents else "⚠️ Инциденты",
                              callback_data="menu_incidents")],
        [InlineKeyboardButton(text="🌍 Сайт детально", callback_data="menu_check_site"),
         InlineKeyboardButton(text="🔍 SEO/GEO", callback_data="menu_seo")],
        [InlineKeyboardButton(text="📋 Ещё", callback_data="menu_more"),
         InlineKeyboardButton(text=f"🔕 Тихо до {mute_until}" if mute_until else "🔕 Тишина",
                              callback_data="menu_mute")],
    ])
    return header, kb


def more_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔒 SSL", callback_data="menu_ssl"),
         InlineKeyboardButton(text="🌐 Домены", callback_data="menu_domains")],
        [InlineKeyboardButton(text="🔗 Ссылки", callback_data="menu_links"),
         InlineKeyboardButton(text="📈 Uptime", callback_data="menu_uptime")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
         InlineKeyboardButton(text="💓 Heartbeats", callback_data="menu_hb")],
        [InlineKeyboardButton(text="📩 Обратная связь", callback_data="menu_feedback:0"),
         InlineKeyboardButton(text="🩺 Диагностика", callback_data="menu_diag")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
    ])


async def send_main_menu(target: Message | CallbackQuery):
    header, kb = await build_main_menu()
    await render(target, header, kb)


@router.message(Command("menu"))
@router.message(F.text == "📱 Меню")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()  # a menu tap always aborts any pending input flow
    await send_main_menu(message)


@router.callback_query(F.data == "menu_main")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await send_main_menu(call)


@router.callback_query(F.data == "fsm_cancel")
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext):
    await ack(call, "Отменено")
    await state.clear()
    await send_main_menu(call)


@router.callback_query(F.data == "menu_more")
async def cb_more(call: CallbackQuery):
    await ack(call)
    await render(call, "📋 Дополнительные проверки:", more_menu())


# ── Status ───────────────────────────────────────────────────────────────────

_NO_SITES = "Сайтов пока нет — добавь через 📱 Меню → «🌍 Сайт детально»."


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


@router.callback_query(F.data == "menu_status")
async def cb_status(call: CallbackQuery):
    await ack(call)
    urls = await get_active_site_urls()
    if not urls:
        await render(call, "Сайтов пока нет — сначала добавь хотя бы один.", back_button())
        return
    await render(call, "▱▱▱ Проверяю доступность...")
    report = format_compact_status_report(await check_all(urls, manage=False),
                                          await get_active_incidents())
    await render(call, report, back_button())


@router.message(Command("sites"))
async def cmd_sites(message: Message):
    urls = await get_active_site_urls()
    if not urls:
        await message.answer(_NO_SITES)
        return
    await message.answer("🌍 Отслеживаю:\n" + "\n".join(f"  • {esc(u)}" for u in urls))


# ── Full check ───────────────────────────────────────────────────────────────

@router.callback_query(F.data == "run_full_check")
async def cb_full_check(call: CallbackQuery):
    await ack(call)
    urls = await get_active_site_urls()
    if not urls:
        await render(call, "Сайтов пока нет — сначала добавь хотя бы один.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")],
                             [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]]))
        return
    http_urls = await get_active_http_site_urls()
    # manage=False everywhere: interactive checks are read-only observers.
    steps = [
        ("Проверяю доступность сайтов...", lambda: check_all(urls, manage=False)),
        ("Проверяю SSL-сертификаты...", lambda: check_all_ssl(http_urls, manage=False)),
        ("Проверяю домены...", lambda: check_all_domains(http_urls, manage=False)),
        ("Проверяю ссылки на страницах...", lambda: check_all_links(http_urls, manage=False)),
    ]
    results = []
    for i, (label, run) in enumerate(steps):
        progress = "▰" * i + "▱" * (len(steps) - i)
        await render(call, f"🚀 Полная проверка\n\n{progress} {label}")
        results.append(await run())
    availability, ssl_results, domain_results, links_results = results

    report = format_compact_status_report(
        availability=availability, incidents=await get_active_incidents(),
        ssl_results=ssl_results, domain_results=domain_results, report_type="status")
    internal = sum(len(r.broken_internal) for r in links_results)
    external = sum(len(r.broken_external) for r in links_results)
    failed = sum(1 for r in links_results if r.status == "error")
    if failed:
        report += f"\n\n⚠️ Ссылки: не удалось просканировать {failed} из {len(links_results)} сайтов"
    elif internal:
        report += f"\n\n⚠️ Битых внутренних ссылок: {internal} шт. — нажми «🔗 Ссылки» для деталей"
    elif external:
        report += f"\n\n✅ Внутренние ссылки в норме (внешних недоступных: {external}, не критично)"
    else:
        report += "\n\n✅ Все ссылки в норме"
    await render(call, report, back_button())


# ── SSL / domains / uptime ───────────────────────────────────────────────────

@router.callback_query(F.data == "menu_ssl")
async def cb_ssl(call: CallbackQuery):
    await ack(call)
    await render(call, "▱▱▱ Проверяю SSL-сертификаты...")
    lines = ["🔒 SSL-сертификаты:\n"]
    for r in await check_all_ssl(await get_active_http_site_urls(), manage=False):
        if r.ssl_info:
            days = r.ssl_info.days_left
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            lines += [f"{icon} {esc(short_host(r.url))}",
                      f"   Осталось: {days} дн. (до {fmt_date(r.ssl_info.not_after)})",
                      f"   Издатель: {esc(r.ssl_info.issuer)}"]
        else:
            lines.append(f"🔴 {esc(short_host(r.url))}: {esc(r.error or 'N/A')}")
    await render(call, "\n".join(lines), back_button())


@router.callback_query(F.data == "menu_domains")
async def cb_domains(call: CallbackQuery):
    await ack(call)
    await render(call, "▱▱▱ Проверяю домены через RDAP...")
    lines = ["🌐 Домены:\n"]
    for r in await check_all_domains(await get_active_http_site_urls(), manage=False):
        info = r.domain_info
        if info and info.days_left is not None:
            icon = "✅" if info.days_left > 30 else ("⚠️" if info.days_left > 7 else "🔴")
            lines += [f"{icon} {esc(r.domain)}",
                      f"   Осталось: {info.days_left} дн. (до {fmt_date(info.expiration_date)})",
                      f"   Регистратор: {esc(info.registrar)} · {info.source.upper()}"]
        elif r.unsupported:
            lines.append(f"ℹ️ {esc(r.domain)}: срок не отслеживается ({esc(r.error)})")
        else:
            lines.append(f"⚠️ {esc(r.domain or r.url)}: {esc(r.error or 'N/A')}")
    await render(call, "\n".join(lines), back_button())


@router.callback_query(F.data == "menu_uptime")
async def cb_uptime(call: CallbackQuery):
    await ack(call)
    sites = await get_all_sites()
    if not sites:
        await render(call, "Нет данных — бот только запустился, подожди несколько минут.",
                     back_button())
        return
    lines = ["📈 Uptime:\n"]
    for s in sites:
        day = await get_uptime_stats(s["id"], hours=24)
        if not day["total_checks"]:
            lines.append(f"⏳ {esc(site_label(s['url']))} — нет данных")
            continue
        week = await get_uptime_over_days(s["id"], 7)
        month = await get_uptime_over_days(s["id"], 30)
        quarter = await get_uptime_over_days(s["id"], 90)
        pct = day["uptime_pct"]
        icon = "✅" if pct >= 99 else ("⚠️" if pct >= 95 else "🔴")
        spark = sparkline(await get_recent_response_times(s["id"]))
        lines.append(
            f"{icon} {esc(site_label(s['url']))}\n"
            f"   24ч {pct}% · 7д {week['uptime_pct']}% · 30д {month['uptime_pct']}% · "
            f"90д {quarter['uptime_pct']}%\n"
            f"   Среднее время ответа: {day['avg_response_ms']}ms"
            + (f"  {spark}" if spark else "") + f"\n   Проверок за сутки: {day['total_checks']}")
    await render(call, "\n".join(lines), back_button())
