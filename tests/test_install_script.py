"""scripts/install.sh — the one-command install the READMEs offer.

It must write exactly the compose file the READMEs show, keep an existing
token, and refuse to guess when Docker is missing.
"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install.sh"


def _script_compose() -> str:
    body = re.search(r"<<'YAML'\n(.*?)\nYAML\n", SCRIPT.read_text("utf-8"), re.S).group(1)
    return "\n".join(line for line in body.splitlines() if not line.startswith("# Written by"))


def _readme_compose(name: str) -> str:
    text = (ROOT / name).read_text("utf-8")
    return re.search(r"`docker-compose\.yml`:\n\n```yaml\n(.*?)\n```", text, re.S).group(1)


@pytest.mark.parametrize("readme", ["README.md", "README.ru.md"])
def test_the_script_writes_the_compose_file_the_readme_shows(readme):
    assert _script_compose() == _readme_compose(readme)


@pytest.mark.parametrize("readme", ["README.md", "README.ru.md"])
def test_the_readme_command_points_at_the_script(readme):
    assert ("https://raw.githubusercontent.com/morgunovtech/Tofs_DevOps_bot/main/scripts/install.sh | sh"
            in (ROOT / readme).read_text("utf-8"))


def _run(tmp_path: Path, *, docker: str | None):
    """Run the script with a fake `docker` (or none) first on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if docker is not None:
        fake = bin_dir / "docker"
        fake.write_text(docker, "utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "TOFS_DIR": str(tmp_path / "bot")}
    if docker is None:   # /usr/bin may hold a real docker on a dev machine
        env["PATH"] = f"{bin_dir}:/bin"
    return subprocess.run(["/bin/sh", str(SCRIPT)], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=30, start_new_session=True)


_FAKE_DOCKER = '#!/bin/sh\necho "$@" >> "$HOME/calls"\n'


def test_a_second_run_keeps_the_token_and_updates(tmp_path):
    bot = tmp_path / "bot"
    bot.mkdir()
    (bot / ".env").write_text("TELEGRAM_BOT_TOKEN=0:kept\nTIMEZONE=Europe/Berlin\n", "utf-8")
    done = _run(tmp_path, docker=_FAKE_DOCKER)
    assert done.returncode == 0, done.stderr
    assert (bot / ".env").read_text("utf-8") == "TELEGRAM_BOT_TOKEN=0:kept\nTIMEZONE=Europe/Berlin\n"
    assert "ghcr.io/morgunovtech/tofs_devops_bot:latest" in (bot / "docker-compose.yml").read_text("utf-8")
    calls = (tmp_path / "calls").read_text("utf-8").splitlines()
    assert calls[-2:] == ["compose pull", "compose up -d"]
    assert "0:kept" not in done.stdout + done.stderr


def test_the_owners_own_compose_file_is_left_alone(tmp_path):
    bot = tmp_path / "bot"
    bot.mkdir()
    (bot / ".env").write_text("TELEGRAM_BOT_TOKEN=0:kept\n", "utf-8")
    (bot / "docker-compose.yml").write_text("services: {mine: {image: x}}\n", "utf-8")
    assert _run(tmp_path, docker=_FAKE_DOCKER).returncode == 0
    assert (bot / "docker-compose.yml").read_text("utf-8") == "services: {mine: {image: x}}\n"


def test_without_docker_it_says_so_and_touches_nothing(tmp_path):
    if os.path.exists("/bin/docker"):
        pytest.skip("a real docker lives in /bin")
    done = _run(tmp_path, docker=None)
    assert done.returncode == 1
    assert "Docker is not installed" in done.stderr
    assert not (tmp_path / "bot").exists()


def test_without_a_terminal_it_asks_for_the_env_file_instead_of_hanging(tmp_path):
    done = _run(tmp_path, docker=_FAKE_DOCKER)
    assert done.returncode == 1
    assert "TELEGRAM_BOT_TOKEN=" in done.stderr
    assert not (tmp_path / "bot" / ".env").exists()
    assert not (tmp_path / "calls").exists() or "up -d" not in (tmp_path / "calls").read_text("utf-8")
