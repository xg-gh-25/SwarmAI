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

- ``build(...)``                — the ``turn_stop`` block.
- ``continuation_note(block)``  — the one-time note for the next send.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

#: rows kept per list (a turn with more running calls is cut, with a count)
MAX_ROWS = 20
_DESC_MAX = 120


def build(
    *,
    open_tool_uses: Mapping[str, Any],
    agent_tools: Mapping[str, Any],
    background: Iterable[Mapping[str, Any]],
    now: float,
    not_run: Optional[str] = None,
) -> dict:
    """The ``turn_stop`` block for a Stop happening now.

    ``open_tool_uses``: tool_use_id -> (start, name) (SessionUnit._open_tool_uses);
    ``agent_tools``: tool_use_id -> {"label"} for foreground Agents;
    ``background``: BackgroundAgents.running() entries; ``not_run``: the call whose
    approval the user had not given yet (it never started). ``now`` is on the same
    clock as the starts (awake_time).
    """
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
        label = (agent_tools.get(tid) or {}).get("label") if isinstance(agent_tools, Mapping) else None
        if isinstance(label, str) and label.strip():
            row["description"] = label.strip()[:_DESC_MAX]
        calls.append(row)
    agents = []
    for e in background:
        tid = e.get("toolUseId")
        if tid:
            agents.append({"toolUseId": str(tid), "description": str(e.get("description") or "")[:_DESC_MAX],
                           "status": "stopped", "background": True})
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
        parts.append("Stopped mid-call: " + "; ".join(rows) + ".")
    if waiting:
        rows = [f"{c.get('name') or 'tool'} (id {c.get('toolUseId')})" for c in waiting]
        parts.append("Never ran (it was waiting for the user's approval): " + "; ".join(rows) + ".")
    agents = block.get("agents") or []
    if agents:
        rows = [f'"{a.get("description") or a.get("toolUseId")}" ({a.get("status")})' for a in agents]
        parts.append("Background agents ended by the stop: " + "; ".join(rows) + ".")
    parts.append(
        "A stopped call may have left partial effects (files written, outputs, started "
        "processes); a process started detached from you (nohup, setsid, a daemon) may "
        "still be running. If you are asked to continue, first check what the stopped "
        "work already did and pick up from there; do not blindly re-run a command with "
        "side effects.]")
    return " ".join(parts)
