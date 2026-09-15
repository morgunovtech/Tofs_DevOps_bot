"""Webhook and heartbeat secrets.

They are deliberately DIFFERENT values: the webhook secret ships inside the
feedback widget to every site visitor (it is a spam filter, not auth), while
the heartbeat secret guards the dead-man switch. Sharing one value would let
anyone who views the site source silence heartbeat alerts.

Empty or placeholder env values are replaced by generated secrets stored in
bot_state, so a bot with only TELEGRAM_BOT_TOKEN set is still safe.
"""

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
_generated: list[str] = []


def _usable(value: str) -> bool:
    return value.strip().lower() not in PLACEHOLDERS and len(value.strip()) >= 8


async def _resolve(name: str, env_value: str) -> str:
    if _usable(env_value):
        return env_value.strip()
    key = f"secret:{name}"
    stored = await get_state(key)
    if stored:
        return stored
    value = _secrets.token_urlsafe(24)
    await set_state(key, value)
    _generated.append(name)
    logger.warning("%s secret was empty or a placeholder — generated one and "
                   "stored it in the DB (see 🩺 Диагностика).", name)
    return value


async def load():
    global _webhook, _heartbeat
    _webhook = await _resolve("webhook", config.webhook_secret_env)
    _heartbeat = await _resolve("heartbeat", config.heartbeat_secret_env)
    if _heartbeat == _webhook:
        # HEARTBEAT_SECRET explicitly set to the same value — still unsafe.
        logger.warning("HEARTBEAT_SECRET equals WEBHOOK_SECRET — the webhook "
                       "secret is public (it is inside the JS widget). "
                       "Give the heartbeat its own value.")


def webhook_secret() -> str:
    return _webhook


def heartbeat_secret() -> str:
    return _heartbeat


def generated() -> list[str]:
    """Names of secrets generated at this boot (for diagnostics)."""
    return list(_generated)
