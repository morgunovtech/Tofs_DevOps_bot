"""🔍 On-demand SEO/GEO audit."""

from aiogram import F, Router
from aiogram.types import CallbackQuery

from db.database import get_active_http_site_urls
from handlers.common import ack, back_button, render, with_running_bar
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
    lines = ["🔍 Виден ли сайт в поиске и ИИ-ассистентам\n"]
    for r in results:
        host = short_host(r.url)
        critical = [p for p in r.problems if p.severity == "critical"]
        improve = [p for p in r.problems if p.severity != "critical"]
        if r.transient:
            lines.append(f"⚠️ {esc(host)} — сайт сейчас не открывается, проверку пропустил")
        elif not r.problems:
            lines.append(f"✅ {esc(host)} — всё в порядке, проверено страниц: {r.pages_checked}")
        else:
            lines.append(f"{'🔴' if critical else '✅'} {esc(host)}"
                         + (f" — сайт исчезает из поиска, {len(critical)} "
                            f"{plural(len(critical), 'причина', 'причины', 'причин')}:" if critical
                            else " — в поиске виден, но есть что улучшить:"))
            for p in critical[:4]:
                lines.append(f"   🔴 {esc(p.message)}")
                if p.hint:
                    lines.append(f"      → {esc(p.hint)}")
            if improve:
                if critical:
                    lines.append(f"   💡 Можно улучшить ({len(improve)}):")
                for p in improve[:5]:
                    lines.append(f"   💡 {esc(p.message)}")
                    if p.hint:
                        lines.append(f"      → {esc(p.hint)}")
                if len(improve) > 5:
                    lines.append(f"   … и ещё {len(improve) - 5}")
        if r.no_js_chars is not None:
            lines.append(f"   📄 Текста без JavaScript: {r.no_js_chars} "
                         f"{plural(r.no_js_chars, 'символ', 'символа', 'символов')} "
                         f"(столько видят ИИ-ассистенты)")
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
    lines.append("Критичное (🔴) я присылаю сразу, как замечу. Остальное — здесь и в воскресном отчёте.")
    return "\n".join(lines)


@router.callback_query(F.data == "menu_seo")
async def cb_seo(call: CallbackQuery):
    await ack(call)
    urls = await get_active_http_site_urls()
    if not urls:
        await render(call, "Пока нет ни одного сайта — проверка видимости в поиске применима только к сайтам.",
                     back_button())
        return
    base = ("Смотрю на сайты глазами Google, Яндекса и ИИ-ассистентов…\n"
            "(это занимает около 30 секунд)")
    await render(call, f"▰▱▱ {base}")
    results = await with_running_bar(call.message, base, check_all_seo(urls, manage=False))
    gsc_status: dict[str, str] = {}
    if gsc.available():
        for u in urls:
            info = await gsc.inspect_url(u.rstrip("/") + "/")
            if info:
                gsc_status[u] = ("главная есть в поиске ✅" if info["verdict"] == "PASS"
                                 else f"главной НЕТ в поиске 🔴 ({info['coverage']})")
    yx_status = (await yandex_webmaster.get_summaries() or {}) if yandex_webmaster.available() else {}
    await render(call, build_seo_report(results, gsc_status, yx_status), back_button())
