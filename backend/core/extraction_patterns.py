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
- ``CORRECTION_PATTERNS``  — Matches user correction indicators.
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


# ---------------------------------------------------------------------------
# Correction patterns — detect user corrections after skill/agent output
# ---------------------------------------------------------------------------
# Used by both session_miner.py (mining transcripts for eval examples) and
# skill_metrics_hook.py (detecting corrections in post-session messages).

# Broadened correction detection: requires correction signals at sentence start,
# after sentence-ending punctuation, or at word boundary for strong signals.
# Covers English corrections, redirects, frustration, and Chinese corrections.
# "actually, let me also add" should NOT match; "Actually, that's wrong" should.
CORRECTION_PATTERNS = re.compile(
    r"(?:"
    # --- Group A: sentence-start anchored (original style) ---
    r"(?:^|(?<=[.!?]\s))"
    r"(?:no[,. !]|don'?t |stop |wrong|incorrect|fix |undo|revert|"
    r"instead[, ]|actually[, ]|wait[,. !]|remove |"
    # Frustration / explicit rejection at sentence start
    r"that'?s not (?:what|right|correct)|this is wrong|this is not|"
    r"not what I (?:asked|wanted|meant)|you (?:got it|got that) wrong|"
    r"that doesn'?t work|that didn'?t work|try again|"
    # Imperative review/redo commands at sentence start
    r"review |redo |rework |rethink |reconsider |"
    r"address |clean.?up )"
    r"|"
    # --- Group B: structural redirects (can appear mid-sentence but strong signal) ---
    r"\b(?:"
    r"use \S+ instead of \S+|"                         # "use X instead of Y"
    r"put (?:it|this|that|them) in (?:a )?different|"  # "put it in a different folder"
    r"(?:the )?format should be \S+ not \S+|"          # "format should be JSON not YAML"
    r"(?:change|move|rename|switch) (?:it|this|that) (?:to|from)|"  # "change it to..."
    r"should (?:be|have been|go) \S+|"                 # "should be X"
    r"(?:not|never) \S+ (?:but|instead)|"              # "not X but Y"
    r"are you sure |"                                  # "are you sure we need this"
    r"(?:do|does|did)n'?t (?:work|look|seem)|"         # "doesn't work"
    r"(?:review|check) (?:again|your|the)|"            # "review again", "check your work"
    r"too (?:slow|fast|long|short|big|small|much|many|verbose|complex)" # comparative
    r")"
    r"|"
    # --- Group C: Chinese corrections (no word-boundary needed for CJK) ---
    r"(?:"
    r"不行|不对|搞错|错了|重新来|改一下|改过来|"
    r"你搞错了|这个不行|这不对|不是这样|"
    r"换一个|换成|改成|改为|重做|再来|重来|"
    r"别这样|不要这样|不应该|不能这样|"
    # Additional Chinese: mid-sentence negation directives
    r"不要|差太远|不合理|有问题|"
    r"太[多少长短大小复杂]|"                                    # "太多", "太复杂"
    r"你看看|确认.{0,4}无回归"                                  # "你看看应该怎么弄", "确认无回归"
    r")"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


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
