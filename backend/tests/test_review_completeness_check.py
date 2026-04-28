"""Tests for pipeline_validator check 8d — review completeness on large changesets.

Verifies that large changesets (>3 code files or >10 tests) without proper
review findings are flagged. Prevents the orchestrator from rubber-stamping
reviews on substantial diffs.
"""

import json
import pytest
from pathlib import Path


def _make_run(stages, profile="bugfix"):
    return {
        "id": "run_test",
        "project": "TestProj",
        "profile": profile,
        "status": "running",
        "stages": stages,
    }


def _make_build_artifact(code_files, tests_generated=0):
    return {
        "files_changed": code_files,
        "tdd": {"tests_generated": tests_generated, "smoke_tests": 1},
    }


def _make_review_artifact(findings_count=None, integration_trace_checked=1):
    art = {"integration_trace": {"checked": integration_trace_checked}}
    if findings_count is not None:
        art["findings_count"] = findings_count
    return art


@pytest.fixture()
def run_dir(tmp_path):
    """Set up a minimal project + run directory structure."""
    proj = tmp_path / "Projects" / "TestProj"
    (proj / ".artifacts" / "runs" / "run_test").mkdir(parents=True)
    # Minimal DDD docs to avoid validator complaints
    for doc in ("PRODUCT.md", "TECH.md", "IMPROVEMENT.md", "PROJECT.md"):
        (proj / doc).write_text(f"# TestProj — {doc}\n\nPlaceholder\n")
    return proj


def _write_run_and_artifacts(run_dir, run_data, build_art=None, review_art=None):
    """Write run.json, artifact files, and manifest.json."""
    run_path = run_dir / ".artifacts" / "runs" / "run_test" / "run.json"
    run_path.write_text(json.dumps(run_data))

    manifest_entries = []

    if build_art is not None:
        rel_path = "runs/run_test/art_build.json"
        art_path = run_dir / ".artifacts" / rel_path
        art_path.write_text(json.dumps(build_art))
        manifest_entries.append({"id": "art_build", "file": rel_path})

    if review_art is not None:
        rel_path = "runs/run_test/art_review.json"
        art_path = run_dir / ".artifacts" / rel_path
        art_path.write_text(json.dumps(review_art))
        manifest_entries.append({"id": "art_review", "file": rel_path})

    # Write manifest.json so _load_artifact_data() can find artifacts
    manifest_path = run_dir / ".artifacts" / "manifest.json"
    manifest_path.write_text(json.dumps({"artifacts": manifest_entries}))


def test_large_changeset_no_findings_count_blocks(run_dir, monkeypatch):
    """8d BLOCK: large changeset + review artifact missing findings_count field."""
    import scripts.pipeline_validator as pv

    # Monkeypatch workspace root
    monkeypatch.setenv("SWARM_WORKSPACE", str(run_dir.parent.parent))

    build_art = _make_build_artifact(
        ["hooks/a.py", "hooks/b.py", "hooks/c.py", "hooks/d.py"],
        tests_generated=17,
    )
    # Review artifact WITHOUT findings_count
    review_art = _make_review_artifact(findings_count=None, integration_trace_checked=3)

    stages = [
        {"name": "evaluate", "status": "completed", "artifact_id": "art_eval"},
        {"name": "plan", "status": "completed", "artifact_id": "art_plan"},
        {"name": "build", "status": "completed", "artifact_id": "art_build"},
        {"name": "review", "status": "completed", "artifact_id": "art_review"},
    ]
    run_data = _make_run(stages)
    _write_run_and_artifacts(run_dir, run_data, build_art, review_art)

    result = pv.validate("TestProj", "run_test", "review")
    # Should have a BLOCK error about missing findings_count
    assert not result["valid"], f"Expected invalid but got: {result}"
    assert any("findings_count" in e for e in result["errors"]), \
        f"Expected findings_count error, got: {result['errors']}"


def test_large_changeset_zero_findings_warns(run_dir, monkeypatch):
    """8d WARN: large changeset + review reports 0 findings — suspicious."""
    import scripts.pipeline_validator as pv

    monkeypatch.setenv("SWARM_WORKSPACE", str(run_dir.parent.parent))

    build_art = _make_build_artifact(
        ["hooks/a.py", "hooks/b.py", "hooks/c.py", "hooks/d.py"],
        tests_generated=17,
    )
    review_art = _make_review_artifact(findings_count=0, integration_trace_checked=3)

    stages = [
        {"name": "evaluate", "status": "completed", "artifact_id": "art_eval"},
        {"name": "plan", "status": "completed", "artifact_id": "art_plan"},
        {"name": "build", "status": "completed", "artifact_id": "art_build"},
        {"name": "review", "status": "completed", "artifact_id": "art_review"},
    ]
    run_data = _make_run(stages)
    _write_run_and_artifacts(run_dir, run_data, build_art, review_art)

    result = pv.validate("TestProj", "run_test", "review")
    # Should warn but not block
    assert any("0 findings" in w for w in result["warnings"]), \
        f"Expected 0-findings warning, got: {result['warnings']}"


def test_large_changeset_no_review_artifact_blocks(run_dir, monkeypatch):
    """8d BLOCK: large changeset but REVIEW has no artifact at all — review skipped."""
    import scripts.pipeline_validator as pv

    monkeypatch.setenv("SWARM_WORKSPACE", str(run_dir.parent.parent))

    build_art = _make_build_artifact(
        ["hooks/a.py", "hooks/b.py", "hooks/c.py", "hooks/d.py"],
        tests_generated=17,
    )

    stages = [
        {"name": "evaluate", "status": "completed", "artifact_id": "art_eval"},
        {"name": "plan", "status": "completed", "artifact_id": "art_plan"},
        {"name": "build", "status": "completed", "artifact_id": "art_build"},
        {"name": "review", "status": "completed"},  # NO artifact_id
    ]
    run_data = _make_run(stages)
    _write_run_and_artifacts(run_dir, run_data, build_art, None)

    result = pv.validate("TestProj", "run_test", "review")
    assert not result["valid"], f"Expected invalid: {result}"
    assert any("skipped entirely" in e for e in result["errors"]), \
        f"Expected 'skipped' error, got: {result['errors']}"


def test_small_changeset_no_findings_ok(run_dir, monkeypatch):
    """8d: small changeset (≤3 code files, ≤10 tests) — no completeness check needed."""
    import scripts.pipeline_validator as pv

    monkeypatch.setenv("SWARM_WORKSPACE", str(run_dir.parent.parent))

    build_art = _make_build_artifact(["hooks/a.py"], tests_generated=3)
    review_art = _make_review_artifact(findings_count=None, integration_trace_checked=1)

    stages = [
        {"name": "evaluate", "status": "completed", "artifact_id": "art_eval"},
        {"name": "plan", "status": "completed", "artifact_id": "art_plan"},
        {"name": "build", "status": "completed", "artifact_id": "art_build"},
        {"name": "review", "status": "completed", "artifact_id": "art_review"},
    ]
    run_data = _make_run(stages)
    _write_run_and_artifacts(run_dir, run_data, build_art, review_art)

    result = pv.validate("TestProj", "run_test", "review")
    # Small changeset — no completeness check fires
    completeness_errors = [e for e in result["errors"] if "findings_count" in e or "skipped" in e]
    assert len(completeness_errors) == 0, f"Unexpected completeness error: {completeness_errors}"


def test_large_changeset_with_findings_passes(run_dir, monkeypatch):
    """8d: large changeset with proper findings_count > 0 — all good."""
    import scripts.pipeline_validator as pv

    monkeypatch.setenv("SWARM_WORKSPACE", str(run_dir.parent.parent))

    build_art = _make_build_artifact(
        ["hooks/a.py", "hooks/b.py", "hooks/c.py", "hooks/d.py", "hooks/e.py"],
        tests_generated=20,
    )
    review_art = _make_review_artifact(findings_count=3, integration_trace_checked=5)

    stages = [
        {"name": "evaluate", "status": "completed", "artifact_id": "art_eval"},
        {"name": "plan", "status": "completed", "artifact_id": "art_plan"},
        {"name": "build", "status": "completed", "artifact_id": "art_build"},
        {"name": "review", "status": "completed", "artifact_id": "art_review"},
    ]
    run_data = _make_run(stages)
    _write_run_and_artifacts(run_dir, run_data, build_art, review_art)

    result = pv.validate("TestProj", "run_test", "review")
    completeness_errors = [e for e in result["errors"] if "findings_count" in e or "skipped" in e]
    completeness_warnings = [w for w in result["warnings"] if "0 findings" in w]
    assert len(completeness_errors) == 0, f"Unexpected error: {completeness_errors}"
    assert len(completeness_warnings) == 0, f"Unexpected warning: {completeness_warnings}"
