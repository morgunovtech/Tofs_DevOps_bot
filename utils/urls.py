"""URL helpers used by every monitor, formatter and handler. One place for
the registrable-domain heuristic, the User-Agent and the monitor kinds."""

import ipaddress
import re
from urllib.parse import urlparse

import idna

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 TofsDevOps/2.0"
)

# Multi-part public suffixes where "last two labels" would be wrong.
# Small and explicit on purpose: the sites this bot watches are a handful
# of personal domains, and pulling the whole PSL for that is overkill.
_TWO_LEVEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk",
    "com.au", "net.au", "org.au", "com.br", "com.cn", "com.tr",
    "co.jp", "ne.jp", "or.jp", "co.kr", "co.nz", "co.za", "com.ua",
    "spb.ru", "msk.ru", "com.ru", "net.ru", "org.ru", "in.ua", "kiev.ua",
})
# A «generic» second label under a two-letter country zone (com.kz, org.ua,
# gob.mx, ac.za, …) is a public suffix too: this one rule covers most of
# the ICANN section of the PSL without pulling the list in.
_GENERIC_SLDS = frozenset({
    "com", "net", "org", "co", "gov", "edu", "ac", "or", "ne", "go", "gob", "mil", "nom", "in",
})

# Shared hosting platforms: x.github.io or nas.synology.me is not the
# owner's domain — its registration, NS and mail are the platform's
# business, and a change there is nothing the owner can act on.
PLATFORM_DOMAINS = frozenset({
    "pages.dev", "workers.dev", "github.io", "vercel.app", "netlify.app", "herokuapp.com",
    "railway.app", "onrender.com", "fly.dev", "web.app", "firebaseapp.com", "appspot.com",
    "azurewebsites.net", "glitch.me", "surge.sh", "replit.app", "deno.dev", "gitlab.io",
    "synology.me", "duckdns.org", "keenetic.pro", "myqnapcloud.com", "tilda.ws", "wixsite.com",
})


def is_http_url(url: str) -> bool:
    """True for web monitors; tcp:// and ping:// get availability only."""
    return url.startswith(("http://", "https://"))


def ssl_applies(url: str) -> bool:
    """THE rule for certificates: only an https:// monitor has one to check.
    On an http:// one port 443 answers with the hosting's default
    certificate (a false «недействителен»), and http://host:8080 has no TLS
    at all. The daily check, the card, the main screen, «🔄 Перепроверить»
    and adding a site all ask this."""
    return url.lower().startswith("https://")


def display_host(host: str) -> str:
    """xn--e1afmkfd.xn--p1ai → пример.рф for screens and messages; a name
    that does not decode stays as stored."""
    if "xn--" not in host:
        return host
    try:
        return idna.decode(host)
    except UnicodeError:   # idna.IDNAError is a UnicodeError
        return host


# Methods that read a page. Any other one (POST, PUT, …) calls a function.
PAGE_METHODS = frozenset({"GET", "HEAD"})


def is_api_url(url: str) -> bool:
    """The address alone says «API»: the path is /api or under /api/, or
    the host is api.<something>."""
    if not is_http_url(url):
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    path = (p.path or "").lower()
    return path == "/api" or path.startswith("/api/") or (p.hostname or "").lower().startswith("api.")


def is_api_check(url: str, method: str | None = None) -> bool:
    """THE rule: an http(s) monitor is an API check — not a web page — when
    its request is not GET/HEAD, or its address is an API one (is_api_url).
    An API check gets availability only: status code, the phrase in the
    response, response time. No SEO/GEO audit, links, deep 5xx sampling,
    sitemap, page meta or AI-crawler probes — they would read a JSON answer
    (or a 405 to their GET) as a broken page."""
    if not is_http_url(url):
        return False
    return (method or "GET").strip().upper() not in PAGE_METHODS or is_api_url(url)


def is_web_page(url: str, method: str | None = None) -> bool:
    """Input filter of every page-oriented check: an http(s) monitor that is
    not an API check. tcp:// and ping:// are not pages either."""
    return is_http_url(url) and not is_api_check(url, method)


def host_key(url: str) -> str:
    """scheme://host:port — what one certificate and one DNS answer cover."""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{(p.hostname or '').lower()}:{p.port or ''}"
    except ValueError:
        return url


def host_of(url: str) -> str:
    """Lower-cased hostname, '' when the URL has none."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def short_host(url: str) -> str:
    return host_of(url) or url


def bare_host(host: str) -> str:
    """The host without a leading «www.»: one site however it is spelled."""
    return host[4:] if host.startswith("www.") else host


_USERINFO_RE = re.compile(r"//[^/?#@]*@")


def site_label(url: str) -> str:
    """Compact display label: host for web sites (plus the path when the
    monitor watches one — an API endpoint next to its site must not look
    like the same thing; never the query, it may carry a token), host:port
    for tcp:// monitors ([v6]:port for an IPv6 address), 'ping host' for
    ping:// ones. International names are shown as people write them.

    Never raises: one malformed stored address (a port «5432x», an
    unclosed «[») must not take down every screen, report and alert loop
    that lists sites. It is shown as stored — without the query and the
    login, which may carry secrets."""
    try:
        return _label(url)
    except ValueError:
        return _USERINFO_RE.sub("//", url.split("?", 1)[0].split("#", 1)[0])


def _label(url: str) -> str:
    p = urlparse(url)
    port = p.port
    host = display_host(p.hostname or "")
    if p.scheme == "tcp":
        shown = f"[{host}]" if ":" in host else host
        return f"{shown}:{port}" if host and port else (shown or url)
    if p.scheme == "ping":
        return f"ping {host}" if host else url
    if not host:
        return url
    return host + (p.path or "").rstrip("/")


def registrable_domain(host: str) -> str:
    """example.com for www.example.com; example.co.uk for a.example.co.uk;
    example.com.kz for shop.example.com.kz; an IP address stays itself."""
    host = (host or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return host
    except ValueError:
        pass
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    suffix = ".".join(parts[-2:])
    if suffix in _TWO_LEVEL_SUFFIXES or (parts[-2] in _GENERIC_SLDS and len(parts[-1]) == 2):
        return ".".join(parts[-3:])
    return suffix


def owned_domain(host: str) -> str | None:
    """The owner's registrable domain behind host — None when there is none
    to watch: an IP address, a single-label name, a platform's domain."""
    host = (host or "").lower().rstrip(".").strip("[]")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        pass
    domain = registrable_domain(host)
    if "." not in domain or domain in PLATFORM_DOMAINS:
        return None
    return domain


def same_site(url_a: str, url_b: str) -> bool:
    a, b = host_of(url_a), host_of(url_b)
    return bool(a and b) and registrable_domain(a) == registrable_domain(b)


# ── Proxy for Telegram (TELEGRAM_PROXY) ──────────────────────────────────────
# What aiogram's proxy support (aiohttp-socks) can parse; anything else fails
# deep inside it with «Invalid scheme component».
PROXY_SCHEMES = ("socks5", "socks4", "http")


def proxy_problem(url: str) -> str | None:
    """None when TELEGRAM_PROXY can be used, otherwise what is wrong — said
    at start instead of a traceback from the proxy library."""
    try:
        p = urlparse(url)
    except ValueError:
        return "адрес записан с ошибкой (лишняя или незакрытая «[») — нужен вид socks5://host:1080"
    if p.scheme not in PROXY_SCHEMES:
        return (f"схема «{p.scheme or 'нет'}» не подходит — нужен адрес вида socks5://host:1080, "
                f"socks4://host:1080 или http://host:3128")
    if not p.hostname:
        return "нет адреса сервера — нужен вид socks5://host:1080"
    try:
        port = p.port
    except ValueError:
        return "порт не число — нужен вид socks5://host:1080"
    if not port:
        return "нет порта — нужен вид socks5://host:1080"
    return None


def proxy_label(url: str) -> str:
    """scheme://host:port — for logs and screens, never the login or password."""
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname}:{p.port}"
