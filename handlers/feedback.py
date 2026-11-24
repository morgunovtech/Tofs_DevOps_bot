"""📩 Messages sent by visitors through the site widget."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import count_feedback, delete_feedback, list_feedback
from handlers.common import STALE_TAP, ack, cb_int, render
from services import public_urls, secrets
from utils.clock import fmt_local
from utils.i18n import t
from utils.text import defang, esc, one_line
from web import widget

router = Router(name="feedback")

PAGE = 5


def widget_snippet() -> str:
    """The code to paste on a site. The widget is requested only after the
    page has loaded — a bot host that hangs cannot stall the site — and
    integrity pins the exact file: a file changed on the bot's host is
    refused by the visitor's browser."""
    return (f"<script>window.DevOpsFeedback={{webhookUrl:'{public_urls.feedback_url()}',"
            f"secret:'{secrets.webhook_secret()}',button:false}};"
            f"(function(){{function l(){{var s=document.createElement('script');"
            f"s.src='{public_urls.widget_url()}';s.integrity='{widget.INTEGRITY}';"
            f"s.crossOrigin='anonymous';s.async=true;document.head.appendChild(s)}}"
            f"document.readyState==='complete'?l():addEventListener('load',l)}})();</script>")


def widget_text() -> str:
    lines = [t("📩 Виджет «нашли опечатку?»"), ""]
    if not public_urls.is_public():
        lines += [esc(public_urls.NOT_PUBLIC), ""]
    lines.append(t("Вставь на сайт перед <code>&lt;/body&gt;</code>:\n<code>{widget_snippet}</code>\n\nЛюбая ссылка с атрибутом <code>data-devops-feedback</code> откроет форму. Язык формы — как у страницы (<code>&lt;html lang&gt;</code>): русский или английский; закрепить — <code>lang:'ru'</code> или <code>lang:'en'</code> в настройке. После обновления бота сниппет может смениться — скажу, и его надо будет заменить на сайтах.\n\nСтрогий Content-Security-Policy на сайте? Подключение без inline-скрипта и с копией файла у себя — в начале файла {widget_url}").format(widget_snippet=esc(widget_snippet()), widget_url=esc(public_urls.widget_url())))
    lines += ["", t("Сообщения посетителей приходят сюда и лежат в «📩 Сообщения». Ключ в сниппете виден любому посетителю — это не пароль, а фильтр от случайных ботов; сменить его можно здесь.")]
    return "\n".join(lines)


def widget_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("📩 Сообщения"), callback_data="menu_feedback:0"),
         InlineKeyboardButton(text=t("🔑 Сменить ключ виджета"), callback_data="fb_secret")],
        [InlineKeyboardButton(text=t("← Для посетителей"), callback_data="menu_visitors")]])


@router.callback_query(F.data == "menu_widget")
async def cb_widget(call: CallbackQuery):
    await ack(call)
    await render(call, widget_text(), widget_kb())


@router.callback_query(F.data.in_({"fb_secret", "fb_secret:ok"}))
async def cb_widget_secret(call: CallbackQuery):
    """A new widget key, after a question: every site's snippet has to be
    replaced, and until it is the old one is refused (403)."""
    if call.data == "fb_secret":
        await ack(call)
        await render(call, t("🔑 Выпустить новый ключ виджета?\n\nСниппет на каждом сайте придётся заменить: со старым ключом сообщения перестанут приниматься. Делай это, если ключ попал туда, где ему не место, или виджет заваливают спамом."),
                     InlineKeyboardMarkup(inline_keyboard=[
                         [InlineKeyboardButton(text=t("🔑 Да, выпустить"), callback_data="fb_secret:ok")],
                         [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_widget")]]))
        return
    if not await ack(call, t("Готово")):          # a tap from the queue after a restart changes no key
        await call.message.answer(t(STALE_TAP))
        return
    await secrets.regenerate("webhook")
    await render(call, t("🔑 Новый ключ виджета выпущен — замени сниппет на сайтах.\n\n") + widget_text(), widget_kb())


@router.callback_query(F.data.startswith("menu_feedback:"))
async def cb_feedback(call: CallbackQuery):
    await ack(call)
    arg = call.data.split(":", 1)[1]
    await show_feedback(call, cb_int(arg) or 0)


async def show_feedback(call: CallbackQuery, offset: int, note: str = ""):
    """The page of visitors' messages from `offset`; note — a line above it."""
    total = await count_feedback()
    rows = await list_feedback(PAGE, offset)
    lines = [f"{note}\n" if note else "", t("📩 Сообщения с сайтов ({total}):").format(total=total) + "\n"]
    lines = lines[1:] if not note else lines
    if not rows:
        lines.append(t("Пока ни одного сообщения.") if offset == 0 else t("Дальше пусто."))
    for fb in rows:
        # A visitor's words as one line, like the alert draws them: a line
        # break or a direction override in them must not draw a row of the
        # bot's, and a link in them is defanged (hxxps://evil[.]example).
        message = one_line(defang(fb["message"]))
        lines.append(f"#{fb['id']} · {fmt_local(fb['created_at'])} · {one_line(fb['page_url'] or fb['site_url'])}\n"
                     f"   {message[:300]}{'…' if len(message) > 300 else ''}")
    lines.append("\n" + t("Подключить виджет на сайт — «📩 Подключить виджет» на экране «🌍 Для посетителей»."))
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text=t("← Новее"), callback_data=f"menu_feedback:{max(0, offset - PAGE)}"))
    if offset + PAGE < total:
        nav.append(InlineKeyboardButton(text=t("Старее →"), callback_data=f"menu_feedback:{offset + PAGE}"))
    kb_rows = [nav] if nav else []
    if rows:   # spam and tests from the widget pile up: the page can be removed, after a question
        ids = [fb["id"] for fb in rows]
        kb_rows.append([InlineKeyboardButton(text=t("🗑 Удалить эти {n}").format(n=len(ids)),
                                             callback_data=f"fb_del:{max(ids)}:{min(ids)}")])
    kb_rows.append([InlineKeyboardButton(text=t("← Для посетителей"), callback_data="menu_visitors")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("fb_del"))
async def cb_feedback_delete(call: CallbackQuery):
    """fb_del:<max>:<min> asks; fb_del_ok:<max>:<min> deletes the messages
    with ids in that range and shows the list again (CR-613)."""
    parts = (call.data or "").split(":")
    if len(parts) != 3 or cb_int(parts[1]) is None or cb_int(parts[2]) is None:
        await ack(call, t("Кнопка устарела — открой список заново"), show_alert=True)
        return
    hi, lo = int(parts[1]), int(parts[2])
    if parts[0] == "fb_del":
        await ack(call)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=t("🗑 Да, удалить"), callback_data=f"fb_del_ok:{hi}:{lo}")],
            [InlineKeyboardButton(text=t("✖️ Отмена"), callback_data="menu_feedback:0")]])
        await render(call, t("Удалить эти сообщения посетителей? Вернуть их будет нельзя."), kb)
        return
    if not await ack(call):           # «Да» from the queue after a restart: nothing is deleted
        await call.message.answer(t(STALE_TAP))
        return
    n = await delete_feedback(hi, lo)
    await show_feedback(call, 0, note=t("🗑 Удалил: {n}").format(n=n))
