"""⏰ Tasks and bots: «tell me if the backup — or another bot — went
silent». Under the hood a dead-man switch: jobs ping the bot, silence
longer than the interval is an alert. They sit on the main screen next to
the sites, each with a card; the words «cron» and «curl» appear only on
that card, next to the ready-made line."""

import contextlib
from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from db.database import get_heartbeats, get_state
from handlers.common import ack, back_button, cancel_kb, render
from services import humanize, public_urls, settings
from utils.text import esc, fmt_duration, parse_sqlite_utc

router = Router(name="heartbeats")

_INTERVALS = [("раз в час", 60), ("раз в 6 часов", 360), ("раз в сутки", 1440), ("раз в неделю", 10080)]


class AddHeartbeatForm(StatesGroup):
    name = State()


def every(interval: int) -> str:
    """«раз в сутки» for a preset interval, «каждые 3 ч» for any other."""
    return next((label for label, mins in _INTERVALS if mins == interval), f"каждые {fmt_duration(interval)}")


async def job_states() -> list[tuple[str, str, str]]:
    """(name, icon, short state) for every task under watch, from the stored
    marks — no network. Late is the watch's own verdict (25% past the
    interval, 🟠); never marked is ⏳ until the watch has said so, then ⚠️
    like its alert."""
    jobs = settings.heartbeat_jobs()
    if not jobs:
        return []
    beats = await get_heartbeats()
    now = datetime.now(UTC)
    out = []
    for job, interval in jobs.items():
        last = parse_sqlite_utc(beats.get(job))
        if last:
            ago = (now - last).total_seconds() / 60
            if ago > interval * 1.25:
                out.append((job, "🟠", f"молчит {fmt_duration(ago)}"))
            else:
                out.append((job, "✅", "отметка только что" if ago < 1 else f"отметка {fmt_duration(ago)} назад"))
        elif await get_state(f"hb_alerted:{job}"):
            out.append((job, humanize.UNCHECKED, "ни одной отметки — не подключили?"))
        else:
            out.append((job, "⏳", "ждёт первой отметки"))
    return out


def _how_to(name: str) -> str:
    return (f"Как подключить «{esc(name)}» — одна строка:\n\n"
            f"<code>&& curl -fsS {esc(public_urls.heartbeat_url(name))}</code>\n\n"
            f"• Задача по расписанию (crontab, планировщик хостинга, GitHub Actions): допиши строку "
            f"в конец её команды.\n"
            f"• Бот или сервис: дай ему адрес из строки как ссылку для сторожа (healthcheck) — пусть "
            f"открывает её после каждого удачного запуска.\n\n"
            f"Отметка не придёт вовремя — напишу.")


async def job_screen(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """The card: when it last marked, the line that connects it, and the
    way out when there is no such task — a delete button for a UI job, the
    env variable for an .env one."""
    interval = settings.heartbeat_jobs()[name]
    _, icon, state = next(s for s in await job_states() if s[0] == name)
    lines = [f"⏰ {esc(name)}", f"{icon} {esc(state[:1].upper() + state[1:])} · жду {every(interval)}", "",
             _how_to(name)]
    rows = []
    marked = icon in ("✅", "🟠")
    if name in settings.ui_heartbeat_jobs():
        if not marked:
            lines += ["", "Если такой задачи у тебя нет — удали её, и я перестану её ждать."]
        rows.append([InlineKeyboardButton(text="🗑 Удалить", callback_data=f"hb_del:{name}")])
    else:
        lines += ["", "Задана в переменной <code>HEARTBEAT_JOBS</code> на сервере бота — убрать её можно "
                      "только там."]
    rows.append([InlineKeyboardButton(text="← Главное меню", callback_data="menu_main")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("hb_show:") | F.data.startswith("hb_how:"))
async def cb_hb_show(call: CallbackQuery):
    """hb_show: from the main screen, edits it in place. hb_how: from the
    morning digest or an alert — answers below and leaves that message as is."""
    kind, name = call.data.split(":", 1)
    if name not in settings.heartbeat_jobs():
        await ack(call, "Задачи больше нет", show_alert=True)
        return
    await ack(call)
    text, kb = await job_screen(name)
    if kind == "hb_how" and call.message is not None:
        await call.message.answer(text, reply_markup=kb)
    else:
        await render(call, text, kb)


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddHeartbeatForm.name)
    await render(call, "⏰ Бот или задача по расписанию\n\n"
                       "Самая коварная поломка бэкапа, выгрузки или другого бота — тишина: он молча "
                       "перестаёт работать, и никто не замечает. Добавь его сюда — он будет отмечаться "
                       "у меня после каждого запуска, а если отметка не придёт вовремя, я напишу.\n\n"
                       "Как назвать? Латиницей, коротко: например <code>backup</code> или "
                       "<code>socialbot</code>.", cancel_kb())


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip().lower()
    if not settings.valid_job_name(name):
        await message.answer("Только латиница, цифры и дефис, до 20 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= settings.MAX_HB_JOBS:
        await state.clear()
        await message.answer(f"Больше {settings.MAX_HB_JOBS} задач добавить нельзя — удали ненужные.",
                             reply_markup=back_button())
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in _INTERVALS[2:]],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])
    await message.answer(f"Как часто ждать отметку от «{esc(name)}»?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    name = (await state.get_data()).get("hb_name")
    await state.clear()
    arg = call.data.split(":", 1)[1]
    if not name or not arg.isdigit():
        await ack(call, "Начни заново: 📱 Меню → ➕ Добавить", show_alert=True)
        return
    await settings.add_heartbeat_job(name, int(arg))
    await ack(call, "Добавил ✅")
    await render(call, f"✅ «{esc(name)}» под присмотром и уже на главном экране.\n\n{_how_to(name)}",
                 back_button())


@router.callback_query(F.data.startswith("hb_rm:"))
async def cb_hb_remove_here(call: CallbackQuery):
    """«🗑 Удалить» under an alert or the digest: remove the task and drop its
    buttons — the message itself stays as it was."""
    name = call.data.split(":", 1)[1]
    removed = await settings.remove_heartbeat_job(name)
    await ack(call, f"Удалил «{name}» — больше не жду" if removed else "Эта задача из .env — убери её там")
    markup = getattr(call.message, "reply_markup", None)
    if removed and markup is not None:
        rows = [row for row in markup.inline_keyboard
                if not any((b.callback_data or "").split(":", 1)[-1] == name for b in row)]
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if not await settings.remove_heartbeat_job(name):
        await ack(call, "Эта задача из .env — убери её там", show_alert=True)
        return
    await ack(call, "Удалил")
    await render(call, f"🗑 «{esc(name)}» больше не жду.", back_button())
