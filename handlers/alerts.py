"""One-tap actions attached to alert messages: act:<action>:<site_id>.
Results go out as replies — the alert text itself stays intact and the
buttons remain usable."""

from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

from db.database import get_active_incidents, set_state
from handlers.common import ack, cb_args, site_by_cb
from monitors.availability import check_availability
from services import humanize, maintenance
from services.actions import purge_cf_cache, trigger_redeploy
from services.screenshots import fetch_screenshot
from utils.clock import now_local
from utils.text import esc
from utils.urls import is_web_page, short_host, site_label

router = Router(name="alerts")


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
        speed = f", {humanize.speed(r.response_time_ms)}" if r.response_time_ms is not None else ""
        if r.ok:
            text = (f"✅ {label} сейчас открывается{speed}." if is_web_page(url, site.get("http_method"))
                    else f"✅ {label} сейчас отвечает{speed}.")
        else:
            text = f"🔴 {label} всё ещё не открывается: {esc(humanize.describe_error(r.error))}."
        await call.message.answer(text)
    elif action == "fix":
        await maintenance.pause_site(site["id"], 60)
        now = datetime.now(UTC)
        for inc in await get_active_incidents():
            if inc["site_id"] == site["id"]:
                await set_state(f"ack:{inc['id']}", (now + timedelta(hours=2)).isoformat())
        await ack(call, "Понял, молчу час")
        await call.message.answer(f"🔧 Понял, ты чинишь {label}. Час не пишу про него, проверки "
                                  f"продолжаются, напишу, когда поднимется.")
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
    elif action == "redeploy":
        await ack(call, "Запускаю передеплой…")
        await call.message.answer(await trigger_redeploy(url))
    elif action == "purge":
        await ack(call, "Сбрасываю кэш…")
        await call.message.answer(await purge_cf_cache(url))
    else:
        await ack(call, "Неизвестное действие", show_alert=True)
