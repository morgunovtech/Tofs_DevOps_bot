import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_chat_id: str = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")
    # Personal user id for admin checks. Falls back to admin_chat_id, which
    # only works while the admin chat is a private chat (user id == chat id).
    admin_user_id: str = os.getenv("TELEGRAM_ADMIN_USER_ID", "")
    # Only trust X-Forwarded-For when explicitly behind a reverse proxy.
    trust_proxy: bool = os.getenv("TRUST_PROXY", "0").lower() in ("1", "true", "yes")
    sites: list[str] = field(default_factory=lambda: [
        s.strip() for s in os.getenv("SITES", "").split(",") if s.strip()
    ])
    check_interval_minutes: int = int(os.getenv("CHECK_INTERVAL_MINUTES", "5"))
    links_check_interval_hours: int = int(os.getenv("LINKS_CHECK_INTERVAL_HOURS", "6"))
    morning_report_hour: int = int(os.getenv("MORNING_REPORT_HOUR", "9"))
    evening_report_hour: int = int(os.getenv("EVENING_REPORT_HOUR", "21"))
    timezone: str = os.getenv("TIMEZONE", "Europe/Moscow")
    webhook_port: int = int(os.getenv("WEBHOOK_PORT", "8080"))
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "change_me")
    db_path: str = os.getenv("DB_PATH", "data/bot.db")

    def get_site_urls(self) -> list[str]:
        return [f"https://{s}" if not s.startswith("http") else s for s in self.sites]


config = Config()
