"""⚙️ Per-site dials: interval, fail threshold, accepted codes, keyword,
slow threshold, HTTP method/headers/body."""

import json

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_active_page_urls, update_site_settings
from handlers.common import ack, back_button, cancel_kb, cb_args, forget_secret, render, site_by_cb
from monitors.availability import CONSECUTIVE_FAILURE_THRESHOLD, HTTP_METHODS, phrase_state
from reports.scheduler import PAGE_CHECKS, close_inapplicable, reset_site_schedule
from services import public_urls, settings
from utils.i18n import t
from utils.parse import parse_accepted_codes, parse_header_lines
from utils.text import esc
from utils.urls import is_api_check, is_http_url, is_web_page, site_label

router = Router(name="site_settings")

_INTERVAL_PRESETS = (1, 3, 5, 15, 30, 60)
_FAIL_PRESETS = (1, 2, 3, 5)
_SLOW_PRESETS = (1000, 2000, 3000, 5000, 10000)
# What a «ssv:» button may set: the presets on the screen, or 0 (= default).
_ALLOWED = {"i": {0, *_INTERVAL_PRESETS}, "f": {0, *_FAIL_PRESETS}, "s": {0, *_SLOW_PRESETS}, "k": {0}}


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
    lines = [t("⚙️ Настройки: {label}").format(label=esc(site_label(site['url']))) + "\n",
             t("⏱ Интервал проверки: {v} мин").format(v=interval or settings.check_interval_minutes())
             + ("" if interval else t(" (по умолчанию)")),
             t("🔁 Неудачных проверок подряд до тревоги: {v}").format(v=thr or CONSECUTIVE_FAILURE_THRESHOLD)
             + ("" if thr else t(" (по умолчанию)")),
             t("🐢 Порог «медленно»: {v} мс").format(v=slow or settings.slow_response_ms())
             + ("" if slow else t(" (по умолчанию)"))]
    if http:
        codes = site.get("accepted_codes")
        lines.append(t("🔢 Приемлемые коды: ") + (esc(codes) if codes else t("по умолчанию (любой &lt; 400)")))
        kw = (site.get("keyword") or "").strip()
        if kw and _is_head(site):
            # An import or an old database can hold both: say it where the dials are.
            lines.append(t("⚠️ Фраза «{kw}» не проверяется: метод HEAD не скачивает страницу").format(kw=esc(kw)))
        elif kw:
            mode = (t("не должна появляться (стоп-фраза)") if site.get("keyword_mode") == "absent"
                    else t("должна быть {where}").format(where=_where(site)))
            lines.append(t("🔍 Фраза: «{kw}» — {mode}").format(kw=esc(kw), mode=mode))
        else:
            lines.append(t("🔍 Ключевая фраза: не задана"))
        method = site.get("http_method") or "GET"
        # Only a JSON object counts — that is what request_options() sends;
        # anything else is named in words instead of a wrong number.
        try:
            parsed = json.loads(site.get("http_headers") or "{}")
        except ValueError:
            parsed = None
        n_headers = len(parsed) if isinstance(parsed, dict) else 0
        req = t("📡 Запрос: {method}").format(method=esc(method))
        if n_headers:
            req += t(", заголовков: {n_headers}").format(n_headers=n_headers)
        elif site.get("http_headers"):
            req += t(", заголовки не распознаны — задай заново")
        if site.get("http_body"):
            req += t(", тело {n} симв.").format(n=len(site['http_body']))
        lines.append(req)
        if is_api_check(site["url"], site.get("http_method")):
            lines.append(t("🔌 Это API-проверка: только ответ — без поиска, ссылок и SEO."))
    if settings.status_page_enabled():
        lines.append(t("🏷 Бейдж аптайма:\n<code>{badge_url}</code>").format(badge_url=esc(public_urls.badge_url(sid))))
    # «:x» — opened from here: the picker and its «← Назад» come back here.
    dials = [(t("⏱ Интервал"), "i"), (t("🔁 Сбоев до тревоги"), "f"), (t("🐢 Медленно"), "s")]
    if http:
        dials += [(t("🔢 Коды ответа"), "c"), (t("🔍 Фраза"), "k"), (t("📡 Метод"), "m"), (t("📡 Заголовки"), "h"), (t("📡 Тело"), "b")]
    buttons = [InlineKeyboardButton(text=text, callback_data=f"ssp:{k}:{sid}:x") for text, k in dials]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text=t("← Простые настройки"), callback_data=f"sset:{sid}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


_SIMPLE_INTERVALS = {1: "чаще (раз в минуту)", 5: "обычно (раз в 5 минут)", 15: "реже (раз в 15 минут)"}


def _where(site: dict) -> str:
    return t("в ответе") if is_api_check(site["url"], site.get("http_method")) else t("на странице")


def _is_head(site: dict) -> bool:
    """HEAD never reads a body: the monitor skips the phrase on such a site."""
    return (site.get("http_method") or "GET").upper() == "HEAD"


async def sset_screen(site: dict) -> tuple[str, InlineKeyboardMarkup]:
    """Two things a person actually decides: how often to check and
    whether to watch a phrase on the page. Everything else is advanced."""
    sid, http = site["id"], is_http_url(site["url"])
    api = is_api_check(site["url"], site.get("http_method"))
    interval = site.get("check_interval_min") or settings.check_interval_minutes()
    how_often = t(_SIMPLE_INTERVALS.get(interval, "раз в {interval} мин")).format(interval=interval)
    lines = [f"⚙️ {esc(site_label(site['url']))}\n",
             t("⏱ Проверяю {how_often}.").format(how_often=how_often)]
    if http:
        kw = (site.get("keyword") or "").strip()
        if kw and _is_head(site):
            lines.append(t("⚠️ Фраза «{kw}» не проверяется: метод HEAD не скачивает страницу. Переключи метод на GET в «🛠 Для продвинутых».").format(kw=esc(kw)))
        elif kw:
            mode = t("не должно появляться") if site.get("keyword_mode") == "absent" else t("должно быть {where}").format(where=_where(site))
            lines.append(t("🔍 Слежу за фразой «{kw}» — {mode}.").format(kw=esc(kw), mode=mode))
        elif api:
            lines.append(t("🔍 За фразой в ответе не слежу. Задай слово, без которого ответ неправильный (например, имя поля из JSON), — замечу «HTTP 200, а внутри ошибка»."))
        else:
            lines.append(t("🔍 За фразой на странице не слежу. Это ловит случай «сайт открывается, но показывает пустую страницу или ошибку»."))
    if api:
        lines.append(t("🔌 Это API-проверка: слежу только за ответом, поиск, ссылки и SEO к ней не относятся."))
    rows = [[InlineKeyboardButton(text=t("⏱ Чаще"), callback_data=f"ssv:i:{sid}:1"),
             InlineKeyboardButton(text=t("⏱ Обычно"), callback_data=f"ssv:i:{sid}:0"),
             InlineKeyboardButton(text=t("⏱ Реже"), callback_data=f"ssv:i:{sid}:15")]]
    if http:
        rows.append([InlineKeyboardButton(text=t("🔍 Следить за фразой"), callback_data=f"ssp:k:{sid}")])
    # «📸 Как выглядит сайт» is gone: the card shows the page itself.
    if is_web_page(site["url"], site.get("http_method")):
        rows.append([InlineKeyboardButton(text=t("👥 Скорость у посетителей"), callback_data=f"spd:{sid}")])
    rows.append([InlineKeyboardButton(text=t("🛠 Для продвинутых"), callback_data=f"ssetx:{sid}")])
    rows.append([InlineKeyboardButton(text=t("🗑 Убрать из мониторинга"), callback_data=f"delsite:{sid}")])
    rows.append([InlineKeyboardButton(text=t("← К сайту"), callback_data=f"check_site:{sid}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(target, site_id: int, advanced: bool = False):
    site = await site_by_cb(str(site_id))
    if not site:   # removed while its form was open
        await render(target, t("Сайт не найден."), back_button())
        return
    text, kb = await (advanced_screen(site) if advanced else sset_screen(site))
    await render(target, text, kb)


# Dials that live on the advanced screen only: their pickers come back there
# even from a button without the «:x» marker (messages from before it).
_ADVANCED_PICKERS = ("f", "s", "c", "m", "h", "b")
_ADVANCED_VALUES = ("f", "s")


def _back_to(sid: int, advanced: bool) -> str:
    return f"ssetx:{sid}" if advanced else f"sset:{sid}"


@router.callback_query(F.data.startswith("ssetx:"))
async def cb_site_settings_advanced(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, t("Сайт не найден."), back_button())
        return
    await _show(call, site["id"], advanced=True)


@router.callback_query(F.data.startswith("sset:"))
async def cb_site_settings(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, t("Сайт не найден."), back_button())
        return
    await _show(call, site["id"])


def _presets(kind: str, sid: int, values, fmt, advanced: bool = False) -> InlineKeyboardMarkup:
    tail = ":x" if advanced else ""
    rows, row = [], []
    for v in values:
        row.append(InlineKeyboardButton(text=fmt(v), callback_data=f"ssv:{kind}:{sid}:{v}{tail}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("↩︎ По умолчанию"), callback_data=f"ssv:{kind}:{sid}:0{tail}")])
    rows.append([InlineKeyboardButton(text=t("← Назад"), callback_data=_back_to(sid, advanced))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("ssp:"))
async def cb_site_setting_picker(call: CallbackQuery, state: FSMContext):
    """ssp:<kind>:<site>[:x] — «:x» when opened from the advanced screen."""
    parts = (call.data or "").split(":")
    marked = parts[-1] == "x"
    args = parts[1:3] if len(parts) == 3 + marked else None
    site = await site_by_cb(args[1]) if args else None
    if not site:
        await ack(call, t("Сайт не найден"), show_alert=True)
        return
    await ack(call)
    kind, sid, label = args[0], site["id"], esc(site_label(site["url"]))
    advanced = marked or kind in _ADVANCED_PICKERS
    if kind == "i":
        await render(call, t("⏱ Как часто проверять {label}?").format(label=label),
                     _presets("i", sid, _INTERVAL_PRESETS, lambda m: t("{m} мин").format(m=m), advanced))
    elif kind == "f":
        await render(call, t("🔁 Сколько неудачных проверок подряд должно случиться, чтобы {label} считался лежащим?\n(1 — тревога с первой неудачи, больше — меньше ложных тревог)").format(label=label),
                     _presets("f", sid, _FAIL_PRESETS, str, advanced))
    elif kind == "s":
        await render(call, t("🐢 С какого времени ответа {label} считать медленным?\n(3 проверки подряд дольше порога → проблема «сайт отвечает медленно»)").format(label=label),
                     _presets("s", sid, _SLOW_PRESETS, lambda ms: t("{ms} мс").format(ms=ms), advanced))
    elif kind == "c":
        await state.set_state(SiteCodesForm.codes)
        await state.update_data(sset_site_id=sid)
        await render(call, t("🔢 Какие HTTP-коды считать нормой для {label}?\n\nПримеры:\n• <code>200-299</code> — только успешные\n• <code>200-399,401</code> — плюс страница за паролем\n• <code>default</code> — вернуть правило по умолчанию (любой &lt; 400)").format(label=label),
                     cancel_kb(f"ssetx:{sid}"))
    elif kind == "k":
        rows = [[InlineKeyboardButton(text=t("✅ Фраза должна быть {where}").format(where=_where(site)),
                                      callback_data=f"sskw:{sid}:present")],
                [InlineKeyboardButton(text=t("🚫 Стоп-фраза (не должна появляться)"),
                                      callback_data=f"sskw:{sid}:absent")]]
        if (site.get("keyword") or "").strip():
            rows.append([InlineKeyboardButton(text=t("🗑 Убрать фразу"), callback_data=f"ssv:k:{sid}:0{':x' if advanced else ''}")])
        rows.append([InlineKeyboardButton(text=t("← Назад"), callback_data=_back_to(sid, advanced))])
        if is_api_check(site["url"], site.get("http_method")):
            text = (t("🔍 Фраза в ответе {label}: API может ответить «200», а внутри — ошибка. Фраза это ловит.\n\n• «должна быть» — например, имя поля из правильного ответа: <code>scoreLabel</code>\n• «стоп-фраза» — например, <code>\"error\"</code>: появилась — значит сломалось\n\nРегистр не важен.").format(label=label))
        else:
            text = (t("🔍 Слежка за фразой на {label}: сайт может «открываться», но показывать пустую страницу или ошибку вместо себя. Фраза это ловит.\n\n• «должна быть» — например, название компании из шапки: пропала — значит вместо сайта что-то другое\n• «стоп-фраза» — например, «Fatal error»: появилась — значит сломалось\n\nРегистр не важен.").format(label=label))
        await render(call, text, InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "m":
        rows = [[InlineKeyboardButton(text=m, callback_data=f"ssm:{sid}:{m}") for m in HTTP_METHODS[:4]],
                [InlineKeyboardButton(text=m, callback_data=f"ssm:{sid}:{m}") for m in HTTP_METHODS[4:]],
                [InlineKeyboardButton(text=t("← Назад"), callback_data=_back_to(sid, advanced))]]
        await render(call, t("📡 Каким методом проверять {label}?\n(HEAD дешевле, но не умеет проверять фразу; POST/PUT отправляют тело, если задано — тело в JSON уходит с Content-Type: application/json)\n\nЛюбой метод, кроме GET и HEAD, делает проверку API-проверкой: слежу только за ответом, поиск, ссылки и SEO для неё не проверяю.").format(label=label),
                     InlineKeyboardMarkup(inline_keyboard=rows))
    elif kind == "h":
        await state.set_state(SiteHeadersForm.headers)
        await state.update_data(sset_site_id=sid)
        await render(call, t("📡 Заголовки запроса для {label}, по одному на строку:\n<code>Authorization: Bearer xxx\nAccept: application/json</code>\n\nПришли <code>-</code>, чтобы убрать все заголовки.").format(label=label), cancel_kb(f"ssetx:{sid}"))
    elif kind == "b":
        await state.set_state(SiteBodyForm.body)
        await state.update_data(sset_site_id=sid)
        await render(call, t("📡 Тело запроса для {label} (отправляется с POST/PUT/PATCH/DELETE), до 4000 символов.\nПришли <code>-</code>, чтобы убрать тело.").format(label=label), cancel_kb(f"ssetx:{sid}"))
    else:
        await ack(call, t("Не понял"), show_alert=True)


@router.callback_query(F.data.startswith("ssv:"))
async def cb_site_setting_value(call: CallbackQuery):
    """ssv:<kind>:<site>:<value>[:x] — apply a picked per-site value; 0
    clears the override; «:x» — back to the advanced screen."""
    parts = (call.data or "").split(":")
    marked = parts[-1] == "x"
    args = parts[1:4] if len(parts) == 4 + marked else None
    # str.isdigit() passes «²»: isascii() keeps int() honest.
    if (not args or args[0] not in _ALLOWED or not (args[2].isascii() and args[2].isdigit())
            or int(args[2]) not in _ALLOWED[args[0]]):
        await ack(call, t("Кнопка устарела — открой настройки заново"), show_alert=True)
        return
    kind, sid, raw = args
    site = await site_by_cb(sid)
    if not site:
        await ack(call, t("Сайт не найден"), show_alert=True)
        return
    value = int(raw) or None
    if kind == "i":
        await update_site_settings(site["id"], check_interval_min=value)
        reset_site_schedule(site["id"])  # apply the new cadence immediately
    elif kind == "f":
        await update_site_settings(site["id"], fail_threshold=value)
    elif kind == "s":
        await update_site_settings(site["id"], slow_ms=value)
    elif kind == "k":
        await update_site_settings(site["id"], keyword=None, keyword_mode=None)
    else:
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call, t("Сохранено ✅"))
    await _show(call, site["id"], advanced=marked or kind in _ADVANCED_VALUES)


@router.callback_query(F.data.startswith("ssm:"))
async def cb_site_method(call: CallbackQuery):
    args = cb_args(call, 2)
    site = await site_by_cb(args[0]) if args else None
    if not site or args[1] not in HTTP_METHODS:
        await ack(call, t("Не понял"), show_alert=True)
        return
    phrase = (site.get("keyword") or "").strip()
    if args[1] == "HEAD" and phrase:
        # The monitor skips the phrase on HEAD: the card would say «на месте» about nothing.
        await ack(call, t("HEAD не скачивает страницу, и фраза «{phrase}» перестанет проверяться. Сначала убери фразу или оставь GET.").format(phrase=phrase[:60]), show_alert=True)
        await _show(call, site["id"], advanced=True)
        return
    await update_site_settings(site["id"], http_method=None if args[1] == "GET" else args[1])
    # A POST endpoint is not a page any more: its old search/links findings
    # close now rather than at the next audit.
    await close_inapplicable(PAGE_CHECKS, await get_active_page_urls())
    await ack(call, t("Сохранено ✅"))
    await _show(call, site["id"], advanced=True)


@router.callback_query(F.data.startswith("sskw:"))
async def cb_site_keyword_mode(call: CallbackQuery, state: FSMContext):
    args = cb_args(call, 2)
    site = await site_by_cb(args[0]) if args else None
    if not site or args[1] not in ("present", "absent"):
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call)
    await state.set_state(SiteKeywordForm.keyword)
    await state.update_data(sset_site_id=site["id"], kw_mode=args[1])
    what = (t("которая должна быть {where}").format(where=_where(site)) if args[1] == "present"
            else t("при появлении которой нужна тревога (стоп-фраза)"))
    await render(call, t("🔍 Пришли фразу, {what}.\nОт 2 до 100 символов, регистр не важен.").format(what=what),
                 cancel_kb(f"sset:{site['id']}"))


async def _form_site(message: Message, state: FSMContext) -> dict | None:
    """The site a form is about — an active one; a site removed while the
    form was open ends the form."""
    data = await state.get_data()
    sid = data.get("sset_site_id")
    site = await site_by_cb(str(sid)) if sid else None
    if not site:
        await state.clear()
        await message.answer(t("Сайт не найден — начни заново."), reply_markup=back_button())
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
        await message.answer(t("Не понял. Формат: <code>200-299</code> или <code>200-399,401</code> (коды 100–599). Или <code>default</code> для сброса."),
                             reply_markup=cancel_kb(f"ssetx:{site['id']}"))
        return
    else:
        await update_site_settings(site["id"], accepted_codes=raw)
    await state.clear()
    await _show(message, site["id"], advanced=True)   # codes live on the advanced screen


@router.message(SiteKeywordForm.keyword)
async def msg_site_keyword(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    kw = (message.text or "").strip()
    if not 2 <= len(kw) <= 100:
        await message.answer(t("Нужна фраза от 2 до 100 символов. Попробуй ещё раз:"),
                             reply_markup=cancel_kb(f"sset:{site['id']}"))
        return
    if _is_head(site):
        await state.clear()
        await message.answer(t("📡 Этот сайт проверяется методом HEAD: страница не скачивается, и фразу проверить нельзя. Переключи метод на GET («🛠 Для продвинутых» → «📡 Метод») и задай фразу снова."),
                             reply_markup=back_button(t("← К настройкам"), f"ssetx:{site['id']}"))
        return
    mode = (await state.get_data()).get("kw_mode", "present")
    status = await message.answer(t("⏳ Проверяю страницу — это до 15 секунд…"))
    found = await phrase_state(site, kw)
    if found is not None and found != (mode == "present"):
        # Saving now would page the owner at the first scheduled checks. The
        # form stays open: the next phrase is checked again.
        await state.update_data(kw_pending=kw)
        await status.edit_text(_mismatch_text(site, kw, mode), reply_markup=_save_anyway_kb(site["id"]))
        return
    await state.clear()
    await update_site_settings(site["id"], keyword=kw, keyword_mode=mode)
    await status.edit_text(t("✅ Проверил: сейчас всё так, как ты описал.") if found is not None
                           else t("⚠️ Сейчас проверить не удалось — сохранил, проверю при следующей проверке."))
    await _show(message, site["id"])


def _mismatch_text(site: dict, kw: str, mode: str) -> str:
    if mode == "absent":
        return (t("🔍 Стоп-фраза «{kw}» уже есть {where} — сохрани я её, тревога пришла бы на первых же проверках. Так бывает, если фраза встречается в обычном тексте или в коде страницы. Пришли другую фразу — или сохрани эту, если сайт сейчас действительно сломан.").format(kw=esc(kw), where=_where(site)))
    return (t("🔍 Сейчас {where} нет фразы «{kw}» — сохрани я её, тревога пришла бы на первых же проверках. Так бывает, если текст рисует JavaScript, в фразе опечатка или она с другой страницы. Пришли другую фразу — или сохрани эту, если сайт сейчас действительно сломан.").format(where=_where(site), kw=esc(kw)))


def _save_anyway_kb(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("💾 Сохранить всё равно"), callback_data=f"sskwf:{sid}")],
        *cancel_kb(f"sset:{sid}").inline_keyboard])


@router.callback_query(F.data.startswith("sskwf:"))
async def cb_site_keyword_force(call: CallbackQuery, state: FSMContext):
    """«💾 Сохранить всё равно»: the phrase that did not match the page now."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    data = await state.get_data()
    kw = data.get("kw_pending")
    if not site or not kw or data.get("sset_site_id") != site["id"]:
        await ack(call, t("Устарело — задай фразу заново"), show_alert=True)
        return
    await state.clear()
    await update_site_settings(site["id"], keyword=kw, keyword_mode=data.get("kw_mode", "present"))
    await ack(call, t("Сохранено ✅"))
    await _show(call, site["id"])


@router.message(SiteHeadersForm.headers)
async def msg_site_headers(message: Message, state: FSMContext):
    site = await _form_site(message, state)
    if not site:
        return
    await forget_secret(message)       # «Authorization: Bearer …», a password in the body
    raw = (message.text or "").strip()
    if raw == "-":
        headers = None
    else:
        parsed = parse_header_lines(raw)
        if parsed is None or len(raw) > 2000:
            await message.answer(t("Не понял. Каждая строка: <code>Имя: значение</code>."),
                                 reply_markup=cancel_kb(f"ssetx:{site['id']}"))
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
    await forget_secret(message)       # «Authorization: Bearer …», a password in the body
    raw = message.text or ""
    if len(raw) > 4000:
        await message.answer(t("Слишком длинно (лимит 4000 символов)."), reply_markup=cancel_kb(f"ssetx:{site['id']}"))
        return
    await state.clear()
    await update_site_settings(site["id"], http_body=None if raw.strip() == "-" else raw)
    await _show(message, site["id"], advanced=True)
