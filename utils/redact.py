"""Secrets out of log lines and error texts: the Bot API token, tokens and
keys in a URL query, a password in a URL, plus the secret values the bot
knows (its own token, the heartbeat secret, the watchdog and deploy-hook
addresses). One formatter covers the message, its arguments and the
traceback of every logger — aiogram, aiohttp and apscheduler included."""

import logging
import re
from collections.abc import Callable, Iterable

_PATTERNS = (
    (re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+"), r"\1<token>"),                    # Bot API, /file/bot… too
    (re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"), "<token>"),                 # a bare bot token
    (re.compile(r"([?&](?:token|key|api_key|apikey|secret|access_token|sig|signature|password)=)"
                r"[^&\s'\"]+", re.I), r"\1<redacted>"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1<user>:<pass>@"),
)
# Shorter values are too likely to be ordinary words.
MIN_SECRET_LEN = 8


def redact(text: str, values: Iterable[str] = ()) -> str:
    for rx, sub in _PATTERNS:
        text = rx.sub(sub, text)
    for value in values:
        if value and len(value) >= MIN_SECRET_LEN:
            text = text.replace(value, "<secret>")
    return text


def without_query(text: str) -> str:
    """For texts the owner sees (an alert, a check result): no URL query at
    all — an API check may carry its token under any parameter name."""
    return re.sub(r"\?[^\s'\"]*", "?…", text)


class RedactingFormatter(logging.Formatter):
    """Formats as usual, then masks — the message and the traceback alike."""

    def __init__(self, fmt: str, values: Callable[[], Iterable[str]] = tuple):
        super().__init__(fmt)
        self._values = values

    def format(self, record: logging.LogRecord) -> str:
        try:
            values = list(self._values())
        except Exception:            # a log line must never fail because of the mask
            values = []
        return redact(super().format(record), values)
