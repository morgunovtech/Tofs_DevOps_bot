"""Credentials and switches for optional integrations, editable from the
chat. Values entered in the bot live in bot_state and win over .env; .env
stays the fallback so existing deployments keep working unchanged.

Keys: yandex_token, gsc_property, gsc_key (service-account JSON text),
deploy_hooks (JSON {host: url}), cf_api_token, cf_zone_id,
second_opinion ("on"/"off").
"""

import json
import os

from config import config
from db.database import get_state, set_state

KEYS = ("yandex_token", "gsc_property", "gsc_key", "deploy_hooks",
        "cf_api_token", "cf_zone_id", "second_opinion")
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
    hooks = dict(config.deploy_hooks)
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


# ── Second opinion ───────────────────────────────────────────────────────────

def second_opinion_enabled() -> bool:
    v = _values.get("second_opinion")
    if v in ("on", "off"):
        return v == "on"
    return config.second_opinion
