"""🌍 Sites: detail screen, add/remove, pause, export/import."""

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
    get_active_incidents,
    get_all_sites,
    get_site,
    get_state,
    get_uptime_over_days,
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
from handlers.menu import best_ms, site_state
from handlers.start import rules_of_the_game
from monitors.availability import check_availability
from monitors.domain_checker import check_domain
from monitors.pagemeta import alt_host_note, suggest_keyword
from monitors.ssl_checker import check_ssl
from reports.formatter import incident_line
from reports.scheduler import reset_site_schedule
from services import humanize, maintenance, settings, sitestatus
from utils.clock import fmt_local, local_at
from utils.text import esc, fmt_date, fmt_duration, plural
from utils.urls import is_http_url, short_host, site_label

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


def parse_site_input(raw: str) -> tuple[str | None, str | None]:
    """User text → canonical monitor URL, or (None, error message)."""
    raw = (raw or "").strip().lower()
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
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not _DOMAIN_RE.fullmatch(host) or not _valid_lengths(host):
        return None, "Не похоже на домен. Пришли что-то вроде example.com"
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    return f"{parsed.scheme}://{host}{port}", None


# ── Detail screen ────────────────────────────────────────────────────────────

@router.callback_query(F.data.in_({"menu_sites", "menu_check_site"}))
async def cb_sites_list(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    sites_ = await get_all_sites()
    lines = ["🌍 Сайты — тап по сайту открывает карточку со всем сразу:\n"]
    rows = []
    for site in sites_:
        icon, state_txt, _ = await site_state(site)
        lines.append(f"{icon} {esc(site_label(site['url']))} · {esc(state_txt)}")
        rows.append([InlineKeyboardButton(text=f"{icon} {site_label(site['url'])}",
                                          callback_data=f"check_site:{site['id']}")])
    if not sites_:
        lines.append("Пока ни одного.")
    rows.append([InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")])
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


def _ago(section: dict | None) -> str:
    age = sitestatus.age_minutes(section)
    if age is None:
        return ""
    return " · только что" if age < 1 else f" · {fmt_duration(age)} назад"


async def card_lines(site: dict) -> list[str]:
    """Everything about one site from the stored snapshot — instant."""
    st = await sitestatus.get(site["id"])
    http = is_http_url(site["url"])
    lines: list[str] = []
    avail = st.get("avail")
    if not avail:
        lines.append("⏳ Ещё не проверял — первая проверка в течение минуты")
    elif avail.get("status") == "ok":
        ms, region = best_ms(st)
        verb = "Открывается" if http else "Отвечает"
        lines.append(f"✅ {verb}, {humanize.speed(ms, region)}{_ago(avail)}")
    else:
        verb = "Не открывается" if http else "Не отвечает"
        lines.append(f"🔴 {verb}: {esc(humanize.describe_error(avail.get('error')))}{_ago(avail)}")
    if http:
        ssl = st.get("ssl")
        if not ssl:
            lines.append("⏳ Сертификат: ещё не проверял")
        elif ssl.get("days_left") is not None:
            days = ssl["days_left"]
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            issuer = f", выдан {esc(ssl['issuer'])}" if ssl.get("issuer") else ""
            lines.append(f"{icon} Сертификат действует ещё {days} дн. (до {fmt_date(ssl.get('not_after'))}){issuer}")
        else:
            lines.append(f"🔴 Сертификат: {esc(humanize.describe_error(ssl.get('error')))}")
        dom = st.get("domain")
        if not dom:
            lines.append("⏳ Домен: ещё не проверял")
        elif dom.get("days_left") is not None:
            days = dom["days_left"]
            icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
            registrar = (f", регистратор {esc(dom['registrar'])}"
                         if dom.get("registrar") not in (None, "", "Unknown") else "")
            lines.append(f"{icon} Домен оплачен до {fmt_date(dom.get('expiration'))} (ещё {days} дн.){registrar}")
        elif dom.get("unsupported"):
            lines.append(f"ℹ️ Домен: {esc(dom.get('error') or 'срок не сообщается')}")
        else:
            lines.append(f"⚠️ Домен: {esc(humanize.describe_error(dom.get('error')))}")
        links = st.get("links")
        if links:
            if links.get("status") == "ok":
                lines.append(f"✅ Ссылки: все работают{_ago(links)}")
            elif links.get("status") == "warning":
                n = links.get("internal") or 0
                lines.append(f"⚠️ Ссылки: {n} {plural(n, 'ведёт', 'ведут', 'ведут')} в никуда{_ago(links)}")
            else:
                lines.append(f"⚠️ Ссылки: не смог проверить{_ago(links)}")
        seo = st.get("seo")
        if seo:
            verdict = {"ok": "✅ В поиске: всё в порядке", "warning": "💡 В поиске: виден, есть что улучшить",
                       "critical": "🔴 В поиске: сайт закрыт от поисковиков!"}.get(seo.get("status"),
                                                                                     "⚠️ В поиске: не смог проверить")
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
        lines += [f"🔴 {esc(incident_line(inc))} <i>с {fmt_local(inc['created_at'])}</i>" for inc in problems[:3]]
    rows = [[InlineKeyboardButton(text="🔍 Проверить сейчас", callback_data=f"check_site_live:{sid}"),
             *pause_row[:1]]]
    if is_http_url(url):
        rows.append([InlineKeyboardButton(text="🔎 Поиск и ИИ", callback_data=f"seo_site:{sid}"),
                     InlineKeyboardButton(text="🔗 Ссылки", callback_data=f"check_links:{sid}")])
    last_row = []
    if problems:
        last_row.append(InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{problems[0]['id']}"))
    last_row.append(InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sset:{sid}"))
    rows.append(last_row)
    rows.append([InlineKeyboardButton(text="← Сайты", callback_data="menu_sites")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    """The card, instantly, from the last stored checks."""
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился. Открой меню заново.",
                     back_button("← Сайты", "menu_sites"))
        return
    await render_card(call, site)


@router.callback_query(F.data.startswith("check_site_live:"))
async def cb_check_site_live(call: CallbackQuery):
    """«🔍 Проверить сейчас»: fresh availability, certificate and domain,
    then the same card."""
    await ack(call, "Проверяю…")
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button("← Сайты", "menu_sites"))
        return
    url = site["url"]
    await render(call, f"▱▱▱ Проверяю, открывается ли {esc(site_label(url))}...")
    await check_availability(url, manage=False)
    if is_http_url(url):
        await render(call, "▰▱▱ Смотрю сертификат...")
        await check_ssl(url, manage=False)
        await render(call, "▰▰▱ Узнаю срок домена...")
        await check_domain(url, manage=False)
    await render_card(call, site)


# ── Add ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "site_add")
async def cb_site_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    if len(await get_all_sites()) >= MAX_SITES:
        await render(call, f"Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга.",
                     back_button())
        return
    await state.set_state(AddSiteForm.url)
    await render(call, "➕ Пришли адрес сайта, например <code>example.com</code>.", _add_kb())


def _add_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Продвинутое: порты и ping", callback_data="site_add_adv")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])


@router.callback_query(F.data == "site_add_adv")
async def cb_site_add_advanced(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddSiteForm.url)
    await render(call, "🛠 Кроме сайтов я умею следить за сервисами без веб-страницы:\n\n"
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
    lines = [f"✅ {esc(label)} под присмотром.\n"]
    if r.ok:
        lines.append(f"✅ Открывается, {humanize.speed(r.response_time_ms)}" if is_http_url(url)
                     else f"✅ Отвечает, {humanize.speed(r.response_time_ms)}")
    else:
        lines.append(f"🔴 Сейчас не открывается: {esc(humanize.describe_error(r.error))}. "
                     f"Я уже слежу и напишу, когда поднимется.")
    if is_http_url(url):
        ssl_r = await check_ssl(url, manage=False)
        lines.append(f"🔒 Сертификат безопасности действует ещё {ssl_r.ssl_info.days_left} дн." if ssl_r.ssl_info
                     else f"⚠️ Сертификат: {esc(humanize.describe_error(ssl_r.error))}")
        lines.append("\nСрок домена, ссылки и видимость в поиске проверю в ближайшие часы сам.")
    if first_site:
        lines.append("\n" + rules_of_the_game())
    reset_site_schedule(site_id)
    kb = back_button()
    if is_http_url(url) and r.ok:
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
                 InlineKeyboardButton(text="Не надо", callback_data="menu_main")]])
    await status.edit_text("\n".join(lines), reply_markup=kb)


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
    await render(call, f"Убрать {esc(short_host(site['url']))} из мониторинга?\n"
                       "Открытые инциденты по нему закроются, история останется.", kb)


@router.callback_query(F.data.startswith("delok:"))
async def cb_site_del_do(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site and await deactivate_site(site["id"]):
        await maintenance.remove_windows_for_site(site["id"])
        reset_site_schedule(site["id"])
        await render(call, f"🗑 {esc(short_host(site['url']))} больше не под присмотром. История сохранилась.",
                     back_button("← Сайты", "menu_sites"))
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
    sid, arg, host = site["id"], args[1], short_host(site["url"])
    if arg == "off":
        await maintenance.pause_site(sid, None)
        await ack(call, "Пауза снята")
        await call.message.answer(f"✅ {esc(host)}: снова пишу обо всём. Ничего делать не нужно.")
        return
    if arg == "morning":
        target = local_at(settings.morning_hour())
        minutes = max(1, int((target - datetime.now(UTC)).total_seconds() / 60))
    else:
        minutes = int(arg) if arg.isdigit() else 60
    await maintenance.pause_site(sid, minutes)
    await ack(call, "Пауза включена")
    await call.message.answer(f"🔧 Понял, {esc(host)} чинится: {fmt_duration(minutes)} про него не пишу, "
                              f"проверки идут. Раньше снять: «🌍 Мои сайты» → сайт.")


# ── Export / import ──────────────────────────────────────────────────────────

_EXPORT_FIELDS = sorted(SITE_SETTING_COLS)


@router.callback_query(F.data == "site_export")
async def cb_site_export(call: CallbackQuery):
    await ack(call)
    sites = [{"url": s["url"], **{k: s.get(k) for k in _EXPORT_FIELDS if s.get(k) is not None}}
             for s in await get_all_sites()]
    doc = {"version": 1, "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "sites": sites}
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode()
    await call.message.answer_document(
        BufferedInputFile(data, filename="tofsdevops-sites.json"),
        caption=f"📤 {len(sites)} сайтов с настройками. Импорт: «⚙️ Настройки» → 📥.")


@router.callback_query(F.data == "site_import")
async def cb_site_import(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(ImportForm.data)
    await render(call, "📥 Пришли JSON из экспорта — файлом или текстом.\n"
                       "Сайты добавятся или обновятся, лишние не удалятся.", cancel_kb())


@router.message(ImportForm.data)
async def msg_site_import(message: Message, state: FSMContext):
    raw = message.text or ""
    if message.document:
        if (message.document.file_size or 0) > 256 * 1024:
            await message.answer("Файл слишком большой (лимит 256 КБ).", reply_markup=cancel_kb())
            return
        buf = await message.bot.download(message.document)
        raw = buf.read().decode("utf-8", errors="replace")
    try:
        doc = json.loads(raw)
        items = doc["sites"] if isinstance(doc, dict) else doc
        assert isinstance(items, list)
    except (ValueError, KeyError, AssertionError):
        await message.answer("Не смог разобрать JSON. Нужен формат из «📤 Экспорт».",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    added, updated, skipped = 0, 0, []
    for item in items[:MAX_SITES]:
        url, error = parse_site_input(str((item or {}).get("url", "")))
        if not url:
            skipped.append(str((item or {}).get("url", "?"))[:60])
            continue
        existing = await get_all_sites()
        if len(existing) >= MAX_SITES and url not in {s["url"] for s in existing}:
            skipped.append(f"{url} (лимит {MAX_SITES})")
            continue
        was = url in {s["url"] for s in existing}
        site_id = await activate_or_create_site(url)
        fields = {k: (str(v) if k in ("accepted_codes", "keyword", "keyword_mode",
                                      "http_method", "http_headers", "http_body")
                      else int(v)) for k, v in item.items()
                  if k in SITE_SETTING_COLS and v not in (None, "")}
        if fields:
            await update_site_settings(site_id, **fields)
        reset_site_schedule(site_id)
        updated += was
        added += not was
    text = f"📥 Импорт: добавлено {added}, обновлено {updated}."
    if skipped:
        text += "\nПропущено:\n" + "\n".join(f"  • {esc(s)}" for s in skipped[:10])
    await message.answer(text, reply_markup=back_button())
