"""⚙️ Per-site dials: interval, fail threshold, accepted codes, keyword,
slow threshold, HTTP method/headers/body."""

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import get_site, update_site_settings
from handlers.common import ack, back_button, cancel_kb, cb_args, render, site_by_cb
from monitors.availability import CONSECUTIVE_FAILURE_THRESHOLD, HTTP_METHODS
from reports.scheduler import reset_site_schedule
from services import public_urls, settings
from utils.parse import parse_accepted_codes, parse_header_lines
from utils.text import esc
from utils.urls import is_http_url, site_label

router = Router(name="site_settings")

_INTERVAL_PRESETS = (1, 3, 5, 15, 30, 60)
_FAIL_PRESETS = (1, 2, 3, 5)
_SLOW_PRESETS = (1000, 2000, 3000, 5000, 10000)


class SiteCodesForm(StatesGroup):
    codes = State()


class SiteKeywordForm(StatesGroup):
    keyword = State()


class SiteHeadersForm(StatesGroup):
    headers = State()


class SiteBodyForm(StatesGroup):
    body = State()


async def advanced_screen(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Every dial. Reached from the simple screen via «🛠 Для продвинутых»."""
    sid, http = site["id"], is_http_url(site["url"])
    interval, thr, slow = site.get("check_interval_min"), site.get("fail_threshold"), site.get("slow_ms")
    lines = [f"⚙️ Настройки: {esc(site_label(site['url']))}\n",
             f"⏱ Интервал проверки: {interval or config.check_interval_minutes} мин"
             + ("" if interval else " (по умолчанию)"),
             f"🔁 Фейлов подряд до алерта: {thr or CONSECUTIVE_FAILURE_THRESHOLD}"
             + ("" if thr else " (по умолчанию)"),
             f"🐢 Порог «медленно»: {slow or config.slow_response_ms} мс"
             + ("" if slow else " (по умолчанию)")]
    if http:
        codes = site.get("accepted_codes")
        lines.append("🔢 Приемлемые коды: " + (esc(codes) if codes else "по умолчанию (любой &lt; 400)"))
        kw = (site.get("keyword") or "").strip()
        if kw:
            mode = ("не должна появляться (стоп-фраза)" if site.get("keyword_mode") == "absent"
                    else "должна быть на странице")
            lines.append(f"🔍 Фраза: «{esc(kw)}» — {mode}")
        else:
            lines.append("🔍 Ключевая фраза: не задана")
        method = site.get("http_method") or "GET"
        n_headers = 0
        try:
            n_headers = len(json.loads(site.get("http_headers") or "{}"))
        except ValueError:
            pass
        req = f"📡 Запрос: {esc(method)}"
        if n_headers:
            req += f", заголовков: {n_headers}"
        if site.get("http_body"):
            req += f", тело {len(site['http_body'])} симв."
        lines.append(req)
    if settings.status_page_enabled():
        lines.append(f"🏷 Бейдж аптайма:\n<code>{esc(public_urls.badge_url(sid))}</code>")
    rows = [[InlineKeyboardButton(text="⏱ Интервал", callback_data=f"ssp:i:{sid}"),
             InlineKeyboardButton(text="🔁 Порог фейлов", callback_data=f"ssp:f:{sid}"),
             InlineKeyboardButton(text="🐢 Медленно", callback_data=f"ssp:s:{sid}")]]
    if http:
        rows.append([InlineKeyboardButton(text="🔢 Коды ответа", callback_data=f"ssp:c:{sid}"),
                     InlineKeyboardButton(text="🔍 Фраза", callback_data=f"ssp:k:{sid}")])
        rows.append([InlineKeyboardButton(text="📡 Метод", callback_data=f"ssp:m:{sid}"),
                     InlineKeyboardButton(text="📡 Заголовки", callback_data=f"ssp:h:{sid}"),
                     InlineKeyboardButton(text="📡 Тело", callback_data=f"ssp:b:{sid}")])
    rows.append([InlineKeyboardButton(text="← Простые настройки", callback_data=f"sset:{sid}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


_SIMPLE_INTERVALS = {1: "чаще (раз в минуту)", 5: "обычно (раз в 5 минут)", 15: "реже (раз в 15 минут)"}


async def sset_screen(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Two things a person actually decides: how often to check and
    whether to watch a phrase on the page. Everything else is advanced."""
    sid, http = site["id"], is_http_url(site["url"])
    interval = site.get("check_interval_min") or config.check_interval_minutes
    how_often = _SIMPLE_INTERVALS.get(interval, f"раз в {interval} мин")
    lines = [f"⚙️ {esc(site_label(site['url']))}\n",
             f"⏱ Проверяю {how_often}."]
    if http:
        kw = (site.get("keyword") or "").strip()
        if kw:
            mode = ("не должно появляться" if site.get("keyword_mode") == "absent" else "должно быть на странице")
            lines.append(f"🔍 Слежу за фразой «{esc(kw)}» — {mode}.")
        else:
            lines.append("🔍 За фразой на странице не слежу. Это ловит случай «сайт открывается, "
                         "но показывает пустую страницу или ошибку».")
    rows = [[InlineKeyboardButton(text="⏱ Чаще", callback_data=f"ssv:i:{sid}:1"),
             InlineKeyboardButton(text="⏱ Обычно", callback_data=f"ssv:i:{sid}:0"),
             InlineKeyboardButton(text="⏱ Реже", callback_data=f"ssv:i:{sid}:15")]]
    if http:
        rows.append([InlineKeyboardButton(text="🔍 Следить за фразой", callback_data=f"ssp:k:{sid}")])
    rows.append([InlineKeyboardButton(text="🛠 Для продвинутых", callback_data=f"ssetx:{sid}")])
    rows.append([InlineKeyboardButton(text="← К сайту", callback_data=f"check_site:{sid}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(target, site_id: int, advanced: bool = False):
    site = await get_site(site_id)
    text, kb = await (advanced_screen(site) if advanced else sset_screen(site))
    await render(target, text, kb)


@router.callback_query(F.data.startswith("ssetx:"))
async def cb_site_settings_advanced(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    await _show(call, site["id"], advanced=True)


@router.callback_query(F.data.startswith("sset:"))
async def cb_site_settings(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    await _show(call, site["id"])


def _presets(kind: str, sid: int, values, fmt) -> InlineKeyboardMarkup:
    rows, row = [], []
    for v in values:
        row.append(InlineKeyboardButton(text=fmt(v), callback_data=f"ssv:{kind}:{sid}:{v}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="↩︎ По умолчанию", callback_data=f"ssv:{kind}:{sid}:0")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"sset:{sid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("ssp:"))
async def cb_site_setting_picker(call: CallbackQuery, state: FSMContext):
    args = cb_args(call, 2)
    site = await site_by_cb(args[1]) if args else None
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    await ack(call)
    kind, sid, label = args[0], site["id"], esc(site_label(site["url"]))
    if kind == "i":
        await render(call, f"⏱ Как часто проверять {label}?",
                     _presets("i", sid, _INTERVAL_PRESETS, lambda m: f"{m} мин"))
    elif kind == "f":
        await render(call, f"🔁 Сколько фейлов подряд должно случиться, чтобы {label} считался "
                           f"лежащим?\n(1 — алерт с первого фейла, больше — меньше ложных тревог)",
                     _presets("f", sid, _FAIL_PRESETS, str))
    elif kind == "s":
        await render(call, f"🐢 С какого времени ответа {label} считать медленным?\n"
                           f"(3 проверки подряд дольше порога → инцидент «performance»)",
                     _presets("s", sid, _SLOW_PRESETS, lambda ms: f"{ms} мс"))
    elif kind == "c":
        await state.set_state(SiteCodesForm.codes)
        await state.update_data(sset_site_id=sid)
        await render(call, f"🔢 Какие HTTP-коды считать нормой для {label}?\n\nПримеры:\n"
                           "• <code>200-299</code> — только успешные\n"
                           "• <code>200-399,401</code> — плюс страница за паролем\n"
                           "• <code>default</code> — вернуть правило по умолчанию (любой &lt; 400)",
                     cancel_kb())
    elif kind == "k":
        rows = [[InlineKeyboardButton(text="✅ Фраза должна быть на странице",
                                      callback_data=f"sskw:{sid}:present")],
                [InlineKeyboardButton(text="🚫 Стоп-фраза (не должна появляться)",
                                      callback_data=f"sskw:{sid}:absent")]]
        if (site.get("keyword") or "").strip():
            rows.append([InlineKeyboardButton(text="🗑 Убрать фразу", callback_data=f"ssv:k:{sid}:0")])
        rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"sset:{sid}")])
        await render(call, f"🔍 Слежка за фразой на {label}: сайт может «открываться», но показывать "
                           f"пустую страницу или ошибку вместо себя. Фраза это ловит.\n\n"
                           f"• «должна быть» — например, название компании из шапки: пропала — значит "
                           f"вместо сайта что-то другое\n"
                           f"• «стоп-фраза» — например, «Fatal error»: появилась — значит сломалось\n\n"
                           f"Регистр не важен.",
                     InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "m":
        rows = [[InlineKeyboardButton(text=m, callback_data=f"ssm:{sid}:{m}") for m in HTTP_METHODS[:4]],
                [InlineKeyboardButton(text=m, callback_data=f"ssm:{sid}:{m}") for m in HTTP_METHODS[4:]],
                [InlineKeyboardButton(text="← Назад", callback_data=f"sset:{sid}")]]
        await render(call, f"📡 Каким методом проверять {label}?\n(HEAD дешевле, но не умеет "
                           f"проверять фразу; POST/PUT отправляют тело, если задано)",
                     InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "h":
        await state.set_state(SiteHeadersForm.headers)
        await state.update_data(sset_site_id=sid)
        await render(call, f"📡 Заголовки запроса для {label}, по одному на строку:\n"
                           "<code>Authorization: Bearer xxx\nAccept: application/json</code>\n\n"
                           "Пришли <code>-</code>, чтобы убрать все заголовки.", cancel_kb())
    elif kind == "b":
        await state.set_state(SiteBodyForm.body)
        await state.update_data(sset_site_id=sid)
        await render(call, f"📡 Тело запроса для {label} (отправляется с POST/PUT/PATCH/DELETE), "
                           "до 4000 символов.\nПришли <code>-</code>, чтобы убрать тело.", cancel_kb())
    else:
        await ack(call, "Не понял", show_alert=True)


@router.callback_query(F.data.startswith("ssv:"))
async def cb_site_setting_value(call: CallbackQuery):
    """Apply a picked per-site value. Value 0 clears the override."""
    args = cb_args(call, 3)
    if not args or not args[2].isdigit():
        await ack(call, "Не понял", show_alert=True)
        return
    kind, sid, raw = args
    site = await site_by_cb(sid)
    if not site:
        await ack(call, "Сайт не найден", show_alert=True)
        return
    value = int(raw) or None
    if kind == "i":
        await update_site_settings(site["id"], check_interval_min=value)
        reset_site_schedule(site["id"])  # apply the new cadence immediately
    elif kind == "f":
        await update_site_settings(site["id"], fail_threshold=value)
    elif kind == "s":
        await update_site_settings(site["id"], slow_ms=value)
    elif kind == "c":
        await update_site_settings(site["id"], accepted_codes=None)
    elif kind == "k":
        await update_site_settings(site["id"], keyword=None, keyword_mode=None)
    else:
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call, "Сохранено ✅")
    await _show(call, site["id"], advanced=kind in ("f", "s", "c") or (kind == "i" and value not in (None, 1, 15)))


@router.callback_query(F.data.startswith("ssm:"))
async def cb_site_method(call: CallbackQuery):
    args = cb_args(call, 2)
    site = await site_by_cb(args[0]) if args else None
    if not site or args[1] not in HTTP_METHODS:
        await ack(call, "Не понял", show_alert=True)
        return
    await update_site_settings(site["id"], http_method=None if args[1] == "GET" else args[1])
    await ack(call, "Сохранено ✅")
    await _show(call, site["id"], advanced=True)


@router.callback_query(F.data.startswith("sskw:"))
async def cb_site_keyword_mode(call: CallbackQuery, state: FSMContext):
    args = cb_args(call, 2)
    site = await site_by_cb(args[0]) if args else None
    if not site or args[1] not in ("present", "absent"):
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call)
    await state.set_state(SiteKeywordForm.keyword)
    await state.update_data(sset_site_id=site["id"], kw_mode=args[1])
    what = ("которая должна быть на странице" if args[1] == "present"
            else "при появлении которой нужен алерт (стоп-фраза)")
    await render(call, f"🔍 Пришли фразу, {what}.\nОт 2 до 100 символов, регистр не важен.", cancel_kb())


async def _form_site(message: Message, state: FSMContext) -> dict | None:
    data = await state.get_data()
    sid = data.get("sset_site_id")
    site = await get_site(sid) if sid else None
    if not site:
        await state.clear()
        await message.answer("Сайт не найден — начни заново.", reply_markup=back_button())
    return site


@router.message(SiteCodesForm.codes)
async def msg_site_codes(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    raw = (message.text or "").strip().lower().replace(" ", "")
    if raw in ("default", "поумолчанию", "сброс", "-"):
        await update_site_settings(site["id"], accepted_codes=None)
        await state.clear()
        await _show(message, site["id"], advanced=True)
        return
    elif len(raw) > 60 or parse_accepted_codes(raw) is None:
        await message.answer("Не понял. Формат: <code>200-299</code> или <code>200-399,401</code> "
                             "(коды 100–599). Или <code>default</code> для сброса.",
                             reply_markup=cancel_kb())
        return
    else:
        await update_site_settings(site["id"], accepted_codes=raw)
    await state.clear()
    await _show(message, site["id"])


@router.message(SiteKeywordForm.keyword)
async def msg_site_keyword(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    kw = (message.text or "").strip()
    if not 2 <= len(kw) <= 100:
        await message.answer("Нужна фраза от 2 до 100 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    mode = (await state.get_data()).get("kw_mode", "present")
    await state.clear()
    await update_site_settings(site["id"], keyword=kw, keyword_mode=mode)
    await _show(message, site["id"])


@router.message(SiteHeadersForm.headers)
async def msg_site_headers(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    raw = (message.text or "").strip()
    if raw == "-":
        headers = None
    else:
        parsed = parse_header_lines(raw)
        if parsed is None or len(raw) > 2000:
            await message.answer("Не понял. Каждая строка: <code>Имя: значение</code>.",
                                 reply_markup=cancel_kb())
            return
        headers = json.dumps(parsed, ensure_ascii=False) if parsed else None
    await state.clear()
    await update_site_settings(site["id"], http_headers=headers)
    await _show(message, site["id"], advanced=True)


@router.message(SiteBodyForm.body)
async def msg_site_body(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    raw = message.text or ""
    if len(raw) > 4000:
        await message.answer("Слишком длинно (лимит 4000 символов).", reply_markup=cancel_kb())
        return
    await state.clear()
    await update_site_settings(site["id"], http_body=None if raw.strip() == "-" else raw)
    await _show(message, site["id"], advanced=True)
