"""English. Russian text → English, split by the area of the bot the strings
belong to (handlers, services, reports, monitors); the files are merged
here. Keys must match the Russian source byte for byte, placeholders
({label}, {n}) included."""

from __future__ import annotations

from locales.en import handlers, services

_PARTS = (handlers, services)

STRINGS: dict[str, str] = {k: v for part in _PARTS for k, v in part.STRINGS.items()}
# Russian singular → (English singular, English plural).
PLURALS: dict[str, tuple[str, str]] = {k: v for part in _PARTS for k, v in part.PLURALS.items()}
