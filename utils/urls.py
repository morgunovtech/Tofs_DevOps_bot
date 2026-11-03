"""URL helpers used by every monitor, formatter and handler. One place for
the registrable-domain heuristic, the User-Agent and the monitor kinds."""

from urllib.parse import urlparse

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


def is_http_url(url: str) -> bool:
    """True for web monitors; tcp:// and ping:// get availability only."""
    return url.startswith(("http://", "https://"))


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


def site_label(url: str) -> str:
    """Compact display label: host for web sites (plus the path when the
    monitor watches one — an API endpoint next to its site must not look
    like the same thing; never the query, it may carry a token), host:port
    for tcp:// monitors, 'ping host' for ping:// ones."""
    p = urlparse(url)
    if p.scheme == "tcp":
        return f"{p.hostname}:{p.port}" if p.port else (p.hostname or url)
    if p.scheme == "ping":
        return f"ping {p.hostname}" if p.hostname else url
    if not p.hostname:
        return url
    return p.hostname + (p.path or "").rstrip("/")


def registrable_domain(host: str) -> str:
    """example.com for www.example.com; example.co.uk for a.example.co.uk."""
    host = (host or "").lower().rstrip(".")
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    if ".".join(parts[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def same_site(url_a: str, url_b: str) -> bool:
    a, b = host_of(url_a), host_of(url_b)
    return bool(a and b) and registrable_domain(a) == registrable_domain(b)
