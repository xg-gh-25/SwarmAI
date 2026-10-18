"""Tests for pipeline_preflight.py — the read-only deliver-stage BLOCK aggregator.

WHAT IS TESTED
    pipeline_preflight aggregates the THREE enforcement sites that gate the
    DELIVER stage and reports, for a given run.json, which fields will BLOCK the
    stage + each field's LOCATION + present/missing. It is READ-ONLY (never
    mutates run.json, never calls any enforcement write path).

METHODOLOGY
    - AC7 terminal fork: a run MISSING a required deliver field reports it as
      missing@<location>; a complete run reports zero missing.
    - AC4 drift tripwire: the run-commit field names preflight hardcodes
      (push_ready/outputs_surfaced/files_touched) MUST actually appear as `.get()`
      string literals in artifact_cli.py — if an enforcement rename drifts the
      real literal, this test goes RED (preflight does not silently become a 4th
      drifting site).
    - AC3 read-only: preflight must not write the run.json it inspects.

KEY INVARIANTS
    - artifact-time fields are sourced LIVE from pipeline_validator.STAGE_SCHEMAS +
      STAGE_DEPTH (import, zero drift).
    - run-state fields (3) are hardcoded in preflight BUT pinned by the drift
      tripwire below.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO / "scripts" / "pipeline_preflight.py"
ARTIFACT_CLI = REPO / "scripts" / "artifact_cli.py"


def _write_run(tmp_path, stages, *, profile="bugfix", top=None):
    """Build a minimal run.json tree: Projects/<P>/.artifacts/runs/<run>/run.json."""
    run_id = "run_preflight_test"
    project = "TestProj"
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "id": run_id,
        "project": project,
        "profile": profile,
        "status": "running",
        "stages": stages,
    }
    if top:
        doc.update(top)
    (run_dir / "run.json").write_text(json.dumps(doc))
    return project, run_id, run_dir


def _run_preflight(workspace, project, run_id, stage="deliver"):
    """Invoke preflight as a subprocess; return (rc, parsed_json)."""
    proc = subprocess.run(
        [sys.executable, str(PREFLIGHT),
         "--stage", stage, "--run-id", run_id, "--project", project,
         "--workspace", str(workspace)],
        capture_output=True, text=True, timeout=30,
    )
    try:
        data = json.loads(proc.stdout)
    except Exception:
        data = {"_raw": proc.stdout, "_err": proc.stderr}
    return proc.returncode, data


def test_import_sources_are_live_not_hardcoded():
    """AC4 (artifact-time half): the deliver artifact-time field list must come
    from pipeline_validator's live dicts, not a hardcoded copy in preflight."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline_preflight", PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # preflight must expose the aggregated spec and it must match the validator's
    # live dicts for deliver (not a frozen copy).
    from scripts import pipeline_validator as pv
    agg = mod.aggregate_deliver_blockers()
    art_fields = {b["field"] for b in agg if b["location"] == "artifact.data"}
    # Every STAGE_SCHEMAS[deliver].required must be surfaced as an artifact-time blocker.
    for f in pv.STAGE_SCHEMAS["deliver"]["required"]:
        assert f in art_fields, f"artifact-time required field {f!r} not aggregated"
    for f in pv.STAGE_DEPTH["deliver"]:
        assert f in art_fields, f"STAGE_DEPTH deliver field {f!r} not aggregated"


def test_runcommit_field_names_match_real_literals():
    """AC4 drift tripwire (run-state half): the three run-commit field names
    preflight hardcodes MUST appear as string literals in artifact_cli.py. If an
    enforcement rename drifts, this goes RED — preflight never silently drifts."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline_preflight", PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = ARTIFACT_CLI.read_text()
    for b in mod.aggregate_deliver_blockers():
        if b["location"] in ("stage-record-top", "run.json-top", "completion-surface"):
            # the real enforcement must reference this field name as a literal
            assert f'"{b["field"]}"' in src or f"'{b['field']}'" in src, (
                f"run-state field {b['field']!r} not found as a literal in "
                f"artifact_cli.py — enforcement drifted, preflight is now stale"
            )


def _extract_deliver_block(pv_src):
    """Return just the `if stage == "deliver":` block body from validate_artifact_data
    — from that line to the next sibling `if stage ==` (or EOF). Scanning the WHOLE
    file is vacuous: a leaf like 'evidence' appears 16× in unrelated gates, so a
    whole-file match passes even if the deliver-block check was deleted (the HIGH
    finding this tripwire was rewritten to catch)."""
    lines = pv_src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if 'if stage == "deliver"' in ln or "if stage == 'deliver'" in ln:
            start = i
            break
    assert start is not None, "deliver block not found in pipeline_validator.py"
    block = [lines[start]]
    for ln in lines[start + 1:]:
        # stop at the next sibling `if stage ==` at the same (or shallower) indent
        if ln.strip().startswith("if stage ==") and (len(ln) - len(ln.lstrip())) <= (
                len(lines[start]) - len(lines[start].lstrip())):
            break
        block.append(ln)
    return "\n".join(block)


def test_artifact_hardcoded_field_names_match_validator_literals():
    """AC4 drift tripwire (artifact-time hardcoded half): the field names preflight
    mirrors for the validator's hardcoded `if stage=="deliver":` block MUST appear as
    literals INSIDE THAT BLOCK (not just anywhere in the file). These are the 3rd
    artifact-time source the two importable dicts do NOT cover (meta_review/convergence/
    adversarial_review.*) — omitting them was a false-clean hole. If the validator
    renames one OR deletes the deliver-block check, this goes RED.

    NON-VACUITY: scanning only the deliver block (not the whole file) is what makes
    this real — a leaf name like 'evidence' appears 16× file-wide in unrelated gates,
    so a whole-file match would pass vacuously (adversarial integration #1, HIGH)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("pipeline_preflight", PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pv_src = (REPO / "scripts" / "pipeline_validator.py").read_text()
    deliver_block = _extract_deliver_block(pv_src)
    for raw in mod._ARTIFACT_HARDCODED_FIELDS:
        leaf = raw.split(".")[-1]
        assert f'"{leaf}"' in deliver_block or f"'{leaf}'" in deliver_block, (
            f"hardcoded artifact-time field {raw!r} (leaf {leaf!r}) not found as a "
            f"literal INSIDE the validator's deliver block — the deliver-block check "
            f"drifted or was removed, preflight is now stale (false-clean risk)"
        )


def test_malformed_run_json_degrades_gracefully(tmp_path):
    """AC3/correctness#5: a half-written run.json must degrade (error field), not
    crash with a traceback — this is an advisory tool."""
    run_id = "run_preflight_test"
    project = "TestProj"
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text('{"id": "x", "stages": [')  # truncated mid-write
    rc, data = _run_preflight(tmp_path, project, run_id)
    assert "error" in data, data
    assert "JSON" in data["error"], data
    # non-dict valid-JSON top-level must ALSO degrade, not AttributeError-crash
    (run_dir / "run.json").write_text("null")
    rc2, data2 = _run_preflight(tmp_path, project, run_id)
    assert "error" in data2, data2
    (run_dir / "run.json").write_text("[]")
    rc3, data3 = _run_preflight(tmp_path, project, run_id)
    assert "error" in data3, data3


def test_malformed_run_json_truncated(tmp_path):
    """Original truncated-JSON graceful degrade (kept distinct from non-dict case)."""
    run_id = "run_preflight_test"
    project = "TestProj"
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text('{"id": "x", "stages": [')  # truncated mid-write
    rc, data = _run_preflight(tmp_path, project, run_id)
    assert "error" in data, data
    assert "valid JSON" in data["error"] or "JSON" in data["error"], data
    # must NOT be a crash dump
    assert "_raw" not in data or data.get("error"), data


def test_required_experts_surfaces_d7_note(tmp_path):
    """correctness#1/integration#2: a declared required_experts must surface a D7
    obligation note (presence ≠ satisfied) so the agent sees it before run-commit."""
    stages = [
        {"stage": "deliver", "status": "completed",
         "push_ready": True, "outputs_surfaced": True},
    ]
    project, run_id, _ = _write_run(
        tmp_path, stages, top={"files_touched": ["a.py"],
                               "required_experts": ["correctness", "integration"]})
    rc, data = _run_preflight(tmp_path, project, run_id)
    notes_blob = " ".join(data.get("notes", []))
    assert "D7" in notes_blob and "correctness" in notes_blob, data
    # required_experts must be present as a blocker (not silently omitted)
    fields = {b["field"] for b in data.get("blockers", [])}
    assert "required_experts" in fields, data


def test_missing_fields_reported(tmp_path):
    """AC2/AC7: a run missing push_ready + files_touched reports them missing."""
    # deliver stage record present but WITHOUT push_ready; no top-level files_touched
    stages = [
        {"stage": "evaluate", "status": "completed"},
        {"stage": "deliver", "status": "recorded"},  # no push_ready
    ]
    project, run_id, _ = _write_run(tmp_path, stages)
    rc, data = _run_preflight(tmp_path, project, run_id)
    missing = {m["field"] for m in data.get("missing", [])}
    assert "push_ready" in missing, data
    assert "files_touched" in missing, data


def test_complete_run_reports_clean(tmp_path):
    """AC2/AC7 (other fork): a run with all run-state fields present reports them
    satisfied (no run-state field in missing)."""
    stages = [
        {"stage": "evaluate", "status": "completed"},
        {"stage": "deliver", "status": "completed",
         "push_ready": True, "outputs_surfaced": True},
    ]
    project, run_id, _ = _write_run(
        tmp_path, stages, top={"files_touched": ["a.py"]})
    rc, data = _run_preflight(tmp_path, project, run_id)
    missing = {m["field"] for m in data.get("missing", [])}
    assert "push_ready" not in missing, data
    assert "files_touched" not in missing, data
    assert "outputs_surfaced" not in missing, data


def test_readonly_does_not_mutate_run_json(tmp_path):
    """AC3: preflight must not modify the run.json it inspects."""
    stages = [{"stage": "deliver", "status": "recorded"}]
    project, run_id, run_dir = _write_run(tmp_path, stages)
    before = (run_dir / "run.json").read_text()
    _run_preflight(tmp_path, project, run_id)
    after = (run_dir / "run.json").read_text()
    assert before == after, "preflight mutated run.json — must be read-only"


def test_path_traversal_refused(tmp_path):
    """#1 (run_22321ff4): a crafted --project/--run-id that escapes the workspace
    must be REFUSED (error, not a read) — containment via resolve()-within-workspace.
    A real run.json is planted OUTSIDE the workspace to prove it is never read."""
    # workspace is tmp_path/ws; plant a run.json OUTSIDE it, then try to reach it via
    # ../ traversal in --project. run_path = ws/Projects/<project>/.artifacts/runs/<run_id>/run.json,
    # so to escape ws the project must climb above ws/Projects (../.. gets to tmp_path).
    ws = tmp_path / "ws"
    (ws / "Projects").mkdir(parents=True)
    # a secret file sibling of ws, reachable only by escaping the workspace
    secret_dir = tmp_path / "secret" / "runs" / "r"
    secret_dir.mkdir(parents=True)
    (secret_dir / "run.json").write_text('{"id":"r","stages":[],"profile":"bugfix"}')
    # --project "../../secret" + run_id "r" with the .artifacts/runs path collapses to
    # ws/Projects/../../secret/.artifacts/runs/r/... — escaping ws. (The exact collapse
    # need not hit the secret file; the point is resolve() lands outside ws → refuse.)
    rc, data = _run_preflight(ws, "../../secret", "r")
    assert "error" in data, data
    assert "outside" in data["error"].lower() or "workspace" in data["error"].lower(), data


def test_legitimate_path_not_rejected(tmp_path):
    """#1 regression guard: a NORMAL in-workspace path must still work (resolve()
    containment must not reject the happy path)."""
    stages = [{"stage": "deliver", "status": "completed",
               "push_ready": True, "outputs_surfaced": True}]
    project, run_id, _ = _write_run(tmp_path, stages, top={"files_touched": ["a.py"]})
    rc, data = _run_preflight(tmp_path, project, run_id)
    assert "error" not in data, data
    assert "blockers" in data, data


def test_required_experts_lowercased_on_add(tmp_path):
    """#3 (run_22321ff4): add_required_expert normalizes to lowercase so stored
    required_experts matches the D7 comparison side, and "Correctness"/"correctness"
    dedupe as one entry (not a cosmetic duplicate)."""
    import os
    ARTIFACT_CLI = REPO / "scripts" / "artifact_cli.py"
    run_id = "run_lc_test"
    project = "TestProj"
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps(
        {"id": run_id, "project": project, "profile": "bugfix", "status": "running",
         "stages": [{"stage": "evaluate", "status": "completed"}]}))
    # run-update locates run.json via SWARM_WORKSPACE env (artifact_cli._get_workspace),
    # not a --workspace flag.
    env = dict(os.environ, SWARM_WORKSPACE=str(tmp_path), PYTHONIOENCODING="utf-8")

    def _add(name):
        return subprocess.run(
            [sys.executable, str(ARTIFACT_CLI), "run-update",
             "--project", project, "--run-id", run_id,
             "--add-required-expert", name],
            capture_output=True, text=True, timeout=30, env=env)

    # mixed-case add → stored lowercase
    _add("Correctness")
    doc = json.loads((run_dir / "run.json").read_text())
    re_list = doc.get("required_experts", [])
    assert "correctness" in re_list, re_list
    assert "Correctness" not in re_list, re_list
    # re-add in a different case → no-op (dedupe), not a duplicate
    _add("correctness")
    doc2 = json.loads((run_dir / "run.json").read_text())
    assert doc2.get("required_experts", []).count("correctness") == 1, doc2.get("required_experts")


def test_outputs_surfaced_is_not_a_deliver_record_obligation(tmp_path):
    """run_a87bfe3f: the completion surface gate reads the daemon marker or the
    run-level --outputs-surfaced flag, and the docs forbid re-recording DELIVER to
    add it. Preflight runs at DELIVER, before anything is surfaced, so listing the
    field as missing there pushed agents toward the DELIVER --stage-json rewrite that
    wiped the record. It must be advisory (present=None, never in missing), and the
    notes must name the completion-time path instead."""
    stages = [
        {"stage": "evaluate", "status": "completed"},
        {"stage": "deliver", "status": "completed", "push_ready": True},
    ]
    project, run_id, _ = _write_run(tmp_path, stages, top={"files_touched": ["a.py"]})
    rc, data = _run_preflight(tmp_path, project, run_id)
    missing = {m["field"] for m in data.get("missing", [])}
    assert "outputs_surfaced" not in missing, data
    entry = [b for b in data["blockers"] if b["field"] == "outputs_surfaced"]
    assert entry and entry[0]["present"] is None, data
    notes_blob = " ".join(data.get("notes", []))
    assert "surface_run_outputs" in notes_blob and "--outputs-surfaced" in notes_blob, data
    assert "--stage-json" not in notes_blob, data
