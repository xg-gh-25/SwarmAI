"""ddd_bindings — DDD↔repo binding schema + PULL/codeIntel (Run 1 BIND layer).

This is the buildable core of the DDD-as-Agent-Brain "BIND" lifecycle: it turns a
DDD project from "4 plain markdown docs" into a DDD that BINDS real repos and builds
codeIntel over them. `s_project-manager` (SKILL.md) orchestrates by calling this.

Two responsibilities, deliberately separable:
  1. SCHEMA (pure, unit-testable) — Pydantic models encode the frozen canonical schema
     (Projects/AIDLC/.artifacts/runs/run_bb2c5bbe/GAP-REPORT-AND-SCHEMA.md §2c):
     ``bindings.yaml`` is an ARRAY of Binding, each carrying a DeliveryContract
     (governance-as-DATA — decision 7 — never in STEERING). ``load_bindings(path)``
     validates + raises a field-specific ValueError.
  2. PULL+INDEX (IO, smoke-testable) — ``bind_repo(binding, worktree_root)`` clones a
     git-clonable target into a worktree OUTSIDE the git-tracked SwarmWS workspace and
     builds a codeIntel graph by REUSING the existing indexer (``parse_repo`` +
     ``GraphStore.bulk_insert``) — zero new indexer.

Scope boundary (Run 1): only git-clonable targets are PULLed here. An ``internal`` /
``brazil``-build binding (e.g. GCRAIDLCPreset via ``brazil ws create``) needs Midway +
Brazil headless auth and is deferred to the pre-Run-2 spike (design §7.2.2) — ``bind_repo``
raises ``NotImplementedError`` for it rather than pretend.

Key public symbols: ``DeliveryContract``, ``Binding``, ``BindingsDoc``, ``BindResult``,
``load_bindings``, ``bind_repo``.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# Bound repos clone here — OUTSIDE ~/.swarm-ai/SwarmWS (the git-tracked workspace) so a
# cloned repo never pollutes workspace git. Resolved lazily (test may override worktree_root).
_DEFAULT_BINDINGS_ROOT = Path.home() / ".swarm-ai" / "bindings"


# ── Schema (frozen canonical §2c) ──────────────────────────────────────────

class DeliveryContract(BaseModel):
    """How a bound repo takes delivery — governance-as-DATA (decision 7).

    ``build_system`` is ORTHOGONAL to ``remote_kind``: a Brazil package is still
    reviewed via a code.amazon.com CR, so both are recorded independently.
    """

    remote_kind: Literal["github-pr", "code-amazon-cr"]
    build_system: Literal["brazil", "none"] = "none"
    branch: str
    version_set: Optional[str] = None
    review_path: str
    auto_send: str


class Binding(BaseModel):
    """One DDD↔repo binding entry."""

    repo: str
    kind: Literal["internal", "external"]
    clone: str  # a git-clonable URL/path OR a build-system command (e.g. "brazil ws create ...")
    worktree: Optional[str] = None
    code_intel: Optional[str] = None
    delivery_contract: DeliveryContract


class BindingsDoc(BaseModel):
    """The whole ``bindings.yaml`` — an ARRAY of bindings from v1 (a DDD may bind many repos)."""

    bindings: list[Binding]


@dataclass
class BindResult:
    """Outcome of a successful ``bind_repo``."""

    worktree: str
    code_intel_db: str
    node_count: int


# ── Load + validate ────────────────────────────────────────────────────────

def _format_validation_error(exc: ValidationError) -> str:
    """Turn a pydantic ValidationError into a message that NAMES each bad field."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) if parts else str(exc)


def load_bindings(path: str | Path) -> BindingsDoc:
    """Parse + validate a ``bindings.yaml`` into a ``BindingsDoc``.

    Raises ``ValueError`` (naming the offending field) on a malformed doc, or
    ``FileNotFoundError`` if *path* does not exist.
    """
    import yaml

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"bindings file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"bindings file is empty: {p}")
    try:
        return BindingsDoc.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(
            f"invalid bindings.yaml ({p}): {_format_validation_error(exc)}"
        ) from exc


# ── PULL + codeIntel ─────────────────────────────────────────────────────────

def bind_repo(binding: Binding, worktree_root: str | Path | None = None) -> BindResult:
    """Clone a git-clonable binding into a worktree and build its codeIntel graph.

    Reuses the existing indexer (``parse_repo`` + ``GraphStore.bulk_insert``) — no new
    indexer. Idempotent: an existing worktree is removed before re-clone (Gate-1 blocker 1).
    A fresh ``GitSyncEngine`` is used per call (git_clone mutates ``self.workspace_dir`` —
    Gate-1 blocker 2). ``graph.clear()`` runs before ``bulk_insert`` (Gate-1 blocker 3).

    Any ``internal`` binding OR any ``brazil`` build_system is DEFERRED to the
    pre-Run-2 Brazil/Midway spike (design §7.2.2) → raises ``NotImplementedError``
    (Run 1 binds git-clonable external targets only).

    Security: ``bind_repo`` performs ``shutil.rmtree`` on the worktree for idempotency,
    so the worktree MUST be confined under the bindings root — an absolute
    ``binding.worktree`` or a ``../`` in ``binding.repo`` would otherwise let a crafted
    bindings.yaml delete an arbitrary directory (Gate-2 HIGH, correctness+security
    confirmed). Both are validated to stay inside ``root`` before any rmtree.
    """
    if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
        raise NotImplementedError(
            f"internal/brazil binding '{binding.repo}' needs Brazil+Midway headless auth — "
            "deferred to the pre-Run-2 spike (design §7.2.2). Run 1 binds git-clonable targets only."
        )

    root = (Path(worktree_root) if worktree_root is not None else _DEFAULT_BINDINGS_ROOT).resolve()

    # Confine the worktree under root BEFORE any rmtree (Gate-2 HIGH: path-injection →
    # arbitrary-dir deletion). An absolute binding.worktree or a repo containing '..' /
    # a path separator is rejected — the destructive rmtree may only ever touch root/*.
    if binding.worktree:
        candidate = (root / binding.worktree).resolve()
    else:
        if "/" in binding.repo or "\\" in binding.repo or binding.repo in ("", ".", ".."):
            raise ValueError(
                f"invalid binding.repo '{binding.repo}': must be a bare name "
                "(no path separators) — it names a directory under the bindings root"
            )
        candidate = (root / binding.repo).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"binding worktree '{candidate}' escapes the bindings root '{root}' — "
            "binding.worktree must be a relative path inside the root (refusing to rmtree outside it)"
        )
    worktree = candidate

    # db_path confinement — validated BEFORE any clone I/O (meta-review MED: a traversal
    # in binding.code_intel should fail fast, not after a wasted network clone). Default
    # lands beside the confined worktree; binding.repo was already validated bare above.
    if binding.code_intel:
        db_path = (root / binding.code_intel).resolve()
        try:
            db_path.relative_to(root)
        except ValueError:
            raise ValueError(
                f"binding.code_intel '{db_path}' escapes the bindings root '{root}'"
            )
    else:
        db_path = worktree.parent / f"{binding.repo}.code_intel.db"

    worktree.parent.mkdir(parents=True, exist_ok=True)  # Gate-1 blocker 4

    # Idempotency: git clone fails (exit 128) into an existing non-empty dir → clean first.
    # Confined to root/* by the validation above.
    if worktree.exists():
        shutil.rmtree(worktree)

    # Fresh engine per call — git_clone mutates self.workspace_dir (Gate-1 blocker 2).
    from core.git_sync_engine import GitSyncEngine

    engine = GitSyncEngine(workspace_dir=worktree)
    ok = engine.git_clone(binding.clone, worktree)
    if not ok:
        # Do NOT echo binding.clone — a clone URL may embed credentials (Gate-2 security).
        raise RuntimeError(
            f"git clone failed for binding '{binding.repo}' (see git output above; "
            "clone source omitted from this message in case it carries credentials)"
        )

    # Index by reusing the existing whole-repo indexer — zero new indexer.
    from core.code_intel.graph_store import GraphStore
    from core.code_intel.parser import parse_repo

    parse_results = parse_repo(worktree)
    graph = GraphStore(db_path)
    try:
        graph.clear()  # Gate-1 blocker 3: bulk_insert is additive; clear first for a clean rebuild.
        if parse_results:
            graph.bulk_insert(parse_results)
        node_count = int(graph.get_codebase_summary().get("total_nodes", 0))
    finally:
        graph.close()

    logger.info(
        "bind_repo: bound '%s' → worktree=%s, code_intel=%s, nodes=%d",
        binding.repo, worktree, db_path, node_count,
    )
    return BindResult(worktree=str(worktree), code_intel_db=str(db_path), node_count=node_count)
