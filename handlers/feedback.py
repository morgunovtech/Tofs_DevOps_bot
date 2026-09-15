"""📩 Messages sent by visitors through the site widget."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import config
from db.database import count_feedback, list_feedback
from handlers.common import ack, render
from services import public_urls, secrets
from utils.clock import fmt_local
from utils.text import esc

router = Router(name="feedback")

PAGE = 5


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
    if not config.public_base_url:
        lines.append("\n⚠️ Задай PUBLIC_BASE_URL, чтобы ссылки ниже были готовы к вставке.")
    lines.append(f"\nПодключение виджета на сайт:\n"
                 f"<code>&lt;script&gt;window.DevOpsFeedback={{webhookUrl:'{esc(public_urls.feedback_url())}',"
                 f"secret:'{esc(secrets.webhook_secret())}',button:false}}&lt;/script&gt;\n"
                 f"&lt;script src=\"{esc(public_urls.widget_url())}\"&gt;&lt;/script&gt;</code>\n"
                 f"Любая ссылка с атрибутом <code>data-devops-feedback</code> откроет форму.")
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="← Новее", callback_data=f"menu_feedback:{max(0, offset - PAGE)}"))
    if offset + PAGE < total:
        nav.append(InlineKeyboardButton(text="Старее →", callback_data=f"menu_feedback:{offset + PAGE}"))
    kb_rows = [nav] if nav else []
    kb_rows.append([InlineKeyboardButton(text="← Настройки", callback_data="menu_settings")])
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=kb_rows))
