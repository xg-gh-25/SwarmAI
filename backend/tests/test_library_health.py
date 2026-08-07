"""Library Health — heuristic scan + cleanup actions over Knowledge/.

Tested at the module level (core.library_health): the scan is a pure filesystem
read that proposes cleanup findings; apply_action is the only mutation and it is
reversible-first + confirm-gated for delete.

Properties:
  1. scan finds old raw-logs (>90d in DailyActivity/Signals/JobResults) → archivable, reversible
  2. scan finds empty/tiny files (<100B) → delete finding, NOT reversible (confirm-gated)
  3. scan flags oversized categories as informational (no action button)
  4. scan is clean when Knowledge/ is tidy
  5. curated categories (Notes/Designs) are NEVER proposed for auto-archive
  6. archive_old_logs MOVES files to Archives/ (source gone, dest present) — reversible
  7. delete_empty WITHOUT confirm is a no-op (confirm_required); WITH confirm deletes
  8. NEGATIVE: a path escaping Knowledge/ (traversal) is rejected, file untouched
  9. a stale path (already moved) is skipped, not an error
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.library_health import (
    scan_library_health,
    apply_action,
    OLD_LOG_DAYS,
    TINY_FILE_BYTES,
    OVERSIZED_CATEGORY_BYTES,
    ARCHIVE_DIR,
)


def _write(p: Path, content: str = "x" * 500, age_days: float = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    if age_days:
        old = time.time() - age_days * 24 * 3600
        os.utime(p, (old, old))
    return p


@pytest.fixture
def kdir(tmp_path: Path) -> Path:
    k = tmp_path / "Knowledge"
    k.mkdir()
    return k


def _find(report: dict, kind: str) -> dict | None:
    return next((f for f in report["findings"] if f["kind"] == kind), None)


# ── 1. old raw-logs ──────────────────────────────────────────────────────────
def test_scan_finds_old_raw_logs(kdir: Path):
    _write(kdir / "DailyActivity" / "2026-01-01.md", age_days=OLD_LOG_DAYS + 5)
    _write(kdir / "Signals" / "old-digest.md", age_days=OLD_LOG_DAYS + 5)
    _write(kdir / "DailyActivity" / "recent.md", age_days=1)  # NOT old

    report = scan_library_health(kdir)
    f = _find(report, "archive_old_logs")
    assert f is not None
    assert f["count"] == 2            # the two old ones, not the recent
    assert f["reversible"] is True    # archive = one-click
    assert f["actionable"] is True
    assert "DailyActivity/recent.md" not in f["paths"]


# ── 2. empty/tiny files ──────────────────────────────────────────────────────
def test_scan_finds_tiny_files_confirm_gated(kdir: Path):
    _write(kdir / "Notes" / "empty.md", content="")
    _write(kdir / "Notes" / "tiny.md", content="hi")
    _write(kdir / "Notes" / "real.md", content="x" * 500)  # NOT tiny

    report = scan_library_health(kdir)
    f = _find(report, "delete_empty")
    assert f is not None
    assert f["count"] == 2
    assert f["reversible"] is False   # delete = destructive → confirm-gated in UI


# ── 3. oversized category (informational) ────────────────────────────────────
def test_scan_flags_oversized_category(kdir: Path):
    big = kdir / "DailyActivity" / "huge.md"
    _write(big, content="x" * (OVERSIZED_CATEGORY_BYTES + 10))

    report = scan_library_health(kdir)
    f = _find(report, "oversized_category")
    assert f is not None
    assert f["actionable"] is False   # flag only, no button
    assert f["action_label"] == ""


# ── 4. clean store ───────────────────────────────────────────────────────────
def test_scan_clean_when_tidy(kdir: Path):
    _write(kdir / "Notes" / "good.md", content="x" * 500, age_days=1)
    report = scan_library_health(kdir)
    assert report["clean"] is True
    assert report["findings"] == []


# ── 5. curated categories never auto-archived ────────────────────────────────
def test_curated_categories_not_archived(kdir: Path):
    # An OLD file in Notes/ (curated) must NOT appear in archive_old_logs.
    _write(kdir / "Notes" / "old-note.md", content="x" * 500, age_days=OLD_LOG_DAYS + 30)
    report = scan_library_health(kdir)
    f = _find(report, "archive_old_logs")
    assert f is None  # Notes is curated, not a raw-log category


# ── 6. archive moves to Archives/ (reversible) ───────────────────────────────
def test_archive_moves_to_archives(kdir: Path):
    src = _write(kdir / "DailyActivity" / "2026-01-01.md", age_days=OLD_LOG_DAYS + 5)
    result = apply_action(kdir, "archive_old_logs", ["DailyActivity/2026-01-01.md"])
    assert result["status"] == "success"
    assert result["applied"] == 1
    assert not src.exists()  # moved out
    assert (kdir / ARCHIVE_DIR / "DailyActivity" / "2026-01-01.md").is_file()  # landed


# ── 7. delete requires confirm ───────────────────────────────────────────────
def test_delete_requires_confirm(kdir: Path):
    empty = _write(kdir / "Notes" / "empty.md", content="")
    # without confirm → no-op
    r1 = apply_action(kdir, "delete_empty", ["Notes/empty.md"], confirm=False)
    assert r1["status"] == "confirm_required"
    assert empty.exists()  # untouched
    # with confirm → deleted
    r2 = apply_action(kdir, "delete_empty", ["Notes/empty.md"], confirm=True)
    assert r2["status"] == "success"
    assert r2["applied"] == 1
    assert not empty.exists()


# ── 8. traversal guard ───────────────────────────────────────────────────────
def test_traversal_path_rejected(kdir: Path, tmp_path: Path):
    outside = _write(tmp_path / "secret.txt", content="")
    result = apply_action(kdir, "delete_empty", ["../secret.txt"], confirm=True)
    assert outside.exists()  # NEVER touched
    assert result["applied"] == 0
    assert any("outside Knowledge" in e for e in result["errors"])


# ── 9. stale path skipped (not an error) ─────────────────────────────────────
def test_stale_path_skipped(kdir: Path):
    # path in the report but already gone → skipped, not errored
    result = apply_action(kdir, "archive_old_logs", ["DailyActivity/already-gone.md"])
    assert result["status"] == "success"
    assert result["applied"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == []


# ── 10. archive collision never clobbers (HIGH fix — unique-name loop) ────────
def test_archive_collision_never_clobbers(kdir: Path):
    # A file already sits in Archives/ at the exact dest path AND shares mtime with
    # the incoming file — the mtime-suffix alone would collide; the loop must find
    # a free name and preserve BOTH files.
    existing = _write(kdir / ARCHIVE_DIR / "DailyActivity" / "2026-01-01.md",
                      content="ARCHIVED-ORIGINAL")
    src = _write(kdir / "DailyActivity" / "2026-01-01.md",
                 content="INCOMING", age_days=OLD_LOG_DAYS + 5)
    # force src to share existing's exact mtime → the mtime-suffix alone would collide
    est = existing.stat()
    os.utime(src, (est.st_atime, est.st_mtime))
    result = apply_action(kdir, "archive_old_logs", ["DailyActivity/2026-01-01.md"])
    assert result["applied"] == 1
    assert not src.exists()
    # original archived file is INTACT (never overwritten)
    assert existing.read_text() == "ARCHIVED-ORIGINAL"
    # incoming landed under a distinct name
    archived = list((kdir / ARCHIVE_DIR / "DailyActivity").glob("2026-01-01*.md"))
    assert len(archived) == 2
    assert any(p.read_text() == "INCOMING" for p in archived)


# ── 11. old AND tiny file listed ONCE (MEDIUM fix — no double-list) ───────────
def test_old_and_tiny_file_not_double_listed(kdir: Path):
    # a 2-byte, >90d file in DailyActivity is BOTH old and tiny → must appear only
    # in archive_old_logs (finding #1 claims it), NOT also in delete_empty.
    _write(kdir / "DailyActivity" / "old-tiny.md", content="hi", age_days=OLD_LOG_DAYS + 5)
    report = scan_library_health(kdir)
    archive = _find(report, "archive_old_logs")
    delete = _find(report, "delete_empty")
    assert archive is not None and "DailyActivity/old-tiny.md" in archive["paths"]
    # not double-listed in delete_empty (which should not exist at all here)
    assert delete is None or "DailyActivity/old-tiny.md" not in delete["paths"]
