"""Pure parsers shared by config (env) and the chat UI. No project imports."""

import re
from datetime import timedelta


def parse_hour_range(raw: str | None) -> tuple[int, int] | None:
    """'23-8' → (23, 8). None for empty/malformed/equal hours."""
    if not raw or "-" not in raw:
        return None
    try:
        a, b = raw.split("-", 1)
        start, end = int(a), int(b)
    except ValueError:
        return None
    if 0 <= start <= 23 and 0 <= end <= 23 and start != end:
        return (start, end)
    return None


def parse_duration(arg: str | None) -> timedelta | None:
    """'1h', '8h', '30m', '1d', '45' (minutes) → timedelta; None if unparseable."""
    if not arg:
        return None
    arg = arg.strip().lower()
    units = {"m": 60, "h": 3600, "d": 86400}
    if arg[-1] in units:
        try:
            num = int(arg[:-1])
        except ValueError:
            return None
        return timedelta(seconds=num * units[arg[-1]]) if num > 0 else None
    try:
        minutes = int(arg)
    except ValueError:
        return None
    return timedelta(minutes=minutes) if minutes > 0 else None


def parse_time_window(raw: str) -> tuple[int, int] | None:
    """'01:30-03:00' → (90, 180) minutes since midnight. Overnight windows
    (23:00-06:00) are allowed; start == end is not."""
    m = re.fullmatch(r"(\d{1,2}):(\d{2})\s*[-—–]\s*(\d{1,2}):(\d{2})", (raw or "").strip())
    if not m:
        return None
    h1, m1, h2, m2 = map(int, m.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    return (start, end) if start != end else None


def parse_accepted_codes(spec: str | None) -> list[tuple[int, int]] | None:
    """'200-399' / '200-299,401,403' → [(lo, hi), …]. None = default rule
    (any status below 400) or a malformed spec."""
    if not spec:
        return None
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        a, _, b = part.partition("-")
        try:
            lo = int(a)
            hi = int(b) if b else lo
        except ValueError:
            return None
        if not (100 <= lo <= hi <= 599):
            return None
        out.append((lo, hi))
    return out or None


def parse_header_lines(raw: str) -> dict[str, str] | None:
    """'Authorization: Bearer x\\nAccept: */*' → dict. None on a bad line."""
    headers: dict[str, str] = {}
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        name, sep, value = line.partition(":")
        name = name.strip()
        if not sep or not name or any(c in name for c in " \t\r\n") or not name.isascii():
            return None
        headers[name] = value.strip()
    return headers
