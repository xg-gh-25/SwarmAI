"""Tests for A1 auto local-commit (run_76932250).

Covers:
- files_touched dedup-append via `run-update --files-touched`
- run-commit gates: refuse when not push_ready / empty files_touched
- run-commit stages ONLY this run's files (git add -- <path>, never -A) so a
  parallel session's dirty file is NOT swept in (R29)
- run-commit NEVER pushes (no remote interaction; pushed:false always)
"""
import json
import subprocess
from argparse import Namespace

import pytest


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects" / "TestProject" / ".artifacts" / "runs").mkdir(parents=True)
    return tmp_path


def _write_run(workspace, run_id, *, status="running", profile="bugfix",
               stages=None, files_touched=None):
    run_dir = workspace / "Projects" / "TestProject" / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "id": run_id, "project": "TestProject", "requirement": "Fix the thing",
        "profile": profile, "status": status, "stages": stages or [],
        "taste_decisions": [], "created_at": "2026-07-04T00:00:00+00:00",
        "updated_at": "2026-07-04T00:00:00+00:00",
    }
    if files_touched is not None:
        data["files_touched"] = files_touched
    (run_dir / "run.json").write_text(json.dumps(data), encoding="utf-8")
    return run_dir / "run.json"


# A delivered run: PUSH-READY deliver + seeded reflect lessons. run-commit refuses
# until the lessons exist, so code and REPORT.md land in ONE commit.
_DELIVERED = [
    {"stage": "deliver", "status": "completed", "push_ready": True},
    {"stage": "reflect", "status": "recorded",
     "lessons": ["[pitfall] a durable lesson long enough to count"]},
]


def _src(files):
    """A commit's files minus this run's review deliverable (which rides along)."""
    return [f for f in files if not f.startswith(".aidlc/deliverables/")]


def _run_update(workspace, run_id, **kw):
    from scripts.artifact_cli import cmd_run_update
    from unittest.mock import patch
    # This mock must carry every flag cmd_run_update BARE-READS on the non-status path
    # (`if args.status:` guards force/reason, which this helper never reaches since it
    # passes status=None). The bare reads on that path are taste_decision/profile/
    # ddd_checksums/files_touched/add_required_expert. `add_required_expert` (argparse
    # default=None) was added with R1.5a and its bare read `if args.add_required_expert:`
    # was not reflected here — omitting it is a mock-drift AttributeError, not a code bug.
    # Add a flag here whenever cmd_run_update gains a NEW bare-read on the non-status path.
    args = Namespace(project="TestProject", run_id=run_id, status=kw.get("status"),
                     stage_json=kw.get("stage_json"), taste_decision=None, profile=None,
                     ddd_checksums=None, files_touched=kw.get("files_touched"),
                     add_required_expert=None,
                     force_checkpoint=False)
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        cmd_run_update(args, reg=None)


def _run_commit(workspace, run_id):
    from scripts.artifact_cli import cmd_run_commit
    from unittest.mock import patch
    args = Namespace(project="TestProject", run_id=run_id)
    captured = {}
    with patch("scripts.artifact_cli._get_workspace", return_value=workspace):
        try:
            cmd_run_commit(args, reg=None)
        except SystemExit as e:
            captured["exit"] = e.code
    return captured


# ── PR-review surface (run_b8ea6d5c): LOCAL_PR.md REMOVED; finish batch = rail rows ──
class TestNoLocalPR:
    """LOCAL_PR.md generation was removed (run_b8ea6d5c): the aggregated doc had no
    review value. The finish deliverable is per-file PR-review ROWS in the Canvas
    OUTPUTS rail, emitted by the surface_run_outputs tool (build_surface_events →
    orchestrator observe-emit), NOT a doc written by the run-commit CLI subprocess."""

    def test_write_local_pr_is_gone(self):
        import scripts.artifact_cli as ac
        assert not hasattr(ac, "_write_local_pr"), (
            "LOCAL_PR.md generation must be removed; the finish deliverable is now "
            "Canvas OUTPUTS rows via surface_run_outputs"
        )

    def test_build_surface_events_emits_source_final_rows_from_commits(self, tmp_path):
        """The replacement: build_surface_events reads a run's committed files and
        yields one file_changed(kind=source-final) event per file (deduped)."""
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_z"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [
                {"repo": "/repo", "files": ["desktop/src/a.tsx", "backend/b.py"]},
                {"repo": "/repo", "files": ["desktop/src/a.tsx", "c.md"]},  # a.tsx dup
            ]
        }))
        events = build_surface_events("run_z", workspace_root=str(tmp_path))
        assert len(events) == 3, "deduped across commits (a.tsx once)"
        assert all(e["type"] == "file_changed" for e in events)
        assert all(e["kind"] == "source-final" for e in events)
        assert all(e["operation"] == "written" for e in events)
        paths = {e["path"] for e in events}
        assert paths == {"desktop/src/a.tsx", "backend/b.py", "c.md"}

    def test_build_surface_events_empty_when_no_commits(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_none"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        # No commits AND no REPORT.md on disk → still empty (the REPORT append is
        # exists-guarded).
        assert build_surface_events("run_none", workspace_root=str(tmp_path)) == []


# ── REPORT.md finish-surface append (run_14e560ed PART 2) ──────────────────────
class TestBuildSurfaceEventsReportAppend:
    """build_surface_events appends the run's REPORT.md LAST as a kind=knowledge
    event so the Canvas auto-opens ON the report (last-write-wins) and renders it as
    CONTENT (no baseRef → not a diff). Source files stay as rows before it."""

    def test_report_appended_last_when_present(self, tmp_path):
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_rep"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [{"repo": "/repo", "files": ["backend/b.py", "desktop/a.tsx"]}],
        }))
        (run_dir / "REPORT.md").write_text("# Pipeline Report\n")
        events = build_surface_events("run_rep", workspace_root=str(tmp_path))
        # Source events + exactly one REPORT.md event, and it is LAST.
        report_events = [e for e in events if str(e.get("path", "")).endswith("REPORT.md")]
        assert len(report_events) == 1, f"exactly one REPORT.md event: {events}"
        last = events[-1]
        assert str(last["path"]).endswith("REPORT.md"), \
            f"REPORT.md must be the LAST event (last-write-wins auto-select): {events}"
        assert last["kind"] == "knowledge", "REPORT renders as knowledge (CONTENT, rail-kept)"
        assert last["relevance"] == "deliverable", "must pass the deliverable auto-pop gate"
        assert "baseRef" not in last, "no baseRef → renders CONTENT not diff"
        assert last["operation"] == "written"
        # Source events precede it, unchanged.
        assert events[0]["kind"] == "source-final"

    def test_no_report_event_when_absent(self, tmp_path, caplog):
        import json
        import logging
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_norep"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({
            "commits": [{"repo": "/repo", "files": ["backend/b.py"]}],
        }))
        # No REPORT.md written.
        with caplog.at_level(logging.WARNING, logger="core.ui_actions"):
            events = build_surface_events("run_norep", workspace_root=str(tmp_path))
        assert not any(str(e.get("path", "")).endswith("REPORT.md") for e in events), \
            "no REPORT event when the file is absent (exists-guard)"
        assert len(events) == 1 and events[0]["kind"] == "source-final", \
            "source events still returned"
        # LOUD-on-degradation: source rows present + REPORT absent → a WARNING fires
        # (surface ran before run-report) so the ordering hazard is observable, not silent.
        assert any("REPORT.md is absent" in r.message for r in caplog.records), \
            "must WARN when source rows exist but REPORT.md is missing (ordering hazard)"

    def test_no_warning_when_no_events_and_no_report(self, tmp_path, caplog):
        # A truly-empty run (no commits, no REPORT) must NOT warn — the loud signal is
        # ONLY for the degradation case (source emitted but report lost to ordering).
        import json
        import logging
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_empty2"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        with caplog.at_level(logging.WARNING, logger="core.ui_actions"):
            events = build_surface_events("run_empty2", workspace_root=str(tmp_path))
        assert events == []
        assert not any("REPORT.md is absent" in r.message for r in caplog.records), \
            "no warning for a genuinely empty run (no source rows to lose a report against)"

    def test_report_appended_even_when_no_commits(self, tmp_path):
        # A knowledge/docs-only run (empty commits[]) still surfaces its REPORT.md.
        import json
        import sys
        sys.path.insert(0, "backend")
        from core.ui_actions import build_surface_events

        run_dir = tmp_path / "Projects" / "P" / ".artifacts" / "runs" / "run_reponly"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"commits": []}))
        (run_dir / "REPORT.md").write_text("# Report\n")
        events = build_surface_events("run_reponly", workspace_root=str(tmp_path))
        assert len(events) == 1, f"just the REPORT event: {events}"
        assert str(events[0]["path"]).endswith("REPORT.md")
        assert events[0]["kind"] == "knowledge"
