"""What changed in the Cloudflare account — its audit log (Audit Logs v2,
GET /accounts/{id}/logs/audit; the token needs «Account → Account Settings →
Read»). A stolen Cloudflare account is a stolen domain and every site on it,
and the DNS snapshot sees only the hosts on the list.

Every 15 minutes the bot reads what happened since the last read:

* a dangerous change rings at once (🔴): a DNS record deleted, DNSSEC or the
  nameservers changed, SSL mode set to Flexible or Off, a redirect rule
  (ruleset rule, page rule, bulk redirect) deleted, a new API token, a login
  from an address the bot has not seen. «✅ Это я» under the alert makes the
  address known: what comes from it later is only listed. Several at once
  are one message;
* every change (not the reads, not the failed attempts, not the logins) goes
  to a two-day list: the morning digest says «☁️ Cloudflare за сутки: N
  изменений» with «📋 Что изменилось», and the cause of a short outage is
  looked up there (services.causes).

The first read only learns: the addresses of the last day become known,
nothing rings. Entries come late sometimes, so every read goes back
OVERLAP before the previous one and skips the ids it has seen.

The audit log's resource names (product, type) are not listed in
Cloudflare's reference: the dangerous kinds are recognised by words in
them and in the action's description [not verified against the real
log]. What is not recognised is still counted and listed — it never rings.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_state, set_state
from services import cloudflare, integrations, notifier
from services.cloudflare import CloudflareError
from services.notifier import Priority
from utils.clock import to_local
from utils.i18n import plural, t
from utils.text import clip, esc, one_line

logger = logging.getLogger(__name__)

SINCE = "cf_audit:since"          # the end of the last read (ISO, UTC)
CHANGES = "cf_audit:changes"      # the changes of the last KEEP, newest last
KNOWN = "cf_audit:known"          # addresses the owner works from
SEEN = "cf_audit:seen"            # ids read within OVERLAP: not counted twice
ALERT = "cf_audit:alert:{}"       # the addresses of one 🔴, for «✅ Это я»

FIRST_LOOK = timedelta(days=1)
LOOK_BACK_MAX = timedelta(days=7)   # a bot that was off for weeks reads the last week
OVERLAP = timedelta(minutes=30)
KEEP = timedelta(days=2)
MAX_CHANGES = 300
MAX_KNOWN = 50
PAGES = 5                          # × 1000 entries per read
DASH = "https://dash.cloudflare.com/{account}/audit-log"
TOKENS = "https://dash.cloudflare.com/profile/api-tokens"
_lock = asyncio.Lock()

# Kinds that ring, in the alert's words. Russian, translated where shown.
DANGER = {
    "dns_deleted": "удалена запись DNS",
    "dnssec": "изменили DNSSEC",
    "nameservers": "сменили NS-серверы зоны",
    "ssl_weak": "режим SSL переключили на",
    "redirect_deleted": "удалили правило переадресации",
    "token_created": "создан новый API-токен",
    "login": "вход в аккаунт с нового адреса",
}


@dataclass
class Change:
    id: str
    at: str           # ISO, UTC
    action: str       # create / update / delete / view / login …
    what: str         # Cloudflare's description and the resource name
    who: str          # e-mail or token name
    ip: str
    zone: str
    kind: str = ""    # a DANGER key, or ""
    detail: str = ""  # the record's name, the SSL mode, the token's name


def _str(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _field(obj, *names) -> str:
    """The first string among obj's names; obj may be anything."""
    if not isinstance(obj, dict):
        return ""
    for name in names:
        if _str(obj.get(name)):
            return _str(obj[name])
    return ""


def _payload(resource: dict) -> dict:
    """What the change carried: the response, else the request (either may
    be a JSON string or an object, or absent)."""
    for key in ("response", "request"):
        raw = resource.get(key)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except ValueError:
                raw = None
        if isinstance(raw, dict):
            inner = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            return inner
    return {}


def parse(entry: dict) -> Change | None:
    """One log entry → a Change; None when it is not one (no id or time)."""
    if not isinstance(entry, dict) or not _str(entry.get("id")):
        return None
    action = entry.get("action")
    action = action if isinstance(action, dict) else {"type": action}
    actor = entry.get("actor") if isinstance(entry.get("actor"), dict) else {}
    resource = entry.get("resource") if isinstance(entry.get("resource"), dict) else {}
    when = cloudflare.parse_time(_field(action, "time") or _field(entry, "when", "timestamp"))
    if when is None:
        return None
    if _field(action, "result") and _field(action, "result").lower() not in ("success", "true"):
        return None                                        # a failed attempt changed nothing
    kind = _field(action, "type").lower()
    payload = _payload(resource)
    name = _field(payload, "name", "hostname") or _field(resource, "id")[:40]
    product = " ".join(_field(resource, k) for k in ("product", "type")).lower()
    words = (product + " " + _field(action, "description")).lower()
    c = Change(id=_str(entry["id"]), at=when.astimezone(UTC).isoformat(timespec="seconds"), action=kind,
               what=one_line(" · ".join(p for p in (_field(action, "description") or product.strip() or kind,
                                                    name if name not in product else "") if p))[:120],
               who=_field(actor, "email", "token_name") or _field(actor, "type"),
               ip=_field(actor, "ip_address", "ip") or _field(entry, "ip"),
               zone=_field(entry.get("zone"), "name").lower())
    c.kind, c.detail = _danger(kind, words, payload, resource, name)
    return c


def _danger(kind: str, words: str, payload: dict, resource: dict, name: str) -> tuple[str, str]:
    if kind == "view":
        return "", ""
    if "login" in words or "log in" in words or "sign in" in words:
        return "login", ""
    if "dnssec" in words and kind in ("update", "delete", "create"):
        return "dnssec", _field(payload, "status")
    if "nameserver" in words or "name server" in words or "name_servers" in words:
        return "nameservers", ""
    if kind == "delete" and "dns" in words and "dnssec" not in words:
        return "dns_deleted", name
    if kind == "delete" and any(w in words for w in ("ruleset", "page_rule", "pagerule", "page rule", "redirect")):
        return "redirect_deleted", ""
    if kind == "create" and "token" in words and "tunnel" not in words:
        return "token_created", _field(payload, "name")
    value = _field(payload, "value").lower()
    if ("ssl" in words or _field(resource, "id") == "ssl") and value in ("flexible", "off"):
        return "ssl_weak", value.capitalize()
    return "", ""


async def _load(key: str, default):
    try:
        value = json.loads(await get_state(key) or "null")
    except ValueError:
        value = None
    return value if isinstance(value, type(default)) else default


def _rfc3339(dt: datetime) -> str:
    """«2026-11-24T14:02:11Z»: a «+00:00» would arrive as « 00:00» — a «+»
    in a query string is a space."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def read_log(session: aiohttp.ClientSession, since: datetime, before: datetime) -> list[Change]:
    acc = await cloudflare.account_id(session)
    out: list[Change] = []
    cursor = None
    for _ in range(PAGES):
        result, info = await cloudflare.get(
            session, f"/accounts/{acc}/logs/audit", "audit", since=_rfc3339(since),
            before=_rfc3339(before + timedelta(seconds=1)),   # the whole last second: the format has no fractions
            limit=1000, direction="asc", cursor=cursor)
        out += [c for c in (parse(e) for e in result or []) if c is not None]
        cursor = info.get("cursor")
        if not cursor or not result:
            break
    return out


async def poll() -> list[Change]:
    """One read: store the changes, ring for the dangerous ones. Returns the
    new changes (for the tests); raises CloudflareError when the log could
    not be read — the caller records it."""
    if not cloudflare.configured():
        return []
    async with _lock:
        now = datetime.now(UTC)
        last = cloudflare.parse_time(await get_state(SINCE))
        first = last is None
        since = now - FIRST_LOOK if first else max(last - OVERLAP, now - LOOK_BACK_MAX)
        async with aiohttp.ClientSession(timeout=cloudflare.TIMEOUT) as session:
            entries = await read_log(session, since, now)
            account = await cloudflare.account_id(session)
        seen = set(await _load(SEEN, []))
        fresh = [c for c in entries if c.id not in seen and c.action != "view"]
        known = await _load(KNOWN, [])
        if first:
            known = _remember(known, [c.ip for c in fresh if c.ip])
            danger = []
        else:
            danger = [c for c in fresh if c.kind and c.ip not in known and c.ip]
        # Nothing is stored before the alarm went out: a read whose 🔴 the
        # notifier did not take is read again, and says it again.
        if danger and not await _ring(danger, account):
            return fresh
        changes = [c for c in fresh if c.kind != "login"]
        stored = [x for x in await _load(CHANGES, []) if isinstance(x, dict)]
        cutoff = (now - KEEP).isoformat(timespec="seconds")
        stored = [x for x in stored if str(x.get("at") or "") >= cutoff] + [asdict(c) for c in changes]
        await set_state(CHANGES, json.dumps(stored[-MAX_CHANGES:], ensure_ascii=False))
        window = (now - 2 * OVERLAP).isoformat(timespec="seconds")
        await set_state(SEEN, json.dumps([c.id for c in entries if c.action != "view" and c.at >= window][-3000:]))
        await set_state(KNOWN, json.dumps(known))
        await set_state(SINCE, now.isoformat(timespec="seconds"))
        return fresh


def _remember(known: list, ips: list[str]) -> list[str]:
    """Known addresses, the most recently confirmed last, at most MAX_KNOWN."""
    out = [ip for ip in known if isinstance(ip, str) and ip not in ips] + list(dict.fromkeys(ips))
    return out[-MAX_KNOWN:]


def _danger_words(c: Change) -> str:
    what = t(DANGER[c.kind])
    if c.kind in ("dns_deleted", "token_created") and c.detail:
        return f"{what} «{esc(c.detail)}»"
    if c.kind == "ssl_weak":
        return f"{what} {esc(c.detail)}"
    if c.kind == "login":
        return f"{what} {esc(c.ip)}"
    if c.kind == "dnssec" and c.detail:
        return f"{what} ({esc(c.detail)})"
    return what


def _who(c: Change) -> str:
    parts = [esc(c.who)] if c.who else []
    if c.ip:
        parts.append(t("с адреса {ip}").format(ip=esc(c.ip)))
    return " · ".join(parts) or t("неизвестно")


def alert_text(danger: list[Change]) -> str:
    """The alert anatomy: headline, what it means, what to do, Cloudflare's
    own words last."""
    if len(danger) == 1:
        c = danger[0]
        head = t("🔴 Cloudflare: {what}").format(what=_danger_words(c))
        lines = [head, t("Кто: {who}, {time}.").format(who=_who(c), time=_hhmm(c.at))]
    else:
        head = t("🔴 Cloudflare: {n} {changes} в аккаунте").format(
            n=len(danger), changes=plural(len(danger), "опасное изменение", "опасных изменения", "опасных изменений"))
        lines = [head] + [f"{i}. {_danger_words(c)} — {_who(c)}, {_hhmm(c.at)}" for i, c in enumerate(danger[:10], 1)]
    lines += ["", t("Если это не ты, у кого-то есть доступ к аккаунту Cloudflare, а через него — к домену и всем "
                    "сайтам на нём."), "", t("Что делать:"),
              t("1. Это ты — нажми «✅ Это я»: изменения с этого адреса дальше будут только в списке за сутки."),
              t("2. Не ты — смени пароль Cloudflare, включи вход с подтверждением, удали лишние API-токены "
                "(кнопка ниже) и верни, что удалили, по журналу.")]
    tech = "; ".join(esc(c.what) for c in danger[:3])
    return clip("\n".join(lines) + (f"\n\n<i>{tech}</i>" if tech else ""))


def _hhmm(iso: str) -> str:
    at = cloudflare.parse_time(iso)
    return to_local(at).strftime("%H:%M") if at else ""


def _key(danger: list[Change]) -> str:
    return hashlib.sha1("".join(c.id for c in danger).encode()).hexdigest()[:12]


def alert_keyboard(key: str, account: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("✅ Это я"), callback_data=f"cfa:me:{key}")],
        [InlineKeyboardButton(text=t("🔗 Журнал в Cloudflare"), url=DASH.format(account=account)),
         InlineKeyboardButton(text=t("🔗 API-токены"), url=TOKENS)]])


async def _ring(danger: list[Change], account: str) -> bool:
    key = _key(danger)
    await set_state(ALERT.format(key), json.dumps(sorted({c.ip for c in danger if c.ip})))
    return bool(await notifier.send(alert_text(danger), Priority.CRITICAL,
                                    reply_markup=alert_keyboard(key, account)))


async def confirm(key: str) -> list[str]:
    """«✅ Это я»: the addresses of that alert become known. Returns them
    ([] when the alert is too old to know)."""
    ips = await _load(ALERT.format(key), [])
    ips = [ip for ip in ips if isinstance(ip, str)]
    if ips:
        await set_state(KNOWN, json.dumps(_remember(await _load(KNOWN, []), ips)))
        await set_state(ALERT.format(key), None)
    return ips


# ── The morning line and the list ────────────────────────────────────────────

async def recent(hours: int = 24) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(hours=hours)).isoformat(timespec="seconds")
    return [x for x in await _load(CHANGES, []) if isinstance(x, dict) and str(x.get("at") or "") >= cutoff]


async def morning_line() -> str | None:
    """«☁️ Cloudflare за сутки: 20 изменений» — nothing on a quiet day."""
    n = len(await recent())
    if not n:
        return None
    return t("☁️ Cloudflare за сутки: {n} {changes} — список под кнопкой «📋 Что изменилось»").format(
        n=n, changes=plural(n, "изменение", "изменения", "изменений"))


LIST_MAX = 30


async def list_text() -> str:
    items = await recent()
    if not items:
        return t("☁️ За сутки в Cloudflare ничего не меняли.")
    lines = [t("☁️ Что изменилось в Cloudflare за сутки ({n}):").format(n=len(items))]
    for x in reversed(items[-LIST_MAX:]):
        who = f" · {esc(str(x.get('who') or ''))}" if x.get("who") else ""
        lines.append(f"{_hhmm(str(x.get('at') or ''))} · {esc(str(x.get('what') or ''))}{who}")
    if len(items) > LIST_MAX:
        lines.append(t("… и ещё {n} — весь журнал в Cloudflare").format(n=len(items) - LIST_MAX))
    return clip("\n".join(lines))


async def changes_between(start: datetime, end: datetime, zone: str | None = None) -> list[dict]:
    """The stored changes of the zone between start and end (services.causes)."""
    lo, hi = start.astimezone(UTC).isoformat(timespec="seconds"), end.astimezone(UTC).isoformat(timespec="seconds")
    return [x for x in await _load(CHANGES, []) if isinstance(x, dict) and lo <= str(x.get("at") or "") <= hi
            and (not zone or not x.get("zone") or x.get("zone") == zone)]


async def run():
    """The scheduled read; its health is what «🔌 Подключения» shows."""
    if not cloudflare.configured():
        return
    try:
        await poll()
        await integrations.record_result("cf_audit", True)
    except CloudflareError as e:
        await integrations.record_result("cf_audit", False, str(e), refused=e.refused)
