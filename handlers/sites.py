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
    get_all_sites,
    get_site,
    update_site_settings,
)
from handlers.common import (
    MAX_SITES,
    back_button,
    cancel_kb,
    cb_args,
    render,
    site_by_cb,
    sites_keyboard,
)
from monitors.availability import check_availability
from monitors.ssl_checker import check_ssl
from reports.scheduler import reset_site_schedule
from services import maintenance, settings
from utils.clock import local_at
from utils.text import esc, fmt_date, fmt_duration
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

@router.callback_query(F.data == "menu_check_site")
async def cb_check_site_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await render(call, "🌍 Сайты в мониторинге — выбери для детальной проверки,\nили управляй списком:",
                 await sites_keyboard("check_site", manage=True))


async def _pause_rows(sid: int) -> tuple[list[InlineKeyboardButton], list[str]]:
    notes = []
    if await maintenance.paused_until(sid):
        notes.append("⏸ На паузе — алерты по сайту молчат.")
        row = [InlineKeyboardButton(text="▶️ Снять паузу", callback_data=f"pause:{sid}:off")]
    else:
        row = [InlineKeyboardButton(text="⏸ Пауза 1ч", callback_data=f"pause:{sid}:60"),
               InlineKeyboardButton(text="⏸ До утра", callback_data=f"pause:{sid}:morning")]
    if maintenance.maintenance_now(sid):
        notes.append("🔧 Сейчас действует тех. окно — алерты молчат.")
    return row, notes


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    await call.answer()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился. Открой меню заново.",
                     back_button())
        return
    url, sid = site["url"], site["id"]
    lines = [f"📋 Детальная проверка\n{esc(url)}\n"]
    if not is_http_url(url):
        await render(call, f"▱▱ Проверяю {esc(url)}...")
        r = await check_availability(url, manage=False)
        ms = f" ({r.response_time_ms}ms)" if r.response_time_ms is not None else ""
        lines.append(f"✅ Доступность: отвечает{ms}" if r.ok
                     else f"🔴 Доступность: {esc(r.error or 'недоступен')}")
        first_row = [InlineKeyboardButton(text="⚙️ Настройки сайта", callback_data=f"sset:{sid}")]
    else:
        await render(call, f"▱▱ Проверяю доступность {esc(url)}...")
        r = await check_availability(url, manage=False)
        await render(call, "▰▱ Доступность — готово\nПроверяю SSL...")
        ssl_r = await check_ssl(url, manage=False)
        ms = f" ({r.response_time_ms}ms)" if r.response_time_ms is not None else ""
        lines.append(f"✅ Доступность: HTTP {r.status_code or '—'}{ms}" if r.ok
                     else f"🔴 Доступность: {esc(r.error or 'недоступен')}")
        if ssl_r.ssl_info:
            days = ssl_r.ssl_info.days_left
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            lines += [f"{icon} SSL: {days} дн. до истечения",
                      f"   ↳ Издатель: {esc(ssl_r.ssl_info.issuer)}",
                      f"   ↳ Истекает: {fmt_date(ssl_r.ssl_info.not_after)}"]
        else:
            lines.append(f"🔴 SSL: {esc(ssl_r.error or 'N/A')}")
        lines.append("ℹ️ Домен: см. «📋 Ещё → 🌐 Домены» (RDAP, проверяется раз в день)")
        first_row = [InlineKeyboardButton(text="📸 Скрин страницы", callback_data=f"act:shot:{sid}"),
                     InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sset:{sid}")]
    pause_row, notes = await _pause_rows(sid)
    if notes:
        lines += ["", *notes]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        first_row, pause_row,
        [InlineKeyboardButton(text="← К списку сайтов", callback_data="menu_check_site")]])
    await render(call, "\n".join(lines), kb)


# ── Add ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "site_add")
async def cb_site_add(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if len(await get_all_sites()) >= MAX_SITES:
        await render(call, f"Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга.",
                     back_button())
        return
    await state.set_state(AddSiteForm.url)
    await render(call, "➕ Пришли домен или URL сайта (например, example.com).\n\n"
                       "Также понимаю:\n"
                       "• <code>tcp://host:порт</code> — проверка TCP-порта (почта, SSH, БД)\n"
                       "• <code>ping://host</code> — ICMP-пинг хоста", cancel_kb())


@router.message(AddSiteForm.url)
async def msg_site_add(message: Message, state: FSMContext):
    url, error = parse_site_input(message.text or "")
    if not url:
        await message.answer(error, reply_markup=cancel_kb())
        return
    await state.clear()
    site_id = await activate_or_create_site(url)
    label = site_label(url)
    status = await message.answer(f"▱▱ Добавил {esc(label)} — делаю первую проверку...")
    # Read-only first look: the scheduled monitor (next minute tick) owns
    # incidents and alert ladders; a manage=True check here would silently
    # consume the SSL alert for a nearly-expired certificate.
    r = await check_availability(url, manage=False)
    lines = [f"✅ {esc(label)} в мониторинге (проверка каждые {config.check_interval_minutes} мин)\n"]
    ms = f" ({r.response_time_ms}ms)" if r.response_time_ms is not None else ""
    if r.ok:
        lines.append(f"✅ Доступность: HTTP {r.status_code}{ms}" if is_http_url(url)
                     else f"✅ Отвечает{ms}")
    else:
        lines.append(f"🔴 Не отвечает: {esc(r.error or 'N/A')} — я уже слежу, сообщу о восстановлении")
    if is_http_url(url):
        ssl_r = await check_ssl(url, manage=False)
        lines.append(f"🔒 SSL: {ssl_r.ssl_info.days_left} дн. до истечения" if ssl_r.ssl_info
                     else f"⚠️ SSL: {esc(ssl_r.error or 'N/A')}")
        lines.append("\nDNS-эталон и SEO-аудит сниму в ближайшие часы автоматически.")
    reset_site_schedule(site_id)
    await status.edit_text("\n".join(lines), reply_markup=back_button())


# ── Remove ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "site_del")
async def cb_site_del(call: CallbackQuery):
    await call.answer()
    await render(call, "🗑 Какой сайт убрать из мониторинга?\n(история проверок сохранится)",
                 await sites_keyboard("delsite", icon="🗑", back="menu_check_site"))


@router.callback_query(F.data.startswith("delsite:"))
async def cb_site_del_confirm(call: CallbackQuery):
    await call.answer()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, убрать", callback_data=f"delok:{site['id']}")],
        [InlineKeyboardButton(text="← Отмена", callback_data="menu_check_site")]])
    await render(call, f"Убрать {esc(short_host(site['url']))} из мониторинга?\n"
                       "Открытые инциденты по нему закроются, история останется.", kb)


@router.callback_query(F.data.startswith("delok:"))
async def cb_site_del_do(call: CallbackQuery):
    await call.answer()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site and await deactivate_site(site["id"]):
        await maintenance.remove_windows_for_site(site["id"])
        reset_site_schedule(site["id"])
        await render(call, f"🗑 {esc(short_host(site['url']))} убран из мониторинга.", back_button())
    else:
        await render(call, "Сайт уже убран.", back_button())


# ── Pause ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pause:"))
async def cb_pause(call: CallbackQuery):
    args = cb_args(call, 2)
    if not args or not args[0].isdigit():
        await call.answer("Не понял", show_alert=True)
        return
    site = await get_site(int(args[0]))
    if not site:
        await call.answer("Сайт не найден", show_alert=True)
        return
    sid, arg, host = site["id"], args[1], short_host(site["url"])
    if arg == "off":
        await maintenance.pause_site(sid, None)
        await call.answer("Пауза снята")
        await call.message.answer(f"▶️ {esc(host)} — алерты снова включены.")
        return
    if arg == "morning":
        target = local_at(settings.morning_hour())
        minutes = max(1, int((target - datetime.now(UTC)).total_seconds() / 60))
    else:
        minutes = int(arg) if arg.isdigit() else 60
    await maintenance.pause_site(sid, minutes)
    await call.answer("Пауза включена")
    await call.message.answer(f"⏸ {esc(host)} — алерты на паузе на {fmt_duration(minutes)} "
                              f"(проверки продолжаются). Снять: «🌍 Сайт детально» → сайт.")


# ── Export / import ──────────────────────────────────────────────────────────

_EXPORT_FIELDS = sorted(SITE_SETTING_COLS)


@router.callback_query(F.data == "site_export")
async def cb_site_export(call: CallbackQuery):
    await call.answer()
    sites = [{"url": s["url"], **{k: s.get(k) for k in _EXPORT_FIELDS if s.get(k) is not None}}
             for s in await get_all_sites()]
    doc = {"version": 1, "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
           "sites": sites}
    data = json.dumps(doc, ensure_ascii=False, indent=2).encode()
    await call.message.answer_document(
        BufferedInputFile(data, filename="tofsdevops-sites.json"),
        caption=f"📤 {len(sites)} сайтов с настройками. Импорт: «🌍 Сайт детально» → 📥.")


@router.callback_query(F.data == "site_import")
async def cb_site_import(call: CallbackQuery, state: FSMContext):
    await call.answer()
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
