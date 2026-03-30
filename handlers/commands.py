import logging
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton
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
from db.database import get_active_incidents, get_all_sites, get_uptime_stats, get_or_create_site
from reports.formatter import (
    format_status_report, format_links_report, format_uptime,
)

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
        ],
    ])


def back_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")]
    ])


def sites_keyboard(action_prefix: str) -> InlineKeyboardMarkup:
    """Keyboard with a button per site."""
    buttons = []
    for url in config.get_site_urls():
        label = url.replace("https://", "")
        buttons.append([InlineKeyboardButton(text=f"🔍 {label}", callback_data=f"{action_prefix}:{url}")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="menu_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    return str(user_id) == config.admin_chat_id


async def send_main_menu(target, text: str = "Выбери действие:"):
    """Send main menu — works for both Message and CallbackQuery."""
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=main_menu())
    else:
        await target.answer(text, reply_markup=main_menu())


# ── /start and /help ─────────────────────────────────────────────────────────

@router.message(CommandStart())
@router.message(Command("help"))
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await send_main_menu(message, "👋 Привет! Я DevOps-бот для мониторинга сайтов.\n\nВыбери действие:")


# ── /menu ─────────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Доступ только для администратора.")
        return
    await send_main_menu(message)


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

    # Append links summary
    broken_total = sum(len(r.get("broken_links", [])) for r in links_results)
    if broken_total:
        report += f"\n\n⚠️ Битых ссылок: {broken_total} шт. — нажми «🔗 Ссылки» для деталей"
    else:
        report += f"\n\n✅ Все ссылки в норме"

    await call.message.edit_text(report, reply_markup=back_button())


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

    await call.message.edit_text(report, reply_markup=back_button())


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
            lines.append(f"{icon} {r['url']}")
            lines.append(f"   Осталось: {days} дн. (до {info['not_after'][:10]})")
            lines.append(f"   Издатель: {info['issuer']}")
        else:
            lines.append(f"🔴 {r['url']}: {r.get('error', 'N/A')}")

    await call.message.edit_text("\n".join(lines), reply_markup=back_button())


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
            exp = info["expiration_date"][:10] if info["expiration_date"] else "N/A"
            lines.append(f"{icon} {r['domain']}")
            lines.append(f"   Осталось: {days} дн. (до {exp})")
            lines.append(f"   Регистратор: {info['registrar']}")
        else:
            lines.append(f"⚠️ {r.get('domain', r['url'])}: {r.get('error', 'N/A')}")

    await call.message.edit_text("\n".join(lines), reply_markup=back_button())


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

    url = call.data.split(":", 1)[1]
    await call.message.edit_text(f"⏳ Сканирую все ссылки на {url}...\n(это может занять ~30 сек)")

    from monitors.links_checker import check_links
    result = await check_links(url)

    if result.get("broken_links"):
        text = format_links_report(result)
    else:
        text = f"✅ Все {result['total_links']} ссылок на {url} работают корректно"

    await call.message.edit_text(text, reply_markup=back_button())


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

    await call.message.edit_text("\n".join(lines), reply_markup=back_button())


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
        text = "\n".join(lines)

    await call.message.edit_text(text, reply_markup=back_button())


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

    url = call.data.split(":", 1)[1]

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
        lines.append(f"   ↳ Истекает: {ssl_r['ssl_info']['not_after'][:10]}")
    else:
        lines.append(f"🔴 SSL: {ssl_r.get('error', 'N/A')}")

    # Domain
    if dom_r.get("domain_info") and dom_r["domain_info"]["days_left"] is not None:
        days = dom_r["domain_info"]["days_left"]
        dom_icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
        exp = dom_r["domain_info"]["expiration_date"][:10] if dom_r["domain_info"]["expiration_date"] else "N/A"
        lines.append(f"{dom_icon} Домен: {days} дн. (до {exp})")
        lines.append(f"   ↳ Регистратор: {dom_r['domain_info']['registrar']}")
    else:
        lines.append(f"⚠️ Домен: {dom_r.get('error', 'N/A')}")

    await call.message.edit_text("\n".join(lines), reply_markup=back_button())
