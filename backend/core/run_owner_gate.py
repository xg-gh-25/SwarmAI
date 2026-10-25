"""run_owner_gate — PreToolUse (Bash) gate for Pipeline v1 run ownership and the
session id it hands to the pipeline CLI (docs/design/pipeline-run-worktree.md §4.2).

For a ``pipeline_v1.py`` command:

- **binding**: a mutation names exactly one safe ``--project`` and one canonical
  ``--run-id`` whose run belongs to that project; ``create --session-id`` may only
  name this session. Key options given twice or abbreviated are refused (argparse
  would act on another value than the one checked here).
- **takeover**: the run's owner is another session → if that session is not
  STREAMING / WAITING_INPUT, this session takes the run over (run.json, the session
  map, and an ``owner_transfer`` event, so every human authorization follows the new
  owner); if it is, DENY. The transfer is written only after every other check of
  the command passed (a denied command changes nothing).
- **session id**: a pipeline command runs in the agent's own process (S6-3, design
  usable-by-anyone §3.3.5: no token, no daemon execution). A prewarmed subprocess
  adopted by a session has no ``SWARM_APP_SESSION_ID``, so the gate puts this
  session's id right before the call when it is the first simple command
  (``_with_session``, ``_token_binding``) via ``updatedInput``. Any other variable set
  on the call is refused.
- **approval**: the person-approved actions (``AFTER_APPROVAL`` with ``--request``)
  are handled in the ``phase="approved"`` registration, after dangerous_command_gate
  recorded the person's approval for this exact command: the gate takes the run over
  if needed and writes the ``auth`` event from the run's ``land_request``
  (``_record_approval``).
- a Bash call to the human-only ``/api/pipelines/<id>/decision`` is denied.

Fail-open on its own errors (never blocks a command because the gate broke).
Public: ``create_run_owner_gate``, ``parse_cli_calls``, ``AFTER_APPROVAL``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_RUN_ID_RE = re.compile(r"^run_[A-Za-z0-9_]{1,64}$")
# Commands that act as the run's owner: only these take a run over (an incidental
# report / read from another tab must not steal it).
_TAKEOVER = frozenset({
    "stage-complete", "review-start", "land", "reflect-prepare", "gate0-start",
    "review-address", "review-proof", "resume", "restore", "abandon",
    "decision-request", "run-land", "accept-finding", "discard-snapshot", "report-tests",
})
_V1_MUTATIONS = frozenset({"create", *_TAKEOVER})
# The daemon's human-only pipeline endpoint (docs/design/pipeline-v1-human-loop.md).
_HUMAN_ONLY_API_RE = re.compile(r"/api/pipelines/[^\s/'\"]+/decision\b")
# Actions the person approves first: handled by the
# phase="approved" registration, after dangerous_command_gate.
AFTER_APPROVAL = frozenset({"run-land", "accept-finding", "discard-snapshot"})
_DECIDE_TIMEOUT = 4.0
# Variables that change where / whether the CLI isolates: never set on a call.
_SUBST = ("$(", "`", "<(", ">(")
from core.pipeline_command import _KEY_OPTS  # noqa: E402 — one definition


def _safe_project(p: str) -> bool:
    """A project name the gate may build a path from (the CLI's own validation is the
    authority; this only refuses traversal)."""
    return bool(p) and len(p) <= 128 and p not in (".", "..") and not any(
        ch in p for ch in ("/", "\\", "\0"))


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
    """(calls, single) for *command*: the shared recogniser ``core.pipeline_command``
    (both the ``"$SWARM_BACKEND_BIN" pipeline-v1`` form and the legacy script form)."""
    from core import pipeline_command
    return pipeline_command.calls(command)


def _single_form(call: dict) -> str:
    """The same call as ONE simple command, for a deny message."""
    from core.worktree_write_gate import v1_command
    return v1_command(shlex.join(call.get("argv") or []))


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

_HEREDOC_WRITE_RE = re.compile(
    r"[ \t]*cat[ \t]*>>?[ \t]*(?P<path>[^\s;&|<>()$`'\"\\]+)[ \t]*<<(?P<dash>-?)[ \t]*"
    r"(?P<q>['\"])(?P<delim>[A-Za-z_]\w*)(?P=q)[ \t]*\n")


def _token_binding(command: str, cwd: "str | None" = None) -> "tuple[str, str] | None":
    """(prefix, rest) such that ``prefix + VAR=… + rest`` hands the session id to the CLI
    call that starts *rest* and to nothing else, or None. *prefix* is zero or more
    ``cat >[>] FILE <<'DELIM'`` … ``DELIM`` writes (a quoted delimiter: the body is
    literal, nothing in it runs); *rest* starts with the CLI call — no subshell or
    substitution anywhere in it — and may continue with any output plumbing."""
    from core.security_hooks import _gate_shell_segments
    i = 0
    while True:
        m = _HEREDOC_WRITE_RE.match(command, i)
        if not m:
            break
        j = m.end()
        lines_end = None
        while j <= len(command):
            nl = command.find("\n", j)
            line = command[j:] if nl == -1 else command[j:nl]
            if (line.lstrip("\t") if m.group("dash") else line) == m.group("delim"):
                lines_end = len(command) if nl == -1 else nl + 1
                break
            if nl == -1:
                break
            j = nl + 1
        if lines_end is None:
            return None          # unterminated heredoc: the CLI is not where it looks
        i = lines_end
    head, rest = command[:i], command[i:]
    stripped = rest.lstrip()
    head += rest[:len(rest) - len(stripped)]
    rest = stripped
    if not rest or any(x in rest for x in _SUBST):
        return None
    seps: list[str] = []
    segs = _gate_shell_segments(rest, seps)
    from core import pipeline_command
    if not segs or seps[0] != "" or not pipeline_command.segment_mentions(segs[0]):
        return None
    return head, rest


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


def _take_over(run_file: Path, run_id: str, sid: str, owner: str) -> "str | None":
    """Make *sid* the owner of *run_id*, whose logged owner is *owner*; a deny reason
    otherwise. Only an owner known not to be at work is replaced: live → deny, unknown
    → deny (R1 review r2: an unprovable takeover is never logged). The logged
    ``owner_transfer`` is the authority; run.json and the session map follow it."""
    live = _owner_live(owner)
    if live:
        return (f"run {run_id} is being worked on by session {owner} right now; wait for "
                "it to stop, or work on another run")
    if live is None:
        return (f"run {run_id} belongs to session {owner}, whose state cannot be told "
                "right now; retry shortly")
    from core import pipeline_human
    pipeline_human.record_owner(run_id, sid)
    from core.run_lock import update_run

    def m(d: dict) -> bool:
        hist = d.get("session_history") if isinstance(d.get("session_history"), list) else []
        hist.append({"session_id": owner, "until": time.time(), "reason": "takeover"})
        d.update(session_history=hist, session_id=sid, session_id_source="takeover")
        return True
    try:
        update_run(run_file, m, timeout=3.0)
        from core.runtime_hooks import write_session_run_map
        write_session_run_map(sid, run_id)
    except Exception:  # noqa: BLE001 — displays and hints; the logged event is the truth
        logger.warning("run_owner_gate: run.json / session map not rewritten for %s", run_id)
    _path, after = _v1_run({"run_id": run_id})
    if not after or after.get("session_id") != sid:
        return "Pipeline v1 takeover could not prove this session owns the run"
    logger.warning("[run-owner-gate] session %s took over run %s from %s", sid, run_id, owner)
    return None


def _v1_run(command: dict) -> "tuple[Path, dict] | tuple[None, None]":
    """Resolve a v1 run by run_id alone; caller-supplied project is not trusted. The
    project is the run directory's own, and ``session_id`` is the logged owner
    (``RunEngine.owner_session``: the creator, then each owner_transfer), never the
    projection's field (R1 review r2: run.json is only a display)."""
    from core.security_hooks import _pipeline_runs_root
    rid = command.get("run_id")
    if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):
        return None, None
    matches = list(Path(_pipeline_runs_root()).glob(
        f"Projects/*/.artifacts/runs/{rid}/run.json"))
    if len(matches) != 1:
        return None, None
    try:
        data = json.loads(matches[0].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    if not isinstance(data, dict):
        return None, None
    project = matches[0].parents[3].name
    owner = _signed_owner(project, rid)
    if owner is None:
        return None, None
    return matches[0], {**data, "project": project, "session_id": owner}


def _unresolved(call: dict) -> str:
    """Why a command's --run-id names no readable run: none (or several) by that id,
    one whose event log has a damaged record (S6-3: the run stops for a person), or
    one whose log cannot be read for another reason."""
    from core.security_hooks import _pipeline_runs_root
    rid = call.get("run_id")
    if not isinstance(rid, str) or not _RUN_ID_RE.fullmatch(rid):
        return "Pipeline v1 command cannot resolve exactly one canonical --run-id"
    matches = list(Path(_pipeline_runs_root()).glob(
        f"Projects/*/.artifacts/runs/{rid}/run.json"))
    if len(matches) != 1:
        return "Pipeline v1 command cannot resolve exactly one canonical --run-id"
    if _log_damaged(matches[0].parents[3].name, rid):
        return (f"run {rid}'s event log has a damaged record, so the run stops here; "
                "tell the user (a person has to look at it)")
    return f"run {rid} has no readable logged owner (its event log cannot be read)"


def _log_damaged(project: str, run_id: str) -> bool:
    """True only when reading the run's log raises ``LogDamaged``."""
    from core.security_hooks import _pipeline_runs_root
    from core.run_protocol.store import LogDamaged
    try:
        from config import get_app_data_dir
        from core.run_protocol.engine import RunEngine
        RunEngine.load(data_dir=get_app_data_dir(), workspace=_pipeline_runs_root(),
                       project=project, run_id=run_id).owner_session()
    except LogDamaged:
        return True
    except Exception as exc:  # noqa: BLE001 — any other unreadable state is not damage
        logger.debug("run_owner_gate: run %s unreadable, not damaged: %s", run_id, exc)
    return False


def _signed_owner(project: str, run_id: str) -> "str | None":
    """The run's owner from its logged events, or None when it is not a readable v1 run."""
    from core.security_hooks import _pipeline_runs_root
    try:
        from config import get_app_data_dir
        from core.run_protocol.engine import RunEngine
        return RunEngine.load(data_dir=get_app_data_dir(), workspace=_pipeline_runs_root(),
                              project=project, run_id=run_id).owner_session()
    except Exception:  # noqa: BLE001 — not a readable v1 run: the caller refuses
        logger.warning("run_owner_gate: run %s has no readable logged owner", run_id)
        return None


def _v1_mutation_check(sid: str, call: dict) -> "str | None":
    """Return a denial reason for an unbound or cross-project/session v1 mutation."""
    sub = call.get("sub")
    if sub not in _V1_MUTATIONS:
        return None
    projects = call.get("projects") or []
    if len(projects) != 1 or not _safe_project(projects[0]):
        return "Pipeline v1 mutations require one explicit, safe --project"
    if sub == "create":
        session_ids = call.get("session_ids") or []
        if session_ids and (len(session_ids) != 1 or session_ids[0] != sid):
            return "Pipeline v1 create --session-id must equal this app session"
        return None
    run_file, data = _v1_run(call)
    if run_file is None or data is None:
        return _unresolved(call)
    actual_project = run_file.parents[3].name
    if projects[0] != actual_project or data.get("project") != actual_project:
        return (f"Pipeline v1 mutation project mismatch: run belongs to {actual_project}, "
                f"not {projects[0]}")
    owner = data.get("session_id")
    if not isinstance(owner, str) or not owner:
        return "Pipeline v1 mutation has no durable owning session"
    if owner != sid and sub not in _TAKEOVER:
        return f"Pipeline v1 run {call.get('run_id')} belongs to session {owner}"
    return None



def _decide(sid: str, command: str, tool_input: dict,
            phase: str = "check", cwd: "str | None" = None) -> "dict | None":
    """The decision for one Bash command (module docstring). S6-3: pipeline commands
    run in the agent's own process; the hook hands them no token, only this session's
    id (``_with_session``), and records the person's approval of an approved action."""
    if _HUMAN_ONLY_API_RE.search(command or ""):
        return _deny("a pipeline decision is answered by a person (the chat question card "
                     "or the dashboard), never by a command. Ask with AskUserQuestion using "
                     "the run's ask_user_question payload, then wait for the answer.")
    calls, _single = parse_cli_calls(command)
    for c in calls:
        c["cwd"] = cwd
        if c["tool"] != "pipeline_v1":
            continue
        if c.get("ambiguous"):
            return _deny("give --run-id / --project / --session-id exactly once and "
                         "unabbreviated, as ONE command.\nRun instead (the values argparse "
                         f"would have used): {_single_form(_dedup_last(c))}")
        if not c.get("sub"):
            from core.worktree_write_gate import v1_command
            return _deny(f"unknown pipeline command; run `{v1_command('--help')}`")
        extra = [a for a in c["assigns"] if a != _SESSION_ENV]
        if extra:
            return _deny("do not set " + ", ".join(sorted(set(extra))) + " on a pipeline "
                         "command: it runs with the session's own environment.\nRun instead: "
                         f"{_single_form(c)}")
        if sid.startswith(_prewarm_prefix()):
            return _deny("this session is not ready yet (no app session id); retry the "
                         "pipeline command in a moment")
        sub = c["sub"]
        if sub not in _V1_MUTATIONS:
            continue
        reason = _v1_mutation_check(sid, c)
        if reason:
            return _deny(reason)
        if sub == "create":
            continue
        run_file, data = _v1_run(c)
        if run_file is None or data is None:
            return _deny(_unresolved(c))
        request_ids = _opt_all(c.get("argv") or [], "--request")
        approved = sub in AFTER_APPROVAL and len(request_ids) == 1 and bool(request_ids[0])
        if phase == "approved" and not approved:
            continue            # the check phase decided it
        if phase == "check" and approved:
            continue            # takeover and approval after the person's approval
        rid = str(c.get("run_id"))
        if data.get("session_id") != sid:
            reason = _take_over(run_file, rid, sid, str(data.get("session_id")))
            if reason:
                return _deny(reason)
        if approved:
            why = _record_approval(rid, request_ids[0])
            if why:
                return _deny(why)
    rewrite = _with_session(command, sid)
    if rewrite is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tool_input, "command": rewrite}}}


_SESSION_ENV = "SWARM_APP_SESSION_ID"


def _prewarm_prefix() -> str:
    from core.session_router import PREWARM_SESSION_PREFIX
    return PREWARM_SESSION_PREFIX


def _with_session(command: str, sid: str) -> "str | None":
    """*command* with this session's app id assigned right before its pipeline call
    (R1 review r2): a prewarmed subprocess adopted by a session has no
    ``SWARM_APP_SESSION_ID`` in its fixed environment, so the hook, which knows the
    session, hands the id to the one CLI call it binds to (``_token_binding``: the
    first simple command). The value never comes from the model: session_link_gate
    refuses any assignment of it in the command. None when the call is not the first
    simple command (the CLI then sees only its own environment)."""
    from core.session_router import PREWARM_SESSION_PREFIX
    if sid.startswith(PREWARM_SESSION_PREFIX):
        return None              # not a session yet: nothing to hand over
    if f"{_SESSION_ENV}={shlex.quote(sid)} " in command:
        return None              # already handed over (a later phase of the same call)
    binding = _token_binding(command)
    if binding is None:
        return None
    head, rest = binding
    return f"{head}{_SESSION_ENV}={shlex.quote(sid)} {rest}"


_APPROVED_ACTIONS = frozenset({"run-land", "accept-finding", "discard-snapshot"})


def _record_approval(run_id: str, request_id: str) -> "str | None":
    """The person approved this command: the daemon records the ``auth`` fact for the
    logged land_request it names (fixing spec C1/C3: no CLI process can write it).
    Returns a denial reason, or None."""
    import time
    try:
        from core import pipeline_human
        from core.run_protocol import landing
        from core.run_protocol.engine import _now
        engine = pipeline_human.load_engine(run_id)
        if engine is None:
            return f"run {run_id} could not be loaded to record the approval"
        landing.approve_from_event(
            engine.store, request_id, now=time.time(), at=_now())
    except Exception as exc:   # an unrecorded approval must never run the command
        logger.warning("run_owner_gate: approval for %s %s not recorded: %s",
                       run_id, request_id, exc)
        return (f"the approval of {request_id} could not be recorded ({exc}); prepare a "
                "new request with --prepare and approve that one")
    return None


def create_run_owner_gate(session_context: "dict[str, Any]", phase: str = "check"):
    """Factory: the PreToolUse (Bash) gate (module docstring). session_context is the
    mutable dict SessionUnit updates; ``sdk_session_id`` is the app session id.
    *phase*: "check" (before dangerous_command_gate) or "approved" (right after it:
    handles the ``AFTER_APPROVAL`` commands only)."""
    if phase not in ("check", "approved"):
        raise ValueError(f"phase must be 'check' or 'approved', not {phase!r}")
    from core import executors

    async def _gate(input_data: "dict[str, Any]", tool_use_id: "str | None",
                    context: Any) -> "dict[str, Any]":
        if input_data.get("tool_name") != "Bash":
            return {"decision": "approve"}
        tool_input = input_data.get("tool_input") or {}
        command = tool_input.get("command") or ""
        from core import pipeline_command
        if not isinstance(command, str) or not (pipeline_command.mentions(command)
                                                or "/api/pipelines/" in command):
            return {"decision": "approve"}  # cheap path: no IO
        if phase == "approved" and not any(c in command for c in AFTER_APPROVAL):
            return {"decision": "approve"}  # nothing of this phase in the command
        if phase == "approved":
            from core.security_hooks import _is_v1_land_request, consume_land_approval
            if _is_v1_land_request(command) \
                    and not consume_land_approval(tool_use_id, command):
                return _deny("this request was not approved by the user (no approval was "
                             "recorded for this command); rerun it as ONE command so the "
                             "user is asked")
        sid = str(session_context.get("sdk_session_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", sid):
            return {"decision": "approve"}
        try:
            res = await asyncio.wait_for(
                executors.run_in("gate", _decide, sid, command, dict(tool_input),
                                 phase, input_data.get("cwd") or None),
                timeout=_DECIDE_TIMEOUT)
        except Exception:  # incl. TimeoutError — fail-open, never block a command
            logger.warning("run_owner_gate: decision failed — failing open", exc_info=True)
            return {"decision": "approve"}
        return res or {"decision": "approve"}

    return _gate
