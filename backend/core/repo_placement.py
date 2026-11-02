"""repo_placement — where a Brain's repositories live on this machine, and where code is
written (fixing-spec docs/design/2026-11-02-new-brain-setup-fixing-spec.md G1).

A Brain's code repository has one checkout on the person's machine, and every path that
touches code uses it: a native coding session works in a session worktree of it, a Hive
task starts from its commit, and the commits a Hive returns land on its branch
(``core/hive_return``). Three kinds of checkout:

- ``repos_dir:<name>``: in the repository workspace (``repos_dir`` in the app config,
  default ``~/SwarmAI-Repos``), where ``artifact_cli bind --url`` clones a repository.
- ``local``: a checkout the person already had, bound with ``bind --local``.
- ``mirror``: the bind-managed clone under ``~/.swarm-ai/bindings/`` (a Brain bound
  before the workspace existed). It is not a place to work: a re-bind replaces it
  (``ddd_bindings._clear_worktree_for_reclone``). ``bind --relocate`` moves it into the
  workspace.

The Project Agent definition records the kind (``intake.placement``), never an absolute
path, so the same definition holds on every machine and on a Hive; the path is resolved
here, on the machine that reads it.

Public: ``DEFAULT_REPOS_DIR``, ``repos_dir()``, ``placement(project_dir)``,
``describe(project_dir)``, ``WORK_RULE``, ``PlacementError``, ``normalize_url(url)``,
``clone_into_workspace(url, branch)``, ``relocate(project_dir, repo)``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_REPOS_DIR = "~/SwarmAI-Repos"

#: where code is written on this machine: the same rule for every Brain
WORK_RULE = ("make code changes in a session worktree of this checkout "
             "(\"$SWARM_BACKEND_BIN\" py -m scripts.session_worktree create <checkout>), "
             "commit there and land it; never edit the checkout itself while another "
             "session may be using it")


def repos_dir() -> Path:
    """The repository workspace: ``repos_dir`` from the app config, else the default."""
    value: Optional[str] = None
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get("repos_dir")
    except Exception as exc:  # noqa: BLE001 — no config (a script, a test): the default
        logger.debug("repo_placement: app config unreadable: %s", exc)
    text = value.strip() if isinstance(value, str) and value.strip() else DEFAULT_REPOS_DIR
    return Path(text).expanduser()


def _kind(binding: Any, root: Optional[Path], workspace: Path) -> str:
    if not getattr(binding, "worktree", None):
        return "mirror"
    if root is not None:
        try:
            rel = root.resolve().relative_to(workspace.resolve())
            return f"repos_dir:{rel.as_posix()}"
        except (ValueError, OSError):
            pass
    return "local"


def placement(project_dir: Path) -> dict[str, dict[str, Any]]:
    """``{repo: {remote, branch, checkout}}`` for each repository the Brain binds;
    ``checkout`` is the kind (see the module docstring), never a path."""
    from core.project_agent import _bindings, repo_roots
    roots = repo_roots(Path(project_dir))
    workspace = repos_dir()
    out: dict[str, dict[str, Any]] = {}
    for name, b in _bindings(Path(project_dir)).items():
        contract = getattr(b, "delivery_contract", None)
        out[name] = {"remote": b.clone, "branch": getattr(contract, "branch", None),
                     "checkout": _kind(b, roots.get(name), workspace)}
    return out


def describe(project_dir: Path) -> list[str]:
    """One line per repository for an agent on this machine: its checkout path (or why
    there is none) and where to write code."""
    from core.project_agent import repo_roots
    roots = repo_roots(Path(project_dir))
    lines = []
    for name, p in placement(project_dir).items():
        root = roots.get(name)
        if root is None:
            lines.append(f"- {name}: no checkout on this machine (bind it: artifact_cli bind "
                         f"--project {Path(project_dir).name} --url <remote>)")
        elif p["checkout"] == "mirror":
            lines.append(f"- {name}: {root} is SwarmAI's managed mirror, not a working "
                         f"checkout; move it first: artifact_cli bind --project "
                         f"{Path(project_dir).name} --relocate {name}")
        else:
            lines.append(f"- {name}: checkout {root} (branch {p['branch'] or 'unknown'}); "
                         f"{WORK_RULE}")
    return lines


# ── putting a repository in the workspace ────────────────────────────────────

class PlacementError(ValueError):
    """A URL or a move this module refuses; the message says why (never the URL's
    credentials: a URL carrying them is refused before anything else)."""


_SCHEMELESS_RE = re.compile(
    r"^(?P<host>[A-Za-z0-9.-]+\.[A-Za-z]{2,})/(?P<path>[\w.-]+(?:/[\w.-]+)+?)(?:\.git)?/?$")


def normalize_url(url: str) -> str:
    """The clone URL for *url*: ``github.com/owner/name`` (no scheme, as the New Brain
    form accepts it) becomes ``https://github.com/owner/name.git``. Refused: a URL that
    carries a user name or password, and any source ``ddd_bindings`` would not clone."""
    from urllib.parse import urlsplit
    from types import SimpleNamespace
    u = (url or "").strip()
    if not u:
        raise PlacementError("no URL given")
    m = _SCHEMELESS_RE.match(u)
    if m and "://" not in u and not u.startswith("git@"):
        u = f"https://{m.group('host')}/{m.group('path')}.git"
    if u.lower().startswith(("https://", "http://", "ssh://")):
        parts = urlsplit(u)
        if parts.password or (parts.username and parts.scheme.lower() != "ssh"):
            raise PlacementError("the URL carries credentials; give the plain repository URL "
                                 "(git uses your own credentials to clone)")
    from core.ddd_bindings import _validate_clone_source
    try:
        _validate_clone_source(SimpleNamespace(clone=u, repo="url"))
    except ValueError as e:
        raise PlacementError(str(e).replace("binding 'url': ", "")) from None
    if u.startswith("/") or u.lower().startswith("file://"):
        return u
    if not u.lower().startswith(("https://", "ssh://", "git@")):
        raise PlacementError("give an https://, ssh:// or git@host:owner/name URL")
    return u


def _segments(url: str) -> list[str]:
    tail = url.rstrip("/").split(":", 1)[1] if url.startswith("git@") else url.rstrip("/")
    segs = [s for s in tail.replace("\\", "/").split("/") if s]
    if segs and segs[-1].endswith(".git"):
        segs[-1] = segs[-1][:-4]
    return segs


def _same_origin(root: Path, url: str) -> bool:
    from core.brain_bootstrap.repo_read import git
    from core.task_repos import _same_repo
    origin = (git(root, "remote", "get-url", "origin") or "").strip()
    if not origin:
        return False
    norm = lambda s: s.rstrip("/").removesuffix(".git")  # noqa: E731
    return _same_repo(origin, url) or norm(origin) == norm(url)


def clone_into_workspace(url: str, branch: Optional[str] = None) -> tuple[Path, bool]:
    """``(checkout, cloned)``: *url* (already ``normalize_url``-ed) in the repository
    workspace. ``<repos_dir>/<name>`` when free; the same repository already there is
    used as it is (``cloned`` False); another repository there moves it to
    ``<owner>-<name>``, and when that is taken too nothing is done."""
    segs = _segments(url)
    if not segs:
        raise PlacementError("the URL names no repository")
    name = segs[-1]
    owner = segs[-2] if len(segs) > 1 else None
    if name in ("", ".", "..") or name.startswith("."):
        raise PlacementError(f"{name!r} is not a usable folder name")
    workspace = repos_dir()
    candidates = [name] + ([f"{owner}-{name}"] if owner else [])
    for cand in candidates:
        target = workspace / cand
        if target.exists():
            if (target / ".git").exists() and _same_origin(target, url):
                return target, False
            continue
        workspace.mkdir(parents=True, exist_ok=True)
        from core.git_sync_engine import GitSyncEngine
        if not GitSyncEngine(target).git_clone(url, target):
            raise PlacementError(f"git could not clone the repository into {target}")
        if branch:
            from core.brain_bootstrap.repo_read import git
            if git(target, "checkout", "-q", branch) is None:
                raise PlacementError(f"cloned into {target}, but branch {branch!r} does not exist")
        return target, True
    raise PlacementError(f"{', '.join(str(workspace / c) for c in candidates)} already hold "
                         "other repositories; bind the one you want with --local")


def relocate(project_dir: Path, repo: str) -> Path:
    """Move *repo*'s bind-managed mirror into the repository workspace and record it
    there (``bindings.yaml`` ``worktree``). Refused, with nothing moved, when the mirror
    holds work that is not on a remote, or the binding already has its own checkout."""
    from core.ddd_bindings import _worktree_has_unsaved_work, binding_worktree
    from core.project_agent import _bindings
    b = _bindings(Path(project_dir)).get(repo)
    if b is None:
        raise PlacementError(f"{Path(project_dir).name} binds no repository named {repo!r}")
    if b.worktree:
        raise PlacementError(f"{repo} already has its own checkout ({b.worktree})")
    mirror = binding_worktree(b)
    if not mirror.is_dir():
        raise PlacementError(f"{repo} has no mirror on this machine; bind it with --url")
    if _worktree_has_unsaved_work(mirror):
        raise PlacementError(f"{mirror} has uncommitted or unpushed work; nothing was moved")
    workspace = repos_dir()
    target = workspace / repo
    if target.exists():
        raise PlacementError(f"{target} already exists; nothing was moved")
    workspace.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.move(str(mirror), str(target))
    from core.local_binding import set_worktree
    try:
        set_worktree(Path(project_dir), repo, target)
    except (OSError, ValueError):
        shutil.move(str(target), str(mirror))            # put it back: nothing half-done
        raise
    return target
