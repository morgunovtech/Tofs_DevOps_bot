"""The main screen answers «is everything OK?» without a tap: blocks —
🌐 web services, what other bots see (📣 Соцсети), 🤖 bots and systems —
a line per thing with its key number, problems right in the text. One
button per block opens its full page; nothing else on the screen."""


import re
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, Update

from config import config
from db.database import (
    get_active_host_urls,
    get_active_incidents,
    get_all_sites,
)
from handlers import seo as seo_screen
from handlers.blocks import backup_state
from handlers.common import ack, render
from handlers.mail import mail_brief
from monitors import domain_checker, ssl_checker
from monitors.mail_checker import MAIL_CHECKS, carriers
from services import heartbeats, humanize, maintenance, notifier, sitestatus, updates
from services.heartbeats import Job, Watchdog, job_states
from utils.clock import to_local, utcnow
from utils.text import esc, plural
from utils.urls import is_api_check, is_web_page, site_label, ssl_applies

router = Router(name="menu")

# The region's timing is measured every 6 hours (reports.scheduler): a
# number younger than seven is the current one.
FRESH_REGION_MINUTES = 7 * 60

# The sites block shows this many lines, the worst first; the rest are a
# count with how many of them are problems.
MAIN_SITES = 10
_RANK = {"🔴": 0, "🟠": 1, "⚠️": 2, "🔧": 3, "⏳": 4, "✅": 5}


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


def _without_label(text: str, label: str) -> str:
    """«example.com медленно отвечает» → «медленно отвечает»: the line names the site already."""
    for sep in (": ", " "):
        if text.startswith(label + sep):
            return text[len(label) + len(sep):]
    return text


def _due(what: str, days: int) -> str:
    """«сертификат истёк» · «сертификат истекает сегодня» · «сертификат через 5 дн.» —
    short, for the main screen; the last days in words (services.humanize)."""
    if days < 0:
        return f"{what} истёк"
    return humanize.expires_words(what, days) if days <= 1 else f"{what} через {days} дн."


async def site_state(site: dict, incidents: list[dict] | None = None) -> tuple[str, str, float | None]:
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
    if avail.get("status") == "unknown":
        return humanize.UNCHECKED, f"не могу проверить: {humanize.describe_error(avail.get('error'))}", age
    if avail.get("status") != "ok":
        return "🔴", humanize.describe_error(avail.get("error")), age
    if is_api_check(site["url"], site.get("http_method")):
        own_host = site["url"] in await get_active_host_urls()
        st = {k: v for k, v in st.items() if k in ("avail", "region") or (own_host and k in ("ssl", "domain"))}
    notes: list[tuple[str, str, str]] = []   # (kind, severity, words)
    # A certificate snapshot left on an http:// site by an older version says nothing.
    ssl = (st.get("ssl") or {}) if ssl_applies(site["url"]) else {}
    dom = st.get("domain") or {}
    days = ssl.get("days_left")
    if days is not None and days <= ssl_checker.WARN_DAYS:
        notes.append(("ssl", "critical" if days <= ssl_checker.CRITICAL_DAYS else "warning", _due("сертификат", days)))
    days = dom.get("days_left")
    if not dom.get("unsupported") and days is not None and days <= domain_checker.WARN_DAYS:
        notes.append(("domain", "critical" if days <= domain_checker.CRITICAL_DAYS else "warning", _due("домен", days)))
    links = st.get("links") or {}
    n = links.get("broken", links.get("internal"))   # «internal»: snapshots from before 11.2026
    if n:
        notes.append(("links", "warning",
                      f"{n} {plural(n, 'ссылка не открывается', 'ссылки не открываются', 'ссылок не открываются')}"))
    seo = st.get("seo") or {}
    if seo.get("status") == "critical":
        notes.append(("seo", "critical", "закрыт от поиска"))
    elif seo.get("status") == "warning" and seo.get("improve"):
        # as many as the card lists: a kind of finding on two pages is one
        n = len(seo_screen.grouped(seo.get("problems") or [])) or seo["improve"]
        notes.append(("seo", "warning", f"{n} {plural(n, 'помеха', 'помехи', 'помех')} в поиске"))
    # The site's other open problems (slow answers, 5xx deeper in, security)
    # come from the incidents; mail has a block of its own — it is the
    # domain's, not a site's.
    if incidents is None:
        incidents = await get_active_incidents()
    said, label = {kind for kind, *_ in notes}, site_label(site["url"])
    for inc in incidents:
        if inc["site_id"] == site["id"] and inc["check_type"] not in (*said, *MAIL_CHECKS):
            words = humanize.incident_headline(inc["check_type"], inc.get("message"), label,
                                               page=is_web_page(site["url"], site.get("http_method")))
            notes.append((inc["check_type"], inc["severity"], _without_label(words, label)))
    paused = "чинится, " if await maintenance.is_paused(site["id"]) else ""
    if notes:
        icon = humanize.level_icon(humanize.worst_severity(sev for _, sev, _ in notes))
        return icon, paused + ", ".join(words for *_, words in notes), age
    ms = best_ms(st)[0]
    word = humanize.fmt_seconds(ms) if ms is not None else "открывается"
    return ("🔧" if paused else "✅"), paused + word, age


def main_job_lines(jobs: list[Job]) -> list[str]:
    """The main screen: a job on time is its name — the headline says when
    things were checked; the time is said only when it is late."""
    return [f"{j.icon} {esc(j.name)}" if j.icon == "✅" else f"{j.icon} {esc(j.name)} · {esc(j.state)}"
            for j in jobs]


def plain_title(title: str) -> str:
    """A block title as words: «📣 Соцсети · 6 227» → «Соцсети · 6 227»."""
    return re.sub(r"^\W+", "", title) or title


async def mail_rows(sites: list[dict]) -> list[tuple[str, str, str]]:
    """(icon, domain, words) per mail domain — mail is the domain's, not a site's."""
    by_url = {s["url"]: s for s in sites}
    out = []
    for domain, url in sorted(carriers(await get_active_host_urls()).items()):
        if url in by_url:
            icon, words = mail_brief((await sitestatus.get(by_url[url]["id"])).get("mail"))
            if words != "почты нет":   # a domain without mail has nothing to watch here
                out.append((icon, domain, words))
    return out


PROBLEM_ICONS = ("🔴", "🟠", "⚠️")


def icon_of(line: str) -> str:
    """The status icon a line starts with ('' for a plain one)."""
    return next((i for i in (*PROBLEM_ICONS, "✅", "⏳", "🔧") if line.startswith(i)), "")


def checked_at(section: dict | None) -> str:
    """« · проверено в 17:33» — a time, not «2 мин назад»: the message stays
    in the chat and an age would soon be wrong."""
    try:
        when = datetime.fromisoformat((section or {}).get("at") or "")
    except ValueError:
        return ""
    local, now = to_local(when), to_local(utcnow())
    return f" · проверено в {local.strftime('%H:%M' if local.date() == now.date() else '%d.%m %H:%M')}"


def headline(problem_icons: list[str], checked: str) -> str:
    """«✅ Всё в порядке · проверено в 17:33» / «🟠 2 проблемы · …» — the worst first."""
    if not problem_icons:
        return f"✅ Всё в порядке{checked}"
    worst = next(i for i in ("🔴", "🟠", "⚠️") if i in problem_icons)
    n = len(problem_icons)
    return f"{worst} {n} {plural(n, 'проблема', 'проблемы', 'проблем')}{checked}"


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
        out.append((watchdog.icon, f"сторож {watchdog.name}", "" if watchdog.icon == "✅" else watchdog.state))
    else:
        out.append(("", "Внешнего сторожа нет", "подключи в «🤖 Боты»"))
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
                "можно добавить бота или задачу по расписанию: скажу, если замолчит.", kb)

    incidents = await get_active_incidents()
    # Problems first (a stable sort: within a level the sites keep their
    # order), so a down site is never below the fold of the first ten.
    states = [(s, *await site_state(s, incidents)) for s in sites]
    states.sort(key=lambda t: _RANK.get(t[1], 9))
    avail = [(await sitestatus.get(s["id"])).get("avail") for s in sites]
    newest = max((a for a in avail if a and a.get("at")), key=lambda a: a["at"], default=None)
    mail = await mail_rows(sites)
    reported = await status_blocks(jobs)
    systems = await system_lines(watchdog)

    lines: list[str] = []
    if states:
        shown, hidden = states[:MAIN_SITES], states[MAIN_SITES:]
        lines += ["", "<b>Сайты</b>", *(f"{icon} {esc(site_label(s['url']))} · {esc(state)}"
                                        for s, icon, state, _ in shown)]
        if hidden:
            bad = sum(1 for _, icon, _, _ in hidden if icon in PROBLEM_ICONS)
            lines.append(f"… и ещё {len(hidden)}" + (f", из них с проблемами: {bad}" if bad else ""))
    if mail:
        lines += ["", "<b>Почта</b>", *(f"{icon} {esc(domain)} · {esc(words)}" for icon, domain, words in mail)]
    for _, title, rows in reported:
        lines += ["", f"<b>{esc(plain_title(title))}</b>",
                  *(f"{icon} {esc(name)} · {esc(text)}" for icon, name, text in rows)]
    lines += ["", "<b>Боты и системы</b>", *main_job_lines(jobs),
              *(f"{icon} {esc(name)}{f' · {esc(state)}' if state else ''}".lstrip()
                for icon, name, state in systems)]

    # Counted by the things themselves, not by the lines on the screen: a
    # site below the fold still counts, and a silent bot's «не знаю» block
    # is the same problem as its line under «Боты и системы».
    problems = [icon for _, icon, _, _ in states if icon in PROBLEM_ICONS]
    problems += [icon for icon, _, _ in mail if icon in PROBLEM_ICONS]
    problems += [icon for j, _, rows in reported if j.icon == "✅" for icon, _, _ in rows if icon in PROBLEM_ICONS]
    problems += [j.icon for j in jobs if j.icon in PROBLEM_ICONS]
    problems += [icon for icon, _, _ in systems if icon in PROBLEM_ICONS]
    if not newest and not any(j.ago is not None for j in jobs):
        head = "⏳ Первая проверка через минуту" if sites else "⏳ Жду первой связи"
    else:
        head = headline(problems, checked_at(newest))
    lines = [head, *lines]

    chips = []
    mute_until = await mute_until_local()
    if mute_until:
        chips.append(f"Тишина до {mute_until}")
    if maintenance.maintenance_now(None):
        chips.append("плановые работы")
    if notifier.in_quiet_hours():
        chips.append("тихие часы")
    if chips:
        lines += ["", " · ".join(chips)]

    rows = []
    if incidents:   # first: on a bad day this is the button that matters
        inc_icon = humanize.level_icon(humanize.worst_severity(i["severity"] for i in incidents))
        rows.append([InlineKeyboardButton(text=f"{inc_icon} Что не так",
                                          callback_data="menu_incidents")])
    blocks = []
    if states:
        blocks.append(InlineKeyboardButton(text="🌐 Сайты", callback_data="blk:sites"))
    else:   # tasks without sites: the way to the first site stays on the screen
        blocks.append(InlineKeyboardButton(text="➕ Сайт", callback_data="site_add"))
    blocks += [InlineKeyboardButton(text=title.split(" · ")[0], callback_data=f"blk:seen:{j.name}")
               for j, title, _ in reported if len(f"blk:seen:{j.name}".encode()) <= 64]
    blocks.append(InlineKeyboardButton(text="🤖 Боты", callback_data="blk:bots"))
    rows += [blocks[i:i + 3] for i in range(0, len(blocks), 3)]
    rows.append([InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def send_main_menu(target: Message | CallbackQuery):
    header, kb = await build_main_menu()
    await render(target, header, kb)


@router.message(Command("menu"))
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


@router.callback_query(F.data.startswith("fsm_cancel"))
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext, dispatcher: Dispatcher, bot: Bot):
    """«✖️ Отмена» under an input step: back to the screen the step was
    opened from (fsm_cancel:<its callback>, drawn by its own handler through
    the dispatcher), else the main screen. On a prompt whose step is already
    closed (a bottom button, another tap, a restart) the way back is the
    same — only the toast differs."""
    closed = await state.get_state() is None
    await state.clear()
    back = call.data.partition(":")[2]
    if not back or back == "menu_main":
        await ack(call, "Нечего отменять — этот шаг уже закрыт" if closed else "Отменено")
        await send_main_menu(call)
        return
    if closed:
        await ack(call, "Нечего отменять — этот шаг уже закрыт")
    await dispatcher.feed_update(bot, Update(update_id=0, callback_query=call.model_copy(update={"data": back})))
