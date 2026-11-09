"""👥 Real visitors' speed: the line on the site card and the screen with
both sources — the owner's own measurement (with the one line to put on
the site) and Google's Chrome data."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render, site_by_cb
from services import crux, humanize, public_urls, rum, sitestatus
from utils.text import esc, plural
from utils.urls import is_web_page, site_label

router = Router(name="speed")

_ICON = {"good": "✅", "improve": "👥", "poor": "🟠"}


async def speed_lines(site: dict) -> list[str]:
    """The card: what visitors actually wait for, or how to start measuring."""
    if not is_web_page(site["url"], site.get("http_method")):
        return []
    chrome = (await sitestatus.get(site["id"])).get("crux")
    source, values = await rum.best_source(site["id"], chrome)
    return [rum.card_line(source, values, crux.available())]


def _metric_lines(values: dict) -> list[str]:
    lines = []
    for key in ("lcp", "inp", "cls"):
        if values.get(key) is not None:
            text = rum.words(key, values[key])
            lines.append(f"{_ICON[rum.level(key, values[key])]} {esc(text[:1].upper() + text[1:])}")
    if values.get("ttfb") is not None:
        lines.append(f"    сервер начинает отвечать за {humanize.fmt_seconds(values['ttfb'])}")
    return lines


async def speed_page(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    sid = site["id"]
    lines = [f"👥 Скорость у посетителей — {esc(site_label(site['url']))}", ""]
    own = await rum.summary(sid)
    n = own["visits"]
    lines.append(f"<b>Твой замер</b> (все браузеры, {rum.WINDOW_DAYS} дней)")
    if n >= rum.MIN_VISITS:
        phones = round(100 * own["mobile"] / n)
        lines.append(f"{n} {plural(n, 'просмотр', 'просмотра', 'просмотров')}, {phones}% с телефонов")
        lines += _metric_lines(own)
    elif n:
        lines.append(f"Пока {n} {plural(n, 'просмотр', 'просмотра', 'просмотров')} — чтобы судить, нужно хотя бы "
                     f"{rum.MIN_VISITS}. Скрипт работает.")
    else:
        lines.append("Замеров пока нет.")

    lines += ["", f"<b>Chrome</b> (данные Google, {rum.WINDOW_DAYS} дней)"]
    chrome = (await sitestatus.get(sid)).get("crux")
    if not crux.available():
        lines.append("Не подключено: 🔌 Подключения → ⚡ Google Chrome UX Report. Бесплатно, нужен ключ Google.")
    elif chrome is None:
        lines.append("Спрошу у Google сегодня в 08:10.")
    elif chrome.get("none"):
        lines.append("У Google мало данных по сайту — так у большинства небольших сайтов. Свой замер видит всех.")
    else:
        lines += _metric_lines(chrome)

    lines += ["", "<i>Числа — как у 3 из 4 посетителей, у остальных дольше. Google считает быстрым: страница "
                  "появляется за 2.5 с или быстрее, кнопки откликаются за 0.2 с, вёрстка почти не прыгает.</i>"]
    if public_urls.is_public():
        lines += ["", "<b>Как поставить свой замер</b> — одна строка в &lt;head&gt; каждой страницы сайта "
                      "(в общий шаблон или в index.html):",
                  f"<code>{esc(rum.snippet(sid))}</code>",
                  "Скрипт не ставит cookie, не запоминает посетителя и не передаёт адрес страницы — только три "
                  "числа скорости и «телефон или нет». Первые числа появятся здесь после первых визитов."]
        if ((await sitestatus.get(sid)).get("security") or {}).get("csp"):
            bot = public_urls.base_url().rstrip("/")
            lines.append(f"⚠️ У сайта есть CSP — без правки браузер заблокирует скрипт. Допиши адрес бота "
                         f"<code>{esc(bot)}</code> в script-src и connect-src.")
    else:
        lines += ["", "Чтобы мерить у посетителей, боту нужен публичный адрес: 🔌 Подключения → публичный адрес."]
    rows = [[InlineKeyboardButton(text="↗️ PageSpeed Insights", url=rum.pagespeed_url(site["url"]))],
            [InlineKeyboardButton(text="🌍 К сайту", callback_data=f"check_site:{sid}"),
             InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")]]
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("spd:"))
async def cb_speed(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site or not is_web_page(site["url"], site.get("http_method")):
        await ack(call, "Скорость у посетителей мерю только для страниц сайта", show_alert=True)
        return
    await ack(call)
    await render(call, *await speed_page(site))
