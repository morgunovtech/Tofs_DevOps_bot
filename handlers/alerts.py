"""One-tap actions attached to alert messages: act:<action>:<site_id>.
Results go out as replies — the alert text itself stays intact and the
buttons remain usable. «🚀»/«🧹» change production: the first tap asks,
«Да» (act:ok:…) within automation.CONFIRM_TTL does — once, journaled."""

import json
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_active_incidents, get_state, set_state
from handlers.common import STALE_TAP, ack, cb_args, render, site_by_cb
from monitors.availability import check_availability
from reports.formatter import incident_line
from services import automation, humanize, maintenance
from services.actions import purge_cf_cache, trigger_redeploy
from services.screenshots import fetch_screenshot
from utils.clock import now_local
from utils.text import esc
from utils.urls import host_of, is_web_page, short_host, site_label

router = Router(name="alerts")

# «🚀»/«🧹» change production: the first tap asks, «Да» within CONFIRM_TTL does.
_CONFIRM = {
    "redeploy": ("🚀 Да, пересобрать",
                 "🚀 Пересобрать {label}? Cloudflare Pages выложит то, что сейчас лежит в ветке сайта, "
                 "сборка займёт 1–2 минуты."),
    "purge": ("🧹 Да, сбросить кэш",
              "🧹 Сбросить кэш Cloudflare для {host}? Первые посетители после сброса пойдут мимо кэша "
              "прямо на сервер сайта."),
}


def _target(action: str, url: str) -> str:
    """What the double-tap guard and the journal key the action by: the hook
    for a rebuild (never its secret address), the host for a purge."""
    return (automation.redeploy_target(url) or host_of(url)) if action == "redeploy" else host_of(url)


async def _ask(call: CallbackQuery, action: str, site: dict):
    url = site["url"]
    button, question = _CONFIRM[action]
    text = question.format(label=esc(site_label(url)), host=esc(host_of(url)))
    if action == "redeploy":
        done = await automation.last_auto("redeploy", _target(action, url), automation.BUDGETS["redeploy"][1])
        if done:
            text += f"\nЯ уже пересобирал его сам в {done}."
    stamp = int(datetime.now(UTC).timestamp())
    await call.message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=button, callback_data=f"act:ok:{action}:{site['id']}:{stamp}"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data=f"act:no:{site['id']}")]]))


# Registered BEFORE cb_alert_action: its F.data.startswith("act:") would take «act:ok:…» first.
@router.callback_query(F.data.startswith("act:ok:"))
async def cb_alert_confirmed(call: CallbackQuery):
    """act:ok:<redeploy|purge>:<site_id>:<unix time of the question>."""
    parts = (call.data or "").split(":")
    if len(parts) != 5 or parts[2] not in _CONFIRM or not parts[3].isdigit() or not parts[4].isdigit():
        await ack(call, "Не понял действие", show_alert=True)
        return
    action, sid, asked = parts[2], parts[3], int(parts[4])
    if not await ack(call, "Запускаю…"):
        await call.message.answer(STALE_TAP)
        return
    site = await site_by_cb(sid)
    if not site:
        await call.message.answer("Сайт не найден или убран из мониторинга — ничего не делаю.")
        return
    if datetime.now(UTC).timestamp() - asked > automation.CONFIRM_TTL.total_seconds():
        await call.message.answer("⌛ Этот вопрос устарел — нажми кнопку под тревогой ещё раз.")
        return
    url = site["url"]
    row, why = await automation.tap(action, _target(action, url), label=host_of(url))
    if row is None:
        await call.message.answer(f"⏳ Не запускаю второй раз: {esc(why)}.")
        return
    if action == "redeploy":
        res = await trigger_redeploy(url)
        text = ("🚀 " if res.ok else "❌ ") + res.text
    else:
        text = await purge_cf_cache(url)
    await automation.finish(row, text)
    await call.message.answer(text)


async def _quiet_hour(site_id: int):
    """«🔧 Я чиню»: FIX_MINUTES without messages about the site and without
    escalation reminders; the checks go on, the ✅ ends the silence."""
    await maintenance.pause_site(site_id, maintenance.FIX_MINUTES)
    until = datetime.now(UTC) + timedelta(minutes=maintenance.FIX_MINUTES)
    for inc in await get_active_incidents():
        if inc["site_id"] == site_id:
            await set_state(f"ack:{inc['id']}", until.isoformat())


@router.callback_query(F.data.startswith("grp:"))
async def cb_alert_group(call: CallbackQuery):
    """Buttons of a message about several sites at once (reports.scheduler,
    GROUP_FROM): grp:list:<id> — the sites one by one, below the alert;
    grp:fix:<id> — «🔧 Я чиню» for all of them."""
    args = cb_args(call, 2)
    raw = await get_state(f"alert_group:{args[1]}") if args and args[1].isdigit() else None
    try:
        ids = [int(i) for i in json.loads(raw)] if raw else []
    except (ValueError, TypeError):
        ids = []
    by_id = {inc["id"]: inc for inc in await get_active_incidents()}
    group = [by_id[i] for i in ids if i in by_id]
    if not group:
        await ack(call, "Все сайты из этой тревоги уже открываются" if raw else "Тревога устарела — открой «Что не так»",
                  show_alert=True)
        return
    if args[0] == "fix":
        if not await ack(call, f"Понял, молчу {maintenance.fix_length()}"):
            await call.message.answer(STALE_TAP)
            return
        for site_id in dict.fromkeys(inc["site_id"] for inc in group):
            await _quiet_hour(site_id)
        length = maintenance.fix_length()
        await call.message.answer(f"🔧 Понял, ты чинишь: {esc(', '.join(site_label(inc['url']) for inc in group))}. "
                                  f"{length.capitalize()} не пишу про них, проверки продолжаются. "
                                  f"Поднимутся раньше — сразу напишу и сниму тишину.")
        return
    await ack(call)
    lines = [f"{n}. 🔴 {esc(incident_line(inc))}" for n, inc in enumerate(group, 1)]
    buttons = [InlineKeyboardButton(text=f"ℹ️ {n}", callback_data=f"inc_explain:{inc['id']}:a")
               for n, inc in enumerate(group, 1)]
    await call.message.answer("\n".join(lines) + "\n\nНомер — что делать с этим сайтом: «🔧 Я чиню», "
                              "«🔍 Проверить сейчас», «✓ Закрыть».",
                              reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 4]
                                                                                 for i in range(0, len(buttons), 4)]))


@router.callback_query(F.data.startswith("act:"))
async def cb_alert_action(call: CallbackQuery):
    args = cb_args(call, 2)
    if not args:
        await ack(call, "Не понял действие", show_alert=True)
        return
    action, sid = args
    site = await site_by_cb(sid)
    if not site:
        await ack(call, "Сайт не найден или убран из мониторинга", show_alert=True)
        return
    url = site["url"]
    label = esc(site_label(url))
    if action == "recheck":
        await ack(call, "Проверяю…")
        r = await check_availability(url, manage=False)
        speed = f" {humanize.speed(r.response_time_ms)}" if r.response_time_ms is not None else ""
        if r.ok:
            text = (f"✅ {label} сейчас открывается{speed}." if is_web_page(url, site.get("http_method"))
                    else f"✅ {label} сейчас отвечает{speed}.")
        elif r.transient:
            text = f"⚠️ {label}: проверить сейчас не могу — {esc(humanize.describe_error(r.error))}."
        else:
            text = f"🔴 {label} всё ещё не открывается: {esc(humanize.describe_error(r.error))}."
        await call.message.answer(text)
    elif action == "fix":
        # An old alert about a site that opens by now: a pause would mute the
        # next real fall for an hour. Pages with errors (deep) are still a fix.
        if not any(i["site_id"] == site["id"] and i["check_type"] in ("availability", "deep")
                   for i in await get_active_incidents()):
            await ack(call, "Сайт сейчас работает — пауза не нужна. Чинишь что-то другое — поставь паузу "
                            "на карточке сайта.", show_alert=True)
            return
        await _quiet_hour(site["id"])
        length = maintenance.fix_length()
        await ack(call, f"Понял, молчу {length}")
        await call.message.answer(f"🔧 Понял, ты чинишь {label}. {length.capitalize()} не пишу про него, проверки "
                                  f"продолжаются. Поднимется раньше — сразу напишу и сниму тишину.")
    elif action == "shot":
        if not is_web_page(url, site.get("http_method")):
            await ack(call, "Здесь нет страницы, которую можно сфотографировать: это API или порт",
                      show_alert=True)
            return
        await ack(call, "Делаю скрин… (~15 сек)")
        try:
            await call.bot.send_chat_action(call.message.chat.id, "upload_photo")
        except Exception:
            pass
        image, reason = await fetch_screenshot(url)
        if image:
            await call.message.answer_photo(
                BufferedInputFile(image, filename="screenshot.png"),
                caption=f"📸 {esc(short_host(url))} · {now_local().strftime('%d.%m %H:%M')}")
        else:
            await call.message.answer(f"❌ Не получилось сделать снимок {esc(short_host(url))}: {esc(reason)}.")
    elif action in _CONFIRM:
        await ack(call)
        await _ask(call, action, site)
    elif action == "no":
        await ack(call)
        await render(call, "✖️ Хорошо, ничего не делаю.")
    else:
        await ack(call, "Неизвестное действие", show_alert=True)
