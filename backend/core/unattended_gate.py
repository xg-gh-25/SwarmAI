"""unattended_gate — the Bash allowlist of a session nobody watches.

An automatic run (``core.attention_auto``) carries out a Needs You item with no person
in front of it. Every other channel that could send data off the machine is closed by
its tool list (web, MCP, sub-agents, file writes are denied), but Bash is not:
``external_approval_gate`` leaves Bash to ``dangerous_command_gate``, which asks only
about destructive or irreversible commands. So a session marked ``unattended`` in its
agent config gets this PreToolUse hook on Bash, and Bash may run only:

- one simple command, with nothing the shell would expand or split: no pipe, chain,
  redirect, subshell, substitution or ``$`` (other than the program word itself), no
  backslash anywhere (an escaped quote would make this check and the shell disagree on
  where a quoted span ends), and none of ``{ } * ? [ ] ~ # !`` outside quotes (brace
  expansion would turn one checked word into several arguments:
  ``--run-now {a,--install}``);
- whose program word is the backend program (the ``$SWARM_BACKEND_BIN`` reference, or
  the exact path that variable holds in this process);
- with one of these subcommands. Reading, for any item:
  ``cognition find|get|map`` · ``ingest status`` ·
  ``py -m jobs.job_manager list|show|validate-cron`` ·
  ``py -m jobs.scheduler --status|--list-jobs|--validate``.
  Changing, only within the item's own scope (``unattended_scope``, set by
  ``attention_auto.unattended_config`` from the item's ``dispatch.context``):
  ``py -m jobs.job_manager pause|resume <id>``, ``edit <id> --json <object>`` where the
  object holds only ``safety.timeout_seconds`` (a whole number of seconds up to
  ``MAX_TIMEOUT_S``) and/or ``schedule``, and ``py -m jobs.scheduler --run-now <id>``,
  each for a job id in the scope's ``job_ids``; ``ingest requeue|wake`` when the scope's
  kind is a Cognitive Distillation one (``CDF_KINDS``).

Scoping to the item matters because an item's text carries raw output (a job's last
error) that someone else may have written: whatever that text asks for, the run can
change only the thing the item is about. Any other ``edit`` and ``create`` are left out
on purpose: a job's prompt, command, type or tool list is code the scheduler runs, so
changing one and then ``--run-now`` would run anything. A time limit and a schedule run
nothing (a job that keeps timing out is the common failure; raising its limit is the fix).

Unlike the other Bash guards this one fails CLOSED (an unparsable command, or the guard's
own error, is denied): it is registered only for unattended sessions, so no person is
ever blocked by it, and an open failure would be the hole it exists to close.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
from typing import Any, Optional

logger = logging.getLogger(__name__)

ENV_VAR = "SWARM_BACKEND_BIN"
_ENV_REFS = frozenset({f"${ENV_VAR}", f"${{{ENV_VAR}}}"})

#: characters that make a command more than one simple command, anywhere
_SHELL_META = set("|&;<>()`\n\r")
#: characters the shell expands or treats specially when they are not quoted
_UNQUOTED_META = set("{}*?[]~\\#!")

#: tools an unattended session never gets (merged into the session's disallowed list)
DENIED_TOOLS = (
    "WebFetch", "WebSearch",            # off the machine
    "AskUserQuestion",                  # nobody is there to answer
    "Write", "Edit", "MultiEdit", "NotebookEdit",  # a written file can run later
    "Agent", "Task",                    # a sub-agent is another session to scope
)

#: the Cognitive Distillation kinds that may requeue or wake CDF
CDF_KINDS = frozenset({"stalled", "poison"})
#: the largest time limit an automatic run may give a job (a hang guard, not a cut)
MAX_TIMEOUT_S = 4 * 60 * 60

_INGEST_READ = frozenset({"status"})
_INGEST_CHANGE = frozenset({"requeue", "wake"})
_COGNITION = frozenset({"find", "get", "map"})
_JOB_READ = frozenset({"list", "show", "validate-cron"})
_JOB_CHANGE = frozenset({"pause", "resume"})
_SCHEDULER_READ = frozenset({"--status", "--list-jobs", "--validate"})


def _expands_unquoted(command: str) -> bool:
    """True when a character the shell would expand appears outside quotes, or a quote
    is left open."""
    quote: Optional[str] = None
    for ch in command:
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
        elif ch in _UNQUOTED_META:
            return True
    return quote is not None


def _safe_job_edit(args: list[str], job_ids: frozenset) -> bool:
    """``edit <id> --json <object>`` changing only the time limit and/or the schedule
    of a job in scope."""
    if len(args) != 4 or args[0] != "edit" or args[2] != "--json" or args[1] not in job_ids:
        return False
    try:
        updates = json.loads(args[3])
    except ValueError:
        return False
    if not isinstance(updates, dict) or not updates or set(updates) - {"safety", "schedule"}:
        return False
    if "schedule" in updates and not isinstance(updates["schedule"], str):
        return False
    if "safety" in updates:
        safety = updates["safety"]
        if not isinstance(safety, dict) or set(safety) != {"timeout_seconds"}:
            return False
        limit = safety["timeout_seconds"]
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= MAX_TIMEOUT_S:
            return False
    return True


def _program_ok(word: str) -> bool:
    if word in _ENV_REFS:
        return True
    real = os.environ.get(ENV_VAR, "")
    return bool(real) and word == real


def unattended_command_allowed(command: Any, scope: Optional[dict] = None) -> bool:
    """True only for one allowlisted backend command (see the module docstring).
    ``scope``: the item's ``kind`` and ``job_ids``; with none, only reading passes."""
    if not isinstance(command, str) or not command.strip():
        return False
    if any(ch in _SHELL_META for ch in command):
        return False
    stripped = command.strip()
    # The only ``$`` allowed is the program reference itself, at the very start.
    head = ""
    for ref in sorted(_ENV_REFS, key=len, reverse=True):
        for quoted in (f'"{ref}"', ref):
            if stripped.startswith(quoted):
                head = quoted
                break
        if head:
            break
    rest_text = stripped[len(head):]
    if "$" in rest_text or "\\" in rest_text or _expands_unquoted(rest_text):
        return False
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return False
    if len(tokens) < 2 or not _program_ok(tokens[0]):
        return False
    scope = scope or {}
    kind = scope.get("kind")
    job_ids = frozenset(str(j) for j in scope.get("job_ids") or () if j)
    sub, rest = tokens[1], tokens[2:]
    if sub == "ingest":
        if not rest:
            return False
        return rest[0] in _INGEST_READ or (rest[0] in _INGEST_CHANGE and kind in CDF_KINDS)
    if sub == "cognition":
        return bool(rest) and rest[0] in _COGNITION
    if sub == "py":
        if len(rest) < 3 or rest[0] != "-m":
            return False
        module, args = rest[1], rest[2:]
        if module == "jobs.job_manager":
            if args[0] in _JOB_READ:
                return True
            if args[0] in _JOB_CHANGE:
                return len(args) == 2 and args[1] in job_ids
            return _safe_job_edit(args, job_ids)
        if module == "jobs.scheduler":
            if args[0] == "--run-now":
                return len(args) == 2 and args[1] in job_ids
            return len(args) == 1 and args[0] in _SCHEDULER_READ
        return False
    return False


def deny_reason(scope: Optional[dict]) -> str:
    """What a denied command is told: the commands this session may run."""
    scope = scope or {}
    change = []
    ids = [str(j) for j in scope.get("job_ids") or () if j]
    if ids:
        change.append(
            f"py -m jobs.job_manager pause|resume {ids[0]}; py -m jobs.job_manager edit "
            f"{ids[0]} --json '{{\"safety\": {{\"timeout_seconds\": N}}}}' (N up to "
            f"{MAX_TIMEOUT_S}, or a schedule); py -m jobs.scheduler --run-now {ids[0]}")
    if scope.get("kind") in CDF_KINDS:
        change.append("ingest requeue --reason <reason>; ingest wake")
    return (
        "This session runs unattended (an automatic Needs You run), so Bash may run only "
        "one backend command from a short list, with nothing the shell would expand "
        "(no braces, *, ?, ~, $ or open quotes). Reading: \"$SWARM_BACKEND_BIN\" cognition "
        "find|get|map; ingest status; py -m jobs.job_manager list|show <job id>; py -m "
        "jobs.scheduler --status|--list-jobs|--validate. Changing, for this item only: "
        + ("; ".join(change) or "nothing")
        + ". Use Read, Grep and Glob to look at files. If the fix needs anything else, stop "
        "and end with 'RESULT: not resolved — <what is needed>' so the person takes it over."
    )


def create_unattended_bash_gate(scope: Optional[dict] = None):
    """PreToolUse (Bash) for an unattended session, bound to its item's scope:
    allowlisted commands pass, all else is denied. Fails closed."""
    reason = deny_reason(scope)

    async def unattended_bash_gate(input_data: dict[str, Any], tool_use_id: Any,
                                   context: Any) -> dict[str, Any]:
        try:
            command = ((input_data or {}).get("tool_input") or {}).get("command")
            if unattended_command_allowed(command, scope):
                return {}
        except Exception:  # noqa: BLE001 — fail closed: the deny below is the safe side
            logger.warning("unattended_gate: the check failed; the command is denied",
                           exc_info=True)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    return unattended_bash_gate
