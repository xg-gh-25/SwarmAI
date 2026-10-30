"""A Hive's commits as a git bundle the host can take back (design
docs/design/2026-10-28-project-agent-flow.md §7 ⑨, Hive side).

At a turn's end, for each declared repository whose facts (core/task_repos) show
commits ``base..head`` (a writable one: a work order's read-only worktree is never
bundled, core/hive_workspace), ``make`` writes ``git bundle create <tmp> <base>..HEAD`` to a
temporary file of the session's run record (core/hive_records); ``seal_bundles``, run by
the turn's coroutine once the bundling thread returned, seals each one as a ``bundle``
entry and puts ``{key, sha256, size, seq}`` in the fact (``bundle``); a turn that made no
new commit reuses the bundle sealed for the same ``base..head`` (``hive_records.sealed_bundle``),
so a long session keeps one copy per change of its branch. A bundle whose
thread returns after the turn gave up on it is never sealed (its temporary file is swept).
``key`` = the first 16 hex of sha256 of the repository's declared ``dir``: the host
recomputes it from the dir it sent and never takes a path from the Hive. The Hive never
pushes; it does not need a write credential. A failed or too large bundle carries
``{"error": ...}`` instead (fixed texts).

``open_bundle(workspace, session, key)`` opens the newest sealed bundle of a key (or one
an older version left in ``Hive/bundles/``) for the download endpoint an older host
uses (routers/hive_brains), never through a link.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
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
    # the daemon's git in the agent's repository: no daemon secret, nothing it configures
    # runs (core/hive_workspace)
    from core.hive_workspace import GIT_SAFE_OPTS, git_env
    env = {**git_env(), "GIT_OPTIONAL_LOCKS": "0"}
    try:
        return subprocess.run(["git", *GIT_SAFE_OPTS, *args],
                              cwd=cwd, env=env, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def make(workspace: Path, session_id: str, fact: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The bundle of one repository fact, or None when there is nothing to bundle."""
    base, head, rdir = fact.get("base"), fact.get("head"), fact.get("dir")
    if (fact.get("error") or not fact.get("commits_total") or not isinstance(base, str)
            or not isinstance(head, str) or not _SHA_RE.fullmatch(base) or not _SHA_RE.fullmatch(head)
            or not isinstance(rdir, str) or not SESSION_RE.fullmatch(session_id or "")
            or fact.get("access") == "read"):
        return None                  # a read-only worktree is never bundled (core/hive_workspace)
    ws = Path(workspace).resolve()
    repo = (ws / rdir).resolve()
    if not (repo.is_relative_to(ws.joinpath("Hive", "repos")) or repo.is_relative_to(ws.joinpath("Hive", "work"))):
        return None
    from core import hive_records
    k = key(rdir)
    try:
        again = hive_records.sealed_bundle(ws, session_id, k, base, head)
    except (OSError, ValueError):
        again = None
    if again is not None:
        return again                                   # no new commit: the sealed one stands
    du = _git(str(repo), "rev-list", "--objects", "--disk-usage", f"{base}..HEAD")
    if du is not None and du.returncode == 0 and du.stdout.strip().isdigit() \
            and int(du.stdout.strip()) > MAX_BUNDLE_BYTES:
        return {"key": k, "error": ERR_TOO_LARGE}                # never written to disk
    try:
        tmp = hive_records.tmp_path(ws, session_id, k)
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
    except (OSError, ValueError) as exc:
        logger.warning("hive_bundle: %s of %s failed (%s)", k, session_id, type(exc).__name__)
        return {"key": k, "error": ERR_FAILED}
    return {"key": k, "tmp": str(tmp)}


def seal_bundles(workspace: Path, session_id: Optional[str], facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seal each fact's bundle (``make``'s temporary file) into the run record."""
    from core import hive_records
    for f in facts:
        b = f.get("bundle") if isinstance(f, dict) else None
        if not isinstance(b, dict) or not b.get("tmp"):
            continue
        try:
            e = hive_records.append(workspace, str(session_id), "bundle", src=Path(b["tmp"]), key=b["key"])
            f["bundle"] = {"key": b["key"], "sha256": e["sha256"], "size": e["size"], "seq": e["seq"]}
        except (OSError, ValueError) as exc:
            logger.warning("hive_bundle: sealing %s of %s failed (%s)", b.get("key"), session_id,
                           type(exc).__name__)
            Path(b["tmp"]).unlink(missing_ok=True)
            f["bundle"] = {"key": b["key"], "error": ERR_TOO_LARGE if "larger" in str(exc) else ERR_FAILED}
            continue
        try:
            if isinstance(f.get("base"), str) and isinstance(f.get("head"), str):
                hive_records.note_bundle(workspace, str(session_id), b["key"], f["base"], f["head"],
                                         f["bundle"]["seq"])
        except (OSError, ValueError) as exc:   # sealed: only the next turn may seal a copy
            logger.warning("hive_bundle: noting %s of %s failed (%s)", b.get("key"), session_id,
                           type(exc).__name__)
    return facts


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
    from core import hive_records
    path = hive_records.latest_bundle(Path(workspace), session_id, k) \
        or _dir(Path(workspace), session_id) / f"{k}.bundle"
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BUNDLE_BYTES:
        os.close(fd)
        raise FileNotFoundError(k)
    return fd, st.st_size
