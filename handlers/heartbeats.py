"""💓 Dead-man switch jobs managed from the chat."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import get_heartbeats
from handlers.common import ack, back_button, cancel_kb, render
from services import public_urls, settings
from utils.text import esc, fmt_duration, parse_sqlite_utc

router = Router(name="heartbeats")

_INTERVALS = [("1 час", 60), ("6 часов", 360), ("сутки", 1440), ("неделя", 10080)]
_MAX_UI_JOBS = 12
_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


class AddHeartbeatForm(StatesGroup):
    name = State()


async def _text() -> str:
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return ("💓 Dead-man switch: джобы (бэкапы, кроны) пингуют бота, а он замечает, "
                "когда они замолкают.\n\nПока ни одной джобы.")
    beats = await get_heartbeats()
    now = datetime.now(UTC)
    lines = ["💓 Heartbeats:\n"]
    for job, interval in jobs.items():
        last = parse_sqlite_utc(beats.get(job))
        if last:
            ago = (now - last).total_seconds() / 60
            icon, state_txt = ("✅" if ago <= interval * 1.25 else "💔"), f"{fmt_duration(ago)} назад"
        else:
            icon, state_txt = "❓", "ещё не пинговал"
        src = "" if job in settings.ui_heartbeat_jobs() else " (из .env)"
        lines.append(f"{icon} {esc(job)} — {state_txt}, ожидание каждые {fmt_duration(interval)}{src}")
    first = next(iter(jobs))
    lines.append(f"\nПинг из крона (пример для «{esc(first)}»):\n"
                 f"<code>curl -fsS {esc(public_urls.heartbeat_url(first))}</code>")
    if not config.public_base_url:
        lines.append("\n⚠️ Задай PUBLIC_BASE_URL в .env, чтобы ссылка выше была готова к вставке.")
    return "\n".join(lines)


def _kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Добавить джобу", callback_data="hb_add")]]
    rows += [[InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"hb_del:{name}")]
             for name in list(settings.ui_heartbeat_jobs())[:_MAX_UI_JOBS]]
    rows.append([InlineKeyboardButton(text="← Подключения", callback_data="menu_connections")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_hb")
async def cb_hb(call: CallbackQuery):
    await ack(call)
    await render(call, await _text(), _kb())


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddHeartbeatForm.name)
    await render(call, "💓 Название джобы латиницей (например, backup или certs-sync):", cancel_kb())


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip().lower()
    # ASCII only: a 20-char CJK name is 60 UTF-8 bytes and overflows the
    # 64-byte callback_data limit, killing the whole keyboard.
    if not name or len(name) > 20 or not set(name) <= _ALLOWED:
        await message.answer("Только латиница/цифры/дефис, до 20 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= _MAX_UI_JOBS:
        await state.clear()
        await message.answer(f"Лимит {_MAX_UI_JOBS} джоб из интерфейса — удали ненужные в 💓 Heartbeats.",
                             reply_markup=back_button())
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[2:]],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])
    await message.answer(f"Как часто «{esc(name)}» должна подавать сигнал?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    name = (await state.get_data()).get("hb_name")
    await state.clear()
    arg = call.data.split(":", 1)[1]
    if not name or not arg.isdigit():
        await ack(call, "Начни заново: 💓 Heartbeats → ➕", show_alert=True)
        return
    await settings.add_heartbeat_job(name, int(arg))
    await ack(call, "Добавлено ✅")
    await render(call, f"💓 «{esc(name)}» добавлена. Вставь в конец крон-строки:\n\n"
                       f"<code>&& curl -fsS {esc(public_urls.heartbeat_url(name))}</code>\n\n"
                       f"Если сигналов не будет дольше ожидания — сообщу.", back_button())


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    removed = await settings.remove_heartbeat_job(call.data.split(":", 1)[1])
    await ack(call, "Удалено" if removed else "Не нашёл (из .env — убери там)")
    await render(call, await _text(), _kb())
