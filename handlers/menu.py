"""Main menu (a mini-dashboard), the three group screens and the read-only
overview screens: check now, availability, certificates, domains."""

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
    get_uptime_over_days,
    get_uptime_stats,
)
from handlers.common import ack, back_button, render
from monitors.availability import check_all
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.ssl_checker import check_all_ssl
from reports.formatter import format_compact_status_report
from services import humanize, maintenance, notifier
from utils.clock import to_local, utcnow
from utils.text import clip, esc, fmt_date, plural
from utils.urls import site_label

router = Router(name="menu")


async def mute_until_local() -> str | None:
    deadline = await notifier.mute_until()
    if not deadline:
        return None
    fmt = "%H:%M" if deadline - utcnow() < timedelta(hours=20) else "%d.%m %H:%M"
    return to_local(deadline).strftime(fmt)


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """The header answers "is everything OK?" before any tap. All data comes
    from the DB — opening the menu must be instant, no network checks."""
    sites = await get_all_sites()
    if not sites:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="🔌 Подключения", callback_data="menu_connections")]])
        return ("👋 Сайтов пока нет. Добавь первый — и я начну проверять его каждые "
                f"{config.check_interval_minutes} мин и напишу, только если что-то сломается.", kb)
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

    # Only what differs from the calm default earns a chip: «✅ 3/3 в порядке»
    # is the whole header on a good day.
    if not total:
        chips = ["⏳ первая проверка через минуту"]
    elif incidents:
        chips = [f"{'✅' if ok == total else '⚠️'} {ok}/{total} "
                 f"{plural(total, 'открывается', 'открываются', 'открываются')}",
                 f"🔴 проблем: {len(incidents)}"]
    else:
        chips = [f"{'✅' if ok == total else '⚠️'} {ok}/{total} в порядке"]
    if mute_until:
        chips.append(f"🔕 тихо до {mute_until}")
    if paused:
        chips.append(f"🔧 чинится: {paused}")
    if maintenance.maintenance_now(None):
        chips.append("🕐 плановые работы")
    if notifier.in_quiet_hours():
        chips.append("🌙 тихие часы")
    header = " · ".join(chips)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔎 Проверить сейчас", callback_data="run_full_check")],
        [InlineKeyboardButton(text=f"🔴 Проблемы ({len(incidents)})" if incidents else "🔴 Проблемы",
                              callback_data="menu_incidents"),
         InlineKeyboardButton(text="🌍 Мои сайты", callback_data="menu_check_site")],
        [InlineKeyboardButton(text="📈 Здоровье сайтов", callback_data="menu_health"),
         InlineKeyboardButton(text="🔍 Поиск и ИИ", callback_data="menu_seo")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings"),
         InlineKeyboardButton(text="🔌 Подключения", callback_data="menu_connections")],
        [InlineKeyboardButton(text=f"🔕 Тихо до {mute_until}" if mute_until else "🔕 Тишина",
                              callback_data="menu_mute")],
    ])
    return header, kb


def health_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Доступность", callback_data="menu_uptime"),
         InlineKeyboardButton(text="🔒 Сертификаты", callback_data="menu_ssl")],
        [InlineKeyboardButton(text="🌐 Домены", callback_data="menu_domains"),
         InlineKeyboardButton(text="🔗 Битые ссылки", callback_data="menu_links")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]])


def connections_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🩺 Диагностика и подключения", callback_data="menu_diag")],
        [InlineKeyboardButton(text="⏰ Контроль задач", callback_data="menu_hb"),
         InlineKeyboardButton(text="📩 Обратная связь", callback_data="menu_feedback:0")],
        [InlineKeyboardButton(text="🔔 Тест алерта", callback_data="menu_testalert")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]])


async def send_main_menu(target: Message | CallbackQuery):
    header, kb = await build_main_menu()
    await render(target, header, kb)


@router.message(Command("menu"))
@router.message(F.text == "📱 Меню")
async def cmd_menu(message: Message, state: FSMContext):
    await state.clear()  # a menu tap always aborts any pending input flow
    await send_main_menu(message)


@router.callback_query(F.data.in_({"menu_main", "menu_more"}))
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await send_main_menu(call)


@router.callback_query(F.data == "fsm_cancel")
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext):
    await ack(call, "Отменено")
    await state.clear()
    await send_main_menu(call)


@router.callback_query(F.data == "menu_health")
async def cb_health(call: CallbackQuery):
    await ack(call)
    await render(call, "📈 Здоровье сайтов — что посмотреть:", health_menu())


@router.callback_query(F.data == "menu_connections")
async def cb_connections(call: CallbackQuery):
    await ack(call)
    await render(call, "🔌 Подключения и служебное:", connections_menu())


# ── Check now ────────────────────────────────────────────────────────────────

_NO_SITES = "Сайтов пока нет — добавь через 📱 Меню → «🌍 Мои сайты»."


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


@router.callback_query(F.data.in_({"run_full_check", "menu_status"}))
async def cb_check_now(call: CallbackQuery):
    """One button: availability first (fast), then certificates, domains and
    links are folded into the same message as they arrive."""
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
                   f"в никуда — подробности в «📈 Здоровье сайтов → 🔗 Битые ссылки»")
    elif external:
        report += f"\n\n✅ Свои ссылки в порядке (не открываются {external} чужих — обычно не важно)"
    else:
        report += "\n\n✅ Все ссылки в порядке"
    await render(call, report, back_button())


# ── Certificates / domains / availability ────────────────────────────────────

@router.callback_query(F.data == "menu_ssl")
async def cb_ssl(call: CallbackQuery):
    await ack(call)
    await render(call, "▱▱▱ Проверяю сертификаты...")
    lines = ["🔒 Сертификаты безопасности (HTTPS):\n"]
    for r in await check_all_ssl(await get_active_http_site_urls(), manage=False):
        if r.ssl_info:
            days = r.ssl_info.days_left
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            issuer = f", выдан {esc(r.ssl_info.issuer)}" if r.ssl_info.issuer else ""
            lines.append(f"{icon} {esc(site_label(r.url))} — действует ещё {days} дн. "
                         f"(до {fmt_date(r.ssl_info.not_after)}){issuer}")
        else:
            lines.append(f"🔴 {esc(site_label(r.url))} — {esc(humanize.describe_error(r.error))}")
    lines.append("\nСертификат обычно продлевается сам за 2–4 недели до срока; если нет — я предупрежу.")
    await render(call, "\n".join(lines), back_button("← Здоровье сайтов", "menu_health"))


@router.callback_query(F.data == "menu_domains")
async def cb_domains(call: CallbackQuery):
    await ack(call)
    await render(call, "▱▱▱ Узнаю сроки доменов...")
    lines = ["🌐 Домены и сроки оплаты:\n"]
    for r in await check_all_domains(await get_active_http_site_urls(), manage=False):
        info = r.domain_info
        if info and info.days_left is not None:
            icon = "✅" if info.days_left > 30 else ("⚠️" if info.days_left > 7 else "🔴")
            registrar = f", регистратор {esc(info.registrar)}" if info.registrar not in ("", "Unknown") else ""
            lines.append(f"{icon} {esc(r.domain)} — оплачен до {fmt_date(info.expiration_date)} "
                         f"(ещё {info.days_left} дн.){registrar}")
        elif r.unsupported:
            lines.append(f"ℹ️ {esc(r.domain)} — срок узнать нельзя: {esc(r.error)}")
        else:
            lines.append(f"⚠️ {esc(r.domain or r.url)} — {esc(humanize.describe_error(r.error))}")
    await render(call, "\n".join(lines), back_button("← Здоровье сайтов", "menu_health"))


@router.callback_query(F.data == "menu_uptime")
async def cb_uptime(call: CallbackQuery):
    await ack(call)
    sites = await get_all_sites()
    if not sites:
        await render(call, "Нет данных — бот только запустился, подожди несколько минут.",
                     back_button("← Здоровье сайтов", "menu_health"))
        return
    lines = ["📈 Доступность:\n"]
    for s in sites:
        day = await get_uptime_stats(s["id"], hours=24)
        label = esc(site_label(s["url"]))
        if not day["total_checks"]:
            lines.append(f"⏳ {label} — ещё нет данных")
            continue
        interval = s.get("check_interval_min") or config.check_interval_minutes
        week = await get_uptime_over_days(s["id"], 7)
        month = await get_uptime_over_days(s["id"], 30)
        icon = "✅" if day["uptime_pct"] >= 99 else ("⚠️" if day["uptime_pct"] >= 95 else "🔴")
        lines.append(
            f"{icon} {label}\n"
            f"   сутки: {humanize.downtime(day['total_checks'], day['ok_checks'], interval)} · "
            f"неделя: {humanize.downtime(week['total_checks'], week['ok_checks'], interval)} · "
            f"месяц: {humanize.downtime(month['total_checks'], month['ok_checks'], interval)}\n"
            f"   открывается {humanize.speed(day['avg_response_ms'])}")
    await render(call, "\n".join(lines), back_button("← Здоровье сайтов", "menu_health"))
