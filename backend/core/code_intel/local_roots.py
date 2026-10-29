"""Local roots: the user's own checkouts a Brain may read into its code graph.

Ownership decides which directory gets parsed into a Brain (and, through the survey,
into its docs, which travel to a Hive). So it has one declaration a document cannot
make: this machine-local registry, ``<bindings root>/local_roots.json``. It lives
outside SwarmWS and outside every repo, and nothing syncs it.

- An agent may PROPOSE a root (``artifact_cli bind --local <path>`` → ``propose``):
  the entry is ``pending`` and owns nothing.
- Only a person CONFIRMS it, with Confirm on the Brain Hub card (``confirm``, called
  by a daemon endpoint). A confirmed root is owned (``confirmed``).
- A path is refused unless it is a git work-tree top, and it may not be ``$HOME``,
  SwarmWS, a parent of SwarmWS, or anything inside SwarmWS or the bindings root. It is
  stored as git's canonical top, and every read re-checks it (``confirmed``): a
  confirmed directory later replaced by a symlink is not followed.

A TECH.md path no longer grants ownership (it is written by cultivation and travels
with Brain sync). ``suggest_from_tech_md`` turns a project's legacy TECH.md path into a
pending entry, so the person confirms it once. The limit: an agent that edits this
file by hand is the same-user limit the product accepts elsewhere; the confirm step
makes the edit visible, not impossible (design 2026-10-29-code-intel-unification §0.3).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

REGISTRY_NAME = "local_roots.json"
PENDING, CONFIRMED = "pending", "confirmed"


class LocalRootError(ValueError):
    """A path that may not be a local root (the reason is the message)."""


def _bindings_root() -> Path:
    from core.ddd_bindings import _default_bindings_root
    return _default_bindings_root().resolve()


def registry_path() -> Path:
    return _bindings_root() / REGISTRY_NAME


def db_path(root: str | Path) -> Path:
    """Where a local root's graph lives: keyed by its real path, so two Brains naming
    the same checkout share one graph."""
    real = os.path.realpath(str(root))
    return _bindings_root() / "local" / f"{hashlib.sha1(real.encode()).hexdigest()[:16]}.code_intel.db"


def _refusal(p: Path) -> str | None:
    """Why *p* (a canonical, real path) may not be a local root, or None. No git call:
    ``confirmed`` re-checks every entry with it each time ownership is read."""
    from config import get_swarmws
    home = Path(os.path.realpath(Path.home()))
    ws = Path(os.path.realpath(get_swarmws()))
    bindings = Path(os.path.realpath(_bindings_root()))
    if p == home:
        return "the home directory cannot be a local root"
    if p == ws or ws.is_relative_to(p):
        return "SwarmWS or a directory containing it"
    if p.is_relative_to(ws):
        return "inside SwarmWS"
    if p == bindings or p.is_relative_to(bindings) or bindings.is_relative_to(p):
        return "the bindings directory or inside it"
    return None


def validate(path: str | Path) -> str:
    """The canonical real path of *path* if it may be a local root; raises
    LocalRootError. Canonical = what git reports as the work-tree top, so a path typed
    in another case on a case-insensitive disk, or through a symlink, is checked (and
    stored) as the directory it really is."""
    try:
        given = Path(path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise LocalRootError(f"{path}: no such directory") from None
    if not given.is_dir():
        raise LocalRootError(f"{given}: not a directory")
    try:
        top = subprocess.run(["git", "-C", str(given), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LocalRootError(f"{given}: git could not read it ({type(exc).__name__})") from None
    if top.returncode != 0 or not top.stdout.strip():
        raise LocalRootError(f"{given}: not a git work tree")
    p = Path(os.path.realpath(top.stdout.strip()))
    if not os.path.samefile(p, given):
        raise LocalRootError(f"{given}: not the top of a git work tree")
    why = _refusal(p)
    if why:
        raise LocalRootError(f"{p}: {why}")
    return str(p)


@contextlib.contextmanager
def _locked():
    """The registry under an exclusive lock (read-modify-write)."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "a+") as fh:
        try:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)
        except ImportError:  # not POSIX: no other writer to exclude in practice
            pass
        yield path


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"version": 1, "projects": {}}
    except (OSError, ValueError) as exc:
        # Unreadable: own nothing (fail closed) and say why; never overwrite it blind.
        raise LocalRootError(f"{path} cannot be read: {exc}") from None
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        raise LocalRootError(f"{path} is not a local-roots registry")
    return data


def _write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".local_roots.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entries(project: str) -> list[dict]:
    """The project's entries (``path``, ``state``, ``added_at``…); [] when unreadable."""
    try:
        data = _read(registry_path())
    except LocalRootError as exc:
        logger.warning("local roots: %s", exc)
        return []
    rows = data["projects"].get(project) or []
    if not isinstance(rows, list):
        logger.warning("local roots: entry for %s is not a list; ignored", project)
        return []
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("path"), str)]


def confirmed(project: str) -> list[str]:
    """The project's confirmed roots that are still what was confirmed: an absolute
    directory whose real path is itself (not since replaced by a symlink, not relative)
    and that no rule refuses. Checked on every read, since the directory can change
    after the person's Confirm; an entry that fails is skipped, never followed."""
    out = []
    for r in entries(project):
        if r.get("state") != CONFIRMED:
            continue
        path = r["path"]
        if not os.path.isabs(path) or os.path.realpath(path) != path or not os.path.isdir(path):
            if os.path.isabs(path) and os.path.lexists(path):
                logger.warning("local roots: %s of %s is no longer the directory confirmed; "
                               "not read", path, project)
            continue
        why = _refusal(Path(path))
        if why:
            logger.warning("local roots: %s of %s is refused now (%s); not read", path, project, why)
            continue
        out.append(path)
    return out


def propose(project: str, path: str | Path, *, source: str = "agent") -> dict | None:
    """Add *path* as a pending root of *project* (a confirmed one stays confirmed).
    A TECH.md suggestion the person removed is not proposed again (None)."""
    real = validate(path)
    with _locked() as reg:
        data = _read(reg)
        if source == "tech-md" and real in (data.get("dismissed") or {}).get(project, []):
            return None
        rows = data["projects"].setdefault(project, [])
        if not isinstance(rows, list):
            raise LocalRootError(f"the registry entry for {project} is not a list")
        for r in rows:
            if r.get("path") == real:
                return dict(r)
        entry = {"path": real, "state": PENDING, "added_at": _now(), "source": source}
        rows.append(entry)
        _write(reg, data)
        return dict(entry)


def confirm(project: str, path: str | Path) -> dict:
    """A person's Confirm: the pending entry for *path* becomes confirmed. Re-checks
    the path (it may have changed since it was proposed); raises LocalRootError when
    there is no such entry or the path is no longer allowed."""
    real = validate(path)
    with _locked() as reg:
        data = _read(reg)
        for r in data["projects"].get(project) or []:
            if r.get("path") == real:
                if r.get("state") != CONFIRMED:
                    r["state"] = CONFIRMED
                    r["confirmed_at"] = _now()
                    _write(reg, data)
                return dict(r)
    raise LocalRootError(f"{real} was not proposed for {project}")


def remove(project: str, path: str | Path) -> bool:
    """Drop *path* from *project*'s roots (its graph file stays until deleted)."""
    real = os.path.realpath(str(path))
    with _locked() as reg:
        data = _read(reg)
        rows = data["projects"].get(project) or []
        if not isinstance(rows, list):
            raise LocalRootError(f"the registry entry for {project} is not a list")
        kept = [r for r in rows if not isinstance(r, dict) or r.get("path") != real]
        if len(kept) == len(rows):
            return False
        data["projects"][project] = kept
        # remembered, so the TECH.md suggestion does not bring it back
        dismissed = data.setdefault("dismissed", {}).setdefault(project, [])
        if real not in dismissed:
            dismissed.append(real)
        _write(reg, data)
        return True


def suggest_from_tech_md(project_dir: str | Path) -> dict | None:
    """The legacy TECH.md path of *project_dir* as a pending entry (once), or None."""
    from . import _tech_md_repo_root
    candidate = _tech_md_repo_root(project_dir)
    if not candidate:
        return None
    try:
        return propose(Path(project_dir).name, candidate, source="tech-md")
    except LocalRootError as exc:
        logger.info("local roots: TECH.md path of %s not suggested: %s",
                    Path(project_dir).name, exc)
        return None
