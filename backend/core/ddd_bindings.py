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
    """How a bound repo takes delivery — governance-as-DATA (decision 7, generalized).

    This is section ⑤ of the canonical DDD structure (DDD-agent-brain spec §3.6): the
    product's FULL delivery 全貌 per bound repo, recorded as DATA/pointers. The DDD
    GOVERNs the physical delivery (指+治) — it never CONTAINS the code nor EXECUTES the
    pipeline (不含+不跑). Every field here is a pointer/policy the ④ capabilities act on
    (``brazil-build``/``cr``/pipeline API); the deploy pipeline itself runs on Amazon infra.

    ``build_system`` is ORTHOGONAL to ``remote_kind``: a Brazil package is still
    reviewed via a code.amazon.com CR, so both are recorded independently.

    ``remote_kind`` values: ``github-pr`` (public GitHub, PR-flow) / ``code-amazon-cr``
    (Amazon-internal, reviewed via a code.amazon.com CR) / ``self-hosted-main``
    (main-only auto-commit, no PR/CR — e.g. SwarmAI's own product source, governed by
    STEERING #5's commit=auto/push=user rule). ``build_system`` values: ``brazil`` /
    ``none`` / ``local-script`` (built by a repo-local script — e.g. SwarmAI's
    ``prod.sh`` / ``npm run build:all`` — NOT a hosted build system; the exact command
    is recorded in ``deploy_pipeline``). NOTE: skill ROUTING on these values lives in
    SKILL.md prose today (only github-pr/code-amazon-cr are routed); a ``self-hosted-main``
    repo loads + governs correctly but is not yet auto-routed by a review skill — that
    routing is a separate, deferred prose change.

    ``deploy_pipeline`` + ``refresh_policy`` (§3.6 ⑤ field list) are pointer DATA:
    ``deploy_pipeline`` names/refs the physical deploy pipeline (e.g. a pipelines.amazon.com
    id) the DDD GOVERNs but never runs; ``refresh_policy`` names when the ⑥ code-intel
    refresher regenerates the (derived, non-member) projection. Both Optional — a v1
    bindings.yaml that omits them still loads (backward-compat).
    """

    remote_kind: Literal["github-pr", "code-amazon-cr", "self-hosted-main"]
    build_system: Literal["brazil", "none", "local-script"] = "none"
    branch: str
    version_set: Optional[str] = None
    deploy_pipeline: Optional[str] = None  # ⑤ pointer: physical deploy pipeline ref/id (GOVERNed, never run here)
    refresh_policy: Optional[str] = None   # ⑤ policy: when ⑥ regenerates the derived code-intel projection
    review_path: str
    auto_send: str


class Binding(BaseModel):
    """One DDD↔repo binding entry.

    NOTE: there is deliberately NO ``code_intel`` field. Per the derived-projection
    rule (DDD-agent-brain spec §3.6), ``code-intel.json`` is a machine-generated
    projection of the code — NOT a DDD/binding member. It lives in a derived zone
    (regenerated locally by the ⑥ refresher, gitignored, never PR-flows-back), so a
    binding never records a projection path. ``bind_repo`` derives the local codeIntel
    db path solely from the worktree. (A legacy bindings.yaml that still lists
    ``code_intel:`` loads fine — pydantic's default ``extra='ignore'`` drops it.)
    """

    repo: str
    kind: Literal["internal", "external"]
    clone: str  # a git-clonable URL/path OR a build-system command (e.g. "brazil ws create ...")
    worktree: Optional[str] = None
    delivery_contract: DeliveryContract
    # Optional REFLOW map (Run 5, sync-back): {repo-relative-doc -> SwarmWS-relative-target}.
    # Declares WHICH DDD docs flow back from the bound repo into SwarmWS, and where.
    # Absent (None) => sync_back is a no-op for this binding (opt-in, safe default).
    sync_back: Optional[dict[str, str]] = None


class BindingsDoc(BaseModel):
    """The whole ``bindings.yaml`` — an ARRAY of bindings from v1 (a DDD may bind many repos)."""

    bindings: list[Binding]


@dataclass
class BindResult:
    """Outcome of a successful ``bind_repo``."""

    worktree: str
    code_intel_db: str
    node_count: int


@dataclass
class BindOutcome:
    """Per-binding result of ``bind_project`` — a tri-state wrapper over ``bind_repo``.

    ``BindResult`` is the SUCCESS-only payload; a project-level loop must also record
    deferred (internal/brazil) and failed (clone/index error) bindings WITHOUT letting
    one bad binding abort the rest. ``status``:
      - ``"bound"``    — bind_repo succeeded; worktree/code_intel_db/node_count set.
      - ``"deferred"`` — internal/brazil binding (bind_repo raised NotImplementedError);
                         PULL deferred to the pre-Run-2 Brazil/Midway spike.
      - ``"failed"``   — bind_repo raised (bad bindings field / clone / index); ``error``
                         carries the message. The loop continues (per-binding isolation).
    """

    repo: str
    kind: str
    status: Literal["bound", "deferred", "failed"]
    error: Optional[str] = None
    worktree: Optional[str] = None
    code_intel_db: Optional[str] = None
    node_count: Optional[int] = None


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


def classify_project(project_dir: str | Path) -> str:
    """The SINGLE SOURCE OF TRUTH for a DDD's class — derived, never stored.

    A DDD's class is NOT a persisted attribute (repo shape is unknown at CREATE, and
    a DDD may bind MANY repos of mixed kinds — any stored scalar would drift). It is a
    pure FUNCTION of the binding set, computed on read from ``<project>/bindings.yaml``:

    - ``"none"``     — no ⑤ bindings.yaml (or empty/unreadable) → pure-DDD, docs ARE the
                       deliverable; ⑥ code-intel + ⑤ delivery are no-ops.
    - ``"internal"`` — ANY binding is ``kind: internal`` (Amazon Brazil/CRUX). Internal
                       wins on a mixed set: the project needs the s_internal-* toolchain
                       + no_git_push gate the moment ONE internal repo is bound.
    - ``"external"`` — has bindings, none internal (GitHub/PR).

    Both provisioning (which skills/gate to copy) and delivery routing read THIS — no
    second source. (run_2acb67e1: replaces the disconnected create-time ``internal``
    flag that never fired.)
    """
    p = Path(project_dir) / "bindings.yaml"
    if not p.exists():
        return "none"
    try:
        doc = load_bindings(p)
    except Exception:
        # Unreadable/empty/malformed bindings (FileNotFoundError, ValueError, or a raw
        # yaml.YAMLError from a syntactically broken doc) → treat as no-repo. Fail-safe:
        # never mis-classify a broken doc as internal and provision CRUX skills spuriously,
        # and never let a bad bindings.yaml crash provisioning/routing.
        return "none"
    if not doc.bindings:
        return "none"
    if any(b.kind == "internal" for b in doc.bindings):
        return "internal"
    return "external"


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

    # binding.repo must ALWAYS be a bare name (no path separators / '..') — it is used
    # BOTH as the default worktree dir AND to build db_path below. Validating it only
    # in the worktree-unset branch left db_path (worktree.parent/<repo>.code_intel.db)
    # open to a '..' escape when binding.worktree was explicitly set (Gate-2 LOW,
    # run_f8ef133b). Check unconditionally so the invariant "binding.repo is bare" holds
    # on every path — this is what makes the db_path derivation safe (comment below).
    if "/" in binding.repo or "\\" in binding.repo or binding.repo in ("", ".", ".."):
        raise ValueError(
            f"invalid binding.repo '{binding.repo}': must be a bare name "
            "(no path separators) — it names a directory / db file under the bindings root"
        )

    # Confine the worktree under root BEFORE any rmtree (Gate-2 HIGH: path-injection →
    # arbitrary-dir deletion). An absolute binding.worktree is rejected — the destructive
    # rmtree may only ever touch root/*.
    if binding.worktree:
        candidate = (root / binding.worktree).resolve()
    else:
        candidate = (root / binding.repo).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise ValueError(
            f"binding worktree '{candidate}' escapes the bindings root '{root}' — "
            "binding.worktree must be a relative path inside the root (refusing to rmtree outside it)"
        )
    worktree = candidate

    # db_path is derived SOLELY from the confined worktree — the code-intel projection
    # is a derived artifact, never a binding-recorded path (derived-projection rule,
    # §3.6). It lands beside the confined worktree; binding.repo was already validated
    # bare above, and worktree is already confined under root, so this stays inside root.
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
            graph.bulk_insert(parse_results, repo_root=worktree)
        node_count = int(graph.get_codebase_summary().get("total_nodes", 0))
    finally:
        graph.close()

    logger.info(
        "bind_repo: bound '%s' → worktree=%s, code_intel=%s, nodes=%d",
        binding.repo, worktree, db_path, node_count,
    )
    # A new worktree now exists on disk → the review-verdict authority's cached
    # worktree map is stale (it would misclassify this repo's source files until
    # daemon restart, Gate-2 F2). Invalidate it now. Local import avoids an import
    # cycle (needs_human_review imports ddd_bindings.load_bindings function-locally).
    try:
        from core.needs_human_review import clear_worktree_cache
        clear_worktree_cache()
    except Exception:  # noqa: BLE001 — cache invalidation must never fail a bind
        pass
    return BindResult(worktree=str(worktree), code_intel_db=str(db_path), node_count=node_count)


# ── ORCHESTRATION: the real CREATE→BIND→PULL caller (run_8a3e7ebf) ─────────────
#
# bind_repo was an ORPHAN — zero production callers, invokable only via a python -c
# prose recipe in s_project-manager/SKILL.md. bind_project is its first real caller:
# it resolves a project's ⑤ bindings.yaml, loops EVERY binding, and PULLs each with
# per-binding error isolation so a DDD that binds MANY repos (bindings is list[Binding])
# processes each independently — one unreachable repo never aborts the others.

def bind_project(
    project_name: str,
    projects_dir: str | Path | None = None,
    worktree_root: str | Path | None = None,
) -> list[BindOutcome]:
    """PULL every binding of a project's ``bindings.yaml`` — the orchestrated caller.

    Resolves ``<projects_dir>/<project_name>/bindings.yaml`` (``projects_dir`` defaults
    to ``jobs.paths.PROJECTS_DIR``; overridable for tests), validates it via
    ``load_bindings``, then loops ALL ``doc.bindings`` calling ``bind_repo`` per entry.

    Per-binding isolation (the multi-repo + negative-path guarantee): each binding is
    wrapped so its outcome is one of bound / deferred / failed, and NO single binding
    can abort the loop —
      - success              → BindOutcome(status="bound", worktree/db/node_count set)
      - NotImplementedError   → BindOutcome(status="deferred")   (internal/brazil, Run-1 scope)
      - any other exception   → BindOutcome(status="failed", error=str(e))

    Returns a list of BindOutcome (one per binding, in file order). A project with no
    bindings.yaml (a pure-DDD "none" project) returns ``[]`` — not an error.
    """
    if projects_dir is not None:
        base = Path(projects_dir)
    else:
        from jobs.paths import PROJECTS_DIR
        base = Path(PROJECTS_DIR)

    bindings_path = base / project_name / "bindings.yaml"
    if not bindings_path.exists():
        # A no-repo / pure-DDD project — nothing to PULL. Not an error.
        logger.info("bind_project: no bindings.yaml for '%s' (pure-DDD) — nothing to PULL", project_name)
        return []

    doc = load_bindings(bindings_path)  # raises ValueError (named field) on a malformed doc

    outcomes: list[BindOutcome] = []
    for b in doc.bindings:
        try:
            r = bind_repo(b, worktree_root)
            outcomes.append(BindOutcome(
                repo=b.repo, kind=b.kind, status="bound",
                worktree=r.worktree, code_intel_db=r.code_intel_db, node_count=r.node_count,
            ))
        except NotImplementedError as e:
            # internal/brazil binding — PULL deferred to the pre-Run-2 Midway spike.
            outcomes.append(BindOutcome(repo=b.repo, kind=b.kind, status="deferred", error=str(e)))
        except Exception as e:  # noqa: BLE001 — per-binding isolation: capture, never abort the loop.
            # Bad bindings field / clone failure / index error → record + continue.
            logger.warning("bind_project: binding '%s' FAILED: %s: %s",
                           b.repo, type(e).__name__, e)
            outcomes.append(BindOutcome(repo=b.repo, kind=b.kind, status="failed", error=str(e)))
    return outcomes


# ── SYNC-BACK (Run 5): the REVERSE of bind_repo — reflow repo DDD edits → SwarmWS ──────

def _confine(base: Path, rel: str, label: str) -> Path:
    """Resolve ``rel`` under ``base`` and REFUSE if it escapes (path-traversal guard,
    same discipline as bind_repo's worktree confinement). ``rel`` from a binding is
    user data — an absolute path or ``..`` must never read/write outside ``base``."""
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        raise ValueError(
            f"sync_back {label} path '{rel}' escapes its root '{base}' — "
            "mapping paths must stay inside the worktree / workspace"
        )
    return candidate


def _diff_doc(repo_doc: Path, ws_target: Path) -> tuple[str, str]:
    """Return (status, unified_diff) comparing a repo doc to its SwarmWS target.

    status ∈ {changed, unchanged, new-in-repo, missing-in-repo, binary, too-large}.
    Every read is guarded — a missing/binary/oversized doc classifies, never crashes
    (the gate must be robust; a huge doc must not OOM the diff — Gate-2 MED)."""
    import difflib

    # A DDD doc is prose (KB-scale). Cap reads so a mis-mapped huge/generated file can't
    # allocate an unbounded in-memory diff (Gate-2 MED: DoS via a 100MB mapped file).
    _MAX_DOC_BYTES = 1_000_000  # 1 MB — generous for any real DDD doc

    def _too_big(p: Path) -> bool:
        try:
            return p.stat().st_size > _MAX_DOC_BYTES
        except OSError:
            return False

    repo_exists, ws_exists = repo_doc.exists(), ws_target.exists()
    if not repo_exists and ws_exists:
        return "missing-in-repo", ""   # doc removed upstream — surface, don't act
    if repo_exists and not ws_exists:
        if _too_big(repo_doc):
            return "too-large", ""
        try:
            repo_doc.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return "binary", ""
        return "new-in-repo", ""
    if not repo_exists and not ws_exists:
        return "missing-in-repo", ""
    if _too_big(repo_doc) or _too_big(ws_target):
        return "too-large", ""   # skip diffing an oversized doc (never OOM)
    try:
        repo_lines = repo_doc.read_text(encoding="utf-8").splitlines(keepends=True)
        ws_lines = ws_target.read_text(encoding="utf-8").splitlines(keepends=True)
    except (UnicodeDecodeError, OSError):
        return "binary", ""
    if repo_lines == ws_lines:
        return "unchanged", ""
    diff = "".join(difflib.unified_diff(
        ws_lines, repo_lines,
        fromfile=f"swarmws/{ws_target.name}", tofile=f"repo/{repo_doc.name}",
    ))
    return "changed", diff


def sync_back(binding: Binding, worktree_root: str | Path, ws_root: str | Path,
              now_iso: Optional[str] = None) -> dict:
    """Reflow a bound repo's DDD edits back toward SwarmWS — the reverse of bind_repo.

    For each entry in ``binding.sync_back`` {repo-doc -> ws-target}: diff the repo doc
    (in the ALREADY-PULLED worktree) against its SwarmWS target and SURFACE a reviewable
    delta. This function is **non-destructive**: it only READS the SwarmWS targets (for
    diffing) and WRITES the delta report to ``Projects/<repo>/.artifacts/sync-back/`` —
    it NEVER mutates the live DDD docs (they carry cultivation edits a blind overwrite
    would destroy). The ``git pull`` is HITL: this returns the pull command for a human
    to run; it performs no network/git call itself (the ssh-agent auth wall lives on the
    human side, same as Run 2/3).

    Returns ``{pull_command, deltas:[{repo_doc, ws_target, status, diff}], report_path}``.
    A binding with no ``sync_back`` map returns an empty delta (opt-in no-op).
    """
    wt = Path(worktree_root).resolve()
    ws = Path(ws_root).resolve()
    pull_command = f"git -C {wt} pull"   # HITL — surfaced, never executed here

    if not binding.sync_back:
        return {"pull_command": pull_command, "deltas": [], "report_path": None}

    deltas = []
    for repo_rel, ws_rel in binding.sync_back.items():
        repo_doc = _confine(wt, repo_rel, "repo-doc")
        ws_target = _confine(ws, ws_rel, "ws-target")
        status, diff = _diff_doc(repo_doc, ws_target)
        deltas.append({
            "repo_doc": repo_rel, "ws_target": ws_rel,
            "status": status, "diff": diff,
        })

    # Surface the delta as a reviewable report — NEVER touch the live DDD docs.
    report_path = None
    changed = [d for d in deltas if d["status"] not in ("unchanged",)]
    if changed:
        ts = now_iso or _utc_now_iso()
        safe_ts = ts.replace(":", "").replace("-", "").replace(".", "")
        out_dir = _confine(ws, f"Projects/{binding.repo}/.artifacts/sync-back", "report-dir")
        out_dir.mkdir(parents=True, exist_ok=True)
        report_path = out_dir / f"{safe_ts}.md"
        lines = [f"# Sync-back delta — {binding.repo} — {ts}", "",
                 f"Pull first (HITL): `{pull_command}`", "",
                 "Review each delta below; nothing was written to the live DDD docs.", ""]
        for d in changed:
            lines.append(f"## `{d['repo_doc']}` → `{d['ws_target']}`  [{d['status']}]")
            if d["diff"]:
                lines.append("```diff\n" + d["diff"] + "\n```")
            lines.append("")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("sync_back: %s — %d changed doc(s) surfaced to %s",
                    binding.repo, len(changed), report_path)

    return {"pull_command": pull_command, "deltas": deltas,
            "report_path": str(report_path) if report_path else None}


def _utc_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# Canonical DDD doc names, in display order.
# Run 0 (run_393e3dc1): single source of truth — see project_registry.DDD_CANONICAL_DOCS.
from core.project_registry import DDD_CANONICAL_DOCS as _DDD_DOC_NAMES
from core.ddd_paths import ddd_path  # six-section layout resolver (SSOT)


def _compute_ddd_freshness(project_dir: Path, docs: list[str]) -> str:
    """Freshness label from the most-recent DDD doc mtime (today / Nd ago / Nd stale).

    ``docs`` are canonical doc NAMES; each is resolved via ddd_path so a migrated
    DDD (docs under 2-understanding/) is read at its real location, not root."""
    import time
    resolved = [ddd_path(project_dir, f) for f in docs]
    mtimes = [p.stat().st_mtime for p in resolved if p.is_file()]
    days_ago = int((time.time() - max(mtimes)) / 86400) if mtimes else 999
    if days_ago == 0:
        return "today"
    if days_ago <= 7:
        return f"{days_ago}d ago"
    return f"**{days_ago}d stale**"


def describe_project_ddd_line(project_dir: str | Path, freshness: str | None = None) -> str | None:
    """Formatter for one 'Active Projects & DDD' index line. NO production caller.

    STATUS (2026-08-14): unreferenced in production, and nothing writes the section
    it formats. All three writers were deleted when the in-prompt markdown indexes
    were removed — ``ddd_orchestrator._ch_inject_knowledge``,
    ``context_health_hook._refresh_knowledge_projects_section``, and
    ``loops_health_check._fix_ddd_injection`` (finding C3). A repo-wide grep for
    ``describe_project_ddd_line`` finds only ``TestSpecDetailsIndexRow`` in
    ``skills/s_repo-to-ddd/scripts/test_ai_ready_helpers.py`` — tests, and ones CI
    does not collect (``pyproject`` sets ``testpaths = ["tests"]``, so that file
    never runs). Do NOT restate this as "the single source of truth for remaining
    callers": there are none, and claiming otherwise is what this docstring is
    replacing.

    Kept rather than deleted only because the body is not cheaply re-derivable — it
    encodes two production incidents that a fresh reimplementation would repeat:
    ``ddd_path`` resolution so a migrated six-section DDD is still found (run_af3dfd9f:
    a bare ``d / f`` root check made migrated projects vanish from the index) and
    ``ddd_path(d, "capabilities")`` so a migrated layout does not report "0 skills"
    (run_cfb0f28f, Gate-2 CRITICAL). If the section is not coming back, this and its
    three tests are a delete candidate; if a writer IS reintroduced it MUST call this
    instead of formatting inline (run_99b70b3c: two writers with divergent formats
    rewrote the section back and forth every cycle).

    Format: ``- **Name** `[cls]` — DOC, DOC, … , extra, extra (updated <freshness>)``
      - ``[cls]`` from classify_project (none/external/internal); omitted if unknown.
      - structure extras (N skills / gates / Knowledge/ / bindings) present on disk.
      - ``(updated <freshness>)`` ALWAYS appended — freshness is computed from doc
        mtime when the caller does not supply it, so a caller passing None cannot
        produce a line that diverges from one passing an explicit value.

    Returns None if the project has none of the 4 canonical DDD docs (skip it).
    Fail-safe: classify_project errors → no tag, never raises.
    """
    d = Path(project_dir)
    # Resolve each canonical doc via ddd_path (strangler): a migrated DDD keeps
    # its 4 docs under 2-understanding/, so a bare `d / f` root check would find
    # none → the project vanishes from the KNOWLEDGE.md index (caught by live
    # post-deploy smoke, run_af3dfd9f). ddd_path finds new-or-old location.
    docs = [f for f in _DDD_DOC_NAMES if ddd_path(d, f).is_file()]
    if not docs:
        return None

    if freshness is None:
        try:
            freshness = _compute_ddd_freshness(d, docs)
        except Exception:
            freshness = None

    cls = None
    try:
        cls = classify_project(d)
    except Exception:
        cls = None

    extras = []
    # Route through the six-section resolver (strangler): finds 4-capabilities/
    # (new) or skills/ (un-migrated), etc. A hardcoded old-path check here would
    # report "0 skills" for a migrated DDD (Gate-2 CRITICAL, run_cfb0f28f).
    skills_dir = ddd_path(d, "capabilities")
    if skills_dir.is_dir():
        n_skills = sum(1 for s in skills_dir.iterdir() if s.is_dir() and s.name.startswith("s_"))
        if n_skills:
            extras.append(f"{n_skills} skills")
    if ddd_path(d, "gates").is_dir():
        extras.append("gates")
    if ddd_path(d, "knowledge").is_dir():
        extras.append("Knowledge/")
    # code-intel v3 (run_b5993cdb A): surface the derived spec-details/ projection
    # so the DDD index makes the system AWARE it exists (OT07 prevention — a
    # generated domain layer that no index shows is a silent orphan). This is a
    # DERIVED projection dir, NOT a 5th canonical doc (project_registry.py) — it
    # rides in `extras`, never in the canonical `docs` list that gates completeness.
    spec_dir = d / "spec-details"
    if spec_dir.is_dir():
        n_specs = sum(1 for s in spec_dir.glob("*.spec.md") if s.is_file())
        if n_specs:
            extras.append(f"spec-details/({n_specs} specs)")
    if (d / "bindings.yaml").is_file():
        extras.append("bindings")

    tag = f" `[{cls}]`" if cls else ""
    struct = f", {', '.join(extras)}" if extras else ""
    suffix = f" (updated {freshness})" if freshness else ""
    return f"- **{d.name}**{tag} — {', '.join(docs)}{struct}{suffix}"
