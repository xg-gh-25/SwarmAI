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
- **session_worktree** (no ownership rules, never denies): a line that names
  ``scripts.session_worktree`` gets ``export`` of this session's id and, from the
  main agent only, the turn's id in front of the whole line
  (``_session_worktree_export``), which ``land`` stamps into the landing's reflog
  entry; composed with the pipeline rewrite into one updatedInput (design
  docs/design/2026-10-26-turn-cards-unified.md §3.2). A pipeline landing
  (``pipeline-v1 land`` / ``run-land``) gets the same export: the run's merge writes
  the same stamp (design 2026-10-29-pipeline-retro-fixes F1). A line that names
  ``scripts.swarm_out`` gets the export too (it is no landing): the Swarm Out CLI records
  the session that dispatches a run and gives commands on it (design
  2026-10-31-swarm-out-agent-owns-its-runs §2.1).
- **approval**: the authorized actions (``AFTER_APPROVAL`` with ``--request``)
  are handled in the ``phase="approved"`` registration, after dangerous_command_gate
  recorded the approval for this exact command (the person's, or for a landing the
  run's own agent's): the gate takes the run over
  if needed and writes the ``auth`` event from the run's ``land_request``
  (``_record_approval``).
- a Bash command that sends an HTTP request (``_makes_http_call``; one that only names a
  route, in a search or a file being edited, is not judged) to the human-only
  ``/api/pipelines/<id>/decision`` or ``/confirm-landed`` is denied, and so is
  one to the user-only CDF redaction ``/api/ingestion/redact`` (CDF §13 tombstone: only
  the person triggers it, from the Flow view), one to the chat's question-card answer
  ``/api/chat/answer-question`` (it records the answer as the person's), and one to any
  Swarm Out / Hive route the owner agent may not call (``core.swarm_out_ops``, the table the regexes are
  generated from).

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
    "landed-as",
})
_V1_MUTATIONS = frozenset({"create", *_TAKEOVER})
# The daemon's human-only pipeline endpoints: a decision's answer
# (docs/design/pipeline-v1-human-loop.md) and a person's "it landed as these commits"
# (design 2026-11-01-brain-eval-and-runs §4.2; the run's agent records it itself with
# landed-as --prepare / --request).
_HUMAN_ONLY_API_RE = re.compile(r"/api/pipelines/[^\s/'\"]+/(?:decision|confirm-landed)\b")
# The user-only CDF tombstone endpoint (CDF design §13; routers/ingestion_redact.py).
_USER_ONLY_REDACT_RE = re.compile(r"/api/ingestion/redact\b")
# The chat's own question-card answer (routers/chat.py answer-question): it records the
# answer as the person's (answered_by "person"), which a pipeline decision and a Hive
# wait forwarded from the chat both rely on, so only the App sends it (design
# 2026-10-31-swarm-out-agent-owns-its-runs P10).
_USER_ONLY_CHAT_ANSWER_RE = re.compile(r"/api/chat/answer-question\b")
# A command on a run says who gives it (``by``; core/swarm_out_actor), which the route
# cannot verify. So an agent gives these commands only through the Swarm Out CLI (its own
# process sends ``by: agent``); an HTTP call to one of them from a command is refused
# whatever its body says (design 2026-10-31-swarm-out-agent-owns-its-runs §9, option A).
# The person's Swarm Out and Hive operations (landing commits by hand, answering a Hive's
# question, its session through the Host proxy, a Hive's password, creating / deleting a
# Hive …) are not listed here: they are the rows of
# core.swarm_out_ops whose callers leave out the owner agent, and the gate's regexes
# are generated from that table (docs/design/2026-10-31-swarm-out-ops-registry.md §5.2).
# Actions the person approves first: handled by the
# phase="approved" registration, after dangerous_command_gate.
AFTER_APPROVAL = frozenset({"run-land", "accept-finding", "discard-snapshot", "landed-as"})
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
    (the ``"$SWARM_BACKEND_BIN" pipeline-v1`` form)."""
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



_DOT_SEGMENT_RE = re.compile(r"/(?:\.(?=/)|[^/\s'\"]+/\.\.(?=/))")


def _as_routed(command: str) -> str:
    """``command`` URL-decoded (twice: a double-encoded path) with runs of ``/`` made
    one (``…/stop//`` is redirected to ``…/stop``, and the Host proxy strips the slashes
    around its path) and ``/./`` and ``/<seg>/../`` collapsed, until nothing changes."""
    from urllib.parse import unquote
    s = re.sub(r"/{2,}", "/", unquote(unquote(command)))
    for _ in range(32):
        t = _DOT_SEGMENT_RE.sub("", s)
        if t == s:
            break
        s = t
    return s


# An HTTP request in a command: a client program run as a word of the command line (not
# inside a heredoc's body, which is a file's text or a script's), or a client library
# called anywhere (``python -c`` / ``python - <<EOF`` / ``node -e``).
_HEREDOC_BODY_RE = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)
_HTTP_CLI_RE = re.compile(r"(?:^|[\s;&|(`'\"=])(?:curl|wget|xh|http|https|httpie)(?=[\s'\";&|)]|$)")
_HTTP_LIB_RE = re.compile(
    r"\burlopen\s*\(|\brequests\.(?:get|post|put|patch|delete|request|Session)\b|\bhttpx\.|\baiohttp\b"
    r"|\bhttp\.client\b|\bfetch\s*\(|\baxios\b|\bXMLHttpRequest\b")


def _makes_http_call(command: str) -> bool:
    """Whether *command* sends an HTTP request (see the patterns above). A command that
    only names a route (a search, a file being written, a test or doc being edited) does
    not: the person-only checks are for calls, and every one of their past denials in
    the owner's sessions was a command that only named the path."""
    outside = _HEREDOC_BODY_RE.sub("<<heredoc", command)
    bare = re.sub(r"[\"'\\]", "", outside)
    return bool(_HTTP_CLI_RE.search(outside) or _HTTP_CLI_RE.search(bare) or _HTTP_LIB_RE.search(command))


def _user_only_deny(command: str) -> "dict | None":
    """The deny for a command that calls a route only the person may call (the
    pipeline decision route, the redaction route, the chat answer route; a Swarm Out or
    Hive operation the owner
    agent may not call, ``core.swarm_out_ops``), else None.
    Pure string work: the gate runs it on every Bash command, before any filter. Only a
    command that sends an HTTP request is judged (``_makes_http_call``): naming a route
    in a search, a file's text or an edit is not calling it. The command is also matched
    URL-decoded with ``.``/``..`` path segments collapsed, as the server would route it.
    This is a speed bump, not a boundary: a path assembled at run time (variables, a
    script file) is not seen; the agent and the daemon share an OS user (boundary A)."""
    if not _makes_http_call(command):
        return None
    if _HUMAN_ONLY_API_RE.search(command):
        if re.search(r"/confirm-landed\b", command):
            return _deny("the dashboard's confirm-landed is the person's button. Record it "
                         "yourself: landed-as --prepare with the commits, then landed-as "
                         "--request <request id>, as one command.")
        return _deny("a pipeline decision is answered by a person (the chat question card "
                     "or the dashboard), never by a command. Ask with AskUserQuestion using "
                     "the run's ask_user_question payload, then wait for the answer.")
    # also with shell quotes and backslashes removed: the shell joins "…/runs/"$D"/stop"
    # into the path the server routes
    bare = re.sub(r"[\"'\\]", "", command)
    command = f"{command}\n{_as_routed(command)}\n{bare}\n{_as_routed(bare)}"
    if _USER_ONLY_CHAT_ANSWER_RE.search(command):
        return _deny("a question card is answered by the person in the chat, never by a "
                     "command. Ask with AskUserQuestion and wait for their answer.")
    if _USER_ONLY_REDACT_RE.search(command):
        return _deny("withdrawing written knowledge is the person's action (the Flow view's "
                     "撤回 button), never a command. Tell the user which unit you think should "
                     "be withdrawn and why; they decide.")
    from core import swarm_out_ops
    run_op = swarm_out_ops.agent_limited_match(command)
    if run_op is not None:
        return _deny(f"`{run_op.name}` is given with the Swarm Out CLI "
                     "(`\"$SWARM_BACKEND_BIN\" py -m scripts.swarm_out <command> <dispatch_id> --why …`), "
                     "never as a direct HTTP call: the CLI says the command is the agent's, and the "
                     "daemon then checks it against the run.")
    op = swarm_out_ops.person_only_match(command)
    if op is not None:
        return _deny(f"`{op.name}` ({op.method} {op.path}) is the person's to do in the App "
                     "(Swarm Out, or Settings → Hive), never a command. Tell the user what you "
                     "think should be done and why; they decide.")
    return None


def _decide(sid: str, command: str, tool_input: dict,
            phase: str = "check", cwd: "str | None" = None,
            approved_by: str = "person") -> "dict | None":
    """The decision for one Bash command (module docstring). S6-3: pipeline commands
    run in the agent's own process; the hook hands them no token, only this session's
    id (``_with_session``), and records the approval of an approved action (the
    person's, or ``host``: the agent that answers for the run)."""
    user_only = _user_only_deny(command or "")
    if user_only is not None:
        return user_only
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
            why = _record_approval(rid, request_ids[0], approved_by)
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


_APPROVED_ACTIONS = AFTER_APPROVAL


# ── session_worktree: hand it the session and the turn (no ownership rules) ──
# design docs/design/2026-10-26-turn-cards-unified.md §3.2: any line naming the module
# gets an ``export`` in front of the whole line (the shape of the command does not
# matter; a wrapper script that does not name it is not seen). Never denies.

_SW_MODULE = "scripts.session_worktree"
#: the Swarm Out CLI records the dispatching session and the session of each command an
#: agent gives on a run (design 2026-10-31-swarm-out-agent-owns-its-runs §2.1); it gets
#: the same export, and is no landing (no turn changes are read for it)
_SO_MODULE = "scripts.swarm_out"


def _session_worktree_export(command: str, sid: str, turn_id: "str | None",
                             sub_agent: bool) -> "str | None":
    """``export SWARM_APP_SESSION_ID=… SWARM_TURN_ID=…; `` to put in front of the WHOLE
    line *command*, or None (design 2026-10-26-turn-cards-unified.md §3.2). ``export``
    reaches every later segment and subshell, so ``cd <wt> && … land``, ``W=$(…); …
    land $W`` and ``bash -c '… land'`` all stamp the turn. A sub-agent can outlive the
    turn that started it, so its call never carries a turn id. A command that sets
    either variable itself is left as it is. Pure string work."""
    from core.app_session_id import valid_app_session_id, valid_turn_id
    from core.session_router import PREWARM_SESSION_PREFIX
    if sid.startswith(PREWARM_SESSION_PREFIX) or not valid_app_session_id(sid):
        return None              # not a session yet: nothing to hand over
    if f"{_SESSION_ENV}=" in command or f"{_TURN_ENV}=" in command:
        return None              # already handed over, or set by the command itself
    assign = f"{_SESSION_ENV}={shlex.quote(sid)}"
    if not sub_agent and valid_turn_id(turn_id):
        assign += f" {_TURN_ENV}={turn_id}"
    return f"export {assign}; "


_TURN_ENV = "SWARM_TURN_ID"


def _record_approval(run_id: str, request_id: str, by: str = "person") -> "str | None":
    """This command was approved (by the person, or ``host``: on a Hive, the agent that
    dispatched the run): the daemon records the ``auth`` fact for the logged land_request
    it names (fixing spec C1/C3: no CLI process can write it), with who authorized it.
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
            engine.store, request_id, now=time.time(), at=_now(), authorized_by=by)
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
        sw_allow = None
        # a landing command gets the session and the turn it stamps into the landing's
        # reflog entry: session_worktree (check phase) and a pipeline landing (both
        # phases: an approved run-land is rewritten by the approved phase too; design
        # 2026-10-29-pipeline-retro-fixes F1)
        lands = isinstance(command, str) and (
            (phase == "check" and (_SW_MODULE in command or pipeline_command.is_land(command)))
            or (phase == "approved" and pipeline_command.is_land(command)
                and any(c in command for c in AFTER_APPROVAL)))
        swarm_out_cli = phase == "check" and isinstance(command, str) and _SO_MODULE in command
        if lands or swarm_out_cli:
            # pure string work, no IO
            sub_agent = bool(input_data.get("agent_id"))
            try:
                export = _session_worktree_export(
                    command, str(session_context.get("sdk_session_id") or ""),
                    session_context.get("_turn_id"), sub_agent)
            except Exception:  # noqa: BLE001 — fail-open, never blocks a command
                logger.warning("run_owner_gate: landing rewrite failed", exc_info=True)
                export = None
            if export is not None:
                if lands and not sub_agent:
                    # TurnChanges reads this turn's landings only when it ran a landing
                    # command (design 2026-10-26-turn-cards-unified.md §3.3)
                    session_context["_turn_sw"] = True

                def sw_allow(base: str, export=export):
                    return {"hookSpecificOutput": {
                        "hookEventName": "PreToolUse", "permissionDecision": "allow",
                        "updatedInput": {**tool_input, "command": export + base}}}
        # The person-only routes are denied before the pipeline pre-filter below: a plain
        # curl to them names no pipeline command, so a check made only in _decide never ran
        # (the redaction deny was dead on the live path until this check moved here).
        user_only = _user_only_deny(command) if isinstance(command, str) else None
        if user_only is not None:
            return user_only
        if not isinstance(command, str) or not (pipeline_command.mentions(command)
                                                or "/api/pipelines/" in command):
            return sw_allow(command) if sw_allow else {"decision": "approve"}  # no IO
        res = await _pipeline_decision(command, tool_input, input_data, tool_use_id)
        if sw_allow is None:
            return res
        if _plain_allow(res):
            return sw_allow(command)
        out = (res or {}).get("hookSpecificOutput") or {}
        if out.get("permissionDecision") == "allow" and "updatedInput" in out:
            # hooks do not feed each other's updatedInput: ONE input carries both
            return sw_allow(str((out.get("updatedInput") or {}).get("command") or command))
        return res

    async def _pipeline_decision(command, tool_input, input_data, tool_use_id):
        if phase == "approved" and not any(c in command for c in AFTER_APPROVAL):
            return {"decision": "approve"}  # nothing of this phase in the command
        approved_by = "person"
        if phase == "approved":
            from core.security_hooks import _is_v1_land_request, consume_land_approval
            if _is_v1_land_request(command):
                who = consume_land_approval(tool_use_id, command)
                if not who:
                    return _deny("this request was not approved by the user (no approval was "
                                 "recorded for this command); rerun it as ONE command so the "
                                 "user is asked")
                approved_by = "host" if who == "host" else "person"
        sid = str(session_context.get("sdk_session_id") or "")
        from core.app_session_id import valid_app_session_id
        if not valid_app_session_id(sid):
            return {"decision": "approve"}
        try:
            res = await asyncio.wait_for(
                executors.run_in("gate", _decide, sid, command, dict(tool_input),
                                 phase, input_data.get("cwd") or None, approved_by),
                timeout=_DECIDE_TIMEOUT)
        except Exception:  # incl. TimeoutError — fail-open, never block a command
            logger.warning("run_owner_gate: decision failed — failing open", exc_info=True)
            return {"decision": "approve"}
        return res or {"decision": "approve"}

    return _gate


def _plain_allow(res: "dict | None") -> bool:
    """*res* lets the command run as it is (no deny, no ask, no rewrite of its own)."""
    if not res or res.get("decision") == "approve":
        return True
    out = res.get("hookSpecificOutput") or {}
    return out.get("permissionDecision") == "allow" and "updatedInput" not in out
