"""🤖 What the bot does to production by itself: a switch per kind, one
pause for everything, and the journal of the last actions
(services.automation). auto:menu · auto:pause:<hours|0>[:a] · auto:resume ·
auto:on:<kind> · auto:off:<kind>; «:a» — the tap came from an alert, so the
answer goes below it and the alert stays as it was."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from handlers.common import STALE_TAP, ack, render
from services import automation, docker_api, integrations
from utils.clock import to_local
from utils.text import esc, parse_iso_utc

router = Router(name="automation")

_TITLE = {"redeploy": "🚀 Пересобираю сайт, когда он падает",
          "restart": "🔁 Перезапускаю контейнеры {names}",
          "cleanup": "🧹 Чищу docker, когда диск заполнен"}
_ICON = {"redeploy": "🚀", "restart": "🔁", "cleanup": "🧹", "purge": "🧹"}


def kinds() -> list[str]:
    """What can be automatic on this installation at all."""
    found = ["redeploy"] if integrations.deploy_hooks() else []
    if docker_api.docker_available():
        found += (["restart"] if config.autorestart_containers else []) + ["cleanup"]
    return found


def _when(entry: dict) -> str:
    dt = parse_iso_utc(entry.get("at"))
    return to_local(dt).strftime("%d.%m %H:%M") if dt else "?"


async def auto_screen() -> tuple[str, InlineKeyboardMarkup]:
    lines, rows = ["🤖 Что я делаю сам", ""], []
    for kind in kinds():
        on = integrations.auto_enabled(kind)
        title = _TITLE[kind].format(names=", ".join(config.autorestart_containers))
        lines.append(f"{title} — {automation.limit_text(kind)}: {'вкл' if on else 'выкл'}")
        verb = "выключить" if on else "включить"
        rows.append([InlineKeyboardButton(text=f"{_ICON[kind]} {verb.capitalize()}",
                                          callback_data=f"auto:{'off' if on else 'on'}:{kind}")])
    if not rows:
        lines.append("Сейчас ничего: нет deploy hook и нет доступа к Docker.")
    until = await automation.paused_until()
    if until:
        when = "до включения" if until == automation.FOREVER else f"до {to_local(until).strftime('%H:%M')}"
        lines.append(f"\n⏸ Сейчас всё остановлено {when}.")
        rows.insert(0, [InlineKeyboardButton(text="▶️ Включить снова", callback_data="auto:resume")])
    elif rows:
        rows.insert(0, [InlineKeyboardButton(text="⏸ Остановить на 2 часа", callback_data="auto:pause:2"),
                        InlineKeyboardButton(text="⏹ До включения", callback_data="auto:pause:0")])
    journal = await automation.recent_actions(10)
    if journal:
        lines.append("\nПоследнее:")
        for a in journal:
            who = "сам" if a.get("auto") else "по кнопке"
            lines.append(f"• {_when(a)} {_ICON.get(a.get('kind'), '•')} {esc(a.get('label') or a.get('target'))}, "
                         f"{who}: {esc(a.get('result') or 'выполняю…')}")
    rows.append([InlineKeyboardButton(text="← К подключениям", callback_data="setup:check")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "auto:menu")
async def cb_auto_menu(call: CallbackQuery):
    await ack(call)
    await render(call, *await auto_screen())


@router.callback_query(F.data.startswith("auto:pause:"))
async def cb_auto_pause(call: CallbackQuery):
    """Stopping is always safe — done even for a tap that waited in the queue."""
    parts = call.data.split(":")
    hours = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 2
    await automation.pause(hours or None)
    await ack(call, "Остановил")
    if call.data.endswith(":a"):
        await call.message.answer(f"⏸ Ничего не делаю сам {'2 часа' if hours else 'до включения'}: "
                                  f"не пересобираю, не перезапускаю, не чищу. Включить: ⚙️ Настройки → "
                                  f"🔌 Подключения → 🤖 Что бот делает сам.")
        return
    await render(call, *await auto_screen())


@router.callback_query(F.data == "auto:resume")
async def cb_auto_resume(call: CallbackQuery):
    if not await ack(call, "Включил"):
        await call.message.answer(STALE_TAP)
        return
    await automation.resume()
    await render(call, *await auto_screen())


@router.callback_query(F.data.startswith(("auto:on:", "auto:off:")))
async def cb_auto_toggle(call: CallbackQuery):
    _, mode, kind = call.data.split(":", 2)
    if kind not in automation.KINDS:
        await ack(call, "Кнопка устарела — открой /menu", show_alert=True)
        return
    fresh = await ack(call, "Готово")
    if mode == "on" and not fresh:          # switching ON waits for a fresh tap
        await call.message.answer(STALE_TAP)
        return
    await integrations.set_value(f"auto_{kind}", mode)
    await render(call, *await auto_screen())
