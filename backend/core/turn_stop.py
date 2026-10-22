"""What a user Stop stopped, as a persisted ``turn_stop`` block (the stop receipt).

A user Stop ends the turn AND the AI process it ran in (PIT01 recycle), so the
foreground call that was running, any foreground sub-agent and every background
agent stop together ("one Stop stops everything", XG 2026-10-22). With no turn
running, Stop on the background-agents line ends the process the same way.

``SessionRouter.interrupt_session`` builds the block FIRST, from what the unit is
running at that instant (``SessionUnit.begin_stop_receipt``), and persists it before
it awaits the interrupt: a message sent meanwhile is stored after it, so the receipt
can never land on the next turn, and the response is not held up by the stop. Every
background agent running then is ended by the stop, so it is written ``stopped``
(the registry is told the same, so its row does not read "lost").

It is a display block: ``context_injector`` keeps it out of resume history. What
the model needs on the next send is ``continuation_note``, injected once.

Only facts the daemon holds are written: call ids, tool names, how long they ran,
whether a call was still waiting for the user's approval (it never ran), background
agents' descriptions. A process started detached from the AI process is not known
here and is named in the note as "may still be running", never as stopped.

What stopped work leaves behind (measured on this machine's CLI transcripts,
2026-10-22): an interrupted foreground call keeps NO output (its result is the SDK's
fixed rejection text), so for a Bash only the files its command names as write
targets that exist now are listed (``writes``: may be written fully or partly); an
Agent, foreground or background, keeps its own transcript with every call it made
but no conclusion (``transcript``: path and call count).

- ``build(...)``                — the ``turn_stop`` block.
- ``agent_transcript(sid, task_id)`` / ``existing_write_targets(paths, cwd)`` — the
  leftovers above.
- ``continuation_note(block)``  — the one-time note for the next send.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

#: rows kept per list (a turn with more running calls is cut, with a count)
MAX_ROWS = 20
_DESC_MAX = 120
#: write targets kept per stopped call
MAX_WRITES = 10
#: a sub-agent transcript larger than this is not read to count its calls
_TRANSCRIPT_READ_MAX = 32 * 1024 * 1024
_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def agent_transcript(sdk_session_id: Optional[str], task_id: Optional[str]) -> Optional[dict]:
    """Where a sub-agent's work survives a stop: the CLI keeps each Agent's own
    transcript at ``<projects>/<cwd>/<sdk-session>/subagents/agent-<task id>.jsonl``
    (measured 2026-10-22: a background agent ended mid-way kept all its calls,
    without a conclusion). ``{"path", "toolCalls"}``, or None when it is not there.
    Ids are checked before they become path parts."""
    if not task_id or not _TASK_ID_RE.match(task_id):
        return None
    from .leak_transcript import find_transcript
    main = find_transcript(sdk_session_id)
    if main is None:
        return None
    path = main.parent / main.stem / "subagents" / f"agent-{task_id}.jsonl"
    try:
        if not path.is_file():
            return None
        out: dict = {"path": str(path)}
        if path.stat().st_size <= _TRANSCRIPT_READ_MAX:
            out["toolCalls"] = path.read_bytes().count(b'"type":"tool_use"')
        return out
    except OSError:
        return None


def existing_write_targets(paths: Iterable[str], cwd: Optional[str]) -> list[str]:
    """A stopped call's write targets (parsed from its command when it started) that
    exist now, as absolute paths: what it may have written, fully or partly."""
    out: list[str] = []
    for p in paths:
        if not isinstance(p, str) or not p.strip():
            continue
        full = p if os.path.isabs(p) else (os.path.join(cwd, p) if cwd else "")
        if full and os.path.isfile(full) and full not in out:
            out.append(os.path.normpath(full))
    return out[:MAX_WRITES]


def build(
    *,
    open_tool_uses: Mapping[str, Any],
    agent_tools: Mapping[str, Any],
    background: Iterable[Mapping[str, Any]],
    now: float,
    not_run: Optional[str] = None,
    writes: Optional[Mapping[str, Iterable[str]]] = None,
    transcripts: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> dict:
    """The ``turn_stop`` block for a Stop happening now.

    ``open_tool_uses``: tool_use_id -> (start, name) (SessionUnit._open_tool_uses);
    ``agent_tools``: tool_use_id -> {"label"} for foreground Agents;
    ``background``: BackgroundAgents.running() entries; ``not_run``: the call whose
    approval the user had not given yet (it never started). ``now`` is on the same
    clock as the starts (awake_time). What the stopped work left behind:
    ``writes``: tool_use_id -> files a stopped call names as a write target that
    exist now (it may have written them, fully or partly); ``transcripts``:
    tool_use_id -> ``agent_transcript()`` of an Agent (foreground or background).
    """
    writes = writes or {}
    transcripts = transcripts or {}
    calls = []
    for tid, val in list(open_tool_uses.items()):
        try:
            start, name = val
        except (TypeError, ValueError):
            continue
        row: dict = {"toolUseId": str(tid), "name": str(name or "")}
        if not_run and str(tid) == not_run:
            row["ran"] = False
        else:
            try:
                row["elapsedMs"] = max(0, int((float(now) - float(start)) * 1000))
            except (TypeError, ValueError):
                pass
            w = [str(p) for p in (writes.get(tid) or [])][:MAX_WRITES]
            if w:
                row["writes"] = w
            if transcripts.get(tid):
                row["transcript"] = dict(transcripts[tid])
        label = (agent_tools.get(tid) or {}).get("label") if isinstance(agent_tools, Mapping) else None
        if isinstance(label, str) and label.strip():
            row["description"] = label.strip()[:_DESC_MAX]
        calls.append(row)
    agents = []
    for e in background:
        tid = e.get("toolUseId")
        if tid:
            row = {"toolUseId": str(tid), "description": str(e.get("description") or "")[:_DESC_MAX],
                   "status": "stopped", "background": True}
            if transcripts.get(tid):
                row["transcript"] = dict(transcripts[tid])
            agents.append(row)
    block: dict = {"type": "turn_stop", "at": datetime.now(timezone.utc).isoformat(),
                   "calls": calls[:MAX_ROWS], "agents": agents[:MAX_ROWS]}
    omitted = max(0, len(calls) - MAX_ROWS) + max(0, len(agents) - MAX_ROWS)
    if omitted:
        block["omitted"] = omitted
    return block


def _dur(ms: Any) -> str:
    try:
        s = int(ms) // 1000
    except (TypeError, ValueError):
        return ""
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def continuation_note(block: Mapping[str, Any]) -> str:
    """The note the next send carries (once), so a Continue resumes from the stop
    instead of re-running a half-done call. App-authored facts only."""
    parts = [f"[App note, not from the user: the user pressed Stop at {block.get('at', '')}."]
    calls = block.get("calls") or []
    cut = [c for c in calls if c.get("ran") is not False]
    waiting = [c for c in calls if c.get("ran") is False]
    if cut:
        rows = []
        for c in cut:
            d = _dur(c.get("elapsedMs"))
            desc = f' "{c["description"]}"' if c.get("description") else ""
            rows.append(f"{c.get('name') or 'tool'}{desc} (id {c.get('toolUseId')}{', after ' + d if d else ''})")
        parts.append("Stopped mid-call: " + "; ".join(rows) + ". A stopped call's output was not kept.")
        for c in cut:
            if c.get("writes"):
                parts.append(f"{c.get('name')} {c.get('toolUseId')} may have written, fully or partly: "
                             + ", ".join(c["writes"]) + ".")
            if (c.get("transcript") or {}).get("path"):
                parts.append(f"Its sub-agent's work so far (transcript): {c['transcript']['path']}.")
    if waiting:
        rows = [f"{c.get('name') or 'tool'} (id {c.get('toolUseId')})" for c in waiting]
        parts.append("Never ran (it was waiting for the user's approval): " + "; ".join(rows) + ".")
    agents = block.get("agents") or []
    if agents:
        rows = [f'"{a.get("description") or a.get("toolUseId")}" ({a.get("status")}'
                + (f', its work so far: {a["transcript"]["path"]}' if (a.get("transcript") or {}).get("path") else "")
                + ")" for a in agents]
        parts.append("Background agents ended by the stop (no conclusion was written): " + "; ".join(rows) + ".")
    parts.append(
        "A stopped call may have left partial effects (files written, outputs, started "
        "processes); a process started detached from you (nohup, setsid, a daemon) may "
        "still be running. If you are asked to continue, first check what the stopped "
        "work already did and pick up from there; do not blindly re-run a command with "
        "side effects. A \"No response requested.\" just before this note was written "
        "by the CLI when it resumed the stopped turn; it is not a reply you gave, and "
        "the user never saw it.]")
    return " ".join(parts)
