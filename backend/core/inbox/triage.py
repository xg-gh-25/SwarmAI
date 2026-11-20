"""Inbox triage: deterministic rules first, then one tool-less model call per batch.

Design: docs/design/2026-11-20-inbox.md §6.1-§6.2. STUB: the signatures below are the
contract the runner calls; the implementation replaces the bodies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from core.inbox.models import Candidate, Identity, Lane, Message, SourceCategory, TriageResult


@dataclass(frozen=True)
class RuleVerdict:
    lane: Lane                 # the lane the rules suggest
    floor: Optional[Lane]      # the lowest lane the model may move it to; None = no floor


@dataclass(frozen=True)
class TriageContext:
    user_profile: str = ""                         # the person's role and, if present, key people
    examples: tuple[str, ...] = field(default=())  # recent re-lane corrections, one line each


def rule_lane(msg: Message, me: Identity, category: SourceCategory) -> Optional[RuleVerdict]:
    """Return None for the person's own message (it only closes threads), else the rule
    verdict. Direct messages, mentions and mail with the person in To get lane REPLY with
    floor AWARE: the model may lower them to AWARE, never to SKIP."""
    raise NotImplementedError


async def triage(candidates: list[Candidate], context: TriageContext) -> list[TriageResult]:
    """One model call for the batch. Every candidate id comes back exactly once, or the
    whole batch is rejected (raise ``TriageRejected``) and retried at the next pull."""
    raise NotImplementedError


class TriageRejected(Exception):
    """The model answer did not cover every candidate id exactly once."""
