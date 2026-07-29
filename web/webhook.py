"""
Lightweight aiohttp web server that receives feedback submissions from websites.

Endpoint:  POST /api/feedback
Headers:   X-Webhook-Secret: <WEBHOOK_SECRET>
Body JSON: {
    "site_url":  "https://s.morgunov.tech",
    "page_url":  "https://s.morgunov.tech/about",
    "message":   "Кнопка не работает на мобиле"
}

SECURITY MODEL: the widget ships the secret to every site visitor, so this
endpoint is effectively PUBLIC. The secret only filters lazy drive-by bots —
real protection is the per-IP/global rate limit and strict size caps below.
"""

import hmac
import logging
import os
import time
from collections import defaultdict, deque

from aiohttp import web
from aiogram import Bot

from config import config
from db.database import save_feedback, heartbeat_ping, get_state, set_state
from reports.formatter import format_feedback

logger = logging.getLogger(__name__)

# The widget does a cross-origin fetch with a custom header, which triggers a
# CORS preflight — both the OPTIONS response and the POST response must carry
# these headers or the browser blocks the request.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Webhook-Secret",
    "Access-Control-Max-Age": "86400",
}

# Server-side caps — the widget's maxlength is client-side only.
MAX_MESSAGE_LEN = 2000
MAX_URL_LEN = 500
MAX_BODY_BYTES = 64 * 1024
# Telegram messages cap at 4096 chars; leave headroom for the header lines.
TG_SAFE_LEN = 3500

RATE_WINDOW_SEC = 60
RATE_LIMIT_PER_IP = 5
RATE_LIMIT_GLOBAL = 30
_ip_hits: dict[str, deque] = defaultdict(deque)
_global_hits: deque = deque()


def _client_ip(request: web.Request) -> str:
    # X-Forwarded-For is attacker-controlled unless we're actually behind a
    # trusted reverse proxy, so it's opt-in via TRUST_PROXY.
    if config.trust_proxy:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.remote or "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_WINDOW_SEC

    while _global_hits and _global_hits[0] < cutoff:
        _global_hits.popleft()
    hits = _ip_hits[ip]
    while hits and hits[0] < cutoff:
        hits.popleft()

    if len(_global_hits) >= RATE_LIMIT_GLOBAL or len(hits) >= RATE_LIMIT_PER_IP:
        if not hits:
            # Don't let the dict grow unbounded with one-shot IPs.
            del _ip_hits[ip]
        return True
    _global_hits.append(now)
    hits.append(now)
    return False


def _response(status: int, text: str | None = None,
              json_body: dict | None = None) -> web.Response:
    if json_body is not None:
        resp = web.json_response(json_body, status=status)
    else:
        resp = web.Response(status=status, text=text)
    resp.headers.update(CORS_HEADERS)
    return resp


async def handle_feedback_options(request: web.Request) -> web.Response:
    """CORS preflight for the feedback endpoint."""
    return _response(204)


async def handle_feedback(request: web.Request) -> web.Response:
    ip_address = _client_ip(request)

    if _rate_limited(ip_address):
        logger.warning(f"Feedback: rate limit hit from {ip_address}")
        return _response(429, text="Too Many Requests")

    # Constant-time comparison; see the security note in the module docstring.
    secret = request.headers.get("X-Webhook-Secret", "")
    if not hmac.compare_digest(secret, config.webhook_secret):
        logger.warning(f"Feedback: invalid secret from {ip_address}")
        return _response(403, text="Forbidden")

    try:
        data = await request.json()
    except Exception:
        return _response(400, text="Invalid JSON")
    if not isinstance(data, dict):
        return _response(400, text="Invalid JSON")

    message = str(data.get("message") or "").strip()[:MAX_MESSAGE_LEN]
    if not message:
        return _response(400, text="message is required")

    site_url = str(data.get("site_url") or "unknown")[:MAX_URL_LEN]
    page_url = str(data.get("page_url") or site_url)[:MAX_URL_LEN]
    user_agent = request.headers.get("User-Agent", "")[:MAX_URL_LEN]

    # Save to DB
    feedback_id = await save_feedback(
        site_url=site_url,
        page_url=page_url,
        message=message,
        user_agent=user_agent,
        ip_address=ip_address,
    )

    # Forward to Telegram — clipped so a long message can't push us past
    # Telegram's 4096-char limit and silently drop the forward.
    bot: Bot = request.app["bot"]
    tg_message = format_feedback(site_url, page_url, message)
    tg_message += f"\n\n🆔 ID: #{feedback_id}\n🌍 IP: {ip_address}"
    if len(tg_message) > TG_SAFE_LEN:
        tg_message = tg_message[:TG_SAFE_LEN] + "\n… (обрезано)"
    try:
        await bot.send_message(chat_id=config.admin_chat_id, text=tg_message)
    except Exception as e:
        logger.error(f"Failed to forward feedback to Telegram: {e}")

    return _response(201, json_body={"ok": True, "id": feedback_id})


async def handle_heartbeat(request: web.Request) -> web.Response:
    """Dead-man switch ping: external jobs (backup cron etc.) hit
    GET /api/heartbeat/<secret>/<job> when they finish successfully.
    The token lives in the path, healthchecks.io-style, so a plain
    `curl <url>` at the end of a cron line is the whole integration."""
    token = request.match_info.get("token", "")
    job = request.match_info.get("job", "")[:64]
    if not hmac.compare_digest(token, config.heartbeat_secret):
        return web.Response(status=403, text="Forbidden")
    if not job:
        return web.Response(status=400, text="job name required")

    await heartbeat_ping(job)

    # If we had alerted that this job went silent — announce recovery.
    if await get_state(f"hb_alerted:{job}"):
        await set_state(f"hb_alerted:{job}", None)
        from reports.scheduler import send_to_admin
        await send_to_admin(
            request.app["bot"], f"💚 Heartbeat «{job}» снова подаёт сигналы"
        )
    return web.Response(text="ok")


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
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app["bot"] = bot
    app.router.add_post("/api/feedback", handle_feedback)
    app.router.add_options("/api/feedback", handle_feedback_options)
    app.router.add_get("/api/heartbeat/{token}/{job}", handle_heartbeat)
    app.router.add_post("/api/heartbeat/{token}/{job}", handle_heartbeat)
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
