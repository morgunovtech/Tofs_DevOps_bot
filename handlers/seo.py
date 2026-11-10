"""🔎 Search & AI visibility for one site.

One screen: what the audit measured (nothing blocks the robots / the site
is closed), what Search Console and Webmaster know about the index (when
connected, with the age of that data), and a numbered list of what gets in
the way — each with a «💡» button that opens the steps for this site's
hosting. Every list entry is an action; what is fine is a caption line.
The screen opens instantly from the snapshot; «🔄 Проверить снова» runs a
live audit (~30 s) and refreshes the index data too."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_site, get_state, resolve_incident, set_state
from handlers.common import ack, render, site_by_cb, with_running_bar
from monitors.seo_checker import check_all_seo
from services import gsc, index_status, seo_fixes, sitestatus, yandex_webmaster
from services import hosting as hosting_svc
from services.actions import seo_fix_keyboard
from services.humanize import level_icon
from utils.text import esc, fmt_duration, plural
from utils.urls import is_web_page, site_label

router = Router(name="seo")

FRESH_MINUTES = 24 * 60
MAX_ITEMS = 6
AI_CODES = {"robots_ai", "ai_blocked", "no_js"}
CF_SEEN = "ai_cf_seen:{}"   # the owner opened the Cloudflare check for this site


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


def _hindrances(n: int) -> str:
    return f"{n} {plural(n, 'помеха', 'помехи', 'помех')}"


def index_lines(snap: dict, google_on: bool, yandex_on: bool) -> tuple[list[str], list[str]]:
    """(lines about the index, engines that say the site is NOT there).
    Says only what a fresh answer says; old data is dated, not asserted."""
    lines, absent = [], []
    g = snap.get("google") if google_on else None
    if google_on:
        if not g:
            lines.append("📇 Google: подключён, данные появятся после утренней проверки")
        else:
            indexed = g.get("verdict") == "PASS"
            crawl = index_status.fmt_day(g.get("last_crawl"))
            if index_status.is_stale(g):
                on = index_status.fmt_day(g.get("at"))
                lines.append(f"📇 Google: по данным на {on} главная {'была' if indexed else 'не была'} в индексе"
                             + ("" if indexed else f" 🔴 ({esc(g.get('coverage') or '?')})") + " — свежих нет")
            elif indexed:
                lines.append("📇 Google: главная в индексе" + (f" (обход {crawl})" if crawl else ""))
            else:
                lines.append(f"📇 Google: главной нет в индексе 🔴 ({esc(g.get('coverage') or '?')})")
                absent.append("Google")
    y = snap.get("yandex") if yandex_on else None
    if yandex_on:
        if not y:
            lines.append("📇 Яндекс: подключён, данные появятся после утренней проверки")
        else:
            chunk = []
            pages = y.get("searchable_pages")
            stale = index_status.is_stale(y)
            if pages is not None:
                if pages > 0:
                    chunk.append(f"{pages} стр. в поиске")
                else:
                    chunk.append("в поиске 0 страниц 🔴")
                    if not stale:
                        absent.append("Яндекс")
            if y.get("sqi") is not None:
                chunk.append(f"ИКС {y['sqi']}")
            problems = y.get("alert_problems") or {}
            if problems:
                n = index_status.yandex_problem_count(problems)
                chunk.append(f"{level_icon(index_status.yandex_problem_level(problems))} {n} "
                             f"{plural(n, 'проблема', 'проблемы', 'проблем')} в Вебмастере")
            if chunk:
                prefix = f"по данным на {index_status.fmt_day(y.get('at'))}: " if stale else ""
                lines.append("📇 Яндекс: " + prefix + ", ".join(chunk))
    return lines, absent


def ai_unseen(seo: dict, codes: set[str], hosting: str | None) -> bool:
    """A Cloudflare site the audit found open to AI: robots.txt lets the bots
    in and a request with their signature passed. Cloudflare recognises the
    real crawlers by address (and blocks them by default on domains added
    since July 2025), which no request from here can show — so the screen
    does not say «open to AI assistants» and offers the check in the panel."""
    return (hosting == "cloudflare" and seo.get("status") not in (None, "error")
            and not codes & {"robots_ai", "ai_blocked"})


async def cloudflare_check_pending(site_id: int, snap: dict) -> bool:
    """The site card says it once: until the owner has opened «🤖 Проверить
    в Cloudflare», a clean Cloudflare site carries a line and a button about
    what the audit cannot see. The search screen keeps it in its caption."""
    seo = snap.get("seo") or {}
    codes = {p.get("code") for p in seo.get("problems") or []}
    return ai_unseen(seo, codes, snap.get("hosting")) and not await get_state(CF_SEEN.format(site_id))


def headline(seo: dict, groups: list[tuple[str, str, list[str]]], absent: list[str],
             ai_checked: bool = True) -> str:
    """Asserts only what was measured: robots/noindex → «открыт/закрыт»,
    an index verdict → only when Search Console / Webmaster said so, access
    for AI assistants → only where the probe can see it (not on Cloudflare)."""
    n = len(groups)
    if not seo or seo.get("status") is None:
        return "⏳ Аудита ещё не было — нажми «🔄 Проверить снова»."
    if seo.get("incomplete"):
        # robots.txt or the homepage did not answer: the audit saw a part of
        # the site and has no verdict — and closed nothing (monitors.seo_checker).
        return ("⚠️ Часть проверки не удалась (robots.txt или главная не ответили) — вердикта нет; "
                "открытые проблемы остаются в «Что сейчас не так».")
    if seo.get("status") == "error":
        return "⚠️ Сайт не открывался, когда я проверял — аудит пропущен. Нажми «🔄 Проверить снова»."
    if any(g[1] == "critical" for g in groups):
        return "🔴 <b>Сайт закрыт от поиска</b> — Google и Яндекс уберут его при следующем обходе."
    if absent:
        where = " и ".join(absent).replace("Яндекс", "Яндексе")
        tail = (f" Есть {_hindrances(n)} — начни с {'неё' if n == 1 else 'них'}." if n
                else " С моей стороны помех нет — причину покажет Search Console или Вебмастер.")
        return f"🔴 <b>В {where} сайта нет</b>.{tail}"
    if n:
        return f"🟠 Сайт открыт для поисковиков, но есть {_hindrances(n)} — у каждой кнопка «что делать»."
    if not ai_checked:
        return "✅ Сайт открыт для поисковиков, помех нет."
    return "✅ Сайт открыт для поисковиков и ИИ-ассистентов, помех нет."


def seo_text(url: str, snap: dict, *, google_on: bool = False, yandex_on: bool = False) -> str:
    """Pure: the audit screen from the site snapshot. Messages may contain
    raw tags («нет <title>») and are escaped here."""
    label = esc(site_label(url))
    seo = snap.get("seo") or {}
    problems = seo.get("problems") or []
    groups = grouped(problems)
    idx_lines, absent = index_lines(snap, google_on, yandex_on)
    codes = {g[0] for g in groups}
    unseen = ai_unseen(seo, codes, snap.get("hosting"))
    lines = [f"🔎 {label} в поиске и у ИИ", "", headline(seo, groups, absent, ai_checked=not unseen)]
    chars = seo.get("no_js_chars")
    if "robots_ai" in codes:
        lines.append("🤖 ИИ-ассистенты: сайт для них закрыт.")
    elif "ai_blocked" in codes:
        lines.append("🤖 ИИ-ассистенты: запрос с их подписью получает отказ — скорее всего, сайт для них закрыт.")
    elif "no_js" in codes:
        lines.append(f"🤖 ИИ-ассистенты: видят почти пустую страницу — {chars} "
                     f"{plural(chars or 0, 'символ', 'символа', 'символов')} текста без JavaScript.")
    lines += idx_lines
    if groups:
        lines += ["", "<b>Что мешает</b> (номер — кнопка ниже):"]
        for i, (code, sev, msgs) in enumerate(groups[:MAX_ITEMS], 1):
            f = seo_fixes.fix(code)
            lines.append(f"{i}. {level_icon(sev)} <b>{esc(f.title)}</b>")
            lines += [f"    {esc(m)}" for m in msgs[:3]]
            if len(msgs) > 3:
                lines.append(f"    … и ещё {len(msgs) - 3} стр.")
        if len(groups) > MAX_ITEMS:
            lines.append(f"… и ещё {len(groups) - MAX_ITEMS}: покажу после того, как починишь эти.")
    # The caption: what was measured and found in order, dated — no icons,
    # no counters of non-problems.
    tail = []
    pages = seo.get("pages")
    if pages:
        tail.append(f"проверил {pages} {plural(pages, 'страницу', 'страницы', 'страниц')}")
    if chars is not None and not (codes & AI_CODES):
        tail.append(f"без JavaScript видно {chars} {plural(chars, 'символ', 'символа', 'символов')} текста")
    if _ago(seo):
        tail.append(_ago(seo))
    caption = " · ".join(tail) if tail else "ещё не проверял"
    if unseen:
        caption += (". ИИ-ассистентов robots.txt пускает, и запрос с их подписью проходит, но настоящих ботов "
                    "Cloudflare узнаёт по адресу — пускает ли он их, видно только в его панели (кнопка ниже)")
    caption += ". Критичное (🔴) присылаю сразу, как замечу; остальное (🟠) не шлю — оно здесь, в «Проблемах» и в сводках."
    if not google_on and not yandex_on:
        caption += " В индексе ли сайт, знают только Google и Яндекс — подключи Search Console или Вебмастер."
    lines += ["", f"<i>{caption}</i>"]
    return "\n".join(lines)


def seo_keyboard(site_id: int, seo: dict, *, google_on: bool = False, yandex_on: bool = False,
                 hosting: str | None = None) -> InlineKeyboardMarkup:
    groups = grouped(seo.get("problems") or [])
    rows = seo_fix_keyboard(site_id, [g[0] for g in groups[:MAX_ITEMS]], numbered=True).inline_keyboard
    if ai_unseen(seo, {g[0] for g in groups}, hosting):
        rows += seo_fix_keyboard(site_id, ["ai_cloudflare"]).inline_keyboard
    if not google_on and not yandex_on:
        rows.append([InlineKeyboardButton(text="🔌 Подключить Google или Яндекс", callback_data="menu_diag")])
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


async def show_seo(call: CallbackQuery, site: dict):
    snap = await sitestatus.get(site["id"])
    google_on, yandex_on = gsc.available(), yandex_webmaster.available()
    await render(call, seo_text(site["url"], snap, google_on=google_on, yandex_on=yandex_on),
                 seo_keyboard(site["id"], snap.get("seo") or {}, google_on=google_on, yandex_on=yandex_on,
                              hosting=snap.get("hosting")))


async def run_live(call: CallbackQuery, site: dict):
    base = (f"Смотрю на {esc(site_label(site['url']))} глазами Google, Яндекса и ИИ-ассистентов…\n"
            "(это занимает около 30 секунд)")
    await render(call, f"▰▱▱ {base}")
    done = await with_running_bar(call.message, base, audit_now(site))
    await show_seo(call, site)
    if not done:
        await call.message.answer("⚠️ Проверка не удалась — на экране прошлые данные. Попробуй ещё раз позже.")


async def audit_now(site: dict) -> bool:
    """A live audit for a screen; False when the audit itself did not run
    (an error inside the check — the snapshot shows the previous data).
    Read-only, with one exception: a clean audit closes an open search
    finding — the screen says «помех нет», the list must not disagree. A
    new finding still waits for the daily audit to become an incident."""
    results = await check_all_seo([site["url"]], manage=False)
    if results and results[0].status == "ok":
        await resolve_incident(site["id"], "seo")
    await index_status.refresh(site["id"], site["url"])
    return bool(results)


async def _auditable(call: CallbackQuery, site: dict | None) -> bool:
    """Search and AI visibility is a question about web pages only — an old
    button on a monitor that became an API check answers so instead."""
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return False
    if not is_web_page(site["url"], site.get("http_method")):
        await ack(call, "Это API-проверка или порт: поиск и ИИ к ним не относятся", show_alert=True)
        return False
    return True


@router.callback_query(F.data.startswith("seo_site:"))
async def cb_seo(call: CallbackQuery):
    """Instant when the daily audit is fresh, live otherwise."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not await _auditable(call, site):
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
    if not await _auditable(call, site):
        return
    await ack(call, "Проверяю…")
    await run_live(call, site)


@router.callback_query(F.data.startswith("seo_fix:"))
async def cb_seo_fix(call: CallbackQuery):
    """seo_fix:<site>:<code>[:a] — «:a» when the button sits on an alert."""
    parts = call.data.split(":")
    args = parts[1:3] if len(parts) in (3, 4) else None
    on_alert = len(parts) == 4 and parts[3] == "a"
    site = await get_site(int(args[0])) if args and args[0].isdigit() else None
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call)
    snap = await sitestatus.get(site["id"])
    code = args[1]
    if code == "ai_cloudflare":
        await set_state(CF_SEEN.format(site["id"]), "1")
    text = fix_text(site["url"], code, snap.get("seo") or {}, snap.get("hosting"))
    kb = fix_keyboard(site["id"], site["url"], code, snap.get("hosting"))
    if on_alert and call.message is not None:
        await call.message.answer(text, reply_markup=kb)   # the 🔴 alert stays as it was
    else:
        await render(call, text, kb)
