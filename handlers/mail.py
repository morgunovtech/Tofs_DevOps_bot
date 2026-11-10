"""✉️ Mail of a site's domain: the line on its card and the screen with
everything — who receives the mail, whether it accepts letters, SPF /
DKIM / DMARC, and for each problem the exact record to paste."""

import asyncio

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, back_button, render, show, site_by_cb
from monitors.mail_checker import DOMAIN_BUDGET_SEC, FAILS_TO_ALERT, carrier_of, check_mail
from reports.formatter import finding_block
from reports.scheduler import MAIL_RECHECK_MIN, schedule_mail_recheck
from services import integrations, sitestatus
from services.humanize import lower_first
from utils.text import esc, fmt_duration, plural
from utils.urls import host_of, registrable_domain

router = Router(name="mail")


def _ago(section: dict) -> str:
    age = sitestatus.age_minutes(section)
    return f" · {fmt_duration(age)} назад" if age is not None and age >= 1 else ""


def _who(mail: dict) -> str:
    return mail.get("provider") or (mail.get("mx") or ["почтовый сервер"])[0]


def _receives_line(mail: dict) -> str:
    """Does the domain accept letters — the thing that matters most. Said
    whole, so it reads the same at the top of the card among the problems."""
    who, domain = esc(_who(mail)), esc(mail.get("domain") or "")
    if mail.get("accepts") is False:
        if (mail.get("fails") or 0) >= FAILS_TO_ALERT:
            why = ("записи MX ведут на сервер, которого нет" if mail.get("reason") == "nowhere"
                   else f"{who} не отвечает")
            return f"🔴 Почта {domain} не принимает письма — {why}"
        return f"⏳ Почта: {who} не ответил — перепроверю через {MAIL_RECHECK_MIN} минут"
    if mail.get("accepts"):
        backup = " (основной сервер молчит, принимает запасной)" if mail.get("backup_only") else ""
        return f"✅ Почта принимает письма · {who}{backup}"
    if not integrations.second_opinion_enabled():
        return f"Почта · {who} — принимает ли письма, не проверяю: внешние проверки выключены"
    return f"Почта · {who}"


def mail_brief(mail: dict | None) -> tuple[str, str]:
    """(icon, words) for the domain's line on the main screen."""
    if mail is None:
        return "⏳", "ещё не проверял"
    if not mail.get("mx") and not mail.get("sends_only"):
        return "✅", "почты нет"
    if mail.get("accepts") is False and (mail.get("fails") or 0) >= FAILS_TO_ALERT:
        return "🔴", "не принимает письма"
    findings = mail.get("findings") or []
    if findings:
        rest = len(findings) - 1
        more = f" и ещё {rest} {plural(rest, 'помеха', 'помехи', 'помех')}" if rest else ""
        return "🟠", f"{lower_first(findings[0]['title'])}{more}"
    return "✅", _who(mail)


def mail_lines(mail: dict | None) -> list[str]:
    """The card: whether letters arrive, and the spam settings — a problem
    said whole («Почта example.com: …»), since the card lifts it to the top."""
    if mail is None:
        return ["⏳ Почта: ещё не проверял"]
    findings = mail.get("findings") or []
    if not mail.get("mx") and not mail.get("sends_only"):
        return ["✅ Почты на домене нет, слать письма от его имени запрещено" if mail.get("locked")
                else "✅ Почты на домене нет"]
    lines = [_receives_line(mail) if mail.get("mx")
             else "✅ Почта только отправляет письма — входящей на домене нет"]
    domain = esc(mail.get("domain") or "")
    if len(findings) == 1:
        lines.append(f"🟠 Почта {domain}: {esc(lower_first(findings[0]['title']))} — письма могут уходить в спам")
    elif findings:
        n = len(findings)
        lines.append(f"🟠 Почта {domain}: {n} {plural(n, 'помеха', 'помехи', 'помех')} — письма могут уходить "
                     f"в спам")
        lines += [f"    {i}. {esc(f['title'])}" for i, f in enumerate(findings[:5], 1)]
    elif mail.get("dmarc") == "none":
        lines.append("    DMARC только наблюдает: подделки твоего адреса всё ещё доходят. Поменяй p=none на "
                     "p=quarantine в записи _dmarc — тогда они будут уходить в спам.")
    return lines


def _record_lines(mail: dict) -> list[str]:
    """SPF, DKIM, DMARC — each one line, from the findings and the snapshot."""
    codes = {f["code"] for f in mail.get("findings") or []}
    by = {prefix: next((f for f in mail.get("findings") or [] if f["code"].startswith(prefix)), None)
          for prefix in ("spf_", "dkim_", "dmarc_")}
    lines = []
    if by["spf_"]:
        lines.append(f"🟠 SPF — {esc(by['spf_']['title'])}")
    else:
        lines.append("✅ SPF — одна запись, без ошибок")
    if by["dkim_"]:
        lines.append(f"🟠 DKIM — {esc(by['dkim_']['title'])}")
    elif mail.get("dkim") is True:
        lines.append("✅ DKIM — письма подписываются")
    elif mail.get("mx"):
        lines.append("ℹ️ DKIM — у этого почтового сервиса имя подписи выбираешь ты, проверить не могу")
    policy = mail.get("dmarc")
    if by["dmarc_"]:
        lines.append(f"🟠 DMARC — {esc(by['dmarc_']['title'])}")
    elif policy == "reject":
        lines.append("✅ DMARC — подделки твоего адреса отклоняются")
    elif policy == "quarantine":
        lines.append("✅ DMARC — подделки твоего адреса уходят в спам")
    elif policy == "none" and "dmarc_missing" not in codes:
        lines.append("ℹ️ DMARC — только наблюдает: подделки всё ещё доходят. Когда SPF и DKIM в порядке, "
                     "поменяй p=none на p=quarantine")
    return lines


async def mail_page(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    mail = (await sitestatus.get(site["id"])).get("mail")
    domain = registrable_domain(host_of(site["url"]))
    lines = [f"✉️ Почта на {esc(domain)}", ""]
    rows = []
    if mail is None:
        lines.append("Ещё не проверял — проверю в течение часа или по кнопке ниже.")
    elif not mail.get("mx") and not mail.get("sends_only"):
        lines.append("Почты на этом домене нет: никто не принимает письма на него. Если она появится — начну "
                     "следить сам.")
        if mail.get("locked"):
            lines.append("✅ Слать письма от его имени запрещено записью SPF «v=spf1 -all» — подделать адрес не "
                         "выйдет.")
    else:
        if mail.get("mx"):
            lines.append(f"Письма принимает: {esc(_who(mail))} ({esc(', '.join(mail['mx'][:3]))})")
            lines.append(_receives_line(mail) + _ago(mail))
        else:
            lines.append("Входящей почты на домене нет — проверяю только то, что нужно для отправки.")
        lines += _record_lines(mail)
        findings = mail.get("findings") or []
        if findings:
            lines += ["", "Что сделать:", "",
                      "\n\n".join(finding_block(i, f) for i, f in enumerate(findings, 1)), "",
                      "Изменения в DNS доходят за 5–30 минут — потом нажми «Перепроверить»."]
        elif mail.get("accepts") and mail.get("dmarc") in ("quarantine", "reject"):
            lines += ["", "Всё в порядке: письма доходят, а подделать твой адрес сложно."]
        if mail.get("status_page"):
            rows.append([InlineKeyboardButton(text=f"↗️ Статус {_who(mail)}", url=mail["status_page"])])
    rows.append([InlineKeyboardButton(text="🔄 Перепроверить почту", callback_data=f"mail_check:{site['id']}")])
    rows.append([InlineKeyboardButton(text="🌍 К сайту", callback_data=f"check_site:{site['id']}"),
                 InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _carrier_site(arg: str | None) -> dict | None:
    site = await site_by_cb(arg)
    return site if site and await carrier_of(site["url"]) == site["url"] else None


@router.callback_query(F.data.startswith("mail:"))
async def cb_mail(call: CallbackQuery):
    site = await _carrier_site(call.data.split(":")[1])     # «mail:<id>:a» from an alert
    if not site:
        await ack(call, "Почту этого домена я больше не проверяю", show_alert=True)
        return
    await ack(call)
    await show(call, *await mail_page(site))


@router.callback_query(F.data.startswith("mail_check:"))
async def cb_mail_check(call: CallbackQuery):
    site = await _carrier_site(call.data.split(":", 1)[1])
    if not site:
        await ack(call)
        await render(call, "Почту этого домена я больше не проверяю.", back_button())
        return
    await ack(call, "Проверяю почту…")
    domain = registrable_domain(host_of(site["url"]))
    await render(call, f"▰▱▱ Проверяю почту {esc(domain)} — это может занять до пары минут…")
    try:
        async with asyncio.timeout(DOMAIN_BUDGET_SEC):
            checked = await check_mail(domain, site["url"], manage=False)
    except TimeoutError:
        checked = []
    text, kb = await mail_page(site)
    if checked:
        # The screen promised a recheck: it is the hourly job's to alert, so
        # the hourly job is asked to come back in ten minutes.
        mail = (await sitestatus.get(site["id"])).get("mail") or {}
        if mail.get("accepts") is False and (mail.get("fails") or 0) < FAILS_TO_ALERT:
            schedule_mail_recheck()
    else:
        text = ("⚠️ Сейчас проверить не получилось: DNS или внешняя проверка не ответили — показываю прошлую "
                "проверку.\n\n" + text)
    await render(call, text, kb)
