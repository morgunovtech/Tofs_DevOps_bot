"""«Обновление поменяло поведение» (~/BOT_PRINCIPLES.md §6).

At every start the bot writes down how it works: when the digests come and
in which timezone, quiet hours, «будить только…», the dials of «🛠 Тонкие
настройки», what it does by itself, the second vantage point. Next to each
value it keeps what the owner gave it — the chat's choice (bot_state) and the
.env variables it falls back to.

A value that moved while the owner's inputs stayed the same was moved by the
code: a new default, a new bound, a language-dependent timezone. That is
said once — «ℹ️ После обновления …: утренняя сводка 08:00 (было 09:00)» —
with a button to the screen where it is changed back. What the owner changed,
in the chat or in .env, is not news. The first start only writes
the snapshot down; a setting this build knows and the previous one did not
is not compared (a new feature is the CHANGELOG's to tell).

The example the rule grew from: Social Tracker Bot #52 made the timezone
follow the language, and REPORT_HOUR_UTC=4 silently became 04:00 Moscow
instead of 09:00 Tashkent.
"""

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_state, set_state
from handlers.settings import TIMEZONES
from handlers.tune import ROWS
from services import integrations, notifier, settings
from services.humanize import lower_first
from services.notifier import Priority
from utils.i18n import t
from utils.text import esc

logger = logging.getLogger(__name__)

SNAPSHOT = "behavior:snapshot"

# Where each value is changed: (the button's words, its callback).
REPORTS = ("🔔 Когда писать", "menu_reports")
QUIET = ("🔕 Тишина", "menu_quiet")
TZ = ("🕐 Часовой пояс", "menu_tz")
TUNE = ("🛠 Тонкие настройки", "menu_tune")
AUTO = ("🤖 Что делаю сам", "auto:menu")
BACKUP = ("💾 Копия базы", "menu_backup")
STATUS = ("🌐 Статус-страница", "menu_statuspage")
VANTAGE = ("🌐 Вторая точка", "setup:so")


@dataclass(frozen=True)
class Item:
    key: str
    title: str                        # Russian, translated where shown
    screen: tuple[str, str]
    state: tuple[str, ...]            # bot_state keys the chat writes
    env: tuple[str, ...]              # .env variables the value falls back to
    value: Callable[[], object]       # the value in force, JSON-able
    words: Callable[[object], str]    # a value in words, translated


def _clock(hour) -> str:
    return t("выкл") if hour is None else f"{int(hour):02d}:00"


def _on_off(on) -> str:
    return t("вкл") if on else t("выкл")


def _range(hours) -> str:
    return t("выкл") if not hours else f"{int(hours[0]):02d}:00–{int(hours[1]):02d}:00"


def _zone(key) -> str:
    city = next((name for name, k in TIMEZONES if k == key), None)
    return t(city) if city else str(key)


def _lang(code) -> str:
    return {"ru": t("русский"), "en": t("английский")}.get(str(code), str(code))


def _dial(row):
    return Item(f"dial:{row.key}", row.title, TUNE, (f"cfg:{row.key}",), (settings.DIALS[row.key].env,),
                lambda: settings.dial(row.key), lambda v: row.words(int(v)))


ITEMS: tuple[Item, ...] = (
    Item("morning", "утренняя сводка", REPORTS, ("cfg:morning_hour",), ("MORNING_REPORT_HOUR",),
         settings.morning_hour, _clock),
    Item("evening", "вечерняя сводка", REPORTS, ("cfg:evening_hour",), ("EVENING_REPORT_HOUR",),
         settings.evening_hour, _clock),
    Item("weekly", "воскресный отчёт", REPORTS, ("cfg:weekly_hour",), ("WEEKLY_REPORT_HOUR",),
         settings.weekly_hour, _clock),
    Item("timezone", "часовой пояс", TZ, ("cfg:timezone",), ("TIMEZONE", "BOT_LANG"), settings.timezone, _zone),
    Item("quiet", "тихие часы", QUIET, ("cfg:quiet_hours",), ("QUIET_HOURS",), settings.quiet_hours, _range),
    Item("ring_only_down", "будить только когда сайт лёг", REPORTS, ("cfg:ring_only_down",), (),
         settings.ring_only_down, _on_off),
    *(_dial(row) for row in ROWS),
    Item("lang", "язык бота", TUNE, ("cfg:lang",), ("BOT_LANG",), settings.lang, _lang),
    Item("auto_redeploy", "пересборка упавшего сайта", AUTO, ("int:auto_redeploy",), ("AUTO_REDEPLOY",),
         lambda: integrations.auto_enabled("redeploy"), _on_off),
    Item("auto_restart", "перезапуск контейнеров", AUTO, ("int:auto_restart",), ("AUTORESTART_CONTAINERS",),
         lambda: integrations.auto_enabled("restart"), _on_off),
    Item("auto_cleanup", "очистка диска", AUTO, ("int:auto_cleanup",), ("AUTO_CLEANUP",),
         lambda: integrations.auto_enabled("cleanup"), _on_off),
    Item("second_opinion", "вторая точка проверки (check-host.net)", VANTAGE, ("int:second_opinion",),
         ("SECOND_OPINION",), integrations.second_opinion_enabled, _on_off),
    Item("region_speed", "скорость из региона", VANTAGE, ("int:region_speed",), ("REGION_SPEED",),
         integrations.region_speed_enabled, _on_off),
    Item("weekly_backup", "копия базы по воскресеньям", BACKUP, ("cfg:weekly_backup",), (),
         settings.weekly_backup, _on_off),
    Item("status_page", "статус-страница", STATUS, ("cfg:status_page",), ("STATUS_PAGE",),
         settings.status_page_enabled, _on_off),
)


async def snapshot() -> dict:
    """{key: {"v": the value in force, "in": what the owner gave it}} — as
    it reads back from JSON, so a tuple and a list compare equal."""
    out = {}
    for item in ITEMS:
        try:
            given = {k: await get_state(k) for k in item.state} | {e: os.environ.get(e) for e in item.env}
            out[item.key] = {"v": item.value(), "in": given}
        except Exception:   # one broken reading costs only its own line
            logger.exception("Behaviour snapshot: %s not read", item.key)
    return json.loads(json.dumps(out))


def shifted(previous: dict, current: dict) -> list[tuple[Item, object, object]]:
    """(item, was, now) for every value that moved while the owner's inputs
    stayed as they were."""
    out = []
    for item in ITEMS:
        was, now = previous.get(item.key), current.get(item.key)
        if not isinstance(was, dict) or not isinstance(now, dict):
            continue                       # new in this build, or not read: nothing to compare
        if was.get("in") == now.get("in") and was.get("v") != now.get("v"):
            out.append((item, was.get("v"), now.get("v")))
    return out


def shift_message(changes: list[tuple[Item, object, object]]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [t("ℹ️ После обновления я работаю иначе, хотя в чате и в .env ничего не менялось:")]
    for item, was, now in changes:
        lines.append(t("• {what}: {now} (было {was})").format(
            what=esc(lower_first(t(item.title))), now=esc(item.words(now)), was=esc(item.words(was))))
    lines += ["", t("Если прежнее было лучше — верни его кнопкой ниже.")]
    screens = list(dict.fromkeys(item.screen for item, _, _ in changes))
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t(words), callback_data=data)]
                                               for words, data in screens])
    return "\n".join(lines), kb


async def report_shift() -> bool:
    """The start step: compare with the previous start, say a shift once.
    True when something was said. A message the notifier did not take keeps
    the old snapshot: the next start says it again."""
    current = await snapshot()
    try:
        previous = json.loads(await get_state(SNAPSHOT) or "null")
    except ValueError:
        previous = None
    changes = shifted(previous, current) if isinstance(previous, dict) else []
    if changes:
        text, kb = shift_message(changes)
        if not await notifier.send(text, Priority.NORMAL, reply_markup=kb):
            return False
    await set_state(SNAPSHOT, json.dumps(current, ensure_ascii=False))
    return bool(changes)
