"""Ready-to-paste URLs for things the bot serves over HTTP."""

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


def badge_url(site_id: int) -> str:
    """Shields-style SVG uptime badge for embedding in a README."""
    return f"{base_url()}{_status_path()}/badge/{site_id}.svg"


def heartbeat_url(job: str) -> str:
    """The job's own address: its token, not the shared secret (services.secrets)."""
    return f"{base_url()}/api/heartbeat/{secrets.heartbeat_token(job)}/{_seg(job)}"


def widget_url() -> str:
    """The widget file under its version: the snippet pins the exact bytes
    (SRI), and a changed file gets a new address, so caches never mix them."""
    return f"{base_url()}/feedback-widget.{widget.VERSION}.js"


def feedback_url() -> str:
    return f"{base_url()}/api/feedback"
