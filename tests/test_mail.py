"""Mail on the sites' domains: the verdicts (pure), the job end to end
with a fake DNS and a fake check-host, the card and the ✉️ screen."""

from conftest import FakeCall, buttons

from db.database import get_active_incidents, set_dns_state
from handlers import incidents as incidents_screen
from handlers import mail as mail_screen
from handlers.sites import card_lines
from monitors import dns_checker, mail_checker
from monitors.mail_checker import PROVIDERS, assess, carriers, merge_spf
from reports import scheduler
from reports.formatter import format_dns_change
from services import humanize, integrations, sitestatus

PROTON = next(p for p in PROVIDERS if p.name == "Proton")
PROTON_MX = ["mail.protonmail.ch", "mailsec.protonmail.ch"]


def codes(findings) -> list[str]:
    return [f.code for f in findings]


def test_two_spf_records_become_one_ready_record():
    """The owner's own domain: Proton plus leftovers of iCloud and Zoho in a
    second record — one record to paste, and the leftovers named."""
    spf = ["v=spf1 include:_spf.protonmail.ch ~all", "v=spf1 include:icloud.com include:zohomail.com ~all"]
    found = assess("ex.test", PROTON_MX, spf, 0, set(), True, ["v=DMARC1; p=quarantine"], "в DNS у Cloudflare")
    assert codes(found) == ["spf_multiple"]
    f = found[0]
    assert f.title == "2 записи SPF вместо одной"
    assert f.record == "v=spf1 include:_spf.protonmail.ch include:icloud.com include:zohomail.com ~all"
    assert "в DNS у Cloudflare" in f.fix
    assert f.note == "Если через iCloud и Zoho Mail ты больше не отправляешь почту — убери их из записи."


def test_nothing_set_up_gives_each_record_to_paste():
    google = ["aspmx.l.google.com"]
    found = assess("ex.test", google, [], 0, set(), False, [])
    assert codes(found) == ["spf_missing", "dkim_missing", "dmarc_missing"]
    assert found[0].record == "v=spf1 include:_spf.google.com ~all"
    assert "google._domainkey" in found[1].note
    # SPF and DKIM are broken: a strict DMARC now would send the owner's own letters to spam.
    assert found[2].record == "v=DMARC1; p=none" and "p=quarantine" in found[2].note
    assert assess("ex.test", PROTON_MX, ["v=spf1 include:_spf.protonmail.ch ~all"], 1,
                  {"_spf.protonmail.ch"}, True, [])[0].record == "v=DMARC1; p=quarantine"


def test_spf_that_lets_anyone_send_or_runs_too_long_or_misses_the_provider():
    found = assess("ex.test", PROTON_MX, ["v=spf1 include:_spf.protonmail.ch +all"], 1, {"_spf.protonmail.ch"},
                   True, ["v=DMARC1; p=reject"])
    assert codes(found) == ["spf_open"] and found[0].record == "v=spf1 include:_spf.protonmail.ch ~all"
    found = assess("ex.test", PROTON_MX, ["v=spf1 include:a.test ~all"], 11, {"a.test", "_spf.protonmail.ch"},
                   True, ["v=DMARC1; p=reject"])
    assert codes(found) == ["spf_too_long"]
    found = assess("ex.test", PROTON_MX, ["v=spf1 include:mail.zendesk.com ~all"], 2, {"mail.zendesk.com"},
                   True, ["v=DMARC1; p=reject"])
    assert codes(found) == ["spf_no_provider"]
    assert found[0].record == "v=spf1 include:_spf.protonmail.ch include:mail.zendesk.com ~all"


def test_dmarc_twice_or_without_a_rule():
    ok_spf = ["v=spf1 include:_spf.protonmail.ch ~all"]
    two = assess("ex.test", PROTON_MX, ok_spf, 1, {"_spf.protonmail.ch"}, True,
                 ["v=DMARC1; p=none", "v=DMARC1; p=reject"])
    assert codes(two) == ["dmarc_multiple"]
    assert codes(assess("ex.test", PROTON_MX, ok_spf, 1, {"_spf.protonmail.ch"}, True, ["v=DMARC1; rua=x"])) \
        == ["dmarc_invalid"]


def test_a_domain_without_mail_has_nothing_to_fix():
    assert assess("ex.test", [], [], 0, set(), None, []) == []
    assert assess("ex.test", [], ["v=spf1 -all"], 0, set(), None, []) == []     # locked: nobody sends
    # No inbox, but letters are sent in its name (a newsletter service): DMARC still matters.
    assert codes(assess("ex.test", [], ["v=spf1 include:sendgrid.net ~all"], 1, {"sendgrid.net"}, None, [])) \
        == ["dmarc_missing"]


def test_merge_keeps_every_sender_once_and_never_tightens_by_accident():
    assert merge_spf(["v=spf1 a -all", "v=spf1 a mx -all"], None) == "v=spf1 a mx -all"
    assert merge_spf(["v=spf1 a -all", "v=spf1 mx ~all"], None) == "v=spf1 a mx ~all"
    assert merge_spf(["v=spf1 +all", "v=spf1 mx"], None) == "v=spf1 mx ~all"
    assert merge_spf(["v=spf1 mx ~all", "v=spf1 a ~all"], PROTON).startswith("v=spf1 include:_spf.protonmail.ch")


def test_carriers_pick_the_bare_domain_and_skip_shared_hosting():
    urls = ["https://app.ex.test", "https://www.ex.test", "https://ex.test", "https://shop.other.test",
            "https://www.other.test", "https://me.pages.dev", "http://10.0.0.1:8080"]
    assert carriers(urls) == {"ex.test": "https://ex.test", "other.test": "https://www.other.test"}


class FakeNet:
    """A zone and check-host answers the test can change between runs."""

    def __init__(self, monkeypatch):
        self.zone: dict[tuple[str, str], list[str]] = {
            ("MX", "ex.test"): list(PROTON_MX),
            ("TXT", "ex.test"): ["v=spf1 include:_spf.protonmail.ch ~all",
                                 "v=spf1 include:icloud.com ~all", "google-site-verification=x"],
            ("TXT", "_dmarc.ex.test"): ["v=DMARC1; p=quarantine"],
            ("TXT", "protonmail._domainkey.ex.test"): ["v=DKIM1; k=rsa; p=MIIB"],
            ("TXT", "_spf.protonmail.ch"): ["v=spf1 ip4:185.70.40.0/24 ~all"],
            ("A", "mail.protonmail.ch"): ["185.70.42.128"], ("A", "mailsec.protonmail.ch"): ["185.70.42.129"],
        }
        self.down: set[str] = set()
        self.asked: list[str] = []
        monkeypatch.setattr(mail_checker, "_mx_sync", lambda d: self.zone.get(("MX", d), []))
        monkeypatch.setattr(mail_checker, "_txt_sync", lambda n: self.zone.get(("TXT", n), []))
        monkeypatch.setattr(mail_checker, "_resolve_sync", lambda h, t: self.zone.get((t, h), []))
        monkeypatch.setattr(mail_checker, "second_opinion_up", self.up)
        monkeypatch.setattr(integrations, "second_opinion_enabled", lambda: True)

    async def up(self, url: str):
        host = url.split("//")[1].split(":")[0]
        self.asked.append(host)
        return host not in self.down


async def test_mail_job_end_to_end(bot, db, monkeypatch):
    """🟠 settings arrive silently with the record to paste; a dead mailbox
    rings only on the second failed check; both say when they are fixed."""
    net = FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await db.activate_or_create_site("https://www.ex.test")
    await set_dns_state("ex.test", "NS", "amy.ns.cloudflare.com, bob.ns.cloudflare.com")

    await scheduler.run_mail_checks()
    [inc] = await get_active_incidents()
    assert (inc["site_id"], inc["check_type"], inc["severity"]) == (sid, "mail_auth", "warning")
    msg = bot.sent[-1]
    assert msg["silent"] is True and msg["text"].startswith("🟠 Почта на ex.test настроена не до конца")
    assert "<code>v=spf1 include:_spf.protonmail.ch include:icloud.com ~all</code>" in msg["text"]
    assert "в DNS у Cloudflare" in msg["text"] and "✉️ Почта" in buttons(msg["reply_markup"])
    n = len(bot.sent)
    await scheduler.run_mail_checks()
    assert len(bot.sent) == n                                     # said once

    net.down = set(PROTON_MX)
    await scheduler.run_mail_checks()                             # first failure: recheck, no alarm
    assert len(bot.sent) == n and "gmail-smtp-in.l.google.com" in net.asked
    assert mail_screen.mail_lines((await sitestatus.get(sid))["mail"])[0].startswith("⏳ Почта: Proton не ответил")
    await scheduler.run_mail_checks()
    msg = bot.sent[-1]
    assert msg["text"].startswith("🔴 Почта на ex.test не принимает письма") and msg["silent"] is False
    assert "↗️ Статус Proton" in buttons(msg["reply_markup"])
    assert {i["check_type"] for i in await get_active_incidents()} == {"mail", "mail_auth"}

    net.down = set()
    net.zone[("TXT", "ex.test")] = ["v=spf1 include:_spf.protonmail.ch ~all"]
    await scheduler.run_mail_checks()
    said = [m["text"] for m in bot.sent[-2:]]
    assert any(t.startswith("✅ Почта на ex.test снова принимает письма") for t in said)
    assert "✅ Почта на ex.test: настройки в порядке, письмам ничего не мешает." in said
    assert await get_active_incidents() == []


async def test_no_verdict_is_not_a_dead_mailbox(bot, db, monkeypatch):
    """check-host itself failing (the control server is «down» too) must
    not count towards the alarm."""
    net = FakeNet(monkeypatch)
    net.zone[("TXT", "ex.test")] = ["v=spf1 include:_spf.protonmail.ch ~all"]
    sid = await db.activate_or_create_site("https://ex.test")
    net.down = {*PROTON_MX, "gmail-smtp-in.l.google.com"}
    for _ in range(3):
        await scheduler.run_mail_checks()
    assert await get_active_incidents() == [] and bot.sent == []
    assert (await sitestatus.get(sid))["mail"]["accepts"] is None


async def test_mx_to_nowhere_is_a_dead_mailbox(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    net.zone[("MX", "ex.test")] = ["mx.gone.test"]
    await db.activate_or_create_site("https://ex.test")
    await scheduler.run_mail_checks()
    await scheduler.run_mail_checks()
    assert any("ведут на сервер, которого нет" in m["text"] for m in bot.sent)


async def test_a_check_from_the_screen_closes_but_never_opens(bot, db, monkeypatch):
    net = FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    await mail_checker.check_mail("ex.test", "https://ex.test", manage=False)
    assert await get_active_incidents() == []                     # the alert belongs to the hourly check
    await scheduler.run_mail_checks()
    assert len(await get_active_incidents()) == 1
    net.zone[("TXT", "ex.test")] = ["v=spf1 include:_spf.protonmail.ch ~all"]
    call = FakeCall(f"mail_check:{sid}")
    await mail_screen.cb_mail_check(call)
    assert await get_active_incidents() == []
    assert "✅ SPF — одна запись, без ошибок" in call.message.texts[-1]


async def test_card_and_mail_screen(bot, db, monkeypatch):
    FakeNet(monkeypatch)
    sid = await db.activate_or_create_site("https://ex.test")
    other = await db.activate_or_create_site("https://www.ex.test")
    await scheduler.run_mail_checks()

    lines = await card_lines(await db.get_site(sid))
    assert "✅ Почта: Proton, принимает письма" in "\n".join(lines)
    assert any(line.startswith("🟠 Настройки почты: 1 помеха") for line in lines)
    assert "    1. 2 записи SPF вместо одной" in lines
    assert not any("Почта" in line for line in await card_lines(await db.get_site(other)))

    call = FakeCall(f"mail:{sid}")
    await mail_screen.cb_mail(call)
    text = call.message.texts[-1]
    assert text.startswith("✉️ Почта на ex.test")
    assert "Письма принимает: Proton (mail.protonmail.ch, mailsec.protonmail.ch)" in text
    assert "🟠 SPF — 2 записи SPF вместо одной" in text and "✅ DKIM — письма подписываются" in text
    assert "✅ DMARC — подделки твоего адреса уходят в спам" in text
    assert "<code>v=spf1 include:_spf.protonmail.ch include:icloud.com ~all</code>" in text
    assert {"🔄 Перепроверить почту", "↗️ Статус Proton"} <= set(buttons(call.message.reply_markup))

    [inc] = await get_active_incidents()
    assert humanize.explain(inc["check_type"], inc["message"]).key == "mail_auth"
    assert "✉️ Почта" in buttons(await incidents_screen.explain_keyboard(inc))


def test_all_fine_is_one_line_and_no_mail_is_said_plainly():
    fine = {"mx": PROTON_MX, "provider": "Proton", "accepts": True, "spf": 1, "dkim": True,
            "dmarc": "reject", "findings": []}
    assert mail_screen.mail_lines(fine) == ["✅ Почта: Proton, принимает письма · SPF, DKIM, DMARC в порядке"]
    assert mail_screen.mail_lines({"mx": [], "locked": True, "findings": []}) == \
        ["✅ Почты на этом домене нет, слать письма от его имени запрещено"]
    assert mail_screen.mail_lines(None) == ["⏳ Почта: ещё не проверял"]


async def test_mx_gone_rings(db, monkeypatch):
    """Letters stop arriving when every MX disappears — that is said loudly,
    a mere change of MX quietly."""
    answers = {"MX": ["mail.protonmail.ch"]}
    monkeypatch.setattr(dns_checker, "_resolve_sync", lambda host, rtype: answers.get(rtype, []))
    assert await dns_checker._check_record("ex.test", "MX") is None              # the baseline
    answers["MX"] = []
    change = await dns_checker._check_record("ex.test", "MX")
    assert change["critical"] is True and change["new"] == "—"
    assert "больше не доходят" in format_dns_change(change)
    moved = {**change, "new": "mx.other.test", "critical": False}
    assert "другой сервер" in format_dns_change(moved)
