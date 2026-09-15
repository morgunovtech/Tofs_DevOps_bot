from types import SimpleNamespace

from handlers.filters import AdminFilter
from handlers.sites import parse_site_input
from services import runtime


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
