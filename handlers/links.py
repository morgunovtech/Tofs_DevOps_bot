"""🔗 On-demand broken-links scan for one site."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from handlers.common import back_button, render, site_by_cb, sites_keyboard, with_running_bar
from monitors.links_checker import check_links
from reports.formatter import format_links_report
from utils.text import esc

router = Router(name="links")


@router.callback_query(F.data == "menu_links")
async def cb_links_menu(call: CallbackQuery):
    await call.answer()
    await render(call, "🔗 Выбери сайт для проверки ссылок:",
                 await sites_keyboard("check_links", http_only=True, back="menu_more"))


@router.callback_query(F.data.startswith("check_links:"))
async def cb_check_links(call: CallbackQuery):
    await call.answer()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился. Открой меню заново.",
                     back_button())
        return
    url = site["url"]
    base = f"Сканирую все ссылки на {esc(url)}…\n(это может занять ~30 сек)"
    await render(call, f"▰▱▱ {base}")
    r = await with_running_bar(call.message, base, check_links(url, manage=False))
    if r.status == "error":
        text = f"❌ Не удалось просканировать {esc(url)}: {esc(r.error or 'страница не загрузилась')}"
    elif r.broken_internal:
        text = format_links_report(r)
    else:
        text = f"✅ Все внутренние ссылки на {esc(url)} работают (проверено: {r.total_links})"
        if r.broken_external:
            text += f"\nℹ️ Внешних ресурсов недоступно: {len(r.broken_external)} — обычно не критично"
    await render(call, text, back_button())
