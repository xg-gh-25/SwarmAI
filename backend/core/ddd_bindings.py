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
``load_bindings``, ``governed_repo_roots``, ``bind_repo``.
"""
from __future__ import annotations

import logging
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

# Bound repos clone here — OUTSIDE ~/.swarm-ai/SwarmWS (the git-tracked workspace) so a
# cloned repo never pollutes workspace git. Resolved lazily (test may override worktree_root).
def _default_bindings_root() -> Path:
    """Default bindings clone-root, resolved AT CALL TIME via the single app-data authority.

    Must NOT be a module-level constant: a frozen home-relative default captures the
    value at import, before a test/sandbox sets SWARM_DATA_DIR.
    """
    from config import get_app_data_dir

    return get_app_data_dir() / "bindings"


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
    refresh_policy: Optional[str] = None   # ⑤ policy: when ⑥ rebuilds the derived code graph
    review_path: str
    auto_send: str


class Binding(BaseModel):
    """One DDD↔repo binding entry.

    NOTE: there is deliberately NO ``code_intel`` field. The code graph is derived
    from the code, NOT a DDD/binding member, so a binding never records its path:
    ``bind_repo`` derives the local codeIntel db path solely from the worktree. (A legacy bindings.yaml that still lists
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
    """The whole ``bindings.yaml`` — an ARRAY of bindings from v1 (a DDD may bind many repos).

    A DDD that governs no repo (a skill-set, jobs, or nothing — its manifest lists
    ``governed_assets`` but no ``bindings:``) binds zero repos; the key is optional
    for that reason. An explicit ``bindings:`` that is not an array is still invalid."""

    bindings: list[Binding] = Field(default_factory=list)


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


def _git_toplevel(path: Path) -> Optional[Path]:
    import os
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=5,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return None
    return Path(r.stdout.strip()).resolve() if r.returncode == 0 and r.stdout.strip() else None


def governed_repo_roots(project_dir: str | Path) -> list[Path]:
    """The local git repos project *project_dir* governs, as its ``bindings.yaml`` declares.

    Both shapes count: ``bindings[].worktree`` and ``governed_assets[]`` entries of
    ``kind: code-repo`` with a ``source_workspace``. A declared path is kept only when it
    is the toplevel of a git repo on disk. Nothing is guessed (usable-by-anyone P7): an
    asset without a path, the project's name, or where the daemon runs from never stand
    in for a declaration. Missing or unreadable bindings -> ``[]``. Order: declared order,
    duplicates dropped.
    """
    import os
    import yaml

    f = Path(project_dir) / "bindings.yaml"
    try:
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 — no / bad bindings = no repo
        logger.debug("governed_repo_roots: %s unreadable: %s", f, exc)
        return []
    if not isinstance(raw, dict):
        return []
    declared: list[str] = []
    for a in raw.get("governed_assets") or []:
        if isinstance(a, dict) and a.get("kind") == "code-repo" and a.get("source_workspace"):
            declared.append(str(a["source_workspace"]))
    for b in raw.get("bindings") or []:
        if isinstance(b, dict) and b.get("worktree"):
            declared.append(str(b["worktree"]))
    out: list[Path] = []
    for d in declared:
        p = Path(os.path.realpath(os.path.expanduser(d)))
        if p.is_dir() and _git_toplevel(p) == p and p not in out:
            out.append(p)
    return out


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

def binding_worktree(binding: Binding, worktree_root: str | Path | None = None) -> Path:
    """Where ``bind_repo`` clones *binding*: confined under the bindings root.

    The ONE reading of ``binding.worktree`` / ``binding.repo`` for bind-managed clones,
    shared by ``bind_repo`` (which writes there) and every reader that has to find the
    clone or its code-intel db again (``code_intel.code_intel_dbs``, the repo-ownership
    oracle). ``binding.worktree`` is resolved relative to the root; the result must be
    a directory directly under the root (an absolute value only if it is one). Raises
    ``ValueError`` when ``binding.repo`` is not a bare name or the worktree is anywhere
    else — e.g. a self-hosted binding whose worktree is the user's own checkout, which
    bind_repo never clones or deletes.
    """
    root = (Path(worktree_root) if worktree_root is not None else _default_bindings_root()).resolve()
    # binding.repo must ALWAYS be a bare name (no path separators / '..') — it is used
    # BOTH as the default worktree dir AND to build the db path. Validating it only
    # in the worktree-unset branch left the db path open to a '..' escape when
    # binding.worktree was explicitly set (Gate-2 LOW, run_f8ef133b).
    if "/" in binding.repo or "\\" in binding.repo or binding.repo in ("", ".", ".."):
        raise ValueError(
            f"invalid binding.repo '{binding.repo}': must be a bare name "
            "(no path separators) — it names a directory / db file under the bindings root"
        )
    candidate = (root / (binding.worktree or binding.repo)).resolve()
    # Exactly one level below the root: not the root itself (a re-bind would move every
    # project's clones), and not nested inside another clone, whose committed files
    # can then act as a git dir with its own config (core.fsmonitor runs a command).
    # ...and not a name this module keeps beside the clones (lock, db, preserved copy).
    if candidate.parent == root and (
            candidate.name.endswith((".bind.lock", ".code_intel.db"))
            or ".preserved-" in candidate.name):
        raise ValueError(
            f"binding worktree name '{candidate.name}' is reserved under the bindings root")
    if candidate.parent != root:
        raise ValueError(
            f"binding worktree '{candidate}' escapes the bindings root '{root}' — "
            "binding.worktree must name one directory directly under it "
            "(refusing to clone, rmtree or rename anywhere else)"
        )
    return candidate


def binding_db_path(binding: Binding, worktree_root: str | Path | None = None) -> Path:
    """The code-intel db ``bind_repo`` builds for *binding*: beside its confined worktree.

    Derived solely from the worktree (derived-projection rule, §3.6) — never a
    binding-recorded path. Raises ``ValueError`` like ``binding_worktree``.
    """
    return binding_worktree(binding, worktree_root).parent / f"{binding.repo}.code_intel.db"


def _worktree_has_unsaved_work(worktree: Path) -> bool:
    """True when *worktree* may hold work that exists nowhere else.

    Uncommitted, untracked or ignored files, or commits no remote has (on a branch, a
    tag, the stash, a detached HEAD or only the reflog). Any doubt — not a checkout git
    clone made, git failing, no remotes to compare against while local commits exist —
    counts as unsaved: the caller then preserves the directory instead of deleting it
    (STEERING #20, SOUL P4: recovery preserves, never destroys).
    """
    import os
    import subprocess

    # The repo is untrusted: never let its config run anything (fsmonitor, hooks), and
    # never let git climb out of the worktree looking for another repo.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0",
           "GIT_CEILING_DIRECTORIES": str(worktree.parent)}
    hardening = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]

    def _git(*args: str) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(["git", *hardening, "-C", str(worktree), *args],
                                  capture_output=True, text=True, timeout=30, env=env)
        except (OSError, subprocess.SubprocessError):
            return None

    def _non_empty() -> bool:
        try:
            return any(worktree.iterdir())
        except OSError:
            return True

    git_dir = worktree / ".git"
    if git_dir.is_symlink() or not git_dir.is_dir():
        # Not a checkout that git clone made: we cannot tell what is in it, so keep
        # it — unless it is genuinely empty.
        return _non_empty()
    top = _git("rev-parse", "--show-toplevel")
    if top is None or top.returncode != 0 or Path(top.stdout.strip()).resolve() != worktree.resolve():
        return _non_empty()
    # Ignored files count: a fresh clone has none, and .gitignore is upstream's, so
    # it can hide anything local (a .env, notes).
    status = _git("status", "--porcelain", "--untracked-files=all", "--ignored=matching")
    if status is None or status.returncode != 0 or status.stdout.strip():
        return True
    # Any commit no remote has, wherever it hangs: a branch, a tag, the stash, a
    # detached HEAD, or only the reflog.
    unpushed = _git("log", "--all", "--reflog", "--not", "--remotes", "--oneline", "-n", "1")
    if unpushed is None or unpushed.returncode != 0 or unpushed.stdout.strip():
        return True
    return False


def _clear_worktree_for_reclone(worktree: Path) -> Optional[Path]:
    """Make room for a fresh clone at *worktree*; return the preserved path, if any.

    A clean checkout (everything committed and on a remote) is deleted — the clone
    recreates it. Anything else is renamed to ``<name>.preserved-<UTC ts>`` beside it,
    never deleted, so a re-bind cannot destroy local work.
    """
    if not worktree.exists():
        return None
    if not _worktree_has_unsaved_work(worktree):
        shutil.rmtree(worktree)
        return None
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    preserved = worktree.with_name(f"{worktree.name}.preserved-{stamp}")
    n = 1
    while preserved.exists():
        preserved = worktree.with_name(f"{worktree.name}.preserved-{stamp}-{n}")
        n += 1
    worktree.rename(preserved)
    logger.warning(
        "bind_repo: worktree %s held uncommitted or unpushed work — preserved as %s "
        "before re-cloning (nothing deleted)", worktree, preserved)
    return preserved


_SCP_LIKE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.-]*:")


class BindInProgress(RuntimeError):
    """Another bind of the same worktree holds its bind lock."""


def _validate_clone_source(binding: Binding) -> None:
    """Refuse a clone source that is not https, ssh or a non-protected local path.

    ``binding.clone`` comes from a bindings.yaml an agent may have written from repo
    content. A local source (a plain path or ``file://``) must not be a protected
    system location — the same exfiltration guard the Library mount and Inbox copy
    use — or a crafted bindings.yaml could copy e.g. ~/.ssh into the bindings root and
    index it. Every other scheme (``ext::``, ``fd::``, ``git://``, ``http://`` ...) is
    refused here, before git; ``git_clone`` also limits git's own protocols.
    """
    clone = binding.clone.strip()
    lowered = clone.lower()
    if lowered.startswith(("https://", "ssh://")) or _SCP_LIKE.match(clone):
        return
    if lowered.startswith("file://"):
        from urllib.parse import unquote
        local = unquote(clone[len("file://"):])
    elif "://" in clone or "::" in clone:
        raise ValueError(
            f"binding '{binding.repo}': clone source scheme not allowed "
            "(https, ssh or a local path only) — refusing")
    else:
        local = clone
    from core.library_mounts import is_protected_system_path
    if not local or is_protected_system_path(local):
        raise ValueError(
            f"binding '{binding.repo}': clone source is a protected system path — refusing")


def _open_lock(path: Path):
    """Open (creating) a lock file without following a symlink planted at *path*."""
    import os
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    return os.fdopen(fd, "r+")


def bind_lock_path(worktree: str | Path) -> Path:
    """The lock file a bind holds while it clears, clones and indexes *worktree*."""
    worktree = Path(worktree)
    return worktree.with_name(f"{worktree.name}.bind.lock")


@contextmanager
def _bind_lock(worktree: Path, repo: str):
    """Hold *worktree*'s bind lock (exclusive, across processes) for one bind.

    Two binds of the same worktree would clear and clone over each other, and the
    file watcher would replay the clear and the clone as edits; the watcher skips
    while this lock is held (``bind_in_progress``). Without ``fcntl`` (not POSIX) no
    lock is taken.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover — non-POSIX
        yield
        return
    fh = _open_lock(bind_lock_path(worktree))
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BindInProgress(f"binding '{repo}': another bind of this repo is running")
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
    finally:
        fh.close()


def bind_in_progress(worktree: str | Path) -> bool:
    """True while some process holds *worktree*'s bind lock. Never raises."""
    try:
        import fcntl
        lock = bind_lock_path(worktree)
        if not lock.exists():
            return False
        with _open_lock(lock) as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
            return False
    except Exception as exc:  # noqa: BLE001 — a probe that cannot decide must not block indexing
        logger.debug("bind_in_progress: %s undecidable, treated as not binding: %s", worktree, exc)
        return False


def bind_repo(binding: Binding, worktree_root: str | Path | None = None) -> BindResult:
    """Clone a git-clonable binding into a worktree and build its codeIntel graph.

    Reuses the existing indexer (``parse_repo`` + ``GraphStore.bulk_insert``) — no new
    indexer. Idempotent: an existing CLEAN worktree is removed before re-clone (Gate-1
    blocker 1); one with uncommitted or unpushed work is renamed aside and kept
    (``_clear_worktree_for_reclone``).
    A fresh ``GitSyncEngine`` is used per call (git_clone mutates ``self.workspace_dir`` —
    Gate-1 blocker 2). ``graph.clear()`` runs before ``bulk_insert`` (Gate-1 blocker 3).

    Any ``internal`` binding OR any ``brazil`` build_system is DEFERRED to the
    pre-Run-2 Brazil/Midway spike (design §7.2.2) → raises ``NotImplementedError``
    (Run 1 binds git-clonable external targets only).

    Security: re-binding may delete or rename the worktree, so the worktree MUST be
    confined under the bindings root — an absolute ``binding.worktree`` outside it or a
    ``../`` in ``binding.repo`` would otherwise let a crafted bindings.yaml touch an
    arbitrary directory (Gate-2 HIGH, correctness+security confirmed).
    ``binding_worktree`` validates both before anything on disk changes.
    """
    if binding.kind == "internal" or binding.delivery_contract.build_system == "brazil":
        raise NotImplementedError(
            f"internal/brazil binding '{binding.repo}' needs Brazil+Midway headless auth — "
            "deferred to the pre-Run-2 spike (design §7.2.2). Run 1 binds git-clonable targets only."
        )

    worktree = binding_worktree(binding, worktree_root)
    db_path = binding_db_path(binding, worktree_root)

    _validate_clone_source(binding)

    worktree.parent.mkdir(parents=True, exist_ok=True)  # Gate-1 blocker 4
    with _bind_lock(worktree, binding.repo):
        return _bind_locked(binding, worktree, db_path)


def _bind_locked(binding: Binding, worktree: Path, db_path: Path) -> BindResult:
    """bind_repo's body, run while this worktree's bind lock is held."""

    # Idempotency: git clone fails (exit 128) into an existing non-empty dir, so the
    # old checkout has to go — deleted when clean, renamed aside when it holds work.
    _clear_worktree_for_reclone(worktree)

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
        else:
            graph.set_meta("repo_root", str(Path(worktree).resolve()))
        # When and at which commit: without these the freshness check reads the
        # graph as never indexed and the analysis reports indexed_commit null. Only
        # a parse that produced something is stamped: an empty graph marked at HEAD
        # would read fresh until the next commit (the reindex job's same rule).
        if parse_results:
            from core.brain_bootstrap.repo_read import git as _git
            head = (_git(Path(worktree), "rev-parse", "HEAD") or "").strip() or None
            graph.mark_indexed(full=True, head=head)
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
    if (d / "bindings.yaml").is_file():
        extras.append("bindings")

    tag = f" `[{cls}]`" if cls else ""
    struct = f", {', '.join(extras)}" if extras else ""
    suffix = f" (updated {freshness})" if freshness else ""
    return f"- **{d.name}**{tag} — {', '.join(docs)}{struct}{suffix}"
