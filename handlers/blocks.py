"""The full page of a main-screen block — one tap, everything at once:
🤖 bots and the bot's own systems, 📣 what another bot sees. The 🌐 sites
block opens the site cards (handlers.sites)."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render
from reports.scheduler import backup_info, run_db_backup
from services import heartbeats, updates
from services.heartbeats import job_states
from utils.clock import to_local
from utils.text import esc, fmt_duration, parse_iso_utc

router = Router(name="blocks")

HOME = InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")


def backup_state() -> tuple[str, str, str]:
    """(icon, a few words for the main screen, the full line for the page)
    of the bot's own database backup — the one thing about it that matters:
    is there a fresh copy."""
    backup = backup_info()
    if backup is None:
        return "⏳", "первая — сегодня ночью", "⏳ Резервная копия базы — первую сделаю сегодня ночью."
    age_h = (datetime.now(UTC) - backup[1]).total_seconds() / 3600
    when = to_local(backup[1]).strftime("%d.%m %H:%M")
    if age_h >= 36:
        return ("🟠", f"последняя {when}",
                f"🟠 Резервная копия базы — последняя {when}: ночные копии не делаются. Чаще всего "
                f"кончилось место на диске.")
    return ("✅", when, f"✅ Резервная копия базы — {when}. Делаю каждую ночь, по воскресеньям присылаю "
                        f"файлом сюда.")


async def bots_page() -> tuple[str, InlineKeyboardMarkup]:
    jobs = await job_states()
    watchdog = await heartbeats.watchdog()
    lines = ["🤖 Боты и системы", ""]
    if jobs:
        lines += [heartbeats.digest_line(j)[0] for j in jobs]
    else:
        lines.append("Ботов и задач по расписанию пока нет. Добавь — и я скажу, если они перестанут "
                     "отмечаться.")
    lines += ["", heartbeats.watchdog_digest_line(watchdog)[0], backup_state()[2]]
    if await updates.deploy_problem():
        lines.append("🟠 Новая версия бота не запустилась — работаю на прежней. Лог сборки — в сообщении "
                     "«Новая версия бота не запустилась».")
    job_buttons = [InlineKeyboardButton(text=f"⚙️ {j.name}", callback_data=f"hb_show:{j.name}")
                   for j in jobs if len(f"hb_show:{j.name}".encode()) <= 64]
    rows = [job_buttons[i:i + 2] for i in range(0, len(job_buttons), 2)]
    if watchdog is None:
        rows.append([InlineKeyboardButton(text="🛡 Подключить сторожа", callback_data="setup:watchdog")])
    rows.append([InlineKeyboardButton(text="💾 Скачать копию базы", callback_data="sys:backup")])
    rows.append([InlineKeyboardButton(text="➕ Бот или задача", callback_data="hb_add"), HOME])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "blk:bots")
async def cb_bots(call: CallbackQuery):
    await ack(call)
    await render(call, *await bots_page())


@router.callback_query(F.data == "sys:backup")
async def cb_backup_now(call: CallbackQuery):
    """A fresh copy, now — not last night's — sent below the page."""
    await ack(call, "Делаю копию…")
    await run_db_backup()
    backup = backup_info()
    if backup is None or call.message is None:
        await ack(call, "Не получилось сделать копию — проверь место на диске", show_alert=True)
        return
    await call.message.answer_document(
        FSInputFile(backup[0]),
        caption=f"💾 Копия базы бота на {to_local(backup[1]).strftime('%d.%m %H:%M')}. В ней токены "
                f"подключений — храни её как пароль.")


@router.callback_query(F.data.startswith("blk:seen:"))
async def cb_seen(call: CallbackQuery):
    """What another bot sees — its lines in full, when it last said so, and
    the way to its own screens (charts, security)."""
    name = call.data.split(":", 2)[2]
    job = next((j for j in await job_states() if j.name == name), None)
    status = await heartbeats.job_status(name) if job else None
    if not job or not status:
        await ack(call, "Этот бот больше ничего не присылает", show_alert=True)
        return
    await ack(call)
    at = parse_iso_utc(status.get("at"))
    ago = f", {fmt_duration((datetime.now(UTC) - at).total_seconds() / 60)} назад" if at else ""
    lines = [f"{esc(heartbeats.status_title(job, status))} · от {esc(name)}{ago}", ""]
    lines += [f"{icon} {esc(item)} — {esc(text)}" for icon, item, text in heartbeats.status_items(job, status)]
    lines += ["", f"Подробности — графики, тревоги, безопасность аккаунтов — в самом {esc(name)}."]
    rows = []
    if status.get("open_url"):
        rows.append([InlineKeyboardButton(text=f"↗️ Открыть {name}", url=status["open_url"])])
    rows.append([InlineKeyboardButton(text=f"⚙️ {name}", callback_data=f"hb_show:{name}"), HOME])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))
