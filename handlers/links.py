"""🔗 On-demand broken-links scan for one site."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from handlers.common import ack, back_button, render, site_by_cb, with_running_bar
from monitors.links_checker import check_links
from reports.formatter import format_links_report, pending_note
from services import humanize
from utils.i18n import plural, t
from utils.text import esc
from utils.urls import is_web_page, site_label

router = Router(name="links")


@router.callback_query(F.data.startswith("check_links:"))
async def cb_check_links(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, t("Сайт не найден — список сайтов изменился. Открой меню заново."),
                     back_button())
        return
    url = site["url"]
    if not is_web_page(url, site.get("http_method")):
        await render(call, t("🔌 {label} — API-проверка: страниц со ссылками у неё нет.").format(label=esc(site_label(url))),
                     back_button(t("← К сайту"), f"check_site:{site['id']}"))
        return
    base = t("Проверяю ссылки на {label}…\n(обычно до 30 секунд)").format(label=esc(site_label(url)))
    await render(call, f"▰▱▱ {base}")
    r = await with_running_bar(call.message, base, check_links(url, manage=False))
    label = esc(site_label(url))
    if r.status == "error":
        text = t("❌ Не смог проверить {label}: {why}").format(label=label, why=esc(humanize.describe_error(r.error)))
    elif r.broken:
        text = format_links_report(r)
        if r.pending:
            text += "\n\n" + pending_note(r.pending)
    elif r.pending:
        # Not «все открываются» while some wait for a recheck: one verdict.
        n = len(r.pending)
        text = (t("🟡 На {label} {n} {words} из {checked} не {words2} с первого раза — похоже на временный сбой, перепроверю при следующем обходе.").format(label=label, n=n, words=plural(n, 'ссылка', 'ссылки', 'ссылок'), checked=r.checked, words2=plural(n, 'открылась', 'открылись', 'открылись')))
    else:
        text = t("✅ На {label} все проверенные ссылки открываются ({checked})").format(label=label, checked=r.checked)
    if r.status != "error" and r.checked < r.total_links:
        text += ("\n\n" + t("На странице {total_links} ссылок — за раз проверяю {checked}, остальные — в следующие обходы.").format(total_links=r.total_links, checked=r.checked))
    await render(call, text, back_button(t("← К сайту"), f"check_site:{site['id']}"))
