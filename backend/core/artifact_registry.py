"""Artifact Registry -- filesystem-only typed skill output chaining.

Skills produce typed artifacts (research, design_doc, changeset, review,
test_report).  Downstream skills auto-discover and consume upstream
artifacts through the manifest.  Storage is pure filesystem under
``Projects/<project>/.artifacts/``.

Independence: works at L0 (no project) by returning empty results.
DDD enrichment is a separate layer, not a dependency here.
No database.  Filesystem IS the repository.

Public API:
  - discover(project, *types) -> list[Artifact]
  - publish(project, type, data, producer, summary, topic) -> str
  - get_pipeline_state(project) -> str | None
  - advance_pipeline(project, new_state) -> None
  - get_artifact(project, artifact_id) -> Artifact | None
  - supersede(project, old_id, new_id) -> None
  - list_projects() -> list[ProjectPipelineStatus]
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Valid artifact types and pipeline states
# ─────────────────────────────────────────────────────────────────────────────

ARTIFACT_TYPES = frozenset({
    "evaluation",
    "research",
    "alternatives",
    "design_doc",
    "changeset",
    "review",
    "test_report",
    "delivery",
    "release",
})

PIPELINE_STATES = (
    "evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect",
)

# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Artifact:
    """A single typed artifact produced by a skill."""

    id: str
    type: str
    producer: str
    created: str  # ISO 8601
    file: str  # Filename relative to .artifacts/
    summary: str
    data: dict = field(default_factory=dict)
    superseded_by: str | None = None

    @property
    def is_active(self) -> bool:
        """True if this artifact has not been superseded."""
        return self.superseded_by is None


@dataclass
class ProjectPipelineStatus:
    """Summary of a project's pipeline state."""

    project: str
    pipeline_state: str
    artifact_count: int
    active_artifact_count: int
    latest_artifact: str | None  # type of most recent artifact


# ─────────────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────────────


class ArtifactRegistry:
    """Filesystem-backed artifact registry.

    One instance per workspace.  Thread-safe for reads; writes are
    append-only to manifest.json (last-write-wins for concurrent publishes,
    which is acceptable since skill invocations are serialized per session).
    """

    def __init__(self, workspace_root: Path):
        self.workspace_root = workspace_root
        self.projects_root = workspace_root / "Projects"

    # ── Discovery ─────────────────────────────────────────────────────

    def discover(
        self, project: str | None, *types: str
    ) -> list[Artifact]:
        """Find active (non-superseded) artifacts of the given types.

        L0-safe: returns ``[]`` when *project* is None or has no artifacts.
        """
        if not project or not types:
            return []

        manifest = self._read_manifest(project)
        if manifest is None:
            return []

        type_set = set(types)
        results = []
        for entry in manifest.get("artifacts", []):
            if entry.get("type") in type_set and entry.get("superseded_by") is None:
                artifact = self._entry_to_artifact(entry, project)
                if artifact is not None:
                    results.append(artifact)

        return results

    def get_artifact(
        self, project: str, artifact_id: str
    ) -> Artifact | None:
        """Load a single artifact by ID, including its data payload."""
        manifest = self._read_manifest(project)
        if manifest is None:
            return None

        for entry in manifest.get("artifacts", []):
            if entry.get("id") == artifact_id:
                return self._entry_to_artifact(entry, project)

        return None

    # ── Publishing ────────────────────────────────────────────────────

    def publish(
        self,
        project: str,
        artifact_type: str,
        data: dict,
        producer: str,
        summary: str,
        topic: str = "",
    ) -> str:
        """Write a new artifact and update the manifest.

        Auto-creates ``.artifacts/`` and ``manifest.json`` if they don't
        exist.  Returns the new artifact ID.

        Args:
            project: Project name under Projects/.
            artifact_type: One of ARTIFACT_TYPES.
            data: The artifact payload (arbitrary JSON-serializable dict).
            producer: Name of the skill or process that produced this.
            summary: One-line human-readable summary.
            topic: Optional topic slug for the filename.

        Raises:
            ValueError: If artifact_type is not recognized.
            FileNotFoundError: If the project directory doesn't exist.
        """
        if artifact_type not in ARTIFACT_TYPES:
            raise ValueError(
                f"Unknown artifact type '{artifact_type}'. "
                f"Valid types: {sorted(ARTIFACT_TYPES)}"
            )

        project_dir = self.projects_root / project
        if not project_dir.is_dir():
            raise FileNotFoundError(f"Project directory not found: {project_dir}")

        artifacts_dir = project_dir / ".artifacts"
        artifacts_dir.mkdir(exist_ok=True)

        now = datetime.now(timezone.utc)
        artifact_id = f"art_{uuid4().hex[:8]}"
        date_str = now.strftime("%Y%m%d")
        topic_slug = f"-{_slugify(topic)}" if topic else ""
        filename = f"{artifact_type}-{date_str}{topic_slug}.json"

        # Write artifact data file
        artifact_path = artifacts_dir / filename
        artifact_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )

        # Update manifest
        manifest = self._read_manifest(project) or {
            "project": project,
            "pipeline_state": "think",
            "updated_at": now.isoformat(),
            "artifacts": [],
        }

        manifest["artifacts"].append({
            "id": artifact_id,
            "type": artifact_type,
            "producer": producer,
            "created": now.isoformat(),
            "file": filename,
            "summary": summary,
            "superseded_by": None,
        })
        manifest["updated_at"] = now.isoformat()

        self._write_manifest(project, manifest)

        logger.info(
            "Published artifact '%s' (%s) for project '%s': %s",
            artifact_id, artifact_type, project, filename,
        )
        return artifact_id

    def supersede(
        self, project: str, old_id: str, new_id: str
    ) -> None:
        """Mark an artifact as superseded by a newer one.

        The old artifact stays on disk for history.  Discovery methods
        skip superseded artifacts.
        """
        manifest = self._read_manifest(project)
        if manifest is None:
            return

        for entry in manifest["artifacts"]:
            if entry["id"] == old_id:
                entry["superseded_by"] = new_id
                break

        self._write_manifest(project, manifest)
        logger.info(
            "Superseded artifact '%s' with '%s' in project '%s'",
            old_id, new_id, project,
        )

    # ── Pipeline state ────────────────────────────────────────────────

    def get_pipeline_state(self, project: str | None) -> str | None:
        """Current lifecycle phase for a project, or None if no project."""
        if not project:
            return None

        manifest = self._read_manifest(project)
        if manifest is None:
            return None

        return manifest.get("pipeline_state")

    def advance_pipeline(self, project: str, new_state: str) -> None:
        """Move project to a new lifecycle phase.

        The state is advisory -- it guides skill suggestions but never
        blocks anything.
        """
        if new_state not in PIPELINE_STATES:
            raise ValueError(
                f"Unknown pipeline state '{new_state}'. "
                f"Valid states: {list(PIPELINE_STATES)}"
            )

        manifest = self._read_manifest(project) or {
            "project": project,
            "pipeline_state": new_state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "artifacts": [],
        }
        manifest["pipeline_state"] = new_state
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._write_manifest(project, manifest)
        logger.info(
            "Advanced project '%s' to pipeline state '%s'",
            project, new_state,
        )

    # ── Project overview ──────────────────────────────────────────────

    def list_projects(self) -> list[ProjectPipelineStatus]:
        """Walk Projects/ and return pipeline status for each."""
        results = []
        if not self.projects_root.is_dir():
            return results

        for candidate in sorted(self.projects_root.iterdir()):
            if not candidate.is_dir() or candidate.name.startswith("."):
                continue

            manifest = self._read_manifest(candidate.name)
            if manifest is None:
                # Project exists but no .artifacts/ yet
                results.append(ProjectPipelineStatus(
                    project=candidate.name,
                    pipeline_state="-",
                    artifact_count=0,
                    active_artifact_count=0,
                    latest_artifact=None,
                ))
                continue

            artifacts = manifest.get("artifacts", [])
            active = [a for a in artifacts if a.get("superseded_by") is None]
            latest_type = artifacts[-1]["type"] if artifacts else None

            results.append(ProjectPipelineStatus(
                project=candidate.name,
                pipeline_state=manifest.get("pipeline_state", "-"),
                artifact_count=len(artifacts),
                active_artifact_count=len(active),
                latest_artifact=latest_type,
            ))

        return results

    # ── Internal helpers ──────────────────────────────────────────────

    def _manifest_path(self, project: str) -> Path:
        return self.projects_root / project / ".artifacts" / "manifest.json"

    def _read_manifest(self, project: str) -> dict | None:
        """Read manifest.json, returning None if missing or corrupt."""
        path = self._manifest_path(project)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read manifest for '%s': %s", project, exc)
            return None

    def _write_manifest(self, project: str, manifest: dict) -> None:
        """Write manifest.json, creating .artifacts/ if needed."""
        path = self._manifest_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest, indent=2, default=str),
            encoding="utf-8",
        )

    # ── Learn feedback ──────────────────────────────────────────────

    def record_outcome(
        self,
        project: str,
        evaluation_id: str,
        outcome: str,
        actual_effort: str | None = None,
        lessons: list[str] | None = None,
    ) -> None:
        """Record the outcome of a pipeline run for learning feedback.

        Compares the evaluation's predicted ROI/effort with actual results
        and appends a calibration entry to ``decision-strategy.json``.

        Args:
            project: Project name.
            evaluation_id: ID of the evaluation artifact.
            outcome: "success", "partial", "failure", or "cancelled".
            actual_effort: Actual effort (T-shirt size or sessions).
            lessons: Short strings describing what to adjust.
        """
        strategy_path = self.projects_root / project / "decision-strategy.json"
        if not strategy_path.is_file():
            logger.info("No decision-strategy.json for '%s', skipping learn", project)
            return

        try:
            strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        # Load evaluation artifact for comparison
        evaluation = self.get_artifact(project, evaluation_id)
        predicted_roi = None
        predicted_effort = None
        if evaluation and evaluation.data:
            scores = evaluation.data.get("scores", {})
            predicted_roi = scores.get("roi")
            predicted_effort = evaluation.data.get("effort_estimate")

        # Append calibration entry
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evaluation_id": evaluation_id,
            "predicted_roi": predicted_roi,
            "predicted_effort": predicted_effort,
            "actual_effort": actual_effort,
            "outcome": outcome,
            "lessons": lessons or [],
        }

        history = strategy.setdefault("calibration_history", [])
        history.append(entry)

        # Keep last 50 entries to prevent unbounded growth
        if len(history) > 50:
            strategy["calibration_history"] = history[-50:]

        # Auto-adjust weights based on outcome patterns (simple heuristic)
        # If last 5 entries show consistent over/under scoring, nudge weights
        recent = history[-5:]
        if len(recent) >= 5:
            success_count = sum(1 for e in recent if e["outcome"] == "success")
            failure_count = sum(1 for e in recent if e["outcome"] == "failure")

            weights = strategy.get("weights", {})
            if failure_count >= 3 and weights.get("inverse_feasibility", 0) < 0.4:
                # Too many failures -> we're underweighting cost/feasibility
                weights["inverse_feasibility"] = min(
                    0.4, weights.get("inverse_feasibility", 0.25) + 0.02
                )
                weights["strategic_alignment"] = max(
                    0.2, weights.get("strategic_alignment", 0.35) - 0.02
                )
                logger.info("Learn: nudged weights toward feasibility for '%s'", project)
            elif success_count >= 4 and weights.get("strategic_alignment", 0) < 0.45:
                # Consistent success -> can slightly favor strategic alignment
                weights["strategic_alignment"] = min(
                    0.45, weights.get("strategic_alignment", 0.35) + 0.01
                )
                logger.info("Learn: nudged weights toward strategy for '%s'", project)

            strategy["weights"] = weights

        strategy_path.write_text(
            json.dumps(strategy, indent=2), encoding="utf-8"
        )
        logger.info(
            "Recorded outcome '%s' for evaluation %s in '%s'",
            outcome, evaluation_id, project,
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _entry_to_artifact(
        self, entry: dict, project: str
    ) -> Artifact | None:
        """Convert a manifest entry to an Artifact, loading data from disk."""
        try:
            data_path = (
                self.projects_root / project / ".artifacts" / entry["file"]
            )
            data = {}
            if data_path.is_file():
                try:
                    data = json.loads(data_path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass  # Data file corrupt — artifact still valid without payload

            return Artifact(
                id=entry["id"],
                type=entry["type"],
                producer=entry.get("producer", "unknown"),
                created=entry.get("created", ""),
                file=entry["file"],
                summary=entry.get("summary", ""),
                data=data,
                superseded_by=entry.get("superseded_by"),
            )
        except KeyError as exc:
            logger.warning("Malformed artifact entry (missing %s): %s", exc, entry)
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────


def _slugify(text: str, max_len: int = 40) -> str:
    """Convert text to a safe filename slug."""
    slug = text.lower().strip()
    slug = "".join(c if c.isalnum() or c == "-" else "-" for c in slug)
    # Collapse consecutive hyphens
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:max_len]
