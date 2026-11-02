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
``check_branch(branch)``, ``repos_dir_refusal(value)``, ``check_repos_dir()``,
``binding_for_remote(project_dir, url)``, ``clone_into_workspace(url, branch)``,
``Placed``, ``attach(project_dir, repo, url, branch) -> Placed``,
``relocate(project_dir, repo) -> Placed``, ``use_branch(root, branch, ours=...)``.
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
    """The repository workspace: ``repos_dir`` from the app config, else the default.
    Where it may be is checked by ``check_repos_dir`` before anything is put there."""
    value: Optional[str] = None
    try:
        from core.app_config_manager import AppConfigManager
        value = AppConfigManager.instance().get("repos_dir")
    except Exception as exc:  # noqa: BLE001 — no config (a script, a test): the default
        logger.debug("repo_placement: app config unreadable: %s", exc)
    text = value.strip() if isinstance(value, str) and value.strip() else DEFAULT_REPOS_DIR
    return Path(text).expanduser()


def repos_dir_refusal(value: str) -> Optional[str]:
    """Why *value* may not be the repository workspace, or None: it must be an absolute
    path (``~`` allowed) and not a place a local root may not be (the home directory
    itself, SwarmWS, the app data or bindings directory, a credential location:
    ``local_roots._refusal``) nor a protected system path. The Settings PUT and every
    clone or move check it, so a folder like SwarmWS's skills can never receive a clone."""
    import os
    text = (value or "").strip()
    if not text:
        return None                                   # empty: the default
    p = Path(text).expanduser()
    if not p.is_absolute():
        return "give an absolute folder (or one starting with ~)"
    real = Path(os.path.realpath(p))
    from core.code_intel.local_roots import _refusal
    why = _refusal(real)
    if why:
        return f"it would be {why}"
    from core.library_mounts import is_protected_system_path
    if is_protected_system_path(str(real)):
        return "it is a protected system location"
    return None


def check_repos_dir() -> Path:
    """``repos_dir()``, refused (PlacementError) when ``repos_dir_refusal`` says so."""
    workspace = repos_dir()
    why = repos_dir_refusal(str(workspace))
    if why:
        raise PlacementError(f"the repository workspace {workspace} cannot be used: {why}; "
                             "change it in Settings > General")
    return workspace


def _under_bindings_root(root: Optional[Path]) -> bool:
    if root is None:
        return False
    try:
        from core.ddd_bindings import _default_bindings_root
        return root.resolve().is_relative_to(_default_bindings_root().resolve())
    except (OSError, ValueError):
        return False


def _kind(binding: Any, root: Optional[Path], workspace: Path) -> str:
    # a checkout under the bindings root is the bind-managed mirror whatever the binding
    # says (a worktree written as a path into it, or none)
    if not getattr(binding, "worktree", None) or _under_bindings_root(root):
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


#: a branch name safe to show in a prompt: letters (any script), digits and ._/@+-, no
#: leading dash, no ".." (git's own rules are checked by ``check_branch``)
_BRANCH_RE = re.compile(r"^(?!-)(?!.*\.\.)[\w./@+-]{1,200}$")


def _shown(text: Any, limit: int = 300) -> str:
    """*text* as one plain line for a prompt: control characters (a newline would end the
    quoted note and start an instruction) and markdown emphasis removed."""
    t = "".join(ch for ch in str(text) if ch.isprintable())
    return t.replace("*", "").replace("`", "")[:limit]


def describe(project_dir: Path) -> list[str]:
    """One line per repository for an agent on this machine: its checkout path (or why
    there is none) and where to write code. Every value from bindings.yaml is shown as a
    plain line (``_shown``); a branch name outside git's ref rules is shown as unknown."""
    from core.project_agent import repo_roots
    roots = repo_roots(Path(project_dir))
    lines = []
    for raw_name, p in placement(project_dir).items():
        name = _shown(raw_name, 100)
        branch = p["branch"] if isinstance(p["branch"], str) and _BRANCH_RE.match(p["branch"]) else None
        root = roots.get(raw_name)
        root = _shown(root) if root is not None else None
        if root is None:
            lines.append(f"- {name}: no checkout on this machine (bind it: artifact_cli bind "
                         f"--project {_shown(Path(project_dir).name, 100)} --url <remote>)")
        elif p["checkout"] == "mirror":
            lines.append(f"- {name}: {root} is SwarmAI's managed mirror, not a working "
                         f"checkout; move it first: artifact_cli bind --project "
                         f"{_shown(Path(project_dir).name, 100)} --relocate {name}")
        else:
            lines.append(f"- {name}: checkout {root} (branch {branch or 'unknown'}); "
                         f"{WORK_RULE}")
    return lines


# ── putting a repository in the workspace ────────────────────────────────────

class PlacementError(ValueError):
    """A URL or a move this module refuses; the message says why (never the URL's
    credentials: a URL carrying them is refused before anything else)."""


_SCHEMELESS_RE = re.compile(
    r"^(?P<host>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})/(?P<path>[\w.-]+(?:/[\w.-]+)+)/?$")


def check_branch(branch: Optional[str]) -> Optional[str]:
    """*branch* if git could take it as a branch name and not as an option, else
    PlacementError; None stays None."""
    if branch is None:
        return None
    b = branch.strip()
    if not _BRANCH_RE.match(b) or b == "@" or not _git_takes_branch(b):
        raise PlacementError(f"{branch!r} is not a branch name")
    return b


def _git_takes_branch(name: str) -> bool:
    """git's own branch-name rules (``git check-ref-format --branch``), run outside any
    repository; the name must come back unchanged (no ``@{-1}`` expansion)."""
    import subprocess
    try:
        out = subprocess.run(["git", "check-ref-format", "--branch", name], cwd="/",
                             capture_output=True, text=True, timeout=10,
                             stdin=subprocess.DEVNULL, check=False)
    except (OSError, subprocess.TimeoutExpired):     # timeout: a hang-guard only
        return False
    return out.returncode == 0 and out.stdout.strip() == name


def normalize_url(url: str) -> str:
    """The clone URL for *url*: ``github.com/owner/name`` (no scheme, as the New Brain
    form accepts it) becomes ``https://github.com/owner/name.git``. Refused: a URL that
    carries a user name or password, and any source ``ddd_bindings`` would not clone."""
    from urllib.parse import urlsplit
    from types import SimpleNamespace
    u = (url or "").strip()
    if not u:
        raise PlacementError("no URL given")
    if any(c in u for c in "?#") or any(not ch.isprintable() or ch.isspace() for ch in u):
        raise PlacementError("give the plain repository URL (no query, fragment or spaces)")
    m = _SCHEMELESS_RE.match(u)
    if m and "://" not in u and not u.startswith("git@"):
        path = m.group("path").removesuffix(".git")
        u = f"https://{m.group('host')}/{path}.git"
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
        # a local source is copied into the workspace: never one a local root may not be
        # (SwarmWS, the app data directory, ...), or bind --url would read it by copying
        import os
        from core.code_intel.local_roots import _refusal
        from core.ddd_bindings import file_url_path
        try:
            local = file_url_path(u) if u.lower().startswith("file://") else u
        except ValueError as e:
            raise PlacementError(str(e)) from None
        why = _refusal(Path(os.path.realpath(local)))
        if why:
            raise PlacementError(f"the source is {why}")
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


def _repo_key(source: str) -> str:
    """One spelling per repository source: a local path and its ``file://`` form are the
    same repository; a trailing / and .git are dropped."""
    import os
    s = str(source).strip()
    if s.lower().startswith("file://"):
        from core.ddd_bindings import file_url_path
        try:
            s = file_url_path(s)
        except ValueError:
            return s
    if s.startswith("/"):
        s = os.path.realpath(s)
    return s.rstrip("/").removesuffix(".git")


def _same_source(a: str, b: str) -> bool:
    from core.task_repos import _same_repo
    return _same_repo(a, b) or _repo_key(a) == _repo_key(b)


def _same_origin(root: Path, url: str) -> bool:
    from core.brain_bootstrap.repo_read import git
    origin = (git(root, "remote", "get-url", "origin") or "").strip()
    return bool(origin) and _same_source(origin, url)


def _check_target(target: Path) -> None:
    """Refuse a target folder a local root may not be, before anything is created there."""
    import os
    from core.code_intel.local_roots import _refusal
    real = Path(os.path.realpath(target.parent)) / target.name
    why = _refusal(real)
    if why:
        raise PlacementError(f"{target} would be {why}")


def _remove_fresh(target: Path) -> None:
    """Remove a folder this call created and could not finish (a clone cut short, a
    branch that does not exist), so the next run does not reuse a broken checkout."""
    import shutil
    shutil.rmtree(target, ignore_errors=True)


def binding_for_remote(project_dir: Path, url: str) -> Optional[str]:
    """The name of *project_dir*'s binding whose clone source is the repository *url*
    names, or None."""
    from core.project_agent import _bindings
    for name, b in _bindings(Path(project_dir)).items():
        if _same_source(b.clone, url):
            return name
    return None


def clone_into_workspace(url: str, branch: Optional[str] = None) -> tuple[Path, bool]:
    """``(checkout, cloned)``: *url* (already ``normalize_url``-ed; *branch* already
    ``check_branch``-ed) in the repository workspace. ``<repos_dir>/<name>`` when free;
    the same repository already there is used as it is (``cloned`` False; with *branch*,
    that branch is checked out, or it is refused); another repository there moves it to
    ``<owner>-<name>``, and when that is taken too nothing is done. Every check runs
    before anything is created, and a clone that does not finish is removed."""
    from core.brain_bootstrap.repo_read import git
    segs = _segments(url)
    if not segs:
        raise PlacementError("the URL names no repository")
    name = segs[-1]
    owner = segs[-2] if len(segs) > 1 else None
    for part in (name, owner or "x"):
        if part in ("", ".", "..") or part.startswith(".") or not re.fullmatch(r"[\w.-]+", part):
            raise PlacementError(f"{part!r} is not a usable folder name")
    workspace = check_repos_dir()
    candidates = [name] + ([f"{owner}-{name}"] if owner else [])
    for cand in candidates:
        target = workspace / cand
        _check_target(target)
        if target.exists():
            if (target / ".git").exists() and _same_origin(target, url):
                if branch and git(target, "checkout", "-q", branch, "--") is None:
                    raise PlacementError(f"{target} holds this repository, but branch {branch!r} "
                                         "could not be checked out there (it may not exist, or "
                                         "the checkout has changes)")
                return target, False
            continue
        workspace.mkdir(parents=True, exist_ok=True)
        from core.git_sync_engine import GitSyncEngine
        if not GitSyncEngine(target).git_clone(url, target):
            _remove_fresh(target)
            raise PlacementError(f"git could not clone the repository into {target}")
        if branch and git(target, "checkout", "-q", branch, "--") is None:
            _remove_fresh(target)
            raise PlacementError(f"branch {branch!r} does not exist in the repository; "
                                 "nothing was kept")
        return target, True
    raise PlacementError(f"{', '.join(str(workspace / c) for c in candidates)} already hold "
                         "other repositories; bind the one you want with --local")


class Placed:
    """Where a bind put a repository's checkout: ``target``. ``undo()`` puts everything
    back when the step after it fails: ``bindings.yaml`` as it was before (*original*,
    the bytes read before the write), the folder moved back to *mirror* (a relocate) or
    removed (*cloned* by this call)."""

    def __init__(self, project_dir: Path, target: Path, *, original: Optional[bytes] = None,
                 mirror: Optional[Path] = None, cloned: bool = False) -> None:
        self.project_dir, self.target = Path(project_dir), Path(target)
        self.original, self.mirror, self.cloned = original, mirror, cloned
        self.note: Optional[str] = None

    def undo(self) -> None:
        import shutil
        if self.original is not None:
            (self.project_dir / "bindings.yaml").write_bytes(self.original)
        if self.mirror is not None:
            if self.target.exists() and not self.mirror.exists():
                shutil.move(str(self.target), str(self.mirror))
        elif self.cloned:
            _remove_fresh(self.target)


def _bindings_bytes(project_dir: Path) -> bytes:
    try:
        return (Path(project_dir) / "bindings.yaml").read_bytes()
    except OSError as e:
        raise PlacementError(f"bindings.yaml could not be read ({type(e).__name__})") from None


def attach(project_dir: Path, repo: str, url: str, branch: Optional[str] = None) -> Placed:
    """Give *repo*'s existing binding a checkout in the workspace when it has none on
    this machine (a binding written by hand, or one whose ``worktree`` is another
    machine's path): *url* is cloned there (or the same repository there is reused, as
    ``clone_into_workspace`` does) and the binding's ``worktree`` points at it."""
    project_dir = Path(project_dir)
    original = _bindings_bytes(project_dir)
    root, cloned = clone_into_workspace(url, branch)
    from core.local_binding import set_worktree
    try:
        set_worktree(project_dir, repo, root)
    except Exception as e:  # noqa: BLE001 — nothing half-done
        if cloned:
            _remove_fresh(root)
        raise PlacementError(f"bindings.yaml could not be updated ({type(e).__name__}); "
                             "nothing was kept") from None
    return Placed(project_dir, root, original=original, cloned=cloned)


def use_branch(root: Path, branch: Optional[str], *, ours: bool) -> None:
    """Make sure *root* is on *branch* (already ``check_branch``-ed; None: any). A
    checkout this bind put in place (*ours*: a relocated mirror) is switched to it; a
    checkout the person already works in is never switched, only refused when it is on
    another branch."""
    if not branch:
        return
    from core.brain_bootstrap.repo_read import git
    if ours:
        if git(root, "checkout", "-q", branch, "--") is None:
            raise PlacementError(f"branch {branch!r} could not be checked out in {root} "
                                 "(it may not exist, or the checkout has changes)")
        return
    current = (git(root, "rev-parse", "--abbrev-ref", "HEAD") or "").strip()
    if current != branch:
        raise PlacementError(f"{root} is on branch {current or 'unknown'}, not {branch}; "
                             f"check out {branch} there yourself, or bind without --branch")


def _move_dir(src: Path, dst: Path) -> Optional[str]:
    """Move the folder *src* to *dst* without losing anything in it; a note when part
    of *src* is left behind, else None. On one disk it is a rename. Between disks it is
    a copy, then the removal of *src*: a copy cut short is removed (*src* was only read)
    and PlacementError says nothing was changed; a removal cut short keeps the complete
    copy and returns the note (the leftovers of *src* are no longer used)."""
    import errno
    import os
    import shutil
    try:
        os.rename(src, dst)
        return None
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise PlacementError(f"{src} could not be moved ({type(e).__name__}); "
                                 "nothing was changed") from None
    try:
        shutil.copytree(src, dst, symlinks=True)
    except Exception as e:  # noqa: BLE001 — the source is untouched: drop the partial copy
        _remove_fresh(dst)
        raise PlacementError(f"{src} could not be copied to {dst} ({type(e).__name__}); "
                             "nothing was changed") from None
    try:
        shutil.rmtree(src)
    except Exception as e:  # noqa: BLE001 — the copy is complete: keep it
        return (f"the checkout is now at {dst}; part of the old folder {src} could not be "
                f"removed ({type(e).__name__}) and is no longer used")
    return None


def _other_brains_on(project_dir: Path, repo: str, mirror: Path) -> list[str]:
    """Other Brains whose binding uses the same mirror folder (it has no Brain in its
    name, so two Brains binding one remote share it)."""
    from core.ddd_bindings import binding_worktree
    from core.project_agent import _bindings
    out = []
    for d in sorted(Path(project_dir).parent.iterdir()) if Path(project_dir).parent.is_dir() else []:
        if d == Path(project_dir) or not (d / "bindings.yaml").is_file():
            continue
        for b in _bindings(d).values():
            try:
                if binding_worktree(b).resolve() == mirror.resolve():
                    out.append(d.name)
            except (ValueError, OSError):
                continue
    return out


def relocate(project_dir: Path, repo: str) -> Placed:
    """Move *repo*'s bind-managed mirror into the repository workspace and record it
    there (``bindings.yaml`` ``worktree``). The folder moves as it is, with any work in
    it (moving deletes nothing; ``_move_dir``). Refused, with nothing moved, when another
    Brain uses the same mirror, the target is taken or not allowed, or the binding
    already has its own checkout. ``Placed.note`` says when part of the old folder was
    left behind."""
    import shutil
    from core.ddd_bindings import binding_worktree
    from core.project_agent import _bindings, repo_roots
    project_dir = Path(project_dir)
    b = _bindings(project_dir).get(repo)
    if b is None:
        raise PlacementError(f"{project_dir.name} binds no repository named {repo!r}")
    try:
        mirror = binding_worktree(b) if not b.worktree else repo_roots(project_dir).get(repo)
    except (ValueError, OSError) as e:
        raise PlacementError(f"{repo}: {e}") from None
    if mirror is None or not _under_bindings_root(mirror):
        raise PlacementError(f"{repo} already has its own checkout ({b.worktree})")
    if not mirror.is_dir():
        raise PlacementError(f"{repo} has no mirror on this machine; bind it with --url")
    others = _other_brains_on(project_dir, repo, mirror)
    if others:
        raise PlacementError(f"{', '.join(others)} also use{'s' if len(others) == 1 else ''} "
                             f"{mirror}; bind this Brain with --url instead (nothing was moved)")
    workspace = check_repos_dir()
    target = workspace / repo
    _check_target(target)
    if target.exists():
        raise PlacementError(f"{target} already exists; nothing was moved")
    original = _bindings_bytes(project_dir)
    workspace.mkdir(parents=True, exist_ok=True)
    note = _move_dir(mirror, target)
    from core.local_binding import set_worktree
    try:
        set_worktree(project_dir, repo, target)
    except Exception as e:  # noqa: BLE001 — put it back: nothing half-done
        name = type(e).__name__
        try:
            shutil.move(str(target), str(mirror))
        except Exception as back:  # noqa: BLE001 — say where the checkout is
            raise PlacementError(
                f"bindings.yaml could not be updated ({name}) and the checkout could not "
                f"be moved back ({type(back).__name__}); it is at {target}") from None
        raise PlacementError(f"bindings.yaml could not be updated ({name}); "
                             "the mirror was put back") from None
    placed = Placed(project_dir, target, original=original, mirror=mirror)
    placed.note = note
    return placed
