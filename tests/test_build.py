"""What the build promises: a start does not load cryptography (GSC is
optional), the pytest warning filters do what they say, and the lock is
what requirements.in asks for."""

import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_a_start_does_not_load_cryptography():
    code = "import sys, main; print(sorted(m for m in sys.modules if m.split('.')[0] == 'cryptography'))"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={"PATH": "/usr/bin:/bin", "TELEGRAM_BOT_TOKEN": "0:fake", "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "[]"


def test_a_deprecation_in_aiogram_fails_the_tests():
    """pyproject.toml: the aiogram rule must come last — pytest applies the
    filters in order and the last match wins."""
    with pytest.raises(DeprecationWarning):
        warnings.warn_explicit("old API", DeprecationWarning, "aiogram/client/bot.py", 1,
                               module="aiogram.client.bot")


def _pins(path: Path) -> dict[str, str]:
    return {re.sub(r"[-_.]+", "-", name).lower(): version
            for name, version in re.findall(r"(?m)^([A-Za-z0-9._-]+)==([^\s;\\]+)", path.read_text())}


def test_the_lock_is_compiled_from_requirements_in():
    """requirements.in names what the bot needs; requirements.txt is generated
    from it with every transitive package pinned and hashed."""
    direct, lock = _pins(ROOT / "requirements.in"), _pins(ROOT / "requirements.txt")
    assert direct and {name: lock.get(name) for name in direct} == direct
    assert len(lock) > len(direct) and "--hash=sha256:" in (ROOT / "requirements.txt").read_text()


def test_the_dev_tools_are_pinned():
    lines = [line.split("#")[0].strip() for line in (ROOT / "requirements-dev.txt").read_text().splitlines()]
    assert all("==" in line for line in lines if line), lines
