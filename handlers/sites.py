"""🌍 Sites: the card (opened from the main screen), add/remove, pause,
export/import."""

import json
import re
from datetime import UTC, datetime
from urllib.parse import urlparse

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config
from db.database import (
    SITE_SETTING_COLS,
    activate_or_create_site,
    deactivate_site,
    get_active_host_urls,
    get_active_incidents,
    get_all_sites,
    get_site,
    get_state,
    get_uptime_over_days,
    restore_from_bytes,
    set_state,
    update_site_settings,
)
from handlers.common import (
    MAX_SITES,
    ack,
    back_button,
    cancel_kb,
    cb_args,
    render,
    site_by_cb,
)
from handlers.menu import best_ms, cb_main_menu
from handlers.start import rules_of_the_game
from monitors.availability import check_availability
from monitors.domain_checker import check_domain
from monitors.pagemeta import alt_host_note, suggest_keyword
from monitors.ssl_checker import check_ssl
from reports.formatter import incident_line
from reports.scheduler import reschedule_all, reset_site_schedule
from services import humanize, integrations, maintenance, runtime, secrets, settings, sitestatus
from utils.clock import fmt_local
from utils.text import esc, fmt_date, fmt_duration, plural
from utils.urls import host_key, is_api_check, is_api_url, is_http_url, is_web_page, site_label

router = Router(name="sites")


class AddSiteForm(StatesGroup):
    url = State()


class ImportForm(StatesGroup):
    data = State()


# Hostname/IPv4 labels; single-label LAN hosts are allowed for tcp/ping.
_LABEL = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
_HOST_RE = re.compile(rf"{_LABEL}(\.{_LABEL})*")
_DOMAIN_RE = re.compile(rf"{_LABEL}(\.{_LABEL})+")


def _valid_lengths(host: str) -> bool:
    """DNS limits: 253 chars total, 63 per label."""
    return len(host) <= 253 and all(len(label) <= 63 for label in host.split("."))


API_PATH_LIMIT = 300


def parse_site_input(raw: str) -> tuple[str | None, str | None]:
    """User text → canonical monitor URL, or (None, error message). A web
    page means its site: a pasted https://example.com/blog/post is
    example.com. An API address (utils.urls.is_api_url: /api/… or an api.*
    host) keeps its path and query exactly as typed — that endpoint is what
    is watched, and paths are case-sensitive."""
    typed = (raw or "").strip()
    raw = typed.lower()
    if raw.startswith(("tcp://", "ping://")):
        try:
            parsed = urlparse(raw)
            host, port = parsed.hostname or "", parsed.port
        except ValueError:
            host, port = "", None
        if not _HOST_RE.fullmatch(host) or not _valid_lengths(host):
            return None, "Не понял хост. Примеры: tcp://mail.example.com:25, ping://10.0.0.1"
        if parsed.scheme == "tcp":
            if not port:
                return None, "Для tcp нужен порт: tcp://host:порт (например, tcp://mail.example.com:25)"
            return f"tcp://{host}:{port}", None
        return f"ping://{host}", None
    if not raw.startswith(("http://", "https://")):
        raw, typed = "https://" + raw, "https://" + typed
    try:
        parsed = urlparse(raw)
        host, port_n = parsed.hostname or "", parsed.port
    except ValueError:
        return None, "Не похоже на адрес. Пришли что-то вроде example.com"
    if not _DOMAIN_RE.fullmatch(host) or not _valid_lengths(host):
        return None, "Не похоже на домен. Пришли что-то вроде example.com"
    port = f":{port_n}" if port_n and port_n not in (80, 443) else ""
    site = f"{parsed.scheme}://{host}{port}"
    if not is_api_url(site + parsed.path):
        return site, None
    as_typed = urlparse(typed)
    path = "" if as_typed.path in ("", "/") else as_typed.path
    tail = path + (f"?{as_typed.query}" if as_typed.query else "")
    if re.search(r"\s", tail) or len(tail) > API_PATH_LIMIT:
        return None, f"Адрес API — без пробелов и не длиннее {API_PATH_LIMIT} символов после домена."
    return site + tail, None


# ── Card ─────────────────────────────────────────────────────────────────────

def _ago(section: dict | None) -> str:
    age = sitestatus.age_minutes(section)
    if age is None:
        return ""
    return " · только что" if age < 1 else f" · {fmt_duration(age)} назад"


def api_line(site: dict) -> str:
    """What an API check sends and expects — one line on its card."""
    method = (site.get("http_method") or "GET").upper()
    kw = (site.get("keyword") or "").strip()
    expect = ""
    if kw:
        expect = (f", в ответе не должно быть «{esc(kw)}»" if site.get("keyword_mode") == "absent"
                  else f", в ответе должно быть «{esc(kw)}»")
    return (f"🔌 API-проверка: {method}{expect}. Слежу только за ответом — поиск, ссылки и SEO "
            f"к API не относятся.")


async def host_checked_by(site: dict) -> str | None:
    """URL of the monitor whose certificate and domain checks cover this
    site's host — itself for a page; None for tcp/ping."""
    url = site["url"]
    if not is_http_url(url):
        return None
    hosts = await get_active_host_urls()
    if url in hosts:
        return url
    return next((u for u in hosts if host_key(u) == host_key(url)), None)


async def card_lines(site: dict) -> list[str]:
    """Everything about one site from the stored snapshot — instant."""
    st = await sitestatus.get(site["id"])
    http = is_http_url(site["url"])
    api = is_api_check(site["url"], site.get("http_method"))
    lines: list[str] = [api_line(site)] if api else []
    avail = st.get("avail")
    if not avail:
        lines.append("⏳ Ещё не проверял — первая проверка в течение минуты")
    elif avail.get("status") == "ok":
        ms, region = best_ms(st)
        verb = "Открывается" if http and not api else "Отвечает"
        lines.append(f"✅ {verb}, {humanize.speed(ms, region)}{_ago(avail)}")
    else:
        verb = "Не открывается" if http and not api else "Не отвечает"
        lines.append(f"🔴 {verb}: {esc(humanize.describe_error(avail.get('error')))}{_ago(avail)}")
    covered_by = await host_checked_by(site)
    if covered_by and covered_by != site["url"]:
        lines.append(f"🔒 Сертификат и домен — на карточке {esc(site_label(covered_by))}: "
                     f"проверяю их один раз на адрес")
    if covered_by == site["url"]:
        ssl = st.get("ssl")
        if not ssl:
            lines.append("⏳ Сертификат: ещё не проверял")
        elif ssl.get("days_left") is not None:
            days = ssl["days_left"]
            icon = "✅" if days > 14 else humanize.level_icon("warning" if days > 3 else "critical")
            issuer = f", выдан {esc(ssl['issuer'])}" if ssl.get("issuer") else ""
            lines.append(f"{icon} Сертификат действует ещё {days} дн. (до {fmt_date(ssl.get('not_after'))}){issuer}")
        else:
            lines.append(f"🔴 Сертификат: {esc(humanize.describe_error(ssl.get('error')))}")
        dom = st.get("domain")
        if not dom:
            if not api:   # an api.* host shares the registration with its site
                lines.append("⏳ Домен: ещё не проверял")
        elif dom.get("days_left") is not None:
            days = dom["days_left"]
            icon = "✅" if days > 30 else humanize.level_icon("warning" if days > 7 else "critical")
            registrar = (f", регистратор {esc(dom['registrar'])}"
                         if dom.get("registrar") not in (None, "", "Unknown") else "")
            lines.append(f"{icon} Домен оплачен до {fmt_date(dom.get('expiration'))} (ещё {days} дн.){registrar}")
        elif dom.get("unsupported"):
            lines.append(f"ℹ️ Домен: {esc(dom.get('error') or 'срок не сообщается')}")
        else:
            lines.append(f"⚠️ Домен: {esc(humanize.describe_error(dom.get('error')))}")
        # Page findings only: an API check is never crawled or audited, and a
        # snapshot left from when it was a page must not speak for it.
        links = st.get("links") if not api else None
        if links:
            if links.get("status") == "ok":
                lines.append(f"✅ Ссылки: все открываются{_ago(links)}")
            elif links.get("status") == "warning":
                n = links.get("broken", links.get("internal")) or 0
                lines.append(f"🟠 Ссылки: {n} не {plural(n, 'открывается', 'открываются', 'открываются')}{_ago(links)}")
            else:
                lines.append(f"⚠️ Ссылки: не смог проверить{_ago(links)}")
        seo = st.get("seo") if not api else None
        if seo:
            # Says what was measured: robots/noindex and page findings. Whether
            # the site is IN an index is the «🔎 Поиск и ИИ» screen's job.
            n = seo.get("improve") or 0
            verdict = {"ok": "✅ Поиск и ИИ: помех нет",
                       "warning": f"🟠 Поиск и ИИ: {n} {plural(n, 'помеха', 'помехи', 'помех')}",
                       "critical": "🔴 Поиск и ИИ: сайт закрыт от поисковиков"}.get(
                seo.get("status"), "⚠️ Поиск и ИИ: не смог проверить")
            lines.append(f"{verdict}{_ago(seo)}")
    interval = site.get("check_interval_min") or config.check_interval_minutes
    week = await get_uptime_over_days(site["id"], 7)
    month = await get_uptime_over_days(site["id"], 30)
    if week["total_checks"]:
        lines.append(f"📈 За неделю {humanize.downtime(week['total_checks'], week['ok_checks'], interval)}, "
                     f"за месяц {humanize.downtime(month['total_checks'], month['ok_checks'], interval)}")
    return lines


async def _pause_rows(sid: int) -> tuple[list[InlineKeyboardButton], list[str]]:
    notes = []
    if await maintenance.paused_until(sid):
        notes.append("🔧 Режим «я чиню»: про этот сайт не пишу, проверки идут.")
        row = [InlineKeyboardButton(text="✅ Починил, пиши снова", callback_data=f"pause:{sid}:off")]
    else:
        row = [InlineKeyboardButton(text="🔧 Я чиню: час тишины", callback_data=f"pause:{sid}:60")]
    if maintenance.maintenance_now(sid):
        notes.append("🕐 Сейчас плановые работы — про этот сайт не пишу.")
    return row, notes


async def render_card(call: CallbackQuery, site: dict):
    url, sid = site["url"], site["id"]
    lines = [f"🌍 {esc(url)}\n", *await card_lines(site)]
    pause_row, notes = await _pause_rows(sid)
    if notes:
        lines += ["", *notes]
    problems = [inc for inc in await get_active_incidents() if inc["site_id"] == sid]
    if problems:
        lines.append("")
        lines += [f"{humanize.level_icon(inc['severity'])} {esc(incident_line(inc))} "
                  f"<i>с {fmt_local(inc['created_at'])}</i>" for inc in problems[:3]]
    rows = [[InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data=f"check_site_live:{sid}"),
             *pause_row[:1]]]
    if is_web_page(url, site.get("http_method")):
        rows.append([InlineKeyboardButton(text="🔎 Поиск и ИИ", callback_data=f"seo_site:{sid}"),
                     InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"check_links:{sid}")])
    last_row = []
    if problems:
        last_row.append(InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{problems[0]['id']}"))
    last_row.append(InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sset:{sid}"))
    rows.append(last_row)
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    """The card, instantly, from the last stored checks."""
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился.", back_button())
        return
    await render_card(call, site)


@router.callback_query(F.data.startswith("check_site_live:"))
async def cb_check_site_live(call: CallbackQuery):
    """«🔍 Проверить сейчас»: fresh availability, certificate and domain
    (when this site is the one they are checked for), then the same card."""
    await ack(call, "Проверяю…")
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    url = site["url"]
    await render(call, f"▱▱▱ Проверяю, открывается ли {esc(site_label(url))}...")
    await check_availability(url, manage=False)
    if await host_checked_by(site) == url:
        await render(call, "▰▱▱ Смотрю сертификат...")
        await check_ssl(url, manage=False)
        await render(call, "▰▰▱ Узнаю срок домена...")
        await check_domain(url, manage=False)
    await render_card(call, site)


# ── Add ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "site_add")
async def cb_site_add(call: CallbackQuery, state: FSMContext):
    """«➕ Добавить» — one door for everything that can be watched: a site
    is just typed in, the other two kinds are a button away."""
    await ack(call)
    if len(await get_all_sites()) >= MAX_SITES:
        await render(call, f"Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга. Бота или задачу "
                           f"по расписанию добавить можно.", InlineKeyboardMarkup(inline_keyboard=[
                               [_HB_BUTTON], *back_button().inline_keyboard]))
        return
    await state.set_state(AddSiteForm.url)
    await render(call, "➕ Пришли адрес сайта, например <code>example.com</code>.\n\n"
                       "Не сайт? Выбери ниже.", _add_kb())


_HB_BUTTON = InlineKeyboardButton(text="⏰ Бот или задача по расписанию", callback_data="hb_add")


def _add_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 API, порт или ping", callback_data="site_add_adv")],
        [_HB_BUTTON],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])


@router.callback_query(F.data == "site_add_adv")
async def cb_site_add_advanced(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddSiteForm.url)
    await render(call, "🛠 Кроме сайтов я умею следить за сервисами без веб-страницы:\n\n"
                       "• <code>example.com/api/health</code> — главная функция сайта, его API: "
                       "код ответа, время и фраза в ответе. Адрес под <code>/api/</code> или на "
                       "<code>api.</code>-домене я считаю API и не проверяю у него поиск, ссылки и SEO; "
                       "метод POST, заголовки и тело задаются в настройках после добавления\n"
                       "• <code>tcp://mail.example.com:25</code> — отвечает ли порт "
                       "(почта, SSH, база данных)\n"
                       "• <code>ping://10.0.0.1</code> — отвечает ли хост на ping\n\n"
                       "Пришли адрес в таком виде.", cancel_kb())


@router.message(AddSiteForm.url)
async def msg_site_add(message: Message, state: FSMContext):
    url, error = parse_site_input(message.text or "")
    if not url:
        await message.answer(error, reply_markup=_add_kb())
        return
    await state.clear()
    first_site = not await get_all_sites()
    site_id = await activate_or_create_site(url)
    label = site_label(url)
    status = await message.answer(f"▱▱ Добавил {esc(label)} — делаю первую проверку...")
    # Read-only first look: the scheduled monitor (next minute tick) owns
    # incidents and alert ladders; a manage=True check here would silently
    # consume the SSL alert for a nearly-expired certificate.
    r = await check_availability(url, manage=False)
    site = await get_site(site_id)
    page = is_web_page(url, site.get("http_method"))
    lines = [f"✅ {esc(label)} под присмотром.\n"]
    if r.ok:
        lines.append(f"✅ {'Открывается' if page else 'Отвечает'}, {humanize.speed(r.response_time_ms)}")
    else:
        lines.append(f"🔴 Сейчас {'не открывается' if page else 'не отвечает'}: "
                     f"{esc(humanize.describe_error(r.error))}. Я уже слежу и напишу, когда поднимется.")
    if r.api:
        lines.append("\n🔌 Это API: слежу только за ответом — код, время и фраза в ответе. Поиск, ссылки "
                     "и SEO к API не относятся.")
        lines.append("Нужен POST, токен или тело запроса — «📡 Настроить запрос»: там метод, заголовки, "
                     "тело и фраза, которая должна быть в ответе."
                     + ("" if r.ok else " Ошибка выше уйдёт, когда запрос станет правильным."))
    if await host_checked_by(site) == url:
        ssl_r = await check_ssl(url, manage=False)
        if ssl_r.ssl_info:
            lines.append(f"🔒 Сертификат безопасности действует ещё {ssl_r.ssl_info.days_left} дн.")
        elif ssl_r.transient:
            lines.append("⚠️ Сертификат: не удалось проверить, попробую при следующей проверке.")
        else:
            lines.append(f"🔴 Сертификат: {esc(humanize.describe_error(ssl_r.error))}")
        lines.append("\nСрок домена, ссылки, поиск и ИИ проверю в ближайшие часы сам." if page
                     else "\nСрок домена проверю в ближайшие часы сам.")
    if first_site:
        lines.append("\n" + rules_of_the_game())
    reset_site_schedule(site_id)
    kb = back_button()
    if r.api:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📡 Настроить запрос", callback_data=f"ssetx:{site_id}")],
            *back_button().inline_keyboard])
    if page and r.ok:
        note = await alt_host_note(url)
        if note:
            lines.append("\n" + note)
        phrase = await suggest_keyword(url)
        if phrase:
            await set_state(f"kw_suggest:{site_id}", phrase)
            lines.append(f"\n💡 На главной есть «{esc(phrase)}». Следить, чтобы она не пропадала? "
                         f"Так я замечу пустую страницу или ошибку вместо сайта.")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Да, следить", callback_data=f"kwok:{site_id}"),
                 InlineKeyboardButton(text="Не надо", callback_data=f"kwno:{site_id}")]])
    await status.edit_text("\n".join(lines), reply_markup=kb)


@router.callback_query(F.data.startswith("kwno:"))
async def cb_keyword_decline(call: CallbackQuery, state: FSMContext):
    """«Не надо» is remembered: the weekly tip never re-suggests it."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site:
        await set_state(f"kw_declined:{site['id']}", "1")
        await set_state(f"kw_suggest:{site['id']}", None)
    await ack(call, "Ок, больше не предложу")
    await cb_main_menu(call, state)


@router.callback_query(F.data.startswith("kwok:"))
async def cb_keyword_accept(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    phrase = await get_state(f"kw_suggest:{site['id']}") if site else None
    if not site or not phrase:
        await ack(call, "Подсказка устарела — задай фразу в настройках сайта", show_alert=True)
        return
    await update_site_settings(site["id"], keyword=phrase, keyword_mode="present")
    await set_state(f"kw_suggest:{site['id']}", None)
    await ack(call, "Слежу ✅")
    await render(call, f"✅ Слежу, чтобы на {esc(site_label(site['url']))} была фраза «{esc(phrase)}». "
                       f"Поменять можно в настройках сайта.", back_button())


# ── Remove ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("delsite:"))
async def cb_site_del_confirm(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, убрать", callback_data=f"delok:{site['id']}")],
        [InlineKeyboardButton(text="← Отмена", callback_data=f"sset:{site['id']}")]])
    await render(call, f"Убрать {esc(site_label(site['url']))} из мониторинга?\n"
                       "Открытые инциденты по нему закроются, история останется.", kb)


@router.callback_query(F.data.startswith("delok:"))
async def cb_site_del_do(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site and await deactivate_site(site["id"]):
        await maintenance.remove_windows_for_site(site["id"])
        reset_site_schedule(site["id"])
        await render(call, f"🗑 {esc(site_label(site['url']))} больше не под присмотром. История сохранилась.",
                     back_button())
    else:
        await render(call, "Сайт уже убран.", back_button())


# ── Pause ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pause:"))
async def cb_pause(call: CallbackQuery):
    args = cb_args(call, 2)
    if not args or not args[0].isdigit():
        await ack(call, "Не понял", show_alert=True)
        return
    site = await get_site(int(args[0]))
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    sid, arg, host = site["id"], args[1], site_label(site["url"])
    if arg == "off":
        await maintenance.pause_site(sid, None)
        await ack(call, "Пауза снята")
        await call.message.answer(f"✅ {esc(host)}: снова пишу обо всём.")
        return
    minutes = int(arg) if arg.isdigit() else 60
    await maintenance.pause_site(sid, minutes)
    await ack(call, "Пауза включена")
    await call.message.answer(f"🔧 Понял, {esc(host)} чинится: {fmt_duration(minutes)} про него не пишу, "
                              f"проверки идут. Раньше снять: 📱 Меню → {esc(host)}.")


# ── Export / import ──────────────────────────────────────────────────────────

_EXPORT_FIELDS = sorted(SITE_SETTING_COLS)


async def export_document() -> dict:
    """Everything a person configured: sites with dials, digest hours, quiet
    hours, maintenance windows, task-control jobs and the integrations
    entered in the chat (tokens included — treat the file as a secret)."""
    sites = [{"url": s["url"], **{k: s.get(k) for k in _EXPORT_FIELDS if s.get(k) is not None}}
             for s in await get_all_sites()]
    quiet = settings.quiet_hours()
    return {
        "version": 2,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sites": sites,
        "settings": {
            "morning_hour": settings.morning_hour(), "evening_hour": settings.evening_hour(),
            "weekly_hour": settings.weekly_hour(), "quiet_hours": f"{quiet[0]}-{quiet[1]}" if quiet else None,
            "status_page": settings.status_page_enabled(), "timezone": settings.timezone(),
            "ring_only_down": settings.ring_only_down(),
        },
        "maintenance_windows": [{k: w[k] for k in ("site_id", "days", "start_min", "end_min")}
                                | {"site_url": (await get_site(w["site_id"]) or {}).get("url") if w.get("site_id") else None}
                                for w in maintenance.windows()],
        "heartbeat_jobs": settings.ui_heartbeat_jobs(),
        "integrations": integrations.stored(),
    }


async def apply_document(doc: dict) -> dict:
    """Apply an export document; returns counters for the summary."""
    counters = {"added": 0, "updated": 0, "skipped": [], "settings": 0, "windows": 0, "jobs": 0, "integrations": 0}
    items = doc.get("sites", doc) if isinstance(doc, dict) else doc
    by_url: dict[str, int] = {}
    for item in list(items)[:MAX_SITES]:
        if not isinstance(item, dict):
            counters["skipped"].append(str(item)[:60])
            continue
        url, _error = parse_site_input(str(item.get("url", "")))
        if not url:
            counters["skipped"].append(str(item.get("url", "?"))[:60])
            continue
        try:
            fields = {k: (str(v) if k in ("accepted_codes", "keyword", "keyword_mode",
                                          "http_method", "http_headers", "http_body")
                          else int(v)) for k, v in item.items()
                      if k in SITE_SETTING_COLS and v not in (None, "")}
        except (TypeError, ValueError):
            counters["skipped"].append(f"{url} (неверные настройки)")
            continue
        existing = await get_all_sites()
        if len(existing) >= MAX_SITES and url not in {s["url"] for s in existing}:
            counters["skipped"].append(f"{url} (лимит {MAX_SITES})")
            continue
        was = url in {s["url"] for s in existing}
        site_id = await activate_or_create_site(url)
        by_url[url] = site_id
        if fields:
            await update_site_settings(site_id, **fields)
        reset_site_schedule(site_id)
        counters["updated" if was else "added"] += 1
    version = doc.get("version", 1) if isinstance(doc, dict) else 1
    if not isinstance(version, int) or version < 2:
        return counters
    st = doc.get("settings") if isinstance(doc.get("settings"), dict) else {}

    def hour(v) -> bool:
        # 24 or -1 would be saved, then crash CronTrigger at every start.
        return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 23

    if hour(st.get("morning_hour")):
        await settings.set_morning_hour(st["morning_hour"])
        counters["settings"] += 1
    if "evening_hour" in st and (st["evening_hour"] is None or hour(st["evening_hour"])):
        await settings.set_evening_hour(st["evening_hour"])
        counters["settings"] += 1
    if hour(st.get("weekly_hour")):
        await settings.set_weekly_hour(st["weekly_hour"])
        counters["settings"] += 1
    if "quiet_hours" in st:
        await settings.set_quiet_hours(st["quiet_hours"] or None)
        counters["settings"] += 1
    if isinstance(st.get("status_page"), bool):
        await settings.set_status_page(st["status_page"])
        counters["settings"] += 1
    if st.get("timezone") and await settings.set_timezone(st["timezone"]):
        counters["settings"] += 1
    if isinstance(st.get("ring_only_down"), bool):
        await settings.set_ring_only_down(st["ring_only_down"])
        counters["settings"] += 1
    for w in doc.get("maintenance_windows") if isinstance(doc.get("maintenance_windows"), list) else []:
        try:
            site_id = by_url.get(w.get("site_url")) if w.get("site_url") else None
            if w.get("site_url") and site_id is None:
                continue
            await maintenance.add_window(site_id, [int(d) for d in w.get("days") or []],
                                         int(w["start_min"]), int(w["end_min"]))
            counters["windows"] += 1
        except (KeyError, TypeError, ValueError):
            continue
    jobs = doc.get("heartbeat_jobs") if isinstance(doc.get("heartbeat_jobs"), dict) else {}
    for name, interval in jobs.items():
        if str(interval).isdigit() and await settings.add_heartbeat_job(str(name).strip().lower(), int(interval)):
            counters["jobs"] += 1
        else:
            counters["skipped"].append(f"задача {str(name)[:20]}")
    links = doc.get("integrations") if isinstance(doc.get("integrations"), dict) else {}
    for key, value in links.items():
        if key in integrations.KEYS and isinstance(value, str):
            await integrations.set_value(key, value)
            counters["integrations"] += 1
    reschedule_all()
    return counters


@router.callback_query(F.data == "site_export")
async def cb_site_export(call: CallbackQuery):
    await ack(call)
    doc = await export_document()
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode()
    await call.message.answer_document(
        BufferedInputFile(data, filename="tofsdevops-export.json"),
        caption=(f"📤 {len(doc['sites'])} сайтов с настройками, сводки, тихие часы, плановые работы, "
                 f"задачи и боты, подключения. В файле есть токены — храни его как пароль. "
                 f"Восстановить: «⚙️ Настройки» → 📥."))


@router.callback_query(F.data == "site_import")
async def cb_site_import(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(ImportForm.data)
    await render(call, "📥 Пришли файл экспорта (JSON) или копию базы (.db из воскресного отчёта).\n"
                       "JSON добавит и обновит сайты и настройки, лишнее не удалит. Копия базы заменит всё "
                       "целиком, текущая база сохранится рядом как .bak.", cancel_kb())


@router.message(ImportForm.data)
async def msg_site_import(message: Message, state: FSMContext):
    raw_bytes = (message.text or "").encode()
    if message.document:
        # The cloud Bot API downloads files up to 20 MB, whatever we allow.
        if (message.document.file_size or 0) > 20 * 1024 * 1024:
            await message.answer("Файл слишком большой (лимит 20 МБ — столько отдаёт Telegram).",
                                 reply_markup=cancel_kb())
            return
        buf = await message.bot.download(message.document)
        raw_bytes = buf.read()
    if raw_bytes.startswith(b"SQLite format 3"):
        await state.clear()
        try:
            n = await restore_from_bytes(raw_bytes)
        except ValueError as e:
            await message.answer(f"❌ Это не похоже на копию базы бота: {esc(e)}.", reply_markup=back_button())
            return
        await reload_services()
        await message.answer(f"✅ База восстановлена из копии: активных сайтов {n}. Прежняя база лежит "
                             f"рядом как .bak. Проверки продолжаются.", reply_markup=back_button())
        return
    try:
        doc = json.loads(raw_bytes.decode("utf-8", errors="replace"))
        items = doc["sites"] if isinstance(doc, dict) else doc
        assert isinstance(items, list)
    except (ValueError, KeyError, AssertionError):
        await message.answer("Не смог разобрать файл. Нужен JSON из «📤 Экспорт» или копия базы .db.",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    c = await apply_document(doc)
    text = f"📥 Импорт: сайтов добавлено {c['added']}, обновлено {c['updated']}."
    extras = [f"настроек {c['settings']}" if c["settings"] else "",
              f"плановых работ {c['windows']}" if c["windows"] else "",
              f"задач {c['jobs']}" if c["jobs"] else "",
              f"подключений {c['integrations']}" if c["integrations"] else ""]
    extras = [e for e in extras if e]
    if extras:
        text += " Также: " + ", ".join(extras) + "."
    if c["skipped"]:
        text += "\nПропущено:\n" + "\n".join(f"  • {esc(s)}" for s in c["skipped"][:10])
    await message.answer(text, reply_markup=back_button())


async def reload_services():
    """After a database restore every in-memory cache must be re-read — and
    the admin seat written back, or the restored file may leave it free."""
    await runtime.persist()
    await secrets.load()
    await settings.load()
    await maintenance.load()
    await integrations.load()
    reschedule_all()
