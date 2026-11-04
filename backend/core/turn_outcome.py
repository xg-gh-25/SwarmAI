"""How one turn ended, as a persisted ``turn_outcome`` display block.

Design: docs/design/2026-10-28-swarm-out-run-results.md §4.1. A turn's terminal events
(``result``, ``error``) are SSE events only; nothing durable said whether a turn
finished, failed or hit its turn limit, so a dispatched run (nobody listening) and a
reloaded chat could not tell. ``SessionRouter._published_turn`` — the one place every
router turn goes through — feeds each event of a started turn to an
``OutcomeRecorder`` and, in its ``finally``, writes ``recorder.block()`` through the
turn's ``TurnWriter`` (``record_outcome``) before ``close()``. A turn that never
started (queued, rejected) writes nothing.

``ended``, in order: ``crashed`` (an exception left the turn), ``error`` (the last
terminal event is an ``error``, or an erroring ``result`` other than the turn limit;
``output_degenerated`` is one), ``turn_limit`` (a ``result`` with subtype
``turn_limit_reached``), ``completed`` (any other ``result``), ``paused`` (no terminal
event; the turn stopped on a question or a permission prompt, whose continuation
writes its own outcome), ``none`` (no terminal event: a Stop — its ``turn_stop`` block
says so — an interrupt, a shutdown), ``daemon_stopped`` (never from a recorder: written
when a Hive's daemon starts again for a turn its death left open, core/hive_boot). Facts
only: the reader combines it with ``turn_stop`` / ``turn_notice``.

``cli_cost_total`` is the CLI process's running total (``result.total_cost_usd``), not
this turn's cost; ``cli_pid`` tells whether two outcomes come from one process.
``turn_costs`` turns a session's outcomes into per-turn costs.

A display block: ``context_injector`` keeps it out of resume history and the chat's
``turnView`` lists it in ``DISPLAY_TYPES``.

- ``BLOCK_TYPE``, ``ENDED``
- ``OutcomeRecorder`` — ``note(event, pid)``, ``crashed(exc)``, ``block(repos)``
- ``daemon_stopped_block(repos)`` — the outcome of a turn the daemon's death cut off
- ``cost_increase(prev, value)`` / ``turn_costs(outcomes)``
"""
from __future__ import annotations

import math

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

BLOCK_TYPE = "turn_outcome"
ENDED = ("completed", "turn_limit", "error", "crashed", "paused", "none", "daemon_stopped")

_PAUSE_EVENTS = ("ask_user_question", "cmd_permission_request")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens",
               "cache_creation_input_tokens")


def _clip(value: Any, n: int) -> Optional[str]:
    return value[:n] if isinstance(value, str) and value else None


def _int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _num(value: Any) -> Optional[float]:
    """A finite, non-negative number (JSON parsers accept ``Infinity`` and ``NaN``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
            or value < 0:
        return None
    return float(value)


class OutcomeRecorder:
    """The facts of one turn's ending, from the events it published."""

    def __init__(self) -> None:
        self._last: Optional[str] = None     # "result" | "error" | "pause"
        self._result: Optional[dict] = None
        self._error: Optional[dict] = None
        self._pid: Optional[int] = None
        self._exception: Optional[str] = None

    def note(self, event: Any, pid: Optional[int] = None) -> None:
        if not isinstance(event, dict):
            return
        t = event.get("type")
        if t == "result":
            self._last, self._result, self._pid = "result", event, _int(pid)
        elif t == "error":
            self._last, self._error = "error", event
        elif t in _PAUSE_EVENTS:
            self._last = "pause"

    def crashed(self, exc: BaseException) -> None:
        self._exception = type(exc).__name__[:64]

    def ended(self) -> str:
        if self._exception:
            return "crashed"
        if self._last == "error":
            return "error"
        if self._last == "result":
            r = self._result or {}
            if r.get("subtype") == "turn_limit_reached":
                return "turn_limit"
            if r.get("is_error") or r.get("subtype") == "output_degenerated":
                return "error"
            return "completed"
        if self._last == "pause":
            return "paused"
        return "none"

    def block(self, repos: Optional[list] = None) -> dict:
        r = self._result if self._last == "result" else None
        e = self._error if self._last == "error" else None
        usage = (r or {}).get("usage")
        block: dict[str, Any] = {
            "type": BLOCK_TYPE, "ended": self.ended(),
            "subtype": _clip((r or {}).get("subtype"), 32),
            "code": _clip((e or {}).get("code"), 64),
            "message": _clip((e or {}).get("message"), 300),
            "exception": self._exception,
            "duration_ms": _int((r or {}).get("duration_ms")),
            "num_turns": _int((r or {}).get("num_turns")),
            "cli_cost_total": _num((r or {}).get("total_cost_usd")),
            "cli_pid": self._pid if r else None,
            "usage": ({k: _int(usage.get(k)) for k in _USAGE_KEYS}
                      if isinstance(usage, dict) else None),
            "at": datetime.now(timezone.utc).isoformat(),
        }
        if repos is not None:
            block["repos"] = repos
        return block


def daemon_stopped_block(repos: Optional[list] = None, at: Optional[str] = None,
                         partial: bool = True) -> dict:
    """The outcome of a turn the daemon's death left open (core/hive_boot): the shape
    ``OutcomeRecorder.block`` writes, with nothing the dead process knew. Its gate
    refusals are lost with it: ``{}`` marked ``gate_refusals_partial`` (``partial``
    False: a turn that only sat on its question, whose paused outcome counted them).
    ``at``: when the turn last wrote (now when not known)."""
    block: dict[str, Any] = {
        "type": BLOCK_TYPE, "ended": "daemon_stopped", "subtype": None, "code": None,
        "message": None, "exception": None, "duration_ms": None, "num_turns": None,
        "cli_cost_total": None, "cli_pid": None, "usage": None,
        "at": at or datetime.now(timezone.utc).isoformat(),
        "gate_refusals": {}, "gate_refusals_partial": partial,
    }
    if repos is not None:
        block["repos"] = repos
    return block


def cost_increase(prev: Optional[tuple[Any, float]], pid: Any, value: float) -> float:
    """This turn's cost from the CLI's running total: the increase over the previous
    total of the same process; a new process (another pid, or a lower total) starts
    from zero, so its first total is that turn's cost. The one rule shared with the
    import of the old ``token_usage`` rows (core/usage_import)."""
    if prev is not None and prev[0] == pid and value >= prev[1]:
        return value - prev[1]
    return value


def turn_costs(outcomes: Iterable[dict]) -> tuple[list[Optional[float]], bool]:
    """Per-turn cost of a session's ``turn_outcome`` blocks in order (None for one
    without a total), and whether the sum may be short. An outcome without a total
    keeps the previous baseline, so the next total of the same process still counts
    the gap and the sum stays right; it is short when such a turn (any ending but a
    pause, whose continuation's total covers it: a crash, a Stop, a synthetic result)
    is not followed by a later total of the same process. Only the first total after
    such a turn can cover it: one from another process starts from zero, so what the
    turn spent is lost however many totals that process reports later."""
    out: list[Optional[float]] = []
    prev: Optional[tuple[Any, float]] = None
    short = False
    gap = False                          # a turn with no total since the last total
    for o in outcomes:
        value = _num(o.get("cli_cost_total")) if isinstance(o, dict) else None
        if value is None:
            if not (isinstance(o, dict) and o.get("ended") == "paused"):
                gap = True
            out.append(None)
            continue
        same = prev is not None and prev[0] == o.get("cli_pid") and value >= prev[1]
        if gap and not same:
            short = True                 # the gap's spend went with the old process
        gap = False                      # covered by this increase, or lost for good
        out.append(cost_increase(prev, o.get("cli_pid"), value))
        prev = (o.get("cli_pid"), value)
    return out, short or gap
