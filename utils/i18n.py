"""The chat language.

Russian is the source language: every user-facing string in the code is
written in Russian and doubles as the lookup key; English lives in
locales/en/. t() returns the text in the configured language (BOT_LANG,
«ru» by default), and a string without an English entry falls back to
Russian — tests/test_i18n.py renders every screen, alert and report in
English and fails on the first Cyrillic letter, so a key that drifted from
its entry is caught.

Module-level constants (button labels, the help text) stay Russian and are
translated where they are shown: t() runs at render time, after config.py
called set_language(). Usage: t("Сохранено"); t("Сайт {label} лёг").format(
label=…) — the placeholders are part of the key; for counts,
plural(n, "сайт", "сайта", "сайтов") — the English forms are looked up by
the Russian singular; fmt_duration() picks the unit words by language.

What visitors see — the status page, its badges and the widget — has its
own STATUS_PAGE_LANG and never goes through here.
"""

from __future__ import annotations

from utils import text as _text

LANGUAGES = ("ru", "en")
DURATION_UNITS = {"ru": ("мин", "ч", "дн"), "en": ("min", "h", "d")}

_lang = "ru"
_strings: dict[str, str] = {}
_plurals: dict[str, tuple[str, str]] = {}


def set_language(lang: str) -> None:
    global _lang, _strings, _plurals
    if lang not in LANGUAGES:
        raise ValueError(f"Unknown language: {lang}")
    _lang = lang
    if lang == "en":
        from locales.en import PLURALS, STRINGS  # noqa: PLC0415 — only when English is on

        _strings, _plurals = STRINGS, PLURALS
    else:
        _strings, _plurals = {}, {}
    # utils.text cannot import this module (it is imported by it): the one
    # string it shows, the mark of a clipped message, is set from here.
    _text.CLIP_SUFFIX = "\n" + t(_text.CLIPPED)


def language() -> str:
    return _lang


def t(text: str) -> str:
    """The text in the chat language; the Russian source when there is no translation."""
    if _lang == "ru":
        return text
    return _strings.get(text, text)


def plural(n: int, one: str, few: str, many: str) -> str:
    """The noun form for n: Russian one/few/many (utils.text.plural), or the
    English singular/plural looked up by the Russian singular."""
    if _lang == "ru":
        return _text.plural(n, one, few, many)
    forms = _plurals.get(one)
    if forms:
        return forms[0] if abs(int(n)) == 1 else forms[1]
    return one if abs(int(n)) == 1 else many


def fmt_duration(minutes: float) -> str:
    """'12 мин' · '3 ч 5 мин' · '2 дн 3 ч' — or '12 min' · '3 h 5 min' · '2 d 3 h'."""
    return _text.fmt_duration(minutes, DURATION_UNITS[_lang])
