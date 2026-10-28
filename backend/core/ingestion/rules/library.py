"""Pure Knowledge Library destination rules L1-L4.

- ``evaluate`` checks independent readability, authority, metadata, and idempotency.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer

VERSION = "cdf-library-r15.1-v1"


def _r(rule: str, passed: bool, reason: str) -> RuleAnswer:
    return RuleAnswer(rule=rule, version=VERSION, passed=passed, reason="pass" if passed else reason)


def evaluate(answer: Card1Answer, destination: Destination, text: str, **facts: Any) -> list[RuleAnswer]:
    """Evaluate the four Library-specific rules."""
    title = str(facts.get("title") or "").strip()
    independently_readable = bool(facts.get("independently_readable", True))
    l1 = bool(title) and len(text) >= 200 and independently_readable
    l2 = not bool(facts.get("human_document_exists", False))
    metadata = facts.get("frontmatter", {})
    l3 = all(key in metadata for key in ("source", "ingestion_candidate", "ontology_types"))
    if facts.get("tainted", False):
        l3 = l3 and metadata.get("taint") == "external"
    message_id = facts.get("message_id")
    l4 = not bool(facts.get("message_id_seen", False)) and (message_id is not None or facts.get("source") != "backflow")
    return [
        _r("L1", l1, "library_not_independent"),
        _r("L2", l2, "superseded_by_human"),
        _r("L3", l3, "library_frontmatter"),
        _r("L4", l4, "already_exists"),
    ]
