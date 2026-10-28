"""Pure EVOLUTION destination rules E1-E2.

- ``evaluate`` enforces agent subject and writer-owned block formatting.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer

VERSION = "cdf-evolution-r15.1-v1"


def evaluate(answer: Card1Answer, destination: Destination, text: str, **facts: Any) -> list[RuleAnswer]:
    """Evaluate agent subject and format eligibility."""
    subject_ok = answer.shape == "Agent" and bool(facts.get("agent_subject", True))
    format_ok = bool(facts.get("format_allocatable", True))
    return [
        RuleAnswer(rule="E1", version=VERSION, passed=subject_ok,
                   reason="pass" if subject_ok else "evolution_wrong_subject"),
        RuleAnswer(rule="E2", version=VERSION, passed=format_ok,
                   reason="pass" if format_ok else "evolution_format"),
    ]
