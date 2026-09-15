"""aiohttp web server: feedback from site widgets, heartbeat pings, the
public status page (+JSON, +badges) and the widget file itself.

SECURITY MODEL of /api/feedback: the widget ships the secret to every site
visitor, so this endpoint is effectively PUBLIC. The secret only filters
lazy drive-by bots — real protection is the per-IP/global rate limit and
strict size caps. The heartbeat secret is a different value on purpose.
"""

import hmac
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path

from aiohttp import web

from config import config
from db.database import get_state, heartbeat_ping, save_feedback, set_state
from reports.formatter import format_feedback
from services import integrations, notifier, public_urls, secrets, settings
from services.notifier import Priority
from utils.text import esc
from web import status_page

logger = logging.getLogger(__name__)

# The widget does a cross-origin fetch with a custom header → CORS preflight;
# both the OPTIONS and the POST response must carry these headers.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Webhook-Secret",
    "Access-Control-Max-Age": "86400",
}
MAX_MESSAGE_LEN = 2000
MAX_URL_LEN = 500
MAX_BODY_BYTES = 64 * 1024
RATE_WINDOW_SEC = 60
RATE_LIMIT_PER_IP = 5
RATE_LIMIT_GLOBAL = 30
_ip_hits: dict[str, deque] = defaultdict(deque)
_global_hits: deque = deque()
_WIDGET = Path(__file__).parent / "feedback-widget.js"


def client_ip(request: web.Request) -> str:
    """Behind a trusted proxy the LAST X-Forwarded-For entry is the one the
    proxy appended; the first is whatever the client claimed."""
    if config.trust_proxy:
        fwd = request.headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[-1].strip() or "unknown"
    return request.remote or "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    cutoff = now - RATE_WINDOW_SEC
    if len(_ip_hits) > 512:  # bound the dict: evict fully-expired windows
        for stale in [k for k, v in _ip_hits.items() if not v or v[-1] < cutoff]:
            del _ip_hits[stale]
    while _global_hits and _global_hits[0] < cutoff:
        _global_hits.popleft()
    hits = _ip_hits[ip]
    while hits and hits[0] < cutoff:
        hits.popleft()
    if len(_global_hits) >= RATE_LIMIT_GLOBAL or len(hits) >= RATE_LIMIT_PER_IP:
        if not hits:
            del _ip_hits[ip]
        return True
    _global_hits.append(now)
    hits.append(now)
    return False


def _cors(resp: web.Response) -> web.Response:
    resp.headers.update(CORS_HEADERS)
    return resp


def _secret_ok(given: str, expected: str) -> bool:
    # Constant-time comparison on BYTES: compare_digest raises TypeError on
    # non-ASCII str input, which would turn a bad header into a 500.
    return hmac.compare_digest(given.encode(), expected.encode())


async def handle_feedback_options(request: web.Request) -> web.Response:
    return _cors(web.Response(status=204))


async def handle_feedback(request: web.Request) -> web.Response:
    ip = client_ip(request)
    if _rate_limited(ip):
        logger.warning("Feedback: rate limit hit from %s", ip)
        return _cors(web.Response(status=429, text="Too Many Requests"))
    if not _secret_ok(request.headers.get("X-Webhook-Secret", ""), secrets.webhook_secret()):
        logger.warning("Feedback: invalid secret from %s", ip)
        return _cors(web.Response(status=403, text="Forbidden"))
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        return _cors(web.Response(status=400, text="Invalid JSON"))
    message = str(data.get("message") or "").strip()[:MAX_MESSAGE_LEN]
    if not message:
        return _cors(web.Response(status=400, text="message is required"))
    site_url = str(data.get("site_url") or "unknown")[:MAX_URL_LEN]
    page_url = str(data.get("page_url") or site_url)[:MAX_URL_LEN]
    user_agent = request.headers.get("User-Agent", "")[:MAX_URL_LEN]

    feedback_id = await save_feedback(site_url, page_url, message, user_agent, ip)
    await notifier.send(format_feedback(site_url, page_url, message, feedback_id, ip),
                        Priority.NORMAL)
    return _cors(web.json_response({"ok": True, "id": feedback_id}, status=201))


async def handle_heartbeat(request: web.Request) -> web.Response:
    """Dead-man switch ping: GET/POST /api/heartbeat/<secret>/<job>.
    The token lives in the path, healthchecks.io-style, so a plain
    `curl <url>` at the end of a cron line is the whole integration."""
    token = request.match_info.get("token", "")
    job = request.match_info.get("job", "")[:64]
    legacy = False
    if not _secret_ok(token, secrets.heartbeat_secret()):
        # Installations older than the split secrets pinged with the
        # webhook secret. Keep them alive, but nag once per job.
        if _secret_ok(token, secrets.webhook_secret()):
            legacy = True
        else:
            return web.Response(status=403, text="Forbidden")
    if not job:
        return web.Response(status=400, text="job name required")
    await heartbeat_ping(job)
    if legacy and not await get_state(f"hb_legacy_warned:{job}"):
        sent = await notifier.send(
            f"⚠️ Heartbeat «{esc(job)}» пришёл со старым секретом (WEBHOOK_SECRET, "
            f"который виден всем посетителям сайта). Замени URL в кроне на:\n"
            f"<code>{esc(public_urls.heartbeat_url(job))}</code>", Priority.NORMAL)
        if sent:
            await set_state(f"hb_legacy_warned:{job}", "1")
    if await get_state(f"hb_alerted:{job}"):
        await set_state(f"hb_alerted:{job}", None)
        await notifier.send(f"💚 Heartbeat «{esc(job)}» снова подаёт сигналы", Priority.NORMAL)
    return web.Response(text="ok")


async def handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


# ── Public status page ───────────────────────────────────────────────────────

def _status_allowed(request: web.Request) -> bool:
    """Page enabled AND the path matches: bare /status when no slug is set,
    /status/<slug> when it is."""
    if not settings.status_page_enabled():
        return False
    got = request.match_info.get("slug", "")
    slug = integrations.status_page_slug()
    if slug:
        return _secret_ok(got, slug)
    return got == ""


_PRIVATE = {"Cache-Control": "public, max-age=30", "X-Robots-Tag": "noindex, nofollow"}


async def handle_status_page(request: web.Request) -> web.Response:
    if not _status_allowed(request):
        return web.Response(status=404, text="Not Found")
    return web.Response(text=await status_page.status_html(), content_type="text/html",
                        charset="utf-8", headers=_PRIVATE)


async def handle_status_json(request: web.Request) -> web.Response:
    if not _status_allowed(request):
        return web.Response(status=404, text="Not Found")
    return web.json_response(await status_page.status_json(), headers=_PRIVATE)


# ASCII digits only, bounded: an astronomically long id would overflow SQLite.
_SID_RE = re.compile(r"[0-9]{1,9}")


async def handle_status_badge(request: web.Request) -> web.Response:
    if not _status_allowed(request):
        return web.Response(status=404, text="Not Found")
    sid = request.match_info.get("sid", "").removesuffix(".svg")
    if not _SID_RE.fullmatch(sid):
        return web.Response(status=404, text="Not Found")
    found, pct = await status_page.badge_pct(int(sid))
    if not found:
        return web.Response(status=404, text="Not Found")
    return web.Response(text=status_page.badge_svg(pct), content_type="image/svg+xml",
                        charset="utf-8",
                        headers={"Cache-Control": "public, max-age=300",
                                 "X-Robots-Tag": "noindex, nofollow"})


async def handle_widget(request: web.Request) -> web.Response:
    return web.Response(text=_WIDGET.read_text("utf-8"), content_type="application/javascript",
                        charset="utf-8",
                        headers={"Access-Control-Allow-Origin": "*",
                                 "Cache-Control": "public, max-age=3600"})


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app.router.add_post("/api/feedback", handle_feedback)
    app.router.add_options("/api/feedback", handle_feedback_options)
    app.router.add_get("/api/heartbeat/{token}/{job}", handle_heartbeat)
    app.router.add_post("/api/heartbeat/{token}/{job}", handle_heartbeat)
    app.router.add_get("/feedback-widget.js", handle_widget)
    app.router.add_get("/health", handle_health)
    # Registration order matters: literal segments before the {slug} catch-all.
    app.router.add_get("/status", handle_status_page)
    app.router.add_get("/status.json", handle_status_json)
    app.router.add_get("/status/badge/{sid}", handle_status_badge)
    app.router.add_get("/status/{slug}.json", handle_status_json)
    app.router.add_get("/status/{slug}/badge/{sid}", handle_status_badge)
    app.router.add_get("/status/{slug}", handle_status_page)
    return app


async def start_web_server() -> web.AppRunner:
    runner = web.AppRunner(create_app())
    await runner.setup()
    await web.TCPSite(runner, host="0.0.0.0", port=config.webhook_port).start()
    logger.info("Web server running on port %s", config.webhook_port)
    return runner
