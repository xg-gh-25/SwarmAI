"""The self-audit run with auto-fix (design docs/design/2026-10-27-ddd-drift-auto-fix.md):
a certain finding is fixed and reported as fixed, the rest are "needs you" with a reason
and the date they were first reported; a dry run changes nothing and writes its own
report; the drift signal counts only what is still open; the weekly report shows the fixes."""
import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import jobs.executor as ex
import jobs.handlers.ddd_self_audit as sa

DOC = """# P

## Current Focus

Next: add a behavioural benchmark once `s_review` exists, graded per dimension.
Phase: deliverable execution (mid-May to mid-June 2026).

## Open Items

- [x] P5 (REVIEW) — the reviewer is ported into the `s_review` skill
"""

FINDINGS = [
    {"title": "DDD drift [P/PROJECT.md]: waits for s_review, which exists",
     "description": "\"once `s_review` exists\" — why it's false: it exists",
     "fix": {"doc": "PROJECT.md", "old": "add a behavioural benchmark once `s_review` exists",
             "new": "add a behavioural benchmark now that `s_review` exists",
             "evidence": [{"kind": "done", "file": "PROJECT.md",
                           "quote": "- [x] P5 (REVIEW) — the reviewer is ported into the `s_review` skill"}]}},
    {"title": "DDD drift [P/PROJECT.md]: current focus is months old",
     "description": "\"deliverable execution (mid-May to mid-June 2026)\" — why it's false: it is October"},
]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    proj = ws / "Projects" / "P"
    (proj / "2-understanding").mkdir(parents=True)
    (proj / "2-understanding" / "PROJECT.md").write_text(DOC)
    results = ws / "Knowledge" / "JobResults"
    results.mkdir(parents=True)
    (results / "2026-09-21-ddd-self-audit.md").write_text("old report: deliverable execution (mid-May to mid-June 2026)\n")
    monkeypatch.setattr(sa, "SWARMWS", ws)
    monkeypatch.setattr(sa, "PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr(sa, "cli_too_old_message", lambda *a: None)
    monkeypatch.setattr(sa, "_resolve_job_model", lambda: "m")
    for name, value in (("_resolve_claude_cli", lambda: "claude"), ("_check_claude_auth", lambda p: None),
                        ("_cli_version", lambda p: "9.9"), ("_get_aws_credentials", lambda: {}),
                        ("_cli_supports_bare", lambda p, v: False), ("_load_mcp_config", lambda: None),
                        ("_build_cli_env", lambda c: {}), ("_parse_cli_output", lambda out: json.loads(out))):
        monkeypatch.setattr(ex, name, value)
    written = {}

    def fake_write(job, text, start, tokens=0, duration=0.0, status="success"):
        path = results / f"{start.strftime('%Y-%m-%d')}-{job.id}.md"
        path.write_text(f"---\njob_id: {job.id}\n---\n\n## {job.name}\n\n{text}\n")
        written["id"], written["text"], written["path"] = job.id, text, path
        return path
    monkeypatch.setattr(ex, "_write_job_result", fake_write)
    out = json.dumps({"result": "Two drifts.\n<!-- RADAR_TODOS\n" + json.dumps(FINDINGS) + "\n-->"})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    return ws, proj, written


def test_the_certain_finding_is_fixed_and_the_rest_need_you(world):
    ws, proj, written = world
    res = sa.run_ddd_self_audit({})
    doc = (proj / "2-understanding" / "PROJECT.md").read_text()
    assert "now that `s_review` exists" in doc and "mid-May" in doc
    assert res["status"] == "success" and res["findings"] == 2 and res["fixed"] == 1
    assert res["summary"].startswith("Found 2 · fixed 1 · needs you 1")
    text = written["text"]
    assert "**Fixed automatically**" in text and "**Needs you**" in text
    open_line = next(ln for ln in text.splitlines() if "current focus is months old" in ln and ln.startswith("- "))
    assert "needs your judgement" in open_line and "first seen 2026-09-21" in open_line
    outcomes = json.loads(text.split("<!-- DRIFT_OUTCOMES ")[1].split(" -->")[0])
    assert [o["outcome"] for o in outcomes] == ["fixed", "open"]
    assert written["id"] == "ddd-self-audit"


def test_a_dry_run_changes_nothing_and_writes_its_own_report(world):
    ws, proj, written = world
    res = sa.run_ddd_self_audit({"dry_run": True})
    assert (proj / "2-understanding" / "PROJECT.md").read_text() == DOC
    assert not (proj / ".artifacts").exists()
    assert written["id"] == "ddd-self-audit-dryrun" and res["dry_run"] is True
    assert res["summary"].startswith("Found 2 · would fix 1 · needs you 1")
    from core.ddd_drift_signal import get_semantic_drift
    # the dry-run file is never read as a report: the latest real one is still the old one
    assert get_semantic_drift(ws)["report_date"] == "2026-09-21"


def test_the_drift_signal_counts_only_open_findings_and_no_fake_project(world):
    ws, proj, written = world
    sa.run_ddd_self_audit({})
    from core.ddd_drift_signal import get_semantic_drift
    sig = get_semantic_drift(ws)
    assert [f["title"] for f in sig["findings"]] == ["DDD drift [P/PROJECT.md]: current focus is months old"]
    assert {f["section_project"] for f in sig["findings"]} == {"P"}


def test_the_weekly_report_lists_the_fix_without_counting_it_as_a_lesson(world, monkeypatch):
    ws, proj, written = world
    sa.run_ddd_self_audit({})
    import jobs.handlers.ddd_weekly_report as wr
    applied = wr._read_changelog(proj, datetime.now(timezone.utc) - timedelta(days=7))
    fixes = wr._read_changelog(proj, datetime.now(timezone.utc) - timedelta(days=7), name="ddd-drift-fixes.jsonl")
    assert applied == [] and len(fixes) == 1
    report = wr._generate_report([], [], {"P": wr._compute_doc_health(proj)}, 7, drift_fixes=[("P", fixes[0])])
    assert "## Drift Fixed Automatically (1)" in report
    assert "now that" in report.split("## Drift Fixed Automatically (1)")[1]
