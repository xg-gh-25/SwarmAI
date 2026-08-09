"""
DDD Cultivation Engine — Tiered Autonomy Model.

Connects Pipeline REFLECT output to DDD documents with graduated autonomy:
- ADDITIVE changes (new lessons, patterns): auto-applied, logged to changelog
- RETIRE (evidence-driven delete/rewrite, run_ecc7a32b): a HIGH-CONFIDENCE
  supersession (unambiguous non-keep-class locate) AUTO-APPLIES reversibly
  (retire_entry: archive+strip; recovery = archive + git, no .bak), capped at
  MAX_AUTO_RETIRES_PER_RUN; a
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

from core.ddd_paths import ddd_path

# Maximum proposals generated per pipeline run (prevents noise)
MAX_PROPOSALS_PER_RUN = 5

# Maximum AUTONOMOUS retires (delete/rewrite) applied per cultivate call
# (run_ecc7a32b). Bounds the blast radius of confident auto-retire within one
# _cultivate_proposals invocation. Retire is reversible (archive + git), so a wrong
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

# The proposal statuses that are AWAITING A HUMAN DECISION — the single source of
# truth for "can this proposal still be acted on (approved/rejected)". Terminal
# states (applied/rejected/expired) are excluded. Reused by read_pending_proposals
# (the list view) AND the router's _find_proposal (the by-id lookup) so the two can
# never drift: a proposal hidden from the list must also be un-approvable (run_93594880).
AWAITING_HUMAN_STATUSES = frozenset({"pending", "escalated"})

# CJK codepoint detector — used by is_quality_lesson to apply a char-length floor
# (not a whitespace-word floor) to Chinese/Japanese/Korean text, which has no
# inter-word spaces. Ranges are kept byte-identical to context_directory_loader's
# full detector (Han + Kana + Hangul + CJK ext); a sync-guard test asserts equality
# so a future edit to either can't silently diverge (test_cjk_re_matches_loader).
# NOTE: this is NOT a single "canonical" regex — memory_index._CJK_RE is
# intentionally Han-ONLY ([一-鿿]) for its recall tokenizer, a deliberately narrower
# detector, not a copy of this one. Do not "sync" the three blindly.
_CJK_RE = re.compile(
    r"[　-〿぀-ゟ゠-ヿ㐀-䶿"
    r"一-鿿가-힯豈-﫿︰-﹏＀-￯"
    r"\U00020000-\U0002a6df\U0002a700-\U0002b73f]"
)

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
    # strip; recovery = archive + git), so a HIGH-CONFIDENCE one (auto_apply_ok, set by a
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
    # run_171a17c2: ADVISORY contradiction signal for APPEND proposals. When
    # detect_contradiction found this append polarity-flips a same-topic curated
    # entry, this holds ContradictionFlag.to_dict() ({conflicting_title, section,
    # flip, shared_topic}); None otherwise. NON-BLOCKING, NON-DESTRUCTIVE — it does
    # NOT change change_type (stays "append") and does NOT trigger any retire; it is
    # surfaced to the human/agent review layer to judge. Additive + defaults None →
    # old proposal JSON round-trips unchanged (AC6).
    contradiction_flag: "Optional[dict]" = None
    # Knowledge Admission Component B (run_8d5fe9d1): the trust stamp. The SOLE
    # authority that (in Component C) lets a proposal auto-apply into ANY doc. Stamped
    # at creation from the source run's canonical gate2_outcome via stamp_trust_from_run.
    # {"passed", "failed", "n/a"} — fail-closed default "n/a" (never auto without an
    # explicit Gate-2 pass). Additive + defaults "n/a" → old proposal JSON round-trips.
    passed_adversarial_gate: str = "n/a"

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
            "contradiction_flag": self.contradiction_flag,
            "passed_adversarial_gate": self.passed_adversarial_gate,
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
            contradiction_flag=data.get("contradiction_flag", None),
            passed_adversarial_gate=data.get("passed_adversarial_gate", "n/a"),
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

    # is_safe_append() REMOVED (run_8d5fe9d1, Component C): its sole job was the
    # hardcoded doc/section whitelist (_PROTECTED_ZONES + SAFE_APPEND_SECTIONS), which
    # trust replaces. The auto decision now lives in admission_band() (trust=passed +
    # quality checks); the writer apply_to_ddd no longer re-gates (human-approve of a
    # REVIEW proposal is a separate authority). No callers remain.


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
    #
    # CJK-aware (run_4443a967): the >=5-WORD floor is whitespace-tokenized, which is
    # blind to CJK — Chinese/Japanese have no inter-word spaces, so a real
    # post-mortem lesson tokenizes to 1-3 "words" and is wrongly rejected. This
    # matters MORE here than at first glance: the correction-capture leg routes
    # prompts that are disproportionately CJK (its trigger regex matches 不对/错了/
    # 应该用) through this floor. For CJK-bearing text, count CJK codepoints as
    # content and require MIN_LESSON_LENGTH chars instead of 5 whitespace-words.
    words = stripped.split()
    if len(words) < 5:
        # A CJK-heavy fragment clears the floor on CHAR length, not word count.
        if _CJK_RE.search(stripped) and len(stripped) >= MIN_LESSON_LENGTH:
            return True
        return False
    return True


# ── Knowledge Admission: is_noise() SSOT (Component A) ────────────────────────
# The SINGLE consolidated noise gate. A proposal that is_noise() → DISCARD before it
# can be queued / scored / shown to a human. Composes the existing quality primitive
# (is_quality_lesson: instance-log / narration / sub-sentence fragment) AND adds
# machine-broadcast detection — the code_intel_feed "Architecture change detected" /
# "Undocumented module" shapes are pure machine OBSERVATIONS, not human-reviewable
# judgments (they belong on the health surface, not the review queue). Returns
# (is_noise, reason) — reason is a short stable token for telemetry, NOT prose.

# Machine-broadcast openers: a distillation entry that OPENS with one of these is a
# code-intel drift observation, not a lesson. Anchored to the start AND requires the
# MACHINE SHAPE punctuation — not just the opening words — so a real human lesson that
# merely OPENS with "Architecture change detected requires…" is NOT dropped (Gate-2
# HOLE#1: err toward accepting real knowledge, knowledge-loss > queue-noise). The
# machine forms are exactly: "Architecture change detected:\n- ..." (colon),
# "Undocumented module `x` (N functions)" (backtick+paren), "Symbol `x` referenced in
# TECH.md but not found" (the stale-ref sentence).
_MACHINE_BROADCAST_RE = re.compile(
    r"^\s*(?:-\s*)?(?:"
    r"architecture change detected\s*:"                       # colon = the machine list header
    r"|undocumented module\s+`[^`]+`\s*\(\d+\s+functions?\)"   # backtick module + (N functions)
    r"|symbol\s+`[^`]+`\s+referenced in tech\.md but not found"  # the exact stale-ref sentence
    r")",
    re.IGNORECASE,
)


def is_noise(text: str) -> "tuple[bool, str]":
    """SSOT noise gate. Returns (is_noise, reason).

    reason ∈ {"empty", "machine_broadcast", "instance_log_or_fragment", ""}.
    "" (empty reason) == NOT noise (clean). Fail toward ACCEPTING real lessons: an
    ambiguous entry is NOT noise (knowledge loss > a little queue noise, per the
    is_quality_lesson doctrine). Only the two unambiguous classes are dropped:
    machine broadcasts and the instance-log/narration/fragment set.
    """
    if not text or not text.strip():
        return (True, "empty")
    if _MACHINE_BROADCAST_RE.match(text.strip()):
        return (True, "machine_broadcast")
    if not is_quality_lesson(text):
        return (True, "instance_log_or_fragment")
    return (False, "")


# Pure-correction-signal patterns: a raw user prompt whose ENTIRE substance is a
# correction TRIGGER ("that's wrong, use async instead", "不对，应该用 rebase") carries
# NO reusable lesson — it is a steering signal, not knowledge. is_quality_lesson()
# alone does NOT catch these (they are grammatical 5+-word sentences that pass the
# floor — verified: "That's wrong, use async instead" passed is_quality_lesson).
# The discriminator: strip the trigger phrases; if the residual teaching body is
# below MIN_LESSON_LENGTH, the prompt was ONLY a correction → not memory-worthy.
#
# DELIBERATELY a residue-length test, NOT a growing spelling denylist (PIT40: a
# per-spelling guard is whack-a-mole — each new phrasing needs a new pattern). We
# match the common trigger STEMS and let the residue-length gate do the deciding,
# so an unseen phrasing with a real lesson body still passes on its residue.
#
# CJK stem PARITY (run_4443a967 Gate-2 meta-review): the EN side enumerates ~7 stem
# families; the CJK side must have comparable breadth or the residue gate is the ONLY
# CJK discriminator AND it under-strips → a long pure-CJK redirect (no lesson) leaks
# into MEMORY (CJK is information-dense: a 30+ char redirect keeps 30+ char residue).
# This is stem PARITY across languages, not per-spelling whack-a-mole. Residual risk
# is bounded by the asymmetry the caller documents: when the gate is uncertain on CJK
# it should REJECT (Principle 1 — a false-positive poisons the brain; a false-negative
# is still captured in corrections.jsonl for post-session classification).
_CORRECTION_TRIGGER_RE = re.compile(
    r"(?ix)"
    r"\b(that'?s|you'?re|it'?s)\s+(wrong|incorrect|not\s+right|not\s+correct)\b"
    r"|\bactually,?\s+(no|not|don'?t|it'?s\s+not|that'?s\s+not)\b"
    r"|\bnot\s+like\s+that\b"
    r"|\b(use|try)\s+\w+\s+instead\b"
    r"|\bdon'?t\s+use\b"
    r"|\b(it|that)\s+should\s+be\b"
    r"|\bthe\s+code\s+is\s+wrong(ly|fully)?\s+\w*\b"
    # CJK trigger stems — wrongness (不对/错了/搞错/不太对/不对劲/不是这样/不是这个意思),
    # redirect (重新|重弄|推倒重来|方向.{0,4}错|完全不行|这样写?不行|不应该这么),
    # imperative-swap (应该用|应该是|改用|别用|不要用|你.{0,6}(重新|再)).
    r"|不对劲|不太对|不对|错了|搞错|不是这样|不是这个意思|不是.{0,10}是"
    r"|完全不行|这样写?不行|不应该这么|推倒重来|重新想|重新弄|重新理解|重新写"
    r"|应该用|应该是|改用|别用|不要用|方向.{0,4}错",
)


def is_memory_worthy_correction(prompt: str) -> bool:
    """True if a correction-detected user prompt carries a reusable lesson worth
    writing to MEMORY.md ``## Pitfalls`` — the UNIFIED value gate for the immediate
    correction-capture leg (runtime_hooks.create_user_correction_detector).

    Symmetric with the golden-case seeding leg (which was already moved post-session
    + CLASS-gated): a raw prompt is a correction SIGNAL, not automatically a lesson.
    Reuses the SAME value-floor primitives as the cultivation writeback path — no
    parallel judgment logic (AC3/AC5):

      1. length floor        — len < MIN_LESSON_LENGTH → reject
      2. is_quality_lesson() — instance-logs / XML dumps / narration / <5-word → reject
      3. pure-correction     — strip trigger phrases; residue < MIN_LESSON_LENGTH → reject
                               (a prompt that is ONLY "that's wrong, use X" teaches nothing)

    NOTE: this gates only what gets SEDIMENTED into the cognitive store. The
    corrections.jsonl append (the durable signal the post-session classifier
    consumes) happens REGARDLESS — rejecting here loses no signal, only DELAYS a
    borderline lesson to post-session classification. That asymmetry is deliberate:
    per Principle 1 a false-POSITIVE (garbage sedimented into the brain) is strictly
    worse than a false-NEGATIVE (a real lesson captured in jsonl, promoted later).
    So the residue floor is intentionally CONSERVATIVE — it errs toward rejecting a
    borderline single-swap redirect (e.g. "Use gVisor instead of runc for untrusted
    tenants" → residue 29 < 30 → rejected here, still in jsonl). A residue-FRACTION
    relaxation was evaluated (run_4443a967 Gate-2) and REJECTED: it could not
    separate that borderline lesson (0.60 non-trigger fraction) from genuine garbage
    ("Actually, not like that. Use rebase." → 0.58) — it re-opened a garbage leak to
    save a non-permanent, jsonl-preserved false-negative. Brain purity wins.

    KNOWN RESIDUAL (run_4443a967 Gate-2 meta-review, MED — accepted, not fixed):
    a LONG pure-CJK redirect that carries no lesson yet matches the upstream
    explicit-error gate (e.g. "不对，你这个整个思路都错了，重新捋一遍需求再来…") is
    NOT reliably separable from a real CJK lesson by any stem/length heuristic — CJK
    is information-dense, so a verbose redirect keeps a 30+ char residue just like a
    lesson does. Chasing perfect separation is PIT40 whack-a-mole (unbounded stem
    list) or PIT05 (a corpus-tuned length threshold). We DELIBERATELY stop at CJK
    stem PARITY with the EN side and accept that a rare long CJK redirect may
    sediment. This is acceptable because such an entry is NOT permanent brain-poison:
    it is (1) MemoryGuard.sanitize()-validated on write, (2) subject to value-aware
    decay, and (3) still reclassified post-session from jsonl. The HIGH direction —
    real CJK lessons wrongly DROPPED by the CJK-blind word floor — is the one that
    mattered and is fixed; this MED residual is the lesser evil (DEC28: the fix that
    loses real knowledge is worse than the one that admits a little noise a decay
    engine will clear).
    """
    stripped = prompt.strip()
    if len(stripped) < MIN_LESSON_LENGTH:
        return False
    if not is_quality_lesson(stripped):
        return False
    residue = _CORRECTION_TRIGGER_RE.sub("", stripped).strip(" ,.;:—-“”\"'。，、！!？?")
    if len(residue) < MIN_LESSON_LENGTH:
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

    # Admission noise gate (Component A SSOT): drop instance-logs / narration /
    # fragments AND machine broadcasts before they can become a proposal.
    _noise, _reason = is_noise(stripped)
    if _noise:
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
        contradiction_flag = None
        if project_dir is not None and _detect_supersession(lesson):
            located = _locate_target_entry(lesson, target_doc, project_dir)
            if located is not None:
                target_title, target_section, confident = located
                change_type = "retire"
                evidence = lesson.strip()
                auto_apply_ok = confident

        # run_171a17c2: ADVISORY contradiction detection on the APPEND path only.
        # A retire already handles a self-declared supersession; here we catch the
        # OTHER case — a non-superseding append that silently polarity-flips a
        # same-topic curated entry. Read-only, non-blocking: sets an advisory flag,
        # never changes change_type, never retires. Only when project_dir is given
        # (pure append-only callers with project_dir=None keep zero-I/O behavior).
        if change_type == "append" and project_dir is not None:
            _flag = detect_contradiction(lesson, target_doc, project_dir)
            if _flag is not None:
                contradiction_flag = _flag.to_dict()

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
            contradiction_flag=contradiction_flag,
            # Component B: stamp trust from the source run's Gate-2 outcome at creation.
            # Fail-closed to "n/a" when project_dir is None (pure-classify callers) or the
            # run can't be resolved — so a proposal never claims trust it can't prove.
            passed_adversarial_gate=stamp_trust_from_run(run_id, project_dir),
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


# The canonical cultivated-entry shape is ``- [type] **Title** — body`` (see
# content_signature docstring). But apply_to_ddd historically wrote raw-prose
# reflect-lessons VERBATIM as ``- {content} (date, run, label)`` with NO bold
# title — and the lifecycle engine's _ENTRY_RE (ddd_entry_lifecycle.py) only
# parses ``- [type]? **Title** …``. So a raw-prose bullet was structurally
# INVISIBLE to parse/decay/reclaim/retire (measured: 707+ per DDD doc, 0 stamped).
# This normalizer closes that gap at the WRITE side by giving every cultivated
# bullet a bold title — via an INSERT-ONLY transform that deletes nothing:
_ALREADY_TITLED_RE = re.compile(r"^(?:\[\w+\] )?\*\*")


def _normalize_cultivated_bullet(content: str, entry_type: str) -> "str | None":
    """Give a cultivated lesson a bold title so the lifecycle engine can parse it.

    INSERT-ONLY: the returned string is ``[{entry_type}] **{prefix}**{rest}`` where
    ``prefix`` + ``rest`` == the ORIGINAL ``content`` byte-for-byte — the ONLY edits
    are (a) a leading ``[type] `` and (b) a ``**…**`` pair wrapping a leading span.
    Nothing is deleted, truncated, or re-separated. This preserves two invariants
    that any change to the corpus MUST hold (both proven against the real 839-bullet
    corpus in test_ddd_cultivation, and re-asserted per-bullet by the migration):

      1. TRUE-LOSSLESS — removing exactly the two inserted ``**`` markers and the
         ``[type] `` prefix restores ``content`` exactly (no data loss — the C044
         "don't destroy knowledge" red line; BLOCK-A).
      2. SIGNATURE-INVARIANT — the closing ``**`` is placed immediately before a
         SPACE (or end-of-string), so content_signature()'s ``text.replace("**", " ")``
         followed by whitespace-collapse is a NO-OP → the migrated bullet signs
         IDENTICALLY to the original. The doc-wide dedup chokepoint (run_e9cb7e2a)
         is NOT re-opened (BLOCK-B).

    Returns None for a degenerate bullet (empty / no usable title span) — the caller
    keeps the original content unchanged rather than emit a broken ``**``.

    Idempotent: content already carrying a leading ``**`` (optionally ``[type] **``)
    is returned unchanged — never double-wrapped.
    """
    if not content or not content.strip():
        return None
    # Idempotency: an already-titled bullet (bold, optionally [type]-prefixed) is
    # left exactly as-is. This is what makes the migration safe to re-run and keeps
    # the ~150 reflect-lessons that ALREADY emit `**Title**` untouched.
    if _ALREADY_TITLED_RE.match(content):
        return content
    # A leading `[type] ` on UNTITLED content (e.g. `[guideline] Verify-first …`, a
    # cultivated lesson that carried a type tag but no bold title) must be CONSUMED,
    # not kept — else we double-prefix (`[guideline] **[guideline] …**`), which drifts
    # the content_signature. The type we emit is the one the caller passed (derived
    # from the same content via classify_entry_type), so dropping the inline tag is
    # lossless w.r.t. the entry's classification. Prefer the content's own declared
    # type when present (it was a deliberate tag) over the re-classified one.
    # `\s+` (not a single literal space) to match the two upstream parse sites
    # (persist_routing._DECLARED_TYPE_RE + ddd_cultivation:892). A double-space
    # `[decision]  body` else traps a space inside the bold markers → non-lossless
    # restore (Gate-2 Finding 2, run_c7e1e39d). content[_tag.end():] then strips ALL
    # the whitespace the tag consumed, so no leading space survives into the title.
    _tag = re.match(r"^\[(\w+)\]\s+", content)
    if _tag:
        entry_type = _tag.group(1)
        content = content[_tag.end():]
        if not content.strip():
            return None

    # The title span MUST end on a SPACE boundary (or EOL) — two invariants ride on it:
    #   • signature-invariance: the closing ** then sits immediately before a space, so
    #     content_signature()'s `**`→space + whitespace-collapse is a no-op.
    #   • inner-** safety (BLOCK-C): if the span ended mid-text adjacent to an existing
    #     `**`, the markers would merge into `****` (garbage capture + lossy).
    # cap = the last space at/before char 80; if the first token is longer, use EOL.
    cap = content.rfind(" ", 0, 81)
    if cap <= 20:
        cap = len(content)

    # Inner-** guard (BLOCK-C): the title span may NOT contain a `**`. If content has an
    # inner `**` at/before the cap, pull the title end back to the last space STRICTLY
    # BEFORE that `**`. If no such clean space exists (the `**` is within the first ~20
    # chars, e.g. `Since it's **already SHIPPED**…`), there is no clean title — SKIP
    # (return None → caller keeps the original untitled content). Honest: an un-titled
    # bullet stays un-titled, never a corrupt 4-star one; it can still be retired by name.
    inner = content.find("**")
    if inner != -1 and inner < cap:
        cut = content.rfind(" ", 0, inner)
        if cut <= 20:
            return None
        end = cut
    else:
        end = cap

    title = content[:end]
    if not title.strip() or "**" in title:
        return None
    return f"[{entry_type}] **{title}**{content[end:]}"


def apply_to_ddd(proposal: CultivationProposal, project_dir: Path) -> str:
    """Apply an additive proposal directly to the target DDD document.

    Appends a bullet point under the target section (newest first).
    Only works for safe_append sections (IMPROVEMENT.md and TECH.md).
    Uses fcntl advisory lock to prevent concurrent write corruption.

    Returns a status string (NOT a bool — callers must compare explicitly):
      - "applied"           — entry written under a pre-existing section heading
      - "created_section"   — the allowlisted section heading was ABSENT, so it
                              was auto-created at end-of-doc and the entry written
                              under it. This makes section-name drift structurally
                              harmless: a lesson is NEVER dropped just because the
                              doc heading is missing. The section name is TRUSTED
                              (sourced from ROUTING_TABLE via SAFE_APPEND_SECTIONS,
                              never user input), so creating it is safe. Surfaced
                              (logged) so latent drift is still visible.
                              (run_45ab67c7 root cause — structural fix.)
      - "duplicate"         — benign no-op, content already present ANYWHERE in the
                              doc (DOC-WIDE content-signature match, run_e9cb7e2a)
      - "rejected_low_value"— failed the value FLOOR (empty / instance-log /
                              narration / <30 chars / <5-word fragment). The gate
                              working as intended, not an error (run_e9cb7e2a).
      - "not_safe"          — target doc/section not in SAFE_APPEND_SECTIONS
      - "doc_missing"       — target document file does not exist
      - "locked"            — another process holds the write lock (retry later)

    Note: "section_not_found" is NO LONGER returned — a missing allowlisted
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
    # NOTE (run_8d5fe9d1, Component C): apply_to_ddd is the WRITER, not the decision.
    # It does NOT re-check trust — the auto/review/discard authority lives in
    # admission_band (auto path) and in the human-approve endpoint (routers/cultivation:
    # a human approving a REVIEW proposal IS the authority, and those proposals are
    # trust=n/a by construction — re-gating trust here would make human approval
    # impossible). The old is_safe_append() doc-whitelist gate is removed; the only
    # invariant enforced here is the non-append refusal above (retire/rewrite have
    # their own applier). Noise/quality already filtered upstream at _classify_lesson.

    # VALUE FLOOR at the chokepoint (run_e9cb7e2a). apply_to_ddd is the ONE gate
    # every write path crosses — but the writeback hook reaches it DIRECTLY,
    # bypassing _classify_lesson where is_quality_lesson/MIN_LESSON_LENGTH lived.
    # So an empty / instance-log / narration / sub-5-word fragment could enter the
    # brain ungated. Enforce the SAME floor here so all paths share it. This is a
    # FLOOR, not a taste judge: is_quality_lesson errs toward ACCEPT when ambiguous
    # (knowledge-loss > noise), and MIN_LESSON_LENGTH is the existing 30-char bar.
    # (Not the fix for the 170K silt — that's the doc-wide dedup below; this closes
    # the orthogonal "junk fragment via writeback" hole.)
    _candidate = proposal.content.strip()
    if len(_candidate) < MIN_LESSON_LENGTH or not is_quality_lesson(_candidate):
        return "rejected_low_value"

    # Six-section resolver (READ, strangler-aware): a migrated DDD keeps canonical
    # docs under 2-understanding/; a bare `project_dir / doc` would hit the empty
    # root → doc_missing → cultivation silently stops sedimenting. ddd_path returns
    # the new location when it exists, else the old root (un-migrated DDDs). We both
    # READ and WRITE-BACK this same resolved path below, so reads/writes never
    # diverge (no split-brain) — ddd_path (not ddd_write_path) is correct here.
    doc_path = ddd_path(project_dir, proposal.target_doc)
    if not doc_path.exists():
        return "doc_missing"

    # Advisory doc-write lock via the SHARED helper (run_06350217): EVERY writer of
    # this doc — apply_to_ddd, orchestrator auto-apply/decay/llm-apply, retire —
    # must lock on the SAME <doc>.md.lock name, or they don't mutually exclude
    # (the old with_suffix(".lock")=IMPROVEMENT.lock diverged from the orchestrator's
    # IMPROVEMENT.md.lock → a lost-update race). md_lock owns the ONE derivation,
    # is cross-platform, and never unlinks (preserves run_24d9f714's inode-race fix).
    # Non-blocking: skip (return "locked") if another writer holds it — retry next run.
    from utils.file_lock import md_lock

    with md_lock(doc_path, blocking=False) as _got:
        if not _got:
            return "locked"
        content = doc_path.read_text(encoding="utf-8")

        # ── DESTRUCTIVE SUPERSEDE (run_6ac7a760, XG-directed; Gate-2-tiered) ────
        # When this append polarity-flips a same-topic curated entry
        # (detect_contradiction set proposal.contradiction_flag), the NEW entry
        # REPLACES the old — new-supersedes-old = clean forgetting = evolution. NOT
        # advisory-flag-and-keep-both (run_171a17c2 = the hoarding/graveyard anti-
        # pattern in EVOLUTION). But NOT a blind delete either (Gate-2, XG=Plan-B):
        #
        #   ORDINARY knowledge (guideline/pitfall/process, non-evergreen section,
        #     exactly ONE match) → STRIP the old in-place = auto clean-forgetting.
        #   PERMANENT knowledge (is_keep_class: principle/correction/decision/model
        #     or an evergreen section or COE) → do NOT auto-delete. It is my judgment
        #     BEDROCK; a single wrong polarity match must not silently erase it. →
        #     ESCALATE a retire-proposal to the human queue instead.
        #   AMBIGUOUS: >1 entry shares (title,section) → a strip would delete ALL of
        #     them while detection identified ONE (Gate-2 MED: connected-delete /
        #     data-loss, exactly what retire_entry refuses). → ESCALATE, never strip.
        #
        # This is NOT the keep-class "carve-out from forgetting" XG rejected — it is
        # ROUTING a high-blast-radius delete to a one-word human confirm (the same
        # bar retire_entry sets with force=True). Ordinary knowledge still auto-
        # forgets with zero prompt.
        #
        # MUST run HERE — after read (:786), BEFORE section_re/body_start: _strip_
        # entries mutates `content` in-memory so downstream offsets are computed on
        # the STRIPPED string (Gate-1 FATAL-2). include_prose=True is MANDATORY
        # (Gate-1 FATAL-1: the flag's key came from parse_entries(include_prose=True)).
        # NO archive (XG: git is the only recovery). .get()-guarded (Gate-1 CRASH).
        cf = proposal.contradiction_flag
        if cf and cf.get("conflicting_title") and cf.get("section"):
            from core.ddd_entry_lifecycle import (
                _strip_entries, parse_entries, is_keep_class,
                MEMORY_EVERGREEN_SECTIONS,
            )
            _old_title, _old_section = cf["conflicting_title"], cf["section"]
            _matches = [
                e for e in parse_entries(content, include_prose=True)
                if e.title == _old_title and e.section == _old_section
            ]
            _keep = any(
                is_keep_class(e, evergreen_sections=MEMORY_EVERGREEN_SECTIONS)
                for e in _matches
            )
            if _matches and (_keep or len(_matches) > 1):
                # PERMANENT or AMBIGUOUS → escalate a retire proposal, do NOT strip.
                _why = "keep-class (permanent knowledge)" if _keep else \
                       f"{len(_matches)} same-title entries (ambiguous — strip would delete all)"
                logger.warning(
                    "[SUPERSEDE-ESCALATE] polarity flip targets %s — routing a retire "
                    "proposal to the human queue instead of auto-deleting: %s § %s | "
                    "flip=%s | run=%s",
                    _why, proposal.target_doc, _old_section, cf.get("flip"),
                    proposal.source_run_id,
                )
                _retire = CultivationProposal(
                    target_doc=proposal.target_doc, target_section=_old_section,
                    content=(f"Superseded by a newer polarity-flipped lesson "
                             f"(flip={cf.get('flip')}); human decide whether to retire."),
                    source_run_id=proposal.source_run_id,
                    confidence=proposal.confidence, change_type="retire",
                    target_title=_old_title, evidence=proposal.content.strip(),
                    auto_apply_ok=False, status="escalated",
                )
                write_proposal(_retire, project_dir)
                # new entry still appends below; the OLD stays until human approves.
            elif _matches:
                # ORDINARY knowledge → auto strip (clean forgetting).
                content = _strip_entries(content, {(_old_title, _old_section)},
                                         include_prose=True)
                logger.warning(
                    "[AUTO-SUPERSEDE] new lesson polarity-flips ordinary curated entry "
                    "— STRIPPED old (git-recoverable, no archive): %s § %s | flip=%s | "
                    "conflicting_title=%r | run=%s",
                    proposal.target_doc, _old_section, cf.get("flip"),
                    _old_title, proposal.source_run_id,
                )
                # Gate-2 MED (audit trail): a hard strip leaves only a transient
                # WARNING — record the deletion DURABLY in the changelog so the DDD
                # weekly report surfaces removals (and makes cross-run oscillation
                # visible) without git archaeology. Body recovery stays git-only (XG).
                _log_supersede_delete(
                    proposal, project_dir,
                    stripped_title=_old_title, stripped_section=_old_section,
                    stripped_body=next((e.raw_text for e in _matches), ""),
                )

        # Emit the canonical `- [type] **Title** — body (date, run, label)` shape.
        # Normalize (INSERT-ONLY) so the lifecycle engine's _ENTRY_RE can parse/decay/
        # reclaim/retire it — a raw untitled bullet (the pre-run_3e43c7ee format) is
        # structurally invisible to every autonomous path. _normalize_cultivated_bullet
        # is lossless + signature-invariant; None = degenerate → keep content unchanged.
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        source_label = f"{proposal.source_stage}" if proposal.source_stage != "reflect" else "auto-cultivated"
        # Prefer the author's DECLARED [type] over a prose guess (root-fix
        # run_c7e1e39d). The lesson's type is known at author-time; re-guessing it
        # from prose is the lossy-re-derivation root that skewed the corpus toward
        # pitfall/guideline. classify_entry_type is now the FALLBACK, fired only when
        # no valid declaration is present — mirroring ddd_entry_lifecycle.py:546's
        # `if not entry_type: guess`. (_normalize_cultivated_bullet:675 also honors
        # the tag, so all three type-parse sites now agree on the declared value.)
        from core.ddd_entry_lifecycle import classify_entry_type, VALID_TYPES
        _decl = re.match(r"^\[(\w+)\]\s+", proposal.content)
        if _decl and _decl.group(1).lower() in VALID_TYPES:
            _etype = _decl.group(1).lower()
        else:
            _etype = classify_entry_type(proposal.content)
        _normalized = _normalize_cultivated_bullet(proposal.content, _etype)
        _entry_body = _normalized if _normalized is not None else proposal.content
        entry = f"- {_entry_body} ({date_str}, {proposal.source_run_id}, {source_label})\n"

        # M1 fix: match section header at line start (## level only, not ###)
        section_re = re.compile(
            r"^## " + re.escape(proposal.target_section) + r"\s*$", re.MULTILINE
        )
        match = section_re.search(content)

        if match:
            # Compute body_start (the section's insert point) — used only to choose
            # WHERE to insert the new entry (newest-first under this
            # heading). The duplicate check itself is DOC-WIDE, not scoped here.
            line_end = content.find("\n", match.start())
            if line_end == -1:
                line_end = len(content)
            body_start = line_end + 1
            while body_start < len(content) and content[body_start] == "\n":
                body_start += 1

            # DOC-WIDE duplicate detection (root-cause fix, run_e9cb7e2a; measured
            # 2026-07-20: 170K archived bullets deduped to ~700 unique — 99.6% were
            # the SAME lesson re-written, many under DIFFERENT sections/dates/session
            # ids). A section-scoped check let the same lesson re-accumulate under a
            # different heading — the direct source of the silt. So sign EVERY `- `
            # bullet in the WHOLE document, not just this section.
            #   • content_signature() is FORMAT-AGNOSTIC — normalizes cultivation
            #     `- text (date,run)` AND writeback `- **date** (session): text` AND
            #     `[type]` markers to the same key, so a lesson dedups regardless of
            #     which writer/format/date/section produced it.
            #   • Signing BOTH sides (existing bullets AND incoming content) is what
            #     catches the writeback-format corpus (signing one side is a no-op).
            #   • Whole-STRING signature (not prefix / not substring) — a genuinely
            #     distinct lesson does NOT collide (guards the old adversarial HIGH:
            #     no dropping short lessons that are substrings of a longer one).
            # This is the ONE chokepoint every write path crosses (writeback /
            # reflect / retire-rewrite / HTTP), so doc-wide dedup here covers them
            # all — no per-path patching.
            existing_sigs = {
                content_signature(ln)
                for ln in content.splitlines()
                if ln.lstrip().startswith("- ")
            }
            existing_sigs.discard("")  # never dedup against empty (blank bullets)
            if content_signature("- " + proposal.content.strip()) in existing_sigs:
                return "duplicate"

            new_content = content[:body_start] + entry + content[body_start:]
            result_status = "applied"
        else:
            # Structural drift fix (run_45ab67c7): the allowlisted section is
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

        # Atomic write: write to temp, rename over original (inside the md_lock).
        tmp_path = doc_path.with_suffix(".tmp")
        tmp_path.write_text(new_content, encoding="utf-8")
        os.replace(str(tmp_path), str(doc_path))
        return result_status
    # md_lock releases + closes the fd on exit and NEVER unlinks the sidecar
    # (run_24d9f714 inode-race fix, preserved by construction).


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


# ── Topic tokenizer (module-level; extracted from _locate_target_entry's nested
# ── _tokens/_GENERIC in run_171a17c2 so detect_contradiction can reuse it
# ── READ-ONLY). Byte-behavior-identical to the former nested version.
# Generic DDD/engineering vocabulary that co-occurs across unrelated entries and
# carries no TOPIC identity. Gate-2 HIGH (run_b8f10185): without this filter a
# lesson about topic A matched topic B on shared structural words ("recovery"+
# "pattern"). NOTE (run_171a17c2): this denylist DELIBERATELY includes the
# polarity words never/always/should/would — they carry no *topic* identity, so
# they MUST be stripped for topic-matching. This is exactly why detect_contradiction
# runs its polarity test on RAW text, never on _entry_tokens output (Gate-1 FATAL
# catch: tokenizing would delete the very never/always the flip test needs).
_ENTRY_GENERIC = frozenset({
    "pattern", "patterns", "recovery", "approach", "works", "worked",
    "fix", "fixes", "fixed", "issue", "issues", "problem", "problems",
    "code", "test", "tests", "session", "sessions", "error", "errors",
    "gate", "check", "checks", "path", "paths", "case", "cases", "data",
    "system", "state", "value", "values", "call", "calls", "func", "function",
    "method", "class", "field", "fields", "change", "changes", "update",
    "using", "used", "when", "with", "that", "this", "from", "into", "must",
    "should", "would", "never", "always", "before", "after", "than", "then",
})


def _entry_tokens(text: str) -> set[str]:
    """Lowercase topic tokens (Latin ≥4 chars / CJK ≥2 chars) minus _ENTRY_GENERIC.
    Used for TOPIC identity only — NOT for polarity (polarity words are in the
    denylist by design). Module-level so both _locate_target_entry and
    detect_contradiction share one tokenizer (run_171a17c2 extract)."""
    return {
        w for w in re.findall(r"[a-zA-Z_]{4,}|[一-鿿]{2,}", text.lower())
        if w not in _ENTRY_GENERIC
    }


@dataclass
class ContradictionFlag:
    """Advisory signal: a newly-admitted APPEND lesson polarity-flips a same-topic
    curated entry. NON-BLOCKING, NON-DESTRUCTIVE — names the conflicting entry for
    a human/agent to judge; NEVER retires or resolves (do-not-delete-on-guesses).

    Recall boundary (honest): this catches EXPLICIT polarity flips only
    (never↔always, do↔don't, use↔avoid, enable↔disable, add↔remove, block↔allow,
    require↔forbid, CJK 要↔不要 / 应该↔不应该). Semantic/paraphrase contradiction is
    NOT token-detectable and remains the deferred D5-LLM job (ddd_health.py)."""
    conflicting_title: str  # exact title of the curated entry the new lesson flips
    section: str            # the curated entry's ## section
    flip: "tuple[str, str]"  # (word_in_existing, word_in_new) antonym pair that flipped
    shared_topic: str = ""  # a distinguishing shared object token (proves same-topic)

    def to_dict(self) -> dict:
        return {
            "conflicting_title": self.conflicting_title,
            "section": self.section,
            "flip": list(self.flip),
            "shared_topic": self.shared_topic,
        }


# Curated antonym pairs (ordered so each side maps to its partner). A "flip" =
# one text contains side-A and the other contains side-B (both as whole words).
# Kept SMALL + high-confidence on purpose — every added pair widens the false-
# positive surface (Principle 1: a false flag that survives poisons the brain).
_POLARITY_ANTONYMS: "tuple[tuple[str, str], ...]" = (
    ("never", "always"),
    ("don't", "do"),
    ("do not", "do"),
    ("avoid", "use"),
    ("disable", "enable"),
    ("remove", "add"),
    ("block", "allow"),
    ("forbid", "require"),
    ("reject", "accept"),
    ("不要", "要"),
    ("不应该", "应该"),
    ("禁止", "允许"),
)
# Whole-word matcher per polarity token (Latin uses word boundaries; CJK, which
# has no \b, uses substring — CJK tokens above are distinctive enough).
_POLARITY_TOKENS = {w for pair in _POLARITY_ANTONYMS for w in pair}


def _polarity_words_present(raw_lower: str) -> set[str]:
    """Which polarity tokens appear in raw_lower (whole-word for Latin, substring
    for CJK). Operates on RAW text — NOT _entry_tokens output (which strips them).

    Gate-2 HIGH (run_171a17c2): a NEGATION must not also register its POSITIVE
    partner. 'do not' contains a boundary-matchable 'do'; the CJK '不要' contains
    '要', '不应该' contains '应该'. Left uncorrected, the flip guard `a not in
    new_pol` silently DROPS a genuine flip when the negation is on the new side
    (existing 'do' vs new 'do not' → missed). Fix: after collecting, drop any
    token that is a PROPER SUBSTRING of another present token (longest wins) —
    so 'do not'→{'do not'}, '不要'→{'不要'}. Correct for both Latin & CJK."""
    present: set[str] = set()
    for tok in _POLARITY_TOKENS:
        if tok.isascii():
            if re.search(r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])", raw_lower):
                present.add(tok)
        else:
            if tok in raw_lower:
                present.add(tok)
    # Substring-subsumption: a token contained in a longer present token is an
    # artifact of that longer token (the positive partner leaking out of its
    # negation), not an independent occurrence — drop it.
    return {
        t for t in present
        if not any(t != other and t in other for other in present)
    }


def detect_contradiction(
    text: str, target_doc: str, project_dir: Path
) -> "ContradictionFlag | None":
    """ADVISORY, READ-ONLY: flag if `text` (a newly-admitted lesson) polarity-flips
    a same-topic curated entry in target_doc. Returns a ContradictionFlag naming the
    conflicting entry, or None. NEVER writes, NEVER retires, NEVER blocks admission,
    NEVER calls an LLM/embedding. Fail-safe: any missing doc / parse error → None.

    Two-stage, both conservative (run_171a17c2, Gate-1 hardened):
      1. TOPIC pre-filter — reuse _entry_tokens (same denylist as _locate_target_entry)
         to find the strongest same-topic curated entry: ≥2 non-generic token overlap
         AND ≥50% coverage AND a distinguishing (doc-frequency-1) shared token. This is
         the SAME strength gate the locate engine uses to avoid cross-topic collisions.
      2. POLARITY flip — on RAW lowercased text of BOTH the new lesson and the located
         entry (title + raw_text), check for an antonym pair where the existing entry
         carries side-A and the new lesson carries side-B (or vice-versa) AND they are
         NOT the same side. Runs on RAW text precisely because _entry_tokens strips
         never/always/should/would (Gate-1 FATAL catch).

    Only an explicit polarity flip fires — semantic contradiction is out of scope
    (honest recall boundary; that is the deferred D5-LLM job)."""
    if not text or not isinstance(text, str):
        return None
    try:
        doc_path = ddd_path(project_dir, target_doc)
        if not doc_path.exists():
            return None
        from core.ddd_entry_lifecycle import parse_entries
        content = doc_path.read_text(encoding="utf-8")
    except (OSError, ImportError, UnicodeDecodeError):
        return None

    entries = parse_entries(content, include_prose=True)
    if not entries:
        return None

    new_tokens = _entry_tokens(text)
    if not new_tokens:
        return None

    # Document-frequency of each title token (for the distinguishing-token gate —
    # denylist-independent proof the match is about THIS entry, not generic collision).
    title_tok_lists = [_entry_tokens(e.title) for e in entries]
    doc_freq: dict[str, int] = {}
    for tt in title_tok_lists:
        for w in tt:
            doc_freq[w] = doc_freq.get(w, 0) + 1

    # Stage 1: strongest same-topic entry (same floor as _locate_target_entry).
    best = None
    best_shared: set[str] = set()
    best_score = 0
    best_ratio = 0.0
    for e, title_tokens in zip(entries, title_tok_lists):
        if not title_tokens:
            continue
        shared = new_tokens & title_tokens
        overlap = len(shared)
        ratio = overlap / len(title_tokens)
        if overlap > best_score or (overlap == best_score and ratio > best_ratio):
            best_score = overlap
            best_ratio = ratio
            best = e
            best_shared = shared
    if best is None or best_score < 2 or best_ratio < 0.5:
        return None
    # Distinguishing token: a shared token unique to this entry's title doc-wide.
    distinguishing = [w for w in best_shared if doc_freq.get(w, 0) == 1]
    if not distinguishing:
        return None

    # Stage 2: polarity flip on RAW text (NOT tokens — they strip never/always).
    new_lower = text.lower()
    existing_lower = (best.title + " " + best.raw_text).lower()
    new_pol = _polarity_words_present(new_lower)
    existing_pol = _polarity_words_present(existing_lower)
    if not new_pol or not existing_pol:
        return None
    for a, b in _POLARITY_ANTONYMS:
        # existing carries side-A and new carries side-B (a genuine flip), and the
        # new lesson does NOT also carry side-A (which would be agreement, not flip).
        if a in existing_pol and b in new_pol and a not in new_pol:
            return ContradictionFlag(
                conflicting_title=best.title, section=best.section,
                flip=(a, b), shared_topic=sorted(distinguishing)[0],
            )
        if b in existing_pol and a in new_pol and b not in new_pol:
            return ContradictionFlag(
                conflicting_title=best.title, section=best.section,
                flip=(b, a), shared_topic=sorted(distinguishing)[0],
            )
    return None


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
    # Six-section resolver (READ, strangler-aware) — see apply_to_ddd note.
    doc_path = ddd_path(project_dir, target_doc)
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

    # Tokenize into topic tokens via the module-level _entry_tokens (extracted
    # run_171a17c2 so detect_contradiction can reuse the SAME topic-identity
    # denylist read-only, without duplicating it). Byte-behavior-identical to the
    # former nested _tokens/_GENERIC. See _ENTRY_GENERIC / _entry_tokens above.
    _tokens = _entry_tokens

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
    machinery (archive → identity-strip; recovery = archive + git). The sibling of apply_to_ddd
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
                         (original recoverable from archive + git)

    For rewrite the replacement is a NEW append (not a cross-file move): we retire
    the stale entry, then append the replacement via apply_to_ddd. If the append
    fails the retire already archived the original (recoverable), so no
    unrecoverable loss — but we surface the partial status.
    """
    if proposal.change_type not in ("retire", "rewrite"):
        return "not_retire"
    if not proposal.target_title:
        return "no_target"  # locator found nothing — refuse to guess

    # Six-section resolver (READ, strangler-aware) — see apply_to_ddd note. The
    # resolved path is also passed as retire_entry(source_path=...) below, so the
    # STRIP (the write-back that removes the entry) lands on the SAME doc we read.
    # NOTE: the retire ARCHIVE (<stem>-archive.md) is written by ddd_entry_lifecycle
    # at the project ROOT, NOT co-located with a migrated 2-understanding/ doc — this
    # is intentional (an archive is cold storage outside the ①→⑥ tree, and FTS5
    # rglob-indexes it wherever it lives, so recall is unaffected). Do not "fix" the
    # strip/archive to the same dir on that assumption.
    doc_path = ddd_path(project_dir, proposal.target_doc)
    if not doc_path.exists():
        return "doc_missing"

    # PREVENTION over recovery (run_e9cb7e2a, Gate-2 MED): for a REWRITE, validate the
    # replacement against the SAME value floor apply_to_ddd enforces — BEFORE we retire
    # the old entry. Otherwise a floor-rejected replacement would leave a half-state
    # (old entry archived + stripped, no replacement appended). Make rewrite
    # all-or-nothing: refuse up-front, fail-loud (client-correctable), retire nothing.
    if proposal.change_type == "rewrite":
        _repl = proposal.replacement_content.strip()
        if _repl and (len(_repl) < MIN_LESSON_LENGTH or not is_quality_lesson(_repl)):
            return "retire_failed:replacement below value floor (too short / not a lesson)"

    from core.ddd_entry_lifecycle import retire_entry, RetireError

    # Archive to the doc-matched archive (BLOCKER-1: retire_entry defaults to
    # IMPROVEMENT-archive.md; a TECH.md retire must archive to TECH-archive.md).
    stem = Path(proposal.target_doc).stem  # "IMPROVEMENT" | "TECH" | ...
    archive_name = f"{stem}-archive.md"

    # SHARED doc-write lock (run_06350217): retire_entry does a read-modify-STRIP of
    # doc_path (source_path=), a WRITE — it must hold the SAME <doc>.md.lock every
    # other writer (apply_to_ddd append, orchestrator decay/auto-apply/llm) uses, or
    # the strip races a concurrent append → lost update. Before this run, retire was
    # the one unlocked doc writer (Gate-1 run_06350217). Blocking. Scope is the
    # read→retire span ONLY — the rewrite-append below calls apply_to_ddd, which
    # RE-ACQUIRES this same lock, so it MUST run AFTER this `with` exits (else
    # self-deadlock on the same lock name).
    from utils.file_lock import md_lock
    try:
        with md_lock(doc_path, blocking=True):
            content = doc_path.read_text(encoding="utf-8")
            retire_entry(
                content,
                proposal.target_title,
                proposal.target_section,
                project_dir,
                archive_name=archive_name,
                source_path=doc_path,
                dry_run=False,
            )
    except (OSError, UnicodeDecodeError) as e:
        return f"retire_failed:read_error {type(e).__name__}"
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
        # Retire succeeded but append didn't — original is in archive + git.
        return f"rewrite_partial:{append_status}"

    return "retired"


def _log_supersede_delete(
    proposal: CultivationProposal, project_dir: Path, *,
    stripped_title: str, stripped_section: str, stripped_body: str,
) -> None:
    """Durably record an AUTO-SUPERSEDE deletion to the DDD changelog (Gate-2 MED,
    run_6ac7a760). A hard strip otherwise leaves only a transient WARNING; this
    makes removals visible in the DDD weekly report and makes cross-run
    oscillation auditable. Body is recorded truncated for context (full recovery
    stays git-only per XG). Non-blocking: a logging failure never aborts the apply."""
    try:
        changelog_path = project_dir / ".artifacts" / "ddd-changelog.jsonl"
        changelog_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "action": "auto-supersede-delete",
            "target_doc": proposal.target_doc,
            "stripped_title": stripped_title,
            "stripped_section": stripped_section,
            "stripped_body": (stripped_body or "")[:300],
            "superseded_by": proposal.content[:200],
            "flip": (proposal.contradiction_flag or {}).get("flip"),
            "source_run_id": proposal.source_run_id,
            "recovery": "git-only (no archive, XG-directed)",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with open(changelog_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("failed to log supersede-delete (non-blocking): %s", e)


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
    # Gate-2 MED (run_171a17c2): surface the advisory contradiction_flag on the
    # AUTO-APPLY path. Without this the flag was computed then discarded exactly
    # when a contradicting lesson silently lands in the doc (the case it exists to
    # make visible) — the escalate path serialized it via to_dict, but auto-apply
    # dropped it. Recording it in the changelog lets the DDD weekly report / audit
    # see "entry applied but contradicts curated entry X" without blocking the
    # append (advisory, non-destructive — do-not-delete-on-guesses).
    if proposal.contradiction_flag is not None:
        entry["contradiction_flag"] = proposal.contradiction_flag
        logger.warning(
            "DDD cultivation: applied append to %s/%s CONTRADICTS curated entry %r "
            "(polarity flip %s) — advisory, review recommended",
            proposal.target_doc, proposal.target_section,
            proposal.contradiction_flag.get("conflicting_title"),
            proposal.contradiction_flag.get("flip"),
        )
    with open(changelog_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Knowledge Admission: canonical adversarial outcome + trust stamp ──────────
# (Design: 2026-08-09-knowledge-admission-subsystem-design.md, step0 + Component B.)
# The trust stamp is the SOLE authority that lets a proposal auto-write into ANY doc
# (incl. SELF.md — no doc is carved out). So its derivation MUST be deterministic and
# fail-closed: only an explicit canonical enum earns trust; prose / absent / unknown /
# block NEVER yields 'passed'. A wrong 'passed' is a permanent evergreen auto-write
# (DEC19: False > Stale > Imperfect).

# Canonical machine-readable outcomes the adversarial/deliver stage MUST emit.
_GATE2_PASS_OUTCOMES = frozenset({"pass", "pass_with_fixes"})
_GATE2_BLOCK_OUTCOMES = frozenset({"block"})
_GATE2_ALL_OUTCOMES = _GATE2_PASS_OUTCOMES | _GATE2_BLOCK_OUTCOMES


def derive_gate2_outcome(run_state: dict) -> str:
    """Derive the canonical adversarial outcome from a run's stages.

    Returns one of ``{"pass", "pass_with_fixes", "block", "n/a"}``.

    FAIL-CLOSED contract (AC10): read ONLY the canonical ``gate2_outcome`` enum on a
    COMPLETED adversarial/deliver stage. A free-text ``gate2_verdict`` (prose) is NEVER
    heuristically parsed — prose-only, absent stage, or an unrecognized enum value all
    return ``"n/a"``. ``"n/a"`` is the safe default: it denies auto-apply to protected
    knowledge, forcing REVIEW.

    Two adversarial-hardened rules (a wrong ``pass`` is a permanent evergreen auto-write):
      • **BLOCK WINS over document order** — a ``pass`` on one stage must NEVER shadow a
        ``block`` on another. We scan ALL qualifying stages; if ANY says block → ``block``.
      • **STATUS-GATED** — a ``gate2_outcome`` is honored ONLY on a stage whose status is
        ``completed``/``done``. A stale ``pass`` on an in-progress/abandoned/failed stage is
        ignored (it is preliminary, not authoritative).
    """
    if not isinstance(run_state, dict):
        return "n/a"
    saw_pass: "str | None" = None
    for stage in run_state.get("stages", []):
        if not isinstance(stage, dict):
            continue
        if stage.get("stage") not in ("adversarial", "deliver"):
            continue
        if stage.get("status") not in ("completed", "done"):
            continue  # status-gated: preliminary/abandoned verdicts are not authoritative
        outcome = stage.get("gate2_outcome")
        if not (isinstance(outcome, str) and outcome in _GATE2_ALL_OUTCOMES):
            continue  # prose / dict / unknown enum → NOT parsed here
        if outcome in _GATE2_BLOCK_OUTCOMES:
            return "block"  # block wins immediately — no pass may shadow it
        # remember the strongest pass seen but keep scanning for a later block
        if saw_pass is None or outcome == "pass":
            saw_pass = outcome
    return saw_pass if saw_pass is not None else "n/a"


def trust_from_gate2_outcome(outcome: "str | None") -> str:
    """Map a canonical gate2 outcome → the proposal trust stamp.

    Returns one of ``{"passed", "failed", "n/a"}``. FAIL-CLOSED: only an explicit
    ``pass``/``pass_with_fixes`` yields ``"passed"``; ``block`` → ``"failed"``; ANY other
    value (``n/a``, ``None``, ``""``, an already-mapped ``"passed"``, or garbage) → ``"n/a"``.
    This never fabricates trust from an unexpected input.
    """
    if outcome in _GATE2_PASS_OUTCOMES:
        return "passed"
    if outcome in _GATE2_BLOCK_OUTCOMES:
        return "failed"
    return "n/a"


# Admission auto-band confidence floor (DEFAULT / base). Component D calibrates it
# PER-CHANNEL from proposal_feedback: a channel with poor precision gets a HIGHER auto
# bar (anti-runaway: the tracker only ever RAISES, never auto-lowers).
_AUTO_CONFIDENCE_THRESHOLD = 0.7


# mtime-keyed cache for channel_stats.json — admission_band calls _channel_auto_threshold
# once PER PROPOSAL, so an un-cached read re-parses the file N times per batch (measured
# ~6.6ms/5-proposal batch, Gate-2 perf flag). Key on (path, mtime) so a batch reuses one
# parse AND a fresh stats write (new mtime) invalidates immediately — no staleness.
_CHANNEL_STATS_CACHE: "dict[str, tuple[float, dict]]" = {}


def _channel_auto_threshold(
    channel: str, base: float, project_dir: "Path | None"
) -> float:
    """Per-channel calibrated auto threshold (Component D, AC6).

    Reads ``<project_dir>/.artifacts/channel_stats.json`` (written on the timer by
    ddd_orchestrator via ProposalFeedbackTracker.compute_channel_stats) and asks the
    tracker for the precision-adjusted threshold for this channel. A channel that keeps
    producing rejected auto-applies gets a HIGHER bar → fewer bad autos → the review
    burden it creates shrinks over time. Fail-safe: no stats / unreadable / no
    project_dir → the base default (never crashes cultivation). mtime-cached so a
    per-proposal batch reads+parses the file once, not N times.
    """
    if project_dir is None:
        return base
    try:
        from core.proposal_feedback import ProposalFeedbackTracker, THRESHOLD_FLOOR
        stats_file = Path(project_dir) / ".artifacts" / "channel_stats.json"
        if not stats_file.is_file():
            return max(base, THRESHOLD_FLOOR)
        key = str(stats_file)
        mtime = stats_file.stat().st_mtime
        cached = _CHANNEL_STATS_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            stats = cached[1]
        else:
            stats = json.loads(stats_file.read_text(encoding="utf-8"))
            _CHANNEL_STATS_CACHE[key] = (mtime, stats)
        return ProposalFeedbackTracker().get_adjusted_threshold(channel, base, stats)
    except Exception:  # noqa: BLE001 — calibration must never break admission
        return base


def apply_channel_self_corrections(project_dir: "Path | None") -> "list[dict]":
    """Consume check_self_correction (Component D, AC7) — the previously-DEAD half.

    For each channel that has accumulated ≥ SELF_CORRECTION_BATCH rejections with a
    dominant reason, surface the mapped fix recommendation as an action record. This
    CLOSES the loop that was 60% wired (stats computed + recommendation function
    existed, but NOTHING consumed it). Returned actions are logged + handed to the
    caller (the maintenance hook) so a persistently-bad channel's correction is
    ACTED ON / visible, not silently accrued. Fail-safe → [] on any error.
    """
    if project_dir is None:
        return []
    try:
        from core.proposal_feedback import ProposalFeedbackTracker
        stats_file = Path(project_dir) / ".artifacts" / "channel_stats.json"
        if not stats_file.is_file():
            return []
        stats = json.loads(stats_file.read_text(encoding="utf-8"))
        tracker = ProposalFeedbackTracker()
        actions: list[dict] = []
        for channel in stats:
            rec = tracker.check_self_correction(channel, stats)
            if rec:
                logger.info(
                    "admission self-correction: channel=%s reason=%s fix=%s (rejections=%s)",
                    rec.get("channel"), rec.get("reason"), rec.get("fix_type"),
                    rec.get("rejection_count"),
                )
                actions.append(rec)
        return actions
    except Exception:  # noqa: BLE001 — never break the maintenance hook
        return []


def admission_band(
    proposal: "CultivationProposal", project_dir: "Path | None"
) -> "tuple[str, str]":
    """The Knowledge Admission decision band (Component C). Returns (verdict, reason)
    where verdict ∈ {"auto", "review", "discard"}.

    TRUST IS THE SOLE AUTHORITY (AC11) — there is NO hardcoded doc/section whitelist.
    A ``trust=passed`` proposal (its source run cleared Gate-2) may AUTO-apply into ANY
    doc, INCLUDING SELF.md / PRODUCT.md / TECH.md/Architecture — the zones the old
    ``_check_safe_doc``/``SAFE_APPEND_SECTIONS``/``_PROTECTED_ZONES`` hardcoded shut.
    Authority moved from "which doc is it?" to "did the producing run survive
    adversarial review?".

    Bands:
      • DISCARD — is_noise (machine broadcast / instance-log / fragment).
      • AUTO    — trust=passed AND reused quality checks clean (magnitude + circuit
                  breaker; the doc-whitelist check is DROPPED) AND confidence ≥ the
                  auto threshold. Retire/rewrite are never auto here (change_type gate).
      • REVIEW  — everything else: trust∈{failed,n/a}, a hard quality block, a gate
                  error (FAIL-CLOSED — DEC19), or confidence below the auto floor.
    """
    # 1. noise → discard (before any work)
    _noise, _nreason = is_noise(proposal.content or "")
    if _noise:
        return ("discard", f"noise:{_nreason}")

    # 2. trust is the gate. Only an explicit Gate-2 pass is auto-eligible.
    if proposal.passed_adversarial_gate != "passed":
        return ("review", f"trust:{proposal.passed_adversarial_gate}")

    # destructive changes (retire/rewrite) are never auto-applied via this band
    if proposal.change_type != "append":
        return ("review", "non_append")

    # 3. reused quality checks — but WITHOUT the doc-whitelist (that is what trust
    #    replaces). Fail-closed: any gate error → review, never auto.
    try:
        from core.ddd_auto_approval import evaluate_auto_approval
        decision = evaluate_auto_approval(proposal, project_dir)
        criteria = decision.criteria_met
    except Exception as e:  # noqa: BLE001 — gate must never crash cultivation
        logger.warning(
            "admission_band: quality gate errored (%s: %s) → FAIL-CLOSED review for %s § %s",
            type(e).__name__, e, proposal.target_doc, proposal.target_section,
        )
        return ("review", "gate_error")

    # HARD blocks that survive the whitelist removal: magnitude + circuit breaker.
    # (safe_target_doc is DELIBERATELY excluded — trust supersedes the doc whitelist.
    # maturity/conflict/precision stay SOFT: logged, not blocking, as in the prior gate.)
    if not criteria.get("small_magnitude", True):
        return ("review", "too_large")
    if not criteria.get("circuit_breaker_ok", True):
        return ("review", "circuit_breaker")

    # 4. confidence floor — PER-CHANNEL calibrated (Component D, AC6): a channel with
    #    poor precision gets a raised bar, so bad-auto-producing channels self-tighten.
    threshold = _channel_auto_threshold(
        proposal.source_stage, _AUTO_CONFIDENCE_THRESHOLD, project_dir
    )
    if proposal.confidence < threshold:
        return ("review", f"below_auto_threshold:{proposal.confidence:.2f}<{threshold:.2f}")

    return ("auto", "trust_passed")


def stamp_trust_from_run(run_id: "str | None", project_dir: "Path | None") -> str:
    """Resolve a proposal's source run → its trust stamp {passed, failed, n/a}.

    Reads ``<project_dir>/.artifacts/runs/<run_id>/run.json``, derives the canonical
    gate2 outcome (derive_gate2_outcome), and maps it (trust_from_gate2_outcome).

    FAIL-CLOSED on EVERYTHING that isn't an explicit Gate-2 pass: a non-run source id
    (``code_intel_drift:x``, a session decision), a missing/unreadable run.json, a bad
    project_dir, or a run with no canonical enum → ``"n/a"`` (never ``"passed"``).
    A wrong ``passed`` is a permanent evergreen auto-write (DEC19).
    """
    if not run_id or not isinstance(run_id, str) or project_dir is None:
        return "n/a"
    # non-run source ids (feeds/session) are not pipeline runs → never trusted.
    if not run_id.startswith("run_"):
        return "n/a"
    # PATH-TRAVERSAL DEFENSE (self-probe: `run_x/../../Other/.../run_win` resolves OUT of
    # this project to another run that DID pass Gate-2 — a trust-forging exploit). A real
    # run_id is a flat token; reject anything with path separators or `..`. Same charset
    # discipline ui_actions._probe already uses. Fail-closed → never trust a crafted id.
    if not re.fullmatch(r"run_[A-Za-z0-9_-]{1,64}", run_id):
        return "n/a"
    try:
        run_dir = (Path(project_dir) / ".artifacts" / "runs" / run_id).resolve()
        runs_root = (Path(project_dir) / ".artifacts" / "runs").resolve()
        # containment check (defense-in-depth): the resolved run dir MUST stay under this
        # project's runs/ root — a belt to the charset suspenders.
        if runs_root not in run_dir.parents and run_dir != runs_root:
            return "n/a"
        run_file = run_dir / "run.json"
        if not run_file.is_file():
            return "n/a"
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "n/a"  # unreadable / malformed → fail closed
    return trust_from_gate2_outcome(derive_gate2_outcome(run_state))


def write_proposal(proposal: CultivationProposal, project_dir: Path) -> Path:
    """Write a proposal as an atomic JSON file (escalation path).

    Used only for RISKY changes that need human approval.
    Creates .artifacts/proposals/ directory if needed.
    """
    proposals_dir = project_dir / ".artifacts" / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)

    # Generation-side dedup (run_97519f7c, Gate-1 root-fix): the churn ROOT was that
    # write_proposal wrote a fresh timestamped JSON unconditionally, so a re-scan of the
    # same reflect input re-emitted the same-content lesson under a NEW id every run —
    # read_pending_proposals only dedups at DISPLAY, the on-disk pending set kept silting.
    # Skip the write if an AWAITING-human proposal with the same content_signature already
    # exists. Only vs live (pending/escalated) proposals — a terminal (rejected/applied)
    # one is not a live dup, so a genuinely-recurring lesson can re-surface after triage.
    # Scope the content-dedup to APPEND proposals ONLY (Gate-2 correctness MED,
    # run_97519f7c): retire/rewrite proposals carry a TEMPLATE content string
    # (e.g. "Superseded by a newer polarity-flipped lesson (flip=[...])") whose
    # distinguishing target lives in target_title/section, NOT the content — so two
    # DISTINCT retire targets flipped by the same polarity pair would collide on a
    # content-only signature and silently drop the second human-review item. The churn
    # this dedup targets is append-lesson re-emission; retire/rewrite are exempt.
    _ct = getattr(proposal, "change_type", "append") or "append"
    new_sig = content_signature("- " + (proposal.content or "").strip()) if _ct == "append" else ""
    if new_sig:
        for existing in proposals_dir.glob("*.json"):
            try:
                data = json.loads(existing.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("status") not in AWAITING_HUMAN_STATUSES:
                continue
            if content_signature("- " + str(data.get("content", "")).strip()) == new_sig:
                return existing  # idempotent — the live pending proposal already carries it

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

    pending = []
    for filepath in proposals_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text())
            proposal = CultivationProposal.from_dict(data)

            if proposal.status not in AWAITING_HUMAN_STATUSES:
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


# ── Learning-fidelity observability (run_abf49550 M0) ──────────────────────
# The cultivation subsystem is the agent's LEARNING ORGAN. Before M0 its failure
# modes were invisible: a write failure (locked/doc_missing) was bucketed with
# healthy rejects, and nothing surfaced whether the brain had silently stopped
# learning. These two functions make per-project outcomes DURABLE (survive daemon
# restart) and computable into a learning-fidelity baseline. Deliberately a
# DEDICATED sink — NOT ddd-changelog.jsonl, whose consumers (_read_changelog,
# _build_changelog_index) count EVERY entry with no action filter (Gate-1 verified),
# so failure records there would pollute the weekly report + usage-health.

_CULTIVATION_OUTCOMES_FILE = "cultivation-outcomes.jsonl"


def record_cultivation_outcome(project_dir: Path, result: dict) -> None:
    """Append a per-project cultivation OUTCOME record to the durable sink.

    result is a _cultivate_proposals() return dict (applied/rejected/write_failed/
    escalated). Best-effort append-only JSONL — a recording failure must NEVER
    break cultivation itself (the learning organ keeps working even if we can't
    log its health). run_abf49550 M0 / AC2.
    """
    try:
        sink = project_dir / ".artifacts" / _CULTIVATION_OUTCOMES_FILE
        sink.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "applied": int(result.get("applied", 0)),
            "rejected": int(result.get("rejected", 0)),
            "write_failed": int(result.get("write_failed", 0)),
            "escalated": int(result.get("escalated", 0)),
            "retired": int(result.get("retired", 0)),
        }
        with open(sink, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError) as exc:
        # Observability must never break the organ it observes (STEERING #2 /
        # O030 family). A failed record is logged loud but non-fatal.
        logger.warning("record_cultivation_outcome failed (non-fatal): %s", exc)


def read_cultivation_health(project_dir: Path, window_days: int = 7) -> dict:
    """Aggregate per-project cultivation outcomes over a window into a health block.

    Read-only. Returns the learning-fidelity baseline + the single north-star flag
    `silent_learning_failure` = (write_failed > 0 in the window). window_days=7
    matches the weekly-report cadence. run_abf49550 M0 / AC1+AC2.
    """
    health = {
        "window_days": window_days,
        "applied": 0,
        "healthy_reject": 0,
        "write_failed": 0,
        "escalated": 0,
        "silent_learning_failure": False,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    sink = project_dir / ".artifacts" / _CULTIVATION_OUTCOMES_FILE
    if not sink.exists():
        return health
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        for line in sink.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue  # skip a corrupt/undated line, never crash the read
            if ts < cutoff:
                continue
            health["applied"] += int(rec.get("applied", 0))
            health["healthy_reject"] += int(rec.get("rejected", 0))
            health["write_failed"] += int(rec.get("write_failed", 0))
            health["escalated"] += int(rec.get("escalated", 0))
    except OSError as exc:
        logger.warning("read_cultivation_health failed (non-fatal): %s", exc)
        return health
    health["silent_learning_failure"] = health["write_failed"] > 0
    return health


# Workspace-GLOBAL cultivation health (run_abf49550 M0, Gate-1 two-grain fix).
# drops/channel-timeouts/channel-errors happen at the workspace-level drain
# (lifecycle_manager._process_cultivation_events), NOT per-project — so they persist
# to a workspace-level sink, never mis-attributed to a project's health.
_WORKSPACE_CULTIVATION_FILE = "cultivation-workspace-health.jsonl"


def record_workspace_cultivation_health(
    root: Path, *, findings: list[str] | None = None, dropped: int = 0
) -> None:
    """Append a workspace-level drain outcome (parsed from executor findings +
    dispatcher.dropped_count) to the durable workspace sink. Best-effort; never
    breaks the maintenance loop (the except-swallow at the drain still stands —
    this only ADDS a surfaced record). run_abf49550 M0 / AC2.

    Restart limitation (documented, M0): dropped_count is in-memory in the
    dispatcher singleton; a daemon restart between 30-min drains loses the
    interim count before it is recorded here. Restart-safe capture is deferred
    to M1.
    """
    try:
        findings = findings or []
        # Prefix-anchored (Gate-2 MED): the executor emits "CHANNEL_TIMEOUT: …" /
        # "CHANNEL_ERROR: …" at the START of a finding (cultivation_dispatcher.py:257-273).
        # A substring test would double-count a finding that merely MENTIONS both tokens.
        timeouts = sum(1 for f in findings if f.startswith("CHANNEL_TIMEOUT"))
        errors = sum(1 for f in findings if f.startswith("CHANNEL_ERROR"))
        sink = root / ".artifacts" / _WORKSPACE_CULTIVATION_FILE
        sink.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel_timeouts": timeouts,
            "channel_errors": errors,
            "dropped_events": int(dropped),
        }
        with open(sink, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except (OSError, ValueError, TypeError) as exc:
        logger.warning("record_workspace_cultivation_health failed (non-fatal): %s", exc)


def read_workspace_cultivation_health(root: Path, window_days: int = 7) -> dict:
    """Aggregate workspace-level drain health over a window. Read-only.
    silent_learning_failure = any drop / timeout / channel-error in-window.
    run_abf49550 M0 / AC1+AC2."""
    health = {
        "window_days": window_days,
        "channel_timeouts": 0,
        "channel_errors": 0,
        "dropped_events": 0,
        "silent_learning_failure": False,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
    sink = root / ".artifacts" / _WORKSPACE_CULTIVATION_FILE
    if not sink.exists():
        return health
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    try:
        for line in sink.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                ts = datetime.fromisoformat(rec.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except (json.JSONDecodeError, ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            health["channel_timeouts"] += int(rec.get("channel_timeouts", 0))
            health["channel_errors"] += int(rec.get("channel_errors", 0))
            health["dropped_events"] += int(rec.get("dropped_events", 0))
    except OSError as exc:
        logger.warning("read_workspace_cultivation_health failed (non-fatal): %s", exc)
        return health
    health["silent_learning_failure"] = (
        health["channel_timeouts"] > 0
        or health["channel_errors"] > 0
        or health["dropped_events"] > 0
    )
    return health


def _cultivate_proposals(
    proposals: List[CultivationProposal], project_dir: Path
) -> dict:
    """Apply or escalate a list of proposals. Shared by all cultivate_from_* entry points.

    Auto-approval gate (ddd_auto_approval) adds maturity, magnitude, precision,
    circuit breaker, and conflict checks on top of is_safe_append().

    Returns:
        {"applied": N, "escalated": M, "rejected": K, "write_failed": W,
         "retired": R, "skipped_protected": S, "drift_errors": [...]}

    skipped_protected (run_97519f7c): proposals dropped because their target is a
    protected zone (human-distill-only) — NOT escalated (that would be a
    dead-on-approve queue entry), NOT a failure. A healthy admission decision.

    write_failed (run_abf49550 M0): apply_to_ddd returned "locked"/"doc_missing" —
    a genuine WRITE FAILURE (learning organ could not write), kept DISTINCT from
    "rejected" (healthy discern: duplicate/low_value/not_safe). Pre-M0 both
    collapsed into "rejected", making a broken write indistinguishable from a
    discerning one — the exact learning-fidelity blind spot M0 exists to remove.

    drift_errors surfaces section-name drift LOUDLY (a config bug where a
    allowlisted routing section has no matching heading in the doc) instead of
    silently counting it as a benign "rejected". See apply_to_ddd docstring /
    run_45ab67c7 root cause.

    run_ecc7a32b: a HIGH-CONFIDENCE retire proposal (auto_apply_ok) AUTO-APPLIES
    via apply_retire_proposal (reversible: archive+bak+strip), up to
    MAX_AUTO_RETIRES_PER_RUN; beyond the cap, or if not confident, it ESCALATES.
    """
    applied = 0
    escalated = 0
    rejected = 0
    write_failed = 0
    retired = 0
    skipped_protected = 0
    drift_errors: List[str] = []

    for proposal in proposals:
        # Admission root-fix (run_97519f7c) + trust cutover (run_8d5fe9d1): a
        # protected-zone target (SELF / PRODUCT>Vision,Non-Goals,Strategic / TECH>
        # Architecture) that is NOT trust=passed is human-distill-only — sediment it
        # UP to the hand-distill candidates sink (not the review queue: it would be
        # DEAD-ON-APPROVE). BUT a trust=passed proposal (its run cleared Gate-2) is
        # now AUTO-eligible into these very zones (Component C, AC11) — it must FALL
        # THROUGH to admission_band, NOT be pre-dropped here. So this pre-drop fires
        # ONLY for the un-trusted case.
        if (proposal.passed_adversarial_gate != "passed"
                and is_protected_zone(proposal.target_doc, proposal.target_section)):
            # Sediment UP, not to a landfill (Principle 1 + Gate-2 red-team MED): a
            # protected-zone lesson is human-distill-only, but dropping it to a DEBUG
            # log is a graveyard — a human never sees the architecture/SELF lessons
            # they should hand-write. Append it to a durable candidates sink the DDD
            # weekly report surfaces ("lessons for you to hand-distill into TECH.md>
            # Architecture / SELF / PRODUCT>Vision"). Best-effort — never break cultivation.
            try:
                cand_dir = project_dir / ".artifacts"
                cand_dir.mkdir(parents=True, exist_ok=True)
                with (cand_dir / "protected-zone-candidates.jsonl").open("a", encoding="utf-8") as _cf:
                    _cf.write(json.dumps({
                        "target_doc": proposal.target_doc,
                        "target_section": proposal.target_section,
                        "content": (proposal.content or "")[:500],
                        "source_run_id": proposal.source_run_id,
                        "confidence": proposal.confidence,
                    }, ensure_ascii=False) + "\n")
            except OSError as _e:
                logger.warning("cultivation: protected-zone candidate sink write failed: %s", _e)
            logger.debug(
                "cultivation: protected-zone lesson → human-distill sink (not escalated): %s § %s",
                proposal.target_doc, proposal.target_section,
            )
            skipped_protected += 1
            continue

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
                        "archived + git): %s § %s | evidence=%s | run=%s",
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
        # ── Admission decision band (Component C, run_8d5fe9d1) ──────────────────
        # TRUST replaces the old doc-whitelist (is_safe_append/_PROTECTED_ZONES): a
        # trust=passed proposal may auto-apply into ANY doc incl SELF.md; else review;
        # noise → discard. Fail-closed (gate error → review). admission_band owns the
        # whole auto/review/discard decision for appends now.
        _verdict, _breason = admission_band(proposal, project_dir)
        if _verdict == "discard":
            # Noise never reaches the queue OR the doc — silently dropped, logged.
            logger.info(
                "admission: DISCARD %s § %s (%s): %.80s",
                proposal.target_doc, proposal.target_section, _breason, proposal.content,
            )
            continue
        if _verdict == "review":
            proposal.status = "escalated"
            write_proposal(proposal, project_dir)
            escalated += 1
            continue
        if _verdict == "auto":
            status = apply_to_ddd(proposal, project_dir)
            if status == "applied":
                proposal.status = "applied"
                log_application(proposal, project_dir)
                applied += 1
            elif status == "created_section":
                # The lesson WAS applied (not dropped) — the allowlisted section
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
                    f"DDD drift (auto-healed): created missing allowlisted section "
                    f"'{_safe(proposal.target_doc)} § {_safe(proposal.target_section)}' "
                    f"(run {_safe(proposal.source_run_id)}). Lesson applied to the new "
                    f"section. Reconcile the doc template / ROUTING_TABLE to avoid drift."
                )
                logger.warning(msg)
                drift_errors.append(msg)
                proposal.status = "applied"
                log_application(proposal, project_dir, created_section=True)
                applied += 1
            elif status in ("locked", "doc_missing"):
                # WRITE FAILURE (run_abf49550 M0 / AC3) — the lesson was NOT
                # applied because cultivation COULD NOT WRITE: another writer held
                # the lock ("locked"), or the target doc is missing ("doc_missing").
                # This is the learning organ FAILING, categorically distinct from a
                # HEALTHY reject below. Counting it as "rejected" (the pre-M0 bug)
                # made a broken brain indistinguishable from a discerning one.
                write_failed += 1
            else:
                # Healthy reject: "duplicate", "rejected_low_value", "not_safe" —
                # the brain DISCERNED and declined to write. Nothing failed.
                rejected += 1
            continue

    result = {
        "applied": applied,
        "escalated": escalated,
        "rejected": rejected,
        "write_failed": write_failed,
        "retired": retired,
        "skipped_protected": skipped_protected,
        "drift_errors": drift_errors,
    }
    # M0 (run_abf49550): persist this batch's outcome to the durable per-project
    # sink so learning-fidelity is observable (AC2). Best-effort — never breaks
    # cultivation. Skip a fully-empty batch (nothing to observe).
    # skipped_protected included (Gate-2 red-team MED, run_97519f7c): an all-skipped
    # batch (only protected-zone lessons) must still record — else the volume of
    # auto-dropped architecture/SELF lessons is invisible to the weekly learning report.
    if applied or escalated or rejected or write_failed or retired or skipped_protected:
        record_cultivation_outcome(project_dir, result)
    return result


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
