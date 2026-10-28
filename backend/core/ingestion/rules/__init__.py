"""Versioned pure destination rules for CDF Phase A.

- ``evaluate_destination_rules`` dispatches by card 2 store.
- Individual modules expose one function per normative rule.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer
from . import context, ddd, evolution, library, memory


def evaluate_destination_rules(
    answer: Card1Answer,
    destination: Destination,
    text: str,
    **facts: Any,
) -> list[RuleAnswer]:
    """Run every rule for one destination in stable order."""
    if destination.store == "DDD":
        return ddd.evaluate(answer, destination, text, **facts)
    if destination.store == "MEMORY":
        return memory.evaluate(answer, destination, text, **facts)
    if destination.store == "EVOLUTION":
        return evolution.evaluate(answer, destination, text, **facts)
    if destination.store in {"STEERING", "USER", "TOOLS"}:
        return context.evaluate(answer, destination, text, **facts)
    if destination.store == "KNOWLEDGE":
        return library.evaluate(answer, destination, text, **facts)
    return []


__all__ = ["evaluate_destination_rules"]
