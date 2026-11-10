"""🔌 Connections: what works, what needs a hand, what could be added.

Every line is one of three kinds — works / needs attention / can be
connected — and every non-green line carries a button that either fixes it
right here (toggle, regenerate) or opens a two-three step wizard whose
steps are buttons too. Optional integrations are entered from the chat and
stored in the DB (services.integrations); .env stays a fallback.
"""

import asyncio
import json
import platform
import re
from dataclasses import dataclass
from datetime import date, timedelta

import aiohttp
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import get_all_sites, get_state, set_state
from handlers.common import (
    HEARTBEAT_SECRET_NOTE,
    RECHECK,
    STALE_TAP,
    ack,
    back_button,
    cancel_kb,
    forget_secret,
    render,
)
from handlers.settings import publish_screen
from reports.scheduler import rerun_visitors_soon
from services import (
    automation,
    crux,
    docker_api,
    github,
    gsc,
    heartbeats,
    integrations,
    notifier,
    public_urls,
    railway,
    runtime,
    secrets,
    settings,
    updates,
    yandex_webmaster,
)
from services.actions import cf_token_status
from services.integrations import Probe
from services.notifier import Priority
from utils.clock import now_local
from utils.text import esc, plural
from utils.urls import host_of, is_http_url, proxy_label

router = Router(name="setup")

# Said after every «🗑 Отключить»: copies of the database sent before this
# version carried the tokens — removing one here does not recall it.
OLD_COPIES_NOTE = ("Если я раньше присылал копии базы — токен остался в них: отзови его у "
                   "провайдера.")
# A key the service could not be asked about is kept: «не достучался» is
# not «не принял», and the owner must not reissue a key the provider shows once.
UNCHECKED_NOTE = ("⚠️ Сохранил, но проверить не смог: {service} не ответил. Проверю при следующем "
                  "открытии «🔌 Подключения».")


class SetupForm(StatesGroup):
    gsc_property = State()
    gsc_key = State()
    yx_token = State()
    cf_hook = State()
    cf_token = State()
    cf_zone = State()
    watchdog_url = State()
    gh_token = State()
    gh_owner = State()
    crux_key = State()


@dataclass
class Item:
    key: str
    status: str                # ok | warn | off
    title: str                 # shown in the "works" line or as the bullet
    detail: str = ""           # what is wrong / what it gives (HTML-escaped)
    button: tuple[str, str] | None = None   # (label, callback_data)
    manage: tuple[str, str] | None = None   # shown while it works: switch off, settings


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
    via = f" через прокси {proxy_label(config.telegram_proxy)}" if config.telegram_proxy else ""
    items.append(Item("telegram", "ok", (f"Telegram @{me}" if me else "Telegram") + via))
    if runtime.db_on_persistent_disk() is False:
        items.append(Item("db", "warn", "база данных",
                          "не на постоянном диске — при каждом обновлении сотрётся. Railway: правый клик "
                          "по сервису → Attach Volume → /app/data, DB_PATH=/app/data/bot.db"))
    else:
        items.append(Item("db", "ok" if await _db_ok() else "warn", "база данных",
                          "не пишется — проверь volume /app/data и права на файл"))
    if config.on_railway:
        days = await railway.days_since_claim()
        if await railway.is_paid():
            items.append(Item("railway", "ok", "Railway Hobby"))
        elif days is not None and days < railway.TRIAL_DAYS + 3:
            # Unknown date = an install from before this check; still running
            # past the trial = the account pays. Neither needs the warning.
            items.append(Item("railway", "warn", "тариф Railway",
                              "на пробном периоде через 30 дней с регистрации я остановлюсь молча — нужен Hobby",
                              ("💳 Railway: тариф", "setup:railway")))
    items.append(Item("web", "ok" if await _web_ok() else "warn", "веб-сервер",
                      f"не отвечает на localhost:{config.webhook_port} — перезапусти бота, "
                      f"проверь WEBHOOK_PORT"))
    if public_urls.base_url().startswith("http://") and public_urls.is_public():
        items.append(Item("url", "warn", "публичный адрес без TLS",
                          "http:// — секрет heartbeat идёт открытым текстом, а виджет на https-сайтах "
                          "браузер заблокирует. Нужен reverse proxy с TLS и адрес https://",
                          ("🌍 Как задать адрес", "setup:url")))
    elif public_urls.is_public():
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

    if secrets.ignored_env():
        items.append(Item("secrets", "warn", "секреты",
                          f"в окружении задан {', '.join(secrets.ignored_env())}, но я его не использую: он совпадает "
                          "с публичным секретом виджета или был заменён кнопкой 🔑. Удали переменную из окружения — "
                          "адреса на карточках задач уже новые"))
    elif secrets.webhook_secret() != secrets.heartbeat_secret():
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
        h = await integrations.health("checkhost")
        if integrations.is_broken(h):
            items.append(Item("second_opinion", "warn", "вторая точка проверки (check-host.net)",
                              f"check-host.net не отвечает ({h.get('reason') or 'нет ответа'}): тревога «сайт лёг» "
                              f"уходит без перепроверки извне", ("🌐 Вторая точка", "setup:so")))
        else:
            items.append(Item("second_opinion", "ok", "вторая точка проверки (check-host.net)",
                              manage=("🌐 Вторая точка: выключить", "setup:so")))
    else:
        # A screen with what goes where, not a one-tap switch: the service
        # learns the sites' addresses, and that is the owner's choice.
        items.append(Item("second_opinion", "off", "🌐 Вторая точка проверки",
                          "перед тревогой «сайт лежит» перепроверка с серверов check-host.net — "
                          "им уходят адреса сайтов",
                          ("🌐 Вторая точка", "setup:so")))

    if settings.heartbeat_jobs():
        items.append(Item("heartbeats", "ok", "задачи и боты"))
    else:
        items.append(Item("heartbeats", "off", "⏰ Задачи и боты",
                          "скажу, если другой бот или задача по расписанию замолчит",
                          ("⏰ Добавить задачу или бота", "hb_add")))

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

    if crux.available():
        h = await integrations.health("crux")
        if integrations.is_broken(h):
            items.append(Item("crux", "warn", "скорость у посетителей Chrome (Google)",
                              f"Google не отвечает на ключ ({h.get('reason') or 'нет ответа'}): строка «у посетителей» "
                              f"не обновляется", ("⚡ Заменить ключ", "setup:crux")))
        else:
            items.append(Item("crux", "ok", "скорость у посетителей Chrome (Google)"))
    else:
        items.append(Item("crux", "off", "⚡ Скорость у посетителей Chrome",
                          "сколько реальные посетители ждут твой сайт — данные Google за 28 дней, для сайтов, "
                          "где посетителей достаточно",
                          ("⚡ Подключить данные Google", "setup:crux")))

    if integrations.deploy_hooks() or integrations.cf_purge_configured():
        items.append(Item("cloudflare", "ok", "Cloudflare"))
    else:
        items.append(Item("cloudflare", "off", "☁️ Cloudflare",
                          "кнопки «пересобрать» и «сбросить кэш» в тревогах, пересборка сама при падении",
                          ("☁️ Подключить Cloudflare", "setup:cf")))

    if github.available():
        login = await github.viewer_login()
        items.append(Item("github", "ok" if login else "warn", "GitHub",
                          "токен есть, но GitHub его не принимает: истёк, отозван или скопирован не целиком",
                          ("🐙 GitHub: проверить", "setup:gh")))
    else:
        items.append(Item("github", "off", "🐙 GitHub",
                          "по воскресеньям — что прибрать в репозиториях: старые PR, лишние ветки, "
                          "красный CI, пропавшие репозитории",
                          ("🐙 Подключить GitHub", "setup:gh")))

    if not config.on_railway:
        # A live probe, not the file's existence: a socket nobody listens on is a mounted file too.
        if docker_api.docker_available() and await docker_api.ping():
            items.append(Item("docker", "ok", "docker socket"))
        elif docker_api.docker_available() and config.docker_socket_root_group:
            items.append(Item("docker", "warn", "docker socket",
                              "сокет Docker принадлежит группе root: нужна ALLOW_ROOT_GROUP_SOCKET=1 в .env "
                              "или отфильтрованный доступ через socket-proxy (docs/SETUP.md)",
                              ("🐳 Как подключить", "setup:docker")))
        elif docker_api.docker_available():
            items.append(Item("docker", "warn", "docker socket",
                              "сокет смонтирован, но Docker не отвечает — автоперезапуск и очистка не работают; "
                              "обычно помогает docker compose up -d",
                              ("🐳 Как подключить", "setup:docker")))
        else:
            items.append(Item("docker", "off", "🐳 Docker на хосте",
                              "автоперезапуск контейнеров; очистка диска — по желанию, AUTO_CLEANUP=1",
                              ("🐳 Как подключить", "setup:docker")))

    w = await heartbeats.watchdog()
    if w and w.icon == "🟠":
        items.append(Item("watchdog", "warn", f"Внешний сторож {w.name}",
                          f"{w.state} — если бот упадёт, никто не узнает",
                          ("🛡 Сторож: проверить", "setup:watchdog")))
    elif w:
        items.append(Item("watchdog", "ok", "внешний сторож"))
    else:
        items.append(Item("watchdog", "off", "🛡 Внешний сторож",
                          "healthchecks.io скажет, если умрёт сам бот",
                          ("🛡 Подключить сторожа", "setup:watchdog")))
    return items


def _button_rows(items: list[Item]) -> list[list[InlineKeyboardButton]]:
    rows, row = [], []
    for i in items:
        if i.button:
            row.append(InlineKeyboardButton(text=i.button[0], callback_data=i.button[1]))
            if len(row) == 2:
                rows.append(row)
                row = []
    return rows + [row] if row else rows


async def render_checklist() -> tuple[str, InlineKeyboardMarkup]:
    """What works is one line, not a list of components; what is broken
    comes with its fix; what could be connected waits behind one button."""
    items = await build_items()
    warn = [i for i in items if i.status == "warn"]
    off = [i for i in items if i.status == "off"]
    deploy = await updates.deploy_problem()
    ok = [i for i in items if i.status == "ok"]
    lines = ["🔌 Подключения", ""]
    if warn or deploy:
        lines.append("Нужна твоя помощь:")
        lines += [f"⚠️ {esc(i.title)}: {esc(i.detail)}" for i in warn]
        if deploy:
            lines.append(f"🟠 Новая версия ({esc(deploy['sha'])}) не запустилась — работаю на прежней. "
                         f"Лог сборки — в сообщении «Новая версия бота не запустилась».")
        lines.append("")
    if ok:
        lines.append("✅ Работает: " + ", ".join(esc(i.title) for i in ok))
    if off:
        lines.append("➕ Можно подключить: " + ", ".join(esc(i.title) for i in off))
    lines.append(f"\n<i>Python {platform.python_version()}"
                 + (f" · сборка {esc(config.app_commit[:7])}" if config.app_commit else "") + "</i>")

    rows = _button_rows(warn)
    # What works has no buttons of its own — except a switch to turn it off.
    manage = [InlineKeyboardButton(text=i.manage[0], callback_data=i.manage[1]) for i in ok if i.manage]
    if manage:
        rows.append(manage)
    if warn:
        rows.append([InlineKeyboardButton(text=RECHECK, callback_data="setup:check")])
    if integrations.deploy_hooks() or docker_api.docker_available():
        rows.append([InlineKeyboardButton(text="🤖 Что бот делает сам", callback_data="auto:menu")])
    if off:
        rows.append([InlineKeyboardButton(text=f"➕ Подключить ещё ({len(off)})", callback_data="setup:more")])
    rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "setup:more")
async def cb_more(call: CallbackQuery):
    """Optional integrations — each with what it gives and one button."""
    await ack(call)
    off = [i for i in await build_items() if i.status == "off"]
    if not off:
        await render(call, "Всё уже подключено.", back_button("← К подключениям", "setup:check"))
        return
    lines = ["➕ Можно подключить — всё необязательно:\n"]
    lines += [f"• {esc(i.title)} — {esc(i.detail)}" for i in off]
    rows = _button_rows(off)
    rows.append([InlineKeyboardButton(text="← К подключениям", callback_data="setup:check")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


async def show_checklist(target):
    text, kb = await render_checklist()
    await render(target, text, kb)


@router.callback_query(F.data.in_({"menu_diag", "setup:check"}))
async def cb_checklist(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.clear()
    await render(call, "▱▱▱ Проверяю сам себя...")
    await show_checklist(call)


# ── Will an alert actually ring? ─────────────────────────────────────────────
#
# The bot cannot see a muted chat or a phone in «Не беспокоить» — and then
# a fallen site rings nobody. The test comes in a minute: Telegram keeps the
# phone quiet while you sit in it on any device, so a test sent right after
# the tap would not ring even when everything is fine.

RING_DELAY_S = 60
RING_TEST = ("🔴 Пробная тревога\n"
             "Так придёт сообщение, если сайт ляжет: со звуком, даже ночью и когда остальное "
             "приглушено.\n\nТелефон зазвонил?")
NO_RING_HELP = ("🔕 Тревога пришла без звука. Что обычно мешает:\n\n"
                "1. Звук выключен у этого чата: нажми моё имя вверху чата → «Уведомления» → включить.\n"
                "2. Уведомления Telegram на телефоне: настройки Telegram → «Уведомления и звуки» → "
                "«Личные чаты» включены.\n"
                "3. Режим «Не беспокоить» или «Фокусирование» на телефоне глушит всё — добавь Telegram "
                "в разрешённые приложения.\n"
                "4. Открыт Telegram на компьютере: пока ты там, телефон молчит. Это нормально — "
                "уйдёшь, и тревоги снова будут звонить на телефоне.\n\n"
                "Поправь и попробуй ещё раз.")
_ring_tasks: set[asyncio.Task] = set()


def _ring_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Да, зазвонил", callback_data="ring:yes"),
        InlineKeyboardButton(text="🔕 Нет", callback_data="ring:no")]])


async def _ring_later():
    await asyncio.sleep(RING_DELAY_S)
    await notifier.send(RING_TEST, Priority.CRITICAL, reply_markup=_ring_kb(), always_ring=True)


def schedule_ring_test() -> asyncio.Task:
    task = asyncio.get_running_loop().create_task(_ring_later())
    _ring_tasks.add(task)
    task.add_done_callback(_ring_tasks.discard)
    return task


@router.callback_query(F.data == "ring:test")
async def cb_test_alert(call: CallbackQuery):
    schedule_ring_test()
    await ack(call, "Через минуту пришлю пробную тревогу. Сверни Telegram везде — на телефоне и на "
                    "компьютере — и жди звонка.", show_alert=True)


@router.callback_query(F.data == "ring:yes")
async def cb_ring_yes(call: CallbackQuery):
    await ack(call)
    await set_state("ring_ok", "1")
    await render(call, "🔴 Пробная тревога\n✅ Звонит: если сайт ляжет, ты узнаешь сразу.")


@router.callback_query(F.data == "ring:no")
async def cb_ring_no(call: CallbackQuery):
    await ack(call)
    await render(call, NO_RING_HELP, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔔 Ещё раз через минуту", callback_data="ring:test")]]))


async def last_steps() -> tuple[str, InlineKeyboardMarkup] | None:
    """After the first site: what makes the bot's silence trustworthy — an
    alert that rings and a watchdog for the bot itself. None when both are
    done."""
    steps, rows = [], []
    if not await get_state("ring_ok"):
        steps.append("🔔 Пробная тревога — проверим, что телефон зазвонит, если сайт ляжет. Чаще всего "
                     "тревогу пропускают из-за выключенного звука у чата.")
        rows.append([InlineKeyboardButton(text="🔔 Пробная тревога", callback_data="ring:test")])
    if not integrations.self_heartbeat_url():
        steps.append("🛡 Сторож — если я остановлюсь, зависну или потеряю связь с Telegram, тебе напишет "
                     "healthchecks.io. Бесплатно, 2 минуты.")
        rows.append([InlineKeyboardButton(text="🛡 Подключить сторожа", callback_data="onb:watchdog")])
    if await railway.asks():
        steps.append(railway.STEP)
        rows += railway.keyboard("railway:paid:onb").inline_keyboard
    if not steps:
        return None
    count = {1: "Ещё один шаг", 2: "Ещё два шага"}.get(len(steps), "Ещё три шага")
    head = f"{count}, чтобы моё молчание точно значило «всё в порядке»:"
    return "\n\n".join([head, *(f"{i}. {step}" for i, step in enumerate(steps, 1))]), \
        InlineKeyboardMarkup(inline_keyboard=rows)


# ── A key without its wizard ─────────────────────────────────────────────────

_URL_SECRET = re.compile(r"https://\S*(deploy_hooks/|hc-ping\.com/|/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-)")


def looks_like_key(text: str | None) -> bool:
    """Does this message look like what the wizards ask for — a token (one
    long word of letters AND digits), a service-account JSON, a deploy hook
    or a watchdog address? The checks are the wizards' own patterns
    (yandex / crux / cloudflare / github tokens, gsc key, cf_hook,
    watchdog_url), narrowed so that an ordinary message never matches."""
    t = (text or "").strip()
    if "private_key" in t and "client_email" in t:
        return True
    if _URL_SECRET.search(t):
        return True
    return bool(re.fullmatch(r"[A-Za-z0-9_\-.]{20,255}", t) and re.search(r"\d", t) and re.search(r"[A-Za-z]", t))


@router.message(F.text, StateFilter(None), F.text.func(looks_like_key))
async def msg_key_without_wizard(message: Message):
    """The wizard's state lives in memory: a restart between «Пришли токен»
    and the token leaves the token in the chat with no handler to delete
    it (SEC-010). A key-like message outside any wizard is removed the same
    way, and the owner is told to start the wizard again."""
    await forget_secret(message)
    await message.answer("⌛ Мастер прервался после перезапуска — начни заново: ⚙️ Настройки → 🔌 Подключения.",
                         reply_markup=back_button("← К подключениям", "setup:check"))


# ── One-tap fixes ────────────────────────────────────────────────────────────

@router.callback_query(F.data == "setup:sp_on")
async def cb_status_page_on(call: CallbackQuery):
    """Not one tap: the same question as in ⚙️ — what becomes public, and a
    secret link; the screen after publishing leads back here."""
    await ack(call)
    await render(call, *await publish_screen(back="setup:check"))


def _switch_source(key: str, env_name: str, env_value: bool) -> str:
    """Where the current on/off comes from — the owner must see that a tap
    here overrides .env."""
    env = f"{env_name}={'1' if env_value else '0'}"
    if integrations.stored().get(key) in ("on", "off"):
        return f"задано здесь, в чате (в .env {env})"
    return f"из .env ({env})"


def second_opinion_screen() -> tuple[str, InlineKeyboardMarkup]:
    """What goes to check-host.net, the switches and where each comes from:
    turning the service on is a choice made on this screen, not one tap."""
    so_on = integrations.second_opinion_enabled()
    rs_on = integrations.region_speed_enabled()
    text = ("🌐 Вторая точка проверки — бесплатный сервис check-host.net\n\n"
            "• Перепроверка перед тревогой «сайт лёг»: адрес сайта уходит на check-host.net, его "
            "серверы в других странах пробуют открыть сайт. Открывается у них — значит, сбой у меня, "
            "и я тебя не бужу. Туда же уходят почтовые серверы домена — проверить порт 25.\n"
            "• Скорость из региона: раз в 6 часов адрес главной каждого сайта уходит туда же — для "
            "строки «из России, 0.8 с» в карточке.\n\n"
            "Внутренние адреса (192.168.x, 10.x, .local, .lan) туда не уходят никогда.\n\n"
            f"Перепроверка: {'включена' if so_on else 'выключена'} — "
            f"{esc(_switch_source('second_opinion', 'SECOND_OPINION', config.second_opinion))}.\n"
            f"Скорость из региона: {'включена' if rs_on else 'выключена'} — "
            f"{esc(_switch_source('region_speed', 'REGION_SPEED', config.region_speed))}.")
    rows = [[InlineKeyboardButton(text="🌐 Перепроверку выключить" if so_on else "🌐 Перепроверку включить",
                                  callback_data="setup:so:off" if so_on else "setup:so:on")],
            [InlineKeyboardButton(text="⏱ Скорость из региона выключить" if rs_on
                                  else "⏱ Скорость из региона включить",
                                  callback_data="setup:rs:off" if rs_on else "setup:rs:on")],
            [InlineKeyboardButton(text="← К подключениям", callback_data="setup:check")]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "setup:so")
async def cb_second_opinion_screen(call: CallbackQuery):
    await ack(call)
    await render(call, *second_opinion_screen())


@router.callback_query(F.data.startswith(("setup:so:", "setup:rs:")))
async def cb_second_opinion(call: CallbackQuery):
    key, env_value = (("second_opinion", config.second_opinion) if call.data.startswith("setup:so:")
                      else ("region_speed", config.region_speed))
    on = call.data.endswith(":on")
    # Switching the service ON is consent: a tap that waited in the queue
    # while the bot was down does not give it (handlers.common.ack).
    fresh = await ack(call, "Включено" if on else "Выключено")
    if on and not fresh:
        await call.message.answer(STALE_TAP)
        return
    # The same as .env says → forget the chat value, so .env is the master again.
    await integrations.set_value(key, None if on == env_value else ("on" if on else "off"))
    await render(call, *second_opinion_screen())


@router.callback_query(F.data == "setup:hb_regen")
async def cb_regen_heartbeat_ask(call: CallbackQuery):
    """Rotation is always one tap away — and never one tap long: every job's
    address derives from the one secret (services.secrets), so every task
    and bot that marks in with its old address goes silent until updated."""
    await ack(call)
    jobs = list(settings.heartbeat_jobs())
    who = (f"Перестанут выходить на связь: {esc(', '.join(jobs))} — пока не вставишь им новые ссылки "
           f"(покажу их сразу)." if jobs else "Задач и ботов пока нет — ссылки для новых будут с новым ключом.")
    await render(call, "🔑 Выпустить новый ключ ссылок для задач?\n\n"
                       f"{who} Делай это, если ссылка могла попасть в чужие руки — например, лежала в файле "
                       "репозитория.",
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text="🔑 Да, выпустить", callback_data="setup:hb_regen:ok"),
                      InlineKeyboardButton(text="← Назад", callback_data="setup:check")]]))


@router.callback_query(F.data == "setup:hb_regen:ok")
async def cb_regen_heartbeat(call: CallbackQuery):
    if not await ack(call, "Готово"):
        await call.message.answer(STALE_TAP)
        return
    await secrets.regenerate("heartbeat")
    jobs = list(settings.heartbeat_jobs()) or ["backup"]
    links = "\n".join(f"{esc(job)}:\n<code>curl -fsS {esc(public_urls.heartbeat_url(job))}</code>" for job in jobs)
    note = ("\n\nHEARTBEAT_SECRET в окружении больше не используется — удали переменную."
            if config.heartbeat_secret_env else "")
    await render(call, f"🔑 Новый ключ выпущен. Вставь каждой задаче и боту её новую ссылку (они есть и в "
                       f"карточке задачи, «🔗 Как подключена»):\n{links}\n\n{HEARTBEAT_SECRET_NOTE}{note}",
                 back_button("← К подключениям", "setup:check"))


# ── «🗑 Отключить» asks first ────────────────────────────────────────────────
#
# One tap turns nothing off: the question names what goes, says when .env
# carries the value too (it is hidden then — «Отключено» must be true), and
# «🗑 Да, отключить» does it under the stale-tap rule of handlers.common.ack.

_OFF = {"watchdog": (("self_heartbeat_url",), "ссылку внешнего сторожа"),
        "yx": (("yandex_token",), "токен Яндекса"),
        "crux": (("crux_key",), "ключ Google о скорости"),
        "gsc": (("gsc_key", "gsc_property"), "ключ и ресурс Search Console"),
        "cf": (("deploy_hooks", "cf_api_token", "cf_zone_id"), "хуки, токен и Zone ID Cloudflare"),
        "gh": (("github_token", "github_owner"), "токен GitHub")}
OFF_LINE = "Отключено здесь — значение из .env не использую."


def _off_button(group: str) -> tuple[str, str] | None:
    """«🗑 Отключить» while a value is in use (from the chat or .env);
    «↩️ Брать из .env» once it is switched off over a value in .env."""
    keys = _OFF[group][0]
    sources = {integrations.source(k) for k in keys}
    if sources & {"bot", "env"}:
        return ("🗑 Отключить", f"setup:{group}_off")
    if "off" in sources and any(integrations.env_value(k) for k in keys):
        return ("↩️ Брать из .env", f"setup:env:{group}")
    return None


@router.callback_query(F.data.in_({f"setup:{g}_off" for g in _OFF}))
async def cb_off_ask(call: CallbackQuery):
    group = call.data.removeprefix("setup:").removesuffix("_off")
    keys, what = _OFF[group]
    text = f"Отключить {what}?"
    if any(integrations.env_value(k) for k in keys):
        text += ("\n\nЭто значение задано и в .env на сервере — его я тоже перестану использовать, пока "
                 "не введёшь новое здесь или не нажмёшь «↩️ Брать из .env».")
    else:
        text += " Вернуть можно только новым значением."
    if group == "watchdog":
        text += "\n\nПоставь check на паузу в healthchecks.io — иначе он пришлёт письмо «бот упал»."
    await ack(call)
    await render(call, text, InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, отключить", callback_data=f"{call.data}:ok"),
         InlineKeyboardButton(text="← Назад", callback_data=f"setup:{group}")]]))


async def _switch_off(call: CallbackQuery, group: str, toast: str) -> bool:
    """The «🗑 Да, отключить» tap: ack first — a tap that waited in the queue
    while the bot was down does nothing (STALE_TAP) — then the tombstones."""
    if not await ack(call, toast, show_alert=True):
        await call.message.answer(STALE_TAP)
        return False
    for key in _OFF[group][0]:
        await integrations.switch_off(key)
    return True


@router.callback_query(F.data.startswith("setup:env:"))
async def cb_use_env(call: CallbackQuery):
    """«↩️ Брать из .env»: the tombstones of the group go, .env is in use again."""
    group = call.data.removeprefix("setup:env:")
    if group not in _OFF:
        await ack(call)
        return
    if not await ack(call, "Снова беру из .env"):
        await call.message.answer(STALE_TAP)
        return
    for key in _OFF[group][0]:
        if integrations.source(key) == "off":
            await integrations.set_value(key, None)
    if group == "gsc":
        gsc.reset_token_cache()
    await show_checklist(call)


# ── Guides ───────────────────────────────────────────────────────────────────

def _guide_kb(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=t, callback_data=c)] for t, c in buttons]
    rows.append([InlineKeyboardButton(text="← К подключениям", callback_data="setup:check")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "setup:url")
async def cb_guide_url(call: CallbackQuery):
    await ack(call)
    if config.on_railway:
        text = ("🌍 Публичный адрес на Railway\n\n"
                "1. Открой сервис → <b>Settings → Networking → Generate Domain</b>, порт <b>8080</b>.\n"
                "2. Railway передеплоит бота, и адрес подхватится сам — переменная PUBLIC_BASE_URL "
                "не нужна.\n"
                "3. Нажми «🔄 Проверить снова».")
    else:
        text = ("🌍 Публичный адрес\n\n"
                "1. Поставь перед ботом reverse proxy с TLS (Caddy, nginx или Cloudflare Tunnel) "
                f"на 127.0.0.1:{config.webhook_port}. Голый проброс порта не подходит: секрет "
                "heartbeat пойдёт открытым текстом, а виджет на https-сайте браузер заблокирует.\n"
                "2. Пропиши в .env: <code>PUBLIC_BASE_URL=https://bot.example.com</code> "
                "и <code>TRUST_PROXY=1</code>.\n"
                "3. Перезапусти бота и нажми «🔄 Проверить снова».\n\n"
                "Пошагово — README.ru.md, раздел «Публичный адрес на своём сервере».")
    await render(call, text, _guide_kb())


@router.callback_query(F.data == "setup:docker")
async def cb_guide_docker(call: CallbackQuery):
    await ack(call)
    await render(call, ("🐳 Docker на хосте бота\n\n"
                        "Даёт автоперезапуск упавших контейнеров из списка. Очистка диска — отдельно "
                        "и по желанию: с <code>AUTO_CLEANUP=1</code> при переполнении удаляю только "
                        "висячие образы и кэш сборки старше суток, не чаще раза в 12 ч, и пишу, что "
                        "удалил. Остановленные контейнеры и образы с тегами не трогаю.\n\n"
                        "1. В docker-compose.yml раскомментируй строку\n"
                        "<code>- /var/run/docker.sock:/var/run/docker.sock</code>\n"
                        "2. В .env перечисли контейнеры: "
                        "<code>AUTORESTART_CONTAINERS=app,db</code> — кроме контейнера самого бота: "
                        "его я не перезапускаю\n"
                        "3. <code>docker compose up -d</code> и «🔄 Проверить снова».\n\n"
                        "Сокет даёт root-доступ к хосту: через него можно сделать с сервером что угодно. "
                        "Включай только на сервере, который полностью твой. Безопаснее — отфильтрованный "
                        "доступ через socket-proxy: docs/SETUP.md, «Свой сервер: доступ к Docker»."), _guide_kb())


def _watchdog_kb() -> InlineKeyboardMarkup:
    buttons = [("🔗 Вставить ссылку", "setup:watchdog_url")]
    if off := _off_button("watchdog"):
        buttons.append(off)
    return _guide_kb(*buttons)


@router.callback_query(F.data == "setup:railway")
async def cb_guide_railway(call: CallbackQuery):
    await ack(call)
    await render(call, railway.GUIDE, InlineKeyboardMarkup(inline_keyboard=[
        *railway.keyboard("railway:paid:setup").inline_keyboard, *_guide_kb().inline_keyboard]))


@router.callback_query(F.data.startswith("railway:paid"))
async def cb_railway_paid(call: CallbackQuery):
    """«✅ Уже на Hobby» — from the first-site steps (:onb), the guide
    (:setup) or the day-25 reminder (bare). Never asked again."""
    await railway.set_paid()
    await ack(call, "Понял, больше не напоминаю")
    where = call.data.removeprefix("railway:paid").lstrip(":")
    if where == "setup":
        await show_checklist(call)
    elif where == "onb" and (steps := await last_steps()):
        await render(call, *steps)
    else:
        await render(call, "✅ Понял: аккаунт Railway на Hobby — больше не напоминаю.")


@router.callback_query(F.data.in_({"setup:watchdog", "onb:watchdog"}))
async def cb_guide_watchdog(call: CallbackQuery):
    """onb: from the first-site steps — answers below, so the other step's
    button stays where it was."""
    await ack(call)
    state_line = {"bot": "Подключён, ссылка введена в боте.", "env": "Подключён через .env.",
                  "off": OFF_LINE}.get(integrations.source("self_heartbeat_url"), "Пока не подключён.")
    target = call.message if call.data == "onb:watchdog" and call.message is not None else call
    await render(target, ("🛡 Внешний сторож\n\n"
                         f"{state_line}\n\n"
                         "Бот следит за сайтами, а кто следит за ботом? healthchecks.io (бесплатно).\n\n"
                         "1. Зарегистрируйся на <a href=\"https://healthchecks.io\">healthchecks.io</a>, "
                         "создай check с периодом 5 минут.\n"
                         "2. Скопируй ссылку вида <code>https://hc-ping.com/…</code> и нажми «🔗 Вставить ссылку».\n"
                         "3. Бот будет выходить с ним на связь каждые 5 минут; замолчит — healthchecks пришлёт письмо."),
                 _watchdog_kb())


@router.callback_query(F.data == "setup:watchdog_url")
async def cb_watchdog_url(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.watchdog_url)
    await render(call, "🔗 Пришли ссылку check-а из healthchecks.io (начинается с https://hc-ping.com/).",
                 cancel_kb("setup:watchdog"))


@router.message(SetupForm.watchdog_url)
async def msg_watchdog_url(message: Message, state: FSMContext):
    await forget_secret(message)
    url = (message.text or "").strip()
    if not re.fullmatch(r"https://[^\s]{10,300}", url):
        await message.answer("Нужна https-ссылка. Попробуй ещё раз:", reply_markup=cancel_kb("setup:watchdog"))
        return
    error = await heartbeats.ping_watchdog(url)
    if error:
        # The step stays open: the next paste is answered, not left in the chat.
        await message.answer(f"❌ Сторож не на связи: {esc(error)}. Проверь, что скопировал ссылку "
                             f"целиком и check не удалён, — и пришли её ещё раз, или «✖️ Отмена».",
                             reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("self_heartbeat_url", url)
    await heartbeats.forget_watchdog()   # the old link's failures are not this one's
    await heartbeats.record_watchdog(None)
    await message.answer("✅ Сторож подключён и на связи. Дальше выхожу с ним на связь каждые 5 минут.",
                         reply_markup=back_button("← К подключениям", "setup:check"))


@router.callback_query(F.data == "setup:watchdog_off:ok")
async def cb_watchdog_off(call: CallbackQuery):
    if not await _switch_off(call, "watchdog", "Отключено. Если я раньше присылал копии базы — ссылка сторожа "
                                               "осталась в них: пересоздай check на healthchecks.io."):
        return
    await heartbeats.forget_watchdog()
    await show_checklist(call)


# ── Yandex.Webmaster wizard ──────────────────────────────────────────────────

def _yx_kb() -> InlineKeyboardMarkup:
    buttons = [("🔑 Ввести токен", "setup:yx_token")]
    if off := _off_button("yx"):
        buttons.append(off)
    return _guide_kb(*buttons)


@router.callback_query(F.data == "setup:yx")
async def cb_guide_yandex(call: CallbackQuery):
    await ack(call)
    src = integrations.source("yandex_token")
    state_line = {"bot": "Токен введён в боте.", "env": "Токен взят из .env.",
                  "off": OFF_LINE}.get(src, "Пока не подключено.")
    await render(call, ("🔎 Яндекс.Вебмастер\n\n"
                        f"{state_line}\n\n"
                        "Даёт число страниц в поиске, ИКС и тревоги о фатальных проблемах сайта.\n\n"
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
    await render(call, "🔑 Пришли OAuth-токен Яндекса одним сообщением.", cancel_kb("setup:yx"))


@router.message(SetupForm.yx_token)
async def msg_yx_token(message: Message, state: FSMContext):
    await forget_secret(message)
    token = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-.]{20,200}", token):
        await message.answer("Не похоже на токен. Попробуй ещё раз:", reply_markup=cancel_kb("setup:yx"))
        return
    previous = integrations.stored().get("yandex_token")
    was_off = integrations.source("yandex_token") == "off"
    await integrations.set_value("yandex_token", token)
    answer = await yandex_webmaster.check_token()
    if answer is Probe.REJECTED:
        await integrations.set_value("yandex_token", previous)
        if was_off:
            await integrations.switch_off("yandex_token")
        await message.answer("❌ Яндекс не принял токен (401/403): проверь скоуп webmaster:read и что токен "
                             "не истёк — и пришли другой, или «✖️ Отмена».", reply_markup=cancel_kb())
        return
    await state.clear()
    if answer is Probe.UNREACHABLE:
        await message.answer(UNCHECKED_NOTE.format(service="Яндекс"),
                             reply_markup=back_button("← К подключениям", "setup:check"))
        return
    hosts = await _yandex_probe()
    counted = f": верифицированных хостов {hosts}" if hosts is not None else ""
    await message.answer(f"✅ Яндекс.Вебмастер подключён{counted}.",
                         reply_markup=back_button("← К подключениям", "setup:check"))


@router.callback_query(F.data == "setup:yx_off:ok")
async def cb_yx_off(call: CallbackQuery):
    if await _switch_off(call, "yx", "Отключено. " + OLD_COPIES_NOTE):
        await show_checklist(call)


# ── Chrome UX Report wizard ──────────────────────────────────────────────────

def _crux_kb() -> InlineKeyboardMarkup:
    buttons = [("🔑 Ввести ключ", "setup:crux_key")]
    if off := _off_button("crux"):
        buttons.append(off)
    return _guide_kb(*buttons)


@router.callback_query(F.data == "setup:crux")
async def cb_guide_crux(call: CallbackQuery):
    await ack(call)
    src = integrations.source("crux_key")
    state_line = {"bot": "Ключ введён в боте.", "env": "Ключ взят из .env.",
                  "off": OFF_LINE}.get(src, "Пока не подключено.")
    await render(call, ("⚡ Скорость у посетителей Chrome\n\n"
                        f"{state_line}\n\n"
                        "Google собирает, сколько реальные посетители в Chrome ждут каждый сайт, и отдаёт эти "
                        "числа бесплатно. На сайты ничего ставить не нужно. У небольших сайтов данных часто нет: "
                        "Google считает только сайты, у которых достаточно посетителей в Chrome.\n\n"
                        "1. Открой <a href=\"https://console.cloud.google.com/apis/library/"
                        "chromeuxreport.googleapis.com\">Chrome UX Report API</a> в Google Cloud (войди, создай "
                        "проект, если спросит) и нажми <b>Enable</b>.\n"
                        "2. Там же: <b>Credentials → Create credentials → API key</b>. Скопируй ключ.\n"
                        "3. Нажми «🔑 Ввести ключ» и пришли его сюда — я сразу проверю живым запросом.\n\n"
                        "Ключ бесплатный и даёт доступ только к этим общедоступным числам."),
                 _crux_kb())


@router.callback_query(F.data == "setup:crux_key")
async def cb_crux_key(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.crux_key)
    await render(call, "🔑 Пришли ключ Google API одним сообщением.", cancel_kb("setup:crux"))


@router.message(SetupForm.crux_key)
async def msg_crux_key(message: Message, state: FSMContext):
    await forget_secret(message)
    key = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,60}", key):
        await message.answer("Не похоже на ключ Google API (он вида AIza…). Попробуй ещё раз:",
                             reply_markup=cancel_kb("setup:crux"))
        return
    answer = await crux.check_key(key)
    if answer is Probe.REJECTED:
        await message.answer("❌ Google не принял ключ. Проверь, что Chrome UX Report API включён в том же "
                             "проекте, где создан ключ, и что ключ скопирован целиком, — и пришли другой, "
                             "или «✖️ Отмена».", reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("crux_key", key)
    rerun_visitors_soon()
    if answer is Probe.UNREACHABLE:
        await message.answer(UNCHECKED_NOTE.format(service="Google"),
                             reply_markup=back_button("← К подключениям", "setup:check"))
        return
    await message.answer("✅ Данные Google о скорости подключены. Спрошу про твои сайты в ближайшие минуты, "
                         "дальше — каждое утро; числа появятся на карточках.",
                         reply_markup=back_button("← К подключениям", "setup:check"))


@router.callback_query(F.data == "setup:crux_off:ok")
async def cb_crux_off(call: CallbackQuery):
    if await _switch_off(call, "crux", "Отключено. " + OLD_COPIES_NOTE):
        await show_checklist(call)


# ── Google Search Console wizard ─────────────────────────────────────────────

def _gsc_kb() -> InlineKeyboardMarkup:
    buttons = [("1️⃣ Указать ресурс", "setup:gsc_prop"), ("2️⃣ Прислать JSON-ключ", "setup:gsc_key")]
    if off := _off_button("gsc"):
        buttons.append(off)
    return _guide_kb(*buttons)


def _gsc_state_line() -> str:
    prop = integrations.gsc_property()
    creds = integrations.gsc_credentials()
    problem = integrations.gsc_key_problem()
    parts = [f"Ресурс: <code>{esc(prop)}</code>" if prop else "Ресурс не указан",
             f"ключ: {esc(creds.get('client_email', '?'))}" if creds
             else f"ключ не загружен: {esc(problem)}" if problem else "ключ не загружен"]
    if "off" in (integrations.source("gsc_key"), integrations.source("gsc_property")):
        parts.append(OFF_LINE.rstrip("."))
    return " · ".join(parts)


@router.callback_query(F.data == "setup:gsc")
async def cb_guide_gsc(call: CallbackQuery):
    await ack(call)
    await render(call, ("🔎 Google Search Console\n\n"
                        f"{_gsc_state_line()}\n\n"
                        "Даёт тревогу «главная выпала из индекса» тем же днём и клики/показы "
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
                 cancel_kb("setup:gsc"))


@router.message(SetupForm.gsc_property)
async def msg_gsc_prop(message: Message, state: FSMContext):
    prop = (message.text or "").strip()
    if not re.fullmatch(r"(sc-domain:[a-z0-9.-]+|https?://[^\s]+)", prop, re.I):
        await message.answer("Формат: <code>sc-domain:example.com</code> или <code>https://example.com/</code>",
                             reply_markup=cancel_kb("setup:gsc"))
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
                 cancel_kb("setup:gsc"))


@router.message(SetupForm.gsc_key)
async def msg_gsc_key(message: Message, state: FSMContext):
    raw = message.text or ""
    if message.document:
        if (message.document.file_size or 0) > 64 * 1024:
            await message.answer("Файл слишком большой для ключа сервис-аккаунта.", reply_markup=cancel_kb("setup:gsc"))
            return
        buf = await message.bot.download(message.document)
        raw = buf.read().decode("utf-8", errors="replace")
    await forget_secret(message)            # the file is in hand; the key must not stay in the chat
    creds = integrations.valid_gsc_key(raw)
    if not creds:
        await message.answer("Это не JSON-ключ сервис-аккаунта (нужны поля client_email и private_key).",
                             reply_markup=cancel_kb("setup:gsc"))
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
                             reply_markup=back_button("← К подключениям", "setup:check"))
    else:
        await message.answer(f"Ключ сохранён, но API пока отвечает ошибкой. Чаще всего не сделан "
                             f"шаг 3: добавь <code>{esc(creds.get('client_email', ''))}</code> "
                             f"в пользователи ресурса в Search Console и нажми «🔄 Проверить снова».",
                             reply_markup=_gsc_kb())


@router.callback_query(F.data == "setup:gsc_off:ok")
async def cb_gsc_off(call: CallbackQuery):
    if not await _switch_off(call, "gsc", "Отключено. " + OLD_COPIES_NOTE):
        return
    gsc.reset_token_cache()
    await show_checklist(call)


# ── Cloudflare wizard ────────────────────────────────────────────────────────

def _cf_kb() -> InlineKeyboardMarkup:
    buttons = [("🚀 Добавить deploy hook", "setup:cf_hook"),
               ("🧹 Токен для сброса кэша", "setup:cf_token"),
               ("🆔 Zone ID", "setup:cf_zone")]
    if integrations.deploy_hooks():
        buttons.append(("🤖 Что бот делает сам", "auto:menu"))
    if off := _off_button("cf"):
        buttons.append(off)
    return _guide_kb(*buttons)


def _cf_state_line() -> str:
    hooks = integrations.deploy_hooks()
    parts = [f"deploy hooks: {', '.join(sorted(hooks))}" if hooks else "deploy hooks: нет",
             "сброс кэша: настроен" if integrations.cf_purge_configured() else "сброс кэша: нет"]
    if hooks:
        parts.append("пересобираю сам: " + ("да, " + automation.limit_text("redeploy")
                                            if integrations.auto_enabled("redeploy") else "нет"))
    if any(integrations.source(k) == "off" for k in _OFF["cf"][0]):
        parts.append(OFF_LINE.rstrip("."))
    return " · ".join(parts)


@router.callback_query(F.data == "setup:cf")
async def cb_guide_cf(call: CallbackQuery):
    await ack(call)
    await render(call, ("☁️ Cloudflare\n\n"
                        f"{esc(_cf_state_line())}\n\n"
                        "<b>Deploy hook</b> даёт кнопку «🚀 Пересобрать сайт» в тревогах. ⚠️ Пока "
                        "пересборка не выключена, я и сам вызываю хук, когда сайт падает, — не чаще раза "
                        "в час на хук и никогда на паузе; выключить — «🤖 Что бот делает сам». Где взять: "
                        "Cloudflare Pages → проект → Settings → Builds → Deploy hooks → Add. Пришли сюда "
                        "«хост URL».\n\n"
                        "<b>Сброс кэша</b> даёт кнопку «🧹»: API-токен с правом Zone → Cache Purge "
                        "(dash.cloudflare.com → My Profile → API Tokens) и Zone ID со страницы "
                        "Overview домена."), _cf_kb())


@router.callback_query(F.data == "setup:cf_hook")
async def cb_cf_hook(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.cf_hook)
    await render(call, "🚀 Пришли хост и URL deploy hook через пробел:\n"
                       "<code>example.com https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/…</code>",
                 cancel_kb("setup:cf"))


@router.message(SetupForm.cf_hook)
async def msg_cf_hook(message: Message, state: FSMContext):
    await forget_secret(message)
    parts = re.split(r"[\s=]+", (message.text or "").strip(), maxsplit=1)
    host = host_of("https://" + parts[0]) if parts and parts[0] else ""
    url = parts[1].strip() if len(parts) == 2 else ""
    if not host or "." not in host or not url.startswith("https://"):
        await message.answer("Формат: <code>example.com https://api.cloudflare.com/…</code>",
                             reply_markup=cancel_kb("setup:cf"))
        return
    # A hook is keyed by the host of a site: one nobody opens would never fire.
    hosts = sorted({host_of(s["url"]) for s in await get_all_sites() if is_http_url(s["url"])})
    if host not in hosts:
        like = [h for h in hosts if h.endswith("." + host) or host.endswith("." + h)] or hosts
        hint = (f" У тебя есть {esc(', '.join(like[:3]))}: пришли «{esc(like[0])} URL»." if like
                else " Сначала добавь сайт, потом хук.")
        await message.answer(f"Ни один сайт не открывается по {esc(host)} — хук не сработал бы.{hint}",
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
    await render(call, "🧹 Пришли API-токен Cloudflare с правом Zone → Cache Purge.", cancel_kb("setup:cf"))


@router.message(SetupForm.cf_token)
async def msg_cf_token(message: Message, state: FSMContext):
    await forget_secret(message)
    token = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{30,100}", token):
        await message.answer("Не похоже на API-токен Cloudflare.", reply_markup=cancel_kb("setup:cf"))
        return
    answer = await cf_token_status(token)
    if answer is Probe.REJECTED:
        await message.answer("❌ Cloudflare не принял токен: нужен API-токен с правами Zone → Zone → Read и "
                             "Zone → Cache Purge на зону сайта, скопированный целиком. Пришли другой, или "
                             "«✖️ Отмена».", reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("cf_api_token", token)
    verdict = ("✅ Токен сохранён, Cloudflare его принял." if answer is Probe.OK else
               "⚠️ Токен сохранил, но подтвердить не смог: Cloudflare не ответил (или это токен аккаунта) — "
               "проверю при первом сбросе кэша.")
    await message.answer(f"{verdict}\n{esc(_cf_state_line())}", reply_markup=_cf_kb())


@router.callback_query(F.data == "setup:cf_zone")
async def cb_cf_zone(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.cf_zone)
    await render(call, "🆔 Пришли Zone ID (32 hex-символа со страницы Overview домена в Cloudflare).",
                 cancel_kb("setup:cf"))


@router.message(SetupForm.cf_zone)
async def msg_cf_zone(message: Message, state: FSMContext):
    zone = (message.text or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", zone):
        await message.answer("Zone ID — это 32 hex-символа. Попробуй ещё раз:", reply_markup=cancel_kb("setup:cf"))
        return
    await state.clear()
    await integrations.set_value("cf_zone_id", zone)
    # No live check: whether the token may read this zone shows at the first purge.
    await message.answer(f"✅ Zone ID сохранён — проверю при первом сбросе кэша.\n{esc(_cf_state_line())}",
                         reply_markup=_cf_kb())


@router.callback_query(F.data == "setup:cf_off:ok")
async def cb_cf_off(call: CallbackQuery):
    if await _switch_off(call, "cf", "Отключено. " + OLD_COPIES_NOTE):
        await show_checklist(call)


# ── GitHub wizard ────────────────────────────────────────────────────────────

def _gh_kb() -> InlineKeyboardMarkup:
    buttons = [("🔑 Ввести токен", "setup:gh_token"), ("👤 Чьи репозитории", "setup:gh_owner")]
    if off := _off_button("gh"):
        buttons.append(off)
    return _guide_kb(*buttons)


def _gh_state_line() -> str:
    src = integrations.source("github_token")
    token = {"bot": "токен введён в боте", "env": "токен из .env",
             "off": "токен из .env отключён здесь"}.get(src, "токена нет")
    owner = integrations.github_owner()
    return f"{token} · репозитории: {esc(owner) if owner else 'владельца токена'}"


@router.callback_query(F.data == "setup:gh")
async def cb_guide_github(call: CallbackQuery):
    await ack(call)
    await render(call, ("🐙 GitHub\n\n"
                        f"{_gh_state_line()}\n\n"
                        "Раз в неделю, в воскресном отчёте — что прибрать в репозиториях: PR старше недели, "
                        "лишние ветки, выключенное удаление веток после мержа, красный CI на основной "
                        "ветке, репозитории, которые пропали, уязвимости от Dependabot. Только чтение; "
                        "когда всё в порядке, раздела нет.\n\n"
                        "1. <a href=\"https://github.com/settings/personal-access-tokens/new\">GitHub → "
                        "Settings → Developer settings → Fine-grained tokens → Generate new token</a>.\n"
                        "2. Repository access: <b>All repositories</b>. Permissions → Repository, всё "
                        "<b>Read-only</b>: Metadata, Contents, Pull requests, Commit statuses; по желанию "
                        "Dependabot alerts.\n"
                        "3. «🔑 Ввести токен» — пришли его сюда, я проверю живым запросом и удалю "
                        "сообщение.\n\n"
                        "Репозитории организации: выбери её в Resource owner при создании токена и "
                        "укажи её в «👤 Чьи репозитории»."), _gh_kb())


@router.callback_query(F.data == "setup:gh_token")
async def cb_gh_token(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.gh_token)
    await render(call, "🔑 Пришли токен GitHub одним сообщением (начинается с <code>github_pat_</code>).",
                 cancel_kb("setup:gh"))


@router.message(SetupForm.gh_token)
async def msg_gh_token(message: Message, state: FSMContext):
    await forget_secret(message)
    token = (message.text or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_]{30,255}", token):
        await message.answer("Не похоже на токен GitHub. Попробуй ещё раз:", reply_markup=cancel_kb("setup:gh"))
        return
    answer, login = await github.check_token(token)
    if answer is Probe.REJECTED:
        await message.answer("❌ GitHub не принял токен. Проверь, что он скопирован целиком и не истёк, — "
                             "и пришли другой, или «✖️ Отмена».", reply_markup=cancel_kb())
        return
    await state.clear()
    await integrations.set_value("github_token", token)
    if answer is Probe.UNREACHABLE:
        await message.answer(UNCHECKED_NOTE.format(service="GitHub"),
                             reply_markup=back_button("← К подключениям", "setup:check"))
        return
    await message.answer(f"✅ GitHub подключён: аккаунт {esc(login)}. Первый раздел придёт в воскресном "
                         f"отчёте — если будет что прибрать.",
                         reply_markup=back_button("← К подключениям", "setup:check"))


@router.callback_query(F.data == "setup:gh_owner")
async def cb_gh_owner(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(SetupForm.gh_owner)
    await render(call, "👤 Пришли логин пользователя или организации, чьи репозитории смотреть. "
                       "<code>-</code> — репозитории владельца токена.", cancel_kb("setup:gh"))


@router.message(SetupForm.gh_owner)
async def msg_gh_owner(message: Message, state: FSMContext):
    owner = (message.text or "").strip().lstrip("@")
    if owner != "-" and not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", owner):
        await message.answer("Логин GitHub — латиница, цифры и дефис, до 39 символов.", reply_markup=cancel_kb("setup:gh"))
        return
    await state.clear()
    await integrations.set_value("github_owner", None if owner == "-" else owner)
    await message.answer(f"✅ Сохранено: {_gh_state_line()}", reply_markup=_gh_kb())


@router.callback_query(F.data == "setup:gh_off:ok")
async def cb_gh_off(call: CallbackQuery):
    if await _switch_off(call, "gh", "Отключено. " + OLD_COPIES_NOTE):
        await show_checklist(call)
