from datetime import timedelta

from utils.parse import (
    parse_accepted_codes,
    parse_duration,
    parse_header_lines,
    parse_hour_range,
    parse_time_window,
)
from utils.text import bar, chunks, clip, fmt_duration, parse_sqlite_utc, plural, sparkline
from utils.urls import registrable_domain, same_site, site_label


def test_plural():
    assert plural(1, "символ", "символа", "символов") == "символ"
    assert plural(2, "символ", "символа", "символов") == "символа"
    assert plural(5, "символ", "символа", "символов") == "символов"
    assert plural(11, "символ", "символа", "символов") == "символов"
    assert plural(21, "символ", "символа", "символов") == "символ"


def test_sparkline_and_bar():
    assert sparkline([1, 2]) == ""
    assert sparkline([5, 5, 5]) == "▂▂▂"
    s = sparkline([0, 50, 100])
    assert s[0] == "▁" and s[-1] == "█"
    assert bar(50, 100, 10) == "▇▇▇▇▇     "
    assert bar(0, 100, 4) == "    "


def test_clip_and_chunks():
    assert clip("short") == "short"
    long = "x" * 5000
    assert clip(long).endswith("… (обрезано)") and len(clip(long)) <= 4096
    parts = chunks("\n".join(f"line {i}" for i in range(2000)), limit=500)
    assert all(len(p) <= 500 for p in parts)
    assert "\n".join(parts).count("line ") == 2000


def test_fmt_duration():
    assert fmt_duration(12) == "12 мин"
    assert fmt_duration(60) == "1 ч"
    assert fmt_duration(185) == "3 ч 5 мин"
    assert fmt_duration(2 * 1440 + 180) == "2 дн 3 ч"


def test_parsers():
    assert parse_hour_range("23-8") == (23, 8)
    assert parse_hour_range("8-8") is None
    assert parse_hour_range("x") is None
    assert parse_duration("8h") == timedelta(hours=8)
    assert parse_duration("30") == timedelta(minutes=30)
    assert parse_duration("0h") is None and parse_duration("abc") is None
    assert parse_time_window("01:30-03:00") == (90, 180)
    assert parse_time_window("23:00-06:00") == (1380, 360)
    assert parse_time_window("25:00-06:00") is None
    assert parse_accepted_codes("200-299,401") == [(200, 299), (401, 401)]
    assert parse_accepted_codes("999") is None and parse_accepted_codes("") is None
    assert parse_header_lines("Authorization: Bearer x\nAccept: */*") == {
        "Authorization": "Bearer x", "Accept": "*/*"}
    assert parse_header_lines("no colon") is None


def test_sqlite_utc():
    dt = parse_sqlite_utc("2026-09-15 10:00:00")
    assert dt and dt.tzinfo is not None
    assert parse_sqlite_utc("garbage") is None


def test_urls():
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("a.b.example.co.uk") == "example.co.uk"
    assert registrable_domain("localhost") == "localhost"
    assert same_site("https://app.example.com/x", "https://example.com")
    assert not same_site("https://evil.com", "https://example.com")
    assert site_label("tcp://mail.example.com:25") == "mail.example.com:25"
    assert site_label("ping://10.0.0.1") == "ping 10.0.0.1"
    assert site_label("https://example.com/") == "example.com"
