"""
Lightweight aiohttp web server that receives feedback submissions from websites.

Endpoint:  POST /api/feedback
Headers:   X-Webhook-Secret: <WEBHOOK_SECRET>
Body JSON: {
    "site_url":  "https://s.morgunov.tech",
    "page_url":  "https://s.morgunov.tech/about",
    "message":   "Кнопка не работает на мобиле"
}
"""

import hmac
import json
import logging
import os

from aiohttp import web
from aiogram import Bot

from config import config
from db.database import save_feedback
from reports.formatter import format_feedback

logger = logging.getLogger(__name__)


async def handle_feedback(request: web.Request) -> web.Response:
    # Validate secret with constant-time comparison
    secret = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(secret, config.webhook_secret):
        logger.warning(f"Feedback: invalid secret from {request.remote}")
        return web.Response(status=403, text="Forbidden")

    try:
        data = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON")

    message = data.get("message", "").strip()
    if not message:
        return web.Response(status=400, text="message is required")

    site_url = data.get("site_url", "unknown")
    page_url = data.get("page_url", site_url)
    user_agent = request.headers.get("User-Agent", "")
    ip_address = request.remote

    # Save to DB
    feedback_id = await save_feedback(
        site_url=site_url,
        page_url=page_url,
        message=message,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    # Forward to Telegram
    bot: Bot = request.app["bot"]
    tg_message = format_feedback(site_url, page_url, message)
    tg_message += f"\n\n🆔 ID: #{feedback_id}\n🌍 IP: {ip_address}"
    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=tg_message)
    except Exception as e:
        logger.error(f"Failed to forward feedback to Telegram: {e}")

    return web.json_response({"ok": True, "id": feedback_id}, status=201)


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def handle_widget(request: web.Request) -> web.Response:
    """Serve the JS feedback widget file."""
    widget_path = os.path.join(os.path.dirname(__file__), "feedback-widget.js")
    try:
        with open(widget_path, "r", encoding="utf-8") as f:
            content = f.read()
        return web.Response(
            text=content,
            content_type="application/javascript",
            headers={"Access-Control-Allow-Origin": "*"},
        )
    except FileNotFoundError:
        return web.Response(status=404, text="Widget not found")


def create_app(bot: Bot) -> web.Application:
    app = web.Application()
    app["bot"] = bot
    app.router.add_post("/api/feedback", handle_feedback)
    app.router.add_get("/feedback-widget.js", handle_widget)
    app.router.add_get("/health", handle_health)
    return app


async def start_web_server(bot: Bot):
    app = create_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.webhook_port)
    await site.start()
    logger.info(f"Webhook server running on port {config.webhook_port}")
    return runner
