import ast
import pathlib
import re
from types import SimpleNamespace

from conftest import FakeMessage, FakeState

import main
from handlers import start
from handlers.filters import AdminFilter
from handlers.sites import parse_site_input
from handlers.start import HELP, rules_of_the_game
from services import runtime


def test_help_explains_the_levels():
    """The legend the owner reads once: what each icon means and how it arrives."""
    assert "🔴 — нужно действие сейчас" in HELP and "🟠 — нужно действие, но не сегодня" in HELP
    assert "⚠️ — не смог проверить" in HELP and "✅ — всё в порядке или снова работает" in HELP
    # Plain words: the owner is not expected to know the jargon.
    for word in ("алерт", "дашборд", "mute и", "дайджест", "noindex", "NS"):
        assert word not in HELP
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


def test_one_word_for_being_alive():
    """«Отметка» meant nothing to the owner; «на связи / молчит» did. No
    user-facing string may bring the old word back."""
    old = re.compile(r"отметк|отмеча|отметил|отчитал", re.IGNORECASE)
    found = []
    for folder in ("handlers", "services", "reports", "web"):
        for path in pathlib.Path(folder).glob("*.py"):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and old.search(node.value):
                    if not node.value.lstrip().startswith(("A ", "The ", "«")):   # docstrings are English
                        found.append(f"{path}:{node.lineno}")
    assert found == []


async def test_start_leaves_nothing_for_the_bottom_buttons_note(bot, db):
    """/start brings the bottom buttons itself, so the one-time «кнопки
    теперь под полем ввода» note must not follow it."""
    msg = FakeMessage("/start")
    msg.from_user, msg.chat = SimpleNamespace(id=777), SimpleNamespace(id=777)
    await start.cmd_start(msg, FakeState())
    n = len(bot.sent)
    await main._show_bottom_buttons(bot)
    assert len(bot.sent) == n
