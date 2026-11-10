"""Basic security of a site — what a visitor or a stranger can see from
outside, nothing intrusive:

- service files open to anyone: /.git/HEAD (the whole code and history
  can be downloaded) and /.env (passwords and keys) — 🔴 «exposed»;
- http:// opens the site without a redirect to https:// — 🟠 «security»;
- the protective headers every site can have without breaking anything:
  HSTS, X-Content-Type-Options, frame protection, Referrer-Policy —
  🟠 «security», with the ready block for the site's hosting (the
  `_headers` file for Cloudflare Pages and Netlify, vercel.json, …).

CSP is said on the screen only: a wrong one breaks the site, so it is
advice, not a problem. A site on GitHub Pages cannot set headers at all —
that is said too, never alerted. Every six hours, https sites only.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import aiohttp

from db.database import get_or_create_site, get_state
from monitors.base import CheckResult, Finding, decode_body, read_capped
from monitors.ladder import apply_incident
from services import humanize, sitestatus
from utils.net import owner_connector
from utils.text import plural
from utils.urls import USER_AGENT

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=15)
SECURITY_CHECKS = ("exposed", "security")   # incident types: 🔴 files open to anyone, 🟠 settings

# The headers asked of every site: (name, value to suggest, what goes wrong without it).
HEADERS: tuple[tuple[str, str, str], ...] = (
    ("Strict-Transport-Security", "max-age=31536000", "браузер может открыть сайт по незащищённому http"),
    ("X-Content-Type-Options", "nosniff", "браузер может выполнить загруженный файл как код"),
    ("X-Frame-Options", "SAMEORIGIN", "сайт можно встроить в чужую страницу и обманом заставить нажимать кнопки"),
    ("Referrer-Policy", "strict-origin-when-cross-origin", "другие сайты видят полные адреса твоих страниц"),
)

_GIT_HEAD = re.compile(r"^(ref: refs/\S+|[0-9a-f]{40})\s*$")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,})\s*=", re.M)
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|PASS|DATABASE_URL|DSN", re.I)


def looks_like_git_head(body: str) -> bool:
    return bool(_GIT_HEAD.match(body.strip()))


def looks_like_env(body: str, content_type: str) -> bool:
    """A real .env, not the site's own 404 page answering 200 (a single-page
    app serves index.html for any path)."""
    if "html" in content_type.lower() or "<html" in body[:500].lower():
        return False
    names = _ENV_LINE.findall(body)
    return len(names) >= 3 or any(_SECRET_NAME.search(n) for n in names)


def missing_headers(headers: dict[str, str]) -> list[str]:
    """Which of HEADERS the answer lacks. Frame protection counts as there
    when the CSP says frame-ancestors."""
    h = {k.lower(): v for k, v in headers.items()}
    missing = []
    for name, _, _ in HEADERS:
        value = h.get(name.lower(), "")
        if name == "Strict-Transport-Security":
            m = re.search(r"max-age\s*=\s*(\d+)", value)
            if not m or int(m.group(1)) == 0:
                missing.append(name)
        elif name == "X-Frame-Options":
            if not value and "frame-ancestors" not in h.get("content-security-policy", "").lower():
                missing.append(name)
        elif not value:
            missing.append(name)
    return missing


def headers_block(hosting_key: str | None, names: list[str]) -> tuple[str, str, str]:
    """(what to do, the block to paste, a note) for the site's hosting."""
    pairs = [(n, v) for n, v, _ in HEADERS if n in names]
    lines = "\n".join(f"{n}: {v}" for n, v in pairs)
    if hosting_key in ("cloudflare", "netlify"):
        note = ("Если это не Cloudflare Pages, а свой сервер за Cloudflare, — добавь те же заголовки в Cloudflare → "
                "Rules → Transform Rules → Modify Response Header." if hosting_key == "cloudflare" else "")
        return ("Положи в папку, которую публикует хостинг (dist, build или public), файл _headers с таким "
                "текстом — или допиши эти строки, если файл уже есть:",
                "/*\n" + "\n".join(f"  {n}: {v}" for n, v in pairs), note)
    if hosting_key == "vercel":
        block = json.dumps({"headers": [{"source": "/(.*)", "headers": [{"key": n, "value": v} for n, v in pairs]}]},
                           indent=2, ensure_ascii=False)
        return ("Добавь в vercel.json в корне проекта:", block,
                "Если vercel.json уже есть — допиши в него только блок headers.")
    return ("Добавь эти заголовки ко всем ответам сайта — в настройках хостинга, в nginx (add_header … always;) "
            "или в самом приложении:", lines, "")


def https_fix(hosting_key: str | None) -> str:
    return {
        "cloudflare": "В Cloudflare → SSL/TLS → Edge Certificates включи «Always Use HTTPS».",
        "github": "В репозитории на GitHub → Settings → Pages включи «Enforce HTTPS».",
        "netlify": "В Netlify → Domain management → HTTPS проверь, что сертификат выпущен: перенаправление "
                   "Netlify включает сам.",
    }.get(hosting_key or "", "Включи на хостинге перенаправление с http:// на https:// (обычно это галочка "
                              "«Force HTTPS»; в nginx — return 301 https://$host$request_uri;).")


def assess(url: str, hosting_key: str | None, git: bool, env: bool, plain_http: bool | None,
           missing: list[str] | None) -> list[Finding]:
    """Pure: what the probes found, each with what to do."""
    out: list[Finding] = []
    origin = _origin(url)
    if git:
        out.append(Finding("exposed_git", "Наружу виден .git — код сайта со всей историей",
                           "любой может скачать весь код сайта со всеми прошлыми версиями — и пароли, если они "
                           "хоть раз были в коде",
                           "Убери папку .git из того, что публикует хостинг: публиковаться должна папка сборки "
                           "(dist, build или public), а не весь репозиторий. Потом смени пароли и ключи, которые "
                           "когда-либо были в коде.", "", f"Проверь сам: {origin}/.git/HEAD"))
    if env:
        out.append(Finding("exposed_env", "Наружу виден файл .env — пароли и ключи",
                           "в нём пароли от базы и ключи сервисов, и забрать их может любой",
                           "Срочно убери .env из публикуемой папки и смени все пароли и ключи из него: считай, "
                           "что их уже забрали.", "", f"Проверь сам: {origin}/.env"))
    if plain_http:
        out.append(Finding("https_missing", "Сайт открывается и без шифрования, по http://",
                           "по http:// пароли и формы можно подсмотреть по дороге, а браузер пишет «Не защищено»",
                           https_fix(hosting_key)))
    if missing and hosting_key != "github":
        what, block, note = headers_block(hosting_key, missing)
        n = len(missing)
        out.append(Finding("headers_missing",
                           f"Не хватает {n} {plural(n, 'защитного заголовка', 'защитных заголовков', 'защитных заголовков')} "
                           f"из {len(HEADERS)}",
                           "; ".join(f"без {name} {without}" for name, _, without in HEADERS if name in missing),
                           what, block, note))
    return out


def summary(findings: list[dict]) -> str:
    rest = len(findings) - 1
    more = f" и ещё {rest} {plural(rest, 'помеха', 'помехи', 'помех')}" if rest else ""
    return f"безопасность — {humanize.lower_first(findings[0]['title'])}{more}"


# ── Probes ───────────────────────────────────────────────────────────────────

def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


async def _get(session: aiohttp.ClientSession, url: str, redirects: bool = False,
               limit: int = 4096) -> tuple[int, dict, str] | None:
    """(status, headers, the start of the body); None = could not connect."""
    try:
        async with session.get(url, allow_redirects=redirects, headers={"User-Agent": USER_AGENT}) as resp:
            body = decode_body(await read_capped(resp, limit), resp.charset)
            return resp.status, dict(resp.headers), body
    except (TimeoutError, aiohttp.ClientError, ValueError) as e:
        logger.debug("Security probe %s: %r", url, e)
        return None


async def plain_http_opens(session: aiohttp.ClientSession, url: str) -> bool | None:
    """True — http:// shows the site itself; False — it leads to https:// or
    does not answer at all (nothing to intercept); None — unclear."""
    p = urlparse(url)
    target = f"http://{p.hostname}/"
    for _ in range(4):                      # http → http://www → https is fine too
        got = await _get(session, target)
        if got is None:
            return False
        status, headers, _ = got
        location = headers.get("Location") or headers.get("location") or ""
        if status in (301, 302, 303, 307, 308) and location:
            if location.startswith("https://"):
                return False
            if not location.startswith("http://"):
                return None                 # a relative redirect on http: still http
            target = location
            continue
        return status < 400
    return None


@dataclass
class SecurityResult(CheckResult):
    findings: list[dict] = field(default_factory=list)
    kind: str = "security"


async def check_security(url: str, manage: bool = True) -> list[SecurityResult]:
    """Both verdicts for one site: [exposed, security]. A homepage that did
    not answer leaves the snapshot and the incidents as they were."""
    site_id = await get_or_create_site(url)
    origin = _origin(url)
    async with aiohttp.ClientSession(timeout=TIMEOUT, connector=owner_connector(url)) as session:
        home, git, env, plain = await asyncio.gather(
            _get(session, url, redirects=True), _get(session, f"{origin}/.git/HEAD"),
            _get(session, f"{origin}/.env"), plain_http_opens(session, url))
    if home is None or home[0] >= 500:
        logger.info("Security check for %s: the site did not answer, keeping the last result", url)
        return []
    hosting_key = await get_state(f"hosting:{site_id}")
    git_open = bool(git and git[0] == 200 and looks_like_git_head(git[2]))
    env_open = bool(env and env[0] == 200 and looks_like_env(env[2], env[1].get("Content-Type", "")))
    missing = missing_headers(home[1])
    findings = [f.as_dict() for f in assess(url, hosting_key, git_open, env_open, plain, missing)]
    csp = any(k.lower() == "content-security-policy" for k in home[1])
    await sitestatus.update(site_id, "security", findings=findings, missing=missing, csp=csp,
                            plain_http=plain, hosting=hosting_key)

    exposed = SecurityResult(url=url, site_id=site_id, kind="exposed",
                             findings=[f for f in findings if f["code"].startswith("exposed_")])
    settings = SecurityResult(url=url, site_id=site_id, kind="security",
                              findings=[f for f in findings if not f["code"].startswith("exposed_")])
    if exposed.findings:
        names = [name for name, is_open in ((".git", git_open), (".env", env_open)) if is_open]
        exposed.status = "critical"
        exposed.error = f"наружу {'видны' if len(names) > 1 else 'виден'} {' и '.join(names)}"
    if settings.findings:
        settings.status, settings.error = "warning", summary(settings.findings)
    for r in (exposed, settings):
        await apply_incident(r, r.kind, open_new=manage)
    return [exposed, settings]


def is_https(url: str) -> bool:
    """http:// sites have no certificate to protect and nothing to redirect."""
    return url.lower().startswith("https://")


async def check_all_security(urls: list[str], manage: bool = True) -> list[SecurityResult]:
    out: list[SecurityResult] = []
    for url in urls:
        if not is_https(url):
            continue
        try:
            out += await check_security(url, manage=manage)
        except Exception as e:   # one site must not stop the others
            logger.error("Security check for %s failed: %r", url, e)
    return out
