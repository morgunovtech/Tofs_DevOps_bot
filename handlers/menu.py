"""The main screen answers «is everything OK?» by itself: one line per
site from the last checks, problems listed inline when there are any, and
three buttons — sites, check now, settings. A fourth («🔴 Проблемы»)
appears only while something is broken."""


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
)
from handlers.common import ack, back_button, render
from monitors.availability import check_all
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.ssl_checker import check_all_ssl
from reports.formatter import format_compact_status_report, incident_line
from services import humanize, maintenance, notifier, sitestatus
from utils.clock import to_local, utcnow
from utils.text import clip, esc, fmt_duration, plural
from utils.urls import site_label

router = Router(name="menu")

FRESH_REGION_MINUTES = 3 * 60


async def mute_until_local() -> str | None:
    deadline = await notifier.mute_until()
    if not deadline:
        return None
    fmt = "%H:%M" if deadline - utcnow() < timedelta(hours=20) else "%d.%m %H:%M"
    return to_local(deadline).strftime(fmt)


def best_ms(status: dict) -> tuple[int | None, str | None]:
    """Response time to show: from the audience's region when fresh, else
    from the bot's own probe."""
    region = status.get("region") or {}
    age = sitestatus.age_minutes(region)
    if region.get("ms") is not None and age is not None and age < FRESH_REGION_MINUTES:
        return region["ms"], region.get("name")
    return (status.get("avail") or {}).get("ms"), None


async def site_state(site: dict) -> tuple[str, str, float | None]:
    """(icon, short state, minutes since the last availability check) from
    the stored snapshot — no network, the menu must open instantly. The
    icon is the worst level among the site's notes (🔴 / 🟠), ✅ when the
    site is up and nothing needs action."""
    st = await sitestatus.get(site["id"])
    avail = st.get("avail")
    if not avail:
        return "⏳", "ждёт первой проверки", None
    age = sitestatus.age_minutes(avail)
    if avail.get("status") != "ok":
        return "🔴", humanize.describe_error(avail.get("error")), age
    notes: list[tuple[str, str]] = []   # (severity, words)
    ssl, dom = st.get("ssl") or {}, st.get("domain") or {}
    days = ssl.get("days_left")
    if days is not None and days <= 14:
        notes.append(("critical" if days <= 3 else "warning",
                      "сертификат истёк" if days < 0 else f"сертификат через {days} дн."))
    days = dom.get("days_left")
    if not dom.get("unsupported") and days is not None and days <= 30:
        notes.append(("critical" if days <= 7 else "warning",
                      "домен истёк" if days < 0 else f"домен через {days} дн."))
    links = st.get("links") or {}
    if links.get("internal"):
        n = links["internal"]
        notes.append(("warning", f"{n} {plural(n, 'ссылка', 'ссылки', 'ссылок')} в никуда"))
    seo = st.get("seo") or {}
    if seo.get("status") == "critical":
        notes.append(("critical", "закрыт от поиска"))
    elif seo.get("status") == "warning" and seo.get("improve"):
        n = seo["improve"]
        notes.append(("warning", f"{n} {plural(n, 'помеха', 'помехи', 'помех')} в поиске"))
    if notes:
        return humanize.level_icon(humanize.worst_severity(s for s, _ in notes)), ", ".join(w for _, w in notes), age
    word = humanize.speed_word(best_ms(st)[0])
    if await maintenance.is_paused(site["id"]):
        return "🔧", f"чинится, {word}", age
    return "✅", word, age


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    sites = await get_all_sites()
    if not sites:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")]])
        return ("👋 Сайтов пока нет. Добавь первый — и я начну проверять его каждые "
                f"{config.check_interval_minutes} мин и напишу, только если что-то сломается.", kb)

    states = [(s, *await site_state(s)) for s in sites]
    ages = [age for *_, age in states if age is not None]
    icons = [icon for _, icon, *_ in states]
    total = sum(1 for icon in icons if icon != "⏳")
    opening = sum(1 for icon in icons if icon in ("✅", "🔧", "🟠"))   # 🟠 sites are up, with a note
    fine = sum(1 for icon in icons if icon in ("✅", "🔧"))
    incidents = await get_active_incidents()
    inc_icon = humanize.level_icon(humanize.worst_severity(i["severity"] for i in incidents))

    if not total:
        headline = "⏳ Первая проверка через минуту"
    elif incidents or fine < total:
        worst = "🔴" if "🔴" in icons else ("🟠" if "🟠" in icons else "✅")
        headline = f"{worst} {opening}/{total} {plural(total, 'открывается', 'открываются', 'открываются')}"
        if incidents:
            headline += f" · {inc_icon} проблем: {len(incidents)}"
    else:
        headline = f"✅ {total}/{total} в порядке"
    if total and ages:
        ago = min(ages)
        headline += f" · проверял {fmt_duration(ago)} назад" if ago >= 1 else " · проверял только что"
    lines = [headline]
    for s, icon, state, _ in states[:10]:
        lines.append(f"{icon} {esc(site_label(s['url']))} · {esc(state)}")
    if len(states) > 10:
        lines.append(f"… и ещё {len(states) - 10}")

    if incidents:
        lines.append("")
        lines += [f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}" for inc in incidents[:3]]
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
        rows.append([InlineKeyboardButton(text=f"{inc_icon} Проблемы ({len(incidents)})", callback_data="menu_incidents")])
    rows.append([InlineKeyboardButton(text="🔎 Проверить всё сейчас", callback_data="run_full_check")])
    rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def send_main_menu(target: Message | CallbackQuery):
    header, kb = await build_main_menu()
    await render(target, header, kb)


@router.message(Command("menu"))
@router.message(F.text.in_({"📱 Меню", "📊 Статус"}))
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
        report += (f"\n\n🟠 {internal} {plural(internal, 'ссылка ведёт', 'ссылки ведут', 'ссылок ведут')} "
                   f"в никуда — подробности в карточке сайта («🌍 Сайты» → сайт → «🔗 Ссылки»)")
    elif external:
        report += f"\n\n✅ Ссылки: свои работают; чужих не открываются {external}"
    else:
        report += "\n\n✅ Ссылки: все работают"
    await render(call, report, back_button())
