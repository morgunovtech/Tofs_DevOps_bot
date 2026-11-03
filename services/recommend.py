"""One suggestion a week, never more: the first thing that would make the
bot more useful for this particular setup, said concretely, with a button
that does it — the person should not have to invent anything."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_all_sites, get_state, set_state
from monitors.pagemeta import suggest_keyword
from services import gsc, integrations, settings, yandex_webmaster
from utils.text import esc
from utils.urls import is_web_page, site_label

Buttons = list[tuple[str, str]]   # (label, callback_data)


async def _keyword_idea(http_sites: list[dict]) -> tuple[str, str, Buttons] | None:
    """A phrase the bot read on the homepage itself, offered as «следить?».
    Without a phrase the idea is too abstract to act on — then none."""
    if any((s.get("keyword") or "").strip() for s in http_sites):
        return None
    for s in http_sites:
        if await get_state(f"kw_declined:{s['id']}"):
            continue
        phrase = await suggest_keyword(s["url"])
        if not phrase:
            continue
        await set_state(f"kw_suggest:{s['id']}", phrase)   # what «✅ Да, следить» will set
        label = esc(site_label(s["url"]))
        return ("keyword",
                f"Сейчас я проверяю только, что {label} отвечает. Если вместо сайта откроется пустая "
                f"страница или заглушка хостинга, я этого не замечу. Могу следить, чтобы на главной была "
                f"фраза «{esc(phrase)}», — пропадёт, напишу. Следить?",
                [("✅ Да, следить", f"kwok:{s['id']}"), ("Не надо", f"kwno:{s['id']}")])
    return None


async def _candidates() -> list[tuple[str, str, Buttons]]:
    """(key, text, buttons) in priority order."""
    sites = await get_all_sites()
    http_sites = [s for s in sites if is_web_page(s["url"], s.get("http_method"))]
    out = []
    idea = await _keyword_idea(http_sites)
    if idea:
        out.append(idea)
    if not settings.status_page_enabled():
        out.append(("status_page",
                    "Могу вести публичную страницу «всё ли работает» с аптаймом — удобно давать ссылку "
                    "клиентам или в README.",
                    [("🌐 Включить статус-страницу", "setup:sp_on")]))
    if http_sites and not gsc.available():
        out.append(("gsc",
                    "Подключи Google Search Console — узнаю в тот же день, если сайт выпадет из поиска, "
                    "и покажу клики за неделю.",
                    [("🔎 Подключить Google", "setup:gsc")]))
    if not settings.heartbeat_jobs():
        out.append(("heartbeat",
                    "Есть бэкап по расписанию или другой бот? Добавь его на главный экран — скажу, "
                    "если он молча перестанет запускаться.",
                    [("⏰ Добавить задачу или бота", "hb_add")]))
    if http_sites and not yandex_webmaster.available():
        out.append(("yandex",
                    "Подключи Яндекс.Вебмастер — увижу проблемы сайта глазами Яндекса.",
                    [("🔎 Подключить Яндекс", "setup:yx")]))
    if http_sites and not integrations.deploy_hooks():
        out.append(("cloudflare",
                    "Если сайт на Cloudflare Pages, дай мне deploy hook — при падении пересоберу сайт сам, "
                    "не дожидаясь тебя.",
                    [("☁️ Подключить Cloudflare", "setup:cf")]))
    return out


async def weekly_recommendation() -> tuple[str, InlineKeyboardMarkup] | None:
    """Rotates through applicable suggestions so the same one is not
    repeated two weeks in a row."""
    candidates = await _candidates()
    if not candidates:
        return None
    keys = [c[0] for c in candidates]
    last = await get_state("recommend:last")
    pick = candidates[(keys.index(last) + 1) % len(keys)] if last in keys else candidates[0]
    await set_state("recommend:last", pick[0])
    _, text, buttons = pick
    return (f"💡 {text}", InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=cb) for label, cb in buttons]]))
