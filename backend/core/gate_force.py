"""The one operator switch that lets a call through a gate: ``SWARM_GATE_FORCE``.

Replaces ten per-gate ``SWARM_*_FORCE=1`` variables. Set it in the backend / daemon
environment (a session's own shell cannot reach the hooks, which run in the daemon) to
a comma-separated list of gate names or group names::

    SWARM_GATE_FORCE=run_amend_gate,expert_gate

Every use is recorded as ``force_bypass`` in ``gate_decisions.jsonl``.

The gate in ``UNFORCEABLE`` cannot be forced: it protects the run<->session link
every other run gate keys on. Naming one is ignored and logged.

Key public symbols:

- ``force_enabled(gate, input_data=None)`` -- True when ``gate`` (or its group) is listed
- ``GROUPS``      -- group name -> member gate names
- ``UNFORCEABLE`` -- gates that ignore the switch
- ``FORCE_ENV``   -- the variable name
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .gate_decision_log import record_force_bypass

logger = logging.getLogger(__name__)

FORCE_ENV = "SWARM_GATE_FORCE"

GROUPS: dict[str, frozenset[str]] = {
    "commit_gate": frozenset({"run_amend_gate", "active_run_commit_gate"}),
    "expert_gate": frozenset({"expert_type_gate", "expert_budget_gate"}),
}

UNFORCEABLE: frozenset[str] = frozenset({"session_link_gate"})


def _listed() -> set[str]:
    raw = os.environ.get(FORCE_ENV, "")
    return {p.strip() for p in raw.split(",") if p.strip()}


def force_enabled(gate: str, input_data: Any = None) -> bool:
    """True when the operator listed ``gate`` or a group containing it.

    Records a ``force_bypass`` line when it returns True, so call it only at the
    point where the bypass is actually taken.
    """
    listed = _listed()
    if not listed:
        return False
    hit = gate in listed or any(gate in GROUPS.get(g, ()) for g in listed)
    if not hit:
        return False
    if gate in UNFORCEABLE:
        logger.warning("%s names %s, which cannot be forced; ignored", FORCE_ENV, gate)
        return False
    logger.warning("[FORCE] %s bypassed by %s", gate, FORCE_ENV)
    record_force_bypass(gate, input_data, f"{FORCE_ENV}={os.environ.get(FORCE_ENV, '')}")
    return True
