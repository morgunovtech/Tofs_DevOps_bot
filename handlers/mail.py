"""✉️ Mail of a site's domain: the line on its card and the screen with
everything — who receives the mail, whether it accepts letters, SPF /
DKIM / DMARC, and for each problem the exact record to paste."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, back_button, render, site_by_cb
from monitors.mail_checker import carrier_of, check_mail
from reports.formatter import finding_block
from services import integrations, sitestatus
from utils.text import esc, fmt_duration, plural
from utils.urls import host_of, registrable_domain

router = Router(name="mail")


def _ago(section: dict) -> str:
    age = sitestatus.age_minutes(section)
    return f" · {fmt_duration(age)} назад" if age is not None and age >= 1 else ""


def _who(mail: dict) -> str:
    return mail.get("provider") or (mail.get("mx") or ["почтовый сервер"])[0]


def _receives_line(mail: dict) -> str:
    """Does the domain accept letters — the thing that matters most."""
    who = esc(_who(mail))
    if mail.get("accepts") is False:
        if (mail.get("fails") or 0) >= 2:
            why = ("записи MX ведут на сервер, которого нет" if mail.get("reason") == "nowhere"
                   else f"{who} не отвечает")
            return f"🔴 Почта не принимает письма — {why}"
        return f"⏳ Почта: {who} не ответил — перепроверю через 10 минут"
    if mail.get("accepts"):
        backup = " (основной сервер молчит, принимает запасной)" if mail.get("backup_only") else ""
        return f"✅ Почта: {who}, принимает письма{backup}"
    if not integrations.second_opinion_enabled():
        return f"✉️ Почта: {who} — принимает ли письма, не проверяю: внешние проверки выключены"
    return f"✉️ Почта: {who}"


def _auth_ok(mail: dict) -> str:
    """«SPF, DKIM, DMARC в порядке» — only what was actually seen fine."""
    ok = [name for name, fine in (("SPF", mail.get("spf") == 1), ("DKIM", mail.get("dkim") is True),
                                  ("DMARC", mail.get("dmarc") in ("quarantine", "reject"))) if fine]
    return f"{', '.join(ok)} в порядке" if ok else ""


def mail_lines(mail: dict | None) -> list[str]:
    """The card: one line when all is well, the problems by name when not."""
    if mail is None:
        return ["⏳ Почта: ещё не проверял"]
    findings = mail.get("findings") or []
    if not mail.get("mx") and not mail.get("sends_only"):
        return ["✅ Почты на этом домене нет, слать письма от его имени запрещено" if mail.get("locked")
                else "✉️ Почты на этом домене нет"]
    head = (_receives_line(mail) if mail.get("mx")
            else "✉️ Почта: только отправка писем — входящей почты на домене нет")
    if not findings:
        auth = _auth_ok(mail)
        lines = [f"{head}{f' · {auth}' if auth else ''}{_ago(mail)}"]
    else:
        n = len(findings)
        lines = [f"{head}{_ago(mail)}",
                 f"🟠 Настройки почты: {n} {plural(n, 'помеха', 'помехи', 'помех')} — письма могут уходить в спам"]
        lines += [f"    {i}. {esc(f['title'])}" for i, f in enumerate(findings[:5], 1)]
    if mail.get("dmarc") == "none" and not findings:
        lines.append("    ℹ️ DMARC только наблюдает: подделки твоего адреса всё ещё доходят. Поменяй p=none на "
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
    site = await _carrier_site(call.data.split(":", 1)[1])
    if not site:
        await ack(call, "Почту этого домена я больше не проверяю", show_alert=True)
        return
    await ack(call)
    await render(call, *await mail_page(site))


@router.callback_query(F.data.startswith("mail_check:"))
async def cb_mail_check(call: CallbackQuery):
    site = await _carrier_site(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Почту этого домена я больше не проверяю.", back_button())
        return
    await ack(call, "Проверяю почту…")
    domain = registrable_domain(host_of(site["url"]))
    await render(call, f"▰▱▱ Проверяю почту {esc(domain)} — около 20 секунд…")
    await check_mail(domain, site["url"], manage=False)
    await render(call, *await mail_page(site))
