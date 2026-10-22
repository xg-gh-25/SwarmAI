"""What a user Stop stopped, as a persisted ``turn_stop`` block (the stop receipt).

A user Stop ends the turn AND the AI process it ran in (PIT01 recycle), so the
foreground call that was running, any foreground sub-agent and every background
agent stop together ("one Stop stops everything", XG 2026-10-22). Before this the
chat only showed "Stopped by you" (or, worse, the interrupted call as a failure) and
said nothing about what was running or what happens on Continue.

``SessionUnit.interrupt`` takes a snapshot of what was running BEFORE it clears the
per-turn tracking; ``SessionRouter.interrupt_session`` turns it into this block once
the process is gone, persists it as an assistant row (so a reload shows it) and
returns it in the ``/chat/stop`` response (the frontend has already aborted the SSE
stream, so this is the only live route). It is a display block: ``context_injector``
keeps it out of resume history. What the model needs on the next send is the short
``continuation_note``, injected once by ``send()``.

Only facts the daemon holds are written: call ids, tool names, how long they ran,
background agents' descriptions and their status after the stop. Nothing is guessed
(a process detached from the AI process is named as "may still be running", never
as stopped or running).

- ``take_snapshot(...)``   — what was running at the moment of the Stop.
- ``build_block(snap, agents_now)`` — the ``turn_stop`` block.
- ``continuation_note(block)`` — the one-time note for the next send.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Optional

#: rows kept per list (a turn with more running calls is cut, with a count)
MAX_ROWS = 20
_DESC_MAX = 120


def take_snapshot(
    *,
    generation: int,
    open_tool_uses: Mapping[str, Any],
    agent_tools: Mapping[str, Any],
    background: Iterable[Mapping[str, Any]],
    now: float,
) -> dict:
    """What was running when the user pressed Stop.

    ``open_tool_uses``: tool_use_id -> (start, name) (SessionUnit._open_tool_uses);
    ``agent_tools``: tool_use_id -> {"label"} for foreground Agents;
    ``background``: BackgroundAgents.running() entries. ``now`` is on the same clock
    as the starts (awake_time).
    """
    calls = []
    for tid, val in list(open_tool_uses.items()):
        try:
            start, name = val
        except (TypeError, ValueError):
            continue
        row = {"toolUseId": str(tid), "name": str(name or "")}
        try:
            row["elapsedMs"] = max(0, int((float(now) - float(start)) * 1000))
        except (TypeError, ValueError):
            pass
        label = (agent_tools.get(tid) or {}).get("label") if isinstance(agent_tools, Mapping) else None
        if isinstance(label, str) and label.strip():
            row["description"] = label.strip()[:_DESC_MAX]
        calls.append(row)
    bg = []
    for e in background:
        tid = e.get("toolUseId")
        if tid:
            bg.append({"toolUseId": str(tid), "description": str(e.get("description") or "")[:_DESC_MAX]})
    return {"generation": generation, "calls": calls, "background": bg,
            "at": datetime.now(timezone.utc).isoformat()}


def _status_after_stop(entry: Optional[Mapping[str, Any]], process_gone: bool) -> str:
    """A background agent's status once the Stop ended the AI process.

    ``lost`` (or still ``running`` once the process is gone) means the Stop took it
    down with the process: it is "stopped". A status the CLI reported before that
    (completed / failed / stopped) is kept; ``running`` with the process still
    alive (the stop did not recycle it) stays running."""
    status = str((entry or {}).get("status") or "")
    if status in ("lost", "") or (status == "running" and process_gone):
        return "stopped"
    return status


def build_block(snap: Mapping[str, Any], agents_now: Mapping[str, Mapping[str, Any]],
                *, process_gone: bool = True) -> dict:
    """The ``turn_stop`` block. ``agents_now``: tool_use_id -> BackgroundAgents entry,
    read after the stop (so its status says how each one ended); ``process_gone``:
    the AI process no longer runs (a user Stop recycles it)."""
    calls = list(snap.get("calls") or [])
    bg = list(snap.get("background") or [])
    agents = []
    for e in bg[:MAX_ROWS]:
        now = agents_now.get(e["toolUseId"])
        row = {"toolUseId": e["toolUseId"], "description": e.get("description", ""),
               "status": _status_after_stop(now, process_gone), "background": True}
        summary = (now or {}).get("summary")
        if isinstance(summary, str) and summary.strip():
            row["summary"] = summary.strip()[:300]
        agents.append(row)
    block: dict = {"type": "turn_stop", "at": snap.get("at") or datetime.now(timezone.utc).isoformat(),
                   "calls": calls[:MAX_ROWS], "agents": agents}
    omitted = max(0, len(calls) - MAX_ROWS) + max(0, len(bg) - MAX_ROWS)
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
    parts = [f"[App note, not from the user: the user pressed Stop on your previous turn at {block.get('at', '')}."]
    calls = block.get("calls") or []
    if calls:
        rows = []
        for c in calls:
            d = _dur(c.get("elapsedMs"))
            desc = f' "{c["description"]}"' if c.get("description") else ""
            rows.append(f"{c.get('name') or 'tool'}{desc} (id {c.get('toolUseId')}{', after ' + d if d else ''})")
        parts.append("Stopped mid-call: " + "; ".join(rows) + ".")
    agents = block.get("agents") or []
    if agents:
        rows = [f'"{a.get("description") or a.get("toolUseId")}" ({a.get("status")})' for a in agents]
        parts.append("Background agents at the stop: " + "; ".join(rows) + ".")
    parts.append(
        "A stopped call may have left partial effects (files written, outputs, started "
        "processes); a process started detached from you (nohup, setsid, a daemon) may "
        "still be running. If you are asked to continue, first check what the stopped "
        "work already did and pick up from there; do not blindly re-run a command with "
        "side effects.]")
    return " ".join(parts)
