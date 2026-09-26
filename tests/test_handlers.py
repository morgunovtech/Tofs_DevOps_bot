from types import SimpleNamespace

from handlers.filters import AdminFilter
from handlers.sites import parse_site_input
from handlers.start import HELP, rules_of_the_game
from services import runtime


def test_help_explains_the_levels():
    """The legend the owner reads once: what each icon means and how it arrives."""
    assert "🔴 — нужно действие сейчас" in HELP and "🟠 — нужно действие, но не сегодня" in HELP
    assert "⚠️ — не смог проверить" in HELP and "✅ — снова работает" in HELP
    assert "Пишу только если что-то сломалось" in rules_of_the_game()


def test_parse_site_input():
    assert parse_site_input("Example.COM") == ("https://example.com", None)
    assert parse_site_input("http://httpbin.org/get") == ("http://httpbin.org", None)
    assert parse_site_input("https://app.example.com:8443") == ("https://app.example.com:8443", None)
    assert parse_site_input("tcp://mail.example.com:25") == ("tcp://mail.example.com:25", None)
    assert parse_site_input("ping://10.0.0.1") == ("ping://10.0.0.1", None)
    assert parse_site_input("tcp://mail.example.com")[0] is None
    assert parse_site_input("not a domain")[0] is None
    assert parse_site_input("localhost")[0] is None          # web sites need a real domain
    assert parse_site_input("a" * 70 + ".com")[0] is None    # label too long


async def test_admin_filter(monkeypatch):
    monkeypatch.setattr(runtime, "_chat_id", "10")
    monkeypatch.setattr(runtime, "_user_id", "10")
    f = AdminFilter()
    assert await f(SimpleNamespace(from_user=SimpleNamespace(id=10)))
    assert not await f(SimpleNamespace(from_user=SimpleNamespace(id=11)))
    assert not await f(SimpleNamespace(from_user=None))
