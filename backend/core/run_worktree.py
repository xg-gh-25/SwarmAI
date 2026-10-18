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

import json
import os
import re
import shutil
import subprocess
import tempfile
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


def _common_dir(cwd) -> str:
    p = _out(cwd, "rev-parse", "--git-common-dir").strip()
    return os.path.realpath(p if os.path.isabs(p) else os.path.join(str(cwd), p))


def _toplevel(cwd) -> "str | None":
    try:
        p = git(cwd, "rev-parse", "--show-toplevel", timeout=10)
    except WorktreeError:
        return None
    return os.path.realpath(p.stdout.strip()) if p.returncode == 0 and p.stdout.strip() else None


def busy_state(cwd) -> "str | None":
    """Name of an in-progress merge / rebase / cherry-pick / revert, else None."""
    for name in _IN_PROGRESS:
        if _git_path(cwd, name).exists():
            return name
    return None


def _symbolic_head(cwd) -> "str | None":
    p = git(cwd, "symbolic-ref", "-q", "HEAD", timeout=10)
    return p.stdout.strip() if p.returncode == 0 else None


# ── switch and bindings ──────────────────────────────────────────────────────
def mode_enabled() -> bool:
    """``config.json["pipeline"]["worktree_mode"] == "on"`` (read-only, fail-safe off).
    $SWARM_WORKTREE_MODE (on/off) overrides — tests and operator use."""
    env = (os.environ.get("SWARM_WORKTREE_MODE") or "").strip().lower()
    if env in ("on", "off"):
        return env == "on"
    try:
        from config import get_app_data_dir
        cfg = json.loads((get_app_data_dir() / "SwarmWS" / "config.json").read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — unreadable config = the default (off)
        logger.debug("worktree_mode: config unreadable (%s) — off", exc)
        return False
    pipe = cfg.get("pipeline") if isinstance(cfg, dict) else None
    return isinstance(pipe, dict) and str(pipe.get("worktree_mode") or "").lower() == "on"


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


def _worktree_list(main: "str | Path") -> "list[dict]":
    """`git worktree list --porcelain` → [{path, head, branch}]."""
    items, cur = [], {}
    for line in _out(main, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur:
                items.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = os.path.realpath(line[9:])
        elif line.startswith("HEAD "):
            cur["head"] = line[5:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:]
    return items


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


def _exclude_node_modules(common_dir: str) -> None:
    f = Path(common_dir) / "info" / "exclude"
    try:
        cur = f.read_text(encoding="utf-8") if f.exists() else ""
        if "/desktop/node_modules" not in cur.splitlines():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(cur + ("" if cur.endswith("\n") or not cur else "\n")
                         + "/desktop/node_modules\n", encoding="utf-8")
    except OSError:
        pass  # only cosmetic: the link would show as untracked


def create(run_id: str, repo_name: str, main: "str | Path",
           workspace: "str | Path | None" = None) -> dict:
    """Create ``WT_ROOT/<run_id>/<repo_name>`` on a new ``run/<run_id>`` branch at the
    main checkout's HEAD (other sessions' uncommitted edits stay out). Returns the
    run.json ``worktrees[]`` entry. Raises WorktreeError (caller falls back to shared)."""
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
    head = _rev(main, "HEAD")
    path.parent.mkdir(parents=True, exist_ok=True)
    if workspace is not None:
        stamp_workspace(run_id, workspace)
    p = git(main, "-c", "core.hooksPath=/dev/null", "worktree", "add", "-q", "-b",
            branch_name(run_id), str(path), head, timeout=120)
    if p.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
        raise WorktreeError("worktree_create_failed", (p.stderr or p.stdout).strip()[:300])
    common = _common_dir(main)
    nm = main / "desktop" / "node_modules"
    if nm.is_dir() and (path / "desktop").is_dir() and not (path / "desktop" / "node_modules").exists():
        try:
            os.symlink(str(nm), str(path / "desktop" / "node_modules"))
        except OSError:
            pass  # frontend tests then fail visibly; never fall back silently to main
    _exclude_node_modules(common)
    return {"repo_name": repo_name, "git_common_dir": common, "integration_branch": head_ref,
            "base": head, "pre_sync_tip": None, "state": "active", "created_at": _now_iso()}


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
    """Unignored, un-added new files (§6.2) — B and the node_modules link excluded."""
    out = _out(wt, "ls-files", "--others", "--exclude-standard", "-z", timeout=60)
    b = deliverable_prefix(run_id)
    return sorted(p for p in out.split("\0")
                  if p and not p.startswith(b) and p != "desktop/node_modules"
                  and not p.startswith("desktop/node_modules/"))


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


def _patch_id(wt, a: str, b: str, run_id: str) -> str:
    diff = _out(wt, "diff", "--binary", "--no-renames", a, b, "--", ".",
                f":(exclude){deliverable_prefix(run_id).rstrip('/')}", timeout=120)
    if not diff.strip():
        return ""
    return _out(wt, "patch-id", "--stable", input_text=diff).split(" ")[0].strip()


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


def _integrate_lock(repo_name: str):
    from core.run_lock import run_lock
    d = wt_root() / ".integrate" / repo_name
    return run_lock(d, timeout=INTEGRATE_LOCK_TIMEOUT_S)


def _main_busy(main: Path, entry: dict) -> "str | None":
    if _symbolic_head(main) != entry.get("integration_branch"):
        return "main checkout is not on the integration branch"
    b = busy_state(main)
    return f"main checkout has {b} in progress" if b else None


def land(run_id: str, entry: dict, main: "str | Path", message: str, expect_tree: str,
         write_b: "Callable[[Path], list] | None" = None,
         still_live: "Callable[[], bool] | None" = None) -> dict:
    """Final commit N in the worktree (parent C == base, full git hooks) + native
    ``git merge --ff-only N`` in the main checkout. On ANY failure the worktree goes
    back to HEAD == base and main is untouched. Mutates *entry* on success (state
    integrated). Returns {"sha", "files", "commit_s", "merge_s"}; raises WorktreeError
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
        try:
            with _integrate_lock(entry["repo_name"]):
                t1 = time.monotonic()
                m = git(main, "merge", "-q", "--ff-only", n, timeout=120)
                merge_s = round(time.monotonic() - t1, 2)
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
            "commit_s": commit_s, "merge_s": merge_s}


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
        _out(wt, "add", "-A", "--", ".", ":(exclude)desktop/node_modules", env=env, timeout=300)
        tree = _out(wt, "write-tree", env=env).strip()
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
                "integration_branch": entry.get("integration_branch")}
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
    link = path / "desktop" / "node_modules"
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
def _iter_runs(workspace: "str | Path"):
    import glob
    for f in glob.glob(os.path.join(str(workspace), "Projects", "*", ".artifacts", "runs",
                                    "run_*", "run.json")):
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
    """(worktree path, repo name) of every live isolated run (no git calls)."""
    out = []
    for _f, data in _iter_runs(workspace):
        if data.get("status") not in _LIVE:
            continue
        rid = str(data.get("id") or "")
        for e in active_entries(data):
            try:
                p = wt_path(rid, str(e.get("repo_name")))
            except WorktreeError:
                continue
            if p.is_dir():
                out.append((str(p), str(e.get("repo_name"))))
    return out


def own_isolated_run(session_id: str, workspace: "str | Path") -> "dict | None":
    """The run THIS session owns that has an active, git-registered worktree (§4.2:
    no time window). → {"project", "id", "worktrees": [(repo_name, path)]}."""
    if not session_id:
        return None
    for f, data in _iter_runs(workspace):
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
        if wts:
            return {"project": str(data.get("project") or f.parents[3].name), "id": rid,
                    "worktrees": wts}
    return None


def main_of(path: "str | Path") -> "Path | None":
    """The main checkout a worktree belongs to (parent of its common dir)."""
    try:
        common = _common_dir(path)
    except WorktreeError:
        return None
    return Path(common).parent if os.path.basename(common) == ".git" else None


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
        if _deliver_done(data) and any(e.get("state") == "active" for e in entries) \
                and now - act > 86400:
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
