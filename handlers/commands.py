import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, ErrorEvent, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
)
from aiogram.filters import Command, CommandStart

from config import config
from monitors.availability import check_all
from monitors.ssl_checker import check_all_ssl
from monitors.domain_checker import check_all_domains
from monitors.links_checker import check_all_links
from monitors.availability import check_availability
from monitors.ssl_checker import check_ssl
from monitors.domain_checker import check_domain
from db.database import (
    get_active_incidents, get_all_sites, get_uptime_stats, get_or_create_site,
    get_state, set_state, resolve_all_incidents,
)
from reports.formatter import (
    format_status_report, format_links_report, format_uptime,
    format_compact_status_report, now_local, fmt_date, _short_host,
)
from services.actions import trigger_redeploy, purge_cf_cache
from services.screenshots import fetch_screenshot

logger = logging.getLogger(__name__)
router = Router()


# ── Keyboards ────────────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🚀 Полная проверка", callback_data="run_full_check"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус сайтов",  callback_data="menu_status"),
            InlineKeyboardButton(text="🔒 SSL",            callback_data="menu_ssl"),
        ],
        [
            InlineKeyboardButton(text="🌐 Домены",         callback_data="menu_domains"),
            InlineKeyboardButton(text="🔗 Ссылки",         callback_data="menu_links"),
        ],
        [
            InlineKeyboardButton(text="📈 Uptime",         callback_data="menu_uptime"),
            InlineKeyboardButton(text="⚠️ Инциденты",      callback_data="menu_incidents"),
        ],
        [
            InlineKeyboardButton(text="🌍 Проверить сайт", callback_data="menu_check_site"),
            InlineKeyboardButton(text="🔕 Тишина",          callback_data="menu_mute"),
        ],
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


def sites_keyboard(action_prefix: str) -> InlineKeyboardMarkup:
    """Keyboard with a button per site.

    callback_data carries the site's index into config.get_site_urls(), not
    the URL itself — Telegram caps callback_data at 64 bytes and a long
    domain would make the whole keyboard fail to send.
    """
    buttons = []
    for i, url in enumerate(config.get_site_urls()):
        label = url.replace("https://", "")
        buttons.append([InlineKeyboardButton(text=f"🔍 {label}", callback_data=f"{action_prefix}:{i}")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _site_from_cb(arg: str) -> str | None:
    """Resolve a sites_keyboard callback index back to a URL."""
    try:
        i = int(arg)
    except ValueError:
        return None
    urls = config.get_site_urls()
    return urls[i] if 0 <= i < len(urls) else None


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


async def send_main_menu(target, text: str = "Выбери действие:"):
    """Send main menu — works for both Message and CallbackQuery."""
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=main_menu())
    else:
        await target.answer(text, reply_markup=main_menu())


# ── Error handler ────────────────────────────────────────────────────────────

@router.errors()
async def on_handler_error(event: ErrorEvent):
    """Catch-all so a failed check (WHOIS timeout etc.) doesn't leave the
    menu stuck on a '⏳ ...' message with no keyboard and no way back."""
    logger.exception("Handler error: %s", event.exception)
    update = event.update
    try:
        if update.callback_query and update.callback_query.message:
            await update.callback_query.message.edit_text(
                "❌ Что-то пошло не так во время проверки.\n"
                "Попробуй ещё раз чуть позже.",
                reply_markup=back_button(),
            )
        elif update.message:
            await update.message.answer(
                "❌ Что-то пошло не так. Попробуй ещё раз чуть позже."
            )
    except Exception:
        # e.g. "message is not modified" or the message is too old to edit —
        # nothing sensible left to do beyond the log line above.
        pass
    return True


# ── /start and /help ─────────────────────────────────────────────────────────

@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    # Install persistent reply keyboard once — stays visible at the bottom.
    await message.answer(
        "👋 Привет! Я DevOps-бот для мониторинга сайтов.\n"
        "Кнопка «📱 Меню» внизу всегда под рукой.",
        reply_markup=persistent_keyboard(),
    )
    await send_main_menu(
        message,
        "Главное меню:",
    )


# ── Reply-keyboard taps ──────────────────────────────────────────────────────

@router.message(F.text == "📱 Меню")
async def reply_menu(message: Message):
    if not is_admin(message.from_user.id):
        return
    await send_main_menu(message)


@router.message(F.text == "📊 Статус")
async def reply_status(message: Message):
    if not is_admin(message.from_user.id):
        return
    await cmd_status(message)


# ── /menu ─────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await send_main_menu(message)


# ── /status — quick one-line summary ─────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await message.answer("⏳ Проверяю...")
    urls = config.get_site_urls()
    availability = await check_all(urls)
    incidents = await get_active_incidents()
    text = format_compact_status_report(
        availability=availability, incidents=incidents,
    )
    await message.answer(text, reply_markup=back_button())


# ── /sites — list configured sites ───────────────────────────────────────────

@router.message(Command("sites"))
async def cmd_sites(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    urls = config.get_site_urls()
    if not urls:
        await message.answer("Сайты не настроены — задай SITES в .env")
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
    _, action, idx = parts
    url = _site_from_cb(idx)
    if not url:
        await call.answer("Сайт не найден — список сайтов изменился", show_alert=True)
        return

    if action == "recheck":
        await call.answer("Проверяю…")
        avail = await check_availability(url)
        if avail["status"] == "ok":
            text = (f"🔍 {url} — доступен: HTTP {avail.get('status_code')} "
                    f"({avail.get('response_time_ms')}ms)")
        else:
            text = f"🔍 {url} — всё ещё недоступен: {avail.get('error', 'N/A')}"
        await call.message.answer(text)
    elif action == "shot":
        await call.answer("Делаю скрин… (~15 сек)")
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
async def cb_main_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await send_main_menu(call)


# ── 🚀 Full real-time check ───────────────────────────────────────────────────

@router.callback_query(F.data == "run_full_check")
async def cb_full_check(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    urls = config.get_site_urls()

    # Step 1 — availability
    await call.message.edit_text(
        "🚀 Запускаю полную проверку...\n\n"
        "⏳ [1/4] Проверяю доступность сайтов...",
        reply_markup=None
    )
    availability = await check_all(urls)

    # Step 2 — SSL
    await call.message.edit_text(
        "🚀 Полная проверка...\n\n"
        "✅ [1/4] Доступность — готово\n"
        "⏳ [2/4] Проверяю SSL-сертификаты...",
    )
    ssl_results = await check_all_ssl(urls)

    # Step 3 — domains
    await call.message.edit_text(
        "🚀 Полная проверка...\n\n"
        "✅ [1/4] Доступность — готово\n"
        "✅ [2/4] SSL — готово\n"
        "⏳ [3/4] Проверяю домены...",
    )
    domain_results = await check_all_domains(urls)

    # Step 4 — links
    await call.message.edit_text(
        "🚀 Полная проверка...\n\n"
        "✅ [1/4] Доступность — готово\n"
        "✅ [2/4] SSL — готово\n"
        "✅ [3/4] Домены — готово\n"
        "⏳ [4/4] Проверяю ссылки на страницах...",
    )
    links_results = await check_all_links(urls)

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
    if internal_broken:
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
    await call.message.edit_text("⏳ Проверяю доступность...")

    urls = config.get_site_urls()
    availability = await check_all(urls)
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
    await call.message.edit_text("⏳ Проверяю SSL-сертификаты...")

    urls = config.get_site_urls()
    results = await check_all_ssl(urls)

    lines = ["🔒 SSL-сертификаты:\n"]
    for r in results:
        if r.get("ssl_info"):
            info = r["ssl_info"]
            days = info["days_left"]
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            lines.append(f"{icon} {_short_host(r['url'])}")
            lines.append(f"   Осталось: {days} дн. (до {fmt_date(info['not_after'])})")
            lines.append(f"   Издатель: {info['issuer']}")
        else:
            lines.append(f"🔴 {_short_host(r['url'])}: {r.get('error', 'N/A')}")

    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=back_button())


# ── 🌐 Domains ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_domains")
async def cb_domains(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text("⏳ Проверяю домены через WHOIS...")

    urls = config.get_site_urls()
    results = await check_all_domains(urls)

    lines = ["🌐 Домены:\n"]
    for r in results:
        info = r.get("domain_info")
        if info and info["days_left"] is not None:
            days = info["days_left"]
            icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
            exp = fmt_date(info["expiration_date"]) if info["expiration_date"] else "N/A"
            lines.append(f"{icon} {r['domain']}")
            lines.append(f"   Осталось: {days} дн. (до {exp})")
            lines.append(f"   Регистратор: {info['registrar']}")
        else:
            lines.append(f"⚠️ {r.get('domain', r['url'])}: {r.get('error', 'N/A')}")

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
        reply_markup=sites_keyboard("check_links")
    )


@router.callback_query(F.data.startswith("check_links:"))
async def cb_check_links(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    url = _site_from_cb(call.data.split(":", 1)[1])
    if not url:
        await call.message.edit_text(
            "Сайт не найден — список сайтов изменился. Открой меню заново.",
            reply_markup=back_button(),
        )
        return
    await call.message.edit_text(f"⏳ Сканирую все ссылки на {url}...\n(это может занять ~30 сек)")

    from monitors.links_checker import check_links
    result = await check_links(url)

    if result.get("broken_internal"):
        text = format_links_report(result)
    else:
        ext = len(result.get("broken_external") or [])
        text = (
            f"✅ Все внутренние ссылки на {url} работают "
            f"({result['total_links']} проверено)"
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
        lines.append(
            f"{icon} {s['url']}\n"
            f"   Доступность: {stats['uptime_pct']}%\n"
            f"   Среднее время ответа: {stats['avg_response_ms']}ms\n"
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

    incidents = await get_active_incidents()
    if not incidents:
        text = "✅ Активных инцидентов нет — всё работает нормально!"
        kb = back_button()
    else:
        lines = [f"⚠️ Активные инциденты ({len(incidents)}):\n"]
        for inc in incidents:
            sev_icon = "🔴" if inc["severity"] == "critical" else "⚠️"
            lines.append(
                f"{sev_icon} {inc['url']}\n"
                f"   Тип: {inc['check_type']}\n"
                f"   Проблема: {inc['message']}\n"
                f"   С: {inc['created_at'][:16]}"
            )
        text = _clip("\n".join(lines))
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(
                text="🗑 Сбросить все (если устарели)",
                callback_data="incidents_clear",
            )],
            [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
        ])

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


# ── 🌍 Check single site ──────────────────────────────────────────────────────

@router.callback_query(F.data == "menu_check_site")
async def cb_check_site_menu(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()
    await call.message.edit_text(
        "🌍 Выбери сайт для детальной проверки:",
        reply_markup=sites_keyboard("check_site")
    )


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Доступ запрещён", show_alert=True)
        return
    await call.answer()

    url = _site_from_cb(call.data.split(":", 1)[1])
    if not url:
        await call.message.edit_text(
            "Сайт не найден — список сайтов изменился. Открой меню заново.",
            reply_markup=back_button(),
        )
        return

    # Real-time progress
    await call.message.edit_text(f"⏳ [1/3] Проверяю доступность {url}...")
    avail = await check_availability(url)

    await call.message.edit_text(
        f"✅ [1/3] Доступность — готово\n"
        f"⏳ [2/3] Проверяю SSL..."
    )
    ssl_r = await check_ssl(url)

    await call.message.edit_text(
        f"✅ [1/3] Доступность — готово\n"
        f"✅ [2/3] SSL — готово\n"
        f"⏳ [3/3] Проверяю домен..."
    )
    dom_r = await check_domain(url)

    # Build result
    lines = [f"📋 Детальная проверка\n{url}\n"]

    # Availability
    icon = "✅" if avail["status"] == "ok" else "🔴"
    code = avail.get("status_code", "N/A")
    ms = avail.get("response_time_ms", "N/A")
    lines.append(f"{icon} Доступность: HTTP {code} ({ms}ms)")
    if avail.get("error"):
        lines.append(f"   ↳ {avail['error']}")

    # SSL
    if ssl_r.get("ssl_info"):
        days = ssl_r["ssl_info"]["days_left"]
        ssl_icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
        lines.append(f"{ssl_icon} SSL: {days} дн. до истечения")
        lines.append(f"   ↳ Издатель: {ssl_r['ssl_info']['issuer']}")
        lines.append(f"   ↳ Истекает: {fmt_date(ssl_r['ssl_info']['not_after'])}")
    else:
        lines.append(f"🔴 SSL: {ssl_r.get('error', 'N/A')}")

    # Domain
    if dom_r.get("domain_info") and dom_r["domain_info"]["days_left"] is not None:
        days = dom_r["domain_info"]["days_left"]
        dom_icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
        exp = fmt_date(dom_r["domain_info"]["expiration_date"]) if dom_r["domain_info"]["expiration_date"] else "N/A"
        lines.append(f"{dom_icon} Домен: {days} дн. (до {exp})")
        lines.append(f"   ↳ Регистратор: {dom_r['domain_info']['registrar']}")
    else:
        lines.append(f"⚠️ Домен: {dom_r.get('error', 'N/A')}")

    idx = call.data.split(":", 1)[1]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📸 Скрин страницы", callback_data=f"act:shot:{idx}")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")],
    ])
    await call.message.edit_text(_clip("\n".join(lines)), reply_markup=kb)
