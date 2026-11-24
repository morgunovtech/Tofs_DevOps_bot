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
"""

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

TIMEOUT_SEC = 30


def short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:8]


def load(manifest_path: Path) -> tuple[dict, Path]:
    manifest = json.loads(manifest_path.read_text("utf-8"))
    root = (manifest_path.parent / manifest.get("root", ".")).resolve()
    return manifest, root


def check(manifest_path: Path) -> int:
    manifest, root = load(manifest_path)
    problems = 0
    for rel, expected in manifest["files"].items():
        path = root / rel
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


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "ds-sync (+https://s.morgunov.tech/ds/v1/USAGE.md)"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as response:
        if response.status != 200:
            raise SystemExit(f"{url}: HTTP {response.status}")
        return response.read()


def write(manifest_path: Path) -> int:
    manifest, root = load(manifest_path)
    origin = manifest["origin"].rstrip("/")
    changes = []
    for rel, old in manifest["files"].items():
        data = fetch(f"{origin}/{rel}")
        new = short_hash(data)
        path = root / rel
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
    manifest_path = Path(argv[1])
    return check(manifest_path) if argv[2] == "--check" else write(manifest_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
