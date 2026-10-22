"""FastAPI router for the Artifacts API.

Two artifact systems coexist:

1. **Git-derived artifacts** (``GET /artifacts/recent``) — recently modified
   files from the workspace git tree.  Read-only, no new database tables.

2. **Pipeline artifacts** (``GET/POST /artifacts/pipeline/*``) — typed skill
   output chaining via the ``ArtifactRegistry``.  Filesystem-backed under
   ``Projects/<project>/.artifacts/``.  Supports publish, discover,
   pipeline state, and supersede operations.

Public endpoints:

- ``GET  /artifacts/recent``              — Git-derived recent files
- ``GET  /artifacts/pipeline/projects``   — Pipeline status for all projects
- ``GET  /artifacts/pipeline/discover``   — Discover artifacts by type
- ``GET  /artifacts/pipeline/state``      — Get pipeline state for a project
- ``POST /artifacts/pipeline/publish``    — Publish a new artifact
- ``POST /artifacts/pipeline/advance``    — Advance pipeline state
- ``POST /artifacts/pipeline/supersede``  — Mark artifact as superseded
"""

import asyncio
import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

from core import executors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from core.needs_human_review import needs_human_review_batch
from core.swarm_workspace_manager import swarm_workspace_manager
from database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["artifacts"])


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

EXTENSION_TYPE_MAP: dict[str, set[str]] = {
    "code": {".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java"},
    "document": {".md", ".txt", ".rst", ".pdf", ".docx"},
    "config": {".json", ".yaml", ".yml", ".toml", ".ini", ".env"},
    "image": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico"},
}

# Build a reverse lookup: extension → type for O(1) classification.
_EXT_TO_TYPE: dict[str, str] = {}
for _type_name, _extensions in EXTENSION_TYPE_MAP.items():
    for _ext in _extensions:
        _EXT_TO_TYPE[_ext] = _type_name

# Membership scope is NOT a directory allowlist here. It is delegated to the SAME
# source-of-truth the Canvas OUTPUTS rail uses — the `needs_human_review` `kind`
# verdict (see _parse_git_log). This is deliberate: the Artifacts list is the
# human-facing product list, so "what counts as a product" must be answered ONCE,
# by the shared classifier, not by a second directory allowlist that drifts from the
# rail. A former 5-dir allowlist (_ARTIFACT_DIRS) + _is_artifact_file lived here and
# was removed for exactly that reason (P8: one brain, many doors — one entry rule).


# ─────────────────────────────────────────────────────────────────────────────
# Response model
# ─────────────────────────────────────────────────────────────────────────────

class ArtifactResponse(BaseModel):
    """A recently modified file in the workspace git tree."""

    path: str
    title: str
    type: str
    modified_at: str


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _get_workspace_path() -> str:
    """Resolve the active workspace path from the database config.

    Uses the same singleton workspace config pattern as
    ``workspace_api._get_workspace_path``.

    Returns:
        Expanded absolute path to the workspace root.

    Raises:
        HTTPException: 404 if no workspace config exists.
    """
    config = await db.workspace_config.get_config()
    if config is None:
        raise HTTPException(status_code=404, detail="Workspace not configured")
    return swarm_workspace_manager.expand_path(config["file_path"])


def _classify_extension(file_path: str) -> str:
    """Derive artifact type from file extension (case-insensitive).

    Args:
        file_path: Relative file path from git log.

    Returns:
        One of: ``code``, ``document``, ``config``, ``image``, ``other``.
    """
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_TYPE.get(ext, "other")


def _parse_git_log(raw_output: str, workspace_path: str) -> list[dict[str, str]]:
    """Parse raw ``git log`` output into deduplicated, scope-filtered artifact records.

    The git log format ``--format=%aI`` produces alternating blocks:
    an ISO timestamp line followed by one or more file path lines,
    separated by blank lines.

    **Membership scope = the Canvas OUTPUTS rail SSOT.** Rather than a private
    directory allowlist, a candidate path is kept iff ``needs_human_review`` classifies
    its ``kind`` as ``content`` or ``knowledge`` — exactly the human-facing set the
    Canvas rail surfaces (``railSsot.isRailKind`` drops ``process`` and ``source``). A
    ``source`` path (a bound-worktree code file) and a ``process`` path (dot-dir /
    machine noise) are dropped, so the Artifacts list and the Canvas rail agree on what
    a "product" is. Classification runs in ONE batch (one check-ignore subprocess per
    tree) and fails CLOSED (an unclassifiable path → ``process`` → dropped).

    Args:
        raw_output: Raw stdout from ``git log`` (run with ``cwd=workspace_path``, so
            paths are workspace-relative).
        workspace_path: Absolute workspace root — passed as ``swarmws_root`` so the
            relative paths resolve against the right tree (load-bearing; omitting it
            would resolve paths against the process cwd and misclassify everything).

    Returns:
        List of dicts with ``path``, ``title``, ``type``, ``modified_at``
        sorted by ``modified_at`` descending (most recent first).
        Deduplicated by path — only the most recent timestamp is kept.
    """
    seen: dict[str, str] = {}  # path → most recent ISO timestamp
    current_timestamp: Optional[str] = None
    # Strict ISO 8601 pattern: YYYY-MM-DDTHH:MM:SS±HH:MM (or Z)
    iso_pattern = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}')

    # Phase 1 — collect EVERY candidate path (dedup by newest timestamp). No membership
    # filter yet; that is a single batch call below.
    for line in raw_output.splitlines():
        stripped = line.strip()
        if not stripped:
            # Blank lines separate commit blocks, but do NOT reset the
            # timestamp.  ``git log --format=%aI --name-only`` inserts a
            # blank line between the format output and the file list:
            #
            #   2026-03-22T13:32:02+08:00   ← timestamp
            #   (blank)                      ← separator
            #   Knowledge/DailyActivity/...  ← file
            #
            # Resetting here would drop every file.  This was a
            # regression in the original parser, not a design choice.
            continue

        # ISO 8601 timestamp line (strict match)
        if iso_pattern.match(stripped):
            current_timestamp = stripped
            continue

        # File path line — only record if we have a timestamp
        if current_timestamp and stripped:
            if stripped not in seen:
                seen[stripped] = current_timestamp

    if not seen:
        return []

    # Phase 2 — one batch classification; keep only human-facing product kinds
    # (content/knowledge), matching the Canvas rail. `swarmws_root` is load-bearing:
    # git-log paths are workspace-relative, so they must resolve against the workspace
    # root, not the process cwd.
    verdicts = needs_human_review_batch(list(seen.keys()), swarmws_root=workspace_path)

    artifacts = []
    for file_path, timestamp in seen.items():
        verdict = verdicts.get(file_path)
        if verdict is None or verdict.kind not in ("content", "knowledge"):
            continue
        artifacts.append({
            "path": file_path,
            "title": Path(file_path).name,
            "type": _classify_extension(file_path),
            "modified_at": timestamp,
        })

    artifacts.sort(key=lambda a: a["modified_at"], reverse=True)
    return artifacts


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/artifacts/recent", response_model=list[ArtifactResponse])
async def get_recent_artifacts(
    workspace_id: str = Query(..., description="Workspace identifier"),
    limit: int = Query(20, ge=1, le=50, description="Max artifacts to return"),
) -> list[ArtifactResponse]:
    """Return recently modified files from the workspace git tree.

    Derives the file list from ``git log --diff-filter=ACMR --name-only``
    scoped to the active workspace path.  Files are deduplicated by path
    (most recent timestamp wins) and classified by extension.

    Args:
        workspace_id: Required workspace identifier (resolved via DB).
        limit: Maximum number of artifacts to return (1–50, default 20).

    Returns:
        List of ``ArtifactResponse`` sorted by ``modified_at`` descending.

    Raises:
        HTTPException: 404 if workspace is not configured.
        HTTPException: 422 if query params are invalid (handled by FastAPI).
    """
    workspace_path = await _get_workspace_path()

    if not Path(workspace_path).is_dir():
        raise HTTPException(status_code=404, detail="Workspace path not found")

    def _run_git_log() -> subprocess.CompletedProcess:
        return subprocess.run(
            [
                # -c core.quotepath=false: emit paths as raw UTF-8, NOT git's
                # default C-style octal-escaped + double-quote-wrapped form for
                # non-ASCII names. Without it a CJK-named file (e.g. a
                # stock-analysis report "…歌尔股份.md") arrives as
                # `"Services/…\346\255\214…"` — the leading quote breaks EVERY
                # downstream consumer: the kind classifier's first-segment match,
                # dedup, extension typing, and baseName display (run_d25d72be).
                "git", "-c", "core.quotepath=false", "log",
                "--diff-filter=ACMR",
                "--name-only",
                f"--format=%aI",
                "--since=30.days",
                f"-n{limit * 3}",
                "--no-merges",
            ],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            # 15s (was 5s): the SwarmWS workspace repo accumulates a commit
            # per conversation turn (2400+ commits), so even this bounded query
            # (--since=30.days, -n cap, --no-merges) walks enough history to
            # exceed a 5s budget and time out ("git log timed out for workspace"
            # ×2/day). The query is already capped; give it room to finish.
            timeout=15,
        )

    try:
        result = await executors.run_in("subprocess", _run_git_log)
    except subprocess.TimeoutExpired:
        logger.warning("git log timed out for workspace %s", workspace_id)
        return []
    except FileNotFoundError:
        logger.warning("git not found on PATH")
        return []
    except Exception:
        logger.exception("Unexpected error running git log")
        return []

    if result.returncode != 0:
        # Not a git repo, no commits, detached HEAD, etc.
        logger.debug(
            "git log returned non-zero (%d) for workspace %s",
            result.returncode,
            workspace_id,
        )
        return []

    artifacts = _parse_git_log(result.stdout, workspace_path)
    return [ArtifactResponse(**a) for a in artifacts[:limit]]


# ─────────────────────────────────────────────────────────────────────────────
# Products — the shared workspace-level PRODUCT registry projection (Artifacts B′ Run 2)
# ─────────────────────────────────────────────────────────────────────────────
#
# The Artifacts overlay reads THIS (not /artifacts/recent) so a gitignored deck —
# which the git-log projection structurally cannot see — surfaces. The store is
# written by the workspace file-watcher (Run 1); this is the read/projection half.


class ProductResponse(BaseModel):
    """One product row projected from products.json (camelCase for the frontend)."""

    path: str
    role: str            # Deliverables | Knowledge | Pipeline | Activity (never Other — dropped at write)
    kind: str
    gitignored: bool
    firstProduced: str
    lastTouched: str
    displayLabel: str    # Run-3 AC8: human label — task name for Pipeline rows, basename otherwise
    starred: bool        # run_2b7230be: user favorite (persisted in products.json)


# ── loading-B: day-one backfill runs in the BACKGROUND, never on the read path ──
# The read (`get_products`) returns stored products IMMEDIATELY so the overlay's
# first paint never blocks on the 30-day git-log. On first-ever open we SCHEDULE the
# backfill as a fire-and-forget task. Two guards, both required:
#   • `_backfill_tasks` — a module-level STRONG-REF set holding the Task object.
#     `asyncio.create_task` only weak-refs its task, so without this the task can be
#     GC'd mid-run before the backfill finishes (CPython footgun — Gate-1). A
#     done-callback discards it (on success OR failure) so the set never grows.
#   • `_backfill_inflight` — workspace paths with a backfill currently scheduled, so N
#     concurrent first-reads (before has_backfilled() flips at the END of backfill) do
#     NOT each spawn a task. check-and-add is atomic on the single event loop (no await
#     between). Cleared in the same done-callback (even on crash) so a failed backfill
#     never wedges the key forever.
_backfill_tasks: set = set()
_backfill_inflight: set = set()


def _schedule_backfill(workspace_path: str) -> None:
    """Fire-and-forget the day-one backfill for `workspace_path` (at most one in
    flight per workspace). No-op if one is already scheduled. Safe to call on every
    read — the has_backfilled() gate upstream + this in-flight gate bound it."""
    from core.product_registry import ProductRegistry

    if workspace_path in _backfill_inflight:
        return
    _backfill_inflight.add(workspace_path)  # atomic w.r.t. the loop (no await before)

    async def _run() -> None:
        try:
            await executors.run_in(
                "io", lambda: ProductRegistry(workspace_path).backfill_from_gitlog(days=30)
            )
        except Exception:  # noqa: BLE001 — a bg backfill failure must never crash the loop
            logger.exception("products: background backfill failed for %s", workspace_path)

    task = asyncio.create_task(_run())
    _backfill_tasks.add(task)  # STRONG ref — else the loop may GC the task mid-run

    def _done(t: asyncio.Task) -> None:
        _backfill_tasks.discard(t)
        _backfill_inflight.discard(workspace_path)  # clear on success OR failure

    task.add_done_callback(_done)


def _display_label(product, workspace_path: str, cache: dict) -> str:
    """Run-3 AC8: a product row's human label (ProductRegistry.display_label)."""
    from core.product_registry import display_label
    return display_label(product, workspace_path, cache)


@router.get("/artifacts/products", response_model=list[ProductResponse])
async def get_products(
    workspace_id: str = Query(..., description="Workspace identifier (resolved via DB)"),
) -> list[ProductResponse]:
    """Return the workspace product registry, role-typed (Artifacts B′ Run 2, AC1).

    Projects ``ProductRegistry.list_products()`` — the role-classified user products
    (decks/reports/images/DDD docs) both the overlay and (future) rail read from. On
    first read of an EMPTY store, backfills from the last 30d of git-log so the overlay
    is not empty day-one (AC7). FAIL-SAFE: any error → [] (never 500) — an empty
    Artifacts list is a benign degrade; a 500 breaks the overlay.
    """
    from core.product_registry import ProductRegistry

    try:
        workspace_path = await _get_workspace_path()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 — fail-safe: unresolved workspace → empty, not 500
        logger.exception("products: workspace resolution failed")
        return []

    def _load() -> tuple[list, bool]:
        # loading-B: the READ never backfills — it returns stored products immediately
        # so the overlay's first paint is instant (never blocks on the 30-day git-log).
        # Returns (products, needs_backfill) so the async caller can SCHEDULE a
        # background backfill without blocking this read.
        reg = ProductRegistry(workspace_path)
        return reg.list_products(), (not reg.has_backfilled())

    try:
        products, needs_backfill = await executors.run_in("io", _load)
    except Exception:  # noqa: BLE001 — fail-safe: never surface a 500 to the overlay
        logger.exception("products: registry read failed")
        return []

    # Day-one backfill (AC3): schedule OFF the read path so the response is immediate.
    # has_backfilled()==False → fire-and-forget one background backfill (in-flight-guarded).
    if needs_backfill:
        _schedule_backfill(workspace_path)

    label_cache: dict[str, str] = {}
    return [
        ProductResponse(
            path=p.path,
            role=getattr(p.role, "value", p.role),  # enum-safe: .value not 'Role.X' (Gate-2 F5)
            kind=p.kind,
            gitignored=bool(p.gitignored),
            firstProduced=p.first_produced,
            lastTouched=p.last_touched,
            displayLabel=_display_label(p, workspace_path, label_cache),
            starred=bool(getattr(p, "starred", False)),
        )
        for p in products
    ]


class StarRequest(BaseModel):
    """Body for PUT /artifacts/products/star — toggle a product's favorite flag."""

    path: str
    starred: bool


@router.put("/artifacts/products/star")
async def set_product_starred(
    body: StarRequest,
    workspace_id: str = Query(..., description="Workspace identifier (resolved via DB)"),
) -> dict:
    """run_2b7230be: toggle the `starred` favorite flag on an EXISTING product.

    Confined-by-construction: reuses the SAME single DB-resolved workspace as
    get_products, and delegates to ProductRegistry.set_starred, which flips ONLY the
    boolean on a row matched by EXACT path equality (no filesystem resolution, no
    create). An unknown/traversal path matches nothing → 404, never appends a row.
    """
    from core.product_registry import ProductRegistry

    try:
        workspace_path = await _get_workspace_path()
    except HTTPException:
        raise

    def _toggle() -> bool:
        return ProductRegistry(workspace_path).set_starred(body.path, body.starred)

    matched = await executors.run_in("io", _toggle)
    if not matched:
        raise HTTPException(status_code=404, detail="product not found")
    return {"path": body.path, "starred": body.starred}


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline Artifact Endpoints (ArtifactRegistry)
# ─────────────────────────────────────────────────────────────────────────────

from core.artifact_registry import ArtifactRegistry


def _get_registry() -> ArtifactRegistry:
    """Lazy-init the singleton ArtifactRegistry."""
    workspace_path = swarm_workspace_manager.expand_path(
        swarm_workspace_manager.DEFAULT_WORKSPACE_CONFIG.get(
            "file_path", "~/.swarm-ai/SwarmWS"
        )
    )
    return ArtifactRegistry(Path(workspace_path))


class PipelineProjectStatus(BaseModel):
    """Pipeline status for a single project."""
    project: str
    pipeline_state: str
    artifact_count: int
    active_artifact_count: int
    latest_artifact: Optional[str]


class PipelineArtifactResponse(BaseModel):
    """A pipeline artifact returned from discovery."""
    id: str
    type: str
    producer: str
    created: str
    file: str
    summary: str
    superseded_by: Optional[str]
    run_id: Optional[str] = None  # Action B #14 — owning run (None for legacy entries)


class PublishRequest(BaseModel):
    """Request body for publishing a new artifact."""
    project: str
    artifact_type: str
    data: dict
    producer: str
    summary: str
    topic: str = ""
    # run_44a06138 (Kiro #13): run_id is REQUIRED — OpenAPI must say so. The old
    # `str | None = None` told generated clients the field was optional while the
    # handler always rejected an absent value at runtime (a 400 the schema didn't
    # predict). A required `str` makes Pydantic reject an absent value with a 422
    # that the schema DOES predict — honest contract. Containment of the value is
    # enforced by the registry trust boundary (_validate_path_identifiers).
    run_id: str


class AdvanceRequest(BaseModel):
    """Request body for advancing pipeline state."""
    project: str
    state: str


class SupersedeRequest(BaseModel):
    """Request body for superseding an artifact."""
    project: str
    old_id: str
    new_id: str


@router.get(
    "/artifacts/pipeline/projects",
    response_model=list[PipelineProjectStatus],
)
async def get_pipeline_projects() -> list[PipelineProjectStatus]:
    """Return pipeline status for all projects."""
    reg = _get_registry()

    def _list():
        return reg.list_projects()

    statuses = await executors.run_in("io", _list)
    return [
        PipelineProjectStatus(
            project=s.project,
            pipeline_state=s.pipeline_state,
            artifact_count=s.artifact_count,
            active_artifact_count=s.active_artifact_count,
            latest_artifact=s.latest_artifact,
        )
        for s in statuses
    ]


@router.get(
    "/artifacts/pipeline/discover",
    response_model=list[PipelineArtifactResponse],
)
async def discover_pipeline_artifacts(
    project: str = Query(..., description="Project name"),
    types: str = Query(..., description="Comma-separated artifact types"),
) -> list[PipelineArtifactResponse]:
    """Discover active artifacts of given types for a project."""
    reg = _get_registry()
    type_list = [t.strip() for t in types.split(",") if t.strip()]

    def _discover():
        return reg.discover(project, *type_list)

    artifacts = await executors.run_in("io", _discover)
    return [
        PipelineArtifactResponse(
            id=a.id, type=a.type, producer=a.producer,
            created=a.created, file=a.file, summary=a.summary,
            superseded_by=a.superseded_by, run_id=a.run_id,
        )
        for a in artifacts
    ]


@router.get("/artifacts/pipeline/state")
async def get_pipeline_state(
    project: str = Query(..., description="Project name"),
) -> dict:
    """Get the current pipeline state for a project."""
    reg = _get_registry()

    def _get():
        return reg.get_pipeline_state(project)

    state = await executors.run_in("io", _get)
    return {"project": project, "pipeline_state": state}


@router.post("/artifacts/pipeline/publish")
async def publish_pipeline_artifact(req: PublishRequest) -> dict:
    """Publish a new artifact for a project."""
    # run_44a06138: run_id is a required `str` on the model (Pydantic 422s an
    # absent value — OpenAPI-honest, Kiro #13). The empty-string case the schema
    # can't catch still fails closed here.
    if not req.run_id or not req.run_id.strip():
        raise HTTPException(
            status_code=400,
            detail=("run_id is required: a run-blind publish writes a bare "
                    "top-level artifact that a sibling run can overwrite. "
                    "Pass run_id to publish run-scoped."),
        )
    reg = _get_registry()
    # TRUST BOUNDARY (run_44a06138): delegate project+run_id containment to the
    # registry SSOT BEFORE any write. reg.publish() also validates internally;
    # doing it here maps a traversal attempt to a clean 400 before threading.
    try:
        reg._validate_path_identifiers(req.project, req.run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    def _publish():
        return reg.publish(
            project=req.project,
            artifact_type=req.artifact_type,
            data=req.data,
            producer=req.producer,
            summary=req.summary,
            topic=req.topic,
            run_id=req.run_id,
        )

    try:
        artifact_id = await executors.run_in("io", _publish)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {"artifact_id": artifact_id, "project": req.project}


@router.post("/artifacts/pipeline/advance")
async def advance_pipeline(req: AdvanceRequest) -> dict:
    """Advance a project's pipeline state."""
    reg = _get_registry()

    def _advance():
        reg.advance_pipeline(req.project, req.state)

    try:
        await executors.run_in("io", _advance)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {"project": req.project, "pipeline_state": req.state}


@router.post("/artifacts/pipeline/supersede")
async def supersede_artifact(req: SupersedeRequest) -> dict:
    """Mark an artifact as superseded by a newer one."""
    reg = _get_registry()

    def _supersede():
        reg.supersede(req.project, req.old_id, req.new_id)

    await executors.run_in("io", _supersede)
    return {"old_id": req.old_id, "new_id": req.new_id, "project": req.project}


class LearnRequest(BaseModel):
    """Request body for recording pipeline outcome."""
    project: str
    evaluation_id: str
    outcome: str  # success, partial, failure, cancelled
    actual_effort: Optional[str] = None
    lessons: list[str] = []


@router.post("/artifacts/pipeline/learn")
async def record_learn_outcome(req: LearnRequest) -> dict:
    """Record pipeline outcome for learning feedback loop."""
    reg = _get_registry()

    def _learn():
        reg.record_outcome(
            project=req.project,
            evaluation_id=req.evaluation_id,
            outcome=req.outcome,
            actual_effort=req.actual_effort,
            lessons=req.lessons or None,
        )

    await executors.run_in("io", _learn)
    return {"project": req.project, "outcome": req.outcome, "recorded": True}
