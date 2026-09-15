"""⏰ Task control: «tell me if the backup did not run». Under the hood a
dead-man switch: jobs ping the bot, silence longer than the interval is an
alert. The words «cron» and «curl» appear only on the very last step,
next to the ready-made line."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_heartbeats
from handlers.common import ack, back_button, cancel_kb, render
from services import public_urls, settings
from utils.text import esc, fmt_duration, parse_sqlite_utc

router = Router(name="heartbeats")

_INTERVALS = [("раз в час", 60), ("раз в 6 часов", 360), ("раз в сутки", 1440), ("раз в неделю", 10080)]
_MAX_UI_JOBS = 12
_ALLOWED = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


class AddHeartbeatForm(StatesGroup):
    name = State()


async def _text() -> str:
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return ("⏰ Контроль задач\n\n"
                "У тебя есть что-то по расписанию — бэкап, выгрузка, синхронизация? Самая коварная "
                "поломка такой задачи — тишина: она молча перестаёт запускаться, и никто не замечает.\n\n"
                "Добавь задачу сюда, и она будет отмечаться у меня после каждого запуска. Если отметка "
                "не придёт вовремя — я напишу.\n\nПока ни одной задачи.")
    beats = await get_heartbeats()
    now = datetime.now(UTC)
    lines = ["⏰ Контроль задач:\n"]
    for job, interval in jobs.items():
        last = parse_sqlite_utc(beats.get(job))
        if last:
            ago = (now - last).total_seconds() / 60
            icon = "✅" if ago <= interval * 1.25 else "⚠️"
            state_txt = f"отмечалась {fmt_duration(ago)} назад"
        else:
            icon, state_txt = "❓", "ещё ни разу не отмечалась"
        src = "" if job in settings.ui_heartbeat_jobs() else " (из .env)"
        lines.append(f"{icon} {esc(job)} — {state_txt}, ожидаю каждые {fmt_duration(interval)}{src}")
    lines.append("\nНажми на задачу, чтобы увидеть строку для её расписания.")
    return "\n".join(lines)


def _kb() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="➕ Добавить задачу", callback_data="hb_add")]]
    rows += [[InlineKeyboardButton(text=f"🔗 {name}", callback_data=f"hb_show:{name}"),
              InlineKeyboardButton(text="🗑", callback_data=f"hb_del:{name}")]
             for name in list(settings.ui_heartbeat_jobs())[:_MAX_UI_JOBS]]
    rows.append([InlineKeyboardButton(text="← Подключения", callback_data="menu_connections")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _how_to(name: str) -> str:
    return (f"Чтобы задача «{esc(name)}» отмечалась, допиши в конец её команды в расписании "
            f"(crontab, планировщик хостинга, GitHub Actions) одну строку:\n\n"
            f"<code>&& curl -fsS {esc(public_urls.heartbeat_url(name))}</code>\n\n"
            f"Смысл: когда задача отработала без ошибок, она открывает этот адрес, и я это вижу. "
            f"Если отметка не придёт вовремя — напишу.")


@router.callback_query(F.data == "menu_hb")
async def cb_hb(call: CallbackQuery):
    await ack(call)
    await render(call, await _text(), _kb())


@router.callback_query(F.data.startswith("hb_show:"))
async def cb_hb_show(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if name not in settings.heartbeat_jobs():
        await ack(call, "Задачи больше нет", show_alert=True)
        return
    await ack(call)
    await render(call, _how_to(name), back_button("← Контроль задач", "menu_hb"))


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddHeartbeatForm.name)
    await render(call, "Как назвать задачу? Латиницей, коротко: например <code>backup</code> "
                       "или <code>sync-photos</code>.", cancel_kb())


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip().lower()
    # ASCII only: a 20-char CJK name is 60 UTF-8 bytes and overflows the
    # 64-byte callback_data limit, killing the whole keyboard.
    if not name or len(name) > 20 or not set(name) <= _ALLOWED:
        await message.answer("Только латиница, цифры и дефис, до 20 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= _MAX_UI_JOBS:
        await state.clear()
        await message.answer(f"Больше {_MAX_UI_JOBS} задач добавить нельзя — удали ненужные.",
                             reply_markup=back_button())
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[2:]],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])
    await message.answer(f"Как часто «{esc(name)}» должна запускаться?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    name = (await state.get_data()).get("hb_name")
    await state.clear()
    arg = call.data.split(":", 1)[1]
    if not name or not arg.isdigit():
        await ack(call, "Начни заново: ⏰ Контроль задач → ➕", show_alert=True)
        return
    await settings.add_heartbeat_job(name, int(arg))
    await ack(call, "Добавил ✅")
    await render(call, f"✅ Задача «{esc(name)}» добавлена.\n\n{_how_to(name)}",
                 back_button("← Контроль задач", "menu_hb"))


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    removed = await settings.remove_heartbeat_job(call.data.split(":", 1)[1])
    await ack(call, "Удалил" if removed else "Эта задача из .env — убери её там")
    await render(call, await _text(), _kb())
