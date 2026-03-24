"""Escalation Protocol — human-in-the-loop for the autonomous pipeline.

Provides structured escalation from any pipeline stage when Swarm needs
human judgment.  v1 implements L0 INFORM and L2 BLOCK in-chat only.

Three escalation levels:

- **L0 INFORM** — "FYI, I did this."  Pipeline continues.  No action needed.
- **L1 CONSULT** — "I chose X, override?"  Pipeline continues with timeout.
  (v2 — not implemented yet)
- **L2 BLOCK** — "I need your input."  Pipeline pauses until human responds.

Escalation data is delivered to the frontend via SSE ``escalation`` events
and rendered by ``EscalationBlock.tsx``.

At L0 (no project), escalations are ephemeral (in-chat only).
At L1+ (project exists), escalations are persisted to ``.artifacts/escalations/``
and appear in Radar todos for async review.

Public API:
  - ``inform()``          — L0: emit FYI annotation (no action needed)
  - ``block()``           — L2: emit blocking question (pipeline pauses)
  - ``resolve()``         — Resolve an open L2 escalation
  - ``get_open()``        — List open escalations for a project
  - ``build_sse_event()`` — Build SSE event dict from an Escalation
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
    "AMBIGUOUS_SCOPE",
    "CONFLICTING_PRIORITIES",
    "LOW_CONFIDENCE_ROI",
    "CLEAR_EVALUATION",

    # THINK stage
    "INCONCLUSIVE_RESEARCH",
    "NO_CLEAR_WINNER",
    "CLEAR_RECOMMENDATION",

    # PLAN stage
    "UNCOMMITTED_DEPENDENCY",
    "DEVIATES_FROM_TECH",
    "FOLLOWS_PATTERNS",

    # BUILD stage
    "EXCEEDS_SCOPE",
    "IMPLEMENTATION_DIFFERS",
    "BUILT_AS_DESIGNED",

    # REVIEW stage
    "CRITICAL_SECURITY_FINDING",
    "NEEDS_HUMAN_JUDGMENT",
    "CLEAN_REVIEW",

    # TEST stage
    "WTF_GATE_TRIGGERED",
    "UNEXPECTED_REGRESSION",
    "FLAKY_TESTS",
    "ALL_PASS",

    # DELIVER stage
    "UNRESOLVED_ESCALATIONS",
    "PR_NEEDS_POLISH",
    "CLEAN_DELIVERY",

    # Cross-cutting
    "FIRST_TIME_DOMAIN",
    "COST_THRESHOLD",
    "RESOURCE_CONTENTION",
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
    """Persist an escalation to disk.  No-op if no project."""
    if not esc.project:
        return
    esc_dir = _escalations_dir(workspace_root, esc.project)
    esc_dir.mkdir(parents=True, exist_ok=True)
    path = esc_dir / f"{esc.id}.json"
    data = asdict(esc)
    # Convert Option dataclasses to dicts for JSON serialization
    data["options"] = [asdict(o) if hasattr(o, "__dataclass_fields__") else o for o in esc.options]
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
