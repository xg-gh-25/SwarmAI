"""
DDD Cultivation Engine — Tiered Autonomy Model.

Connects Pipeline REFLECT output to DDD documents with graduated autonomy:
- ADDITIVE changes (new lessons, patterns): auto-applied, logged to changelog
- RISKY changes (modify/delete/contradict): escalated via proposal queue

Zero LLM calls — pure keyword heuristic filtering.

Public API:
    CultivationProposal  — data model for a single proposal
    filter_lessons_for_ddd(lessons, run_id, project) → List[CultivationProposal]
    apply_to_ddd(proposal, project_dir) → str (applied|duplicate|section_not_found|not_safe|doc_missing|locked)
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
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CultivationProposal":
        """Deserialize from dict."""
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
        """Determine if this proposal is a safe additive change (auto-apply)."""
        allowed = SAFE_APPEND_SECTIONS.get(self.target_doc)
        if allowed is None:
            return False  # PRODUCT.md, PROJECT.md changes need escalation
        return self.target_section in allowed


def _classify_lesson(lesson: str, project: str = "SwarmAI") -> Optional[tuple]:
    """Classify a lesson into target_doc and target_section.

    Delegates to the unified classify_content() from persist_routing.py.
    Returns (target_doc, target_section, confidence) or None if rejected.
    """
    stripped = lesson.strip()

    # Reject empty or too short
    if len(stripped) < MIN_LESSON_LENGTH:
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
    lessons: List[str], run_id: str, project: str
) -> List[CultivationProposal]:
    """Filter pipeline REFLECT lessons into DDD cultivation proposals.

    Pure function — no side effects, no I/O, no LLM calls.
    Returns at most MAX_PROPOSALS_PER_RUN proposals.
    """
    proposals = []

    for lesson in lessons:
        if not lesson or not isinstance(lesson, str):
            continue

        classification = _classify_lesson(lesson, project=project)
        if classification is None:
            continue

        target_doc, target_section, confidence = classification

        proposal = CultivationProposal(
            target_doc=target_doc,
            target_section=target_section,
            content=lesson.strip(),
            source_run_id=run_id,
            confidence=confidence,
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

            # Duplicate detection scoped to THIS section, matched on whole bullet
            # lines (not raw substring) — a shorter lesson that is a substring of a
            # longer existing bullet is NOT a duplicate.
            existing_contents = {
                _extract_bullet_content(ln)
                for ln in section_body.splitlines()
                if ln.lstrip().startswith("- ")
            }
            if proposal.content.strip() in existing_contents:
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
    """Read all pending (non-expired, non-resolved) proposals for a project.

    These are RISKY changes awaiting human approval (escalations only).
    """
    proposals_dir = workspace_dir / "Projects" / project / ".artifacts" / "proposals"

    if not proposals_dir.exists():
        return []

    pending = []
    for filepath in proposals_dir.glob("*.json"):
        try:
            data = json.loads(filepath.read_text())
            proposal = CultivationProposal.from_dict(data)

            if proposal.status != "pending":
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
        {"applied": N, "escalated": M, "rejected": K, "drift_errors": [...]}

    drift_errors surfaces section-name drift LOUDLY (a config bug where a
    whitelisted routing section has no matching heading in the doc) instead of
    silently counting it as a benign "rejected". See apply_to_ddd docstring /
    run_45ab67c7 root cause.
    """
    applied = 0
    escalated = 0
    rejected = 0
    drift_errors: List[str] = []

    for proposal in proposals:
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
        {"applied": N, "escalated": M, "rejected": K}
    """
    proposals = filter_lessons_for_ddd(lessons, run_id, project)
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
        {"applied": N, "escalated": M, "rejected": K}
    """
    proposals = filter_lessons_for_ddd(corrections, session_id, project)

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
        {"applied": N, "escalated": M, "rejected": K}
    """
    proposals = filter_lessons_for_ddd(decisions, session_id, project)
    for p in proposals:
        p.source_stage = "decision"
    return _cultivate_proposals(proposals, project_dir)
