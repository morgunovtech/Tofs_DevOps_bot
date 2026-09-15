"""🩺 Diagnostics as a setup checklist.

Every line is one of three kinds — works / needs attention / can be
connected — and every non-green line carries a button that either fixes it
right here (toggle, regenerate) or opens a two-three step wizard whose
steps are buttons too. Optional integrations are entered from the chat and
stored in the DB (services.integrations); .env stays a fallback.
"""

import json
import platform
import re
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import get_all_sites, set_state
from handlers.common import ack, back_button, cancel_kb, render
from services import (
    docker_api,
    gsc,
    integrations,
    notifier,
    public_urls,
    runtime,
    secrets,
    settings,
    updates,
    yandex_webmaster,
)
from services.notifier import Priority
from utils.clock import now_local
from utils.text import esc, plural
from utils.urls import host_of

router = Router(name="setup")


class SetupForm(StatesGroup):
    gsc_property = State()
    gsc_key = State()
    yx_token = State()
    cf_hook = State()
    cf_token = State()
    cf_zone = State()


@dataclass
class Item:
    key: str
    status: str                # ok | warn | off
    title: str                 # shown in the "works" line or as the bullet
    detail: str = ""           # what is wrong / what it gives (HTML-escaped)
    button: tuple[str, str] | None = None   # (label, callback_data)


# ── Probes ───────────────────────────────────────────────────────────────────

async def _web_ok() -> bool:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
            async with s.get(f"http://127.0.0.1:{config.webhook_port}/health") as resp:
                return resp.status == 200
    except Exception:
        return False


async def _db_ok() -> bool:
    try:
        await set_state("diag_ping", now_local().isoformat())
        return True
    except Exception:
        return False


async def _gsc_probe() -> bool:
    d = (date.today() - timedelta(days=3)).isoformat()
    return await gsc.search_totals(d, d) is not None


async def _yandex_probe() -> int | None:
    summaries = await yandex_webmaster.get_summaries()
    return None if summaries is None else len(summaries)


# ── Checklist ────────────────────────────────────────────────────────────────

async def build_items() -> list[Item]:
    items: list[Item] = []
    me = runtime.bot_username()
    items.append(Item("telegram", "ok", f"Telegram @{me}" if me else "Telegram"))
    items.append(Item("db", "ok" if await _db_ok() else "warn", "база данных",
                      "не пишется — проверь volume /app/data и права на файл"))
    items.append(Item("web", "ok" if await _web_ok() else "warn", "веб-сервер",
                      f"не отвечает на localhost:{config.webhook_port} — перезапусти бота, "
                      f"проверь WEBHOOK_PORT"))
    if public_urls.is_public():
        src = "из PUBLIC_BASE_URL" if config.public_base_url else "домен Railway"
        items.append(Item("url", "ok", f"публичный адрес ({src})"))
    else:
        items.append(Item("url", "warn", "публичный адрес",
                          "не задан — heartbeat-ссылки, виджет и статус-страница будут с плейсхолдером",
                          ("🌍 Как задать адрес", "setup:url")))

    n_sites = len(await get_all_sites())
    if n_sites:
        items.append(Item("sites", "ok", f"{n_sites} {plural(n_sites, 'сайт', 'сайта', 'сайтов')}"))
    else:
        items.append(Item("sites", "warn", "сайты", "пока ни одного — нечего мониторить",
                          ("➕ Добавить сайт", "site_add")))

    if secrets.webhook_secret() != secrets.heartbeat_secret():
        items.append(Item("secrets", "ok", "секреты"))
    else:
        items.append(Item("secrets", "warn", "секреты",
                          "heartbeat-секрет совпадает с webhook-секретом, который виден всем "
                          "посетителям сайта — любой сможет глушить dead-man switch",
                          ("🔑 Новый heartbeat-секрет", "setup:hb_regen")))

    if settings.status_page_enabled():
        items.append(Item("status_page", "ok", "статус-страница"))
    else:
        items.append(Item("status_page", "off", "🌐 Статус-страница",
                          "публичная страница «всё ли работает» с аптаймом и бейджами для README",
                          ("🌐 Включить статус-страницу", "setup:sp_on")))

    if integrations.second_opinion_enabled():
        items.append(Item("second_opinion", "ok", "вторая точка проверки"))
    else:
        items.append(Item("second_opinion", "off", "🌐 Вторая точка проверки",
                          "перед алертом «сайт лежит» перепроверка с внешних нод check-host.net",
                          ("🌐 Включить вторую точку", "setup:so:on")))

    if settings.heartbeat_jobs():
        items.append(Item("heartbeats", "ok", "контроль задач"))
    else:
        items.append(Item("heartbeats", "off", "⏰ Контроль задач",
                          "скажу, если бэкап или другая задача по расписанию не отработала",
                          ("⏰ Добавить задачу", "hb_add")))

    if gsc.available():
        ok = await _gsc_probe()
        items.append(Item("gsc", "ok" if ok else "warn", "Google Search Console",
                          "настроен, но API отвечает ошибкой: проверь, что email сервис-аккаунта "
                          "добавлен в пользователи ресурса в Search Console",
                          ("🔎 Google: проверить", "setup:gsc")))
    else:
        items.append(Item("gsc", "off", "🔎 Google Search Console",
                          "«главная выпала из индекса» тем же днём, клики и показы в недельном отчёте",
                          ("🔎 Подключить Google", "setup:gsc")))

    if yandex_webmaster.available():
        hosts = await _yandex_probe()
        items.append(Item("yandex", "ok" if hosts is not None else "warn", "Яндекс.Вебмастер",
                          "токен есть, но API отвечает ошибкой: токен истёк или без скоупа webmaster:read",
                          ("🔎 Яндекс: проверить", "setup:yx")))
    else:
        items.append(Item("yandex", "off", "🔎 Яндекс.Вебмастер",
                          "страницы в поиске, ИКС и фатальные проблемы сайта от Яндекса",
                          ("🔎 Подключить Яндекс", "setup:yx")))

    if integrations.deploy_hooks() or integrations.cf_purge_configured():
        items.append(Item("cloudflare", "ok", "Cloudflare"))
    else:
        items.append(Item("cloudflare", "off", "☁️ Cloudflare",
                          "кнопки «передеплой» и «сброс кэша» в алертах, автопередеплой при падении",
                          ("☁️ Подключить Cloudflare", "setup:cf")))

    if not config.on_railway:
        if docker_api.docker_available():
            items.append(Item("docker", "ok", "docker socket"))
        else:
            items.append(Item("docker", "off", "🐳 Docker на хосте",
                              "автоперезапуск контейнеров и очистка диска на сервере бота",
                              ("🐳 Как подключить", "setup:docker")))

    if config.self_heartbeat_url:
        items.append(Item("watchdog", "ok", "внешний сторож"))
    else:
        items.append(Item("watchdog", "off", "🛡 Внешний сторож",
                          "healthchecks.io скажет, если умрёт сам бот",
                          ("🛡 Как подключить", "setup:watchdog")))
    return items


async def render_checklist() -> tuple[str, InlineKeyboardMarkup]:
    items = await build_items()
    ok = [i for i in items if i.status == "ok"]
    warn = [i for i in items if i.status == "warn"]
    off = [i for i in items if i.status == "off"]
    lines = ["🩺 Диагностика и настройка\n"]
    if ok:
        lines.append("✅ Работает: " + " · ".join(esc(i.title) for i in ok))
    if warn:
        lines.append("\n⚠️ Требует внимания:")
        lines += [f"• {esc(i.title)}: {esc(i.detail)}" for i in warn]
    if off:
        lines.append("\n➕ Можно подключить (необязательно):")
        lines += [f"• {esc(i.title)} — {esc(i.detail)}" for i in off]
    if not warn and not off:
        lines.append("\nВсё подключено 🐕")
    versions = updates.installed_versions()
    footer = (f"Python {platform.python_version()} · aiogram {versions.get('aiogram', '?')} · "
              f"aiohttp {versions.get('aiohttp', '?')}"
              + (f" · сборка {esc(config.app_commit)}" if config.app_commit else "")
              + f" · скриншоты {esc(host_of(config.screenshot_template) or '?')}")
    lines += ["", f"<i>{footer}</i>"]
    lines += [f"<i>{line}</i>" for line in updates.summary_lines(await updates.last_check())]

    rows, row = [], []
    for i in warn + off:
        if i.button:
            row.append(InlineKeyboardButton(text=i.button[0], callback_data=i.button[1]))
            if len(row) == 2:
                rows.append(row)
                row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="🔁 Проверить снова", callback_data="setup:check"),
                 InlineKeyboardButton(text="🔔 Тест алерта", callback_data="menu_testalert")])
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def show_checklist(target):
    text, kb = await render_checklist()
    await render(target, text, kb)


@router.callback_query(F.data.in_({"menu_diag", "setup:check"}))
async def cb_checklist(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await render(call, "▱▱▱ Проверяю сам себя...")
    await show_checklist(call)


@router.callback_query(F.data == "menu_testalert")
async def cb_test_alert(call: CallbackQuery):
    await ack(call, "Отправляю тестовый алерт…")
    await notifier.send("🚨 ТЕСТОВЫЙ АЛЕРТ\nТак выглядит критическое уведомление — оно пробивает "
                        "mute и тихие часы.\n\nЕсли ты это видишь — доставка работает. ✅",
                        Priority.CRITICAL)


# ── One-tap fixes ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "setup:sp_on")
async def cb_status_page_on(call: CallbackQuery):
    await settings.set_status_page(True)
    await ack(call, "Статус-страница включена")
    await render(call, f"🌐 Статус-страница включена:\n{esc(public_urls.status_page_url())}\n\n"
                       f"Выключить можно в ⚙️ Настройках.", back_button("← К диагностике", "setup:check"))


@router.callback_query(F.data.startswith("setup:so:"))
async def cb_second_opinion(call: CallbackQuery):
    on = call.data.endswith(":on")
    await integrations.set_value("second_opinion", "on" if on else "off")
    await ack(call, "Включено" if on else "Выключено")
    await show_checklist(call)


@router.callback_query(F.data == "setup:hb_regen")
async def cb_regen_heartbeat(call: CallbackQuery):
    await secrets.regenerate("heartbeat")
    await ack(call, "Готово")
    jobs = list(settings.heartbeat_jobs())
    example = jobs[0] if jobs else "backup"
    note = ("\n\n⚠️ В окружении задан HEARTBEAT_SECRET — удали его из переменных, иначе после "
            "рестарта вернётся старое значение." if config.heartbeat_secret_env else "")
    await render(call, f"🔑 Новый heartbeat-секрет сохранён. Обнови строку в кроне:\n"
                       f"<code>curl -fsS {esc(public_urls.heartbeat_url(example))}</code>{note}",
                 back_button("← К диагностике", "setup:check"))


# ── Guides ───────────────────────────────────────────────────────────────────

def _guide_kb(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t, callback_data=c)] for t, c in buttons]
    rows.append([InlineKeyboardButton(text="🔁 Проверить снова", callback_data="setup:check"),
                 InlineKeyboardButton(text="← Назад", callback_data="setup:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "setup:url")
async def cb_guide_url(call: CallbackQuery):
    await ack(call)
    if config.on_railway:
        text = ("🌍 Публичный адрес на Railway\n\n"
                "1. Открой сервис → <b>Settings → Networking → Generate Domain</b>, порт <b>8080</b>.\n"
                "2. Railway передеплоит бота, и адрес подхватится сам — переменная PUBLIC_BASE_URL "
                "не нужна.\n"
                "3. Нажми «🔁 Проверить снова».")
    else:
        text = ("🌍 Публичный адрес\n\n"
                "1. Открой порт бота наружу (reverse proxy с TLS или проброс порта "
                f"{config.webhook_port}).\n"
                "2. Пропиши в .env: <code>PUBLIC_BASE_URL=https://bot.example.com</code> "
                "и, если есть прокси, <code>TRUST_PROXY=1</code>.\n"
                "3. Перезапусти бота и нажми «🔁 Проверить снова».")
    await render(call, text, _guide_kb())


@router.callback_query(F.data == "setup:docker")
async def cb_guide_docker(call: CallbackQuery):
    await ack(call)
    await render(call, ("🐳 Docker на хосте бота\n\n"
                        "Даёт автоперезапуск упавших контейнеров и очистку диска (docker prune).\n\n"
                        "1. В docker-compose.yml раскомментируй строку\n"
                        "<code>- /var/run/docker.sock:/var/run/docker.sock</code>\n"
                        "2. В .env перечисли контейнеры: "
                        "<code>AUTORESTART_CONTAINERS=app,db</code>\n"
                        "3. <code>docker compose up -d</code> и «🔁 Проверить снова».\n\n"
                        "Сокет даёт root-доступ к хосту — включай только на сервере, который "
                        "полностью твой."), _guide_kb())


@router.callback_query(F.data == "setup:watchdog")
async def cb_guide_watchdog(call: CallbackQuery):
    await ack(call)
    await render(call, ("🛡 Внешний сторож\n\n"
                        "Бот следит за сайтами, а кто следит за ботом? healthchecks.io (бесплатно).\n\n"
                        "1. Зарегистрируйся на healthchecks.io, создай check с периодом 5 минут.\n"
                        "2. Скопируй ссылку вида <code>https://hc-ping.com/…</code> в переменную "
                        "<code>SELF_HEARTBEAT_URL</code> (Railway → Variables или .env).\n"
                        "3. После перезапуска бот отмечается там каждые 5 минут; пропадёт — "
                        "healthchecks пришлёт письмо."), _guide_kb())


# ── Yandex.Webmaster wizard ──────────────────────────────────────────────────

def _yx_kb() -> InlineKeyboardMarkup:
    buttons = [("🔑 Ввести токен", "setup:yx_token")]
    if integrations.source("yandex_token") == "bot":
        buttons.append(("🗑 Отключить", "setup:yx_off"))
    return _guide_kb(*buttons)


@router.callback_query(F.data == "setup:yx")
async def cb_guide_yandex(call: CallbackQuery):
    await ack(call)
    src = integrations.source("yandex_token")
    state_line = {"bot": "Токен введён в боте.", "env": "Токен взят из .env."}.get(src, "Пока не подключено.")
    await render(call, ("🔎 Яндекс.Вебмастер\n\n"
                        f"{state_line}\n\n"
                        "Даёт число страниц в поиске, ИКС и алерты о фатальных проблемах сайта.\n\n"
                        "1. Открой <a href=\"https://oauth.yandex.ru/\">oauth.yandex.ru</a> → "
                        "«Зарегистрировать приложение», права: <b>Яндекс.Вебмастер → чтение</b>.\n"
                        "2. Получи OAuth-токен для этого приложения (ссылка «Получить токен» "
                        "на странице приложения).\n"
                        "3. Нажми «🔑 Ввести токен» и пришли его сюда — я сразу проверю живым запросом."),
                 _yx_kb())


@router.callback_query(F.data == "setup:yx_token")
async def cb_yx_token(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.yx_token)
    await render(call, "🔑 Пришли OAuth-токен Яндекса одним сообщением.", cancel_kb())


@router.message(SetupForm.yx_token)
async def msg_yx_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,200}", token):
        await message.answer("Не похоже на токен. Попробуй ещё раз:", reply_markup=cancel_kb())
        return
    await state.clear()
    previous = integrations._values.get("yandex_token")
    await integrations.set_value("yandex_token", token)
    hosts = await _yandex_probe()
    if hosts is None:
        await integrations.set_value("yandex_token", previous)
        await message.answer("❌ Яндекс не принял токен (401/403). Проверь скоуп webmaster:read "
                             "и что токен не истёк.", reply_markup=_yx_kb())
        return
    await message.answer(f"✅ Яндекс.Вебмастер подключён: верифицированных хостов {hosts}.",
                         reply_markup=back_button("← К диагностике", "setup:check"))


@router.callback_query(F.data == "setup:yx_off")
async def cb_yx_off(call: CallbackQuery):
    await integrations.set_value("yandex_token", None)
    await ack(call, "Отключено")
    await show_checklist(call)


# ── Google Search Console wizard ─────────────────────────────────────────────

def _gsc_kb() -> InlineKeyboardMarkup:
    buttons = [("1️⃣ Указать ресурс", "setup:gsc_prop"), ("2️⃣ Прислать JSON-ключ", "setup:gsc_key")]
    if integrations.source("gsc_key") == "bot" or integrations.source("gsc_property") == "bot":
        buttons.append(("🗑 Отключить", "setup:gsc_off"))
    return _guide_kb(*buttons)


def _gsc_state_line() -> str:
    prop = integrations.gsc_property()
    creds = integrations.gsc_credentials()
    parts = [f"Ресурс: <code>{esc(prop)}</code>" if prop else "Ресурс не указан",
             f"ключ: {esc(creds.get('client_email', '?'))}" if creds else "ключ не загружен"]
    return " · ".join(parts)


@router.callback_query(F.data == "setup:gsc")
async def cb_guide_gsc(call: CallbackQuery):
    await ack(call)
    await render(call, ("🔎 Google Search Console\n\n"
                        f"{_gsc_state_line()}\n\n"
                        "Даёт алерт «главная выпала из индекса» тем же днём и клики/показы "
                        "неделя к неделе в воскресном отчёте.\n\n"
                        "1. <a href=\"https://console.cloud.google.com/iam-admin/serviceaccounts\">"
                        "Google Cloud → Service Accounts</a>: создай аккаунт, вкладка Keys → "
                        "Add key → JSON. Файл скачается.\n"
                        "2. <a href=\"https://console.cloud.google.com/apis/library/searchconsole.googleapis.com\">"
                        "Включи Google Search Console API</a> в том же проекте.\n"
                        "3. В Search Console → настройки ресурса → Пользователи → добавь email "
                        "сервис-аккаунта (он есть в JSON, поле client_email).\n"
                        "4. Здесь: «1️⃣ Указать ресурс» (например <code>sc-domain:example.com</code>) "
                        "и «2️⃣ Прислать JSON-ключ» файлом."), _gsc_kb())


@router.callback_query(F.data == "setup:gsc_prop")
async def cb_gsc_prop(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.gsc_property)
    await render(call, "1️⃣ Пришли ресурс Search Console: <code>sc-domain:example.com</code> "
                       "(доменный, покрывает поддомены) или <code>https://example.com/</code>.",
                 cancel_kb())


@router.message(SetupForm.gsc_property)
async def msg_gsc_prop(message: Message, state: FSMContext):
    prop = (message.text or "").strip()
    if not re.fullmatch(r"(sc-domain:[a-z0-9.-]+|https?://[^\s]+)", prop, re.I):
        await message.answer("Формат: <code>sc-domain:example.com</code> или <code>https://example.com/</code>",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("gsc_property", prop)
    gsc.reset_token_cache()
    await message.answer(f"✅ Ресурс сохранён: <code>{esc(prop)}</code>\n{_gsc_state_line()}",
                         reply_markup=_gsc_kb())


@router.callback_query(F.data == "setup:gsc_key")
async def cb_gsc_key(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.gsc_key)
    await render(call, "2️⃣ Пришли JSON-ключ сервис-аккаунта файлом (или вставь его текст).",
                 cancel_kb())


@router.message(SetupForm.gsc_key)
async def msg_gsc_key(message: Message, state: FSMContext):
    raw = message.text or ""
    if message.document:
        if (message.document.file_size or 0) > 64 * 1024:
            await message.answer("Файл слишком большой для ключа сервис-аккаунта.", reply_markup=cancel_kb())
            return
        buf = await message.bot.download(message.document)
        raw = buf.read().decode("utf-8", errors="replace")
    creds = integrations.valid_gsc_key(raw)
    if not creds:
        await message.answer("Это не JSON-ключ сервис-аккаунта (нужны поля client_email и private_key).",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("gsc_key", json.dumps(creds))
    gsc.reset_token_cache()
    if not integrations.gsc_property():
        await message.answer("✅ Ключ сохранён. Теперь укажи ресурс: «1️⃣ Указать ресурс».",
                             reply_markup=_gsc_kb())
        return
    if await _gsc_probe():
        await message.answer("✅ Google Search Console подключён, API отвечает.",
                             reply_markup=back_button("← К диагностике", "setup:check"))
    else:
        await message.answer(f"Ключ сохранён, но API пока отвечает ошибкой. Чаще всего не сделан "
                             f"шаг 3: добавь <code>{esc(creds.get('client_email', ''))}</code> "
                             f"в пользователи ресурса в Search Console и нажми «🔁 Проверить снова».",
                             reply_markup=_gsc_kb())


@router.callback_query(F.data == "setup:gsc_off")
async def cb_gsc_off(call: CallbackQuery):
    await integrations.set_value("gsc_key", None)
    await integrations.set_value("gsc_property", None)
    gsc.reset_token_cache()
    await ack(call, "Отключено")
    await show_checklist(call)


# ── Cloudflare wizard ────────────────────────────────────────────────────────

def _cf_kb() -> InlineKeyboardMarkup:
    buttons = [("🚀 Добавить deploy hook", "setup:cf_hook"),
               ("🧹 Токен для сброса кэша", "setup:cf_token"),
               ("🆔 Zone ID", "setup:cf_zone")]
    if any(integrations.source(k) == "bot" for k in ("deploy_hooks", "cf_api_token", "cf_zone_id")):
        buttons.append(("🗑 Сбросить введённое в боте", "setup:cf_off"))
    return _guide_kb(*buttons)


def _cf_state_line() -> str:
    hooks = integrations.deploy_hooks()
    parts = [f"deploy hooks: {', '.join(sorted(hooks))}" if hooks else "deploy hooks: нет",
             "сброс кэша: настроен" if integrations.cf_purge_configured() else "сброс кэша: нет"]
    return " · ".join(parts)


@router.callback_query(F.data == "setup:cf")
async def cb_guide_cf(call: CallbackQuery):
    await ack(call)
    await render(call, ("☁️ Cloudflare\n\n"
                        f"{esc(_cf_state_line())}\n\n"
                        "<b>Deploy hook</b> даёт кнопку «🚀 Передеплой» в алертах (и автопередеплой "
                        "при падении, если включить AUTO_REDEPLOY): Cloudflare Pages → проект → "
                        "Settings → Builds → Deploy hooks → Add. Пришли сюда «хост URL».\n\n"
                        "<b>Сброс кэша</b> даёт кнопку «🧹»: API-токен с правом Zone → Cache Purge "
                        "(dash.cloudflare.com → My Profile → API Tokens) и Zone ID со страницы "
                        "Overview домена."), _cf_kb())


@router.callback_query(F.data == "setup:cf_hook")
async def cb_cf_hook(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.cf_hook)
    await render(call, "🚀 Пришли хост и URL deploy hook через пробел:\n"
                       "<code>example.com https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/…</code>",
                 cancel_kb())


@router.message(SetupForm.cf_hook)
async def msg_cf_hook(message: Message, state: FSMContext):
    parts = re.split(r"[\s=]+", (message.text or "").strip(), maxsplit=1)
    host = host_of("https://" + parts[0]) if parts and parts[0] else ""
    url = parts[1].strip() if len(parts) == 2 else ""
    if not host or "." not in host or not url.startswith("https://"):
        await message.answer("Формат: <code>example.com https://api.cloudflare.com/…</code>",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_deploy_hook(host, url)
    await message.answer(f"✅ Deploy hook для {esc(host)} сохранён.\n{esc(_cf_state_line())}",
                         reply_markup=_cf_kb())


@router.callback_query(F.data == "setup:cf_token")
async def cb_cf_token(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.cf_token)
    await render(call, "🧹 Пришли API-токен Cloudflare с правом Zone → Cache Purge.", cancel_kb())


@router.message(SetupForm.cf_token)
async def msg_cf_token(message: Message, state: FSMContext):
    token = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,100}", token):
        await message.answer("Не похоже на API-токен Cloudflare.", reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("cf_api_token", token)
    await message.answer(f"✅ Токен сохранён.\n{esc(_cf_state_line())}", reply_markup=_cf_kb())


@router.callback_query(F.data == "setup:cf_zone")
async def cb_cf_zone(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.cf_zone)
    await render(call, "🆔 Пришли Zone ID (32 hex-символа со страницы Overview домена в Cloudflare).",
                 cancel_kb())


@router.message(SetupForm.cf_zone)
async def msg_cf_zone(message: Message, state: FSMContext):
    zone = (message.text or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", zone):
        await message.answer("Zone ID — это 32 hex-символа. Попробуй ещё раз:", reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("cf_zone_id", zone)
    await message.answer(f"✅ Zone ID сохранён.\n{esc(_cf_state_line())}", reply_markup=_cf_kb())


@router.callback_query(F.data == "setup:cf_off")
async def cb_cf_off(call: CallbackQuery):
    for key in ("deploy_hooks", "cf_api_token", "cf_zone_id"):
        await integrations.set_value(key, None)
    await ack(call, "Сброшено")
    await show_checklist(call)
