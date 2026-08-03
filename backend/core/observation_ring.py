"""Session Observation Ring — fixed-size tool invocation recorder.

Records PreToolUse intent + PostToolUse result into an in-memory ring buffer.
Consumers (checkpoint, DDD events) pull data from the ring on demand.

Public symbols:
    - Observation      — single tool invocation record (dataclass)
    - ObservationRing  — bounded deque wrapper with record/snapshot/cleanup API

Design constraints:
    - record_pre/record_post: <0.1ms (no IO, no lock, no network)
    - Memory: ~80KB at capacity (200 observations × ~400 bytes avg)
    - Thread safety: single writer guaranteed by sequential hook chain (GIL)
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass


@dataclass(slots=True)
class Observation:
    """Single tool invocation record. Immutable after completion."""

    ts: float  # monotonic timestamp (for duration calc)
    tool_name: str  # "Bash", "Read", "Edit", "Skill", "Agent", etc.
    intent: str  # 1-line purpose (max 200 chars)
    files: list[str]  # file paths from input (max 5)
    completed: bool = False
    result_status: str = ""  # "success" | "error"
    duration_ms: int = 0


class ObservationRing:
    """Fixed-size ring buffer of tool observations.

    Invariants:
        - Max size: ``maxlen`` observations (default 200)
        - Memory: ~80KB at full capacity
        - Write latency: <0.1ms (deque append, no IO)
        - No locks needed: single writer (hook chain is sequential)
    """

    def __init__(self, maxlen: int = 200) -> None:
        self._ring: deque[Observation] = deque(maxlen=maxlen)
        self._pending: dict[str, Observation] = {}  # tool_use_id → Observation ref

    # ── Recording ────────────────────────────────────────────────

    def record_pre(self, tool_use_id: str, tool_name: str, tool_input: dict) -> None:
        """Record PreToolUse. Called from hook — must be <1ms."""
        obs = Observation(
            ts=time.monotonic(),
            tool_name=tool_name,
            intent=self._extract_intent(tool_name, tool_input),
            files=self._extract_files(tool_name, tool_input),
        )
        self._ring.append(obs)
        self._pending[tool_use_id] = obs  # Store object ref (survives deque rotation)

    def record_post(self, tool_use_id: str, error: str | None) -> Observation | None:
        """Record PostToolUse result. Called from hook — must be <1ms.

        Returns the completed Observation (or None if tool_use_id unknown).
        """
        obs = self._pending.pop(tool_use_id, None)
        if obs is None:
            return None
        obs.completed = True
        obs.result_status = "error" if error else "success"
        obs.duration_ms = int((time.monotonic() - obs.ts) * 1000)
        return obs

    # ── Consumers ────────────────────────────────────────────────

    def snapshot(self, last_n: int = 10) -> list[dict]:
        """Return last N completed observations as dicts (for checkpoint JSON)."""
        completed = [o for o in self._ring if o.completed]
        return [self._to_dict(o) for o in completed[-last_n:]]

    def last_completed(self) -> Observation | None:
        """Most recent completed observation (for DDD event check)."""
        for obs in reversed(self._ring):
            if obs.completed:
                return obs
        return None

    def all_completed(self) -> list[Observation]:
        """All completed observations (for future pattern mining)."""
        return [o for o in self._ring if o.completed]

    # ── Maintenance ──────────────────────────────────────────────

    def pending_cleanup(self) -> None:
        """Discard pending entries older than 5 minutes. Call periodically."""
        now = time.monotonic()
        stale_keys = [
            key for key, obs in self._pending.items()
            if (now - obs.ts) > 300
        ]
        for k in stale_keys:
            del self._pending[k]

    # ── Private helpers ──────────────────────────────────────────

    @staticmethod
    def _extract_intent(tool_name: str, tool_input: dict) -> str:
        """Extract 1-line intent from tool input. Pure string ops, <0.01ms."""
        if tool_name == "Bash":
            desc = tool_input.get("description", "")
            if desc:
                return desc[:200]
            cmd = tool_input.get("command", "")
            return f"$ {cmd}"[:200]
        elif tool_name in ("Read", "Glob", "Grep"):
            path = tool_input.get("file_path") or tool_input.get("pattern") or ""
            return f"{tool_name}: {path}"[:200]
        elif tool_name in ("Edit", "Write"):
            path = tool_input.get("file_path", "")
            return f"{tool_name}: {path}"[:200]
        elif tool_name == "Skill":
            skill = tool_input.get("skill", "?")
            return f"Skill: {skill}"[:200]
        elif tool_name == "Agent":
            desc = tool_input.get("description", "")
            return f"Agent: {desc}"[:200]
        return tool_name[:200]

    @staticmethod
    def _extract_files(tool_name: str, tool_input: dict) -> list[str]:
        """Extract file paths from tool input (max 5)."""
        if "file_path" in tool_input:
            return [tool_input["file_path"]]
        if "path" in tool_input:
            return [tool_input["path"]]
        return []

    @staticmethod
    def _to_dict(obs: Observation) -> dict:
        """Serialize observation for JSON checkpoint."""
        return {
            "tool": obs.tool_name,
            "intent": obs.intent,
            "status": obs.result_status,
            "duration_ms": obs.duration_ms,
            "files": obs.files,
        }
