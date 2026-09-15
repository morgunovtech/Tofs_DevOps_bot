"""Text helpers: Telegram HTML escaping, message limits, Russian plurals,
durations, sparklines. Pure functions, no project imports."""

import html
from datetime import UTC, datetime

TG_MESSAGE_LIMIT = 4096
SAFE_LIMIT = TG_MESSAGE_LIMIT - 100


def esc(value) -> str:
    """HTML-escape a value for ParseMode.HTML."""
    if value is None:
        return ""
    return html.escape(str(value), quote=False)


def clip(text: str, limit: int = SAFE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n… (обрезано)"


def chunks(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Split a long message on newlines into Telegram-sized pieces without
    losing anything (unlike clip)."""
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    buf = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:limit])
            line = line[limit:]
        candidate = f"{buf}\n{line}" if buf else line
        if len(candidate) > limit:
            out.append(buf)
            buf = line
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian numeral agreement: 1 символ, 2 символа, 5 символов."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def fmt_date(iso: str | None) -> str:
    """'2026-10-15…' → '15.10.2026'."""
    try:
        return datetime.strptime((iso or "")[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso or "N/A"


def parse_sqlite_utc(value: str | None) -> datetime | None:
    """sqlite datetime('now') string ('YYYY-MM-DD HH:MM:SS', UTC) → aware dt."""
    try:
        return datetime.strptime(value or "", "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=UTC)
    except ValueError:
        return None


def parse_iso_utc(value: str | None) -> datetime | None:
    """ISO string (naive = UTC) → aware datetime, None when unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fmt_duration(minutes: float) -> str:
    """The one duration formatter: '12 мин' · '3 ч 5 мин' · '2 дн 3 ч'."""
    m = max(0, round(minutes))
    if m < 60:
        return f"{m} мин"
    if m < 24 * 60:
        h, rest = divmod(m, 60)
        return f"{h} ч {rest} мин" if rest else f"{h} ч"
    d, rest = divmod(m, 24 * 60)
    h = rest // 60
    return f"{d} дн {h} ч" if h else f"{d} дн"


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list) -> str:
    """▂▃▂▁▅▂ for response-time trends; '' when there is too little data."""
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return ""
    lo, hi = min(vals), max(vals)
    if hi <= lo:
        return SPARK_CHARS[1] * len(vals)
    return "".join(
        SPARK_CHARS[min(7, int((v - lo) / (hi - lo) * 7 + 0.5))] for v in vals)


def bar(value: float, max_value: float, width: int = 20) -> str:
    """Monospace bar for ASCII charts: '▇▇▇▇▇     '."""
    if max_value <= 0 or value <= 0:
        return " " * width
    n = max(1, round(value / max_value * width))
    return "▇" * n + " " * (width - n)
