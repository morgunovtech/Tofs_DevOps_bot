import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command, CommandStart

from config import config
from monitors.availability import check_availability, check_all
from monitors.ssl_checker import check_ssl, check_all_ssl
from monitors.domain_checker import check_domain, check_all_domains
from monitors.links_checker import check_links
from db.database import get_active_incidents, get_all_sites, get_uptime_stats, get_or_create_site
from reports.formatter import (
    format_status_report, format_ssl_alert, format_domain_alert,
    format_links_report, format_uptime,
)

logger = logging.getLogger(__name__)
router = Router()


def is_admin(message: Message) -> bool:
    return str(message.chat.id) == config.admin_chat_id


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я DevOps-бот для мониторинга сайтов.\n\n"
        "Команды:\n"
        "/status — статус всех сайтов\n"
        "/check <url> — проверить конкретный сайт\n"
        "/sites — список сайтов\n"
        "/ssl — статус SSL-сертификатов\n"
        "/domains — статус доменов\n"
        "/links <url> — проверка ссылок на сайте\n"
        "/uptime — статистика доступности\n"
        "/help — эта справка"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await cmd_start(message)


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    await message.answer("🔄 Проверяю сайты...")

    urls = config.get_site_urls()
    availability = await check_all(urls)
    incidents = await get_active_incidents()

    report = format_status_report(availability, incidents)
    await message.answer(report)


@router.message(Command("check"))
async def cmd_check(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /check <url>\nПример: /check s.morgunov.tech")
        return

    url = args[1].strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    await message.answer(f"🔄 Проверяю {url}...")

    result = await check_availability(url)
    ssl_result = await check_ssl(url)
    domain_result = await check_domain(url)

    lines = []
    # Availability
    icon = "✅" if result["status"] == "ok" else "🔴"
    lines.append(f"{icon} Доступность: {result.get('status_code', 'N/A')} ({result.get('response_time_ms', 'N/A')}ms)")
    if result.get("error"):
        lines.append(f"   ⚠️ {result['error']}")

    # SSL
    if ssl_result.get("ssl_info"):
        days = ssl_result["ssl_info"]["days_left"]
        ssl_icon = "🔒" if days > 14 else ("⚠️" if days > 3 else "🔴")
        lines.append(f"{ssl_icon} SSL: {days} дн. до истечения")
        lines.append(f"   Издатель: {ssl_result['ssl_info']['issuer']}")
    elif ssl_result.get("error"):
        lines.append(f"🔴 SSL: {ssl_result['error']}")

    # Domain
    if domain_result.get("domain_info") and domain_result["domain_info"]["days_left"] is not None:
        days = domain_result["domain_info"]["days_left"]
        dom_icon = "🌐" if days > 30 else ("⚠️" if days > 7 else "🔴")
        lines.append(f"{dom_icon} Домен: {days} дн. до истечения")
        lines.append(f"   Регистратор: {domain_result['domain_info']['registrar']}")
    elif domain_result.get("error"):
        lines.append(f"⚠️ Домен: {domain_result['error']}")

    await message.answer(f"📋 Проверка {url}\n\n" + "\n".join(lines))


@router.message(Command("sites"))
async def cmd_sites(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    sites = await get_all_sites()
    if not sites:
        # Initialize sites from config
        for url in config.get_site_urls():
            await get_or_create_site(url)
        sites = await get_all_sites()

    lines = ["📋 Отслеживаемые сайты:\n"]
    for s in sites:
        lines.append(f"  • {s['url']}")

    await message.answer("\n".join(lines))


@router.message(Command("ssl"))
async def cmd_ssl(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    await message.answer("🔄 Проверяю SSL-сертификаты...")
    urls = config.get_site_urls()
    results = await check_all_ssl(urls)

    lines = ["🔒 SSL-сертификаты:\n"]
    for r in results:
        if r.get("ssl_info"):
            info = r["ssl_info"]
            days = info["days_left"]
            icon = "✅" if days > 14 else ("⚠️" if days > 3 else "🔴")
            lines.append(f"{icon} {r['url']}")
            lines.append(f"   Осталось: {days} дн.")
            lines.append(f"   Издатель: {info['issuer']}")
            lines.append(f"   Истекает: {info['not_after'][:10]}")
        else:
            lines.append(f"🔴 {r['url']}: {r.get('error', 'N/A')}")

    await message.answer("\n".join(lines))


@router.message(Command("domains"))
async def cmd_domains(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    await message.answer("🔄 Проверяю домены...")
    urls = config.get_site_urls()
    results = await check_all_domains(urls)

    lines = ["🌐 Домены:\n"]
    for r in results:
        info = r.get("domain_info")
        if info and info["days_left"] is not None:
            days = info["days_left"]
            icon = "✅" if days > 30 else ("⚠️" if days > 7 else "🔴")
            lines.append(f"{icon} {r['domain']}")
            lines.append(f"   Осталось: {days} дн.")
            lines.append(f"   Регистратор: {info['registrar']}")
            lines.append(f"   Истекает: {info['expiration_date'][:10]}")
            if info["name_servers"]:
                lines.append(f"   NS: {', '.join(info['name_servers'][:3])}")
        else:
            lines.append(f"⚠️ {r.get('domain', r['url'])}: {r.get('error', 'N/A')}")

    await message.answer("\n".join(lines))


@router.message(Command("links"))
async def cmd_links(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /links <url>\nПример: /links https://s.morgunov.tech")
        return

    url = args[1].strip()
    if not url.startswith("http"):
        url = f"https://{url}"

    await message.answer(f"🔄 Проверяю ссылки на {url}...")
    result = await check_links(url)

    if result.get("broken_links"):
        await message.answer(format_links_report(result))
    else:
        await message.answer(f"✅ Все {result['total_links']} ссылок на {url} работают")


@router.message(Command("uptime"))
async def cmd_uptime(message: Message):
    if not is_admin(message):
        await message.answer("⛔ Доступ только для администратора.")
        return

    sites = await get_all_sites()
    if not sites:
        await message.answer("Нет отслеживаемых сайтов.")
        return

    lines = ["📈 Uptime за 24 часа:\n"]
    for s in sites:
        stats = await get_uptime_stats(s["id"], hours=24)
        icon = "✅" if stats["uptime_pct"] >= 99 else ("⚠️" if stats["uptime_pct"] >= 95 else "🔴")
        lines.append(
            f"{icon} {s['url']}: {stats['uptime_pct']}% "
            f"(avg {stats['avg_response_ms']}ms, {stats['total_checks']} checks)"
        )

    await message.answer("\n".join(lines))
