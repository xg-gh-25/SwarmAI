"""Library Health — heuristic scan of Knowledge/ for cleanup candidates.

WHY (product): the Library Native store (`Knowledge/`) grows unbounded — raw
logs (DailyActivity/Signals/JobResults) accumulate forever, empty files pile up.
Without a health view + a cleanup ENTRY POINT it rots into a graveyard (Principle
1: the brain must not hoard dead weight). This module is the SCAN half; the job
(handlers/library_health) runs it weekly, the API serves the report + executes
the actions.

DESIGN PRINCIPLES:
- **Heuristic, not LLM** — pure filesystem stat reads: zero token cost, sub-second,
  safe to run weekly. No Bedrock, no external calls.
- **Decision-oriented, not a dashboard** — every finding carries an EXECUTABLE
  action (archive / delete) OR is a flag that needs no action. We do NOT emit
  drift-prone dead numbers as the product (R30): the counts here exist only to
  size an action ("archive 60 old logs"), and are recomputed live each scan —
  never stored as a standalone metric.
- **Reversible-first (STEERING #2)** — `archive` MOVES files to Archives/ (fully
  recoverable); `delete` is the only destructive action and is CONFIRM-GATED at
  the API layer (never auto-executed by the scan or the job).
- **Scoped to Knowledge/** — every path is validated to live under Knowledge/
  before any action (no traversal, no touching the rest of the workspace).

The scan is READ-ONLY: it proposes, it never mutates. Mutation lives in
`apply_action` and only runs on an explicit API call.

@exports scan_library_health, apply_action, ActionKind
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

REPORT_FILENAME = ".library-health.json"

# ── Heuristic thresholds (disaster-recovery floors, not judgment — P6) ──
OLD_LOG_DAYS = 90                     # raw-log age past which it's archivable
TINY_FILE_BYTES = 100                 # a file this small is effectively empty
OVERSIZED_CATEGORY_BYTES = 30_000_000  # a category this large gets a "review" flag

# Raw-log categories: append-only machine output, safe to age out to Archives/.
# (Curated knowledge — Notes/Designs/Learned/Reports — is NEVER auto-archived.)
RAW_LOG_CATEGORIES = ("DailyActivity", "Signals", "JobResults")
ARCHIVE_DIR = "Archives"
_SKIP = {"__pycache__", ".git", ".DS_Store", ARCHIVE_DIR}

ActionKind = Literal["archive_old_logs", "delete_empty", "oversized_category"]


@dataclass
class HealthFinding:
    """One health finding. `action` names what CAN be done; `paths` are the exact
    files it applies to (validated at apply time). `reversible` drives the UI
    (one-click vs confirm). `count`/`bytes` size the action, recomputed each scan."""
    kind: ActionKind
    title: str                 # human one-liner, e.g. "60 old raw-logs (>90d)"
    detail: str                # what the action does
    action_label: str          # button text, e.g. "Archive to Archives/"
    actionable: bool           # False = informational flag (no button)
    reversible: bool           # True = one-click; False = confirm-gated
    count: int
    total_bytes: int
    paths: list[str] = field(default_factory=list)  # Knowledge-relative paths

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "title": self.title,
            "detail": self.detail,
            "action_label": self.action_label,
            "actionable": self.actionable,
            "reversible": self.reversible,
            "count": self.count,
            "total_bytes": self.total_bytes,
            "paths": self.paths,
        }


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return time.time()  # unreadable → treat as "now" (never flag as old)


def scan_library_health(knowledge_dir: Path, now: float | None = None) -> dict:
    """Scan Knowledge/ and return a health report (READ-ONLY, no mutation).

    Returns {generated_at, root, findings: [HealthFinding.to_dict()], clean}.
    `clean` is True when there is nothing to act on — the UI shows "healthy".
    """
    now = now if now is not None else time.time()
    findings: list[HealthFinding] = []

    if not knowledge_dir.is_dir():
        return {"generated_at": now, "root": "Knowledge/", "findings": [], "clean": True}

    old_cutoff = now - OLD_LOG_DAYS * 24 * 3600

    # ── 1. Archivable old raw-logs (DailyActivity/Signals/JobResults >90d) ──
    old_paths: list[str] = []
    old_bytes = 0
    for cat in RAW_LOG_CATEGORIES:
        cdir = knowledge_dir / cat
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*"):
            if not p.is_file() or p.name.startswith("."):
                continue
            if _safe_mtime(p) < old_cutoff:
                old_paths.append(_rel(p, knowledge_dir))
                old_bytes += _safe_size(p)
    if old_paths:
        findings.append(HealthFinding(
            kind="archive_old_logs",
            title=f"{len(old_paths)} old raw-logs (>{OLD_LOG_DAYS}d)",
            detail=f"Append-only logs in {', '.join(RAW_LOG_CATEGORIES)} older than "
                   f"{OLD_LOG_DAYS} days — move to {ARCHIVE_DIR}/ (recoverable).",
            action_label=f"Archive to {ARCHIVE_DIR}/",
            actionable=True, reversible=True,
            count=len(old_paths), total_bytes=old_bytes, paths=sorted(old_paths),
        ))

    # ── 2. Empty / tiny files (<100B) anywhere under Knowledge/ ──
    # A file already claimed by finding #1 (an old raw-log) is EXCLUDED here, so a
    # file that is both old AND tiny is proposed once (archive), never listed twice.
    old_set = set(old_paths)
    tiny_paths: list[str] = []
    tiny_bytes = 0
    for p in knowledge_dir.rglob("*"):
        if not p.is_file() or p.name.startswith("."):
            continue
        if _in_skip(p, knowledge_dir):
            continue
        if _rel(p, knowledge_dir) in old_set:
            continue  # already covered by archive_old_logs — don't double-list
        if _safe_size(p) < TINY_FILE_BYTES:
            tiny_paths.append(_rel(p, knowledge_dir))
            tiny_bytes += _safe_size(p)
    if tiny_paths:
        findings.append(HealthFinding(
            kind="delete_empty",
            title=f"{len(tiny_paths)} empty/tiny files (<{TINY_FILE_BYTES}B)",
            detail="Near-empty files carrying no knowledge — delete (needs confirm).",
            action_label="Delete",
            actionable=True, reversible=False,   # destructive → confirm-gated
            count=len(tiny_paths), total_bytes=tiny_bytes, paths=sorted(tiny_paths),
        ))

    # ── 3. Oversized categories (informational flag — no auto-action) ──
    for sub in sorted(knowledge_dir.iterdir()):
        if not sub.is_dir() or sub.name in _SKIP:
            continue
        cat_bytes = sum(_safe_size(p) for p in sub.rglob("*") if p.is_file())
        if cat_bytes >= OVERSIZED_CATEGORY_BYTES:
            findings.append(HealthFinding(
                kind="oversized_category",
                title=f"{sub.name} is large ({_fmt_bytes(cat_bytes)})",
                detail=f"{sub.name}/ is the biggest weight in the store — review "
                       f"whether older entries can be trimmed or archived.",
                action_label="",
                actionable=False, reversible=False,
                count=1, total_bytes=cat_bytes, paths=[sub.name],
            ))

    return {
        "generated_at": now,
        "root": "Knowledge/",
        "findings": [f.to_dict() for f in findings],
        "clean": len(findings) == 0,
    }


def apply_action(knowledge_dir: Path, kind: ActionKind, paths: list[str],
                 confirm: bool = False) -> dict:
    """Execute a cleanup action on the given Knowledge-relative paths.

    - archive_old_logs → MOVE each file into Archives/<original-category-path>
      (reversible; the folder structure under Archives/ mirrors the source).
    - delete_empty → DELETE each file, but ONLY if `confirm=True` (destructive).
    - oversized_category → no-op (informational only).

    Every path is re-validated: it must resolve UNDER knowledge_dir (no traversal)
    and still exist (the report may be stale — a file already moved is skipped, not
    an error). Returns {status, applied, skipped, errors}.
    """
    if kind == "oversized_category":
        return {"status": "noop", "applied": 0, "skipped": 0, "errors": []}
    if kind == "delete_empty" and not confirm:
        return {"status": "confirm_required", "applied": 0, "skipped": len(paths), "errors": []}

    kroot = knowledge_dir.resolve()
    applied = 0
    skipped = 0
    errors: list[str] = []

    for rel in paths:
        try:
            target = (knowledge_dir / rel).resolve()
        except (OSError, ValueError):
            errors.append(f"{rel}: bad path")
            continue
        # Traversal guard: the resolved target MUST live under Knowledge/.
        if kroot != target and kroot not in target.parents:
            errors.append(f"{rel}: outside Knowledge/ (rejected)")
            continue
        if not target.is_file():
            skipped += 1  # already gone (stale report) — not an error
            continue

        try:
            if kind == "archive_old_logs":
                dest = knowledge_dir / ARCHIVE_DIR / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                # collision-safe: never overwrite an existing archived file. A
                # single mtime-suffix is NOT unique (two sources can share a name
                # AND mtime), so loop to the first genuinely-free name.
                if dest.exists():
                    stem, suffix = dest.stem, dest.suffix
                    n = 1
                    while dest.exists():
                        dest = dest.with_name(f"{stem}.{n}{suffix}")
                        n += 1
                shutil.move(str(target), str(dest))
                applied += 1
            elif kind == "delete_empty":
                target.unlink()
                applied += 1
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    return {
        "status": "success" if not errors else "partial",
        "applied": applied, "skipped": skipped, "errors": errors,
    }


def write_report_atomic(knowledge_dir: Path, report: dict) -> None:
    """Persist a health report to Knowledge/.library-health.json atomically
    (temp file in the same dir + os.replace) so a concurrent reader never sees a
    torn file. Shared by the weekly job and the post-action API refresh. Raises
    OSError on write failure (caller decides whether that's fatal)."""
    report_path = knowledge_dir / REPORT_FILENAME
    fd, tmp = tempfile.mkstemp(dir=str(knowledge_dir), prefix=".library-health-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, report_path)
    except OSError:
        try:
            os.unlink(tmp)  # don't leak the temp file on failure
        except OSError:
            pass
        raise


def _rel(p: Path, knowledge_dir: Path) -> str:
    return p.relative_to(knowledge_dir).as_posix()


def _in_skip(p: Path, knowledge_dir: Path) -> bool:
    """True if p is under a skipped top-level dir (Archives/, __pycache__, ...)."""
    parts = p.relative_to(knowledge_dir).parts
    return bool(parts) and parts[0] in _SKIP


def _fmt_bytes(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.0f}K"
    return f"{n}B"
