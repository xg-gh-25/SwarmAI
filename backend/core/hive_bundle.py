"""A Hive's commits as a git bundle the host can take back (design
docs/design/2026-10-28-project-agent-flow.md §7 ⑨, Hive side).

At a turn's end, for each declared repository whose facts (core/task_repos) show
commits ``base..head``, ``make`` writes ``git bundle create <file> <base>..HEAD`` to
``Hive/bundles/<session>/<key>.bundle`` and adds ``{key, sha256, size}`` to the fact
(``bundle``). ``key`` = the first 16 hex of sha256 of the repository's declared ``dir``:
the host recomputes it from the dir it sent and never takes a path from the Hive. The
Hive never pushes; it does not need a write credential. A failed or too large bundle
carries ``{"error": ...}`` instead (fixed texts).

``open_bundle(workspace, session, key)`` opens one for the download endpoint
(routers/hive_brains), never through a link.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
import stat
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BUNDLES_ROOT = ("Hive", "bundles")
MAX_BUNDLE_BYTES = 100 * 1024 * 1024
GIT_TIMEOUT_S = 40
SESSION_RE = re.compile(r"[A-Za-z0-9_-]{8,80}")
KEY_RE = re.compile(r"[0-9a-f]{16}")
_SHA_RE = re.compile(r"[0-9a-f]{40,64}")
ERR_TOO_LARGE = "the bundle is larger than a Hive sends"
ERR_FAILED = "the bundle could not be made"
ERR_MOVED = "HEAD moved while the bundle was made"


def key(repo_dir: str) -> str:
    return hashlib.sha256(repo_dir.encode("utf-8")).hexdigest()[:16]


def _dir(workspace: Path, session_id: str) -> Path:
    return Path(workspace).joinpath(*BUNDLES_ROOT, session_id)


def _git(cwd: str, *args: str) -> Optional[subprocess.CompletedProcess]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    try:
        return subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
                              cwd=cwd, env=env, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def make(workspace: Path, session_id: str, fact: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The bundle of one repository fact, or None when there is nothing to bundle."""
    base, head, rdir = fact.get("base"), fact.get("head"), fact.get("dir")
    if (fact.get("error") or not fact.get("commits_total") or not isinstance(base, str)
            or not isinstance(head, str) or not _SHA_RE.fullmatch(base) or not _SHA_RE.fullmatch(head)
            or not isinstance(rdir, str) or not SESSION_RE.fullmatch(session_id or "")):
        return None
    ws = Path(workspace).resolve()
    repo = (ws / rdir).resolve()
    if not repo.is_relative_to(ws.joinpath("Hive", "repos")):
        return None
    out = _dir(ws, session_id)
    k = key(rdir)
    du = _git(str(repo), "rev-list", "--objects", "--disk-usage", f"{base}..HEAD")
    if du is not None and du.returncode == 0 and du.stdout.strip().isdigit() \
            and int(du.stdout.strip()) > MAX_BUNDLE_BYTES:
        return {"key": k, "error": ERR_TOO_LARGE}                # never written to disk
    try:
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / f".{k}.{secrets.token_hex(6)}.tmp"
        p = _git(str(repo), "bundle", "create", "-q", str(tmp), f"{base}..HEAD")
        now = _git(str(repo), "rev-parse", "--verify", "--quiet", "HEAD")
        if p is None or p.returncode != 0 or not tmp.is_file():
            tmp.unlink(missing_ok=True)
            return {"key": k, "error": ERR_FAILED}
        if now is None or now.stdout.decode().strip() != head:
            tmp.unlink(missing_ok=True)
            return {"key": k, "error": ERR_MOVED}
        size = tmp.stat().st_size
        if size > MAX_BUNDLE_BYTES:
            tmp.unlink(missing_ok=True)
            return {"key": k, "error": ERR_TOO_LARGE}
        h = hashlib.sha256()
        with open(tmp, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        os.replace(tmp, out / f"{k}.bundle")
    except OSError as exc:
        logger.warning("hive_bundle: %s of %s failed (%s)", k, session_id, type(exc).__name__)
        return {"key": k, "error": ERR_FAILED}
    return {"key": k, "sha256": h.hexdigest(), "size": size}


def on_hive() -> bool:
    return os.environ.get("SWARMAI_MODE") == "hive"


def add_bundles(workspace: Path, session_id: Optional[str], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``facts`` with each repository's ``bundle`` (only on a Hive)."""
    if not on_hive() or not session_id:
        return facts
    for f in facts:
        b = make(workspace, session_id, f) if isinstance(f, dict) else None
        if b is not None:
            f["bundle"] = b
    return facts


def open_bundle(workspace: Path, session_id: str, k: str) -> tuple[int, int]:
    """(fd, size) of a bundle, opened without following a link; raises FileNotFoundError."""
    if not SESSION_RE.fullmatch(session_id or "") or not KEY_RE.fullmatch(k or ""):
        raise FileNotFoundError(k)
    path = _dir(Path(workspace), session_id) / f"{k}.bundle"
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BUNDLE_BYTES:
        os.close(fd)
        raise FileNotFoundError(k)
    return fd, st.st_size
