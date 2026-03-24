"""Escalation Protocol — human-in-the-loop for the autonomous pipeline.

Provides structured escalation from any pipeline stage when Swarm needs
human judgment.  v2 implements all three levels with Radar todo integration.

Three escalation levels:

- **L0 INFORM** — "FYI, I did this."  Pipeline continues.  No action needed.
- **L1 CONSULT** — "I chose X, override within 24h."  Pipeline continues.
  Auto-accepts recommendation on timeout.  Creates Radar todo for async review.
- **L2 BLOCK** — "I need your input."  Pipeline pauses until human responds.
  Creates high-priority Radar todo.

Escalation data is delivered to the frontend via SSE ``escalation`` events
and rendered by ``EscalationBlock.tsx``.

At L0 (no project), escalations are ephemeral (in-chat only).
At L1+ (project exists), escalations are persisted to ``.artifacts/escalations/``
and appear in Radar todos for async review.

Public API:
  - ``inform()``            — L0: emit FYI annotation (no action needed)
  - ``consult()``           — L1: emit override-window question (pipeline continues)
  - ``block()``             — L2: emit blocking question (pipeline pauses)
  - ``resolve()``           — Resolve an open escalation
  - ``resolve_expired()``   — Auto-resolve expired L1 CONSULTs
  - ``create_radar_todo()`` — Create Radar todo from L1/L2 escalation
  - ``build_sse_event()``   — Build SSE event dict from an Escalation
"""

from __future__ import annotations

import json
import logging
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Escalation levels and trigger types
# ─────────────────────────────────────────────────────────────────────────────

class Level(IntEnum):
    """Escalation urgency level."""
    INFORM = 0    # FYI — pipeline continues, no action needed
    CONSULT = 1   # Override window — pipeline continues, timeout auto-accepts (v2)
    BLOCK = 2     # Paused — pipeline waits for human decision


# Machine-readable trigger types. Skills reference these in their
# escalation_triggers SKILL.md metadata.
TRIGGER_TYPES = frozenset({
    # EVALUATE stage
    "AMBIGUOUS_SCOPE",           # L2: can't determine what "done" looks like
    "CONFLICTING_PRIORITIES",    # L2: PRODUCT.md priorities contradict
    "LOW_CONFIDENCE_ROI",        # L1: borderline ROI (2.5-3.5)
    "MISSING_INFORMATION",       # L2: can't answer 2+ of 4 DDD questions
    "CLEAR_EVALUATION",          # L0: high-confidence go/defer/reject

    # THINK stage (research + alternatives)
    "INCONCLUSIVE_RESEARCH",     # L1: multiple contradictory sources
    "NO_CLEAR_WINNER",           # L1: all alternatives have similar tradeoffs
    "CLEAR_RECOMMENDATION",      # L0: clear winner with evidence

    # PLAN stage (design doc)
    "UNCOMMITTED_DEPENDENCY",    # L2: design requires new dep / API change
    "DEVIATES_FROM_TECH",        # L1: approach differs from TECH.md conventions
    "FOLLOWS_PATTERNS",          # L0: design follows established patterns

    # BUILD stage
    "EXCEEDS_SCOPE",             # L2: changes exceed design_doc scope
    "IMPLEMENTATION_DIFFERS",    # L1: practical constraint forced deviation
    "BUILT_AS_DESIGNED",         # L0: implemented per design

    # REVIEW stage (code review + security)
    "CRITICAL_SECURITY_FINDING", # L2: high-confidence security issue
    "NEEDS_HUMAN_JUDGMENT",      # L1: medium findings, human decides severity
    "CLEAN_REVIEW",              # L0: no findings or low-severity only

    # TEST stage (QA)
    "WTF_GATE_TRIGGERED",        # L2: fix attempts getting risky
    "UNEXPECTED_REGRESSION",     # L2: failures outside changeset scope
    "FLAKY_TESTS",               # L1: flaky — skip or investigate?
    "ALL_PASS",                  # L0: all tests pass

    # DELIVER stage
    "UNRESOLVED_ESCALATIONS",    # L2: open L1 consultations from earlier stages
    "PR_NEEDS_POLISH",           # L1: PR description needs human touch
    "CLEAN_DELIVERY",            # L0: ready for merge

    # REFLECT stage
    "CONTRADICTS_LESSON",        # L1: new lesson contradicts IMPROVEMENT.md entry
    "LESSONS_CAPTURED",          # L0: added N lessons to IMPROVEMENT.md

    # Cross-cutting (any stage)
    "FIRST_TIME_DOMAIN",         # L1: novel domain with no prior history
    "COST_THRESHOLD",            # L2: operation exceeds token/cost budget
    "RESOURCE_CONTENTION",       # L2: too many concurrent pipelines
    "NON_OBVIOUS_CHOICE",        # L1: generic — used when no specific trigger fits
})


# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Option:
    """A choice the human can make."""
    label: str
    description: str
    risk: str | None = None          # "low" / "medium" / "high"
    is_recommendation: bool = False


@dataclass
class Escalation:
    """A self-contained decision packet.

    Contains everything the human needs to decide without switching context.
    """
    id: str
    level: int                        # 0, 1, 2
    trigger: str                      # Machine-readable trigger type

    # Context
    title: str                        # One-line summary
    situation: str                    # What happened and why escalation fired
    options: list[Option] = field(default_factory=list)
    recommendation: str | None = None # Swarm's pick (None for L2 = genuinely unsure)
    evidence: list[str] = field(default_factory=list)

    # Pipeline context
    project: str | None = None
    pipeline_stage: str = ""          # evaluate/think/plan/build/review/test/deliver
    upstream_artifacts: list[str] = field(default_factory=list)

    # Lifecycle
    status: str = "open"              # open / resolved / expired
    created_at: str = ""
    timeout_at: str | None = None
    resolved_at: str | None = None
    resolved_by: str | None = None    # "user" / "timeout" / "swarm"
    resolution: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Factory functions
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_id() -> str:
    return f"esc_{uuid4().hex[:8]}"


def inform(
    title: str,
    situation: str,
    *,
    trigger: str = "CLEAR_EVALUATION",
    pipeline_stage: str = "",
    project: str | None = None,
    evidence: list[str] | None = None,
) -> Escalation:
    """Create an L0 INFORM escalation — FYI, no action needed.

    The pipeline continues immediately.  This is an annotation, not a gate.
    """
    return Escalation(
        id=_make_id(),
        level=Level.INFORM,
        trigger=trigger,
        title=title,
        situation=situation,
        evidence=evidence or [],
        project=project,
        pipeline_stage=pipeline_stage,
        status="resolved",       # L0 is auto-resolved on creation
        created_at=_now_iso(),
        resolved_at=_now_iso(),
        resolved_by="swarm",
    )


def block(
    title: str,
    situation: str,
    options: list[Option],
    *,
    trigger: str = "AMBIGUOUS_SCOPE",
    recommendation: str | None = None,
    pipeline_stage: str = "",
    project: str | None = None,
    evidence: list[str] | None = None,
    upstream_artifacts: list[str] | None = None,
) -> Escalation:
    """Create an L2 BLOCK escalation — pipeline pauses until human decides.

    Args:
        title: One-line summary (e.g. "Ambiguous scope: improve performance")
        situation: Full context (2-3 sentences)
        options: Choices for the human. Include a "Discuss" option if helpful.
        trigger: Machine-readable trigger type from TRIGGER_TYPES.
        recommendation: Swarm's pick, or None if genuinely unsure.
        pipeline_stage: Current pipeline stage (evaluate/think/plan/etc.)
        project: Project name, or None for L0 sessions.
        evidence: DDD doc excerpts, artifact references, data points.
        upstream_artifacts: IDs of artifacts consumed in this pipeline run.
    """
    return Escalation(
        id=_make_id(),
        level=Level.BLOCK,
        trigger=trigger,
        title=title,
        situation=situation,
        options=options,
        recommendation=recommendation,
        evidence=evidence or [],
        project=project,
        pipeline_stage=pipeline_stage,
        upstream_artifacts=upstream_artifacts or [],
        status="open",
        created_at=_now_iso(),
    )


def consult(
    title: str,
    situation: str,
    options: list[Option],
    *,
    trigger: str = "NON_OBVIOUS_CHOICE",
    recommendation: str | None = None,
    pipeline_stage: str = "",
    project: str | None = None,
    evidence: list[str] | None = None,
    upstream_artifacts: list[str] | None = None,
    timeout_hours: int = 24,
) -> Escalation:
    """Create an L1 CONSULT escalation — pipeline continues, override window.

    Swarm acts on its ``recommendation`` immediately.  The human has
    ``timeout_hours`` to override.  If no response, the recommendation
    is auto-accepted.

    Args:
        timeout_hours: Hours before auto-accepting the recommendation.
            Default 24h.  Set to 0 to disable timeout (acts like BLOCK).
    """
    now = datetime.now(timezone.utc)
    timeout_at = (
        (now + timedelta(hours=timeout_hours)).isoformat()
        if timeout_hours > 0 else None
    )
    return Escalation(
        id=_make_id(),
        level=Level.CONSULT,
        trigger=trigger,
        title=title,
        situation=situation,
        options=options,
        recommendation=recommendation,
        evidence=evidence or [],
        project=project,
        pipeline_stage=pipeline_stage,
        upstream_artifacts=upstream_artifacts or [],
        status="open",
        created_at=now.isoformat(),
        timeout_at=timeout_at,
    )


def resolve(esc: Escalation, resolution: str, resolved_by: str = "user") -> Escalation:
    """Resolve an open escalation with the human's decision.

    Returns a new Escalation with updated status.  Does not mutate the input.
    """
    return Escalation(
        **{
            **asdict(esc),
            "options": esc.options,   # Preserve Option objects (asdict converts to dicts)
            "status": "resolved",
            "resolved_at": _now_iso(),
            "resolved_by": resolved_by,
            "resolution": resolution,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# SSE event builder
# ─────────────────────────────────────────────────────────────────────────────

def build_sse_event(esc: Escalation) -> dict:
    """Build an SSE event dict from an Escalation.

    Returns a dict suitable for ``json.dumps()`` and delivery via the
    SSE streaming pipeline (``sse_with_heartbeat``).

    The frontend renders this via ``EscalationBlock.tsx``.
    """
    return {
        "type": "escalation",
        "id": esc.id,
        "level": esc.level,
        "levelName": Level(esc.level).name,
        "trigger": esc.trigger,
        "title": esc.title,
        "situation": esc.situation,
        "options": [asdict(o) for o in esc.options],
        "recommendation": esc.recommendation,
        "evidence": esc.evidence,
        "project": esc.project,
        "pipelineStage": esc.pipeline_stage,
        "status": esc.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Persistence (L1+ only — project has .artifacts/)
# ─────────────────────────────────────────────────────────────────────────────

def _escalations_dir(workspace_root: Path, project: str) -> Path:
    return workspace_root / "Projects" / project / ".artifacts" / "escalations"


def save_escalation(workspace_root: Path, esc: Escalation) -> None:
    """Persist an escalation to disk atomically.  No-op if no project."""
    if not esc.project:
        return
    esc_dir = _escalations_dir(workspace_root, esc.project)
    esc_dir.mkdir(parents=True, exist_ok=True)
    path = esc_dir / f"{esc.id}.json"
    data = asdict(esc)
    # Convert Option dataclasses to dicts for JSON serialization
    data["options"] = [asdict(o) if hasattr(o, "__dataclass_fields__") else o for o in esc.options]
    # Atomic write: temp file + rename prevents corruption on crash
    fd, tmp_path = tempfile.mkstemp(dir=str(esc_dir), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
        Path(tmp_path).replace(path)
    except BaseException:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise
    logger.info("escalation.saved id=%s project=%s level=%d", esc.id, esc.project, esc.level)


def load_escalation(workspace_root: Path, project: str, esc_id: str) -> Escalation | None:
    """Load a single escalation from disk."""
    path = _escalations_dir(workspace_root, project) / f"{esc_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["options"] = [Option(**o) for o in data.get("options", [])]
        return Escalation(**data)
    except Exception as exc:
        logger.warning("escalation.load_failed id=%s: %s", esc_id, exc)
        return None


def get_open_escalations(workspace_root: Path, project: str) -> list[Escalation]:
    """List all open (unresolved) escalations for a project."""
    esc_dir = _escalations_dir(workspace_root, project)
    if not esc_dir.exists():
        return []
    results = []
    for path in sorted(esc_dir.glob("esc_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") == "open":
                data["options"] = [Option(**o) for o in data.get("options", [])]
                results.append(Escalation(**data))
        except Exception:
            continue
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Radar Todo Integration (L1 + L2 — creates a self-contained work packet)
# ─────────────────────────────────────────────────────────────────────────────

_DB_PATH = Path.home() / ".swarm-ai" / "data.db"
_WORKSPACE_ID = "swarmws"


def create_radar_todo(esc: Escalation, db_path: Path | None = None) -> str | None:
    """Create a Radar todo from an L1/L2 escalation.

    Writes directly to SQLite (same pattern as ``todo_db.py``).
    Returns the todo ID, or None if creation failed or not applicable.

    L0 INFORM escalations are skipped (no todo needed).
    """
    if esc.level == Level.INFORM:
        return None

    import sqlite3 as _sqlite3

    db = db_path or _DB_PATH
    if not db.exists():
        logger.warning("escalation.radar_todo: DB not found at %s", db)
        return None

    try:
        todo_id = str(uuid4())
        now = _now_iso()
        level_name = Level(esc.level).name
        priority = "high" if esc.level == Level.BLOCK else "medium"

        # Build linked_context with full escalation packet
        linked_context = json.dumps({
            "escalation_id": esc.id,
            "escalation_level": level_name,
            "project": esc.project,
            "pipeline_stage": esc.pipeline_stage,
            "trigger": esc.trigger,
            "options": [asdict(o) for o in esc.options],
            "recommendation": esc.recommendation,
            "evidence": esc.evidence,
            "timeout_at": esc.timeout_at,
            "next_step": f"Resolve escalation: {esc.title}",
            "acceptance": "Choose an option or discuss further",
        })

        # Description includes the situation + options for quick scanning
        options_text = "\n".join(
            f"  {i+1}. {o.label}{' (recommended)' if o.is_recommendation else ''}"
            for i, o in enumerate(esc.options)
        )
        description = f"{esc.situation}\n\nOptions:\n{options_text}" if options_text else esc.situation

        with _sqlite3.connect(str(db), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """INSERT INTO todos (id, workspace_id, title, description, source,
                   source_type, status, priority, due_date, linked_context, task_id,
                   created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, NULL, ?, ?)""",
                (
                    todo_id, _WORKSPACE_ID,
                    f"[{level_name}] {esc.title}",
                    description,
                    f"escalation:{esc.id}",
                    "ai_detected",
                    priority,
                    esc.timeout_at,  # due_date = timeout for visual urgency
                    linked_context,
                    now, now,
                ),
            )
            conn.commit()

        logger.info(
            "escalation.radar_todo id=%s esc=%s level=%s priority=%s",
            todo_id, esc.id, level_name, priority,
        )
        return todo_id

    except Exception as exc:
        logger.warning("escalation.radar_todo failed esc=%s: %s", esc.id, exc)
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Timeout Resolution (L1 CONSULT — auto-accept on expiry)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_expired(workspace_root: Path, project: str) -> list[Escalation]:
    """Find and auto-resolve expired L1 CONSULT escalations.

    L1 escalations with ``timeout_at`` in the past are resolved with
    Swarm's recommendation (or "deferred" if no recommendation).

    Call this at session start or periodically — it's idempotent.

    Returns the list of escalations that were auto-resolved.
    """
    open_escs = get_open_escalations(workspace_root, project)
    now = datetime.now(timezone.utc)
    resolved_list = []

    for esc in open_escs:
        if esc.level != Level.CONSULT or not esc.timeout_at:
            continue

        try:
            timeout = datetime.fromisoformat(esc.timeout_at)
            if now <= timeout:
                continue  # Not expired yet
        except (ValueError, TypeError):
            continue  # Invalid timestamp — skip

        # Auto-resolve with recommendation or "deferred"
        resolution = esc.recommendation or "deferred (timeout)"
        resolved_esc = resolve(esc, resolution=resolution, resolved_by="timeout")
        save_escalation(workspace_root, resolved_esc)

        # Also mark the Radar todo as handled
        mark_todo_handled(esc.id)

        resolved_list.append(resolved_esc)
        logger.info(
            "escalation.timeout_resolved id=%s resolution=%s project=%s",
            esc.id, resolution, project,
        )

    return resolved_list


def mark_todo_handled(escalation_id: str, db_path: Path | None = None) -> None:
    """Mark the Radar todo associated with an escalation as handled."""
    import sqlite3 as _sqlite3

    db = db_path or _DB_PATH
    if not db.exists():
        return

    try:
        with _sqlite3.connect(str(db), timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "UPDATE todos SET status = 'handled', updated_at = ? WHERE source = ?",
                (_now_iso(), f"escalation:{escalation_id}"),
            )
            conn.commit()
    except Exception as exc:
        logger.warning("escalation.mark_todo_handled failed esc=%s: %s", escalation_id, exc)
