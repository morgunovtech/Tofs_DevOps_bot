"""🔗 On-demand broken-links scan for one site."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from handlers.common import ack, back_button, render, site_by_cb, with_running_bar
from monitors.links_checker import check_links
from reports.formatter import format_links_report, pending_note
from services import humanize
from utils.text import esc, plural
from utils.urls import is_web_page, site_label

router = Router(name="links")


@router.callback_query(F.data.startswith("check_links:"))
async def cb_check_links(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился. Открой меню заново.",
                     back_button())
        return
    url = site["url"]
    if not is_web_page(url, site.get("http_method")):
        await render(call, f"🔌 {esc(site_label(url))} — API-проверка: страниц со ссылками у неё нет.",
                     back_button("← К сайту", f"check_site:{site['id']}"))
        return
    base = f"Проверяю ссылки на {esc(site_label(url))}…\n(обычно до 30 секунд)"
    await render(call, f"▰▱▱ {base}")
    r = await with_running_bar(call.message, base, check_links(url, manage=False))
    label = esc(site_label(url))
    if r.status == "error":
        text = f"❌ Не смог проверить {label}: {esc(humanize.describe_error(r.error))}"
    elif r.broken:
        text = format_links_report(r)
        if r.pending:
            text += "\n\n" + pending_note(r.pending)
    elif r.pending:
        # Not «все открываются» while some wait for a recheck: one verdict.
        n = len(r.pending)
        text = (f"🟡 На {label} {n} {plural(n, 'ссылка', 'ссылки', 'ссылок')} из {r.checked} не "
                f"{plural(n, 'открылась', 'открылись', 'открылись')} с первого раза — похоже на "
                f"временный сбой, перепроверю при следующем обходе.")
    else:
        text = f"✅ На {label} все проверенные ссылки открываются ({r.checked})"
    if r.status != "error" and r.checked < r.total_links:
        text += (f"\n\nНа странице {r.total_links} ссылок — за раз проверяю {r.checked}, "
                 f"остальные — в следующие обходы.")
    await render(call, text, back_button("← К сайту", f"check_site:{site['id']}"))
