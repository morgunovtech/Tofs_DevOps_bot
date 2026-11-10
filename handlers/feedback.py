"""📩 Messages sent by visitors through the site widget."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from db.database import count_feedback, list_feedback
from handlers.common import ack, render
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
    offset = int(arg) if arg.isdigit() else 0
    total = await count_feedback()
    rows = await list_feedback(PAGE, offset)
    lines = [f"📩 Обратная связь с сайтов ({total}):\n"]
    if not rows:
        lines.append("Пока ни одного сообщения." if offset == 0 else "Дальше пусто.")
    for fb in rows:
        lines.append(f"#{fb['id']} · {fmt_local(fb['created_at'])} · {esc(fb['page_url'] or fb['site_url'])}\n"
                     f"   {esc(fb['message'][:300])}{'…' if len(fb['message']) > 300 else ''}")
    if not public_urls.is_public():
        lines.append("\n⚠️ Задай PUBLIC_BASE_URL, чтобы ссылки ниже были готовы к вставке.")
    lines.append(f"\nПодключение виджета на сайт (перед <code>&lt;/body&gt;</code>):\n"
                 f"<code>{esc(widget_snippet())}</code>\n"
                 f"Любая ссылка с атрибутом <code>data-devops-feedback</code> откроет форму. "
                 f"После обновления бота сниппет может смениться — бот скажет, и его надо заменить на сайтах.\n"
                 f"Строгий Content-Security-Policy на сайте? Подключение без inline-скрипта и с копией "
                 f"файла у себя — в начале файла {esc(public_urls.widget_url())}")
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="← Новее", callback_data=f"menu_feedback:{max(0, offset - PAGE)}"))
    if offset + PAGE < total:
        nav.append(InlineKeyboardButton(text="Старее →", callback_data=f"menu_feedback:{offset + PAGE}"))
    kb_rows = [nav] if nav else []
    kb_rows.append([InlineKeyboardButton(text="← Для посетителей", callback_data="menu_visitors")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))
