"""scripts/ds_sync.py trusts nothing in the manifest: --write puts bytes from
the network on disk, so the origin is an allow-listed https host (redirects
included), and the manifest, its root and every listed file stay inside the
directory they belong to."""

import importlib.util
import json
import pathlib
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("ds_sync", ROOT / "scripts" / "ds_sync.py")
ds_sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ds_sync)

CSS = b":root { --v4-ink: #111 }\n"


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A consumer repository in a temp directory: web/ds.json and its copy."""
    repo = tmp_path / "repo"
    (repo / "web" / "copy" / "ds" / "v1").mkdir(parents=True)
    (repo / "web" / "copy" / "ds" / "v1" / "tokens.css").write_bytes(CSS)
    monkeypatch.setattr(ds_sync, "REPO", repo.resolve())
    return repo


def _manifest(repo, **changes) -> str:
    manifest = {"origin": "https://s.morgunov.tech", "root": "copy",
                "files": {"ds/v1/tokens.css": ds_sync.short_hash(CSS)}, **changes}
    path = repo / "web" / "ds.json"
    path.write_text(json.dumps(manifest), "utf-8")
    return str(path)


def _run(*argv: str) -> int:
    return ds_sync.main(["ds_sync.py", *argv])


def test_the_real_manifest_passes_the_check():
    assert _run(str(ROOT / "web" / "ds.json"), "--check") == 0


def test_check_and_write_do_what_they_did(repo, monkeypatch, capsys):
    path = _manifest(repo)
    assert _run(path, "--check") == 0
    fetched = []
    new = CSS + b"/* v2 */\n"

    def fetch(host, rel):
        fetched.append((host, rel))
        return new
    monkeypatch.setattr(ds_sync, "fetch", fetch)
    assert _run(path, "--write") == 0
    assert fetched == [("s.morgunov.tech", "ds/v1/tokens.css")]
    assert (repo / "web" / "copy" / "ds" / "v1" / "tokens.css").read_bytes() == new
    assert json.loads((repo / "web" / "ds.json").read_text("utf-8"))["files"] == {
        "ds/v1/tokens.css": ds_sync.short_hash(new)}
    assert capsys.readouterr().out.splitlines()[-1].startswith("summary: ds/v1/tokens.css ")
    assert _run(path, "--write") == 0
    assert capsys.readouterr().out.splitlines()[-1] == "summary: no change"


def test_the_manifest_must_be_a_json_file_inside_the_repository(repo, tmp_path):
    outside = tmp_path / "ds.json"
    outside.write_text("{}", "utf-8")
    (repo / "web" / "link.json").symlink_to(outside)
    (repo / "notes.txt").write_text("{}", "utf-8")
    for arg in (str(outside), str(repo / "web" / ".." / ".." / "ds.json"), str(repo / "web" / "link.json"),
                str(repo / "notes.txt"), "/etc/passwd"):
        with pytest.raises(SystemExit, match="must be a .json file inside"):
            _run(arg, "--check")


@pytest.mark.parametrize("rel", ["../../../outside.css", "/etc/cron.d/x", "ds/../../x.css", "ds//x.css", "",
                                 "ds/./x.css", "..", "ds\\x.css", "ds/x.css?v=1", "ds/x.css#a", ".hidden/x.css"])
def test_a_listed_file_cannot_leave_the_root(repo, tmp_path, monkeypatch, rel):
    monkeypatch.setattr(ds_sync, "fetch", lambda *a: pytest.fail(f"fetched {a}"))
    path = _manifest(repo, files={"ds/v1/tokens.css": "00000000", rel: "00000000"})
    for mode in ("--check", "--write"):
        with pytest.raises(SystemExit, match="plain names"):
            _run(path, mode)
    assert (repo / "web" / "copy" / "ds" / "v1" / "tokens.css").read_bytes() == CSS     # nothing was written
    assert not (tmp_path / "outside.css").exists()


def test_a_symlink_inside_the_root_does_not_lead_out(repo, tmp_path, monkeypatch):
    (tmp_path / "elsewhere").mkdir()
    (repo / "web" / "copy" / "out").symlink_to(tmp_path / "elsewhere")
    monkeypatch.setattr(ds_sync, "fetch", lambda host, rel: CSS)
    with pytest.raises(SystemExit, match="leaves"):
        _run(_manifest(repo, files={"out/x.css": "00000000"}), "--write")
    assert not (tmp_path / "elsewhere" / "x.css").exists()


@pytest.mark.parametrize("root", ["..", "../..", "/tmp", "copy/../../.."])
def test_the_root_stays_inside_the_manifests_directory(repo, root):
    with pytest.raises(SystemExit, match="root .* leaves"):
        _run(_manifest(repo, root=root), "--check")


@pytest.mark.parametrize("origin", ["http://s.morgunov.tech", "https://example.com", "https://s.morgunov.tech:8443",
                                    "https://s.morgunov.tech@example.com", "https://s.morgunov.tech.example.com",
                                    "file:///etc", "https://169.254.169.254", "https://s.morgunov.tech/ds/..", ""])
def test_only_the_allowed_origin_is_fetched(repo, monkeypatch, origin):
    monkeypatch.setattr(ds_sync, "fetch", lambda *a: pytest.fail(f"fetched {a}"))
    with pytest.raises(SystemExit, match="origin"):
        _run(_manifest(repo, origin=origin), "--write")


def test_fetch_itself_refuses_other_addresses_and_redirects_to_them():
    for host in ("example.com", "169.254.169.254", "s.morgunov.tech@example.com", "s.morgunov.tech:8443", ""):
        with pytest.raises(SystemExit, match="only https://"):
            ds_sync.fetch(host, "ds/v1/tokens.css")
    for rel in ("../x.css", "ds/v1/tokens.css?x=1", "//example.com/x", "ds/../../x"):
        with pytest.raises(SystemExit, match="plain names"):
            ds_sync.fetch("s.morgunov.tech", rel)
    handler = ds_sync.SameOriginRedirects()
    request = urllib.request.Request("https://s.morgunov.tech/ds/v1/tokens.css")
    for target in ("http://169.254.169.254/latest/meta-data/", "https://example.com/tokens.css",
                   "http://s.morgunov.tech/ds/v1/tokens.css"):
        with pytest.raises(SystemExit, match="only https://"):
            handler.redirect_request(request, None, 302, "Found", {}, target)
    followed = handler.redirect_request(request, None, 302, "Found", {}, "https://s.morgunov.tech/ds/v1/t.css")
    assert followed.full_url == "https://s.morgunov.tech/ds/v1/t.css"
