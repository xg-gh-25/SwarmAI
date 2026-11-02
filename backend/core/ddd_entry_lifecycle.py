"""DDD Entry Lifecycle — per-entry parsing, typing and metadata for knowledge docs.

Each bullet entry of a DDD doc or a context file can carry:
- a type tag: [guideline], [pitfall], [decision], [model], [process], [principle], [correction]
- a metadata comment ``<!-- source:… | taint:… -->`` (plus ``valid_until`` /
  ``superseded_by`` when set)

Nothing decays with age any more: what is in a main file is in force, and an entry
leaves it only by an override or a withdrawal (design docs/design/2026-10-30-brain-
driven.md §2.3). So the operational fields ``ref``/``last``/``decay`` are dead: no
writer writes them (2026-11-02). Older files and every archive shard still carry the
old prefix ``<!-- ref:N | last:<date> | decay:<state> | …``; ``_META_RE`` reads both.

Public API:
    EntryMetadata        — dataclass for per-entry state
    parse_entries(content) → list[EntryMetadata]
    split_entries(body) → list[str]  (recall's entry splitter)
    classify_entry_type(text) → str
    reclaim_duplicate_entries(...) → ReclaimReport  (exact duplicates, read-only)
"""

import hashlib
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

# ── KNOWLEDGE.md / EVOLUTION.md Evergreen Sections ────────────────────────────
#
# Reference sections by full header text, as parse_entries records `section`.
# Read by the duplicate selector's callers; nothing ages out of any section.
KNOWLEDGE_EVERGREEN_SECTIONS = frozenset({
    "Architecture Overview [model]",
    "The 12 Context Files [model]",
    "Claude Code CLI Hidden Defaults [constraint]",
    "Code Documentation Standards [guideline]",
    "Codebase Navigation [model]",
    "Frontend Architecture [model]",
    "Database Schema [model]",
    "Hook System [model]",
    "Pipeline — What It Is & How To Check State [model]",
    "Job System [model]",
})

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
# reclaim). It DELIBERATELY does NOT match emoji/marker-
# prefixed hand-curated prose bullets (e.g. "- 🟡 **Title**") — because those paths
# WRITE: inject stamps <!-- ref --> metadata, reclaim strips+archives. Making prose
# parseable here would let the autonomous jobs mutate/strip XG's curated Open-Threads
# prose (Gate-2 run_748f14a7: confirmed inject would stamp 8 OT bullets; ddd_orchestrator
# reclaim would strip aged emoji bullets in IMPROVEMENT.md — neither is evergreen-guarded).
# So prose-matching is OPT-IN, isolated to the retire path (see _ENTRY_RE_PROSE below).
_ENTRY_RE = re.compile(
    r"^- (?:\[(\w+)\] )?\*\*(.+?)\*\*"
)

# Leading author-declared "[type] " prefix on a RAW lesson string (NOT a bullet).
# Used by route_lesson_type to honor an author-declared type over a keyword guess
# (run_4ad5a44b). Anchored at start; group(1)=type.
_DECLARED_TYPE_RE = re.compile(r"^\[(\w+)\]\s+")

# ── Shared boundary-detection fragments (SSOT, run_3cb6b9ae Cycle-5 #5) ────────
# The MEMORY entry-boundary detectors deliberately DIFFER in scope (each is tuned
# to its job — see the note below), but two regex FRAGMENTS were duplicated as
# string literals across `distillation_hook._is_entry_start` and
# `context_recall._slice_section_entries`. They are defined ONCE here (the format
# authority, alongside _ENTRY_RE + VALID_TYPES) and referenced there, so the
# entry-shape vocabulary drifts in ONE place, not N. This is NOT a merge of the
# detectors — the deliberate scope differences are preserved:
#   • ddd_entry_lifecycle._ENTRY_RE — REQUIRES a bold `**title**` (autonomous
#     parse/decay/reclaim; refuses to touch bold-less curated prose).
#   • distillation._is_entry_start — BROAD (any `- ` OR a bare typed/ID lead) for
#     size-valve span detection (must catch every archivable line).
#   • context_recall entry-lead — NARROW (typed/ID/emoji bullets only, never a
#     plain `- `) so a body sub-bullet can't orphan on the recall read path.
# ID-lead = an ID-shaped bare tag like [PRI01]/[COE8] (2-4 upper + 1-3 digits).
_ID_LEAD_PAT = r"\[[A-Z]{2,4}\d{1,3}\]"
# Type-lead = a bracketed VALID_TYPES tag, case-insensitive (the valve lowercases
# before matching, so recall must too — P8 same-vocabulary-both-doors).
_TYPE_LEAD_PAT = r"(?i:\[(?:" + "|".join(VALID_TYPES) + r")\])"

# Prose-inclusive matcher — OPT-IN (parse_entries(include_prose=True)); no production
# caller uses it since the by-name retire path was removed.
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
    include_prose=True: _ENTRY_RE_PROSE — no production caller opts in (the by-name retire path is gone), so
    a curator can name a hand-curated Open-Threads bullet deliberately.
    Both return group(1)=type, group(2)=title.
    """
    return (_ENTRY_RE_PROSE if include_prose else _ENTRY_RE).match(line)

# Regex for inline metadata comment
# Current shape: "  <!-- source:auto -->", "  <!-- source:S7 | taint:external -->".
# Old shape (archives, files not yet migrated): the same fields after a dead prefix,
#   "  <!-- ref:N | last:YYYY-MM-DD | decay:state | source:auto -->"; groups 1-3 are
#   None for the current shape. The lookahead keeps a comment that starts with any
#   other word (a cand: marker, a guidance comment) from being read as metadata.
# Bi-temporal supersession (run_24299917): two more OPTIONAL trailing segments:
#   "... | valid_until:YYYY-MM-DD | superseded_by:<anchor>"
# Provenance taint (design 2026-10-27-new-brain-initial-ddd §5.5): one more OPTIONAL
# segment right after source, "... | source:bootstrap | taint:external", marking an
# entry whose content was read from a repo or another outside file (CDF §9 trust).
# All four trailing groups (source, taint, valid_until, superseded_by) are independent
# and optional — the leading space of each lives INSIDE its own group so a comment
# carrying only a later field (e.g. superseded_by, no source) still matches, and a
# legacy comment with none of them is byte-identical to the pre-supersession form.
# superseded_by is a TITLE anchor and titles contain SPACES (e.g. "New unified
# cache strategy"), so its group is `([^|]+?)` — any run of non-pipe chars,
# non-greedy, terminated by the trailing ` -->$` anchor. It is the LAST field so
# it cannot collide with a following ` | ` separator; the captured value is
# .strip()'d at parse time to drop the space before ` -->`. (valid_until stays a
# strict date token; the `null` sentinel + missing-field both mean "not set".)
_META_FIELDS = ("ref", "source", "taint", "valid_until", "superseded_by")
_META_RE = re.compile(
    r"^\s*<!--\s*(?=(?:" + "|".join(_META_FIELDS) + r"):)"
    r"(?:ref:(\d+)\s*\|\s*last:([\w\-]+)\s*\|\s*decay:(\w+))?"
    r"(?:\s*\|?\s*source:(\w+))?"
    r"(?:\s*\|?\s*taint:(\w+))?"
    r"(?:\s*\|?\s*valid_until:([\w\-]+))?"
    r"(?:\s*\|?\s*superseded_by:([^|]+?))?"
    r"\s*-->$"
)


def is_meta_line(line: str) -> bool:
    """True when *line* is an entry's metadata comment, in either shape."""
    return bool(_META_RE.match(line))


def meta_comment(*, source: str = "", taint: str = "", valid_until: "date | None" = None,
                 superseded_by: "str | None" = None) -> str:
    """THE metadata line every writer writes: only the fields that are set, in
    ``_META_RE``'s order. "" when none is set (the entry then has no metadata line)."""
    parts = []
    if source:
        parts.append(f"source:{source}")
    if taint:
        parts.append(f"taint:{taint}")
    if valid_until:
        parts.append(f"valid_until:{valid_until.isoformat()}")
    if superseded_by:
        parts.append(f"superseded_by:{superseded_by}")
    return f"  <!-- {' | '.join(parts)} -->" if parts else ""

# Regex for date extraction from entry text "(YYYY-MM-DD, ...)"
_DATE_RE = re.compile(r"\((\d{4}-\d{2}-\d{2})")


def format_memory_entry(
    entry_type: str,
    title: str,
    body: str,
    date_str: str,
    run_id: "str | None" = None,
    *,
    source: str = "auto",
) -> "tuple[str, str]":
    """THE single source of truth for a MEMORY.md entry's on-disk shape (run_3cb6b9ae).

    Every MEMORY write door MUST route through this so the entry line + metadata are
    IDENTICAL across doors — the fix for the format-drift bug where distillation's
    `_format_enriched_entry` wrote a bare ``- {date}: {text}`` (no [type], no **title**,
    no metadata) while context_health wrote the canonical shape. A divergent shape is
    invisible to decay/dedup/recall boundary detection (parse_entries._ENTRY_RE requires
    ``- [type] **title**``), so a malformed entry silently never decays and never dedups.

    Returns ``(entry_line, meta_line)`` — the caller joins them with a newline. Shape:

        - [<type>] **<title>** — <body> (<date>[, <run_id>])
          <!-- source:<source> -->

    IDEMPOTENT / build-time only: this builds a NEW entry from PARTS. It never scans or
    rewrites existing entries, so the entries already in MEMORY.md are untouched. The
    caller derives (type, title, body) — e.g. via `route_lesson_type` + a title split —
    BEFORE calling this; this function does not re-parse, it trusts its args (writer≠parser).

    Args:
        entry_type: one of VALID_TYPES (falls back to DEFAULT_TYPE if unknown).
        title: short bold title (no surrounding ``**``; trailing ``.`` stripped here).
        body: the entry body (the durable rule). May equal or extend the title.
        date_str: YYYY-MM-DD (the entry's date).
        run_id: optional provenance run id, appended as ``(date, run_id)``.
        source: metadata `source:` value (``auto`` | ``manual`` | ...).
    """
    etype = entry_type if entry_type in VALID_TYPES else DEFAULT_TYPE
    clean_title = title.strip().strip("*").rstrip(".").strip() or body[:60].strip()
    prov = f"({date_str}, {run_id})" if run_id else f"({date_str})"
    entry_line = f"- [{etype}] **{clean_title}** — {body} {prov}"
    meta_line = meta_comment(source=source)
    return entry_line, meta_line


_BOLD_LEAD_RE = re.compile(r"^\*\*(.+?)\*\*\s*(?:—\s*)?(.*)$")
_LEAD_SCAFFOLD_RE = re.compile(r"^\s*(?:-\s+)?(?:\d{4}-\d{2}-\d{2}:?\s+)?(?:\[\w+\]\s+)?")


TAINTS = ("external",)


# What a rendered entry may not contain (render_ddd_unit refuses it; the bootstrap
# entry contract states it from these same constants).
TITLE_FORBIDDEN = ("**", "|")
COMMENT_MARKERS = ("<!--", "-->")


def render_ddd_unit(entry: dict, *, date_str: str, source: str = "bootstrap",
                    taint: "str | None" = None) -> str:
    """THE one renderer of a DDD knowledge entry (entry line + metadata line).

    Shared by every writer of DDD entries (design 2026-10-27-new-brain-initial-ddd
    §5.5: the New Brain bootstrap now, CDF's writer next), so what is written is
    exactly what ``parse_entries`` reads back. Shape:

        - [<type>] **<title>** <what> **How:** … **Why:** … **Rejected:** …
          **Applies when:** … (sources: a; b)
          <!-- source:<source>[ | taint:external] -->

    ``entry``: ``type`` (one of VALID_TYPES, or ``gap`` — written as ``guideline``,
    since a gap is not an ontology type), ``title``, ``what``; optional ``how``,
    ``why``, ``rejected``, ``applicability``, ``sources`` (list). The title is never
    truncated. ``taint`` is None or ``"external"`` (content read from a repo or
    another outside file). Raises ValueError for what the parser could not read back
    or what would break the doc: an empty title or ``what``; a title with ``**``, a
    line break or ``|`` (a title is also a supersession anchor); ``<!--`` / ``-->``
    anywhere.

    Metadata contract (``_META_RE``): the segments appear in this fixed order — ``source``, ``taint``, ``valid_until``, ``superseded_by`` — and
    ``source`` / ``taint`` values are single words. A comment that breaks the order is
    not read as metadata. (``format_memory_entry`` and ``canonical_entry_from_text``
    are MEMORY.md's renderers; they write no taint.)
    """
    etype = str(entry.get("type", "")).strip()
    if etype == "gap":
        etype = "guideline"
    if etype not in VALID_TYPES:
        raise ValueError(f"unknown entry type {entry.get('type')!r}")
    title = str(entry.get("title", "")).strip()
    # splitlines(), not "\n": U+2028, \x85, \x0c … also end a line for the parser.
    if not title or len(title.splitlines()) != 1 or any(bad in title for bad in TITLE_FORBIDDEN):
        raise ValueError("entry title must be one non-empty line without '**' or '|'")
    if not str(entry.get("what", "")).strip():
        raise ValueError("entry 'what' must not be empty")
    for key in ("title", "what", "how", "why", "rejected", "applicability", "sources"):
        if any(c in str(entry.get(key, "")) for c in COMMENT_MARKERS):
            raise ValueError(f"entry {key} must not contain an HTML comment marker")
    if taint is not None and taint not in TAINTS:
        raise ValueError(f"unknown taint {taint!r} (known: {', '.join(TAINTS)})")
    if not re.fullmatch(r"\w+", source):
        raise ValueError(f"source must be a single word, got {source!r}")

    def _one_line(v) -> str:
        return " ".join(str(v).split())

    parts = [_one_line(entry.get("what", ""))]
    for key, label in (("how", "How"), ("why", "Why"), ("rejected", "Rejected"),
                       ("applicability", "Applies when")):
        if entry.get(key):
            parts.append(f"**{label}:** {_one_line(entry[key])}")
    sources = [s for s in (entry.get("sources") or []) if str(s).strip()]
    if sources:
        parts.append("(sources: " + "; ".join(_one_line(s) for s in sources) + ")")
    body = " ".join(p for p in parts if p)
    meta = EntryMetadata(title=title, entry_type=etype, source=source,
                         taint=taint or "",
                         last_referenced=date.fromisoformat(date_str))
    line = f"- [{etype}] **{title}** {body}".rstrip()
    return f"{line}\n{meta.to_comment()}"


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
    # "external" when the content came from a repo or another outside file (CDF §9);
    # "" otherwise. Kept verbatim through to_comment.
    taint: str = ""
    # Bi-temporal supersession (run_24299917). When superseded_by is set, this
    # entry has been REPLACED by a newer entry (anchor = the superseding entry's
    # title): it is FILTERED from default recall + SKIPPED by age-decay + NEVER
    # deleted (lineage preserved — Principle 1: sediment flows UP, not just OUT).
    # valid_until = the date supersession was marked (the entry was "true" over
    # [created_date, valid_until]). Both None for a live entry.
    valid_until: Optional[date] = None
    superseded_by: Optional[str] = None  # title-anchor of the superseding entry
    # Last line index (inclusive) of this entry's span — see entry_span_end. -1 when
    # the entry was not built by parse_entries.
    end_line: int = -1

    def __post_init__(self):
        if self.entry_type not in VALID_TYPES:
            self.entry_type = DEFAULT_TYPE
        if self.decay_state not in ("active", "dormant", "archived"):
            self.decay_state = "active"

    def to_comment(self) -> str:
        """The entry's metadata line in the current shape (``meta_comment``); "" when
        the entry has none of its fields. ref_count / last_referenced / decay_state are
        read from old-shape comments and never written back."""
        return meta_comment(source=self.source, taint=self.taint,
                            valid_until=self.valid_until, superseded_by=self.superseded_by)


@dataclass
class ReclaimReport:
    """What a reclaim / retire selector picked. Read-only: since CDF stage B no
    automated path archives or strips an entry (design 2026-10-26 CDF §8)."""
    candidates: list[str] = field(default_factory=list)   # titles selected
    kept_protected: int = 0                                # noisy-but-keep-class count


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


# CommonMark ATX heading: up to 3 leading spaces, 1-6 '#', then space/tab or EOL.
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")


def _scan_entry(lines: "list[str]", start: int) -> "tuple[int, int | None, int]":
    """THE one reading of an entry whose lead bullet is ``lines[start]``.

    Returns ``(text_end, meta_idx, end)`` — all exclusive line indices except
    ``meta_idx``: ``lines[start:text_end]`` is the entry text (lead + continuation),
    ``meta_idx`` the ``<!-- ref ... -->`` line or None, ``lines[start:end]`` the whole
    span to remove. Used by parse_entries and both strip
    helpers, so what is read as one entry is rewritten and removed as one entry
    (design 2026-10-25-memory-write-entry-dedup §3.1).

    Rules: the entry text runs over continuation lines and stops at a column-0
    ``- ``, a heading, the metadata line, or a blank line. A blank run followed by
    the metadata line belongs to the entry; any other blank ends it. After the
    metadata line, indented non-blank lines (the ``Detail:`` / ``valid_from`` tail)
    belong to the entry, up to a heading or another metadata line.
    """
    n = len(lines)
    j = start + 1
    while j < n:
        nxt = lines[j]
        if nxt.startswith("- ") or _HEADING_RE.match(nxt):
            return j, None, j
        if _META_RE.match(nxt):
            text_end, meta = j, j
            break
        if nxt.strip() == "":
            k = j
            while k < n and lines[k].strip() == "":
                k += 1
            if k < n and _META_RE.match(lines[k]):
                text_end, meta = j, k
                break
            return j, None, j
        j += 1
    else:
        return n, None, n
    end = meta + 1
    while (end < n and lines[end].strip() and lines[end][0] in " \t"
           and not _META_RE.match(lines[end]) and not _HEADING_RE.match(lines[end])):
        end += 1
    return text_end, meta, end


def _h3_span_lines(lines: "list[str]") -> "set[int]":
    """Line indices inside a ``###`` block (header to the next ``###`` / ``##``)."""
    inside: set[int] = set()
    in_block = False
    for idx, ln in enumerate(lines):
        if ln.startswith("### "):
            in_block = True
        elif ln.startswith("## ") or ln.startswith("# "):
            in_block = False
        if in_block:
            inside.add(idx)
    return inside


def split_entries(body: str) -> "list[str]":
    """Every column-0 ``- `` entry of *body*, read by ``_scan_entry``: the lead bullet,
    its continuation lines and sub-bullets, and its metadata tail. What lies between
    entries (a ``###`` subsection, a table, a paragraph) belongs to none of them.
    Recall's one entry splitter (design 2026-10-26-run-knowledge §3.0 R1), so what
    recall shows as an entry is what parse_entries, decay and dedup treat as one.

    Code fences are not tracked here (``split_blocks_at`` tracks them for blocks): a
    fence opened in a cut entry is often never closed, and honouring it would swallow
    every later entry. So a ``- `` line inside a fence is an entry for recall's entry
    leg; the knowledge unit table reads blocks through ``split_blocks_at`` and can then
    differ for such a line (a known, rare gap: knowledge addressing §7)."""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("- "):
            _text_end, _meta, end = _scan_entry(lines, i)
            out.append("\n".join(lines[i:end]).strip())
            i = max(end, i + 1)
        else:
            i += 1
    return out


def split_blocks(body: str) -> "list[tuple[str, str]]":
    """*body* as recall units ``(kind, text)``, in order -- ``split_blocks_at`` without
    the line numbers."""
    return [(kind, text) for kind, text, _line in split_blocks_at(body)]


def split_blocks_at(body: str) -> "list[tuple[str, str, int]]":
    """*body* as recall units ``(kind, text, line)``, in order; ``line`` is the 0-based
    index in ``body.splitlines()`` of the unit's first line. Each column-0 ``- `` entry (read as
    ``split_entries`` reads it) is ``("entry", text)``; what lies between entries is cut
    at blank lines into ``("block", text)`` -- a paragraph, a table, a ``###`` heading with
    the lines under it. For docs written as prose (PRODUCT): a whole ``## `` section is
    too coarse to rank (one 40K-character section matches any query), a paragraph is not.
    run-knowledge §3.6.9. A fenced code block is one block: a blank line or a ``- `` line
    inside a fence is code, and cutting there would show half of it."""
    lines = body.splitlines()
    out: list[tuple[str, str, int]] = []
    buf: list[str] = []
    buf_start = [0]
    fence = ""

    def flush() -> None:
        text = "\n".join(buf).strip()
        if text:
            lead = next(n for n, ln in enumerate(buf) if ln.strip())
            out.append(("block", text, buf_start[0] + lead))
        buf.clear()
    i = 0
    while i < len(lines):
        mark = lines[i].lstrip()[:3]
        if mark in ("```", "~~~") and (not fence or fence == mark):
            fence = "" if fence else mark
            if not buf:
                buf_start[0] = i
            buf.append(lines[i])
            i += 1
            continue
        if fence:
            if not buf:
                buf_start[0] = i
            buf.append(lines[i])
            i += 1
            continue
        if lines[i].startswith("- "):
            flush()
            _text_end, _meta, end = _scan_entry(lines, i)
            out.append(("entry", "\n".join(lines[i:end]).strip(), i))
            i = max(end, i + 1)
            continue
        if not lines[i].strip():
            flush()
        else:
            if not buf:
                buf_start[0] = i
            buf.append(lines[i])
        i += 1
    flush()
    return out


# Leading LEGACY writeback-hook prefix: "**2026-06-08** (session f1f7201b): ".
# No live writer produces this FRONT-prefix shape any more -- it is retained ONLY
# so pre-existing archive bullets written in that shape keep the same signature
# (the old TRAILING-only dedup never matched this front-prefix → 43K dups).
# Session-id class is deliberately broad ([0-9a-fA-F-]+): 8-hex covers 100% of the
# real corpus today, but a future context.session_id[:N] slice (dashed UUID /
# longer) must not silently survive the strip and break cross-format dedup.
_WRITEBACK_PREFIX_RE = re.compile(
    r"^\*\*\d{4}-\d{2}-\d{2}\*\*\s*\(session\s+[0-9a-fA-F-]+\)\s*:\s*", re.IGNORECASE
)
# Leading "[type] " classification marker (entry form: "- [pitfall] **T**").
_TYPE_PREFIX_RE = re.compile(r"^\[[a-z]+\]\s*", re.IGNORECASE)


def content_signature(line: str) -> str:
    """Format-AGNOSTIC dedup signature for a knowledge-entry bullet.

    Two bullet shapes exist in DDD docs and archives:
      - typed entry:      ``- [type] **Title** — text (YYYY-MM-DD, run_x, label)``
      - legacy writeback: ``- **YYYY-MM-DD** (session xxxxxxxx): text``
    Both must reduce to the SAME signature for the same lesson so a lesson
    written in one shape dedups against the other (stripping only the TRAILING
    attribution left the writeback FRONT-prefix in place, so the signatures never
    collided — a no-op on the 43K-dup corpus).

    Normalization (whole-string, NOT first-N-chars — a prefix cut would
    false-merge distinct lessons that share an opening stem, Gate-1 #2):
      1. strip leading ``- `` bullet marker
      2. strip the writeback ``**date** (session id):`` FRONT prefix
      3. strip the trailing ``(date, run, label)`` attribution
      4. strip a leading ``[type]`` marker
      5. drop ``**`` bold markers, lowercase, collapse all whitespace to single spaces

    Deterministic + pure. Empty string in → empty string out.
    """
    text = line.lstrip()
    if text.startswith("- "):
        text = text[2:]
    text = text.strip()
    # 2. writeback front-prefix (the load-bearing strip)
    text = _WRITEBACK_PREFIX_RE.sub("", text)
    # 3. trailing attribution
    m = re.search(r"\s*\((?:\d{4}-\d{2}-\d{2}|[0-9a-f]{6,}|run_)[^)]*\)\s*$", text)
    if m:
        text = text[: m.start()]
    # 4. leading [type] marker
    text = _TYPE_PREFIX_RE.sub("", text)
    # 5. drop bold markers, lowercase, collapse whitespace
    text = text.replace("**", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


_ANY_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def unit_id(text: str) -> str:
    """The identity of one recall unit (an entry or a prose block): 8 hex characters.

    The same for every copy of the unit wherever it is read -- a recall hit, a
    ``knowledge find`` result, a line of the doc on disk -- and stable across the
    rewrites nobody would call an edit: comments are removed first (the metadata
    line, whose dead ``ref:… | last:… | decay:…`` prefix a migration removes, and the
    ``<!-- maturity: … -->`` marker), then an entry is reduced by
    ``content_signature`` (bullet, writeback prefix, ``[type]``, bold,
    trailing attribution, case, whitespace) -- the same rule the write-side dedup uses
    for "the same lesson" -- and a block's whitespace is collapsed and lowercased.
    Hashing the raw text instead changed the id each time a metadata line changed.
    Knowledge addressing design §2.1."""
    t = _ANY_COMMENT_RE.sub(" ", text or "").strip()
    if t.startswith("- "):
        norm = content_signature(t)
    else:
        norm = re.sub(r"\s+", " ", t).strip().lower()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:8]


def parse_entries(content: str, *, include_prose: bool = False,
                  skip_h3_spans: bool = False) -> list[EntryMetadata]:
    """Parse all knowledge entries from DDD markdown content.

    Extracts entries from bullet lists (- **Title** ...) with optional
    [type] prefix and optional inline metadata comment.

    skip_h3_spans=True: lines inside a ``###`` block produce no entries. Only the
    EVOLUTION.md sweeps pass it — there a ``### Cnnn`` / ``### CLASS`` block is one
    record whose field bullets are not entries of their own (§3.4). Default False
    leaves every other caller unchanged.

    include_prose=False (DEFAULT): narrow matcher — the shape every autonomous
    lifecycle path (inject/decay/reclaim) must use, so emoji-prefixed curated
    prose bullets are NOT treated as decay-managed entries (never stamped/stripped
    by background jobs). include_prose=True (no production caller since retire went) so
    a curator can name a hand-curated Open-Threads bullet deliberately.

    Returns list of EntryMetadata in document order.
    """
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    entries: list[EntryMetadata] = []
    current_section = ""
    skip = _h3_span_lines(lines) if skip_h3_spans else frozenset()
    i = 0

    while i < len(lines):
        line = lines[i]

        # Track section headers
        if line.startswith("## ") and not line.startswith("### "):
            current_section = line[3:].strip()
            i += 1
            continue

        if i in skip:
            i += 1
            continue

        # Check for entry bullet
        m = _match_entry_line(line, include_prose=include_prose)
        if m:
            entry_type = m.group(1) or ""
            title = m.group(2)

            # Extract created_date from entry text
            created_date = _extract_date(line)

            text_end, meta_idx, span_end = _scan_entry(lines, i)
            raw_text = "\n".join(lines[i:text_end])

            # Check for metadata comment on next line
            ref_count = 0
            last_referenced = None
            decay_state = "active"
            valid_until = None
            superseded_by = None

            source = ""
            taint = ""
            if meta_idx is not None:
                meta_match = _META_RE.match(lines[meta_idx])
                if meta_match:
                    ref_count = int(meta_match.group(1) or 0)
                    last_str = meta_match.group(2)
                    if last_str and last_str != "none":
                        try:
                            last_referenced = date.fromisoformat(last_str)
                        except ValueError:
                            last_referenced = None
                    decay_state = meta_match.group(3) or "active"
                    source = meta_match.group(4) or ""
                    taint = meta_match.group(5) or ""
                    vu_str = meta_match.group(6)
                    if vu_str:
                        try:
                            valid_until = date.fromisoformat(vu_str)
                        except ValueError:
                            valid_until = None
                    sup_raw = meta_match.group(7)
                    superseded_by = sup_raw.strip() if sup_raw else None
                    # `null` sentinel (memory_index idiom) means not-superseded
                    if superseded_by == "null":
                        superseded_by = None

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
                taint=taint,
                valid_until=valid_until,
                superseded_by=superseded_by,
                end_line=span_end - 1,
            ))
            i = span_end
        else:
            i += 1

    return entries


# Types an automated writer never auto-writes into MEMORY (route_lesson_type): a
# principle, correction, decision or model goes through its own review path.
_KEEP_TYPES = frozenset({"principle", "correction", "decision", "model"})



def route_lesson_type(lesson: str) -> "tuple[str | None, str]":
    """Shared type-routing decision for auto-written MEMORY lessons.

    ONE source of truth for "which MEMORY section does this lesson belong to,
    and may it be auto-written at all?" — used by BOTH the distillation hook
    (cross-day recurring themes) and context_health (single-run REFLECT lessons)
    so the KEEP_TYPES hold-back rule can never drift between the two writers.

    Returns ``(section, entry_type)``:
      • ``entry_type`` — the author-declared leading [type] tag if valid (one of
        the 7 VALID_TYPES), else the classify_entry_type keyword guess.
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
    # run_4ad5a44b: HONOR an author-declared leading [type] tag as the PRIMARY
    # signal — the type was KNOWN at author-time (REFLECT lessons carry a
    # "[pitfall]"/"[principle]" prefix), so re-guessing it from prose keywords
    # is strictly worse (measured 82% keyword accuracy, with a systematic skew
    # that demotes principle/correction -> pitfall/guideline because the pitfall
    # signal words bug/wrong/failed are greedy). Honoring it HERE makes every
    # caller consistent (P8) with no caller change. classify_entry_type stays the
    # fallback for untagged lessons (the DailyActivity distillation path, which has
    # no author tag).
    _m = _DECLARED_TYPE_RE.match(lesson)
    declared = _m.group(1).lower() if _m else None
    etype = declared if (declared in VALID_TYPES) else classify_entry_type(lesson)
    if etype in _KEEP_TYPES:
        return (None, etype)
    return (MEMORY_TYPE_TO_SECTION.get(etype, "Guidelines"), etype)


def reclaim_duplicate_entries(
    content: str,
    today: date,
    project_dir: Path,
    *,
    evergreen_sections: "frozenset[str] | set[str] | None" = None,
    archive_name: str = "IMPROVEMENT-archive.md",
    source_path: "Path | None" = None,
    skip_h3_spans: bool = False,
) -> ReclaimReport:
    """Select ALREADY-ACCUMULATED EXACT duplicates (read-only).

    Distinct from the two adjacent mechanisms — this fills a real gap:
      • a write-side ``content_signature`` dedup only blocks a NEW write
        against existing entries — it never cleans the backlog already on disk.
    This sweeps the backlog: group all entries by ``content_signature`` (the
    format-agnostic normalizer above, so a lesson dedups identically whichever
    shape it was written in), and within each collision group
    keep ONE survivor; the rest are the candidates.

    Survivor selection (deterministic): highest ``ref_count``, tie → newest
    ``created_date`` (a date-less entry sorts oldest). The survivor is the entry
    most likely to be the canonical/most-referenced copy.

    NOT near-dup / similarity: signatures must be EXACTLY equal after normalization
    (strip bullet marker, [type] tag, attribution, bold, whitespace, case). This is
    deliberate — the knowledge governance rule is "never delete on a guess";
    similarity-based deletion is a non-goal.

    It writes nothing (CDF stage B, design 2026-10-26 CDF §8): the candidates feed
    the C&M entry-health counts. *project_dir*, *archive_name* and *source_path* are
    unused, kept for the callers' signature.
    """
    report = ReclaimReport()
    if not content or not content.strip():
        return report

    entries = parse_entries(content, skip_h3_spans=skip_h3_spans)
    if not entries:
        return report

    # ONE duplicate rule (design 2026-10-25-memory-write-entry-dedup §3.2): two
    # entries are duplicates iff they sit in the SAME section and have the same
    # content_signature. Every type is eligible: a second copy of a decision in the
    # same section carries nothing the first lacks, and section scoping keeps a
    # deliberate cross-section repeat. Only the COE Registry is excluded — it is a
    # keyed record (one entry per COE topic, frozen at the CDF cutover), not a list.
    def _dedup_excluded(e: EntryMetadata) -> bool:
        return e.section == "COE Registry"

    def _sig(e: EntryMetadata) -> str:
        # raw_text already includes the leading "- " bullet marker; do NOT prepend
        # another (a double "- - " leaves a residual "- " that content_signature's
        # single-marker strip can't remove, poisoning the signature). Fall back to
        # a synthesized bullet only when raw_text is empty. raw_text is the entry
        # text (lead + continuation) and ends before the metadata line, so the
        # Detail/valid_from tail and lifecycle metadata never enter the signature.
        raw = e.raw_text if (e.raw_text and e.raw_text.lstrip().startswith("- ")) else "- " + (e.raw_text or e.title)
        return content_signature(raw)

    groups: dict[tuple[str, str], list[EntryMetadata]] = {}
    for entry in entries:
        if _dedup_excluded(entry):
            continue
        sig = _sig(entry)
        if not sig:
            continue
        groups.setdefault((entry.section, sig), []).append(entry)

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

    return report


# ── Stacked-Metadata Heal (R-1, run_55c02bbe) ─────────────────────────────────


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
