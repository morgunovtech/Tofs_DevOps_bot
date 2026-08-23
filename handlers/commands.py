import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, ErrorEvent, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import config
from monitors.availability import check_all
from monitors.ssl_checker import check_all_ssl
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.availability import check_availability
from monitors.ssl_checker import check_ssl
from monitors.domain_checker import check_domain
from urllib.parse import urlparse

from db.database import (
    get_active_incidents, get_all_sites, get_uptime_stats, get_or_create_site,
    get_state, set_state, resolve_all_incidents, get_last_check,
    get_active_site_urls, get_active_http_site_urls, is_http_url,
    get_site, activate_or_create_site, deactivate_site, update_site_settings,
    resolve_incident_by_id, get_heartbeats, get_recent_response_times,
)
from services import settings
from reports.formatter import (
    format_status_report, format_links_report, format_uptime,
    format_compact_status_report, now_local, fmt_date, _short_host, _esc,
    sparkline, plural, fmt_local, site_label,
)
from services.actions import trigger_redeploy, purge_cf_cache
from services.screenshots import fetch_screenshot

logger = logging.getLogger(__name__)
router = Router()


# ── Keyboards ────────────────────────────────────────────────────────────────

async def _mute_until_local() -> str | None:
    """Human-readable local time the mute expires, or None when not muted."""
    until_iso = await get_state("mute_until")
    if not until_iso:
        return None
    try:
        deadline = datetime.fromisoformat(until_iso)
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    if deadline <= now_utc:
        return None
    local = now_local() + (deadline - now_utc)
    fmt = "%H:%M" if deadline - now_utc < timedelta(hours=20) else "%d.%m %H:%M"
    return local.strftime(fmt)


async def build_main_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Main menu as a mini-dashboard: the header answers "is everything OK?"
    before any tap, and button labels carry live state (incident count,
    mute-until time). All data comes from the DB — opening the menu must be
    instant, no network checks here."""
    sites = await get_all_sites()
    if not sites:
        # Empty state: guide to the first site instead of a hollow dashboard.
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить первый сайт", callback_data="site_add")],
            [InlineKeyboardButton(text="📋 Ещё", callback_data="menu_more")],
        ])
        return ("👋 Сайтов пока нет. Добавь первый — и я начну проверять "
                f"его каждые {config.check_interval_minutes} мин.", kb)
    ok = total = 0
    paused_count = 0
    for s in sites:
        if await settings.is_paused(s["id"]):
            paused_count += 1
        last = await get_last_check(s["id"], "availability")
        if last:
            total += 1
            if last["status"] == "ok":
                ok += 1
    incidents = await get_active_incidents()
    mute_until = await _mute_until_local()

    if total:
        site_chip = f"{'✅' if ok == total else '⚠️'} {ok}/{total} сайтов ок"
    else:
        site_chip = "⏳ ещё нет проверок"
    inc_chip = (f"инцидентов: {len(incidents)}" if incidents
                else "инцидентов нет")
    mute_chip = f"🔕 тихо до {mute_until}" if mute_until else "🔔 алерты вкл"
    header = f"{site_chip} · {inc_chip} · {mute_chip}"
    if paused_count:
        header += f" · ⏸ на паузе: {paused_count}"
    if settings.maintenance_now(None):
        header += " · 🔧 тех. окно"
    from reports.scheduler import in_quiet_hours
    if in_quiet_hours():
        header += " · 🌙 тихие часы"

    inc_label = (f"⚠️ Инциденты ({len(incidents)})" if incidents
                 else "⚠️ Инциденты")
    mute_label = f"🔕 Тихо до {mute_until}" if mute_until else "🔕 Тишина"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Полная проверка", callback_data="run_full_check"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус",        callback_data="menu_status"),
            InlineKeyboardButton(text=inc_label,          callback_data="menu_incidents"),
        ],
        [
            InlineKeyboardButton(text="🌍 Сайт детально", callback_data="menu_check_site"),
            InlineKeyboardButton(text="🔍 SEO/GEO",       callback_data="menu_seo"),
        ],
        [
            InlineKeyboardButton(text="📋 Ещё",           callback_data="menu_more"),
            InlineKeyboardButton(text=mute_label,         callback_data="menu_mute"),
        ],
    ])
    return header, kb


def more_menu() -> InlineKeyboardMarkup:
    """Second-level menu: direct views that are useful occasionally but
    don't deserve first-screen real estate (they're all in the daily
    reports anyway)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔒 SSL",    callback_data="menu_ssl"),
            InlineKeyboardButton(text="🌐 Домены", callback_data="menu_domains"),
        ],
        [
            InlineKeyboardButton(text="🔗 Ссылки", callback_data="menu_links"),
            InlineKeyboardButton(text="📈 Uptime", callback_data="menu_uptime"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки",  callback_data="menu_settings"),
            InlineKeyboardButton(text="💓 Heartbeats", callback_data="menu_hb"),
        ],
        [
            InlineKeyboardButton(text="🩺 Диагностика", callback_data="menu_diag"),
            InlineKeyboardButton(text="🔔 Тест алерта", callback_data="menu_testalert"),
        ],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
    ])


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]
    ])


def persistent_keyboard() -> ReplyKeyboardMarkup:
    """Always-visible bottom keyboard so the menu is one tap away."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Меню"), KeyboardButton(text="📊 Статус")],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Тапни «📱 Меню» или введи команду",
    )


async def sites_keyboard(action_prefix: str,
                         manage: bool = False,
                         http_only: bool = False) -> InlineKeyboardMarkup:
    """Keyboard with a button per active site (from the DB).

    callback_data carries the site's DB id — stable across list edits and
    well under Telegram's 64-byte callback_data limit. `manage=True` adds
    the add/remove controls (used on the "Сайт детально" screen).
    `http_only=True` hides tcp://-and-ping:// monitors — for pickers of
    checks that only make sense for web pages (links, SEO).
    """
    sites = await get_all_sites()
    if http_only:
        sites = [s for s in sites if is_http_url(s["url"])]
    icon = "🗑" if action_prefix == "delsite" else "🔍"
    buttons = []
    for site in sites:
        buttons.append([InlineKeyboardButton(
            text=f"{icon} {site_label(site['url'])}",
            callback_data=f"{action_prefix}:{site['id']}")])
    if manage:
        row = [InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")]
        if sites:
            row.append(InlineKeyboardButton(text="🗑 Удалить", callback_data="site_del"))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _site_by_cb(arg: str) -> dict | None:
    """Resolve a callback site id back to an active site row."""
    if not arg.isdigit():
        return None
    site = await get_site(int(arg))
    return site if site and site["active"] else None


class AddSiteForm(StatesGroup):
    url = State()


class AddHeartbeatForm(StatesGroup):
    name = State()


class SiteCodesForm(StatesGroup):
    codes = State()


class SiteKeywordForm(StatesGroup):
    keyword = State()


class MaintTimeForm(StatesGroup):
    time = State()


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    # admin_chat_id fallback only works for private chats (user id == chat id);
    # set TELEGRAM_ADMIN_USER_ID if the admin chat is a group/channel.
    return str(user_id) == (config.admin_user_id or config.admin_chat_id)


TG_MESSAGE_LIMIT = 4096


def _clip(text: str, limit: int = TG_MESSAGE_LIMIT - 100) -> str:
    """Keep a message under Telegram's 4096-char limit instead of failing."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (обрезано)"


async def _with_running_bar(message: Message, base_text: str, coro,
                            tick: float = 4.0):
    """Await a long coroutine while a runner segment (▰▱▱ → ▱▰▱ → ▱▱▰)
    metronomes on the status message, so 30-second operations don't look
    frozen. Edits are throttled to one per `tick` seconds."""
    task = asyncio.ensure_future(coro)
    frames = ("▰▱▱", "▱▰▱", "▱▱▰", "▱▰▱")
    i = 0
    while True:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=tick)
        except asyncio.TimeoutError:
            # A TimeoutError can also come from INSIDE the finished task —
            # re-raise it instead of treating it as an animation tick,
            # or this loop would spin hot editing the message forever.
            if task.done():
                return task.result()
            i += 1
            try:
                await message.edit_text(f"{frames[i % len(frames)]} {base_text}")
            except TelegramBadRequest:
                pass


async def send_main_menu(target):
    """Send main menu — works for both Message and CallbackQuery."""
    header, kb = await build_main_menu()
    if isinstance(target, CallbackQuery):
        try:
            await target.message.edit_text(header, reply_markup=kb)
        except TelegramBadRequest:
            # "message is not modified" — the dashboard state hasn't changed
            # since the menu was last drawn; nothing to do.
            pass
    else:
        await target.answer(header, reply_markup=kb)


# ── Error handler ────────────────────────────────────────────────────────────

@router.errors()
async def on_handler_error(event: ErrorEvent):
    """Catch-all so a failed check (WHOIS timeout etc.) doesn't leave the
    menu stuck on a '▱▱▱ ...' message with no keyboard and no way back."""
    update = event.update
    call = update.callback_query if update else None

    # Double-tap on any button re-renders identical content — Telegram
    # answers 400 "message is not modified". That's not an error at all:
    # acknowledge the tap and keep the screen intact.
    if "message is not modified" in str(event.exception):
        if call:
            try:
                await call.answer()
            except Exception:
                pass
        return True

    logger.exception("Handler error: %s", event.exception)
    try:
        if call and (call.data or "").startswith("act:"):
            # Action buttons live ON alert messages — never overwrite the
            # alert text with an error screen; reply separately instead.
            await call.message.answer(
                "❌ Действие не удалось. Попробуй ещё раз чуть позже.")
        elif call and call.message:
            await call.message.edit_text(
                "❌ Что-то пошло не так во время проверки.\n"
                "Попробуй ещё раз чуть позже.",
                reply_markup=back_button(),
            )
        elif update and update.message:
            await update.message.answer(
                "❌ Что-то пошло не так. Попробуй ещё раз чуть позже."
            )
    except Exception:
        # The message may be too old to edit (48h+) — at least acknowledge
        # the tap so the button doesn't die silently.
        if call:
            try:
                await call.answer("Сообщение устарело — открой /menu",
                                  show_alert=True)
            except Exception:
                pass
    return True


# ── /start and /help ─────────────────────────────────────────────────────────

@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()  # /start always aborts any pending input flow
    # First-run: no admin configured anywhere — the first /start claims it.
    if not config.admin_chat_id:
        config.admin_chat_id = str(message.chat.id)
        config.admin_user_id = str(message.from_user.id)
        await set_state("admin_chat_id", config.admin_chat_id)
        await set_state("admin_user_id", config.admin_user_id)
        await message.answer(
            "🔑 Ты стал администратором этого бота — все отчёты и алерты "
            "будут приходить сюда."
        )
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    # Install persistent reply keyboard once — stays visible at the bottom.
    await message.answer(
        "👋 Привет! Я TofsDevOps — личный девопс твоих сайтов.\n"
        "Кнопка «📱 Меню» внизу всегда под рукой.\n\n"
        "Назван в честь Тофса — ирландского терьера, который принимает "
        "аптайм близко к сердцу.",
        reply_markup=persistent_keyboard(),
    )
    await send_main_menu(message)


# ── Reply-keyboard taps ──────────────────────────────────────────────────────

@router.message(F.text == "📱 Меню")
async def reply_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()  # a menu tap always aborts any pending input flow
    await send_main_menu(message)


@router.message(F.text == "📊 Статус")
async def reply_status(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await cmd_status(message)


# ── /menu ─────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await state.clear()
    await send_main_menu(message)


# ── /status — quick one-line summary ─────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    urls = await get_active_site_urls()
    if not urls:
        await message.answer("Сайтов пока нет — добавь через 📱 Меню → «🌍 Сайт детально».")
        return
    progress = await message.answer("▱▱▱ Проверяю...")
    availability = await check_all(urls, manage=False)
    incidents = await get_active_incidents()
    text = format_compact_status_report(
        availability=availability, incidents=incidents,
    )
    await progress.edit_text(_clip(text), reply_markup=back_button())


# ── /sites — list configured sites ───────────────────────────────────────────

@router.message(Command("sites"))
async def cmd_sites(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    urls = await get_active_site_urls()
    if not urls:
        await message.answer("Сайтов пока нет — добавь через 📱 Меню → «🌍 Сайт детально».")
        return
    text = "🌍 Отслеживаю:\n" + "\n".join(f"  • {u}" for u in urls)
    await message.answer(text)


# ── /mute и /unmute ──────────────────────────────────────────────────────────

def _parse_duration(arg: str) -> timedelta | None:
    """Parse '1h', '8h', '30m', '1d' → timedelta. Default unit: minutes."""
    if not arg:
        return None
    arg = arg.strip().lower()
    unit_map = {"m": 60, "h": 3600, "d": 86400}
    if arg[-1] in unit_map:
        try:
            num = int(arg[:-1])
        except ValueError:
            return None
        return timedelta(seconds=num * unit_map[arg[-1]])
    try:
        return timedelta(minutes=int(arg))
    except ValueError:
        return None


@router.message(Command("mute"))
async def cmd_mute(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1] if len(parts) > 1 else "8h"
    duration = _parse_duration(arg)
    if not duration:
        await message.answer(
            "Использование: /mute &lt;время&gt;\n"
            "Примеры: /mute 1h, /mute 8h, /mute 30m, /mute 1d\n"
            "По умолчанию: 8h"
        )
        return
    deadline = datetime.now(timezone.utc) + duration
    await set_state("mute_until", deadline.isoformat())
    local_until = (now_local() + duration).strftime("%d.%m %H:%M")
    await message.answer(
        f"🔕 Алерты приглушены до {local_until}.\n"
        f"Критические алерты (сайт лежит) всё равно придут.\n"
        f"Снять: /unmute"
    )


@router.message(Command("unmute"))
async def cmd_unmute(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await set_state("mute_until", None)
    await message.answer("🔔 Алерты снова включены.")


# ── 🔕 Mute via inline menu ──────────────────────────────────────────────────

def _mute_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="1 час",  callback_data="mute:1h"),
            InlineKeyboardButton(text="4 часа", callback_data="mute:4h"),
            InlineKeyboardButton(text="8 часов", callback_data="mute:8h"),
        ],
        [
            InlineKeyboardButton(text="1 день", callback_data="mute:1d"),
            InlineKeyboardButton(text="🔔 Снять тишину", callback_data="mute:off"),
        ],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
    ])


@router.callback_query(F.data == "menu_mute")
async def cb_mute_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    until_iso = await get_state("mute_until")
    status = ""
    if until_iso:
        try:
            deadline = datetime.fromisoformat(until_iso)
            if deadline.tzinfo is None:
                # Legacy value written by the old naive-utcnow code — it was UTC.
                deadline = deadline.replace(tzinfo=timezone.utc)
            now_utc = datetime.now(timezone.utc)
            if deadline > now_utc:
                local = (now_local() + (deadline - now_utc)).strftime("%d.%m %H:%M")
                status = f"🔕 Сейчас приглушено до {local}\n\n"
        except ValueError:
            pass
    await call.message.edit_text(
        f"{status}На сколько приглушить алерты?\n"
        f"(критические — «сайт лежит» — всё равно придут)",
        reply_markup=_mute_menu(),
    )


@router.callback_query(F.data.startswith("mute:"))
async def cb_mute_pick(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    arg = call.data.split(":", 1)[1]
    if arg == "off":
        await set_state("mute_until", None)
        await call.message.edit_text("🔔 Алерты снова включены.", reply_markup=back_button())
        return
    duration = _parse_duration(arg)
    if not duration:
        await call.message.edit_text("Не понял длительность.", reply_markup=back_button())
        return
    deadline = datetime.now(timezone.utc) + duration
    await set_state("mute_until", deadline.isoformat())
    local_until = (now_local() + duration).strftime("%d.%m %H:%M")
    await call.message.edit_text(
        f"🔕 Алерты приглушены до {local_until}.\n"
        f"Критические алерты всё равно придут.\n"
        f"Снять: /unmute",
        reply_markup=back_button(),
    )


# ── One-tap actions attached to alerts ───────────────────────────────────────
# callback_data: "act:<action>:<site index>". These arrive on alert messages,
# so results go out as replies — the alert text itself stays intact and the
# buttons remain usable.

@router.callback_query(F.data.startswith("act:"))
async def cb_alert_action(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return

    parts = call.data.split(":", 2)
    if len(parts) != 3:
        await call.answer("Не понял действие", show_alert=True)
        return
    _, action, sid = parts
    site = await _site_by_cb(sid)
    if not site:
        await call.answer("Сайт не найден или убран из мониторинга", show_alert=True)
        return
    url = site["url"]

    if action == "recheck":
        await call.answer("Проверяю…")
        avail = await check_availability(url, manage=False)
        if avail["status"] == "ok":
            ms = avail.get("response_time_ms")
            ms_part = f" ({ms}ms)" if ms is not None else ""
            if is_http_url(url):
                text = (f"🔍 {url} — доступен: "
                        f"HTTP {avail.get('status_code')}{ms_part}")
            else:
                text = f"🔍 {url} — отвечает{ms_part}"
        else:
            text = f"🔍 {url} — всё ещё недоступен: {_esc(avail.get('error', 'N/A'))}"
        await call.message.answer(text)
    elif action == "shot":
        await call.answer("Делаю скрин… (~15 сек)")
        # Native "sending a photo…" status in the chat header while we wait.
        try:
            await call.bot.send_chat_action(call.message.chat.id, "upload_photo")
        except Exception:
            pass
        image = await fetch_screenshot(url)
        if image:
            await call.message.answer_photo(
                BufferedInputFile(image, filename="screenshot.png"),
                caption=f"📸 {_short_host(url)} · {now_local().strftime('%d.%m %H:%M')}",
            )
        else:
            await call.message.answer(
                f"❌ Не удалось получить скрин {_short_host(url)} — "
                f"сервис рендеринга не ответил, попробуй ещё раз."
            )
    elif action == "redeploy":
        await call.answer("Запускаю передеплой…")
        await call.message.answer(await trigger_redeploy(url))
    elif action == "purge":
        await call.answer("Сбрасываю кэш…")
        await call.message.answer(await purge_cf_cache(url))
    else:
        await call.answer("Неизвестное действие", show_alert=True)


# ── Back to main menu ────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_main")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await state.clear()  # «← Главное меню» aborts any pending input flow
    await send_main_menu(call)


# ── 📋 "Ещё" submenu ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_more")
async def cb_more(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "📋 Дополнительные проверки:", reply_markup=more_menu(),
    )


# ── 🚀 Full real-time check ───────────────────────────────────────────────────

@router.callback_query(F.data == "run_full_check")
async def cb_full_check(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    urls = await get_active_site_urls()
    if not urls:
        await call.message.edit_text(
            "Сайтов пока нет — сначала добавь хотя бы один.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add")],
                [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
            ]))
        return

    # Step 1 — availability
    await call.message.edit_text(
        "🚀 Полная проверка\n\n"
        "▱▱▱▱ Проверяю доступность сайтов...",
        reply_markup=None
    )
    # manage=False everywhere below: interactive checks are read-only
    # observers and must never consume the scheduled monitors' transitions.
    availability = await check_all(urls, manage=False)
    # tcp:// and ping:// monitors are availability-only.
    http_urls = await get_active_http_site_urls()

    # Step 2 — SSL
    await call.message.edit_text(
        "🚀 Полная проверка\n\n"
        "▰▱▱▱ Доступность — готово\n"
        "Проверяю SSL-сертификаты...",
    )
    ssl_results = await check_all_ssl(http_urls, manage=False)

    # Step 3 — domains
    await call.message.edit_text(
        "🚀 Полная проверка\n\n"
        "▰▰▱▱ SSL — готово\n"
        "Проверяю домены...",
    )
    domain_results = await check_all_domains(http_urls, manage=False)

    # Step 4 — links
    await call.message.edit_text(
        "🚀 Полная проверка\n\n"
        "▰▰▰▱ Домены — готово\n"
        "Проверяю ссылки на страницах...",
    )
    links_results = await check_all_links(http_urls, manage=False)

    # Compose full report
    incidents = await get_active_incidents()
    report = format_status_report(
        availability=availability,
        incidents=incidents,
        ssl_results=ssl_results,
        domain_results=domain_results,
        report_type="status",
    )

    # Append links summary (internal-only is what matters)
    internal_broken = sum(len(r.get("broken_internal", [])) for r in links_results)
    external_broken = sum(len(r.get("broken_external", [])) for r in links_results)
    failed_scans = sum(1 for r in links_results if r.get("status") == "error")
    if failed_scans:
        report += (f"\n\n⚠️ Ссылки: не удалось просканировать "
                   f"{failed_scans} из {len(links_results)} сайтов")
    elif internal_broken:
        report += (
            f"\n\n⚠️ Битых внутренних ссылок: {internal_broken} шт. "
            "— нажми «🔗 Ссылки» для деталей"
        )
    elif external_broken:
        report += (
            f"\n\n✅ Внутренние ссылки в норме "
            f"(внешних недоступных: {external_broken}, не критично)"
        )
    else:
        report += "\n\n✅ Все ссылки в норме"

    await call.message.edit_text(_clip(report), reply_markup=back_button())


# ── 📊 Status ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_status")
async def cb_status(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    urls = await get_active_site_urls()
    if not urls:
        await call.message.edit_text(
            "Сайтов пока нет — сначала добавь хотя бы один.",
            reply_markup=back_button())
        return
    await call.message.edit_text("▱▱▱ Проверяю доступность...")
    availability = await check_all(urls, manage=False)
    incidents = await get_active_incidents()
    report = format_status_report(availability, incidents)

    await call.message.edit_text(_clip(report), reply_markup=back_button())


# ── 🔒 SSL ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_ssl")
async def cb_ssl(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("▱▱▱ Проверяю SSL-сертификаты...")

    urls = await get_active_http_site_urls()
    results = await check_all_ssl(urls, manage=False)

    lines = ["🔒 SSL-сертификаты:\n"]
    for r in results:
        if r.get("ssl_info"):
            info = r["ssl_info"]
            days = info["days_left"]
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            lines.append(f"{icon} {_short_host(r['url'])}")
            lines.append(f"   Осталось: {days} дн. (до {fmt_date(info['not_after'])})")
            lines.append(f"   Издатель: {_esc(info['issuer'])}")
        else:
            lines.append(f"🔴 {_short_host(r['url'])}: {_esc(r.get('error', 'N/A'))}")

    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


# ── 🌐 Domains ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_domains")
async def cb_domains(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("▱▱▱ Проверяю домены через WHOIS...")

    urls = await get_active_http_site_urls()
    results = await check_all_domains(urls, manage=False)

    lines = ["🌐 Домены:\n"]
    for r in results:
        info = r.get("domain_info")
        if info and info["days_left"] is not None:
            days = info["days_left"]
            icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
            exp = fmt_date(info["expiration_date"]) if info["expiration_date"] else "N/A"
            lines.append(f"{icon} {r['domain']}")
            lines.append(f"   Осталось: {days} дн. (до {exp})")
            lines.append(f"   Регистратор: {_esc(info['registrar'])}")
        else:
            lines.append(f"⚠️ {r.get('domain', r['url'])}: {_esc(r.get('error', 'N/A'))}")

    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


# ── 🔗 Links ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_links")
async def cb_links_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🔗 Выбери сайт для проверки ссылок:",
        reply_markup=await sites_keyboard("check_links", http_only=True)
    )


@router.callback_query(F.data.startswith("check_links:"))
async def cb_check_links(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    site = await _site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await call.message.edit_text(
            "Сайт не найден — список сайтов изменился. Открой меню заново.",
            reply_markup=back_button(),
        )
        return
    url = site["url"]
    base = f"Сканирую все ссылки на {url}…\n(это может занять ~30 сек)"
    await call.message.edit_text(f"▰▱▱ {base}")

    from monitors.links_checker import check_links
    result = await _with_running_bar(call.message, base,
                                     check_links(url, manage=False))

    if result.get("status") == "error":
        # A failed page fetch must not masquerade as "all links fine".
        text = (f"❌ Не удалось просканировать {url}: "
                f"{_esc(result.get('error') or 'страница не загрузилась')}")
    elif result.get("broken_internal"):
        text = format_links_report(result)
    else:
        ext = len(result.get("broken_external") or [])
        text = (
            f"✅ Все внутренние ссылки на {url} работают "
            f"(проверено: {result['total_links']})"
        )
        if ext:
            text += f"\nℹ️ Внешних ресурсов недоступно: {ext} — обычно не критично"

    await call.message.edit_text(_clip(text), reply_markup=back_button())


# ── 📈 Uptime ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_uptime")
async def cb_uptime(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    sites = await get_all_sites()
    if not sites:
        await call.message.edit_text("Нет данных — бот только запустился, подожди несколько минут.", reply_markup=back_button())
        return

    lines = ["📈 Uptime за 24 часа:\n"]
    for s in sites:
        stats = await get_uptime_stats(s["id"], hours=24)
        if stats["total_checks"] == 0:
            lines.append(f"⏳ {s['url']} — нет данных")
            continue
        icon = "✅" if stats["uptime_pct"] >= 99 else ("⚠️" if stats["uptime_pct"] >= 95 else "🔴")
        spark = sparkline(await get_recent_response_times(s["id"]))
        lines.append(
            f"{icon} {s['url']}\n"
            f"   Доступность: {stats['uptime_pct']}%\n"
            f"   Среднее время ответа: {stats['avg_response_ms']}ms"
            + (f"  {spark}" if spark else "") + "\n"
            f"   Всего проверок: {stats['total_checks']}"
        )

    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


# ── ⚠️ Incidents ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_incidents")
async def cb_incidents(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await _render_incidents(call)


async def _render_incidents(call: CallbackQuery):
    incidents = await get_active_incidents()
    if not incidents:
        text = "✅ Активных инцидентов нет — всё работает нормально!"
        kb = back_button()
    else:
        lines = [f"⚠️ Активные инциденты ({len(incidents)}):\n"]
        for inc in incidents:
            sev_icon = "🔴" if inc["severity"] == "critical" else "⚠️"
            lines.append(
                f"{sev_icon} {_esc(inc['url'])}\n"
                f"   Тип: {_esc(inc['check_type'])}\n"
                f"   Проблема: {_esc(inc['message'])}\n"
                f"   С: {fmt_local(inc['created_at'])}"
            )
        text = _clip("\n".join(lines))
        rows = [
            [InlineKeyboardButton(
                text=f"✓ Закрыть: {_short_host(inc['url'])} [{inc['check_type']}]",
                callback_data=f"inc_close:{inc['id']}")]
            for inc in incidents[:6]
        ]
        rows.append([InlineKeyboardButton(
            text="🗑 Сбросить все (если устарели)",
            callback_data="incidents_clear",
        )])
        rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
        kb = InlineKeyboardMarkup(inline_keyboard=rows)

    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data == "incidents_clear")
async def cb_incidents_clear(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    n = await resolve_all_incidents()
    await call.message.edit_text(
        f"🗑 Сброшено инцидентов: {n}\n"
        f"При следующей проверке те, что реальны, откроются заново.",
        reply_markup=back_button(),
    )


# ── 🔍 SEO/GEO audit ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_seo")
async def cb_seo(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    if not await get_active_http_site_urls():
        await call.message.edit_text(
            "Веб-сайтов пока нет — SEO-аудит не применим к tcp/ping-мониторам.",
            reply_markup=back_button())
        return
    base = ("Гоняю SEO/GEO-аудит по всем сайтам…\n"
            "(robots, sitemap, мета, noindex, AI-боты, контент без JS — ~30 сек)")
    await call.message.edit_text(f"▰▱▱ {base}")

    from monitors.seo_checker import check_all_seo
    from services import gsc, yandex_webmaster

    results = await _with_running_bar(
        call.message, base,
        check_all_seo(await get_active_http_site_urls(), manage=False))

    # Live index status (only when tokens are configured).
    gsc_status: dict[str, str] = {}
    if gsc.available():
        for u in await get_active_http_site_urls():
            info = await gsc.inspect_url(u.rstrip("/") + "/")
            if info:
                gsc_status[u] = ("в индексе ✅" if info["verdict"] == "PASS"
                                 else f"НЕ в индексе 🔴 ({info['coverage']})")
    yx_status: dict[str, dict] = {}
    if yandex_webmaster.available():
        yx_status = await yandex_webmaster.get_summaries() or {}

    text = build_seo_report(results, gsc_status, yx_status)
    await call.message.edit_text(_clip(text), reply_markup=back_button())


def build_seo_report(results: list[dict], gsc_status: dict[str, str],
                     yx_status: dict[str, dict]) -> str:
    """Assemble the on-demand audit message (pure — unit-tested for Telegram
    HTML safety: problem texts mention raw tags like «нет <title>» and must
    arrive escaped, or parse mode rejects the whole message)."""
    lines = ["🔍 SEO/GEO-аудит:\n"]
    for r in results:
        host = r["url"].replace("https://", "")
        problems = r["problems"]
        if not problems:
            lines.append(f"✅ {host} — всё чисто "
                         f"(проверено страниц: {r['pages_checked']})")
        else:
            has_critical = any(p["severity"] == "critical" for p in problems)
            lines.append(f"{'🔴' if has_critical else '⚠️'} {host} — "
                         f"проблем: {len(problems)}")
            for p in problems[:6]:
                sev = "🔴" if p["severity"] == "critical" else "⚠️"
                lines.append(f"   {sev} {_esc(p['message'])}")
            if len(problems) > 6:
                lines.append(f"   … и ещё {len(problems) - 6}")
        if r.get("no_js_chars") is not None:
            n_js = r["no_js_chars"]
            lines.append(f"   📄 Текст без JS: {n_js} "
                         f"{plural(n_js, 'символ', 'символа', 'символов')}")
        if r["url"] in gsc_status:
            lines.append(f"   📇 Google: {_esc(gsc_status[r['url']])}")
        yx = yx_status.get(host)
        if yx:
            chunk = []
            if yx.get("searchable_pages") is not None:
                chunk.append(f"{yx['searchable_pages']} стр. в поиске")
            if yx.get("sqi") is not None:
                chunk.append(f"ИКС {yx['sqi']}")
            if yx["alert_problems"]:
                chunk.append(f"🔴 проблем: {len(yx['alert_problems'])}")
            if chunk:
                lines.append(f"   📇 Яндекс: " + ", ".join(chunk))
        for note in (r.get("infos") or [])[:3]:
            lines.append(f"   ℹ️ {_esc(note)}")
        lines.append("")
    return "\n".join(lines)


async def _pause_controls(sid: int) -> tuple[list[InlineKeyboardButton], bool]:
    """Pause/unpause button row for the site detail screen."""
    paused = await settings.paused_until(sid)
    if paused:
        return [InlineKeyboardButton(
            text="▶️ Снять паузу", callback_data=f"pause:{sid}:off")], True
    return [
        InlineKeyboardButton(text="⏸ Пауза 1ч", callback_data=f"pause:{sid}:60"),
        InlineKeyboardButton(text="⏸ До утра", callback_data=f"pause:{sid}:morning"),
    ], False


# ── 🌍 Check single site ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_check_site")
async def cb_check_site_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🌍 Сайты в мониторинге — выбери для детальной проверки,\n"
        "или управляй списком:",
        reply_markup=await sites_keyboard("check_site", manage=True)
    )


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    site = await _site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await call.message.edit_text(
            "Сайт не найден — список сайтов изменился. Открой меню заново.",
            reply_markup=back_button(),
        )
        return
    url = site["url"]

    if not is_http_url(url):
        # tcp:// / ping:// monitor — availability IS the whole story:
        # no SSL, no domain, no screenshot.
        await call.message.edit_text(f"▱▱ Проверяю {url}...")
        avail = await check_availability(url, manage=False)
        lines = [f"📋 Детальная проверка\n{url}\n"]
        if avail["status"] == "ok":
            ms = avail.get("response_time_ms")
            ms_part = f" ({ms}ms)" if ms is not None else ""
            lines.append(f"✅ Доступность: отвечает{ms_part}")
        else:
            lines.append(
                f"🔴 Доступность: {_esc(avail.get('error') or 'недоступен')}")
        sid = site["id"]
        pause_row, paused = await _pause_controls(sid)
        if paused:
            lines.append("\n⏸ Сервис на паузе — алерты по нему молчат.")
        if settings.maintenance_now(sid):
            lines.append("🔧 Сейчас действует тех. окно — алерты молчат.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Настройки сайта",
                                  callback_data=f"sset:{sid}")],
            pause_row,
            [InlineKeyboardButton(text="← Главное меню",
                                  callback_data="menu_main")],
        ])
        await call.message.edit_text(_clip("\n".join(lines)), reply_markup=kb)
        return

    # Real-time progress
    await call.message.edit_text(f"▱▱▱ Проверяю доступность {url}...")
    avail = await check_availability(url, manage=False)

    await call.message.edit_text(
        f"▰▱▱ Доступность — готово\n"
        f"Проверяю SSL..."
    )
    ssl_r = await check_ssl(url, manage=False)

    await call.message.edit_text(
        f"▰▰▱ SSL — готово\n"
        f"Проверяю домен..."
    )
    dom_r = await check_domain(url, manage=False)

    # Build result
    lines = [f"📋 Детальная проверка\n{url}\n"]

    # Availability — keys always exist (value None on failure), so use `or`.
    if avail["status"] == "ok":
        code = avail.get("status_code") or "—"
        ms = avail.get("response_time_ms")
        ms_part = f" ({ms}ms)" if ms is not None else ""
        lines.append(f"✅ Доступность: HTTP {code}{ms_part}")
    else:
        lines.append(f"🔴 Доступность: {_esc(avail.get('error') or 'недоступен')}")

    # SSL
    if ssl_r.get("ssl_info"):
        days = ssl_r["ssl_info"]["days_left"]
        ssl_icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
        lines.append(f"{ssl_icon} SSL: {days} дн. до истечения")
        lines.append(f"   ↳ Издатель: {_esc(ssl_r['ssl_info']['issuer'])}")
        lines.append(f"   ↳ Истекает: {fmt_date(ssl_r['ssl_info']['not_after'])}")
    else:
        lines.append(f"🔴 SSL: {_esc(ssl_r.get('error', 'N/A'))}")

    # Domain
    if dom_r.get("domain_info") and dom_r["domain_info"]["days_left"] is not None:
        days = dom_r["domain_info"]["days_left"]
        dom_icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
        exp = fmt_date(dom_r["domain_info"]["expiration_date"]) if dom_r["domain_info"]["expiration_date"] else "N/A"
        lines.append(f"{dom_icon} Домен: {days} дн. (до {exp})")
        lines.append(f"   ↳ Регистратор: {_esc(dom_r['domain_info']['registrar'])}")
    else:
        lines.append(f"⚠️ Домен: {_esc(dom_r.get('error', 'N/A'))}")

    sid = site["id"]
    pause_row, paused = await _pause_controls(sid)
    if paused:
        lines.append("\n⏸ Сайт на паузе — алерты по нему молчат.")
    if settings.maintenance_now(sid):
        lines.append("🔧 Сейчас действует тех. окно — алерты молчат.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Скрин страницы", callback_data=f"act:shot:{sid}"),
         InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"sset:{sid}")],
        pause_row,
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
    ])
    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=kb)


# ── ➕ / 🗑 Site management ────────────────────────────────────────────────────

MAX_SITES = 20


@router.callback_query(F.data == "site_add")
async def cb_site_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    if len(await get_all_sites()) >= MAX_SITES:
        await call.message.edit_text(
            f"Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга.",
            reply_markup=back_button())
        return
    await state.set_state(AddSiteForm.url)
    await call.message.edit_text(
        "➕ Пришли домен или URL сайта (например, example.com).\n\n"
        "Также понимаю:\n"
        "• <code>tcp://host:порт</code> — проверка TCP-порта (почта, SSH, БД)\n"
        "• <code>ping://host</code> — ICMP-пинг хоста",
        reply_markup=_cancel_kb(),
    )


# Hostname/IPv4 labels for tcp:// and ping:// targets: single-label LAN
# hosts are allowed (unlike web sites, which need a real domain).
_HOST_RE = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*"


def _valid_host_lengths(host: str) -> bool:
    """DNS limits: 253 chars total, 63 per label — a 64-char label makes
    the IDNA codec raise deep inside the resolver."""
    return (len(host) <= 253
            and all(len(label) <= 63 for label in host.split(".")))


@router.message(AddSiteForm.url)
async def msg_site_add(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    import re as _re
    raw = (message.text or "").strip().lower()

    if raw.startswith(("tcp://", "ping://")):
        # Non-HTTP monitor: tcp://host:port or ping://host.
        try:
            parsed = urlparse(raw)
            host, port = parsed.hostname or "", parsed.port
        except ValueError:
            host, port = "", None
        if not _re.fullmatch(_HOST_RE, host) or not _valid_host_lengths(host):
            await message.answer(
                "Не понял хост. Примеры: tcp://mail.example.com:25, "
                "ping://10.0.0.1",
                reply_markup=_cancel_kb())
            return
        if parsed.scheme == "tcp":
            if not port:
                await message.answer(
                    "Для tcp нужен порт: tcp://host:порт (например, "
                    "tcp://mail.example.com:25)",
                    reply_markup=_cancel_kb())
                return
            url = f"tcp://{host}:{port}"
        else:
            url = f"ping://{host}"
        await state.clear()
        site_id = await activate_or_create_site(url)
        label = site_label(url)
        status = await message.answer(
            f"▱▱ Добавил {label} — делаю первую проверку...")
        avail = await check_availability(url)
        lines = [f"✅ {label} в мониторинге (проверка каждые "
                 f"{config.check_interval_minutes} мин)\n"]
        if avail["status"] == "ok":
            ms = avail.get("response_time_ms")
            lines.append(f"✅ Отвечает" + (f" ({ms}ms)" if ms is not None else ""))
        else:
            lines.append(f"🔴 Не отвечает: {_esc(avail.get('error', 'N/A'))} — "
                         f"я уже слежу, сообщу о восстановлении")
        await status.edit_text(_clip("\n".join(lines)),
                               reply_markup=back_button())
        return

    # startswith(("http://", ...)): a bare domain like httpbin.org must not
    # be mistaken for an already-schemed URL.
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    # Strict hostname charset: anything else (spaces, <, >, cyrillic…) is
    # either a typo or would poison every screen that renders the URL.
    if (not _re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+", host)
            or not _valid_host_lengths(host)):
        await message.answer(
            "Не похоже на домен. Пришли что-то вроде example.com",
            reply_markup=_cancel_kb())
        return
    await state.clear()
    url = f"{parsed.scheme}://{host}"

    site_id = await activate_or_create_site(url)
    status = await message.answer(f"▱▱▱ Добавил {host} — делаю первую проверку...")

    # Instant feedback: the user sees the site working (or not) right away.
    avail = await check_availability(url)
    ssl_r = await check_ssl(url)
    lines = [f"✅ {host} в мониторинге (проверка каждые "
             f"{config.check_interval_minutes} мин)\n"]
    if avail["status"] == "ok":
        lines.append(f"✅ Доступность: HTTP {avail.get('status_code')} "
                     f"({avail.get('response_time_ms')}ms)")
    else:
        lines.append(f"🔴 Доступность: {_esc(avail.get('error', 'N/A'))} — "
                     f"я уже слежу, сообщу о восстановлении")
    if ssl_r.get("ssl_info"):
        lines.append(f"🔒 SSL: {ssl_r['ssl_info']['days_left']} дн. до истечения")
    else:
        lines.append(f"⚠️ SSL: {_esc(ssl_r.get('error', 'N/A'))}")
    lines.append("\nDNS-эталон и SEO-аудит сниму в ближайшие часы автоматически.")
    await status.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


@router.callback_query(F.data == "site_del")
async def cb_site_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🗑 Какой сайт убрать из мониторинга?\n"
        "(история проверок сохранится)",
        reply_markup=await sites_keyboard("delsite"),
    )


@router.callback_query(F.data.startswith("delsite:"))
async def cb_site_del_confirm(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    site = await _site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await call.message.edit_text("Сайт не найден.", reply_markup=back_button())
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, убрать",
                              callback_data=f"delok:{site['id']}")],
        [InlineKeyboardButton(text="← Отмена", callback_data="menu_check_site")],
    ])
    await call.message.edit_text(
        f"Убрать {_esc(_short_host(site['url']))} из мониторинга?\n"
        f"Открытые инциденты по нему закроются, история останется.",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("delok:"))
async def cb_site_del_do(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    site = await _site_by_cb(call.data.split(":", 1)[1])
    if site and await deactivate_site(site["id"]):
        # Its maintenance windows would otherwise keep triggering the
        # 🔧 indicator and the status-page banner forever.
        await settings.remove_windows_for_site(site["id"])
        await call.message.edit_text(
            f"🗑 {_esc(_short_host(site['url']))} убран из мониторинга.",
            reply_markup=back_button())
    else:
        await call.message.edit_text("Сайт уже убран.", reply_markup=back_button())


@router.callback_query(F.data == "fsm_cancel")
async def cb_fsm_cancel(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer("Отменено")
    await state.clear()
    await send_main_menu(call)


# ── ⏸ Site pause (maintenance mode) ──────────────────────────────────────────

@router.callback_query(F.data.startswith("pause:"))
async def cb_pause(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit():
        await call.answer("Не понял", show_alert=True)
        return
    site_id, arg = int(parts[1]), parts[2]
    site = await get_site(site_id)
    if not site:
        await call.answer("Сайт не найден", show_alert=True)
        return
    host = _short_host(site["url"])

    if arg == "off":
        await settings.pause_site(site_id, None)
        await call.answer("Пауза снята")
        await call.message.answer(f"▶️ {host} — алерты снова включены.")
        return
    if arg == "morning":
        # Until the next morning report, in the user's timezone. Built via
        # tz.localize() on a naive target — replace()/timedelta arithmetic
        # on a pytz-aware datetime silently breaks across DST transitions.
        import pytz
        tz = pytz.timezone(config.timezone)
        now_l = now_local()
        naive = datetime(now_l.year, now_l.month, now_l.day,
                         settings.morning_hour())
        if naive <= now_l.replace(tzinfo=None):
            naive += timedelta(days=1)
        target = tz.localize(naive)
        minutes = max(1, int((target - datetime.now(timezone.utc))
                             .total_seconds() / 60))
    else:
        minutes = int(arg) if arg.isdigit() else 60
    await settings.pause_site(site_id, minutes)
    await call.answer("Пауза включена")
    await call.message.answer(
        f"⏸ {host} — алерты на паузе на {minutes // 60}ч {minutes % 60}м "
        f"(проверки продолжаются). Снять: «🌍 Сайт детально» → сайт."
    )


# ── ⚙️ Settings ───────────────────────────────────────────────────────────────

def _settings_kb() -> InlineKeyboardMarkup:
    sp_on = settings.status_page_enabled()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌅 {h}:00", callback_data=f"set_m:{h}")
         for h in (7, 8, 9, 10)],
        [InlineKeyboardButton(text=f"🌙 {h}:00", callback_data=f"set_e:{h}")
         for h in (20, 21, 22)]
        + [InlineKeyboardButton(text="🌙 выкл", callback_data="set_e:off")],
        [
            InlineKeyboardButton(text="🔕 23–8",  callback_data="set_q:23-8"),
            InlineKeyboardButton(text="🔕 0–7",   callback_data="set_q:0-7"),
            InlineKeyboardButton(text="🔕 выкл",  callback_data="set_q:off"),
        ],
        [
            InlineKeyboardButton(text="🔧 Тех. окна", callback_data="menu_maint"),
            InlineKeyboardButton(
                text=("🌐 Статус: выключить" if sp_on else "🌐 Статус: включить"),
                callback_data=f"set_sp:{'off' if sp_on else 'on'}"),
        ],
        [InlineKeyboardButton(text="← Назад", callback_data="menu_more")],
    ])


def _settings_text() -> str:
    evening = settings.evening_hour()
    quiet = settings.quiet_hours()
    n_windows = len(settings.maintenance_windows())
    if settings.status_page_enabled():
        sp = f"вкл · {settings.status_page_url()}"
    else:
        sp = "выключена"
    return (
        "⚙️ Настройки\n\n"
        f"🌅 Утренний отчёт: {settings.morning_hour()}:00\n"
        f"🌙 Вечерний отчёт: {'выключен' if evening is None else f'{evening}:00 (только при инцидентах)'}\n"
        f"🔕 Тихие часы: {'выключены' if not quiet else f'{quiet[0]}–{quiet[1]}'}\n"
        f"🔧 Тех. окна: {n_windows or 'нет'}\n"
        f"🌐 Статус-страница: {_esc(sp)}\n\n"
        "Тапни, чтобы изменить:"
    )


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(_settings_text(), reply_markup=_settings_kb())


@router.callback_query(F.data.startswith("set_"))
async def cb_settings_pick(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    kind, _, value = call.data.partition(":")
    if kind == "set_m" and value.isdigit():
        await settings.set_morning_hour(int(value))
    elif kind == "set_e":
        await settings.set_evening_hour(None if value == "off" else int(value))
    elif kind == "set_q":
        await settings.set_quiet_hours(None if value == "off" else value)
    elif kind == "set_sp":
        await settings.set_status_page(value == "on")
    else:
        await call.answer("Не понял", show_alert=True)
        return
    # Apply new report hours to the running scheduler immediately.
    from reports.scheduler import reschedule_report_jobs
    reschedule_report_jobs()
    await call.answer("Сохранено ✅")
    try:
        await call.message.edit_text(_settings_text(), reply_markup=_settings_kb())
    except TelegramBadRequest:
        pass  # same value re-picked — nothing changed


# ── ⚙️ Per-site settings ─────────────────────────────────────────────────────

_INTERVAL_PRESETS = (1, 3, 5, 15, 30, 60)
_FAIL_PRESETS = (1, 2, 3, 5)


async def _sset_screen(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Per-site settings screen: current values + buttons to change them."""
    from monitors.availability import CONSECUTIVE_FAILURE_THRESHOLD
    sid = site["id"]
    http = is_http_url(site["url"])
    interval = site.get("check_interval_min")
    thr = site.get("fail_threshold")
    lines = [f"⚙️ Настройки: {_esc(site_label(site['url']))}\n"]
    lines.append(
        f"⏱ Интервал проверки: {interval or config.check_interval_minutes} мин"
        + ("" if interval else " (по умолчанию)"))
    lines.append(
        f"🔁 Фейлов подряд до алерта: {thr or CONSECUTIVE_FAILURE_THRESHOLD}"
        + ("" if thr else " (по умолчанию)"))
    if http:
        codes = site.get("accepted_codes")
        lines.append(f"🔢 Приемлемые коды: "
                     + (_esc(codes) if codes else "по умолчанию (любой &lt; 400)"))
        kw = (site.get("keyword") or "").strip()
        if kw:
            mode = ("не должна появляться (стоп-фраза)"
                    if site.get("keyword_mode") == "absent"
                    else "должна быть на странице")
            lines.append(f"🔍 Фраза: «{_esc(kw)}» — {mode}")
        else:
            lines.append("🔍 Ключевая фраза: не задана")
    if settings.status_page_enabled():
        lines.append(f"🏷 Бейдж аптайма:\n"
                     f"<code>{_esc(settings.badge_url(sid))}</code>")
    rows = [
        [InlineKeyboardButton(text="⏱ Интервал", callback_data=f"ssp:i:{sid}"),
         InlineKeyboardButton(text="🔁 Порог фейлов", callback_data=f"ssp:f:{sid}")],
    ]
    if http:
        rows.append(
            [InlineKeyboardButton(text="🔢 Коды ответа", callback_data=f"ssp:c:{sid}"),
             InlineKeyboardButton(text="🔍 Фраза", callback_data=f"ssp:k:{sid}")])
    rows.append([InlineKeyboardButton(text="← К списку сайтов",
                                      callback_data="menu_check_site")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sset:"))
async def cb_site_settings(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await state.clear()
    site = await _site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await call.message.edit_text("Сайт не найден.", reply_markup=back_button())
        return
    text, kb = await _sset_screen(site)
    await call.message.edit_text(text, reply_markup=kb)


@router.callback_query(F.data.startswith("ssp:"))
async def cb_site_setting_picker(call: CallbackQuery, state: FSMContext):
    """Sub-pickers: which value of the chosen setting?"""
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3:
        await call.answer("Не понял", show_alert=True)
        return
    _, kind, sid = parts
    site = await _site_by_cb(sid)
    if not site:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.answer()
    sid = site["id"]
    label = _esc(site_label(site["url"]))

    if kind == "i":
        rows = [
            [InlineKeyboardButton(text=f"{m} мин", callback_data=f"ssv:i:{sid}:{m}")
             for m in _INTERVAL_PRESETS[:3]],
            [InlineKeyboardButton(text=f"{m} мин", callback_data=f"ssv:i:{sid}:{m}")
             for m in _INTERVAL_PRESETS[3:]],
            [InlineKeyboardButton(text="↩︎ По умолчанию",
                                  callback_data=f"ssv:i:{sid}:0")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"sset:{sid}")],
        ]
        await call.message.edit_text(
            f"⏱ Как часто проверять {label}?", 
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "f":
        rows = [
            [InlineKeyboardButton(text=str(n), callback_data=f"ssv:f:{sid}:{n}")
             for n in _FAIL_PRESETS],
            [InlineKeyboardButton(text="↩︎ По умолчанию",
                                  callback_data=f"ssv:f:{sid}:0")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"sset:{sid}")],
        ]
        await call.message.edit_text(
            f"🔁 Сколько фейлов подряд должно случиться, чтобы {label} "
            f"считался лежащим?\n(1 — алерт с первого фейла, больше — "
            f"меньше ложных тревог)",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "c":
        await state.set_state(SiteCodesForm.codes)
        await state.update_data(sset_site_id=sid)
        await call.message.edit_text(
            f"🔢 Какие HTTP-коды считать нормой для {label}?\n\n"
            "Примеры:\n"
            "• <code>200-299</code> — только успешные\n"
            "• <code>200-399,401</code> — плюс страница за паролем\n"
            "• <code>default</code> — вернуть правило по умолчанию (любой &lt; 400)",
            reply_markup=_cancel_kb())
    elif kind == "k":
        kw_set = bool((site.get("keyword") or "").strip())
        rows = [
            [InlineKeyboardButton(text="✅ Фраза должна быть на странице",
                                  callback_data=f"sskw:{sid}:present")],
            [InlineKeyboardButton(text="🚫 Стоп-фраза (не должна появляться)",
                                  callback_data=f"sskw:{sid}:absent")],
        ]
        if kw_set:
            rows.append([InlineKeyboardButton(
                text="🗑 Убрать фразу", callback_data=f"ssv:k:{sid}:0")])
        rows.append([InlineKeyboardButton(text="← Назад",
                                          callback_data=f"sset:{sid}")])
        await call.message.edit_text(
            f"🔍 Проверка содержимого {label}: ловит «HTTP 200, а на странице "
            f"ошибка или белый экран».\n\n"
            f"• «должна быть» — алерт, когда фраза пропала\n"
            f"• «стоп-фраза» — алерт, когда фраза появилась "
            f"(например, «Fatal error»)\n\n"
            f"Ищу без учёта регистра в первом 1 МБ страницы.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    else:
        await call.answer("Не понял", show_alert=True)


@router.callback_query(F.data.startswith("ssv:"))
async def cb_site_setting_value(call: CallbackQuery):
    """Apply a picked per-site value. Value 0 clears the override."""
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 4 or not parts[3].lstrip("-").isdigit():
        await call.answer("Не понял", show_alert=True)
        return
    _, kind, sid, raw = parts
    site = await _site_by_cb(sid)
    if not site:
        await call.answer("Сайт не найден", show_alert=True)
        return
    value = int(raw) or None
    if kind == "i":
        await update_site_settings(site["id"], check_interval_min=value)
        # Apply the new cadence immediately, not after the old one expires.
        from reports.scheduler import reset_site_schedule
        reset_site_schedule(site["id"])
    elif kind == "f":
        await update_site_settings(site["id"], fail_threshold=value)
    elif kind == "c":
        await update_site_settings(site["id"], accepted_codes=None)
    elif kind == "k":
        await update_site_settings(site["id"], keyword=None, keyword_mode=None)
    else:
        await call.answer("Не понял", show_alert=True)
        return
    await call.answer("Сохранено ✅")
    site = await get_site(site["id"])
    text, kb = await _sset_screen(site)
    await call.message.edit_text(text, reply_markup=kb)


@router.message(SiteCodesForm.codes)
async def msg_site_codes(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    from monitors.availability import parse_accepted_codes
    data = await state.get_data()
    sid = data.get("sset_site_id")
    site = await get_site(sid) if sid else None
    if not site:
        await state.clear()
        await message.answer("Сайт не найден — начни заново.",
                             reply_markup=back_button())
        return
    raw = (message.text or "").strip().lower().replace(" ", "")
    if raw in ("default", "поумолчанию", "сброс"):
        await state.clear()
        await update_site_settings(site["id"], accepted_codes=None)
    else:
        if len(raw) > 60 or parse_accepted_codes(raw) is None:
            await message.answer(
                "Не понял. Формат: <code>200-299</code> или "
                "<code>200-399,401</code> (коды 100–599). "
                "Или <code>default</code> для сброса.",
                reply_markup=_cancel_kb())
            return
        await state.clear()
        await update_site_settings(site["id"], accepted_codes=raw)
    site = await get_site(site["id"])
    text, kb = await _sset_screen(site)
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sskw:"))
async def cb_site_keyword_mode(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or parts[2] not in ("present", "absent"):
        await call.answer("Не понял", show_alert=True)
        return
    site = await _site_by_cb(parts[1])
    if not site:
        await call.answer("Сайт не найден", show_alert=True)
        return
    await call.answer()
    await state.set_state(SiteKeywordForm.keyword)
    await state.update_data(sset_site_id=site["id"], kw_mode=parts[2])
    what = ("которая должна быть на странице"
            if parts[2] == "present" else
            "при появлении которой нужен алерт (стоп-фраза)")
    await call.message.edit_text(
        f"🔍 Пришли фразу, {what}.\n"
        f"От 2 до 100 символов, регистр не важен.",
        reply_markup=_cancel_kb())


@router.message(SiteKeywordForm.keyword)
async def msg_site_keyword(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    data = await state.get_data()
    sid, mode = data.get("sset_site_id"), data.get("kw_mode", "present")
    site = await get_site(sid) if sid else None
    if not site:
        await state.clear()
        await message.answer("Сайт не найден — начни заново.",
                             reply_markup=back_button())
        return
    kw = (message.text or "").strip()
    if not 2 <= len(kw) <= 100:
        await message.answer("Нужна фраза от 2 до 100 символов. Попробуй ещё раз:",
                             reply_markup=_cancel_kb())
        return
    await state.clear()
    await update_site_settings(site["id"], keyword=kw, keyword_mode=mode)
    site = await get_site(site["id"])
    text, kb = await _sset_screen(site)
    await message.answer(text, reply_markup=kb)


# ── 🔧 Maintenance windows ───────────────────────────────────────────────────

_MW_DAYS = {"a": [], "w": [0, 1, 2, 3, 4], "e": [5, 6]}
_MW_TIME_PRESETS = ((120, 240), (180, 300), (1380, 360))  # 02–04, 03–05, 23–06


async def _maint_screen() -> tuple[str, InlineKeyboardMarkup]:
    windows = settings.maintenance_windows()
    lines = ["🔧 Тех. окна — в это время алерты и эскалация молчат, "
             "проверки и статистика продолжаются.\n"]
    rows = []
    if not windows:
        lines.append("Пока ни одного окна.")
    for w in windows:
        if w.get("site_id") is None:
            scope = "все сайты"
        else:
            site = await get_site(w["site_id"])
            scope = site_label(site["url"]) if site else f"сайт #{w['site_id']}"
        active = " · 🟢 активно сейчас" if settings.window_active_now(w) else ""
        desc = f"{scope} · {settings.fmt_window(w)}"
        lines.append(f"• {_esc(desc)}{active}")
        rows.append([InlineKeyboardButton(
            text=f"🗑 {desc}"[:60], callback_data=f"mw_del:{w['id']}")])
    rows.insert(0, [InlineKeyboardButton(text="➕ Добавить окно",
                                         callback_data="mw_add")])
    rows.append([InlineKeyboardButton(text="← Настройки",
                                      callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_maint")
async def cb_maint(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await state.clear()
    text, kb = await _maint_screen()
    await call.message.edit_text(_clip(text), reply_markup=kb)


@router.callback_query(F.data == "mw_add")
async def cb_mw_add(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    if len(settings.maintenance_windows()) >= settings.MAX_MAINT_WINDOWS:
        await call.message.edit_text(
            f"Лимит {settings.MAX_MAINT_WINDOWS} окон — удали ненужные.",
            reply_markup=back_button())
        return
    rows = [[InlineKeyboardButton(text="🌐 Все сайты", callback_data="mw_scope:0")]]
    for s in await get_all_sites():
        rows.append([InlineKeyboardButton(
            text=site_label(s["url"]), callback_data=f"mw_scope:{s['id']}")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")])
    await call.message.edit_text(
        "🔧 Для чего это тех. окно?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("mw_scope:"))
async def cb_mw_scope(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    arg = call.data.split(":", 1)[1]
    if not arg.isdigit():
        await call.answer("Не понял", show_alert=True)
        return
    await call.answer()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Каждый день", callback_data=f"mw_days:{arg}:a")],
        [InlineKeyboardButton(text="Будни", callback_data=f"mw_days:{arg}:w"),
         InlineKeyboardButton(text="Выходные", callback_data=f"mw_days:{arg}:e")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")],
    ])
    await call.message.edit_text("🔧 В какие дни?", reply_markup=kb)


@router.callback_query(F.data.startswith("mw_days:"))
async def cb_mw_days(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in _MW_DAYS:
        await call.answer("Не понял", show_alert=True)
        return
    await call.answer()
    scope, days = parts[1], parts[2]

    def _fmt(m):
        return f"{m // 60:02d}:{m % 60:02d}"

    rows = [[InlineKeyboardButton(
        text=f"{_fmt(s)}–{_fmt(e)}",
        callback_data=f"mw_time:{scope}:{days}:{s}-{e}")
        for s, e in _MW_TIME_PRESETS]]
    rows.append([InlineKeyboardButton(
        text="✏️ Своё время", callback_data=f"mw_custom:{scope}:{days}")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")])
    await call.message.edit_text(
        "🔧 В какое время (по вашей таймзоне)?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


async def _mw_create(call: CallbackQuery, scope: str, days_key: str,
                     start_min: int, end_min: int):
    site_id = int(scope) or None
    if site_id and not await get_site(site_id):
        await call.message.edit_text("Сайт не найден.", reply_markup=back_button())
        return
    await settings.add_maintenance_window(
        site_id, _MW_DAYS[days_key], start_min, end_min)
    text, kb = await _maint_screen()
    await call.message.edit_text(_clip("✅ Окно добавлено.\n\n" + text),
                                 reply_markup=kb)


@router.callback_query(F.data.startswith("mw_time:"))
async def cb_mw_time(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    ok = (len(parts) == 4 and parts[1].isdigit() and parts[2] in _MW_DAYS
          and "-" in parts[3])
    if ok:
        a, _, b = parts[3].partition("-")
        ok = a.isdigit() and b.isdigit()
    if not ok:
        await call.answer("Не понял", show_alert=True)
        return
    await call.answer()
    await _mw_create(call, parts[1], parts[2], int(a), int(b))


@router.callback_query(F.data.startswith("mw_custom:"))
async def cb_mw_custom(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    parts = call.data.split(":")
    if len(parts) != 3 or not parts[1].isdigit() or parts[2] not in _MW_DAYS:
        await call.answer("Не понял", show_alert=True)
        return
    await call.answer()
    await state.set_state(MaintTimeForm.time)
    await state.update_data(mw_scope=parts[1], mw_days=parts[2])
    await call.message.edit_text(
        "🔧 Пришли время окна в формате <code>ЧЧ:ММ-ЧЧ:ММ</code>, "
        "например <code>01:30-03:00</code>.\n"
        "Окно через полночь тоже можно: <code>23:00-06:00</code>.",
        reply_markup=_cancel_kb())


@router.message(MaintTimeForm.time)
async def msg_mw_time(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    import re as _re
    m = _re.fullmatch(r"(\d{1,2}):(\d{2})\s*[-—–]\s*(\d{1,2}):(\d{2})",
                      (message.text or "").strip())
    start_min = end_min = -1
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        if h1 <= 23 and h2 <= 23 and m1 <= 59 and m2 <= 59:
            start_min, end_min = h1 * 60 + m1, h2 * 60 + m2
    if start_min < 0 or start_min == end_min:
        await message.answer(
            "Не понял. Формат: <code>ЧЧ:ММ-ЧЧ:ММ</code>, и время начала "
            "должно отличаться от конца.",
            reply_markup=_cancel_kb())
        return
    data = await state.get_data()
    scope, days = data.get("mw_scope"), data.get("mw_days")
    await state.clear()
    if scope is None or days not in _MW_DAYS:
        await message.answer("Начни заново: Настройки → 🔧 Тех. окна.",
                             reply_markup=back_button())
        return
    site_id = int(scope) or None
    if site_id and not await get_site(site_id):
        await message.answer("Сайт не найден.", reply_markup=back_button())
        return
    await settings.add_maintenance_window(
        site_id, _MW_DAYS[days], start_min, end_min)
    text, kb = await _maint_screen()
    await message.answer(_clip("✅ Окно добавлено.\n\n" + text), reply_markup=kb)


@router.callback_query(F.data.startswith("mw_del:"))
async def cb_mw_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    arg = call.data.split(":", 1)[1]
    removed = arg.isdigit() and await settings.remove_maintenance_window(int(arg))
    await call.answer("Удалено" if removed else "Уже удалено")
    text, kb = await _maint_screen()
    try:
        await call.message.edit_text(_clip(text), reply_markup=kb)
    except TelegramBadRequest:
        pass


# ── 💓 Heartbeats management ─────────────────────────────────────────────────

_HB_INTERVALS = [("1 час", 60), ("6 часов", 360), ("сутки", 1440), ("неделя", 10080)]


async def _hb_text() -> str:
    from reports.formatter import _parse_sqlite_utc
    from reports.scheduler import _fmt_ago
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return ("💓 Dead-man switch: джобы (бэкапы, кроны) пингуют бота, "
                "а он замечает, когда они замолкают.\n\nПока ни одной джобы.")
    beats = await get_heartbeats()
    now = datetime.now(timezone.utc)
    lines = ["💓 Heartbeats:\n"]
    for job, interval in jobs.items():
        row = beats.get(job)
        last = _parse_sqlite_utc(row["last_ping"]) if row and row.get("last_ping") else None
        if last:
            ago = (now - last).total_seconds() / 60
            icon = "✅" if ago <= interval * 1.25 else "💔"
            state_txt = f"{_fmt_ago(ago)} назад"
        else:
            icon, state_txt = "❓", "ещё не пинговал"
        src = "" if job in settings.ui_heartbeat_jobs() else " (из .env)"
        lines.append(f"{icon} {_esc(job)} — {state_txt}, ожидание каждые "
                     f"{_fmt_ago(interval)}{src}")
    first = next(iter(jobs))
    lines.append(
        f"\nПинг из крона (пример для «{first}»):\n"
        f"<code>curl -fsS {_esc(settings.heartbeat_url(first))}</code>"
    )
    if not config.public_base_url:
        lines.append("\n⚠️ Задай PUBLIC_BASE_URL в .env, чтобы ссылка выше "
                     "была готова к вставке.")
    return "\n".join(lines)


async def _hb_kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Добавить джобу", callback_data="hb_add")]]
    ui_jobs = settings.ui_heartbeat_jobs()
    for name in list(ui_jobs)[:12]:
        rows.append([InlineKeyboardButton(
            text=f"🗑 {name}", callback_data=f"hb_del:{name}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="menu_more")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_hb")
async def cb_hb(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        _clip(await _hb_text()), reply_markup=await _hb_kb())


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await state.set_state(AddHeartbeatForm.name)
    await call.message.edit_text(
        "💓 Название джобы латиницей (например, backup или certs-sync):",
        reply_markup=_cancel_kb(),
    )


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    name = (message.text or "").strip().lower()
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789-_")
    # str.isalnum() passes Unicode (кириллицу, CJK) — a 20-char CJK name is
    # 60 UTF-8 bytes and overflows the 64-byte callback_data limit, killing
    # the whole Heartbeats keyboard. ASCII only, as the prompt promises.
    if not name or len(name) > 20 or not set(name) <= allowed:
        await message.answer(
            "Только латиница/цифры/дефис, до 20 символов. Попробуй ещё раз:",
            reply_markup=_cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= 12:
        await state.clear()
        await message.answer(
            "Лимит 12 джоб из интерфейса — удали ненужные в 💓 Heartbeats.",
            reply_markup=back_button())
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}")
         for label, mins in _HB_INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}")
         for label, mins in _HB_INTERVALS[2:]],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")],
    ])
    await message.answer(
        f"Как часто «{_esc(name)}» должна подавать сигнал?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    data = await state.get_data()
    name = data.get("hb_name")
    await state.clear()
    if not name:
        await call.answer("Начни заново: 💓 Heartbeats → ➕", show_alert=True)
        return
    interval = int(call.data.split(":", 1)[1])
    await settings.add_heartbeat_job(name, interval)
    await call.answer("Добавлено ✅")
    await call.message.edit_text(
        f"💓 «{_esc(name)}» добавлена. Вставь в конец крон-строки:\n\n"
        f"<code>&& curl -fsS {_esc(settings.heartbeat_url(name))}</code>\n\n"
        f"Если сигналов не будет дольше ожидания — сообщу.",
        reply_markup=back_button(),
    )


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    name = call.data.split(":", 1)[1]
    removed = await settings.remove_heartbeat_job(name)
    await call.answer("Удалено" if removed else "Не нашёл (из .env — убери там)")
    try:
        await call.message.edit_text(
            _clip(await _hb_text()), reply_markup=await _hb_kb())
    except TelegramBadRequest:
        pass


# ── 🩺 Diagnostics ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_diag")
async def cb_diag(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("▱▱▱ Проверяю сам себя...")

    import aiohttp
    from services import gsc, yandex_webmaster, docker_api

    lines = ["🩺 Диагностика:\n"]

    me = await call.bot.get_me()
    lines.append(f"✅ Telegram: @{me.username}")

    try:
        await set_state("diag_ping", now_local().isoformat())
        lines.append("✅ База данных: пишется")
    except Exception as e:
        lines.append(f"⛔ База данных: {_esc(e)}")

    try:
        async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(
                    f"http://127.0.0.1:{config.webhook_port}/health") as resp:
                lines.append("✅ Веб-сервер (feedback/heartbeat): отвечает"
                             if resp.status == 200 else
                             f"⚠️ Веб-сервер: HTTP {resp.status}")
    except Exception:
        lines.append("⛔ Веб-сервер: не отвечает на localhost")

    lines.append("✅ PUBLIC_BASE_URL задан" if config.public_base_url
                 else "⚠️ PUBLIC_BASE_URL не задан — heartbeat-ссылки будут с плейсхолдером")

    if settings.status_page_enabled():
        lines.append(f"✅ Статус-страница: {_esc(settings.status_page_url())}")
    else:
        lines.append("ℹ️ Статус-страница выключена (Настройки → 🌐)")

    sites = await get_all_sites()
    lines.append(f"✅ Сайтов в мониторинге: {len(sites)}" if sites
                 else "⚠️ Сайтов нет — добавь через «🌍 Сайт детально»")

    if docker_api.docker_available():
        containers = await docker_api.list_containers()
        n_c = len(containers or [])
        lines.append(f"✅ Docker socket: доступен "
                     f"({n_c} {plural(n_c, 'контейнер', 'контейнера', 'контейнеров')})"
                     if containers is not None else "⚠️ Docker socket: есть, но API не отвечает")
    else:
        lines.append("ℹ️ Docker socket не смонтирован (автоперезапуск/очистка выключены)")

    if gsc.available():
        from datetime import date, timedelta as td
        d = (date.today() - td(days=3)).isoformat()
        probe = await gsc.search_totals(d, d)
        lines.append("✅ Google Search Console: токен работает" if probe is not None
                     else "⛔ Google Search Console: настроен, но API не отвечает (см. логи)")
    else:
        lines.append("ℹ️ Google Search Console не настроен")

    if yandex_webmaster.available():
        probe = await yandex_webmaster.get_summaries()
        n_h = len(probe or {})
        lines.append(f"✅ Яндекс.Вебмастер: токен работает "
                     f"({n_h} {plural(n_h, 'хост', 'хоста', 'хостов')})"
                     if probe is not None else
                     "⛔ Яндекс.Вебмастер: настроен, но API не отвечает (см. логи)")
    else:
        lines.append("ℹ️ Яндекс.Вебмастер не настроен")

    host = urlparse(config.screenshot_template).hostname or "?"
    lines.append(f"ℹ️ Скриншоты: {host}")
    lines.append(f"ℹ️ Вторая точка проверки: "
                 f"{'вкл' if config.second_opinion else 'выкл'}")

    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


# ── 🔔 Test alert ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_testalert")
async def cb_test_alert(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer("Отправляю тестовый алерт…")
    await call.bot.send_message(
        chat_id=config.admin_chat_id,
        text=("🚨 ТЕСТОВЫЙ АЛЕРТ\n"
              "Так выглядит критическое уведомление — оно пробивает mute и "
              "тихие часы.\n\n"
              "Если ты это видишь — доставка работает. ✅"),
    )


# ── ✓ Close a single incident ────────────────────────────────────────────────

@router.callback_query(F.data.startswith("inc_close:"))
async def cb_incident_close(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    arg = call.data.split(":", 1)[1]
    if arg.isdigit() and await resolve_incident_by_id(int(arg)):
        await call.answer("Закрыт ✅")
    else:
        await call.answer("Уже закрыт")
    # Re-render the incidents screen with the fresh list.
    await _render_incidents(call)
