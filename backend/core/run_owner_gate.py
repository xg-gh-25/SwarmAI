"""run_owner_gate — PreToolUse (Bash) gate for run ownership and the one-time hook
token (docs/design/pipeline-run-worktree.md §4.2).

For an ``artifact_cli`` / ``pipeline_test`` command that writes run state with
``--run-id R`` (active only while ``worktree_mode`` is on, or R is isolated):

- **takeover**: R's owner (``run.session_id``) is another session → if that session
  is not STREAMING / WAITING_INPUT, this session takes the run over (old owner to
  ``session_history[]``, session→run map rewritten); if it is, DENY.
- **token**: for the commands that may change worktree state (every stage-start: it
  creates, rebuilds from the ledger or syncs the worktree; stage-complete of deliver;
  run-update to a terminal status) the CLI call must be a top-level simple command
  (_token_binding): output plumbing may follow it (``| tail``, ``> f; python3 -c …``),
  and ``&&`` / ``;`` / newline-joined commands may precede it when each runs python or
  a simple file tool (_PREFIX_PROGRAMS), none names the CLI scripts, there is no
  substitution or subshell, and after a ``cd`` the CLI uses absolute paths
  (docs/design/pipeline-gate-expert-simplification.md G2); the gate issues a
  token (recorded only in the daemon, ``core.hook_token``) and puts
  ``SWARM_HOOK_TOKEN=<token>`` right before the CLI call via ``updatedInput``. An
  assignment prefix binds to that one simple command only, so nothing before or after
  it in the line receives the token. The CLI burns
  it through the daemon's socket — proof the hook really ran (an environment variable
  is inherited by a hookless ``claude --print --bare`` child).
- **after approval** (``AFTER_APPROVAL``: ``run-claim-main``): a command the user must
  approve first (dangerous_command_gate, which may wait hours) gets its token only
  AFTER that approval, so the token's TTL is not spent waiting for the user. The gate
  is registered twice: ``phase="check"`` before the approval gate (every check and
  deny, so the user is never asked to approve a command this gate refuses; no token
  for these commands) and ``phase="approved"`` right after it (the same checks, then
  the token, for these commands only). For a claim the check phase also refuses a
  session that does not own the run and dry-runs ``run_worktree.plan_claim`` (the CLI's
  own planner), so the user is only asked about a claim that will run.

Fail-open on its own errors (never blocks a command because the gate broke).
Public: ``create_run_owner_gate``, ``parse_cli_calls``, ``TOKEN_SUBCOMMANDS``,
``AFTER_APPROVAL``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9_]{1,64}$")
# Commands that act as the run's owner: only these take a run over (an incidental
# report / observe / test call from another tab must not steal it).
_TAKEOVER = frozenset({"stage-start", "stage-complete", "run-resume", "run-update",
                       "run-checkpoint", "run-commit"})


def _safe_project(p: str) -> bool:
    """A project name the gate may build a path from (the CLI's own
    _validate_path_identifiers is the authority; this only refuses traversal)."""
    return bool(p) and len(p) <= 128 and p not in (".", "..") and not any(
        ch in p for ch in ("/", "\\", "\0"))


def _isolated(proj, rid) -> bool:
    from core.security_hooks import _pipeline_runs_root
    if not (proj and rid and _RUN_ID_RE.fullmatch(str(rid)) and _safe_project(str(proj))):
        return False
    f = Path(_pipeline_runs_root()) / "Projects" / proj / ".artifacts" / "runs" / rid / "run.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")).get("worktree_mode") == "isolated"
    except (OSError, ValueError):
        return False
_WRITING = frozenset({
    "run-update", "run-commit", "run-checkpoint", "run-resume", "run-report",
    "stage-start", "stage-complete", "publish", "run-observe", "run-diff",
    "run-surface-changes", "run-repo-add", "run-claim-main",
})
# The stages whose first start decides an undecided run's mode (pipeline_worktree).
_DECIDING_STAGES = frozenset({"plan", "build", "goal_cycle"})
_TERMINAL = frozenset({"completed", "complete", "abandoned", "failed", "cancelled"})
TOKEN_SUBCOMMANDS = ("stage-start", "stage-complete", "run-update", "run-repo-add",
                     "run-claim-main")
# Token subcommands the user approves first: their token is issued by the
# phase="approved" registration, after dangerous_command_gate (module docstring).
AFTER_APPROVAL = frozenset({"run-claim-main"})
_DECIDE_TIMEOUT = 4.0
# Variables that change where / whether the CLI isolates: the gate and the CLI must agree.
_STEERING_ENV = ("SWARM_WORKTREE_MODE", "SWARM_WORKTREE_ROOT", "SWARM_WORKTREE_LEDGER",
                 "SWARM_DATA_DIR")
_SUBST = ("$(", "`", "<(", ">(")


def _opt_all(tokens: list[str], name: str) -> "list[str]":
    """Every value given for option *name* (exact spelling) — a repeated option is
    judged on ALL its values, since argparse acts on the last one."""
    out = []
    for i, t in enumerate(tokens):
        if t == name and i + 1 < len(tokens):
            out.append(tokens[i + 1])
        elif t.startswith(name + "="):
            out.append(t[len(name) + 1:])
    return out


def _opt(tokens: list[str], name: str) -> "str | None":
    for i, t in enumerate(tokens):
        if t == name and i + 1 < len(tokens):
            return tokens[i + 1]
        if t.startswith(name + "="):
            return t[len(name) + 1:]
    return None


def parse_cli_calls(command: str) -> "tuple[list[dict], bool]":
    """(calls, single). One dict per simple command that runs artifact_cli.py /
    pipeline_test.py: {tool, sub, run_id, project, stage, status, assigns}. *single* is
    True iff the whole command is exactly one simple command with no substitution."""
    from core.security_hooks import _gate_shell_segments, _skip_leading_wrappers
    seps: list[str] = []
    segments = _gate_shell_segments(command, seps)
    if segments is None:
        return [], False
    single = len(segments) == 1 and seps == [""] and not any(m in command for m in _SUBST)
    from core.security_hooks import _PY_INTERP_RE, _unwrap_program
    calls = []
    for seg in segments:
        if not any(os.path.basename(t) in _SCRIPTS for t in seg):
            continue
        prog = _unwrap_program(seg)
        # The call only counts when a python interpreter RUNS the script (`python
        # [-flags] …/artifact_cli.py`): a script path that is merely an argument (e.g. of
        # `claude --print`) must never receive the token.
        j = 1
        while prog and j < len(prog) and prog[j].startswith("-") and prog[j] not in ("-c", "-m"):
            j += 1
        runs_it = (bool(prog) and _PY_INTERP_RE.match(os.path.basename(prog[0])) is not None
                   and j < len(prog) and os.path.basename(prog[j]) in _SCRIPTS)
        lead = seg[:len(seg) - len(_skip_leading_wrappers(seg))]
        if not runs_it:
            calls.append({"tool": "foreign", "sub": None, "run_id": None, "project": None,
                          "stage": None, "status": None, "ambiguous": False, "assigns": []})
            continue
        tool = os.path.basename(prog[j])[:-3]
        rest = prog[j + 1:]
        sub = next((t for t in rest if not t.startswith("-")), None) if tool == "artifact_cli" else "run"
        calls.append({"tool": tool, "sub": sub, "run_id": _opt(rest, "--run-id"),
                      "project": _opt(rest, "--project"), "stage": _opt(rest, "--stage"),
                      "status": _opt(rest, "--status"), "ambiguous": _ambiguous(rest),
                      "assigns": [t.split("=", 1)[0] for t in lead if "=" in t],
                      "argv": list(rest), "run_ids": _opt_all(rest, "--run-id"),
                      "projects": _opt_all(rest, "--project")})
    return calls, single


_SCRIPTS = ("artifact_cli.py", "pipeline_test.py")
_KEY_OPTS = ("--run-id", "--project", "--stage", "--status")


def _ambiguous(rest: list[str]) -> bool:
    """A key option given twice, or abbreviated (argparse would take the last one / a
    prefix, while this gate reads the first exact one)."""
    seen: dict[str, int] = {}
    for t in rest:
        name = t.split("=", 1)[0]
        if name in _KEY_OPTS:
            seen[name] = seen.get(name, 0) + 1
        elif name.startswith("--") and len(name) > 2 and any(
                k.startswith(name) and k != name for k in _KEY_OPTS):
            return True
    return any(n > 1 for n in seen.values())


def _needs_token(call: dict) -> "str | None":
    if call["tool"] != "artifact_cli":
        return None
    sub = call["sub"]
    if sub == "stage-start":
        return "stage-start"
    if sub == "stage-complete" and call.get("stage") == "deliver":
        return "stage-complete"
    if sub == "run-update" and call.get("status") in _TERMINAL:
        return "run-update"
    if sub in ("run-repo-add", "run-claim-main"):
        return sub
    return None


def _scope(data: dict) -> "str | None":
    """How far the gate applies to a run (§16.9): "isolated" (everything below);
    "undecided" with the switch on (only the stage-start that decides the mode); None
    for a run recorded shared, an ended undecided run, or the switch off and not
    isolated — that run is treated exactly as with the switch off."""
    from core.run_worktree import mode_enabled
    mode = data.get("worktree_mode")
    if mode == "isolated":
        return "isolated"
    if mode is None and data.get("status") not in _TERMINAL and mode_enabled():
        return "undecided"
    return None


def _any_named_run_applies(call: dict) -> bool:
    from core.run_worktree import mode_enabled
    from core.security_hooks import _pipeline_runs_root
    rids = call.get("run_ids") or [call.get("run_id")]
    projs = call.get("projects") or [call.get("project")]
    for proj in projs:
        for rid in rids:
            if not (rid and proj and _RUN_ID_RE.fullmatch(str(rid)) and _safe_project(str(proj))):
                if mode_enabled():
                    return True
                continue
            f = (Path(_pipeline_runs_root()) / "Projects" / proj / ".artifacts" / "runs" / rid
                 / "run.json")
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if mode_enabled():
                    return True
                continue
            scope = _scope(d) if isinstance(d, dict) else None
            if scope == "isolated":
                return True
            if scope == "undecided" and call.get("sub") == "stage-start":
                stages = _opt_all(call.get("argv") or [], "--stage")
                # every --stage value counts; none visible (abbreviated) → refuse
                if not stages or any(st in _DECIDING_STAGES for st in stages):
                    return True
    return False


def _dedup_last(call: dict) -> dict:
    """The call with each repeated key option given once, at its LAST value (what argparse
    acts on), in the position of its first occurrence."""
    argv = list(call.get("argv") or [])

    def opt(i: int) -> "tuple[str | None, str | None, int]":   # (name, value, width)
        t = argv[i]
        if t in _KEY_OPTS and i + 1 < len(argv):
            return t, argv[i + 1], 2
        n = t.split("=", 1)[0]
        return (n, t.split("=", 1)[1], 1) if n in _KEY_OPTS and "=" in t else (None, None, 1)

    last, i = {}, 0
    while i < len(argv):
        n, v, w = opt(i)
        if n:
            last[n] = v
        i += w
    out, done, i = [], set(), 0
    while i < len(argv):
        n, _v, w = opt(i)
        if n:
            if n not in done:
                out += [n, last[n]]
                done.add(n)
        else:
            out.append(argv[i])
        i += w
    return dict(call, argv=out)

# Programs a segment BEFORE the token-carrying CLI call may run
# (docs/design/pipeline-gate-expert-simplification.md G2). Shell builtins that change
# how the next command is found or what it inherits (alias, function definitions,
# source / ., eval, export, set, trap, declare, hash, enable, builtin, command, a bare
# assignment) are not on it, so they are refused. A python script can still do anything,
# but so can the same script run in the previous Bash call: requiring the CLI to be the
# first command never closed that, it only cost a turn (31 denies in 2 days).
_PREFIX_PROGRAMS = frozenset({
    "cat", "echo", "printf", "mkdir", "cp", "mv", "ls", "grep", "sed", "jq", "head",
    "tail", "test", "[", "true", "cd", "touch", "tee", "wc", "sort", "diff",
})
_DELIM_RE = re.compile(r"<<(?P<dash>-?)[ \t]*(?P<q>['\"]?)(?P<delim>[A-Za-z_]\w*)(?P=q)")


def _top_spans(command: str) -> "tuple[list[tuple[int, int]], list[tuple[int, int, bool]]] | None":
    """Quote- and heredoc-aware split of *command* at top-level ``&&`` / ``;`` /
    newline. Returns (spans as (start, end) offsets, heredoc bodies as (start, end,
    quoted)), or None when the text cannot be read (unterminated quote or heredoc, a
    background ``&``, or a subshell / group bracket outside quotes)."""
    spans, bodies, pending = [], [], []
    i, n, start = 0, len(command), 0
    q = None
    while i < n:
        c = command[i]
        if q == "'":
            if c == "'":
                q = None
            i += 1
            continue
        if q == '"':
            if c == "\\":
                i += 2
                continue
            if c == '"':
                q = None
            i += 1
            continue
        if c == "\\":
            i += 2
            continue
        if c in ("'", '"'):
            q = c
            i += 1
            continue
        if command.startswith("<<", i) and not command.startswith("<<<", i):
            m = _DELIM_RE.match(command, i)
            if not m:
                return None
            pending.append((m.group("delim"), bool(m.group("dash")), bool(m.group("q"))))
            i = m.end()
            continue
        if c == "\n":
            spans.append((start, i))
            i += 1
            for delim, dash, quoted in pending:
                b0 = i
                while True:
                    if i > n:
                        return None
                    nl = command.find("\n", i)
                    line = command[i:] if nl == -1 else command[i:nl]
                    if (line.lstrip("\t") if dash else line) == delim:
                        bodies.append((b0, i, quoted))
                        i = n if nl == -1 else nl + 1
                        break
                    if nl == -1:
                        return None
                    i = nl + 1
            pending = []
            start = i
            continue
        if command.startswith("&&", i):
            spans.append((start, i))
            i += 2
            start = i
            continue
        if command.startswith("||", i):
            i += 2
            continue
        if c == "&":
            if command.startswith("&>", i) or (i and command[i - 1] in "<>"):
                i += 1
                continue
            return None
        if c in "(){}":
            return None
        if c == ";":
            spans.append((start, i))
            i += 1
            start = i
            continue
        i += 1
    if q or pending:
        return None
    spans.append((start, n))
    return spans, bodies


def _prefix_ok(prefix: str, bodies: "list[tuple[int, int, bool]]") -> bool:
    """Every simple command of *prefix* runs an allowed program (_PREFIX_PROGRAMS or a
    python interpreter), no substitution appears outside a quoted heredoc body, and
    nothing in it names the CLI scripts (so no write to them is visible)."""
    from core.security_hooks import _PY_INTERP_RE, _gate_shell_segments, _unwrap_program
    if any(os.path.basename(x) in prefix or x in prefix for x in _SCRIPTS):
        return False
    visible, k = [], 0
    for b0, b1, quoted in sorted(bodies):
        if b0 >= len(prefix):
            break
        if quoted:
            visible.append(prefix[k:b0])
            k = min(b1, len(prefix))
    visible.append(prefix[k:])
    if any(x in "".join(visible) for x in _SUBST):
        return False
    segs = _gate_shell_segments(prefix)
    if segs is None:
        return False
    for seg in segs:
        if not seg:
            continue
        prog = _unwrap_program(seg)
        if not prog:
            return False   # a bare assignment changes what later commands inherit
        base = os.path.basename(prog[0])
        if base not in _PREFIX_PROGRAMS and _PY_INTERP_RE.match(base) is None:
            return False
    return True


def _token_binding(command: str) -> "tuple[str, str] | None":
    """(prefix, rest) such that ``prefix + TOKEN=… + rest`` hands the token to the CLI
    call that starts *rest* and to nothing else, or None.

    *rest* starts with the CLI call — no subshell or substitution anywhere in it — and
    may continue with any output plumbing. *prefix* is zero or more top-level ``&&`` /
    ``;`` / newline-separated commands before it, each passing _prefix_ok. After a
    ``cd`` the CLI's interpreter and script must be absolute paths (the ``cd`` must not
    change which script runs)."""
    from core.security_hooks import _gate_shell_segments, _unwrap_program
    parsed = _top_spans(command)
    if parsed is None:
        return None
    spans, bodies = parsed
    cli_at = None
    for s0, s1 in spans:
        if any(c["tool"] != "foreign" for c in parse_cli_calls(command[s0:s1])[0]):
            cli_at = s0
            break
    if cli_at is None:
        return None
    head, rest = command[:cli_at], command[cli_at:]
    stripped = rest.lstrip()
    head += rest[:len(rest) - len(stripped)]
    rest = stripped
    if not rest or any(x in rest for x in _SUBST):
        return None
    seps: list[str] = []
    segs = _gate_shell_segments(rest, seps)
    if not segs or seps[0] != "" or not any(os.path.basename(t) in _SCRIPTS for t in segs[0]):
        return None
    if head.strip():
        if not _prefix_ok(head, bodies):
            return None
        prog = _unwrap_program(segs[0])
        script = next((t for t in prog[1:] if os.path.basename(t) in _SCRIPTS), "")
        # nothing before the CLI may name the directory its script is loaded from (a
        # glob or a directory target writes into it without naming the script)
        sdir = os.path.dirname(script)
        dirs = {d for d in (sdir, os.path.realpath(sdir) if sdir else "") if d}
        if any(d in head for d in dirs):
            return None
        pre = _gate_shell_segments(head) or []
        if not any(_unwrap_program(x)[:1] == ["cd"] for x in pre if x):
            return head, rest
        if not (prog and os.path.isabs(prog[0]) and os.path.isabs(script)):
            return None
    return head, rest


def _single_form(data: dict, call: dict) -> str:
    """The same call as ONE simple command with absolute paths, for a deny message."""
    from core.worktree_write_gate import cli_command
    return cli_command(data.get("cli"), shlex.join(call.get("argv") or []))


def _owner_live(owner: str) -> "bool | None":
    """True iff the owner session is STREAMING / WAITING_INPUT; None = unknown."""
    try:
        from core import session_registry
        from core.session_unit import SessionState
        router = session_registry.session_router
        if router is None:
            return None
        unit = router.get_unit(owner)
        return unit is not None and unit.state in (SessionState.STREAMING,
                                                   SessionState.WAITING_INPUT)
    except Exception:  # noqa: BLE001 — registry edge: unknown, caller fails safe
        logger.debug("run_owner_gate: owner liveness unknown", exc_info=True)
        return None


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": "DENY (run owner gate): " + reason}}


def _takeover(run_file: Path, run_id: str, sid: str) -> "str | None":
    """Make *sid* the owner; returns a deny reason when the owner is still live."""
    from core.run_lock import update_run
    holder: dict = {}

    def m(d: dict) -> bool:
        owner = d.get("session_id")
        if owner == sid:
            return False
        if owner:
            live = _owner_live(owner)
            if live is None:
                return False  # cannot tell → leave ownership alone (fail-open)
            if live:
                holder["deny"] = (f"run {run_id} is being worked on by session {owner} "
                                  "right now; wait for it to stop, or work on another run")
                return False
            hist = d.get("session_history") if isinstance(d.get("session_history"), list) else []
            hist.append({"session_id": owner, "until": time.time(), "reason": "takeover"})
            d["session_history"] = hist
        d["session_id"] = sid
        d["session_id_source"] = "takeover" if owner else "backend_stamp"
        holder["took"] = True
        return True
    update_run(run_file, m, timeout=3.0)
    if holder.get("took"):
        try:
            from core.runtime_hooks import write_session_run_map
            write_session_run_map(sid, run_id)
        except Exception:  # noqa: BLE001 — the map is a hint; run.json is the truth
            logger.warning("run_owner_gate: session map not rewritten for %s", run_id)
        logger.warning("[run-owner-gate] session %s took over run %s", sid, run_id)
    return holder.get("deny")


def _claim_files(argv: "list[str]") -> "tuple[list[str], str]":
    """(--files values, --resolve) of a run-claim-main argv (argparse nargs='+')."""
    files, resolve, i = [], "merge", 0
    while i < len(argv):
        t = argv[i]
        if t == "--files":
            i += 1
            while i < len(argv) and not argv[i].startswith("--"):
                files.append(argv[i])
                i += 1
            continue
        if t == "--resolve" and i + 1 < len(argv):
            resolve = argv[i + 1]
        elif t.startswith("--resolve="):
            resolve = t.split("=", 1)[1]
        i += 1
    return files, resolve


def _claim_refusal(sid: str, rid: str, data: dict, c: dict) -> "str | None":
    """Why a run-claim-main would fail, judged BEFORE the user is asked to approve it:
    another session's run, or a refusal of the CLI's own planner (run_worktree.plan_claim
    dry run against the run's active worktrees). None: ask the user."""
    from core import run_worktree as rw
    owner = data.get("session_id")
    if owner and owner != sid:
        return (f"run {rid} belongs to session {owner}; only that session can claim "
                "main-checkout edits into it")
    files, resolve = _claim_files(c.get("argv") or [])
    active = [(e, Path(os.path.realpath(str(e.get("main"))))) for e in rw.active_entries(data)
              if e.get("main")]
    if not files or not active:
        return None   # the CLI reports these (argparse / not_isolated)
    try:
        rw.plan_claim(rid, active, files, resolve if resolve in rw.CLAIM_RESOLVES else "merge")
    except rw.WorktreeError as err:
        return f"this claim would be refused ({err.code}): {err.message}"[:1200]
    return None


def _decide(sid: str, command: str, tool_input: dict,
            phase: str = "check") -> "dict | None":
    from core import hook_token
    from core.run_worktree import mode_enabled
    from core.security_hooks import _pipeline_runs_root
    calls, single = parse_cli_calls(command)
    rewrite = None
    for c in calls:
        if c["tool"] == "foreign":
            continue
        rid, proj = c.get("run_id"), c.get("project")
        known = bool(rid and proj and _RUN_ID_RE.fullmatch(rid) and _safe_project(proj))
        data = None
        if known:
            run_file = (Path(_pipeline_runs_root()) / "Projects" / proj / ".artifacts" / "runs"
                        / rid / "run.json")
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
        scope = _scope(data) if isinstance(data, dict) else None
        applies = scope == "isolated" or (scope == "undecided" and c["tool"] == "artifact_cli"
                                          and c["sub"] == "stage-start"
                                          and c.get("stage") in _DECIDING_STAGES)
        if c.get("ambiguous") and _any_named_run_applies(c):
            # a repeated / abbreviated key option: argparse acts on another value than the
            # one read above, so every value counts — any run the gate applies to, or one
            # it cannot read while the switch is on, refuses the call
            return _deny("give --run-id / --project / --stage / --status exactly once and "
                         "unabbreviated, as ONE command.\nRun instead (the values argparse "
                         f"would have used): {_single_form(data or {}, _dedup_last(c))}")
        if data is None or not applies:
            continue
        if c["tool"] == "artifact_cli" and c["sub"] not in _WRITING:
            continue
        late = _needs_token(c) in AFTER_APPROVAL
        if phase == "approved" and not late:
            continue   # the check phase already handled it
        if c["tool"] == "artifact_cli" and c["sub"] in _TAKEOVER:
            reason = _takeover(run_file, rid, sid)
            if reason:
                return _deny(reason)
        sub = _needs_token(c)
        if sub is None:
            continue
        steer = [a for a in c["assigns"] if a in _STEERING_ENV]
        if steer or any(e + "=" in command for e in _STEERING_ENV):
            return _deny("do not set " + ", ".join(steer or _STEERING_ENV) + " on a pipeline "
                         f"command; worktree mode is a setting.\nRun instead: {_single_form(data, c)}")
        if hook_token.TOKEN_ENV in c["assigns"] or hook_token.TOKEN_ENV + "=" in command:
            return _deny(f"do not set {hook_token.TOKEN_ENV} yourself; the hook issues it.\n"
                         f"Run instead: {_single_form(data, c)}")
        binding = _token_binding(command)
        first = parse_cli_calls(binding[1])[0][:1] if binding else []
        if (binding is None or not first or first[0].get("sub") != c.get("sub")
                or first[0].get("run_id") != c.get("run_id")
                or sum(1 for x in calls if x["tool"] != "foreign" and _needs_token(x)) != 1):
            return _deny("this command changes the run's worktree state, so the hook hands "
                         "it a one-time token. Commands before it (joined by `&&`, `;` or a "
                         "newline) may only run python or simple file tools (cat, cp, mkdir, "
                         "sed, jq, cd, ...), must not name artifact_cli.py / pipeline_test.py, "
                         "and after a `cd` the CLI needs absolute paths. No `source`, `eval`, "
                         "`export`, `trap`, functions, subshells or `$(...)`; output may be "
                         "piped or redirected after it. Only one such CLI call per line."
                         f"\nRun instead: {_single_form(data, c)}")
        if late and phase != "approved":
            why = _claim_refusal(sid, rid, data, c)
            if why:
                return _deny(why)
            continue   # token after the user's approval (phase="approved")
        nonce = hook_token.issue(run_file.parent, sid, rid, sub)
        if nonce is None:  # the daemon's token service is down: never run unproven
            logger.warning("run_owner_gate: hook-token service not running; %s %s denied",
                           rid, sub)
            return _deny("the SwarmAI backend's hook-token service is not running, so this "
                         "worktree command cannot be proven; tell the user (restart SwarmAI)")
        head, rest = binding
        rewrite = f"{head}{hook_token.TOKEN_ENV}={shlex.quote(nonce)} {rest}"
    if rewrite is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tool_input, "command": rewrite}}}


def create_run_owner_gate(session_context: "dict[str, Any]", phase: str = "check"):
    """Factory: the PreToolUse (Bash) gate (module docstring). session_context is the
    mutable dict SessionUnit updates; ``sdk_session_id`` is the app session id.
    *phase*: "check" (before dangerous_command_gate) or "approved" (right after it:
    issues the token of the ``AFTER_APPROVAL`` commands only)."""
    if phase not in ("check", "approved"):
        raise ValueError(f"phase must be 'check' or 'approved', not {phase!r}")
    from core import executors

    async def _gate(input_data: "dict[str, Any]", tool_use_id: "str | None",
                    context: Any) -> "dict[str, Any]":
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        tool_input = input_data.get("tool_input") or {}
        command = tool_input.get("command") or ""
        if not isinstance(command, str) or (
                "artifact_cli" not in command and "pipeline_test" not in command):
            return {"decision": "approve"}  # cheap path: no IO
        if phase == "approved" and not any(c in command for c in AFTER_APPROVAL):
            return {"decision": "approve"}  # nothing of this phase in the command
        if phase == "approved":
            from core.security_hooks import _is_main_claim, consume_claim_approval
            if _is_main_claim(command) and not consume_claim_approval(tool_use_id, command):
                return _deny("this claim was not approved by the user (no approval was "
                             "recorded for this command); rerun it as ONE command so the "
                             "user is asked")
        sid = str(session_context.get("sdk_session_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", sid):
            return {"decision": "approve"}
        try:
            res = await asyncio.wait_for(
                executors.run_in("subprocess", _decide, sid, command, dict(tool_input),
                                 phase),
                timeout=_DECIDE_TIMEOUT)
        except Exception:  # incl. TimeoutError — fail-open, never block a command
            logger.warning("run_owner_gate: decision failed — failing open", exc_info=True)
            return {"decision": "approve"}
        return res or {"decision": "approve"}

    return _gate
