"""Pure DDD destination rules D1-D6.

- ``evaluate`` returns all rule outcomes without filesystem access.
- D1 preserves what/how/why or symptom/cause/avoidance as one unit.
"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer

VERSION = "cdf-ddd-r15.1-v1"
_VOLATILE = re.compile(r"(?:\bline\s+\d+\b|\b\d+\s+(?:tests?|files?|bytes?)\b|\b\d+(?:\.\d+)?\s*(?:KB|MB|GB)\b)", re.I)
_VERSIONED = re.compile(r"(?:as of|截至|version|版本|commit|release)", re.I)


def _result(rule: str, passed: bool, reason: str = "pass", reroute_to: str | None = None) -> RuleAnswer:
    return RuleAnswer(rule=rule, version=VERSION, passed=passed, reason=reason, reroute_to=reroute_to)


def evaluate(answer: Card1Answer, destination: Destination, text: str, **facts: Any) -> list[RuleAnswer]:
    """Evaluate D1-D6 using explicit structural facts from normalization/judgment."""
    why = (facts.get("why") or "").strip()
    how = (facts.get("how") or "").strip()
    semantic_complete = bool(facts.get("semantic_complete", True))
    if answer.entry_type == "decision":
        d1 = bool(text.strip() and how and why and semantic_complete)
    elif answer.entry_type in {"pitfall", "correction"}:
        d1 = bool(facts.get("symptom") and facts.get("cause") and facts.get("avoidance") and semantic_complete)
    else:
        d1 = semantic_complete
    target_matches = bool(facts.get("target_matches", True))
    reroute_to = facts.get("rule_reroute_to") if not target_matches else None
    contextual = bool(facts.get("contextual", True))
    stable = not _VOLATILE.search(text) or bool(_VERSIONED.search(text))
    format_ok = bool(facts.get("format_ok", True))
    project_count = int(facts.get("project_count", 1))
    return [
        _result("D1", d1, "pass" if d1 else "ddd_missing_why"),
        _result("D2", target_matches, "pass" if target_matches else "destination_mismatch", reroute_to),
        _result("D3", contextual, "pass" if contextual else "ddd_context_split"),
        _result("D4", stable, "pass" if stable else "ddd_volatile"),
        _result("D5", format_ok, "pass" if format_ok else "ddd_format"),
        _result("D6", project_count >= 1, "pass" if project_count >= 1 else "ambiguous_project"),
    ]
