"""👥 Real visitors' speed from Google's Chrome data: the line on the site
card and the screen behind it."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render, site_by_cb
from reports.scheduler import audit_time
from services import crux, humanize, sitestatus
from utils.text import esc
from utils.urls import is_web_page, site_label

router = Router(name="speed")

_ICON = {"good": "✅", "improve": "👥", "poor": "🟠"}


async def speed_lines(site: dict) -> list[str]:
    """The card: what Chrome visitors actually wait for — once Google answered."""
    if not crux.available() or not is_web_page(site["url"], site.get("http_method")):
        return []
    line = crux.card_line((await sitestatus.get(site["id"])).get("crux"))
    return [line] if line else []


def _metric_lines(values: dict) -> list[str]:
    lines = []
    for key in ("lcp", "inp", "cls"):
        if values.get(key) is not None:
            text = crux.words(key, values[key])
            lines.append(f"{_ICON[crux.level(key, values[key])]} {esc(text[:1].upper() + text[1:])}")
    if values.get("ttfb") is not None:
        lines.append(f"    сервер начинает отвечать за {humanize.fmt_seconds(values['ttfb'])}")
    return lines


async def speed_page(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"👥 Скорость у посетителей — {esc(site_label(site['url']))}",
             f"<i>Посетители Chrome за {crux.WINDOW_DAYS} дней, данные Google</i>", ""]
    chrome = (await sitestatus.get(site["id"])).get("crux")
    if not crux.available():
        lines.append("Не подключено: 🔌 Подключения → ⚡ Скорость у посетителей Chrome. Бесплатно, нужен ключ "
                     "Google; на сайт ничего ставить не нужно.")
    elif chrome is None:
        lines.append(f"Спрошу у Google сегодня в {audit_time('visitor_checks')}.")
    elif not crux.has_data(chrome):
        lines.append("У Google мало данных по сайту — так у большинства небольших сайтов: Google считает только "
                     "сайты, у которых достаточно посетителей в Chrome. Как сайт открывается сейчас, покажет "
                     "PageSpeed Insights.")
    else:
        lines += _metric_lines(chrome)
        lines += ["", "<i>Числа — как у 3 из 4 посетителей, у остальных дольше. Google считает быстрым: страница "
                      "появляется за 2.5 с или быстрее, кнопки откликаются за 0.2 с, вёрстка почти не прыгает.</i>"]
    rows = [[InlineKeyboardButton(text="↗️ PageSpeed Insights", url=crux.pagespeed_url(site["url"]))],
            [InlineKeyboardButton(text="🌍 К сайту", callback_data=f"check_site:{site['id']}"),
             InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("spd:"))
async def cb_speed(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site or not is_web_page(site["url"], site.get("http_method")):
        await ack(call, "Скорость у посетителей знаю только для страниц сайта", show_alert=True)
        return
    await ack(call)
    await render(call, *await speed_page(site))
