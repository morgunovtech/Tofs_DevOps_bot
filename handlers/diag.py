"""🩺 Self-diagnostics and the 🔔 test alert."""

import platform
from datetime import date, timedelta

import aiohttp
from aiogram import F, Router
from aiogram.types import CallbackQuery

from config import config
from db.database import get_all_sites, set_state
from handlers.common import ack, back_button, render
from services import docker_api, gsc, notifier, public_urls, secrets, settings, updates, yandex_webmaster
from services.notifier import Priority
from utils.clock import now_local
from utils.text import esc, plural
from utils.urls import host_of

router = Router(name="diag")


@router.callback_query(F.data == "menu_diag")
async def cb_diag(call: CallbackQuery):
    await ack(call)
    await render(call, "▱▱▱ Проверяю сам себя...")
    lines = ["🩺 Диагностика:\n"]
    me = await call.bot.get_me()
    lines.append(f"✅ Telegram: @{me.username}")
    try:
        await set_state("diag_ping", now_local().isoformat())
        lines.append("✅ База данных: пишется")
    except Exception as e:
        lines.append(f"⛔ База данных: {esc(e)}")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(f"http://127.0.0.1:{config.webhook_port}/health") as resp:
                lines.append("✅ Веб-сервер (feedback/heartbeat/status): отвечает" if resp.status == 200
                             else f"⚠️ Веб-сервер: HTTP {resp.status}")
    except Exception:
        lines.append("⛔ Веб-сервер: не отвечает на localhost")
    lines.append("✅ PUBLIC_BASE_URL задан" if config.public_base_url
                 else "⚠️ PUBLIC_BASE_URL не задан — ссылки будут с плейсхолдером")
    generated = secrets.generated()
    lines.append("✅ Секреты: webhook и heartbeat разные" if secrets.webhook_secret() != secrets.heartbeat_secret()
                 else "⚠️ Секреты: HEARTBEAT_SECRET совпадает с WEBHOOK_SECRET — задай отдельный")
    if generated:
        lines.append(f"ℹ️ Сгенерировано при старте (хранится в базе): {', '.join(generated)}")
    lines.append(f"ℹ️ Webhook-секрет для виджета: <code>{esc(secrets.webhook_secret())}</code>")
    lines.append(f"✅ Статус-страница: {esc(public_urls.status_page_url())}" if settings.status_page_enabled()
                 else "ℹ️ Статус-страница выключена (Настройки → 🌐)")
    sites = await get_all_sites()
    lines.append(f"✅ Сайтов в мониторинге: {len(sites)}" if sites
                 else "⚠️ Сайтов нет — добавь через «🌍 Сайт детально»")
    if docker_api.docker_available():
        containers = await docker_api.list_containers()
        n = len(containers or [])
        lines.append(f"✅ Docker socket: доступен ({n} {plural(n, 'контейнер', 'контейнера', 'контейнеров')})"
                     if containers is not None else "⚠️ Docker socket: есть, но API не отвечает")
    else:
        lines.append("ℹ️ Docker socket не смонтирован (автоперезапуск/очистка выключены)")
    if gsc.available():
        d = (date.today() - timedelta(days=3)).isoformat()
        lines.append("✅ Google Search Console: токен работает" if await gsc.search_totals(d, d) is not None
                     else "⛔ Google Search Console: настроен, но API не отвечает (см. логи)")
    else:
        lines.append("ℹ️ Google Search Console не настроен")
    if yandex_webmaster.available():
        probe = await yandex_webmaster.get_summaries()
        n = len(probe or {})
        lines.append(f"✅ Яндекс.Вебмастер: токен работает ({n} {plural(n, 'хост', 'хоста', 'хостов')})"
                     if probe is not None else "⛔ Яндекс.Вебмастер: настроен, но API не отвечает (см. логи)")
    else:
        lines.append("ℹ️ Яндекс.Вебмастер не настроен")
    lines.append(f"ℹ️ Скриншоты: {esc(host_of(config.screenshot_template) or '?')}")
    lines.append(f"ℹ️ Вторая точка проверки: {'вкл' if config.second_opinion else 'выкл'}")
    versions = updates.installed_versions()
    lines.append(f"ℹ️ Python {platform.python_version()}, aiogram {versions.get('aiogram', '?')}, "
                 f"aiohttp {versions.get('aiohttp', '?')}"
                 + (f", сборка {esc(config.app_commit)}" if config.app_commit else ""))
    deps = updates.summary_lines(await updates.last_check())
    lines += deps
    await render(call, "\n".join(lines), back_button())


@router.callback_query(F.data == "menu_testalert")
async def cb_test_alert(call: CallbackQuery):
    await ack(call, "Отправляю тестовый алерт…")
    await notifier.send("🚨 ТЕСТОВЫЙ АЛЕРТ\nТак выглядит критическое уведомление — оно пробивает "
                        "mute и тихие часы.\n\nЕсли ты это видишь — доставка работает. ✅",
                        Priority.CRITICAL)
