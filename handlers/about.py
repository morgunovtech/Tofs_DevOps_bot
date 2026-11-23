"""📖 «Что я умею»: every function as «what it does → what you get», and
every optional connection with what it gives and who needs it — the whole
bot in one place, so the owner decides what is worth connecting."""

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from handlers.common import ack, render
from services import crux, github, gsc, heartbeats, integrations, settings, yandex_webmaster
from utils.i18n import t

router = Router(name="about")


def _features() -> list[tuple[str, list[tuple[str, str]]]]:
    """(group, [(what I do, what you get)]) — everything that works with no setup."""
    return [
        (t("🌐 Сайт работает"), [
            (t("Проверяю каждый сайт каждые {minutes} мин").format(minutes=config.check_interval_minutes),
             t("узнаёшь, что сайт лёг, раньше клиентов — и когда он снова поднялся")),
            (t("Слежу за фразой на странице (предложу при добавлении сайта)"),
             t("ловлю «открывается, но не то»: пустую страницу, заглушку хостинга, текст ошибки")),
            (t("Обхожу страницы из карты сайта и ссылки с главной"),
             t("битые страницы и ссылки не теряют тебе посетителей")),
            (t("Могу проверять главную функцию — форму или API (в карточке сайта)"),
             t("знаешь, что сайт не просто открывается, а работает")),
        ]),
        (t("🔒 Ничего не истечёт и не сломается тихо"), [
            (t("Слежу за сроком SSL-сертификата и домена"),
             t("предупрежу заранее: браузер не покажет «опасно», домен не уйдёт")),
            (t("Сравниваю настройки домена (DNS) каждый час"),
             t("сразу заметишь, если их кто-то сменил")),
            (t("Проверяю почту домена"),
             t("письма клиентов доходят и не уходят в спам")),
            (t("Ищу открытые наружу файлы с кодом и паролями, проверяю https и защитные заголовки"),
             t("закроешь дыры раньше, чем их найдут")),
        ]),
        (t("🔍 Тебя находят"), [
            (t("Каждый день проверяю, открыт ли сайт для Google, Яндекса и ИИ-ассистентов"),
             t("сайт не выпадет из поиска из-за одной строки в robots.txt")),
        ]),
        (t("🤖 Боты и задачи"), [
            (t("Слежу, чтобы бэкапы, рассылки и другие боты выходили на связь (🤖 Боты → ➕)"),
             t("узнаешь, что бэкап не сделался, а не в день, когда он понадобится")),
        ]),
        (t("🔔 Пишу только по делу"), [
            (t("Молчу, когда всё хорошо; утром — одно «всё в порядке»"),
             t("не привыкаешь пропускать мои сообщения")),
            (t("В каждой тревоге — что случилось, что это значит для посетителей и что делать"),
             t("не нужно быть инженером")),
            (t("Звоню, только когда сайт лёг; остальное приходит без звука") if settings.ring_only_down()
             else t("Звоню по 🔴 и когда упавший сайт снова открылся; остальное приходит без звука"),
             t("не дёргаю по мелочам")),
            (t("По воскресеньям — отчёт за неделю"),
             t("видишь, сколько сайт был недоступен и что стоит улучшить")),
            (t("Звоню и помощникам — до трёх человек (⚙️ Настройки → 👥)"),
             t("отвечаешь за сайт не один")),
        ]),
    ]


def features_text() -> str:
    lines = [t("📖 <b>Что я умею — сам, без настроек</b>")]
    for group, items in _features():
        lines += ["", f"<b>{group}</b>"]
        lines += [f"• {what} → <i>{gain}</i>" for what, gain in items]
    lines += ["", t("<b>Где что</b>"),
              t("🏠 Главная → 🌐 Сайты → карточка: проверить снова, «🔧 Я чиню», 🔎 Поиск и ИИ, ✉️ Почта, 🛡 Безопасность, ⚙️ Настройки сайта."),
              t("🏠 Главная → 🤖 Боты: задачи и боты по расписанию, внешний сторож."),
              t("⚙️ Настройки: 🔔 Когда писать · 🔕 Тишина · 👥 Помощники · 🔌 Подключения · 🌍 Для посетителей · 💾 Копия базы · 🕐 Часовой пояс."),
              t("❓ Значки (кнопка под полем ввода) — что значат 🔴 🟠 ✅ ⚠️, пять видов тишины и команды.")]
    lines += ["", t("Ещё больше — если подключить сервисы: «🔌 Что можно подключить».")]
    return "\n".join(lines)


def features_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("🔌 Что можно подключить"), callback_data="about:modules")],
        [InlineKeyboardButton(text=t("🏠 Главная"), callback_data="menu_main")]])


@dataclass(frozen=True)
class Module:
    title: str
    gives: str          # what it gives
    who: str            # who needs it — «подключать ли»
    button: tuple[str, str]


# Russian, like every constant: modules_page() translates each field as it draws it.
MODULES = (
    ("second_opinion", Module(
        "🌐 Вторая точка",
        "перед тревогой «сайт лёг» перепроверю с серверов check-host.net в других странах; по желанию — "
        "скорость из страны твоей аудитории",
        "почти всем: меньше ложных тревог. Бесплатный сервис check-host.net, ему уходят адреса сайтов",
        ("🌐 Вторая точка", "setup:so"))),
    ("watchdog", Module(
        "🛡 Внешний сторож",
        "healthchecks.io напишет тебе, если перестану работать я сам",
        "всем: иначе моё молчание может значить, что я упал. Бесплатно",
        ("🛡 Сторож", "setup:watchdog"))),
    ("gsc", Module(
        "🔎 Google Search Console",
        "в тот же день скажу, если главная выпала из поиска Google; клики и показы — в отчёте за неделю",
        "если клиенты приходят из поиска Google",
        ("🔎 Search Console", "setup:gsc"))),
    ("yandex", Module(
        "🔎 Яндекс.Вебмастер",
        "сколько страниц в поиске Яндекса, ИКС и проблемы, которые видит Яндекс",
        "если твоя аудитория ищет через Яндекс",
        ("🔎 Вебмастер", "setup:yx"))),
    ("crux", Module(
        "⚡ Скорость у посетителей",
        "сколько реальные посетители в Chrome ждут твой сайт — по данным Google; станет медленно — скажу",
        "если у сайта заметный трафик: о небольших сайтах Google данных не собирает",
        ("⚡ Скорость", "setup:crux"))),
    ("cloudflare", Module(
        "☁️ Cloudflare и хостинг",
        "кнопки «передеплой» и «сброс кэша» прямо в тревоге, упавший сайт перезапущу сам",
        "если сайт на Cloudflare или у хостинга есть deploy hook",
        ("☁️ Cloudflare", "setup:cf"))),
    ("github", Module(
        "🐙 GitHub",
        "по воскресеньям — что прибрать в репозиториях: старые PR, лишние ветки, красный CI",
        "если код сайтов лежит на GitHub",
        ("🐙 GitHub", "setup:gh"))),
    ("status_page", Module(
        "🌐 Статус-страница",
        "публичная страница «всё работает» с аптаймом и бейджи для README",
        "если хочешь показывать клиентам или команде, что всё в порядке",
        ("🌐 Статус-страница", "setup:sp_on"))),
)


async def _connected() -> dict[str, bool]:
    """What is connected — the cheap way, without asking any API."""
    return {
        "second_opinion": integrations.second_opinion_enabled(),
        "watchdog": await heartbeats.watchdog() is not None,
        "gsc": gsc.available(),
        "yandex": yandex_webmaster.available(),
        "crux": crux.available(),
        "cloudflare": bool(integrations.deploy_hooks() or integrations.cf_purge_configured()),
        "github": github.available(),
        "status_page": settings.status_page_enabled(),
    }


async def modules_page() -> tuple[str, InlineKeyboardMarkup]:
    on = await _connected()
    # Connected, but its key is refused or it has not answered for a day.
    silent = {key for key in ("gsc", "yandex", "crux", "github") if on.get(key) and await integrations.broken(key)}
    lines = [t("🔌 <b>Что можно подключить</b> — всё необязательно, я работаю и без этого."),
             t("<i>Что даёт → кому нужно</i>")]
    off = []
    for key, m in MODULES:
        mark = (t(" ⚠️ подключено, но не отвечает") if key in silent else t(" ✅ подключено")) if on.get(key) else ""
        gives = t(m.gives)
        lines += ["", f"<b>{t(m.title)}</b>{mark}", f"{gives[:1].upper()}{gives[1:]} → <i>{t(m.who)}</i>."]
        label = t(m.button[0])
        if not on.get(key) or key == "second_opinion":   # its screen is where it is switched off too
            off.append((label, m.button[1]))
        elif key != "status_page":   # a connected one keeps a way into its wizard: a new key, «🗑 Отключить»
            off.append((f"⚙️ {label.split(' ', 1)[-1]}", m.button[1]))
    rows, row = [], []
    for label, data in off:
        row.append(InlineKeyboardButton(text=label, callback_data=data))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text=t("← Что я умею"), callback_data="about:main"),
                 InlineKeyboardButton(text=t("🏠 Главная"), callback_data="menu_main")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "about:main")
async def cb_about(call: CallbackQuery):
    await ack(call)
    await render(call, features_text(), features_keyboard())


@router.callback_query(F.data == "about:modules")
async def cb_modules(call: CallbackQuery):
    await ack(call)
    await render(call, *await modules_page())
