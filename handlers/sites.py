"""🌍 Sites: the card (opened from the main screen), add/remove, pause,
restore from a database copy or an old JSON export sent to the chat."""

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import re
import sqlite3
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import idna
from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import config
from db.database import (
    SITE_SETTING_COLS,
    activate_or_create_site,
    count_sites_total,
    db_dir,
    deactivate_site,
    get_active_host_urls,
    get_active_incidents,
    get_all_sites,
    get_daily_availability,
    get_dns_record,
    get_last_incident,
    get_site,
    get_state,
    get_uptime_over_days,
    probe_copy,
    restore_from_file,
    set_state,
    update_site_settings,
)
from handlers import seo as seo_screen
from handlers.common import (
    MAX_SITES,
    STALE_TAP,
    ack,
    back_button,
    cancel_kb,
    cb_args,
    forget_secret,
    render,
    site_by_cb,
    with_running_bar,
)
from handlers.mail import mail_lines
from handlers.menu import _RANK, PROBLEM_ICONS, best_ms, checked_at, headline, icon_of, site_state
from handlers.security import security_lines
from handlers.setup import last_steps
from handlers.speed import speed_lines
from handlers.start import rules_of_the_game
from monitors import dns_checker
from monitors.availability import HTTP_METHODS, check_availability
from monitors.domain_checker import check_domain
from monitors.links_checker import check_links
from monitors.mail_checker import MAIL_CHECKS, carrier_of, check_mail
from monitors.pagemeta import alt_host_note, suggest_keyword
from monitors.security_checker import SECURITY_CHECKS, check_security
from monitors.ssl_checker import check_ssl
from reports.scheduler import next_run_words, pause_jobs, reschedule_all, reset_site_schedule, resume_jobs
from services import (
    gsc,
    humanize,
    integrations,
    maintenance,
    recommend,
    runtime,
    screenshots,
    secrets,
    seo_fixes,
    settings,
    sitestatus,
    yandex_webmaster,
)
from services import hosting as hosting_svc
from utils import validate
from utils.clock import to_local, utcnow
from utils.net import is_lan_host, normalize_ipv4
from utils.parse import parse_accepted_codes
from utils.text import esc, fmt_date, fmt_duration, incident_start, parse_sqlite_utc, plural
from utils.urls import (
    host_key,
    host_of,
    is_api_check,
    is_api_url,
    is_http_url,
    is_web_page,
    owned_domain,
    registrable_domain,
    site_label,
    ssl_applies,
)

logger = logging.getLogger(__name__)
router = Router(name="sites")


class AddSiteForm(StatesGroup):
    url = State()


# Hostname/IPv4 labels; single-label LAN hosts are allowed for tcp/ping.
_LABEL = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
_HOST_RE = re.compile(rf"{_LABEL}(\.{_LABEL})*")
_DOMAIN_RE = re.compile(rf"{_LABEL}(\.{_LABEL})+")


def _valid_lengths(host: str) -> bool:
    """DNS limits: 253 chars total, 63 per label."""
    return len(host) <= 253 and all(len(label) <= 63 for label in host.split("."))


def _ascii_host(host: str) -> str | None:
    """пример.рф → xn--e1afmkfd.xn--p1ai: the form DNS, certificates and every
    monitor use (the same IDNA 2008/UTS 46 rules as aiohttp).
    None when the name cannot be one. ASCII names pass as they are: IDNA is
    stricter than DNS for them (ab--cd.com)."""
    if host.isascii():
        return host
    try:
        return idna.encode(host, uts46=True).decode("ascii")
    except UnicodeError:   # idna.IDNAError is a UnicodeError
        return None


def _ipv6(host: str) -> str | None:
    """'2001:DB8:0::1' → '2001:db8::1'; None for anything that is not IPv6."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return None
    return addr.compressed if addr.version == 6 else None


PRIVATE_OFF = ("Внутренние адреса (локальная сеть, 127.0.0.1, 10.x, 192.168.x) выключены на этом сервере: "
               "ALLOW_PRIVATE_TARGETS=0.")


def _host_policy(host: str) -> tuple[str, str | None]:
    """An IPv4 in any spelling (0x7f.1, 2130706433) becomes 127.0.0.1-style:
    what is checked is what the owner sees. With ALLOW_PRIVATE_TARGETS=0 an
    internal host is refused — for http(s) a public name that merely
    resolves inside is refused by the connector (utils.net) as well; for
    tcp:// and ping:// only what is typed is judged."""
    host = normalize_ipv4(host) or host
    if host in ("0.0.0.0", "255.255.255.255"):
        return host, "Это не адрес сервера. Пришли домен или IP сайта"
    if not config.allow_private_targets and is_lan_host(host):
        return host, PRIVATE_OFF
    return host, None


API_PATH_LIMIT = 300


def parse_site_input(raw: str) -> tuple[str | None, str | None]:
    """User text → canonical monitor URL, or (None, error message). A web
    page means its site: a pasted https://example.com/blog/post is
    example.com. An API address (utils.urls.is_api_url: /api/… or an api.*
    host) keeps its path and query exactly as typed — that endpoint is what
    is watched, and paths are case-sensitive."""
    typed = (raw or "").strip()
    raw = typed.lower()
    if raw.startswith(("tcp://", "ping://")):
        try:
            parsed = urlparse(raw)
            host, port = parsed.hostname or "", parsed.port
        except ValueError:
            host, port = "", None
        v6 = _ipv6(host)
        if not v6:
            host = _ascii_host(host) or ""
            if not _HOST_RE.fullmatch(host) or not _valid_lengths(host):
                return None, ("Не понял хост. Примеры: tcp://mail.example.com:25, ping://10.0.0.1, "
                              "ping://[2001:db8::1]")
        host, refused = _host_policy(v6 or host)
        if refused:
            return None, refused
        if v6:
            host = f"[{host}]"   # stored in brackets: a URL needs them around an IPv6 address
        if parsed.scheme == "tcp":
            if not port:
                return None, "Для tcp нужен порт: tcp://host:порт (например, tcp://mail.example.com:25)"
            return f"tcp://{host}:{port}", None
        return f"ping://{host}", None
    if not raw.startswith(("http://", "https://")):
        raw, typed = "https://" + raw, "https://" + typed
    try:
        parsed = urlparse(raw)
        host, port_n = parsed.hostname or "", parsed.port
    except ValueError:
        return None, "Не похоже на адрес. Пришли что-то вроде example.com"
    host = _ascii_host(host) or ""
    if not _DOMAIN_RE.fullmatch(host) or not _valid_lengths(host):
        return None, "Не похоже на домен. Пришли что-то вроде example.com"
    host, refused = _host_policy(host)
    if refused:
        return None, refused
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{port_n}" if port_n and port_n != default_port else ""
    site = f"{parsed.scheme}://{host}{port}"
    if not is_api_url(site + parsed.path):
        return site, None
    as_typed = urlparse(typed)
    path = "" if as_typed.path in ("", "/") else as_typed.path
    tail = path + (f"?{as_typed.query}" if as_typed.query else "")
    if re.search(r"\s", tail) or len(tail) > API_PATH_LIMIT:
        return None, f"Адрес API — без пробелов и не длиннее {API_PATH_LIMIT} символов после домена."
    return site + tail, None


# ── Card ─────────────────────────────────────────────────────────────────────
#
# One set of rules for the card and the main screen: only status icons; the
# time of the check once, at the top; a line is a status, what it is and one
# number; a problem is said once — the card lifts it to the top, the blocks
# below keep what is fine, most important first.

# Problems the card shows on one of its lines; any other kind (a DNS change,
# slow answers) gets a line of its own among the problems.
_SHOWN_ON_A_LINE = frozenset({"availability", "ssl", "domain", "links", "deep", "seo", "visitors",
                              *MAIL_CHECKS, *SECURITY_CHECKS})


def api_line(site: dict) -> str:
    """What an API check sends and expects — one line on its card."""
    method = (site.get("http_method") or "GET").upper()
    kw = (site.get("keyword") or "").strip()
    expect = ""
    if kw:
        expect = (f", в ответе не должно быть «{esc(kw)}»" if site.get("keyword_mode") == "absent"
                  else f", в ответе должно быть «{esc(kw)}»")
    return (f"API-проверка: {method}{expect}. Слежу только за ответом — поиск, ссылки и SEO "
            f"к API не относятся.")


async def host_checked_by(site: dict) -> str | None:
    """URL of the monitor whose certificate and domain checks cover this
    site's host — itself for a page; None for tcp/ping."""
    url = site["url"]
    if not is_http_url(url):
        return None
    hosts = await get_active_host_urls()
    if url in hosts:
        return url
    return next((u for u in hosts if host_key(u) == host_key(url)), None)


def _items(lines: list[str]) -> list[list[str]]:
    """Lines grouped into items: a line and the indented ones under it."""
    out: list[list[str]] = []
    for line in lines:
        if line.startswith("    ") and out:
            out[-1].append(line)
        else:
            out.append([line])
    return out


async def card_parts(site: dict) -> tuple[list[list[str]], list[tuple[str, list[str]]]]:
    """(problems, [(block title, lines)]) from the stored snapshot — instant."""
    st = await sitestatus.get(site["id"])
    http = is_http_url(site["url"])
    api = is_api_check(site["url"], site.get("http_method"))
    page = http and not api
    avail = st.get("avail")

    access = [api_line(site)] if api else []
    access.append(await _avail_line(site, st, page))
    access += await _history_lines(site)
    access += _keyword_lines(site, avail)
    domain: list[str] = []
    mail: list[str] = []
    protection: list[str] = []
    search: list[str] = []
    covered_by = await host_checked_by(site)
    if covered_by and covered_by != site["url"]:
        what = "Сертификат и домен" if ssl_applies(site["url"]) else "Домен"
        domain.append(f"{what} — на карточке {esc(site_label(covered_by))}: проверяю их один раз "
                      f"на адрес")
    if covered_by == site["url"]:
        domain += _domain_lines(st.get("domain"), api)
        if ssl_applies(site["url"]):
            domain += _ssl_lines(st.get("ssl"))
        domain += await _dns_lines(site)
        if await carrier_of(site["url"]) == site["url"]:
            mail += mail_lines(st.get("mail"))
        if page:
            protection += security_lines(st.get("security"))
        # Page findings only: an API check is never crawled or audited, and a
        # snapshot left from when it was a page must not speak for it.
        if page:
            access += _pages_lines(st.get("deep"))
            access += _links_lines(st.get("links"))
            search += _seo_lines(st, cf_pending=await seo_screen.cloudflare_check_pending(site["id"], st))
    access += await speed_lines(site)

    problems: list[list[str]] = []
    blocks: list[tuple[str, list[str]]] = []
    mail_title = "Почта и защита" if mail and protection else "Почта" if mail else "Защита"
    for title, lines in (("Доступность", access), ("Домен", domain), (mail_title, mail + protection),
                         ("Поиск", search)):
        rest: list[str] = []
        for item in _items(lines):
            if item[0].startswith(PROBLEM_ICONS):
                problems.append(item)
            else:
                rest += item
        if rest:
            blocks.append((title, rest))
    for inc in await get_active_incidents():
        if inc["site_id"] == site["id"] and inc["check_type"] not in _SHOWN_ON_A_LINE:
            problems.append([f"{humanize.level_icon(inc['severity'])} "
                             f"{esc(humanize.incident_headline(inc['check_type'], inc.get('message'), 'Сайт', page=page))}"])
    return problems, blocks


def _card_body(found: list[list[str]], blocks: list[tuple[str, list[str]]]) -> list[str]:
    """Problems first, then a titled block each, every part after an empty
    line — the one assembly the card shows (render_card) and tests read."""
    lines = ["", *(line for item in found for line in item)] if found else []
    for title, rest in blocks:
        lines += ["", f"<b>{title}</b>", *rest]
    return lines


async def card_lines(site: dict) -> list[str]:
    """The card's text under its headline, exactly as render_card shows it."""
    lines = _card_body(*await card_parts(site))
    return lines[1:] if lines and lines[0] == "" else lines


async def _typical_ms(site: dict) -> int | None:
    """The usual answer time this week: the median of the daily averages."""
    days = sorted(d["avg_ms"] for d in await get_daily_availability(site["id"], 7) if d.get("avg_ms"))
    return days[len(days) // 2] if days else None


async def _avail_line(site: dict, st: dict, page: bool) -> str:
    avail = st.get("avail")
    if not avail:
        return "⏳ Ещё не проверял — первая проверка в течение минуты"
    if avail.get("status") == "unknown":
        return f"{humanize.UNCHECKED} Не могу проверить: {esc(humanize.describe_error(avail.get('error')))}"
    if avail.get("status") != "ok" and avail.get("kw"):
        # The page loaded; the phrase verdict is what failed — not an outage.
        return (f"🔴 {'Открывается' if page else 'Отвечает'}, но показывает не то: "
                f"{esc(humanize.describe_error(avail.get('error')))}")
    if avail.get("status") != "ok":
        return f"🔴 {'Не открывается' if page else 'Не отвечает'}: {esc(humanize.describe_error(avail.get('error')))}"
    ms, region = best_ms(st)
    typical = await _typical_ms(site)
    usual = f", обычно за {humanize.fmt_seconds(typical)}" if typical else ""
    return f"✅ {'Открывается' if page else 'Отвечает'} {humanize.speed(ms, region)}{usual}"


async def _history_lines(site: dict) -> list[str]:
    """Downtime over the week and the month, and when it last went down.
    ✅ only for a week and a month without a single outage; a line with
    downtime in it carries no icon — history, neither fine nor a problem."""
    week = await get_uptime_over_days(site["id"], 7)
    parts, clean = [], False
    if week["total_checks"]:
        month = await get_uptime_over_days(site["id"], 30)
        w, m = humanize.downtime(week), humanize.downtime(month)
        clean = w == m == "без сбоев"
        parts.append("без сбоев ни за неделю, ни за месяц" if clean else
                     f"простой: за неделю {w.removeprefix('недоступен ')}, за месяц {m.removeprefix('недоступен ')}")
    outage = await _last_outage(site)
    if outage:
        parts.append(outage)
    text = "; ".join(parts)
    return [f"{'✅ ' if clean else ''}{text[:1].upper()}{text[1:]}"] if text else []


async def _last_outage(site: dict) -> str:
    """«последний сбой 05.11 14:03 — 5 мин, хостинг отвечает ошибкой 502»."""
    inc = await get_last_incident(site["id"], "availability")
    started = incident_start(inc) if inc else None
    if not inc or not inc["resolved"] or not started or utcnow() - started > timedelta(days=90):
        return ""   # nothing recent; an open one is a problem at the top
    ended = parse_sqlite_utc(inc.get("resolved_at")) or started
    minutes = max(1, round((ended - started).total_seconds() / 60))
    why = humanize.describe_error(inc.get("message"))
    return f"последний сбой {to_local(started).strftime('%d.%m %H:%M')} — {fmt_duration(minutes)}, {esc(why)}"


def _keyword_lines(site: dict, avail: dict | None) -> list[str]:
    """The phrase the owner asked to watch — said when it is fine too."""
    phrase = site.get("keyword")
    if phrase and (site.get("http_method") or "").upper() == "HEAD":
        return [f"⚠️ Фраза «{esc(phrase)}» не проверяется: метод HEAD"]
    if not phrase or not avail or avail.get("status") != "ok":
        return []
    if site.get("keyword_mode") == "absent":
        return [f"✅ Фразы «{esc(phrase)}» на странице нет, как и должно быть"]
    return [f"✅ Фраза «{esc(phrase)}» на месте"]


def _ssl_lines(ssl: dict | None) -> list[str]:
    if not ssl:
        return ["⏳ Сертификат: ещё не проверял"]
    days = ssl.get("days_left")
    if days is None:
        return [f"🔴 Сертификат: {esc(humanize.describe_error(ssl.get('error')))}"]
    until = f"до {fmt_date(ssl['not_after'])} · " if ssl.get("not_after") else ""
    if days > 14:
        return [f"✅ Сертификат {until}ещё {days} дн."]
    issuer = f", выдан {esc(ssl['issuer'])}" if ssl.get("issuer") else ""
    return [f"{humanize.level_icon('warning' if days > 3 else 'critical')} Сертификат "
            f"{'истёк' if days < 0 else f'{until}осталось {days} дн.'}{issuer}"]


def _domain_lines(dom: dict | None, api: bool) -> list[str]:
    if not dom:
        return [] if api else ["⏳ Домен: ещё не проверял"]   # an api.* host shares its site's registration
    if dom.get("unsupported"):
        return [f"Домен: {esc(dom.get('error') or 'срок регистратура не сообщает')}"]
    days = dom.get("days_left")
    if days is None:
        return [f"⚠️ Домен: {esc(humanize.describe_error(dom.get('error')))}"]
    until = f"оплачен до {fmt_date(dom.get('expiration'))}"
    if days > 30:
        return [f"✅ Домен {until} · ещё {days} дн."]
    # Running out: who to pay is the next question.
    registrar = (f", регистратор {esc(dom['registrar'])}"
                 if dom.get("registrar") not in (None, "", "Unknown") else "")
    return [f"{humanize.level_icon('warning' if days > 7 else 'critical')} Домен "
            f"{'истёк' if days < 0 else f'{until} · осталось {days} дн.'}{registrar}"]


async def _dns_lines(site: dict) -> list[str]:
    """Whether the domain's servers stayed put — and whether they answer at all."""
    domain = owned_domain(host_of(site["url"]))
    if not domain:
        return []      # an IP or a platform's domain: no DNS of the owner's to watch
    n = dns_checker.failing(domain, "NS")
    if n >= dns_checker.FAILS_TO_SAY:
        return [f"🔴 DNS: NS-серверы {esc(domain)} не отвечают уже {n} ч"]
    ns = await get_dns_record(domain, "NS")
    since = parse_sqlite_utc(ns[1]) if ns and ns[0] else None
    return [f"✅ DNS не менялись с {to_local(since).strftime('%d.%m.%Y')}"] if since else []


async def hosting_words(site: dict) -> str:
    """« · Cloudflare» for the card's title — what to open when it breaks."""
    hosting = hosting_svc.label(await get_state(f"hosting:{site['id']}"))
    return f" · {esc(hosting)}" if hosting else ""


def _pages_lines(deep: dict | None) -> list[str]:
    """The rest of the site: pages from the sitemap, hourly — one line when
    they all answer, else only the broken ones by name."""
    if not deep or not deep.get("sampled"):
        return []
    n, bad = deep["sampled"], deep.get("errors") or 0
    if not bad:
        return [f"✅ Страницы из карты сайта: {n} из {n} отвечают"]
    # code None — answered fine; 0 — did not answer at all; else a 5xx.
    broken = [(path, code) for path, code in (deep.get("pages") or []) if code is not None]
    silent = any(code == 0 for _, code in broken)
    lines = [f"🟠 Страницы из карты сайта: {bad} из {n} отвечают ошибкой сервера"
             + (" или не отвечают" if silent else "")]
    lines += [f"    🔴 {esc(path)} — {'не ответила' if code == 0 else f'ошибка {code}'}" for path, code in broken[:10]]
    if len(broken) > 10:
        lines.append(f"    … и ещё {len(broken) - 10}")
    return lines


def _links_lines(links: dict | None) -> list[str]:
    """The links on the page — and, when some do not open, which and why,
    right here: the card is the whole story, not a pointer to a screen."""
    if not links:
        return []
    total = links.get("total")
    if links.get("status") == "ok":
        return [f"✅ Ссылки: {f'{total} из {total}' if total else 'все'} открываются"]
    if links.get("status") != "warning":
        return ["⚠️ Ссылки: не смог проверить"]
    n = links.get("broken", links.get("internal")) or 0
    of = f" из {total}" if total else ""
    lines = [f"🟠 Ссылки: {n}{of} не {plural(n, 'открывается', 'открываются', 'открываются')}"]
    lines += [f"    • {esc(i['url'])} — {esc(humanize.link_reason(i.get('code'), i.get('error'), i['url']))}"
              for i in (links.get("items") or [])[:5]]
    lines.append("    Что сделать: поставь новый адрес, убери ссылку или дай копию из web.archive.org.")
    return lines


def _seo_lines(st: dict, cf_pending: bool = False) -> list[str]:
    """Search and AI in full: the verdict, the index when Google or Yandex
    is connected, and each kind of finding by name. cf_pending: a Cloudflare
    site whose access for AI the audit cannot see (said until opened once)."""
    seo = st.get("seo")
    if not seo:
        return []
    groups = seo_screen.grouped(seo.get("problems") or [])
    n = len(groups) or seo.get("improve") or 0   # snapshots from before 11.2026 kept a count only
    verdict = {"ok": "✅ Поиск и ИИ: помех нет",
               "warning": f"🟠 Поиск и ИИ: {n} {plural(n, 'помеха', 'помехи', 'помех')}",
               "critical": "🔴 Поиск и ИИ: сайт закрыт от поисковиков"}.get(
        seo.get("status"), "⚠️ Поиск и ИИ: не смог проверить")
    lines = [verdict]
    idx, _ = seo_screen.index_lines(st, gsc.available(), yandex_webmaster.available())
    lines += [f"    {line}" for line in idx]
    if cf_pending:
        lines.append("    Пускает ли Cloudflare ИИ-ассистентов, снаружи не видно: настоящих ботов он узнаёт "
                     "по адресу. Где посмотреть — кнопка ниже.")
    lines += [f"    {i}. {humanize.level_icon(sev)} {esc(seo_fixes.fix(code).title)}"
              for i, (code, sev, _) in enumerate(groups[:5], 1)]
    if n > 5:
        lines.append(f"    … и ещё {n - 5}")
    return lines


async def _pause_rows(sid: int) -> tuple[list[InlineKeyboardButton], list[str]]:
    notes = []
    if await maintenance.paused_until(sid):
        notes.append("🔧 Режим «я чиню»: про этот сайт не пишу, проверки идут.")
        row = [InlineKeyboardButton(text="✅ Починил, пиши снова", callback_data=f"pause:{sid}:off")]
    else:
        row = [InlineKeyboardButton(text=f"🔧 Я чиню: {maintenance.fix_length()} тишины",
                                    callback_data=f"pause:{sid}:{maintenance.FIX_MINUTES}")]
    if maintenance.maintenance_now(sid):
        notes.append("Сейчас плановые работы — про этот сайт не пишу.")
    return row, notes


async def render_card(call: CallbackQuery, site: dict):
    """One site, everything about it; ◀ ▶ flip to the neighbours, so the
    sites block is one message rather than a button per site."""
    url, sid = site["url"], site["id"]
    sites = await get_all_sites()
    pos = next((i for i, s in enumerate(sites) if s["id"] == sid), 0)
    st = await sitestatus.get(sid)
    found, blocks = await card_parts(site)
    # Before the first check the card knows nothing: not «всё в порядке».
    head = (headline([icon_of(item[0]) for item in found], checked_at(st.get("avail"))) if st.get("avail")
            else "⏳ Ещё не проверял — первая проверка в течение минуты")
    lines = [f"<b>{esc(site_label(url))}</b>{await hosting_words(site)}", head]
    pause_row, notes = await _pause_rows(sid)
    lines += notes
    lines += _card_body(found, blocks)
    problems = [inc for inc in await get_active_incidents() if inc["site_id"] == sid]
    rows = []
    # One way to the fix, only when there is something to fix: search
    # findings and mail have a screen with the steps, anything else its
    # «что делать».
    seo = st.get("seo") or {}
    other = [inc for inc in problems if inc["check_type"] not in (*MAIL_CHECKS, *SECURITY_CHECKS, "visitors")]
    if is_web_page(url, site.get("http_method")) and seo.get("problems"):
        rows.append([InlineKeyboardButton(text="🛠 Как исправить поиск", callback_data=f"seo_site:{sid}")])
    elif other:
        rows.append([InlineKeyboardButton(text="ℹ️ Что делать", callback_data=f"inc_explain:{other[0]['id']}")])
    if (st.get("mail") or {}).get("findings") or any(inc["check_type"] in MAIL_CHECKS for inc in problems):
        rows.append([InlineKeyboardButton(text="✉️ Как починить почту", callback_data=f"mail:{sid}")])
    if (st.get("security") or {}).get("findings") or any(inc["check_type"] in SECURITY_CHECKS for inc in problems):
        rows.append([InlineKeyboardButton(text="🛡 Как защитить сайт", callback_data=f"sec:{sid}")])
    if any(inc["check_type"] == "visitors" for inc in problems):
        rows.append([InlineKeyboardButton(text="👥 Почему медленно", callback_data=f"spd:{sid}")])
    if is_web_page(url, site.get("http_method")) and await seo_screen.cloudflare_check_pending(sid, st):
        rows.append([InlineKeyboardButton(text="🤖 Проверить в Cloudflare", callback_data=f"seo_fix:{sid}:ai_cloudflare")])
    rows.append([InlineKeyboardButton(text="🔄 Перепроверить", callback_data=f"check_site_live:{sid}"),
                 *pause_row[:1]])
    rows.append([InlineKeyboardButton(text="⚙️ Настройки сайта", callback_data=f"sset:{sid}")])
    if len(sites) > 1:
        prev, nxt = sites[pos - 1], sites[(pos + 1) % len(sites)]
        rows.append([InlineKeyboardButton(text="◀", callback_data=f"check_site:{prev['id']}"),
                     InlineKeyboardButton(text=f"📋 Все сайты · {pos + 1}/{len(sites)}", callback_data="blk:sites"),
                     InlineKeyboardButton(text="▶", callback_data=f"check_site:{nxt['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add"),
                 InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")])
    picture = None
    if is_web_page(url, site.get("http_method")):
        picture = await screenshots.card_picture_url(sid)
        # The first time, or a stale one: ready by the next look. is_due is
        # false without a public address (nowhere to show it), and refresh
        # itself takes one picture per site at a time.
        if await screenshots.is_due(sid, url):
            _later(screenshots.refresh(sid, url), f"screenshot of site {sid}")
    await render(call, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows), picture=picture)


_background: set = set()


def _later(coro, what: str):
    """Run without holding the screen; keep a reference so it is not
    collected half-way, and put its failure in the log — a background task
    that dies silently looks like a feature that never runs."""
    task = asyncio.get_running_loop().create_task(coro)
    _background.add(task)

    def _done(t: asyncio.Task):
        _background.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("%s failed", what, exc_info=t.exception())

    task.add_done_callback(_done)


SITES_PAGE = 8


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n - 1] + "…"


@router.callback_query(F.data.startswith("blk:sites"))
async def cb_sites_block(call: CallbackQuery):
    """«🌐 Сайты» on the main screen: one site — its card at once; more — a
    list, the sites that need a look first, SITES_PAGE per page
    (blk:sites:<page>)."""
    sites = await get_all_sites()
    if not sites:
        await ack(call, "Сайтов пока нет", show_alert=True)
        return
    await ack(call)
    if len(sites) == 1:
        await render_card(call, sites[0])
        return
    arg = call.data.split(":")[2] if call.data.count(":") == 2 else "0"
    incidents = await get_active_incidents()
    states = [(s, (await site_state(s, incidents))[0]) for s in sites]
    states.sort(key=lambda t: _RANK.get(t[1], 9))
    pages = -(-len(states) // SITES_PAGE)
    page = min(int(arg), pages - 1) if arg.isdigit() else 0
    rows = [[InlineKeyboardButton(text=f"{icon} {_short(site_label(s['url']), 26)}",
                                  callback_data=f"check_site:{s['id']}")]
            for s, icon in states[page * SITES_PAGE:(page + 1) * SITES_PAGE]]
    nav = ([InlineKeyboardButton(text="◀", callback_data=f"blk:sites:{page - 1}")] if page > 0 else []) + \
          ([InlineKeyboardButton(text="▶", callback_data=f"blk:sites:{page + 1}")] if page < pages - 1 else [])
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Добавить сайт", callback_data="site_add"),
                 InlineKeyboardButton(text="🏠 Главная", callback_data="menu_main")])
    head = f"🌐 Сайты ({len(sites)})" + (f" · страница {page + 1} из {pages}" if pages > 1 else "")
    await render(call, f"{head}\nСначала те, где что-то не так.", InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith("check_site:"))
async def cb_check_single_site(call: CallbackQuery):
    """The card, instantly, from the last stored checks."""
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден — список сайтов изменился.", back_button())
        return
    await render_card(call, site)


# Sites whose «🔄» is running: a second tap waits for the first.
_live_running: set[int] = set()
LIVE_CHECK_SEC = 180


@router.callback_query(F.data.startswith("check_site_live:"))
async def cb_check_site_live(call: CallbackQuery):
    """«🔄 Перепроверить»: everything the card shows, live — availability,
    certificate and domain (when this site is the one they are checked
    for), links and search — then the same card. One run per site at a
    time, and the whole run within LIVE_CHECK_SEC: a host that hangs on
    every port must not leave a «▱▱▱ Проверяю…» for ever."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await ack(call)
        await render(call, "Сайт не найден.", back_button())
        return
    if site["id"] in _live_running:
        await ack(call, "Уже проверяю — подожди")
        return
    await ack(call, "Проверяю…")
    _live_running.add(site["id"])
    try:
        async with asyncio.timeout(LIVE_CHECK_SEC):
            await _live_checks(call, site)
    except TimeoutError:
        await render(call, f"⌛ Проверка {esc(site_label(site['url']))} не уложилась в {LIVE_CHECK_SEC // 60} мин — "
                           f"сайт или его DNS отвечают очень медленно. Ниже карточка по тому, что успел узнать.")
    finally:
        _live_running.discard(site["id"])
    await render_card(call, site)


async def _live_checks(call: CallbackQuery, site: dict):
    url = site["url"]
    await render(call, f"▱▱▱ Проверяю, открывается ли {esc(site_label(url))}...")
    await check_availability(url, manage=False)
    if await host_checked_by(site) == url:
        mail_domain = registrable_domain(host_of(url)) if await carrier_of(url) == url else None
        cert = "сертификат, " if ssl_applies(url) else ""
        base = f"Смотрю {cert}срок домена{', почту' if mail_domain else ''} и защиту сайта..."
        await render(call, f"▰▱▱ {base}")
        await with_running_bar(call.message, base, _host_now(site, mail_domain))
    if is_web_page(url, site.get("http_method")):
        base = "Проверяю ссылки и смотрю на сайт глазами Google, Яндекса и ИИ (около 30 секунд)..."
        await render(call, f"▰▰▱ {base}")
        await with_running_bar(call.message, base, _pages_now(site))


async def _host_now(site: dict, mail_domain: str | None):
    url = site["url"]
    if ssl_applies(url):
        await check_ssl(url, manage=False)
    await check_domain(url, manage=False)
    if mail_domain:
        await check_mail(mail_domain, url, manage=False)
    if is_web_page(url, site.get("http_method")):
        await check_security(url, manage=False)


async def _pages_now(site: dict):
    await check_links(site["url"], manage=False)
    await seo_screen.audit_now(site)


# ── Add ──────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "site_add")
async def cb_site_add(call: CallbackQuery, state: FSMContext):
    """«➕ Добавить» — one door for everything that can be watched: a site
    is just typed in, the other two kinds are a button away."""
    await ack(call)
    if await _limit_reached():
        await render(call, *_limit_screen())
        return
    await state.set_state(AddSiteForm.url)
    await render(call, "➕ Пришли адрес сайта, например <code>example.com</code>.\n\n"
                       "Не сайт? Выбери ниже.", _add_kb())


_HB_BUTTON = InlineKeyboardButton(text="⏰ Бот или задача по расписанию", callback_data="hb_add")


async def _limit_reached(url: str | None = None) -> bool:
    """MAX_SITES monitors are watched and url is not one of them — one rule
    for «➕ Добавить», «🛠 API, порт или ping» and the address typed in
    (re-adding a watched address is not a new site)."""
    active = {s["url"] for s in await get_all_sites()}
    return url not in active and len(active) >= MAX_SITES


def _limit_screen() -> tuple[str, InlineKeyboardMarkup]:
    return (f"Лимит {MAX_SITES} сайтов — сними что-нибудь с мониторинга. Бота или задачу "
            f"по расписанию добавить можно.",
            InlineKeyboardMarkup(inline_keyboard=[[_HB_BUTTON], *back_button().inline_keyboard]))


def _add_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 API, порт или ping", callback_data="site_add_adv")],
        [_HB_BUTTON],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])


@router.callback_query(F.data == "site_add_adv")
async def cb_site_add_advanced(call: CallbackQuery, state: FSMContext):
    await ack(call)
    if await _limit_reached():               # «🛠» on an «➕» screen opened before the limit was reached
        await state.clear()
        await render(call, *_limit_screen())
        return
    await state.set_state(AddSiteForm.url)
    await render(call, "🛠 Кроме сайтов я умею следить за сервисами без веб-страницы:\n\n"
                       "• <code>example.com/api/health</code> — главная функция сайта, его API: "
                       "код ответа, время и фраза в ответе. Адрес под <code>/api/</code> или на "
                       "<code>api.</code>-домене я считаю API и не проверяю у него поиск, ссылки и SEO; "
                       "метод POST, заголовки и тело задаются в настройках после добавления\n"
                       "• <code>tcp://mail.example.com:25</code> — отвечает ли порт "
                       "(почта, SSH, база данных)\n"
                       "• <code>ping://10.0.0.1</code> — отвечает ли хост на ping\n\n"
                       "Пришли адрес в таком виде.", cancel_kb())


@router.message(AddSiteForm.url)
async def msg_site_add(message: Message, state: FSMContext):
    url, error = parse_site_input(message.text or "")
    if not url:
        await message.answer(error, reply_markup=_add_kb())
        return
    await state.clear()
    raw = urlparse((message.text or "").strip())
    if urlparse(url).query or raw.password or "@" in raw.netloc:
        await forget_secret(message)        # an API address with ?token=… or user:pass@ in it
    if await _limit_reached(url):            # the form may have been opened before the limit was reached
        text, kb = _limit_screen()
        await message.answer(text, reply_markup=kb)
        return
    first_site = not await get_all_sites()
    site_id = await activate_or_create_site(url)
    label = site_label(url)
    status = await message.answer(f"▱▱ Добавил {esc(label)} — делаю первую проверку...")
    # Read-only first look: the scheduled monitor (next minute tick) owns
    # incidents and alert ladders; a manage=True check here would silently
    # consume the SSL alert for a nearly-expired certificate.
    r = await check_availability(url, manage=False)
    site = await get_site(site_id)
    page = is_web_page(url, site.get("http_method"))
    lines = [f"✅ {esc(label)} под присмотром.\n"]
    if r.ok:
        lines.append(f"✅ {'Открывается' if page else 'Отвечает'} {humanize.speed(r.response_time_ms)}")
    elif r.transient:
        lines.append(f"{humanize.UNCHECKED} Проверить не могу: {esc(humanize.describe_error(r.error))}.")
    elif r.keyword_failed:
        lines.append(f"🔴 Сейчас {'открывается' if page else 'отвечает'}, но показывает не то: "
                     f"{esc(humanize.describe_error(r.error))}. Я уже слежу и напишу, когда станет как надо.")
    else:
        lines.append(f"🔴 Сейчас {'не открывается' if page else 'не отвечает'}: "
                     f"{esc(humanize.describe_error(r.error))}. Я уже слежу и напишу, когда поднимется.")
    if r.api:
        lines.append("\n🔌 Это API: слежу только за ответом — код, время и фраза в ответе. Поиск, ссылки "
                     "и SEO к API не относятся.")
        lines.append("Нужен POST, токен или тело запроса — «📡 Настроить запрос»: там метод, заголовки, "
                     "тело и фраза, которая должна быть в ответе."
                     + ("" if r.ok else " Ошибка выше уйдёт, когда запрос станет правильным."))
    if await host_checked_by(site) == url:
        if ssl_applies(url):
            ssl_r = await check_ssl(url, manage=False)
            if ssl_r.ssl_info:
                lines.append(f"🔒 Сертификат безопасности действует ещё {ssl_r.ssl_info.days_left} дн.")
            elif ssl_r.transient:
                lines.append("⚠️ Сертификат: не удалось проверить, попробую при следующей проверке.")
            else:
                lines.append(f"🔴 Сертификат: {esc(humanize.describe_error(ssl_r.error))}")
        # The real next runs (reports.scheduler), not a promise the schedule may break.
        domain_at = next_run_words("domain_checks") or "утром"
        links = (f", ссылки — в ближайшие {config.links_check_interval_hours} ч"
                 if config.links_check_interval_hours else "")
        lines.append(f"\nСрок домена проверю {domain_at}, поиск и ИИ — {next_run_words('seo_checks') or 'утром'} "
                     f"(итоги — в утренней сводке){links}." if page else f"\nСрок домена проверю {domain_at}.")
    if first_site:
        lines.append("\n" + rules_of_the_game())
    reset_site_schedule(site_id)
    rows = [[InlineKeyboardButton(text="🌍 Открыть карточку", callback_data=f"check_site:{site_id}")]]
    if r.api:
        rows.append([InlineKeyboardButton(text="📡 Настроить запрос", callback_data=f"ssetx:{site_id}")])
    kb = InlineKeyboardMarkup(inline_keyboard=[*rows, *back_button().inline_keyboard])
    phrase = None
    if page and r.ok:
        note = await alt_host_note(url)
        if note:
            lines.append("\n" + esc(note))
        phrase = await suggest_keyword(url)
    await status.edit_text("\n".join(lines), reply_markup=kb)
    if phrase:
        # A question of its own: its answer removes the buttons, not the result above.
        await set_state(f"kw_suggest:{site_id}", phrase)
        await message.answer(f"💡 На главной есть «{esc(phrase)}». Следить, чтобы она не пропадала? "
                             f"Так я замечу пустую страницу или ошибку вместо сайта.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                 [InlineKeyboardButton(text="✅ Да, следить", callback_data=f"kwok:{site_id}"),
                                  InlineKeyboardButton(text="Не надо", callback_data=f"kwno:{site_id}")]]))
    if first_site and (steps := await last_steps()):
        await message.answer(steps[0], reply_markup=steps[1])


@router.callback_query(F.data.startswith("kwno:"))
async def cb_keyword_decline(call: CallbackQuery):
    """«Не надо» is remembered: the weekly tip never re-suggests it. The
    question loses its buttons; the screen stays where it was."""
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site:
        await set_state(f"kw_declined:{site['id']}", "1")
        await set_state(f"kw_suggest:{site['id']}", None)
    await ack(call, "Ок, больше не предложу")
    if call.message is not None and hasattr(call.message, "edit_reply_markup"):
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("rec_no:"))
async def cb_tip_decline(call: CallbackQuery):
    """«Не предлагать» under a weekly tip: that tip never comes back. The
    tip's text stays, only its buttons go."""
    key = call.data.split(":", 1)[1]
    if key in recommend.TIPS:
        await set_state(f"{recommend.DECLINED}{key}", "1")
    await ack(call, "Ок, больше не предложу")
    await call.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("kwok:"))
async def cb_keyword_accept(call: CallbackQuery):
    site = await site_by_cb(call.data.split(":", 1)[1])
    phrase = await get_state(f"kw_suggest:{site['id']}") if site else None
    if not site or not phrase:
        await ack(call, "Подсказка устарела — задай фразу в настройках сайта", show_alert=True)
        return
    await update_site_settings(site["id"], keyword=phrase, keyword_mode="present")
    await set_state(f"kw_suggest:{site['id']}", None)
    await ack(call, "Слежу ✅")
    await render(call, f"✅ Слежу, чтобы на {esc(site_label(site['url']))} была фраза «{esc(phrase)}». "
                       f"Поменять можно в настройках сайта.", back_button())


# ── Remove ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("delsite:"))
async def cb_site_del_confirm(call: CallbackQuery):
    await ack(call)
    site = await site_by_cb(call.data.split(":", 1)[1])
    if not site:
        await render(call, "Сайт не найден.", back_button())
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, убрать", callback_data=f"delok:{site['id']}")],
        [InlineKeyboardButton(text="← Отмена", callback_data=f"sset:{site['id']}")]])
    await render(call, f"Убрать {esc(site_label(site['url']))} из мониторинга?\n"
                       "Открытые инциденты по нему закроются, история останется. Его настройки (фраза, "
                       "метод, заголовки, пороги) и пауза «🔧 Я чиню» сбросятся — если добавишь его снова, "
                       "начнёшь с чистого листа.", kb)


@router.callback_query(F.data.startswith("delok:"))
async def cb_site_del_do(call: CallbackQuery):
    if not await ack(call):                     # «🗑 Да, убрать» from the queue after a restart: no
        await call.message.answer(STALE_TAP)
        return
    site = await site_by_cb(call.data.split(":", 1)[1])
    if site and await deactivate_site(site["id"]):
        await maintenance.remove_windows_for_site(site["id"])
        await screenshots.forget(site["id"])        # its picture must not be served any more
        reset_site_schedule(site["id"])
        await render(call, f"🗑 {esc(site_label(site['url']))} больше не под присмотром. История сохранилась, "
                     f"настройки сброшены.", back_button())
    else:
        await render(call, "Сайт уже убран.", back_button())


# ── Pause ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("pause:"))
async def cb_pause(call: CallbackQuery):
    args = cb_args(call, 2)
    site = await site_by_cb(args[0]) if args else None     # an active site only, like every card button
    # «off» or up to a day: 5000000 minutes from a forged button would be 9.5
    # years of silence, 99999999999 a crash. The card's own button carries
    # maintenance.FIX_MINUTES.
    minutes = validate.int_in(args[1], 1, 24 * 60) if args else None
    if not site or (args[1] != "off" and minutes is None):
        await ack(call, "Кнопка устарела — открой карточку сайта заново", show_alert=True)
        return
    sid, arg = site["id"], args[1]
    # The card itself says «🔧 Режим «я чиню»» and offers «✅ Починил»: a toast and the card again.
    if arg == "off":
        await maintenance.pause_site(sid, None)
        await ack(call, "Пауза снята — снова пишу обо всём")
    else:
        await maintenance.pause_site(sid, minutes)
        await ack(call, f"🔧 {fmt_duration(minutes)} не пишу про этот сайт, проверки идут")
    await render_card(call, site)


# ── Restore from a file ──────────────────────────────────────────────────────
#
# No «📤 Экспорт / 📥 Импорт» buttons: the bot copies its database every
# night and sends one here on Sundays (or now: «🤖 Боты» → «💾 Скачать
# копию базы»). To restore, send that file back — the bot recognises it and
# asks before replacing anything. A JSON export from older versions still
# works the same way.

_EXPORT_FIELDS = sorted(SITE_SETTING_COLS)


_IMPORT_CAP = 500     # entries looked at in one file — bounded work, and nothing silently cut at 20

# The chat's own rules for each per-site setting (handlers/site_settings.py):
# a value from a file passes the same door or is named in «Пропущено».
_FIELD_RULES = {
    "check_interval_min": lambda v: validate.int_in(v, *validate.CHECK_INTERVAL_MIN),
    "fail_threshold": lambda v: validate.int_in(v, *validate.FAIL_THRESHOLD),
    "slow_ms": lambda v: validate.int_in(v, *validate.SLOW_MS),
    "accepted_codes": lambda v: v if isinstance(v, str) and len(v) <= 60 and parse_accepted_codes(v) else None,
    "keyword": lambda v: v.strip() if isinstance(v, str) and 2 <= len(v.strip()) <= 100 else None,
    "keyword_mode": lambda v: v if v in ("present", "absent") else None,
    "http_method": lambda v: v.upper() if isinstance(v, str) and v.upper() in HTTP_METHODS else None,
    "http_headers": validate.headers_json,
    "http_body": lambda v: v if isinstance(v, str) and len(v) <= 4000 else None,
}


def _site_fields(item: dict) -> tuple[dict, list[str]]:
    """The settings of one entry that pass the rules, and the names of those that do not."""
    fields, bad = {}, []
    for key, value in item.items():
        if key not in _FIELD_RULES or value in (None, ""):
            continue
        clean = _FIELD_RULES[key](value)
        if clean is None:
            bad.append(key)
        else:
            fields[key] = clean
    return fields, bad


async def apply_document(doc) -> dict:
    """Apply an export document; returns counters for the summary. Every
    value passes the rule the chat applies; what does not is named in
    «Пропущено» and the rest still applies."""
    counters = {"added": 0, "updated": 0, "skipped": [], "settings": 0, "windows": 0, "jobs": 0, "integrations": 0}
    skipped = counters["skipped"]
    items = doc.get("sites", doc) if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        skipped.append("список сайтов: в файле не список")
        items = []
    by_url: dict[str, int] = {}
    try:
        for item in items[:_IMPORT_CAP]:
            if not isinstance(item, dict):
                skipped.append(str(item)[:60])
                continue
            url, _error = parse_site_input(str(item.get("url", "")))
            if not url:
                skipped.append(str(item.get("url", "?"))[:60])
                continue
            fields, bad = _site_fields(item)
            existing = await get_all_sites()
            if len(existing) >= MAX_SITES and url not in {s["url"] for s in existing}:
                skipped.append(f"{url} (лимит {MAX_SITES})")
                continue
            was = url in {s["url"] for s in existing}
            site_id = await activate_or_create_site(url)
            by_url[url] = site_id
            if fields:
                await update_site_settings(site_id, **fields)
            if bad:
                skipped.append(f"{url}: {', '.join(bad)} (неверные значения)")
            reset_site_schedule(site_id)
            counters["updated" if was else "added"] += 1
        if len(items) > _IMPORT_CAP:
            skipped.append(f"ещё {len(items) - _IMPORT_CAP} записей сверх {_IMPORT_CAP}")
        version = doc.get("version", 1) if isinstance(doc, dict) else 1
        if not isinstance(version, int) or version < 2:
            return counters
        await _apply_settings(doc.get("settings") if isinstance(doc.get("settings"), dict) else {}, counters)
        await _apply_windows(doc.get("maintenance_windows"), by_url, counters)
        jobs = doc.get("heartbeat_jobs") if isinstance(doc.get("heartbeat_jobs"), dict) else {}
        for name, interval in jobs.items():
            minutes = validate.int_in(interval, 1, 525_600)
            if minutes and await settings.add_heartbeat_job(str(name).strip().lower(), minutes):
                counters["jobs"] += 1
            else:
                skipped.append(f"задача {str(name)[:20]}")
        links = doc.get("integrations") if isinstance(doc.get("integrations"), dict) else {}
        for key, value in links.items():
            rule = integrations.RULES.get(key)
            if rule and rule(value):
                await integrations.set_value(key, value)
                counters["integrations"] += 1
            else:
                skipped.append(f"подключение {str(key)[:20]} — задай заново в «🔌 Подключения»")
    finally:
        reschedule_all()      # hours in memory and cron jobs must not disagree, whatever happened above
    return counters


async def _apply_settings(st: dict, counters: dict):
    skipped = counters["skipped"]
    setters = (("morning_hour", settings.set_morning_hour), ("evening_hour", settings.set_evening_hour),
               ("weekly_hour", settings.set_weekly_hour),
               ("quiet_hours", lambda v: settings.set_quiet_hours(v or None)))
    for key, setter in setters:
        if key not in st:
            continue
        if await setter(st[key]):          # False: not an hour / not a range — nothing saved
            counters["settings"] += 1
        else:
            skipped.append(f"настройка {key}")
    if isinstance(st.get("status_page"), bool):
        await settings.set_status_page(st["status_page"])
        counters["settings"] += 1
    if st.get("timezone"):
        if await settings.set_timezone(st["timezone"]):
            counters["settings"] += 1
        else:
            skipped.append("настройка timezone")
    if isinstance(st.get("ring_only_down"), bool):
        await settings.set_ring_only_down(st["ring_only_down"])
        counters["settings"] += 1


async def _apply_windows(windows, by_url: dict[str, int], counters: dict):
    for w in windows if isinstance(windows, list) else []:
        added = None
        if isinstance(w, dict):
            site_url = w.get("site_url")
            site_id = by_url.get(site_url) if site_url else None
            if not (site_url and site_id is None):   # a window of a site not in the file is not this file's
                added = await maintenance.add_window(site_id, w.get("days") or [],
                                                     validate.int_in(w.get("start_min"), 0, 1439),
                                                     validate.int_in(w.get("end_min"), 0, 1439))
        if added is None:
            counters["skipped"].append(f"плановые работы: неверное время или дни, чужой сайт или уже "
                                       f"{maintenance.MAX_MAINT_WINDOWS} окон")
        else:
            counters["windows"] += 1


_MAX_FILE = 20 * 1024 * 1024   # what the cloud Bot API lets a bot download


async def _restore_bytes(raw: bytes) -> str:
    """Apply an old JSON export; the answer in words. A database copy goes
    through _restore_db."""
    try:
        doc = json.loads(raw.decode("utf-8", errors="replace"))
        items = doc["sites"] if isinstance(doc, dict) else doc
    except (ValueError, KeyError):
        items = None
    if not isinstance(items, list):     # an explicit check: `python -O` would drop an assert
        return "Не смог разобрать файл. Нужна копия базы (.db), которую я присылаю по воскресеньям."
    c = await apply_document(doc)
    text = f"✅ Из файла: сайтов добавлено {c['added']}, обновлено {c['updated']}."
    extras = [f"настроек {c['settings']}" if c["settings"] else "",
              f"плановых работ {c['windows']}" if c["windows"] else "",
              f"задач {c['jobs']}" if c["jobs"] else "",
              f"подключений {c['integrations']}" if c["integrations"] else ""]
    extras = [e for e in extras if e]
    if extras:
        text += " Также: " + ", ".join(extras) + "."
    if c["skipped"]:
        text += "\nПропущено:\n" + "\n".join(f"  • {esc(x)}" for x in c["skipped"][:10])
        if len(c["skipped"]) > 10:
            text += f"\n  … и ещё {len(c['skipped']) - 10}"
    return text


async def _restore_db(path: str) -> str:
    """Swap the database for the uploaded copy; the answer in words."""
    pause_jobs()
    try:
        n, bak = await restore_from_file(path)
    except (ValueError, sqlite3.Error) as e:
        return f"❌ Не восстановил: {esc(e)}. Текущая база не тронута."
    finally:
        await reload_services()     # the previous or the new file — re-read either way
        resume_jobs()
    return (f"✅ Восстановил из копии: активных сайтов {n}. Прежняя база сохранена рядом: "
            f"{esc(os.path.basename(bak))}. Проверки продолжаются.")


def _drop_upload(pending: dict | None):
    if pending and pending.get("path"):
        with contextlib.suppress(FileNotFoundError):
            os.remove(pending["path"])


def _clear_uploads():
    """One pending file at a time: a new one replaces the rest — leftovers of
    a restart (the question lives in memory only) included."""
    for old in Path(db_dir()).glob("upload-*.tmp"):
        old.unlink(missing_ok=True)


def _list(urls: list[str]) -> str:
    return ", ".join(esc(u) for u in urls[:5]) + (f" и ещё {len(urls) - 5}" if len(urls) > 5 else "")


async def _db_question(info: dict, name: str) -> str:
    """What «✅ Восстановить» will change — said before the button."""
    now = {s["url"] for s in await get_all_sites()}
    theirs = set(info["urls"])
    lines = [f"💾 «{esc(name)}» — копия базы бота. Восстановить из неё сайты, настройки и подключения?",
             f"Сайтов в копии: {len(theirs)}, сейчас: {len(now)}."]
    if gone := sorted(now - theirs):
        lines.append(f"Пропадут: {_list(gone)}.")
    if new := sorted(theirs - now):
        lines.append(f"Появятся: {_list(new)}.")
    stored = integrations.stored()
    # A shareable copy carries no tokens: what it lacks stays as it is now.
    differ = [k for k in integrations.KEYS
              if info["state"].get(f"int:{k}") and info["state"][f"int:{k}"] != stored.get(k)]
    if differ:
        lines.append("Подключения станут как в копии: " + ", ".join(differ) + ".")
    lines.append("Ключи подключений, заголовки и тела API-проверок, которых в копии нет, останутся текущими "
                 "(на новом сервере их нужно ввести заново).")
    # What silences alerts right after the restore must not come as a surprise.
    try:
        windows = len(json.loads(info["state"].get("cfg:maint_windows") or "[]"))
    except ValueError:
        windows = 0
    paused = sum(1 for k in info["state"] if k.startswith("paused:"))
    if windows or paused:
        lines.append(f"В копии окон тишины: {windows}, сайтов на паузе «🔧 Я чиню»: {paused}.")
    lines.append("Владелец и помощники останутся как сейчас, "
                 + ("секреты ссылок — тоже." if await count_sites_total()
                    else "секреты ссылок возьму из копии — старые ссылки задач продолжат работать.")
                 + " Текущая база сохранится рядом.")
    return "\n".join(lines)


@router.message(F.document, StateFilter(None))
async def msg_backup_file(message: Message, state: FSMContext):
    """A file sent to the chat outside any wizard: if it is a copy of the
    bot's database (or an old export), offer to restore from it. The file is
    downloaded once, checked, and kept on disk until the answer."""
    doc = message.document
    if doc.file_size is None:
        await message.answer("Не вижу размер файла — пришли его ещё раз как документ (📎 → Файл).")
        return
    if doc.file_size > _MAX_FILE:
        await message.answer("Файл слишком большой (лимит 20 МБ — столько отдаёт Telegram).")
        return
    _clear_uploads()
    await state.update_data(restore=None)
    path = os.path.join(db_dir(), f"upload-{doc.file_unique_id}.tmp")
    try:
        # The default 30 s for the whole file fails a 20 MB copy below ~0.7 MB/s.
        await message.bot.download(doc, destination=path, timeout=max(60, doc.file_size // (128 * 1024)))
    except Exception as e:
        _drop_upload({"path": path})
        await message.answer(f"Не смог скачать файл из Telegram ({esc(e)}). Пришли его ещё раз.")
        return
    with open(path, "rb") as f:
        head = f.read(16)
    name = doc.file_name or "файл"
    if head.startswith(b"SQLite format 3"):
        try:
            info = await asyncio.to_thread(probe_copy, path)
        except ValueError as e:
            _drop_upload({"path": path})
            await message.answer(f"❌ Не похоже на мою копию базы: {esc(e)}.")
            return
        kind, question = "db", await _db_question(info, name)
    elif head.lstrip()[:1] in (b"{", b"["):
        kind = "json"
        question = (f"📥 «{esc(name)}» — файл экспорта из прежней версии. Добавить из него сайты и настройки? "
                    "Лишнее не удалю.")
    else:
        _drop_upload({"path": path})
        await message.answer("Не знаю, что делать с этим файлом. Чтобы восстановить бота, пришли копию базы "
                             "(.db), которую я присылаю по воскресеньям.")
        return
    sent = await message.answer(question, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Восстановить", callback_data="restore:yes"),
        InlineKeyboardButton(text="✖️ Не надо", callback_data="restore:no")]]))
    await state.update_data(restore={"path": path, "kind": kind, "question_id": sent.message_id})


@router.callback_query(F.data.in_({"restore:yes", "restore:no"}))
async def cb_restore(call: CallbackQuery, state: FSMContext):
    pending = (await state.get_data()).get("restore")
    if not pending or pending.get("question_id") != call.message.message_id:
        # Only the newest question counts: «✅» under an older one must not
        # apply a different file.
        await ack(call, "Это старый вопрос")
        await render(call, "Это старый вопрос — пришли файл ещё раз.", back_button())
        return
    await state.update_data(restore=None)
    try:
        if call.data == "restore:no":
            await ack(call, "Ничего не менял")
            await render(call, "Ничего не менял.", back_button())
            return
        await ack(call, "Восстанавливаю…")
        if pending["kind"] == "db":
            text = await _restore_db(pending["path"])
        else:
            with open(pending["path"], "rb") as f:
                text = await _restore_bytes(f.read())
        await render(call, text, back_button())
    finally:
        _drop_upload(pending)


async def reload_services():
    """After a database restore every in-memory cache must be re-read — and
    the admin seat written back, or the restored file may leave it free."""
    await runtime.persist()
    await secrets.load()
    await settings.load()
    await maintenance.load()
    await integrations.load()
    reschedule_all()
