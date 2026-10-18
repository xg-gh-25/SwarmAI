#!/usr/bin/env python3
"""pipeline_preflight.py — READ-ONLY deliver-stage BLOCK aggregator.

WHY THIS EXISTS
    The fields that gate the DELIVER stage are scattered across THREE enforcement
    sites with no single read-only view telling the agent "what will block this
    stage + where each field must live". The agent hits publish/commit rejections
    one field at a time (ratchet-style trial-and-error). This script aggregates
    the three sites and reports, for a given run.json, the full BLOCK-field set +
    each field's LOCATION + present/missing — in ONE read, before publish.

    It is READ-ONLY: it never mutates run.json, never calls any enforcement write
    path. It does NOT make a block/pass verdict authoritative — the three real
    enforcement sites remain the authority; preflight only reports what they will
    check, so a drift at worst UNDER-reports (misses a newly-added field), never
    falsely BLOCKs.

THE THREE ENFORCEMENT SITES (deliver)
    1. artifact-time (pipeline_validator.validate_artifact_data, stage==deliver):
       required fields come from TWO places — (a) the importable dicts
       STAGE_SCHEMAS["deliver"] + STAGE_DEPTH["deliver"] (SOURCED LIVE, zero drift),
       AND (b) a hardcoded `if stage == "deliver":` block that independently BLOCKS
       on meta_review / convergence / adversarial_review.{evidence,spawned} with NO
       importable table — those NAMES are mirrored in _ARTIFACT_HARDCODED_FIELDS and
       pinned by the drift-tripwire test. Both are reported.
    2. run-state (artifact_cli.py run-commit): push_ready (deliver stage-record
       top), files_touched (run.json top), outputs_surfaced (completion-time
       evidence, advisory here — see _RUNSTATE_FIELDS).
       These are hardcoded `.get()` literals in procedural code with NO importable
       table — preflight mirrors the three NAMES here and a drift-tripwire test
       (test_pipeline_preflight.py::test_runcommit_field_names_match_real_literals)
       asserts each name still appears as a literal in artifact_cli.py, so a
       rename goes RED instead of silently drifting.
    3. completion (artifact_cli.py run-update --status completed): all profile
       stages done + cross_boundary E2E (reported qualitatively, not per-field).

OUTPUT (JSON on stdout)
    {"stage","run_id","blockers":[{field,location,present}],
     "missing":[{field,location}], "clean": bool, "notes":[...]}
"""
import argparse
import json
import sys
from pathlib import Path

# artifact-time field source — LIVE import, zero drift (AC4 half 1)
_BACKEND = Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from scripts import pipeline_validator as _pv  # noqa: E402

# ── run-state field names (AC4 half 2) ────────────────────────────────────────
# These are hardcoded `.get()` literals in artifact_cli.py's run-commit /
# completion path with NO importable table. Mirrored here and PINNED by the
# drift-tripwire test — a rename in artifact_cli.py turns that test RED.
#   push_ready        → deliver stage RECORD top-level (artifact_cli.py run-commit)
#   files_touched     → run.json top-level            (artifact_cli.py run-commit)
#   outputs_surfaced  → COMPLETION-time evidence      (artifact_cli.py run-UPDATE
#                       --status completed gate — NOT run-commit, NOT the deliver
#                       record). The gate accepts the daemon marker written by the
#                       surface_run_outputs tool (core.surface_marker, beside
#                       run.json), the run-level --outputs-surfaced flag, or a legacy
#                       deliver-record flag. All of these appear AFTER run-commit, so
#                       at DELIVER preflight it is advisory only (present=None, never
#                       "missing"): reporting it missing here is what pushed agents to
#                       re-record DELIVER with --stage-json, which wiped the record.
#   required_experts  → run.json top-level            (artifact_cli.py run-commit
#                       D7 gate _d7_missing_required_experts; a non-empty declared
#                       set BLOCKS unless every expert reviewed — presence here means
#                       "declared", NOT "satisfied"; preflight flags it so the agent
#                       sees the D7 obligation BEFORE commit)
_RUNSTATE_FIELDS = [
    {"field": "push_ready", "location": "stage-record-top", "stage": "deliver"},
    {"field": "files_touched", "location": "run.json-top", "stage": None},
    {"field": "outputs_surfaced", "location": "completion-surface", "stage": None},
    {"field": "required_experts", "location": "run.json-top", "stage": None},
]

# ── artifact-time fields the TWO dicts do NOT cover (AC4) ──────────────────────
# validate_artifact_data has a hardcoded `if stage == "deliver":` block that
# independently BLOCKS on these for full/bugfix profiles — a THIRD artifact-time
# source beyond STAGE_SCHEMAS + STAGE_DEPTH. No importable table exists for them,
# so the NAMES are mirrored here and PINNED by the drift-tripwire test (asserts
# each appears as a literal in pipeline_validator.py). Omitting them was a
# false-clean hole (adversarial correctness #1 / integration #2).
#   meta_review                     → data.get("meta_review") (or meta_review_verdict)
#   convergence                     → data.get("convergence").all_pass / push-ready
#   adversarial_review.evidence     → Rule-23 spawned=true requires evidence
#   adversarial_review.spawned      → sub-agent must actually have run
_ARTIFACT_HARDCODED_FIELDS = [
    "meta_review",
    "convergence",
    "adversarial_review.evidence",
    "adversarial_review.spawned",
]


def aggregate_deliver_blockers():
    """Return the full ordered list of fields that gate the deliver stage, each
    tagged with its LOCATION. Pure data — no run.json needed. Used by both the
    report path and the drift-tripwire test."""
    blockers = []
    seen = set()

    def _add(field, location):
        key = (field, location)
        if key not in seen:
            seen.add(key)
            blockers.append({"field": field, "location": location})

    # 1a. artifact-time (live from the validator dicts — zero drift)
    for f in _pv.STAGE_SCHEMAS.get("deliver", {}).get("required", []):
        _add(f, "artifact.data")
    for f in _pv.STAGE_DEPTH.get("deliver", {}):
        _add(f, "artifact.data")
    # 1b. artifact-time (hardcoded `if stage == "deliver":` block in
    #     validate_artifact_data — a THIRD source the two dicts do NOT cover.
    #     These field NAMES are mirrored (no importable table) and PINNED by the
    #     drift-tripwire test against pipeline_validator.py literals. Omitting
    #     them would make preflight report clean:true while the deliver validator
    #     still BLOCKs on publish — a false-clean (the one direction that lulls).
    for f in _ARTIFACT_HARDCODED_FIELDS:
        _add(f, "artifact.data")
    # 2. run-state (mirrored names, pinned by drift tripwire)
    for rs in _RUNSTATE_FIELDS:
        blockers.append({"field": rs["field"], "location": rs["location"]})
    return blockers


def _deliver_stage_record(run_doc):
    for s in run_doc.get("stages", []):
        if s.get("stage") == "deliver":
            return s
    return None


def _field_present(run_doc, blocker):
    """Is this blocker's field present/truthy at its location in the run.json?

    artifact.data fields are published into the artifact (not run.json), so
    preflight cannot see them from run.json alone — it reports them as
    'artifact-time (checked at publish)' rather than missing. The run-state
    fields ARE visible in run.json and are the ratchet-trap this tool targets."""
    loc = blocker["location"]
    field = blocker["field"]
    if loc == "artifact.data":
        return None  # not visible from run.json — validated at publish time
    if field == "required_experts":
        # D7 gate: a NON-EMPTY declared set BLOCKS run-commit unless every expert
        # reviewed. "Satisfied" (declared ⊆ reviewed) is NOT decidable from run.json
        # alone (needs the adversarial markers), so presence here is advisory-only:
        # return None (not a present/missing claim) — the report NOTE carries the
        # D7 obligation. An EMPTY/absent set means D7 does not gate (nothing declared).
        return None
    if loc == "completion-surface":
        # Produced after run-commit (see the outputs_surfaced comment above), so it is
        # not decidable at DELIVER: advisory, like required_experts. The note carries
        # the path.
        return None
    if loc == "run.json-top":
        return bool(run_doc.get(field))
    if loc == "stage-record-top":
        rec = _deliver_stage_record(run_doc)
        return bool(rec and rec.get(field))
    return None


def run_preflight(stage, project, run_id, workspace):
    if stage != "deliver":
        return {"stage": stage, "run_id": run_id,
                "notes": [f"preflight currently models the 'deliver' stage only; "
                          f"'{stage}' not modeled"],
                "blockers": [], "missing": [], "clean": None}

    run_path = (Path(workspace) / "Projects" / project / ".artifacts"
                / "runs" / run_id / "run.json")
    # Containment: --project / --run-id are external inputs concatenated into a path.
    # Resolve BOTH sides (so a legitimately symlinked workspace root is not wrongly
    # rejected) and refuse anything that escapes the workspace — BEFORE touching the
    # filesystem. Return error (not raise): this is an advisory tool (exit 0 always).
    _ws = Path(workspace).resolve()
    if not run_path.resolve().is_relative_to(_ws):
        return {"stage": stage, "run_id": run_id,
                "error": f"resolved run.json path is outside the workspace "
                         f"({_ws}); refusing to read: {run_path}"}
    if not run_path.is_file():
        return {"stage": stage, "run_id": run_id, "error": f"run.json not found: {run_path}"}

    try:
        run_doc = json.loads(run_path.read_text())  # READ-ONLY
    except (json.JSONDecodeError, ValueError) as e:
        # A half-written run.json must degrade gracefully, not crash — this is an
        # advisory tool (adversarial correctness #5). Mirror the missing-file shape.
        return {"stage": stage, "run_id": run_id,
                "error": f"run.json is not valid JSON ({e}); cannot preflight — "
                         f"the run may be mid-write, retry shortly: {run_path}"}
    if not isinstance(run_doc, dict):
        # A syntactically-valid but non-dict top-level (null / [] / "x") parses fine
        # but would AttributeError on run_doc.get(...) downstream — degrade, don't
        # crash (adversarial correctness #3, 2nd-pass).
        return {"stage": stage, "run_id": run_id,
                "error": f"run.json top-level is {type(run_doc).__name__}, not an "
                         f"object; cannot preflight: {run_path}"}
    blockers = aggregate_deliver_blockers()

    report_blockers, missing = [], []
    for b in blockers:
        present = _field_present(run_doc, b)
        entry = {"field": b["field"], "location": b["location"], "present": present}
        report_blockers.append(entry)
        # Only run-state fields are checkable from run.json; a False → missing.
        if present is False:
            missing.append({"field": b["field"], "location": b["location"]})

    # completion-layer note (qualitative; not per-field)
    profile = run_doc.get("profile") or "bugfix"
    notes = [
        "artifact.data fields are validated at PUBLISH time (not visible in run.json "
        "pre-publish) — ensure they are in the --data payload.",
        f"completion gate (run-update --status completed) additionally requires ALL "
        f"'{profile}' profile stages done + cross_boundary E2E if flagged.",
    ]
    notes.append(
        "completion surface gate (run-update --status completed): after run-commit, "
        "call the surface_run_outputs tool for this run (it records the evidence); "
        "without that tool, add --outputs-surfaced to the completing run-update. "
        "Do not re-record the deliver stage to add it."
    )
    # D7 obligation note — surfaced whenever a non-empty required_experts is declared
    # (presence ≠ satisfied; satisfaction needs the adversarial markers, not visible here).
    _declared = run_doc.get("required_experts")
    if isinstance(_declared, list) and _declared:
        notes.append(
            f"D7 gate: run declared required_experts {sorted(_declared)} — run-commit "
            f"BLOCKS unless EACH ran an adversarial review this run. Dispatch the full "
            f"set in ONE parallel batch (deliver.md Step 1 union rule)."
        )

    return {
        "stage": stage,
        "run_id": run_id,
        "project": project,
        "blockers": report_blockers,
        "missing": missing,
        "clean": len(missing) == 0,
        "notes": notes,
    }


def main():
    ap = argparse.ArgumentParser(description="Read-only deliver-stage BLOCK aggregator")
    ap.add_argument("--stage", required=True, help="pipeline stage (currently: deliver)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--workspace", default=str(Path.home() / ".swarm-ai" / "SwarmWS"),
                    help="SwarmWS workspace root (default: ~/.swarm-ai/SwarmWS)")
    args = ap.parse_args()
    report = run_preflight(args.stage, args.project, args.run_id, args.workspace)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    # exit 0 always — this is an advisory report, not a gate (never blocks a workflow).
    return 0


if __name__ == "__main__":
    sys.exit(main())
