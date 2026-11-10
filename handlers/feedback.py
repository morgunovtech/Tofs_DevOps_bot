"""📩 Messages sent by visitors through the site widget."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import count_feedback, delete_feedback, list_feedback
from handlers.common import STALE_TAP, ack, render
from services import public_urls, secrets
from utils.clock import fmt_local
from utils.text import esc
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


@router.callback_query(F.data.startswith("menu_feedback:"))
async def cb_feedback(call: CallbackQuery):
    await ack(call)
    arg = call.data.split(":", 1)[1]
    await show_feedback(call, int(arg) if arg.isdigit() else 0)


async def show_feedback(call: CallbackQuery, offset: int, note: str = ""):
    """The page of visitors' messages from `offset`; note — a line above it."""
    total = await count_feedback()
    rows = await list_feedback(PAGE, offset)
    lines = [f"{note}\n" if note else "", f"📩 Обратная связь с сайтов ({total}):\n"]
    lines = lines[1:] if not note else lines
    if not rows:
        lines.append("Пока ни одного сообщения." if offset == 0 else "Дальше пусто.")
    for fb in rows:
        lines.append(f"#{fb['id']} · {fmt_local(fb['created_at'])} · {esc(fb['page_url'] or fb['site_url'])}\n"
                     f"   {esc(fb['message'][:300])}{'…' if len(fb['message']) > 300 else ''}")
    if not public_urls.is_public():
        lines.append("\n⚠️ Задай PUBLIC_BASE_URL, чтобы ссылки ниже были готовы к вставке.")
    lines.append(f"\nПодключение виджета на сайт (перед <code>&lt;/body&gt;</code>):\n"
                 f"<code>{esc(widget_snippet())}</code>\n"
                 f"Любая ссылка с атрибутом <code>data-devops-feedback</code> откроет форму.\n"
                 f"Язык формы — как у страницы (<code>&lt;html lang&gt;</code>): русский или английский; "
                 f"закрепить — <code>lang:'ru'</code> или <code>lang:'en'</code> в настройке. "
                 f"После обновления бота сниппет может смениться — бот скажет, и его надо заменить на сайтах.\n"
                 f"Строгий Content-Security-Policy на сайте? Подключение без inline-скрипта и с копией "
                 f"файла у себя — в начале файла {esc(public_urls.widget_url())}")
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="← Новее", callback_data=f"menu_feedback:{max(0, offset - PAGE)}"))
    if offset + PAGE < total:
        nav.append(InlineKeyboardButton(text="Старее →", callback_data=f"menu_feedback:{offset + PAGE}"))
    kb_rows = [nav] if nav else []
    if rows:   # spam and tests from the widget pile up: the page can be removed, after a question
        ids = [fb["id"] for fb in rows]
        kb_rows.append([InlineKeyboardButton(text=f"🗑 Удалить эти {len(ids)}",
                                             callback_data=f"fb_del:{max(ids)}:{min(ids)}")])
    kb_rows.append([InlineKeyboardButton(text="← Для посетителей", callback_data="menu_visitors")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))


@router.callback_query(F.data.startswith("fb_del"))
async def cb_feedback_delete(call: CallbackQuery):
    """fb_del:<max>:<min> asks; fb_del_ok:<max>:<min> deletes the messages
    with ids in that range and shows the list again (CR-613)."""
    parts = (call.data or "").split(":")
    if len(parts) != 3 or not (parts[1].isdigit() and parts[2].isdigit()):
        await ack(call, "Кнопка устарела — открой список заново", show_alert=True)
        return
    hi, lo = int(parts[1]), int(parts[2])
    if parts[0] == "fb_del":
        await ack(call)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"fb_del_ok:{hi}:{lo}")],
            [InlineKeyboardButton(text="← Не надо", callback_data="menu_feedback:0")]])
        await render(call, "Удалить эти сообщения посетителей? Вернуть их будет нельзя.", kb)
        return
    if not await ack(call):           # «Да» from the queue after a restart: nothing is deleted
        await call.message.answer(STALE_TAP)
        return
    n = await delete_feedback(hi, lo)
    await show_feedback(call, 0, note=f"🗑 Удалил: {n}")
