"""Ready-to-paste URLs for things the bot serves over HTTP."""

from config import config
from services import secrets


def base_url() -> str:
    return config.public_base_url or f"http://YOUR_SERVER:{config.webhook_port}"


def _status_path() -> str:
    path = "/status"
    if config.status_page_slug:
        path += f"/{config.status_page_slug}"
    return path


def status_page_url() -> str:
    return base_url() + _status_path()


def status_json_url() -> str:
    return base_url() + _status_path() + ".json"


def badge_url(site_id: int) -> str:
    """Shields-style SVG uptime badge for embedding in a README."""
    return f"{base_url()}{_status_path()}/badge/{site_id}.svg"


def heartbeat_url(job: str) -> str:
    return f"{base_url()}/api/heartbeat/{secrets.heartbeat_secret()}/{job}"


def widget_url() -> str:
    return f"{base_url()}/feedback-widget.js"


def feedback_url() -> str:
    return f"{base_url()}/api/feedback"
