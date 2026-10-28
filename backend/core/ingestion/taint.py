"""CDF taint constraints that run before deterministic routing.

- ``check_taint`` prevents tainted rules from reaching governed cognition.
- Tainted references are constrained to the ``Reference`` shape.
"""

from __future__ import annotations

from .contracts import Card1Answer

TAINT_VERSION = "cdf-taint-r15.1-v1"


def check_taint(answer: Card1Answer, tainted: bool) -> str | None:
    """Return a rejection reason, or ``None`` when routing may continue."""
    if not tainted:
        return None
    if answer.content_form == "rule":
        return "tainted_rule"
    if answer.shape != "Reference":
        return "invalid_shape_for_trust"
    return None
