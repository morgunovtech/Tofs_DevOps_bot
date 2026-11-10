"""Credentials and switches for optional integrations, editable from the
chat. Values entered in the bot live in bot_state and win over .env; .env
stays the fallback so existing deployments keep working unchanged.

Keys: yandex_token, gsc_property, gsc_key (service-account JSON text),
deploy_hooks (JSON {host: url}), cf_api_token, cf_zone_id, cf_zone_name
(learnt from Cloudflare on the first purge), second_opinion ("on"/"off"),
github_token, github_owner, crux_key (Google API key for the Chrome UX
Report), auto_redeploy / auto_restart / auto_cleanup ("on"/"off": the
switches of «🤖 Что бот делает сам», they win over .env), region_speed
("on"/"off": the «⏱ Скорость из региона» switch of «🌐 Вторая точка»).

«🗑 Отключить» in the chat must mean off even when .env still carries the
value: such a key gets a tombstone (bot_state int_off:<key>, switch_off)
that hides the .env value until a new one is entered here or «↩️ Брать из
.env» is pressed. Old code never reads the tombstones, so a rollback
brings the .env values back by itself.

Probe is the three-way answer of a live check of a key (the wizards and
the «🔌 Подключения» screen): the service said yes, the service refused
the key, or the service could not be reached — which is not the key's
fault and must never read as «не принял».

The end of the module keeps the health of every external service the bot
talks to (HEALTH, record_result): a refused key or a day without a single
success is said once by reports.scheduler.run_integration_health, and the
screens show «не отвечает» instead of «✅».
"""

import json
import logging
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from config import config
from db.database import get_state, set_state

logger = logging.getLogger(__name__)

KEYS = ("yandex_token", "gsc_property", "gsc_key", "deploy_hooks",
        "cf_api_token", "cf_zone_id", "cf_zone_name", "second_opinion", "self_heartbeat_url",
        "status_page_slug", "github_token", "github_owner", "crux_key",
        "auto_redeploy", "auto_restart", "auto_cleanup", "region_speed")
_values: dict[str, str] = {}
_off: set[str] = set()      # switched off in the chat while .env still has a value


class Probe(StrEnum):
    """The answer of a live check of a key or token."""
    OK = "ok"
    REJECTED = "rejected"         # the service answered and turned the key down
    UNREACHABLE = "unreachable"   # network, timeout, 5xx — not the key's fault


async def load():
    global _values, _off
    _values, _off = {}, set()
    for key in KEYS:
        v = await get_state(f"int:{key}")
        if v:
            _values[key] = v
        if await get_state(f"int_off:{key}"):
            _off.add(key)


async def set_value(key: str, value: str | None):
    """A value from the chat; None forgets it and .env is the fallback
    again — the tombstone of «🗑 Отключить» goes away either way."""
    if key not in KEYS:
        raise ValueError(key)
    if value:
        _values[key] = value
    else:
        _values.pop(key, None)
    await set_state(f"int:{key}", value or None)
    if key in _off:
        _off.discard(key)
        await set_state(f"int_off:{key}", None)
    if key == "cf_zone_id" and _values.get("cf_zone_name"):
        await set_value("cf_zone_name", None)   # another zone: its name is learnt again


async def switch_off(key: str) -> bool:
    """«🗑 Отключить»: forget the chat value, and when .env has one too —
    hide it as well (True) until a new value is entered here."""
    await set_value(key, None)
    hide = bool(env_value(key))
    if hide:
        _off.add(key)
        await set_state(f"int_off:{key}", "1")
    return hide


def stored() -> dict[str, str]:
    """What was entered in the chat (not .env) — the Yandex wizard puts the
    old token back when a new one fails the live check."""
    return {k: v for k, v in _values.items() if k in KEYS and v}


def env_value(key: str):
    """What .env says for the key ('' or {} when nothing)."""
    return {
        "yandex_token": config.yandex_webmaster_token,
        "gsc_property": config.gsc_property,
        "gsc_key": config.gsc_service_account_file,
        "deploy_hooks": config.deploy_hooks,
        "cf_api_token": config.cf_api_token,
        "cf_zone_id": config.cf_zone_id,
        "self_heartbeat_url": config.self_heartbeat_url,
        "status_page_slug": config.status_page_slug,
        "github_token": config.github_token,
        "github_owner": config.github_owner,
        "crux_key": config.crux_api_key,
    }.get(key)


def source(key: str) -> str | None:
    """'bot' when entered in the chat, 'env' when it comes from .env, 'off'
    when switched off in the chat over a value in .env, None."""
    if key in _off:
        return "off"
    if _values.get(key):
        return "bot"
    return "env" if env_value(key) else None


def _get(key: str, env: str) -> str:
    if key in _off:
        return ""
    return _values.get(key) or env


# ── Yandex.Webmaster ─────────────────────────────────────────────────────────

def yandex_token() -> str:
    return _get("yandex_token", config.yandex_webmaster_token)


# ── Google Search Console ────────────────────────────────────────────────────

def gsc_property() -> str:
    return _get("gsc_property", config.gsc_property)


_gsc_key_problem: str | None = None    # what is wrong with GSC_SERVICE_ACCOUNT_FILE, for the screens


def gsc_credentials() -> dict | None:
    """Service-account dict from the chat-uploaded JSON, else from the file.
    Both go through valid_gsc_key; a file that cannot be read is named in
    the log once per new reason and on the screens (gsc_key_problem)."""
    global _gsc_key_problem
    if "gsc_key" in _off:
        _gsc_key_problem = None
        return None
    raw = _values.get("gsc_key")
    if not raw:
        path = config.gsc_service_account_file
        if not path or not os.path.exists(path):
            _gsc_key_problem = None
            return None
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            problem = f"{type(e).__name__}: {e.strerror or e}"
            if problem != _gsc_key_problem:
                logger.warning("GSC key %s is unusable for uid %s: %s", path,
                               getattr(os, "getuid", lambda: "?")(), problem)
            _gsc_key_problem = problem
            return None
    creds = valid_gsc_key(raw)
    _gsc_key_problem = None if creds else "это не JSON-ключ сервис-аккаунта (нет client_email или private_key)"
    return creds


def gsc_key_problem() -> str | None:
    """Why the GSC key is not usable, for «🔌 Подключения»; None when it is."""
    return _gsc_key_problem


def gsc_configured() -> bool:
    return bool(gsc_property() and gsc_credentials())


def valid_gsc_key(raw: str) -> dict | None:
    """Parse a pasted/uploaded service-account JSON; None when it is not one."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("client_email") or not data.get("private_key"):
        return None
    return data


# ── Cloudflare ───────────────────────────────────────────────────────────────

def deploy_hooks() -> dict[str, str]:
    """{host: hook url} from .env and the chat; a hook hidden in the chat
    (an empty url, or «🗑 Отключить» over .env) is not there."""
    hooks = {} if "deploy_hooks" in _off else {str(k).lower(): v for k, v in config.deploy_hooks.items()}
    raw = _values.get("deploy_hooks")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                hooks.update({str(k).lower(): str(v) for k, v in extra.items()})
        except ValueError:
            pass
    return {h: u for h, u in hooks.items() if u}


async def set_deploy_hook(host: str, url: str | None):
    """None removes the host's hook; for a host from .env that means hiding
    it (an empty url): the .env line stays, the bot stops using it."""
    raw = _values.get("deploy_hooks")
    try:
        hooks = json.loads(raw) if raw else {}
    except ValueError:
        hooks = {}
    host = host.lower()
    if url:
        hooks[host] = url
    elif host in {str(k).lower() for k in config.deploy_hooks}:
        hooks[host] = ""
    else:
        hooks.pop(host, None)
    await set_value("deploy_hooks", json.dumps(hooks) if hooks else None)


def cf_api_token() -> str:
    return _get("cf_api_token", config.cf_api_token)


def cf_zone_id() -> str:
    return _get("cf_zone_id", config.cf_zone_id)


def cf_purge_configured() -> bool:
    return bool(cf_api_token() and cf_zone_id())


def cf_zone_name() -> str:
    """example.com once Cloudflare named the zone (the first purge); '' until then."""
    return _values.get("cf_zone_name", "")


# ── Auto-actions ─────────────────────────────────────────────────────────────

def auto_enabled(kind: str) -> bool:
    """redeploy / restart / cleanup. The switch from the chat wins over .env."""
    v = _values.get(f"auto_{kind}")
    if v in ("on", "off"):
        return v == "on"
    return {"redeploy": config.auto_redeploy, "restart": bool(config.autorestart_containers),
            "cleanup": config.auto_cleanup}[kind]


# ── Watchdog & status page ───────────────────────────────────────────────────

def self_heartbeat_url() -> str:
    return _get("self_heartbeat_url", config.self_heartbeat_url)


def status_page_slug() -> str:
    return _get("status_page_slug", config.status_page_slug)


# ── GitHub ───────────────────────────────────────────────────────────────────

def github_token() -> str:
    return _get("github_token", config.github_token)


def github_owner() -> str:
    """'' means «the token's own account»."""
    return _get("github_owner", config.github_owner)


# ── Chrome UX Report ─────────────────────────────────────────────────────────

def crux_key() -> str:
    return _get("crux_key", config.crux_api_key)


# ── Second opinion ───────────────────────────────────────────────────────────

def second_opinion_enabled() -> bool:
    v = _values.get("second_opinion")
    if v in ("on", "off"):
        return v == "on"
    return config.second_opinion


def region_speed_enabled() -> bool:
    """«⏱ Скорость из региона» — the same external service (check-host.net),
    its own switch: every site's address goes there every 6 hours."""
    v = _values.get("region_speed")
    if v in ("on", "off"):
        return v == "on"
    return config.region_speed


# ── Rules for a value that arrives without its wizard (the JSON import) ──────

def _like(pattern: str, flags: int = 0) -> Callable[[object], bool]:
    rx = re.compile(pattern, flags)
    return lambda v: isinstance(v, str) and rx.fullmatch(v) is not None


def _hooks_ok(value: object) -> bool:
    try:
        hooks = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return False
    return isinstance(hooks, dict) and all(isinstance(h, str) and "." in h and isinstance(u, str)
                                           and u.startswith("https://") for h, u in hooks.items())


# What each value must look like: the same checks the «🔌 Подключения»
# wizards make (handlers/setup.py), minus their live calls. The JSON import
# stores a key only through its rule; a key without a rule is not imported.
RULES: dict[str, Callable[[object], bool]] = {
    "yandex_token": _like(r"[A-Za-z0-9_\-.]{20,200}"),
    "crux_key": _like(r"[A-Za-z0-9_\-]{30,60}"),
    "gsc_property": _like(r"(sc-domain:[a-z0-9.-]+|https?://[^\s]+)", re.I),
    "gsc_key": lambda v: isinstance(v, str) and valid_gsc_key(v) is not None,
    "deploy_hooks": _hooks_ok,
    "cf_api_token": _like(r"[A-Za-z0-9_\-]{30,100}"),
    "cf_zone_id": _like(r"[0-9a-f]{32}"),
    "github_token": _like(r"[A-Za-z0-9_]{30,255}"),
    "github_owner": _like(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"),
    "self_heartbeat_url": _like(r"https://[^\s]{10,300}"),
    "status_page_slug": _like(r"[A-Za-z0-9_-]{8,64}"),
    "second_opinion": lambda v: v in ("on", "off"),
    "cf_zone_name": _like(r"[a-z0-9.-]{3,253}"),
    "auto_redeploy": lambda v: v in ("on", "off"),
    "auto_restart": lambda v: v in ("on", "off"),
    "auto_cleanup": lambda v: v in ("on", "off"),
    "region_speed": lambda v: v in ("on", "off"),
}


# ── Health of the external services ──────────────────────────────────────────

HEALTH = {   # name: (как называть владельцу, что он теряет, кнопка мастера или None)
    "gsc": ("Google Search Console", "тревога «Google убрал сайт из поиска» и клики в воскресном отчёте не работают",
            "setup:gsc"),
    "yandex": ("Яндекс.Вебмастер", "тревоги о проблемах в Вебмастере и строки Яндекса в отчёте не работают",
               "setup:yx"),
    "crux": ("данные Google о скорости у посетителей", "строка «у посетителей» и её 🟠 не обновляются", "setup:crux"),
    "github": ("GitHub", "воскресная проверка репозиториев не работает", "setup:gh"),
    "checkhost": ("check-host.net — вторая точка проверки",
                  "тревога «сайт лёг» уходит без перепроверки извне, замер из региона не обновляется", None),
    "osv": ("база уязвимостей OSV", "проверка уязвимостей в пакетах бота не работает", None),
    "rdap": ("RDAP — сроки доменов", "сроки регистрации доменов не обновляются", None),
}
SILENT_FAIL = timedelta(days=1)       # a failure nobody refused is said after this long without success


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def health(name: str) -> dict:
    """{ok_at, fail_since, reason, refused, told} — {} when never recorded."""
    try:
        data = json.loads(await get_state(f"int_health:{name}") or "{}")
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


async def _save_health(name: str, data: dict):
    await set_state(f"int_health:{name}", json.dumps(data, ensure_ascii=False))


async def record_result(name: str, ok: bool, reason: str | None = None, refused: bool = False):
    """How the last call to an external service went. refused — it turned the
    key or token down (400 invalid_grant, 401, 403). A side note of the call
    it describes: it never raises into it."""
    try:
        h = await health(name)
        if ok:
            if not h.get("fail_since") and str(h.get("ok_at") or "")[:13] == _now()[:13]:
                return                     # healthy and already written this hour
            h.update(ok_at=_now(), fail_since=None, reason=None, refused=False)
        else:
            h.update(fail_since=h.get("fail_since") or _now(), reason=reason,
                     refused=refused or bool(h.get("refused")))
        await _save_health(name, h)
    except Exception as e:
        logger.debug("Health record for %s not written: %s", name, str(e) or type(e).__name__)


def is_broken(h: dict) -> bool:
    """Worth the owner's attention: a refused key, or a day without success."""
    since = h.get("fail_since")
    if not since:
        return False
    if h.get("refused"):
        return True
    try:
        return datetime.now(UTC) - datetime.fromisoformat(since) >= SILENT_FAIL
    except (TypeError, ValueError):
        return False


async def broken(name: str) -> bool:
    return is_broken(await health(name))


async def set_told(name: str, told: bool):
    h = await health(name)
    h["told"] = told
    await _save_health(name, h)
