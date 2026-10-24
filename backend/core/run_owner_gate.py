"""run_owner_gate — PreToolUse (Bash) gate for Pipeline v1 run ownership and the
one-time hook token (docs/design/pipeline-run-worktree.md §4.2; v1 only, r6 §5).

For a ``pipeline_v1.py`` command:

- **binding**: a mutation names exactly one safe ``--project`` and one canonical
  ``--run-id`` whose run belongs to that project; ``create --session-id`` may only
  name this session. Key options given twice or abbreviated are refused (argparse
  would act on another value than the one checked here).
- **takeover**: the run's owner is another session → if that session is not
  STREAMING / WAITING_INPUT, this session takes the run over (run.json, the session
  map, and a signed ``owner_transfer`` event, so every human authorization follows
  the new owner); if it is, DENY. The transfer is signed only after every other
  check of the command passed (a denied command changes nothing).
- **token**: EVERY ``pipeline_v1.py`` command gets a one-time token (B-class B-b: the
  daemon runs nothing without one). The call must be the FIRST simple command of the line
  (``_token_binding``) and must run THIS backend's own script (``_is_our_script``: a
  file with the same name elsewhere gets nothing); the gate issues the token (recorded
  only in the daemon, ``core.hook_token``) and puts ``SWARM_HOOK_TOKEN=<token>`` right
  before the call via ``updatedInput``. Its scope is ``cli_scope.scope``, the
  daemon's own function.
  The person-approved actions (``AFTER_APPROVAL``) get their token only in the
  ``phase="approved"`` registration, after dangerous_command_gate recorded the
  person's approval for this exact command; the gate then writes the signed ``auth``
  event from the run's ``land_request`` (``_record_approval``).
- a Bash call to the human-only ``/api/pipelines/<id>/decision`` is denied.

Fail-open on its own errors (never blocks a command because the gate broke).
Public: ``create_run_owner_gate``, ``parse_cli_calls``, ``AFTER_APPROVAL``.
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
# report / read from another tab must not steal it).
_TAKEOVER = frozenset({
    "stage-complete", "review-start", "land", "reflect-prepare", "gate0-start",
    "review-address", "review-proof", "resume", "restore", "abandon",
    "decision-request", "run-land", "accept-finding", "discard-snapshot", "report-tests",
})
_V1_MUTATIONS = frozenset({"create", *_TAKEOVER})
# The daemon's human-only pipeline endpoint (docs/design/pipeline-v1-human-loop.md).
_HUMAN_ONLY_API_RE = re.compile(r"/api/pipelines/[^\s/'\"]+/decision\b")
# Token actions the person approves first: their token is issued by the
# phase="approved" registration, after dangerous_command_gate.
AFTER_APPROVAL = frozenset({"run-land", "accept-finding", "discard-snapshot"})
_DECIDE_TIMEOUT = 4.0
# Variables that change where / whether the CLI isolates: never set on a call.
_STEERING_ENV = ("SWARM_WORKTREE_MODE", "SWARM_WORKTREE_ROOT", "SWARM_WORKTREE_LEDGER",
                 "SWARM_DATA_DIR")
_SUBST = ("$(", "`", "<(", ">(")
from core.pipeline_command import LEGACY_SCRIPTS as _LEGACY  # noqa: E402
_SCRIPTS = tuple(_LEGACY)
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


def _needs_token(call: dict) -> "str | None":
    """The token scope of a pipeline_v1 call (every one has a token: the daemon runs
    only token-carrying commands, B-class B-b), or None for any other call. The scope
    function is the daemon's own (``cli_scope.scope``)."""
    if call.get("tool") != "pipeline_v1" or not call.get("sub"):
        return None
    from core.run_protocol.cli_scope import scope
    request_ids = _opt_all(call.get("argv") or [], "--request")
    return scope(str(call["sub"]),
                 request_ids[0] if len(request_ids) == 1 and request_ids[0] else None)


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
    """(prefix, rest) such that ``prefix + TOKEN=… + rest`` hands the token to the CLI
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
        dest = m.group("path")
        if os.path.basename(dest) in _SCRIPTS or _resolves_into_scripts(dest, cwd):
            return None          # it would rewrite the script that gets the token
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
    """Make *sid* the owner of *run_id*, whose SIGNED owner is *owner*; a deny reason
    otherwise. Only an owner known not to be at work is replaced: live → deny, unknown
    → deny (R1 review r2: an unprovable takeover is never signed). The signed
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
    except Exception:  # noqa: BLE001 — displays and hints; the signed event is the truth
        logger.warning("run_owner_gate: run.json / session map not rewritten for %s", run_id)
    _path, after = _v1_run({"run_id": run_id})
    if not after or after.get("session_id") != sid:
        return "Pipeline v1 takeover could not prove this session owns the run"
    logger.warning("[run-owner-gate] session %s took over run %s from %s", sid, run_id, owner)
    return None


def _v1_run(command: dict) -> "tuple[Path, dict] | tuple[None, None]":
    """Resolve a v1 run by run_id alone; caller-supplied project is not trusted. The
    project is the run directory's own, and ``session_id`` is the SIGNED owner
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


def _signed_owner(project: str, run_id: str) -> "str | None":
    """The run's owner from its signed events, or None when it is not a readable v1 run."""
    from core.security_hooks import _pipeline_runs_root
    try:
        from config import get_app_data_dir
        from core.run_protocol.engine import RunEngine
        return RunEngine.load(data_dir=get_app_data_dir(), workspace=_pipeline_runs_root(),
                              project=project, run_id=run_id).owner_session()
    except Exception:  # noqa: BLE001 — not a readable v1 run: the caller refuses
        logger.warning("run_owner_gate: run %s has no readable signed owner", run_id)
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
        return "Pipeline v1 mutation cannot resolve exactly one canonical --run-id"
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
    from core import hook_token
    if _HUMAN_ONLY_API_RE.search(command or ""):
        return _deny("a pipeline decision is answered by a person (the chat question card "
                     "or the dashboard), never by a command. Ask with AskUserQuestion using "
                     "the run's ask_user_question payload, then wait for the answer.")
    calls, _single = parse_cli_calls(command)
    for c in calls:
        c["cwd"] = cwd
    rewrite = None
    tokened = [c for c in calls if _needs_token(c)]
    for c in calls:
        if c["tool"] != "pipeline_v1":
            continue
        if c.get("ambiguous"):
            return _deny("give --run-id / --project / --session-id exactly once and "
                         "unabbreviated, as ONE command.\nRun instead (the values argparse "
                         f"would have used): {_single_form(_dedup_last(c))}")
        if not c.get("sub"):
            from core.worktree_write_gate import v1_command
            return _deny(f"unknown pipeline command; run `{v1_command('--help')}`")
        extra = [a for a in c["assigns"] if a not in (_SESSION_ENV, hook_token.TOKEN_ENV)]
        if extra:
            return _deny("do not set " + ", ".join(sorted(set(extra))) + " on a pipeline "
                         "command: it runs with the session's own environment.\nRun instead: "
                         f"{_single_form(c)}")
        if sid.startswith(_prewarm_prefix()):
            return _deny("this session is not ready yet (no app session id); retry the "
                         "pipeline command in a moment")
        token_dir = None
        if c["tool"] == "pipeline_v1" and c.get("sub") in _V1_MUTATIONS:
            reason = _v1_mutation_check(sid, c)
            if reason:
                return _deny(reason)
        if c["tool"] == "pipeline_v1" and c.get("sub") == "create":
            from core.security_hooks import _pipeline_runs_root
            rid = None
            token_dir = Path(_pipeline_runs_root())
        else:
            run_file, data = _v1_run(c)
            if run_file is None or data is None:
                return _deny("Pipeline v1 command cannot resolve exactly one canonical "
                             "--run-id")
            rid = str(c.get("run_id"))
            token_dir = run_file.parent
        # a takeover is SIGNED only after every check that could still deny this
        # command has passed (B round 1: a denied command must change nothing)
        takeover = (c["tool"] == "pipeline_v1" and c.get("sub") in _V1_MUTATIONS
                    and c.get("sub") != "create" and data.get("session_id") != sid
                    and phase == "check")       # a late action takes over when approved
        if not _is_our_script(c):
            return _deny("run the SwarmAI checkout's own pipeline script (this one is "
                         "another file with the same name).\nRun instead: "
                         f"{_single_form(c)}")
        sub = _needs_token(c)
        if sub is None:
            continue
        if c["tool"] == "pipeline_v1" and c.get("sub") == "create":
            data = {}
        late = sub.split(":", 1)[0] in AFTER_APPROVAL
        if phase == "approved" and not late:
            continue   # the check phase issued it
        steer = [a for a in c["assigns"] if a in _STEERING_ENV]
        if steer or any(e + "=" in command for e in _STEERING_ENV):
            return _deny("do not set " + ", ".join(steer or _STEERING_ENV) + " on a pipeline "
                         f"command; worktree mode is a setting.\nRun instead: {_single_form(c)}")
        if hook_token.TOKEN_ENV in c["assigns"] or hook_token.TOKEN_ENV + "=" in command:
            return _deny(f"do not set {hook_token.TOKEN_ENV} yourself; the hook issues it.\n"
                         f"Run instead: {_single_form(c)}")
        binding = _token_binding(command, c.get("cwd"))
        first = parse_cli_calls(binding[1])[0][:1] if binding else []
        if (binding is None or not first or first[0].get("sub") != c.get("sub")
                or first[0].get("run_id") != c.get("run_id") or len(tokened) != 1):
            return _deny("a pipeline command must be the "
                         "FIRST command of the line (output may be piped or redirected after "
                         "it; only `cat > FILE <<'EOF'` writes may come before it; no `cd … "
                         "&&`, `source …`, subshells or `$(...)`), so the hook can hand it "
                         f"its one-time token.\nRun instead: {_single_form(c)}")
        if late and phase != "approved":
            continue   # token (and any takeover) after the person's approval
        # Everything that can still deny comes first, the token is issued next, and
        # only then are state changes made (takeover, the approval's auth event); a
        # state change that fails burns the token, so a denied command changed nothing
        # and left nothing usable (B round 2).
        nonce = hook_token.issue(token_dir, sid, rid, sub)
        if nonce is None:  # the daemon's token service is down: never run unproven
            logger.warning("run_owner_gate: hook-token service not running; %s %s denied",
                           rid, sub)
            return _deny("the SwarmAI backend's hook-token service is not running, so this "
                         "command cannot be proven; tell the user (restart SwarmAI)")
        if takeover or (late and data.get("session_id") != sid):
            reason = _take_over(run_file, rid, sid, str(data.get("session_id")))
            if reason:
                hook_token.redeem(nonce)
                return _deny(reason)
        if late:
            why = _record_approval(rid, sub.split(":", 1)[1])
            if why:
                hook_token.redeem(nonce)
                return _deny(why)
        head, rest = binding
        given = f"{_SESSION_ENV}={shlex.quote(sid)} "
        rewrite = (f"{head}{hook_token.TOKEN_ENV}={shlex.quote(nonce)} "
                   f"{'' if given in command else given}{rest}")
    if rewrite is None:
        rewrite = _with_session(command, sid)
    if rewrite is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
                                   "updatedInput": {**tool_input, "command": rewrite}}}


_SESSION_ENV = "SWARM_APP_SESSION_ID"


def _resolves_into_scripts(path: str, cwd: "str | None") -> bool:
    """A heredoc destination that is, or (through a link) lands in, this backend's
    scripts directory: a write there could change the script before it runs. A
    relative destination with no known cwd cannot be checked and counts as one."""
    from utils.source_repo import pipeline_script
    script = pipeline_script("pipeline_v1")
    ours = script.parent if script is not None \
        else (Path(__file__).resolve().parents[1] / "scripts").resolve()
    try:
        target = Path(os.path.expanduser(path))
        if not target.is_absolute():
            if not cwd:
                return True
            target = Path(cwd) / target
        target = target.resolve()
    except (OSError, RuntimeError):
        return True
    return target == ours or ours in target.parents


def _is_our_script(call: dict) -> bool:
    """This backend's own pipeline CLI (``pipeline_command.is_ours``): only it receives
    a token (B round 1)."""
    from core import pipeline_command
    return pipeline_command.is_ours(call)


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
    signed land_request it names (fixing spec C1/C3: no CLI process can write it).
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
