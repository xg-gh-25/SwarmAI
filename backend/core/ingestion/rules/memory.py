"""Pure MEMORY destination rules M1-M4.

- ``evaluate`` checks global relevance, executable form, and quota facts.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer

VERSION = "cdf-memory-r15.1-v1"


def _r(rule: str, passed: bool, reason: str) -> RuleAnswer:
    return RuleAnswer(rule=rule, version=VERSION, passed=passed, reason="pass" if passed else reason)


def evaluate(answer: Card1Answer, destination: Destination, text: str, **facts: Any) -> list[RuleAnswer]:
    """Evaluate the four MEMORY-specific admission rules."""
    global_ok = answer.shape == "WorldCrossGlobal" and bool(getattr(answer, "global_relevance", False))
    executable = len(text) <= 600 and bool(getattr(answer, "applicability", "")) and bool(getattr(answer, "claims", []))
    quota = bool(facts.get("daily_quota_available", True) and facts.get("total_quota_available", True))
    distinct = not bool(facts.get("duplicates_product_context", False))
    return [
        _r("M1", global_ok, "not_global"),
        _r("M2", executable, "memory_not_executable"),
        _r("M3", quota, "quota"),
        _r("M4", distinct, "already_exists"),
    ]
