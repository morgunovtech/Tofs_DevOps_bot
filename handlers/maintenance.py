"""🔧 Recurring maintenance windows."""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_all_sites, get_site
from handlers.common import ack, back_button, cancel_kb, cb_args, render
from services import maintenance
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
    lines = ["🔧 Тех. окна — в это время алерты и эскалация молчат, "
             "проверки и статистика продолжаются.\n"]
    rows = [[InlineKeyboardButton(text="➕ Добавить окно", callback_data="mw_add")]]
    if not windows:
        lines.append("Пока ни одного окна.")
    for w in windows:
        if w.get("site_id") is None:
            scope = "все сайты"
        else:
            site = await get_site(w["site_id"])
            scope = site_label(site["url"]) if site else f"сайт #{w['site_id']}"
        desc = f"{scope} · {maintenance.fmt_window(w)}"
        active = " · 🟢 активно сейчас" if maintenance.window_active_now(w) else ""
        lines.append(f"• {esc(desc)}{active}")
        rows.append([InlineKeyboardButton(text=f"🗑 {desc}"[:60], callback_data=f"mw_del:{w['id']}")])
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
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
        await render(call, f"Лимит {maintenance.MAX_MAINT_WINDOWS} окон — удали ненужные.", back_button())
        return
    rows = [[InlineKeyboardButton(text="🌐 Все сайты", callback_data="mw_scope:0")]]
    rows += [[InlineKeyboardButton(text=site_label(s["url"]), callback_data=f"mw_scope:{s['id']}")]
             for s in await get_all_sites()]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")])
    await render(call, "🔧 Для чего это тех. окно?", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("mw_scope:"))
async def cb_mw_scope(call: CallbackQuery):
    scope = call.data.split(":", 1)[1]
    if not scope.isdigit():
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Каждый день", callback_data=f"mw_days:{scope}:a")],
        [InlineKeyboardButton(text="Будни", callback_data=f"mw_days:{scope}:w"),
         InlineKeyboardButton(text="Выходные", callback_data=f"mw_days:{scope}:e")],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")]])
    await render(call, "🔧 В какие дни?", kb)


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


@router.callback_query(F.data.startswith("mw_days:"))
async def cb_mw_days(call: CallbackQuery):
    args = cb_args(call, 2)
    if not args or not args[0].isdigit() or args[1] not in _DAYS:
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call)
    scope, days = args
    rows = [[InlineKeyboardButton(text=f"{_fmt(s)}–{_fmt(e)}",
                                  callback_data=f"mw_time:{scope}:{days}:{s}-{e}")
             for s, e in _TIME_PRESETS],
            [InlineKeyboardButton(text="✏️ Своё время", callback_data=f"mw_custom:{scope}:{days}")],
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="menu_maint")]]
    await render(call, "🔧 В какое время (по вашей таймзоне)?", InlineKeyboardMarkup(inline_keyboard=rows))


async def _create(target, scope: str, days_key: str, start_min: int, end_min: int):
    site_id = int(scope) or None
    if site_id and not await get_site(site_id):
        await render(target, "Сайт не найден.", back_button())
        return
    await maintenance.add_window(site_id, _DAYS[days_key], start_min, end_min)
    await _show(target, "✅ Окно добавлено.\n\n")


@router.callback_query(F.data.startswith("mw_time:"))
async def cb_mw_time(call: CallbackQuery):
    args = cb_args(call, 3)
    ok = args and args[0].isdigit() and args[1] in _DAYS and "-" in args[2]
    if ok:
        a, _, b = args[2].partition("-")
        ok = a.isdigit() and b.isdigit()
    if not ok:
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call)
    await _create(call, args[0], args[1], int(a), int(b))


@router.callback_query(F.data.startswith("mw_custom:"))
async def cb_mw_custom(call: CallbackQuery, state: FSMContext):
    args = cb_args(call, 2)
    if not args or not args[0].isdigit() or args[1] not in _DAYS:
        await ack(call, "Не понял", show_alert=True)
        return
    await ack(call)
    await state.set_state(MaintTimeForm.time)
    await state.update_data(mw_scope=args[0], mw_days=args[1])
    await render(call, "🔧 Пришли время окна в формате <code>ЧЧ:ММ-ЧЧ:ММ</code>, например "
                       "<code>01:30-03:00</code>.\nОкно через полночь тоже можно: "
                       "<code>23:00-06:00</code>.", cancel_kb())


@router.message(MaintTimeForm.time)
async def msg_mw_time(message: Message, state: FSMContext):
    window = parse_time_window(message.text or "")
    if not window:
        await message.answer("Не понял. Формат: <code>ЧЧ:ММ-ЧЧ:ММ</code>, и время начала должно "
                             "отличаться от конца.", reply_markup=cancel_kb())
        return
    data = await state.get_data()
    scope, days = data.get("mw_scope"), data.get("mw_days")
    await state.clear()
    if scope is None or days not in _DAYS:
        await message.answer("Начни заново: Настройки → 🔧 Тех. окна.", reply_markup=back_button())
        return
    await _create(message, scope, days, *window)


@router.callback_query(F.data.startswith("mw_del:"))
async def cb_mw_del(call: CallbackQuery):
    arg = call.data.split(":", 1)[1]
    removed = arg.isdigit() and await maintenance.remove_window(int(arg))
    await ack(call, "Удалено" if removed else "Уже удалено")
    await _show(call)
