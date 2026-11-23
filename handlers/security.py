"""🛡 Basic security of a site: the line on its card and the screen with
everything — service files, https, protective headers, CSP — and for each
problem the ready text for the site's hosting."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import RECHECK, ack, back_button, render, show, site_by_cb
from monitors.security_checker import HEADERS, check_security
from reports.formatter import finding_block
from services import sitestatus
from services.humanize import lower_first
from utils.i18n import fmt_duration, plural, t
from utils.text import esc
from utils.urls import is_web_page, site_label

router = Router(name="security")


def _icon(finding: dict) -> str:
    return "🔴" if finding["code"].startswith("exposed_") else "🟠"


def security_lines(sec: dict | None) -> list[str]:
    """The card: one line when all is well, each problem by name when not."""
    if sec is None:
        return [t("⏳ Безопасность: ещё не проверял")]
    findings = sec.get("findings") or []
    if not findings:
        parts = [t("только https")] if sec.get("plain_http") is False else []
        if sec.get("missing") is not None and sec.get("hosting") != "github":
            parts.append(t("защитные заголовки"))
        parts.append(t("служебные файлы снаружи не видны"))
        return [t("✅ Защита: {items}").format(items=', '.join(parts))]
    if len(findings) == 1:
        return [t("{icon} Защита: {text}").format(icon=_icon(findings[0]), text=esc(lower_first(findings[0]['title'])))]
    n = len(findings)
    worst = "🔴" if any(_icon(f) == "🔴" for f in findings) else "🟠"
    lines = [t("{worst} Защита: {n} {words}").format(worst=worst, n=n, words=plural(n, 'помеха', 'помехи', 'помех'))]
    lines += [f"    {i}. {_icon(f)} {esc(f['title'])}" for i, f in enumerate(findings, 1)]
    return lines


def _check_lines(sec: dict) -> list[str]:
    codes = {f["code"]: f for f in sec.get("findings") or []}
    lines = []
    exposed = [f for c, f in codes.items() if c.startswith("exposed_")]
    lines += [f"🔴 {esc(f['title'])}" for f in exposed] or [t("✅ .git и .env снаружи не видны")]
    if sec.get("missing") is None:   # an http:// site: the service files are all that is asked
        lines.append(t("ℹ️ Сайт работает по http:// — переход на https и защитные заголовки проверяю только у https-сайтов."))
        return lines
    if "https_missing" in codes:
        lines.append(f"🟠 {esc(codes['https_missing']['title'])}")
    elif sec.get("plain_http") is False:
        lines.append(t("✅ По http:// сайт перенаправляет на https://"))
    else:
        lines.append(t("⚠️ Не понял, перенаправляет ли http:// на https://"))
    missing = sec.get("missing") or []
    if sec.get("hosting") == "github" and missing:
        lines.append(t("ℹ️ Защитные заголовки: GitHub Pages не даёт ставить свои. Если они нужны — поставь сайт за Cloudflare (бесплатно) и добавь их там."))
    elif missing:
        lines.append(t("🟠 Защитные заголовки: нет {n} из {n2} — {items}").format(n=len(missing), n2=len(HEADERS), items=esc(', '.join(missing))))
    else:
        lines.append(t("✅ Защитные заголовки: все {n} на месте").format(n=len(HEADERS)))
    if sec.get("csp"):
        lines.append(t("✅ CSP есть: браузер не запустит чужие скрипты"))
    else:
        lines.append(t("ℹ️ CSP нет. Это следующий уровень защиты — запрещает браузеру запускать чужие скрипты. Ставить осторожно: неправильный CSP ломает сайт, поэтому я о нём не напоминаю."))
    return lines


async def security_page(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    sec = (await sitestatus.get(site["id"])).get("security")
    lines = [t("🛡 Безопасность {label}").format(label=esc(site_label(site['url']))), ""]
    if sec is None:
        lines.append(t("Ещё не проверял — проверю в течение шести часов или по кнопке ниже."))
    else:
        age = sitestatus.age_minutes(sec)
        lines += _check_lines(sec)
        findings = sec.get("findings") or []
        if findings:
            lines += ["", t("Что сделать:"), "", "\n\n".join(finding_block(i, f) for i, f in enumerate(findings, 1)),
                      "", t("После выкладки нажми «🔄 Проверить снова».")]
        if age is not None:
            lines += ["", t("<i>Проверял {words}.</i>").format(words=t("только что") if age < 1 else t("{duration} назад").format(duration=fmt_duration(age)))]
    rows = [[InlineKeyboardButton(text=t(RECHECK), callback_data=f"sec_check:{site['id']}")],
            [InlineKeyboardButton(text=t("🌍 К сайту"), callback_data=f"check_site:{site['id']}"),
             InlineKeyboardButton(text=t("🏠 Главная"), callback_data="menu_main")]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _checked_site(arg: str | None) -> dict | None:
    site = await site_by_cb(arg)
    return site if site and is_web_page(site["url"], site.get("http_method")) else None


@router.callback_query(F.data.startswith("sec:"))
async def cb_security(call: CallbackQuery):
    site = await _checked_site(call.data.split(":")[1])     # «sec:<id>:a» from an alert
    if not site:
        await ack(call, t("Безопасность этого адреса я не проверяю"), show_alert=True)
        return
    await ack(call)
    await show(call, *await security_page(site))


@router.callback_query(F.data.startswith("sec_check:"))
async def cb_security_check(call: CallbackQuery):
    site = await _checked_site(call.data.split(":", 1)[1])
    if not site:
        await ack(call)
        await render(call, t("Безопасность этого адреса я не проверяю."), back_button())
        return
    await ack(call, t("Проверяю…"))
    await render(call, t("▰▱▱ Смотрю {label} снаружи…").format(label=esc(site_label(site['url']))))
    checked = await check_security(site["url"], manage=False)
    text, kb = await security_page(site)
    if not checked:   # the site did not answer: the screen shows the last check, and says so
        text = t("⚠️ Сайт сейчас не ответил — показываю прошлую проверку. Попробуй через пару минут.\n\n") + text
    await render(call, text, kb)
