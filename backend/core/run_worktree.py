"""run_worktree — one git worktree per pipeline run: create, validate, sync, land,
release, sweep (docs/design/pipeline-run-worktree.md, r4).

All deterministic git work for an isolated run lives here; artifact_cli,
pipeline_test, the write gate and the daemon sweep call it. Invariants:

- Paths and branches are DERIVED from run_id, never read from run.json:
  ``WT_ROOT/<run_id>/<repo_name>`` and ``refs/heads/run/<run_id>`` (§5.2).
- The run's change stays uncommitted relative to ``base``: worktree HEAD == base,
  except transiently inside ``sync`` / ``land`` (§7.1).
- Change set = tracked edits/deletions + explicitly ``git add``-ed new files;
  never ``add -A`` (§6.2). ``T`` = that set's tree (B excluded).
- Landing is native ``git merge --ff-only`` in the main checkout (§7.4).
- An unintegrated worktree is deleted only after a verified ledger snapshot (§8.1).

Key public symbols: ``WorktreeError``, ``wt_root``, ``wt_path``, ``branch_ref``,
``mode_enabled``, ``code_repos``, ``create``, ``validate``, ``change_tree``,
``untracked_new_files``, ``changed_paths``, ``sync``, ``land``, ``release``,
``snapshot_to_ledger``, ``sweep``, ``mark``, ``diff_since``, ``own_isolated_run``,
``run_for_path``, ``active_worktree_roots``, ``last_activity``.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

logger = logging.getLogger(__name__)

RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9_]{1,64}$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
COMMIT_TIMEOUT_S = 300.0
INTEGRATE_LOCK_TIMEOUT_S = 30.0
STALE_DAYS = 14
LEDGER_KEEP_DAYS = 90
_LIVE = ("running", "paused")
_IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply")
# Dependency dirs of the main checkout made visible in a new worktree as symlinks (the
# worktree holds tracked files only). Each link is excluded from git (info/exclude:
# a symlink does not match a ``node_modules/`` gitignore line).
DEP_LINKS = ("desktop/node_modules",)
# No other dependency dir (.venv, venv, a top-level node_modules): an install or sync run
# in the worktree would write through the link into the main checkout's environment.
# Environments for other repos come with the multi-repo test runner.


class WorktreeError(Exception):
    """A refused worktree operation: ``code`` is the error the model sees (§7.7)."""

    def __init__(self, code: str, message: str, **data):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, **self.data}


# ── locations ────────────────────────────────────────────────────────────────
def _home() -> Path:
    return Path(os.path.realpath(os.path.expanduser("~")))


def _data_dir() -> Path:
    """The app data dir (``~/.swarm-ai``; $SWARM_DATA_DIR in tests and other installs),
    so a test or a second install never sees this install's worktrees."""
    try:
        from config import get_app_data_dir
        return Path(os.path.realpath(str(get_app_data_dir())))
    except Exception as exc:  # noqa: BLE001 — standalone use without the backend config
        logger.debug("app data dir unavailable (%s); using ~/.swarm-ai", exc)
        return _home() / ".swarm-ai"


def wt_root() -> Path:
    """``<app data>/worktrees``, or $SWARM_WORKTREE_ROOT (tests)."""
    env = os.environ.get("SWARM_WORKTREE_ROOT")
    return Path(os.path.realpath(env)) if env else _data_dir() / "worktrees"


def ledger_root() -> Path:
    env = os.environ.get("SWARM_WORKTREE_LEDGER")
    return Path(os.path.realpath(env)) if env else _data_dir() / "worktree-ledger"


_WS_STAMP = ".workspace"


def stamp_workspace(run_id: str, workspace: "str | Path") -> None:
    """Record which SwarmWS owns ``WT_ROOT/<run_id>/`` — the sweep only judges
    worktrees of its own workspace (a test's temp workspace never sees real runs)."""
    d = wt_root() / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / _WS_STAMP).write_text(os.path.realpath(str(workspace)) + "\n", encoding="utf-8")


def drop_stamp_only_dir(run_id: str, workspace: "str | Path") -> bool:
    """Remove ``WT_ROOT/<run_id>/`` when it holds nothing but this workspace's stamp (an
    isolated run that never got a worktree): a left-over dir keeps the write gate armed
    for every session. Never touches a dir holding anything else."""
    d = wt_root() / run_id
    try:
        if not RUN_ID_RE.fullmatch(run_id) or os.path.islink(d) or not d.is_dir():
            return False
        if [p.name for p in d.iterdir()] != [_WS_STAMP] or not owned_by(run_id, workspace):
            return False
        (d / _WS_STAMP).unlink()
        d.rmdir()
        return True
    except OSError:
        return False


def owned_by(run_id: str, workspace: "str | Path") -> bool:
    try:
        got = (wt_root() / run_id / _WS_STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return got == os.path.realpath(str(workspace))


def _check_ids(run_id: str, repo_name: "str | None" = None) -> None:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise WorktreeError("worktree_invalid", f"unsafe run id {run_id!r}")
    if repo_name is not None and (not isinstance(repo_name, str)
                                  or not REPO_NAME_RE.fullmatch(repo_name)
                                  or repo_name in (".", "..")):
        raise WorktreeError("worktree_invalid", f"unsafe repo name {repo_name!r}")


def wt_path(run_id: str, repo_name: str) -> Path:
    _check_ids(run_id, repo_name)
    return wt_root() / run_id / repo_name


def repo_key(main: "str | Path", entries: "Iterable[dict]") -> str:
    """The worktree key (``repo_name`` in run.json, the directory under
    ``WT_ROOT/<run_id>/``) for *main*: its directory name, unless another entry of the
    run already uses that name for a DIFFERENT repo — then ``<name>-<8 hex of the main
    path>``. *entries* must carry the resolved ``main`` of each entry (an entry without
    one counts as a different repo, so a key is never shared)."""
    real = os.path.realpath(str(main))
    base = re.sub(r"[^A-Za-z0-9._-]", "-", os.path.basename(real)).lstrip("._-") or "repo"
    base = base[:55]
    clash = any(isinstance(e, dict) and e.get("repo_name") == base
                and os.path.realpath(str(e.get("main") or "")) != real for e in entries)
    if not clash:
        return base
    return f"{base}-{hashlib.sha256(real.encode()).hexdigest()[:8]}"


def branch_name(run_id: str) -> str:
    _check_ids(run_id)
    return f"run/{run_id}"


def branch_ref(run_id: str) -> str:
    return "refs/heads/" + branch_name(run_id)


def run_for_path(path: "str | Path") -> "tuple[str, str] | None":
    """(run_id, repo_name) when *path* lies inside a derived worktree location."""
    try:
        rel = Path(os.path.realpath(str(path))).relative_to(os.path.realpath(str(wt_root())))
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) < 2 or not RUN_ID_RE.fullmatch(parts[0]) or not REPO_NAME_RE.fullmatch(parts[1]):
        return None
    return parts[0], parts[1]


def _no_symlinks(p: Path) -> bool:
    """No component from $HOME (or WT_ROOT's parent in tests) down to *p* is a symlink."""
    p = Path(os.path.abspath(str(p)))
    anchor = _home() if str(p).startswith(str(_home()) + os.sep) else Path(p.anchor)
    cur = anchor
    for part in p.relative_to(anchor).parts:
        cur = cur / part
        try:
            if os.path.islink(cur):
                return False
        except OSError:
            return False
    return True


# ── git ──────────────────────────────────────────────────────────────────────
_GIT_ENV = {"GIT_EDITOR": "true", "GIT_SEQUENCE_EDITOR": "true", "GIT_TERMINAL_PROMPT": "0",
            "GIT_MERGE_AUTOEDIT": "no"}


def git(cwd: "str | Path", *args: str, timeout: float = 60.0, env: "dict | None" = None,
        input_text: "str | None" = None) -> subprocess.CompletedProcess:
    """List-form git with a timeout; never opens an editor or a prompt. Raises
    WorktreeError('git_failed') when git cannot run at all (timeout included)."""
    # Inherited git env is dropped: GIT_DIR & co. would retarget the command, and
    # GIT_CONFIG_* (COUNT/KEY_n/VALUE_n/PARAMETERS) could switch the final commit's
    # hooks off (core.hooksPath) or rewrite the author — seen leaking between shells.
    e = {k: v for k, v in os.environ.items()
         if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_LITERAL_PATHSPECS",
                      "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY")
         and not k.startswith("GIT_CONFIG")}
    e.update(_GIT_ENV)
    if env:
        e.update(env)
    try:
        return subprocess.run(["git", "-C", str(cwd), "-c", "core.quotepath=off", *args],
                              capture_output=True, text=True, timeout=timeout, env=e,
                              input=input_text,
                              stdin=None if input_text is not None else subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise WorktreeError("git_timeout", f"git {args[0]} timed out after {timeout:.0f}s") from exc
    except OSError as exc:
        raise WorktreeError("git_failed", f"git could not run: {exc}") from exc


def _out(cwd, *args, **kw) -> str:
    p = git(cwd, *args, **kw)
    if p.returncode != 0:
        raise WorktreeError("git_failed", f"git {' '.join(args[:3])} failed: "
                            f"{(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def _rev(cwd, ref: str) -> str:
    return _out(cwd, "rev-parse", "--verify", "-q", ref + "^{commit}").strip()


def _git_path(cwd, name: str) -> Path:
    p = _out(cwd, "rev-parse", "--git-path", name).strip()
    return Path(p) if os.path.isabs(p) else Path(cwd) / p


# Per-process memo for the read-only answers one CLI call asks again and again (a
# stage-start ran --show-toplevel, --git-common-dir, symbolic-ref HEAD and worktree
# list several times each). Every key names the files the answer is read from, by
# identity (inode, mtime, size): git rewrites them by lock + rename, so any change
# gives a new key and a stale answer is never returned. A failure is never stored.
_REV_MEMO: "dict[tuple, object]" = {}
_REV_MEMO_MAX = 512


def _stat_key(path) -> "tuple | None":
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def _git_marker(cwd) -> "tuple | None":
    """Identity of a repo/worktree ROOT's own `.git` entry, None for any other cwd.
    A `.git` FILE (linked worktree) is keyed on the file itself. A `.git` DIRECTORY is
    keyed on its inode plus `config` (core.worktree / core.bare) and `commondir`, the
    only things inside it that the root answers depend on. Not on the directory's
    mtime: that moves with every index.lock git takes, so the memo missed after any
    git write in the main checkout."""
    dot = os.path.join(str(cwd), ".git")
    try:
        st = os.stat(dot)
    except OSError:
        return None
    real = os.path.realpath(str(cwd))
    if os.path.isdir(dot):
        return (real, "dir", st.st_ino, _stat_key(os.path.join(dot, "config")),
                _stat_key(os.path.join(dot, "commondir")))
    return (real, "file", st.st_ino, st.st_mtime_ns, st.st_size)


def _memo_get(key, compute):
    if key is not None and key in _REV_MEMO:
        return _REV_MEMO[key]
    val = compute()
    if key is not None and val:
        if len(_REV_MEMO) >= _REV_MEMO_MAX:
            _REV_MEMO.clear()
        _REV_MEMO[key] = val
    return val


def _files_refs(common) -> bool:
    """True when the repo keeps refs as files. A reftable repo (git >= 2.45) leaves
    HEAD as a fixed stub and keeps the real HEAD in reftable/, so no HEAD file stat
    can tell a branch switch: HEAD-derived answers are not memoised there."""
    return not os.path.isdir(os.path.join(str(common), "reftable"))


def _memo(kind: str, cwd, compute) -> "str | None":
    m = _git_marker(cwd)
    return _memo_get((kind, *m) if m else None, compute)


def _common_dir(cwd) -> str:
    def compute() -> str:
        p = _out(cwd, "rev-parse", "--git-common-dir").strip()
        return os.path.realpath(p if os.path.isabs(p) else os.path.join(str(cwd), p))
    return _memo("common", cwd, compute)


def _toplevel(cwd) -> "str | None":
    def compute() -> "str | None":
        try:
            p = git(cwd, "rev-parse", "--show-toplevel", timeout=10)
        except WorktreeError:
            return None
        return os.path.realpath(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None
    return _memo("top", cwd, compute)


def busy_state(cwd) -> "str | None":
    """Name of an in-progress merge / rebase / cherry-pick / revert, else None.
    One rev-parse resolves every name (it prints one path per --git-path)."""
    args = [a for name in _IN_PROGRESS for a in ("--git-path", name)]
    paths = _out(cwd, "rev-parse", *args).splitlines()
    if len(paths) != len(_IN_PROGRESS):
        raise WorktreeError("git_failed", "git rev-parse --git-path returned "
                            f"{len(paths)} paths for {len(_IN_PROGRESS)} names")
    for name, p in zip(_IN_PROGRESS, paths):
        p = p.strip()
        if (Path(p) if os.path.isabs(p) else Path(cwd) / p).exists():
            return name
    return None


def _symbolic_head(cwd) -> "str | None":
    def compute() -> "str | None":
        p = git(cwd, "symbolic-ref", "-q", "HEAD", timeout=10)
        return p.stdout.strip() if p.returncode == 0 else None
    # Memoised only for a main checkout (`.git` directory) that keeps refs as files:
    # the answer is then the content of .git/HEAD. A detached HEAD (None) is never
    # stored.
    m = _git_marker(cwd)
    key = None
    if m and m[1] == "dir" and _files_refs(os.path.join(str(cwd), ".git")):
        hk = _stat_key(os.path.join(str(cwd), ".git", "HEAD"))
        key = ("head", *m, hk) if hk else None
    return _memo_get(key, compute)


# ── switch and bindings ──────────────────────────────────────────────────────
def mode_enabled() -> bool:
    """True unless ``config.json["pipeline"]["worktree_mode"]`` is ``"off"`` (any case or
    whitespace) or JSON false (read-only).
    On is the default: a missing key or an unreadable config means on, and a run that
    cannot get a worktree still falls back to the shared checkout at stage-start
    (pipeline_worktree._shared). $SWARM_WORKTREE_MODE (on/off) overrides — tests and
    operator use."""
    env = (os.environ.get("SWARM_WORKTREE_MODE") or "").strip().lower()
    if env in ("on", "off"):
        return env == "on"
    try:
        from config import get_app_data_dir
        cfg = json.loads((get_app_data_dir() / "SwarmWS" / "config.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — unreadable config = the default (on)
        logger.debug("worktree_mode: config unreadable (%s) — on", exc)
        return True
    pipe = cfg.get("pipeline") if isinstance(cfg, dict) else None
    raw = pipe.get("worktree_mode") if isinstance(pipe, dict) else None
    if raw is False:
        return False            # a hand edit that wrote JSON false means off
    return str(raw or "").strip().lower() != "off"


def code_repos(workspace: "str | Path", project: str, self_repo: "str | Path") -> "list[tuple[str, Path]]":
    """(repo_name, main checkout) for each ``kind: code-repo`` asset of the project's
    bindings.yaml. A ``source_workspace`` path is used as is; an asset without one is the
    self-hosted repo when its name matches *self_repo*'s directory name. Each path must be
    the toplevel of a git repo. Unreadable bindings → []."""
    import yaml
    f = Path(workspace) / "Projects" / project / "bindings.yaml"
    try:
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — no / bad bindings = no code repo (today's behaviour)
        logger.debug("bindings unreadable for %s (%s): no code repo", project, exc)
        return []
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for a in (doc.get("governed_assets") or []) if isinstance(doc, dict) else []:
        if not isinstance(a, dict) or a.get("kind") != "code-repo":
            continue
        src = a.get("source_workspace")
        if src:
            path = Path(os.path.realpath(os.path.expanduser(str(src))))
        elif str(a.get("name") or "").lower() == Path(self_repo).name.lower():
            path = Path(os.path.realpath(str(self_repo)))
        else:
            continue
        name = path.name
        if not REPO_NAME_RE.fullmatch(name) or name in seen or _toplevel(path) != str(path):
            continue
        seen.add(name)
        out.append((name, path))
    return sorted(out)


def _worktree_list_key(main) -> "tuple | None":
    """Identity of every file `git worktree list` reads a path or branch from: the
    common dir's HEAD and config, and each admin dir's HEAD and gitdir. None when the
    main checkout is not a root with its own `.git` directory, or the repo uses
    reftable (no memo then)."""
    m = _git_marker(main)
    common = os.path.join(str(main), ".git")
    if not m or m[1] != "dir" or not _files_refs(common):
        return None
    parts: list = [m, _stat_key(os.path.join(common, "HEAD"))]
    root = os.path.join(common, "worktrees")
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    for n in names:
        d = os.path.join(root, n)
        parts.append((n, _stat_key(os.path.join(d, "HEAD")), _stat_key(os.path.join(d, "gitdir"))))
    return ("wtlist", *parts)


def _worktree_list(main: "str | Path") -> "list[dict]":
    """`git worktree list --porcelain` → [{path, branch}] (no branch: detached).
    Memoised on the files those two fields are read from; a commit on a branch moves
    no such file and changes neither field."""
    def compute() -> "tuple":
        items, cur = [], {}
        for line in _out(main, "worktree", "list", "--porcelain").splitlines() + [""]:
            if not line:
                if cur:
                    items.append(cur)
                cur = {}
            elif line.startswith("worktree "):
                cur["path"] = os.path.realpath(line[9:])
            elif line.startswith("branch "):
                cur["branch"] = line[7:]
        return tuple(tuple(sorted(c.items())) for c in items)
    return [dict(c) for c in (_memo_get(_worktree_list_key(main), compute) or ())]


def is_registered(main, run_id: str, repo_name: str) -> bool:
    want = str(wt_path(run_id, repo_name))
    try:
        for w in _worktree_list(main):
            if w.get("path") != want:
                continue
            if w.get("branch") == branch_ref(run_id):
                return True
            # mid-rebase the worktree is detached; the branch is in head-name
            for d in ("rebase-merge", "rebase-apply"):
                f = _git_path(want, d) / "head-name"
                if f.is_file() and f.read_text(encoding="utf-8").strip() == branch_ref(run_id):
                    return True
        return False
    except (WorktreeError, OSError):
        return False


# ── create / validate ────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _exclude_paths(common_dir: str, rels: "Iterable[str]") -> None:
    """Add ``/<rel>`` lines to the repo's info/exclude (shared by its worktrees)."""
    f = Path(common_dir) / "info" / "exclude"
    try:
        cur = f.read_text(encoding="utf-8") if f.exists() else ""
        have = set(cur.splitlines())
        add = [f"/{r}" for r in rels if f"/{r}" not in have]
        if add:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n")
                         + "\n".join(add) + "\n", encoding="utf-8")
    except OSError:
        pass  # only cosmetic: the link would show as untracked


def _ignored(wt: Path, rel: str) -> bool:
    """git ignores *rel* in *wt* (.gitignore or info/exclude)."""
    return git(wt, "check-ignore", "-q", "--no-index", "--", rel).returncode == 0


def _link_dependencies(main: Path, wt: Path) -> "list[str]":
    """Symlink each DEP_LINKS dir of *main* into *wt* when the worktree lacks it (so it
    is not tracked) → the repo-relative paths linked."""
    linked = []
    for rel in DEP_LINKS:
        src, dst = main / rel, wt / rel
        if not src.is_dir() or not dst.parent.is_dir() or os.path.lexists(dst):
            continue
        try:
            os.symlink(str(src), str(dst))
            linked.append(rel)
        except OSError:
            pass  # tests/builds then fail visibly; never fall back silently to main
    return linked


def create(run_id: str, repo_name: str, main: "str | Path",
           workspace: "str | Path | None" = None, at: "str | None" = None) -> dict:
    """Create ``WT_ROOT/<run_id>/<repo_name>`` on a new ``run/<run_id>`` branch at the
    main checkout's HEAD (other sessions' uncommitted edits stay out), or at commit
    *at* (a ledger restore). Returns the run.json ``worktrees[]`` entry. Raises
    WorktreeError (caller falls back to shared)."""
    main = Path(os.path.realpath(str(main)))
    head_ref = _symbolic_head(main)
    if not head_ref or not head_ref.startswith("refs/heads/"):
        raise WorktreeError("main_checkout_busy", "main checkout is on a detached HEAD")
    busy = busy_state(main)
    if busy:
        raise WorktreeError("main_checkout_busy", f"main checkout has {busy} in progress")
    path = wt_path(run_id, repo_name)
    if path.exists():
        raise WorktreeError("worktree_invalid", f"{path} already exists")
    if not _no_symlinks(path.parent.parent):
        raise WorktreeError("worktree_invalid", f"a component of {path.parent.parent} is a symlink")
    head = _rev(main, "HEAD" if at is None else at)
    path.parent.mkdir(parents=True, exist_ok=True)
    if workspace is not None:
        stamp_workspace(run_id, workspace)
    p = git(main, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-q", "-b",
            branch_name(run_id), str(path), head, timeout=120)
    if p.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise WorktreeError("worktree_create_failed", (p.stderr or p.stdout).strip()[:300])
    common = _common_dir(main)
    links = _link_dependencies(main, path)
    _exclude_paths(common, links)
    return {"repo_name": repo_name, "main": str(main), "git_common_dir": common,
            "integration_branch": head_ref, "base": head, "pre_sync_tip": None,
            "state": "active", "created_at": _now_iso(), "dep_links": links}


def adopt(run_id: str, repo_name: str, main: "str | Path") -> dict:
    """The entry for a worktree ``create`` made but its caller never recorded (killed
    in between). Only an untouched one is adopted: HEAD still at main's HEAD."""
    main = Path(os.path.realpath(str(main)))
    path = wt_path(run_id, repo_name)
    head_ref = _symbolic_head(main)
    base = _rev(path, "HEAD")
    if not head_ref or not is_registered(main, run_id, repo_name):
        raise WorktreeError("worktree_invalid", f"{path} cannot be adopted")
    return {"repo_name": repo_name, "main": str(main), "git_common_dir": _common_dir(main),
            "integration_branch": head_ref, "base": base, "pre_sync_tip": None,
            "state": "active", "created_at": _now_iso(), "dep_links": [],
            "adopted": True}


def validate(run_id: str, entry: dict, main: "str | Path") -> Path:
    """§5.2 checks before ANY use; returns the worktree path. Nothing is "repaired"
    from run.json — a mismatch is refused (WorktreeError 'worktree_invalid')."""
    name = entry.get("repo_name") if isinstance(entry, dict) else None
    path = wt_path(run_id, str(name))
    bad = lambda why: WorktreeError("worktree_invalid", f"worktree {path}: {why}")  # noqa: E731
    if os.path.realpath(str(path)) != str(path) or not _no_symlinks(path):
        raise bad("path is not the derived location or crosses a symlink")
    if not path.is_dir():
        raise bad("missing on disk")
    main = Path(os.path.realpath(str(main)))
    if not is_registered(main, run_id, str(name)):
        raise bad(f"not registered in git with branch {branch_ref(run_id)}")
    common = _common_dir(path)
    if common != entry.get("git_common_dir") or common != _common_dir(main):
        raise bad("git common dir differs from the recorded one")
    ib = entry.get("integration_branch")
    if not isinstance(ib, str) or not re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", ib) \
            or ib != _symbolic_head(main):
        raise bad(f"integration branch {ib!r} is not the main checkout's branch")
    return path


# ── change set ───────────────────────────────────────────────────────────────
def deliverable_prefix(run_id: str) -> str:
    """B (.aidlc/deliverables/<run_id>/) — generated at landing, never part of T."""
    return f".aidlc/deliverables/{run_id}/"


def _temp_index(wt) -> "tuple[str, dict]":
    """A private copy of the worktree's index → (path, env). The caller removes it."""
    fd, tmp = tempfile.mkstemp(prefix="swarm-idx-")
    os.close(fd)
    src = _git_path(wt, "index")
    if src.exists():
        # copy2 keeps the index mtime: git's racy-entry check compares each entry's
        # mtime with the INDEX FILE's; a fresh mtime would hide a same-size edit
        # made in the same second as the checkout.
        shutil.copy2(src, tmp)
    else:
        os.unlink(tmp)
    return tmp, {"GIT_INDEX_FILE": tmp}


def change_tree(wt: "str | Path", run_id: str) -> str:
    """``T``: the tree of the run's change set — index + tracked edits/deletions
    (``add -u`` on a private index copy), minus B. Untracked files are not in it."""
    tmp, env = _temp_index(wt)
    try:
        _out(wt, "add", "-u", env=env, timeout=120)
        _out(wt, "rm", "-r", "-q", "--cached", "--ignore-unmatch", "--",
             deliverable_prefix(run_id).rstrip("/"), env=env)
        return _out(wt, "write-tree", env=env).strip()
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def untracked_new_files(wt: "str | Path", run_id: str) -> "list[str]":
    """Unignored, un-added new files (§6.2) — B and the dependency links excluded."""
    out = _out(wt, "ls-files", "--others", "--exclude-standard", "-z", timeout=60)
    b = deliverable_prefix(run_id)
    return sorted(p for p in out.split("\0")
                  if p and not p.startswith(b)
                  and not any(p == d or p.startswith(d + "/") for d in DEP_LINKS))


def changed_paths(wt: "str | Path", base: str, tree: str) -> "list[str]":
    """Repo-relative paths that differ between *base* and the tree *tree*."""
    out = _out(wt, "diff-tree", "-r", "--no-renames", "--name-only", "-z", base, tree)
    return sorted(p for p in out.split("\0") if p)


def hardlinked(wt: "str | Path", paths: Iterable[str]) -> "list[str]":
    """Tracked paths whose file has st_nlink > 1 (writes would reach another inode)."""
    bad = []
    for rel in paths:
        try:
            st = os.lstat(os.path.join(str(wt), rel))
        except OSError:
            continue
        if st.st_nlink > 1 and not os.path.islink(os.path.join(str(wt), rel)):
            bad.append(rel)
    return bad


def _snapshot(wt, parent: str, tree: str, msg: str) -> str:
    return _out(wt, "commit-tree", tree, "-p", parent, "-m", msg).strip()


# ── review snapshots and test overlays (deterministic convergence §4) ────────
SNAPSHOT_REF_PREFIX = "refs/swarm/snapshots/"
_LABEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
# Overlay commits are content-addressed: a fixed identity and date make the same base
# plus the same file bytes give the same commit, so a checkout of it is reusable.
_FIXED_ID = {"GIT_AUTHOR_NAME": "swarm-overlay", "GIT_AUTHOR_EMAIL": "overlay@swarm.local",
             "GIT_COMMITTER_NAME": "swarm-overlay", "GIT_COMMITTER_EMAIL": "overlay@swarm.local",
             "GIT_AUTHOR_DATE": "1970-01-01T00:00:00+0000",
             "GIT_COMMITTER_DATE": "1970-01-01T00:00:00+0000"}


def snapshot_ref(run_id: str, label: str) -> str:
    _check_ids(run_id)
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
        raise WorktreeError("bad_label", f"snapshot label {label!r} is not [A-Za-z0-9_-]{{1,32}}")
    return f"{SNAPSHOT_REF_PREFIX}{run_id}/{label}"


def snapshot_commit(wt: "str | Path", run_id: str, label: str) -> dict:
    """Freeze the run's change set ``T`` into a commit whose parent is the worktree's
    HEAD, and point ``refs/swarm/snapshots/<run_id>/<label>`` at it. Read-only for the
    worktree: ``change_tree`` works on a private index copy, so the worktree's index,
    files and branch are untouched. Untracked files are not part of ``T`` (§6.2).
    Returns ``{"sha", "tree", "base", "ref"}``."""
    ref = snapshot_ref(run_id, label)
    head = _rev(wt, "HEAD")
    tree = change_tree(wt, run_id)
    sha = _out(wt, "commit-tree", tree, "-p", head, "-m",
               f"swarm snapshot {run_id} {label}").strip()
    _out(wt, "update-ref", ref, sha)
    return {"sha": sha, "tree": tree, "base": head, "ref": ref}


def change_patch_id(wt: "str | Path", base: str, tree: str) -> str:
    """The stable patch-id of the change *base* -> *tree* ("" for no change). Two change
    sets with the same patch-id carry the same edits, also on different bases: a rebase
    that applied cleanly keeps it, any edit to the run's own change moves it, whitespace
    (Python indentation) and binary content included (``--verbatim``, ``--binary``)."""
    diff = _out(wt, "diff-tree", "-p", "--binary", "--no-renames", "--full-index", base, tree,
                timeout=120)
    if not diff.strip():
        return ""
    out = _out(wt, "patch-id", "--verbatim", input_text=diff).split()
    return out[0] if out else ""


def drop_snapshots(cwd: "str | Path", run_id: str) -> "list[str]":
    """Delete every ``refs/swarm/snapshots/<run_id>/*`` ref; returns the deleted refs."""
    _check_ids(run_id)
    refs = [r for r in _out(cwd, "for-each-ref", "--format=%(refname)",
                            f"{SNAPSHOT_REF_PREFIX}{run_id}/").split("\n") if r]
    for r in refs:
        git(cwd, "update-ref", "-d", r)
    return refs


def overlay_commit(wt: "str | Path", base_sha: str, paths: "Iterable[str]") -> dict:
    """A commit of *base_sha*'s tree with each of *paths* replaced by the worktree's
    current file (a path missing from the worktree is removed). Built on a private
    index, so the worktree is never touched. Content-addressed (``_FIXED_ID``): the
    same base and the same bytes give the same sha. When nothing differs from the base
    the base itself is returned. Returns ``{"sha", "tree", "blobs": {path: blob|None}}``."""
    root = os.path.realpath(str(wt))
    fd, tmp = tempfile.mkstemp(prefix="swarm-ovl-")
    os.close(fd)
    os.unlink(tmp)  # read-tree writes a fresh index here
    env = {"GIT_INDEX_FILE": tmp}
    try:
        _out(wt, "read-tree", base_sha, env=env)
        blobs: dict = {}
        for rel in sorted(set(paths)):
            if not isinstance(rel, str) or not rel or os.path.isabs(rel) \
                    or ".." in Path(rel).parts:
                raise WorktreeError("bad_path", f"overlay path {rel!r} is not repo-relative")
            p = os.path.join(root, rel)
            if not os.path.realpath(p).startswith(root + os.sep):
                raise WorktreeError("bad_path", f"overlay path {rel!r} leaves the worktree")
            if os.path.isfile(p) and not os.path.islink(p):
                blob = _out(wt, "hash-object", "-w", "--", rel).strip()
                mode = "100755" if os.access(p, os.X_OK) else "100644"
                _out(wt, "update-index", "--add", "--cacheinfo", f"{mode},{blob},{rel}", env=env)
                blobs[rel] = blob
            else:
                _out(wt, "update-index", "--force-remove", "--", rel, env=env)
                blobs[rel] = None
        tree = _out(wt, "write-tree", env=env).strip()
        if tree == _out(wt, "rev-parse", base_sha + "^{tree}").strip():
            return {"sha": base_sha, "tree": tree, "blobs": blobs}
        sha = _out(wt, "commit-tree", tree, "-p", base_sha, "-m", "swarm overlay",
                   env={**env, **_FIXED_ID}).strip()
        return {"sha": sha, "tree": tree, "blobs": blobs}
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _patch_id(wt, a: str, b: str, run_id: str) -> str:
    diff = _out(wt, "diff", "--binary", "--no-renames", a, b, "--", ".",
                f":(exclude){deliverable_prefix(run_id).rstrip('/')}", timeout=120)
    if not diff.strip():
        return ""
    return _out(wt, "patch-id", "--stable", input_text=diff).split(" ")[0].strip()


def carries_over(wt: "str | Path", run_id: str, old_base: str, old_tree: str,
                 new_base: str, new_tree: str) -> bool:
    """A review or test of the change (old_base → old_tree) still holds for
    (new_base → new_tree): every path that differs between the two trees was changed by
    main's commits since old_base, and none of those is a path the run changed — so
    each of the run's files is byte-identical in both trees. Main moves every few
    minutes; without this every rebase voided the review and the tests and the landing
    retry rebased again, a loop that never landed. Compared by blob, never by
    ``git patch-id`` (it ignores whitespace, so a re-indent would pass). False on doubt."""
    if not all((old_base, old_tree, new_base, new_tree)):
        return False
    if (old_base, old_tree) == (new_base, new_tree):
        return True
    try:
        moved = {p for p in _out(wt, "diff", "--name-only", "--no-renames", "-z",
                                 old_base, new_base).split("\0") if p}
        differs = set(changed_paths(wt, old_tree, new_tree))
        mine = set(changed_paths(wt, old_base, old_tree)) | set(changed_paths(wt, new_base, new_tree))
    except WorktreeError:
        return False
    return differs <= moved and not (moved & mine)


# ── sync (deliver stage-start, §7.2) ─────────────────────────────────────────
def _unmerged(wt) -> "list[str]":
    out = _out(wt, "diff", "--name-only", "--diff-filter=U", "-z")
    return sorted(p for p in out.split("\0") if p)


def _rebase_onto(wt) -> "str | None":
    for d in ("rebase-merge", "rebase-apply"):
        f = _git_path(wt, d) / "onto"
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    return None


def sync(run_id: str, entry: dict, main: "str | Path") -> dict:
    """Rebase the run's change set onto the integration branch. Mutates *entry*
    (base / pre_sync_tip) on success. Returns {"status": "noop"|"rebased", "base",
    "patch_changed"} or raises WorktreeError: 'conflict' (rebase left in progress,
    ``unmerged`` listed), 'untracked_collision', 'worktree_invalid'."""
    wt = validate(run_id, entry, main)
    old_base = entry["base"]
    onto = _rebase_onto(wt)
    restore_invariant(run_id, entry, wt)
    if onto:                                   # re-entry after the model resolved conflicts
        left = _unmerged(wt)
        if left:
            raise WorktreeError("conflict", "rebase conflicts are not resolved yet",
                                unmerged=left, worktree=str(wt))
        p = git(wt, "rebase", "--continue", timeout=300)
        if p.returncode != 0:
            left = _unmerged(wt)
            if left or _rebase_onto(wt):
                raise WorktreeError("conflict", "the next commit of the rebase conflicts",
                                    unmerged=left, worktree=str(wt))
            raise WorktreeError("git_failed", (p.stderr or p.stdout).strip()[:300])
        return _finish_rebase(run_id, entry, wt, onto, old_base)
    target = _rev(main, entry["integration_branch"])
    if target == old_base:
        return {"status": "noop", "base": old_base, "patch_changed": False}
    if _symbolic_head(wt) != branch_ref(run_id) or _rev(wt, "HEAD") != old_base:
        raise WorktreeError("worktree_invalid", "worktree HEAD is not the run's base "
                            "(was something committed by hand?)")
    if git(wt, "merge-base", "--is-ancestor", old_base, target).returncode != 0:
        raise WorktreeError("worktree_invalid", "base is not an ancestor of the integration "
                            "branch (was it rewritten?)")
    tree = change_tree(wt, run_id)
    snap = _snapshot(wt, old_base, tree, f"swarm: pre-sync snapshot of {run_id}")
    entry["pre_sync_tip"] = snap
    _out(wt, "reset", "-q", "--mixed", snap)
    try:
        p = git(wt, "rebase", "-q", target, timeout=300)
    except WorktreeError:
        git(wt, "rebase", "--abort")
        git(wt, "reset", "-q", "--soft", old_base)   # back to the invariant
        raise
    if p.returncode != 0:
        err = (p.stderr or p.stdout)
        if _rebase_onto(wt):
            raise WorktreeError("conflict", "rebase onto the latest integration branch "
                                "conflicts", unmerged=_unmerged(wt), worktree=str(wt))
        git(wt, "reset", "-q", "--soft", old_base)   # back to the invariant
        if "untracked working tree files would be overwritten" in err:
            raise WorktreeError("untracked_collision", "un-added files collide with files the "
                                "integration branch added — git add or delete them",
                                files=_overwritten_list(err))
        raise WorktreeError("git_failed", err.strip()[:300])
    return _finish_rebase(run_id, entry, wt, target, old_base)


def restore_invariant(run_id: str, entry: dict, wt: Path) -> bool:
    """Re-enter cleanly after an interrupted sync (killed, timed out, or the model ran
    ``git rebase --abort``): HEAD left on the pre-sync snapshot S (parent = base) with no
    rebase in progress → ``reset --soft base``. The change itself is untouched (S's tree
    is the working state). Returns True when it repaired something."""
    snap = entry.get("pre_sync_tip")
    base = entry.get("base")
    if not snap or not base or _rebase_onto(wt):
        return False
    try:
        head = _rev(wt, "HEAD")
        if head != snap or _rev(wt, f"{snap}^") != base \
                or _symbolic_head(wt) != branch_ref(run_id):
            return False
    except WorktreeError:
        return False
    _out(wt, "reset", "-q", "--soft", base)
    logger.warning("run %s: recovered an interrupted sync (HEAD back to base)", run_id)
    return True


def landed_already(run_id: str, entry: dict, main: "str | Path") -> "str | None":
    """The final commit N when a landing merged into main but run.json was never
    updated (crash between merge and record): HEAD == N, N^ == base, N reachable from
    the integration branch. None otherwise."""
    try:
        wt = wt_path(run_id, entry["repo_name"])
        head = _rev(wt, "HEAD")
        if head == entry.get("base") or _rev(wt, "HEAD^") != entry.get("base"):
            return None
        if git(main, "merge-base", "--is-ancestor", head,
               entry["integration_branch"]).returncode == 0:
            return head
    except (WorktreeError, KeyError):
        return None
    return None


def _overwritten_list(err: str) -> "list[str]":
    lines = err.splitlines()
    out, on = [], False
    for ln in lines:
        if "would be overwritten" in ln:
            on = True
            continue
        if on:
            if not ln.startswith(("\t", " ")) or not ln.strip():
                break
            out.append(ln.strip())
    return out


def _finish_rebase(run_id: str, entry: dict, wt: Path, onto: str, old_base: str) -> dict:
    _out(wt, "reset", "-q", "--soft", onto)
    entry["base"] = onto
    new_tree = change_tree(wt, run_id)
    snap = entry.get("pre_sync_tip")
    changed = bool(snap) and _patch_id(wt, old_base, snap, run_id) != _patch_id(wt, onto, new_tree, run_id)
    return {"status": "rebased", "base": onto, "patch_changed": changed,
            "pre_sync_tip": snap}


def range_diff(wt: "str | Path", old_base: str, snap: str, new_base: str, run_id: str) -> str:
    """`git range-diff` of the run's change before and after the sync (re-review input)."""
    new_snap = _snapshot(wt, new_base, change_tree(wt, run_id), "swarm: post-sync snapshot")
    p = git(wt, "range-diff", f"{old_base}..{snap}", f"{new_base}..{new_snap}", timeout=120)
    return p.stdout if p.returncode == 0 else ""


# ── landing (deliver stage-complete, §7.4) ───────────────────────────────────
def path_check(wt: "str | Path", base: str, tree: str) -> "list[str]":
    """Paths the landing refuses (§7.4 step 1): gitlinks, .gitmodules, symlinks that are
    absolute or leave the repo, .gitattributes filter/diff/merge drivers, .git variants."""
    bad: list[str] = []
    raw = _out(wt, "diff-tree", "-r", "--no-renames", "-z", base, tree)
    fields = raw.split("\0")
    i = 0
    while i + 1 < len(fields):
        meta, rel = fields[i], fields[i + 1]
        i += 2
        if not meta.startswith(":"):
            continue
        new_mode = meta[1:].split(" ")[1] if len(meta[1:].split(" ")) > 1 else ""
        parts = rel.split("/")
        if new_mode == "160000":
            bad.append(f"{rel}: submodule (gitlink) changes are not landed")
        elif rel == ".gitmodules":
            bad.append(".gitmodules: submodule config changes are not landed")
        elif any(p.lower() in (".git",) or p.lower().startswith(".git~") for p in parts):
            bad.append(f"{rel}: a .git path component")
        elif new_mode == "120000":
            target = _out(wt, "cat-file", "-p", f"{tree}:{rel}").strip()
            dest = os.path.normpath(os.path.join(os.path.dirname(rel), target))
            if os.path.isabs(target) or dest.startswith(".."):
                bad.append(f"{rel}: symlink pointing outside the repo ({target})")
        elif parts[-1] == ".gitattributes":
            text = git(wt, "cat-file", "-p", f"{tree}:{rel}").stdout
            if re.search(r"\b(filter|diff|merge)=", text):
                bad.append(f"{rel}: declares a filter/diff/merge driver — needs a human review")
    return bad


def _integrate_lock(entry: dict):
    """One lock per target REPO (its git common dir), whatever key each run gave it."""
    from core.run_lock import run_lock
    common = str(entry.get("git_common_dir") or entry.get("repo_name"))
    d = wt_root() / ".integrate" / hashlib.sha256(common.encode()).hexdigest()[:16]
    return run_lock(d, timeout=INTEGRATE_LOCK_TIMEOUT_S)


def _blob(main: Path, rev: str, rel: str) -> "bytes | None":
    """Bytes of *rel* at *rev* ("" = the index), None when it is not there. cat-file, not
    show: ``git show HEAD:app/[i].py`` for a path absent at HEAD falls back to treating the
    argument as a pathspec glob and exits 0 with no output, so a missing file read as
    empty bytes."""
    p = subprocess.run(["git", "-C", str(main), "cat-file", "blob", f"{rev}:{rel}"],
                       capture_output=True,
                       timeout=60, env={**os.environ, **_GIT_ENV})
    return p.stdout if p.returncode == 0 else None


_LAST_CARRY_BACKUP: "dict[str, str]" = {}


def _three_way(ours: bytes, base: bytes, theirs: bytes) -> "bytes | None":
    """The one 3-way rule landing (_dirty_merge_plan) and claiming (claim_main) share:
    ``git merge-file`` of *ours* and *theirs* against *base* → the merged bytes, or None
    for a binary input or a conflict."""
    if b"\0" in ours or b"\0" in base or b"\0" in theirs:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="swarm-3way-"))
    try:
        for name, data in (("ours", ours), ("base", base), ("theirs", theirs)):
            (tmp / name).write_bytes(data)
        r = subprocess.run(["git", "merge-file", "-p", str(tmp / "ours"), str(tmp / "base"),
                            str(tmp / "theirs")], capture_output=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return r.stdout if r.returncode == 0 else None


def _dirty_merge_plan(main: Path, base: str, n: str, files: "list[str]",
                      run_id: str) -> "list[tuple[str, bytes, bytes, bytes, bytes]]":
    """Uncommitted edits in the main checkout (another session's) to files this run
    changes: ``git merge --ff-only`` refuses them, which blocked every landing while
    any session worked in main. Plan instead to carry them over: per file
    (rel, their bytes, base bytes, 3-way merge of theirs + the run's change, the run's bytes). Raises
    WorktreeError('blocked_by_uncommitted') — touching nothing — when an edit is
    staged, binary, a deletion, or does not merge cleanly."""
    unstaged = {p for p in _out(main, "diff", "--name-only", "-z").split("\0") if p}
    staged = {p for p in _out(main, "diff", "--cached", "--name-only", "-z").split("\0") if p}
    hit = sorted((unstaged | staged) & set(files))
    if not hit:
        return []
    plan, bad = [], []
    for rel in hit:
        cur_p = main / rel
        base_b, new_b = _blob(main, base, rel), _blob(main, n, rel)
        if (rel in staged or base_b is None or new_b is None or not cur_p.is_file()
                or cur_p.is_symlink()):
            bad.append(rel)
            continue
        cur_b = cur_p.read_bytes()
        merged = _three_way(cur_b, base_b, new_b)
        if merged is None:
            bad.append(rel)
            continue
        plan.append((rel, cur_b, base_b, merged, new_b))
    if bad:
        raise WorktreeError("blocked_by_uncommitted", "uncommitted changes in the main "
                            "checkout overlap this run's files and cannot be merged "
                            "automatically", files=bad)
    # their bytes go to disk first: nothing below may lose them (STEERING #20)
    keep = ledger_root() / run_id / "main-uncommitted" / time.strftime("%Y%m%dT%H%M%S")
    for rel, cur_b, *_rest in plan:
        dst = keep / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(cur_b)
    _LAST_CARRY_BACKUP[run_id] = str(keep)
    return plan


def _replace_bytes(path: Path, data: bytes) -> None:
    """Write *data* over *path* atomically (temp file + rename), keeping its mode."""
    tmp = path.with_name(f".{path.name}.swarm-{os.getpid()}.tmp")
    tmp.write_bytes(data)
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)


def _merge_carrying(main: Path, n: str, plan) -> subprocess.CompletedProcess:
    """``git merge --ff-only n`` with the planned uncommitted files carried over: each
    is set back to base (refused if it changed since planned), the merge runs, then
    each gets the 3-way result. If the merge fails or raises, their bytes go back
    (whether git left the file at base or already at the run's version). A file a third
    writer changed in between is left as that writer left it; their planned bytes are
    in the ledger copy either way."""
    reset: "list[tuple[str, bytes, bytes, bytes, bytes]]" = []
    m = None
    try:
        for item in plan:
            rel, cur_b, base_b, _m, _n = item
            if (main / rel).read_bytes() != cur_b:
                raise WorktreeError("blocked_by_uncommitted", "an uncommitted file in the "
                                    "main checkout changed while landing", files=[rel])
            _replace_bytes(main / rel, base_b)
            reset.append(item)
        m = git(main, "merge", "-q", "--ff-only", n, timeout=120)
    finally:
        ok = m is not None and m.returncode == 0
        for rel, cur_b, base_b, merged, new_b in reset:
            p = main / rel
            now = p.read_bytes() if p.is_file() else None
            if ok and now == new_b:
                _replace_bytes(p, merged)          # their edit + the run's change
            elif not ok and now in (base_b, new_b):
                _replace_bytes(p, cur_b)           # nothing landed: exactly what they had
    return m


def _main_busy(main: Path, entry: dict) -> "str | None":
    if _symbolic_head(main) != entry.get("integration_branch"):
        return "main checkout is not on the integration branch"
    b = busy_state(main)
    return f"main checkout has {b} in progress" if b else None


def land(run_id: str, entry: dict, main: "str | Path", message: str, expect_tree: str,
         write_b: "Callable[[Path], list] | None" = None,
         still_live: "Callable[[], bool] | None" = None) -> dict:
    """Final commit N in the worktree (parent C == base, full git hooks) + native
    ``git merge --ff-only N`` in the main checkout. Uncommitted edits in main to the
    run's files (another session's) are carried over by a clean 3-way merge (backed up
    to the ledger first), else the landing is refused (_dirty_merge_plan). On ANY
    failure the worktree goes back to HEAD == base, main's HEAD is unchanged and those
    edits are back as they were. Mutates *entry* on success (state integrated). Returns
    {"sha", "files", "commit_s", "merge_s", "carried_uncommitted", "carried_backup"};
    raises WorktreeError
    with code changed_since_review | needs_resync | main_checkout_busy |
    blocked_by_uncommitted | busy | commit_slow | path_rejected | untracked_new_files."""
    main = Path(os.path.realpath(str(main)))
    wt = validate(run_id, entry, main)
    base = entry["base"]
    if _rebase_onto(wt) or _unmerged(wt):
        raise WorktreeError("conflict", "a rebase is in progress — rerun deliver stage-start")
    if _rev(wt, "HEAD") != base:
        raise WorktreeError("worktree_invalid", "worktree HEAD is not the run's base")
    left = untracked_new_files(wt, run_id)
    if left:
        raise WorktreeError("untracked_new_files", "un-added new files in the worktree",
                            files=left, worktree=str(wt))
    tree = change_tree(wt, run_id)
    if tree != expect_tree:
        raise WorktreeError("changed_since_review", "the change differs from the tree that "
                            "was tested and reviewed")
    if _rev(main, entry["integration_branch"]) != base:
        raise WorktreeError("needs_resync", "the integration branch moved since the last sync")
    why = _main_busy(main, entry)
    if why:
        raise WorktreeError("main_checkout_busy", why)
    bad = path_check(wt, base, tree)
    if bad:
        raise WorktreeError("path_rejected", "landing refused for some paths", paths=bad)
    files = changed_paths(wt, base, tree)
    if not files:
        raise WorktreeError("nothing_to_land", "the run changed nothing in this repo")
    try:
        b_paths = list(write_b(wt) or []) if write_b else []
        _out(wt, "add", "-u")
        if b_paths:
            _out(wt, "add", "--", *b_paths)
        t0 = time.monotonic()
        try:
            p = git(wt, "commit", "-q", "-m", message, timeout=COMMIT_TIMEOUT_S)
        except WorktreeError as exc:
            if exc.code == "git_timeout":
                raise WorktreeError("commit_slow", f"the final commit took over "
                                    f"{COMMIT_TIMEOUT_S:.0f}s and was stopped") from exc
            raise
        commit_s = round(time.monotonic() - t0, 2)
        if p.returncode != 0:
            raise WorktreeError("commit_failed", (p.stderr or p.stdout).strip()[:400])
        n = _rev(wt, "HEAD")
        if _rev(wt, "HEAD^") != base:
            raise WorktreeError("worktree_invalid", "final commit's parent is not the base")
        if still_live is not None and not still_live():
            raise WorktreeError("run_not_live", "the run was cancelled before landing")
        from core.run_lock import RunLockTimeout
        carried: "list[str]" = []
        carried_backup = None
        try:
            with _integrate_lock(entry):
                plan = _dirty_merge_plan(main, base, n, files, run_id)
                t1 = time.monotonic()
                m = _merge_carrying(main, n, plan)
                merge_s = round(time.monotonic() - t1, 2)
                carried = [rel for rel, *_ in plan] if m.returncode == 0 else []
                carried_backup = _LAST_CARRY_BACKUP.pop(run_id, None) if carried else None
        except RunLockTimeout as exc:
            raise WorktreeError("busy", "another run is landing into this repo") from exc
        if m.returncode != 0:
            err = (m.stderr or m.stdout)
            if "would be overwritten" in err:
                raise WorktreeError("blocked_by_uncommitted", "uncommitted changes in the main "
                                    "checkout overlap this run's files",
                                    files=_overwritten_list(err) or None)
            if "Not possible to fast-forward" in err or "not possible to fast-forward" in err:
                raise WorktreeError("needs_resync", "the integration branch moved")
            if "index.lock" in err:
                raise WorktreeError("busy", "the main checkout's index is locked")
            raise WorktreeError("merge_failed", err.strip()[:400])
    except BaseException:
        git(wt, "reset", "-q", "--soft", base)
        raise
    entry.update(state="integrated", integrated={"sha": n, "at": _now_iso()}, base=n)
    return {"sha": n, "files": files + [p for p in b_paths if p not in files],
            "commit_s": commit_s, "merge_s": merge_s, "carried_uncommitted": carried,
            "carried_backup": carried_backup}


# ── claiming main-checkout edits into the run (run_3b9b1515) ──────────────────
# The worktree design leaves every uncommitted main edit in main as "another
# session's" (create), and landing refuses one that overlaps the run and does not
# merge. An edit the run's OWN session left there had no way out: the write gate
# denies that session git writes and Edit/Write in main. claim_main is the one
# sanctioned move, run only as ``run-claim-main`` after the user approved it.
CLAIM_RESOLVES = ("merge", "keep-worktree", "take-main")


def main_dirty(main: "str | Path") -> "list[dict]":
    """Every uncommitted change in the main checkout: {path, status, staged}; status
    modified | added | deleted | untracked | conflicted (renames as their two paths,
    ignored files left out). Raises WorktreeError when git fails."""
    # GIT_OPTIONAL_LOCKS=0: a status here runs after every Bash call (main_dirty_scan);
    # its index refresh must never hold main's index.lock against another writer
    p = git(main, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames",
            timeout=20, env={"GIT_OPTIONAL_LOCKS": "0"})
    if p.returncode != 0:
        raise WorktreeError("git_failed", (p.stderr or p.stdout).strip()[:300])
    out = []
    for rec in p.stdout.split("\0"):
        if len(rec) < 4:
            continue
        x, y, path = rec[0], rec[1], rec[3:]
        if x == "?":
            out.append({"path": path, "status": "untracked", "staged": False})
            continue
        if "U" in (x, y) or (x, y) in (("A", "A"), ("D", "D")):
            status = "conflicted"
        elif "D" in (x, y):
            status = "deleted"
        elif "A" in (x, y):
            status = "added"     # y == "A": intent-to-add (`git add -N`), nothing staged
        else:
            status = "modified"
        out.append({"path": path, "status": status, "staged": x not in (" ", "?")})
    return out


def claim_args(project: str, run_id: str, files: "Iterable[str]",
               resolve: "str | None" = None) -> str:
    """The ``run-claim-main`` arguments (after the CLI prefix): the one builder every
    hint uses (pipeline_worktree.claim_command, main_dirty_scan)."""
    import shlex
    names = " ".join(shlex.quote(str(f)) for f in files) or "<path ...>"
    tail = f" --resolve {resolve}" if resolve else ""
    # run_id may come from a run.json any session can write: quote it like every other
    # value, so a hint an agent copies can never carry a shell command (the placeholder
    # stays bare)
    rid = run_id if run_id == "<run_id>" else shlex.quote(str(run_id))
    return (f"run-claim-main --project {shlex.quote(str(project or '<project>'))} "
            f"--run-id {rid} --files {names}{tail}")


def _abs_claim_path(f: str) -> str:
    """An absolute --files path with its PARENT resolved (as _claim_rel does), so a
    symlinked file keeps its own name and is refused as 'a symlink', not misrouted."""
    return os.path.join(os.path.realpath(os.path.dirname(f)), os.path.basename(f))


def plan_claim(run_id: str, active: "list[tuple[dict, Path]]", files: "Iterable[str]",
               resolve: str = "merge") -> "list[tuple[dict, Path, list[str]]]":
    """Route each --files path to the active worktree entry whose main checkout holds it
    and dry-run every group (claim_main dry_run): [(entry, main, paths)]. Raises
    WorktreeError('claim_refused', files=…) for a path in none of the repos, a relative
    path in a run with several repos, or any refusal of claim_main — before anything is
    written. The one planner of both the approval hook (run_owner_gate, before the user
    is asked) and the CLI (cmd_run_claim_main, before it moves files)."""
    files = list(files)
    rel = [f for f in files if not os.path.isabs(f)]
    if rel and len(active) > 1:
        raise WorktreeError("claim_refused", f"this run has worktrees for {len(active)} "
                            "repos, so a relative path does not say which main checkout it "
                            "is in; give absolute paths. Nothing was claimed", files=rel,
                            relative=True)
    groups: "dict[int, list[str]]" = {}
    for f in files:
        i = 0
        if os.path.isabs(f):
            full = _abs_claim_path(f)
            hit = [(len(os.path.realpath(str(m))), k) for k, (_e, m) in enumerate(active)
                   if full.startswith(os.path.realpath(str(m)) + os.sep)]
            if not hit:
                raise WorktreeError("claim_refused", f"{f} is in none of this run's repos; "
                                    "nothing was claimed", files=[f])
            i = max(hit)[1]   # the innermost repo holding it (nested repos)
        groups.setdefault(i, []).append(f)
    out = []
    for i, paths in groups.items():
        entry, main = active[i]
        claim_main(run_id, entry, main, paths, resolve=resolve, dry_run=True)
        out.append((entry, main, paths))
    return out


def _claim_rel(main: Path, p: str) -> "str | None":
    """Repo-relative form of a --files path (absolute, or relative to main); None when
    it is outside the repo, the repo root itself, or under .git."""
    q = Path(p)
    full = q if q.is_absolute() else main / q
    rel = os.path.relpath(os.path.join(os.path.realpath(str(full.parent)), full.name),
                          str(main))
    if rel in (".", "") or rel.startswith(".." + os.sep) or rel == ".." or os.path.isabs(rel):
        return None
    rel = rel.replace(os.sep, "/")
    return None if rel.split("/")[0] == ".git" else rel


def _read(p: Path) -> "bytes | None":
    return p.read_bytes() if p.is_file() and not p.is_symlink() else None


# A claimed path is a file name, never a pattern: `app/[id]/x.tsx` must not also match
# `app/i/x.tsx` (git() drops an inherited GIT_LITERAL_PATHSPECS, so pass the flag).
LITERAL_PATHSPECS = "--literal-pathspecs"   # also scripts/consumers_gate._git
_LIT = LITERAL_PATHSPECS


def _index_entry(cwd, rel: str) -> str:
    return _out(cwd, _LIT, "ls-files", "-s", "-z", "--", rel).strip("\0")


def _exec_bit(p: Path) -> "bool | None":
    """Whether a regular file is executable (None: no file)."""
    try:
        return bool(os.lstat(p).st_mode & 0o100) if p.is_file() else None
    except OSError:
        return None


def _set_exec_bit(p: Path, on: "bool | None") -> None:
    if on is None or not p.is_file() or _exec_bit(p) == on:
        return
    m = os.lstat(p).st_mode & 0o7777
    os.chmod(p, (m | ((m & 0o444) >> 2)) if on else (m & ~0o111))


def _head_exec(main: Path, rel: str) -> "bool | None":
    line = _out(main, _LIT, "ls-tree", "HEAD", "--", rel).strip()
    return line.split(" ", 1)[0] == "100755" if line else None


def _write_worktree_file(wt: Path, rel: str, data: "bytes | None", tracked: bool,
                         exec_bit: "bool | None") -> None:
    """Put *data* at wt/rel with *exec_bit* (None data: delete it); a path new to the
    worktree is git-added (the landing refuses un-added new files)."""
    p = wt / rel
    if data is None:
        if p.is_file():
            p.unlink()
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    _replace_bytes(p, data) if p.exists() else p.write_bytes(data)
    _set_exec_bit(p, exec_bit)
    if not tracked:
        _out(wt, _LIT, "add", "--", rel)


def _undo_worktree_file(wt: Path, rel: str, prior: "bytes | None", tracked: bool,
                        exec_bit: "bool | None") -> None:
    """Put wt/rel back as it was: prior bytes and exec bit, and out of the index again
    when it was not there (_write_worktree_file git-added it)."""
    p = wt / rel
    if not tracked:
        git(wt, _LIT, "rm", "-q", "--cached", "--ignore-unmatch", "--", rel)
    if prior is None:
        if p.is_file():
            p.unlink()
    else:
        p.parent.mkdir(parents=True, exist_ok=True)
        _replace_bytes(p, prior) if p.exists() else p.write_bytes(prior)
        _set_exec_bit(p, exec_bit)


def _restore_main_file(main: Path, rel: str, in_head: bool) -> None:
    """Main's rel back to HEAD: tracked → ``checkout HEAD --`` (file, mode + index); new
    → out of the index (staged, or an intent-to-add entry), file removed."""
    if in_head:
        _out(main, _LIT, "checkout", "HEAD", "--", rel)
        return
    _out(main, _LIT, "rm", "-q", "--cached", "--ignore-unmatch", "--", rel)
    p = main / rel
    if p.is_file() or p.is_symlink():
        p.unlink()


def claim_main(run_id: str, entry: dict, main: "str | Path", paths: "Iterable[str]",
               resolve: str = "merge", dry_run: bool = False) -> dict:
    """Move the listed uncommitted main-checkout edits into the run's worktree.

    Plan first, for every file (all-or-nothing: any refusal raises
    WorktreeError('claim_refused', files=…) touching nothing). Target bytes for the
    worktree, per *resolve* (it applies to EVERY listed file): keep-worktree → the
    worktree's (main's edit is dropped from main, backed up); take-main → main's; merge
    → already equal to main's: unchanged; a file the run did not change (its bytes are
    the run's base, ``entry["base"]``, or main's HEAD) → main's, also when main HEAD has
    moved past the base since the worktree was made; a file the run changed →
    ``_three_way`` with main's HEAD as the edit's ancestor, or refused. The executable
    bit moves with the edit (main's when main changed it, else the worktree's;
    keep-worktree keeps the worktree's), so a mode-only change is not lost.
    *dry_run*: plan only (refusals raise as above), write nothing and take no lock,
    return {"planned": [...]} (plan_claim). Then: back up main's bytes AND the worktree's
    prior bytes to ``<ledger>/<run>/main-claimed/<ts>/`` (+ manifest.json), write the
    worktree, and restore main to HEAD only when its bytes and index entry still equal
    what was read — otherwise the worktree file goes back to its prior bytes and the
    file is listed in ``main_left`` (a file is moved whole or not at all). The compare
    and the restore are two steps: a write landing in between is the accepted gap (other
    sessions take no lock). Rerunnable after a kill: an already-moved file plans as
    "worktree unchanged" and only main is restored. Held under the repo's integrate lock.
    Returns {"claimed", "main_left", "backup", "worktree_changed"}: worktree_changed =
    the claimed files whose worktree bytes or mode changed (code the review has not
    seen)."""
    if resolve not in CLAIM_RESOLVES:
        raise WorktreeError("claim_refused", f"--resolve must be one of {CLAIM_RESOLVES}")
    main = Path(os.path.realpath(str(main)))
    wt = validate(run_id, entry, main)
    why = _main_busy(main, entry)
    if why:
        raise WorktreeError("main_checkout_busy", why)
    if dry_run:   # reads only: no lock, so a planning hook never waits on a landing
        return _claim_locked(run_id, main, wt, list(paths), resolve,
                             str(entry.get("base") or "HEAD"), dry_run=True)
    from core.run_lock import RunLockTimeout
    try:
        with _integrate_lock(entry):
            return _claim_locked(run_id, main, wt, list(paths), resolve,
                                 str(entry.get("base") or "HEAD"))
    except RunLockTimeout as exc:
        raise WorktreeError("busy", "a run is landing into this repo") from exc


def _claim_locked(run_id: str, main: Path, wt: Path, paths: "list[str]", resolve: str,
                  base: str, dry_run: bool = False) -> dict:
    dirty = {d["path"]: d for d in main_dirty(main)}
    tracked_wt = set(p for p in _out(wt, "ls-files", "-z").split("\0") if p)
    plan, bad, reasons = [], [], {}
    seen: set = set()
    for raw in paths:
        rel = _claim_rel(main, raw)
        key = rel or raw
        if key in seen:
            continue
        seen.add(key)
        d = dirty.get(rel) if rel else None
        mp = main / rel if rel else None
        if rel is None:
            reasons[raw] = "outside the repo or under .git"
        elif d is None:
            reasons[rel] = "no uncommitted change in the main checkout"
        elif d["status"] == "conflicted":
            reasons[rel] = "unmerged in the main checkout"
        elif mp.is_symlink() or (wt / rel).is_symlink() or not _no_symlinks(mp.parent) \
                or not _no_symlinks((wt / rel).parent):
            reasons[rel] = "a symlink"
        elif mp.exists() and not mp.is_file():
            reasons[rel] = "not a regular file"
        if key in reasons:
            bad.append(key)
            continue
        head_b = _blob(main, "HEAD", rel)
        cur_b = _read(mp)
        idx_b = None
        if d["staged"] and _index_entry(main, rel):
            idx_b = _blob(main, "", rel)
            # one version moves (the file's): with plain merge the index must hold the
            # same bytes; under an explicit --resolve the staged version is dropped from
            # main and kept in the backup (index/), so a partial stage cannot deadlock
            if idx_b != cur_b and resolve == "merge":
                reasons[rel] = ("partially staged (the index and the file differ): rerun "
                                "with --resolve take-main (the file's bytes move; the staged "
                                "version is kept in the backup) or --resolve keep-worktree")
                bad.append(rel)
                continue
            if idx_b == cur_b:
                idx_b = None   # nothing beyond the file to back up
        wt_b = _read(wt / rel)
        cur_x, wt_x = _exec_bit(mp), _exec_bit(wt / rel)
        if resolve == "keep-worktree":
            target_x = wt_x
        elif resolve == "take-main" or cur_x != _head_exec(main, rel):
            target_x = cur_x
        else:
            target_x = wt_x
        if resolve == "keep-worktree" or wt_b == cur_b:
            target = wt_b
        elif resolve == "take-main" or wt_b == head_b or wt_b == _blob(main, base, rel):
            target = cur_b   # chosen, or a file the run did not change
        else:
            target = _three_way(wt_b, head_b, cur_b) if None not in (wt_b, head_b, cur_b) else None
            if target is None:
                reasons[rel] = ("does not merge with the run's change: rerun with --resolve "
                                "keep-worktree (keep the run's version, drop main's edit) or "
                                "--resolve take-main (main's version replaces the run's)")
                bad.append(rel)
                continue
        if target is None:
            target_x = None
        plan.append({"rel": rel, "cur": cur_b, "head": head_b, "wt": wt_b, "target": target,
                     "idx": idx_b,
                     "staged": d["staged"], "index": _index_entry(main, rel),
                     "tracked": rel in tracked_wt, "status": d["status"],
                     "wt_x": wt_x, "target_x": target_x, "cur_x": cur_x,
                     "moves": target != wt_b or (target is not None and target_x != wt_x)})
    if bad:
        detail = "; ".join(f"{f}: {reasons.get(f, '?')}" for f in bad)
        hint = (" (a file that does not merge can be claimed with --resolve keep-worktree "
                "or --resolve take-main)")
        raise WorktreeError("claim_refused", f"nothing was claimed — {detail}{hint}"[:1500],
                            files=bad)
    if dry_run:
        return {"planned": [it["rel"] for it in plan]}
    keep = ledger_root() / run_id / "main-claimed" / time.strftime("%Y%m%dT%H%M%S")
    n = 0
    while keep.exists():
        n += 1
        keep = keep.with_name(keep.name.split(".")[0] + f".{n}")
    manifest = []
    for it in plan:
        for side, data in (("main", it["cur"]), ("worktree", it["wt"]), ("index", it["idx"])):
            if data is not None:
                dst = keep / side / it["rel"]
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)
        manifest.append({"path": it["rel"], "status": it["status"], "staged": it["staged"],
                         "main_sha256": hashlib.sha256(it["cur"]).hexdigest()
                         if it["cur"] is not None else None,
                         "worktree_had": it["wt"] is not None, "resolve": resolve,
                         "index_kept": it["idx"] is not None,
                         "main_exec": it["cur_x"], "worktree_exec": it["wt_x"]})
    keep.mkdir(parents=True, exist_ok=True)
    (keep / "manifest.json").write_text(json.dumps({"run_id": run_id, "main": str(main),
                                                   "at": _now_iso(), "files": manifest},
                                                  indent=1))
    claimed, left, error = [], [], None
    for it in plan:
        rel = it["rel"]
        wrote = False
        try:
            if it["moves"]:
                _write_worktree_file(wt, rel, it["target"], it["tracked"], it["target_x"])
                wrote = True
            if (_read(main / rel) != it["cur"] or _exec_bit(main / rel) != it["cur_x"]
                    or _index_entry(main, rel) != it["index"]):
                if wrote:
                    _undo_worktree_file(wt, rel, it["wt"], it["tracked"], it["wt_x"])
                left.append(rel)
                continue
            _restore_main_file(main, rel, it["head"] is not None)
            claimed.append(rel)
        except (WorktreeError, OSError) as exc:
            # e.g. another writer holds main's index.lock: put this file's worktree side
            # back (main is as it was, or already HEAD — both bytes are in the backup),
            # stop, and return what WAS claimed so the caller records it (I2/I4)
            if wrote:
                try:
                    _undo_worktree_file(wt, rel, it["wt"], it["tracked"], it["wt_x"])
                except (WorktreeError, OSError):
                    pass   # the backup holds the worktree's prior bytes
            left.append(rel)
            error = f"{rel}: {getattr(exc, 'message', None) or exc}"[:300]
            left += [x["rel"] for x in plan[plan.index(it) + 1:]]
            break
    changed = [it["rel"] for it in plan if it["rel"] in claimed and it["moves"]]
    out = {"claimed": claimed, "main_left": left, "backup": str(keep),
           "worktree_changed": changed}
    if error:
        out["error"] = error
    return out


# ── release, snapshot, ledger (§8.1) ─────────────────────────────────────────
def snapshot_to_ledger(run_id: str, entry: dict, main: "str | Path", reason: str) -> Path:
    """Snapshot EVERY unignored file of the worktree (rebase in progress included, not
    aborted) into ``ledger/<run_id>/<repo>/`` as bundle + binary diff, and VERIFY the
    bundle restores the same tree. Raises WorktreeError('snapshot_failed') — the
    caller then keeps the worktree."""
    repo_name = entry["repo_name"]
    wt = wt_path(run_id, repo_name)
    main = Path(os.path.realpath(str(main)))
    dest = ledger_root() / run_id / repo_name
    dest.mkdir(parents=True, exist_ok=True)
    fd, idx = tempfile.mkstemp(prefix="swarm-snap-idx-")
    os.close(fd)
    os.unlink(idx)
    env = {"GIT_INDEX_FILE": idx}
    ref = f"refs/swarm-ledger/{run_id}/{repo_name}"
    try:
        head = _rev(wt, "HEAD")
        _out(wt, "read-tree", head, env=env)
        # A dependency link already ignored (info/exclude) must NOT be named again:
        # git refuses `add` when a pathspec — even an :(exclude) one — names an ignored
        # path ("paths are ignored", rc=1), and the whole snapshot then failed. Only a
        # link git would otherwise pick up is excluded explicitly.
        _out(wt, "add", "-A", "--", ".", *(f":(exclude){d}" for d in DEP_LINKS
                                            if os.path.lexists(wt / d) and not _ignored(wt, d)),
             env=env, timeout=300)
        tree = _out(wt, "write-tree", env=env).strip()
        change, unadded = None, None   # None: restore by hand (README)
        if busy_state(wt) is None and head == entry.get("base"):
            try:  # what was IN the change set vs merely on disk (a restore keeps the split)
                change, unadded = change_tree(wt, run_id), untracked_new_files(wt, run_id)
            except WorktreeError:
                change, unadded = None, None
        snap = _snapshot(wt, head, tree, f"swarm: ledger snapshot of {run_id} ({reason})")
        base = entry.get("base") or head
        prereq = _out(wt, "merge-base", base, snap).strip() or base
        _out(main, "update-ref", ref, snap)
        bundle = dest / "worktree.bundle"
        _out(main, "bundle", "create", str(bundle), ref, f"^{prereq}", timeout=300)
        (dest / "changes.diff").write_text(
            _out(wt, "diff", "--binary", prereq, snap, timeout=300), encoding="utf-8")
        _verify_bundle(main, bundle, ref, tree)
        meta = {"run_id": run_id, "repo_name": repo_name, "base": prereq, "snapshot": snap,
                "tree": tree, "reason": reason, "at": _now_iso(),
                "integration_branch": entry.get("integration_branch"),
                "change_tree": change, "unadded": unadded,
                "git_common_dir": entry.get("git_common_dir")}
        (dest / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (dest / "README.md").write_text(_LEDGER_README.format(**meta), encoding="utf-8")
        return dest
    except WorktreeError as exc:
        raise WorktreeError("snapshot_failed", f"ledger snapshot failed: {exc.message}") from exc
    finally:
        if os.path.exists(idx):
            os.unlink(idx)
        git(main, "update-ref", "-d", ref)


def _verify_bundle(main: Path, bundle: Path, ref: str, tree: str) -> None:
    if git(main, "bundle", "verify", str(bundle), timeout=120).returncode != 0:
        raise WorktreeError("snapshot_failed", "git bundle verify failed")
    tmp = Path(tempfile.mkdtemp(prefix="swarm-ledger-check-"))
    try:
        _out(tmp, "init", "-q", "--bare", ".")
        alt = Path(_common_dir(main)) / "objects"
        (tmp / "objects" / "info").mkdir(parents=True, exist_ok=True)
        (tmp / "objects" / "info" / "alternates").write_text(str(alt) + "\n", encoding="utf-8")
        _out(tmp, "fetch", "-q", str(bundle), f"{ref}:refs/check", timeout=300)
        if _out(tmp, "rev-parse", "refs/check^{tree}").strip() != tree:
            raise WorktreeError("snapshot_failed", "restored snapshot tree differs")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


_LEDGER_README = """# Ledger snapshot of {run_id} / {repo_name}

The run ended ({reason}) before its change was integrated. The worktree was deleted
only after this snapshot was verified to restore the same tree.

- base: `{base}` (integration branch {integration_branch})
- snapshot commit: `{snapshot}` (tree `{tree}`), inside `worktree.bundle`
- `changes.diff`: the same change as a binary diff, for reading

Restore:

    git worktree add -b restore/{run_id} /tmp/restore-{run_id} {base}
    git -C /tmp/restore-{run_id} fetch {ledger_hint} 'refs/swarm-ledger/*:refs/swarm-ledger/*'
    git -C /tmp/restore-{run_id} read-tree -u --reset {snapshot}

Ignored files (.gitignore) are not in the snapshot. Kept 90 days.
""".replace("{ledger_hint}", "<this dir>/worktree.bundle")


def _admin_dir_for(main: Path, path: Path) -> "Path | None":
    """The ``$GIT_COMMON_DIR/worktrees/<x>`` directory whose gitdir points at *path*
    (its name follows the path's last segment, not the run id)."""
    root = Path(_common_dir(main)) / "worktrees"
    if not root.is_dir():
        return None
    for d in root.iterdir():
        try:
            gd = (d / "gitdir").read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if os.path.realpath(os.path.dirname(gd)) == str(path):
            return d
    return None


def _safe_to_touch(run_id: str, repo_name: str, main: Path) -> Path:
    """The derived path, after proving it is a real (non-symlinked) location that git
    has registered as this run's worktree. Anything else is refused: a planted symlink
    could otherwise point a delete at the main checkout."""
    path = wt_path(run_id, repo_name)
    if os.path.lexists(path):
        if not _no_symlinks(path) or os.path.realpath(str(path)) != str(path):
            raise WorktreeError("worktree_invalid", f"{path} crosses a symlink — not touched")
        if is_listed(main, path) is not True:
            raise WorktreeError("worktree_invalid", f"{path} is not a registered linked "
                                "worktree of this repo — not touched")
    return path


def is_listed(main, path: "str | Path") -> "bool | None":
    """True/False: *path* is / is not a LINKED worktree of *main* (any branch, detached
    included; never the main worktree itself). None: git could not tell."""
    want = os.path.realpath(str(path))
    try:
        items = _worktree_list(main)
    except WorktreeError:
        return None
    return any(w.get("path") == want for w in items[1:]) and items[0].get("path") != want


def _remove(run_id: str, entry: dict, main: Path, merged: bool) -> None:
    path = _safe_to_touch(run_id, entry["repo_name"], main)
    for rel in DEP_LINKS:  # the dependency links point INTO main: drop them first
        link = path / rel
        if os.path.islink(link):
            os.unlink(link)
    if path.exists():
        p = git(main, "worktree", "remove", "--force", str(path), timeout=120)
        if p.returncode != 0:
            # never fall back to rmtree: git refusing means this is not what we think it is
            raise WorktreeError("remove_failed", f"git worktree remove refused {path}: "
                                f"{(p.stderr or p.stdout).strip()[:200]}")
    admin = _admin_dir_for(main, path)
    if admin is not None:
        shutil.rmtree(admin, ignore_errors=True)
    git(main, "branch", "-q", "-d" if merged else "-D", branch_name(run_id))
    drop_srcgraph_cache(path)
    refs = git(main, "for-each-ref", "--format=%(refname)", f"refs/run/{run_id}/").stdout
    for r in refs.split():
        git(main, "update-ref", "-d", r)
    parent = path.parent
    try:
        if parent.is_dir() and not os.path.islink(parent) \
                and all(c.name == _WS_STAMP for c in parent.iterdir()):
            (parent / _WS_STAMP).unlink(missing_ok=True)
            parent.rmdir()
    except OSError:
        pass


def release(run_id: str, run_state: dict, mains: "dict[str, Path]", reason: str) -> "list[dict]":
    """Release every worktree of a run (§8.1). Integrated → remove; active → verified
    ledger snapshot, then remove; snapshot failure → keep and report. Mutates the
    entries in ``run_state["worktrees"]``. *mains*: repo_name → main checkout."""
    results = []
    for entry in run_state.get("worktrees") or []:
        if not isinstance(entry, dict) or entry.get("state") == "released":
            continue
        name = entry.get("repo_name")
        main = mains.get(name)
        if main is None:
            results.append({"repo": name, "ok": False, "error": "main checkout unknown"})
            continue
        try:
            path = _safe_to_touch(run_id, name, main)
            if entry.get("state") == "integrated":
                _remove(run_id, entry, main, merged=True)
                entry.update(state="released", released_at=_now_iso())
                results.append({"repo": name, "ok": True, "integrated": True})
                continue
            ledger = None
            if path.exists():
                ledger = snapshot_to_ledger(run_id, entry, main, reason)
            _remove(run_id, entry, main, merged=False)
            entry.update(state="released", released_at=_now_iso(),
                         ledger=str(ledger) if ledger else None, release_reason=reason)
            results.append({"repo": name, "ok": True, "integrated": False,
                            "ledger": str(ledger) if ledger else None})
        except WorktreeError as exc:
            results.append({"repo": name, "ok": False, "error": exc.message})
    return results


# ── goal-cycle anchors (§6.3) ────────────────────────────────────────────────
_MARK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def mark(run_id: str, entry: dict, main, name: str) -> str:
    """Snapshot the current change set as ``refs/run/<run_id>/marks/<name>``."""
    if not _MARK_RE.fullmatch(name or ""):
        raise WorktreeError("bad_mark", f"invalid mark name {name!r}")
    wt = validate(run_id, entry, main)
    snap = _snapshot(wt, _rev(wt, "HEAD"), change_tree(wt, run_id), f"swarm: mark {name}")
    _out(wt, "update-ref", f"refs/run/{run_id}/marks/{name}", snap)
    return snap


def diff_since(run_id: str, entry: dict, main, name: "str | None", stat: bool = False) -> str:
    """Diff of the change set since mark *name* (None = since base)."""
    wt = validate(run_id, entry, main)
    if name is None:
        start = entry["base"]
    else:
        if not _MARK_RE.fullmatch(name):
            raise WorktreeError("bad_mark", f"invalid mark name {name!r}")
        start = _rev(wt, f"refs/run/{run_id}/marks/{name}")
    tree = change_tree(wt, run_id)
    args = ["diff", "--no-renames"] + (["--stat"] if stat else ["--binary"])
    return _out(wt, *args, start, tree, timeout=120)


# ── run discovery ────────────────────────────────────────────────────────────
def _iter_runs(workspace: "str | Path", ids: "set[str] | None" = None):
    """(run.json path, data) of every run; *ids*: only the run dirs named in it, the
    others are not even read (the caller already knows which runs it wants)."""
    import glob
    for f in glob.glob(os.path.join(str(workspace), "Projects", "*", ".artifacts", "runs",
                                    "run_*", "run.json")):
        if ids is not None and os.path.basename(os.path.dirname(f)) not in ids:
            continue
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            yield Path(f), data


def active_entries(data: dict) -> "list[dict]":
    if data.get("worktree_mode") != "isolated":
        return []
    return [e for e in data.get("worktrees") or []
            if isinstance(e, dict) and e.get("state") == "active"]


def active_worktree_roots(workspace: "str | Path") -> "list[tuple[str, str]]":
    """(worktree path, repo name) of every live isolated run (no git calls).

    Hot path (artifact drift / source classification, once per Canvas row): a run.json
    is parsed only when its (mtime_ns, size) changed since the last call, so the cost is
    one stat per run instead of reading + parsing the whole run history (1000+ files)."""
    import glob
    prefix = os.path.join(str(workspace), "")
    out = []
    seen = set()
    for f in glob.glob(os.path.join(str(workspace), "Projects", "*", ".artifacts", "runs",
                                    "run_*", "run.json")):
        seen.add(f)
        for rid, repo in _live_summary(f):
            try:
                p = wt_path(rid, repo)
            except WorktreeError:
                continue
            if p.is_dir():
                out.append((str(p), repo))
    with _LIVE_SUMMARY_LOCK:
        for gone in [k for k in _LIVE_SUMMARY if k.startswith(prefix) and k not in seen]:
            del _LIVE_SUMMARY[gone]
    return out


# run.json path -> ((mtime_ns, size), ((run_id, repo_name), ...)). Only a live run has
# entries; a terminal run caches () and is re-read only when its file changes.
_LIVE_SUMMARY: "dict[str, tuple[tuple[int, int], tuple[tuple[str, str], ...]]]" = {}
_LIVE_SUMMARY_LOCK = threading.Lock()


def _live_summary(f: str) -> "tuple[tuple[str, str], ...]":
    """(run_id, repo_name) of the active worktrees of the live run at *f*, or ()."""
    try:
        st = os.stat(f)
    except OSError:
        return ()
    key = (st.st_mtime_ns, st.st_size)
    with _LIVE_SUMMARY_LOCK:
        hit = _LIVE_SUMMARY.get(f)
    if hit is not None and hit[0] == key:
        return hit[1]
    try:
        data = json.loads(Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()   # not cached: a half-written file is read again next call
    val: "tuple[tuple[str, str], ...]" = ()
    if isinstance(data, dict) and data.get("status") in _LIVE:
        rid = str(data.get("id") or "")
        val = tuple((rid, str(e.get("repo_name"))) for e in active_entries(data))
    with _LIVE_SUMMARY_LOCK:
        _LIVE_SUMMARY[f] = (key, val)
    return val


def own_isolated_run(session_id: str, workspace: "str | Path",
                     with_empty: bool = True,
                     ids: "set[str] | None" = None) -> "dict | None":
    """The isolated run THIS session owns (§4.2: no time window) → {"project", "id",
    "worktrees": [(repo_name, path)], "cli"}; ``worktrees`` lists its active,
    git-registered worktrees. *with_empty*=False: only a run that has at least one.
    *ids*: consider only these run ids (a caller that already narrowed them reads only
    those run.json files, not the whole history)."""
    if not session_id:
        return None
    best = None
    for f, data in _iter_runs(workspace, ids):
        if data.get("session_id") != session_id or data.get("status") not in _LIVE:
            continue
        rid = str(data.get("id") or f.parent.name)
        wts = []
        for e in active_entries(data):
            try:
                p = wt_path(rid, str(e.get("repo_name")))
            except WorktreeError:
                continue
            main = main_of(p)
            if main is not None and is_registered(main, rid, str(e.get("repo_name"))):
                wts.append((str(e.get("repo_name")), str(p)))
        settled = any(isinstance(e, dict) and e.get("state") in ("integrated", "released")
                      for e in data.get("worktrees") or [])
        if wts or (with_empty and data.get("worktree_mode") == "isolated" and not settled
                   and data.get("status") == "running"):
            # with_empty: a RUNNING isolated run with no worktree yet still owns the
            # session's writes (its repos are isolated on first write, §16.9) — until it
            # lands or a worktree is released; a paused run never claims new repos
            try:
                t = f.stat().st_mtime
            except OSError:
                t = 0.0
            cand = (t, {"project": str(data.get("project") or f.parents[3].name), "id": rid,
                        "worktrees": wts, "cli": data.get("cli"), "settled": settled,
                        "status": data.get("status")})
            if best is None or cand[0] > best[0]:   # the session's most recent run wins
                best = cand
    return best[1] if best else None


def main_of(path: "str | Path") -> "Path | None":
    """The main checkout a worktree belongs to (parent of its common dir)."""
    try:
        common = _common_dir(path)
    except WorktreeError:
        return None
    return Path(common).parent if os.path.basename(common) == ".git" else None


def entry_main(run_id: str, entry: dict, hint: "str | Path | None" = None) -> Path:
    """The main checkout of a run.json worktree entry, NAMED BY GIT (§16.9: run.json is
    untrusted, bindings are only a hint). A worktree on disk names its main through its
    own git pointer, which must be a linked worktree of that main and agree with the
    entry's pinned ``main`` and recorded common dir. A worktree no longer on disk (a
    released one) uses the pinned ``main`` (legacy entries: *hint*), accepted only if
    its git common dir is the recorded one. Raises WorktreeError 'worktree_invalid'."""
    name = str(entry.get("repo_name")) if isinstance(entry, dict) else ""
    wt = wt_path(run_id, name)
    pinned = entry.get("main") or (str(hint) if hint is not None else None)
    recorded = entry.get("git_common_dir")
    bad = lambda why: WorktreeError("worktree_invalid", f"worktree {wt}: {why}")  # noqa: E731
    if os.path.lexists(wt):
        m = main_of(wt)
        if m is None:
            raise bad("its git pointer names no main checkout")
        m = Path(os.path.realpath(str(m)))
        if entry.get("main") and os.path.realpath(str(entry["main"])) != str(m):
            raise bad("the recorded main checkout is not the one git registered it under")
        if recorded and _common_dir(m) != recorded:
            raise bad("git common dir differs from the recorded one")
        if is_listed(m, wt) is not True:
            raise bad("not a registered linked worktree of its main checkout")
        return m
    if not pinned:
        raise bad("not on disk and no recorded main checkout")
    m = Path(os.path.realpath(str(pinned)))
    try:
        ok = _toplevel(m) == str(m) and recorded is not None and _common_dir(m) == recorded
    except WorktreeError:
        ok = False
    if not ok:
        raise bad(f"{m} is not the repo this entry was created in")
    return m


_STATE_FILES = ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD",
                "REVERT_HEAD", "BISECT_LOG")


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


def repo_for_write(path: "str | Path", workspace: "str | Path | None") -> "Path | None":
    """The main checkout a write to *path* would land in, when that repo can be isolated
    in a run worktree (§16.9) — else None. File reads only (it runs inside the write
    gate's time budget). Not isolatable: no git repo; a linked worktree or submodule
    (``.git`` is a file); any run worktree; SwarmWS itself; the home dir or the app data
    dir ITSELF as a repo (a repo cloned under it, e.g. a bindings clone, is isolated);
    a repo with submodules; a Brazil workspace package; a main checkout
    on a detached HEAD or with a rebase / merge / cherry-pick / revert / bisect going."""
    p = os.path.realpath(str(path))
    if _under(p, os.path.realpath(str(wt_root()))):
        return None
    cur = Path(p)
    while not os.path.lexists(cur / ".git"):
        if cur.parent == cur:
            return None
        cur = cur.parent
    g = cur / ".git"
    if os.path.islink(g) or not g.is_dir() or not (g / "objects").is_dir():
        return None
    top = str(cur)
    if workspace is not None and top == os.path.realpath(str(workspace)):
        return None
    if top in (str(_home()), str(_data_dir())):
        return None
    if (cur / ".gitmodules").exists():
        return None
    if cur.parent.name == "src" and (cur.parent.parent / "packageInfo").exists():
        return None
    try:
        head = (g / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: refs/heads/"):
        return None
    if any(os.path.lexists(g / n) for n in _STATE_FILES):
        return None
    ref = head[len("ref: "):].strip()
    if not (g / ref).is_file():          # unborn branch (no commit yet): no worktree base
        try:
            packed = (g / "packed-refs").read_text(encoding="utf-8")
        except OSError:
            packed = ""
        if not any(line.endswith(" " + ref) for line in packed.splitlines()):
            return None
    return cur


def last_activity(run_id: str, data: dict) -> "float | None":
    """Newest mtime among the run's changed / untracked worktree files (and the
    worktree's index) — "is somebody still working here?" for the reapers (§8.2)."""
    best = None
    for e in active_entries(data):
        try:
            wt = wt_path(run_id, str(e.get("repo_name")))
            if not wt.is_dir():
                continue
            cands = [_git_path(wt, "index")]
            out = _out(wt, "status", "--porcelain", "-z", "--untracked-files=all", timeout=30)
            cands += [wt / rec[3:] for rec in out.split("\0") if len(rec) > 3]
        except WorktreeError:
            continue
        for c in cands:
            try:
                t = os.lstat(c).st_mtime
            except OSError:
                continue
            best = t if best is None or t > best else best
    return best


# ── sweep (§8.3) ─────────────────────────────────────────────────────────────
def _quarantine(d: Path, report: dict) -> None:
    q = wt_root() / ".quarantine"
    q.mkdir(parents=True, exist_ok=True)
    dest = q / f"{d.name}-{int(time.time())}"
    try:
        os.rename(d, dest)
        report["quarantined"].append(str(dest))
    except OSError as exc:
        report["errors"].append(f"quarantine {d}: {exc}")


def _iso_ts(v) -> float:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _deliver_done(data: dict) -> bool:
    return any(isinstance(s, dict) and s.get("stage") == "deliver"
               and s.get("status") in ("completed", "done") for s in data.get("stages") or [])


def sweep(workspace: "str | Path", owner_active: "Callable[[str | None], bool]",
          now: "float | None" = None, release_orphans: bool = False) -> dict:
    """Release what nobody will release (§8.3). Only worktrees under WT_ROOT that git
    has registered, and ``run/*`` branches, are touched. *owner_active(session_id)*
    must return True when the owner may still be working (the run is then left)."""
    from core.run_lock import RunLockTimeout, update_run
    now = time.time() if now is None else now
    report: dict = {"released": [], "kept": [], "quarantined": [], "errors": [],
                    "branches": [], "tokens_purged": 0, "ledgers_purged": 0}
    runs = {str(d.get("id") or f.parent.name): (f, d) for f, d in _iter_runs(workspace)}
    root = wt_root()
    for rd in sorted(root.iterdir()) if root.is_dir() else []:
        if rd.name.startswith(".") or not rd.is_dir():
            continue
        if os.path.islink(rd) or not RUN_ID_RE.fullmatch(rd.name):
            _quarantine(rd, report)
            continue
        if not owned_by(rd.name, workspace):
            continue  # another workspace's run (a test, a second install): not ours to judge
        for wd in sorted(rd.iterdir()):
            if wd.name == _WS_STAMP:
                continue
            if os.path.islink(wd) or not wd.is_dir() or not (wd / ".git").exists():
                _quarantine(wd, report)  # certainly not a worktree git knows
                continue
            main = main_of(wd)
            listed = is_listed(main, wd) if main is not None else None
            if listed is None:
                report["errors"].append(f"{rd.name}/{wd.name}: git could not tell — kept")
                continue
            if listed is False:
                _quarantine(wd, report)
                continue
            if rd.name not in runs and release_orphans:
                entry = {"repo_name": wd.name, "state": "active", "base": _rev(main, "HEAD")}
                try:
                    snapshot_to_ledger(rd.name, entry, main, "run record missing")
                    _remove(rd.name, entry, main, merged=False)
                    report["released"].append(f"{rd.name}/{wd.name} (no run record)")
                except WorktreeError as exc:
                    report["errors"].append(f"{rd.name}/{wd.name}: {exc.message}")
    for rid, (f, data) in runs.items():
        _sweep_run(rid, f, data, owner_active, now, report, update_run, RunLockTimeout)
    for rd in sorted(root.iterdir()) if root.is_dir() else []:
        rec = runs.get(rd.name)
        try:
            idle = rec is not None and now - rec[0].stat().st_mtime > 86400
        except OSError:
            idle = False
        if rec is None or rec[1].get("status") not in _LIVE or idle:
            drop_stamp_only_dir(rd.name, workspace)   # a stage-start re-arms an idle run
    _sweep_branches(runs, report)
    _purge_ledgers(now, report)
    return report


def _sweep_run(rid, f, data, owner_active, now, report, update_run, RunLockTimeout) -> None:
    from core import hook_token
    try:
        hook_token.purge_expired(f.parent, now)
    except OSError:
        pass
    entries = active_entries(data) + [e for e in data.get("worktrees") or []
                                      if isinstance(e, dict) and e.get("state") == "integrated"]
    if not entries:
        return
    live = data.get("status") in _LIVE
    reason = None
    if not live:
        reason = f"run {data.get('status')}"
    else:
        act = last_activity(rid, data) or 0.0
        # Deliver is recorded completed BEFORE its landing; a landing that is backing off
        # (busy, blocked by uncommitted main edits) stamps landing_attempt_at.
        tried = _iso_ts(data.get("landing_attempt_at"))
        if _deliver_done(data) and any(e.get("state") == "active" for e in entries) \
                and now - max(act, tried) > 86400:
            reason = "deliver finished but never integrated"
        elif now - max(act, f.stat().st_mtime) > STALE_DAYS * 86400:
            reason = f"no activity for {STALE_DAYS} days"
    if reason is None:
        return
    if owner_active(data.get("session_id")):
        report["kept"].append(f"{rid}: owner session still active")
        return
    mains = {}
    for e in entries:
        m = main_of(wt_path(rid, e["repo_name"])) if wt_path(rid, e["repo_name"]).exists() else None
        if m is not None and _common_dir(m) == e.get("git_common_dir"):
            mains[e["repo_name"]] = m
    holder: dict = {}

    def _mut(fresh: dict) -> bool:
        if fresh.get("status") in _LIVE and live is False:
            return False                      # revived meanwhile
        res = release(rid, fresh, mains, reason)
        holder["res"] = res
        if live:
            fresh["worktree_released_stale"] = {"at": _now_iso(), "reason": reason}
        fresh.pop("release_requested", None)
        return True
    try:
        update_run(f, _mut, timeout=5.0)
        report["released"].append({"run": rid, "reason": reason, "results": holder.get("res")})
    except (RunLockTimeout, OSError, ValueError) as exc:
        report["errors"].append(f"{rid}: {exc}")


def _sweep_branches(runs: dict, report: dict) -> None:
    mains = set()
    for _rid, (_f, data) in runs.items():
        for e in data.get("worktrees") or []:
            c = e.get("git_common_dir") if isinstance(e, dict) else None
            if c and os.path.basename(c) == ".git" and Path(c).is_dir():
                mains.add(Path(c).parent)
    for main in sorted(mains):
        try:
            refs = _out(main, "for-each-ref", "--format=%(refname)", "refs/heads/run/").split()
            listed = {w.get("branch") for w in _worktree_list(main)}
            head = _symbolic_head(main) or "HEAD"
        except WorktreeError:
            continue
        for ref in refs:
            rid = ref.rsplit("/", 1)[-1]
            if ref in listed or not RUN_ID_RE.fullmatch(rid):
                continue
            data = runs.get(rid, (None, {}))[1]
            if data.get("status") in _LIVE:
                continue
            merged = git(main, "merge-base", "--is-ancestor", ref, head).returncode == 0
            if not merged:
                dest = ledger_root() / rid / "branch"
                dest.mkdir(parents=True, exist_ok=True)
                b = dest / "branch.bundle"
                if git(main, "bundle", "create", str(b), ref, f"^{head}").returncode != 0 \
                        or git(main, "bundle", "verify", str(b)).returncode != 0:
                    report["errors"].append(f"{ref}: bundle failed — branch kept")
                    continue
            git(main, "branch", "-q", "-D", ref[len("refs/heads/"):])
            report["branches"].append(f"{ref} ({'merged' if merged else 'bundled'})")


def _purge_ledgers(now: float, report: dict) -> None:
    root = ledger_root()
    for d in root.iterdir() if root.is_dir() else []:
        try:
            if d.is_dir() and now - d.stat().st_mtime > LEDGER_KEEP_DAYS * 86400:
                shutil.rmtree(d)
                report["ledgers_purged"] += 1
        except OSError:
            continue


# ── after release (§6.4, §8.1, §8.3) ─────────────────────────────────────────
def drop_srcgraph_cache(path: "str | Path") -> None:
    """Delete the test runner's source-graph cache of a removed worktree. Same file
    name as ``pipeline_test.build_source_graph`` (sha256 of the repo path)."""
    import hashlib
    env = os.environ.get("SWARM_TEST_CACHE_DIR")
    d = Path(env) if env else Path.home() / ".swarm-ai" / "cache" / "pipeline_test"
    for p in {str(path), os.path.realpath(str(path))}:
        try:
            (d / f"srcgraph-{hashlib.sha256(p.encode()).hexdigest()[:16]}.json").unlink()
        except OSError:
            pass


def landed_path(path: "str | Path", workspace: "str | Path") -> "Path | None":
    """A file path inside a run worktree that no longer exists → the same file in the
    main checkout, once that run's change landed there. Canvas rows recorded during
    the run keep resolving after the worktree is released (§6.4). None otherwise."""
    import glob
    if os.path.exists(str(path)):
        return None
    hit = run_for_path(path)
    if hit is None:
        return None
    rid, repo = hit
    try:
        rel = Path(os.path.realpath(str(path))).relative_to(os.path.realpath(str(wt_path(rid, repo))))
    except ValueError:
        return None
    if not rel.parts or ".." in rel.parts:
        return None
    for f in glob.glob(os.path.join(str(workspace), "Projects", "*", ".artifacts", "runs",
                                    rid, "run.json")):
        try:
            data = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for e in data.get("worktrees") or [] if isinstance(data, dict) else []:
            if not isinstance(e, dict) or e.get("repo_name") != repo \
                    or not (e.get("integrated") or {}).get("sha"):
                continue
            common = str(e.get("git_common_dir") or "")
            if os.path.basename(common) != ".git":
                continue
            main = os.path.realpath(os.path.dirname(common))
            cand = os.path.realpath(os.path.join(main, str(rel)))
            if cand.startswith(main + os.sep) and os.path.isfile(cand):
                return Path(cand)
    return None


def restore_from_ledger(run_id: str, entry: dict, main: "str | Path",
                        workspace: "str | Path | None" = None) -> dict:
    """Rebuild a worktree released after a long idle period from its verified ledger
    snapshot (§8.3). Files = the snapshot; index = the change set as it was, so a file
    that was never ``git add``-ed comes back un-added and §6.2 still applies. Returns
    the new ``worktrees[]`` entry; raises WorktreeError('restore_failed')."""
    name = str(entry.get("repo_name"))
    _check_ids(run_id, name)
    main = Path(os.path.realpath(str(main)))
    dest = ledger_root() / run_id / name
    fail = lambda why: WorktreeError("restore_failed", why, ledger=str(dest))  # noqa: E731
    try:
        meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise fail("no readable ledger snapshot for this worktree") from None
    if not isinstance(meta, dict) or not meta.get("change_tree") or meta.get("unadded") is None:
        raise fail("the snapshot was taken mid-rebase or by an older version: restore it by "
                   "hand (ledger README)")
    if meta.get("git_common_dir") not in (None, _common_dir(main)):
        raise fail("the ledger belongs to another clone of this repo")
    if meta.get("integration_branch") != _symbolic_head(main):
        raise fail(f"the main checkout is not on {meta.get('integration_branch')}")
    ref = f"refs/swarm-ledger/{run_id}/{name}"
    try:
        _out(main, "fetch", "-q", str(dest / "worktree.bundle"), f"{ref}:{ref}", timeout=300)
        snap = _rev(main, ref)
        if snap != meta.get("snapshot"):
            raise fail("the bundle holds a different snapshot than meta.json")
        new = create(run_id, name, main, workspace=workspace, at=str(meta.get("base")))
        wt = wt_path(run_id, name)
        try:
            _out(wt, "read-tree", "--reset", "-u", snap, timeout=300)   # files
            _out(wt, "read-tree", str(meta["change_tree"]))              # index
            if change_tree(wt, run_id) != meta["change_tree"] \
                    or untracked_new_files(wt, run_id) != sorted(meta["unadded"]):
                raise fail("the rebuilt worktree differs from the snapshot")
        except WorktreeError:
            _remove(run_id, new, main, merged=False)
            raise
    except WorktreeError as exc:
        if exc.code == "restore_failed":
            raise
        raise fail(exc.message) from exc
    finally:
        git(main, "update-ref", "-d", ref)
    new["restored_from"] = str(dest)
    return new
