"""🔧 Recurring maintenance windows."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_all_sites, get_site
from handlers.common import STALE_TAP, ack, back_button, cancel_kb, cb_args, cb_int, render, site_by_cb
from services import maintenance
from utils.i18n import t
from utils.parse import parse_time_window
from utils.text import esc
from utils.urls import site_label

router = Router(name="maintenance")

_DAYS = {"a": [], "w": [0, 1, 2, 3, 4], "e": [5, 6]}
_TIME_PRESETS = ((120, 240), (180, 300), (1380, 360))  # 02–04, 03–05, 23–06


class MaintTimeForm(StatesGroup):
    time = State()


async def _screen() -> tuple[str, InlineKeyboardMarkup]:
    windows = maintenance.windows()
    lines = [t("🗓 Плановые работы — в это время я не пишу о падениях и не напоминаю, проверки и статистика продолжаются.\n")]
    rows = [[InlineKeyboardButton(text=t("➕ Добавить окно"), callback_data="mw_add")]]
    if not windows:
        lines.append(t("Пока ни одного окна."))
    for w in windows:
        if w.get("site_id") is None:
            scope = t("все сайты")
        else:
            site = await get_site(w["site_id"])
            scope = site_label(site["url"]) if site else t("сайт #{site_id}").format(site_id=w['site_id'])
        desc = f"{scope} · {maintenance.fmt_window(w)}"
        active = t(" · 🟢 активно сейчас") if maintenance.window_active_now(w) else ""
        lines.append(f"• {esc(desc)}{active}")
        rows.append([InlineKeyboardButton(text=f"🗑 {desc}"[:60], callback_data=f"mw_del:{w['id']}")])
    rows.append([InlineKeyboardButton(text=t("← Тишина"), callback_data="menu_quiet")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def _show(target, prefix: str = ""):
    text, kb = await _screen()
    await render(target, prefix + text, kb)


@router.callback_query(F.data == "menu_maint")
async def cb_maint(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await _show(call)


@router.callback_query(F.data == "mw_add")
async def cb_mw_add(call: CallbackQuery):
    await ack(call)
    if len(maintenance.windows()) >= maintenance.MAX_MAINT_WINDOWS:
        await render(call, t("Лимит {MAX_MAINT_WINDOWS} окон — удали ненужные.").format(MAX_MAINT_WINDOWS=maintenance.MAX_MAINT_WINDOWS), back_button(t("← Плановые работы"), "menu_maint"))
        return
    rows = [[InlineKeyboardButton(text=t("🌐 Все сайты"), callback_data="mw_scope:0")]]
    rows += [[InlineKeyboardButton(text=site_label(s["url"]), callback_data=f"mw_scope:{s['id']}")]
             for s in await get_all_sites()]
    rows.append([InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_maint")])
    await render(call, t("🔧 Для чего эти плановые работы?"), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("mw_scope:"))
async def cb_mw_scope(call: CallbackQuery):
    scope = call.data.split(":", 1)[1]
    if cb_int(scope) is None:
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("Каждый день"), callback_data=f"mw_days:{scope}:a")],
        [InlineKeyboardButton(text=t("Будни"), callback_data=f"mw_days:{scope}:w"),
         InlineKeyboardButton(text=t("Выходные"), callback_data=f"mw_days:{scope}:e")],
        [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_maint")]])
    await render(call, t("🔧 В какие дни?"), kb)


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


@router.callback_query(F.data.startswith("mw_days:"))
async def cb_mw_days(call: CallbackQuery):
    args = cb_args(call, 2)
    if not args or cb_int(args[0]) is None or args[1] not in _DAYS:
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call)
    scope, days = args
    rows = [[InlineKeyboardButton(text=f"{_fmt(s)}–{_fmt(e)}",
                                  callback_data=f"mw_time:{scope}:{days}:{s}-{e}")
             for s, e in _TIME_PRESETS],
            [InlineKeyboardButton(text=t("✏️ Своё время"), callback_data=f"mw_custom:{scope}:{days}")],
            [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_maint")]]
    await render(call, t("🔧 В какое время (по вашей таймзоне)?"), InlineKeyboardMarkup(inline_keyboard=rows))


async def _create(target, scope: str, days_key: str, start_min: int, end_min: int):
    site_id = int(scope) or None
    if site_id and not await site_by_cb(scope):     # an active site only, like every other button
        await render(target, t("Сайт не найден."), back_button(t("← Плановые работы"), "menu_maint"))
        return
    if await maintenance.add_window(site_id, _DAYS[days_key], start_min, end_min) is None:
        await render(target, t("Не добавил: время окна неверное или уже есть {MAX_MAINT_WINDOWS} окон — удали ненужные.").format(MAX_MAINT_WINDOWS=maintenance.MAX_MAINT_WINDOWS), back_button(t("← Плановые работы"), "menu_maint"))
        return
    await _show(target, t("✅ Окно добавлено.\n\n"))


@router.callback_query(F.data.startswith("mw_time:"))
async def cb_mw_time(call: CallbackQuery):
    args = cb_args(call, 3)
    ok = args and cb_int(args[0]) is not None and args[1] in _DAYS and "-" in args[2]
    if ok:
        a, _, b = args[2].partition("-")
        ok = cb_int(a) is not None and cb_int(b) is not None
    if not ok:
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call)
    await _create(call, args[0], args[1], int(a), int(b))


@router.callback_query(F.data.startswith("mw_custom:"))
async def cb_mw_custom(call: CallbackQuery, state: FSMContext):
    args = cb_args(call, 2)
    if not args or cb_int(args[0]) is None or args[1] not in _DAYS:
        await ack(call, t("Не понял"), show_alert=True)
        return
    await ack(call)
    await state.set_state(MaintTimeForm.time)
    await state.update_data(mw_scope=args[0], mw_days=args[1])
    await render(call, t("🔧 Пришли время окна в формате <code>ЧЧ:ММ-ЧЧ:ММ</code>, например <code>01:30-03:00</code>.\nОкно через полночь тоже можно: <code>23:00-06:00</code>."), cancel_kb())


@router.message(MaintTimeForm.time)
async def msg_mw_time(message: Message, state: FSMContext):
    window = parse_time_window(message.text or "")
    if not window:
        await message.answer(t("Не понял. Формат: <code>ЧЧ:ММ-ЧЧ:ММ</code>, и время начала должно отличаться от конца."), reply_markup=cancel_kb())
        return
    data = await state.get_data()
    scope, days = data.get("mw_scope"), data.get("mw_days")
    await state.clear()
    if scope is None or days not in _DAYS:
        await message.answer(t("Начни заново: ⚙️ Настройки → 🔔 Когда писать → 🗓 Плановые работы."),
                             reply_markup=back_button(t("← Плановые работы"), "menu_maint"))
        return
    await _create(message, scope, days, *window)


@router.callback_query(F.data.startswith("mw_del:"))
async def cb_mw_del(call: CallbackQuery):
    """A question first, like every other 🗑: a window is one tap to add, but
    a stray tap must not take tonight's silence away unnoticed."""
    await ack(call)
    arg = call.data.split(":", 1)[1]
    window = next((w for w in maintenance.windows() if cb_int(arg) is not None and w.get("id") == int(arg)), None)
    if window is None:
        await _show(call, t("Уже удалено.\n\n"))
        return
    await render(call, t("Удалить окно {window}?").format(window=esc(maintenance.fmt_window(window))),
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text=t("🗑 Да, удалить"), callback_data=f"mw_del_ok:{arg}")],
                     [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_maint")]]))


@router.callback_query(F.data.startswith("mw_del_ok:"))
async def cb_mw_del_ok(call: CallbackQuery):
    arg = call.data.split(":", 1)[1]
    if not await ack(call, t("Удаляю…")):
        await call.message.answer(t(STALE_TAP))
        return
    if cb_int(arg) is None or not await maintenance.remove_window(int(arg)):
        await _show(call, t("Уже удалено.\n\n"))
        return
    await _show(call, t("✅ Окно удалено.\n\n"))
