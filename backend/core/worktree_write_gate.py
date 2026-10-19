"""worktree_write_gate — PreToolUse gate that keeps an isolated run's writes inside
its own worktree (docs/design/pipeline-run-worktree.md §6.1).

Matcher ``Write|Edit|MultiEdit|NotebookEdit|Bash|Read|Grep``, its own hook group (not
the 4-hour Bash chain), bounded by ``_GATE_TIMEOUT``, fail-open on its own errors,
operator bypass ``SWARM_WORKTREE_GATE_FORCE=1`` in the daemon environment. Zero cost
while no run worktree exists. Judgments:

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
import time
from typing import Any

logger = logging.getLogger(__name__)

_GATE_TIMEOUT = 3.0
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
    and what is needed to print its ``run-repo-add`` command (project, cli record)."""

    def __init__(self, run_id: str, wts: "list[tuple[str, str]]", project: "str | None" = None,
                 cli: "dict | None" = None, lazy: bool = True):
        from core.run_worktree import main_of
        self.id = run_id
        self.project = project
        self.cli = cli
        self.lazy = lazy   # may still isolate new repos (running, nothing landed yet)
        self.wts = dict(wts)
        self.mains = {}
        for name, path in wts:
            m = main_of(path)
            if m is not None:
                self.mains[name] = os.path.realpath(str(m))

    def unisolated_repo(self, p: str) -> "str | None":
        """The main checkout of a git repo *p* lies in that this run has no worktree for
        yet but could have one (run_worktree.repo_for_write) — the write that must wait
        for ``run-repo-add``. None: not a repo, not isolatable, or already isolated."""
        from core.run_worktree import repo_for_write
        from core.security_hooks import _pipeline_runs_root
        if not self.lazy:
            return None
        m = repo_for_write(p, _pipeline_runs_root())
        if m is None:
            return None
        m = os.path.realpath(str(m))
        return None if m in self.mains.values() else m

    def repo_add_command(self, main: str) -> str:
        args = (f"run-repo-add --project {shlex.quote(str(self.project or '<project>'))} "
                f"--run-id {self.id} --path {shlex.quote(main)}")
        return cli_command(self.cli, args)


    def main_hit(self, p: str) -> "tuple[str, str] | None":
        """(repo, worktree twin of p) when p lies in one of this run's main checkouts."""
        from core.run_worktree import wt_root
        if _under(p, os.path.realpath(str(wt_root()))):
            return None
        for name, main in self.mains.items():
            if _under(p, main):
                return name, os.path.join(self.wts[name], os.path.relpath(p, main))
        return None


def cli_command(cli: "dict | None", args: str, script: str = "artifact_cli.py") -> str:
    """``<python> <scripts dir>/<script> <args>`` from a run's recorded CLI (run.json
    "cli", written by artifact_cli itself at run-create and every stage-start). run.json
    is untrusted, so the record is used only when it names an absolute, executable python
    interpreter and an existing absolute artifact_cli.py outside every run worktree;
    otherwise the main-checkout-relative form is printed (a run created before the CLI
    recorded itself, until its next stage-start)."""
    from core.security_hooks import _PY_INTERP_RE
    py = (cli or {}).get("python") if isinstance(cli, dict) else None
    sc = (cli or {}).get("script") if isinstance(cli, dict) else None
    if (isinstance(py, str) and isinstance(sc, str) and os.path.isabs(py) and os.path.isabs(sc)
            and _PY_INTERP_RE.match(os.path.basename(py))
            and os.path.basename(sc) == "artifact_cli.py" and os.path.isfile(sc)
            and os.access(py, os.X_OK)):
        from core.run_worktree import run_for_path
        sdir = os.path.dirname(os.path.realpath(sc))
        target = os.path.join(sdir, script)
        # the record must look like the real CLI tree, not a planted pair of files
        if (run_for_path(sc) is None and os.path.isfile(target)
                and os.path.isfile(os.path.join(sdir, "pipeline_worktree.py"))
                and os.path.isfile(os.path.join(os.path.dirname(sdir), "core", "run_worktree.py"))):
            return f"{shlex.quote(py)} {shlex.quote(target)} {args}"
    return f"backend/.venv/bin/python backend/scripts/{script} {args}"


def _file_verdict(p: str, own: "_Own | None") -> "dict | None":
    from core.run_worktree import run_for_path
    hit = run_for_path(p)
    if hit is not None:
        if own is None:
            return None
        rid, repo = hit
        if rid != own.id or repo not in own.wts:
            return _deny(f"{p} is inside run {rid}'s worktree, not this session's run "
                         f"({own.id}). Write only under {', '.join(own.wts.values())}.")
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
            return _deny(f"this session's run {own.id} works in its own worktree; do not "
                         f"write the main checkout. Use {m[1]} instead of {p}.")
        repo = own.unisolated_repo(p)
        if repo is not None:
            return _repo_add_deny(own, repo, p)
    return None


def _repo_add_deny(own: "_Own", repo: str, what: str) -> dict:
    return _deny(f"{what} is in the git repo {repo}, which run {own.id} has no worktree for "
                 "yet. Give it one first (one simple command, nothing chained):\n"
                 f"Run: {own.repo_add_command(repo)}\n"
                 "then make this change at the same relative path under the worktree it "
                 "prints.")


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
    if sub == "stash" and in_wt is not None:
        return _deny("git stash is shared by every worktree (refs/stash): a stash made here "
                     "can be popped into the main checkout. To look at base, use "
                     "`artifact_cli.py run-diff --mark <name>` and `run-diff --since <name>`.")
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


def _bash_targets(command: str, cwd: str) -> "list[str]":
    from core.file_change_classifier import (parse_bash_delete_targets,
                                             parse_bash_heredoc_write_candidates,
                                             parse_bash_write_targets)
    from core.security_hooks import _cd_segment_target, _gate_shell_segments
    base = cwd
    for seg in _gate_shell_segments(command) or []:
        t = _cd_segment_target(seg, base)
        if t:
            base = t
    raw = (parse_bash_write_targets(command) + parse_bash_heredoc_write_candidates(command)
           + parse_bash_delete_targets(command))
    return [_real(r, base) for r in dict.fromkeys(raw)]

def judge(input_data: "dict[str, Any]", own: "_Own | None") -> "dict | None":
    """The verdict for one tool call (None = allow without comment)."""
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
        return _file_verdict(_real(p, cwd), own) if p else None
    if tool != "Bash":
        return None
    command = ti.get("command") or ""
    if not isinstance(command, str) or not command.strip():
        return None
    if "git" in command:
        for g in _git_segments(command, cwd):
            v = _git_verdict(g, own)
            if v is not None:
                return v
    for p in _bash_targets(command, cwd):
        v = _file_verdict(p, own)
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
    updates; ``sdk_session_id`` holds the app session id (read at call time)."""
    from core import executors
    cache: dict = {}

    def _decide(sid: str, input_data: dict) -> "dict | None":
        if not _any_run_worktree():
            return None
        hit = cache.get(sid)
        if hit is None or time.monotonic() - hit[0] > _OWN_TTL:
            from core.run_worktree import own_isolated_run
            from core.security_hooks import _pipeline_runs_root
            r = own_isolated_run(sid, _pipeline_runs_root()) if sid else None
            own = _Own(r["id"], r["worktrees"], project=r.get("project"), cli=r.get("cli"),
                       lazy=not r.get("settled") and r.get("status") == "running") if r else None
            cache.clear()
            cache[sid] = (time.monotonic(), own)
            return judge(input_data, own)
        v = judge(input_data, hit[1])
        if v is not None and v.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
            cache.pop(sid, None)  # e.g. run-repo-add just added a worktree: judge afresh
            return _decide(sid, input_data)
        return v

    async def _gate(input_data: "dict[str, Any]", tool_use_id: "str | None",
                    context: Any) -> "dict[str, Any]":
        # No verdict → {} (not "approve": an approve would skip can_use_tool's checks).
        if input_data.get("tool_name") not in _PATH_KEYS and input_data.get("tool_name") != "Bash":
            return {}
        try:
            if not _any_run_worktree():  # inline, no thread hop: the common case is free
                return {}
        except Exception:  # noqa: BLE001 — fail-open
            logger.debug("worktree_write_gate: WT_ROOT check failed", exc_info=True)
            return {}
        sid = str(session_context.get("sdk_session_id") or "")
        try:
            v = await asyncio.wait_for(
                executors.run_in("subprocess", _decide, sid, input_data), timeout=_GATE_TIMEOUT)
        except Exception:  # incl. TimeoutError — fail-open, the sync check is the backstop
            logger.warning("worktree_write_gate: judgment failed — failing open", exc_info=True)
            return {}
        if v is None:
            return {}
        if v.get("hookSpecificOutput", {}).get("permissionDecision") == "deny" \
                and os.environ.get("SWARM_WORKTREE_GATE_FORCE") == "1":
            logger.warning("[worktree-gate] FORCE override (%s): %s", sid,
                           v["hookSpecificOutput"]["permissionDecisionReason"][:200])
            return {}
        if "hookSpecificOutput" in v:
            logger.warning("[BLOCKED] worktree_write_gate (%s): %s", sid,
                           v["hookSpecificOutput"]["permissionDecisionReason"][:200])
        return v

    return _gate
