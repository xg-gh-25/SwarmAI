"""Library Health — heuristic scan of Knowledge/ for cleanup candidates.

WHY (product): the Library Native store (`Knowledge/`) grows unbounded — job
logs (JobResults) accumulate forever, empty files pile up.
Left alone it rots into a graveyard (Principle 1: the brain must not hoard dead
weight). This module scans; the weekly job (handlers/library_health) runs
`auto_archive`, which moves the JobResults logs older than 90 days into
Archives/JobResults/ by itself (a move, so reversible) and records what it moved
in the report; the API serves the report and still executes an action on an
explicit call. DailyActivity is not this module's: context_health's retention
pass already moves its day files to Archives/ (flat, by the date in the name) and
expires them after 365 days, and core.coe_threads reads its `.jsonl` sidecars in
place.

DESIGN PRINCIPLES:
- **Heuristic, not LLM** — pure filesystem stat reads: zero token cost, sub-second,
  safe to run weekly. No Bedrock, no external calls.
- **Decision-oriented, not a dashboard** — every finding carries an EXECUTABLE
  action (archive / delete) OR is a flag that needs no action. We do NOT emit
  drift-prone dead numbers as the product (R30): the counts here exist only to
  size an action ("archive 60 old logs"), and are recomputed live each scan —
  never stored as a standalone metric.
- **Reversible-first (STEERING #2)** — `archive` MOVES files to Archives/ (fully
  recoverable), which is why the weekly job may do it unasked; `delete` is the only
  destructive action and is CONFIRM-GATED at the API layer (never auto-executed by
  the scan or the job).
- **Infrastructure is not clutter** — `*.lock` flock sidecars (utils.file_lock.md_lock
  keeps one beside every markdown file it guards) are never "empty files" and never
  archived; nor is a dotfile (`JobResults/.job-results.jsonl` is the live run index).
- **Scoped to Knowledge/** — every path is validated to live under Knowledge/
  before any action (no traversal, no touching the rest of the workspace).

The scan is READ-ONLY: it proposes, it never mutates. Mutation lives in
`apply_action` (an explicit API call) and `auto_archive` (the weekly job, archive
only).

@exports scan_library_health, apply_action, auto_archive, write_report_atomic, ActionKind
"""

from __future__ import annotations

import json
import logging
import os
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

# Raw-log categories: append-only machine output, safe to age out to Archives/.
# (Curated knowledge — Notes/Designs/Learned/Reports — is NEVER auto-archived.
# DailyActivity's retention belongs to context_health, see the module docstring.)
RAW_LOG_CATEGORIES = ("JobResults",)
ARCHIVE_DIR = "Archives"
_SKIP = {"__pycache__", ".git", ".DS_Store", ARCHIVE_DIR}

ActionKind = Literal["archive_old_logs", "delete_empty"]


def _is_lock(p: Path | str) -> bool:
    """A flock sidecar (`<file>.md.lock`, utils.file_lock.md_lock): infrastructure."""
    return str(p).endswith(".lock")


def _archivable(rel: str, mtime: float, now: float) -> bool:
    """The one rule the scan and the action share: a Knowledge-relative raw log under
    a raw-log category, older than OLD_LOG_DAYS, not a lock sidecar and with no
    dot-named part (a live index such as `JobResults/.job-results.jsonl`)."""
    parts = Path(rel).parts
    if len(parts) < 2 or parts[0] not in RAW_LOG_CATEGORIES or _is_lock(rel):
        return False
    if any(part.startswith(".") for part in parts):
        return False
    return mtime < now - OLD_LOG_DAYS * 24 * 3600


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

    # ── 1. Archivable old raw-logs (JobResults >90d) ──
    old_paths: list[str] = []
    old_bytes = 0
    for cat in RAW_LOG_CATEGORIES:
        cdir = knowledge_dir / cat
        if not cdir.is_dir():
            continue
        for p in cdir.rglob("*"):
            if not p.is_file():
                continue
            if _archivable(_rel(p, knowledge_dir), _safe_mtime(p), now):
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
        if not p.is_file() or p.name.startswith(".") or _is_lock(p):
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

    return {
        "generated_at": now,
        "root": "Knowledge/",
        "findings": [f.to_dict() for f in findings],
        "clean": len(findings) == 0,
    }


def apply_action(knowledge_dir: Path, kind: ActionKind, paths: list[str],
                 confirm: bool = False, now: float | None = None) -> dict:
    """Execute a cleanup action on the given Knowledge-relative paths.

    - archive_old_logs → MOVE each file into Archives/<original-category-path>
      (reversible; the folder structure under Archives/ mirrors the source).
      Only an old raw log (`_archivable`, the scan's own rule) is moved; a
      collision never overwrites an existing archived file.
    - delete_empty → DELETE each file, but ONLY if `confirm=True` (destructive).

    Every path is re-validated: it must resolve UNDER knowledge_dir (no traversal)
    and still exist (the report may be stale — a file already moved is skipped, not
    an error). Returns {status, applied, skipped, errors, bytes}.
    """
    if kind == "delete_empty" and not confirm:
        return {"status": "confirm_required", "applied": 0, "skipped": len(paths), "errors": []}

    kroot = knowledge_dir.resolve()
    applied = 0
    skipped = 0
    moved_bytes = 0
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
                rel_target = target.relative_to(kroot).as_posix()
                if not _archivable(rel_target, _safe_mtime(target), time.time() if now is None else now):
                    errors.append(f"{rel}: not an old raw log ({', '.join(RAW_LOG_CATEGORIES)}, "
                                  f"older than {OLD_LOG_DAYS} days)")
                    continue
                size = _safe_size(target)
                _archive_move(target, kroot / ARCHIVE_DIR / rel_target)
                applied += 1
                moved_bytes += size
            elif kind == "delete_empty":
                from core.ingestion.api import apply_user_edit, revision_of
                apply_user_edit(
                    target, None, expected_revision=revision_of(target), actor="user",
                )
                applied += 1
        except OSError as exc:
            errors.append(f"{rel}: {exc}")

    return {
        "status": "success" if not errors else "partial",
        "applied": applied, "skipped": skipped, "errors": errors, "bytes": moved_bytes,
    }


def _archive_move(src: Path, dest: Path) -> Path:
    """Move one raw log (JobResults: a log, not a governed corpus file, so not a CDF
    materializer write) to `dest` under Archives/, mirroring its folder.
    Never overwrites: a taken name gets the first free `.N` suffix (the exists check
    and the move are not atomic; the weekly job and a person's action racing on one
    name is accepted). Returns where it went."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = dest.with_name(f"{stem}.{n}{suffix}")
        n += 1
    os.replace(src, dest)
    return dest


def auto_archive(knowledge_dir: Path, now: float | None = None) -> dict:
    """The weekly job's pass: scan, move every raw log older than OLD_LOG_DAYS into
    Archives/ (reversible), then rescan. The report carries what was moved as
    `auto_archived` = {at, count, bytes, errors} (count 0 when nothing was old)."""
    now = now if now is not None else time.time()
    first = scan_library_health(knowledge_dir, now=now)
    old = next((f for f in first["findings"] if f["kind"] == "archive_old_logs"), None)
    result = {"applied": 0, "bytes": 0, "errors": []}
    if old is not None:
        result = apply_action(knowledge_dir, "archive_old_logs", old["paths"], now=now)
        if result["errors"]:
            logger.warning("library health: %d archive move(s) failed, first: %s",
                           len(result["errors"]), "; ".join(result["errors"][:3]))
    report = scan_library_health(knowledge_dir, now=now) if result["applied"] else first
    report["auto_archived"] = {
        "at": now, "count": result["applied"], "bytes": result.get("bytes", 0),
        "errors": len(result["errors"]),
    }
    return report


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
