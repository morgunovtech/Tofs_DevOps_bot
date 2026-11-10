"""Small promises of the code kept: a follow-up does not edit an alert of a
removed site, the card shows what card_lines returns, a weekly tip can be
switched off and is not offered where it cannot help."""

from conftest import FakeCall

from handlers import sites
from reports import scheduler
from services import notifier, recommend, screenshots, settings, sitestatus


async def _no_phrase(url):
    return None                                       # ex.test is not a real page


async def _no_picture(url):
    return None, "tests: no screenshot service"


async def test_a_followup_for_a_removed_site_edits_nothing(bot, db, monkeypatch):
    edits = []

    async def fake_edit(message_id, text, reply_markup=None):
        edits.append(text)
        return True
    monkeypatch.setattr(notifier, "edit", fake_edit)
    sid = await db.activate_or_create_site("https://gone.test")
    await db.deactivate_site(sid)
    await scheduler._followup("https://gone.test", sid, 4242, "🔴 alert", None, 2)
    await scheduler._followup_slow("https://gone.test", sid, 4243, "🟠 slow", None, 3000)
    await scheduler._group_followup(["https://gone.test"], 4244, "🔴 group", None, 2)
    assert edits == []


async def test_the_card_shows_exactly_what_card_lines_returns(bot, db, monkeypatch):
    monkeypatch.setattr(screenshots, "fetch_screenshot", _no_picture)
    sid = await db.activate_or_create_site("https://ex.test")
    await sitestatus.update(sid, "avail", status="ok", ms=90, error=None)
    await sitestatus.update(sid, "ssl", days_left=5, issuer="LE", not_after=None, error=None)
    call = FakeCall(f"check_site:{sid}")
    await sites.cb_check_single_site(call)
    body = "\n".join(await sites.card_lines(await db.get_site(sid)))
    assert body and body in call.message.texts[-1]


async def test_a_declined_tip_never_comes_back(bot, db, monkeypatch):
    monkeypatch.setattr(recommend, "suggest_keyword", _no_phrase)
    await db.activate_or_create_site("https://ex.test")
    text, kb = await recommend.weekly_recommendation()
    no = [b.callback_data for row in kb.inline_keyboard for b in row if b.text == "Не предлагать"]
    assert len(no) == 1 and no[0].startswith("rec_no:")
    call = FakeCall(no[0])
    await sites.cb_tip_decline(call)
    assert call.answers == ["Ок, больше не предложу"] and call.message.reply_markup is None
    assert call.message.texts == []                   # the tip's text stays, only its buttons go
    seen = {(await recommend.weekly_recommendation())[0] for _ in range(12)}
    assert text not in seen and seen                   # the others still rotate


async def test_tips_follow_what_the_bot_knows(bot, db, cfg, monkeypatch):
    monkeypatch.setattr(recommend, "suggest_keyword", _no_phrase)
    sid = await db.activate_or_create_site("https://ex.test")
    await db.set_state(f"hosting:{sid}", "vercel")     # not Cloudflare: its deploy hook is no help
    cfg(public_base_url="", railway_public_domain="")  # no public address: a status page nobody can open
    assert not settings.status_page_enabled()
    keys = {key for key, _, _ in await recommend._candidates()}
    assert "cloudflare" not in keys and "status_page" not in keys
    await db.set_state(f"hosting:{sid}", "cloudflare")
    assert "cloudflare" in {key for key, _, _ in await recommend._candidates()}
