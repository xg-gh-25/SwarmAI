"""The build identity of a SwarmAI build: the commit it was built from and a digest of the
source it was built from, so a Host can tell whether a Hive runs its code when the version
string has not changed (VERSION moves only at a release).

- ``commit``: ``git rev-parse HEAD`` of the checkout, None outside one (a source tarball).
- ``digest``: sha256 over ``<path> <blob id>`` lines, sorted by path, of every file under
  SOURCE_DIRS (minus SKIPPED) and of VERSION. Inside a git work tree the files are the tracked
  ones that are still there, and each blob id is git's own (the index entry, or ``git
  hash-object`` of the worktree file when it is modified): line endings are normalised the way
  git stores them, so a Windows and a Linux builder of the same commit agree, and files a build
  writes (untracked) never change it. Outside one, every file under those directories, each
  blob id computed here the way git computes it.
- ``id``: the first 16 hex digits of sha256(``<digest>``): what is compared. The commit is
  kept for display, never compared: a commit that changes nothing under SOURCE_DIRS (a CI
  workflow, a doc) leaves the code a Hive runs the same, so a Host built before it and a
  package built after it must agree.

The Hive package (hive/release.sh), the desktop backend (desktop/scripts/build-backend.sh) and
a Host started from its checkout (core/build_identity) all call ``compute`` on the same
checkout, so they agree whenever they were built from the same source. Imports only the
standard library: the build scripts run it with the system python3.

CLI:
  build_identity.py write <root> <out.json> [--version V]   BUILD.json for a build of <root>
  build_identity.py sidecar <build.json> <package> <out>    the package's <package>.build.json:
                                                            BUILD.json plus its sha256
  build_identity.py id <build.json>                         print its id (empty when invalid)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

#: what the identity covers: the source a Hive package and a Host backend are built from
SOURCE_DIRS = ("backend", "hive", "desktop")
#: inside SOURCE_DIRS but not part of what a Hive or the Host's backend runs
SKIPPED = ("desktop/src-tauri",)
#: never source, whatever a tarball holds
_SKIP_PARTS = {"node_modules", "__pycache__", ".venv", ".git", "dist"}
ID_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_TIMEOUT_S = 60


def _git(root: Path, *args: str, stdin: Optional[str] = None) -> Optional[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True,
                             text=True, timeout=GIT_TIMEOUT_S, env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


def _covered(rel: str) -> bool:
    if rel == "VERSION":
        return True
    if not any(rel.startswith(d + "/") for d in SOURCE_DIRS):
        return False
    if any(rel == s or rel.startswith(s + "/") for s in SKIPPED):
        return False
    return not any(p in _SKIP_PARTS for p in rel.split("/"))


def _blob_id(data: bytes) -> str:
    """git's blob id of *data* (what ``git hash-object`` prints): an identifier the digest
    is built from, compared with git's own; the digest itself is sha256."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def _git_blobs(root: Path) -> Optional[dict[str, str]]:
    """{path: blob id} of the covered tracked files still in the worktree, or None when
    *root* is not the top of a git work tree."""
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is None or Path(top.strip()).resolve() != root.resolve():
        return None
    staged = _git(root, "ls-files", "-s", "-z")
    deleted = _git(root, "ls-files", "-d", "-z")
    modified = _git(root, "ls-files", "-m", "-z")
    if staged is None or deleted is None or modified is None:
        return None
    gone = set(filter(None, deleted.split("\0")))
    blobs: dict[str, str] = {}
    for entry in filter(None, staged.split("\0")):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) < 3 or parts[0] == "160000" or path in gone or not _covered(path):
            continue                                    # a submodule is not a file of ours
        blobs[path] = parts[1]
    changed = sorted(p for p in set(filter(None, modified.split("\0"))) - gone if p in blobs)
    if changed:
        got = _git(root, "hash-object", "--stdin-paths", stdin="\n".join(changed) + "\n")
        ids = (got or "").split()
        if len(ids) != len(changed):
            return None
        blobs.update(zip(changed, ids))
    return blobs


def _walk_blobs(root: Path) -> dict[str, str]:
    blobs: dict[str, str] = {}
    for top in (*SOURCE_DIRS, "VERSION"):
        base = root / top
        if base.is_file():
            blobs[top] = _blob_id(base.read_bytes())
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_PARTS)
            for name in filenames:
                p = Path(dirpath) / name
                rel = p.relative_to(root).as_posix()
                if _covered(rel) and p.is_file() and not p.is_symlink():
                    blobs[rel] = _blob_id(p.read_bytes())
    return blobs


def make_id(commit: Optional[str], digest: str) -> str:
    """The compared id: from the source digest alone (*commit* is display only, kept in the
    signature so every caller still names both)."""
    return hashlib.sha256(digest.encode()).hexdigest()[:16]


def compute(root: Path | str) -> dict[str, Any]:
    """``{commit, digest, id}`` of the source at *root* (a checkout's top, or an unpacked
    source tree)."""
    root = Path(root)
    blobs = _git_blobs(root)
    commit = None
    if blobs is None:
        blobs = _walk_blobs(root)
    else:
        head = (_git(root, "rev-parse", "--verify", "HEAD") or "").strip()
        commit = head if _HEX40.fullmatch(head) else None
    lines = "".join(f"{p} {b}\n" for p, b in sorted(blobs.items()))
    digest = hashlib.sha256(lines.encode()).hexdigest()
    return {"commit": commit, "digest": digest, "id": make_id(commit, digest)}


def valid(d: Any) -> Optional[dict[str, Any]]:
    """A build identity read from a file or a Hive's answer, checked (its id must be the one
    its digest makes), or None."""
    if not isinstance(d, dict):
        return None
    commit, digest, bid = d.get("commit"), d.get("digest"), d.get("id")
    if commit is not None and not (isinstance(commit, str) and _HEX40.fullmatch(commit)):
        return None
    if not (isinstance(digest, str) and _HEX64.fullmatch(digest)) or not isinstance(bid, str):
        return None
    if bid != make_id(commit, digest):
        return None
    out = {"commit": commit, "digest": digest, "id": bid}
    if isinstance(d.get("version"), str) and len(d["version"]) <= 64:
        out["version"] = d["version"]
    if isinstance(d.get("package_sha256"), str) and _HEX64.fullmatch(d["package_sha256"]):
        out["package_sha256"] = d["package_sha256"]
    return out


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(out: Path, d: dict[str, Any]) -> None:
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(d, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, out)


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "write":
        d = compute(argv[1])
        if len(argv) == 5 and argv[3] == "--version":
            d["version"] = argv[4]
        elif len(argv) != 3:
            print("usage: build_identity.py write <root> <out.json> [--version V]", file=sys.stderr)
            return 2
        _write_json(Path(argv[2]), d)
        print(d["id"])
        return 0
    if len(argv) == 4 and argv[0] == "sidecar":
        d = valid(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
        if d is None:
            print(f"build_identity: {argv[1]} is not a valid build identity", file=sys.stderr)
            return 1
        d["package_sha256"] = _sha256_file(Path(argv[2]))
        _write_json(Path(argv[3]), d)
        return 0
    if len(argv) == 2 and argv[0] == "id":
        try:
            d = valid(json.loads(Path(argv[1]).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            d = None
        print(d["id"] if d else "")
        return 0
    print(__doc__.split("CLI:", 1)[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
