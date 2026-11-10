"""What the build promises: a start does not load cryptography (GSC is
optional), and the pytest warning filters do what they say."""

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
