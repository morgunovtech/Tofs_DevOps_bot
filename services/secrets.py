"""Webhook and heartbeat secrets.

They are deliberately DIFFERENT values: the webhook secret ships inside the
feedback widget to every site visitor (it is a spam filter, not auth), while
the heartbeat secret guards the dead-man switch. Sharing one value would let
anyone who views the site source silence heartbeat alerts.

Empty or placeholder env values are replaced by generated secrets stored in
bot_state, so a bot with only TELEGRAM_BOT_TOKEN set is still safe. A secret
replaced with «🔑» in the bot stays replaced: the env value is remembered as
revoked and ignored until it changes. /shot addresses are signed with a key
of their own, never shown anywhere; the README uptime badges with another
one — a badge address must not carry the status page's secret slug.
"""

import hashlib
import hmac
import logging
import secrets as _secrets

from config import config
from db.database import get_state, set_state

logger = logging.getLogger(__name__)

PLACEHOLDERS = frozenset({
    "", "change_me", "change_me_to_random_string", "changeme", "secret",
    "your_secret", "your_webhook_secret", "password",
})

_webhook: str = ""
_heartbeat: str = ""
_shots: str = ""
_badge: str = ""
# Env names whose value is set but not used (equal to the public widget
# secret, or replaced with «🔑» in the bot) — «🔌 Подключения» says so.
_ignored_env: set[str] = set()


def _usable(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDERS and len(value.strip()) >= 8


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.strip().encode()).hexdigest()


def _env_name(name: str) -> str:
    return f"{name.upper()}_SECRET"


async def _resolve(name: str, env_value: str) -> str:
    key = f"secret:{name}"
    if _usable(env_value):
        if await get_state(f"{key}:revoked") != _fingerprint(env_value):
            return env_value.strip()
        _ignored_env.add(_env_name(name))
        logger.warning("%s was replaced in the bot (🔑) — the new secret is used; "
                       "remove the old value from the environment", _env_name(name))
    value, generated = await _stored_or_new(key)
    if generated:
        logger.warning("%s secret was empty or a placeholder — generated one and stored it in the DB "
                       "(the widget snippet in ⚙️ Настройки → 🌍 Для посетителей → 📩 Сообщения carries the "
                       "webhook one; each job's card carries its heartbeat address).", name)
    return value


async def _stored_or_new(key: str) -> tuple[str, bool]:
    stored = await get_state(key)
    if stored:
        return stored, False
    value = _secrets.token_urlsafe(24)
    await set_state(key, value)
    return value, True


async def load():
    global _webhook, _heartbeat, _shots, _badge
    _ignored_env.clear()
    _webhook = await _resolve("webhook", config.webhook_secret_env)
    _heartbeat = await _resolve("heartbeat", config.heartbeat_secret_env)
    if _heartbeat == _webhook:
        # The webhook secret is public (it is inside the JS widget): a heartbeat
        # that accepted it would let any visitor keep a dead job «alive».
        logger.error("HEARTBEAT_SECRET equals the public WEBHOOK_SECRET — ignoring it; the heartbeat "
                     "uses its own generated secret (⚙️ Настройки → 🔌 Подключения)")
        _ignored_env.add("HEARTBEAT_SECRET")
        _heartbeat = await _resolve("heartbeat", "")
        if _heartbeat == _webhook:
            _heartbeat = await regenerate("heartbeat")
    # The key that signs /shot addresses: never shown anywhere, so a leaked
    # heartbeat address cannot sign them.
    _shots, _ = await _stored_or_new("secret:shots")
    # The key behind README badge addresses (/badge/<id>-<key>.svg): never
    # from .env, never shown — a badge in a public README must not reveal
    # the page's secret slug, and a site number alone must not fetch it.
    _badge, _ = await _stored_or_new("secret:badge")


async def regenerate(name: str) -> str:
    """Replace a secret with a fresh random one (stored in the DB). The env
    value, if any, is remembered as revoked: it is ignored after a restart
    too, until the operator changes it."""
    global _webhook, _heartbeat, _badge
    value = _secrets.token_urlsafe(24)
    await set_state(f"secret:{name}", value)
    env_value = {"webhook": config.webhook_secret_env, "heartbeat": config.heartbeat_secret_env}.get(name, "")
    if _usable(env_value):
        await set_state(f"secret:{name}:revoked", _fingerprint(env_value))
        _ignored_env.add(_env_name(name))
    if name == "webhook":
        _webhook = value
    elif name == "badge":
        _badge = value
    else:
        _heartbeat = value
    return value


def webhook_secret() -> str:
    return _webhook


def heartbeat_secret() -> str:
    return _heartbeat


def heartbeat_token(job: str) -> str:
    """The job's own token for its ping address, derived from the heartbeat
    secret: a cron line that leaks exposes this one job, not all of them.
    Rotating the secret («🔑») therefore changes every job's address."""
    return hmac.new(_heartbeat.encode(), f"hb:{job}".encode(), hashlib.sha256).hexdigest()[:24]


def shots_key() -> str:
    return _shots


def badge_key() -> str:
    return _badge


def ignored_env() -> list[str]:
    return sorted(_ignored_env)
