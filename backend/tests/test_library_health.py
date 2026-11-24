"""Library Health — heuristic scan + cleanup actions over Knowledge/.

Tested at the module level (core.library_health): the scan is a pure filesystem
read that proposes cleanup findings; apply_action (explicit call) and auto_archive
(the weekly job, archive only) are the mutations, reversible-first + confirm-gated
for delete.

Properties:
  1. scan finds old raw-logs (>90d in JobResults) → archivable, reversible; DailyActivity
     is context_health's and never proposed
  2. scan finds empty/tiny files (<100B) → delete finding, NOT reversible (confirm-gated)
  3. `.lock` sidecars are never empty files and never archived; nor are dotfiles; no
     "category is large" finding
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
    ARCHIVE_DIR,
    auto_archive,
)


@pytest.fixture(autouse=True)
def _governed_writer(monkeypatch):
    from core.ingestion import api

    monkeypatch.setattr(api, "revision_of", lambda path: "revision")

    def apply(path, data, *, expected_revision, actor="user"):
        assert expected_revision == "revision"
        if data is None:
            path.unlink()
            return ""
        path.write_bytes(data)
        return "next"

    monkeypatch.setattr(api, "apply_user_edit", apply)


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
    _write(kdir / "DailyActivity" / "2026-01-01.md", age_days=OLD_LOG_DAYS + 5)  # context_health's
    _write(kdir / "JobResults" / "old-run.md", age_days=OLD_LOG_DAYS + 5)
    _write(kdir / "JobResults" / "old-run.jsonl", age_days=OLD_LOG_DAYS + 5)
    _write(kdir / "JobResults" / "recent.md", age_days=1)  # NOT old

    report = scan_library_health(kdir)
    f = _find(report, "archive_old_logs")
    assert f is not None
    assert f["count"] == 2            # the two old ones, not the recent
    assert f["reversible"] is True    # archive = one-click
    assert f["actionable"] is True
    assert f["paths"] == ["JobResults/old-run.jsonl", "JobResults/old-run.md"]


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


# ── 3. infrastructure is not clutter ─────────────────────────────────────────
def test_lock_sidecars_are_never_empty_files(kdir: Path):
    _write(kdir / "Notes" / "a.md")
    _write(kdir / "Notes" / "a.md.lock", content="")
    _write(kdir / "Notes" / "tiny.md", content="hi")
    f = _find(scan_library_health(kdir), "delete_empty")
    assert f is not None and f["paths"] == ["Notes/tiny.md"]


def test_old_lock_and_daily_jsonl_sidecars_are_not_archived(kdir: Path):
    old = OLD_LOG_DAYS + 5
    _write(kdir / "JobResults" / "run.md", age_days=old)
    _write(kdir / "JobResults" / "run.jsonl", age_days=old)
    _write(kdir / "JobResults" / "run.md.lock", content="", age_days=old)
    _write(kdir / "DailyActivity" / "2026-01-01.jsonl", age_days=old)
    _write(kdir / "JobResults" / ".job-results.jsonl", age_days=old)
    f = _find(scan_library_health(kdir), "archive_old_logs")
    assert f is not None and f["paths"] == ["JobResults/run.jsonl", "JobResults/run.md"]


def test_no_large_category_finding(kdir: Path):
    _write(kdir / "Designs" / "huge.md", content="x" * 40_000_000)
    report = scan_library_health(kdir)
    assert {f["kind"] for f in report["findings"]} <= {"archive_old_logs", "delete_empty"}
    assert report["clean"] is True


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


# ── 6. archive MOVES raw logs to Archives/ (reversible) ──────────────────────
def test_archive_moves_raw_logs_into_archives(kdir: Path):
    src = _write(kdir / "JobResults" / "2026-01-01-run.md", content="DAY", age_days=OLD_LOG_DAYS + 5)
    result = apply_action(kdir, "archive_old_logs", ["JobResults/2026-01-01-run.md"])
    assert result["status"] == "success" and result["applied"] == 1
    assert result["bytes"] == 3
    assert not src.exists()
    assert (kdir / ARCHIVE_DIR / "JobResults" / "2026-01-01-run.md").read_text() == "DAY"


def test_archive_action_refuses_what_the_scan_would_not_propose(kdir: Path):
    # the action applies the scan's own rule: a caller cannot move a young log, the
    # live run index (a dotfile) or a DailyActivity day (context_health's)
    young = _write(kdir / "JobResults" / "today.md", age_days=1)
    index = _write(kdir / "JobResults" / ".job-results.jsonl", age_days=OLD_LOG_DAYS + 5)
    day = _write(kdir / "DailyActivity" / "2026-01-01.md", age_days=OLD_LOG_DAYS + 5)
    result = apply_action(kdir, "archive_old_logs",
                          ["JobResults/today.md", "JobResults/.job-results.jsonl", "DailyActivity/2026-01-01.md"])
    assert result["applied"] == 0 and len(result["errors"]) == 3
    assert young.exists() and index.exists() and day.exists()


def test_archive_refuses_a_path_that_is_not_a_raw_log(kdir: Path):
    note = _write(kdir / "Notes" / "keep.md", age_days=OLD_LOG_DAYS + 5)
    lock = _write(kdir / "JobResults" / "x.md.lock", content="", age_days=OLD_LOG_DAYS + 5)
    result = apply_action(kdir, "archive_old_logs", ["Notes/keep.md", "JobResults/x.md.lock"])
    assert result["applied"] == 0 and len(result["errors"]) == 2
    assert note.exists() and lock.exists()


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
    result = apply_action(kdir, "archive_old_logs", ["JobResults/already-gone.md"])
    assert result["status"] == "success"
    assert result["applied"] == 0
    assert result["skipped"] == 1
    assert result["errors"] == []


# ── 10. archive never clobbers an existing archived file ─────────────────────
def test_archive_never_clobbers(kdir: Path):
    existing = _write(kdir / ARCHIVE_DIR / "JobResults" / "run.md",
                      content="ARCHIVED-ORIGINAL")
    src = _write(kdir / "JobResults" / "run.md",
                 content="INCOMING", age_days=OLD_LOG_DAYS + 5)
    result = apply_action(kdir, "archive_old_logs", ["JobResults/run.md"])
    assert result["applied"] == 1
    assert not src.exists()
    assert existing.read_text() == "ARCHIVED-ORIGINAL"
    assert (kdir / ARCHIVE_DIR / "JobResults" / "run.1.md").read_text() == "INCOMING"


# ── 12. the weekly job archives by itself and records it ─────────────────────
def test_auto_archive_moves_old_logs_and_records_them(kdir: Path):
    _write(kdir / "JobResults" / "old.md", content="x" * 10, age_days=OLD_LOG_DAYS + 5)
    _write(kdir / "JobResults" / "new.md", content="x" * 10, age_days=1)
    _write(kdir / "Notes" / "old-note.md", age_days=OLD_LOG_DAYS + 5)
    report = auto_archive(kdir)
    assert report["auto_archived"]["count"] == 1
    assert report["auto_archived"]["bytes"] == 10
    assert report["auto_archived"]["errors"] == 0
    assert (kdir / ARCHIVE_DIR / "JobResults" / "old.md").exists()
    assert (kdir / "JobResults" / "new.md").exists()
    assert (kdir / "Notes" / "old-note.md").exists()
    # the report is the state after the move: nothing old is left to propose
    assert _find(report, "archive_old_logs") is None


def test_auto_archive_with_nothing_old_records_zero(kdir: Path):
    _write(kdir / "JobResults" / "new.md", age_days=1)
    report = auto_archive(kdir)
    assert report["auto_archived"]["count"] == 0
    assert (kdir / "JobResults" / "new.md").exists()


def test_job_writes_the_report_with_what_it_archived(kdir: Path, monkeypatch):
    import json
    from core.library_health import REPORT_FILENAME
    from jobs.handlers import library_health as handler
    monkeypatch.setattr(handler, "_knowledge_dir", lambda: kdir)
    _write(kdir / "JobResults" / "2026-01-01-run.md", age_days=OLD_LOG_DAYS + 5)
    out = handler.run_library_health()
    assert out["status"] == "success" and out["archived"] == 1
    saved = json.loads((kdir / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert saved["auto_archived"]["count"] == 1


# ── 11. old AND tiny file listed ONCE (MEDIUM fix — no double-list) ───────────
def test_old_and_tiny_file_not_double_listed(kdir: Path):
    # a 2-byte, >90d file in JobResults is BOTH old and tiny → must appear only
    # in archive_old_logs (finding #1 claims it), NOT also in delete_empty.
    _write(kdir / "JobResults" / "old-tiny.md", content="hi", age_days=OLD_LOG_DAYS + 5)
    report = scan_library_health(kdir)
    archive = _find(report, "archive_old_logs")
    delete = _find(report, "delete_empty")
    assert archive is not None and "JobResults/old-tiny.md" in archive["paths"]
    # not double-listed in delete_empty (which should not exist at all here)
    assert delete is None or "JobResults/old-tiny.md" not in delete["paths"]
