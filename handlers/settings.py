"""⚙️ Settings: the hub for everything that is not «is it OK» — «🔔 Когда
писать» (digests, quiet hours, mute, planned work, the test alert),
«👥 Помощники», «🔌 Подключения», «🌍 Для посетителей» (status page,
feedback widget) and «🕐 Часовой пояс». What is watched — sites, tasks
and bots — is on the main screen, not here."""

import secrets as _secrets
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import config
from db.database import count_feedback, get_all_sites
from handlers.blocks import backup_state
from handlers.common import STALE_TAP, ack, back_button, cancel_kb, cb_int, render
from handlers.menu import mute_until_local
from monitors import region
from reports.scheduler import reschedule_all
from services import (
    automation,
    github,
    gsc,
    helpers,
    integrations,
    maintenance,
    notifier,
    public_urls,
    runtime,
    settings,
    yandex_webmaster,
)
from utils.clock import now_local, to_local
from utils.i18n import plural, t
from utils.text import esc
from utils.urls import site_label
from web import status_page


class TzForm(StatesGroup):
    key = State()


TIMEZONES = [
    ("Москва", "Europe/Moscow"), ("Калининград", "Europe/Kaliningrad"), ("Самара", "Europe/Samara"),
    ("Екатеринбург", "Asia/Yekaterinburg"), ("Новосибирск", "Asia/Novosibirsk"),
    ("Красноярск", "Asia/Krasnoyarsk"), ("Иркутск", "Asia/Irkutsk"), ("Владивосток", "Asia/Vladivostok"),
    ("Киев", "Europe/Kyiv"), ("Минск", "Europe/Minsk"), ("Алматы", "Asia/Almaty"), ("Тбилиси", "Asia/Tbilisi"),
    ("Ереван", "Asia/Yerevan"), ("Берлин", "Europe/Berlin"), ("Лондон", "Europe/London"),
    ("Нью-Йорк", "America/New_York"), ("UTC", "UTC"),
]

router = Router(name="settings")


async def paused_sites() -> list[tuple[int, str]]:
    """(id, label) of the sites on a «🔧 Я чиню» pause right now."""
    out = []
    for s in await get_all_sites():
        if await maintenance.paused_until(s["id"]):
            out.append((s["id"], site_label(s["url"])))
    return out


async def silence_summary() -> str:
    """One line for the hub: everything that keeps the bot quiet right now
    — the five silences in one place (the «🔕 Тишина» screen has the buttons)."""
    quiet, mute = settings.quiet_hours(), await mute_until_local()
    parts = [t("молчу до {mute}").format(mute=mute)] if mute else []
    parts.append(t("тихие часы {hours}").format(hours=f"{quiet[0]}–{quiet[1]}") if quiet else t("тихих часов нет"))
    n = len(maintenance.windows())
    if n:
        parts.append(t("{n} {windows} плановых работ").format(n=n, windows=plural(n, "окно", "окна", "окон")))
    paused = await paused_sites()
    if paused:
        parts.append(t("чинится: {sites}").format(sites=", ".join(label for _, label in paused)))
    if await automation.paused_until():
        parts.append(t("автодействия остановлены"))
    return " · ".join(parts)


async def hub_text() -> str:
    """Seven questions, each answered in one line: when to write, what is
    silent, who else hears it, what is connected, what visitors see, is
    there a copy, which clock."""
    ring = t("только «сайт лёг»") if settings.ring_only_down() else t("всё критичное")
    write = t("сводка {morning_hour}:00 · будить: {ring}").format(morning_hour=settings.morning_hour(), ring=ring)
    silence = await silence_summary()
    # From what is configured, no probes: the hub must open at once; the
    # «🔌 Подключения» screen checks each one live.
    known = [(t("сторож"), bool(integrations.self_heartbeat_url())), ("Google", gsc.available()),
             (t("Яндекс"), yandex_webmaster.available()),
             ("Cloudflare", bool(integrations.cf_api_token() or integrations.deploy_hooks())),
             ("GitHub", github.available())]
    # A key the service refused, or a day without one answer, is not «✅»:
    # the record is kept by every call (services.integrations), no probe here.
    broken = [name for name, key in (("Google", "gsc"), (t("Яндекс"), "yandex"), ("GitHub", "github"))
              if dict(known)[name] and await integrations.broken(key)]
    on = [name for name, ok in known if ok and name not in broken]
    off = [name for name, ok in known if not ok]
    connections = ((f"{', '.join(on)} ✅" if on else t("ничего не подключено"))
                   + "".join(t(" · {name} ⚠️ не отвечает").format(name=name) for name in broken)
                   + (t(" · можно: {items}").format(items=', '.join(off)) if off else ""))
    sp = t("статус-страница вкл.") if settings.status_page_enabled() else t("статус-страница выкл.")
    feedback = await count_feedback()
    visitors = sp + (t(" · сообщений с сайтов: {feedback}").format(feedback=feedback) if feedback else "")
    helpers_now = ", ".join(h["name"] for h in await helpers.helpers()) or t("нет")
    backup = backup_state()[1] + (t(" · по воскресеньям в личку") if settings.weekly_backup() else "")
    return (t("⚙️ Настройки\n\n🔔 Когда писать — {write}\n🔕 Тишина — {silence}\n👥 Помощники — {helpers_now}\n🔌 Подключения — {connections}\n🌍 Для посетителей — {visitors}\n💾 Копия базы — {backup}\n🕐 Часовой пояс — {timezone}, сейчас {time}").format(write=esc(write), silence=esc(silence), helpers_now=esc(helpers_now), connections=esc(connections), visitors=esc(visitors), backup=esc(backup), timezone=esc(settings.timezone()), time=now_local().strftime('%H:%M')))


def hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("🔔 Когда писать"), callback_data="menu_reports"),
         InlineKeyboardButton(text=t("🔕 Тишина"), callback_data="menu_quiet")],
        [InlineKeyboardButton(text=t("👥 Помощники"), callback_data="menu_helpers"),
         InlineKeyboardButton(text=t("🔌 Подключения"), callback_data="menu_diag")],
        [InlineKeyboardButton(text=t("🌍 Для посетителей"), callback_data="menu_visitors"),
         InlineKeyboardButton(text=t("💾 Копия базы"), callback_data="menu_backup")],
        [InlineKeyboardButton(text=t("🕐 Часовой пояс"), callback_data="menu_tz"),
         InlineKeyboardButton(text=t("🏠 Главная"), callback_data="menu_main")]])


async def show_hub(target):
    await render(target, await hub_text(), hub_kb())


@router.callback_query(F.data == "menu_settings")
async def cb_settings(call: CallbackQuery):
    await ack(call)
    await show_hub(call)


# ── Helpers ──────────────────────────────────────────────────────────────────

async def helpers_screen() -> tuple[str, InlineKeyboardMarkup]:
    items = await helpers.helpers()
    lines = [t("👥 Помощники"), ""]
    if items:
        lines.append(t("«Сайт лёг» и «снова работает» приходят тебе и:"))
        lines += [f"• {esc(h['name'])}" for h in items]
    else:
        lines.append(t("«Сайт лёг» и «снова работает» сейчас приходят только тебе. Если ты в отпуске или сел телефон, о лежащем сайте никто не узнает."))
    lines += ["", t("Помощник получает только эти два сообщения (и первое напоминание, пока сайт лежит), текстом без кнопок, и не может управлять ботом. Уйти сам может командой /stop.")]
    rows = [[InlineKeyboardButton(text=t("🗑 Убрать {name}").format(name=h['name']), callback_data=f"helper_rm:{h['chat_id']}")]
            for h in items]
    if len(items) < helpers.MAX_HELPERS:
        rows.append([InlineKeyboardButton(text=t("🔗 Пригласить помощника"), callback_data="helper_invite")])
    rows.append([InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_helpers")
async def cb_helpers(call: CallbackQuery):
    await ack(call)
    await render(call, *await helpers_screen())


async def _show_invite(call: CallbackQuery, token: str, until: datetime):
    payload = helpers.PAYLOAD_PREFIX + token
    me = runtime.bot_username()
    link = f"https://t.me/{me}?start={payload}" if me else f"/start {payload}"
    await render(call, t("🔗 Перешли эту ссылку тому, кто подстрахует тебя:\n\n{link}\n\nОн откроет её и нажмёт «Start» — и будет получать «сайт лёг» и «снова работает». Ссылка сработает один раз и живёт сутки — до {time}.").format(link=esc(link), time=to_local(until).strftime('%d.%m %H:%M')),
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text=t("🔄 Новая ссылка (прежняя перестанет работать)"),
                                           callback_data="helper_invite_new")],
                     [InlineKeyboardButton(text=t("← Помощники"), callback_data="menu_helpers")]]))


@router.callback_query(F.data == "helper_invite")
async def cb_helper_invite(call: CallbackQuery):
    """A second look at the screen shows the link given before, while it works."""
    await ack(call)
    await _show_invite(call, *await helpers.live_or_new_invite())


@router.callback_query(F.data == "helper_invite_new")
async def cb_helper_invite_new(call: CallbackQuery):
    if not await ack(call, t("Прежняя ссылка больше не сработает")):   # a stale tap kills no link
        await call.message.answer(t(STALE_TAP))
        return
    await _show_invite(call, *await helpers.new_invite())


@router.callback_query(F.data.startswith("helper_rm:"))
async def cb_helper_remove(call: CallbackQuery):
    """Two steps: removing a person is not one stray tap."""
    await ack(call)
    chat_id = call.data.split(":", 1)[1]
    who = next((h for h in await helpers.helpers() if h["chat_id"] == chat_id), None)
    if not who:
        await render(call, *await helpers_screen())
        return
    await render(call, t("Убрать {name}? «Сайт лёг» и «снова работает» перестанут ему приходить, а ему придёт сообщение, что ты его убрал.").format(name=esc(who['name'])),
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text=t("🗑 Да, убрать"), callback_data=f"helper_rm_ok:{chat_id}")],
                     [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_helpers")]]))


@router.callback_query(F.data.startswith("helper_rm_ok:"))
async def cb_helper_remove_ok(call: CallbackQuery):
    if not await ack(call):                     # a tap from the queue after a restart removes nobody
        await call.message.answer(t(STALE_TAP))
        return
    gone = await helpers.remove(call.data.split(":", 1)[1])
    if gone:
        await notifier.send_to(gone["chat_id"], t("Владелец убрал тебя из помощников — тревоги больше не приходят."))
    text, kb = await helpers_screen()
    await render(call, f"{t("Убрал: ") + esc(gone['name']) if gone else t("Его уже нет")}.\n\n{text}", kb)


# ── Digests and quiet hours ──────────────────────────────────────────────────

# What the screen offers is what a button may set: a forged or stale
# «set_m:99» must not reach the scheduler (one bad hour stops every start).
MORNING_HOURS, EVENING_HOURS, WEEKLY_HOURS = (7, 8, 9, 10), (20, 21, 22), (10, 11, 12, 18)
QUIET_CHOICES = ("23-8", "0-7")


def _offered(value: str, hours: tuple[int, ...]) -> int | None:
    return int(value) if value in {str(h) for h in hours} else None


def _mark(on: bool, text: str) -> str:
    """✅ before the value that is current — every row says what is chosen."""
    return f"✅ {text}" if on else text


def _reports_kb() -> InlineKeyboardMarkup:
    ring_on = settings.ring_only_down()
    morning, evening, weekly = settings.morning_hour(), settings.evening_hour(), settings.weekly_hour()
    return InlineKeyboardMarkup(inline_keyboard=[
        # Two buttons, the current one marked — not one whose label is the state and whose tap is the opposite.
        [InlineKeyboardButton(text=_mark(not ring_on, t("🔔 Будить: всё критичное")), callback_data="set_ring:off"),
         InlineKeyboardButton(text=_mark(ring_on, t("🔔 Только «сайт лёг»")), callback_data="set_ring:on")],
        [InlineKeyboardButton(text=_mark(h == morning, f"🌅 {h}:00"), callback_data=f"set_m:{h}") for h in MORNING_HOURS],
        # The morning view: what is wrong and a count of the sites in order,
        # or the old line per site — the current one is marked.
        [InlineKeyboardButton(text=_mark(not settings.digest_full(), t("🌅 Коротко")), callback_data="set_dg:short"),
         InlineKeyboardButton(text=_mark(settings.digest_full(), t("🌅 Каждый сайт")), callback_data="set_dg:full")],
        [InlineKeyboardButton(text=_mark(h == evening, f"🌙 {h}:00"), callback_data=f"set_e:{h}") for h in EVENING_HOURS]
        + [InlineKeyboardButton(text=_mark(evening is None, t("🌙 выкл")), callback_data="set_e:off")],
        [InlineKeyboardButton(text=_mark(h == weekly, t("📊 вс {h}:00").format(h=h)), callback_data=f"set_w:{h}") for h in WEEKLY_HOURS],
        # The test that the phone actually rings lives with «when».
        [InlineKeyboardButton(text=t("🔔 Пробная тревога"), callback_data="ring:test")],
        [InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")]])


def _reports_text() -> str:
    evening = settings.evening_hour()
    return (t("🔔 Когда писать\n\n🌅 Утренняя сводка: {morning_hour}:00 — {words}.\n🌙 Вечерняя: {words2}.\n📊 Недельная: воскресенье {weekly_hour}:00 — простой за неделю и месяц, строки скорости только для сайтов, где она менялась, одна идея.\n🔔 Будить: {words5}.\n\nТихие часы, «помолчать» и плановые работы — на экране «🔕 Тишина»; копия базы — «💾 Копия базы».\n\nТапни, чтобы изменить:").format(morning_hour=settings.morning_hour(), words=t("строка на каждый сайт") if settings.digest_full() else t("только то, что не так, и число сайтов в порядке"), words2=t("выключена") if evening is None else t("{evening}:00, только если появилось новое после утренней сводки или что-то лежит (🔴)").format(evening=evening), weekly_hour=settings.weekly_hour(), words5=t("только когда сайт лёг — остальное, даже критичное, ждёт тихие часы") if settings.ring_only_down() else t("всё критичное: сайт лёг, смена NS-серверов, сайт закрыт от поиска")))


# ── Silence: the five ways the bot keeps quiet, on one screen ────────────────

async def quiet_text() -> str:
    quiet, mute = settings.quiet_hours(), await mute_until_local()
    lines = [t("🔕 Тишина"), ""]
    lines.append(t("🔕 Помолчать: {state} — всё, кроме звонящего, ждёт; снять можно в любой момент.").format(
        state=t("молчу до {mute}").format(mute=mute) if mute else t("не молчу")))
    lines.append(t("🌙 Тихие часы: {state} — некритичное ждёт до утра, «сайт лежит» приходит всегда.").format(
        state=f"{quiet[0]}–{quiet[1]}" if quiet else t("выключены")))
    n = len(maintenance.windows())
    lines.append(t("🗓 Плановые работы: {state} — в это время не пишу о падениях и не напоминаю.").format(
        state=t("{n} {windows}").format(n=n, windows=plural(n, "окно", "окна", "окон")) if n else t("нет окон")))
    paused = await paused_sites()
    lines.append(t("🔧 Я чиню: {state} — про такой сайт не пишу, проверки идут; ставится на карточке сайта и в тревоге.").format(
        state=", ".join(label for _, label in paused) if paused else t("сейчас никто")))
    if await automation.paused_until():
        lines.append(t("⏸ Автодействия остановлены: не пересобираю и не перезапускаю сам."))
    lines += ["", t("Тапни, чтобы изменить:")]
    return "\n".join(lines)


async def quiet_kb() -> InlineKeyboardMarkup:
    quiet, mute = settings.quiet_hours(), await mute_until_local()
    quiet_now = f"{quiet[0]}-{quiet[1]}" if quiet else None
    rows = [[InlineKeyboardButton(text=t("🔔 Снять тишину") if mute else t("🔕 Помолчать…"),
                                  callback_data="mute:off" if mute else "menu_mute")],
            [InlineKeyboardButton(text=_mark(q == quiet_now, f"🌙 {q.replace('-', '–')}"), callback_data=f"set_q:{q}")
             for q in QUIET_CHOICES]
            + [InlineKeyboardButton(text=_mark(quiet is None, t("🌙 выкл")), callback_data="set_q:off")],
            [InlineKeyboardButton(text=t("🗓 Плановые работы"), callback_data="menu_maint")]]
    for sid, label in await paused_sites():
        rows.append([InlineKeyboardButton(text=t("✅ Починил {label}, пиши снова").format(label=label)[:60],
                                          callback_data=f"pause:{sid}:off")])
    if await automation.paused_until():
        rows.append([InlineKeyboardButton(text=t("▶️ Автодействия снова"), callback_data="auto:resume")])
    rows.append([InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_quiet")
async def cb_quiet(call: CallbackQuery):
    await ack(call)
    await render(call, await quiet_text(), await quiet_kb())


# ── The database copy ────────────────────────────────────────────────────────

def backup_text() -> str:
    weekly = settings.weekly_backup()
    return "\n".join([
        t("💾 Копия базы"), "", backup_state()[2], "",
        t("В копии — сайты, настройки, история проверок, задачи и боты; ключей подключений и секретов ссылок в ней нет."),
        t("Восстановить из копии — пришли мне файл .db прямо в этот чат: скажу, что изменится, и заменю базу только после твоего «да». Прежняя база сохранится рядом."),
        "", t("🗄 По воскресеньям в личку: {state}.").format(state=t("присылаю") if weekly else t("не присылаю"))])


def backup_kb() -> InlineKeyboardMarkup:
    weekly = settings.weekly_backup()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("💾 Скачать копию базы"), callback_data="sys:backup")],
        [InlineKeyboardButton(text=_mark(weekly, t("🗄 По воскресеньям: присылать")), callback_data="set_wb:on"),
         InlineKeyboardButton(text=_mark(not weekly, t("🗄 не присылать")), callback_data="set_wb:off")],
        [InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")]])


@router.callback_query(F.data == "menu_backup")
async def cb_backup(call: CallbackQuery):
    await ack(call)
    await render(call, backup_text(), backup_kb())


@router.callback_query(F.data == "menu_reports")
async def cb_reports(call: CallbackQuery):
    await ack(call)
    await render(call, _reports_text(), _reports_kb())


@router.callback_query(F.data.startswith("set_"))
async def cb_settings_pick(call: CallbackQuery):
    kind, _, value = call.data.partition(":")
    if kind == "set_sp" and value == "on" and not settings.status_page_enabled():
        await ack(call)
        await render(call, *await publish_screen(back="menu_statuspage"))   # what becomes public — first
        return
    if kind == "set_m" and _offered(value, MORNING_HOURS) is not None:
        await settings.set_morning_hour(int(value))
    elif kind == "set_e" and (value == "off" or _offered(value, EVENING_HOURS) is not None):
        await settings.set_evening_hour(None if value == "off" else int(value))
    elif kind == "set_w" and _offered(value, WEEKLY_HOURS) is not None:
        await settings.set_weekly_hour(int(value))
    elif kind == "set_q" and (value == "off" or value in QUIET_CHOICES):
        await settings.set_quiet_hours(None if value == "off" else value)
    elif kind == "set_sp" and value in ("on", "off"):
        await settings.set_status_page(value == "on")
    elif kind == "set_ring" and value in ("on", "off"):
        await settings.set_ring_only_down(value == "on")
    elif kind == "set_wb" and value in ("on", "off"):
        await settings.set_weekly_backup(value == "on")
    elif kind == "set_dg" and value in ("short", "full"):
        await settings.set_digest_full(value == "full")
    elif kind == "set_tz" and cb_int(value, 0, len(TIMEZONES) - 1) is not None:
        await settings.set_timezone(TIMEZONES[int(value)][1])
    elif kind == "set_tz" and value == "env":
        await settings.set_timezone(None)
    else:
        await ack(call, t("Не понял"), show_alert=True)
        return
    failed = reschedule_all()  # apply new hours / timezone to the running scheduler now
    await ack(call, t("Сохранено, но расписание не обновилось — подробности в логе") if failed else t("Сохранено ✅"))
    # The same value again («not modified») is not an error: render() knows.
    if kind == "set_sp":
        await show_status_page(call)
    elif kind == "set_tz":
        await render(call, _tz_text(), _tz_kb())
    elif kind == "set_q":
        await render(call, await quiet_text(), await quiet_kb())
    elif kind == "set_wb":
        await render(call, backup_text(), backup_kb())
    else:
        await render(call, _reports_text(), _reports_kb())


# ── Timezone ─────────────────────────────────────────────────────────────────

def _tz_text() -> str:
    where = ""
    if integrations.region_speed_enabled():      # the same switch as run_region_checks
        country = region.country_for_timezone(settings.timezone())
        where = (t(" По нему же выбираю, откуда раз в 6 ч мерить скорость «у посетителей»: {node_name}.").format(node_name=region.node_name(country)))
    return (t("🕐 Часовой пояс: <b>{timezone}</b>, сейчас у тебя {time}.\nОт него зависят время сводок, тихие часы и все времена в сообщениях.{where}\n\nВыбери город или нажми «✏️ Другой» и пришли ключ вида <code>Europe/Prague</code>:").format(timezone=esc(settings.timezone()), time=now_local().strftime('%H:%M'), where=where))


def _tz_kb() -> InlineKeyboardMarkup:
    rows, row = [], []
    for i, (name, key) in enumerate(TIMEZONES):
        mark = "✅ " if key == settings.timezone() else ""
        row.append(InlineKeyboardButton(text=f"{mark}{t(name)}", callback_data=f"set_tz:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("✏️ Другой"), callback_data="tz_custom"),
                 InlineKeyboardButton(text=t("↩︎ Как на сервере ({timezone})").format(timezone=config.timezone), callback_data="set_tz:env")])
    rows.append([InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "menu_tz")
async def cb_tz(call: CallbackQuery):
    await ack(call)
    await render(call, _tz_text(), _tz_kb())


@router.callback_query(F.data == "tz_custom")
async def cb_tz_custom(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(TzForm.key)
    await render(call, t("Пришли ключ часового пояса из базы IANA, например <code>Europe/Prague</code> или <code>Asia/Tashkent</code>."), cancel_kb("menu_tz"))


@router.message(TzForm.key)
async def msg_tz(message: Message, state: FSMContext):
    key = (message.text or "").strip()
    if not await settings.set_timezone(key):
        await message.answer(t("Такого пояса не знаю. Формат: <code>Регион/Город</code>, например <code>Europe/Prague</code>."), reply_markup=cancel_kb("menu_tz"))
        return
    await state.clear()
    reschedule_all()
    await message.answer(t("✅ Часовой пояс: {key}, сейчас у тебя {time}.").format(key=esc(key), time=now_local().strftime('%H:%M')),
                         reply_markup=back_button(t("← Настройки"), "menu_settings"))


# ── Status page ──────────────────────────────────────────────────────────────

async def show_status_page(target, back: tuple[str, str] | None = None):
    back = back or (t("← Для посетителей"), "menu_visitors")
    on = settings.status_page_enabled()
    slug_src = integrations.source("status_page_slug")
    lines = [t("🌐 Публичная статус-страница"), ""]
    if on:
        lines.append(t("Включена: {status_page_url}").format(status_page_url=esc(public_urls.status_page_url())))
        if not public_urls.is_public():
            lines.append(esc(public_urls.NOT_PUBLIC))
        lines.append(t("Показывает, открываются ли сайты, простой за сутки/неделю/месяц и историю проблем без подробностей. Удобно давать клиентам или ставить в README."))
        badges = [(label, s["id"]) for s in await get_all_sites()
                  if (label := status_page.public_label(s["url"], s.get("http_method")))]
        if badges:
            lines += ["", t("🏷 Бейдж аптайма для README — картинка shields-стиля со своим адресом у каждого сайта:")]
            lines += [f"{esc(label)}: <code>{esc(public_urls.badge_url(sid))}</code>" for label, sid in badges[:10]]
            if len(badges) > 10:
                lines.append(t("… остальные — в «⚙️ Настройки сайта → 🛠 Для продвинутых»."))
    else:
        lines.append(t("Выключена. Включи — и по ссылке будет страница «всё ли работает» с аптаймом."))
    if slug_src == "env":
        lines.append(t("\n🔑 Секретная ссылка задана в .env (STATUS_PAGE_SLUG)."))
    elif slug_src == "bot":
        lines.append(t("\n🔑 Секретная ссылка включена: без секретного хвоста страница отвечает «не найдено»."))
    rows = [[InlineKeyboardButton(text=t("🌐 Выключить") if on else t("🌐 Включить"),
                                  callback_data=f"set_sp:{'off' if on else 'on'}")]]
    if slug_src == "bot":
        rows.append([InlineKeyboardButton(text=t("🔑 Секретная ссылка: выключить"), callback_data="sp_secret:off"),
                     InlineKeyboardButton(text=t("🔄 Новая ссылка"), callback_data="sp_secret:new")])
    elif slug_src is None:
        rows.append([InlineKeyboardButton(text=t("🔑 Сделать ссылку секретной"), callback_data="sp_secret:on")])
    if on and not public_urls.is_public():
        rows.append([InlineKeyboardButton(text=t("🌍 Как задать адрес"), callback_data="setup:url")])
    rows.append([InlineKeyboardButton(text=back[0], callback_data=back[1])])
    await render(target, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "menu_visitors")
async def cb_visitors(call: CallbackQuery):
    """What the bot shows to the people who visit the sites."""
    await ack(call)
    sp = (t("включена: {status_page_url}").format(status_page_url=public_urls.status_page_url()) if settings.status_page_enabled()
          else t("выключена"))
    n = await count_feedback()
    await render(call, t("🌍 Для посетителей\n\n🌐 Статус-страница — {sp}. Публичная страница «всё работает» с историей простоев — её можно дать посетителям.\n📩 Виджет «нашли опечатку?» — сообщения с сайтов приходят сюда").format(sp=esc(sp))
                       + (t("; получено: {n}.").format(n=n) if n else t("; пока ни одного.")),
                 InlineKeyboardMarkup(inline_keyboard=[
                     [InlineKeyboardButton(text=t("🌐 Статус-страница"), callback_data="menu_statuspage"),
                      InlineKeyboardButton(text=t("📩 Сообщения"), callback_data="menu_feedback:0")],
                     [InlineKeyboardButton(text=t("📩 Подключить виджет"), callback_data="menu_widget")],
                     [InlineKeyboardButton(text=t("← Настройки"), callback_data="menu_settings")]]))


@router.callback_query(F.data == "menu_statuspage")
async def cb_status_page(call: CallbackQuery):
    await ack(call)
    await show_status_page(call)


# Where the owner came from: «⚙️ → 🌐» or «🔌 Подключения → ➕ Подключить ещё».
_SP_DOORS = {"": ("← Для посетителей", "menu_visitors"), "setup": ("← К подключениям", "setup:check")}


async def publish_screen(back: str) -> tuple[str, InlineKeyboardMarkup]:
    """One confirmation for both doors (⚙️ → 🌐 and 🔌 → ➕ Подключить ещё):
    what becomes public, shown before it does. The door is carried in the
    callbacks, so the screen after publishing leads back to it."""
    door = ":setup" if back.startswith("setup:") else ""
    labels = await status_page.public_labels()
    shown = ", ".join(labels[:5]) + (t(" и ещё {n}").format(n=len(labels) - 5) if len(labels) > 5 else "")
    text = (t("🌐 Включить статус-страницу?\n\nЕё увидит любой, кто узнает адрес: {n} {words}{words2} — работают ли, аптайм и история проблем без подробностей. Порты, tcp/ping-проверки, внутренние адреса и пути API на странице не показываются.\n\nОткрытая страница живёт по адресу …/status — его легко угадать. С секретной ссылкой без секретного хвоста страница отвечает «не найдено».").format(n=len(labels), words=plural(len(labels), 'сайт', 'сайта', 'сайтов'), words2=' — ' + esc(shown) if labels else ''))
    rows = [[InlineKeyboardButton(text=t("🔑 Включить с секретной ссылкой"), callback_data=f"sp_pub:secret{door}")],
            [InlineKeyboardButton(text=t("🌐 Включить открытой"), callback_data=f"sp_pub:open{door}")],
            [InlineKeyboardButton(text=t("← Назад"), callback_data=back)]]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("sp_pub:"))
async def cb_status_page_publish(call: CallbackQuery):
    """ack first: a tap from the queue after a restart must not publish the
    page. «Открытой» means open — a slug the bot issued earlier is dropped,
    or /status would answer «не найдено» against the screen's words."""
    if not await ack(call):
        await call.message.answer(t(STALE_TAP))
        return
    parts = call.data.split(":")
    action, door = parts[1], (parts[2] if len(parts) > 2 else "")
    if action not in ("secret", "open") or door not in _SP_DOORS:
        return
    src = integrations.source("status_page_slug")
    if action == "secret" and not src:
        await integrations.set_value("status_page_slug", _secrets.token_urlsafe(8))
    elif action == "open" and src == "bot":
        await integrations.set_value("status_page_slug", None)
    await settings.set_status_page(True)
    await show_status_page(call, back=(t(_SP_DOORS[door][0]), _SP_DOORS[door][1]))


@router.callback_query(F.data.startswith("sp_secret:"))
async def cb_status_page_secret(call: CallbackQuery):
    """ack first (a stale tap does nothing), and idempotent: a second tap or
    an old screen never re-issues the link already handed out. A new link
    and an open page — each after a question. The result is the screen
    itself, re-rendered."""
    if not await ack(call):
        await call.message.answer(t(STALE_TAP))
        return
    action, src = call.data.split(":", 1)[1], integrations.source("status_page_slug")
    if action == "on" and src is None:
        await integrations.set_value("status_page_slug", _secrets.token_urlsafe(8))
    elif action in ("off", "new") and src == "bot":
        question = (t("Выключить секретную ссылку? Страница станет доступна всем по …/status.") if action == "off"
                    else t("Выпустить новую секретную ссылку? Прежняя, которую ты уже раздал, перестанет работать."))
        await render(call, question, InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("Да"), callback_data=f"sp_secret:{action}_ok")],
            [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_statuspage")]]))
        return
    elif action == "off_ok" and src == "bot":
        await integrations.set_value("status_page_slug", None)
    elif action == "new_ok" and src == "bot":
        await integrations.set_value("status_page_slug", _secrets.token_urlsafe(8))
    # anything else is already so («Уже так»): the screen says what is
    await show_status_page(call)
