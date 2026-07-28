"""DDD Brain Hub — read-only projection router (GET /api/ddd/brains[/{name}]).

The backend half of the Brain Hub Phase-1 "lens" (design 2026-07-28). It is a
PURE READ projection over existing state — it introduces NO new source of truth
and NO stored metric (R30#4: every count is computed live per request). Two
endpoints:

  GET /api/ddd/brains          → Gallery: one summary card per DDD project.
  GET /api/ddd/brains/{name}   → Brain view: the six-section breakdown + the
                                 per-entry decay/type state of the ② docs.

Data sources (all existing, all read-only):
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
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from core.ddd_entry_lifecycle import parse_entries
from core.ddd_paths import IDENTITY_FILE, ddd_path, section_dir

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ddd-brain"])

# The 4 canonical ② docs whose entries carry decay/type state.
_KNOWLEDGE_DOCS = ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md")

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
    """
    if _has_distribute_output(project_dir):
        return "DISTRIBUTE"
    if pending > 0:
        return "REVIEW"
    if _entry_count(project_dir) > 0:
        return "GROW"
    return "CREATE"


def _has_distribute_output(project_dir: Path) -> bool:
    art = project_dir / ".artifacts"
    if not art.is_dir():
        return False
    for pat in ("dist", "distribute", "package", "packages"):
        if (art / pat).exists():
            return True
    return False


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
    }


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
    """Brain view: six-section breakdown + ② per-entry decay/type state."""
    project_dir = _projects_root() / name

    def _detail_or_none():
        if not (project_dir.is_dir() and (project_dir / ".project.json").exists()):
            return None
        return _brain_detail(project_dir)

    detail = await asyncio.to_thread(_detail_or_none)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return detail
