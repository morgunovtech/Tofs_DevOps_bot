"""🔍 On-demand SEO/GEO audit."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from db.database import get_active_http_site_urls
from handlers.common import back_button, render, with_running_bar
from monitors.base import SeoResult
from monitors.seo_checker import check_all_seo
from services import gsc, yandex_webmaster
from utils.text import esc, plural
from utils.urls import short_host

router = Router(name="seo")


def build_seo_report(results: list[SeoResult], gsc_status: dict[str, str],
                     yx_status: dict[str, dict]) -> str:
    """Pure: problem texts mention raw tags like «нет <title>» and must
    arrive escaped, or parse mode rejects the whole message."""
    lines = ["🔍 SEO/GEO-аудит:\n"]
    for r in results:
        host = short_host(r.url)
        if r.transient:
            lines.append(f"⚠️ {esc(host)} — сайт недоступен, аудит пропущен")
        elif not r.problems:
            lines.append(f"✅ {esc(host)} — всё чисто (проверено страниц: {r.pages_checked})")
        else:
            lines.append(f"{'🔴' if r.has_critical else '⚠️'} {esc(host)} — проблем: {len(r.problems)}")
            lines += [f"   {'🔴' if p.severity == 'critical' else '⚠️'} {esc(p.message)}"
                      for p in r.problems[:6]]
            if len(r.problems) > 6:
                lines.append(f"   … и ещё {len(r.problems) - 6}")
        if r.no_js_chars is not None:
            lines.append(f"   📄 Текст без JS: {r.no_js_chars} "
                         f"{plural(r.no_js_chars, 'символ', 'символа', 'символов')}")
        if r.url in gsc_status:
            lines.append(f"   📇 Google: {esc(gsc_status[r.url])}")
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
                lines.append("   📇 Яндекс: " + ", ".join(chunk))
        lines += [f"   ℹ️ {esc(note)}" for note in r.infos[:3]]
        lines.append("")
    return "\n".join(lines)


@router.callback_query(F.data == "menu_seo")
async def cb_seo(call: CallbackQuery):
    await call.answer()
    urls = await get_active_http_site_urls()
    if not urls:
        await render(call, "Веб-сайтов пока нет — SEO-аудит не применим к tcp/ping-мониторам.",
                     back_button())
        return
    base = ("Гоняю SEO/GEO-аудит по всем сайтам…\n"
            "(robots, sitemap, мета, noindex, AI-боты, контент без JS — ~30 сек)")
    await render(call, f"▰▱▱ {base}")
    results = await with_running_bar(call.message, base, check_all_seo(urls, manage=False))
    gsc_status: dict[str, str] = {}
    if gsc.available():
        for u in urls:
            info = await gsc.inspect_url(u.rstrip("/") + "/")
            if info:
                gsc_status[u] = ("в индексе ✅" if info["verdict"] == "PASS"
                                 else f"НЕ в индексе 🔴 ({info['coverage']})")
    yx_status = (await yandex_webmaster.get_summaries() or {}) if yandex_webmaster.available() else {}
    await render(call, build_seo_report(results, gsc_status, yx_status), back_button())
