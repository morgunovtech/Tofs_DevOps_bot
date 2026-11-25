#!/usr/bin/env python3
"""ds_sync.py — keeps a vendored copy of the morgunov.tech design system in step
with its origin. The same script lives in every consumer that holds a copy.

    python3 scripts/ds_sync.py <manifest> --check   offline: each listed file hashes as the manifest says
    python3 scripts/ds_sync.py <manifest> --write   online: fetch each file from the origin, update the manifest

The manifest (ds.json) names the origin, the directory of the copy (relative
to the manifest) and the files — paths relative both to that directory and to
the origin — with the hash of each: the first 8 hex characters of sha1(file),
the value the origin uses as its ?v= cache buster (about-me-page,
scripts/version-stamp.py), so an entry reads «synced to tokens.css fa7fc139».

    {"origin": "https://s.morgunov.tech", "root": "s.morgunov.tech",
     "files": {"ds/v1/tokens.css": "fa7fc139", "ds/v1/fonts.css": "64eea157"}}

--check fails on a file edited by hand or a half-done sync: the copy must be
the origin's bytes, so a diff against the canon is a plain diff. --write
prints one line per file and, last, «summary: …» — the workflow in
.github/workflows/ds-sync.yml runs it weekly and opens a PR when anything
changed. Standard library only; the origin is reached through urllib (the
https_proxy environment variable is honoured).

The manifest is data, and --write puts bytes from the network on disk, so
nothing in it is trusted: the origin must be https:// on a host from
ALLOWED_ORIGINS (a redirect to any other host is refused), the root must stay
inside the manifest's directory, and every file path — plain segments of
letters, digits, «.», «_» and «-» — must stay inside the root. The manifest
itself must be a .json file inside the repository this script lives in.
"""

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

TIMEOUT_SEC = 30
# The repository this copy of the script lives in (scripts/ds_sync.py): the
# manifest it is given must be inside.
REPO = Path(__file__).resolve().parent.parent
# Where a copy may come from. A consumer of another design system adds its host here.
ALLOWED_ORIGINS = frozenset({"s.morgunov.tech"})
# One path segment of a listed file: no «..», no separators, nothing a URL or
# a file system reads as more than a name.
SEGMENT = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*")


def short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


def manifest_file(arg: str) -> Path:
    """The manifest named on the command line: a .json file inside this
    repository, symlinks resolved."""
    path = Path(arg).resolve()
    if path.suffix != ".json" or not path.is_relative_to(REPO):
        raise SystemExit(f"{arg}: the manifest must be a .json file inside {REPO}")
    if not path.is_file():
        raise SystemExit(f"{arg}: no such file")
    return path


def inside(base: Path, rel: str, what: str) -> Path:
    """base/rel with symlinks resolved — or a stop when it leaves base."""
    path = (base / rel).resolve()
    if not path.is_relative_to(base):
        raise SystemExit(f"{what} {rel!r} leaves {base}")
    return path


def file_path(root: Path, rel: str) -> Path:
    if not isinstance(rel, str) or not all(SEGMENT.fullmatch(part) for part in rel.split("/")):
        raise SystemExit(f"file {rel!r}: a relative path of plain names is expected (ds/v1/tokens.css)")
    return inside(root, rel, "file")


def origin_host(manifest: dict) -> str:
    """The manifest's origin must be https://<allowed host> and nothing else
    (no port, login, path or query). What is returned is the allow-list's own
    entry, not the manifest's text: the address fetched is built from
    constants."""
    origin = str(manifest.get("origin", "")).rstrip("/")
    parts = urlsplit(origin)
    if parts.scheme == "https" and not (parts.path or parts.query or parts.fragment):
        for host in ALLOWED_ORIGINS:
            if host == parts.netloc:
                return host
    raise SystemExit(f"origin {origin!r}: expected https:// on one of {sorted(ALLOWED_ORIGINS)}")


def load(manifest_path: Path) -> tuple[dict, Path]:
    manifest = json.loads(manifest_path.read_text("utf-8"))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise SystemExit(f"{manifest_path.name}: expected an object with «files»")
    root = inside(manifest_path.parent, str(manifest.get("root", ".")), "root")
    return manifest, root


def check(manifest_path: Path) -> int:
    manifest, root = load(manifest_path)
    problems = 0
    for rel, expected in manifest["files"].items():
        path = file_path(root, rel)
        if not path.is_file():
            print(f"{rel}: missing (manifest {expected})")
            problems += 1
            continue
        actual = short_hash(path.read_bytes())
        if actual != expected:
            print(f"{rel}: manifest {expected}, file {actual} — edited by hand or a half-done sync; run --write")
            problems += 1
        else:
            print(f"{rel}: {expected} (ok)")
    return 1 if problems else 0


def allowed_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in ALLOWED_ORIGINS:
        raise SystemExit(f"{url}: only https:// on {sorted(ALLOWED_ORIGINS)} is fetched")
    return url


class SameOriginRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is followed only to an allowed https origin: the allow-list
    must hold for the address the bytes really come from."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, allowed_url(newurl))


def fetch(host: str, rel: str) -> bytes:
    """https://<host>/<rel>: the host from the allow-list, the path made of
    checked plain names, each percent-encoded."""
    if host not in ALLOWED_ORIGINS:
        raise SystemExit(f"{host}: only https:// on {sorted(ALLOWED_ORIGINS)} is fetched")
    names = rel.split("/")
    if not all(SEGMENT.fullmatch(name) for name in names):
        raise SystemExit(f"file {rel!r}: a relative path of plain names is expected (ds/v1/tokens.css)")
    url = f"https://{host}/" + "/".join(quote(name, safe="") for name in names)
    request = urllib.request.Request(url, headers={"User-Agent": "ds-sync (+https://s.morgunov.tech/ds/v1/USAGE.md)"})
    with urllib.request.build_opener(SameOriginRedirects).open(request, timeout=TIMEOUT_SEC) as response:
        if response.status != 200:
            raise SystemExit(f"{url}: HTTP {response.status}")
        return response.read()


def write(manifest_path: Path) -> int:
    manifest, root = load(manifest_path)
    host = origin_host(manifest)
    # Every path is checked before the first request: a bad entry writes nothing.
    paths = {rel: file_path(root, rel) for rel in manifest["files"]}
    changes = []
    for rel, old in manifest["files"].items():
        data = fetch(host, rel)
        new = short_hash(data)
        path = paths[rel]
        if path.is_file() and path.read_bytes() == data and old == new:
            print(f"{rel}: {new} (unchanged)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        manifest["files"][rel] = new
        print(f"{rel}: {old} → {new}")
        changes.append(f"{rel} {old}→{new}")
    if changes:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print("summary: " + (", ".join(changes) if changes else "no change"))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in ("--check", "--write"):
        print(__doc__.strip().splitlines()[0])
        print("usage: ds_sync.py <manifest.json> --check | --write")
        return 2
    manifest_path = manifest_file(argv[1])
    return check(manifest_path) if argv[2] == "--check" else write(manifest_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
