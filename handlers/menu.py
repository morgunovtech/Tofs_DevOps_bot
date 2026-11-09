"""The main screen answers «is everything OK?» without a tap: blocks —
🌐 web services, what other bots see (📣 Соцсети), 🤖 bots and systems —
a line per thing with its key number, problems right in the text. One
button per block opens its full page; nothing else on the screen."""


from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import (
    get_active_host_urls,
    get_active_incidents,
    get_active_site_urls,
    get_all_sites,
)
from handlers.blocks import backup_state
from handlers.common import ack, back_button, render
from monitors.availability import check_all
from reports.formatter import format_compact_status_report, incident_line
from services import heartbeats, humanize, maintenance, notifier, sitestatus, updates
from services.heartbeats import Job, Watchdog, job_states
from utils.clock import to_local, utcnow
from utils.text import clip, esc, fmt_duration, plural
from utils.urls import is_api_check, site_label

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
    site is up and nothing needs action. An API check speaks only of its
    answer — and of its certificate and domain when no site shares them."""
    st = await sitestatus.get(site["id"])
    avail = st.get("avail")
    if not avail:
        return "⏳", "ждёт первой проверки", None
    age = sitestatus.age_minutes(avail)
    if avail.get("status") != "ok":
        return "🔴", humanize.describe_error(avail.get("error")), age
    if is_api_check(site["url"], site.get("http_method")):
        own_host = site["url"] in await get_active_host_urls()
        st = {k: v for k, v in st.items() if k in ("avail", "region") or (own_host and k in ("ssl", "domain"))}
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
    n = links.get("broken", links.get("internal"))   # «internal»: snapshots from before 11.2026
    if n:
        notes.append(("warning", f"{n} {plural(n, 'ссылка не открывается', 'ссылки не открываются', 'ссылок не открываются')}"))
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


def job_lines(jobs: list[Job], watchdog: Watchdog | None = None) -> list[str]:
    """A line per task (and the watchdog last) — for /sites."""
    lines = [f"{j.icon} {esc(j.name)} · {esc(j.state)}" for j in jobs]
    if watchdog:
        lines.append(f"{watchdog.icon} сторож {esc(watchdog.name)} · {esc(watchdog.state)}")
    return lines


async def status_blocks(jobs: list[Job]) -> list[tuple[Job, str, list[tuple[str, str, str]]]]:
    """(job, title, [(icon, name, text)]) for every bot that reports what it
    sees; a late bot's block is one «не знаю» line."""
    out = []
    for j in jobs:
        status = await heartbeats.job_status(j.name)
        if status:
            out.append((j, heartbeats.status_title(j, status), heartbeats.status_items(j, status)))
    return out


async def system_lines(watchdog: Watchdog | None) -> list[tuple[str, str, str]]:
    """(icon, name, state) of the bot's own systems: the watchdog that would
    tell if it died, its database backup, a deploy that did not start."""
    out = []
    if watchdog:
        out.append((watchdog.icon, f"сторож {watchdog.name}", watchdog.state))
    else:
        out.append(("🛡", "внешнего сторожа нет", "подключить — «🤖 Боты»"))
    icon, words, _ = backup_state()
    out.append((icon, "резервная копия", words))
    deploy = await updates.deploy_problem()
    if deploy:
        out.append(("🟠", "новая версия бота", "не запустилась, работаю на прежней"))
    return out


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    sites = await get_all_sites()
    jobs, watchdog = await job_states(), await heartbeats.watchdog()
    if not sites and not jobs:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")]])
        return ("👋 Пока ни за чем не слежу. Добавь первый сайт — и я начну проверять его каждые "
                f"{config.check_interval_minutes} мин и напишу, только если что-то сломается. Там же "
                "можно добавить бота или задачу по расписанию: скажу, если перестанет отмечаться.", kb)

    states = [(s, *await site_state(s)) for s in sites]
    ages = [age for *_, age in states if age is not None]
    site_icons = [icon for _, icon, *_ in states]
    reported = await status_blocks(jobs)
    seen = [(icon, name) for j, _, rows in reported if j.icon == "✅" for icon, name, _ in rows]
    systems = await system_lines(watchdog)
    sys_icons = [icon for icon, *_ in systems if icon in ("✅", "🟠", "🔴")]
    icons = site_icons + [i for i, _ in seen] + [j.icon for j in jobs] + sys_icons
    total = sum(1 for icon in icons if icon != "⏳")
    sites_total = sum(1 for icon in site_icons if icon != "⏳")
    opening = sum(1 for icon in site_icons if icon in ("✅", "🔧", "🟠"))   # 🟠 sites are up, with a note
    fine = sum(1 for icon in icons if icon in ("✅", "🔧"))
    silent = [j.name for j in jobs if j.icon in ("🟠", humanize.UNCHECKED)]
    incidents = await get_active_incidents()
    inc_icon = humanize.level_icon(humanize.worst_severity(i["severity"] for i in incidents))

    if not total:
        headline = "⏳ Первая проверка через минуту" if sites else "⏳ Жду первой отметки"
    elif incidents or fine < total:
        worst = next((i for i in ("🔴", "🟠", humanize.UNCHECKED) if i in icons), "✅")
        parts = []
        if sites_total:
            parts.append(f"{opening}/{sites_total} {plural(sites_total, 'открывается', 'открываются', 'открываются')}")
        if silent:
            parts.append(f"⏰ {esc(silent[0])} молчит" if len(silent) == 1 else f"⏰ молчат: {len(silent)}")
        parts += [f"{i} {esc(name)}" for i, name, _ in systems if i in ("🟠", "🔴")]
        troubled = [(i, n) for i, n in seen if i != "✅"]
        if troubled:
            worst_item = "🔴" if any(i == "🔴" for i, _ in troubled) else "🟠"
            parts.append(" · ".join(f"{i} {esc(n)}" for i, n in troubled) if len(troubled) <= 2
                         else f"{worst_item} с замечаниями: {len(troubled)}")
        if incidents:
            parts.append(f"{inc_icon} проблем: {len(incidents)}")
        headline = f"{worst} " + " · ".join(parts)
    else:
        headline = "✅ Всё в порядке"
    if total and ages:
        ago = min(ages)
        headline += f" · проверял {fmt_duration(ago)} назад" if ago >= 1 else " · проверял только что"

    lines = [headline]
    if states:
        lines += ["", f"🌐 Веб-сервисы · {opening}/{sites_total}" if sites_total else "🌐 Веб-сервисы"]
        lines += [f"{icon} {esc(site_label(s['url']))} · {esc(state)}" for s, icon, state, _ in states[:10]]
        if len(states) > 10:
            lines.append(f"… и ещё {len(states) - 10}")
    for _, title, rows in reported:
        lines += ["", esc(title), *(f"{icon} {esc(name)} · {esc(text)}" for icon, name, text in rows)]
    lines += ["", "🤖 Боты и системы", *job_lines(jobs),
              *(f"{icon} {esc(name)} · {esc(state)}" for icon, name, state in systems)]

    if incidents:   # what no line above can say: a DNS change, 5xx deep inside, slowness
        lines += ["", *(f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))}" for inc in incidents[:3])]
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

    rows = []
    if incidents:   # first: on a bad day this is the button that matters
        rows.append([InlineKeyboardButton(text=f"{inc_icon} Что не так ({len(incidents)})",
                                          callback_data="menu_incidents")])
    blocks = []
    if states:
        blocks.append(InlineKeyboardButton(text="🌐 Сайты", callback_data="blk:sites"))
    blocks += [InlineKeyboardButton(text=title, callback_data=f"blk:seen:{j.name}")
               for j, title, _ in reported if len(f"blk:seen:{j.name}".encode()) <= 64]
    blocks.append(InlineKeyboardButton(text="🤖 Боты", callback_data="blk:bots"))
    rows += [blocks[i:i + 3] for i in range(0, len(blocks), 3)]
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


# Old buttons from earlier layouts still land somewhere sensible: the
# «🌍 Сайты» list and «⏰ Контроль задач» are the main screen now.
_LEGACY_MAIN = {"menu_main", "menu_more", "menu_health", "menu_connections", "menu_sites", "menu_check_site",
                "menu_hb", "menu_uptime", "menu_ssl", "menu_domains", "menu_links", "menu_seo", "menu_status",
                "run_full_check"}   # «🔎 Проверить всё сейчас» — the main screen is never older than a minute or two


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

_NO_SITES = "Сайтов пока нет — добавь: 🏠 Главная → «🌐 Сайты» → «➕ Добавить сайт»."


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
    urls, jobs = await get_active_site_urls(), await job_states()
    if not urls and not jobs:
        await message.answer(_NO_SITES)
        return
    lines = ["🌍 Слежу за:", *(f"  • {esc(u)}" for u in urls)] if urls else []
    if jobs:
        lines += ([""] if lines else []) + ["⏰ Боты и задачи:", *job_lines(jobs)]
    await message.answer("\n".join(lines))
