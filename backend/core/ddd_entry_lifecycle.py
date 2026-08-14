"""DDD Entry Lifecycle — per-entry knowledge tracking, decay, and archival.

Per-entry reference tracking for DDD knowledge documents (primarily IMPROVEMENT.md).
Each bullet entry can have:
- Type classification: [guideline], [pitfall], [decision], [model], [process]
- Reference count: how many times this entry influenced a decision
- Last referenced date: when this entry was last used
- Decay state: active → dormant → archived

Decay rules (see DORMANT_THRESHOLD_DAYS / ARCHIVED_THRESHOLD_DAYS below — the SoT):
- active → dormant: 60 days without reference (tunable per-doc, e.g. 45 for MEMORY.md)
- dormant → archived: 90 days TOTAL since last reference (NOT additional-after-dormant)
- New entries (< 30 days old): immune to decay (grace period)
- Evergreen sections: entries within are immune
- Evergreen TYPES (the 5 judgment types — decision/model/principle/correction/pitfall):
  immune regardless of age (Step 3, run_123652ae). Only guideline/process age-decay.
  (The ref>=10 "2x grace" and the 90/180 windows were removed 2026-06; ref_count has
   no live producer, so decay is age + evergreen-section + evergreen-type + grace — see assess_decay.)

Public API:
    EntryMetadata        — dataclass for per-entry state
    DecayTransition      — dataclass for state change records
    NoiseReport          — dataclass for per-doc noise measurement
    parse_entries(content) → list[EntryMetadata]
    inject_entry_metadata(content, entries) → str
    bump_references(entries, text, today) → int
    assess_decay(entries, today) → list[DecayTransition]
    compute_entry_noise(entries, today) → NoiseReport
    classify_entry_type(text) → str
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

VALID_TYPES = ("guideline", "pitfall", "decision", "model", "process",
               "principle", "correction")
DEFAULT_TYPE = "guideline"

# ── MEMORY.md Section Schema (Single Source of Truth) ─────────────────────────
#
# ALL consumers of MEMORY.md sections import from HERE. Never hardcode
# section names elsewhere. If structure changes, change HERE only.
#
# Three layers: Meta-cognitive (top attention) → Cognitive → Operational
MEMORY_SECTIONS: dict[str, dict] = {
    "Principles":              {"type": "principle",  "layer": "meta-cognitive", "prefix": "PRI", "evergreen": True},
    "Corrections":             {"type": "correction", "layer": "meta-cognitive", "prefix": "COR", "evergreen": True},
    "Decisions":               {"type": "decision",   "layer": "cognitive",      "prefix": "DEC", "evergreen": False},
    "Guidelines":              {"type": "guideline",  "layer": "operational",    "prefix": "GUI", "evergreen": False},
    "Pitfalls":                {"type": "pitfall",    "layer": "operational",    "prefix": "PIT", "evergreen": False},
    "Processes":               {"type": "process",    "layer": "operational",    "prefix": "PRC", "evergreen": False},
    "Models":                  {"type": "model",      "layer": "cognitive",      "prefix": "MOD", "evergreen": False},
    "COE Registry":             {"type": "pitfall",   "layer": "meta-cognitive", "prefix": "COE", "evergreen": True},
    "Open Threads":            {"type": "process",    "layer": "operational",    "prefix": "OT",  "evergreen": True},
    "Standing Preferences":    {"type": "guideline",  "layer": "meta-cognitive", "prefix": "SP",  "evergreen": True},
}

# Derived lookups (computed once, used by consumers)
MEMORY_SECTION_NAMES = tuple(MEMORY_SECTIONS.keys())
MEMORY_EVERGREEN_SECTIONS = frozenset(k for k, v in MEMORY_SECTIONS.items() if v["evergreen"])
MEMORY_PERMANENT_SECTIONS = frozenset(k for k, v in MEMORY_SECTIONS.items() if v["layer"] == "meta-cognitive")
MEMORY_ACTIVE_SECTIONS = frozenset(k for k, v in MEMORY_SECTIONS.items() if v["layer"] in ("cognitive", "operational"))
MEMORY_PREFIX_MAP = {k: v["prefix"] for k, v in MEMORY_SECTIONS.items()}
MEMORY_PREFIX_TO_SECTION = {v["prefix"]: k for k, v in MEMORY_SECTIONS.items()}

# ── KNOWLEDGE.md Evergreen Sections ───────────────────────────────────────────
#
# UNLIKE MEMORY (which accumulates disposable operational churn — GUI/PIT — that
# SHOULD decay), KNOWLEDGE.md is almost ENTIRELY load-bearing reference: the
# runtime Self-Identity Anchor, context-file assembly specs, hook/pipeline/schema
# architecture. Those entries are typed `guideline`/`pitfall` (not a keep-TYPE),
# so is_keep_class would NOT protect them — reclaim would archive+strip
# mission-critical facts after dormancy (Gate-1 CRITICAL, run_a1ec08e7: verified
# ~23 reference entries were strip-eligible). This frozenset protects EVERY real
# reference section by name; only genuinely disposable non-reference sections
# (e.g. scratch notes) and the auto-managed "Knowledge Index" fall through to
# reclaim. Section names are matched by their FULL header text (incl. any
# `[model]`/`[guideline]` tag suffix), as parse_entries records `section`.
# When a new reference section is added to KNOWLEDGE.md, add it here.
KNOWLEDGE_EVERGREEN_SECTIONS = frozenset({
    "Architecture Overview [model]",
    "The 11 Context Files [model]",
    "Claude Code CLI Hidden Defaults [constraint]",
    "Code Documentation Standards [guideline]",
    "Codebase Navigation [model]",
    "Frontend Architecture [model]",
    "Database Schema [model]",
    "Hook System [model]",
    "Pipeline — What It Is & How To Check State [model]",
    "Job System [model]",
})

# ── EVOLUTION.md Evergreen Sections (run_2816ab1c) ────────────────────────────
#
# EVOLUTION.md had ZERO lifecycle integration before this — it accreted forever
# (its Corrections Captured section is managed SEPARATELY by
# evolution_maintenance_hook.fold_corrections, which folds narrative DATA-POINT
# sub-bullets, NOT age-decays entries).
#
# DESIGN DECISION (user directive, run_2816ab1c): EVOLUTION is FULLY evergreen for
# AGE-DECAY — EVERY section is listed here, so no O-entry is ever archived merely
# for being old-and-unreferenced. The lifecycle wiring runs ONLY the exact-dup
# DEDUP sweep on EVOLUTION (never age-reclaim). Rationale (Principle 1 — value, not
# age, decides survival): the "Optimizations Learned" O-entries are DISTILLED
# operational wisdom (O030 disaster-recovery doctrine, O025 "know your runtime",
# O009 "mock≠reality"), closer to KNOWLEDGE's load-bearing reference than to
# MEMORY's disposable GUI/PIT churn. They carry no `created_date` metadata (only
# prose dates) and EVOLUTION has no usage→ref bridge, so age-decay would treat them
# as infinitely-old ref:0 noise and strip ALL of them — burying hard-won judgment
# because a counter never ticked (exactly the Principle-1 failure). Size control
# for EVOLUTION is fold_corrections (owns the huge Corrections section) + dedup, NOT
# age-decay. is_keep_class ALSO protects by TYPE inside any section (defence in
# depth); this frozenset is the section-level layer and now covers all 7 sections.
EVOLUTION_EVERGREEN_SECTIONS = frozenset({
    'Design Philosophy — What "Evolution" Means',
    "Capabilities Built",
    "Optimizations Learned",
    "Corrections Captured",
    "Governance Candidates",
    "Competence Learned",
    "Failed Evolutions",
})
# PRIMARY insertion target per type. When multiple sections share a type
# (e.g. "guideline" → Guidelines AND Standing Preferences), this gives the
# default destination for new entries. Explicitly defined, not computed.
MEMORY_TYPE_TO_SECTION: dict[str, str] = {
    "principle": "Principles",
    "correction": "Corrections",
    "decision": "Decisions",
    "guideline": "Guidelines",
    "pitfall": "Pitfalls",
    "process": "Processes",
    "model": "Models",
}

# Grace period: new entries are immune to decay
GRACE_PERIOD_DAYS = 30

# Decay thresholds (days since last reference). Tightened 90/180 -> 60/150
# (run_186a5f15) -> archived 150->90 (run_2816ab1c, user directive): let archive
# ACTUALLY trigger on a high-recall brain (pure-time 150d was rarely reached).
# ARCHIVED is TOTAL days-since-ref (60 idle -> dormant, then 30 more -> archived at
# 90 total), NOT additional-after-dormant — dormant→archived keeps a 30d buffer.
# Archived entries stay FTS5-recallable (knowledge_store indexes Archives) — this
# only stops always-injection sooner, it does not drop anything from recall.
DORMANT_THRESHOLD_DAYS = 60
ARCHIVED_THRESHOLD_DAYS = 90

# Types that are EVERGREEN BY TYPE for AGE-DECAY (Step 3, run_123652ae): a real
# judgment lesson must never be buried on a timer merely for not being recalled
# (Principle 1 — a brain that forgets its best judgment because a counter didn't
# tick is failing). assess_decay treats these as immune to active→dormant→archived
# age transitions, exactly like an evergreen SECTION. guideline/process (operational
# notes) still age-decay normally. Safe because the intake gate (Step 1: dedup +
# value floor) + the one-time cleanse (Step 2) guarantee LIVE judgment entries are
# real, not silt. Genuine staleness is handled by evidence-based retire
# (ddd_cultivation retire path), NEVER by silent age-death.
#
# The set is ALL five judgment types (cognitive + meta-cognitive): decision, model
# (cognitive) + principle, correction (meta-cognitive) + pitfall (hard-won failure
# lesson). Only OPERATIONAL types (guideline, process) age-decay. Rationale for
# including principle/model too (Gate-2 axis-2, run_123652ae): the Principle-1
# argument — "judgment must not be buried on a timer" — is STRONGEST for the
# meta-cognitive layer, so it would be incoherent to keep `correction` evergreen but
# let `principle` age out. This makes EVERGREEN_TYPES = _KEEP_TYPES ∪ {pitfall}.
#
# ⚠️ STILL DELIBERATELY DISTINCT from _KEEP_TYPES (below, ~line 946) — they govern
# DIFFERENT actions and differ on `pitfall`:
#   • EVERGREEN_TYPES → "never AGE-DECAY" (retention / hot-context recall-priority).
#     INCLUDES pitfall — the dominant hard-won failure-lesson type (294 across DDDs;
#     the MEMORY [PIT##] entries are real lessons, not churn — the old A2 "PIT is
#     fast-churn noise" premise was wrong; guideline is the fast-churn type).
#   • _KEEP_TYPES → "never RECLAIM-STRIP" (the harsher physical removal). EXCLUDES
#     pitfall (a legacy pre-stamped-dormant pitfall stays reclaim-eligible — that's
#     how Step-2-era archived noise is removable).
# Do NOT collapse them into one set — dropping pitfall from EVERGREEN_TYPES re-breaks
# retention; adding it to _KEEP_TYPES re-breaks reclaim of legacy archived noise.
EVERGREEN_TYPES = frozenset({"decision", "model", "principle", "correction", "pitfall"})

# High-ref entries (ref >= HIGH_REF_THRESHOLD) get extended grace (2x)
HIGH_REF_THRESHOLD = 10
HIGH_REF_MULTIPLIER = 2

# Type classification signal words (order matters — first match wins)
# NOTE: Signals must be distinctive. Common words like "→", "pipeline", "step"
# appear in ALL entry types and shouldn't trigger classification alone.
#
# Priority chain: pitfall → decision → correction → principle → guideline → process → model
# Rationale: pitfall/decision have strongest signals. correction before principle
# because its signals are more specific (less false-positive risk).
_TYPE_SIGNALS: dict[str, list[str]] = {
    "pitfall": ["bug", "broke", "break", "failed", "failure", "regression",
                "race condition", "silent", "crash", "hang", "corrupt",
                "anti-pattern", "wrong", "mistake"],
    "decision": ["chose", "chosen", "selected", "instead of",
                 "approach:", "vs ", "trade-off", "tradeoff",
                 "we decided", "architecture decision"],
    "correction": ["class a", "class b", "cognitive bias", "tendency to",
                   "i tend to", "self-correction", "认知偏差",
                   "0 self-corrections", "agent 的偏差",
                   "correction:", "我倾向于", "consistently fail",
                   "same mistake", "11 occurrences", "behavioral pattern",
                   "behavioral bias", "self-cognition failure"],
    "principle": ["philosophy", "principle:", "first principle",
                  "design principle", "系统思维", "fundamental",
                  "north star", "axiom", "core belief", "引用=",
                  "architectural principle", "design decision:",
                  "the reason is", "达尔文", "darwinian",
                  "natural selection", "进化论", "> multi-agent",
                  "sovereignty", "compound"],
    "process": ["workflow:", "state machine:", "lifecycle:",
                "sequence of steps", "procedure:", "protocol:"],
    "model": ["entity", "schema", "field", "relationship", "data structure",
              "data model", "table schema"],
    # guideline is the fallback — most entries are lessons/recommendations
    "guideline": ["pattern:", "rule:", "lesson:", "should", "prefer",
                  "always", "never", "must", "best practice",
                  "roi", "saves", "prevents", "eliminates", "tip:"],
}

# Regex for entry bullet with optional type prefix — the DEFAULT (narrow) matcher.
# Matches: "- [type] **Title** — description (date, run)"
# Or:      "- **Title** — description (date, run)"
# group(1)=type (optional), group(2)=title.
#
# ⚠️ This is the matcher every AUTONOMOUS lifecycle path uses (parse_entries default,
# inject_entry_metadata, decay, reclaim). It DELIBERATELY does NOT match emoji/marker-
# prefixed hand-curated prose bullets (e.g. "- 🟡 **Title**") — because those paths
# WRITE: inject stamps <!-- ref --> metadata, reclaim strips+archives. Making prose
# parseable here would let the autonomous jobs mutate/strip XG's curated Open-Threads
# prose (Gate-2 run_748f14a7: confirmed inject would stamp 8 OT bullets; ddd_orchestrator
# reclaim would strip aged emoji bullets in IMPROVEMENT.md — neither is evergreen-guarded).
# So prose-matching is OPT-IN, isolated to the retire path (see _ENTRY_RE_PROSE below).
_ENTRY_RE = re.compile(
    r"^- (?:\[(\w+)\] )?\*\*(.+?)\*\*"
)

# Prose-inclusive matcher — OPT-IN, used ONLY by the deliberate, by-name ddd-retire
# path (parse_entries(include_prose=True) + _strip_entries(..., include_prose=True)).
# Tolerates an OPTIONAL leading run of EMOJI/STATUS glyphs (🟡🟢🔵✅⚠️… VS16/ZWJ-aware)
# before the optional [type] + **title**, so a curator can retire a hand-curated
# Open-Threads bullet BY NAME. This is safe precisely because it is NOT on any
# autonomous write path — retire is an explicit, single-entry, human-invoked removal.
# The leading class is an EXPLICIT emoji/symbol range (NOT a broad negation) so it
# cannot swallow structural markdown ('>' blockquote, '|' table, ':'/'@'/'#' leads) —
# those must NOT become false entries (Gate-2 MED-1). group(1)=type, group(2)=title
# stay identical to _ENTRY_RE for every non-prose bullet.
_ENTRY_RE_PROSE = re.compile(
    # Leading glyph class = emoji (U+1F300–1FAFF) + a CONTIGUOUS U+2190–2BFF block
    # (←-⯿) that covers arrows, geometric shapes, media controls, dingbats, misc
    # symbols AND VS16/ZWJ — the glyphs real curated bullets actually lead with
    # (🟡🟢🔵✅⚠️🚀🧬 AND → ▶️ ◀️ ⏸, e.g. IMPROVEMENT.md's "- → **BLOCK…**").
    # It starts at U+2190, ABOVE the structural markers '>'(3E) '|'(7C) '@'(40)
    # ':'(3A) '#'(23) '~'(7E), so those can never become false entries (MED-1).
    r"^- (?:[\U0001F300-\U0001FAFF←-⯿️‍]+\s*)?"
    r"(?:\[(\w+)\] )?\*\*(.+?)\*\*"
)


def _match_entry_line(line: str, *, include_prose: bool = False):
    """Single source of truth for 'is this line a knowledge-entry bullet?'.

    include_prose=False (DEFAULT): narrow _ENTRY_RE — autonomous paths (parse/inject/
    decay/reclaim). Never matches emoji-prefixed curated prose (protects it from being
    stamped/stripped by background jobs).
    include_prose=True: _ENTRY_RE_PROSE — ONLY the by-name ddd-retire path opts in, so
    a curator can archive+strip a hand-curated Open-Threads bullet deliberately.
    Both return group(1)=type, group(2)=title.
    """
    return (_ENTRY_RE_PROSE if include_prose else _ENTRY_RE).match(line)

# Regex for inline metadata comment
# Matches: "  <!-- ref:N | last:YYYY-MM-DD | decay:state -->"
# Optional: "  <!-- ref:N | last:YYYY-MM-DD | decay:state | source:auto -->"
# Bi-temporal supersession (run_24299917): two more OPTIONAL trailing segments:
#   "... | valid_until:YYYY-MM-DD | superseded_by:<anchor>"
# All three trailing groups (source, valid_until, superseded_by) are independent
# and optional — the leading space of each lives INSIDE its own group so a comment
# carrying only a later field (e.g. superseded_by, no source) still matches, and a
# legacy comment with none of them is byte-identical to the pre-supersession form.
# superseded_by is a TITLE anchor and titles contain SPACES (e.g. "New unified
# cache strategy"), so its group is `([^|]+?)` — any run of non-pipe chars,
# non-greedy, terminated by the trailing ` -->$` anchor. It is the LAST field so
# it cannot collide with a following ` | ` separator; the captured value is
# .strip()'d at parse time to drop the space before ` -->`. (valid_until stays a
# strict date token; the `null` sentinel + missing-field both mean "not set".)
_META_RE = re.compile(
    r"^\s*<!-- ref:(\d+) \| last:([\w\-]+) \| decay:(\w+)"
    r"(?:\s*\|\s*source:(\w+))?"
    r"(?:\s*\|\s*valid_until:([\w\-]+))?"
    r"(?:\s*\|\s*superseded_by:([^|]+?))?"
    r"\s*-->$"
)

# Regex for date extraction from entry text "(YYYY-MM-DD, ...)"
_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})")


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class EntryMetadata:
    """Per-entry knowledge lifecycle state."""
    title: str
    entry_type: str = DEFAULT_TYPE
    ref_count: int = 0
    last_referenced: Optional[date] = None
    decay_state: str = "active"  # active | dormant | archived
    created_date: Optional[date] = None
    section: str = ""  # Which ## section this belongs to
    line_number: int = 0  # Line in the file (for injection)
    raw_text: str = ""  # Full bullet text (for archival)
    source: str = ""  # "auto" | "manual" | "" (legacy entries without tag)
    # Bi-temporal supersession (run_24299917). When superseded_by is set, this
    # entry has been REPLACED by a newer entry (anchor = the superseding entry's
    # title): it is FILTERED from default recall + SKIPPED by age-decay + NEVER
    # deleted (lineage preserved — Principle 1: sediment flows UP, not just OUT).
    # valid_until = the date supersession was marked (the entry was "true" over
    # [created_date, valid_until]). Both None for a live entry.
    valid_until: Optional[date] = None
    superseded_by: Optional[str] = None  # title-anchor of the superseding entry

    def __post_init__(self):
        if self.entry_type not in VALID_TYPES:
            self.entry_type = DEFAULT_TYPE
        if self.decay_state not in ("active", "dormant", "archived"):
            self.decay_state = "active"

    def to_comment(self) -> str:
        """Serialize to inline metadata HTML comment."""
        last_str = (
            self.last_referenced.isoformat()
            if self.last_referenced
            else "none"
        )
        base = f"  <!-- ref:{self.ref_count} | last:{last_str} | decay:{self.decay_state}"
        if self.source:
            base += f" | source:{self.source}"
        # Supersession segments emitted ONLY when set → a live entry's comment is
        # byte-identical to the pre-supersession format (backward-compat guarantee).
        if self.valid_until:
            base += f" | valid_until:{self.valid_until.isoformat()}"
        if self.superseded_by:
            base += f" | superseded_by:{self.superseded_by}"
        base += " -->"
        return base


@dataclass
class DecayTransition:
    """Record of a decay state change."""
    entry: EntryMetadata
    old_state: str
    new_state: str
    reason: str


@dataclass
class ReclaimReport:
    """Result of a reclaim pass (CLEAN). dry_run leaves new_content None."""
    candidates: list[str] = field(default_factory=list)   # titles selected for reclaim
    archived: int = 0                                      # count actually moved (0 if dry_run)
    kept_protected: int = 0                                # noisy-but-keep-class count
    new_content: Optional[str] = None                      # source with bullets stripped (None if dry_run)


@dataclass
class NoiseReport:
    """Per-document entry-noise measurement (read-only diagnostic).

    Noise = entries that are demonstrably NOT earning their tokens:
    zero references, past the new-entry grace period, AND already
    decayed (dormant or archived). This is the HONEST signal — it does
    not use the section-level `used`/`verified` field, which
    context_health_hook auto-flips to true on any pipeline completion.
    """
    total: int = 0
    noisy: int = 0
    noise_rate: float = 0.0           # noisy / total (0.0 when total == 0)
    noisy_titles: list[str] = field(default_factory=list)
    by_section: dict[str, int] = field(default_factory=dict)  # section → noisy count


# ── Public API ────────────────────────────────────────────────────────────────


def classify_entry_type(text: str) -> str:
    """Classify a knowledge entry's type from its text content.

    Uses signal word matching with priority ordering:
    1. pitfall (strongest — bug/failure language)
    2. decision (chose/selected language)
    3. correction (behavioral patterns, self-awareness, cognitive biases)
    4. principle (design philosophy, first principles, system thinking)
    5. guideline (pattern/rule/lesson — most common)
    6. process (workflow/steps)
    7. model (structure/schema)
    Default: guideline (safe for ambiguous cases)

    Three layers of knowledge:
    - Operational (guideline, pitfall, process): how to DO things
    - Cognitive (decision, model): how to UNDERSTAND things
    - Meta-cognitive (principle, correction): how to THINK and EVOLVE
    """
    text_lower = text.lower()

    # Priority 1: pitfall signals are strong and unambiguous
    for signal in _TYPE_SIGNALS["pitfall"]:
        if signal in text_lower:
            return "pitfall"

    # Priority 2: decision signals
    for signal in _TYPE_SIGNALS["decision"]:
        if signal in text_lower:
            return "decision"

    # Priority 3: correction (specific self-awareness signals)
    for signal in _TYPE_SIGNALS["correction"]:
        if signal in text_lower:
            return "correction"

    # Priority 4: principle (philosophy/first-principles)
    for signal in _TYPE_SIGNALS["principle"]:
        if signal in text_lower:
            return "principle"

    # Priority 5: guideline (most common — lessons, patterns, rules)
    for signal in _TYPE_SIGNALS["guideline"]:
        if signal in text_lower:
            return "guideline"

    # Priority 6-7: process and model (rare, very specific signals)
    for signal in _TYPE_SIGNALS["process"]:
        if signal in text_lower:
            return "process"
    for signal in _TYPE_SIGNALS["model"]:
        if signal in text_lower:
            return "model"

    return DEFAULT_TYPE


def parse_entries(content: str, *, include_prose: bool = False) -> list[EntryMetadata]:
    """Parse all knowledge entries from DDD markdown content.

    Extracts entries from bullet lists (- **Title** ...) with optional
    [type] prefix and optional inline metadata comment.

    include_prose=False (DEFAULT): narrow matcher — the shape every autonomous
    lifecycle path (inject/decay/reclaim) must use, so emoji-prefixed curated
    prose bullets are NOT treated as decay-managed entries (never stamped/stripped
    by background jobs). include_prose=True: the by-name ddd-retire path opts in so
    a curator can archive+strip a hand-curated Open-Threads bullet deliberately.

    Returns list of EntryMetadata in document order.
    """
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    entries: list[EntryMetadata] = []
    current_section = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track section headers
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            i += 1
            continue

        # Check for entry bullet
        m = _match_entry_line(line, include_prose=include_prose)
        if m:
            entry_type = m.group(1) or ""
            title = m.group(2)

            # Extract created_date from entry text
            created_date = _extract_date(line)

            # Collect full entry text (may span multiple lines until next - or <!-- or ##)
            raw_lines = [line]
            j = i + 1
            while j < len(lines):
                next_line = lines[j]
                if next_line.startswith("- ") or next_line.startswith("## "):
                    break
                if _META_RE.match(next_line):
                    break
                if next_line.strip() == "":
                    # Blank line might separate entries
                    # Look ahead: if next non-blank is a new entry or section, stop
                    k = j + 1
                    while k < len(lines) and lines[k].strip() == "":
                        k += 1
                    if k >= len(lines) or lines[k].startswith("- ") or lines[k].startswith("## "):
                        break
                raw_lines.append(next_line)
                j += 1

            raw_text = "\n".join(raw_lines)

            # Check for metadata comment on next line
            ref_count = 0
            last_referenced = None
            decay_state = "active"
            valid_until = None
            superseded_by = None

            meta_line_idx = j
            source = ""
            if meta_line_idx < len(lines):
                meta_match = _META_RE.match(lines[meta_line_idx])
                if meta_match:
                    ref_count = int(meta_match.group(1))
                    last_str = meta_match.group(2)
                    if last_str != "none":
                        try:
                            last_referenced = date.fromisoformat(last_str)
                        except ValueError:
                            last_referenced = None
                    decay_state = meta_match.group(3)
                    source = meta_match.group(4) or ""
                    vu_str = meta_match.group(5)
                    if vu_str:
                        try:
                            valid_until = date.fromisoformat(vu_str)
                        except ValueError:
                            valid_until = None
                    sup_raw = meta_match.group(6)
                    superseded_by = sup_raw.strip() if sup_raw else None
                    # `null` sentinel (memory_index idiom) means not-superseded
                    if superseded_by == "null":
                        superseded_by = None
                    j = meta_line_idx + 1  # Skip the metadata line

            # Classify type if not explicitly tagged
            if not entry_type:
                entry_type = classify_entry_type(raw_text)

            entries.append(EntryMetadata(
                title=title,
                entry_type=entry_type,
                ref_count=ref_count,
                last_referenced=last_referenced,
                decay_state=decay_state,
                created_date=created_date,
                section=current_section,
                line_number=i,
                raw_text=raw_text,
                source=source,
                valid_until=valid_until,
                superseded_by=superseded_by,
            ))
            i = j
        else:
            i += 1

    return entries


def inject_entry_metadata(content: str, entries: list[EntryMetadata]) -> str:
    """Write/update inline metadata comments for entries in content.

    For each entry in `entries`, finds the matching bullet by title and
    adds or replaces the metadata comment line immediately after the entry.

    Returns updated content string.
    """
    if not content or not entries:
        return content

    lines = content.splitlines()
    result_lines: list[str] = []
    # Key by (title, section) to handle duplicate titles across sections
    entry_map: dict[tuple[str, str], EntryMetadata] = {}
    for e in entries:
        entry_map[(e.title, e.section)] = e
    # Also build title-only fallback for entries without section context
    title_map: dict[str, EntryMetadata] = {}
    for e in entries:
        if e.title not in title_map:
            title_map[e.title] = e
    skip_next_meta = False
    current_section = ""
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track section headers for section-aware lookup
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            result_lines.append(line)
            i += 1
            continue

        if skip_next_meta:
            # Check if this line is an old metadata comment to skip
            if _META_RE.match(line):
                i += 1
                skip_next_meta = False
                continue
            skip_next_meta = False

        # Check if this is an entry bullet
        m = _ENTRY_RE.match(line)
        if m:
            title = m.group(2)
            result_lines.append(line)

            # Lookup: prefer (title, section), fall back to title-only
            entry = entry_map.get((title, current_section)) or title_map.get(title)
            if entry:
                # Collect continuation lines (indented, non-metadata, non-entry)
                i += 1
                while i < len(lines):
                    next_line = lines[i]
                    if next_line.startswith("- ") or next_line.startswith("## "):
                        break
                    if _META_RE.match(next_line):
                        # Skip old metadata — we'll inject new one
                        i += 1
                        break
                    if next_line.strip() == "":
                        # Keep blank line but check if metadata follows
                        peek = i + 1
                        if peek < len(lines) and _META_RE.match(lines[peek]):
                            result_lines.append(next_line)
                            i = peek + 1  # Skip old metadata
                            break
                        # Check if next content is a new entry or section
                        peek2 = i + 1
                        while peek2 < len(lines) and lines[peek2].strip() == "":
                            peek2 += 1
                        if peek2 >= len(lines) or lines[peek2].startswith("- ") or lines[peek2].startswith("## "):
                            break
                    result_lines.append(next_line)
                    i += 1

                # Inject new metadata comment
                result_lines.append(entry.to_comment())
                continue
            else:
                i += 1
                continue
        else:
            result_lines.append(line)
            i += 1

    # Preserve trailing newline
    trailing = content.endswith("\n")
    result = "\n".join(result_lines)
    if trailing and not result.endswith("\n"):
        result += "\n"
    return result


def mark_superseded(
    content: str,
    old_anchor: str,
    new_anchor: str,
    today: date,
) -> str:
    """Mark the DDD entry titled ``old_anchor`` as superseded by ``new_anchor``.

    Bi-temporal supersession (方案A, run_24299917) — the DDD-native counterpart of
    ``memory_index.mark_entry_superseded`` (which is KEY-anchored + down-weights;
    this is TITLE-anchored + filters). Sets ``valid_until=today`` and
    ``superseded_by=new_anchor`` on the matching entry, then re-serializes via
    ``inject_entry_metadata``. The bullet is NEVER stripped or deleted — its
    lineage is preserved (Principle 1); it is merely filtered from default recall
    (``recall_multi._ddd_entry_hits``) and skipped by age-decay (``assess_decay``).

    Human/cultivation-marked ONLY — supersession is NOT auto-detected (方案B is
    out of scope). ``new_anchor`` is a human-readable title-slug pointer to the
    superseding entry, not a strict FK (appropriate for a human-marked signal).

    Idempotent: re-marking an already-superseded entry UPDATES the pointer
    (no duplicate entry, no stacked comment). No-op (returns ``content``
    unchanged) if no entry's title matches ``old_anchor``.
    """
    # Guard the anchor grammar (meta-review MED): `|` or `-->` in the anchor would
    # void the ENTIRE metadata comment on re-parse (the field is pipe/terminator
    # delimited), silently discarding ref/last/decay. Reject rather than corrupt —
    # matters once 方案B wires an automated caller.
    if "|" in new_anchor or "-->" in new_anchor:
        raise ValueError(
            f"superseded_by anchor may not contain '|' or '-->': {new_anchor!r}"
        )
    entries = parse_entries(content)
    matched = [e for e in entries if e.title == old_anchor]
    if not matched:
        return content  # no-op — nothing matches the anchor
    for e in matched:
        e.valid_until = today
        e.superseded_by = new_anchor
    # inject_entry_metadata rewrites ONLY the matched entries' comment lines
    # (keyed by (title, section)); untouched entries pass through verbatim.
    return inject_entry_metadata(content, matched)


def bump_references(
    entries: list[EntryMetadata],
    text: str,
    today: date,
    context_files: list[str] | None = None,
    graph_path: "Path | None" = None,
) -> int:
    """Bump reference count for entries whose titles appear in text.

    Uses case-insensitive title match. Titles < 8 chars are skipped entirely
    (e.g., "Build", "API"). Titles 8-20 chars use word-boundary matching to
    reduce false positives. Titles > 20 chars use substring containment.
    Mutates entries in-place.

    Auto-extraction (G1 compound loop):
        When context_files and graph_path are provided, for each bumped entry
        whose raw_text mentions a context file, auto-creates an `applies_to`
        relation in the knowledge graph. This grows the graph organically from
        normal pipeline usage.
    """
    text_lower = text.lower()
    bumped = 0
    bumped_entries: list[EntryMetadata] = []

    for entry in entries:
        title_lower = entry.title.lower()
        title_len = len(title_lower)

        # Skip very short titles (too many false positives)
        if title_len < 8:
            continue

        matched = False
        if title_len <= 20:
            # Medium titles: use word-boundary regex to avoid partial matches
            try:
                pattern = r'\b' + re.escape(title_lower) + r'\b'
                matched = bool(re.search(pattern, text_lower))
            except re.error:
                matched = title_lower in text_lower
        else:
            # Long titles (>20 chars): substring is safe enough
            matched = title_lower in text_lower

        if matched:
            entry.ref_count += 1
            entry.last_referenced = today
            if entry.decay_state == "dormant":
                entry.decay_state = "active"  # Revive
            bumped += 1
            bumped_entries.append(entry)

    # G1: Auto-extraction — grow knowledge graph from pipeline usage
    if context_files and graph_path and bumped_entries:
        try:
            from core.knowledge_graph import add_relation
            for entry in bumped_entries:
                entry_text_lower = (entry.raw_text or entry.title).lower()
                for cf in context_files:
                    if len(cf) >= 4 and cf.lower() in entry_text_lower:
                        add_relation(
                            graph_path,
                            entry.title[:60],
                            "applies_to",
                            cf,
                        )
        except Exception:
            pass  # Auto-extraction is best-effort, never blocks bump

    return bumped


def assess_decay(
    entries: list[EntryMetadata],
    today: date,
    evergreen_sections: set[str] | None = None,
    dormant_days: int | None = None,
) -> list[DecayTransition]:
    """Assess decay state for all entries. Returns transitions to apply.

    Decay rules:
    - Evergreen sections: entries within are immune (never decay)
    - Evergreen TYPES (EVERGREEN_TYPES = the 5 judgment types decision/model/
      principle/correction/pitfall): immune regardless of section/age (Step 3,
      run_123652ae — judgment must not be buried on a timer). Only operational
      guideline/process age-decay.
    - Grace period: entries < 30 days old are immune
    - active → dormant: `dormant_days` days since last_referenced
      (defaults to the global DORMANT_THRESHOLD_DAYS=60 when None)
    - dormant → archived: ARCHIVED_THRESHOLD_DAYS (90 total) — NOT parameterized
    - Entries already archived are skipped
    - Entries with no date info are treated as infinitely old (decay immediately)

    Args:
        dormant_days: per-call active→dormant threshold (A2, run_55cb38d6).
            None → use the global DORMANT_THRESHOLD_DAYS (backward-compatible —
            all existing callers pass nothing and keep 60d). The MEMORY.md decay
            path passes 45 so volatile operational memory ages faster than the
            hard-won failure-lessons in IMPROVEMENT.md (which stay at 60d). Only
            the dormant threshold is tunable; dormant→archived stays at the
            global 90d so a faster-dormant entry still gets an archive buffer.
    """
    # dormant_days<1 would make `days_since_ref >= threshold` always true →
    # mark everything past grace dormant. No live caller passes <1 (only 45),
    # but guard the footgun rather than trust future callers (Gate-2 LOW).
    if dormant_days is not None and dormant_days < 1:
        raise ValueError(f"dormant_days must be >= 1, got {dormant_days}")

    transitions: list[DecayTransition] = []
    _evergreen = evergreen_sections or set()

    for entry in entries:
        if entry.decay_state == "archived":
            continue

        # Supersession immunity (run_24299917): a superseded entry keeps its
        # lineage — it is NEVER age-decayed (nor deleted; see _is_reclaimable_noise).
        # Its disposition is filter-from-recall, not timer-death (Principle 1).
        if entry.superseded_by:
            continue

        # Evergreen section immunity
        if entry.section in _evergreen:
            continue

        # Evergreen-by-TYPE immunity (Step 3, run_123652ae): judgment types
        # (decision/pitfall/correction) never age-decay — real judgment must not be
        # buried on a timer for lack of recall (Principle 1). Mirrors section
        # immunity above; distinct from is_keep_class/_KEEP_TYPES (see EVERGREEN_TYPES
        # definition — that guards reclaim-strip, this guards age-decay, they differ
        # on pitfall by design). guideline/process fall through to normal age decay.
        if entry.entry_type in EVERGREEN_TYPES:
            continue

        # Grace period for new entries
        if entry.created_date:
            age_days = (today - entry.created_date).days
            if age_days < GRACE_PERIOD_DAYS:
                continue

        # Determine effective thresholds.
        # HIGH_REF 2x-grace REMOVED (R2-prime, run_e50621b6): ref_count has NO
        # live producer reaching body entries. Gate-2 verified: the only honest
        # producer (memory_decay.bump_entry_references) writes a 5-field
        # `sessions:N` comment that this engine's _META_RE cannot parse, into the
        # index block parse_entries ignores. So ref_count is a DEAD input here —
        # honoring it only preserves the toxic prose residue (DISCUSSION ref:1010)
        # as undeserved 2x decay grace. Decay now on age + evergreen + grace only,
        # all honestly observable. (If a real body-ref producer is wired later,
        # re-introduce a multiplier THEN — not on a dead signal now.)
        dormant_threshold = (
            dormant_days if dormant_days is not None else DORMANT_THRESHOLD_DAYS
        )
        archived_threshold = ARCHIVED_THRESHOLD_DAYS

        # Calculate days since last reference
        if entry.last_referenced:
            days_since_ref = (today - entry.last_referenced).days
        elif entry.created_date:
            days_since_ref = (today - entry.created_date).days
        else:
            # No date info — treat as infinitely old (triggers decay)
            days_since_ref = archived_threshold + 1

        # Check transitions
        if entry.decay_state == "active":
            if days_since_ref >= dormant_threshold:
                transitions.append(DecayTransition(
                    entry=entry,
                    old_state="active",
                    new_state="dormant",
                    reason=f"{days_since_ref}d since last reference (threshold: {dormant_threshold}d)",
                ))
        elif entry.decay_state == "dormant":
            if days_since_ref >= archived_threshold:
                transitions.append(DecayTransition(
                    entry=entry,
                    old_state="dormant",
                    new_state="archived",
                    reason=f"{days_since_ref}d since last reference (threshold: {archived_threshold}d)",
                ))

    return transitions


def _resolve_archive_path(
    project_dir: Path, archive_name: str, source_path: "Path | None",
) -> Path:
    """Resolve where an archive file must live: NEXT TO its source doc.

    The archive is cold storage for entries stripped from a specific doc, so it
    must be a sibling of that doc — else reads (the live doc under 2-understanding/)
    and writes (the archive) diverge into a split-brain, which is exactly the bug
    that grew a 17MB orphan at the pre-migration project root (run_f71e5920).

    - source_path given (the normal path — every live caller has it): archive is
      source_path.parent / archive_name. Correct for BOTH a migrated six-section
      doc (2-understanding/IMPROVEMENT.md → 2-understanding/) AND MEMORY.md
      (.context/MEMORY.md → .context/, unchanged).
    - source_path absent (fallback): derive the doc from archive_name
      ("TECH-archive.md" → "TECH.md") and resolve its WRITE dir via ddd_write_path.
      Must NOT hardcode IMPROVEMENT.md — TECH/PRODUCT/PROJECT archives exist.
    """
    if source_path is not None:
        return Path(source_path).parent / archive_name
    # Fallback: derive the owning doc name from the archive name, then resolve
    # its canonical (new-layout) directory. ddd_write_path knows the six-section
    # map; a non-canonical stem (e.g. MEMORY) passes through to project_dir root.
    from core.ddd_paths import ddd_write_path
    # Derive the owning doc stem from the archive name. Handle BOTH the fixed form
    # ("TECH-archive.md" → "TECH.md") AND the monthly-shard form
    # ("EVOLUTION-archive-2026-08.md" → "EVOLUTION.md") — a plain replace() would
    # leave the shard suffix and mis-resolve the doc dir. Strip from "-archive"
    # onward, then re-add ".md".
    stem = re.sub(r"-archive.*$", "", archive_name)
    doc_name = f"{stem}.md"
    doc_dir = ddd_write_path(project_dir, doc_name).parent
    return doc_dir / archive_name


def archive_entries(
    project_dir: Path, entries: list[EntryMetadata],
    archive_name: str = "IMPROVEMENT-archive.md",
    source_path: "Path | None" = None,
) -> int:
    """Move entries to an archive file and return count archived.

    Creates archive file if it doesn't exist. Appends entries with their
    full raw_text + metadata comment. Marks entries as 'archived' in-place.

    Args:
        project_dir: Path to the project directory (e.g., Projects/SwarmAI/)
        entries: Entries to archive (should be dormant or otherwise marked)
        archive_name: Archive filename (default IMPROVEMENT-archive.md;
            MEMORY.md lifecycle passes "MEMORY-archive.md").
        source_path: The resolved path of the doc these entries came from. When
            given, the archive lands as its SIBLING (source_path.parent) — the
            correct co-location. When omitted, the doc dir is derived from
            archive_name via ddd_write_path (see _resolve_archive_path). Callers
            SHOULD pass it (they hold the resolved doc path) so the hot decay path
            never relies on the fallback.

    Returns:
        Number of entries successfully archived.
    """
    if not entries:
        return 0

    archive_path = _resolve_archive_path(project_dir, archive_name, source_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    # Build archive content to append
    archive_lines: list[str] = []
    for entry in entries:
        # Use raw_text if available, otherwise reconstruct
        if entry.raw_text:
            archive_lines.append(entry.raw_text)
        else:
            archive_lines.append(
                f"- [{entry.entry_type}] **{entry.title}** — (archived)"
            )
        # Add metadata with archived state
        entry.decay_state = "archived"
        archive_lines.append(entry.to_comment())
        archive_lines.append("")  # Blank separator

    new_content = "\n".join(archive_lines)

    # Write to archive file (create or append)
    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        archive_path.write_text(
            existing + "\n" + new_content, encoding="utf-8"
        )
    else:
        header = "# Archived Knowledge Entries\n\n"
        header += "_Entries archived by the Knowledge Lifecycle decay engine. "
        header += "These entries had no references for 90+ days._\n\n"
        archive_path.write_text(
            header + new_content, encoding="utf-8"
        )

    return len(entries)


def archive_raw_lines(
    project_dir: Path, lines: list[str], archive_name: str,
    source_path: "Path | None" = None,
    block_header: "str | None" = None,
    create_header: "str | None" = None,
    dedup_by_signature: bool = False,
) -> int:
    """Append pre-formatted raw markdown lines to an archive file (the raw-line
    sibling of archive_entries).

    THE convergence point for MEMORY-archive writers that operate on raw stripped
    markdown lines rather than EntryMetadata (distillation RC-archival + section-cap,
    context_health open-threads). Those writers used to each hardcode
    ``ws_path/"Knowledge"/"Archives"`` + their own create/append ``write_text`` — a
    split, git-tracked (public-repo) destination. This routes them ALL through the
    single ``_resolve_archive_path`` resolver, so passing ``source_path`` =
    ``.context/MEMORY.md`` lands the archive as its sibling in the gitignored,
    private ``.context/`` (the privacy partition), never in a tracked dir.

    Unlike archive_entries, this does NOT reconstruct entries from EntryMetadata —
    it takes the caller's already-stripped lines verbatim (no lossy
    line→EntryMetadata→line round-trip that would re-run the classifier the wider
    refactor is fixing).

    Args:
        project_dir: Project dir (used ONLY by the _resolve_archive_path fallback
            when source_path is None; ignored when source_path is given).
        lines: Pre-formatted markdown lines to archive (verbatim).
        archive_name: Archive filename (e.g. "MEMORY-archive-2026-08.md").
        source_path: The resolved path of the doc these lines came from. When given,
            the archive lands as its SIBLING — pass the .context/MEMORY.md path so
            the archive stays private.
        block_header: Optional header line prepended before this block (e.g.
            "### Archived Recent Context (2026-08-14)"). None → no block header.
        create_header: Optional file header used ONLY when creating the archive.
            MUST be supplied by the caller for MEMORY archives — do NOT fall back to
            archive_entries' "no references for 90+ days" decay blurb, which is
            factually wrong for RC/OT/section-cap content. None → a neutral
            "# Memory Archive" header.
        dedup_by_signature: When True, each incoming line whose format-agnostic
            ``content_signature`` already appears in the existing shard is SKIPPED
            (not appended). This is the shared append-layer guard that makes
            fold + size-valve double-move STRUCTURALLY impossible — a lesson already
            archived (in ANY bullet format) is never written twice, regardless of
            which writer sends it. Default False preserves the legacy append-always
            behavior for existing callers (MEMORY reclaim). A block that reduces to
            an empty signature (no real content) is never treated as a dup.

    Returns:
        Number of lines archived (0 = no-op, nothing written; dup-skipped lines
        do not count).
    """
    if not lines:
        return 0

    archive_path = _resolve_archive_path(project_dir, archive_name, source_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    if dedup_by_signature:
        # Skip any incoming ITEM whose content_signature already appears in the shard.
        # An incoming `lines` element may be a MULTI-LINE block (a folded/evicted
        # entry), and content_signature collapses ALL internal whitespace to single
        # spaces (ddd_cultivation) — so a block reduces to ONE signature. We must
        # therefore match that block-signature against a WHOLE-SHARD normalized
        # haystack (a substring test), NOT against per-line signatures (which could
        # never reconstruct a multi-line block's sig — the Gate-2 finding B bug).
        # content_signature is the SSOT for the needle; the haystack applies the SAME
        # final normalization (drop **, lowercase, collapse whitespace) so the needle,
        # whose edge markers/attribution are additionally stripped, is a substring.
        # Function-local import (verified: no cycle — cultivation imports lifecycle).
        from core.ddd_cultivation import content_signature  # noqa: PLC0415
        import re as _re  # noqa: PLC0415

        def _normalize_haystack(text: str) -> str:
            return _re.sub(r"\s+", " ", text.replace("**", " ")).strip().lower()

        haystack = ""
        if archive_path.exists():
            haystack = _normalize_haystack(archive_path.read_text(encoding="utf-8"))
        kept: list[str] = []
        for item in lines:
            sig = content_signature(item)
            if sig and sig in haystack:
                continue  # already archived (any format) — do not double-move
            if sig:
                haystack += " " + sig  # guard intra-batch dups too
            kept.append(item)
        lines = kept
        if not lines:
            return 0

    block_parts: list[str] = []
    if block_header:
        block_parts.append(block_header)
    block_parts.extend(lines)
    block = "\n".join(block_parts)

    if archive_path.exists():
        existing = archive_path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            existing += "\n"
        archive_path.write_text(existing + "\n" + block + "\n", encoding="utf-8")
    else:
        header = (create_header or "# Memory Archive").rstrip("\n") + "\n\n"
        archive_path.write_text(header + block + "\n", encoding="utf-8")

    return len(lines)


# ── Entry Noise Metric (read-only diagnostic) ─────────────────────────────────


# An entry is "noisy" only after it has had a fair chance to prove its worth.
# noisy ⇔ ref_count == 0  AND  age > grace  AND  decay ∈ {dormant, archived}.
# The decay-state requirement means evergreen/permanent sections (which
# assess_decay never transitions out of "active") are excluded automatically —
# no special-casing needed here.
_NOISY_DECAY_STATES = frozenset({"dormant", "archived"})

# Eval threshold (design §3 ①): a document FAILS the noise gate above this.
NOISE_FAIL_THRESHOLD = 0.30


def compute_entry_noise(
    entries: list[EntryMetadata],
    today: date,
    grace_days: int = GRACE_PERIOD_DAYS,
) -> NoiseReport:
    """Measure per-entry knowledge noise for a parsed DDD document.

    READ-ONLY: never mutates `entries` (unlike bump_references/assess_decay).

    An entry counts as noise when ALL hold:
      1. ref_count == 0            — never influenced a decision
      2. age >= grace_days         — past the grace window (same boundary as
                                     assess_decay: age < grace ⇒ immune)
      3. decay_state ∈ {dormant, archived} — the decay engine already
         judged it stale (this also excludes evergreen sections, which
         assess_decay leaves "active" forever)

    Age is measured from created_date. Entries with no created_date are
    treated as infinitely old (past grace) — consistent with assess_decay,
    which treats date-less entries as decay-eligible.

    Args:
        entries: Parsed entries (from parse_entries). Not modified.
        today: Reference date for age computation.
        grace_days: New-entry immunity window (default GRACE_PERIOD_DAYS).

    Returns:
        NoiseReport. noise_rate is 0.0 when there are no entries.
    """
    total = len(entries)
    if total == 0:
        return NoiseReport()

    noisy_titles: list[str] = []
    by_section: dict[str, int] = {}

    for entry in entries:
        if entry.ref_count != 0:
            continue
        if entry.decay_state not in _NOISY_DECAY_STATES:
            continue
        # Age check: date-less entries are treated as past grace.
        # Boundary MUST match assess_decay's immunity test (age < grace),
        # so an entry the decay engine has just marked dormant at age==grace
        # is not silently excluded here for a day.
        if entry.created_date is not None:
            age_days = (today - entry.created_date).days
            if age_days < grace_days:
                continue
        noisy_titles.append(entry.title)
        section_key = entry.section or "(no section)"
        by_section[section_key] = by_section.get(section_key, 0) + 1

    noisy = len(noisy_titles)
    return NoiseReport(
        total=total,
        noisy=noisy,
        noise_rate=noisy / total,
        noisy_titles=noisy_titles,
        by_section=by_section,
    )


def compute_reclaimable_noise(
    entries: list[EntryMetadata],
    today: date,
    grace_days: int = GRACE_PERIOD_DAYS,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
) -> NoiseReport:
    """The GATE metric: raw noise MINUS keep-class (reclaimable noise only).

    compute_entry_noise is the HONEST raw gauge — it counts even
    permanent-but-dormant knowledge (a dormant COE, an unreferenced
    principle) as noise, which is correct for measurement but wrong for a
    pass/fail gate: a doc that is healthy but principle-heavy would FAIL.

    This gate metric excludes is_keep_class entries from the numerator, so it
    measures only noise that reclaim_noise_entries could actually remove.
    noise_rate denominator stays `total` (the whole doc) so the rate is
    comparable across docs.

    READ-ONLY: never mutates `entries`.
    """
    total = len(entries)
    if total == 0:
        return NoiseReport()

    noisy_titles: list[str] = []
    by_section: dict[str, int] = {}
    for entry in entries:
        # Gate == action: use the SAME reclaimable predicate reclaim uses
        # (date REQUIRED), so a date-less-dominant doc isn't stuck FAILing
        # forever on noise no reclaim run can clean (adversarial H3).
        if not _is_reclaimable_noise(entry, today, grace_days):
            continue
        if is_keep_class(entry, evergreen_sections=evergreen_sections):
            continue
        noisy_titles.append(entry.title)
        section_key = entry.section or "(no section)"
        by_section[section_key] = by_section.get(section_key, 0) + 1

    noisy = len(noisy_titles)
    return NoiseReport(
        total=total,
        noisy=noisy,
        noise_rate=noisy / total,
        noisy_titles=noisy_titles,
        by_section=by_section,
    )


# ── Reclaim (CLEAN) — physically remove stale noise, protect permanent knowledge ─


def _is_reclaimable_noise(
    entry: EntryMetadata, today: date, grace_days: int
) -> bool:
    """Single source of truth for "reclaimable noise" (gate AND action).

    Stricter than compute_entry_noise's raw-noise predicate: destructive
    reclaim requires a REAL created_date (date-less = unknown-age, NOT
    proven-stale — see run_94fd5597). Both compute_reclaimable_noise (the gate)
    and reclaim_noise_entries (the action) call this, so they can never drift.
    Does NOT apply keep-class — callers layer that on top.
    """
    # Supersession is never reclaimable (run_24299917, Gate-1 C4): a superseded
    # entry is FILTERED from recall + SKIPPED by age-decay, and it must ALSO be
    # immune to the physical strip path — otherwise a superseded operational
    # entry (guideline/pitfall/process, ref 0, dormant, past grace) would be
    # deleted from disk, destroying the very lineage supersession preserves.
    if entry.superseded_by:
        return False
    if entry.ref_count != 0:
        return False
    if entry.decay_state not in _NOISY_DECAY_STATES:
        return False
    if entry.created_date is None:
        return False
    if (today - entry.created_date).days < grace_days:
        return False
    return True


# Types that are NEVER reclaimed regardless of ref/age. Operational noise
# (guideline/pitfall/process) is reclaimable; cognitive (decision/model) and
# meta-cognitive (principle/correction) knowledge is permanent.
_KEEP_TYPES = frozenset({"principle", "correction", "decision", "model"})

# ref_count at or above this floor is treated as load-bearing → keep.
_KEEP_REF_FLOOR = 2


def route_lesson_type(lesson: str) -> "tuple[str | None, str]":
    """Shared type-routing decision for auto-written MEMORY lessons.

    ONE source of truth for "which MEMORY section does this lesson belong to,
    and may it be auto-written at all?" — used by BOTH the distillation hook
    (cross-day recurring themes) and context_health (single-run REFLECT lessons)
    so the KEEP_TYPES hold-back rule can never drift between the two writers.

    Returns ``(section, entry_type)``:
      • ``entry_type`` — the classify_entry_type guess (one of the 7 types).
      • ``section`` — the target MEMORY.md section for auto-write, OR ``None``
        when the type is a KEEP_TYPE (principle/correction/decision/model).

    Why ``None`` for KEEP_TYPES (the load-bearing invariant): those tiers are
    evergreen — decay can NEVER reclaim them, so a wrong auto-commit is
    PERMANENT. They must never be auto-written to ANY section (not their own,
    and — the bug this fixes — not silently buried in Guidelines either). A
    ``None`` section signals the caller to HOLD BACK (log + drop, or escalate to
    human review), never to auto-sink. Operational types (guideline/pitfall/
    process) are decay-reclaimable + git-revertable, so they route to their
    section. classify_entry_type is a fallible keyword classifier: a KEEP-type
    lesson phrased without its signal token may misroute to 'guideline' — that
    is graceful degradation (reclaimable), NOT a hole; the guarantee is "no
    PERMANENT auto-commit of protected knowledge", not "no protected lesson is
    ever auto-written".
    """
    etype = classify_entry_type(lesson)
    if etype in _KEEP_TYPES:
        return (None, etype)
    return (MEMORY_TYPE_TO_SECTION.get(etype, "Guidelines"), etype)


def is_keep_class(
    entry: EntryMetadata,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
) -> bool:
    """True if `entry` is permanent knowledge that must NEVER be reclaimed.

    Errs toward KEEPING — a false-archive of a COE/principle/correction is
    unrecoverable context loss. Three independent, layered rules (any → keep):

      1. Section is evergreen (Principles/Corrections/COE Registry/...)
      2. Type ∈ {principle, correction, decision, model} (cognitive+meta layers)
      3. "COE" appears in the section or title (post-mortem registry entries,
         which may live in a custom section like "Key Lessons" with a
         misclassified type — rule 3 is the backstop for rules 1-2)
      (ref_count keep-rule removed R2-prime — ref is a dead input, see below)

    Only plain operational entries (guideline/pitfall/process) with ref 0,
    not in an evergreen section, and not COE-tagged are reclaimable.
    """
    if evergreen_sections and entry.section in evergreen_sections:
        return True
    if entry.entry_type in _KEEP_TYPES:
        return True
    # ref-based keep REMOVED (R2-prime, run_e50621b6): ref_count is a dead input
    # (no live body producer — Gate-2 verified) carrying only toxic prose residue.
    # Keeping on it protected exactly the generic-titled noise we set out to evict.
    # Keep-class now = evergreen-section OR keep-type OR COE-tagged (all honest).
    if "COE" in entry.section or "COE" in entry.title:
        return True
    return False


def reclaim_noise_entries(
    content: str,
    today: date,
    project_dir: Path,
    *,
    grace_days: int = GRACE_PERIOD_DAYS,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
    archive_name: str = "IMPROVEMENT-archive.md",
    source_path: "Path | None" = None,
    dry_run: bool = True,
) -> ReclaimReport:
    """Reclaim (archive + physically strip) stale operational-noise entries.

    Selection = exactly the set compute_entry_noise flags as noisy
    (ref_count==0 AND age>=grace AND decay∈{dormant,archived}), MINUS any
    entry protected by is_keep_class. This keeps the gauge and the action
    consistent (measure == action).

    Why a dedicated strip step (Gate-1 finding, verified): neither
    archive_entries nor inject_entry_metadata removes a bullet from the
    source — archived entries otherwise persist with decay:archived and are
    still counted as noise. This function is the ONLY path that lowers a
    doc's noise_rate.

    dry_run=True (default): pure preview — selects candidates, counts
    protected, writes nothing, returns new_content=None. dry_run=False:
    archives the candidates to `archive_name` and returns new_content with
    those bullets physically removed (caller persists it).

    Idempotent: a second call on the stripped content reclaims nothing
    (the noisy bullets are gone).
    """
    report = ReclaimReport()
    if not content or not content.strip():
        return report

    entries = parse_entries(content)
    if not entries:
        return report

    # Selection: reclaimable-noise set (shared predicate — gate==action),
    # minus keep-class.
    selected: list[EntryMetadata] = []
    for entry in entries:
        if not _is_reclaimable_noise(entry, today, grace_days):
            continue
        # Reclaimable. Now apply the protection guard.
        if is_keep_class(entry, evergreen_sections=evergreen_sections):
            report.kept_protected += 1
            continue
        selected.append(entry)

    # Duplicate-guard (parity with retire_entry, run_3e43c7ee): _strip_entries matches
    # by the (title, section) SET, so if two selected entries share an identical
    # (title, section) a single strip would remove BOTH while archive_entries records
    # each once — data loss. This became reachable when cultivated bullets gained
    # derived titles (a rare 1/839 title collision measured). retire_entry already
    # raises on this; the autonomous reclaim path must SKIP the ambiguous group (never
    # silently mass-strip a collision) and fail loud so it can be retired by name.
    import logging
    from collections import Counter
    _key_counts = Counter((e.title, e.section) for e in selected)
    _ambiguous = {k for k, n in _key_counts.items() if n > 1}
    if _ambiguous:
        logging.getLogger(__name__).warning(
            "reclaim: skipping %d entry(ies) in %d ambiguous (title, section) group(s) "
            "— a SET-strip would remove all colliders while archiving one (data loss). "
            "Disambiguate or retire by name. Titles: %s",
            sum(_key_counts[k] for k in _ambiguous), len(_ambiguous),
            sorted(t for t, _ in _ambiguous),
        )
        selected = [e for e in selected if (e.title, e.section) not in _ambiguous]

    report.candidates = [e.title for e in selected]

    if dry_run or not selected:
        return report

    # Apply via the shared archive+strip tail (also used by retire_entry;
    # recovery = archive + git, NO .bak — see _archive_and_strip docstring below).
    _archive_and_strip(content, selected, today, project_dir, report,
                       archive_name=archive_name, source_path=source_path)
    return report


def reclaim_duplicate_entries(
    content: str,
    today: date,
    project_dir: Path,
    *,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
    archive_name: str = "IMPROVEMENT-archive.md",
    source_path: "Path | None" = None,
    dry_run: bool = True,
) -> ReclaimReport:
    """Reclaim ALREADY-ACCUMULATED EXACT duplicates (archive + physically strip).

    Distinct from the two adjacent mechanisms — this fills a real gap:
      • ``reclaim_noise_entries`` selects by AGE/decay (ref==0 AND dormant/archived).
        A fresh exact-dup that is still ``active`` is invisible to it.
      • cultivation's ``content_signature`` intake-dedup only blocks a NEW write
        against existing entries — it never cleans the backlog already on disk.
    This sweeps the backlog: group all entries by ``content_signature`` (the SAME
    format-agnostic normalizer cultivation uses at intake, imported here so a lesson
    dedups identically whichever path wrote it), and within each collision group
    keep ONE survivor, archive+strip the rest.

    Survivor selection (deterministic): highest ``ref_count``, tie → newest
    ``created_date`` (a date-less entry sorts oldest). The survivor is the entry
    most likely to be the canonical/most-referenced copy.

    ``is_keep_class`` entries NEVER enter a candidate group (Principle 1: a
    principle/correction/decision/model — or an evergreen-section / COE entry — is
    never deleted on a duplicate signal; only plain guideline/pitfall/process are
    dedup-eligible). This is the SAME protection ``reclaim_noise_entries`` applies.

    NOT near-dup / similarity: signatures must be EXACTLY equal after normalization
    (strip bullet marker, [type] tag, attribution, bold, whitespace, case). This is
    deliberate — the knowledge governance rule is "never delete on a guess" (see
    ddd_cultivation.py supersession-language note); similarity-based deletion is a
    non-goal.

    Recovery: reuses ``_archive_and_strip`` (archive succeeds BEFORE the source is
    stripped; if ``archive_entries`` raises, the strip never runs). For the
    non-git-tracked ``.context/*.md`` files, the forward-append archive is the ONLY
    recovery path (git recovery does not apply) — so archive-before-strip is
    load-bearing, not just tidy.

    dry_run=True (default): pure preview (candidates listed, nothing written).
    dry_run=False: archive the non-survivors and persist the stripped source.
    Idempotent: a second sweep on the stripped content reclaims nothing.
    """
    from core.ddd_cultivation import content_signature  # unidirectional import (verified: lifecycle imports nothing from core; cultivation already imports lifecycle, never the reverse)

    report = ReclaimReport()
    if not content or not content.strip():
        return report

    entries = parse_entries(content)
    if not entries:
        return report

    # Dedup protection is by TYPE ONLY — NOT full is_keep_class (which also protects
    # evergreen SECTIONS). Rationale: removing an EXACT duplicate loses nothing (the
    # survivor keeps the content), so it is safe in ANY section — an evergreen
    # section protects against AGE-death, not against de-duplication. But the 4
    # judgment TYPES (principle/correction/decision/model) are hand-authored and a
    # same-signature "dup" there may be an intentional cross-reference, so those are
    # never auto-deduped (retire by name instead). This is the key difference from
    # reclaim_noise_entries (which uses full is_keep_class) — and it is what lets
    # dedup act on EVOLUTION at all (EVOLUTION is fully evergreen for age-decay, so
    # a section-based guard would make dedup a permanent no-op there too).
    def _dedup_protected(e: EntryMetadata) -> bool:
        return e.entry_type in _KEEP_TYPES or "COE" in e.section or "COE" in e.title

    def _sig(e: EntryMetadata) -> str:
        # raw_text already includes the leading "- " bullet marker; do NOT prepend
        # another (a double "- - " leaves a residual "- " that content_signature's
        # single-marker strip can't remove, poisoning the signature). Fall back to
        # a synthesized bullet only when raw_text is empty.
        raw = e.raw_text if (e.raw_text and e.raw_text.lstrip().startswith("- ")) else "- " + (e.raw_text or e.title)
        return content_signature(raw)

    # Group non-type-protected entries by exact content_signature.
    groups: dict[str, list[EntryMetadata]] = {}
    for entry in entries:
        if _dedup_protected(entry):
            continue
        sig = _sig(entry)
        if not sig:
            continue
        groups.setdefault(sig, []).append(entry)

    # Count type-protected entries that DO collide (protected-from-dedup gauge).
    _keep_sigs: dict[str, int] = {}
    for entry in entries:
        if not _dedup_protected(entry):
            continue
        sig = _sig(entry)
        if sig:
            _keep_sigs[sig] = _keep_sigs.get(sig, 0) + 1
    report.kept_protected = sum(n - 1 for n in _keep_sigs.values() if n > 1)

    # Within each collision group (>=2), keep the survivor, select the rest.
    def _rank(e: EntryMetadata) -> tuple:
        # Higher ref first; tie → newer created_date first (None sorts oldest).
        cd = e.created_date or date.min
        return (e.ref_count, cd)

    selected: list[EntryMetadata] = []
    for sig, grp in groups.items():
        if len(grp) < 2:
            continue
        grp_sorted = sorted(grp, key=_rank, reverse=True)
        # grp_sorted[0] = survivor; the rest are duplicates to reclaim.
        selected.extend(grp_sorted[1:])

    report.candidates = [e.title for e in selected]

    if dry_run or not selected:
        return report

    # LINE-PRECISE archive+strip (NOT _archive_and_strip's (title,section) set-strip).
    # A dedup group shares one content_signature — and since content_signature is
    # title-INCLUSIVE, the non-survivors necessarily share the survivor's
    # (title, section). So _archive_and_strip's set-strip would remove the SURVIVOR
    # too (it can't tell them apart by title) — that is exactly the data-loss the
    # old ambiguous-guard tried to dodge, at the cost of making dedup a permanent
    # no-op (every real signature-dup is same-title). The correct identity for
    # dedup is the entry's own line span. archive the non-survivors, then strip
    # ONLY their blocks by line_number, leaving the survivor untouched.
    archived = archive_entries(project_dir, selected, archive_name=archive_name,
                               source_path=source_path)
    report.archived = archived
    # Strip by the entry's own 0-based line index. parse_entries ALWAYS assigns a
    # real index (0 is valid — an entry at the very first line), so do NOT filter
    # `> 0` (that would leave a line-0 dup un-stripped — the invariant-weakening
    # Gate-2 flagged). `selected` are all parsed entries, so every line_number is
    # genuine; a synthesized entry (line_number defaulting to 0) never reaches here.
    report.new_content = _strip_entries_by_line(
        content, {e.line_number for e in selected}
    )
    if source_path is not None:
        Path(source_path).write_text(report.new_content, encoding="utf-8")
    return report


def _archive_and_strip(
    content: str,
    selected: list[EntryMetadata],
    today: date,
    project_dir: Path,
    report: ReclaimReport,
    *,
    archive_name: str,
    source_path: "Path | None",
    include_prose: bool = False,
) -> None:
    """Shared apply-tail for reclaim_noise_entries AND retire_entry (R25 dedup):
    archive the `selected` entries to `archive_name`, physically strip them from
    `content` by (title, section) identity, and — if `source_path` is given —
    persist the stripped content (NO .bak; recovery = archive + git, see below).
    Mutates `report` (sets .archived + .new_content). Selection is the CALLER's
    job; this is the common machinery both selection strategies share.

    Strip by (title, section) identity — NOT title alone — so a keep-class entry
    that merely SHARES a title with a selected one is never deleted (adversarial
    C1: title-only strip silently destroyed protected knowledge that wasn't even
    archived).

    Recovery of a stripped entry has TWO independent, live paths — NO dated .bak
    (run_a6482355): (1) `archive_entries` above appends every stripped entry (full
    raw_text + metadata) to <archive_name>, which recall/FTS reads — the entry is
    never lost, only moved; (2) the source doc is git-tracked and the workspace
    auto-commits many times/minute, so `git show HEAD~N:<file>` recovers any
    pre-strip whole-file state (dirty window negligible). The old dated .bak was a
    THIRD copy nobody reads that silted into a graveyard (14 stale .bak purged
    across DDDs 2026-07-20) — a disaster-recovery copy masquerading as safety
    (Principle 1 / STEERING #2). Removed: archive + git already make recovery robust.
    """
    archived = archive_entries(project_dir, selected, archive_name=archive_name,
                               source_path=source_path)
    report.archived = archived
    report.new_content = _strip_entries(
        content, {(e.title, e.section) for e in selected},
        include_prose=include_prose,
    )
    if source_path is not None:
        Path(source_path).write_text(report.new_content, encoding="utf-8")


class RetireError(ValueError):
    """retire_entry could not act safely — no match, or an ambiguous duplicate.
    Fail-LOUD by design: a silent zero-strip or a strip-2-archive-1 would be data
    loss (Gate-1 findings). The caller must correct the (title, section) and retry.
    """


def retire_entry(
    content: str,
    title: str,
    section: str,
    project_dir: Path,
    *,
    archive_name: str = "IMPROVEMENT-archive.md",
    source_path: "Path | None" = None,
    dry_run: bool = True,
    force: bool = False,
    today: "date | None" = None,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
) -> ReclaimReport:
    """Agent-directed retire of ONE named (title, section) entry — the sanctioned
    'out' side of the knowledge layer (mirrors s_self-evolution's RETIRE for the
    governance layer). Reuses the same archive+strip machinery as
    reclaim_noise_entries via _archive_and_strip; the ONLY difference is selection:
    exactly the one entry the caller names, not the autonomous noise set.

    This is why a raw markdown Edit-to-delete is NOT the sanctioned path: it skips
    the archive (→ lost from FTS5 recall) and the (title, section) identity-strip
    (→ can destroy a same-title sibling). Route removals through here.

    Safety (Gate-1, run_186a5f15):
    - EXACTLY-ONE match required. Zero matches → RetireError (never a silent
      zero-strip). Two+ entries sharing the exact (title, section) → RetireError
      (a single strip would remove BOTH while archive records ONE = data loss).
    - keep-class entries (decision/model/principle/correction, COE/evergreen
      sections) are REFUSED unless force=True — so a curator CAN deliberately
      retire permanent knowledge by name (the point of agent-directed removal),
      but never by accident.

    dry_run=True (default): preview only — sets report.candidates, writes nothing.
    dry_run=False: archive + strip + persist (recovery = archive + git, no .bak).

    For a MOVE across files: the CALLER must add-to-target FIRST (via s_persist,
    dedup-checked) and retire-from-source SECOND — so the entry is durable in its
    new home before it leaves the old one (a mid-crash then leaves a recoverable
    duplicate, never a loss).
    """
    report = ReclaimReport()
    if not content or not content.strip():
        raise RetireError("empty content — nothing to retire")

    # include_prose=True: retire is the ONE by-name, human-invoked path allowed to
    # act on emoji-prefixed curated prose (Open-Threads bullets). Autonomous paths
    # never opt in — so background inject/decay/reclaim can't touch prose (Gate-2).
    matches = [e for e in parse_entries(content, include_prose=True)
               if e.title == title and e.section == section]
    if len(matches) == 0:
        raise RetireError(
            f"no entry titled {title!r} in section {section!r} — nothing retired "
            f"(check the exact title + section; retire is fail-loud, not silent)."
        )
    if len(matches) > 1:
        raise RetireError(
            f"{len(matches)} entries share (title={title!r}, section={section!r}) — "
            f"refusing: a strip would remove all {len(matches)} but archive only one "
            f"(data loss). Disambiguate before retiring."
        )

    entry = matches[0]
    # Keep-class check MUST receive the doc's evergreen set — else section-based
    # immunity (rule 1: Open Threads=process, Standing Preferences=guideline in
    # MEMORY_EVERGREEN_SECTIONS) is silently dead and those permanent entries would
    # be retirable WITHOUT --force, defeating the guard (Gate-2 MED). Parity with
    # reclaim_noise_entries, which passes evergreen_sections. Default to the MEMORY
    # set when the caller doesn't specify (the strictest, safest default).
    _evergreen = evergreen_sections if evergreen_sections is not None else MEMORY_EVERGREEN_SECTIONS
    if not force and is_keep_class(entry, evergreen_sections=_evergreen):
        raise RetireError(
            f"{title!r} is keep-class (type={entry.entry_type}, section={section!r}) — "
            f"refusing without force=True. Pass force to deliberately retire permanent "
            f"knowledge (decision/model/principle/correction/COE)."
        )

    report.candidates = [entry.title]
    if dry_run:
        return report

    _archive_and_strip(content, [entry], today=today or date.today(),
                       project_dir=project_dir, report=report,
                       archive_name=archive_name, source_path=source_path,
                       include_prose=True)
    return report


def _strip_entries(content: str, keys: "set[tuple[str, str]]", *,
                   include_prose: bool = False) -> str:
    """Return content with the bullet blocks for `keys` physically removed.

    `keys` is a set of (title, section) pairs — NOT bare titles. Matching by
    (title, section) ensures a keep-class entry that merely shares a title with
    a reclaimed entry in a different section is never deleted (adversarial C1).

    A "block" is the entry bullet line, its continuation/wrapped lines, and the
    trailing metadata comment (mirrors parse_entries' block boundaries). Section
    headers and non-matching entries are preserved verbatim.

    include_prose MUST match the value parse_entries used to BUILD `keys` — else
    the line-scan here and the entry-detection there disagree and a keyed prose
    entry is never found to strip (archive-without-strip split). The retire path
    passes include_prose=True on BOTH; autonomous reclaim leaves it False on both.
    """
    if not keys:
        return content

    lines = content.splitlines()
    result: list[str] = []
    i = 0
    n = len(lines)
    current_section = ""

    while i < n:
        line = lines[i]
        # Track section headers exactly as parse_entries does, so the
        # (title, section) key matches the parsed entry's section.
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            result.append(line)
            i += 1
            continue
        m = _match_entry_line(line, include_prose=include_prose)
        if m and (m.group(2), current_section) in keys:
            # Skip this entry's whole block: bullet + continuations + meta.
            i += 1
            while i < n:
                nxt = lines[i]
                if nxt.startswith("- ") or nxt.startswith("## "):
                    break
                if _META_RE.match(nxt):
                    i += 1  # consume the metadata line, then stop
                    break
                if nxt.strip() == "":
                    # Blank: stop unless it's the separator before this block's meta.
                    peek = i + 1
                    if peek < n and _META_RE.match(lines[peek]):
                        i = peek + 1
                    break
                i += 1
            # Drop a single trailing blank separator left behind, to avoid
            # accumulating blank-line runs across repeated reclaims.
            if result and result[-1].strip() == "" and i < n and lines[i].strip() == "":
                i += 1
            continue
        result.append(line)
        i += 1

    trailing = content.endswith("\n")
    out = "\n".join(result)
    if trailing and not out.endswith("\n"):
        out += "\n"
    return out


def _strip_entries_by_line(content: str, start_lines: "set[int]") -> str:
    """Return content with the bullet blocks that START at `start_lines` removed.

    `start_lines` are 0-based indices into content.splitlines() — the EXACT
    `EntryMetadata.line_number` values parse_entries assigned. Used by
    reclaim_duplicate_entries: a dedup group shares one (title, section) (because
    content_signature is title-inclusive), so the (title, section) set-strip in
    _strip_entries CANNOT keep the survivor — it would strip every collider. Line
    identity is the only precise handle. A "block" is the bullet line + its
    continuation/wrapped lines + the trailing metadata comment (same boundary as
    _strip_entries / parse_entries), so the survivor (a different start line) is
    left fully intact.
    """
    if not start_lines:
        return content
    lines = content.splitlines()
    result: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if i in start_lines:
            # Skip this entry's whole block: bullet + continuations + meta.
            i += 1
            while i < n:
                nxt = lines[i]
                if nxt.startswith("- ") or nxt.startswith("## "):
                    break
                if _META_RE.match(nxt):
                    i += 1  # consume the metadata line, then stop
                    break
                if nxt.strip() == "":
                    peek = i + 1
                    if peek < n and _META_RE.match(lines[peek]):
                        i = peek + 1
                    break
                i += 1
            # Drop a single trailing blank separator to avoid blank-run buildup.
            if result and result[-1].strip() == "" and i < n and lines[i].strip() == "":
                i += 1
            continue
        result.append(lines[i])
        i += 1
    trailing = content.endswith("\n")
    out = "\n".join(result)
    if trailing and not out.endswith("\n"):
        out += "\n"
    return out


# ── Stacked-Metadata Heal (R-1, run_55c02bbe) ─────────────────────────────────


def collapse_stacked_metadata(content: str) -> str:
    """Collapse consecutive ``<!-- ref ... -->`` metadata lines to ONE per entry.

    Heals the orphan-metadata bug: ``_extract_lessons_to_memory``'s off-by-one
    splice inserted a new entry+meta BETWEEN an existing bullet and its meta,
    orphaning the existing meta as a 2nd consecutive metadata line.
    ``inject_entry_metadata`` is orphan-blind (consumes only the first meta), so
    a dedicated sweep is required.

    Rule: within a run of consecutive metadata lines (no bullet/section between),
    keep exactly ONE — preferring a real ``last:DATE`` over ``last:none`` (an
    un-referenced orphan default). When multiple real dates collide, keep the
    FIRST (it is the entry's own lifecycle-injected meta; later ones are
    displaced neighbors). Bullets and single-meta entries are untouched.

    Pure function (no I/O). Idempotent: a second call is a no-op.
    """
    if not content:
        return content

    lines = content.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _META_RE.match(lines[i]):
            # Gather the full run of consecutive metadata lines.
            run = [lines[i]]
            j = i + 1
            while j < n and _META_RE.match(lines[j]):
                run.append(lines[j])
                j += 1
            out.append(_pick_meta(run))
            i = j
        else:
            out.append(lines[i])
            i += 1

    trailing = content.endswith("\n")
    result = "\n".join(out)
    if trailing and not result.endswith("\n"):
        result += "\n"
    return result


def _pick_meta(run: list[str]) -> str:
    """Pick the surviving metadata line from a run of consecutive metas.

    Priority: among lines with a real ``last:`` date (not ``none``), keep the
    one with the highest ``ref:`` count (the most-referenced lifecycle meta);
    ties broken by first occurrence. If every line is ``last:none``, keep the
    highest-ref one, else the first. This never discards a real date in favour
    of an orphan default, and prefers the richer (higher-ref) survivor when two
    real metas ever stack (R-1 Gate-2 #5).
    """
    if len(run) == 1:
        return run[0]

    def ref_of(line: str) -> int:
        m = _META_RE.match(line)
        return int(m.group(1)) if m else -1

    real = [ln for ln in run if (_META_RE.match(ln) and _META_RE.match(ln).group(2) != "none")]
    candidates = real if real else run
    # max() is stable on ties → keeps first occurrence among equal-ref lines.
    return max(candidates, key=ref_of)


# ── Private Helpers ───────────────────────────────────────────────────────────


def _extract_date(text: str) -> Optional[date]:
    """Extract the first YYYY-MM-DD date from entry text."""
    m = _DATE_RE.search(text)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            return None
    return None
