"""The full page of a main-screen block — one tap, everything at once:
🤖 bots and the bot's own systems, 📣 what another bot sees. The 🌐 sites
block opens the site cards (handlers.sites)."""

from datetime import UTC, datetime

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from handlers.common import ack, render
from reports.scheduler import backup_info, send_copy_to_owner
from services import heartbeats, settings, updates
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
    weekly = ("по воскресеньям присылаю тебе в личку — без ключей подключений" if settings.weekly_backup()
              else "в Telegram не присылаю (выключено в «🔔 Когда писать»)")
    return "✅", when, f"✅ Резервная копия базы — {when}. Делаю каждую ночь, {weekly}."


async def bots_page() -> tuple[str, InlineKeyboardMarkup]:
    jobs = await job_states()
    watchdog = await heartbeats.watchdog()
    lines = ["🤖 Боты и системы", ""]
    if jobs:
        lines += [heartbeats.digest_line(j)[0] for j in jobs]
    else:
        lines.append("Ботов и задач по расписанию пока нет. Добавь — и я скажу, если они замолчат.")
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
    """A fresh copy, now — not last night's: its own file, so the nightly
    ones stay as they were; to the owner's private chat only, without
    tokens and secrets (reports.scheduler.send_copy_to_owner). A double tap
    is safe: the copies are made one after another."""
    await ack(call, "Делаю копию…")
    problem = await send_copy_to_owner(
        f"💾 Копия базы бота на {to_local(datetime.now(UTC)).strftime('%d.%m %H:%M')}: сайты, настройки, "
        f"история. Ключей подключений и секретов в ней нет. Чтобы восстановить — пришли мне этот файл.")
    if call.message is not None:
        await call.message.answer(f"❌ Копию не прислал: {esc(problem)}." if problem
                                  else "💾 Прислал копию тебе в личку.")


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
