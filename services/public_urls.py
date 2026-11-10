"""Ready-to-paste URLs for things the bot serves over HTTP."""

import hashlib
import hmac
from urllib.parse import quote

from config import config
from services import integrations, secrets
from web import widget

PLACEHOLDER_HOST = "YOUR_SERVER"
NOT_PUBLIC = ("⚠️ У бота нет публичного адреса, поэтому ссылка ниже не заработает: вместо YOUR_SERVER "
              "нужен адрес сервера бота. Задай PUBLIC_BASE_URL (https://…) — кнопка «🌍 Как задать адрес».")


def base_url() -> str:
    """PUBLIC_BASE_URL, else the domain Railway generated, else a placeholder."""
    if config.public_base_url:
        return config.public_base_url
    if config.railway_public_domain:
        return f"https://{config.railway_public_domain}"
    return f"http://{PLACEHOLDER_HOST}:{config.webhook_port}"


def is_public() -> bool:
    return PLACEHOLDER_HOST not in base_url()


def _seg(value: str) -> str:
    """One path segment, percent-encoded: a space or «/» in it must not break the link."""
    return quote(value, safe="")


def _status_path() -> str:
    slug = integrations.status_page_slug()
    return f"/status/{_seg(slug)}" if slug else "/status"


def status_page_url() -> str:
    return base_url() + _status_path()


def badge_token(site_id: int) -> str:
    """The badge's own key: a README badge must not carry the page's secret
    slug, and a site number alone must not be enough to fetch it."""
    return hmac.new(secrets.badge_key().encode(), f"badge:{site_id}".encode(), hashlib.sha256).hexdigest()[:16]


def badge_token_ok(site_id: int, token: str) -> bool:
    return hmac.compare_digest(badge_token(site_id).encode(), token.encode())


def badge_url(site_id: int) -> str:
    """Shields-style SVG uptime badge for embedding in a README: its own
    signed address — no secret page slug in it, and not guessable by number."""
    return f"{base_url()}/badge/{site_id}-{badge_token(site_id)}.svg"


def heartbeat_url(job: str) -> str:
    """The job's own address: its token, not the shared secret (services.secrets)."""
    return f"{base_url()}/api/heartbeat/{secrets.heartbeat_token(job)}/{_seg(job)}"


def widget_url() -> str:
    """The widget file under its version: the snippet pins the exact bytes
    (SRI), and a changed file gets a new address, so caches never mix them."""
    return f"{base_url()}/feedback-widget.{widget.VERSION}.js"


def feedback_url() -> str:
    return f"{base_url()}/api/feedback"
