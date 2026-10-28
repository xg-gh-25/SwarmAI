"""Deterministic CDF card 0 checks.

- ``evaluate_card0`` checks safety, noise, size, identity, and quota inputs.
- Taint is enforced separately after card 1 because content form is then known.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection

from .contracts import Card0Answer
from .normalize import ClaimGroup

CARD0_VERSION = "cdf-card0-r15.1-v1"
_UNSAFE = (
    re.compile(r"(?im)^\s*(?:system|assistant|developer)\s*:\s*"),
    re.compile(r"(?i)(?:ignore|override)\s+(?:all\s+)?(?:(?:previous|prior)(?:\s+system)?|system)\s+instructions?"),
    re.compile(r"(?i)BEGIN[_ ](?:SYSTEM|PROMPT|INSTRUCTIONS)"),
)
_NOISE_ONLY = (
    re.compile(r"^(?:session|run)[-_ ]?id\s*[:=]", re.I),
    re.compile(r"^pid\s*[:=]?\s*\d+$", re.I),
    re.compile(r"^[0-9a-f]{7,40}$", re.I),
    re.compile(r"^\d+\s+(?:tests?|测试)\s+(?:passed|通过)$", re.I),
)


def content_fingerprint(text: str) -> str:
    """Hash whitespace-normalized UTF-8 content."""
    normalized = " ".join((text or "").split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def evaluate_card0(
    group: ClaimGroup,
    *,
    content_form: str | None = None,
    existing_fingerprints: Collection[str] = (),
    session_exists: bool = True,
    quota_available: bool = True,
    id_collision: bool = False,
    reference_limit: int = 16_384,
) -> Card0Answer:
    """Apply card 0 in normative order and return the first reason code."""
    text = group.text.strip()
    if any(pattern.search(text) for pattern in _UNSAFE):
        return Card0Answer(passed=False, reason="unsafe")
    if not text or any(pattern.fullmatch(text) for pattern in _NOISE_ONLY):
        return Card0Answer(passed=False, reason="noisy")
    byte_count = len(text.encode("utf-8"))
    if content_form == "rule" and len(text) > 600:
        return Card0Answer(passed=False, reason="not_atomic")
    if content_form == "reference" and byte_count > reference_limit:
        return Card0Answer(passed=False, reason="too_large")
    if content_fingerprint(text) in existing_fingerprints:
        return Card0Answer(passed=False, reason="already_exists")
    if not session_exists:
        return Card0Answer(passed=False, reason="session_gone")
    if not quota_available:
        return Card0Answer(passed=False, reason="quota")
    if id_collision:
        return Card0Answer(passed=False, reason="id_collision")
    return Card0Answer(passed=True)
