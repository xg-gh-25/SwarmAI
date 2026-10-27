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
from core import executors
import hashlib
import json
import logging
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.ddd_entry_lifecycle import (
    MEMORY_EVERGREEN_SECTIONS,
    VALID_TYPES,
    EntryMetadata,
    compute_reclaimable_noise,
    parse_entries,
)
from core.ddd_paths import IDENTITY_FILE, ddd_path, section_dir
from core.project_agent import PROJECT_AGENT_FILE, load_project_agent, project_agent_status
from core.ddd_setup_state import read_setup_progress, read_setup_state
from core.project_registry import DDD_CANONICAL_DOCS, get_pinned_projects

logger = logging.getLogger(__name__)


def _GIT_READ_ENV() -> dict:
    """Environment for this module's git calls: ``GIT_OPTIONAL_LOCKS=0`` so a
    read (status / diff / log) never refreshes the index, which would take
    .git/index.lock and collide with chat's per-turn snapshot (design
    left-nav-backend-and-visual-system.md B5)."""
    return {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}

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
        logger.debug("_workspace_root: suppressed exception on a fail-safe path", exc_info=True)
        from config import get_swarmws
        return get_swarmws()


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


# ─── Runs: last landing + in-progress, per project ───────────────────────────

def _run_facts(ws: Path) -> dict[str, dict]:
    """{project name: {"landed_at": ISO | None, "live": bool}} from the runs of the
    last 30 days (core.run_index.recent, a cached read; no git). Design
    left-nav-backend-and-visual-system.md B3: a project's changes and commits are
    what its pipeline runs landed, so Brain Hub reads them from the runs.

    landed: a legacy run with recorded ``commits`` (its ``completed_at``), or a v1 run
    whose ``landing`` is landed / human_override (its ``updated_at``). live: any run
    whose status is in ``run_index.LIVE_STATUSES``. Blocking: call it off the loop."""
    from core import run_index
    out: dict[str, dict] = {}
    for r in run_index.recent(ws):
        name = r.path.parents[3].name
        facts = out.setdefault(name, {"landed_at": None, "live": False})
        data = r.data
        if data.get("status") in run_index.LIVE_STATUSES:
            facts["live"] = True
        if data.get("commits"):
            at = data.get("completed_at") or data.get("updated_at")
        elif data.get("landing") in ("landed", "human_override"):
            at = data.get("updated_at")
        else:
            at = None
        if isinstance(at, str) and at and (facts["landed_at"] is None or at > facts["landed_at"]):
            facts["landed_at"] = at
    return out


# ─── Git: per-file marks of the brain detail view (read-only, no index lock) ──

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
            env=_GIT_READ_ENV(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "clean"
    if result.returncode != 0 or not result.stdout.strip("\0").strip():
        return "clean"
    xy = result.stdout.split("\0")[0][:2]
    return _map_git_xy(xy)


def _map_git_xy(xy: str) -> str:
    """Map a porcelain XY code to our status vocabulary (SSOT for _file_git_status
    + _batch_git_status — one mapping, no drift)."""
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


def _batch_git_status(project_dir: Path) -> dict[str, str]:
    """ONE `git status --porcelain -z` over the project subtree → {ws-rel path →
    status} (run_43dc94f6 perf). Replaces N per-file forks in the detail view.

    Keyed by WORKSPACE-relative posix path (same string _file_git_status builds at
    line ~205: project_dir.relative_to(ws)/file_rel), so a member lookup is
    `.get(ws_rel, "clean")` — a file NOT in porcelain (unchanged, or gitignored per
    STEERING #5) is absent → caller defaults "clean", identical to the per-file
    "empty stdout → clean". Scoped `-- <project_rel>` so git scans only this subtree
    (not the whole repo → no latency regression). Fail-soft: .git-absent / non-zero /
    timeout / OSError → {} so every lookup falls back to "clean" (parity with the
    per-file except path). Rename-aware: porcelain -z emits `R<sp> new\\0old\\0` as
    TWO null fields (VERIFIED against real git — the FIRST field is the NEW path, the
    second is OLD) — key the NEW path (the one on disk + in a member list), consume
    both fields (Gate-1 E)."""
    ws = _workspace_root()
    if not (ws / ".git").exists():  # worktree/submodule has .git as a FILE
        return {}
    try:
        proj_rel = project_dir.relative_to(ws).as_posix()
    except ValueError:
        return {}
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-unormal", "--", proj_rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            env=_GIT_READ_ENV(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    out: dict[str, str] = {}
    fields = result.stdout.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        if not rec.strip():
            i += 1
            continue
        xy = rec[:2]
        path = rec[3:]  # porcelain: "XY <path>", path starts at index 3
        if "R" in xy:
            # rename/copy: this field is the NEW path; the NEXT field is the OLD path
            # (porcelain -z order). Key the new path, consume both fields.
            out[path] = "renamed"
            i += 2
            continue
        out[path] = _map_git_xy(xy)
        i += 1
    return out


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

def _pending_by_source(project_name: str) -> dict[str, int]:
    """THIS brain's Need You items, counted per source (escalation | paused_run |
    cultivation | job).

    Asks the unified AttentionAuthority (per-brain query) — NOT just cultivation.
    Historically this only counted `read_pending_proposals` (cultivation), so a
    brain with an open L2 escalation or a paused-decision run still showed
    "✓ Nothing needs you" — a lying badge (design 2026-08-08 §1 root cause #1).
    The per-source split lets the Hub say WHAT waits (a Run, a rule proposal, an
    escalation) instead of calling every item a proposal. Governance (brain=None)
    is OS-level and excluded.
    """
    try:
        from core.attention_authority import collect

        out: dict[str, int] = {}
        for it in collect(_workspace_root(), brain=project_name).items:
            out[it.source] = out.get(it.source, 0) + 1
        return out
    except Exception as e:
        # Degrade-observable (GC19): still return {} so the gallery cannot 500
        # (list_brains needs no-500), but LOG the failure type — a silent
        # empty result renders a real authority failure as "nothing needs you".
        logger.warning("pending_count failed for %s, defaulting to 0: %s", project_name, e)
        return {}


def _pending_count(project_name: str) -> int:
    """Count of items in THIS brain's Need You queue (all sources)."""
    return sum(_pending_by_source(project_name).values())


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
        # ⑤ = bindings.yaml (what it governs + the repo delivery contract) and the
        # Project Agent definition next to it (how it delivers).
        out = []
        p = ddd_path(project_dir, "bindings.yaml")
        if p.exists():
            out.append(_rel(project_dir, p))
        if (project_dir / PROJECT_AGENT_FILE).is_file():
            out.append(PROJECT_AGENT_FILE)
        return out

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

# The 7 knowledge types — imported from the SSOT (ddd_entry_lifecycle.VALID_TYPES),
# NOT re-declared, so a future 8th type can't be silently under-counted here.
_TYPE_KEYS = VALID_TYPES


def _parse_knowledge_docs_grouped(project_dir: Path) -> dict[str, list[EntryMetadata]]:
    """Parse the ② canonical docs ONCE into {project-relative-path → entries}
    (run_43dc94f6 perf). Keyed by _rel(project_dir, ddd_path(doc)) — the SAME
    string _section_members yields for a knowledge member — so a detail caller can
    reuse it for ALL parse consumers (sinking/typeCounts, noise, sections[].entries,
    per-file entryCount) instead of reading+parsing the same 4 docs 4+ times.

    Stores ALL entries UNFILTERED (Gate-1 B): sinking (dormant/archived) is a
    per-entry recompute the callers do over these lists, never pre-filtered here.
    Missing/unreadable doc degrades to no key (matches the sibling helpers' skip),
    so a caller's `.get(key, [])` yields the same [] the self-parse path would.
    """
    grouped: dict[str, list[EntryMetadata]] = {}
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        try:
            grouped[_rel(project_dir, p)] = parse_entries(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
    return grouped


def _gallery_entry_stats(
    project_dir: Path, parsed: Optional[dict[str, list[EntryMetadata]]] = None
) -> dict:
    """ONE parse pass over the ② canonical docs → {sinking, typeCounts}.

    `parsed` (run_43dc94f6): a pre-parsed {rel→entries} dict from the detail path
    to REUSE instead of re-reading the docs. None (gallery + all existing callers)
    → self-parse, byte-identical to before. When provided, the loop reads entries
    from the dict but runs the SAME per-entry sinking/type tally, so output is
    identical either way.

    Both signals are derived from the SAME parse_entries walk that the gallery
    already pays for `sinking` — folding the per-entry_type tally into that loop
    adds a dict-increment per entry, NOT a second glob (so the compact card's
    3-layer ontology bar costs nothing beyond the sinking count that was always
    computed). typeCounts is keyed by the 7 VALID_TYPES; parse_entries clamps any
    unknown type to 'guideline', so an 8th key can never appear (run_9ada46ae).

    Corrects the old `_sinking_count` docstring that claimed the gallery does "no
    parse_entries" — it always parsed; this just also buckets the type.
    """
    sinking = 0
    type_counts = {k: 0 for k in _TYPE_KEYS}
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        try:
            if parsed is not None:
                entries = parsed.get(_rel(project_dir, p), [])
            else:
                entries = parse_entries(p.read_text(encoding="utf-8"))
            for e in entries:
                if e.decay_state in ("dormant", "archived"):
                    sinking += 1
                # entry_type is guaranteed ∈ VALID_TYPES (clamped in parse_entries);
                # .get guard is belt-and-suspenders against a future new type.
                if e.entry_type in type_counts:
                    type_counts[e.entry_type] += 1
        # UnicodeError (a ValueError) on a non-UTF-8 doc must NOT crash the
        # gallery — a single bad file degrades to 0, never a 500 (PIT44 class).
        except (OSError, ValueError, UnicodeError):
            continue
    return {"sinking": sinking, "typeCounts": type_counts}


def _sinking_count(project_dir: Path) -> int:
    """dormant + archived entries across the ② canonical docs (live).

    Thin wrapper over _gallery_entry_stats for callers that only need sinking.
    """
    return _gallery_entry_stats(project_dir)["sinking"]


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


def _file_mtime_relative(fp: Path) -> str:
    """Human 'N ago' of a file's FILESYSTEM mtime (NOT git-commit time).

    Filesystem stat is the correct source for the DDD-doc hero cards' "last
    updated": non-SwarmAI DDD projects are gitignored (STEERING #5), so a
    git-commit time would be empty/wrong for them, while st_mtime is always real.
    Computed live (never stored — R30#4). Feeds the existing _relative_time
    formatter via an ISO string. Missing/unstattable file → "unknown"."""
    try:
        ts = datetime.fromtimestamp(fp.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return "unknown"
    return _relative_time(ts)


def _parse_all_knowledge_entries(project_dir: Path) -> list[EntryMetadata]:
    """All parsed entries across the ② canonical docs (shared by health metrics).

    One parse pass per canonical doc — the same per-doc loop _sinking_count /
    _entry_count use, factored out so _brain_health reuses it. A non-UTF-8 or
    unreadable doc degrades to zero contribution (never a 500), matching the
    sibling helpers' fault tolerance.
    """
    entries: list[EntryMetadata] = []
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        try:
            entries.extend(parse_entries(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, UnicodeError):
            continue
    return entries


def _brain_health_base(project_dir: Path, present: dict, pending: int, *, sinking: int,
                       run_facts: Optional[dict] = None) -> dict:
    """The CHEAP health fields present at EVERY density (gallery + detail).

    `sinking` is passed in (computed once by build_brain_state via
    _gallery_entry_stats). `run_facts` is this project's entry of `_run_facts`: the
    gallery computes it once for every project; None (detail / single brain) reads
    it here (a cached run_index read, no git).

    - liveRun: the project has a pipeline run in progress.
    - lastChangeRelative: when its newest landed run landed, "--" when none landed
      in the last 30 days.
    """
    facts = run_facts if run_facts is not None else \
        _run_facts(_workspace_root()).get(project_dir.name, {})
    landed = facts.get("landed_at")
    return {
        "sinking": sinking,
        "pending": pending,
        "liveRun": bool(facts.get("live")),
        "lastChangeRelative": _relative_time(landed) if landed else "--",
    }


def build_brain_state(
    project_dir: Path, *, with_noise: bool, pending_override: Optional[dict[str, int]] = None,
    parsed: Optional[dict[str, list[EntryMetadata]]] = None,
    run_facts: Optional[dict] = None,
) -> dict:
    """THE single source of a brain's state (Cycle-1 unify — no fork).

    ONE builder feeds BOTH routes; the ONLY axis is `with_noise` (a.k.a. detail):
      - with_noise=False → gallery projection: identity + sectionsPresent +
        lifecycleStage + the CHEAP health base + typeCounts. The ONE cheap parse
        pass (_gallery_entry_stats) yields BOTH sinking and the 7-type histogram;
        it does NOT call compute_reclaimable_noise / section_health (the truly
        expensive detail-only block). (The gallery ALWAYS parsed for `sinking` —
        the old "no parse_entries" claim was wrong; typeCounts rides that pass.)
      - with_noise=True  → detail superset: the same base PLUS the admission-passing
        detail metrics (noise/trust/escalation/recall/cultivation/diagnostics).

    The health shape is a SUPERSET, not two different shapes: detail = base ∪ metrics.
    typeCounts is a top-level SIBLING of health (like lifecycleStage), NOT nested in
    health — so the gallery's exact 4-key health contract is preserved.

    Callers (_brain_summary, _brain_detail) delegate here — there is no second,
    independent health builder. `_brain_detail` layers sections/entries/specs on top.
    """
    name = project_dir.name
    present = _sections_present(project_dir)
    # pending_override lets the GALLERY (list_brains) aggregate the attention channel
    # ONCE via collect(ws) and partition per-brain, instead of paying a full-workspace
    # collect(brain=name) scan per project (measured 4.67s × N = the 7s stall). When
    # None (the detail/single-brain path), fall back to the per-brain _pending_count.
    by_source = pending_override if pending_override is not None else _pending_by_source(name)
    pending = sum(by_source.values())
    # `parsed` (run_43dc94f6): the detail path parses the ② docs once and passes them
    # here so stats + metrics reuse them (None → self-parse, gallery unchanged).
    stats = _gallery_entry_stats(project_dir, parsed=parsed)  # sinking + typeCounts
    health = _brain_health_base(project_dir, present, pending, sinking=stats["sinking"],
                                run_facts=run_facts)
    recent = _recent_activity(project_dir)
    if with_noise:
        # Thread the already-computed `pending` into the detail metrics so
        # escalationPending reuses it instead of a SECOND _pending_count() scan
        # (the per-open double-scan; _pending_count → collect() is the expensive leg).
        health.update(_brain_detail_metrics(project_dir, parsed=parsed, pending=pending,
                                            recent_activity=recent))
    # On the detail path `parsed` is in hand — reuse its entry count instead of
    # a third re-parse inside _lifecycle_stage (_entry_count re-reads the same ②
    # docs). sum-over-parsed == _entry_count(project_dir): both iterate _KNOWLEDGE_DOCS
    # with the same parse_entries + same fault-skip, unfiltered. None (gallery) →
    # _lifecycle_stage self-parses, unchanged.
    entry_count = sum(len(v) for v in parsed.values()) if parsed is not None else None
    return {
        "name": name,
        "kind": _read_kind(project_dir),
        "description": _read_description(project_dir),
        "sectionsPresent": present,
        "lifecycleStage": _lifecycle_stage(project_dir, present, pending, entry_count=entry_count),
        # setting-up | ready | None (no record: a Brain made before setup tracking)
        "setupState": read_setup_state(project_dir),
        # {done, total} checks from the last P6 --record while setting up, else None
        "setupProgress": read_setup_progress(project_dir),
        # draft (waits for the person) | reviewed | invalid | None (no project-agent.yaml)
        "projectAgentStatus": project_agent_status(project_dir),
        "health": health,
        "typeCounts": stats["typeCounts"],
        # health.pending split by source: escalation | paused_run | cultivation | job.
        # A sibling of health (the gallery's 4-key health contract holds).
        "pendingBySource": by_source,
        # ddd-changelog entries stamped in the last 30 days. A sibling of health (like
        # typeCounts) so the gallery's 4-key health contract holds; detail health
        # carries the same number as health.recentActivity (one computation).
        "recentActivity": recent,
    }


def _recent_activity(project_dir: Path) -> int:
    """recentActivity — Q3 "is it growing?" (value≠size). Count of ddd-changelog
    entries STAMPED within the last 30d. This is a SEDIMENTATION signal (is the
    brain being actively written to?), NOT a size/entry_count (Principle-1: a
    bigger brain is not a better one; an ACTIVELY-CULTIVATED one is). NOTE: this
    is DOMINANTLY auto-applied cultivation, not human edits (E2E audit run_d1e933aa:
    ~820/851 on SwarmAI carry action:"applied") — so the UI labels it "sediments",
    never "edits", to avoid overstating human authorship. Absent changelog →
    honest 0, never fabricated, never None.

    Deliberately NOT via _build_changelog_index: that helper counts UNDATED
    entries as in-window (ts is None → counted), which would let timestamp-less
    legacy rows inflate a "last 30d" number. A "30d activity" figure must count ONLY
    entries whose real timestamp is within 30d; undated rows are excluded.

    One read of one jsonl per Brain, so the gallery can carry it too (Hub LEARNED).
    """
    count = 0
    try:
        cl = project_dir / ".artifacts" / "ddd-changelog.jsonl"
        if cl.is_file():
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            for line in cl.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    ts = datetime.fromisoformat(json.loads(line).get("timestamp", ""))
                except (ValueError, TypeError, AttributeError, json.JSONDecodeError):
                    continue  # undated / malformed → NOT counted as recent
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    count += 1
    except (OSError, ValueError):  # never 500 a brain view
        return 0
    return count


def _brain_detail_metrics(
    project_dir: Path, parsed: Optional[dict[str, list[EntryMetadata]]] = None,
    pending: Optional[int] = None, recent_activity: Optional[int] = None,
) -> dict:
    """The DETAIL-only, admission-passing DDD health metrics (per-open).

    Design: Knowledge/Designs/2026-08-04-ddd-health-metrics-brainhub-component.md.
    Each metric earns its place by the §1 admission gate (owner action + live/read,
    never a frozen verdict). Layered on top of the cheap base ONLY when
    build_brain_state(with_noise=True) — NEVER on the gallery projection
    (with_noise=False), because compute_reclaimable_noise parse_entries would N-glob
    the gallery. This is the sole expensive block; the base is always cheap.

    - noise: {reclaimable, rate} — computed LIVE via compute_reclaimable_noise over
      the ② docs (owner action: >threshold → reclaim-strip). No side effect.
    - trust / diagnostics / computedAt: READ from the stored section_health.json in a
      SINGLE parse (written by the scheduled health path). The GET path NEVER calls
      compute_section_health — that function WRITES the file, and a disk write in a
      read handler is forbidden (Gate-1 CRITICAL, run_d7146171). All three derive from
      one snapshot so trust/diagnostics can't go stale relative to computedAt (Gate-2
      HIGH: a two-read version risked a TOCTOU mismatch). Absent/corrupt score → None
      (honest: no scheduled computation yet; never fabricated, never a write). No
      project-composite is invented (Gate-1 MAJOR — vanity); per-doc scores verbatim.
    - escalationPending: the caller's already-computed `pending` (same source as the
      gallery — no divergence, and no SECOND _pending_count scan per open). Falls back
      to _pending_count(name) only if the caller passes None (defensive / direct call).
    - recall: {value:None, experimental:True} — recall_suite is a pinned-corpus
      benchmark with no cheap per-DDD value; the tile is shown but labeled experimental
      (design §4), never fabricated.
    """
    # noise — live, no side effect. Narrow except (Gate-2 MED, GUI19): catch the
    # real failure modes (bad file / bad data shape) so an accidental future write
    # or a genuine logic bug surfaces instead of being swallowed to a silent zero.
    try:
        # noise is order-insensitive + across-all-docs (Gate-1 B), so a flatten of
        # the shared `parsed` groups is identical to _parse_all_knowledge_entries.
        if parsed is not None:
            entries = [e for group in parsed.values() for e in group]
        else:
            entries = _parse_all_knowledge_entries(project_dir)
        nr = compute_reclaimable_noise(
            entries, date.today(), evergreen_sections=MEMORY_EVERGREEN_SECTIONS
        )
        noise = {"reclaimable": nr.noisy, "rate": round(nr.noise_rate, 4)}
    except (OSError, ValueError, UnicodeError):  # noise never 500s a brain view
        noise = {"reclaimable": 0, "rate": 0.0}

    # trust / diagnostics / computedAt — READ-ONLY from the stored scheduled score.
    # ONE read of section_health.json (Gate-2 HIGH: reading it twice risked a TOCTOU
    # where trust came from the old file and computedAt from a mid-write new one; and
    # Gate-2 MED: it parsed the same file twice). We parse once here and derive all
    # three from that single snapshot. The GET path NEVER calls compute_section_health
    # (the writer) — only this read (Gate-1 CRITICAL). Absent/corrupt → all None.
    trust = None
    diagnostics = None
    computed_at = None
    sh = project_dir / ".artifacts" / "section_health.json"
    try:
        if sh.is_file():
            snapshot = json.loads(sh.read_text(encoding="utf-8"))
            docs = snapshot.get("docs", {})
            computed_at = snapshot.get("computed_at")
            if docs:
                # per-doc trust (as stored — no invented rollup, Gate-1 MAJOR)
                trust = {
                    doc: {
                        sec: s.get("trust")
                        for sec, s in doc_data.get("sections", {}).items()
                    }
                    for doc, doc_data in docs.items()
                }
                diagnostics = docs  # the 5-dim per-section scores, verbatim
    except (OSError, ValueError, KeyError):  # bad file / shape → honest None, no write
        trust = None
        diagnostics = None
        computed_at = None

    # cultivation — learning-fidelity health (run_abf49550 M0). READ-ONLY: aggregates
    # the durable per-project outcomes sink over a 7d window (no write in the GET path
    # — Gate-1 CRITICAL). silent_learning_failure is the north-star flag: True iff a
    # genuine write failure (locked/doc_missing) occurred in-window. Never fabricated —
    # absent sink → honest all-zero, unflagged.
    try:
        from core.ddd_cultivation import read_cultivation_health
        cultivation = read_cultivation_health(project_dir, window_days=7)
    except (OSError, ValueError, ImportError):  # never 500 a brain view on health read
        cultivation = None

    recent_activity = recent_activity if recent_activity is not None else _recent_activity(project_dir)

    return {
        "noise": noise,
        "trust": trust,
        "escalationPending": pending if pending is not None else _pending_count(project_dir.name),
        "recall": {"value": None, "experimental": True},
        "cultivation": cultivation,
        "recentActivity": recent_activity,
        "diagnostics": diagnostics,
        "computedAt": computed_at,
    }


def _sections_present(project_dir: Path) -> dict[str, bool]:
    return {key: bool(_section_members(project_dir, key)) for key, *_ in _SECTIONS}


def _lifecycle_stage(
    project_dir: Path, present: dict[str, bool], pending: int,
    entry_count: Optional[int] = None,
) -> str:
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

    `entry_count` (perf, run_9af622ee): the detail path already parsed the ② docs
    (build_brain_state's `parsed`) and passes their entry count here so this doesn't
    re-parse via _entry_count(). It is authoritative for the request — both counts
    derive from the same _KNOWLEDGE_DOCS parse under the same read, so they match; a
    TOCTOU divergence (a doc becoming unreadable mid-request) would only make the passed
    count the fresher of two equally-valid reads. None (gallery + direct callers) →
    self-parse via _entry_count, byte-identical to before.
    """
    if pending > 0:
        return "REVIEW"
    if _has_distribute_output(project_dir):
        return "DISTRIBUTE"
    # `entry_count` (detail path) is the count from the caller's single ②-doc parse —
    # reuse it instead of a redundant _entry_count() re-parse. It is authoritative for
    # this request (both derive from the same _KNOWLEDGE_DOCS parse under the same read).
    # None (gallery / the 4 direct unit tests) → self-parse via _entry_count, unchanged.
    count = entry_count if entry_count is not None else _entry_count(project_dir)
    if count > 0:
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


def _read_description(project_dir: Path) -> Optional[str]:
    """A one-line brain briefing, read from aim.json `description` if present
    (same aim.json file _read_kind reads — one small extra read_text per project, not
    a tree scan). Returns None when aim.json is absent/unreadable/malformed or the
    field is missing/non-string, so the gallery card cleanly omits the briefing line
    (daemon-skew safe). The create-time placeholder (DEFAULT_AIM_DESCRIPTION, exact
    match) also reads as None: it describes the format, not the Brain."""
    from core.swarm_workspace_manager import DEFAULT_AIM_DESCRIPTION

    aim = project_dir / "aim.json"
    if aim.exists():
        try:
            import json

            data = json.loads(aim.read_text(encoding="utf-8"))
            desc = data.get("description") if isinstance(data, dict) else None
            if isinstance(desc, str) and desc.strip():
                desc = desc.strip()
                if desc == DEFAULT_AIM_DESCRIPTION.replace("{project_name}", project_dir.name):
                    return None
                return desc
        except (OSError, ValueError):
            pass
    return None


def _brain_summary(project_dir: Path, pending_override: Optional[dict[str, int]] = None,
                   run_facts: Optional[dict] = None) -> dict:
    """Gallery card data (cheap: build_brain_state with_noise=False).

    `pending_override` / `run_facts` are the gallery's once-per-request aggregates
    (attention channel, _run_facts), so no card re-scans them."""
    return build_brain_state(
        project_dir, with_noise=False, pending_override=pending_override,
        run_facts=run_facts,
    )


def _brain_detail(project_dir: Path) -> dict:
    # Detail = the single builder's full state (with_noise=True) PLUS the
    # per-open-only structural extras (sections/entries/specs/hasCodeIntel).
    # health/identity/lifecycle come from build_brain_state — no second builder.
    #
    # PERF (run_43dc94f6): parse the ② docs ONCE + fetch git status ONCE up front,
    # then feed EVERY downstream consumer from those two caches — instead of the old
    # path that read+parsed the same 4 docs 4+ times (_gallery_entry_stats +
    # _parse_all_knowledge_entries + _knowledge_entries + per-member _file_entry_count)
    # and forked git once per member (the old per-member _file_entry_count parse is
    # gone — entryCount now reads len off the shared parse). Response JSON is
    # byte-identical; only faster.
    parsed = _parse_knowledge_docs_grouped(project_dir)
    git_status = _batch_git_status(project_dir)
    ws = _workspace_root()

    def _git_of(rel: str) -> str:
        # Look up the member's WS-relative path in the batch dict (parity with the old
        # per-file _file_git_status key); absent (unchanged/gitignored) → "clean".
        try:
            ws_rel = (project_dir.relative_to(ws) / rel).as_posix()
        except ValueError:
            ws_rel = rel
        return git_status.get(ws_rel, "clean")

    state = build_brain_state(project_dir, with_noise=True, parsed=parsed)
    sections = []
    for key, num, label, own_govern, curator in _SECTIONS:
        member_rels = _section_members(project_dir, key)
        members = [
            {"path": rel, "gitStatus": _git_of(rel)}
            for rel in member_rels
        ]
        # ② knowledge members are the 4 canonical DDD docs surfaced as hero cards
        # (run_a607f2b0): enrich each with its own last-updated (fs mtime) + entry
        # count so the card answers "is this doc active, and how big". DETAIL-only —
        # never on the cheap gallery projection (build_brain_state with_noise=False).
        # entryCount reuses the shared parse (len of THIS doc's entries) — keyed by
        # m["path"], the same rel string _parse_knowledge_docs_grouped keys on.
        if key == "knowledge":
            for m in members:
                m["mtime"] = _file_mtime_relative(project_dir / m["path"])
                m["entryCount"] = len(parsed.get(m["path"], []))
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
            section["entries"] = _knowledge_entries(project_dir, parsed=parsed)
        sections.append(section)

    # Layer the per-open structural extras onto the single builder's state.
    state["sections"] = sections
    # hasCodeIntel = a live PRESENCE check of the on-disk code_intel.db, NOT gated
    # on `kind` (all DDDs resolve to kind='knowledge'). One stat, per-brain-on-open.
    from core.code_intel import code_intel_db_for
    state["hasCodeIntel"] = code_intel_db_for(project_dir) is not None
    # hasSpecs = a survey.json exists (design 2026-10-27 §5.3): the Specs disclosure
    # renders only then, and fetches the list only when expanded.
    from core.brain_bootstrap.survey import survey_path
    state["hasSpecs"] = survey_path(project_dir).is_file()
    # projectAgent = the ⑤ Project Agent definition (project-agent.yaml), parsed and
    # validated; None when the Brain has none yet. One small file read, on open only.
    state["projectAgent"] = load_project_agent(project_dir)
    return state


def _knowledge_entries(
    project_dir: Path, parsed: Optional[dict[str, list[EntryMetadata]]] = None
) -> list[dict]:
    """Per-entry decay/type state for the ② canonical docs.

    `parsed` (run_43dc94f6): reuse the shared {rel→entries} dict instead of a live
    re-parse. None → self-parse (byte-identical). Doc iteration order + per-doc entry
    order are unchanged (same _KNOWLEDGE_DOCS loop, same parse_entries doc order), so
    the emitted list is identical whether shared or self-parsed.
    """
    out: list[dict] = []
    for doc in _KNOWLEDGE_DOCS:
        p = ddd_path(project_dir, doc)
        if not p.exists():
            continue
        rel = _rel(project_dir, p)
        if parsed is not None:
            entries = parsed.get(rel, [])
        else:
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

#: GET /ddd/brains slower than this logs its per-leg timings.
_SLOW_GALLERY_MS = 1500


@router.get("/brains")
async def list_brains() -> dict:
    """Gallery: one live summary per DDD project (read-only)."""
    import time as _time
    t_start = _time.perf_counter()
    leg_ms: dict[str, float] = {}

    async def _timed(name: str, coro):
        t = _time.perf_counter()
        try:
            return await coro
        finally:
            leg_ms[name] = (_time.perf_counter() - t) * 1000

    dirs = await executors.run_nav(_list_project_dirs)

    # Two producer legs, run concurrently; neither starts a subprocess (design
    # left-nav-backend-and-visual-system.md B3):
    # - pending: the attention channel aggregated ONCE (run_cfb460ac) and bucketed
    #   per brain here. Fail-soft: any error -> every brain pending=0.
    # - runs: each project's last landing + in-progress state from its runs of the
    #   last 30 days (_run_facts, a cached run_index read). Fail-soft -> no facts.
    async def _pending_leg() -> dict[str, dict[str, int]]:
        by_brain: dict[str, dict[str, int]] = {}
        try:
            from core.attention_authority import collect

            agg = await executors.run_nav(collect, _workspace_root())
            for it in agg.items:
                if it.brain:  # governance items have brain=None — excluded (parity with _pending_count)
                    src = by_brain.setdefault(it.brain, {})
                    src[it.source] = src.get(it.source, 0) + 1
        except Exception as e:  # never 500 the gallery on an attention-scan hiccup
            logger.warning("attention aggregate failed, brains show pending=0: %s", e)
        return by_brain

    _pending_r, _runs_r = await asyncio.gather(
        _timed("pending", _pending_leg()),
        _timed("runs", executors.run_nav(_run_facts, _workspace_root())),
        return_exceptions=True,
    )
    pending_by_brain = _pending_r if isinstance(_pending_r, dict) else {}
    run_facts = _runs_r if isinstance(_runs_r, dict) else {}
    for _leg, _res in (("pending", _pending_r), ("runs", _runs_r)):
        if isinstance(_res, BaseException):
            logger.warning("brains list leg %s raised, degraded: %s", _leg, _res)

    t_sum = _time.perf_counter()
    results = await asyncio.gather(
        *(
            executors.run_nav(_brain_summary, d, pending_by_brain.get(d.name, {}),
                              run_facts.get(d.name, {}))
            for d in dirs
        ),
        return_exceptions=True,
    )
    leg_ms["summaries"] = (_time.perf_counter() - t_sum) * 1000
    total_ms = (_time.perf_counter() - t_start) * 1000
    # Measured 2026-10-23: 0.4-0.9 s warm, 3.8 s on the first call after a restart, with no
    # single leg dominating in a standalone run. Log the legs when slow, so the next slow
    # gallery says which one it was (design left-nav-overlay-single-source.md S4).
    if total_ms > _SLOW_GALLERY_MS:
        logger.warning("brains gallery slow: %.0f ms (%s, %d projects)", total_ms,
                       ", ".join(f"{k} {v:.0f}" for k, v in leg_ms.items()), len(dirs))
    brains = []
    for d, r in zip(dirs, results):
        if isinstance(r, Exception):
            logger.warning("brain summary failed for %s: %s", d.name, r)
            continue
        brains.append(r)
    # pinned: SwarmAI-first ordered focus projects (existence-guarded). A field on
    # this response, NOT a separate endpoint (Gate-1: one fewer round-trip). The
    # frontend renders these as the top-row bento (SwarmAI big + 2 small stacked)
    # and the Welcome Top-N; the rest fall into the 3-per-row grid.
    return {"brains": brains, "pinned": get_pinned_projects()}


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

    detail = await executors.run_nav(_detail_or_none)
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
            env=_GIT_READ_ENV(),
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
            env=_GIT_READ_ENV(),
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
            env=_GIT_READ_ENV(),
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
            env=_GIT_READ_ENV(),
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
            env=_GIT_READ_ENV(),
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
        logger.debug("_scoped_diff_hunks: suppressed exception on a fail-safe path", exc_info=True)
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
    except Exception as exc:  # noqa: BLE001
        # Degrade-OBSERVABLE, and the highest-consequence instance of the pattern in
        # this router: [] renders as "nothing is waiting for approval", so a broken
        # payload builder does not surface as an error — it makes every pending
        # proposal INVISIBLE and therefore unapprovable, while the UI looks calm.
        logger.warning("cannot build pending-proposals payload for %s, reporting "
                       "none pending: %s", project_dir, exc)
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

    result = await executors.run_nav(_work)
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

    head = await executors.run_nav(_work)
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
                env=_GIT_READ_ENV(),
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
                env=_GIT_READ_ENV(),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            raise HTTPException(status_code=500, detail=f"git apply failed: {e}")
        if proc.returncode != 0:
            raise HTTPException(
                status_code=409,
                detail=f"Hunk no longer applies cleanly: {proc.stderr.strip()}",
            )
        return {"reverted": True, "file": target["file"], "signature": body.hunk_signature}

    return await executors.run_nav(_work)


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


def _output_is_committed(project_dir: Path, out_dir: Path) -> bool:
    """Return whether the distribute-output directory has a commit in this tree.

    Uncommitted output has no commit baseline, so freshness remains unknown.
    """
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
            env=_GIT_READ_ENV(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


def _content_changed_since_output(project_dir: Path, out_dir: Path) -> Optional[bool]:
    """Return whether committed DDD content changed after the output commit.

    Commit identity, rather than filesystem or commit timestamps, defines ordering.
    Git timestamps have one-second resolution and filesystem mtimes are reset by
    checkout, so comparing either can miss a source commit created in the same second.
    ``None`` means Git could not establish the relationship.
    """
    ws = _workspace_root()
    if not (ws / ".git").exists():
        return None
    try:
        project_rel = project_dir.relative_to(ws).as_posix()
        output_rel = out_dir.relative_to(ws).as_posix()
    except ValueError:
        return None
    try:
        output_commit = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", output_rel],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            env=_GIT_READ_ENV(),
        )
        output_hash = output_commit.stdout.strip()
        if output_commit.returncode != 0 or not output_hash:
            return None
        newer_content = subprocess.run(
            ["git", "log", "-1", "--format=%H", f"{output_hash}..HEAD", "--",
             project_rel, f":(exclude){project_rel}/.artifacts/**"],
            cwd=str(ws), capture_output=True, text=True, timeout=5,
            env=_GIT_READ_ENV(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if newer_content.returncode != 0:
        return None
    return bool(newer_content.stdout.strip())


def _distribution_state(project_dir: Path) -> dict:
    """Live distribution projection for one DDD (declared reach + output state)."""
    from core.ddd_distribution_policy import validate_distribution_file

    pol = validate_distribution_file(project_dir / "aim.json")
    out_dir = _distribute_output_dir(project_dir)
    output_path: Optional[str] = None
    last_distribute_time: Optional[str] = None
    # TRISTATE (F2): None = freshness UNKNOWN. A committed output provides an
    # exact Git baseline; later commits touching reviewable DDD content make it
    # stale. An uncommitted output has no baseline, so it remains unknown.
    source_changed_since: Optional[bool] = None
    if out_dir is not None:
        output_path = out_dir.name  # the .artifacts/<name> stem (never an abs host path)
        try:
            out_mtime = out_dir.stat().st_mtime
            last_distribute_time = datetime.fromtimestamp(
                out_mtime, tz=timezone.utc
            ).isoformat()
            if _output_is_committed(project_dir, out_dir):
                # Source freshness is a commit-order question. The output commit is
                # the baseline; any later commit touching reviewable DDD content
                # (excluding .artifacts) makes the output stale.
                source_changed_since = _content_changed_since_output(project_dir, out_dir)
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

    result = await executors.run_nav(_work)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return result


@router.get("/brains/{name}/specs")
async def get_specs(name: str) -> dict:
    """The repo documents of one brain, by tier, from its survey.json (design
    2026-10-27-new-brain-initial-ddd §5.3). No text; ``available: false`` before the
    first survey."""

    def _work() -> Optional[dict]:
        project_dir = _resolve_brain_dir(name)   # containment-checked (Run 2 guard)
        if project_dir is None:
            return None
        from core.brain_bootstrap.survey import specs_view
        return specs_view(project_dir)

    result = await executors.run_nav(_work)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    return result


@router.get("/brains/{name}/specs/doc")
async def get_spec_doc(name: str, repo: str = "", path: str = "") -> dict:
    """One repo document's text. The path is confined again to the repo the brain
    owns now; survey.json is never trusted for where to read (§5.3, §8)."""

    def _work():
        project_dir = _resolve_brain_dir(name)
        if project_dir is None:
            return "no-brain"
        from core.brain_bootstrap.survey import read_spec
        return read_spec(project_dir, repo, path)

    result = await executors.run_nav(_work)
    if result == "no-brain":
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    if result is None:
        raise HTTPException(status_code=404, detail="No such document")
    return result


# The domains a Brain Hub recall fans over: the DDD knowledge for this project plus the
# library/codeintel it governs. Shares the recall_all BACKEND with session + library
# recall (thin-unify, NO facade — design §2/§3.6), NOT their policy: this is the OVERLAY
# contract (empty-ok / degrade-to-empty), never session's never-empty/fallback.
_BRAINHUB_RECALL_DOMAINS = ("ddd", "library", "codeintel")


@router.get("/brains/{name}/recall")
async def brain_recall(name: str, q: str = "") -> dict:
    """Recall DDD knowledge for one brain — the Brain Hub's search box.

    Overlay contract: empty q → empty hits; ANY failure degrades to empty, never 500
    (mirrors library_api.library_search). Records a ``brainhub_overlay`` metric so the
    recall-metrics visibility layer (Run 3) can see Brain Hub latency alongside the
    session + library contexts. A 404 is still raised for an unknown brain (a real
    routing error, distinct from an empty-recall result)."""
    project_dir = _resolve_brain_dir(name)   # containment-checked (Run 2 guard)
    if project_dir is None:
        raise HTTPException(status_code=404, detail=f"No such DDD brain: {name}")
    if not q.strip():
        return {"query": q, "count": 0, "hits": []}
    try:
        import time as _time
        from core.recall_multi import recall_all
        _t0 = _time.perf_counter()
        result = await executors.run_nav(
            recall_all, q, project=name, domains=_BRAINHUB_RECALL_DOMAINS)
        _latency_ms = (_time.perf_counter() - _t0) * 1000.0
        hits: list[dict] = []
        for domain in _BRAINHUB_RECALL_DOMAINS:
            for h in (result.buckets.get(domain) or []):
                source = h.get("source") or h.get("file_path") or h.get("mount_path") or ""
                hits.append({
                    "domain": domain,
                    "title": h.get("heading") or h.get("name") or source or "",
                    "source": source,
                    "content": (h.get("content") or "")[:400],
                })
        # Recall metric (Run 3): one sample for this overlay search. Fire-and-forget —
        # never breaks the search (mirrors library_api).
        try:
            from core.recall_metrics import record_recall_metric
            record_recall_metric("brainhub_overlay", _BRAINHUB_RECALL_DOMAINS,
                                  _latency_ms, hit_count=len(hits))
        except Exception:  # noqa: BLE001
            logger.debug("brain_recall: suppressed exception on a fail-safe path", exc_info=True)
            pass
        return {"query": q, "count": len(hits), "hits": hits}
    except Exception as exc:  # noqa: BLE001 — degrade to empty, never 500 the overlay
        logger.warning("brain recall failed for %s: %s", name, exc)
        return {"query": q, "count": 0, "hits": [], "error": "recall unavailable"}
