"""Memory content validation — catches prompt injection before MEMORY.md writes.

MEMORY.md is injected into every future session's system prompt. A poisoned
memory entry affects ALL future conversations — worse than a single-session
prompt injection. This module validates content before it's written.

Public symbols:

- ``validate_memory_content``  — Check text against known injection patterns.
- ``INJECTION_PATTERNS``       — Compiled patterns (for testing/inspection).
"""

import re
import logging

logger = logging.getLogger(__name__)

# ── Injection Patterns ────────────────────────────────────────────────
#
# Each pattern is (name, compiled_regex). The name is returned when a
# match is found so the caller can log which pattern triggered.
#
# Design: patterns are intentionally broad — false positives on memory
# writes are cheap (logged warning, content rejected, user can rephrase).
# False negatives are expensive (persistent prompt injection).

_RAW_PATTERNS: list[tuple[str, str]] = [
    # Direct instruction override
    ("ignore_instructions", r"ignore\s+(all\s+)?previous\s+instructions"),
    ("ignore_above", r"ignore\s+(everything|all|anything)\s+(above|before)"),
    ("disregard_instructions", r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions"),

    # Role override
    ("you_are_now", r"you\s+are\s+now\s+(?:a|an|the)\s+"),
    ("act_as", r"(?:from\s+now\s+on\s*,?\s*)?act\s+as\s+"),
    ("pretend_to_be", r"pretend\s+(?:to\s+be|you\s+are)"),

    # System prompt extraction / manipulation
    ("system_prompt_colon", r"system\s+prompt\s*:"),
    ("reveal_instructions", r"(?:do\s+not\s+)?reveal\s+(?:your\s+)?instructions"),
    ("show_system_prompt", r"show\s+(?:me\s+)?(?:your\s+)?system\s+prompt"),
    ("print_instructions", r"(?:print|output|display|repeat)\s+(?:your\s+)?(?:system\s+)?(?:prompt|instructions)"),

    # LLM-specific instruction markers
    ("inst_marker", r"\[/?INST\]"),
    ("sys_marker", r"<</?SYS>>"),
    ("human_marker", r"\n(?:Human|Assistant)\s*:"),

    # Base64-encoded payloads (40+ chars of base64 alphabet is suspicious
    # in memory content — legitimate technical notes rarely have this)
    ("base64_payload", r"[A-Za-z0-9+/]{40,}={0,2}"),

    # Jailbreak patterns
    ("dan_jailbreak", r"(?:act\s+as|you\s+are)\s+DAN"),
    ("developer_mode", r"(?:enter|enable|activate)\s+developer\s+mode"),
]

INJECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in _RAW_PATTERNS
]


def validate_memory_content(text: str) -> tuple[bool, str | None]:
    """Check text for prompt injection patterns.

    Args:
        text: Content about to be written to MEMORY.md.

    Returns:
        (True, None) if safe.
        (False, pattern_name) if an injection pattern was detected.
    """
    if not text or not text.strip():
        return (True, None)

    for name, pattern in INJECTION_PATTERNS:
        if pattern.search(text):
            logger.warning(
                "Memory injection blocked — pattern '%s' matched in: %.100s",
                name, text,
            )
            return (False, name)

    return (True, None)
