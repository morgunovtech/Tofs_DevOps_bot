"""entrypoint.sh decisions with id/stat/setpriv/find/mkdir stubbed: which
directory gets a new owner and which groups the bot process receives."""

import socket
import subprocess
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parent.parent / "entrypoint.sh"
STUBS = {
    "id": 'echo 0',
    "stat": '[ "$FAKE_GID" = fail ] && exit 1; echo "$FAKE_GID"',
    # The second line shows what the bot process would inherit (set by `export`).
    "setpriv": 'echo "setpriv $*"; echo "DOCKER_SOCKET_ROOT_GROUP=${DOCKER_SOCKET_ROOT_GROUP-unset}"',
    "find": 'echo "find $*"',
    "mkdir": 'echo "mkdir $*"',
    "chown": 'echo "chown $*"',
}


def _run(tmp_path: Path, **env: str) -> str:
    bindir = tmp_path / "bin"
    if not bindir.exists():
        bindir.mkdir()
        for name, body in STUBS.items():
            (bindir / name).write_text(f"#!/bin/sh\n{body}\n")
            (bindir / name).chmod(0o755)
    done = subprocess.run(["/bin/sh", str(ENTRYPOINT), "python", "main.py"], cwd=tmp_path,
                          env={"PATH": f"{bindir}:/usr/bin:/bin", **env},
                          capture_output=True, text=True, timeout=10)
    return done.stdout + done.stderr


def test_only_the_data_directory_changes_owner(tmp_path):
    out = _run(tmp_path, DOCKER_SOCKET="none.sock")
    assert "find /app/data -xdev" in out and "--init-groups python main.py" in out
    assert "chown -R" not in out
    assert "find /app/data -xdev" in _run(tmp_path, DB_PATH="data/bot.db", DOCKER_SOCKET="none.sock")
    assert "find /mnt/vol -xdev" in _run(tmp_path, DB_PATH="/mnt/vol/bot.db", RAILWAY_VOLUME_MOUNT_PATH="/mnt/vol/",
                                         DOCKER_SOCKET="none.sock")
    for db_path in ("/bot.db", "/run/bot.db", "/app/data/../../etc/bot.db", "bot.db"):
        out = _run(tmp_path, DB_PATH=db_path, DOCKER_SOCKET="none.sock")
        assert "find" not in out and "keeps its owner" in out, db_path
        assert "--init-groups python main.py" in out, db_path          # the bot still starts


def test_socket_group_but_never_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)              # a relative socket path: AF_UNIX paths are short
    with socket.socket(socket.AF_UNIX) as s:
        s.bind("docker.sock")
        out = _run(tmp_path, DOCKER_SOCKET="docker.sock", FAKE_GID="999")
        assert "--groups 999 python main.py" in out and "DOCKER_SOCKET_ROOT_GROUP=unset" in out
        out = _run(tmp_path, DOCKER_SOCKET="docker.sock", FAKE_GID="0")
        assert "--init-groups python main.py" in out and "group root" in out
        assert "DOCKER_SOCKET_ROOT_GROUP=1" in out                      # the bot is told why
        out = _run(tmp_path, DOCKER_SOCKET="docker.sock", FAKE_GID="0", ALLOW_ROOT_GROUP_SOCKET="1")
        assert "--groups 0 python main.py" in out and "DOCKER_SOCKET_ROOT_GROUP=unset" in out
        out = _run(tmp_path, DOCKER_SOCKET="docker.sock", FAKE_GID="fail")
        assert "--init-groups python main.py" in out and "cannot read the group" in out
        assert "--groups ''" not in out and "--groups  " not in out
        # An empty DOCKER_SOCKET is the default path (config.py does the same): the bot starts.
        assert "python main.py" in _run(tmp_path, DOCKER_SOCKET="", FAKE_GID="999")
