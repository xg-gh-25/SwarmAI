"""The Swarm Run card in a lead run's chat (design docs/design/2026-10-31-swarm-run.md
§5.9; impl step 8).

A ``swarm_run`` block, like the dispatch card (core/swarm_out_card): written by the
backend after a ``pipeline-v1 child … dispatch`` call's result, from this machine's
dispatch log, never from model text, and persisted as its own row. One card per Swarm
Run: only the call that sent the run's first children makes it; later dispatches show
in the same card, which reads the live runs (the desktop SwarmRunCard filters Swarm Out's
``/runs`` by ``parent_run`` and reads ``/swarm-runs/<run>`` for its head). A child gets
no dispatch card of its own.

- ``watched(command)``: a child dispatch call.
- ``cards(command, output, since)``: ``[block]`` or ``[]``.
- ``channel_text(block)``: the card as one line where it cannot be drawn.
"""
from __future__ import annotations

import re
import shlex
from typing import Any, Optional

BLOCK_TYPE = "swarm_run"
_CHILD_DISPATCH_RE = re.compile(r"\bpipeline-v1\s+child\b(?:\s+--?\S+(?:\s+[^-\s]\S*)?)*\s+dispatch\b")
_RUN_RE = re.compile(r"run_[0-9a-f]{8,32}")
_SKEW_S = 5.0


def watched(command: Any) -> bool:
    if not isinstance(command, str):
        return False
    from core.swarm_out_card import _without_heredoc_bodies
    return bool(_CHILD_DISPATCH_RE.search(_without_heredoc_bodies(command)))


def _run_id(command: str) -> Optional[str]:
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    for i, w in enumerate(words):
        value = words[i + 1] if w == "--run-id" and i + 1 < len(words) else (
            w.split("=", 1)[1] if w.startswith("--run-id=") else None)
        if value and _RUN_RE.fullmatch(value):
            return value
    return None


def cards(command: str, output: str, since: Optional[float] = None, *_: Any) -> list[dict]:
    """The run's card when this call sent its first children (see the module doc)."""
    from core import swarm_out, swarm_run
    from core.swarm_out_card import _sent_at
    run_id = _run_id(command)
    if run_id is None or since is None:
        return []
    kids = swarm_run.children(swarm_out._read_log(), run_id)
    times = [t for t in (_sent_at(r) for r in kids) if t is not None]
    if not times or min(times) < since - _SKEW_S:
        return []          # nothing went out, or the card was made by an earlier call
    project = next((r.get("project") for r in kids if r.get("project")), None)
    return [{"type": BLOCK_TYPE, "runId": run_id, "project": project}]


def channel_text(block: dict) -> str:
    return f"Swarm Run {block.get('runId')}: its children are on the person's Hives (see Swarm Out)."
