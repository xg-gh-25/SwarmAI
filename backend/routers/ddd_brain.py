"""DDD Brain Hub — projection + review router (/api/ddd/brains…).

The backend half of the Brain Hub "lens" (design 2026-07-28), grown across four
Runs. Most endpoints are READ projections over existing state that introduce NO
stored metric (R30#4: every count is computed live per request) — but the Review
tab (Run 2) added two **mutating** POSTs, so this router is NO LONGER pure-read.

Six handlers (4 read, 2 mutating):

  READ:
  GET  /api/ddd/brains                     → Gallery: one summary card per DDD.
  GET  /api/ddd/brains/{name}              → Brain view: six-section breakdown +
                                             per-entry decay/type state of ② docs.
  GET  /api/ddd/brains/{name}/review       → Review queue: tagged git-diff hunks
                                             since the watermark + pending proposals.
  GET  /api/ddd/brains/{name}/distribution → Distribute: declared reach + output state.

  MUTATING (write the working tree / a stored artifact — NOT read-only):
  POST /api/ddd/brains/{name}/review/approve → advances the last-reviewed watermark
                                             to HEAD by WRITING .artifacts/.last-reviewed-sha
                                             (a per-DDD stored file — the one persisted artifact).
  POST /api/ddd/brains/{name}/review/reject  → reverse-applies ONE hunk via
                                             `git apply -R` (MODIFIES the working tree,
                                             subtree-scoped, --check-guarded).

⚠️ No app-layer auth on these POSTs — consistent with every other SwarmAI router
(the daemon binds 127.0.0.1 and Hive fronts /api/* with Caddy basic_auth; app-layer
auth is not the codebase pattern). Do not add route auth to only this router.

Data sources:
  - structure   ← core.ddd_paths (six-section layout SSOT — NEVER hardcode dirs)
  - judgment    ← core.ddd_entry_lifecycle.parse_entries (decay_state/type/…)
  - change      ← `git status --porcelain -z` (run here via asyncio.to_thread —
                  Gate-1 revision: we do NOT import workspace_api's private
                  `_get_git_status` across a router boundary) + `git log -1`
  - pending     ← core.ddd_cultivation.read_pending_proposals (staged proposals)

Design conformance:
  - ⑤ Delivery (bindings.yaml) / ⑥ Refresher (REFRESHER.md) resolve to the
    project ROOT via ddd_paths; they are single well-known FILES, so we
    enumerate them as such and NEVER iterdir the project root (Gate-1 revision).
  - No ref_count / recall-heat number is emitted (ref_count is dead — showing it
    would be fabricated data). Only the decay-time proxy is surfaced.
  - An empty ③Gates section is COMPLETE, not broken (R31).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.ddd_entry_lifecycle import (
    MEMORY_EVERGREEN_SECTIONS,
    compute_reclaimable_noise,
    parse_entries,
)
from core.ddd_paths import IDENTITY_FILE, ddd_path, section_dir
from core.project_registry import DDD_CANONICAL_DOCS, SPEC_DETAILS_DIR

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ddd-brain"])

# The 4 canonical ② docs whose entries carry decay/type state. Single-source
# from project_registry (Run 0 rule — never hardcode the canonical-4 tuple).
_KNOWLEDGE_DOCS = DDD_CANONICAL_DOCS

# Section descriptor: (key, circled-num, display label, OWN|GOVERN, curator role).
# Curator roles are informational seams for future multi-user ownership (design
# §4.2) — single writer today. Verbatim from the AGENTS.md six-section table.
_SECTIONS: tuple[tuple[str, str, str, str, str], ...] = (
    ("identity", "①", "Identity & Manifest", "OWN", "Owner"),
    ("knowledge", "②", "Knowledge", "OWN", "PM / Tech Lead / QA / TPM"),
    ("gates", "③", "Gates", "OWN", "Tech Lead"),
    ("capabilities", "④", "Capabilities", "OWN", "Tech Lead"),
    ("delivery", "⑤", "Delivery Contract", "GOVERN", "TPM / SDM"),
    ("refresher", "⑥", "Refresher", "GOVERN", "TPM / SDM"),
)


# ─── Workspace resolution ────────────────────────────────────────────────────

def _workspace_root() -> Path:
    """Resolve the active workspace root (…/SwarmWS).

    Uses the swarm_workspace_manager's resolver so tests + prod agree. Falls
    back to the conventional path if the manager isn't wired (test import).
    """
    try:
        from core.swarm_workspace_manager import swarm_workspace_manager

        # public wrapper (== _resolve_workspace_path(None)) — avoids coupling to
        # the private method (meta-review nit); same resolver projects.py uses.
        return Path(swarm_workspace_manager.get_workspace_path())
    except Exception:  # pragma: no cover - defensive fallback
        return Path.home() / ".swarm-ai" / "SwarmWS"


def _projects_root() -> Path:
    return _workspace_root() / "Projects"


def _resolve_brain_dir(name: str) -> Optional[Path]:
    """Resolve a brain NAME to its project dir, CONTAINED within Projects/.

    Defense-in-depth (Gate-2 security): Starlette already normalizes `../` in the
    URL path (a traversal name 404s at routing), but this is the ONLY place the
    review endpoints — one of which runs a destructive `git apply -R` — turn an
    external name into a filesystem path. So we containment-check here too: the
    resolved dir MUST be a direct child of the resolved Projects/ root AND carry a
    .project.json. Anything else → None (→ 404), never a path outside Projects/."""
    root = _projects_root().resolve()
    try:
        pd = (_projects_root() / name).resolve()
    except (OSError, ValueError):
        return None
    if pd.parent != root:                      # must be a DIRECT child of Projects/
        return None
    if not (pd.is_dir() and (pd / ".project.json").exists()):
        return None
    return pd


def _list_project_dirs() -> list[Path]:
    """Every DDD project dir (a Projects/ child carrying a .project.json)."""
    root = _projects_root()
    if not root.is_dir():
        return []
    dirs = [
        d for d in root.iterdir()
        if d.is_dir() and (d / ".project.json").exists()
    ]
    dirs.sort(key=lambda d: d.name.lower())
    return dirs


# ─── Git (self-contained — Gate-1: no private cross-router import) ───────────

def _git_status_dirty(project_dir: Path) -> bool:
    """True if the project subtree has uncommitted changes.

    Scoped to the project path via a pathspec so we don't scan the whole
    workspace. Runs in a worker thread (see callers) — subprocess is blocking.
    """
    ws = _workspace_root()
    if not (ws / ".git").exists():  # .exists(): a worktree/submodule has .git as a FILE
        return False
    try:
        rel = project_dir.relative_to(ws).as_posix()
    except ValueError:
        rel = project_dir.as_posix()
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-unormal", "--", rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip("\0").strip())


def _git_last_commit_iso(project_dir: Path) -> Optional[str]:
    """ISO timestamp of the last commit touching this project subtree, or None."""
    ws = _workspace_root()
    if not (ws / ".git").exists():  # .exists(): a worktree/submodule has .git as a FILE
        return None
    try:
        rel = project_dir.relative_to(ws).as_posix()
    except ValueError:
        rel = project_dir.as_posix()
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    out = result.stdout.strip()
    return out or None


def _file_git_status(project_dir: Path, file_rel: str) -> str:
    """Git status of a single project-relative file: clean|modified|untracked|…"""
    ws = _workspace_root()
    if not (ws / ".git").exists():  # .exists(): a worktree/submodule has .git as a FILE
        return "clean"
    try:
        rel = (project_dir.relative_to(ws) / file_rel).as_posix()
    except ValueError:
        rel = file_rel
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-unormal", "--", rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "clean"
    if result.returncode != 0 or not result.stdout.strip("\0").strip():
        return "clean"
    xy = result.stdout.split("\0")[0][:2]
    if "U" in xy:
        return "conflicting"
    if "R" in xy:
        return "renamed"
    if xy == "??":
        return "untracked"
    if "D" in xy:
        return "deleted"
    if "A" in xy:
        return "added"
    return "modified"


def _relative_time(iso: Optional[str]) -> str:
    """Human 'N ago' from an ISO timestamp (computed live — never stored)."""
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return "unknown"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - then
    secs = int(delta.total_seconds())
    if secs < 0:
        return "just now"
    if secs < 3600:
        return f"{max(1, secs // 60)}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


# ─── Pending proposals (read-only) ───────────────────────────────────────────

def _pending_count(project_name: str) -> int:
    try:
        from core.ddd_cultivation import read_pending_proposals

        return len(read_pending_proposals(_workspace_root(), project_name))
    except Exception:  # pragma: no cover - defensive
        return 0


# ─── Section member enumeration (six-section, SSOT-resolved) ─────────────────

def _section_members(project_dir: Path, key: str) -> list[str]:
    """Project-relative member paths for a section, via ddd_paths SSOT.

    ⑤/⑥ resolve to '.' (root) — they are single well-known FILES, so we return
    the specific file (if present) and NEVER iterdir the root (Gate-1 revision).
    ① is the single AGENTS.md file. ②/③/④ are real directories we scan (files
    for ②, immediate children for ③/④).
    """
    if key == "identity":
        return [IDENTITY_FILE] if (project_dir / IDENTITY_FILE).exists() else []

    if key == "delivery":
        p = ddd_path(project_dir, "bindings.yaml")
        return [_rel(project_dir, p)] if p.exists() else []

    if key == "refresher":
        p = ddd_path(project_dir, "REFRESHER.md")
        return [_rel(project_dir, p)] if p.exists() else []

    if key == "knowledge":
        # ② members = the canonical docs (PRODUCT/TECH/…). They live directly
        # under 2-understanding/ (resolved per-doc via ddd_path's canonical-doc
        # special case) — NOT under section_dir("knowledge"), which is the
        # 2-understanding/knowledge/ recall CORPUS subdir. Enumerate per-doc.
        out = []
        for doc in _KNOWLEDGE_DOCS:
            fp = ddd_path(project_dir, doc)
            if fp.exists():
                out.append(_rel(project_dir, fp))
        return out

    # ③ gates / ④ capabilities — real section directories, immediate children.
    d = section_dir(project_dir, key)
    if not d.is_dir() or d == project_dir:
        return []

    # skip dotfiles / .gitkeep.
    out = []
    for child in sorted(d.iterdir(), key=lambda c: c.name.lower()):
        if child.name.startswith("."):
            continue
        out.append(_rel(project_dir, child))
    return out


def _rel(project_dir: Path, p: Path) -> str:
    try:
        return p.relative_to(project_dir).as_posix()
    except ValueError:
        return p.name


# ─── Summary + detail builders ───────────────────────────────────────────────

def _sinking_count(project_dir: Path) -> int:
    """dormant + archived entries across the ② canonical docs (live)."""
    total = 0
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        try:
            for e in parse_entries(p.read_text(encoding="utf-8")):
                if e.decay_state in ("dormant", "archived"):
                    total += 1
        # UnicodeError (a ValueError) on a non-UTF-8 doc must NOT crash the
        # gallery — a single bad file degrades to 0, never a 500 (PIT44 class).
        except (OSError, ValueError, UnicodeError):
            continue
    return total


def _entry_count(project_dir: Path) -> int:
    total = 0
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if p.exists():
            try:
                total += len(parse_entries(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, UnicodeError):
                continue
    return total


def _parse_all_knowledge_entries(project_dir: Path) -> list:
    """All parsed entries across the ② canonical docs (shared by health metrics).

    One parse pass per canonical doc — the same per-doc loop _sinking_count /
    _entry_count use, factored out so _brain_health reuses it. A non-UTF-8 or
    unreadable doc degrades to zero contribution (never a 500), matching the
    sibling helpers' fault tolerance.
    """
    entries: list = []
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        try:
            entries.extend(parse_entries(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, UnicodeError):
            continue
    return entries


def _brain_health(project_dir: Path) -> dict:
    """Admission-passing DDD health metrics for the DETAIL view (per-open).

    Design: Knowledge/Designs/2026-08-04-ddd-health-metrics-brainhub-component.md.
    Each metric earns its place by the §1 admission gate (owner action + live/read,
    never a frozen verdict). Lives in _brain_detail (per-open) — NEVER _brain_summary
    (that N-globs the gallery, ddd_brain.py:458/463).

    - noise: {reclaimable, rate} — computed LIVE via compute_reclaimable_noise over
      the ② docs (owner action: >threshold → reclaim-strip). No side effect.
    - trust / diagnostics / computedAt: READ from the stored section_health.json via
      the read-only _load_last_scores (written by the scheduled health path). The GET
      path NEVER calls compute_section_health — that function WRITES the file, and a
      disk write in a read handler is forbidden (Gate-1 CRITICAL, run_d7146171). Absent
      score → None (honest: no scheduled computation yet; never fabricated, never a
      write). No project-composite is invented (Gate-1 MAJOR — that would be a vanity
      metric); the per-doc scores are surfaced verbatim.
    - escalationPending: reuse _pending_count (same source as the gallery — no divergence).
    - recall: {value:None, experimental:True} — recall_suite is a pinned-corpus
      benchmark with no cheap per-DDD value; the tile is shown but labeled experimental
      (design §4), never fabricated.
    """
    # noise — live, no side effect
    try:
        entries = _parse_all_knowledge_entries(project_dir)
        nr = compute_reclaimable_noise(
            entries, date.today(), evergreen_sections=MEMORY_EVERGREEN_SECTIONS
        )
        noise = {"reclaimable": nr.noisy, "rate": round(nr.noise_rate, 4)}
    except Exception:  # pragma: no cover - defensive: noise never 500s a brain view
        noise = {"reclaimable": 0, "rate": 0.0}

    # trust / diagnostics — READ-ONLY from the stored scheduled score (no write)
    trust = None
    diagnostics = None
    computed_at = None
    try:
        from core.ddd_health import _load_last_scores

        stored = _load_last_scores(project_dir)  # {} when section_health.json absent
        if stored:
            # per-doc trust (as stored — no invented rollup, Gate-1 MAJOR)
            trust = {
                doc: {
                    sec: s.get("trust")
                    for sec, s in doc_data.get("sections", {}).items()
                }
                for doc, doc_data in stored.items()
            }
            diagnostics = stored  # the 5-dim per-section scores, verbatim
    except Exception:  # pragma: no cover - defensive
        trust = None
        diagnostics = None
    # computedAt lives on the state file, not inside `docs` — read it read-only.
    try:
        import json as _json

        sh = project_dir / ".artifacts" / "section_health.json"
        if sh.is_file():
            computed_at = _json.loads(sh.read_text(encoding="utf-8")).get("computed_at")
    except (OSError, ValueError):
        computed_at = None

    return {
        "noise": noise,
        "trust": trust,
        "escalationPending": _pending_count(project_dir.name),
        "recall": {"value": None, "experimental": True},
        "diagnostics": diagnostics,
        "computedAt": computed_at,
    }


def _sections_present(project_dir: Path) -> dict[str, bool]:
    return {key: bool(_section_members(project_dir, key)) for key, *_ in _SECTIONS}


def _lifecycle_stage(project_dir: Path, present: dict[str, bool], pending: int) -> str:
    """Heuristic stage (design §4.1 R4 — explicit + cheap, not over-modeled).

    CREATE     — skeleton only, no sedimented knowledge yet.
    GROW       — has ② knowledge entries (the default steady state).
    REVIEW     — has pending proposals awaiting a human decision.
    DISTRIBUTE — has a distribute output under .artifacts/.
    A brain with 0 governed assets is COMPLETE, not stuck (R31) — so we never
    emit a 'blocked' stage; the four are progressive-but-non-terminal.

    ORDER — REVIEW dominates DISTRIBUTE (not step-index order). A brain that has
    already distributed AND has accrued new pending proposals must surface REVIEW,
    not DISTRIBUTE: a pending human decision is the more actionable signal, and the
    frontend renders lifecycleStage as a linear stepper (DISTRIBUTE = terminal),
    so DISTRIBUTE-first would light the bar fully green and HIDE the un-reviewed
    work. `health.pending` still carries the count independently.
    """
    if pending > 0:
        return "REVIEW"
    if _has_distribute_output(project_dir):
        return "DISTRIBUTE"
    if _entry_count(project_dir) > 0:
        return "GROW"
    return "CREATE"


def _distribute_output_dir(project_dir: Path) -> Optional[Path]:
    """The distribute-output dir under .artifacts/ if one exists, else None.

    Single source for BOTH the lifecycle-stage bool (via _has_distribute_output)
    and the Run-3 distribution projection (which needs the PATH + mtime). Returns
    the FIRST matching well-known output dir."""
    art = project_dir / ".artifacts"
    if not art.is_dir():
        return None
    for pat in ("dist", "distribute", "package", "packages"):
        cand = art / pat
        if cand.exists():
            return cand
    return None


def _has_distribute_output(project_dir: Path) -> bool:
    return _distribute_output_dir(project_dir) is not None


def _read_kind(project_dir: Path) -> str:
    """The asset kind, read from aim.json if present (asset-neutral — R31)."""
    aim = project_dir / "aim.json"
    if aim.exists():
        try:
            import json

            data = json.loads(aim.read_text(encoding="utf-8"))
            kind = data.get("kind") or data.get("asset_kind")
            if isinstance(kind, str) and kind:
                return kind
        except (OSError, ValueError):
            pass
    return "knowledge"


def _brain_summary(project_dir: Path) -> dict:
    name = project_dir.name
    present = _sections_present(project_dir)
    pending = _pending_count(name)
    return {
        "name": name,
        "kind": _read_kind(project_dir),
        "sectionsPresent": present,
        "lifecycleStage": _lifecycle_stage(project_dir, present, pending),
        "health": {
            "sinking": _sinking_count(project_dir),
            "pending": pending,
            "uncommitted": _git_status_dirty(project_dir),
            "lastChangeRelative": _relative_time(_git_last_commit_iso(project_dir)),
        },
    }


def _brain_detail(project_dir: Path) -> dict:
    name = project_dir.name
    sections = []
    for key, num, label, own_govern, curator in _SECTIONS:
        member_rels = _section_members(project_dir, key)
        members = [
            {"path": rel, "gitStatus": _file_git_status(project_dir, rel)}
            for rel in member_rels
        ]
        section = {
            "key": key,
            "num": num,
            "label": label,
            "ownGovern": own_govern,
            "curator": curator,
            "members": members,
            "entries": [],
            # R31: an empty section (esp. ③Gates) is COMPLETE, not broken.
            "completeNotBroken": len(members) == 0,
        }
        if key == "knowledge":
            section["entries"] = _knowledge_entries(project_dir)
        sections.append(section)

    return {
        "name": name,
        "kind": _read_kind(project_dir),
        "sections": sections,
        # specs = spec-details/*.spec.md filenames (a DERIVED PROJECTION, NOT a
        # _SECTIONS entry — six-section invariant untouched, R31). A sibling
        # informational field so a DDD owner can find the domain's specs. Cheap
        # glob (~N filenames) — safe HERE in _brain_detail (per-brain, on open);
        # NB: must NOT be added to _brain_summary (would N-glob the gallery).
        "specs": _spec_files(project_dir),
        # hasCodeIntel = a live PRESENCE check of the on-disk code_intel.db, NOT
        # gated on `kind` (all DDDs resolve to kind='knowledge' — aim.json carries
        # brain_kind, never kind/asset_kind, so a kind gate never fires). One stat;
        # per-brain-on-open (like specs). NB: must NOT be added to _brain_summary
        # (would N-stat the gallery). A 0-symbol/stale db still reports true — the
        # CodeIntel panel renders a graceful "No code intelligence indexed" copy.
        "hasCodeIntel": (project_dir / "code_intel.db").exists(),
        # health = admission-passing DDD metrics (design 2026-08-04). Per-open ONLY
        # (like specs/hasCodeIntel) — NEVER added to _brain_summary (N-globs gallery).
        # Read-side: noise computed live, trust READ from stored score (no GET write).
        "health": _brain_health(project_dir),
    }


def _spec_files(project_dir: Path) -> list[str]:
    """spec-details/*.spec.md filenames for a brain (sorted); [] when absent.

    Containment: project_dir is already resolved by _resolve_brain_dir
    (parent==root guard), and .glob is scoped to the SPEC_DETAILS_DIR subdir —
    no traversal. Filenames only (owner opens content via the existing file
    preview), mirroring how section members[] are surfaced.
    """
    d = project_dir / SPEC_DETAILS_DIR
    if not d.is_dir():
        return []
    # is_file() (not is_symlink()) — surface only regular files: a subdir or a
    # symlink named *.spec.md must not appear as a spec (a dir would feed the file
    # preview a directory path; a symlink could resolve outside spec-details).
    return sorted(p.name for p in d.glob("*.spec.md") if p.is_file() and not p.is_symlink())


def _knowledge_entries(project_dir: Path) -> list[dict]:
    """Per-entry decay/type state for the ② canonical docs (live parse)."""
    out: list[dict] = []
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        rel = _rel(project_dir, p)
        try:
            entries = parse_entries(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        for e in entries:
            out.append({
                "title": e.title,
                "entryType": e.entry_type,
                "decayState": e.decay_state,
                "section": e.section,
                "source": e.source,
                "file": rel,
            })
    return out


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/brains")
async def list_brains() -> dict:
    """Gallery: one live summary per DDD project (read-only)."""
    dirs = await asyncio.to_thread(_list_project_dirs)
    # return_exceptions=True: one malformed project (e.g. a git/FS transient)
    # degrades to a dropped card, never a 500 on the whole gallery. The per-file
    # parse helpers already swallow UnicodeError/OSError; this is the outer
    # belt-and-suspenders so an unforeseen raise in ONE summary can't take the
    # list down (the resilient-lens contract).
    results = await asyncio.gather(
        *(asyncio.to_thread(_brain_summary, d) for d in dirs),
        return_exceptions=True,
    )
    brains = []
    for d, r in zip(dirs, results):
        if isinstance(r, Exception):
            logger.warning("brain summary failed for %s: %s", d.name, r)
            continue
        brains.append(r)
    return {"brains": brains}


@router.get("/brains/{name}")
async def get_brain(name: str) -> dict:
    """Brain view: six-section breakdown + ② per-entry decay/type state.

    Resolves via _resolve_brain_dir for the SAME containment (parent==root +
    resolved-symlink) guard the review/distribution endpoints use — get_brain is
    read-only, but keeping all brain endpoints on one resolver removes the
    "why is this one different" seam (no bare _projects_root()/name join).
    """
    def _detail_or_none():
        project_dir = _resolve_brain_dir(name)   # containment-checked (parent==root, resolved)
        if project_dir is None:
            return None
        return _brain_detail(project_dir)

    detail = await asyncio.to_thread(_detail_or_none)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return detail


# ─── Review tab (Run 2) ──────────────────────────────────────────────────────
#
# The Review tab makes "how do I review DDD knowledge / how does it decay"
# operable, on a GIT substrate: DDD docs live in the SwarmWS git tree and
# cultivation auto-commits, so a scoped `git diff <last-reviewed-sha>..HEAD --
# Projects/<ddd>/` IS the review queue. Three concepts:
#   - watermark   — a per-DDD last-reviewed commit SHA, stored as ONE plain file
#                   under Projects/<ddd>/.artifacts/.last-reviewed-sha (atomic
#                   write; auto_commit_hook's `git add -A` sweeps it — race-safe
#                   because every read sees a COMPLETE sha). Default when absent =
#                   the PARENT of the last commit touching the subtree (design
#                   §4.3 "prior auto-commit").
#   - hunks       — the scoped diff parsed at git's own hunk boundaries; each is
#                   identified by a CONTENT SIGNATURE (file + old-side line-span +
#                   +/- lines) — NOT a position index, so a stale index from a
#                   concurrent auto-commit can NEVER silently revert the wrong
#                   hunk (Gate-1 point #2), AND two identical text changes at
#                   different locations get DISTINCT signatures (REVIEW CRITICAL).
#   - reject      — reverse-apply ONLY that hunk via `git apply -R`, subtree-
#                   scoped with --include (REVIEW HIGH hardening). NEVER
#                   `git checkout <file>` — that nukes unshipped edits (GUI83/
#                   GUI127). No stored metric (R30#4).

_WATERMARK_REL = ".artifacts/.last-reviewed-sha"


def _rev_parse_head(ws: Path) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _default_watermark(ws: Path, rel: str) -> Optional[str]:
    """First-run watermark (no stored file yet): the PARENT of the most recent
    commit touching the subtree — so the Review tab opens showing the latest
    unreviewed change (design §4.3 "prior auto-commit"), never floods with full
    history, and never shows an empty diff when there IS a recent change. If the
    last subtree commit has no parent (the DDD's birth commit), fall back to that
    commit itself → an empty diff (nothing before birth to review)."""
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    last = r.stdout.strip()
    if not last:
        return None
    try:
        pr = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{last}^"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return last
    parent = pr.stdout.strip()
    return parent or last


def _read_watermark(project_dir: Path) -> Optional[str]:
    """Read the stored last-reviewed SHA, or None if never reviewed."""
    wm = project_dir / _WATERMARK_REL
    try:
        txt = wm.read_text().strip()
    except (OSError, FileNotFoundError):
        return None
    return txt or None


def _write_watermark(project_dir: Path, sha: str) -> None:
    """Atomically write the watermark (tmp + os.replace) — always a complete SHA
    even if auto_commit_hook's `git add -A` races the write (R3/R29). The tmp is
    created IN .artifacts/ (same dir as the target) so os.replace is a same-fs
    atomic rename; on success tmp is renamed away, so the finally-unlink is a
    no-op guarded by exists()."""
    art = project_dir / ".artifacts"
    art.mkdir(parents=True, exist_ok=True)
    wm = project_dir / _WATERMARK_REL
    fd, tmp = tempfile.mkstemp(dir=str(art), prefix=".wm-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(sha + "\n")
        os.replace(tmp, wm)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _hunk_signature(file_rel: str, hunk_header: str, hunk_body: str) -> str:
    """Stable signature for a hunk — file + the @@ header's OLD-side span +
    the added/removed lines. Rationale (REVIEW CRITICAL fix): the old-side
    line-span (`-A,B`) disambiguates two hunks that make the SAME textual change
    at DIFFERENT locations in one file (e.g. `line5→X` and `line35→X`) — without
    it both collide on identical +/- lines and reject would revert the first
    match, not the one the user chose (AC3 violation). The old-side span is
    stable at reject-time because the diff is recomputed against the SAME
    watermark base (Gate-1 point #2: content-identity, not a client position
    index). We use ONLY the old-side (`-A,B`) — the new-side (`+C,D`) shifts as
    earlier hunks are reverted within a session, the old-side does not."""
    old_span = ""
    # hunk_header looks like: @@ -A,B +C,D @@ optional-section-heading
    for p in hunk_header.split():
        if p.startswith("-"):
            old_span = p  # the -A,B token
            break
    payload_lines = [
        ln for ln in hunk_body.splitlines()
        if ln[:1] in ("+", "-") and not ln.startswith(("+++", "---"))
    ]
    payload = file_rel + "\n" + old_span + "\n" + "\n".join(payload_lines)
    # Non-cryptographic content-signature (hunk identity/dedup watermark), NOT a
    # security digest — usedforsecurity=False (bandit B324, no collision-attack surface).
    return hashlib.sha1(
        payload.encode("utf-8", "replace"), usedforsecurity=False
    ).hexdigest()[:16]


def _parse_hunks(diff_text: str) -> list[dict]:
    """Parse `git diff` output into per-file hunks (git's own @@ boundaries).

    Returns [{file, header, diff_text, signature}] — diff_text is a
    self-contained single-file, single-hunk patch (file header + one @@ block)
    that `git apply -R` can consume standalone.
    """
    hunks: list[dict] = []
    cur_file: Optional[str] = None
    file_header: list[str] = []
    cur_hunk: Optional[list[str]] = None

    def _flush():
        if cur_file and cur_hunk:
            header_line = cur_hunk[0]  # the @@ … @@ line
            body = "\n".join(cur_hunk)
            patch = "\n".join(file_header + cur_hunk) + "\n"
            hunks.append({
                "file": cur_file,
                "diff_text": patch,
                "signature": _hunk_signature(cur_file, header_line, body),
            })

    for line in diff_text.splitlines():
        if line.startswith("diff --git"):
            _flush()
            cur_hunk = None
            file_header = [line]
            cur_file = None
        elif line.startswith("+++ b/"):
            cur_file = line[len("+++ b/"):]
            file_header.append(line)
        elif line.startswith(("--- ", "index ", "new file", "deleted file",
                              "old mode", "new mode", "similarity", "rename")):
            file_header.append(line)
        elif line.startswith("@@"):
            _flush()
            cur_hunk = [line]
        elif cur_hunk is not None:
            cur_hunk.append(line)
    _flush()
    return hunks


def _tag_hunk(file_rel: str, proposal_files: set[str]) -> str:
    """Provenance tag for a hunk. Phase-1: everything in the git diff is already
    COMMITTED sediment (cultivation auto-commits), so the default is
    `cultivation·auto-applied`. A hunk touching a file that also has a pending
    proposal is surfaced as `risky·staged`. (`decay·sinking` is engine-driven and
    not derivable from the git diff alone — deferred, never fabricated: R30#4.)"""
    base = file_rel.rsplit("/", 1)[-1]
    if file_rel in proposal_files or base in proposal_files:
        return "risky·staged"
    return "cultivation·auto-applied"


def _sha_exists(ws: Path, sha: str) -> bool:
    """True if <sha> is a resolvable commit object in this repo."""
    if not sha:
        return False
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _resolve_watermark(project_dir: Path, ws: Path, rel: str, fallback: str) -> str:
    """The base SHA to diff against, with STALE-watermark protection (Gate-2
    correctness): a stored watermark whose commit was rebased/gc'd away would make
    `git diff <stale>..HEAD` fail → `_scoped_diff_hunks` return [] → the user sees
    'nothing to review' when there IS unreviewed work (a silent review-bypass).
    So a stored watermark is used ONLY if its commit still exists; otherwise we
    fall back to the default (parent-of-last-commit), never to a dead sha."""
    stored = _read_watermark(project_dir)
    if stored and _sha_exists(ws, stored):
        return stored
    return _default_watermark(ws, rel) or fallback


class DiffIncompleteError(Exception):
    """The scoped git-diff could not complete (timeout) — the hunk list would be
    silently EMPTY, indistinguishable from a genuinely clean subtree. Raised so
    callers can distinguish 'nothing to review' from 'diff timed out' and refuse
    to advance the review watermark over an unknown queue (F8). Never swallowed
    into an empty list on the review path."""


def _scoped_diff_hunks(project_dir: Path, base_sha: str) -> list[dict]:
    """Live scoped diff base..HEAD for this DDD subtree, parsed into tagged hunks.

    Raises DiffIncompleteError on a git timeout (the ONE case where an empty
    result would be a false 'nothing to review' — F8). A genuinely clean diff
    still returns []. Both callers (get_review / reject_review) catch it.
    """
    ws = _workspace_root()
    if not (ws / ".git").exists():
        return []
    try:
        rel = project_dir.relative_to(ws).as_posix()
    except ValueError:
        rel = project_dir.as_posix()
    try:
        # Exclude .artifacts/ — pipeline bookkeeping (the watermark file, run
        # state, proposals) is NOT reviewable DDD knowledge; including it would
        # surface the watermark's own commits as review hunks (self-reference).
        r = subprocess.run(
            ["git", "diff", f"{base_sha}..HEAD", "--unified=3", "--",
             rel, f":(exclude){rel}/.artifacts/**"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except subprocess.TimeoutExpired as e:
        # LOUD on degradation (F8): a silent [] here reads as "nothing to review"
        # and would let the reviewer advance the watermark over unseen work.
        logger.warning(
            "review diff timed out for %s (base=%s) — surfacing diff_incomplete",
            project_dir.name, base_sha[:8] if base_sha else "?",
        )
        raise DiffIncompleteError from e
    except OSError:
        return []
    if r.returncode != 0:
        return []
    hunks = _parse_hunks(r.stdout)
    proposal_files: set[str] = set()
    try:
        from core.ddd_cultivation import read_pending_proposals
        for p in read_pending_proposals(ws, project_dir.name):
            td = getattr(p, "target_doc", None)
            if td:
                proposal_files.add(td)
    except Exception:  # pragma: no cover - proposals are best-effort provenance
        pass
    for h in hunks:
        h["tag"] = _tag_hunk(h["file"], proposal_files)
    return hunks


def _pending_proposals_payload(project_dir: Path) -> list[dict]:
    ws = _workspace_root()
    try:
        from core.ddd_cultivation import read_pending_proposals
        out = []
        for p in read_pending_proposals(ws, project_dir.name):
            out.append({
                "id": getattr(p, "id", ""),
                "target_doc": getattr(p, "target_doc", ""),
                "target_section": getattr(p, "target_section", ""),
                "content": (getattr(p, "content", "") or "")[:400],
                "confidence": getattr(p, "confidence", None),
                "source_run_id": getattr(p, "source_run_id", ""),
            })
        return out
    except Exception:  # pragma: no cover
        return []


class RejectHunkBody(BaseModel):
    file: str
    hunk_signature: str


@router.get("/brains/{name}/review")
async def get_review(name: str) -> dict:
    """Review queue: tagged git-diff hunks since the watermark + pending proposals."""

    def _work() -> Optional[dict]:
        project_dir = _resolve_brain_dir(name)   # containment-checked (Gate-2)
        if project_dir is None:
            return None
        ws = _workspace_root()
        head = _rev_parse_head(ws) or ""
        try:
            rel = project_dir.relative_to(ws).as_posix()
        except ValueError:
            rel = project_dir.as_posix()
        wm = _resolve_watermark(project_dir, ws, rel, head)   # stale-safe (Gate-2)
        diff_incomplete = False
        try:
            hunks = _scoped_diff_hunks(project_dir, wm) if wm else []
        except DiffIncompleteError:
            # F8: the diff timed out — return an EMPTY hunk list but FLAG it, so the
            # frontend disables "Mark all seen" instead of advancing the watermark
            # over an empty-because-timed-out queue (silent review-bypass).
            hunks = []
            diff_incomplete = True
        return {
            "last_reviewed_sha": wm,
            "head_sha": head,
            "hunks": hunks,
            "proposals": _pending_proposals_payload(project_dir),
            "diff_incomplete": diff_incomplete,
        }

    result = await asyncio.to_thread(_work)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return result


@router.post("/brains/{name}/review/approve")
async def approve_review(name: str) -> dict:
    """Advance the last-reviewed watermark to HEAD (mark-all-seen)."""

    def _work() -> Optional[str]:
        project_dir = _resolve_brain_dir(name)   # containment-checked (Gate-2)
        if project_dir is None:
            return None
        head = _rev_parse_head(_workspace_root())
        if not head:
            raise HTTPException(status_code=500, detail="Cannot resolve HEAD")
        _write_watermark(project_dir, head)
        return head

    head = await asyncio.to_thread(_work)
    if head is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return {"last_reviewed_sha": head}


@router.post("/brains/{name}/review/reject")
async def reject_review(name: str, body: RejectHunkBody) -> dict:
    """Reverse-apply ONE hunk (identified by content signature) via git apply -R.

    NEVER `git checkout <file>` (GUI83/GUI127 — that would clobber unshipped
    edits). The hunk is re-located in the CURRENT diff by its content signature,
    so a stale client index can't revert the wrong hunk; if no current hunk
    matches → 404 (fail-loud, revert nothing).
    """

    def _work() -> dict:
        project_dir = _resolve_brain_dir(name)   # containment-checked (Gate-2)
        if project_dir is None:
            raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
        ws = _workspace_root()
        try:
            rel = project_dir.relative_to(ws).as_posix()
        except ValueError:
            rel = project_dir.as_posix()
        wm = _resolve_watermark(project_dir, ws, rel, _rev_parse_head(ws) or "")   # stale-safe
        try:
            hunks = _scoped_diff_hunks(project_dir, wm) if wm else []
        except DiffIncompleteError:
            # F8: diff timed out — we CANNOT locate the target hunk, so reverting
            # would be blind. Fail loud (409, retry) rather than 404 "no match"
            # (which would falsely imply the hunk is gone) or a blind revert.
            raise HTTPException(
                status_code=409,
                detail="Review diff timed out — cannot locate the hunk to revert; retry.",
            )
        target = next(
            (h for h in hunks if h["signature"] == body.hunk_signature), None
        )
        if target is None:
            # Fail-loud: signature matches no current hunk → revert NOTHING.
            raise HTTPException(
                status_code=404,
                detail=f"No current hunk matches signature {body.hunk_signature}",
            )
        # Reverse-apply ONLY this hunk against the working tree.
        # Hardening (REVIEW HIGH): --include=<rel>/* constrains git apply to this
        # DDD's subtree — even a crafted patch header naming a path outside
        # Projects/<ddd>/ can't touch it. Combined with git's own path
        # normalization (diff output can't carry `..`), reject can only ever
        # modify files inside the project it's scoped to.
        #
        # META-REVIEW HIGH (half-applied-under-race): git apply modifies the working
        # tree DURING the attempt, so a hunk that fails partway (offset drift from a
        # concurrent auto_commit_hook edit) would leave the file half-reverted. Guard
        # with `--check` FIRST: it validates the patch applies cleanly WITHOUT
        # touching the tree. Only if the check passes do we run the live apply. This
        # closes the "409 but tree already half-modified" window — a failed reject now
        # leaves the tree UNTOUCHED, never half-reverted.
        _apply_base = ["git", "apply", "-R", "--recount", f"--include={rel}/*"]
        try:
            check = subprocess.run(
                _apply_base + ["--check", "-"],
                input=target["diff_text"], cwd=str(ws),
                capture_output=True, text=True, timeout=5,
            )
            if check.returncode != 0:
                # Does NOT apply cleanly (likely a concurrent edit shifted context).
                # Tree is UNTOUCHED. Fail loud, revert nothing.
                raise HTTPException(
                    status_code=409,
                    detail=f"Hunk no longer applies cleanly: {check.stderr.strip()}",
                )
            proc = subprocess.run(
                _apply_base + ["-"],
                input=target["diff_text"], cwd=str(ws),
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HTTPException(status_code=500, detail=f"git apply failed: {e}")
        if proc.returncode != 0:
            raise HTTPException(
                status_code=409,
                detail=f"Hunk no longer applies cleanly: {proc.stderr.strip()}",
            )
        return {"reverted": True, "file": target["file"], "signature": body.hunk_signature}

    return await asyncio.to_thread(_work)


# ─── Distribute tab (Run 3) ──────────────────────────────────────────────────
#
# A READ-ONLY projection of each DDD's distribution state. The data model is the
# DDD's DECLARED REACH — NOT a "target host": aim.json carries a `distribution`
# block {targets ⊆ [aim-capabilities, open-plugin], visibility} read via the
# policy validator (ddd_distribution_policy.validate_distribution_file — the SSOT;
# we NEVER hand-parse it). A DDD with no block is fail-closed "not distributable"
# (the honest phase-1 state for every current DDD — never fabricate targets).
#
# The [Distribute a brain] button does NOT run distribution server-side:
# s_ddd-distribute is human-in-the-loop by design (confirms targets + fail-closed
# content-safety scan + emit≠publish). The frontend surfaces the exact chat
# command instead — the HITL skill stays the only emit path (Gate-1/THINK).
# No stored metric — all live-computed (R30#4).


def _last_content_commit_iso(project_dir: Path) -> Optional[str]:
    """ISO time of the last commit touching this DDD's KNOWLEDGE content —
    the subtree EXCLUDING .artifacts/ (pipeline bookkeeping, incl. the distribute
    output itself). Mirrors the Run-2 review-diff .artifacts exclusion."""
    ws = _workspace_root()
    if not (ws / ".git").exists():
        return None
    try:
        rel = project_dir.relative_to(ws).as_posix()
    except ValueError:
        rel = project_dir.as_posix()
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--",
             rel, f":(exclude){rel}/.artifacts/**"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _output_is_committed(project_dir: Path, out_dir: Path) -> bool:
    """True if the distribute-output dir has at least one commit touching it in
    this tree — i.e. its mtime is a trustworthy freshness anchor (F2). An
    uncommitted output (freshly emitted, or a just-checked-out copy) has no
    reliable commit time, so freshness is UNKNOWN. `git log -1 -- <output path>`
    returning a commit == committed."""
    ws = _workspace_root()
    if not (ws / ".git").exists():
        return False
    try:
        rel = out_dir.relative_to(ws).as_posix()
    except ValueError:
        return False
    try:
        r = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _distribution_state(project_dir: Path) -> dict:
    """Live distribution projection for one DDD (declared reach + output state)."""
    from core.ddd_distribution_policy import validate_distribution_file

    pol = validate_distribution_file(project_dir / "aim.json")
    out_dir = _distribute_output_dir(project_dir)
    output_path: Optional[str] = None
    last_distribute_time: Optional[str] = None
    # TRISTATE (F2): None = freshness UNKNOWN. The freshness signal anchors on the
    # output dir's filesystem mtime, which git does NOT preserve — after a clone /
    # checkout / worktree switch every mtime is reset to checkout-time, so an
    # mtime-based boolean would read "up to date" (or "changed") at random for a
    # genuinely-stale output. We can only trust the mtime when the output dir is
    # actually git-committed in THIS tree (so its content is anchored, not a
    # just-checked-out copy). Uncommitted output → we don't know → None, and the
    # frontend shows "freshness unknown" rather than a confident-but-wrong boolean.
    source_changed_since: Optional[bool] = None
    if out_dir is not None:
        output_path = out_dir.name  # the .artifacts/<name> stem (never an abs host path)
        try:
            out_mtime = out_dir.stat().st_mtime
            last_distribute_time = datetime.fromtimestamp(
                out_mtime, tz=timezone.utc
            ).isoformat()
            if _output_is_committed(project_dir, out_dir):
                # source_changed_since: the subtree's last KNOWLEDGE commit is NEWER
                # than the output. "Source" = the DDD's reviewable content, EXCLUDING
                # .artifacts/ (the same exclusion the Run-2 review diff uses), so
                # committing the output itself never counts as a source change.
                # Compare epoch-to-epoch (parse the ISO commit time — never str-vs-float).
                iso = _last_content_commit_iso(project_dir)
                if iso:
                    commit_epoch = datetime.fromisoformat(iso).timestamp()
                    source_changed_since = commit_epoch > out_mtime
                else:
                    source_changed_since = False  # committed output, no content commits → up to date
            # else: uncommitted output → leave None (freshness unknown)
        except (OSError, ValueError):
            source_changed_since = None
    return {
        "declared_targets": list(pol.targets),
        "visibility": pol.visibility,
        "distributable": pol.is_distributable,
        "declared": pol.declared,
        "warnings": list(pol.warnings),
        "has_output": out_dir is not None,
        "output_path": output_path,
        "last_distribute_time": last_distribute_time,
        "source_changed_since": source_changed_since,
    }


@router.get("/brains/{name}/distribution")
async def get_distribution(name: str) -> dict:
    """Distribution state: declared reach (targets+visibility) + output state."""

    def _work() -> Optional[dict]:
        project_dir = _resolve_brain_dir(name)   # containment-checked (Run 2 guard)
        if project_dir is None:
            return None
        return _distribution_state(project_dir)

    result = await asyncio.to_thread(_work)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return result
