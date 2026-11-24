"""One-tap remediation actions for Cloudflare Pages sites + the alert keyboard.

The sites are static (Cloudflare Pages), so there is nothing to "restart" —
the meaningful actions are: re-run the check, trigger a Pages deploy hook
(rebuild + redeploy), and purge the Cloudflare cache.
"""

import logging
from typing import NamedTuple

import aiohttp
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from services import cloudflare, humanize, integrations, maintenance, seo_fixes
from services import hosting as hosting_svc
from services.integrations import Probe
from utils.i18n import t
from utils.text import esc
from utils.urls import host_of, is_http_url

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_PROBE_TIMEOUT = aiohttp.ClientTimeout(total=10)
# The same words as handlers.common.RECHECK (a test keeps them equal): the
# handlers package cannot be imported from a service without a cycle.
RECHECK = "🔄 Проверить снова"


async def cf_token_status(token: str) -> Probe:
    """Is the Cloudflare API token any good — asked before «✅ сохранён».
    With a Zone ID in hand the question is the one «🧹» asks first, GET
    /zones/{id}: a refusal there is a refusal (the token must read the
    zone to purge it). Without one, GET /user/tokens/verify: an active
    token is OK; a 4xx there is not called a refusal — an account-owned
    token has a verify endpoint of its own (/accounts/{id}/tokens/verify,
    docs: developers.cloudflare.com/api/resources/accounts/subresources/
    tokens/methods/verify/), and such a token would be turned away here
    [not verified against the real API] — it is saved unconfirmed and the
    first purge tells."""
    zone_id = integrations.cf_zone_id()
    by_zone = bool(cloudflare.ZONE_ID.fullmatch(zone_id))
    url = f"{cloudflare.API}/zones/{zone_id}" if by_zone else f"{cloudflare.API}/user/tokens/verify"
    try:
        async with aiohttp.ClientSession(timeout=_PROBE_TIMEOUT) as s:
            async with s.get(url, headers=cloudflare.headers(token)) as resp:
                body, status = await cloudflare.envelope(resp), resp.status
    except Exception as e:
        logger.warning("Cloudflare token probe failed: %s", type(e).__name__)
        return Probe.UNREACHABLE
    if status == 200 and body.get("success"):
        if by_zone or str((body.get("result") or {}).get("status")) == "active":
            return Probe.OK
        return Probe.REJECTED                       # verify answered: disabled or expired
    if status in (400, 401, 403):
        logger.warning("Cloudflare token probe: HTTP %s (%s)", status, cloudflare.error_text(body, status))
        return Probe.REJECTED if by_zone else Probe.UNREACHABLE
    return Probe.UNREACHABLE


def deploy_hook_for(url: str) -> str | None:
    # Hooks are keyed by hostname, but only WEB sites can be redeployed —
    # a tcp://example.com:25 incident must not fire example.com's hook.
    if not is_http_url(url):
        return None
    return integrations.deploy_hooks().get(host_of(url))


def in_cf_zone(url: str, hosting: str | None = None) -> bool:
    """Can «🧹» help this site: the host is in the configured zone (its name is
    learnt on the first purge); until the name is known — when the site is
    served by Cloudflare."""
    if not is_http_url(url) or not integrations.cf_purge_configured():
        return False
    zone, host = integrations.cf_zone_name(), host_of(url)
    if zone:
        return host == zone or host.endswith("." + zone)
    return hosting == "cloudflare"


def alert_actions_keyboard(url: str, site_id: int, incident_id: int | None = None,
                           hosting: str | None = None, api: bool = False) -> InlineKeyboardMarkup:
    """Action buttons attached to availability alerts. The site is addressed
    by its DB id — stable across list edits and well under the 64-byte
    callback_data limit. «:a» on a button marks it as living on an alert:
    its screen answers below instead of replacing the alert text. An API
    check has no page to photograph."""
    http = is_http_url(url)
    rows = [[InlineKeyboardButton(text=t("🔧 Я чиню, {length} тишины").format(length=maintenance.fix_length()),
                                  callback_data=f"act:fix:{site_id}"),
             InlineKeyboardButton(text=t(RECHECK), callback_data=f"act:recheck:{site_id}")]]
    second = []
    if http and not api:
        second.append(InlineKeyboardButton(text=t("📸 Как выглядит сайт"), callback_data=f"act:shot:{site_id}"))
    if incident_id:
        second.append(InlineKeyboardButton(text=t("ℹ️ Что делать"), callback_data=f"inc_explain:{incident_id}:a"))
    if second:
        rows.append(second)
    extra = []
    if http and deploy_hook_for(url):
        extra.append(InlineKeyboardButton(text=t("🚀 Пересобрать сайт"), callback_data=f"act:redeploy:{site_id}"))
    if in_cf_zone(url, hosting):
        extra.append(InlineKeyboardButton(text=t("🧹 Сбросить кэш"), callback_data=f"act:purge:{site_id}"))
    if extra:
        rows.append(extra)
    if http and deploy_hook_for(url) and integrations.auto_enabled("redeploy"):
        # The bot will rebuild this site by itself (services.automation): one tap to stop that
        # for a while — the owner may be rolling back by hand. The panel link stays last.
        rows.append([InlineKeyboardButton(text=t("⏸ Не пересобирать сам 2 ч"), callback_data="auto:pause:2:a")])
    panel = hosting_svc.panel_url(hosting)
    if panel:
        rows.append([InlineKeyboardButton(text=t("🔗 Открыть панель {panel}").format(panel=hosting_svc.label(hosting)),
                                          url=panel)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_keyboard(group_id: int) -> InlineKeyboardMarkup:
    """Buttons of a message about several sites at once (reports.scheduler,
    GROUP_FROM). The per-site buttons are a tap away: the list answers
    below, the alert stays intact. group_id — the first incident of the
    group; its sites live in bot_state (alert_group:<id>)."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("📋 Показать по сайтам"), callback_data=f"grp:list:{group_id}")],
        [InlineKeyboardButton(text=t("🔧 Я чиню все — {length} тишины").format(length=maintenance.fix_length()),
                              callback_data=f"grp:fix:{group_id}")]])


def deep_alert_keyboard(url: str, site_id: int, incident_id: int | None) -> InlineKeyboardMarkup | None:
    """«5xx на страницах»: the home page opens, so «🔧 Я чиню» (silences the
    whole site), «🔄 Проверить снова» (checks the home page only) and «📸»
    (photographs the home page) would answer a different question."""
    redeploy = ([InlineKeyboardButton(text=t("🚀 Пересобрать сайт"), callback_data=f"act:redeploy:{site_id}")]
                if deploy_hook_for(url) else [])
    return explain_keyboard(incident_id, redeploy)


def explain_keyboard(incident_id: int | None, *extra_rows: list[InlineKeyboardButton]) -> InlineKeyboardMarkup | None:
    """«ℹ️ Что делать» for an incident, plus any extra rows (links etc.)."""
    rows = [[InlineKeyboardButton(text=t("ℹ️ Что делать"), callback_data=f"inc_explain:{incident_id}:a")]] if incident_id else []
    rows += [row for row in extra_rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def seo_fix_keyboard(site_id: int, codes: list[str], *, numbered: bool = False, on_alert: bool = False,
                     extra_rows: tuple[list[InlineKeyboardButton], ...] = ()) -> InlineKeyboardMarkup:
    """One «💡 …» button per kind of search/AI finding → its steps screen
    (answered below the message when the buttons sit on an alert)."""
    tail = ":a" if on_alert else ""
    rows = []
    for i, code in enumerate(codes, 1):
        label = seo_fixes.button_label(code)
        rows.append([InlineKeyboardButton(text=f"{i}. {label}" if numbered else label,
                                          callback_data=f"seo_fix:{site_id}:{code}{tail}")])
    rows += [row for row in extra_rows if row]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def heartbeat_keyboard(jobs: list[str], seen: frozenset[str] | set[str] = frozenset()) -> InlineKeyboardMarkup | None:
    """Per silent task, the two ways out in one tap: its card (answered
    below) — «🔍 Что с …» for a task that has been in touch (seen), «🔗
    Подключить» for one that never was — and «🗑 Удалить» when there is no
    such task."""
    rows = []
    for job in jobs[:3]:
        if len(f"hb_how:{job}".encode()) > 64:   # an .env name may be anything
            continue
        rows.append([InlineKeyboardButton(text=(t("🔍 Что с {job}") if job in seen else t("🔗 Подключить {job}")).format(job=job),
                                          callback_data=f"hb_how:{job}"),
                     InlineKeyboardButton(text=t("🗑 Удалить"), callback_data=f"hb_rm:{job}")])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def domain_keyboard(registrar: str | None, incident_id: int | None = None) -> InlineKeyboardMarkup | None:
    """«ℹ️ Что делать» and a «Продлить у регистратора» link when known."""
    url = humanize.registrar_url(registrar)
    link_row = [InlineKeyboardButton(text=t("🔗 Открыть панель {panel}").format(panel=registrar), url=url)] if url else []
    return explain_keyboard(incident_id, link_row)


class RedeployResult(NamedTuple):
    ok: bool
    text: str    # one sentence, no icon: the caller adds 🚀 / ❌ or «🤖 …»


async def trigger_redeploy(url: str) -> RedeployResult:
    """Fire the Cloudflare Pages deploy hook. The text says what happened and,
    on a failure, what to do about it. Never the exception text or the hook
    address: the address is the secret."""
    hook = deploy_hook_for(url)
    if not hook:
        return RedeployResult(False, t("Пересборка не настроена для этого сайта "
                                       "(⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)"))
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.post(hook) as resp:
                status = resp.status
    except aiohttp.InvalidURL:
        logger.warning("Deploy hook for %s is not a valid URL", host_of(url))
        return RedeployResult(False, t("Адрес deploy hook испорчен — задай его заново "
                                       "(⚙️ Настройки → 🔌 Подключения → ☁️ Cloudflare)"))
    except aiohttp.ClientConnectorError:
        logger.warning("Deploy hook for %s: cannot connect", host_of(url))
        return RedeployResult(False, t("Не удалось связаться с Cloudflare — нажми «🚀 Пересобрать сайт» чуть позже"))
    except TimeoutError:
        logger.warning("Deploy hook for %s: timed out", host_of(url))
        return RedeployResult(False, t("Cloudflare не ответил за {sec} с — нажми «🚀 Пересобрать сайт» чуть позже")
                              .format(sec=int(_TIMEOUT.total)))
    except Exception as e:
        logger.warning("Deploy hook for %s failed: %s", host_of(url), type(e).__name__)
        return RedeployResult(False, t("Не удалось запустить пересборку — подробности в логе бота"))
    if status in (200, 201):
        return RedeployResult(True, t("Запустил пересборку сайта — Cloudflare Pages соберёт его за 1–2 минуты"))
    if status in (404, 410):
        return RedeployResult(False, t("Cloudflare не знает этот хук пересборки (ошибка {status}) — похоже, его "
                                       "удалили. Создай новый в Cloudflare Pages и вставь его: ⚙️ Настройки → "
                                       "🔌 Подключения → ☁️ Cloudflare").format(status=status))
    if status >= 500:
        return RedeployResult(False, t("Cloudflare сейчас не принимает команды (ошибка {status}) — "
                                       "нажми «🚀 Пересобрать сайт» чуть позже").format(status=status))
    return RedeployResult(False, t("Cloudflare не принял команду пересборки (ошибка {status})").format(status=status))


async def purge_cf_cache(url: str) -> str:
    """Purge the Cloudflare cache of this site's host only — the rest of the
    zone (www, api, other subdomains) keeps its cache. Purge by hostname is
    documented for every plan since 2025, but that was not verified against
    a Free zone: if Cloudflare refuses, its own words are shown, and a
    full purge stays one click away in the Cloudflare panel."""
    if not integrations.cf_purge_configured():
        return t("❌ Cloudflare API token / zone id не настроены (⚙️ Настройки → 🔌 Подключения → Cloudflare)")
    zone_id = integrations.cf_zone_id()
    if not cloudflare.ZONE_ID.fullmatch(zone_id):
        return t("❌ Zone ID Cloudflare испорчен — задай его заново (⚙️ Настройки → 🔌 Подключения → Cloudflare)")
    zone = f"{cloudflare.API}/zones/{zone_id}"
    auth = cloudflare.headers()
    host = host_of(url)
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as s:
            async with s.get(zone, headers=auth) as resp:
                body, status = await cloudflare.envelope(resp), resp.status
            if not body.get("success"):
                return t("❌ Cloudflare не дал прочитать зону: {why}. Проверь токен (права на зону) и Zone ID").format(
                    why=esc(cloudflare.error_text(body, status)))
            name = str((body.get("result") or {}).get("name") or "").lower()
            if name and name != integrations.cf_zone_name():
                await integrations.set_value("cf_zone_name", name)
            # The configured zone is one domain: never purge it for a site
            # that lives elsewhere (another zone, Vercel, …).
            if not name or not (host == name or host.endswith("." + name)):
                return t("❌ {host} не в зоне Cloudflare {zone} — кэш не трогаю").format(host=esc(host), zone=esc(name or "?"))
            async with s.post(zone + "/purge_cache", json={"hosts": [host]}, headers=auth) as resp:
                body, status = await cloudflare.envelope(resp), resp.status
            if body.get("success"):
                return t("🧹 Кэш Cloudflare для {host} сброшен — посетители увидят свежую версию сайта").format(host=esc(host))
            return "❌ Cloudflare: " + esc(cloudflare.error_text(body, status))
    except (TimeoutError, aiohttp.ClientError) as e:
        logger.warning("CF cache purge failed: %s", type(e).__name__)
        return t("❌ Cloudflare не ответил за {sec} с — попробуй через минуту или сбрось кэш "
                 "в панели Cloudflare").format(sec=int(_TIMEOUT.total))
    except Exception as e:
        logger.warning("CF cache purge failed: %s", type(e).__name__)
        return t("❌ Не удалось сбросить кэш — подробности в логе бота; попробуй через минуту")
