"""worktree_write_gate — PreToolUse gate that keeps an isolated run's writes inside
its own worktree (docs/design/pipeline-run-worktree.md §6.1).

Matcher ``Write|Edit|MultiEdit|NotebookEdit|Bash|Read|Grep``, its own hook group (not
the 4-hour Bash chain), bounded by ``_GATE_TIMEOUT``, fail-open on its own errors,
operator bypass ``SWARM_GATE_FORCE=worktree_write_gate`` in the daemon environment. Zero cost
while worktree mode is off and no run worktree exists. Judgments:

1. this session owns an isolated run → DENY writes to that run's main checkout (file
   tools, Bash writes ``file_change_classifier`` can parse, git index/ref-changing
   subcommands there), naming the worktree path to use instead;
2. same session → DENY writes into ANOTHER run's worktree (or a deregistered one);
3. git state shared by all worktrees → DENY, for every session: ``stash`` inside any
   run worktree; ref changes of another run's ``run/*`` branch; ``worktree
   add/remove/move/prune`` aimed at WT_ROOT. A command run INSIDE a run worktree (or by
   a session owning an isolated run) additionally may not touch any ref but its own
   branch, nor config (without --worktree), gc --prune, reflog expire, replace, notes,
   ``push .``.

4. worktree mode on, the session is bound to a real app session, and it has no run
   isolating its writes → DENY writes into any run's worktree (file tools, Bash writes,
   git commands that write a work tree). Writes to a main checkout are NOT judged: native
   coding there is the default (design 2026-10-24-swarmai-usable-by-anyone §3.4; the
   rule that sent main-checkout code writes to the pipeline was removed).

A file-write deny of rule 1 whose target is already an uncommitted change in the
main checkout (an untracked leftover, a stray edit) adds a note: the worktree is made
from HEAD and does not have that file (``_main_edit_note``).

Also: a hardlinked file (st_nlink > 1) in the worktree is not writable; Read / Grep
of the main checkout gets a reminder (allow + additionalContext). Bash parsing is
fixed at today's parsers — gaps are caught by the sync-time check (§7.2), not by
growing this parser. Public: ``create_worktree_write_gate``, ``judge``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.gate_force import force_enabled

logger = logging.getLogger(__name__)

_GATE_TIMEOUT = 3.0
_NOTE_CAP = 1.0      # most a deny hint's git call may take
_NOTE_MARGIN = 0.5   # gate budget kept back for the rest of the judgment
_TL = threading.local()   # .deadline: monotonic end of this judgment's gate budget
_OWN_TTL = 5.0
MATCHER = "Write|Edit|MultiEdit|NotebookEdit|Bash|Read|Grep"
_PATH_KEYS = {"Write": "file_path", "Edit": "file_path", "MultiEdit": "file_path",
              "NotebookEdit": "notebook_path", "Read": "file_path", "Grep": "path"}
_GIT_WRITES_MAIN = frozenset({
    "commit", "add", "rm", "mv", "restore", "checkout", "switch", "stash", "apply", "am",
    "reset", "clean", "merge", "pull", "rebase", "cherry-pick", "revert", "update-index"})
_REF_CMDS = frozenset({"update-ref", "symbolic-ref", "branch", "tag"})
_SHARED_STATE = frozenset({"replace", "notes"})
_CONFIG_READ = frozenset({"--get", "--get-all", "--get-regexp", "-l", "--list",
                          "--get-urlmatch", "--show-origin", "--show-scope", "--name-only"})


# E: the stash deny names read-only git commands that exist (no retired run-diff)
STASH_DENY = ("git stash is shared by every worktree (refs/stash): a stash made here can be "
              "popped into the main checkout. To look at the base without moving anything, "
              "use read-only git in this worktree against the branch the run forked from "
              "(`git worktree list` shows the main checkout and its branch <base>): "
              "`git diff <base>...HEAD`, `git show <base>:<path>`.")


def _deny(msg: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "DENY (worktree gate): " + msg}}


def _real(p: str, cwd: str) -> str:
    p = os.path.expanduser(p)
    return os.path.realpath(p if os.path.isabs(p) else os.path.join(cwd or os.getcwd(), p))


def _under(p: str, root: str) -> bool:
    return p == root or p.startswith(root.rstrip(os.sep) + os.sep)


class _Own:
    """The session's isolated run: id, {repo: worktree path}, {repo: main checkout},
    and its project (for the commands a deny prints)."""

    def __init__(self, run_id: str, wts: "list[tuple[str, str]]", project: "str | None" = None,
                 lazy: bool = True):
        from core.run_worktree import main_of
        self.id = run_id
        self.project = project
        self.lazy = lazy   # may still isolate new repos (running, nothing landed yet)
        self.wts = dict(wts)
        self.mains = {}
        for name, path in wts:
            m = main_of(path)
            if m is not None:
                self.mains[name] = os.path.realpath(str(m))

    def unisolated_repo(self, p: str) -> "str | None":
        """The main checkout of a git repo *p* lies in that this run has no worktree for
        yet but could have one (run_worktree.repo_for_write): a v1 run's repos are fixed
        at create, so that write belongs in a new run. None: not a repo, not isolatable,
        or already isolated."""
        from core.run_worktree import repo_for_write
        from core.security_hooks import _pipeline_runs_root
        if not self.lazy:
            return None
        m = repo_for_write(p, _pipeline_runs_root())
        if m is None:
            return None
        m = os.path.realpath(str(m))
        return None if m in self.mains.values() else m

    def main_hit(self, p: str) -> "tuple[str, str] | None":
        """(repo, worktree twin of p) when p lies in one of this run's main checkouts."""
        from core.run_worktree import wt_root
        if _under(p, os.path.realpath(str(wt_root()))):
            return None
        for name, main in self.mains.items():
            if _under(p, main):
                return name, os.path.join(self.wts[name], os.path.relpath(p, main))
        return None


def _v1_reserved(p: str, worktree: str, run_id: str) -> "str | None":
    """A v1 run's ``.aidlc/deliverables/`` is written only by its landing, which builds
    the REPORT into the delivery tree itself (fixing spec C4): an agent write there
    would land unreviewed bytes beside the REPORT or shadow it."""
    try:
        rel = os.path.relpath(p, os.path.realpath(worktree)).replace(os.sep, "/")
    except ValueError:
        return None
    if not (rel == ".aidlc/deliverables" or rel.startswith(".aidlc/deliverables/")):
        return None
    try:
        from config import get_app_data_dir
        from core.run_protocol.contract import is_v1_run
        if not is_v1_run(get_app_data_dir(), run_id):
            return None
    except Exception:   # unknown: the reserved path is still reserved
        logger.debug("v1 run check failed run=%s", run_id, exc_info=True)
    return (f"{p} is under .aidlc/deliverables/, which run {run_id}'s landing writes "
            "(the REPORT goes into the commit from the audit log); do not write it")


def _file_verdict(p: str, own: "_Own | None") -> "dict | None":
    from core.run_worktree import run_for_path
    hit = run_for_path(p)
    if hit is not None:
        rid, repo = hit
        if own is None:
            # a session that owns no run never writes into a run's worktree, whatever
            # mode it is in (re-audit 536bb62b: this used to be allowed)
            return _deny(f"{p} is inside run {rid}'s worktree, and this session owns no "
                         "run. Only that run's session writes there.")
        if rid != own.id or repo not in own.wts:
            return _deny(f"{p} is inside run {rid}'s worktree, not this session's run "
                         f"({own.id}). Write only under {', '.join(own.wts.values())}.")
        reserved = _v1_reserved(p, own.wts[repo], rid)
        if reserved:
            return _deny(reserved)
        try:
            st = os.lstat(p)
            if st.st_nlink > 1 and not os.path.islink(p):
                return _deny(f"{p} is a hard link (it shares its data with another file, "
                             "possibly in the main checkout); recreate it as a normal file "
                             "with `cp --remove-destination` style replacement first")
        except OSError:
            pass
        return None
    if own is not None:
        m = own.main_hit(p)
        if m is not None:
            note = _main_edit_note(own.mains[m[0]], p)
            return _deny(f"this session's run {own.id} works in its own worktree; do not "
                         f"write the main checkout. Use {m[1]} instead of {p}." + note)
        repo = own.unisolated_repo(p)
        if repo is not None:
            return _repo_add_deny(own, repo, p)
    return None


class _NoRun:
    """Rule 4 state of a session: *run* = its own live run that does not isolate (id,
    status, worktree_mode) or None."""

    def __init__(self, run: "dict | None" = None):
        self.run = run


def _session_runs_light(sid: str) -> "list[dict]":
    """This session's live v1 runs touched in the last 3 days, newest first. Owner,
    status and mode are the logged ones (``run_worktree._iter_runs``), never run.json's
    (re-audit 536bb62b: a forged ``worktree_mode: shared`` lifted rule 4)."""
    from core.run_worktree import _LIVE, _iter_runs
    from core.security_hooks import _pipeline_runs_root
    cutoff = time.time() - 3 * 86400
    out = []
    for f, d in _iter_runs(_pipeline_runs_root()):
        try:
            mtime = f.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff or d.get("session_id") != sid or d.get("status") not in _LIVE:
            continue
        out.append((mtime, {"id": d["id"], "project": d["project"], "status": d.get("status"),
                            "worktree_mode": d.get("worktree_mode")}))
    return [r for _t, r in sorted(out, key=lambda x: x[0], reverse=True)]


def _with_deadline(deadline: float, fn, *args):
    """Run *fn* with ``_TL.deadline`` set on this thread, cleared afterwards (also when
    *fn* raises), so a pooled thread never carries one judgment's budget into the next."""
    _TL.deadline = deadline
    try:
        return fn(*args)
    finally:
        _TL.deadline = None


def _budget_left(cap: float) -> float:
    """Seconds the optional deny hint (``_main_edit_note``) may spend on git: *cap*, or less
    when the gate's deadline (``_TL.deadline``, set by ``_decide_by``) minus
    ``_NOTE_MARGIN`` is nearer, so the hint can never push the judgment past
    ``_GATE_TIMEOUT``. Only the hint is budgeted: the checks a deny depends on keep their
    own fixed timeouts, and a short budget drops the hint, never the deny."""
    deadline = getattr(_TL, "deadline", None)
    return cap if deadline is None else min(cap, deadline - time.monotonic() - _NOTE_MARGIN)


_GIT_READ_FLAGS = frozenset({"--check", "--stat", "--numstat", "--summary",
                             "--show-current-patch"})


def _git_maintenance(sub: str, args: "list[str]", command: str = "") -> bool:
    """A git command that may run in a main checkout without a run: a read-only form,
    an abort of an operation in progress, or a fast-forward sync of the integration
    branch (it cannot rewrite anyone's uncommitted work: git refuses an overlapping
    fast-forward). ``stash`` / ``reset`` / ``checkout`` stay denied — in a shared
    checkout they move or discard other sessions' work."""
    if sub == "stash" and args[:1] in (["list"], ["show"]):
        return True
    if sub in ("apply", "am") and any(a in _GIT_READ_FLAGS for a in args) \
            and "--apply" not in args:      # --apply turns applying back on
        return True
    if sub in ("merge", "rebase", "cherry-pick", "revert", "am") and "--abort" in args:
        return True   # recovery: it rolls back whoever started the operation
    if sub not in ("pull", "merge") or "--ff-only" not in args:
        return False
    # a fast-forward that cannot touch uncommitted work and only follows upstream:
    # no autostash (it stashes every session's edits; also via -c or config), no
    # rebase/squash, and no target that is a run's branch or the repo itself ('.')
    if "autostash" in command.lower() or any(
            a in ("-r", "--squash", "--no-ff") or a.startswith("--rebase") for a in args):
        return False
    pos = [a for a in args if not a.startswith("-")]
    return not any(a == "." or a.startswith(("run/", "refs/heads/run/")) for a in pos)


def _no_run_git_verdict(g: dict, nr: "_NoRun", command: str = "") -> "dict | None":
    """Rule 4 for a git command that writes the work tree, index or HEAD of a project
    repo's main checkout (``_GIT_WRITES_MAIN``, minus _git_maintenance)."""
    sub, args = g["sub"], g["args"]
    if sub not in _GIT_WRITES_MAIN or _git_maintenance(sub, args, command):
        return None
    return _no_run_verdict(os.path.join(os.path.realpath(g["dir"]), ".swarm-git-op"), nr,
                           what=f"git {sub} in {g['dir']}")


def _no_run_verdict(p: str, nr: "_NoRun", what: "str | None" = None) -> "dict | None":
    """Rule 4 (module docstring) for one write target (*what* names a git command): a
    session without an isolating run may not write into a run's worktree. Its own main
    checkouts are its own business (design 2026-10-24-swarmai-usable-by-anyone §3.4)."""
    from core.run_worktree import run_for_path
    hit = run_for_path(p)
    if hit is not None:
        return _deny(f"{what or p} is inside run {hit[0]}'s worktree; this session does not "
                     "own that run. Write only in a worktree of a run of your own.")
    return None


def _main_edit_note(main: str, p: str) -> str:
    """The deny's extra line when *p* is already an uncommitted change in the main
    checkout *main* (untracked, modified, deleted, ...): the worktree is made from HEAD,
    so it does not have that version and "the same change in the worktree" cannot reach
    it (``V1_MAIN_EDIT_REMEDY``). '' for a clean path, a directory, a path
    outside *main*, or when git cannot tell in time. The git call gets what is left of
    the gate's budget (``_TL.deadline``) minus ``_NOTE_MARGIN``, at most ``_NOTE_CAP``,
    and is skipped below 0.2s: a hint must never push the judgment past the budget,
    where the gate fails open and the deny itself is lost."""
    from core.run_worktree import git
    rel = os.path.relpath(p, main)
    if rel == "." or rel.startswith(".." + os.sep) or rel == ".." or os.path.isdir(p):
        return ""
    if getattr(_TL, "no_note", False):
        return ""            # a pass whose deny is re-judged afresh: its note is discarded
    budget = _budget_left(_NOTE_CAP)
    if budget < 0.2:
        return ""
    try:
        r = git(main, "status", "--porcelain=v1", "-z", "--untracked-files=all",
                "--no-renames", "--", ":(literal)" + rel, timeout=budget,
                env={"GIT_OPTIONAL_LOCKS": "0"})
    except Exception:   # noqa: BLE001 — a hint only; the deny stands without it
        logger.debug("_main_edit_note: suppressed exception on a fail-safe path", exc_info=True)
        return ""
    if r.returncode != 0 or not r.stdout.strip("\0"):
        return ""
    return (f"\n{p} is already an uncommitted change in the main checkout, so the worktree "
            f"does not have it. {V1_MAIN_EDIT_REMEDY}")


# Pipeline v1 has no claim or repo-add command (repos are fixed at `create --repo`), so
# every hint about a main-checkout edit points at this one executable remedy.
V1_MAIN_EDIT_REMEDY = (
    "If this session wrote it, re-apply that change at the same relative path in the "
    "run's worktree and tell the user the main-checkout copy is theirs to discard; never "
    "delete or restore it yourself. If another session wrote it, leave it alone.")


def v1_command(args: str) -> str:
    """``"$SWARM_BACKEND_BIN" pipeline-v1 <args>``: the run CLI, the same packaged and in
    dev (design 2026-10-24-swarmai-usable-by-anyone §3.2; ``pipeline_command.command_text``).
    The variable names this backend's own program wherever the agent's cwd is."""
    from core import pipeline_command
    return pipeline_command.command_text(args)


# direct has no REQUIREMENTS document: create takes the user's request verbatim
_REQ = "--requirement '<the user request, verbatim>'"


def _repo_add_deny(own: "_Own", repo: str, what: str) -> dict:
    return _deny(f"{what} is in the git repo {repo}, which run {own.id} has no worktree for. "
                 "A v1 run's repos are fixed when it is created, so this change belongs in "
                 "a new run that includes it (one simple command, nothing chained):\n"
                 "Run: " + v1_command(
                     f"create --project {shlex.quote(str(own.project or '<project>'))} "
                     f"--profile direct {_REQ} --repo {shlex.quote(repo)}"))


def _git_segments(command: str, cwd: str) -> "list[dict]":
    """Each `git …` simple command → {sub, args, dir} with -C / --work-tree /
    --git-dir / GIT_DIR / GIT_WORK_TREE and leading cd/pushd applied."""
    from core.security_hooks import (_cd_segment_target, _gate_shell_segments,
                                     _skip_leading_wrappers)
    segs = _gate_shell_segments(command) or []
    cur, out = cwd, []
    for seg in segs:
        t = _cd_segment_target(seg, cur)
        if t is not None:
            cur = t or cur
            continue
        toks = _skip_leading_wrappers(seg)
        lead = seg[:len(seg) - len(toks)]
        if not toks or os.path.basename(toks[0]) != "git":
            continue
        d, k = cur, 1
        for a in lead:
            if a.startswith("GIT_WORK_TREE="):
                d = _real(a.split("=", 1)[1], cur)
            elif a.startswith("GIT_DIR="):
                d = os.path.dirname(_real(a.split("=", 1)[1], cur))
        while k < len(toks):
            a = toks[k]
            if a in ("-C", "--work-tree", "--git-dir") and k + 1 < len(toks):
                v = _real(toks[k + 1], d)
                d = os.path.dirname(v) if a == "--git-dir" else v
                k += 2
            elif a.startswith(("--work-tree=", "--git-dir=")):
                v = _real(a.split("=", 1)[1], d)
                d = os.path.dirname(v) if a.startswith("--git-dir") else v
                k += 1
            elif a.startswith("-C") and len(a) > 2:
                d, k = _real(a[2:], d), k + 1
            elif a in ("-c", "--namespace", "--exec-path") and k + 1 < len(toks):
                k += 2
            elif a.startswith("-"):
                k += 1
            else:
                break
        out.append({"sub": toks[k] if k < len(toks) else None, "args": toks[k + 1:], "dir": d})
    return out


def _ref_writes(sub: str, args: "list[str]") -> "list[str] | None":
    """Ref names a ref-changing git command writes; None = a read-only form."""
    pos = [a for a in args if not a.startswith("-")]
    flags = [a for a in args if a.startswith("-")]
    if sub == "update-ref":
        return pos[:1] or None
    if sub == "symbolic-ref":
        return pos[:1] if len(pos) >= 2 or "-d" in flags or "--delete" in flags else None
    if sub == "branch":
        reads = {"--list", "-l", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
                 "--show-current", "--contains", "--merged", "--no-merged", "--points-at",
                 "--format", "--sort", "--color", "--no-color", "--column"}
        if not pos or any(f.split("=")[0] in reads for f in flags):
            return None
        if any(f in ("-d", "-D", "--delete") for f in flags):
            return pos
        if any(f in ("-m", "-M", "-c", "-C", "--move", "--copy") for f in flags):
            return pos[:2]
        return pos[:1]  # create / -f: the second positional is a start point
    if sub == "tag":
        if not pos or any(f in ("-l", "--list", "-v", "--verify") for f in flags):
            return None
        return pos[:1] if "-d" not in flags and "--delete" not in flags else pos
    return None


def _norm_ref(name: str) -> str:
    if name.startswith("refs/"):
        return name
    return "refs/heads/" + name


def _git_verdict(g: dict, own: "_Own | None") -> "dict | None":
    from core.run_worktree import run_for_path, wt_root
    sub, args, d = g["sub"], g["args"], g["dir"]
    if not sub:
        return None
    in_wt = run_for_path(d)
    strict = in_wt is not None or own is not None
    root = os.path.realpath(str(wt_root()))
    # `stash list` / `stash show` only read refs/stash (denied 10-20 and 10-21)
    if sub == "stash" and in_wt is not None and not (args and args[0] in ("list", "show")):
        return _deny(STASH_DENY)
    if sub == "worktree" and args and args[0] in ("add", "remove", "move", "prune"):
        if strict or any(_under(_real(a, d), root) for a in args[1:] if not a.startswith("-")):
            return _deny("run worktrees are created and removed by the pipeline tools only")
    if sub in _REF_CMDS:
        names = _ref_writes(sub, args) or []
        own_ref = f"refs/heads/run/{own.id}" if own is not None else None
        for n in names:
            r = _norm_ref(n)
            if r.startswith("refs/heads/run/") and r != own_ref:
                return _deny(f"{r} belongs to another pipeline run")
            if strict and r != own_ref:
                return _deny(f"refs are shared by every worktree; {sub} on {r} from a run "
                             "worktree would bypass the integration (landing happens at "
                             "deliver stage-complete)")
    if strict:
        if sub == "config" and "--worktree" not in args and not any(
                a in _CONFIG_READ for a in args) and (
                len([a for a in args if not a.startswith("-")]) >= 2
                or any(a.startswith(("--unset", "--add", "--replace", "--rename", "--remove"))
                       for a in args)):
            return _deny("repository config is shared by every worktree; use "
                         "`git config --worktree` or ask the user")
        if (sub == "gc" and any(a.startswith("--prune") for a in args)) \
                or (sub == "reflog" and args[:1] == ["expire"]) or sub in _SHARED_STATE \
                or (sub == "push" and "." in args):
            return _deny(f"git {sub} changes state shared by every worktree")
    if own is not None and sub in _GIT_WRITES_MAIN:
        m = own.main_hit(os.path.realpath(d))
        if m is not None:
            return _deny(f"this session's run {own.id} works in its own worktree; run git "
                         f"{sub} in {own.wts[m[0]]}, not in the main checkout")
        repo = own.unisolated_repo(os.path.realpath(d))
        if repo is not None:
            return _repo_add_deny(own, repo, f"git {sub} in {d}")
    return None


def _bash_targets(command: str, cwd: str, with_raw: bool = False):
    from core.file_change_classifier import (parse_bash_delete_targets,
                                             parse_bash_heredoc_write_candidates,
                                             parse_bash_write_targets)
    from core.security_hooks import _cd_segment_target, _gate_shell_segments
    base = cwd
    late_cd = False   # a cd AFTER another command: relative targets have no single base
    other = False
    for seg in _gate_shell_segments(command) or []:
        t = _cd_segment_target(seg, base)
        if t is None:
            other = True
            continue
        late_cd = late_cd or other
        if t:
            base = t
    raw = (parse_bash_write_targets(command) + parse_bash_heredoc_write_candidates(command)
           + parse_bash_delete_targets(command))
    out = [_real(r, base) for r in dict.fromkeys(raw)]
    if not with_raw:
        return out
    sure = [not late_cd or os.path.isabs(os.path.expanduser(r)) for r in dict.fromkeys(raw)]
    return list(zip(out, sure))

def judge(input_data: "dict[str, Any]", own: "_Own | None",
          no_run: "_NoRun | None" = None) -> "dict | None":
    """The verdict for one tool call (None = allow without comment). *no_run*: rule 4
    applies (worktree mode on, no run isolating this session's writes)."""
    tool = input_data.get("tool_name")
    ti = input_data.get("tool_input") or {}
    cwd = input_data.get("cwd") or ""
    if tool in ("Read", "Grep"):
        p = ti.get(_PATH_KEYS[tool])
        m = own.main_hit(_real(p, cwd)) if own is not None and p else None
        if m is not None:
            return {"decision": "approve", "additionalContext":
                    f"Note: you are reading the main checkout; run {own.id}'s copy is {m[1]}."}
        return None
    if tool in _PATH_KEYS:
        p = ti.get(_PATH_KEYS[tool])
        if not p:
            return None
        v = _file_verdict(_real(p, cwd), own)
        return v if v is not None or no_run is None else _no_run_verdict(_real(p, cwd), no_run)
    if tool != "Bash":
        return None
    command = ti.get("command") or ""
    if not isinstance(command, str) or not command.strip():
        return None
    if "git" in command:
        for g in _git_segments(command, cwd):
            v = _git_verdict(g, own)
            if v is None and no_run is not None:
                v = _no_run_git_verdict(g, no_run, command)
            if v is not None:
                return v
    for p, sure in _bash_targets(command, cwd, with_raw=True):
        v = _file_verdict(p, own)
        if v is None and no_run is not None and sure:
            v = _no_run_verdict(p, no_run)
        if v is not None:
            return v
    return None


def _any_run_worktree() -> bool:
    from core.run_worktree import wt_root
    root = wt_root()
    try:
        return root.is_dir() and any(d.name.startswith("run_") for d in root.iterdir())
    except OSError:
        return False


def create_worktree_write_gate(session_context: "dict[str, Any]"):
    """Factory (module docstring). session_context: the mutable dict SessionUnit
    updates; ``sdk_session_id`` holds the app session id (read at call time). A context
    marked ``_unbound`` (hook_builder's fallback when no session context was given, e.g.
    a prewarm: its id is the agent id, no run can ever be linked to it) never gets
    rule 4 — it could never start the run the deny asks for. (Prewarms pass their unit's
    own context now, so after adoption they are judged as the real session.)"""
    from core import executors
    cache: dict = {}

    def _state(sid: str, mode_on: bool, any_wt: bool):
        from core.run_worktree import own_isolated_run
        from core.security_hooks import _pipeline_runs_root
        r = own_isolated_run(sid, _pipeline_runs_root()) if sid and any_wt else None
        own = _Own(r["id"], r["worktrees"], project=r.get("project"),
                   lazy=not r.get("settled") and r.get("status") in ("active", "reflecting")) if r else None
        nr = None
        if mode_on and sid and own is None and not session_context.get("_unbound"):
            runs = _session_runs_light(sid)
            if not any(x.get("worktree_mode") == "shared" for x in runs):
                nr = _NoRun(runs[0] if runs else None)   # a shared-mode run writes main as before
        return own, nr

    def _decide(sid: str, input_data: dict) -> "dict | None":
        from core.run_worktree import mode_enabled
        mode_on = mode_enabled()
        any_wt = _any_run_worktree()
        if not mode_on and not any_wt:
            return None
        if not any_wt and input_data.get("tool_name") in ("Read", "Grep"):
            return None      # rules 1–3 need a worktree; rule 4 never judges reads
        hit = cache.get(sid)
        if hit is None or time.monotonic() - hit[0] > _OWN_TTL:
            own, nr = _state(sid, mode_on, any_wt)
            cache.clear()
            cache[sid] = (time.monotonic(), own, nr)
            return judge(input_data, own, no_run=nr)
        # a cached deny is judged again afresh below: no hint in this pass (it would be
        # thrown away after spending the shared budget)
        _TL.no_note = True
        try:
            v = judge(input_data, hit[1], no_run=hit[2])
        finally:
            _TL.no_note = False
        if v is not None and v.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            cache.pop(sid, None)  # e.g. the run just made a worktree: judge afresh
            return _decide(sid, input_data)
        return v

    def _decide_by(deadline: float, sid: str, input_data: dict) -> "dict | None":
        # the deadline counts from submission, so time queued in the executor is spent too
        return _with_deadline(deadline, _decide, sid, input_data)

    async def _gate(input_data: "dict[str, Any]", tool_use_id: "str | None",
                    context: Any) -> "dict[str, Any]":
        # No verdict → {} (not "approve": an approve would skip can_use_tool's checks).
        if input_data.get("tool_name") not in _PATH_KEYS and input_data.get("tool_name") != "Bash":
            return {}
        try:
            from core.run_worktree import mode_enabled
            if not _any_run_worktree() and not mode_enabled():  # inline: off is free
                return {}
        except Exception:  # noqa: BLE001 — fail-open
            logger.debug("worktree_write_gate: WT_ROOT check failed", exc_info=True)
            return {}
        sid = str(session_context.get("sdk_session_id") or "")
        t0 = time.monotonic()
        try:
            v = await asyncio.wait_for(
                executors.run_in("gate", _decide_by, time.monotonic() + _GATE_TIMEOUT,
                                 sid, input_data), timeout=_GATE_TIMEOUT)
        except Exception as exc:  # incl. TimeoutError — fail-open, the sync check is the backstop
            ti = input_data.get("tool_input") or {}
            what = ti.get("command") or ti.get(_PATH_KEYS.get(input_data.get("tool_name"), "")) or ""
            logger.warning("worktree_write_gate: judgment failed after %.1fs — failing open "
                           "(%s %s): %s", time.monotonic() - t0, input_data.get("tool_name"),
                           str(what)[:120], type(exc).__name__)
            return {}
        if v is None:
            return {}
        if v.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" \
                and force_enabled("worktree_write_gate", input_data):
            logger.warning("[worktree-gate] FORCE override (%s): %s", sid,
                           v["hookSpecificOutput"]["permissionDecisionReason"][:200])
            return {}
        if "hookSpecificOutput" in v:
            logger.warning("[BLOCKED] worktree_write_gate (%s): %s", sid,
                           v["hookSpecificOutput"]["permissionDecisionReason"][:200])
        return v

    return _gate
