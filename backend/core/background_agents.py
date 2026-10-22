"""BackgroundAgents — the background Agents of one session and their status.

An Agent called with ``run_in_background`` returns at once ("Async agent launched")
and keeps running after the turn that started it ended; the CLI reports its end with
a ``task_notification`` (or a terminal ``task_updated``) and then starts a turn of its
own to answer it. This registry follows each one so its status can be shown where it
was started (its Agent step), in one line above the composer while it runs, and at
the top of the CLI turn that answers it.

Statuses: ``running`` → ``completed`` / ``failed`` / ``stopped`` (the CLI said so),
or ``lost`` (the CLI process went away first: its result will never come), or
``stopped`` when that process went away because of the user's Stop (``user_stop``).

- ``launch(tool_use_id, description)``: the orchestrator saw a background Agent call.
- ``task_started`` / ``task_finished``: the stream reader's task frames, with or
  without a turn open. A task that finishes before its launch was recorded (the reader
  runs ahead of the orchestrator) is kept and applied at launch.
- ``lose_all()``: the CLI process is gone; ``user_stop(on)`` frames a user Stop.
- ``task_id_of(tool_use_id)``: the task (agent) id of any Agent call a frame named.
- Every change calls ``on_change(entry)``; ``take_unannounced()`` returns the agents
  that finished since the last CLI turn took them (recent ones only).

Pure bookkeeping, no I/O. Bounded: the newest ``MAX_AGENTS`` are kept.

@exports BackgroundAgents, TERMINAL, normalize_status
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Callable, Optional

MAX_AGENTS = 50
#: a finish older than this is not announced at the top of a later CLI turn
ANNOUNCE_WINDOW_S = 600.0
TERMINAL = frozenset({"completed", "failed", "stopped", "lost"})


def normalize_status(status: Any) -> Optional[str]:
    """The CLI's terminal task status as one of ``TERMINAL`` (None if not terminal)."""
    s = str(status or "").lower()
    if s in ("completed", "failed", "stopped"):
        return s
    if s in ("killed", "cancelled", "canceled"):
        return "stopped"
    return None


def _now_ms() -> int:
    return int(time.time() * 1000)


class BackgroundAgents:
    def __init__(self, on_change: Optional[Callable[[dict], None]] = None) -> None:
        self._on_change = on_change
        self._agents: "OrderedDict[str, dict]" = OrderedDict()  # tool_use_id -> entry
        self._task_tool: dict[str, str] = {}  # task_id -> tool_use_id
        self._early: dict[str, dict] = {}  # tool_use_id -> a finish seen before launch
        self._unannounced: list[str] = []
        self._user_stop = False

    # ── input ─────────────────────────────────────────────────────────

    def launch(self, tool_use_id: str, description: str = "") -> None:
        if not tool_use_id or tool_use_id in self._agents:
            return
        entry = {"toolUseId": tool_use_id, "description": (description or "")[:200],
                 "status": "running", "startedAt": _now_ms()}
        self._agents[tool_use_id] = entry
        while len(self._agents) > MAX_AGENTS:
            old, _ = self._agents.popitem(last=False)
            self._early.pop(old, None)
        early = self._early.pop(tool_use_id, None)
        if early is not None:
            self._finish(entry, **early)
            return
        self._changed(entry)

    def task_started(self, task_id: str, tool_use_id: Optional[str]) -> None:
        if task_id and tool_use_id:
            self._task_tool[task_id] = tool_use_id
            while len(self._task_tool) > 4 * MAX_AGENTS:
                self._task_tool.pop(next(iter(self._task_tool)))

    def task_finished(self, task_id: str, status: Any, *, tool_use_id: Optional[str] = None,
                      summary: Any = None, usage: Any = None) -> None:
        final = normalize_status(status)
        if final is None:
            return
        tid = tool_use_id or self._task_tool.get(task_id)
        if not tid:
            return
        info = {"status": final, "summary": summary, "usage": usage}
        entry = self._agents.get(tid)
        if entry is None:
            # not (yet) a launched background agent: a foreground agent ends here too,
            # so keep it only briefly, for a launch that is about to be recorded
            self._early[tid] = info
            while len(self._early) > MAX_AGENTS:
                self._early.pop(next(iter(self._early)))
            return
        if entry["status"] == "running":
            self._finish(entry, **info)

    def lose_all(self) -> None:
        # a user Stop is ending the process: what it ends is "stopped", not "lost"
        status = "stopped" if self._user_stop else "lost"
        for entry in self._agents.values():
            if entry["status"] == "running":
                entry["status"] = status
                entry["endedAt"] = _now_ms()
                entry.setdefault("durationMs", max(0, entry["endedAt"] - entry["startedAt"]))
                self._changed(entry)

    def user_stop(self, on: bool) -> None:
        """While on, the process going away is the user's Stop (core/turn_stop)."""
        self._user_stop = bool(on)

    # ── output ────────────────────────────────────────────────────────

    def snapshot(self) -> list[dict]:
        return [dict(e) for e in self._agents.values()]

    def task_id_of(self, tool_use_id: str) -> Optional[str]:
        """The SDK task id (the agent id) a stream frame gave this Agent call, any
        Agent (foreground or background), or None if no frame named it yet."""
        for task_id, tid in reversed(list(self._task_tool.items())):
            if tid == tool_use_id:
                return task_id
        return None

    def running(self) -> list[dict]:
        return [dict(e) for e in self._agents.values() if e["status"] == "running"]

    def take_unannounced(self) -> list[dict]:
        cutoff = _now_ms() - ANNOUNCE_WINDOW_S * 1000
        out = [dict(self._agents[t]) for t in self._unannounced
               if t in self._agents and self._agents[t].get("endedAt", 0) >= cutoff]
        self._unannounced = []
        return out

    # ── internals ─────────────────────────────────────────────────────

    def _finish(self, entry: dict, status: str, summary: Any = None, usage: Any = None) -> None:
        entry["status"] = status
        entry["endedAt"] = _now_ms()
        if isinstance(usage, dict):
            if isinstance(usage.get("duration_ms"), (int, float)):
                entry["durationMs"] = int(usage["duration_ms"])
            if isinstance(usage.get("tool_uses"), (int, float)):
                entry["toolUses"] = int(usage["tool_uses"])
        entry.setdefault("durationMs", max(0, entry["endedAt"] - entry["startedAt"]))
        if isinstance(summary, str) and summary.strip():
            entry["summary"] = summary.strip()[:300]
        if status != "lost":
            self._unannounced.append(entry["toolUseId"])
        self._changed(entry)

    def _changed(self, entry: dict) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(dict(entry))
        except Exception:  # noqa: BLE001 — a display aid never breaks the reader
            import logging
            logging.getLogger(__name__).exception("background_agents.on_change_failed")
