"""Pre-first-token turn phases sent to the chat UI.

Before the first token the user saw only a pulsing dot for a median 15s
(TTFT p90 60s, cold spawn up to 120s). These SSE events say which step the turn
is in, so the wait is legible. Display only: no state, no timing decision.

- ``PREPARING`` — router-side work after the slot: prompt build, DB reads, recall
- ``STARTING``  — the unit is COLD and is spawning the CLI subprocess
- ``WAITING``   — the query is about to go to the model; first token pending
- ``turn_phase_event(phase)`` — the event dict yielded down the stream

The frontend clears the phase on the first event that is not a phase, heartbeat,
still_working or session_start notice, so a stale phase can never outlive the wait.
"""

from __future__ import annotations

PREPARING = "preparing"
STARTING = "starting"
WAITING = "waiting"
PHASES = (PREPARING, STARTING, WAITING)


def turn_phase_event(phase: str) -> dict:
    """The ``turn_phase`` SSE event for one of PHASES."""
    if phase not in PHASES:
        raise ValueError(f"unknown turn phase: {phase}")
    return {"type": "turn_phase", "phase": phase}
