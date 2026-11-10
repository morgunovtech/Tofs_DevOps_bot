"""Credentials and switches for optional integrations, editable from the
chat. Values entered in the bot live in bot_state and win over .env; .env
stays the fallback so existing deployments keep working unchanged.

Keys: yandex_token, gsc_property, gsc_key (service-account JSON text),
deploy_hooks (JSON {host: url}), cf_api_token, cf_zone_id, cf_zone_name
(learnt from Cloudflare on the first purge), second_opinion ("on"/"off"),
github_token, github_owner, crux_key (Google API key for the Chrome UX
Report), auto_redeploy / auto_restart / auto_cleanup ("on"/"off": the
switches of «🤖 Что бот делает сам», they win over .env).
"""

import json
import os
import re
from collections.abc import Callable

from config import config
from db.database import get_state, set_state

KEYS = ("yandex_token", "gsc_property", "gsc_key", "deploy_hooks",
        "cf_api_token", "cf_zone_id", "cf_zone_name", "second_opinion", "self_heartbeat_url",
        "status_page_slug", "github_token", "github_owner", "crux_key",
        "auto_redeploy", "auto_restart", "auto_cleanup")
_values: dict[str, str] = {}


async def load():
    global _values
    _values = {}
    for key in KEYS:
        v = await get_state(f"int:{key}")
        if v:
            _values[key] = v


async def set_value(key: str, value: str | None):
    if key not in KEYS:
        raise ValueError(key)
    if value:
        _values[key] = value
    else:
        _values.pop(key, None)
    await set_state(f"int:{key}", value or None)
    if key == "cf_zone_id" and _values.get("cf_zone_name"):
        await set_value("cf_zone_name", None)   # another zone: its name is learnt again


def stored() -> dict[str, str]:
    """What was entered in the chat (not .env) — for export and rollback."""
    return {k: v for k, v in _values.items() if k in KEYS and v}


def source(key: str) -> str | None:
    """'bot' when entered in the chat, 'env' when it comes from .env, None."""
    if _values.get(key):
        return "bot"
    env = {
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
    return "env" if env else None


# ── Yandex.Webmaster ─────────────────────────────────────────────────────────

def yandex_token() -> str:
    return _values.get("yandex_token") or config.yandex_webmaster_token


# ── Google Search Console ────────────────────────────────────────────────────

def gsc_property() -> str:
    return _values.get("gsc_property") or config.gsc_property


def gsc_credentials() -> dict | None:
    """Service-account dict from the chat-uploaded JSON, else from the file."""
    raw = _values.get("gsc_key")
    if raw:
        try:
            return json.loads(raw)
        except ValueError:
            return None
    path = config.gsc_service_account_file
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    return None


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
    hooks = {str(k).lower(): v for k, v in config.deploy_hooks.items()}
    raw = _values.get("deploy_hooks")
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                hooks.update({str(k).lower(): str(v) for k, v in extra.items()})
        except ValueError:
            pass
    return hooks


async def set_deploy_hook(host: str, url: str | None):
    raw = _values.get("deploy_hooks")
    try:
        hooks = json.loads(raw) if raw else {}
    except ValueError:
        hooks = {}
    if url:
        hooks[host.lower()] = url
    else:
        hooks.pop(host.lower(), None)
    await set_value("deploy_hooks", json.dumps(hooks) if hooks else None)


def cf_api_token() -> str:
    return _values.get("cf_api_token") or config.cf_api_token


def cf_zone_id() -> str:
    return _values.get("cf_zone_id") or config.cf_zone_id


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
    return _values.get("self_heartbeat_url") or config.self_heartbeat_url


def status_page_slug() -> str:
    return _values.get("status_page_slug") or config.status_page_slug


# ── GitHub ───────────────────────────────────────────────────────────────────

def github_token() -> str:
    return _values.get("github_token") or config.github_token


def github_owner() -> str:
    """'' means «the token's own account»."""
    return _values.get("github_owner") or config.github_owner


# ── Chrome UX Report ─────────────────────────────────────────────────────────

def crux_key() -> str:
    return _values.get("crux_key") or config.crux_api_key


# ── Second opinion ───────────────────────────────────────────────────────────

def second_opinion_enabled() -> bool:
    v = _values.get("second_opinion")
    if v in ("on", "off"):
        return v == "on"
    return config.second_opinion


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
}
