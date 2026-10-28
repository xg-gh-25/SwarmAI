"""A session that works under a Brain's Project Agent (design
2026-10-28-project-agent-flow §8): the pinned values its gates read, and the Brain
gates it runs.

**The pin.** When a session starts under a Project Agent (a chat request or a
dispatch names the Brain and, optionally, the mode and the revision it was shown),
``make_pin`` reads the definition once and fixes what the session's hooks enforce:
the mode, the definition's revision, the actions reserved for a person and the gates
to run (the mode's own lists, which only add to the shared ones), and the digest of
the Brain's ``3-gates/``. The router records it in ``sessions.metadata.project_agent``
as soon as the session row exists, before the turn waits for a slot (a session whose
pin cannot be recorded does not start). The hooks read it from there on their first
call (``current_pin``, then cached in the hook context), so a queued first send, a
drained message and a session after a backend restart are all pinned to the same
values. A pin that cannot be read gives ``{"broken": <why>}``: Bash, Write and Edit
are refused, never let through.

**The gates** (``run_gates``): every file of ``3-gates/`` is read once; the digest of
those bytes must equal the pinned one ("the Brain's gates were changed" otherwise),
and each enforced gate runs from those same bytes, copied into an empty temporary
directory: the backend's own interpreter (isolated mode in a source checkout), that
directory as cwd and HOME, only ``PATH`` and ``LANG`` from the environment, its own
process group, killed as a group after ``GATE_TIMEOUT_S``. The Kiro protocol: the tool
call as JSON on stdin; exit 2 refuses it, with stderr as the reason. Any other
non-zero exit, a timeout, or a gate that cannot be started refuses the call too: under
a Project Agent the rules are meant to hold.
"""
from __future__ import annotations

import json
import logging
import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

import yaml

from core.project_agent import (GENERAL_BRAIN, PROJECT_AGENT_FILE, _list_of_dicts, _mode_view,
                                gates_snapshot, general_brain_dir, normalize, revision, validate)

logger = logging.getLogger(__name__)

PIN_KEY = "project_agent"
_PIN_FOR = "_project_agent_session"
GATE_TIMEOUT_S = 5
#: the tools a Brain gate runs for (v1): the ones that run a command or write a file
GATED_TOOLS = frozenset({"Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"})
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}")
_REASON_MAX = 1000


class PinError(ValueError):
    """The session cannot be pinned to this Project Agent."""


def brain_dir(workspace: Path, brain: str) -> Path:
    """The Brain's directory: ``Projects/<brain>`` (a direct child), or the General
    Brain's ``Knowledge/Library``."""
    if brain == GENERAL_BRAIN:
        return general_brain_dir(workspace)
    if not isinstance(brain, str) or not _NAME_RE.fullmatch(brain) or brain in (".", ".."):
        raise PinError(f"not a Brain name: {brain!r}")
    root = (Path(workspace) / "Projects").resolve()
    d = (root / brain).resolve()
    if d.parent != root or not d.is_dir() or brain not in os.listdir(root):
        # on a case-insensitive disk "swarmai" resolves to SwarmAI: the name must be
        # the one on disk, or a Hive (case-sensitive) gets a second copy
        raise PinError(f"no Brain named {brain}")
    return d


def make_pin(workspace: Path, brain: str, mode: Optional[str] = None,
             seen_revision: Optional[str] = None) -> dict[str, Any]:
    """The values a session under *brain*'s Project Agent enforces. Raises PinError
    when there is no valid definition, the mode does not exist, or the definition
    changed since *seen_revision*."""
    d = brain_dir(workspace, brain)
    target = d / PROJECT_AGENT_FILE
    try:
        raw = target.read_text(encoding="utf-8")
        doc = yaml.safe_load(raw)
    except (OSError, UnicodeError, yaml.YAMLError):
        raise PinError(f"{brain} has no readable Project Agent") from None
    if not isinstance(doc, dict):
        raise PinError(f"{brain}'s Project Agent is not a mapping")
    problems = validate(doc, d)
    if problems:
        raise PinError(f"{brain}'s Project Agent has problems: {'; '.join(problems[:3])}")
    before, _files = gates_snapshot(d)
    rev = revision(d)
    digest, _files = gates_snapshot(d)
    if before != digest:
        raise PinError(f"{brain}'s gates changed while the session was being pinned; try again")
    if seen_revision and rev != seen_revision:
        raise PinError(f"{brain}'s Project Agent changed since it was shown")
    n = normalize(doc)
    modes = {m.get("id"): m for m in _list_of_dicts(n.get("modes"))}
    mid = mode or n.get("default_mode")
    if mid not in modes:
        raise PinError(f"{brain}'s Project Agent has no mode {mid!r}")
    view = _mode_view(modes[mid], n)
    return {"brain": brain, "mode": mid, "revision": rev, "profile": view["profile"],
            "reserved": view["reservedForHuman"], "gates": view["gates"], "gates_digest": digest}


def set_pin(session_context: dict[str, Any], pin: Optional[dict[str, Any]]) -> None:
    """Put *pin* into the hook context for the session the context now names."""
    session_context[PIN_KEY] = pin
    session_context[_PIN_FOR] = session_context.get("sdk_session_id")


def _valid_pin(value: Any) -> Optional[dict[str, Any]]:
    """A stored pin as the hooks read it, or ``{"broken": …}`` when it is not one."""
    if not isinstance(value, dict):
        return {"broken": "the session's Project Agent record cannot be read"}
    keys = ("brain", "mode", "revision", "gates_digest")
    if not all(isinstance(value.get(k), str) and value.get(k) for k in keys) or not isinstance(
            value.get("reserved"), list) or not isinstance(value.get("gates"), list):
        return {"broken": "the session's Project Agent record is incomplete"}
    return value


async def current_pin(session_context: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The pin of the session the hook context names: the one set at its start, else
    the one recorded on the session (after a restart). None = not a Project Agent
    session. A database that cannot be read gives ``{"broken": …}`` for this call
    (not cached): a session that may be under a Project Agent never runs a gated
    tool without its rules."""
    if not session_context:
        return None
    sid = session_context.get("sdk_session_id")
    if PIN_KEY in session_context and session_context.get(_PIN_FOR) == sid:
        return session_context[PIN_KEY]
    if not sid:
        return None
    try:
        from database import db
        row = await db.sessions.get(sid)
    except Exception as exc:  # noqa: BLE001 — unknown this call; asked again next call
        # it may be a Project Agent session: its gated tools are refused for this
        # call rather than run without its rules (the answer is not cached)
        logger.warning("project agent: session %s record unreadable: %s", sid, exc)
        return {"broken": "the session record cannot be read right now; try again"}
    meta = (row or {}).get("metadata")
    stored = meta.get(PIN_KEY) if isinstance(meta, dict) else None
    pin = None if stored is None else _valid_pin(stored)
    set_pin(session_context, pin)
    return pin


async def record_pin(session_id: str, pin: dict[str, Any]) -> bool:
    """Write *pin* into the session's ``metadata.project_agent`` (read back after a
    restart), keeping the rest of the metadata. False when the row is missing or the
    write failed: the caller does not start the session (its rules would be lost on
    a restart)."""
    try:
        from database import db
        row = await db.sessions.get(session_id)
        if row is None:
            logger.error("project agent: session %s not found to record its pin", session_id)
            return False
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        await db.sessions.update(session_id, {"metadata": {**meta, PIN_KEY: pin}})
        return True
    except Exception as exc:  # noqa: BLE001 — reported to the caller
        logger.error("project agent: recording the pin failed for %s: %s", session_id, exc)
        return False


# ── Brain gates ──────────────────────────────────────────────────────────────

def _gate_argv(script: Path) -> list[str]:
    from utils.backend_bin import is_frozen, python_argv
    # a source checkout's interpreter takes -I (no user site, no PYTHON* variables, no
    # script directory on sys.path); the packaged program's `py` has no such flags and
    # gets the same effect from the empty directory and the allowlisted environment
    return [*python_argv(), *([] if is_frozen() else ["-I"]), str(script)]


def _run_one(name: str, body: bytes, payload: bytes) -> Optional[str]:
    """Run one gate from *body*; the refusal reason, or None when it lets the call
    through."""
    with tempfile.TemporaryDirectory(prefix="brain-gate-") as tmp:
        script = Path(tmp) / f"{name}.py"
        script.write_bytes(body)
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": os.environ.get("LANG", "C.UTF-8"),
               "HOME": tmp}
        # stdin and stderr are files beside the temp directory, not pipes: nothing a
        # gate leaves running can hold a pipe open and keep this call waiting
        io_dir = Path(tempfile.mkdtemp(prefix="brain-gate-io-"))
        try:
            (io_dir / "in.json").write_bytes(payload)
            with open(io_dir / "in.json", "rb") as fin, open(io_dir / "err.txt", "wb") as ferr:
                try:
                    proc = subprocess.Popen(_gate_argv(script), cwd=tmp, env=env, stdin=fin,
                                            stdout=subprocess.DEVNULL, stderr=ferr,
                                            start_new_session=True)
                except OSError as exc:
                    return f"Brain gate {name} could not start ({type(exc).__name__})"
                try:
                    proc.wait(timeout=GATE_TIMEOUT_S)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                    return f"Brain gate {name} did not answer within {GATE_TIMEOUT_S}s"
            text = (io_dir / "err.txt").read_bytes()[:_REASON_MAX * 4].decode("utf-8", "replace").strip()[:_REASON_MAX]
        finally:
            import shutil
            shutil.rmtree(io_dir, ignore_errors=True)
        if proc.returncode == 0:
            return None
        if proc.returncode == 2:
            return f"Brain gate {name} refused this: {text or 'no reason given'}"
        return f"Brain gate {name} failed (exit {proc.returncode}){': ' + text if text else ''}"


def run_gates(workspace: Path, pin: dict[str, Any], input_data: dict[str, Any]) -> Optional[str]:
    """Run the pinned Brain's enforced gates for one tool call; the refusal reason, or
    None when every gate lets it through."""
    if pin.get("broken"):
        return f"This Project Agent session cannot run its gates: {pin['broken']}"
    names = [g for g in pin.get("gates") or [] if isinstance(g, str)]
    if not names:
        return None
    try:
        d = brain_dir(workspace, str(pin.get("brain")))
        digest, files = gates_snapshot(d)
    except (PinError, OSError) as exc:
        return f"The Brain's gates cannot be read: {exc}"
    if digest != pin.get("gates_digest"):
        return ("The Brain's gates were changed since this session started; "
                "the gates did not run and the call is refused")
    payload = json.dumps({"hook_event_name": "PreToolUse", "hookEventName": "PreToolUse",
                          "tool_name": input_data.get("tool_name"),
                          "tool_input": input_data.get("tool_input"),
                          "cwd": input_data.get("cwd")}).encode("utf-8")
    for name in names:
        body = files.get(f"{name}.py")
        if body is None:
            return f"Brain gate {name} is not in the Brain's 3-gates/"
        reason = _run_one(name, body, payload)
        if reason:
            return reason
    return None


REFUSALS_KEY = "brain_gate_refusals"
_GATE_REASON = re.compile(r"Brain gate ([A-Za-z0-9][A-Za-z0-9._-]{0,79}) (refused this|could not start|"
                          r"did not answer|failed)")


def refusal_label(reason: str) -> str:
    """What refused a call: the gate's name (it refused, exit 2), ``error:<name>`` (it
    could not start, timed out or failed), or ``integrity`` (the gates could not be
    read, were changed since the session started, or are missing)."""
    m = _GATE_REASON.match(reason or "")
    if not m:
        return "integrity"
    return m.group(1) if m.group(2) == "refused this" else f"error:{m.group(1)}"


def take_refusals(session_context: Optional[dict[str, Any]]) -> Optional[dict[str, int]]:
    """The refusals counted since the last call, and reset them; None when this is not
    a Project Agent session (no pin was ever read into the context). A refusal made
    after a session's last turn (a background agent's call) is counted into its next
    turn, or not recorded when there is none."""
    if not session_context or (PIN_KEY not in session_context and REFUSALS_KEY not in session_context):
        return None
    got = session_context.pop(REFUSALS_KEY, None)
    return dict(got) if isinstance(got, dict) else {}


def restore_refusals(session_context: Optional[dict[str, Any]], refusals: Optional[dict[str, int]]) -> None:
    """Put back counts a failed outcome write did not record (they go to the next one)."""
    if not session_context or not refusals:
        return
    now = session_context.setdefault(REFUSALS_KEY, {})
    for k, n in refusals.items():
        now[k] = now.get(k, 0) + n


def create_brain_gate_hook(session_context: dict[str, Any]):
    """PreToolUse hook (Bash, Write, Edit, MultiEdit, NotebookEdit): in a Project
    Agent session, run the Brain's enforced gates (``run_gates``); outside one, do
    nothing. The gates run off the event loop, one after another, each bounded by
    ``GATE_TIMEOUT_S``; the hook is registered without the chain's 5 s cut-off so a
    slow gate is refused by its own bound, never let through by a cancelled hook."""

    async def brain_gate(input_data: dict[str, Any], tool_use_id: Any, context: Any) -> dict[str, Any]:
        if input_data.get("tool_name") not in GATED_TOOLS:
            return {}
        pin = await current_pin(session_context)
        if not pin:
            return {}
        try:
            from core import executors
            from core.project_registry import get_swarmws
            reason = await executors.run_in("brain_gate", run_gates, get_swarmws(), pin, input_data)
        except Exception as exc:  # noqa: BLE001 — a gate that cannot run refuses, never lets through
            reason = f"The Brain's gates could not run ({type(exc).__name__}); the call is refused"
        if not reason:
            return {}
        logger.warning("[BRAIN_GATE] %s refused for %s: %s", input_data.get("tool_name"),
                       pin.get("brain"), reason[:200])
        # counted for the turn's outcome record (SessionRouter._write_turn_outcome)
        refusals = session_context.setdefault(REFUSALS_KEY, {})
        label = refusal_label(reason)
        refusals[label] = refusals.get(label, 0) + 1
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                       "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}

    return brain_gate
