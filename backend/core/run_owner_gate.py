"""run_owner_gate — PreToolUse (Bash) gate for run ownership and the one-time hook
token (docs/design/pipeline-run-worktree.md §4.2).

For an ``artifact_cli`` / ``pipeline_test`` command that writes run state with
``--run-id R`` (active only while ``worktree_mode`` is on, or R is isolated):

- **takeover**: R's owner (``run.session_id``) is another session → if that session
  is not STREAMING / WAITING_INPUT, this session takes the run over (old owner to
  ``session_history[]``, session→run map rewritten); if it is, DENY.
- **token**: for the commands that change worktree state (stage-start of
  plan/build/goal_cycle/deliver, stage-complete of deliver, run-update to a terminal
  status) the command must be ONE simple command; the gate issues a token and
  rewrites the command to ``SWARM_HOOK_TOKEN=<nonce> <command>`` via ``updatedInput``.
  The CLI consumes it under the run lock — proof the hook really ran (an
  environment variable is inherited by a hookless ``claude --print --bare`` child).

Fail-open on its own errors (never blocks a command because the gate broke).
Public: ``create_run_owner_gate``, ``parse_cli_calls``, ``TOKEN_SUBCOMMANDS``.
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
    "run-surface-changes",
})
_TOKEN_STAGES = frozenset({"plan", "build", "goal_cycle", "deliver"})
_TERMINAL = frozenset({"completed", "complete", "abandoned", "failed", "cancelled"})
TOKEN_SUBCOMMANDS = ("stage-start", "stage-complete", "run-update")
_DECIDE_TIMEOUT = 4.0
_SUBST = ("$(", "`", "<(", ">(")


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
                      "assigns": [t.split("=", 1)[0] for t in lead if "=" in t]})
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
    if sub == "stage-start" and call.get("stage") in _TOKEN_STAGES:
        return "stage-start"
    if sub == "stage-complete" and call.get("stage") == "deliver":
        return "stage-complete"
    if sub == "run-update" and call.get("status") in _TERMINAL:
        return "run-update"
    return None


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


def _decide(sid: str, command: str, tool_input: dict) -> "dict | None":
    from core import hook_token
    from core.run_worktree import mode_enabled
    from core.security_hooks import _pipeline_runs_root
    calls, single = parse_cli_calls(command)
    rewrite = None
    for c in calls:
        if c["tool"] == "foreign":
            continue
        rid, proj = c.get("run_id"), c.get("project")
        if c.get("ambiguous") and (mode_enabled() or _isolated(proj, rid)):
            return _deny("give --run-id / --project / --stage / --status exactly once and "
                         "unabbreviated")
        if not (rid and proj and _RUN_ID_RE.fullmatch(rid) and _safe_project(proj)):
            continue
        if c["tool"] == "artifact_cli" and c["sub"] not in _WRITING:
            continue
        run_file = Path(_pipeline_runs_root()) / "Projects" / proj / ".artifacts" / "runs" / rid / "run.json"
        try:
            data = json.loads(run_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not (mode_enabled() or data.get("worktree_mode") == "isolated"):
            continue
        if c["tool"] == "artifact_cli" and c["sub"] in _TAKEOVER:
            reason = _takeover(run_file, rid, sid)
            if reason:
                return _deny(reason)
        sub = _needs_token(c)
        if sub is None:
            continue
        if hook_token.TOKEN_ENV in c["assigns"] or hook_token.TOKEN_ENV + "=" in command:
            return _deny(f"do not set {hook_token.TOKEN_ENV} yourself; the hook issues it")
        if not single or len(calls) != 1:
            return _deny("this command changes the run's worktree state: run it on its own, "
                         "as ONE command (no `;`, `&&`, `|`, subshells or `$(...)`), so the "
                         "hook can issue its one-time token")
        nonce = hook_token.issue(run_file.parent, sid, rid, sub)
        rewrite = f"{hook_token.TOKEN_ENV}={shlex.quote(nonce)} {command}"
    if rewrite is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tool_input, "command": rewrite}}}


def create_run_owner_gate(session_context: "dict[str, Any]"):
    """Factory: the PreToolUse (Bash) gate (module docstring). session_context is the
    mutable dict SessionUnit updates; ``sdk_session_id`` is the app session id."""
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
        sid = str(session_context.get("sdk_session_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", sid):
            return {"decision": "approve"}
        try:
            res = await asyncio.wait_for(
                executors.run_in("subprocess", _decide, sid, command, dict(tool_input)),
                timeout=_DECIDE_TIMEOUT)
        except Exception:  # incl. TimeoutError — fail-open, never block a command
            logger.warning("run_owner_gate: decision failed — failing open", exc_info=True)
            return {"decision": "approve"}
        return res or {"decision": "approve"}

    return _gate
