"""One suggestion a week, never more: the first thing that would make the
bot more useful for this particular setup, with a button that does it."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_all_sites, get_state, set_state
from services import gsc, integrations, settings, yandex_webmaster
from utils.urls import is_http_url


async def _candidates() -> list[tuple[str, str, str, str]]:
    """(key, text, button label, callback) in priority order."""
    sites = await get_all_sites()
    http_sites = [s for s in sites if is_http_url(s["url"])]
    out = []
    undecided = [s for s in http_sites if not (s.get("keyword") or "").strip()
                 and not await get_state(f"kw_declined:{s['id']}")]
    if undecided and not any((s.get("keyword") or "").strip() for s in http_sites):
        out.append(("keyword",
                    "Хочешь, буду проверять не только «открывается ли», но и «показывает ли то, что надо»? "
                    "Задай фразу с главной страницы — поймаю пустую страницу вместо сайта.",
                    "🔍 Задать фразу", "menu_sites"))
    if not settings.status_page_enabled():
        out.append(("status_page",
                    "Могу вести публичную страницу «всё ли работает» с аптаймом — удобно давать ссылку "
                    "клиентам или в README.",
                    "🌐 Включить статус-страницу", "setup:sp_on"))
    if http_sites and not gsc.available():
        out.append(("gsc",
                    "Подключи Google Search Console — узнаю в тот же день, если сайт выпадет из поиска, "
                    "и покажу клики за неделю.",
                    "🔎 Подключить Google", "setup:gsc"))
    if not settings.heartbeat_jobs():
        out.append(("heartbeat",
                    "Есть бэкап по расписанию? Добавь его в контроль задач — скажу, если он молча "
                    "перестанет запускаться.",
                    "⏰ Добавить задачу", "hb_add"))
    if http_sites and not yandex_webmaster.available():
        out.append(("yandex",
                    "Подключи Яндекс.Вебмастер — увижу проблемы сайта глазами Яндекса.",
                    "🔎 Подключить Яндекс", "setup:yx"))
    if http_sites and not integrations.deploy_hooks():
        out.append(("cloudflare",
                    "Если сайт на Cloudflare Pages, дай мне deploy hook — при падении пересоберу сайт сам, "
                    "не дожидаясь тебя.",
                    "☁️ Подключить Cloudflare", "setup:cf"))
    return out


async def weekly_recommendation() -> tuple[str, InlineKeyboardMarkup] | None:
    """Rotates through applicable suggestions so the same one is not
    repeated two weeks in a row."""
    candidates = await _candidates()
    if not candidates:
        return None
    last = await get_state("recommend:last")
    pick = next((c for c in candidates if c[0] != last), candidates[0])
    await set_state("recommend:last", pick[0])
    key, text, label, callback = pick
    return (f"💡 Одна идея на неделю: {text}",
            InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=callback)]]))
