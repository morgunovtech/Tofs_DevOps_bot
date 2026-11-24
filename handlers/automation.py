"""🤖 What the bot does to production by itself: a switch per kind, one
pause for everything, and the journal of the last actions
(services.automation). auto:menu · auto:pause:<hours|0>[:a] · auto:resume ·
auto:on:<kind> · auto:off:<kind>; «:a» — the tap came from an alert, so the
answer goes below it and the alert stays as it was."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from handlers.common import STALE_BUTTON, STALE_TAP, ack, cb_int, render
from services import automation, docker_api, integrations
from utils.clock import to_local
from utils.i18n import t
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
    lines, rows = [t("🤖 Что делаю сам"), ""], []
    for kind in kinds():
        on = integrations.auto_enabled(kind)
        title = t(_TITLE[kind]).format(names=", ".join(config.autorestart_containers))
        lines.append(f"{title} — {automation.limit_text(kind)}: {t("вкл") if on else t("выкл")}")
        verb = t("выключить") if on else t("включить")
        rows.append([InlineKeyboardButton(text=f"{_ICON[kind]} {verb.capitalize()}",
                                          callback_data=f"auto:{'off' if on else 'on'}:{kind}")])
    if not rows:
        lines.append(t("Сейчас ничего: нет deploy hook и нет доступа к Docker."))
    until = await automation.paused_until()
    if until:
        when = t("до включения") if until == automation.FOREVER else t("до {time}").format(time=to_local(until).strftime('%H:%M'))
        lines.append("\n" + t("⏸ Сейчас всё остановлено {when}.").format(when=when))
        rows.insert(0, [InlineKeyboardButton(text=t("▶️ Включить снова"), callback_data="auto:resume")])
    elif rows:
        rows.insert(0, [InlineKeyboardButton(text=t("⏸ Остановить на 2 часа"), callback_data="auto:pause:2"),
                        InlineKeyboardButton(text=t("⏹ До включения"), callback_data="auto:pause:0")])
    journal = await automation.recent_actions(10)
    if journal:
        lines.append(t("\nПоследнее:"))
        for a in journal:
            who = t("сам") if a.get("auto") else t("по кнопке")
            lines.append(f"• {_when(a)} {_ICON.get(a.get('kind'), '•')} {esc(a.get('label') or a.get('target'))}, "
                         f"{who}: {esc(a.get('result') or t("выполняю…"))}")
    rows.append([InlineKeyboardButton(text=t("← К подключениям"), callback_data="setup:check")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "auto:menu")
async def cb_auto_menu(call: CallbackQuery):
    await ack(call)
    await render(call, *await auto_screen())


@router.callback_query(F.data.startswith("auto:pause:"))
async def cb_auto_pause(call: CallbackQuery):
    """Stopping is always safe — done even for a tap that waited in the queue."""
    parts = call.data.split(":")
    hours = cb_int(parts[2]) if len(parts) > 2 else None
    if hours is None:
        hours = 2
    await automation.pause(hours or None)
    await ack(call, t("Остановил"))
    if call.data.endswith(":a"):
        await call.message.answer(t("⏸ Ничего не делаю сам {words}: не пересобираю, не перезапускаю, не чищу. Включить: ⚙️ Настройки → 🔌 Подключения → 🤖 Что делаю сам.").format(words=t("2 часа") if hours else t("до включения")))
        return
    await render(call, *await auto_screen())


@router.callback_query(F.data == "auto:resume")
async def cb_auto_resume(call: CallbackQuery):
    if not await ack(call, t("Включил")):
        await call.message.answer(t(STALE_TAP))
        return
    await automation.resume()
    await render(call, *await auto_screen())


@router.callback_query(F.data.startswith(("auto:on:", "auto:off:")))
async def cb_auto_toggle(call: CallbackQuery):
    _, mode, kind = call.data.split(":", 2)
    if kind not in automation.KINDS:
        await ack(call, t(STALE_BUTTON), show_alert=True)
        return
    fresh = await ack(call, t("Готово"))
    if mode == "on" and not fresh:          # switching ON waits for a fresh tap
        await call.message.answer(t(STALE_TAP))
        return
    await integrations.set_value(f"auto_{kind}", mode)
    await render(call, *await auto_screen())
