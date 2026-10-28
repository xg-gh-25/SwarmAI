"""Pure user-context destination rules C1-C3.

- ``is_live_context_target`` enforces the C1 path boundary.
- ``evaluate`` requires explicit-user evidence and append-only semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import Card1Answer, Destination, RuleAnswer

VERSION = "cdf-context-r15.1-v1"
_ELIGIBLE = frozenset({"USER.md", "STEERING.md", "TOOLS.md"})


def is_live_context_target(
    target: Path,
    *,
    live_context_root: Path,
    templates_root: Path,
    git_tracked: bool,
    auto_write_eligible: bool,
) -> bool:
    """Return true only for an eligible untracked file directly under live .context."""
    try:
        resolved = target.resolve()
        live = live_context_root.resolve()
        templates = templates_root.resolve()
    except (OSError, RuntimeError):
        return False
    return (
        auto_write_eligible
        and not git_tracked
        and resolved.parent == live
        and resolved.name in _ELIGIBLE
        and resolved != templates
        and templates not in resolved.parents
    )


def evaluate(answer: Card1Answer, destination: Destination, text: str, **facts: Any) -> list[RuleAnswer]:
    """Evaluate C1-C3 from caller-supplied read-only path facts."""
    target = Path(facts.get("target_path", destination.relative_path))
    live = Path(facts.get("live_context_root", ".context"))
    templates = Path(facts.get("templates_root", "templates"))
    c1 = is_live_context_target(
        target, live_context_root=live, templates_root=templates,
        git_tracked=bool(facts.get("git_tracked", False)),
        auto_write_eligible=bool(facts.get("auto_write_eligible", destination.store in {"USER", "STEERING", "TOOLS"})),
    )
    evidence = facts.get("evidence")
    c2 = evidence == "user_explicit"
    c3 = bool(facts.get("append_only", True)) and not bool(facts.get("conflicts_user_text", False))
    return [
        RuleAnswer(rule="C1", version=VERSION, passed=c1, reason="pass" if c1 else "invalid_context_path"),
        RuleAnswer(rule="C2", version=VERSION, passed=c2, reason="pass" if c2 else "insufficient_evidence"),
        RuleAnswer(rule="C3", version=VERSION, passed=c3, reason="pass" if c3 else "conflicts_user_text"),
    ]
