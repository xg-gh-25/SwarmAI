"""
DDD Cultivation Engine — Tiered Autonomy Model.

Connects Pipeline REFLECT output to DDD documents with graduated autonomy:
- ADDITIVE changes (new lessons, patterns): auto-applied, logged to changelog
- RETIRE (evidence-driven delete/rewrite, run_ecc7a32b): a HIGH-CONFIDENCE
  supersession (unambiguous non-keep-class locate) AUTO-APPLIES reversibly
  (retire_entry: archive+bak+strip), capped at MAX_AUTO_RETIRES_PER_RUN; a
  borderline / close-runner-up / keep-class one is escalated via the proposal queue
- RISKY appends (protected zones, conversation-derived): escalated via proposal queue

Zero LLM calls — pure keyword heuristic filtering.

Public API:
    CultivationProposal  — data model for a single proposal
    filter_lessons_for_ddd(lessons, run_id, project[, project_dir]) → List[CultivationProposal]
    apply_to_ddd(proposal, project_dir) → str (applied|duplicate|section_not_found|not_safe|doc_missing|locked)
    apply_retire_proposal(proposal, project_dir) → str (retired|rewritten|no_target|doc_missing|retire_failed:…)
    log_application(proposal, project_dir) → None
    write_proposal(proposal, project_dir) → Path  (escalation path only)
    read_pending_proposals(workspace_dir, project) → List[CultivationProposal]
    cultivate_from_reflect(lessons, run_id, project, project_dir) → dict
    cultivate_from_corrections(corrections, session_id, project, project_dir) → dict
    cultivate_from_decisions(decisions, session_id, project, project_dir) → dict
"""

import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

# Maximum proposals generated per pipeline run (prevents noise)
MAX_PROPOSALS_PER_RUN = 5

# Maximum AUTONOMOUS retires (delete/rewrite) applied per cultivate call
# (run_ecc7a32b). Bounds the blast radius of confident auto-retire within one
# _cultivate_proposals invocation. Retire is reversible (archive+bak), so a wrong
# auto-delete is recoverable — but a low cap keeps volume sane + auditable.
MAX_AUTO_RETIRES_PER_RUN = 2

# SESSION/DAY-wide cap across ALL entrypoints (Gate-2 #3, run_ecc7a32b): the
# per-call cap above is insufficient — cultivate_from_reflect + _corrections +
# _decisions each call _cultivate_proposals separately, so one session could
# auto-delete 2×3=6 entries. This module-level counter, keyed by (project, UTC
# date), bounds the TOTAL autonomous retires per project per day across every
# entrypoint, and self-resets daily. The per-call cap still applies (whichever
# is hit first). A wrong auto-delete is reversible, but the user is not notified
# on success — so the true blast-radius ceiling must be honest, not per-call.
MAX_AUTO_RETIRES_PER_DAY = 3
_auto_retire_ledger: dict[tuple[str, str], int] = {}


def _auto_retire_budget_remaining(project_dir: Path) -> int:
    """Remaining session/day-wide auto-retire budget for this project (Gate-2 #3).

    Keyed by (project name, UTC date) in a module-level ledger — shared across
    cultivate_from_reflect/_corrections/_decisions within one process/day, and
    self-resetting when the date rolls. Returns how many more autonomous retires
    are permitted today; 0 → all further confident retires escalate instead.
    """
    project = Path(project_dir).name
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    used = _auto_retire_ledger.get((project, today), 0)
    return max(0, MAX_AUTO_RETIRES_PER_DAY - used)


def _record_auto_retire(project_dir: Path) -> None:
    """Increment the session/day-wide auto-retire ledger (Gate-2 #3)."""
    project = Path(project_dir).name
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _auto_retire_ledger[(project, today)] = _auto_retire_ledger.get((project, today), 0) + 1

# Minimum lesson length to be considered for DDD promotion
MIN_LESSON_LENGTH = 30

# ── Routing: single source of truth in persist_routing.py ────────────────────
# Keywords, classification logic, and safe-append rules all live there.
# This module only imports what it needs for the auto cultivation path.
from core.persist_routing import (
    ROUTING_TABLE,
    classify_content,
    NOISE_PATTERNS,
)

logger = logging.getLogger(__name__)

# Derive SAFE_APPEND_SECTIONS from the routing table (single source of truth)
SAFE_APPEND_SECTIONS: dict[str, set[str]] = {}
for _route in ROUTING_TABLE.values():
    if _route["safe_auto"] and _route.get("section"):
        SAFE_APPEND_SECTIONS.setdefault(_route["doc"], set()).add(_route["section"])


@dataclass
class CultivationProposal:
    """A single proposal for DDD document enrichment.

    Created by the filter function from pipeline REFLECT output.
    For additive changes: auto-applied + logged.
    For risky changes: stored as JSON in .artifacts/proposals/ for escalation.
    """

    target_doc: str  # "IMPROVEMENT.md" | "TECH.md" | "PRODUCT.md" | "PROJECT.md"
    target_section: str  # e.g., "What Worked" or "Runtime Traps"
    content: str  # The proposed addition (1-3 sentences)
    source_run_id: str  # pipeline run that generated this
    confidence: float  # 0.0-1.0
    id: str = field(default_factory=lambda: f"proposal_{uuid.uuid4().hex[:8]}")
    source_stage: str = "reflect"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_days: int = 14
    status: str = "pending"  # pending | applied | rejected | expired | escalated
    # ── Evidence-driven DELETE/REWRITE (run_b8f10185; auto-apply run_ecc7a32b) ──
    # change_type discriminates the "out" side of the knowledge layer from the
    # default append. A "retire"/"rewrite" is REVERSIBLE (retire_entry: archive →
    # dated .bak → strip), so a HIGH-CONFIDENCE one (auto_apply_ok, set by a
    # unambiguous non-keep-class locate) AUTO-APPLIES up to MAX_AUTO_RETIRES_PER_RUN;
    # a borderline / close-runner-up / keep-class one ESCALATES to the human queue.
    # Either way it NEVER goes through the append applier (apply_to_ddd hard-refuses
    # non-append). Defaults to "append" everywhere for backward-compat.
    change_type: str = "append"  # "append" | "retire" | "rewrite"
    target_title: str = ""  # exact EntryMetadata.title of the entry to retire/rewrite
    evidence: str = ""  # verbatim quote proving the target is falsified/superseded
    replacement_content: str = ""  # rewrite only — new entry text (unused for retire)
    # run_ecc7a32b: a retire proposal from a HIGH-CONFIDENCE locate (unambiguous +
    # non-keep-class) may AUTO-APPLY (retire is reversible: archive+bak+strip).
    # False → the retire ESCALATES to the human queue (borderline / close runner-up
    # / keep-class). Only meaningful when change_type != "append".
    auto_apply_ok: bool = False

    def to_dict(self) -> dict:
        """Serialize to dict for JSON storage."""
        return {
            "id": self.id,
            "target_doc": self.target_doc,
            "target_section": self.target_section,
            "content": self.content,
            "source_run_id": self.source_run_id,
            "source_stage": self.source_stage,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "ttl_days": self.ttl_days,
            "status": self.status,
            "change_type": self.change_type,
            "target_title": self.target_title,
            "evidence": self.evidence,
            "replacement_content": self.replacement_content,
            "auto_apply_ok": self.auto_apply_ok,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CultivationProposal":
        """Deserialize from dict.

        change_type/target_title/evidence/replacement_content default to the
        append-shape on OLD proposal JSON (pre-run_b8f10185) that lacks the keys —
        so any existing on-disk proposal round-trips as a plain append (AC6).
        """
        return cls(
            id=data["id"],
            target_doc=data["target_doc"],
            target_section=data["target_section"],
            content=data["content"],
            source_run_id=data["source_run_id"],
            source_stage=data.get("source_stage", "reflect"),
            confidence=data["confidence"],
            created_at=data["created_at"],
            ttl_days=data.get("ttl_days", 14),
            status=data.get("status", "pending"),
            change_type=data.get("change_type", "append"),
            target_title=data.get("target_title", ""),
            evidence=data.get("evidence", ""),
            replacement_content=data.get("replacement_content", ""),
            auto_apply_ok=data.get("auto_apply_ok", False),
        )

    def is_expired(self) -> bool:
        """Check if proposal has exceeded its TTL."""
        try:
            created = datetime.fromisoformat(self.created_at)
            now = datetime.now(timezone.utc)
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            return now > created + timedelta(days=self.ttl_days)
        except (ValueError, TypeError):
            return True  # Treat unparseable dates as expired (safe default)

    def is_safe_append(self) -> bool:
        """Determine if this proposal is a safe additive change (auto-apply).

        M2: authoritative zones (Architecture/Vision/Non-Goals/SELF.md) are NEVER
        auto-applied — they fall through to escalation (or full block for SELF.md).
        retire/rewrite are NOT appends → always False here; their auto-apply-vs-
        escalate decision lives on `auto_apply_ok` (run_ecc7a32b), handled by
        _cultivate_proposals via apply_retire_proposal — NOT this append gate.
        """
        if self.change_type != "append":
            return False  # retire/rewrite are destructive — never auto-apply
        if is_protected_zone(self.target_doc, self.target_section):
            return False  # authoritative zone — escalate, never auto-apply
        allowed = SAFE_APPEND_SECTIONS.get(self.target_doc)
        if allowed is None:
            return False  # PRODUCT.md, PROJECT.md changes need escalation
        return self.target_section in allowed


# M2: authoritative zones — auto-cultivation is STRUCTURALLY blocked from these.
# Only human edits / distillation may write here. (run_123a6530)
# Intentionally a SUPERSET of ddd_orchestrator._SEMANTIC_SECTIONS (Non-Goals,
# Vision, Architecture): cultivation also protects PRODUCT/Strategic Priorities
# and the whole resident SELF.md. Broader-here is safe (more escalation, never
# less). NOT a mirror — do not assume the two lists are identical.
_PROTECTED_ZONES: dict[str, set[str] | None] = {
    "SELF.md": None,  # None = whole doc protected (human/distill only)
    "PRODUCT.md": {"Vision", "Non-Goals", "Strategic Priorities"},
    "TECH.md": {"Architecture"},
}

# M2: instance-log / slip signatures — text that is an EVENT record, not a
# generalizable lesson. These must never be cultivated into DDD.
# ALL alternations are anchored to LINE-START (^\s*) so prose that merely
# *mentions* "completed in 4s" / "exits 0" / "returncode" mid-sentence is NOT
# wrongly dropped (adversarial: err toward accepting real lessons).
_INSTANCE_LOG_RE = re.compile(
    r"^\s*(stdout|stderr|exit|exit_code|EXIT)\s*[:=]"
    r"|^\s*run_[0-9a-f]{6,}\b"
    r"|^\s*\S+ completed in \d"      # a bare "<thing> completed in 4s" log line
    r"|^\s*exits? \d"                 # a bare "exit 1" / "exits 0" log line
    r"|^\s*returncode[:=\s]*\d",      # a bare "returncode: 0" log line
    re.IGNORECASE,
)

# First-person-SINGULAR / meta-cognition NARRATION — process-chatter, not a lesson.
# The session transcript is full of "I'll diagnose…", "let me check…", "now I'll…"
# — these keyword-match a lesson extractor but carry zero transferable knowledge.
# A real lesson states a fact/rule ("X breaks because Y"), not the author's intent.
#
# DELIBERATELY NARROW (Gate-2 finding C, run_d7cb3941): only first-person-SINGULAR
# intent + explicit meta-chatter. We do NOT reject "we should/need …" — plural
# imperatives are a legitimate, common lesson voice ("We should validate at the
# boundary"), and silent knowledge-loss is worse than the noise we filter (this
# module errs toward ACCEPTING). Anchored at START (a lesson may quote "…so I'll
# never do X again" mid-sentence; the tell is a fragment OPENING with narrator intent).
_NARRATION_RE = re.compile(
    r"^\s*(?:-\s*)?"
    r"(?:i['’]?(?:ll|m| will| have| need| think| want)\b"   # "I'll / I'm / I have / I need / I think / I want" (singular intent)
    r"|let me\b|let['’]?s\b"                                 # "let me / let's"
    r"|this crosses\b|enough to\b|now (?:i|let)\b"           # transcript chatter
    r"|(?:ok|okay|alright|great|perfect)[,!. ]"             # filler openers
    r"|going to\b|about to\b)",                             # "I'm going to / about to" leads
    re.IGNORECASE,
)


def is_protected_zone(target_doc: str, target_section: str) -> bool:
    """True if (doc, section) is an authoritative zone auto-cultivation must not touch."""
    if target_doc not in _PROTECTED_ZONES:
        return False
    sections = _PROTECTED_ZONES[target_doc]
    if sections is None:
        return True  # whole-doc protection (e.g. SELF.md)
    return target_section in sections


def is_quality_lesson(lesson: str) -> bool:
    """True if `lesson` is a generalizable, well-formed lesson worth cultivating.

    Rejects (M2 gate):
      - instance-logs / stdout fragments / run-id slips (event records, not lessons)
      - fragments lacking a complete sentence (no clause of real length)
    Errs toward ACCEPTING when ambiguous (knowledge loss > noise — like is_keep_class).
    """
    stripped = lesson.strip()
    if not stripped:
        return False
    # Reject instance-logs / slips outright.
    if _INSTANCE_LOG_RE.search(stripped):
        return False
    # Reject first-person / meta-cognition narration (process-chatter, not a lesson).
    # e.g. "I have enough to diagnose the root cause", "This crosses your threshold →
    # I'll diagnose…" — these keyword-match a lesson extractor but teach nothing.
    if _NARRATION_RE.search(stripped):
        return False
    # Require at least one "sentence": >= 5 words AND ends like prose OR is long.
    # A bare fragment ("done", "tests pass") has < 5 words and no sentence shape.
    words = stripped.split()
    if len(words) < 5:
        return False
    return True


def _classify_lesson(lesson: str, project: str = "SwarmAI") -> Optional[tuple]:
    """Classify a lesson into target_doc and target_section.

    Delegates to the unified classify_content() from persist_routing.py.
    Returns (target_doc, target_section, confidence) or None if rejected.
    """
    stripped = lesson.strip()

    # Reject empty or too short
    if len(stripped) < MIN_LESSON_LENGTH:
        return None

    # M2 quality gate: reject instance-logs / non-lesson fragments.
    if not is_quality_lesson(stripped):
        return None

    result = classify_content(stripped, project=project)

    # Governance content is not DDD — reject (handled by s_self-evolution)
    if result.get("is_governance"):
        return None

    # Low confidence = noise or no keyword match.
    # Threshold 0.35 ensures no-keyword content (confidence=0.3) is rejected here
    # but caught by the PE-1 fallback in cultivate_from_corrections (→ "What Failed").
    if result["confidence"] <= 0.3:
        return None

    return (result["doc"], result["section"], result["confidence"])


def filter_lessons_for_ddd(
    lessons: List[str],
    run_id: str,
    project: str,
    project_dir: "Path | None" = None,
) -> List[CultivationProposal]:
    """Filter pipeline REFLECT lessons into DDD cultivation proposals.

    Returns at most MAX_PROPOSALS_PER_RUN proposals. Classification is pure; the
    ONLY I/O is the optional supersession locator (reads the target doc to find
    the entry a superseding lesson refutes). Pass project_dir to enable evidence-
    driven retire proposals; omit it (or None) for the pure append-only behavior
    (backward-compatible with all existing callers).

    run_b8f10185: a lesson carrying explicit supersession language (_detect_
    supersession) whose target entry can be located (_locate_target_entry) becomes
    a change_type='retire' proposal — it will ESCALATE (never auto-apply) and
    retire the located entry only on human approve. Everything else → append.
    """
    proposals = []

    for lesson in lessons:
        if not lesson or not isinstance(lesson, str):
            continue

        classification = _classify_lesson(lesson, project=project)
        if classification is None:
            continue

        target_doc, target_section, confidence = classification

        # Evidence-driven retire: explicit supersession language AND a locatable
        # target entry. Fail-safe — no project_dir, no supersession marker, or no
        # located target → plain append. run_ecc7a32b: a HIGH-CONFIDENCE locate
        # (unambiguous + non-keep-class) sets auto_apply_ok → the retire AUTO-
        # APPLIES (reversible); a borderline/keep-class locate → auto_apply_ok
        # False → the retire ESCALATES to the human queue.
        change_type = "append"
        target_title = ""
        evidence = ""
        auto_apply_ok = False
        if project_dir is not None and _detect_supersession(lesson):
            located = _locate_target_entry(lesson, target_doc, project_dir)
            if located is not None:
                target_title, target_section, confident = located
                change_type = "retire"
                evidence = lesson.strip()
                auto_apply_ok = confident

        proposal = CultivationProposal(
            target_doc=target_doc,
            target_section=target_section,
            content=lesson.strip(),
            source_run_id=run_id,
            confidence=confidence,
            change_type=change_type,
            target_title=target_title,
            evidence=evidence,
            auto_apply_ok=auto_apply_ok,
        )
        proposals.append(proposal)

        if len(proposals) >= MAX_PROPOSALS_PER_RUN:
            break

    return proposals


def _extract_bullet_content(line: str) -> str:
    """Extract the lesson content from a cultivated bullet line, for duplicate
    detection. Cultivated entries have the shape:
        "- <content> (YYYY-MM-DD, run_xxx, label)"
    Strip the leading "- " and the trailing "(date, run, label)" attribution so
    duplicate matching compares the actual lesson text, not the attribution.
    Lines without the attribution suffix fall back to the de-bulleted text.
    """
    text = line.lstrip()
    if text.startswith("- "):
        text = text[2:]
    text = text.strip()
    # Remove a trailing attribution parenthetical: "(2026-..., run_..., ...)"
    m = re.search(r"\s*\((?:\d{4}-\d{2}-\d{2}|[0-9a-f]{6,}|run_)[^)]*\)\s*$", text)
    if m:
        text = text[: m.start()].strip()
    return text


# Leading LEGACY writeback-hook prefix: "**2026-06-08** (session f1f7201b): ".
# improvement_writeback_hook now routes through apply_to_ddd (cultivation format),
# so this FRONT-prefix shape is NO LONGER PRODUCED by any live writer — it is
# retained ONLY to dedup incoming lessons against the ~55K pre-existing archive
# bullets that were silted before the unification (the two writers' TRAILING-only
# dedup never matched this front-prefix → 43K dups; Gate-1 killer, run_4c5f81ce).
# Session-id class is deliberately broad ([0-9a-fA-F-]+): 8-hex covers 100% of the
# real corpus today, but a future context.session_id[:N] slice (dashed UUID /
# longer) must not silently survive the strip and break cross-format dedup.
_WRITEBACK_PREFIX_RE = re.compile(
    r"^\*\*\d{4}-\d{2}-\d{2}\*\*\s*\(session\s+[0-9a-fA-F-]+\)\s*:\s*", re.IGNORECASE
)
# Leading "[type] " classification marker (cultivation form: "- [pitfall] **T**").
_TYPE_PREFIX_RE = re.compile(r"^\[[a-z]+\]\s*", re.IGNORECASE)


def content_signature(line: str) -> str:
    """Format-AGNOSTIC dedup signature for a knowledge-entry bullet.

    The two IMPROVEMENT.md writers use different bullet shapes:
      - cultivation:      ``- [type] **Title** — text (YYYY-MM-DD, run_x, label)``
      - writeback hook:   ``- **YYYY-MM-DD** (session xxxxxxxx): text``
    Both must reduce to the SAME signature for the same lesson so a lesson
    written by one writer dedups against the other. This is the single fix that
    makes cross-writer dedup real (the old ``_extract_bullet_content`` stripped
    only the TRAILING attribution, so the writeback FRONT-prefix survived and the
    signatures never collided — a no-op on the 43K-dup corpus).

    Normalization (whole-string, NOT first-N-chars — a prefix cut would
    false-merge distinct lessons that share an opening stem, Gate-1 #2):
      1. strip leading ``- `` bullet marker
      2. strip the writeback ``**date** (session id):`` FRONT prefix
      3. strip the trailing ``(date, run, label)`` cultivation attribution
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
    # 3. trailing cultivation attribution (reuse the same pattern as _extract_bullet_content)
    m = re.search(r"\s*\((?:\d{4}-\d{2}-\d{2}|[0-9a-f]{6,}|run_)[^)]*\)\s*$", text)
    if m:
        text = text[: m.start()]
    # 4. leading [type] marker
    text = _TYPE_PREFIX_RE.sub("", text)
    # 5. drop bold markers, lowercase, collapse whitespace
    text = text.replace("**", " ")
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def apply_to_ddd(proposal: CultivationProposal, project_dir: Path) -> str:
    """Apply an additive proposal directly to the target DDD document.

    Appends a bullet point under the target section (newest first).
    Only works for safe_append sections (IMPROVEMENT.md and TECH.md).
    Uses fcntl advisory lock to prevent concurrent write corruption.

    Returns a status string (NOT a bool — callers must compare explicitly):
      - "applied"           — entry written under a pre-existing section heading
      - "created_section"   — the whitelisted section heading was ABSENT, so it
                              was auto-created at end-of-doc and the entry written
                              under it. This makes section-name drift structurally
                              harmless: a lesson is NEVER dropped just because the
                              doc heading is missing. The section name is TRUSTED
                              (sourced from ROUTING_TABLE via SAFE_APPEND_SECTIONS,
                              never user input), so creating it is safe. Surfaced
                              (logged) so latent drift is still visible.
                              (run_45ab67c7 root cause — structural fix.)
      - "duplicate"         — benign no-op, exact content already present
      - "not_safe"          — target doc/section not in SAFE_APPEND_SECTIONS
      - "doc_missing"       — target document file does not exist
      - "locked"            — another process holds the write lock (retry later)

    Note: "section_not_found" is NO LONGER returned — a missing whitelisted
    section is auto-created rather than treated as a drop. The drift is still
    observable via the "created_section" status (logged by callers).
    """
    # Defense-in-depth (run_b8f10185 HIGH-3): apply_to_ddd is the APPEND applier
    # only. A retire/rewrite must NEVER land here (it would append instead of
    # deleting) — regardless of whether it auto-applies or escalates, its apply
    # path is apply_retire_proposal, never this one. is_safe_append() already
    # returns False for non-append, but this hard refusal makes the invariant
    # independent of that classifier — belt + suspenders.
    if proposal.change_type != "append":
        return "not_safe"
    if not proposal.is_safe_append():
        return "not_safe"

    doc_path = project_dir / proposal.target_doc
    if not doc_path.exists():
        return "doc_missing"

    import fcntl

    # H1 fix: advisory lock prevents concurrent pipeline writes
    lock_path = doc_path.with_suffix(".lock")
    lock_fd = None
    try:
        lock_fd = open(lock_path, "w")
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        # Another process holds the lock — skip this write, it'll retry next run
        if lock_fd:
            lock_fd.close()
        return "locked"

    try:
        content = doc_path.read_text(encoding="utf-8")

        # M2 fix: match existing entry format — plain bullet with trailing attribution
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        source_label = f"{proposal.source_stage}" if proposal.source_stage != "reflect" else "auto-cultivated"
        entry = f"- {proposal.content} ({date_str}, {proposal.source_run_id}, {source_label})\n"

        # M1 fix: match section header at line start (## level only, not ###)
        section_re = re.compile(
            r"^## " + re.escape(proposal.target_section) + r"\s*$", re.MULTILINE
        )
        match = section_re.search(content)

        if match:
            # Compute the target section's text span [body_start, body_end) so the
            # duplicate check is SCOPED to this section, not the whole document.
            # (Adversarial HIGH: a whole-doc substring match dropped legit lessons
            # when the same text appeared in a DIFFERENT section, and dropped short
            # lessons that were substrings of a longer unrelated entry.)
            line_end = content.find("\n", match.start())
            if line_end == -1:
                line_end = len(content)
            body_start = line_end + 1
            while body_start < len(content) and content[body_start] == "\n":
                body_start += 1
            # Section body ends at the next '## ' heading (or EOF).
            next_h = re.compile(r"^## ", re.MULTILINE).search(content, body_start)
            body_end = next_h.start() if next_h else len(content)
            section_body = content[body_start:body_end]

            # Duplicate detection scoped to THIS section, matched on a
            # FORMAT-AGNOSTIC content signature (not raw substring, not
            # exact-string). content_signature() normalizes BOTH writer formats
            # (cultivation `- text (date,run)` AND writeback `- **date**
            # (session): text`) so a lesson written by either writer dedups
            # against the other. Signing BOTH sides — the existing bullets AND
            # the incoming content — is what makes it catch the 43K writeback-
            # format corpus (Gate-1: signing only one side is a no-op).
            existing_sigs = {
                content_signature(ln)
                for ln in section_body.splitlines()
                if ln.lstrip().startswith("- ")
            }
            existing_sigs.discard("")  # never dedup against empty (blank bullets)
            if content_signature("- " + proposal.content.strip()) in existing_sigs:
                return "duplicate"

            new_content = content[:body_start] + entry + content[body_start:]
            result_status = "applied"
        else:
            # Structural drift fix (run_45ab67c7): the whitelisted section is
            # absent (doc heading drifted from / never matched the routing table).
            # CREATE it at end-of-doc rather than DROP the lesson. The section
            # name is TRUSTED — is_safe_append() already confirmed the (doc,
            # section) PAIR is in SAFE_APPEND_SECTIONS (derived from ROUTING_TABLE),
            # not user input. Makes section-name drift structurally harmless across
            # ALL projects, not just ones whose headings happen to match.
            base = content.rstrip("\n")
            prefix = f"{base}\n\n" if base else ""
            new_content = f"{prefix}## {proposal.target_section}\n\n{entry}"
            result_status = "created_section"

        # Atomic write: write to temp, rename over original
        tmp_path = doc_path.with_suffix(".tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(str(tmp_path), str(doc_path))
        return result_status
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()
        # Clean up lock file (best effort)
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


# ── Evidence-driven supersession detection + retire (run_b8f10185; ────────────
# ── auto-apply run_ecc7a32b) ──────────────────────────────────────────────────
# The "out" side of cultivation: when a lesson explicitly says a PRIOR entry is
# now false/superseded, retire that entry instead of only appending a
# contradicting bullet. Conservative by construction: fires ONLY on explicit
# supersession LANGUAGE (never similarity/embeddings — "do-not-delete-on-guesses"),
# locates the target by EXACT parsed (title, section). A HIGH-CONFIDENCE locate
# (unambiguous + non-keep-class) AUTO-APPLIES the retire (reversible: archive+bak+
# strip), capped at MAX_AUTO_RETIRES_PER_RUN; a borderline / close-runner-up /
# keep-class locate ESCALATES to the human queue.
#
# Scope (run_b8f10185 BLOCKER-2, verified): supersession detection is wired ONLY
# into the filter_lessons_for_ddd (reflect/correction/decision-lesson) path. The
# other 5 CultivationProposal producers (signal_ddd_bridge, code_intel_feed,
# code_change_feed, ddd_orchestrator, conversation) stay append-only — they never
# get retire detection. This is deliberate: the reflect path is where a human-
# authored "X was wrong, now Y" lesson arrives; the feed producers emit additive
# drift signals, not supersession claims.
#
# Target format (run_b8f10185 BLOCKER-1, verified): retire_entry matches entries
# with a **bold title** (_ENTRY_RE / _ENTRY_RE_PROSE). It therefore can retire
# human/distill-authored **Title** entries and curated prose bullets — NOT the
# plain-bullet entries apply_to_ddd itself writes (those have no bold title and are
# invisible to parse_entries). _locate_target_entry uses parse_entries(include_
# prose=True) so it returns the SAME exact (title, section) retire_entry will match.

# Explicit supersession/contradiction language. Anchored to whole words; requires
# an explicit "this replaces/invalidates prior knowledge" signal, NOT mere novelty.
_SUPERSESSION_RE = re.compile(
    r"(?i)("
    r"\bno longer\b|\bused to\b|"
    r"\breplaces?\b|\bsupersed(?:e|es|ed)\b|\bobsoletes?\b|\bwas wrong\b|"
    r"\bpreviously .*\bnow\b|"
    r"\bdeprecat(?:e|es|ed)\b|\bcontradicts?\b|"
    r"不再|已废弃|取代|已过时|已失效|之前.*现在"
    r")"
    # Gate-2 MED (run_b8f10185): the bare "now (does|is|uses|instead)" branch was
    # REMOVED — it false-positived on benign additive prose ("the parser now does
    # full validation", "the API now is stricter"). Supersession requires an
    # EXPLICIT prior-referent (no longer / replaces / was wrong / superseded),
    # not mere present-tense novelty. "previously … now …" is kept (explicit
    # prior-vs-now contrast).
)


def _detect_supersession(lesson: str) -> bool:
    """True iff the lesson carries EXPLICIT supersession/contradiction language.

    Conservative: this is the ONLY trigger for a retire proposal. Additive lessons
    (no supersession marker) → False → normal append path (AC4). Zero LLM, zero
    similarity — a lesson must SAY it invalidates prior knowledge, not merely look
    similar to an existing entry.
    """
    if not lesson or not isinstance(lesson, str):
        return False
    return _SUPERSESSION_RE.search(lesson) is not None


def _locate_target_entry(
    lesson: str, target_doc: str, project_dir: Path
) -> "tuple[str, str, bool] | None":
    """Locate the existing entry a superseding lesson refutes.

    Returns (title, section, confident) — the EXACT (title, section) parsed from
    the target doc (the identity retire_entry matches on) plus a CONFIDENCE flag —
    or None if no candidate clears the locate floor.

    Two thresholds on ONE overlap score (run_ecc7a32b, deliberately NOT a 3-way
    taxonomy — the engine already hard-refuses ambiguous-identity and keep-class,
    so this layer only decides auto-vs-escalate for a located entry):

      • locate floor (return non-None): ≥2 non-generic overlap AND ≥50% coverage.
      • confident=True (caller may AUTO-retire, reversible): ≥3 overlap AND ≥60%
        coverage AND a clear MARGIN over the 2nd-best candidate (≥2) AND the target
        is NOT keep-class. The margin is the ONE check that buys real safety — the
        token scorer (unlike retire_entry's exact-identity match) CAN confuse two
        distinct-titled entries, so "clearly THIS one, not the runner-up" is what
        separates auto from escalate.
      • confident=False → caller ESCALATES to the human queue (borderline, close
        runner-up, or keep-class). Keep-class routing here is UX (avoid a loud
        retire_failed) — SAFETY is already guaranteed by retire_entry's force=False
        refusal, verified Gate-0 (ddd_entry_lifecycle.py:1128).

    Scoring: keyword overlap between the lesson and each parsed entry's title.
    Uses parse_entries(include_prose=True) so both **bold** entries and curated
    prose bullets are candidates. Fail-safe: no doc/entries/overlap → None → append.
    """
    doc_path = project_dir / target_doc
    if not doc_path.exists():
        return None
    try:
        from core.ddd_entry_lifecycle import (
            parse_entries, is_keep_class, MEMORY_EVERGREEN_SECTIONS,
        )
        content = doc_path.read_text(encoding="utf-8")
    except (OSError, ImportError, UnicodeDecodeError):
        return None

    entries = parse_entries(content, include_prose=True)
    if not entries:
        return None

    # Tokenize into lowercase word-stems ≥4 chars (Latin) or ≥2 chars (CJK),
    # MINUS generic DDD/engineering vocabulary that co-occurs across unrelated
    # entries. Gate-2 HIGH (run_b8f10185): without this filter, a lesson about
    # topic A retires an entry about topic B on 2 shared structural words (e.g.
    # "recovery"+"pattern", "fix"+"works"). These carry no topic identity.
    _GENERIC = frozenset({
        "pattern", "patterns", "recovery", "approach", "works", "worked",
        "fix", "fixes", "fixed", "issue", "issues", "problem", "problems",
        "code", "test", "tests", "session", "sessions", "error", "errors",
        "gate", "check", "checks", "path", "paths", "case", "cases", "data",
        "system", "state", "value", "values", "call", "calls", "func", "function",
        "method", "class", "field", "fields", "change", "changes", "update",
        "using", "used", "when", "with", "that", "this", "from", "into", "must",
        "should", "would", "never", "always", "before", "after", "than", "then",
    })

    def _tokens(text: str) -> set[str]:
        return {
            w for w in re.findall(r"[a-zA-Z_]{4,}|[一-鿿]{2,}", text.lower())
            if w not in _GENERIC
        }

    lesson_tokens = _tokens(lesson)
    if not lesson_tokens:
        return None

    # Document-frequency of each title token across ALL entries in the doc.
    # Gate-2 #1 (run_ecc7a32b): the hand-maintained _GENERIC denylist is provably
    # incomplete — a title of non-denylisted-but-structural words ("stage layer
    # module logic") auto-deleted on a coincidental phrase. A token that appears
    # in MANY entry titles carries no identifying power REGARDLESS of the denylist.
    # So confidence additionally requires a DISTINGUISHING token: one the lesson
    # shares with the target whose doc-frequency is 1 (unique to this entry's
    # title). This is denylist-independent and derived from the real corpus.
    _title_tok_lists = [_tokens(e.title) for e in entries]
    _doc_freq: dict[str, int] = {}
    for _tt in _title_tok_lists:
        for _w in _tt:
            _doc_freq[_w] = _doc_freq.get(_w, 0) + 1

    best_entry: "EntryMetadata | None" = None
    best_title_tokens: set[str] = set()
    best_score = 0
    best_ratio = 0.0
    second_score = 0  # 2nd-best overlap — margin gates auto-retire (Gate-0)
    for e, title_tokens in zip(entries, _title_tok_lists):
        if not title_tokens:
            continue
        overlap = len(lesson_tokens & title_tokens)
        # Ratio guards against a long lesson coincidentally overlapping a short
        # title on a couple of topic words: at least HALF the entry's topic
        # tokens must be present in the lesson.
        ratio = overlap / len(title_tokens)
        if overlap > best_score or (overlap == best_score and ratio > best_ratio):
            second_score = best_score  # demote former best to runner-up
            best_score = overlap
            best_ratio = ratio
            best_entry = e
            best_title_tokens = title_tokens
        elif overlap > second_score:
            second_score = overlap

    # Locate floor: ≥2 non-generic overlap AND ≥50% coverage. Below → None → append
    # (conservative: never target an entry on a weak guess).
    if best_entry is None or best_score < 2 or best_ratio < 0.5:
        return None

    # Confident (auto-retire eligible): strong overlap + strong coverage + clear
    # margin over the runner-up + NOT keep-class. Any miss → escalate (confident
    # =False). Keep-class → escalate (UX: retire_entry would refuse force=False).
    #
    # Gate-2 #1 (run_ecc7a32b): a DISTINGUISHING token — one the lesson shares
    # with the target whose doc-frequency is 1 (unique to this entry's title
    # across the whole doc). Without it, a title made of structural words shared
    # doc-wide ("stage layer module logic") auto-deletes on a coincidental phrase.
    # A unique shared token proves the lesson is about THIS entry, not a generic
    # collision — denylist-independent, derived from the corpus. Prose/curated
    # entries (which parse_entries yields with include_prose) participate too.
    has_distinguishing = any(
        _doc_freq.get(w, 0) == 1 for w in (lesson_tokens & best_title_tokens)
    )

    # Gate-2 (run_ecc7a32b): is_keep_class MUST receive MEMORY_EVERGREEN_SECTIONS
    # — the SAME strict default retire_entry uses (ddd_entry_lifecycle.py:1127).
    # Without it, is_keep_class's rule-1 (evergreen SECTION) is silently dead
    # here, so a guideline-TYPE entry in an evergreen section (Open Threads /
    # Standing Preferences) would be marked confident → auto-apply → retire_entry
    # then REFUSES it (retire_failed) → needless escalation + a scary log line
    # every run. Passing the evergreen set makes the confidence gate agree with
    # the engine: such an entry is keep-class → confident=False → escalate cleanly.
    confident = (
        best_score >= 3
        and best_ratio >= 0.6
        and (best_score - second_score) >= 2
        and has_distinguishing
        and not is_keep_class(best_entry, evergreen_sections=MEMORY_EVERGREEN_SECTIONS)
    )
    return (best_entry.title, best_entry.section, confident)


def apply_retire_proposal(proposal: CultivationProposal, project_dir: Path) -> str:
    """Apply a retire/rewrite proposal via the reversible retire_entry
    machinery (archive → dated .bak → identity-strip). The sibling of apply_to_ddd
    for the "out" side. Two callers (run_ecc7a32b): (1) _cultivate_proposals for a
    HIGH-CONFIDENCE (auto_apply_ok) retire — autonomous, reversible; (2) the approve
    router for a human-approved escalated retire. retire_entry is fail-loud (no
    match / ambiguous / keep-class → RetireError, NO strip) so both callers are safe.

    Returns a status string (parallel to apply_to_ddd):
      - "retired"      — the named (title, section) entry was archived + stripped
      - "rewritten"    — retired, then replacement_content appended (rewrite)
      - "not_retire"   — proposal.change_type is not retire/rewrite (guard)
      - "no_target"    — target_title empty (locator found nothing — never retire)
      - "doc_missing"  — target document file does not exist
      - "retire_failed:<reason>" — retire_entry raised RetireError (fail-loud:
                         no match / ambiguous duplicate / keep-class refused)
      - "rewrite_partial:<status>" — retire succeeded but replacement append didn't
                         (original recoverable from archive + .bak)

    For rewrite the replacement is a NEW append (not a cross-file move): we retire
    the stale entry, then append the replacement via apply_to_ddd. If the append
    fails the retire already archived the original (recoverable), so no
    unrecoverable loss — but we surface the partial status.
    """
    if proposal.change_type not in ("retire", "rewrite"):
        return "not_retire"
    if not proposal.target_title:
        return "no_target"  # locator found nothing — refuse to guess

    doc_path = project_dir / proposal.target_doc
    if not doc_path.exists():
        return "doc_missing"

    from core.ddd_entry_lifecycle import retire_entry, RetireError

    # Archive to the doc-matched archive (BLOCKER-1: retire_entry defaults to
    # IMPROVEMENT-archive.md; a TECH.md retire must archive to TECH-archive.md).
    stem = Path(proposal.target_doc).stem  # "IMPROVEMENT" | "TECH" | ...
    archive_name = f"{stem}-archive.md"

    try:
        content = doc_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return f"retire_failed:read_error {type(e).__name__}"

    try:
        retire_entry(
            content,
            proposal.target_title,
            proposal.target_section,
            project_dir,
            archive_name=archive_name,
            source_path=doc_path,
            dry_run=False,
        )
    except RetireError as e:
        # Fail-loud: no match / ambiguous / keep-class refused. Surface, don't strip.
        return f"retire_failed:{str(e)[:120]}"

    if proposal.change_type == "rewrite" and proposal.replacement_content.strip():
        # Append the corrected entry via the normal safe-append applier. Build an
        # append-shaped proposal so is_safe_append + the change_type guard pass.
        replacement = CultivationProposal(
            target_doc=proposal.target_doc,
            target_section=proposal.target_section,
            content=proposal.replacement_content.strip(),
            source_run_id=proposal.source_run_id,
            confidence=proposal.confidence,
            source_stage=proposal.source_stage,
            change_type="append",
        )
        append_status = apply_to_ddd(replacement, project_dir)
        if append_status in ("applied", "created_section"):
            return "rewritten"
        # Retire succeeded but append didn't — original is in archive + .bak.
        return f"rewrite_partial:{append_status}"

    return "retired"


def log_application(
    proposal: CultivationProposal, project_dir: Path, *, created_section: bool = False
) -> None:
    """Log an applied change to the DDD changelog (append-only JSONL).

    Changelog is used by the weekly report to summarize DDD changes.

    created_section: when True, records that the target section heading was
    absent and auto-created (drift auto-healed). This keeps the drift signal in
    the DURABLE record — not just a transient log line — so the weekly report
    can surface "N sections auto-created (reconcile templates)" instead of drift
    recurring silently every run. (Adversarial observability MED.)
    """
    changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
    changelog_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "id": proposal.id,
        "action": "applied",
        "created_section": created_section,
        "target_doc": proposal.target_doc,
        "target_section": proposal.target_section,
        "content": proposal.content[:200],
        "source_run_id": proposal.source_run_id,
        "source_stage": proposal.source_stage,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(changelog_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_proposal(proposal: CultivationProposal, project_dir: Path) -> Path:
    """Write a proposal as an atomic JSON file (escalation path).

    Used only for RISKY changes that need human approval.
    Creates .artifacts/proposals/ directory if needed.
    """
    proposals_dir = project_dir / ".artifacts" / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{proposal.id}_{ts}.json"
    filepath = proposals_dir / filename

    # Atomic write
    content = json.dumps(proposal.to_dict(), indent=2, ensure_ascii=False)
    fd, tmp_path = tempfile.mkstemp(dir=str(proposals_dir), suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        os.rename(tmp_path, str(filepath))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if Path(tmp_path).exists():
            os.unlink(tmp_path)
        raise
    return filepath


def read_pending_proposals(
    workspace_dir: Path, project: str
) -> List[CultivationProposal]:
    """Read all proposals AWAITING HUMAN DECISION for a project.

    These are RISKY changes awaiting human approval (escalations). Both
    ``pending`` and ``escalated`` are awaiting-human states — ``write_proposal``
    (the escalate path) persists ``status="escalated"``, so filtering to
    ``pending`` alone would make EVERY escalated proposal invisible to the
    approval UX (router GET /proposals + briefing L5), a silent black hole.
    Terminal states (``applied`` / ``rejected`` / ``expired``) are excluded.
    (run_e346b8ed: capability C's human-gate depends on escalated proposals
    surfacing; this also fixes the same latent bug for reflect/decision
    escalations.)
    """
    proposals_dir = workspace_dir / "Projects" / project / ".artifacts" / "proposals"

    if not proposals_dir.exists():
        return []

    _AWAITING_HUMAN = {"pending", "escalated"}
    pending = []
    for filepath in proposals_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text())
            proposal = CultivationProposal.from_dict(data)

            if proposal.status not in _AWAITING_HUMAN:
                continue
            if proposal.is_expired():
                continue

            pending.append(proposal)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # Deduplicate by (target_doc, content)
    seen = set()
    deduped = []
    for p in pending:
        key = (p.target_doc, p.content.strip())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    deduped.sort(key=lambda p: p.confidence, reverse=True)
    return deduped


def _cultivate_proposals(
    proposals: List[CultivationProposal], project_dir: Path
) -> dict:
    """Apply or escalate a list of proposals. Shared by all cultivate_from_* entry points.

    Auto-approval gate (ddd_auto_approval) adds maturity, magnitude, precision,
    circuit breaker, and conflict checks on top of is_safe_append().

    Returns:
        {"applied": N, "escalated": M, "rejected": K, "retired": R, "drift_errors": [...]}

    drift_errors surfaces section-name drift LOUDLY (a config bug where a
    whitelisted routing section has no matching heading in the doc) instead of
    silently counting it as a benign "rejected". See apply_to_ddd docstring /
    run_45ab67c7 root cause.

    run_ecc7a32b: a HIGH-CONFIDENCE retire proposal (auto_apply_ok) AUTO-APPLIES
    via apply_retire_proposal (reversible: archive+bak+strip), up to
    MAX_AUTO_RETIRES_PER_RUN; beyond the cap, or if not confident, it ESCALATES.
    """
    applied = 0
    escalated = 0
    rejected = 0
    retired = 0
    drift_errors: List[str] = []

    for proposal in proposals:
        # 宁缺毋滥 — conversation-derived knowledge is NEVER auto-written
        # (capability C, run_e346b8ed). The "settled decision vs chatter?"
        # judgment is an LLM call UPSTREAM (conversation_extract); the
        # STRUCTURAL guarantee that a wrong judgment can't silently land in DDD
        # lives HERE: force this source down the escalate branch regardless of
        # target section, so it always requires XG's approve-time confirmation
        # (routers/cultivation.py). A safe-append target would otherwise
        # auto-apply and silently defeat the human-gate. DEC19: False > Stale >
        # Imperfect — a wrong DDD entry is worse than a missing one.
        if proposal.source_stage == "conversation":
            proposal.status = "escalated"
            write_proposal(proposal, project_dir)
            escalated += 1
            continue
        # Evidence-driven RETIRE (run_ecc7a32b): confident + under the per-run cap
        # → auto-apply (reversible). Not confident, or cap reached → escalate. The
        # cap bounds blast radius. retire NEVER goes through the append branch
        # below — apply_to_ddd hard-refuses it (defense-in-depth).
        if proposal.change_type in ("retire", "rewrite"):
            # Auto-apply eligibility (all must hold):
            #  - confident locate (auto_apply_ok)
            #  - change_type == "retire" ONLY. rewrite ALWAYS escalates (Gate-2 #5):
            #    filter_lessons_for_ddd never emits rewrite today, and the rewrite
            #    branch has a delete-then-failed-append partial-state trap — so
            #    autonomous rewrite is disallowed; a human approves it.
            #  - under BOTH the per-call cap AND the session/day-wide cap (#3).
            _day_budget = _auto_retire_budget_remaining(project_dir)
            _eligible = (
                proposal.auto_apply_ok
                and proposal.change_type == "retire"
                and retired < MAX_AUTO_RETIRES_PER_RUN
                and _day_budget > 0
            )
            if _eligible:
                status = apply_retire_proposal(proposal, project_dir)
                if status == "retired":
                    # Loud, user-auditable record of an AUTONOMOUS delete (Gate-2:
                    # a wrong auto-delete is reversible only if someone can NOTICE
                    # it — so every auto-retire is logged at WARNING with the exact
                    # (doc, title) + the evidence that triggered it).
                    logger.warning(
                        "[AUTO-RETIRE] autonomously retired DDD entry (reversible: "
                        "archived + .bak): %s § %s | evidence=%s | run=%s",
                        proposal.target_doc, proposal.target_title,
                        proposal.evidence[:120].replace("\n", "\\n"),
                        proposal.source_run_id,
                    )
                    proposal.status = "applied"
                    log_application(proposal, project_dir)
                    _record_auto_retire(project_dir)
                    retired += 1
                else:
                    # Fail-loud outcome (retire_failed / no_target / doc_missing):
                    # do NOT silently drop — escalate so a human sees the miss.
                    logger.warning(
                        "Auto-retire did not apply (status=%s) → escalating: %s § %s",
                        status, proposal.target_doc, proposal.target_title,
                    )
                    proposal.status = "escalated"
                    write_proposal(proposal, project_dir)
                    escalated += 1
            else:
                # Not confident, rewrite, or a cap (per-call OR day-wide) reached →
                # human queue.
                proposal.status = "escalated"
                write_proposal(proposal, project_dir)
                escalated += 1
            continue
        if proposal.is_safe_append():
            # Additional auto-approval gate (maturity, magnitude, circuit breaker)
            # Gate is advisory: if it blocks, escalate. If it errors, allow (fail-open).
            try:
                from core.ddd_auto_approval import evaluate_auto_approval
                decision = evaluate_auto_approval(proposal, project_dir)
                if not decision.approved:
                    # Only escalate if a HARD gate blocked (not maturity absence)
                    # Hard gates: safe_target_doc, circuit_breaker_ok
                    hard_blocked = (
                        not decision.criteria_met.get("safe_target_doc", True)
                        or not decision.criteria_met.get("circuit_breaker_ok", True)
                        or not decision.criteria_met.get("small_magnitude", True)
                    )
                    if hard_blocked:
                        proposal.status = "escalated"
                        write_proposal(proposal, project_dir)
                        escalated += 1
                        continue
                    # Soft gates (maturity, conflict, precision) — log but allow
            except (ImportError, Exception):
                pass  # Auto-approval module unavailable or errored — allow through

            status = apply_to_ddd(proposal, project_dir)
            if status == "applied":
                proposal.status = "applied"
                log_application(proposal, project_dir)
                applied += 1
            elif status == "created_section":
                # The lesson WAS applied (not dropped) — the whitelisted section
                # heading was absent so it was auto-created. Count as applied, but
                # surface the drift as observable (latent doc/table divergence) so
                # it can be reconciled. NOT an error — the lesson is safe.
                # Sanitize proposal-derived fields (untrusted: from on-disk
                # proposal JSON / reflect lessons) before they reach log/stderr/
                # HTTP sinks — prevents CRLF log-injection (a forged source_run_id
                # with newlines could forge log records or break log parsers).
                def _safe(v: str) -> str:
                    return str(v).replace("\n", "\\n").replace("\r", "\\r")
                msg = (
                    f"DDD drift (auto-healed): created missing whitelisted section "
                    f"'{_safe(proposal.target_doc)} § {_safe(proposal.target_section)}' "
                    f"(run {_safe(proposal.source_run_id)}). Lesson applied to the new "
                    f"section. Reconcile the doc template / ROUTING_TABLE to avoid drift."
                )
                logger.warning(msg)
                drift_errors.append(msg)
                proposal.status = "applied"
                log_application(proposal, project_dir, created_section=True)
                applied += 1
            else:
                # Benign no-op: "duplicate", "doc_missing", "locked", "not_safe".
                rejected += 1
        else:
            proposal.status = "escalated"
            write_proposal(proposal, project_dir)
            escalated += 1

    return {
        "applied": applied,
        "escalated": escalated,
        "rejected": rejected,
        "retired": retired,
        "drift_errors": drift_errors,
    }


def cultivate_from_reflect(
    lessons: List[str], run_id: str, project: str, project_dir: Path
) -> dict:
    """One-call entry point: filter lessons → auto-apply safe ones → escalate risky ones.

    This is the function the REFLECT stage calls. It handles the full lifecycle:
    1. Filter lessons into proposals
    2. For each proposal:
       - If safe additive: apply directly to DDD + log to changelog
       - If risky: write to proposal queue for escalation
    3. Return summary for REFLECT stage output

    Returns:
        {"applied": N, "escalated": M, "rejected": K, "retired": R, "drift_errors": [...]}
        (retired = confident auto-retires applied this run — run_ecc7a32b)
    """
    proposals = filter_lessons_for_ddd(lessons, run_id, project, project_dir)
    return _cultivate_proposals(proposals, project_dir)


def cultivate_from_corrections(
    corrections: List[str], session_id: str, project: str, project_dir: Path
) -> dict:
    """Cultivate user corrections from session DailyActivity into DDD docs.

    Corrections are the highest-priority signal (Ch6 in HLD): explicit "no,
    do X instead" from the user. Routes to TECH.md Runtime Traps / Conventions
    or IMPROVEMENT.md What Failed based on keyword classification.

    PE-1 fix: corrections are pre-curated by the LLM extraction step — their
    existence alone proves relevance. When keyword classification returns None
    (no keyword match), corrections fall through to IMPROVEMENT.md "What Failed"
    with confidence 0.4 instead of being silently rejected. This ensures ALL
    corrections produce a DDD entry (the most valuable signal channel).

    Uses the same filter/apply pipeline as reflect lessons — zero LLM cost.
    source_stage="correction" for changelog attribution.

    Returns:
        {"applied": N, "escalated": M, "rejected": K, "retired": R, "drift_errors": [...]}
        (retired = confident auto-retires applied this run — run_ecc7a32b)
    """
    proposals = filter_lessons_for_ddd(corrections, session_id, project, project_dir)

    # PE-1: Corrections that fail keyword classification still deserve DDD entry.
    # They're pre-curated by the extraction LLM — existence = relevance.
    # Fallback: route to IMPROVEMENT.md "What Failed" (safe append section).
    classified_contents = {p.content for p in proposals}  # O(1) content-based dedup
    for correction in corrections:
        if not correction or not isinstance(correction, str):
            continue
        stripped = correction.strip()
        if len(stripped) < MIN_LESSON_LENGTH:
            continue
        if NOISE_PATTERNS.match(stripped):
            continue
        # M2 quality gate on the PE-1 fallback too (Gate-2 finding D, run_d7cb3941):
        # this fallback writes straight to "What Failed" — without this it was a THIRD
        # unguarded writer (alongside the keyword path + writeback hook) through which
        # first-person narration / instance-logs could still reach IMPROVEMENT.md.
        if not is_quality_lesson(stripped):
            continue
        # Skip if already classified by keyword path
        if stripped in classified_contents:
            continue
        if len(proposals) >= MAX_PROPOSALS_PER_RUN:
            break
        # Fallback: unclassified correction → IMPROVEMENT.md "What Failed"
        proposals.append(CultivationProposal(
            target_doc="IMPROVEMENT.md",
            target_section="What Failed",
            content=stripped,
            source_run_id=session_id,
            confidence=0.4,
        ))

    for p in proposals:
        p.source_stage = "correction"
    return _cultivate_proposals(proposals, project_dir)


def cultivate_from_decisions(
    decisions: List[str], session_id: str, project: str, project_dir: Path
) -> dict:
    """Cultivate session decisions from DailyActivity into DDD docs.

    Decisions are explicit choices/commitments made during a session (Ch5 in
    HLD). Routes to TECH.md Conventions / IMPROVEMENT.md What Worked based on
    keyword classification.

    Uses the same filter/apply pipeline as reflect lessons — zero LLM cost.
    source_stage="decision" for changelog attribution.

    Returns:
        {"applied": N, "escalated": M, "rejected": K, "retired": R, "drift_errors": [...]}
        (retired = confident auto-retires applied this run — run_ecc7a32b)
    """
    proposals = filter_lessons_for_ddd(decisions, session_id, project, project_dir)
    for p in proposals:
        p.source_stage = "decision"
    return _cultivate_proposals(proposals, project_dir)


# Capability C (conversation→DDD, run_e346b8ed) --------------------------------
# Anti-flood: conversation is a LOW-signal, adversarial source (raw multi-party
# chat) vs the pre-curated reflect/decision sources. Cap it tighter and give it a
# LOWER default confidence so that in the briefing's top-5-by-confidence view
# (proactive_intelligence L5) a genuine reflect/correction escalation outranks a
# chat extraction on a tie. (Skeptic anti-flood finding, run_e346b8ed.)
MAX_CONVERSATION_PROPOSALS_PER_RUN = 3
CONVERSATION_DEFAULT_CONFIDENCE = 0.3


def cultivate_from_conversation(
    candidates: List[dict], session_id: str, project: str, project_dir: Path
) -> dict:
    """Cultivate DDD candidates extracted from a group-channel conversation.

    Capability C (run_e346b8ed). Each candidate is the output of the CONSERVATIVE
    upstream extractor (``conversation_extract``, DoD2) — a settled, owner-ratified
    conclusion with an evidence quote and a *suggested* target doc/section. This
    function only converts candidates → proposals and routes them; it makes NO
    judgment about whether a candidate is worthy (that already happened upstream).

    STRUCTURAL 宁缺毋滥 GUARANTEE: every proposal is tagged
    ``source_stage="conversation"``, which ``_cultivate_proposals`` forces down the
    escalate branch — so a conversation-derived entry can NEVER auto-apply and
    ALWAYS requires XG's approve-time confirmation (routers/cultivation.py). This
    tag is load-bearing: if it were omitted, a safe-append target would auto-apply
    and silently defeat the human-gate. That is why it is set here on EVERY
    proposal, mirroring cultivate_from_decisions' source_stage discipline.

    candidate dict shape (from the extractor):
        {"content": str,            # the proposed DDD entry (1-3 sentences)
         "target_doc": str,         # SUGGESTED doc (XG can re-target at approve)
         "target_section": str,     # SUGGESTED section
         "evidence": str,           # verbatim conversation quote(s) — for review
         "confidence": float}       # optional; defaults low (anti-flood)

    Returns: {"applied": N, "escalated": M, "rejected": K, "drift_errors": [...]}.
    In practice applied is ALWAYS 0 for this source (the guard forces escalation);
    the return shape is kept identical to the other sources for caller uniformity.
    """
    proposals: List[CultivationProposal] = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        content = (cand.get("content") or "").strip()
        if not content:
            continue
        # Evidence quote is carried in the content tail so the human-gate reviewer
        # (routers/cultivation.py list/approve) sees WHAT conversation line drove
        # the proposal — never extract without a traceable source.
        evidence = (cand.get("evidence") or "").strip()
        body = f"{content}\n\n_Source (conversation {session_id}): {evidence}_" if evidence else content
        proposals.append(
            CultivationProposal(
                target_doc=cand.get("target_doc") or "PROJECT.md",
                target_section=cand.get("target_section") or "Recent Decisions",
                content=body,
                source_run_id=session_id,
                confidence=float(cand.get("confidence") or CONVERSATION_DEFAULT_CONFIDENCE),
                source_stage="conversation",  # load-bearing: forces escalation
            )
        )
        if len(proposals) >= MAX_CONVERSATION_PROPOSALS_PER_RUN:
            break
    return _cultivate_proposals(proposals, project_dir)
