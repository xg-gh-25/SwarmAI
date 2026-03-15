"""Shared extraction patterns for the memory pipeline.

Centralizes regex patterns used by both the summarization pipeline
(stage 1: conversation → DailyActivity) and the distillation hook
(stage 2: DailyActivity → MEMORY.md / EVOLUTION.md).

Having two copies of these patterns caused divergence: summarization
had broader patterns (matching agent monologue like "Confirmed — state
is garbage") while distillation had tighter ones. Centralizing ensures
both stages filter consistently.

Key public symbols:

- ``DECISION_PATTERNS``    — Matches decision-indicator phrases.
- ``LESSON_PATTERNS``      — Matches lesson/learning phrases.
- ``AGENT_MONOLOGUE``      — Matches agent internal monologue (filter).
- ``NOISE_PATTERNS``       — Matches filler/noise in user messages.
- ``is_noise_entry``       — Detect noise leaked from agent monologue or tables.

Design principle: patterns used in summarization (broader, runs on raw
conversation) import and extend these. Patterns used in distillation
(tighter, runs on already-extracted DailyActivity) use them directly.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Decision patterns — detect lines recording a decision
# ---------------------------------------------------------------------------
# Two variants:
#   DECISION_PATTERNS_STRICT  — requires a verb phrase after the keyword
#                                (for distillation of already-extracted content)
#   DECISION_PATTERNS_BROAD   — allows looser matching including "confirmed that"
#                                (for extraction from raw conversation)

DECISION_PATTERNS_STRICT = re.compile(
    r"(?:decided to \w+|chose to \w+|will use \w+|going with \w+|switched to \w+|"
    r"adopted \w+|the approach is \w+|opted for \w+|"
    r"selected \w+ (?:as|for|over|instead))",
    re.IGNORECASE,
)

DECISION_PATTERNS_BROAD = re.compile(
    r"\b(?:decided to|chose (?:to|a|the)|will use|going with|recommend|"
    r"the approach is|selected (?:a|the|for)|switched to|adopted|"
    r"confirmed (?:that|working|live|the|this|we|our|it)|"
    r"approved|rejected|using .+ instead of|opted for)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Lesson patterns
# ---------------------------------------------------------------------------

LESSON_PATTERNS = re.compile(
    r"(?:lesson learned|learned that|mistake was|fixed by \w+|root cause (?:was|is)|"
    r"workaround[: ]|should have \w+|next time \w+|"
    r"bug was \w+|issue was \w+|problem was \w+|important to \w+ before)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Agent monologue — filter, not extract
# ---------------------------------------------------------------------------
# Covers both planning ("Let me") and status reporting ("Confirmed —",
# "Found 3 files") patterns that leak through LLM enrichment into
# Decisions sections.

AGENT_MONOLOGUE = re.compile(
    r"^(?:Let me|I'll |I need to |I should |I can |I will |"
    r"Checking |Looking at |Reading |Now |OK |Alright |"
    r"Let me also |This is |"
    r"Confirmed —|Verified —|"
    r"Item \d|Found |Good —|Wait —|Hmm)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Noise patterns — filler in user messages
# ---------------------------------------------------------------------------

NOISE_PATTERNS = re.compile(
    r"^(?:ok|yes|no|sure|thanks|thank you|got it|right|"
    r"hmm|ah|oh|please|hi|hello|hey)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Composite noise detector for distilled entries
# ---------------------------------------------------------------------------

# Compiled once — used by is_noise_entry()
_EMOJI_PREFIX = re.compile(r"^(?:\u2705|\u274c|\u26a0\ufe0f|\U0001f534|\U0001f7e1|\U0001f535) ")


def is_noise_entry(entry: str) -> bool:
    """Detect noise entries leaked from agent monologue or table fragments.

    Used by the distillation hook to filter entries before writing to
    MEMORY.md. Must be kept in sync with ``AGENT_MONOLOGUE`` above.
    """
    # Table fragments: starts with |
    if entry.startswith("|") or re.match(r"^\|.*\|.*\|", entry):
        return True
    # Agent internal monologue
    if AGENT_MONOLOGUE.match(entry):
        return True
    # Checkbox/status markers that aren't decisions
    if _EMOJI_PREFIX.match(entry):
        return True
    return False
