"""What is published but not watched, and what in the zone points nowhere.

Once a day (and right before the Sunday report) the bot compares its own
list — the sites and API checks — with what is really out there:

* the zone's DNS records that serve the web: proxied A/AAAA/CNAME, and a
  CNAME to a hosting platform even when it is not proxied (Zone → DNS → Read);
* the custom domains of the Pages projects (Account → Cloudflare Pages → Read);
* the Worker routes, «fprint.example.com/api/*» (Zone → Workers Routes → Read);
* the «homepage» of the owner's GitHub repositories (the GitHub token).

What is out there and not on the list goes to the Sunday report as one 🟠
line with «➕ Следить» / «Не надо» per address: a new product gets watched
even when the owner forgot to add it. «Не надо» is remembered.

The same read looks for records that point nowhere, over the whole zone and
not only the watched hosts:

* a CNAME to a Pages project or a Worker the account does not have, to a
  platform name or a domain that does not exist — 🔴 at once: whoever
  creates that project or buys that domain shows their pages on the owner's
  address. Said once; ✅ when the record is gone or fixed;
* a CNAME to a name that does not exist elsewhere — 🟠 «можно удалить» on
  Sunday;
* the proof-of-ownership TXT and the DKIM records of a mail service that is
  no longer in MX or SPF (apple-domain, zoho-verification, sig1._domainkey…)
  — 🟠 «можно удалить» on Sunday.

What could not be read (a permission the token lacks) is one ⚠️ line in the
same message, naming the permission.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_all_sites, get_state, set_state
from monitors.dns_checker import resolve
from monitors.mail_checker import PROVIDERS, Provider, provider_by_include, provider_for, spf_records
from services import cloudflare, github, integrations, notifier
from services.cloudflare import CloudflareError
from services.github import GitHubError
from services.notifier import Priority
from utils.i18n import plural, t
from utils.text import esc
from utils.urls import PLATFORM_DOMAINS, host_of, is_http_url, is_web_page, registrable_domain

logger = logging.getLogger(__name__)

LAST = "published:last"          # the last read: unwatched, leftovers, nowhere, unread
IGNORED = "published:ignored"    # addresses the owner said «Не надо» to
DANGLING = "published:dangling"  # {record name: target} already said 🔴
BUTTONS = 5                      # «➕ Следить» rows under the Sunday message
# DKIM selectors too common to say whose they are.
_GENERIC_SELECTORS = frozenset({"mail", "default", "selector1", "selector2", "dkim", "k1", "s1", "s2"})
_lock = asyncio.Lock()


@dataclass
class Read:
    """One look at what is published."""
    zone: str = ""
    account: str = ""
    candidates: dict[str, dict] = field(default_factory=dict)   # key → {url, sources}
    dangling: dict[str, dict] = field(default_factory=dict)     # record name → {target, why}
    nowhere: list[dict] = field(default_factory=list)           # {name, target}
    leftovers: list[dict] = field(default_factory=list)         # {name, provider}
    unread: list[str] = field(default_factory=list)             # «маршруты Worker-ов: токену не хватает права …»
    unknown: set[str] = field(default_factory=set)              # CNAMEs without a verdict this time


def _web_record(r: dict) -> bool:
    name = str(r.get("name") or "")
    if r.get("type") not in ("A", "AAAA", "CNAME") or not name or any(
            label.startswith(("*", "_")) for label in name.split(".")):
        return False                       # a wildcard, or a service record (_dmarc, sig1._domainkey)
    target = str(r.get("content") or "").lower().rstrip(".")
    return bool(r.get("proxied")) or (r.get("type") == "CNAME" and registrable_domain(target) in PLATFORM_DOMAINS)


def _add(read: Read, key: str, url: str, source: str):
    item = read.candidates.setdefault(key, {"url": url, "sources": []})
    if source not in item["sources"]:
        item["sources"].append(source)


def _route(pattern: str) -> tuple[str, str] | None:
    """«fprint.example.com/api/*» → (host, «/api/»); None for a wildcard host."""
    raw = pattern.strip().removeprefix("https://").removeprefix("http://")
    host, _, path = raw.partition("/")
    host = host.lower()
    if not host or "*" in host:
        return None
    return host, "/" + path.rstrip("*")


async def _ask(read: Read, what: str, coro):
    """A source that refuses is a ⚠️ line, not a failed read."""
    try:
        return await coro
    except CloudflareError as e:
        read.unread.append(t("{what}: {why}").format(what=t(what), why=e))
    except GitHubError as e:
        read.unread.append(t("{what}: {why}").format(what=t(what), why=e))
    return None


async def collect() -> Read:
    read = Read()
    if cloudflare.configured():
        async with aiohttp.ClientSession(timeout=cloudflare.TIMEOUT) as s:
            records = await _ask(read, "записи DNS", cloudflare.dns_records(s))
            if records is not None:
                read.zone = await cloudflare.zone_name(s)
                read.account = await cloudflare.account_id(s)
            projects = await _ask(read, "проекты Pages", cloudflare.pages_projects(s))
            routes = await _ask(read, "маршруты Worker-ов", cloudflare.worker_routes(s))
            scripts = await _ask(read, "Worker-ы аккаунта", cloudflare.worker_scripts(s))
            sub = None
            if scripts is not None:
                try:
                    sub = await cloudflare.workers_subdomain(s)
                except CloudflareError:
                    sub = None              # only the *.workers.dev check needs it
        for r in records or []:
            if _web_record(r):
                name = str(r["name"]).lower()
                _add(read, name, f"https://{name}", "DNS")
        for p in projects or []:
            for d in p.get("domains") or []:
                if isinstance(d, str) and d and not d.endswith(".pages.dev"):
                    _add(read, d.lower(), f"https://{d.lower()}", "Pages")
        for r in routes or []:
            parsed = _route(str(r.get("pattern") or ""))
            if parsed:
                host, path = parsed
                key = host if path == "/" else f"{host}{path}*"
                _add(read, key, f"https://{host}{path}", "Worker")
        if records is not None:
            await _dangling(read, records, projects, scripts, sub)
            _leftovers(read, records)
    if github.available():
        repos = await _ask(read, "репозитории GitHub", github.published_repos())
        for repo in repos or []:
            # A site is watched by its host (its pages come from the sitemap):
            # a homepage with a path is its host too.
            host = host_of(repo["homepage"])
            if host and host != "github.com":
                _add(read, host, f"https://{host}", "GitHub")
    return read


async def _dangling(read: Read, records: list[dict], projects: list[dict] | None,
                    scripts: list[dict] | None, sub: str | None):
    pages = {str(p.get("subdomain") or "").lower() for p in projects or []}
    workers = {str(w.get("id") or "").lower() for w in scripts or []}
    for r in records:
        if r.get("type") != "CNAME":
            continue
        name = str(r.get("name") or "").lower()
        target = str(r.get("content") or "").lower().rstrip(".")
        if not name or not target or (read.zone and (target == read.zone or target.endswith("." + read.zone))):
            continue                                      # inside the zone: its own records answer for it
        if target.endswith(".pages.dev"):
            # x.pages.dev or a preview alias, branch.x.pages.dev: the project is x.
            if projects is None:
                read.unknown.add(name)
            elif f"{target.split('.')[-3]}.pages.dev" not in pages:
                read.dangling[name] = {"target": target, "why": "pages"}
            continue
        if target.endswith(".workers.dev"):
            parts = target.split(".")
            if scripts is None or not sub or len(parts) < 4 or parts[-3] != sub:
                read.unknown.add(name)                    # another account's subdomain: not ours to judge
            elif parts[-4] not in workers:
                read.dangling[name] = {"target": target, "why": "workers"}
            continue
        exists = await _exists(target)
        if exists is None:
            read.unknown.add(name)                        # the lookup failed: no verdict
        if exists is not False:
            continue
        domain = registrable_domain(target)
        if domain in PLATFORM_DOMAINS:
            read.dangling[name] = {"target": target, "why": "platform"}
        elif await resolve(domain, "NS") == []:
            read.dangling[name] = {"target": target, "why": "domain"}
        else:
            read.nowhere.append({"name": name, "target": target})


async def _exists(host: str) -> bool | None:
    answers = await asyncio.gather(resolve(host, "A"), resolve(host, "AAAA"))
    if any(answers):
        return True
    return None if None in answers else False


def _leftovers(read: Read, records: list[dict]):
    """Proofs and DKIM of the mail services the zone's apex no longer uses."""
    apex = read.zone
    if not apex:
        return
    mx = [str(r.get("content") or "").lower().rstrip(".") for r in records
          if r.get("type") == "MX" and str(r.get("name") or "").lower() == apex]
    txt = [str(r.get("content") or "").strip().strip('"') for r in records
           if r.get("type") == "TXT" and str(r.get("name") or "").lower() == apex]
    used: set[Provider] = set()
    if (p := provider_for(mx)) is not None:
        used.add(p)
    for record in spf_records(txt):
        for term in record.split():
            if term.lower().startswith("include:") and (p := provider_by_include(term[8:])) is not None:
                used.add(p)
    for p in PROVIDERS:
        if p in used:
            continue
        for value in txt:
            if any(value.lower().startswith(v) for v in p.verify):
                shown = f"{value.split('=')[0]}=…" if "=" in value else value[:24] + "…"
                read.leftovers.append({"name": shown, "provider": p.name})
        for sel in p.dkim:
            if sel in _GENERIC_SELECTORS:
                continue
            name = f"{sel}._domainkey.{apex}"
            if any(str(r.get("name") or "").lower() == name for r in records):
                read.leftovers.append({"name": f"{sel}._domainkey", "provider": p.name})


async def unwatched(read: Read) -> list[dict]:
    """[{key, url, sources}] the list does not cover, minus «Не надо»."""
    sites = [s for s in await get_all_sites() if is_http_url(s["url"])]
    hosts = {host_of(s["url"]) for s in sites}
    api = [s["url"].split("://", 1)[1].split("?", 1)[0] for s in sites if not is_web_page(s["url"], s.get("http_method"))]
    ignored = set(await _load(IGNORED, []))
    out = []
    for key, item in sorted(read.candidates.items()):
        if key in ignored or key in read.dangling:      # a record anyone can take over is said on its own
            continue
        if "/" in key:                                    # a Worker route: an API check under it covers it
            if any(a.startswith(key.rstrip("*")) for a in api):
                continue
        elif key in hosts:
            continue
        out.append({"key": key, **item})
    return out


async def _load(key: str, default):
    try:
        value = json.loads(await get_state(key) or "null")
    except ValueError:
        value = None
    return value if isinstance(value, type(default)) else default


# ── 🔴 a record anyone can take over ─────────────────────────────────────────

_WHY = {
    "pages": "такого проекта Cloudflare Pages в аккаунте нет",
    "workers": "такого Worker-а в аккаунте нет",
    "platform": "такого имени на этой платформе больше нет",
    "domain": "домена, на который она ведёт, не существует — его может купить кто угодно",
}


def dangling_text(name: str, target: str, why: str) -> str:
    return "\n".join([
        t("🔴 {name} можно захватить: запись CNAME ведёт на {target}, а {why}").format(
            name=esc(name), target=esc(target), why=t(_WHY[why])),
        t("Кто создаст это имя у себя, будет показывать свои страницы на твоём адресе — с твоим доменом и "
          "сертификатом."),
        "", t("Что делать:"),
        t("1. Не нужна — удали запись {name} в DNS у Cloudflare (кнопка ниже).").format(name=esc(name)),
        t("2. Нужна — создай проект или имя, на которое она ведёт, у себя."),
        "", f"<i>CNAME {esc(name)} → {esc(target)}</i>"])


def _dns_keyboard(account: str, zone: str) -> InlineKeyboardMarkup | None:
    if not account or not zone:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text=t("🔗 DNS в Cloudflare"), url=f"https://dash.cloudflare.com/{account}/{zone}/dns/records")]])


async def _say_dangling(read: Read):
    told = await _load(DANGLING, {})
    for name, d in sorted(read.dangling.items()):
        if name in told:
            continue
        if await notifier.send(dangling_text(name, d["target"], d["why"]), Priority.CRITICAL,
                               reply_markup=_dns_keyboard(read.account, read.zone)):
            told[name] = d["target"]
    # ✅ only for a verdict: a record whose lookup failed this time stays said.
    for name in [n for n in told if n not in read.dangling and n not in read.unknown]:
        if await notifier.send(t("✅ {name}: запись, которая вела на {target}, больше не опасна.").format(
                name=esc(name), target=esc(told[name])), Priority.NORMAL):
            told.pop(name)
    await set_state(DANGLING, json.dumps(told))


async def run():
    """The daily read; stores what the Sunday message shows, rings for a
    record anyone can take over. The DNS read's health is «🔌 Подключения»'."""
    if not cloudflare.configured() and not github.available():
        return
    async with _lock:
        read = await collect()
        if cloudflare.configured():
            dns_refused = next((u for u in read.unread if cloudflare.PERMISSIONS["dns"] in u), None)
            await integrations.record_result("cf_zone", dns_refused is None, dns_refused,
                                             refused=dns_refused is not None)
            if dns_refused is None and read.zone:
                await _say_dangling(read)
        await set_state(LAST, json.dumps({
            "checked_at": datetime.now(UTC).isoformat(timespec="minutes"),
            "unwatched": await unwatched(read), "nowhere": read.nowhere,
            "leftovers": read.leftovers, "unread": read.unread}, ensure_ascii=False))


# ── The Sunday message ───────────────────────────────────────────────────────

def _listing(items: list[str], limit: int = 5) -> str:
    more = t(" и ещё {n}").format(n=len(items) - limit) if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


async def sunday_message() -> tuple[str, InlineKeyboardMarkup | None] | None:
    last = await _load(LAST, {})
    lines, rows = [], []
    ignored = set(await _load(IGNORED, []))
    free = [u for u in last.get("unwatched") or [] if isinstance(u, dict) and u.get("key") not in ignored]
    if free:
        n = len(free)
        lines.append(t("🟠 {n} {addresses} без присмотра: {listing} — опубликованы, но я за ними не слежу.").format(
            n=n, addresses=plural(n, "адрес", "адреса", "адресов"),
            listing=_listing([f"{esc(u['key'])} ({esc(', '.join(u.get('sources') or []))})" for u in free])))
        for u in free[:BUTTONS]:
            rows.append([InlineKeyboardButton(text=t("➕ Следить: {key}").format(key=u["key"])[:60],
                                              callback_data=f"pub:add:{key_hash(u['key'])}"),
                         InlineKeyboardButton(text=t("Не надо"), callback_data=f"pub:no:{key_hash(u['key'])}")])
    nowhere = [x for x in last.get("nowhere") or [] if isinstance(x, dict)]
    if nowhere:
        lines.append(t("🟠 Записи DNS ведут в никуда: {listing} — можно удалить.").format(
            listing=_listing([f"{esc(x['name'])} → {esc(x['target'])}" for x in nowhere])))
    left = [x for x in last.get("leftovers") or [] if isinstance(x, dict)]
    if left:
        lines.append(t("🟠 В DNS остались записи почтовых сервисов, которых нет в MX и SPF: {listing} — можно удалить.")
                     .format(listing=_listing([f"{esc(x['name'])} ({esc(x['provider'])})" for x in left])))
    lines += [t("⚠️ Не прочитал {what}.").format(what=esc(u)) for u in last.get("unread") or [] if isinstance(u, str)]
    if not lines:
        return None
    return "\n".join(lines), (InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


def key_hash(key: str) -> str:
    """A short name for an address in callback_data (64 bytes at most)."""
    return hashlib.sha1(key.encode()).hexdigest()[:12]


async def pick(h: str) -> dict | None:
    """The address behind a «➕ Следить» / «Не надо» button of the last read."""
    last = await _load(LAST, {})
    return next((u for u in last.get("unwatched") or [] if isinstance(u, dict) and key_hash(str(u.get("key"))) == h),
                None)


async def ignore(key: str):
    ignored = await _load(IGNORED, [])
    if key not in ignored:
        ignored.append(key)
        await set_state(IGNORED, json.dumps(ignored[-200:]))
