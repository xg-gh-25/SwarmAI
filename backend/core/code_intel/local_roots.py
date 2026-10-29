"""Local roots: the user's own checkouts a Brain reads into its code graph.

Ownership decides which directory gets parsed into a Brain (and, through the survey,
into its docs, which travel to a Hive). Its one record is this machine-local
registry, ``<bindings root>/local_roots.json``: outside SwarmWS and every repo, and
nothing syncs it.

- A root is owned when it is DECLARED on this machine: ``artifact_cli bind --local
  <path>`` (an agent, or the New Brain flow through it) → ``declare``. There is no
  second step by a person (design 2026-10-29-code-intel-auto D1): a Confirm on the
  desktop was never a barrier to an agent (the daemon takes any loopback request
  without a token, and the registry is a user-owned file), so what stays is making
  the act visible — the Brain Hub card lists every root with its source and a
  Remove, and a new root is a Need You item for a day (``attention_authority``).
- A path is refused unless it is a git work-tree top, and it may not be ``$HOME`` or
  a parent of it, SwarmWS or a parent of it, anything inside SwarmWS, the app data
  directory or the bindings root, or a credential location (``~/.ssh``, ``~/.aws``…).
  It is stored as git's canonical top, and every read re-checks it (``confirmed``): a
  directory later replaced by a symlink is not followed.
- One Brain per root: a path another Brain owns is refused with its name, so a run
  in that checkout always finds that Brain's graph.
- A document never adds a root: a TECH.md path (written by cultivation, carried by
  Brain sync) counts only once, in the install-time migration (``migrate_once``),
  which also promotes the r3 ``pending`` entries; after its marker, nothing does.
- Only the desktop daemon (``SWARMAI_MODE`` unset or ``daemon``) reads or writes
  local roots: in any other mode (a Hive) none is owned and every write raises.
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
# CONFIRMED: owned. PENDING was r3's "declared, waiting for a person"; the migration
# turns each into CONFIRMED and nothing writes it any more.
PENDING, CONFIRMED = "pending", "confirmed"
MIGRATION_KEY = "r4_owned"
_LOCAL_MODES = frozenset({"", "daemon"})
# Credential locations under $HOME: never a root, nor anything inside one.
_CREDENTIAL_DIRS = (".ssh", ".aws", ".gnupg", ".password-store", ".config", "Library")


class LocalRootError(ValueError):
    """A path that may not be a local root (the reason is the message)."""


def local_mode() -> bool:
    """Local roots exist only on the desktop daemon (or a dev run): fail closed in
    any other mode, a Hive or one not named here."""
    return os.environ.get("SWARMAI_MODE", "") in _LOCAL_MODES


def _require_local() -> None:
    if not local_mode():
        raise LocalRootError("local repositories are declared on the desktop, not here")


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
    from config import get_app_data_dir, get_swarmws
    home = Path(os.path.realpath(Path.home()))
    ws = Path(os.path.realpath(get_swarmws()))
    bindings = Path(os.path.realpath(_bindings_root()))
    app_data = Path(os.path.realpath(get_app_data_dir()))
    if p == home or home.is_relative_to(p):
        return "the home directory or a directory containing it"
    if p == ws or ws.is_relative_to(p):
        return "SwarmWS or a directory containing it"
    if p.is_relative_to(ws):
        return "inside SwarmWS"
    if p == bindings or p.is_relative_to(bindings) or bindings.is_relative_to(p):
        return "the bindings directory or inside it"
    if p.is_relative_to(app_data) or app_data.is_relative_to(p):
        return "the app data directory or inside it"
    for name in _CREDENTIAL_DIRS:
        cred = Path(os.path.realpath(home / name))
        if p.is_relative_to(cred):
            return f"a credential location (~/{name}) or inside it"
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
        top = subprocess.run(["git", *GIT_SAFE, "-C", str(given), "rev-parse",
                              "--show-toplevel"], capture_output=True, text=True, timeout=10)
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


# Git on an owned root runs with no hooks, no fsmonitor, explicit bare repos and no
# signature program: the hourly job and every review read the root outside any
# session. ``repo_read.GIT_HARDENING`` minus its empty ``diff.external`` (that makes
# git try to run "" for a patch); a patch diff passes ``--no-ext-diff`` instead.
GIT_SAFE: tuple[str, ...] = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.fsmonitor=",
    "-c", "safe.bareRepository=explicit",
    "-c", "log.showSignature=false",
)

_STARTER_CHILDREN_CAP = 20     # work-tree children listed for a folder of repos


def _work_tree_top(d: Path) -> Path | None:
    """The real path of the git work-tree top *d* is in, or None (hardened git)."""
    try:
        out = subprocess.run(["git", *GIT_SAFE, "-C", str(d), "rev-parse",
                              "--show-toplevel"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    return Path(os.path.realpath(out.stdout.strip()))


def classify_starter(path: str | Path) -> dict:
    """The role a New Brain starter path takes, read-only (design
    2026-10-29-code-intel-auto D2). ``{"path", "role", "why", "roots"}``:

    - a single file → DISTILL;
    - the top of a git work tree, or a folder inside one → GOVERN, ``roots`` = [the
      top] (``bind --local`` reads it in place);
    - a folder that is not in a work tree but has work-tree children (one level) →
      GOVERN, ``roots`` = those children (at most ``_STARTER_CHILDREN_CAP``);
    - otherwise → SHELF.
    A top that may not be a local root (``$HOME`` as a dotfiles repo, SwarmWS, a
    credential location…) is never offered. Raises LocalRootError when the path does
    not exist."""
    given = Path(path).expanduser()
    if given.is_file():
        return {"path": str(given), "role": "DISTILL", "why": "a single file", "roots": []}
    if not given.is_dir():
        raise LocalRootError(f"{path}: no such file or folder")
    real = Path(os.path.realpath(given))
    top = _work_tree_top(real)
    if top is not None and _refusal(top) is None:
        why = ("the top of a git work tree" if top == real else
               f"inside the git work tree {top}; that repository is read")
        return {"path": str(real), "role": "GOVERN", "why": why, "roots": [str(top)]}
    roots: list[str] = []
    try:
        children = sorted(c for c in real.iterdir() if c.is_dir() and (c / ".git").exists())
    except OSError:
        children = []
    for child in children:
        t = _work_tree_top(child)
        if t is not None and os.path.realpath(child) == str(t) and _refusal(t) is None:
            roots.append(str(t))
        if len(roots) >= _STARTER_CHILDREN_CAP:
            break
    if roots:
        return {"path": str(real), "role": "GOVERN",
                "why": f"a folder of {len(roots)} git repositories", "roots": roots}
    return {"path": str(real), "role": "SHELF", "why": "a folder that is not a git repository",
            "roots": []}


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
    """The project's entries (``path``, ``state``, ``added_at``, ``source``); [] when
    unreadable, and in any mode but the desktop's."""
    if not local_mode():
        return []
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
    """The project's owned roots that are still what was declared: an absolute
    directory whose real path is itself (not since replaced by a symlink, not relative)
    and that no rule refuses. Checked on every read, since the directory can change
    after it was declared; an entry that fails is skipped, never followed."""
    out = []
    for r in entries(project):
        if r.get("state") != CONFIRMED:
            continue
        path = r["path"]
        if not os.path.isabs(path) or os.path.realpath(path) != path or not os.path.isdir(path):
            if os.path.isabs(path) and os.path.lexists(path):
                logger.warning("local roots: %s of %s is no longer the directory declared; "
                               "not read", path, project)
            continue
        why = _refusal(Path(path))
        if why:
            logger.warning("local roots: %s of %s is refused now (%s); not read", path, project, why)
            continue
        out.append(path)
    return out


def _owner(data: dict, real: str, *, besides: str) -> str | None:
    """The project other than *besides* that owns *real*, or None."""
    for name, rows in data["projects"].items():
        if name == besides or not isinstance(rows, list):
            continue
        if any(isinstance(r, dict) and r.get("path") == real and r.get("state") == CONFIRMED
               for r in rows):
            return name
    return None


def declare(project: str, path: str | Path, *, source: str = "agent") -> tuple[dict, bool]:
    """*path* becomes an owned root of *project* now: ``(entry, added)``, *added*
    False when it already was. A path removed before is declared again (an explicit
    act on this machine). Raises LocalRootError for a refused path, one another
    Brain owns, or outside the desktop."""
    _require_local()
    real = validate(path)
    with _locked() as reg:
        data = _read(reg)
        owner = _owner(data, real, besides=project)
        if owner:
            raise LocalRootError(f"{real} is already read by the Brain {owner}; "
                                 "remove it there first")
        rows = data["projects"].setdefault(project, [])
        if not isinstance(rows, list):
            raise LocalRootError(f"the registry entry for {project} is not a list")
        for r in rows:
            if isinstance(r, dict) and r.get("path") == real:
                if r.get("state") == CONFIRMED:
                    return dict(r), False
                r.update(state=CONFIRMED, confirmed_at=_now())
                _write(reg, data)
                return dict(r), True
        entry = {"path": real, "state": CONFIRMED, "added_at": _now(), "source": source}
        rows.append(entry)
        dismissed = (data.get("dismissed") or {}).get(project)
        if isinstance(dismissed, list) and real in dismissed:
            dismissed.remove(real)
        _write(reg, data)
        return dict(entry), True


def remove(project: str, path: str | Path) -> bool:
    """Drop *path* from *project*'s roots (its graph file stays until deleted). The
    removal is remembered, so the install-time migration never adds it back."""
    _require_local()
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
        # remembered, so the migration does not bring it back
        dismissed = data.setdefault("dismissed", {}).setdefault(project, [])
        if real not in dismissed:
            dismissed.append(real)
        _write(reg, data)
        return True


def migrate_once(projects_dir: str | Path) -> dict:
    """The one-time step of an install moving to r4 (D1), run by the code-intel job:
    every r3 ``pending`` entry becomes owned (declared then, and r4 has no Confirm to
    promote it), and a Brain with no entry whose TECH.md names a git work-tree top —
    not removed before, not another Brain's — gets that root. Then a marker is set and
    this does nothing again: a later TECH.md line, or a Brain created later, never
    adds a root. Returns ``{"ran": bool, "promoted": [...], "added": [...]}``."""
    from . import _tech_md_repo_root
    if not local_mode():
        return {"ran": False, "promoted": [], "added": []}
    with _locked() as reg:
        data = _read(reg)
        if (data.get("migrations") or {}).get(MIGRATION_KEY):
            return {"ran": False, "promoted": [], "added": []}
        promoted, added = [], []
        for name, rows in data["projects"].items():
            if not isinstance(rows, list):
                continue
            for r in rows:
                if isinstance(r, dict) and r.get("state") == PENDING:
                    r.update(state=CONFIRMED, confirmed_at=_now())
                    promoted.append({"project": name, "path": r.get("path")})
        projects = Path(projects_dir)
        dirs = sorted(d for d in projects.iterdir() if d.is_dir()) if projects.is_dir() else []
        for d in dirs:
            if data["projects"].get(d.name):
                continue
            candidate = _tech_md_repo_root(d)
            if not candidate:
                continue
            try:
                real = validate(candidate)
            except LocalRootError as exc:
                logger.info("local roots: TECH.md path of %s not migrated: %s", d.name, exc)
                continue
            if real in (data.get("dismissed") or {}).get(d.name, []) or \
                    _owner(data, real, besides=d.name):
                continue
            data["projects"][d.name] = [{"path": real, "state": CONFIRMED,
                                         "added_at": _now(), "source": "migrated"}]
            added.append({"project": d.name, "path": real})
        data.setdefault("migrations", {})[MIGRATION_KEY] = _now()
        _write(reg, data)
    return {"ran": True, "promoted": promoted, "added": added}
