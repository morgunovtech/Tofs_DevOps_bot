"""🔎 Search & AI visibility for one site.

One screen: is the site found, do AI assistants see it, and a numbered list
of what gets in the way — each with a «💡» button that opens the steps for
this site's hosting. The screen opens instantly from the daily audit's
snapshot; «🔄 Проверить снова» runs a live audit (~30 s)."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_site
from handlers.common import ack, cb_args, render, site_by_cb, with_running_bar
from monitors.seo_checker import check_all_seo
from services import gsc, seo_fixes, sitestatus, yandex_webmaster
from services import hosting as hosting_svc
from services.actions import seo_fix_keyboard
from utils.text import esc, fmt_duration, plural
from utils.urls import is_http_url, short_host, site_label

router = Router(name="seo")

FRESH_MINUTES = 24 * 60
MAX_ITEMS = 6


def grouped(problems: list[dict]) -> list[tuple[str, str, list[str]]]:
    """[(code, severity, [messages])] in first-seen order — one list item and
    one button per kind of problem, however many pages share it."""
    out: dict[str, tuple[str, list[str]]] = {}
    for p in problems:
        code = p.get("code") or "?"
        if code not in out:
            out[code] = (p.get("severity", "warning"), [])
        out[code][1].append(p.get("message", ""))
    return [(code, sev, msgs) for code, (sev, msgs) in out.items()]


def _ago(seo: dict) -> str:
    age = sitestatus.age_minutes(seo)
    if age is None:
        return ""
    return "только что" if age < 2 else f"{fmt_duration(int(age))} назад"


def seo_text(url: str, seo: dict, gsc_line: str | None = None, yx: dict | None = None) -> str:
    """Pure: the audit screen from a snapshot section. Messages may contain
    raw tags («нет <title>») and are escaped here."""
    label = esc(site_label(url))
    problems = seo.get("problems") or []
    infos = seo.get("infos") or []
    groups = grouped(problems)
    critical = [g for g in groups if g[1] == "critical"]
    lines = [f"🔎 {label} в поиске и у ИИ", ""]
    if seo.get("status") == "error":
        lines.append("⚠️ Сайт не открывался, когда я проверял — аудит пропущен. Нажми «🔄 Проверить снова».")
    elif critical:
        lines.append("🔴 <b>Сайт закрыт от поиска</b> — Google и Яндекс уберут его при следующем обходе.")
    elif groups:
        n = len(groups)
        lines.append(f"✅ В поиске виден. {n} {plural(n, 'помеха', 'помехи', 'помех')} — у каждой есть кнопка «что делать».")
    else:
        lines.append("✅ В поиске виден, ИИ-ассистентам открыт. Мешать нечему.")
    codes = {g[0] for g in groups}
    chars = seo.get("no_js_chars")
    if codes & {"robots_ai", "ai_blocked"}:
        lines.append("🤖 ИИ-ассистенты: сайт для них закрыт.")
    elif "no_js" in codes:
        lines.append(f"🤖 ИИ-ассистенты: видят почти пустую страницу — {chars} "
                     f"{plural(chars or 0, 'символ', 'символа', 'символов')} текста без JavaScript.")
    elif chars is not None:
        lines.append(f"🤖 ИИ-ассистенты: видят текст сайта ({chars} {plural(chars, 'символ', 'символа', 'символов')}).")
    if gsc_line:
        lines.append(f"📇 Google: {gsc_line}")
    if yx:
        chunk = []
        if yx.get("searchable_pages") is not None:
            chunk.append(f"{yx['searchable_pages']} стр. в поиске")
        if yx.get("sqi") is not None:
            chunk.append(f"ИКС {yx['sqi']}")
        if yx.get("alert_problems"):
            chunk.append(f"🔴 проблем в Вебмастере: {len(yx['alert_problems'])}")
        if chunk:
            lines.append("📇 Яндекс: " + ", ".join(chunk))
    if groups:
        lines += ["", "<b>Что мешает</b> (номер — кнопка ниже):"]
        for i, (code, sev, msgs) in enumerate(groups[:MAX_ITEMS], 1):
            f = seo_fixes.fix(code)
            lines.append(f"{i}. {'🔴' if sev == 'critical' else f.icon} <b>{esc(f.title)}</b>")
            lines += [f"    {esc(m)}" for m in msgs[:3]]
            if len(msgs) > 3:
                lines.append(f"    … и ещё {len(msgs) - 3} стр.")
        if len(groups) > MAX_ITEMS:
            lines.append(f"… и ещё {len(groups) - MAX_ITEMS}: покажу после того, как починишь эти.")
    if infos:
        lines += ["", f"<b>Мелочи</b> ({len(infos)}), не мешают быть в поиске:"]
        lines += [f"  • {esc(i.get('message', ''))}" for i in infos[:6]]
        if len(infos) > 6:
            lines.append(f"  • … и ещё {len(infos) - 6}")
    pages = seo.get("pages")
    tail = []
    if pages:
        tail.append(f"проверено страниц: {pages}")
    if _ago(seo):
        tail.append(f"проверял {_ago(seo)}")
    lines += ["", f"<i>{' · '.join(tail) if tail else 'ещё не проверял'}. Критичное (🔴) присылаю сразу, "
                  f"как замечу; остальное — здесь и в воскресном отчёте.</i>"]
    return "\n".join(lines)


def seo_keyboard(site_id: int, seo: dict) -> InlineKeyboardMarkup:
    groups = grouped(seo.get("problems") or [])
    rows = seo_fix_keyboard(site_id, [g[0] for g in groups[:MAX_ITEMS]], numbered=True).inline_keyboard
    if seo.get("infos"):
        rows.append([InlineKeyboardButton(text="💡 Мелочи: что с ними делать", callback_data=f"seo_minor:{site_id}")])
    rows.append([InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"seo_live:{site_id}"),
                 InlineKeyboardButton(text="← К сайту", callback_data=f"check_site:{site_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def fix_text(url: str, code: str, seo: dict, hosting: str | None) -> str:
    f = seo_fixes.fix(code)
    found = [p.get("message", "") for p in (seo.get("problems") or []) if p.get("code") == code]
    lines = [f"{f.icon} <b>{esc(f.title)}</b>", f"<i>{esc(site_label(url))}</i>"]
    if found:
        lines += ["", "Что я увидел:"] + [f"  • {esc(m)}" for m in found[:4]]
    if f.why:
        lines += ["", f"<b>Чем грозит:</b> {esc(f.why)}"]
    steps = seo_fixes.steps(code, hosting, url)
    lines += ["", "<b>Что делать:</b>"] + [f"{i}. {esc(s)}" for i, s in enumerate(steps, 1)]
    if hosting in f.by_hosting:
        lines += ["", f"<i>Шаги для {esc(hosting_svc.label(hosting) or hosting)} — я узнал хостинг по ответам сервера.</i>"]
    return "\n".join(lines)


def fix_keyboard(site_id: int, url: str, code: str, hosting: str | None) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, url=target)] for label, target in seo_fixes.links(code, hosting, url)]
    rows.append([InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"seo_live:{site_id}"),
                 InlineKeyboardButton(text="← Назад", callback_data=f"seo_site:{site_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minor_text(url: str, seo: dict) -> str:
    lines = [f"💡 Мелочи на {esc(site_label(url))}",
             "Не мешают быть в поиске, но делают сайт аккуратнее. Одна строка — одно действие.", ""]
    for i, info in enumerate((seo.get("infos") or [])[:12], 1):
        f = seo_fixes.fix(info.get("code") or "")
        lines.append(f"{i}. {f.icon} <b>{esc(info.get('message', ''))}</b>")
        if f.why:
            lines.append(f"    {esc(f.why)}")
        for s in seo_fixes.steps(f.code, None, url)[:1]:
            lines.append(f"    → {esc(s)}")
    return "\n".join(lines)


async def _integration_lines(url: str) -> tuple[str | None, dict | None]:
    gsc_line = None
    if gsc.available():
        info = await gsc.inspect_url(url.rstrip("/") + "/")
        if info:
            gsc_line = ("главная есть в поиске ✅" if info["verdict"] == "PASS"
                        else f"главной НЕТ в поиске 🔴 ({info['coverage']})")
    yx = None
    if yandex_webmaster.available():
        yx = ((await yandex_webmaster.get_summaries()) or {}).get(short_host(url))
    return gsc_line, yx


async def show_seo(call: CallbackQuery, site: dict):
    seo = (await sitestatus.get(site["id"])).get("seo") or {}
    gsc_line, yx = await _integration_lines(site["url"])
    await render(call, seo_text(site["url"], seo, gsc_line, yx), seo_keyboard(site["id"], seo))


async def run_live(call: CallbackQuery, site: dict):
    base = (f"Смотрю на {esc(site_label(site['url']))} глазами Google, Яндекса и ИИ-ассистентов…\n"
            "(это занимает около 30 секунд)")
    await render(call, f"▰▱▱ {base}")
    await with_running_bar(call.message, base, check_all_seo([site["url"]], manage=False))
    await show_seo(call, site)


@router.callback_query(F.data.startswith("seo_site:"))
async def cb_seo(call: CallbackQuery):
    """Instant when the daily audit is fresh, live otherwise."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site or not is_http_url(site["url"]):
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call)
    seo = (await sitestatus.get(site["id"])).get("seo") or {}
    age = sitestatus.age_minutes(seo)
    if "problems" in seo and age is not None and age < FRESH_MINUTES:
        await show_seo(call, site)
    else:
        await run_live(call, site)


@router.callback_query(F.data.startswith("seo_live:"))
async def cb_seo_live(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site or not is_http_url(site["url"]):
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call, "Проверяю…")
    await run_live(call, site)


@router.callback_query(F.data.startswith("seo_fix:"))
async def cb_seo_fix(call: CallbackQuery):
    args = cb_args(call, 2)
    site = await get_site(int(args[0])) if args and args[0].isdigit() else None
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call)
    snap = await sitestatus.get(site["id"])
    code = args[1]
    await render(call, fix_text(site["url"], code, snap.get("seo") or {}, snap.get("hosting")),
                 fix_keyboard(site["id"], site["url"], code, snap.get("hosting")))


@router.callback_query(F.data.startswith("seo_minor:"))
async def cb_seo_minor(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call)
    seo = (await sitestatus.get(site["id"])).get("seo") or {}
    await render(call, minor_text(site["url"], seo), InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="← Назад", callback_data=f"seo_site:{site['id']}")]]))
