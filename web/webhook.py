"""aiohttp web server: feedback from site widgets, heartbeat pings, the
public status page (+JSON, +badges) and the widget file itself.

SECURITY MODEL of /api/feedback: the widget ships the secret to every site
visitor, so this endpoint is effectively PUBLIC. The secret only filters
lazy drive-by bots — real protection is that browsers may post only from the
owner's sites (CORS), separate rate limits per address and per site, strict
size caps, a budget of chat messages per site — the rest is stored and summed
up once an hour. The heartbeat secret is a different value on purpose.
"""

import asyncio
import hmac
import ipaddress
import logging
import re
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import web

from config import config
from db.database import get_all_sites, get_heartbeats, get_state, heartbeat_ping, save_feedback, set_state
from reports.formatter import format_feedback
from services import health, heartbeats, integrations, notifier, public_urls, screenshots, secrets, settings
from services.notifier import Priority
from utils.text import esc, fmt_duration, parse_sqlite_utc, plural
from utils.urls import bare_host, host_of, is_http_url
from web import status_page, widget

logger = logging.getLogger(__name__)

# The widget does a cross-origin fetch with a custom header → CORS preflight;
# both the OPTIONS and the POST response carry these headers — and the origin
# itself only when it is one of the owner's sites.
CORS_HEADERS = {
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-Webhook-Secret",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
}
MAX_MESSAGE_LEN = 2000
MAX_URL_LEN = 500
MAX_BODY_BYTES = 64 * 1024
RATE_WINDOW_SEC = 60
RATE_LIMIT_PER_IP = 5           # accepted messages per address (IPv6: per /64)
RATE_LIMIT_PER_SITE = 30        # accepted messages per site — one site's flood does not close the others
RATE_LIMIT_BAD_SECRET = 20      # wrong-secret attempts per address: they never touch the quotas above
NOTIFY_PER_HOUR = 5             # chat messages per site; the rest is stored and summed up hourly
_ip_hits: dict[str, deque] = defaultdict(deque)
_site_hits: dict[str, deque] = defaultdict(deque)
_bad_hits: dict[str, deque] = defaultdict(deque)
_notified: dict[str, deque] = defaultdict(deque)
_held: dict[str, int] = defaultdict(int)
_warned: dict[str, float] = {}                  # log lines said at most once an hour, by key
_origins: tuple[float, list[str]] | None = None  # (when fetched, site hosts); None — not yet
WIDGET_SERVED = "widget:served"                 # the widget version sites have loaded
_widget_noted: set[str] = set()
UNKNOWN_SITE = "без адреса сайта"
# The server shares the process — and the container's file-descriptor limit,
# 1024 by default — with every check the bot makes. A real client sends its
# request within milliseconds; a socket that stays silent only holds a slot.
KEEPALIVE_SEC = 15
MAX_CONNECTIONS = 200
MAX_HEADER_BYTES = 4096


def _networks(raw: tuple[str, ...]) -> list:
    nets = []
    for item in raw:
        try:
            nets.append(ipaddress.ip_network(item.strip(), strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES: %r is not a network — ignored", item)
    return nets


_TRUSTED = _networks(config.trusted_proxies)


def _trusted(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED)


def _once_an_hour(key: str) -> bool:
    """True the first time `key` is seen within an hour: a log line per
    stranger, not per request — a flood must not become a flood of warnings."""
    now = time.monotonic()
    if len(_warned) > 512:
        for stale in [k for k, t in _warned.items() if t < now - 3600]:
            del _warned[stale]
    if _warned.get(key, float("-inf")) > now - 3600:
        return False
    _warned[key] = now
    return True


def client_ip(request: web.Request) -> str:
    """The visitor's address. X-Forwarded-For counts only with TRUST_PROXY=1,
    and only entries that are IP addresses — an arbitrary string is not an
    address, it is text from whoever sent it. With TRUSTED_PROXIES empty the
    LAST entry is the visitor (the one the proxy in front appended); with it
    set, the header is read right-to-left past our own proxies, and only
    when the peer itself is one of them."""
    peer = request.remote or ""
    forwarded = request.headers.get("X-Forwarded-For", "") if config.trust_proxy else ""
    if not forwarded:
        return peer or "unknown"
    hops = [hop.strip() for hop in forwarded.split(",")]
    if not _TRUSTED:
        try:
            return str(ipaddress.ip_address(hops[-1]))
        except ValueError:
            return peer or "unknown"
    if not _trusted(peer):
        if _once_an_hour(f"peer:{peer}"):
            logger.warning("X-Forwarded-For from %s ignored — add it to TRUSTED_PROXIES", peer or "unknown")
        return peer or "unknown"
    client = peer
    for hop in reversed(hops):
        try:
            client = str(ipaddress.ip_address(hop))
        except ValueError:
            break
        if not _trusted(client):
            break
    return client or "unknown"


def _ip_key(ip: str) -> str:
    """IPv6 addresses are cheap to rotate inside one /64 — count the network."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    return str(ipaddress.ip_network(f"{addr}/64", strict=False)) if addr.version == 6 else ip


def _over(windows: dict[str, deque], key: str, limit: int, period: float = RATE_WINDOW_SEC) -> bool:
    """True when `key` already used its `limit` within `period`; otherwise counts this hit."""
    now = time.monotonic()
    if len(windows) > 512:  # bound the dict: evict fully-expired windows
        for stale in [k for k, v in windows.items() if not v or v[-1] < now - period]:
            del windows[stale]
    hits = windows[key]
    while hits and hits[0] < now - period:
        hits.popleft()
    if len(hits) >= limit:
        return True
    hits.append(now)
    return False


async def _site_hosts() -> list[str]:
    """Hosts of the monitored sites without a leading www., the most specific
    first; fetched at most once a minute. None, not «0.0», marks an empty
    cache: monotonic time starts at boot, and a container that comes up with
    the host would otherwise refuse every site for the first minute."""
    global _origins
    if _origins is None or time.monotonic() - _origins[0] > 60:
        hosts = {bare_host(host_of(s["url"])) for s in await get_all_sites() if is_http_url(s["url"])}
        _origins = (time.monotonic(), sorted(filter(None, hosts), key=len, reverse=True))
    return _origins[1]


def _under(host: str, site_host: str) -> bool:
    return host == site_host or host.endswith("." + site_host)


async def _site_for_origin(origin: str) -> str | None:
    """The owner's site this Origin belongs to — its host (www. stripped),
    None when it is not one of them. www. and subdomains of the site count;
    a neighbour on the same platform (evil.github.io next to shop.github.io)
    does not, which is why hosts are compared, not registrable domains."""
    host = host_of(origin)
    if not host:
        return None
    for site_host in await _site_hosts():
        if _under(host, site_host):
            return site_host
    return None


def _cors(resp: web.Response, origin: str | None = None) -> web.Response:
    resp.headers.update(CORS_HEADERS)
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
    return resp


def _secret_ok(given: str, expected: str) -> bool:
    # Constant-time comparison on BYTES; surrogateescape: aiohttp turns a raw
    # non-UTF-8 header byte into a lone surrogate, plain .encode() would raise.
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"),
                               expected.encode("utf-8", "surrogateescape"))


def _text(value, limit: int) -> str:
    """A string the DB and Telegram accept: lone surrogates become «?»."""
    return str(value).encode("utf-8", "replace").decode("utf-8")[:limit]


def _without_query(url: str) -> str:
    """Query strings carry reset tokens, e-mails and order numbers; the
    owner needs only the page. The widget asks for more with fullUrl:true."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.split("?", 1)[0].split("#", 1)[0]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


async def read_json(request: web.Request) -> tuple[object, web.Response | None]:
    """(data, None) or (None, the answer to give): 413 for a body over
    client_max_size, 400 for anything that is not JSON — never a 500."""
    try:
        return await request.json(), None
    except web.HTTPRequestEntityTooLarge:
        return None, web.Response(status=413, text="Payload too large")
    except (ValueError, RecursionError, UnicodeDecodeError):
        return None, web.Response(status=400, text="Invalid JSON")


def _foreign(origin: str) -> web.Response:
    """A page that is not the owner's: 403 without Access-Control-Allow-Origin,
    so the browser will not POST. Said in the log — once an hour per origin —
    so the owner learns why a widget on a site not yet in the bot is silent."""
    if _once_an_hour(f"origin:{origin}"):
        logger.warning("Feedback: Origin %s is not a monitored site", origin)
    return _cors(web.Response(status=403, text="Forbidden"))


async def handle_feedback_options(request: web.Request) -> web.Response:
    origin = request.headers.get("Origin", "")
    if origin and await _site_for_origin(origin):
        return _cors(web.Response(status=204), origin)
    return _foreign(origin or "(none)")


async def handle_feedback(request: web.Request) -> web.Response:
    ip = client_ip(request)
    origin = request.headers.get("Origin", "")
    site = await _site_for_origin(origin) if origin else None
    if origin and site is None:
        return _foreign(origin)
    allow = origin if site else None
    if not _secret_ok(request.headers.get("X-Webhook-Secret", ""), secrets.webhook_secret()):
        if _over(_bad_hits, _ip_key(ip), RATE_LIMIT_BAD_SECRET):
            return _cors(web.Response(status=429, text="Too Many Requests"), allow)
        logger.warning("Feedback: invalid secret from %s", ip)
        return _cors(web.Response(status=403, text="Forbidden"), allow)
    site_key = site or UNKNOWN_SITE
    if _over(_ip_hits, _ip_key(ip), RATE_LIMIT_PER_IP) or _over(_site_hits, site_key, RATE_LIMIT_PER_SITE):
        logger.warning("Feedback: rate limit hit from %s for %s", ip, site_key)
        return _cors(web.Response(status=429, text="Too Many Requests"), allow)
    data, error = await read_json(request)
    if error is not None or not isinstance(data, dict):
        return _cors(error or web.Response(status=400, text="Invalid JSON"), allow)
    message = _text(data.get("message") or "", MAX_MESSAGE_LEN).strip()
    if not message:
        return _cors(web.Response(status=400, text="message is required"), allow)
    site_url = _text(data.get("site_url") or "unknown", MAX_URL_LEN)
    page_url = str(data.get("page_url") or site_url)
    if data.get("full_url") is not True:
        page_url = _without_query(page_url)
    page_url = _text(page_url, MAX_URL_LEN)
    # «Сайт:» in the chat is what the browser vouches for (Origin), not what the body claims.
    verified = site is not None and _under(host_of(site_url), site)
    try:
        # The User-Agent is not kept: nothing showed it, and it is one more
        # thing about the visitor that the owner never asked for.
        feedback_id = await save_feedback(site_url, page_url, message, "", ip)
        if _may_notify(site_key):
            await notifier.send(format_feedback(site_url, page_url, message, feedback_id, ip,
                                                verified=verified), Priority.NORMAL)
    except Exception:
        logger.exception("Feedback could not be stored or sent")
        return _cors(web.Response(status=500, text="Internal Server Error"), allow)
    return _cors(web.json_response({"ok": True, "id": feedback_id}, status=201), allow)


def _may_notify(site_key: str) -> bool:
    """Up to NOTIFY_PER_HOUR chat messages per site; the rest waits for the hourly summary."""
    if _over(_notified, site_key, NOTIFY_PER_HOUR, period=3600):
        _held[site_key] += 1
        return False
    return True


async def send_feedback_summary():
    """Hourly: what the per-site budget held back — one quiet message per site."""
    held = {k: n for k, n in _held.items() if n}
    _held.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📩 Сообщения",
                                                                     callback_data="menu_feedback:0")]])
    for site, n in held.items():
        await notifier.send(f"📩 {esc(site)}: ещё {n} {plural(n, 'сообщение', 'сообщения', 'сообщений')} "
                            f"с сайта за час. Я их сохранил, но не присылал по одному — открыть все:",
                            Priority.DIGEST, reply_markup=kb)


async def handle_heartbeat(request: web.Request) -> web.Response:
    """Dead-man switch ping: GET/POST /api/heartbeat/<job's token>/<job>.
    The token lives in the path, healthchecks.io-style, so a plain
    `curl <url>` at the end of a cron line is the whole integration. Each
    job has its own token (services.secrets): a leaked line exposes one job.
    The old address with the shared secret still works — the owner is told
    once per job to move it. Never the webhook secret: it sits in every
    site's widget code, and accepting it would let any visitor keep a dead
    job «alive». A job the bot does not wait for is 404: a typo in the cron
    line fails there, not as a false «молчит» weeks later."""
    token = request.match_info.get("token", "")
    job = request.match_info.get("job", "")[:64]
    if not job:
        return web.Response(status=400, text="job name required")
    own = _secret_ok(token, secrets.heartbeat_token(job))
    shared = not own and _secret_ok(token, secrets.heartbeat_secret())
    if not (own or shared):
        return web.Response(status=403, text="Forbidden")
    if job not in settings.heartbeat_jobs():
        return web.Response(status=404, text="unknown job: add it in the bot first")
    if shared:
        await set_state(f"hb_legacy:{job}", datetime.now(UTC).isoformat(timespec="seconds"))
        if not await get_state(f"hb_legacy_noted:{job}"):
            await set_state(f"hb_legacy_noted:{job}", "1")
            await notifier.send(
                f"⚠️ «{esc(job)}» выходит на связь по старому адресу — общему для всех задач. Он продолжает "
                f"работать, но лучше заменить его на собственный адрес задачи: тогда утечка одной строки не "
                f"откроет остальные. Адрес — в карточке задачи, «🔗 Как подключена».", Priority.NORMAL)
    elif await get_state(f"hb_legacy:{job}"):
        await set_state(f"hb_legacy:{job}", None)      # moved to its own address
    mark = True
    if request.method == "POST" and request.content_type == "application/json":
        # A bot may send what it sees along with the mark (services.heartbeats).
        data, error = await read_json(request)
        if error is not None:
            return error
        try:
            status = heartbeats.parse_status(data)
        except (ValueError, heartbeats.StatusError) as e:
            return web.Response(status=400, text=f"bad status: {e}")
        mark = data.get("heartbeat", True) is not False
        await heartbeats.save_status(job, status)
    if mark:
        last = parse_sqlite_utc((await get_heartbeats()).get(job))
        await heartbeat_ping(job)
        if await get_state(f"hb_alerted:{job}"):
            await set_state(f"hb_alerted:{job}", None)
            away = fmt_duration((datetime.now(UTC) - last).total_seconds() / 60) if last else None
            await notifier.send(f"✅ «{esc(job)}» снова на связи — без связи было {away}." if away
                                else f"✅ «{esc(job)}» вышел на связь — теперь слежу за ним.", Priority.NORMAL)
    return web.Response(text="ok")


def _file_head(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read(12)


async def handle_shot(request: web.Request) -> web.StreamResponse:
    """The site card's picture (services.screenshots), on a signed address.
    Up to 10 MB: sent from disk by aiohttp (sendfile), not read into memory
    and not read in the event loop; the first bytes name the image type."""
    try:
        site_id = int(request.match_info.get("sid", ""))
    except ValueError:
        return web.Response(status=404)
    if not screenshots.sig_ok(site_id, request.match_info.get("sig", "")):
        return web.Response(status=404)
    path = screenshots.shot_path(site_id)
    try:
        head = await asyncio.to_thread(_file_head, path)
    except OSError:
        return web.Response(status=404)
    return web.FileResponse(path, headers={"Content-Type": screenshots.image_type(head),
                                           "Cache-Control": "public, max-age=3600"})


async def handle_health(request: web.Request) -> web.Response:
    """200 only while the bot does its job: the availability tick finishes,
    Telegram answers, the database takes writes (services.health). The list
    of what is wrong goes to the log, not to whoever asks: the page is public."""
    problems = health.problems()
    if problems:
        logger.warning("/health: stale — %s", ", ".join(problems))
        return web.json_response({"status": "stale"}, status=503)
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


async def handle_badge(request: web.Request) -> web.Response:
    """/badge/<id>-<token>.svg — the address the bot gives for a README: its
    own key, no page slug in it. Served while the status page is on."""
    if not settings.status_page_enabled():
        return web.Response(status=404, text="Not Found")
    sid = int(request.match_info["sid"])
    if not public_urls.badge_token_ok(sid, request.match_info["tok"]):
        return web.Response(status=404, text="Not Found")
    found, pct = await status_page.badge_pct(sid)
    if not found:
        return web.Response(status=404, text="Not Found")
    return web.Response(text=status_page.badge_svg(pct), content_type="image/svg+xml",
                        charset="utf-8",
                        headers={"Cache-Control": "public, max-age=300",
                                 "X-Robots-Tag": "noindex, nofollow"})


async def handle_status_badge(request: web.Request) -> web.Response:
    """The old badge addresses (/status/badge/… and /status/<slug>/badge/…):
    kept for badges already pasted into READMEs, to be removed in a later
    release — the bot hands out handle_badge's address now."""
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


def _widget(cache: str) -> web.Response:
    # ACAO * is what lets a site load the file with crossorigin="anonymous"
    # and check its integrity — do not narrow it, and never add
    # Cross-Origin-Resource-Policy: same-origin to this answer.
    return web.Response(body=widget.BODY, content_type="application/javascript", charset="utf-8",
                        headers={"Access-Control-Allow-Origin": "*", "Cache-Control": cache})


async def handle_widget(request: web.Request) -> web.Response:
    """The unversioned address that old snippets load: same bytes, short cache."""
    return _widget("public, max-age=3600")


async def handle_widget_versioned(request: web.Request) -> web.Response:
    """The address the snippet pins with its integrity hash: a year in the
    cache, since a different file lives at a different address."""
    if request.match_info["ver"] != widget.VERSION:
        return web.Response(status=404, text="Not Found")
    if widget.VERSION not in _widget_noted:      # one write per run, not per visitor
        _widget_noted.add(widget.VERSION)
        await set_state(WIDGET_SERVED, widget.VERSION)
    return _widget("public, max-age=31536000, immutable")


async def widget_changed_notice() -> None:
    """Sites pin the widget by its hash: after an update that changed the
    file, a snippet with the old hash loads nothing. Say so once."""
    served = await get_state(WIDGET_SERVED)
    if served and served != widget.VERSION:
        await set_state(WIDGET_SERVED, None)
        await notifier.send("📩 Виджет обратной связи обновился вместе с ботом. На сайтах со старым сниппетом "
                            "он больше не появляется — возьми новый: ⚙️ Настройки → 📩 Обратная связь.",
                            Priority.NORMAL)


async def _harden(request: web.Request, response: web.StreamResponse) -> None:
    """No versions for scanners; the browser gets the safe defaults. Only the
    application's answers: the 400s aiohttp forms itself at the protocol level
    (a broken request line, a raw byte in the path) never reach this signal and
    still name its version — only a reverse proxy in front hides those."""
    response.headers["Server"] = "TofsDevOps"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    if response.content_type == "text/html":
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")


def create_app() -> web.Application:
    app = web.Application(client_max_size=MAX_BODY_BYTES)
    app.on_response_prepare.append(_harden)   # runs before aiohttp sets its own Server header
    app.router.add_post("/api/feedback", handle_feedback)
    app.router.add_options("/api/feedback", handle_feedback_options)
    # No HEAD: link checkers and chat previews send it — a look is not a run.
    app.router.add_get("/api/heartbeat/{token}/{job}", handle_heartbeat, allow_head=False)
    app.router.add_post("/api/heartbeat/{token}/{job}", handle_heartbeat)
    app.router.add_get("/feedback-widget.js", handle_widget)
    app.router.add_get("/feedback-widget.{ver:[0-9a-f]{12}}.js", handle_widget_versioned)
    app.router.add_get("/health", handle_health)
    app.router.add_get("/shot/{sig}/{sid}", handle_shot)
    app.router.add_get(r"/badge/{sid:\d{1,9}}-{tok:[0-9a-f]{16}}.svg", handle_badge)
    # Registration order matters: literal segments before the {slug} catch-all.
    app.router.add_get("/status", handle_status_page)
    app.router.add_get("/status.json", handle_status_json)
    app.router.add_get("/status/badge/{sid}", handle_status_badge)
    app.router.add_get("/status/{slug}.json", handle_status_json)
    app.router.add_get("/status/{slug}/badge/{sid}", handle_status_badge)
    app.router.add_get("/status/{slug}", handle_status_page)
    return app


def _cap_connections(server: web.Server, limit: int) -> None:
    """aiohttp has no ceiling on open connections, and middleware never sees a
    socket that sends nothing, so the ceiling sits where aiohttp registers
    every accepted socket. Past it the OLDEST connections are closed: a flood
    of silent sockets cannot eat the process's file descriptors, and a fresh
    request (the newest connection) still gets through."""
    register = server.connection_made

    def connection_made(handler: web.RequestHandler, transport: asyncio.Transport) -> None:
        register(handler, transport)
        open_now = server.connections            # oldest first
        for victim in open_now[:max(0, len(open_now) - limit)]:
            victim.force_close()

    server.connection_made = connection_made


async def start_web_server(host: str = "0.0.0.0", port: int | None = None) -> web.AppRunner:
    await widget_changed_notice()       # notifier is configured before the server starts (main.py)
    # No access log: request lines carry the heartbeat secret and the status
    # page slug in the path, and stdout ends up in the hosting's logs.
    # A request in flight must not stretch a stop past the container's grace period.
    runner = web.AppRunner(create_app(), access_log=None, keepalive_timeout=KEEPALIVE_SEC, shutdown_timeout=5,
                           max_line_size=MAX_HEADER_BYTES, max_field_size=MAX_HEADER_BYTES)
    await runner.setup()
    _cap_connections(runner.server, MAX_CONNECTIONS)
    port = config.webhook_port if port is None else port
    await web.TCPSite(runner, host=host, port=port).start()
    logger.info("Web server running on port %s", port)
    return runner
