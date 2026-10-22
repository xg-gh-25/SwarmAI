#!/usr/bin/env python3
"""CLI for artifact registry and pipeline run operations.

Called by the agent via bash to discover/publish artifacts and manage
pipeline runs.  Follows the same pattern as ``locked_write.py`` —
a standalone script with no FastAPI dependency.

Usage — Artifact Registry:
    python artifact_cli.py discover --project SwarmAI --types research,alternatives [--full]
    python artifact_cli.py publish --project SwarmAI --type evaluation \\
        --producer s_evaluate --summary "GO" --data '{"roi": 3.2}' [--run-id run_xxx]
    python artifact_cli.py state --project SwarmAI
    python artifact_cli.py advance --project SwarmAI --state think
    python artifact_cli.py learn --project SwarmAI --evaluation-id art_xxx --outcome success
    python artifact_cli.py projects

Usage — Pipeline Runs (stored in .artifacts/runs/<run_id>/):
    python artifact_cli.py run-create --project SwarmAI --requirement "Add feature" [--profile full]
    python artifact_cli.py run-update --project SwarmAI --run-id run_xxx [--stage-json '...'] [--status completed]
    python artifact_cli.py run-get --project SwarmAI [--run-id run_xxx]
    python artifact_cli.py run-budget --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-checkpoint --project SwarmAI --run-id run_xxx --stage build --reason "L2 BLOCK"
    python artifact_cli.py run-history --project SwarmAI [--limit 10]
    python artifact_cli.py run-status [--active-only]
    python artifact_cli.py run-resume --project SwarmAI --run-id run_xxx

Usage — Stage boundaries (one call in, one call out per stage):
    python artifact_cli.py stage-start --project SwarmAI --run-id run_xxx --stage build
    python artifact_cli.py stage-complete --project SwarmAI --run-id run_xxx --stage build \\
        --data @/tmp/run_xxx/build.json [--check]

Usage — Pipeline Metrics:
    python artifact_cli.py run-metrics --project SwarmAI --run-id run_xxx
    python artifact_cli.py run-analytics --project SwarmAI [--limit 50]

Storage layout:
    Projects/<project>/.artifacts/
        manifest.json                   # global artifact index
        <type>-<date>-<topic>.json      # standalone artifacts (no pipeline)
        runs/
            <run_id>/
                run.json                # pipeline run state
                METRICS.json            # extracted metrics (auto on completion)
                <type>-<date>.json      # artifacts scoped to this run

Public symbols:
- ``main``  — CLI entry point with subcommand dispatch.
- ``cmd_stage_start`` / ``cmd_stage_complete`` — the stage-boundary commands.
  stage-complete writes run.json ONCE (atomic) after a stub-free publish; a write
  failure prints one ``{"ok":false,"code":"write_failed",...}`` line.
- ``_deliver_push_ready_bound`` — the DELIVER push_ready upper bound (all runner
  evidence, freshness vs files_touched, exit contract); shared by stage-complete
  and the legacy ``run-update --stage-json`` path, which may only LOWER push_ready.
- ``_profile_change_error`` — profile changes after EVALUATE may not drop a stage
  of the current profile that has not completed yet.
"""

import argparse
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent directory to path so we can import core modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.artifact_registry import ArtifactRegistry
from core.pipeline_profiles import get_profile_stages, normalize_profile

# Sibling module in backend/scripts/ — pure completion commit+surface gate logic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from completion_gate import completion_surface_verdict
from core.surface_marker import MARKER_NAME as SURFACE_MARKER_NAME, marker_covers


# §9 "no lessons" placeholder — the string cmd_run_report emits when the reflect
# record carries no lessons. Renderer-only: §9 lives in ledger A and is not part of
# the committed review deliverable B (_B_REVIEW_SECTION_WHITELIST), because REFLECT
# writes the lessons after DELIVER has landed the change.
_NO_LESSONS_PLACEHOLDER = "(REFLECT stage did not record lessons for this run)"


def _get_workspace() -> Path:
    """Resolve workspace root from environment or default."""
    import os
    from config import get_app_data_dir
    ws = os.environ.get("SWARM_WORKSPACE", str(get_app_data_dir() / "SwarmWS"))
    return Path(ws).expanduser().resolve()


def cmd_discover(args, reg: ArtifactRegistry) -> None:
    """Discover active artifacts of given types."""
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    artifacts = reg.discover(args.project, *types)

    if not artifacts:
        print(json.dumps({"artifacts": [], "count": 0}))
        return

    result = []
    for a in artifacts:
        entry = {
            "id": a.id,
            "type": a.type,
            "producer": a.producer,
            "summary": a.summary,
            "file": a.file,
        }
        # Optionally load full data
        if args.full:
            artifact_dir = (
                _get_workspace() / "Projects" / args.project / ".artifacts"
            )
            data_file = artifact_dir / a.file
            if data_file.exists():
                try:
                    entry["data"] = json.loads(
                        data_file.read_text(encoding="utf-8")
                    )
                except (json.JSONDecodeError, OSError):
                    pass
        result.append(entry)

    print(json.dumps({"artifacts": result, "count": len(result)}, indent=2))


# NOTE (run_f3975b8b): the `_find_active_run` / `_find_active_runs` helpers were
# DELETED. They existed only to pick "the newest active run" when a caller omitted
# --run-id — i.e. to GUESS which run a publish/validate targets. That guess is the
# root of all cross-run contamination (run_3caef1d3 / run_b9ecb07a): even a single
# active run may belong to a sibling session. The contamination is now eradicated
# structurally — every write/validate path REQUIRES an explicit --run-id and fails
# closed (publish) or skips (advance) when it is absent — so there is no longer any
# code path that needs to enumerate "active runs" to guess a target. Do not
# reintroduce a newest-active selector for a mutate/validate path.


def _as_list(v) -> list:
    """Coerce an agent-supplied stage-json field into a list safe to slice/iterate.

    cmd_run_report reads several fields (alternatives, key_findings, eval_criteria,
    success_criteria) straight from raw stage-json — which agents populate freely
    and which bypasses validate_artifact_data's type checks. A field recorded as a
    truthy scalar (e.g. ``alternatives: 3``) crashed report generation at
    ``alternatives[:4]`` with ``TypeError: 'int' object is not subscriptable``
    (run_932c0991). This guards at the consumer — the layer where the crash occurs.

    - list → returned unchanged
    - non-empty str → ``[str]`` (a single render item; matches the prior
      success_criteria str-guard)
    - anything else (int, dict, None, empty str, tuple, …) → ``[]`` (skip the
      section rather than crash; a scalar in a list-field is malformed by
      definition and its section is purely descriptive)
    """
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v:
        return [v]
    return []


def _append_stage_to_run(
    project: str, run_id: str, stage_record: dict, reg: ArtifactRegistry
) -> None:
    """Append a stage record to run.json (same as run-update --stage-json)."""
    run_file = (
        Path(reg.workspace_root) / "Projects" / project
        / ".artifacts" / "runs" / run_id / "run.json"
    )
    if not run_file.exists():
        return
    data = json.loads(run_file.read_text())
    stages = data.get("stages", [])
    # If the stage already has a record (e.g. Gate-1 wrote a `build` record with
    # gate1_verdict BEFORE publish ran), make the existing record reflect the
    # LATEST published artifact. Publish is the SOLE writer of a stage's
    # artifact_id (grep: every other `stage.artifact_id` write —
    # _CARRY_FORWARD_FIELDS at cmd_run_update, and the test/deliver metrics
    # backfills — is if-absent only and never originates a competing id), so the
    # just-published artifact IS the current one and the stage must follow it.
    # Two facets, both handled by the `!= _incoming_aid` condition below:
    #   • BACK-FILL (run_b7620c6e): existing record has NO artifact_id (gate-1
    #     wrote it first) → link it. (`None != art_x` is True.) Skipping entirely
    #     (the original behavior) left the stage permanently unlinked.
    #   • RE-POINT (run_59e9e36a / run_05b42b8b): existing record has the FIRST
    #     publish's id and the SAME stage is published again → update to the new
    #     id. The interim back-fill-ONLY guard (`not _existing.get(...)`) fixed
    #     back-fill but pinned re-publish to the stale id, so the validator read
    #     acceptance-criteria from the OLD artifact → false "AC not covered"
    #     (~4 manual re-record cycles, IMPROVEMENT.md:1685). Re-pointing is safe
    #     precisely because publish is the sole writer — there is no "explicit
    #     value from elsewhere" to clobber. A same-id re-record is a no-op.
    _existing = next((s for s in stages if s.get("stage") == stage_record["stage"]), None)
    if _existing is not None:
        _incoming_aid = stage_record.get("artifact_id")
        if _incoming_aid and _existing.get("artifact_id") != _incoming_aid:
            _existing["artifact_id"] = _incoming_aid
            data["stages"] = stages
            data["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_run_atomic(run_file, data)
        return
    stages.append(stage_record)
    data["stages"] = stages
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    # Match the formatting of every other run.json writer (cmd_run_update,
    # cmd_run_create, etc.): indent=2, default ensure_ascii, utf-8. Avoids the
    # same file flipping between raw-UTF8 and \uXXXX escapes depending on which
    # command wrote last (noisy diffs in .artifacts/runs/*/run.json).
    _write_run_atomic(run_file, data)


def _read_json_arg(raw: str, argname: str) -> str:
    """Resolve a CLI JSON argument's RAW-STRING source — @path / @- / inline.

    Shared by ``publish --data`` and ``run-update --stage-json`` so both read a
    file identically (run_b7620c6e introduced @file for --data only; this unifies
    it — R25 de-dup / P8 one entry rule). ``@PATH`` reads the file, ``@-`` reads
    stdin, anything else is returned verbatim (a JSON value never starts with '@',
    so the prefix is an unambiguous discriminator).

    DELIBERATELY does NOT ``json.loads`` — it returns the raw string and the caller
    parses it. This preserves the asymmetric exception semantics at the two
    run-update call sites: the deliver-ack precheck swallows a parse error
    (best-effort), the main parse lets it raise. Merging json.loads here would
    erase that difference (contract drift). ``argname`` only parameterizes the
    read-failure message so a --stage-json failure does not misreport "--data".
    """
    if isinstance(raw, str) and raw.startswith("@"):
        try:
            if raw == "@-":
                return sys.stdin.read()
            return Path(raw[1:]).expanduser().read_text(encoding="utf-8")
        except (OSError, IOError) as e:
            print(json.dumps({"error": f"Could not read {argname} source {raw!r}: {e}"}),
                  file=sys.stderr)
            sys.exit(1)
    return raw


class _PublishRefused(Exception):
    """A publish refused BEFORE any write (unsafe identifier / phantom run).

    ``payload`` is the exact JSON error object the CLI prints; ``exit_code`` is the
    documented exit status (3 = rejected before write)."""

    def __init__(self, payload: dict, exit_code: int = 3):
        super().__init__(payload.get("error", "publish refused"))
        self.payload = payload
        self.exit_code = exit_code


def _publish_core(
    reg: ArtifactRegistry, *, project: str, artifact_type: str, data: dict,
    producer: str, summary: str, topic: str = "", run_id: "str | None" = None,
    stage: "str | None" = None, record_stub: bool = True,
) -> dict:
    """THE artifact publish code path — shared by ``publish`` and ``stage-complete``.

    1. TRUST BOUNDARY (run_44a06138, Kiro #2): validate project + run_id BEFORE
       reg.publish() writes anything, so a bad identifier writes ZERO bytes.
    2. PHANTOM-RUN check (Kiro #3): a --stage publish to a named run requires that
       run to exist and own this project/id — else a phantom runs/<id>/ is minted.
    3. reg.publish() (ValueError / FileNotFoundError propagate to the caller).
    4. Auto-record SAFETY NET (stage + run_id): append a ``status="recorded"`` stub
       (NOT "completed", no stage_doc_consumed) so a forgotten finalize never loses
       the stage→artifact link, while the completion gate still demands a real
       completed record. Best-effort: a failure is RETURNED as ``warning``.
       ``record_stub=False`` (stage-complete) skips it: that caller writes the
       full record itself in one atomic run.json write.

    Raises _PublishRefused for steps 1-2. Returns
    ``{"artifact_id", "auto_recorded": True|False|None, "warning": dict|None}``.
    """
    try:
        reg._validate_path_identifiers(project, run_id)
    except ValueError as _ve:
        raise _PublishRefused({"error": f"unsafe path identifier (rejected before write): {_ve}"})

    if stage and run_id:
        _run_json = (
            Path(reg.workspace_root) / "Projects" / project
            / ".artifacts" / "runs" / run_id / "run.json"
        )
        _phantom = None
        if not _run_json.is_file():
            _phantom = f"run '{run_id}' does not exist (no runs/{run_id}/run.json)"
        else:
            try:
                _rj = json.loads(_run_json.read_text(encoding="utf-8"))
                _rj_proj = _rj.get("project")
                _rj_id = _rj.get("id") or _rj.get("pipeline_id") or _rj.get("run_id")
                if _rj_proj not in (None, project):
                    _phantom = f"run '{run_id}' belongs to project {_rj_proj!r}, not {project!r}"
                elif _rj_id not in (None, run_id):
                    _phantom = f"run.json id {_rj_id!r} does not match run_id {run_id!r}"
            except (json.JSONDecodeError, OSError) as _e:
                _phantom = f"run '{run_id}' run.json unreadable: {_e}"
        if _phantom:
            raise _PublishRefused({"error": f"stage publish to a non-existent/foreign run "
                                            f"(rejected before write): {_phantom}"})

    artifact_id = reg.publish(
        project=project, artifact_type=artifact_type, data=data,
        producer=producer, summary=summary, topic=topic or "", run_id=run_id,
    )
    out: dict = {"artifact_id": artifact_id, "auto_recorded": None, "warning": None}
    if stage and run_id and record_stub:
        try:
            _append_stage_to_run(project, run_id, {
                "stage": stage, "status": "recorded",
                "artifact_id": artifact_id, "auto_recorded": True,
            }, reg)
            out["auto_recorded"] = True
        except Exception as e:
            out["auto_recorded"] = False
            out["warning"] = {
                "warning": "auto-record stage failed; run.json not updated. "
                           "Record manually via run-update --stage-json.",
                "stage": stage,
                "detail": str(e),
            }
    return out


def cmd_publish(args, reg: ArtifactRegistry) -> None:
    """Publish a bare artifact (no run, no stage): manifest-tracked, read by id.

    Pipeline v1 retired ``--stage`` / ``--run-id`` publishes; run documents are
    written through ``pipeline_v1.py``.
    """
    # --data accepts a raw JSON string OR "@PATH" (read a file) OR "@-" (stdin),
    # resolved by the shared _read_json_arg helper (same path as --stage-json).
    _raw = _read_json_arg(args.data, "--data")
    try:
        data = json.loads(_raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"Invalid JSON data: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Pre-publish validation: check schema + depth BEFORE writing to disk
    # Uses pipeline_validator as single source of truth (no duplicate schemas).
    # `--stage` / `--run-id` publishes are retired (Pipeline v1; main() refuses them):
    # only a bare publish reaches here. A bare publish is how standalone skills
    # (s_evaluate on ESCALATE, s_deliver / s_qa standalone) emit an artifact outside a
    # run; it is manifest-tracked and read only by artifact_id.
    run_id, stage = None, None

    # ── TRUST BOUNDARY + PHANTOM-RUN + write + auto-record ──
    # Delegated to _publish_core — the ONE publish code path, shared with
    # `stage-complete` (A1) so the two cannot drift. Validation order (identifiers
    # → phantom run → write) and the exit codes are unchanged.
    try:
        try:
            _core = _publish_core(
                reg, project=args.project, artifact_type=args.type, data=data,
                producer=args.producer, summary=args.summary,
                topic=args.topic or "", run_id=run_id, stage=stage,
            )
        except _PublishRefused as _pr:
            if getattr(args, "quiet", False):
                print(json.dumps(_pr.payload), file=sys.stderr)
            else:
                print(json.dumps(_pr.payload, indent=2), file=sys.stderr)
            sys.exit(_pr.exit_code)
        artifact_id = _core["artifact_id"]
        result = {"artifact_id": artifact_id, "project": args.project}
        if run_id:
            result["run_id"] = run_id

        if stage and not run_id:
            # NEVER GUESS (run_f3975b8b, XG directive): a --stage publish with NO
            # --run-id ALWAYS fails closed — any active run may belong to a sibling
            # session, and guessing the target is the root of all cross-run
            # contamination (run_3caef1d3 / run_b9ecb07a). The artifact is already
            # published (not lost); re-run with an explicit --run-id. STDERR + exit 3
            # (the orchestrator guard reads STDERR and json.loads STDOUT).
            refused = {
                "error": (
                    f"auto-record REFUSED: publish --stage with NO --run-id "
                    f"cannot safely pick a target run in '{args.project}' — "
                    f"any active run may belong to a sibling session. "
                    f"Re-run this publish with an explicit --run-id."
                ),
                "artifact_id": artifact_id,
                "stage": stage,
            }
            print(json.dumps(refused), file=sys.stderr)
            sys.exit(3)
        if stage and _core["auto_recorded"] is True:
            result["auto_recorded"] = True
            result["run_id"] = run_id
        elif stage and _core["auto_recorded"] is False:
            # Best-effort auto-record failed — never silently: surface on stderr so
            # the orchestrator can fall back to run-update.
            result["auto_recorded"] = False
            print(json.dumps(_core["warning"]), file=sys.stderr)
        if stage == "deliver" and run_id:
            # Gate-2 Red Team HIGH trigger: add `red-team` to the run's
            # required_experts so run-commit's D7 gate demands its marker.
            _rt_file = (Path(reg.workspace_root) / "Projects" / args.project
                        / ".artifacts" / "runs" / run_id / "run.json")
            try:
                _rt_state = json.loads(_rt_file.read_text(encoding="utf-8"))
                if _require_red_team_expert(_rt_state, data):
                    _write_run_atomic(_rt_file, _rt_state)
                    result["required_experts_added"] = [_RED_TEAM_EXPERT_ID]
            except (OSError, ValueError, AttributeError) as _rt_err:
                print(json.dumps({"warning": "Red Team required-expert update failed: "
                                             f"{type(_rt_err).__name__}: {_rt_err}"}),
                      file=sys.stderr)

        # ── EVALUATE glass-box: hand the agent the rendered "需求理解" block ──
        # The real-time inline emit (evaluate.md §4.6.4 / INSTRUCTIONS.md Phase-2)
        # used to be a pure [MUST] telling the agent to HAND-TYPE a markdown block —
        # F004 attention-decay meant it almost never appeared, unlike the run-end
        # REPORT §4.6 audit which is mechanically rendered (artifact_cli.py cmd_run_report
        # → render_audit_trail_md). Fix: publish --stage evaluate is a STRUCTURALLY
        # mandatory step, so make IT return the same CLI-rendered block as a result
        # FIELD. The agent relays a ready-made block instead of recalling-and-retyping
        # (the F004 "eliminate the exception" form — chat stream has no CLI-ownable
        # file analogue, publish is an SSE-less subprocess, so agent-relay is the
        # architectural ceiling). The gate matches the REPORT side's condition
        # EXACTLY (the §4.6 audit render, where _eval_art.get("requirements") is a
        # BARE truthy check + isinstance(schema_version,int) >= REQ_SCHEMA_VERSION) —
        # so preview and archive render on IDENTICAL inputs and cannot drift. Do NOT
        # tighten the requirements check to isinstance(...,list): REPORT uses bare
        # truthy, render_audit_trail_md itself degrades a non-list via _iter_requirements,
        # and a stricter check here would render in REPORT but not here (the exact
        # drift this mirror forbids — Gate-2 integration finding). Relaxed/legacy runs
        # (no requirements[] / schema_version<1) get zero ceremony. It is a result KEY
        # (not bare markdown appended after the JSON line) so stdout stays a SINGLE
        # valid JSON object (Gate-1 skeptic BLOCK#3: a non-quiet publish example exists
        # [evaluate.md] and the pipeline parse habit is json.load whole stdout
        # [INSTRUCTIONS.md] — appending bare text would break both). Quiet is untouched
        # (parse-proof single line). Best-effort: a render failure degrades VISIBLY
        # (stderr note) and NEVER fails the publish.
        if (
            not getattr(args, "quiet", False)
            and stage == "evaluate"
            and data.get("requirements")
            and isinstance(data.get("schema_version"), int)
        ):
            try:
                from pipeline_validator import render_audit_trail_md, REQ_SCHEMA_VERSION
                if data["schema_version"] >= REQ_SCHEMA_VERSION:
                    result["_display_block"] = render_audit_trail_md(
                        data.get("requirement", ""), data
                    )
            except Exception as _disp_err:
                # VISIBLE degrade, never silent — but the publish itself still succeeds.
                print(json.dumps({
                    "warning": "evaluate _display_block render failed; "
                               "the §4.6 audit still renders at run-end REPORT.",
                    "detail": f"{type(_disp_err).__name__}: {_disp_err}",
                }), file=sys.stderr)

        if getattr(args, "quiet", False):
            # Parse-proof success: ONLY the artifact_id, single line. The
            # auto-record into run.json still happened above (side effect); the
            # orchestrator just doesn't need project/run_id/auto_recorded echoed.
            print(json.dumps({"artifact_id": artifact_id}))
        else:
            print(json.dumps(result))
    except (ValueError, FileNotFoundError) as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(1)


def _structured_errors_safe(stage, errors, data=None) -> list:
    """A3: structured form of legacy error strings for the failure payloads of
    publish / advance / run-update completion (additive key — the old string
    lists stay). Never raises: a mapping failure degrades to [] so an error path
    can never be turned into a crash."""
    try:
        from pipeline_validator import structure_errors
        return structure_errors(stage, list(errors or []), data=data)
    except Exception:
        return []


def cmd_schema(args, reg: "ArtifactRegistry | None") -> None:
    """Print a stage's expected artifact schema + template as single-line JSON.

    Read-only. Lets a caller see the required/recommended/depth/template for a
    stage WITHOUT triggering a failed publish (the only way to see it before).
    Reuses pipeline_validator.get_stage_schema as the single source of truth —
    no duplicate schema definitions. Output is single-line (parse-proof, mirrors
    publish --quiet) so orchestrators can json.load it directly. (run_88b9f986)
    """
    from pipeline_validator import get_stage_schema
    info = get_stage_schema(args.stage)
    # Surface `template` FIRST + a _hint: a consumer that reads only `required`/
    # `depth` (field-name lists) misses that nested objects like
    # layers.ac_driven must be {run, pass} — the shape lives ONLY in `template`.
    # Additive key reorder: every original key is preserved (dict insertion order
    # puts template first), so json.load(out)['required'] etc. keep working.
    template = info.get("template")
    out = {"stage": args.stage}
    if template is not None:
        out["template"] = template
        out["_hint"] = ("Build your payload FROM `template` — it carries the full "
                        "nested shape (e.g. layers.ac_driven:{run,pass}). `required`/"
                        "`depth` are field-NAME lists only, not the nested structure.")
    for k, v in info.items():
        if k not in out:
            out[k] = v
    print(json.dumps(out))


def cmd_state(args, reg: ArtifactRegistry) -> None:
    """Get pipeline state for a project."""
    state = reg.get_pipeline_state(args.project)
    print(json.dumps({"project": args.project, "pipeline_state": state}))


# ── Stage-advance continuation contract (the CONTINUE-side carrier) ─────────
# The pipeline's STOP side is code-enforced (this function's own fail-closed
# sys.exit(1) below; `run-update --status completed`; `cmd_run_checkpoint`'s
# should_checkpoint hard-block). Its CONTINUE side used to be prose ONLY
# (INSTRUCTIONS.md §3f / the Core Loop / Rule 20) with no turn-boundary
# directive and no carrier — so `advance` read as a terminal action and the
# orchestrating agent ended its turn at the stage boundary (stop_reason=
# end_turn, zero tool_use). This constant is that missing carrier, and it is
# SINGLE-SOURCED on purpose: the same phrase is asserted in the 7 stage docs,
# the 3 sibling skills, and INSTRUCTIONS.md §3f, so the contract cannot drift
# across those sources (R27). Changing this string requires updating them all —
# `backend/tests/test_advance_continuation.py` enforces that.
_CONTINUATION_PHRASE = "advance is not the end of your turn"

# Where the stage docs physically live. `Path(__file__).parent.parent` is
# `backend/` (this file is `backend/scripts/artifact_cli.py`), matching the
# existing convention used for pipeline_validator/pollinate_validator lookups.
# This deliberately resolves the `backend/skills/` SOURCE OF TRUTH, never the
# `.claude/skills/` projection, which can be stale within a session
# (INSTRUCTIONS.md § "Read from backend/skills").
_STAGE_DOC_DIR = Path(__file__).resolve().parent.parent / "skills" / "s_autonomous-pipeline" / "stages"


def _next_stage_continuation(
    project: str, next_state: str, run_id: str | None
) -> tuple[str | None, str]:
    """Resolve what the agent should do immediately after advancing.

    ⚠️ ``next_state`` is the stage being ENTERED, not the one just finished, so the
    doc to read next is ``next_state``'s own doc. Evidence: ``stages/build.md``
    issues ``advance --state review`` at the END of BUILD — the state named is
    where the run is going. An earlier revision of this helper returned
    ``stages[index(next_state) + 1]`` and therefore told the agent to skip the very
    stage it had just entered (advance-to-review pointed at ``test.md``). The spec
    reviewer caught it; worse, the first version of the test suite had HARDCODED
    that skew as its expected value, so the tests ratified the bug instead of
    catching it (RP64: a hardcoded-literal test LOCKS a wrong answer). Do not
    "fix" this back to ``+ 1``.

    Returns ``(next_stage_doc, next_action)`` where ``next_stage_doc`` is an
    ABSOLUTE path to that stage's doc, or ``None`` when no doc exists for it
    (e.g. a terminal ``advance`` target). A path is emitted ONLY after
    ``Path.is_file()`` passes — emitting an unverified path would send the agent
    to a failing Read, which is worse than emitting nothing (a fabricated
    instruction).

    ``next_action`` is CONDITION-AWARE and this is the load-bearing subtlety:
    when a checkpoint is genuinely due it must NOT urge continuation, or it
    would compete with a legitimate stop (INSTRUCTIONS.md Rule 19: context
    exhausted → CHECKPOINT). The checkpoint judgement is NOT re-derived here —
    it delegates to ``_compute_should_checkpoint``, the same SSOT that
    ``cmd_run_budget`` and the checkpoint guard use (STEERING #3), so there is
    exactly one definition of "a checkpoint is due" in this file.

    Never raises: the caller treats this as an enhancement and degrades to the
    plain payload on any failure.
    """
    stages = _get_profile_stages("full")
    # Budget is NEVER a reason to withhold continuation (STEERING #2 + XG directive
    # "budget 从来不应该是 block run 的重要因素"). The former should_checkpoint /
    # budget_known tri-state existed ONLY to stop a missing token_cost from
    # fail-opening a budget-due judgment to "not due" — with budget removed as a
    # checkpoint producer, there is no budget-due judgment to protect, so the whole
    # tri-state is dead-weight (R25: a good fix deletes code). advance always
    # continues; a REAL checkpoint (L2/retry/stuck) is surfaced by the pause gates
    # and the stage docs' Escalation Routing Protocol, not by this budget probe.
    # `stages` is still resolved per-profile so an off-profile state resolves to no
    # doc rather than a confidently-wrong one.
    if run_id:
        try:
            run_dir = _run_dir(project, run_id)
            run_file = run_dir / "run.json"
            if run_file.is_file():
                run_state = json.loads(run_file.read_text(encoding="utf-8"))
                if isinstance(run_state, dict):
                    stages = _get_profile_stages(run_state.get("profile", "full"))
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            # Narrow by design: a missing/unreadable/malformed run.json just leaves
            # `stages` at the full-profile default — advance still continues (budget
            # is no longer a gating consideration). Anything outside this set is a
            # real defect and should surface; the caller's own guard keeps it from
            # failing advance.
            pass

    # The stage to execute next IS next_state (see the docstring's warning).
    target = next_state if next_state in stages else None

    doc_path = None
    if target:
        candidate = _STAGE_DOC_DIR / f"{target}.md"
        if candidate.is_file():
            doc_path = str(candidate)

    if doc_path:
        return doc_path, (
            f"Now Read the {target} execution card {doc_path} and execute the stage, "
            f"including its gates (read a stages/ref/ file only when the card's "
            f"'Read only if' condition holds) — {_CONTINUATION_PHRASE}. Hand control back only for a "
            f"stage-boundary L2 Judgment (via the Escalation Routing Protocol), a "
            f"true checkpoint trigger, retries exhausted, a mid-stage L2, a pending "
            f"question awaiting the user, or an abandon verdict."
        )

    if target is None and next_state:
        # In PIPELINE_STATES (advance_pipeline accepted it) but NOT in this run's
        # profile. Deliberately does NOT point at stages/ — the whole reason
        # doc_path was withheld is that naming a doc for a stage this run does not
        # run would be confidently wrong, and a prose pointer defeats that exactly
        # as a populated field would (the same field-vs-string inconsistency the
        # checkpoint branch was hardened against, inverted).
        return None, (
            f"State '{next_state}' is not part of this run's profile, so no stage "
            f"doc applies. Verify the profile and the intended state before doing "
            f"any more stage work — do not guess a stage doc."
        )

    return None, (
        f"No stage doc resolves for state '{next_state}' — if the run's stages are "
        f"done, proceed to the COMPLETE step (read stages/complete.md and output "
        f"the summary). {_CONTINUATION_PHRASE}."
    )


def cmd_advance(args, reg: ArtifactRegistry) -> None:
    """Advance pipeline state. Auto-validates if a run is active.

    If a pipeline run exists and the current stage has a record,
    runs the pipeline validator. Refuses to advance on BLOCK errors.
    Warnings are printed but don't block advancement.
    """
    # Auto-validate before advancing (structural enforcement)
    # FAIL-CLOSED: if validator crashes, block advancement.
    # Prior behavior (fail-open) allowed advancement when validator errored,
    # which is the most dangerous failure mode — gate opens on crash.
    try:
        _auto_validate_before_advance(
            args.project, args.state, getattr(args, "run_id", None)
        )
    except SystemExit:
        raise  # Re-raise if validation blocks
    except Exception as e:
        # FAIL-CLOSED: validator crash → block advancement
        print(json.dumps({
            "validation_blocked": True,
            "error": f"Validator crashed: {e}. Fix the validator before advancing.",
        }, indent=2), file=sys.stderr)
        sys.exit(1)

    try:
        reg.advance_pipeline(args.project, args.state)
        # Continuation carrier: tell the agent what to do NEXT, in the machine
        # output it necessarily reads, rather than relying on it to recall a prose
        # rule. Both keys are pre-seeded so the payload SHAPE is stable even if
        # resolution fails — INSTRUCTIONS.md documents them as always present, and
        # a consumer doing payload["next_action"] must not hit a KeyError.
        if getattr(args, "quiet", False):
            # --quiet: a COMPACT, stable payload for a programmatic caller that
            # only needs to confirm the transition. The large next_stage_doc /
            # next_action continuation fields are DROPPED — they bloat the output
            # and get display-truncated. A non-quiet caller (the orchestrator
            # following the continuation contract) still gets them below.
            print(json.dumps({
                "project": args.project,
                "pipeline_state": args.state,
                "advanced": True,
            }))
        else:
            payload = {
                "project": args.project,
                "pipeline_state": args.state,
                "next_stage_doc": None,
                "next_action": None,
            }
            try:
                next_doc, next_action = _next_stage_continuation(
                    args.project, args.state, getattr(args, "run_id", None)
                )
                payload["next_stage_doc"] = next_doc
                payload["next_action"] = next_action
            except Exception:
                # The hint is an enhancement; a failure here must never fail an
                # otherwise-successful advance. Keys stay present but null.
                pass
            print(json.dumps(payload))
    except ValueError as e:
        # Explicit blocked-advance shape: {advanced:false, reason} so a caller
        # distinguishes "the transition was refused" from "the output failed to
        # parse". Kept on stderr+exit1 (unchanged contract) for both quiet and
        # non-quiet — a blocked advance is an error either way.
        print(json.dumps({"advanced": False, "reason": str(e)}), file=sys.stderr)
        sys.exit(1)


def _auto_validate_before_advance(
    project: str, next_state: str, run_id: str | None = None
) -> None:
    """Run pipeline validator on the current stage of the NAMED run before advancing.

    Validates ONLY the run explicitly named by --run-id. If no --run-id is given,
    SKIP the auto-validate entirely (run_f3975b8b): the validator is a best-effort
    pre-advance check, and guessing "the newest active run" project-wide could
    validate/block a SIBLING session's run (the same root contamination this run
    eradicates on the publish path). Skipping a best-effort check is safe; acting
    on the WRONG run is not — and the explicit run-update/publish path with
    --run-id still validates correctly at the completion gate.

    Blocks on errors, warns on warnings. No-op when run-id absent or run not found.
    """
    import subprocess

    # NEVER guess the target run. No --run-id → nothing to validate (the named-run
    # publish path validates at its own gate). This deliberately replaces the old
    # "newest active project-wide" selection (a guess-the-target footgun).
    if not run_id:
        return

    # run_dd8411f6 (#2, Gate-1 amendment A): route through _run_dir so a traversal
    # run_id is rejected here too — `advance` builds this path inline from the
    # --run-id CLI arg and previously bypassed the _run_dir choke point. A
    # traversal id raises ValueError; a safe-but-unknown id passes validation and
    # falls through to the existing `.exists()` no-op (so the typo no-op is kept).
    run_file = _run_dir(project, run_id) / "run.json"
    if not run_file.exists():
        # Typo'd / unknown run-id → nothing to validate. Blocks YOUR advance loudly
        # only if you also can't advance the state machine; never touches another run.
        return
    try:
        run_data = json.loads(run_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    stages = run_data.get("stages", [])

    if not stages:
        return

    # Determine current stage (last completed)
    current_stage = None
    current_record = None
    for s in reversed(stages):
        status = s.get("status", "")
        if status in ("done", "completed"):
            # No default: the `if not current_stage` guard below depends on a
            # key-less (corrupt) record staying falsy — a "?" default would make
            # it truthy and run validation against a bogus stage. (3b: the dead
            # `name` fallback is removed; the None default is preserved on purpose.)
            current_stage = s.get("stage")
            current_record = s
            break

    if not current_stage:
        return

    # Drift guard: a completed stage that produced NO artifact usually means a
    # silently-failed publish (the exact failure this tooling has hit before —
    # publish returns an error to stderr but advance proceeds anyway, leaving
    # state ahead of reality). Warn, don't block. Some stages legitimately
    # produce no artifact (reflect closes the loop; goal_cycle commits
    # incrementally) — exempt them to avoid false-positive noise.
    _ARTIFACTLESS_STAGES = {"reflect", "goal_cycle"}
    if current_record is not None and not current_record.get("artifact_id") \
            and current_stage not in _ARTIFACTLESS_STAGES:
        print(json.dumps({
            "warning": f"stage '{current_stage}' is marked completed but has no "
                       f"artifact_id — its publish may have failed silently. "
                       f"Verify before advancing to '{next_state}'.",
        }), file=sys.stderr)

    # REPORT.md gate: deliver stage MUST produce REPORT.md before advancing.
    # 52% of historical "completed" runs lacked REPORT.md because this was never enforced.
    if current_stage == "deliver":
        # run_f3975b8b: `artifacts_dir` was previously defined by the deleted
        # active-run scan. Now that we validate ONLY the explicitly-named run,
        # build the run dir from the named run_id directly.
        # run_dd8411f6 (#2, Gate-1 amendment A): via _run_dir (traversal-guarded).
        # Unreachable with a traversal id anyway — :854 above already validated —
        # but routing here too keeps the single choke point, no inline path-build.
        run_dir = _run_dir(project, run_id)
        if not (run_dir / "REPORT.md").exists():
            _report_errs = [
                "[deliver] REPORT.md not found. DELIVER stage requires generating "
                "REPORT.md at .artifacts/runs/<RUN_ID>/REPORT.md before advancing. "
                "Run: artifact_cli.py run-report --project <PROJECT> --run-id <RUN_ID>"
            ]
            print(json.dumps({
                "validation_blocked": True,
                "stage": "deliver",
                "errors": _report_errs,
                "structured_errors": _structured_errors_safe("deliver", _report_errs),
            }, indent=2), file=sys.stderr)
            sys.exit(1)

    # Run validator
    try:
        validator = Path(__file__).parent / "pipeline_validator.py"
        result = subprocess.run(
            [sys.executable, str(validator), "check",
             "--project", project, "--run-id", run_id, "--stage", current_stage],
            capture_output=True, text=True, timeout=10,
            cwd=str(Path(__file__).parent.parent),
        )
        # FAIL-CLOSED on validator crash (Operational review HIGH-8): an
        # UNWRAPPED check that raises makes the validator exit non-zero with
        # EMPTY stdout (traceback on stderr). Without this guard, `if result.stdout`
        # would be False, the whole block would be skipped, and advance would
        # proceed = the hard gate fails OPEN. A crashed validator can NEVER verify
        # a stage, so it must block — matching the TimeoutExpired/JSONDecodeError
        # handlers below and the _CheckGuard hard-fail-closed contract.
        if result.returncode != 0 and not result.stdout.strip():
            raise RuntimeError(
                f"Pipeline validator crashed (exit {result.returncode}, no output) — "
                f"cannot verify stage '{current_stage}'. stderr: {result.stderr.strip()[:500]}"
            )
        if result.stdout:
            validation = json.loads(result.stdout)
            # errored[] (run_55710438): checks that could NOT run, distinct from
            # content failures. Surfaced in the audit + stderr so a blocked
            # advance tells the user WHY (bad content vs broken check).
            errored = validation.get("errored", [])
            if not validation.get("valid", True):
                errors = validation.get("errors", [])
                # Record validation block event in run.json
                _record_validation_event(
                    project, run_id, current_stage,
                    passed=False, errors=errors,
                    warnings=validation.get("warnings", []),
                    errored=errored,
                )
                print(json.dumps({
                    "validation_blocked": True,
                    "stage": current_stage,
                    "errors": errors,
                    "structured_errors": _structured_errors_safe(current_stage, errors),
                    "errored": errored,
                }, indent=2), file=sys.stderr)
                sys.exit(1)
            warnings = validation.get("warnings", [])
            if warnings or errored:
                print(json.dumps({
                    "validation_warnings": warnings,
                    "errored_checks": errored,
                }), file=sys.stderr)
    except subprocess.TimeoutExpired:
        # FAIL-CLOSED: validator timeout → block advancement (F3 fix)
        raise RuntimeError("Pipeline validator timed out (>10s) — cannot verify stage")
    except (json.JSONDecodeError, OSError) as exc:
        # FAIL-CLOSED: validator parse error → block advancement (F3 fix)
        raise RuntimeError(f"Pipeline validator produced invalid output: {exc}")


def cmd_learn(args, reg: ArtifactRegistry) -> None:
    """Record outcome of a pipeline run for learning feedback."""
    lessons = [l.strip() for l in (args.lessons or "").split(";") if l.strip()]
    reg.record_outcome(
        project=args.project,
        evaluation_id=args.evaluation_id,
        outcome=args.outcome,
        actual_effort=args.actual_effort,
        lessons=lessons or None,
    )
    print(json.dumps({
        "project": args.project,
        "evaluation_id": args.evaluation_id,
        "outcome": args.outcome,
        "recorded": True,
    }))


def cmd_projects(args, reg: ArtifactRegistry) -> None:
    """List all projects with pipeline status."""
    statuses = reg.list_projects()
    result = [
        {
            "project": s.project,
            "pipeline_state": s.pipeline_state,
            "artifact_count": s.artifact_count,
            "active_artifact_count": s.active_artifact_count,
            "latest_artifact": s.latest_artifact,
        }
        for s in statuses
    ]
    print(json.dumps({"projects": result, "count": len(result)}, indent=2))


# ── Pipeline run management ──────────────────────────────────────────

# Default token budget estimates per stage (CONSERVATIVE — used for run-budget
# should_checkpoint calculation). These are intentionally HIGHER than the
# INSTRUCTIONS.md "Base stage costs" table (which shows typical/observed costs)
# because run-budget must avoid premature checkpoints. The INSTRUCTIONS.md values
# (evaluate=6K, think=10K, plan=8K, build=40K, etc.) are what the agent targets;
# these values are what the budget system uses as worst-case estimates.
# After 5+ completed runs, _calibrated_stage_budget() overrides these with
# historical averages + 20% buffer — making both sets irrelevant.
DEFAULT_STAGE_BUDGETS = {
    "evaluate": 10_000,
    "think": 40_000,
    "plan": 30_000,
    "build": 60_000,
    "review": 25_000,
    "test": 40_000,
    "deliver": 15_000,
    "reflect": 10_000,
}
SESSION_BUDGET = 800_000       # 80% of 1M context window
CHECKPOINT_RESERVE = 50_000    # Reserve for checkpoint handoff


def _pipeline_runs_dir(project: str) -> Path:
    """Get the base runs directory: .artifacts/runs/."""
    return _get_workspace() / "Projects" / project / ".artifacts" / "runs"


def _run_dir(project: str, run_id: str) -> Path:
    """Get directory for a specific run: .artifacts/runs/<run_id>/.

    THE single choke point for building a run dir from a caller-supplied run_id —
    `_resolve_run_file` routes through here, so validating here covers every
    run-id-bearing command (update/checkpoint/resume/observe/report/commit).
    run_dd8411f6 (#2): run_id is a CLI arg that upstream tooling may fill from
    requirement/LLM text; an unvalidated segment like '../../OTHER/.artifacts/
    runs/run_x' would let a subsequent write escape to another project. SSOT
    (mirrors _ensure_project_dir): delegate containment to ArtifactRegistry.
    _validate_path_identifiers — the ONE place that owns "is this caller-supplied
    identifier safe to build a path from?" Raises ValueError on traversal.
    """
    from core.artifact_registry import ArtifactRegistry
    ArtifactRegistry(_get_workspace())._validate_path_identifiers(project, run_id)
    return _pipeline_runs_dir(project) / run_id


def _ensure_project_dir(project: str) -> Path:
    """Return ``Projects/<project>/``, guaranteeing it exists AND is REGISTERED.

    THE single choke point for minting a project directory from the pipeline CLI.
    Every command that would ``mkdir(parents=True)`` a ``Projects/<name>/`` subtree
    (run-create, release-gate, run-report, run-analytics) must obtain the project dir
    THROUGH here first — so a ``Projects/<name>/`` can NEVER be created without its
    ``.project.json`` (which is what makes it visible to Brain Hub). This closes the
    "pseudo-DDD" class at the birth point rather than in one command (see run_be30a0c6).

    Idempotent + non-destructive: ``ensure_project_metadata`` is only-if-absent, so an
    already-registered project keeps its metadata/history untouched. Returns the dir.
    """
    from core.swarm_workspace_manager import ensure_project_metadata
    # Containment guard (adversarial-review HIGH): `project` is a CLI arg that upstream
    # tooling may fill from requirement/LLM text — an unvalidated segment like
    # "../../.ssh" would let mkdir(parents=True) escape the workspace.
    # SSOT (run_44a06138, Gate-1 amendment A): delegate the project-name
    # containment to ArtifactRegistry._validate_path_identifiers — the ONE place
    # that owns "is this caller-supplied identifier safe to build a path from?"
    # Do NOT re-derive the resolve()/relative_to idiom here (a 2nd copy drifts).
    from core.artifact_registry import ArtifactRegistry
    projects_root = _get_workspace() / "Projects"
    ArtifactRegistry(_get_workspace())._validate_path_identifiers(project)
    pdir = projects_root / project
    pdir.mkdir(parents=True, exist_ok=True)
    ensure_project_metadata(pdir, project)
    return pdir


def _resolve_run_file(project: str, run_id: str) -> Path:
    """Find the run.json file at the new path (runs/<id>/run.json)."""
    new_path = _run_dir(project, run_id) / "run.json"
    if new_path.exists():
        return new_path

    print(json.dumps({"error": f"Pipeline run {run_id} not found"}), file=sys.stderr)
    sys.exit(1)


# Crash auto-checkpoint stamps this exact reason; an INTENTIONAL pause carries a
# real decision reason instead (e.g. "Gate 1 BLOCK", "DELIVERED ...", "ROOT CAUSE
# FALSIFIED"). This string is the primary zombie discriminator.
_CRASH_ZOMBIE_REASON = "session_crash_auto_detected"

# C1: the terminal status set — a run in one of these has FINISHED. Single source
# of truth shared by is_terminal_run() (liveness) AND the run-update transition guard
# (revival protection), so the two can never drift on what "terminal" means.
_TERMINAL_STATUSES = ("completed", "complete", "abandoned", "failed", "cancelled")
# All legal status values run-update accepts (argparse choices) — rejects typos/junk.
_VALID_STATUSES = ("running", "paused", "completed", "complete", "failed",
                   "cancelled", "abandoned")


def is_terminal_run(data: dict) -> bool:
    """Single source of truth: has this run reached a TERMINAL (finished) state,
    judged by STAGE PROGRESS — not by the top-level `status` string?

    Why stage-based, not status-based: a since-deleted orphan-transition
    (running->paused + reason=session_crash_auto_detected, once run on every
    session refresh) left legacy run.json files whose STAGES are all done but
    whose status is 'paused'. Such a run is FINISHED, not a crash orphan — but every liveness check
    that keys off `status` alone mis-reads it as a resumable crash-zombie and
    re-surfaces it forever (run_bf840159: 6/6 stages incl. reflect completed, yet
    status='paused'+crash-reason → endless auto-resume prompt).

    A run is terminal when EITHER:
      - top-level status is an explicit terminal value, OR
      - a completed 'reflect' or 'deliver' stage exists (the pipeline's own
        end-of-run markers — reaching either means the run delivered).

    NOT terminal just because "every LISTED stage is completed": a paused
    mid-pipeline run only lists the stages done SO FAR and carries a
    `checkpoint.next_stage` pointing at remaining work (e.g. evaluate+think done,
    next_stage=build). Treating that as terminal would silently skip a genuinely
    resumable run. The reflect/deliver marker is the reliable end-of-run signal —
    the pipeline appends those only at the very end.

    Terminal runs are NEVER crash orphans: crash-detect, orphan-transition,
    supersede, and _abandon_verdict must all skip them regardless of `status`.
    """
    if not isinstance(data, dict):
        return False
    status = data.get("status")
    if status in _TERMINAL_STATUSES:
        return True
    # End-of-run stage markers ONLY (see _delivered_by_stages): a completed
    # reflect/deliver means the run produced its deliverable. (Do NOT infer
    # terminal from "all listed stages done" — a mid-pipeline pause satisfies
    # that yet has next_stage remaining.)
    return _delivered_by_stages(data)


def _delivered_by_stages(data: dict) -> bool:
    """True iff the run's STAGE ARRAY shows a completed 'reflect' or 'deliver'
    stage — the pipeline's own end-of-run markers, appended only at the very end.

    This is the ONLY non-circular way to ask "did this run actually deliver?".
    `is_terminal_run` cannot answer it for an abandoned/paused run, because its
    status short-circuit (line above) returns True for status=='abandoned'
    REGARDLESS of stage progress — so a garbage-abandoned empty shell and a
    delivered-then-mislabeled-abandoned run are indistinguishable by
    `is_terminal_run`. Extracted (run_0e68e235) so `_is_garbage_run` and
    `_effective_analytics_status` can key on real delivery, not the status string.
    Behavior-preserving: `is_terminal_run` calls this after its status
    short-circuit, so all its consumers see identical results.
    """
    if not isinstance(data, dict):
        return False
    stages = data.get("stages") or []
    if isinstance(stages, dict):
        return stages.get("reflect") in ("completed", "degraded") \
            or stages.get("deliver") in ("completed", "degraded")
    if not isinstance(stages, list) or not stages:
        return False
    return any(
        isinstance(s, dict)
        and s.get("stage") in ("reflect", "deliver")
        and s.get("status") == "completed"
        for s in stages
    )


def _is_garbage_run(raw: dict) -> bool:
    """Single source of truth: is this run GARBAGE that must be excluded from ALL
    pipeline analytics/stats (needs-you, completion_rate denominator, tokens,
    trend, profile_mix, by-project) AND is eligible for retention purge?

    Garbage = a run that NEVER delivered a result AND is in a dead/dead-residue
    state the user can take no action on:
      - status == 'abandoned' (reaper death verdict: crash_zombie /
        crash_residue_empty_shell / superseded_by_* / orphaned_no_resume), OR
      - status == 'paused' with the crash auto-checkpoint reason
        (_CRASH_ZOMBIE_REASON) — a dead session's residue, not a real decision-pause
    ...MINUS any run that actually delivered (`_delivered_by_stages`): the
    "mislabeled-done" class (measured 22/182 abandoned) reached a completed
    reflect/deliver stage and was only stamped abandoned/superseded AFTERWARD.
    Those are real completions — excluding them would UNDERCOUNT delivery
    (Gate-1 skeptic catch). They are recategorized to 'completed' by
    `_effective_analytics_status`, never dropped.

    NOT garbage: completed/complete, cancelled (a real user decision), failed
    (a real signal), running, and genuine (non-crash) decision-pauses. XG
    directive (run_0e68e235): only abandoned + crash-residue-paused are garbage.
    """
    if not isinstance(raw, dict):
        return False
    status = raw.get("status")
    is_crash_paused = (
        status == "paused"
        and (raw.get("checkpoint") or {}).get("reason") == _CRASH_ZOMBIE_REASON
    )
    if status != "abandoned" and not is_crash_paused:
        return False
    # A run that reached deliver/reflect delivered — never garbage.
    return not _delivered_by_stages(raw)


def _completion_verdict(run_state: dict) -> "tuple[bool | None, list[str]]":
    """Whether delivery is verified; v1 reads the generated audit projection."""
    if isinstance(run_state.get("stages"), dict):
        ready = run_state.get("push_ready")
        return (ready, [] if ready is True else ["run_audit push_ready is false"]
                if ready is False else [])
    recs = [s for s in run_state.get("stages") or [] if isinstance(s, dict)
            and s.get("stage") == "deliver" and s.get("status") == "completed"]
    if not recs:
        return None, []
    rec = recs[-1]
    if rec.get("push_ready") is True:
        return True, []
    why = str(rec.get("push_ready_reason") or "push_ready is false")
    return False, [r.strip() for r in why.split("; ") if r.strip()][:10]


def _effective_analytics_status(raw: dict) -> str:
    """The status a run should be COUNTED AS in analytics rollups — corrects the
    mislabeled-done class. A run whose stages show a completed reflect/deliver has
    DELIVERED, regardless of a status string later stamped 'abandoned'/'paused'
    (superseded/crash-after-the-fact). Count those as 'completed' so
    completion_rate does not undercount (Gate-1 #4: covers BOTH abandoned+delivered
    AND paused+delivered). Everything else passes through unchanged.

    Consumers must still drop `_is_garbage_run` rows FIRST; this only decides how a
    KEPT run is bucketed (the 22 delivered-abandoned survive the garbage filter via
    _delivered_by_stages and land here → completed)."""
    if not isinstance(raw, dict):
        return ""
    status = raw.get("status") or ""
    if isinstance(raw.get("stages"), dict):
        return {"active": "running", "awaiting_human": "paused",
                "reflecting": "running"}.get(status, status)
    if status in ("completed", "complete"):
        return "completed"
    if status in ("abandoned", "paused") and _delivered_by_stages(raw):
        return "completed"
    return status


def _trash_dir(path: Path) -> None:
    """Delete a run directory, PREFERRING recoverable trash but SILENTLY DEGRADING to
    PERMANENT delete when trash is unavailable. Path 1 (recoverable): macOS `trash` →
    goes to Trash, undoable (STEERING safety: trash > rm). Path 2 (PERMANENT, NOT
    recoverable): shutil.rmtree — taken when the `trash` tool is absent (CI, tmpfs, PATH
    gap under launchd) or errors. This is a deliberate degrade (a purge must still make
    progress), but it is NOT "recoverable" as the name implies — so which path ran is
    RECORDED to stderr, otherwise a permanent delete looks identical to a recoverable one
    in the logs. Raises on total failure so the caller counts it as not-purged."""
    import shutil
    import subprocess
    trash_bin = shutil.which("trash")
    if trash_bin:
        try:
            r = subprocess.run([trash_bin, str(path)], capture_output=True, timeout=30)
            if r.returncode == 0:
                print(json.dumps({"trash": "recoverable", "path": str(path)}),
                      file=sys.stderr)
                return
        except (OSError, subprocess.SubprocessError):
            pass  # fall through to rmtree
    # PERMANENT delete path — make it auditable (see docstring): the delete is real and
    # unrecoverable, so record it rather than let it pass as a recoverable trash.
    print(json.dumps({"trash": "PERMANENT_rmtree", "path": str(path),
                      "reason": "trash tool absent or failed"}), file=sys.stderr)
    shutil.rmtree(path)


def _has_git_dir(repo_root: Path) -> bool:
    """Filesystem-only (NO subprocess) check for whether repo_root is inside a git
    working tree: walk up looking for a `.git` dir OR file (worktree/submodule use a
    `.git` FILE). Deliberately avoids `git`; it is the fail-CLOSED fallback used when
    the git SUBPROCESS is exactly what failed, so it must not depend on git running."""
    p = repo_root
    for _ in range(40):  # bounded walk-up (defensive against a symlink loop)
        if (p / ".git").exists():
            return True
        if p.parent == p:
            break
        p = p.parent
    return False


def _is_git_tracked(path: Path, repo_root: Path) -> bool:
    """True iff `path` MUST be treated as git-TRACKED for delete-safety purposes.

    Fail-CLOSED (run: purge fail-open fix): the guard's JOB is to STOP an unattended
    delete of a tracked file (Gate-2 CRITICAL run_0e68e235). `_trash_dir` deletes via
    trash/rmtree — NOT git — so deleting a tracked run's dir mutates the (public) git
    working tree even when git itself is unavailable. So we must distinguish:
      • git RAN and said untracked (returncode != 0) → genuinely untracked → False.
      • git RAN and said tracked (returncode == 0)   → tracked → True.
      • git could NOT run (OSError/timeout/subprocess error): INDETERMINATE. In a git
        working tree (`_has_git_dir`) we CANNOT rule out tracked → return True
        (fail-closed: skip the delete). Only when there is NO `.git` anywhere up the
        tree is there truly nothing to mutate → return False (non-git ws purges normally).
    The prior version returned False on ANY error, so a transient git hiccup under
    launchd (missing PATH / lock / timeout) would let the sweep trash a tracked public
    run unattended — the exact fail-open this closes."""
    import subprocess
    try:
        rel = path.relative_to(repo_root)
    except ValueError:
        return False
    try:
        r = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(rel)],
            cwd=str(repo_root), capture_output=True, timeout=15,
        )
        # Distinguish by EXACT rc (adversarial-review CONFIRMED bug): rc==0 tracked,
        # rc==1 genuinely untracked, rc>=128 FATAL (corrupt/locked index — a real
        # unattended failure mode). The old `return rc==0` collapsed rc==1 AND rc==128
        # into "untracked" → a corrupt index would let the sweep trash a TRACKED public
        # run (fail-open). Route any non-{0,1} code to the fail-CLOSED fallback.
        if r.returncode == 0:
            return True   # tracked
        if r.returncode == 1:
            return False  # genuinely untracked → delete-eligible
        return _has_git_dir(repo_root)  # fatal/unexpected → indeterminate → fail-closed
    except (OSError, subprocess.SubprocessError):
        # Indeterminate: fail-CLOSED inside a git working tree, open only if truly non-git.
        return _has_git_dir(repo_root)


def purge_garbage_runs(retention_days: float = 30.0, apply: bool = False,
                       include_tracked: bool = False) -> dict:
    """Retention purge: recoverably TRASH garbage run directories older than
    ``retention_days`` across ALL projects (run_0e68e235). SSOT for both the
    one-time cleanup script and the scheduled retention job — one definition, no
    drift (R25).

    Garbage = `_is_garbage_run` (abandoned OR crash-residue-paused, that never
    delivered). NEVER purges completed / cancelled / failed / running /
    genuine-decision-pause / delivered-but-mislabeled (they are not garbage) — nor
    FRESH garbage newer than the retention window (a just-crashed run may still be
    diagnosable). Age is measured on `abandoned_at` || `updated_at` || `created_at`.

    Dry-run is the DEFAULT (apply=False): reports `would_purge` and touches nothing.
    apply=True trashes the dirs (recoverable) and reports `purged`.

    🔒 GIT-TRACKED GUARD (Gate-2 CRITICAL, run_0e68e235): a run dir that is git-TRACKED
    is SKIPPED by default (counted in `skipped_tracked`). In the real workspace the
    SwarmAI project's `.artifacts/runs/*` ARE tracked (origin = the PUBLIC repo) — an
    earlier assumption that they were gitignored was WRONG (verified via `git ls-files`,
    not `git check-ignore`). Silently `git rm`-ing tracked runs from an unattended job
    would mutate the public working tree with no commit/guard. So the scheduled job
    only trashes UNTRACKED local garbage; deleting tracked garbage is a deliberate,
    user-owned decision (STEERING #5) gated behind `include_tracked=True`. Fail-safe:
    a non-git workspace has nothing tracked → everything purges normally.

    Ordering note (Gate-1 #6): this DELETES dirs that cleanup_orphans/proactive_
    intelligence earlier MARKED abandoned. The two never conflict — mark then delete,
    same direction; and _load_pipeline_runs tolerates a mid-scan-deleted dir
    (its read is wrapped in except OSError). Purge runs on a schedule, marking on
    session-refresh — different triggers, no race.
    """
    from datetime import datetime, timezone, timedelta

    ws = _get_workspace()
    projects_dir = ws / "Projects"
    if not projects_dir.exists():
        return {"would_purge": 0, "purged": 0, "projects_scanned": 0, "errors": 0,
                "skipped_tracked": 0}

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    would_purge = 0
    purged = 0
    errors = 0
    skipped_tracked = 0
    projects_scanned = 0

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue
        runs_dir = project_dir / ".artifacts" / "runs"
        if not runs_dir.exists():
            continue
        projects_scanned += 1

        for rd in list(runs_dir.iterdir()):
            if not rd.is_dir():
                continue
            run_file = rd / "run.json"
            if not run_file.exists():
                continue
            try:
                data = json.loads(run_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            if not _is_garbage_run(data):
                continue
            # A run whose worktree is not released yet keeps its record: the sweep
            # needs it to snapshot + release the worktree (pipeline-run-worktree §8.1).
            if any(isinstance(e, dict) and e.get("state") in ("active", "integrated")
                   for e in data.get("worktrees") or []):
                continue

            # Age gate: only purge garbage OLDER than the retention window.
            ts = data.get("abandoned_at") or data.get("updated_at") or data.get("created_at") or ""
            try:
                when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError, AttributeError):
                # Undated / unparseable timestamp: fail-CLOSED — treat as FRESH and
                # KEEP it (run: purge fail-open fix). The prior code treated undated
                # garbage as OLD and purged it, which is the unsafe direction for an
                # unattended destructive sweep: a malformed (not truly absent) ts, or a
                # schema drift that stops populating these fields, would silently make
                # EVERY run purge-eligible. A dead-residue dir that genuinely has no
                # timestamp costs little to keep; deleting a mis-dated run is not
                # recoverable-by-design. (AttributeError guards ts being a non-str.)
                continue
            if when >= cutoff:
                continue  # fresh garbage — keep for now

            # 🔒 GIT-TRACKED GUARD (Gate-2 CRITICAL): never silently delete a tracked
            # run — that would `git rm` from the public working tree unattended. Skip
            # unless the caller explicitly opts into the deliberate git-rm path.
            if not include_tracked and _is_git_tracked(run_file, ws):
                skipped_tracked += 1
                continue

            would_purge += 1
            if apply:
                try:
                    _trash_dir(rd)
                    purged += 1
                except Exception:  # noqa: BLE001 — count failure, never abort the sweep
                    errors += 1

    return {
        "would_purge": would_purge,
        "purged": purged,
        "skipped_tracked": skipped_tracked,
        "projects_scanned": projects_scanned,
        "errors": errors,
        "retention_days": retention_days,
        "applied": apply,
        "include_tracked": include_tracked,
    }


def _load_completed_runs(project: str, limit: int = 10) -> list[dict]:
    """Load completed pipeline runs for historical calibration.

    Scans the run path (runs/*/run.json).
    """
    artifacts_dir = _get_workspace() / "Projects" / project / ".artifacts"
    runs = []

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    # D8 (run_57929039): bucket via _effective_analytics_status, NOT the
                    # raw status string. A run that DELIVERED (completed reflect/deliver
                    # stage) but was later stamped 'abandoned'/'paused' (superseded /
                    # crash-after-the-fact) is a real completed run for CALIBRATION —
                    # excluding it (the old raw `== "completed"` check) undercounts
                    # history/analytics/budget calibration and drops exactly the
                    # delivered-but-mislabeled runs the normalizer exists to recover.
                    if _effective_analytics_status(state) == "completed" and state.get("stages"):
                        runs.append(state)
                        if len(runs) >= limit:
                            return runs
                except (json.JSONDecodeError, KeyError):
                    continue

    return runs


def _calibrated_stage_budget(project: str, stage: str) -> int:
    """Get calibrated token budget for a stage from historical data.
    Falls back to DEFAULT_STAGE_BUDGETS if no history."""
    runs = _load_completed_runs(project, limit=5)
    costs = []
    for r in runs:
        for s in r.get("stages", []):
            if s.get("stage") == stage and s.get("token_cost", 0) > 0:
                costs.append(s["token_cost"])
    if costs:
        avg = sum(costs) / len(costs)
        return int(avg * 1.2)  # 20% buffer over historical average
    return DEFAULT_STAGE_BUDGETS.get(stage, 30_000)


def _estimate_session_budget(project: str) -> dict:
    """Build a full budget estimate for a pipeline run."""
    stage_estimates = {}
    for stage in DEFAULT_STAGE_BUDGETS:
        stage_estimates[stage] = _calibrated_stage_budget(project, stage)

    return {
        "session_total": SESSION_BUDGET,
        "checkpoint_reserve": CHECKPOINT_RESERVE,
        "consumed": 0,
        "remaining": SESSION_BUDGET,
        "stage_estimates": stage_estimates,
        "calibration_source": "historical" if _load_completed_runs(project, 1) else "defaults",
    }


def _load_stage_artifacts(project: str, run_id: str, run_state: dict, stages=("evaluate", "deliver", "test")) -> dict:
    """{stage: artifact data} for this run's own stage artifacts (read contract:
    own-run + type enforced via _load_artifact_for_metrics)."""
    out: dict = {}
    for s in run_state.get("stages", []) or []:
        name = s.get("stage") if isinstance(s, dict) else None
        if name in stages and s.get("artifact_id"):
            loaded = _load_artifact_for_metrics(project, run_id, s["artifact_id"],
                                                _STAGE_ARTIFACT_TYPE.get(name))
            if loaded and isinstance(loaded.data, dict):
                out[name] = loaded.data
    return out


def _one_line(value) -> str:
    """Model text for one REPORT markdown line: newlines folded, ends stripped."""
    if not isinstance(value, (str, int, float)):
        return ""
    return " ".join(str(value).split())


def _review_parts(run_state: dict, delivery: dict) -> dict:
    """Shared three-part review content (see core.run_delivery_summary.review_summary).
    Never raises: an import or data problem yields empty parts, which render 未记录."""
    try:
        from core.run_delivery_summary import review_summary
        return review_summary(run_state, delivery)
    except Exception as exc:  # noqa: BLE001 — the report must still render
        print(json.dumps({"warning": f"review summary unavailable: {type(exc).__name__}"}),
              file=sys.stderr)
        # Fail CLOSED: an empty leftovers list would render "无遗留" / "None
        # identified" — the exact false-clean this summary exists to prevent.
        return {"solved": None, "approach": [],
                "leftovers": [f"review summary could not be built ({type(exc).__name__}); "
                              "leftovers were NOT checked — read the deliver artifact"]}


def _review_report_lines(parts: dict) -> list:
    """REPORT TL;DR rendering of the three parts: the shared renderer, full lists,
    leftovers pointed at §10 (which lists every one of them)."""
    try:
        from core.run_delivery_summary import render_review_lines
        return render_review_lines(parts, esc=lambda v, limit=None: _one_line(v),
                                   caps=False, leftovers_inline=False)
    except Exception as exc:  # noqa: BLE001 — the report must still render
        print(json.dumps({"warning": f"review summary render failed: {type(exc).__name__}"}),
              file=sys.stderr)
        return ["**解决了什么问题**", "- 未记录"]


def _delivery_summary_for(project: str, run_id: str) -> str:
    """Render the §4-E chat delivery summary for a run. Never raises."""
    try:
        from core.run_delivery_summary import build_delivery_summary
        run_file = _resolve_run_file(project, run_id)
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        arts = _load_stage_artifacts(project, run_id, run_state)
        if "test" in arts:
            tm = _report_test_metrics(arts["test"]) or {}
            arts["test"] = {k: tm.get(k) for k in ("tests_total", "failed",
                                                   "pre_existing_failures", "flaky_failures",
                                                   "tests_reused")}
        # This repo is the git root ONLY for the SwarmAI project's own runs; any
        # other project's files resolve their repo per file (repo_root=None).
        repo_root = _BACKEND_DIR.parent if project == "SwarmAI" else None
        try:
            return build_delivery_summary(run_state, arts, run_file.parent, repo_root=repo_root)
        except TypeError:
            if repo_root is not None:
                raise
            return build_delivery_summary(run_state, arts, run_file.parent)
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — summary must never break completion
        return f"(delivery summary unavailable: {type(exc).__name__}; run `artifact_cli.py run-summary --project {project} --run-id {run_id}`)"


def cmd_run_summary(args, reg: ArtifactRegistry) -> None:
    """Print the §4-E delivery summary for a run as {"_display_block": md}."""
    _resolve_run_file(args.project, args.run_id)  # exits 1 on an unknown run
    print(json.dumps({"run_id": args.run_id,
                      "_display_block": _delivery_summary_for(args.project, args.run_id)},
                     ensure_ascii=False))


def cmd_run_surface_changes(args, reg: ArtifactRegistry) -> None:
    """Read-only git-based COMPLETE sweep (run_608a6217).

    Stands on GIT — the author-agnostic change truth. `git status` on the SwarmWS
    tree + every bound source worktree sees main-agent / sub-agent / CLI-subprocess /
    post-session-hook writes ALIKE (unlike process-tracing, which the SDK's sidechain
    filtering makes blind to sub-agent writes). Classifies every changed path via
    needs_human_review and prints the four buckets as JSON:
      {"content":[...], "knowledge":[...], "source":[...], "process":[...]}
    The COMPLETE step surfaces content+knowledge to Canvas immediately (file_changed)
    and batch-emits source as PR-review rows via the surface_run_outputs tool
    (run_b8ea6d5c — the LOCAL_PR aggregate was removed). Never mutates any tree.
    """
    from core.run_surface_changes import sweep_run_changes

    ws_root = _get_workspace()
    buckets = sweep_run_changes(swarmws_root=ws_root)
    print(json.dumps(buckets.as_dict()))


_RED_TEAM_EXPERT_ID = "red-team"


def _red_team_required_now(run_state: dict, artifact: "dict | None") -> bool:
    """True when the Gate-2 Red Team HIGH trigger fires on this deliver artifact
    (a specialist HIGH on a strict profile) for a run the rule is ENFORCED on
    (run.json created_at >= pipeline_validator.RED_TEAM_CUTOVER). Independent of
    any `red_team_dispatched` claim — the caller proves the run by its marker."""
    import pipeline_validator as pv
    if not isinstance(artifact, dict) or not isinstance(run_state, dict):
        return False
    if _deterministic(run_state):
        return False  # a finding's severity is a model verdict: the ledger decides there
    if not pv._red_team_enforced(run_state.get("created_at") or ""):
        return False
    return bool(pv._red_team_triggered(artifact.get("adversarial_review"),
                                       run_state.get("profile") or "full"))


def _require_red_team_expert(run_state: dict, artifact: "dict | None") -> bool:
    """Append `red-team` to run_state["required_experts"] when the Red Team
    trigger fires (`_red_team_required_now`), so run-commit's D7 gate refuses
    unless the red-team EXPERT-ID marker exists for this run. This closes the
    legacy path (publish / run-update + run-commit) that never passes through
    stage-complete's own marker check. Append-only and idempotent, like
    `--add-required-expert`; a malformed (non-list) value is left alone
    (run-commit warns about it). Returns True when run_state changed."""
    if not _red_team_required_now(run_state, artifact):
        return False
    req = run_state.get("required_experts")
    if req is None:
        req = []
    if not isinstance(req, list):
        return False
    if any(isinstance(e, str) and e.strip().lower() == _RED_TEAM_EXPERT_ID for e in req):
        return False
    run_state["required_experts"] = [*req, _RED_TEAM_EXPERT_ID]
    return True


GATE1_PROFILES = ("full", "bugfix")  # stages/ref/build/gate1.md: BUILD always runs Gate-1


# _write_local_pr REMOVED (run_b8ea6d5c): LOCAL_PR.md aggregation deleted. The
# review deliverable is now per-file PR-review rows in the Canvas OUTPUTS rail,
# emitted at COMPLETE by the surface_run_outputs tool (ui_actions.build_surface_events
# → orchestrator observe-emit). run-commit records commits; it no longer writes a doc.


# ── release-gate: the code-enforced CI-green gate for s_swarm-release 7b/7c ──────
#
# Design A (run_9fec1fb1, 2026-07-04; hook→flow-step run_d613bb27): `--poll` is the
# ONLY writer of the CI-green marker; `--verify` is the fail-closed reader that
# authorizes the publish (the "check CI" half + the "enforce" half, both in this CLI).
# `--verify` REPLACES the old `release_publish_guard` PreToolUse hook — a per-command
# product-wide hook was the wrong layer for SwarmAI's OWN release discipline, so the
# enforce step moved into the release flow (s_swarm-release 7c calls `--verify` before
# `gh release edit --draft=false`). Both `--poll` (network `gh run list`) and `--verify`
# (local marker read + `git rev-parse`) run as explicit one-time CLI calls, so neither
# reintroduces the foreground-timeout hang trap that lived in the 7b runbook poll.
#
# Marker: Projects/<project>/.artifacts/.release-ci-green.json
#   {"head_sha": "<40-char>", "repo_root": "<abs>", "run_id": <int>, "tag": "<opt>",
#    "conclusion": "success", "ts": "<iso>"}
# --verify authorizes publish IFF the marker attests the commit being released (tag-anchored
# via the marker's `tag`, else HEAD-anchored). A stale marker authorizes nothing — fail-closed.

def _release_marker_path(project: str) -> Path:
    return (_get_workspace() / "Projects" / project / ".artifacts"
            / ".release-ci-green.json")


def _release_marker_authorizes_head(
    marker: Path, repo_root: str | None, published_tag: str | None = None,
) -> tuple[bool, str]:
    """(authorized, reason). True IFF the CI-green marker attests the commit BEING
    PUBLISHED. Moved from security_hooks.release_publish_guard (run_d613bb27) — the
    pre-publish check is now a one-time gate in the release flow, not a per-command
    hook. Fail-CLOSED on any failure.

    Two modes, chosen by whether the marker records a `tag`:
    - **Tag-anchored** (marker has `tag`): resolve the PUBLISHED tag to its commit
      LOCALLY and require it == marker.head_sha (CI green on the exact shipped commit).
    - **HEAD-anchored** (legacy, no `tag`): require marker.head_sha == current HEAD.
    """
    import subprocess
    if not marker.exists():
        return False, "no CI-green marker (run `artifact_cli.py release-gate --poll` until PASS)"
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return False, f"marker unreadable: {type(e).__name__}"
    marker_head = data.get("head_sha") or ""
    # fail-CLOSED: the marker MUST record its own repo_root (--poll always writes it).
    # Do NOT fall back to the caller's cwd — a marker missing repo_root is a stale/
    # malformed marker and must BLOCK, exactly as the original release_publish_guard did.
    marker_repo = data.get("repo_root") or ""
    marker_tag = data.get("tag") or ""
    if not marker_head or not marker_repo:
        return False, "marker missing head_sha/repo_root (stale format — re-poll release-gate)"

    def _rev_parse(spec: str) -> str | None:
        try:
            r = subprocess.run(["git", "-C", marker_repo, "rev-parse", spec],
                               capture_output=True, text=True, timeout=10)
            return r.stdout.strip() if r.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError):
            return None

    if marker_tag:
        if not published_tag:
            return (False,
                    f"marker is tag-anchored (tag {marker_tag}) but the publish names no "
                    "tag to verify — fail-closed (pass --ref <tag>, or re-poll release-gate)")
        published_commit = _rev_parse(f"{published_tag}^{{commit}}")
        if not published_commit:
            return (False,
                    f"cannot resolve published tag '{published_tag}' in {marker_repo} "
                    "(local tag missing?) — fail-closed")
        if published_commit != marker_head:
            return (False,
                    f"published tag {published_tag} → {published_commit[:8]} != CI-verified "
                    f"commit {marker_head[:8]} (marker tag {marker_tag}) — CI not green on the "
                    "commit being published")
        return (True,
                f"CI green on {marker_head[:8]} — published tag {published_tag} matches "
                f"marker tag {marker_tag} (run {data.get('run_id')})")

    head = _rev_parse("HEAD")
    if not head:
        return False, f"git HEAD unresolved in marker repo_root {marker_repo}"
    if marker_head != head:
        return False, f"marker HEAD {marker_head[:8]} != current HEAD {head[:8]} (stale — CI not green on THIS HEAD)"
    return True, f"CI green on HEAD {head[:8]} (run {data.get('run_id')})"


def cmd_release_gate(args, reg: "ArtifactRegistry") -> None:
    """CI-green gate for the release skill's Stage 7b (design A, run_9fec1fb1).

    --poll: do ONE `gh run list` check for the CI run on the CURRENT git HEAD.
      - CI conclusion == success  → atomically write the marker, print PASS, exit 0
      - CI completed but red       → print BLOCK + failing conclusion, exit 1 (no marker)
      - CI not done / not registered → print WAIT, exit 3 (agent should sleep + re-poll)
    --clear: delete the marker (call after a successful publish, or to reset).
    --status: print current marker vs HEAD without touching CI.

    NEVER loops internally — the AGENT drives polling (one fast call per invocation),
    so no long-lived process can be foreground-timeout-killed. Bounded git/gh calls
    each carry a timeout.
    """
    import subprocess
    from datetime import datetime, timezone

    marker = _release_marker_path(args.project)

    ref = getattr(args, "ref", None)  # optional tag/sha of the commit BEING RELEASED

    def _head_and_root() -> tuple[str | None, str | None]:
        """(commit sha, repo root) of the CWD's git repo. The CLI is run by the agent
        via `cd $SWARMAI_ROOT && ...`, so this resolves the SOURCE repo. Both are
        recorded in the marker so the (differently-cwd'd) hook can re-resolve against
        the SAME repo via `git -C <repo_root>` — never its own daemon cwd.

        When `--ref <tag-or-sha>` is given, resolve THAT ref to its commit
        (`<ref>^{commit}` — derefs an annotated tag to the commit it points at, and
        is idempotent on a plain sha). This is the fix for tag-based releases: the
        gate must verify the commit BEING RELEASED (the tag target), not the moving
        branch tip. No `--ref` → resolve HEAD (unchanged legacy behavior)."""
        target = f"{ref}^{{commit}}" if ref else "HEAD"
        try:
            r = subprocess.run(["git", "rev-parse", target, "--show-toplevel"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode != 0:
                return None, None
            lines = r.stdout.strip().splitlines()
            head = lines[0].strip() if lines else None
            root = lines[1].strip() if len(lines) > 1 else None
            return head, root
        except (subprocess.TimeoutExpired, OSError):
            return None, None

    def _head() -> str | None:
        return _head_and_root()[0]

    if getattr(args, "clear", False):
        existed = marker.exists()
        marker.unlink(missing_ok=True)
        print(json.dumps({"cleared": existed, "marker": str(marker)}))
        return

    head, repo_root = _head_and_root()
    if not head:
        print(json.dumps({"error": "cannot resolve git HEAD (not a repo / git unavailable)"}),
              file=sys.stderr)
        sys.exit(2)

    if getattr(args, "status", False):
        cur = None
        if marker.exists():
            try:
                cur = json.loads(marker.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                cur = None
        matches = bool(cur and cur.get("head_sha") == head)
        print(json.dumps({"head": head, "marker": cur, "authorizes_current_head": matches}))
        return

    if getattr(args, "verify", False):
        # --verify: the PRE-PUBLISH gate. Formerly the `release_publish_guard`
        # PreToolUse hook; moved into the release flow (s_swarm-release Stage 7b)
        # as a one-time check instead of a per-command runtime hook (a self-dev
        # concern does not belong in the product-wide hook layer). Fail-CLOSED:
        # authorizes the publish IFF the CI-green marker attests the commit being
        # released. Prints the verdict; exit 0 = authorized, exit 1 = blocked.
        # All-local (marker read + `git -C <repo_root> rev-parse`), never network.
        ok, reason = _release_marker_authorizes_head(
            marker, repo_root, published_tag=ref,
        )
        print(json.dumps({"state": "PASS" if ok else "BLOCK", "reason": reason,
                          "head": head, "published_ref": ref}),
              file=sys.stdout if ok else sys.stderr)
        sys.exit(0 if ok else 1)

    # --poll (default): one gh check for the CI run on the target commit.
    # With --ref, query by the resolved COMMIT (`gh run list --commit <sha>`), NOT
    # a limited `--branch main` window: a re-pointed tag's commit can sit many
    # commits behind the moving tip and fall outside `--limit N`, making the gate
    # falsely WAIT forever (the exact class of bug this fix exists to kill). The
    # commit-filtered query is exact and window-independent. No --ref → legacy
    # branch-tip scan (unchanged).
    gh_cmd = (["gh", "run", "list", "--commit", head, "--limit", "20",
               "--json", "databaseId,name,headSha,status,conclusion"]
              if ref else
              ["gh", "run", "list", "--branch", "main", "--limit", "8",
               "--json", "databaseId,name,headSha,status,conclusion"])
    try:
        r = subprocess.run(gh_cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        print(json.dumps({"state": "WAIT", "reason": "gh run list timed out (30s) — retry"}),
              file=sys.stderr)
        sys.exit(3)
    except OSError as e:
        print(json.dumps({"error": f"gh unavailable: {e}"}), file=sys.stderr)
        sys.exit(2)

    if r.returncode != 0:
        print(json.dumps({"state": "WAIT", "reason": f"gh error: {r.stderr.strip()[:160]}"}),
              file=sys.stderr)
        sys.exit(3)

    try:
        runs = json.loads(r.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"state": "WAIT", "reason": "gh returned non-JSON — retry"}),
              file=sys.stderr)
        sys.exit(3)

    ci = next((x for x in runs
               if x.get("name") == "CI" and (x.get("headSha") or "").startswith(head[:8])),
              None)
    if ci is None:
        print(json.dumps({"state": "WAIT", "reason": "CI run not registered for HEAD yet",
                          "head": head}))
        sys.exit(3)

    status, concl = ci.get("status"), ci.get("conclusion")
    if status != "completed":
        print(json.dumps({"state": "WAIT", "status": status, "run_id": ci.get("databaseId"),
                          "head": head}))
        sys.exit(3)

    if concl == "success":
        # Atomic write (temp + replace) — mirror the file-write discipline used elsewhere.
        # Register the project first — this .artifacts/ mkdir would otherwise mint an
        # unregistered Projects/<name>/ (pseudo-DDD) if release-gate ran before run-create.
        _ensure_project_dir(args.project)
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = {"head_sha": head, "repo_root": repo_root,
                   "run_id": ci.get("databaseId"),
                   "conclusion": "success",
                   "ts": datetime.now(timezone.utc).isoformat()}
        # Record the released ref (tag/sha) so the publish guard can verify the
        # PUBLISHED tag derefs to this same commit — the marker is authoritative,
        # the hook does one LOCAL deref and compares (no network). Absent when the
        # gate ran without --ref (legacy branch-tip release → hook falls back to HEAD).
        if ref:
            payload["tag"] = ref
        tmp = marker.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(marker)
        print(json.dumps({"state": "PASS", **payload, "marker": str(marker)}))
        return

    # completed + red
    print(json.dumps({"state": "BLOCK", "conclusion": concl,
                      "run_id": ci.get("databaseId"), "head": head,
                      "hint": "gh run view <run_id> --json jobs → fix → push → re-poll"}),
          file=sys.stderr)
    sys.exit(1)


def cmd_run_get(args, reg: ArtifactRegistry) -> None:
    """Get a pipeline run's current state.

    ``--brief`` prints the compact view (``_run_brief``), ``--field PATH`` (repeatable,
    dot path, list index as a number) prints only those values; both single-line JSON.
    They replace the inline ``python3 -c "json.load(open('…/run.json'))…"`` calls
    agents used to write (1,075 such reads of run.json in one week of transcripts).
    """
    brief = getattr(args, "brief", False)
    fields = getattr(args, "field", None) or []
    if (brief or fields) and not args.run_id:
        print(json.dumps({"error": "--brief / --field need --run-id"}), file=sys.stderr)
        sys.exit(1)
    if args.run_id:
        run_file = _resolve_run_file(args.project, args.run_id)
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        if fields:
            print(json.dumps({p: _run_field(run_state, p) for p in fields}, ensure_ascii=False))
        elif brief:
            print(json.dumps(_run_brief(run_state, run_file.parent), ensure_ascii=False))
        else:
            print(json.dumps(run_state, indent=2))
        return

    # List all pipeline runs for this project (scan both new and legacy paths)
    runs = []
    artifacts_dir = _get_workspace() / "Projects" / args.project / ".artifacts"

    # New path: .artifacts/runs/*/run.json
    runs_dir = artifacts_dir / "runs"
    if runs_dir.exists():
        for rd in sorted(runs_dir.iterdir(), reverse=True):
            run_file = rd / "run.json"
            if run_file.exists():
                try:
                    state = json.loads(run_file.read_text(encoding="utf-8"))
                    runs.append(_run_summary(state))
                except (json.JSONDecodeError, KeyError):
                    continue

    print(json.dumps({"runs": runs, "count": len(runs)}, indent=2))


def _run_summary(state: dict) -> dict:
    """Extract summary fields from a pipeline run state."""
    return {
        "id": state["id"],
        "requirement": state["requirement"][:80],
        "status": state["status"],
        "profile": state.get("profile"),
        "stages_completed": sum(
            1 for s in state.get("stages", []) if s.get("status") == "completed"
        ),
        "created_at": state["created_at"],
    }


def _run_field(state, path: str):
    """``a.b.0.c`` out of run.json; a path that does not exist reads as ``None``
    with ``{"missing": path}`` so it is never confused with a stored null."""
    cur = state
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.lstrip("-").isdigit() and -len(cur) <= int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return {"missing": path}
    return cur


_BRIEF_SUMMARY = 160
_BRIEF_FAILURES = 10


def _latest_tests(run_dir: Path) -> list:
    """One line per test mode from ``evidence/tests/latest-<mode>.json``: when it
    finished, its counts and the failing node ids (first ``_BRIEF_FAILURES``)."""
    out = []
    tests = run_dir / "evidence" / "tests"
    if not tests.is_dir():
        return out
    for f in sorted(tests.glob("latest-*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(d, dict):
            continue
        failures = [x.get("nodeid") for x in d.get("failures") or [] if isinstance(x, dict)]
        out.append({
            "mode": d.get("mode") or f.stem[len("latest-"):],
            "finished_at": d.get("finished_at"),
            "counts": d.get("counts"),
            "failures": failures[:_BRIEF_FAILURES],
            "more_failures": max(0, len(failures) - _BRIEF_FAILURES),
            "evidence": f"evidence/tests/{f.name}",
        })
    return out


def _run_brief(state: dict, run_dir: Path) -> dict:
    """The fields agents read run.json for, measured from a week of transcripts
    (status, stages, failures, counts, files_touched, …), without the stage decisions,
    token bookkeeping and hand-outs that make run.json ~16 KB."""
    done = {s.get("stage"): s for s in state.get("stages") or [] if isinstance(s, dict)}
    plan = _get_profile_stages(normalize_profile(state.get("profile")))
    current = next((st for st in plan if (done.get(st) or {}).get("status") != "completed"), None)
    stages = []
    for st in plan + [s for s in done if s not in plan]:
        s = done.get(st)
        row = {"stage": st, "status": (s or {}).get("status", "pending")}
        summary = (s or {}).get("summary")
        if isinstance(summary, str) and summary:
            row["summary"] = summary if len(summary) <= _BRIEF_SUMMARY else summary[: _BRIEF_SUMMARY - 1] + "…"
        stages.append(row)
    cp = state.get("checkpoint") if isinstance(state.get("checkpoint"), dict) else None
    return {
        "id": state.get("id"),
        "status": state.get("status"),
        "profile": state.get("profile"),
        "current_stage": current,
        "stages": stages,
        "files_touched": state.get("files_touched") or [],
        "worktree_mode": state.get("worktree_mode"),
        "worktrees": state.get("worktrees") or [],
        "checkpoint": {k: cp.get(k) for k in ("reason", "stage", "checkpointed_at", "resumed_at")} if cp else None,
        "tests": _latest_tests(run_dir),
        "updated_at": state.get("updated_at"),
        "full": "run-get --run-id <id> (whole run.json) or --field <dot.path>",
    }


# ── Checkpoint guard vocabulary (single source of truth) ──────────────
# True triggers: a checkpoint with any of these in its reason is a REAL,
# measurement- or event-backed pause and is always allowed.
_CHECKPOINT_TRUE_TRIGGERS = (
    # NOTE: "budget" is DELIBERATELY ABSENT (removed 2026-10: budget is never a
    # legitimate pause reason — STEERING #2 + XG directive "budget 从来不应该是 block run
    # 的重要因素"). A budget-worded pause reason therefore no longer counts as a true
    # trigger: it is refused by both pause gates and no longer protected from auto-abandon.
    # Do NOT re-add it. "exhaust" stays (resource/retry exhaustion, not token budget).
    "l2", "block", "error", "crash", "escalat", "stuck",
    "judgment", "retry", "gate_spawn_blocked", "exhaust",
    "git revert", "external", "mutation", "re-baseline",  # event-triggers (GC11 kept half)
)
# Prefix-stems: triggers meant to match a word-START + any inflection
# ("escalat" → escalate/escalated/escalation). Without this they were matched as
# whole words (\bescalat\b), which never hit a real inflection → the stem was dead
# (Gate-2 informational finding, run_17e3399c). Listed explicitly so the default
# stays strict whole-word for everything else (no substring leaks).
_CHECKPOINT_TRUE_TRIGGER_STEMS = frozenset({"escalat"})


def _checkpoint_reason_has_true_trigger(reason: str) -> bool:
    """True iff reason contains a true-trigger as a WHOLE WORD (not substring).

    Substring matching let 'blocked'/'roadblock' satisfy 'block' and 'budget hygiene'
    satisfy 'budget' — a confabulation reason rode a borrowed keyword (adversarial
    run_a822b3e8). Word-boundary match closes that.
    """
    r = reason.lower()
    for t in _CHECKPOINT_TRUE_TRIGGERS:
        if " " in t or "-" in t or "_" in t:
            # multi-word/event triggers (e.g. "git revert", "re-baseline") match as phrase
            pat = re.escape(t)
        elif t in _CHECKPOINT_TRUE_TRIGGER_STEMS:
            # prefix-stems (e.g. "escalat" → escalat/escalate/escalation/escalated):
            # word-START boundary + allow trailing word chars. A bare \bescalat\b
            # never matched any real inflection (the trailing letters are word chars),
            # so the stem was DEAD — this revives it without reopening substring leaks
            # ('escalat' still can't match mid-word like 'deescalation' would need a
            # leading boundary, which \b provides).
            pat = rf"\b{re.escape(t)}\w*"
        else:
            # single whole-word tokens must match on both boundaries (so 'block'
            # does NOT match 'roadblock'/'blocked' — run_a822b3e8).
            pat = rf"\b{re.escape(t)}\b"
        if re.search(pat, r):
            return True
    return False


# ─────────────────────── Autonomous-rate observability (AIDLC design §6) ──────
# Classify a checkpoint reason into an intervention KIND, reusing the SAME
# _CHECKPOINT_TRUE_TRIGGERS vocabulary (no new taxonomy — design §4.2). The
# mapping is deliberately NARROW: only reasons that imply a HUMAN was in the loop
# become a human-intervention kind; systemic events (budget/crash/retry) map to
# None (they are the pipeline pausing itself, not a person stepping in).
#
# Mapping (Gate-1-corrected to tokens that ACTUALLY exist in the trigger tuple):
#   "judgment"                        -> "judgment"  (a human judgment-class decision)
#   "l2" / "block" / gate_spawn_blocked -> "unblock"  (a human cleared a block)
#   "external"                        -> "approval"   (an external approval gate)
#   everything else systemic          -> None         (not a human intervention)
# Precedence: judgment > approval > unblock (most-human-specific wins), so a reason
# mentioning both a judgment call AND a block classifies as judgment.
def _classify_intervention_kind(reason: "str | None") -> "str | None":
    """Map a checkpoint reason to an intervention kind, or None if systemic.

    Pure function (no I/O). Reuses _checkpoint_reason_has_true_trigger's word-
    boundary semantics via the shared _CHECKPOINT_TRUE_TRIGGERS tuple so the
    classifier can never drift from the pause vocabulary (design §4.2).
    """
    if not reason:
        return None
    r = reason.lower()

    def _has(token: str) -> bool:
        # Whole-word match, mirroring _checkpoint_reason_has_true_trigger so
        # 'block' does not match 'roadblock'. Multi-word tokens match as phrase.
        if " " in token or "-" in token:
            pat = re.escape(token)
        elif token in _CHECKPOINT_TRUE_TRIGGER_STEMS:
            pat = rf"\b{re.escape(token)}\w*"
        else:
            pat = rf"\b{re.escape(token)}\b"
        return bool(re.search(pat, r))

    # Precedence order: most-human-specific first.
    if _has("judgment"):
        return "judgment"
    if _has("external"):
        return "approval"
    if _has("l2") or _has("block") or _has("gate_spawn_blocked"):
        return "unblock"
    return None


def _append_intervention(run_state: dict, *, stage: str, kind: str,
                         avoidable: bool, note: str) -> None:
    """Append one intervention record to run_state['interventions'] (lazy-init).

    Mutates run_state in place. interventions[] is absent-by-default (same
    convention as commits/checkpoint — no migration of existing runs); readers
    use .get('interventions', []). note is truncated to 200 chars.
    """
    run_state.setdefault("interventions", []).append({
        "stage": stage,
        "kind": kind,
        "avoidable": bool(avoidable),
        "note": (note or "")[:200],
    })


def _infer_codefix_from_commits(
    commits: "list[dict] | None", files_touched: "list[str] | None",
    *, normalizer=None, known_generated: "list[str] | None" = None
) -> "list[str]":
    """Return committed files that the run did NOT self-report in files_touched.

    These are the human-codefix blindspot (design §4.2): a file landed in a
    run's commit but the agent never recorded editing it → a human edited it.

    HONEST LOWER-BOUND: files_touched is None (legacy / not recorded) means we
    CANNOT determine what the agent touched, so we infer NOTHING — never a FALSE
    codefix. An EMPTY list [] is different: it means "recorded, zero files", so
    every committed file is genuinely extra.

    PATH NORMALIZATION (Gate-2 F1): committed[].files is git repo-relative;
    files_touched is free-form (often ABSOLUTE). A raw compare marks EVERY file a
    codefix when files_touched is absolute → a FALSE codefix that deflates the
    rate below true (the opposite of the honest-upper-bound guarantee). The real
    caller injects ``normalizer(repo, files_touched) -> set[repo-relative paths]``
    (git ls-files, same idiom cmd_run_commit already uses for this exact format
    gap). ``normalizer=None`` (the default) treats files_touched as already
    comparable — keeps this a PURE function for unit tests with synthetic
    repo-relative paths. Pure core; the git fact is injected, not baked in.

    ``known_generated`` (#4): absolute paths of SYSTEM-GENERATED deliverables (the
    DELIVER-time derivative "B" under ``.aidlc/deliverables/<run_id>/``) that land
    in the commit but are NOT agent-edited source — so they must NOT count as a
    human codefix. They are normalized through the SAME per-repo ``normalizer`` as
    files_touched (B is absolute, committed[].files is repo-relative) and treated
    as tracked, so B never enters ``extra``. The exclusion is PRECISE (only the
    exact B paths of THIS run) — a genuine unreported human edit still surfaces
    (NOT fail-open). ``known_generated=None`` → no exclusion (default, unchanged).
    """
    if files_touched is None:
        return []  # cannot determine → honest lower-bound, no inference
    extra: list[str] = []
    seen: set[str] = set()
    cache: dict[str, set] = {}
    gen_cache: dict[str, set] = {}
    for c in (commits or []):
        if not isinstance(c, dict):
            continue
        repo = c.get("repo") or ""
        if repo not in cache:
            cache[repo] = (normalizer(repo, files_touched) if normalizer
                           else set(files_touched))
            # Normalize the known-generated set the SAME way, per repo — so a B
            # absolute path resolves to the repo-relative form committed[].files
            # uses. A B path foreign to this repo normalizes to nothing (harmless).
            gen_cache[repo] = (
                (normalizer(repo, known_generated) if normalizer
                 else set(known_generated))
                if known_generated else set()
            )
        tracked = cache[repo]
        generated = gen_cache[repo]
        for f in (c.get("files") or []):
            if f not in tracked and f not in generated and f not in seen:
                extra.append(f)
                seen.add(f)
    return extra


def _compute_should_checkpoint(run_state: dict, project: str) -> dict:
    """Compute should_checkpoint + budget numbers for a run (single source).

    Extracted so cmd_run_budget AND the checkpoint guard share ONE calculation
    (STEERING #3 — no duplicate logic). Returns the dict run-budget reports.
    """
    budget = run_state.get("budget", _estimate_session_budget(project))
    consumed = sum(s.get("token_cost", 0) for s in run_state.get("stages", []))
    remaining = budget["session_total"] - consumed
    usable = remaining - budget["checkpoint_reserve"]

    completed_stages = {s.get("stage", "unknown") for s in run_state.get("stages", []) if s.get("status") == "completed"}
    profile_stages = _get_profile_stages(run_state.get("profile", "full"))
    next_stage = next((s for s in profile_stages if s not in completed_stages), None)
    next_stage_estimate = budget.get("stage_estimates", DEFAULT_STAGE_BUDGETS).get(next_stage, 30_000) if next_stage else 0

    # Budget is NEVER a checkpoint/block trigger (STEERING #2: a budget control that
    # truncates in-progress real work = 容灾 masquerading as business logic — banned;
    # XG directive "budget 从来不应该是 block run 的重要因素"; measured 0/809 runs ever
    # blocked on budget). The two former token judgments — `usable < next_stage_estimate`
    # and `pct_consumed > 0.7` — are deleted. consumed/remaining/usable/pct_consumed are
    # STILL computed and returned for run-budget REPORTING (STEERING #3 one-calc-shared);
    # they are informational numbers, not a verdict. Physical context overflow is handled
    # by SDK autocompact (session_unit), not this function. should_checkpoint is hard-False.
    pct_consumed = consumed / budget["session_total"] if budget["session_total"] > 0 else 0

    return {
        "consumed": consumed, "remaining": remaining, "usable": usable,
        "pct_consumed": round(pct_consumed * 100, 1), "next_stage": next_stage,
        "next_stage_estimate": next_stage_estimate, "should_checkpoint": False,
        "budget_total": budget["session_total"],
        "calibration_source": budget.get("calibration_source", "defaults"),
    }


def cmd_run_history(args, reg: ArtifactRegistry) -> None:
    """Show historical token costs per stage from completed pipeline runs.

    Used for calibration: the agent reads this to know how many tokens
    each stage actually consumes, not just the default estimates.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
            "stage_averages": {},
            "calibration": "defaults",
        }))
        return

    # Aggregate token costs per stage
    stage_costs: dict[str, list[int]] = {}
    for r in runs:
        for s in r.get("stages", []):
            name = s.get("stage", "unknown")
            cost = s.get("token_cost", 0)
            if cost > 0:
                stage_costs.setdefault(name, []).append(cost)

    averages = {}
    for stage, costs in sorted(stage_costs.items()):
        avg = sum(costs) / len(costs)
        averages[stage] = {
            "avg_tokens": int(avg),
            "min_tokens": min(costs),
            "max_tokens": max(costs),
            "samples": len(costs),
            "calibrated_estimate": int(avg * 1.2),  # +20% buffer
        }

    # Pipeline-level stats
    run_totals = []
    for r in runs:
        total = sum(s.get("token_cost", 0) for s in r.get("stages", []))
        if total > 0:
            run_totals.append(total)

    pipeline_stats = {}
    if run_totals:
        pipeline_stats = {
            "avg_total_tokens": int(sum(run_totals) / len(run_totals)),
            "min_total_tokens": min(run_totals),
            "max_total_tokens": max(run_totals),
            "runs_analyzed": len(run_totals),
            "fits_single_session": int(sum(run_totals) / len(run_totals)) < SESSION_BUDGET,
        }

    print(json.dumps({
        "project": args.project,
        "stage_averages": averages,
        "pipeline_stats": pipeline_stats,
        "calibration": "historical",
    }, indent=2))


def cmd_run_budget(args, reg: ArtifactRegistry) -> None:
    """Report budget CONSUMPTION for an active pipeline run (informational only).

    Reports consumed tokens, remaining budget, and % consumed as NUMBERS for
    visibility. Budget is NEVER a checkpoint/block trigger (STEERING #2 + XG
    directive "budget 从来不应该是 block run 的重要因素") — `should_checkpoint` is
    always False here; a real checkpoint comes only from a true-trigger reason
    (L2/retry/stuck) via the pause gates, never from these numbers. Physical
    context overflow is handled by SDK autocompact, not this report.
    """
    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    # Single source of truth for should_checkpoint + budget numbers (STEERING #3).
    info = _compute_should_checkpoint(run_state, args.project)
    next_stage = info["next_stage"]
    result = {
        "pipeline_id": args.run_id,
        "budget_total": info["budget_total"],
        "consumed": info["consumed"],
        "remaining": info["remaining"],
        "usable": info["usable"],
        "pct_consumed": info["pct_consumed"],
        "next_stage": next_stage,
        "next_stage_estimate": info["next_stage_estimate"],
        # Always False — budget is not a checkpoint trigger (STEERING #2). Kept for
        # consumer compatibility; the reason is a plain consumption report, never a
        # checkpoint verdict (the former "Budget insufficient"/"Context quality
        # degradation" branches were counterfactual once budget stopped blocking).
        "should_checkpoint": info["should_checkpoint"],
        "reason": f"Budget {info['pct_consumed']}% consumed (informational — budget never blocks a run)",
        "calibration_source": info["calibration_source"],
    }
    print(json.dumps(result, indent=2))


_get_profile_stages = get_profile_stages  # alias for backward compat within this file


def _status_entry(state: dict, project_name: str) -> dict:
    """Build a dashboard entry from a pipeline run state dict."""
    completed_stages = [s for s in state.get("stages", []) if s.get("status") == "completed"]
    total_stages = len(_get_profile_stages(state.get("profile")))
    consumed = sum(s.get("token_cost", 0) for s in state.get("stages", []))
    # Terminal-but-crashed presentation (parity with routers/pipelines.py _to_response):
    # a run that finished all stages (completed reflect/deliver) but crashed before
    # `run-update --status completed` is left status=running/paused on disk. Present it
    # as completed here too, so the CLI `run-status` dashboard and the web dashboard
    # agree — otherwise the two surfaces split-brain (CLI "running" vs web "completed")
    # for the identical run.json. Read-path only; disk is not mutated. Explicit terminal
    # statuses (failed/cancelled/abandoned) pass through is_terminal_run unchanged.
    raw_status = state.get("status", "running")
    display_status = raw_status
    if raw_status in ("running", "paused") and is_terminal_run(state):
        display_status = "completed"
    return {
        "id": state["id"],
        "project": project_name,
        "requirement": state.get("requirement", "")[:80],
        "status": display_status,
        "profile": state.get("profile", "full"),
        "progress": f"{len(completed_stages)}/{total_stages}",
        "stages_completed": len(completed_stages),
        "stages_total": total_stages,
        "tokens_consumed": consumed,
        "taste_decisions": len(state.get("taste_decisions", [])),
        "checkpoint": state.get("checkpoint"),
        # Surface WHY a run was abandoned so the dashboard can distinguish an
        # unrecovered orphan (orphaned_no_resume) from a legitimate supersession
        # (superseded_by_<id>). Present-but-None for non-abandoned runs, mirroring
        # the `checkpoint` key above (additive contract — never removes a key).
        "abandon_reason": state.get("abandon_reason"),
        # completed ≠ verified (_completion_verdict): None when not applicable.
        "verified": (_completion_verdict(state)[0] if display_status == "completed" else None),
        "created_at": state.get("created_at", ""),
        "updated_at": state.get("updated_at", ""),
    }


def cmd_run_status(args, reg: ArtifactRegistry) -> None:
    """Cross-project pipeline dashboard data.

    Returns all active and recent pipeline runs across all projects.
    Designed for the Radar sidebar pipeline panel.
    """
    workspace = _get_workspace()
    projects_dir = workspace / "Projects"
    if not projects_dir.exists():
        print(json.dumps({"pipelines": [], "count": 0}))
        return

    all_pipelines = []
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        artifacts_dir = project_dir / ".artifacts"
        if not artifacts_dir.exists():
            continue

        project_name = project_dir.name

        # New path: runs/*/run.json
        runs_dir = artifacts_dir / "runs"
        if runs_dir.exists():
            for rd in sorted(runs_dir.iterdir(), reverse=True):
                rf = rd / "run.json"
                if rf.exists():
                    try:
                        state = json.loads(rf.read_text(encoding="utf-8"))
                        state["_project"] = project_name
                        all_pipelines.append(_status_entry(state, project_name))
                    except (json.JSONDecodeError, OSError, KeyError):
                        continue

    # Sort: running first, then paused, then completed. Within each group, newest first.
    # ISO timestamps sort lexicographically, so negate by prepending complement for descending.
    status_order = {"running": 0, "paused": 1, "failed": 2, "completed": 3, "cancelled": 4}
    all_pipelines.sort(key=lambda p: p.get("updated_at", ""), reverse=True)  # newest first
    all_pipelines.sort(key=lambda p: status_order.get(p["status"], 9))  # stable: preserves newest-first within group

    # Limit: show all active (running/paused), up to 5 completed per project
    active = [p for p in all_pipelines if p["status"] in ("running", "paused")]
    completed = [p for p in all_pipelines if p["status"] not in ("running", "paused")]

    if args.active_only:
        output = active
    else:
        # Keep max 5 completed per project
        seen_completed: dict[str, int] = {}
        filtered_completed = []
        for p in completed:
            count = seen_completed.get(p["project"], 0)
            if count < 5:
                filtered_completed.append(p)
                seen_completed[p["project"]] = count + 1
        output = active + filtered_completed

    # Gap-2: split abandoned into GENUINE failures (crash/OOM/orphan) vs
    # REPLACED duplicates (superseded_by_*). The superseded_by_* reason is
    # written ONLY by proactive_intelligence under a newest-completed-successor
    # guard, so it always denotes a rerun that a completed successor replaced —
    # counting it as a failure double-counts one work unit and depresses the
    # completion rate a reader/judge computes. Keep `abandoned` = genuine
    # failures; surface `superseded` separately (neither success nor failure).
    def _is_superseded(p: dict) -> bool:
        return (p["status"] == "abandoned"
                and str(p.get("abandon_reason") or "").startswith("superseded_by_"))
    abandoned_all = [p for p in all_pipelines if p["status"] == "abandoned"]
    superseded = [p for p in abandoned_all if _is_superseded(p)]
    summary = {
        "running": sum(1 for p in all_pipelines if p["status"] == "running"),
        "paused": sum(1 for p in all_pipelines if p["status"] == "paused"),
        "completed": sum(1 for p in all_pipelines if p["status"] == "completed"),
        # Genuine failures only (crash/OOM/orphan) — replaced duplicates excluded.
        "abandoned": len(abandoned_all) - len(superseded),
        # Replaced-by-completed-successor reruns; not a failure, shown for legibility.
        "superseded": len(superseded),
        "total_tokens": sum(p["tokens_consumed"] for p in all_pipelines),
    }

    print(json.dumps({
        "pipelines": output,
        "count": len(output),
        "summary": summary,
    }, indent=2))


# ── REPORT read helpers: the reader follows the writer ───────────────────────
# The report used to read fields no stage writes (``tdd.tests_total`` off the BUILD
# record, adversarial ``verdict``, completion ``criteria_met``, ``confidence_score``)
# and default each miss to 0 / "?", so a real run rendered "Tests: 0/0", "verdict: ?"
# and "Confidence 0/10" over a 2847-test, 16-finding, push-ready delivery. Every field
# read below is pinned to the writer schema (pipeline_validator.get_stage_schema) by
# tests/test_run_report_writer_fields.py — a reader that drifts goes red there. A
# value no stage wrote is OMITTED ("not recorded"), never rendered as a measurement.


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _int_or_none(v):
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _report_test_metrics(test_data: dict) -> dict:
    """Measured test metrics from the test_report artifact; {} when none recorded.

    ``passed`` is a boolean "suite passed" in the current schema. Older runs wrote
    an integer pass COUNT there, in which case total = passed + failed.
    """
    if not isinstance(test_data, dict) or not test_data:
        return {}
    out: dict = {}
    total = _int_or_none(test_data.get("tests_total"))
    failed = _int_or_none(test_data.get("failed"))
    passed = test_data.get("passed")
    if total is None and _int_or_none(passed) is not None:
        total = passed + (failed or 0)
    if total is not None:
        out["tests_total"] = total
    if failed is not None:
        out["failed"] = failed
    for k in ("tests_new", "regressions", "pre_existing_failures", "flaky_failures",
              "tests_reused"):
        v = _int_or_none(test_data.get(k))
        if v is not None:
            out[k] = v
    if isinstance(passed, bool):
        out["suite_passed"] = passed
    return out


def _report_build_tests_written(build_tdd: dict):
    """Tests WRITTEN during BUILD (``tdd.tests_generated``) — not the suite total."""
    if not isinstance(build_tdd, dict):
        return None
    return _int_or_none(build_tdd.get("tests_generated"))


def _report_tests_phrase(m: dict):
    """Header phrase, e.g. "2847 run, 0 failed"; None when no test count was recorded."""
    if not m or "tests_total" not in m:
        return None
    phrase = f"{m['tests_total']} run"
    if m.get("tests_reused"):
        phrase += f" ({m['tests_reused']} reused unchanged)"
    if "failed" in m:
        phrase += f", {m['failed']} failed"
    if m.get("pre_existing_failures"):
        phrase += f", {m['pre_existing_failures']} pre-existing"
    if m.get("flaky_failures"):
        phrase += f", {m['flaky_failures']} flaky"
    return phrase


def _report_adversarial_summary(ar: dict):
    """"16 found · 14 fixed · 0 open" from the writer's counts; None if not recorded."""
    if not isinstance(ar, dict):
        return None
    total = _int_or_none(ar.get("findings_total"))
    if total is None:
        return None
    parts = [f"{total} found"]
    fixed = _int_or_none(ar.get("findings_fixed"))
    if fixed is not None:
        parts.append(f"{fixed} fixed")
    open_ = _int_or_none(ar.get("findings_remaining"))
    if open_ is not None:
        parts.append(f"{open_} open")
    return " · ".join(parts)


def _report_completion_lines(ca: dict) -> list:
    """Completion-audit lines from the writer fields (legacy criteria_* still render)."""
    if not isinstance(ca, dict):
        return []
    lines: list = []
    met = _int_or_none(ca.get("requirements_met"))
    total = _int_or_none(ca.get("requirements_total"))
    if met is not None and total is not None:
        lines.append(f"**Requirements met:** {met}/{total}")
    if isinstance(ca.get("all_green"), bool):
        lines.append(f"**All green:** {'yes' if ca['all_green'] else 'no'}")
    for key, label in (("gaps", "Open gaps"), ("unfixable_gaps", "Unfixable gaps (user decides)")):
        v = _int_or_none(ca.get(key))
        if v:
            lines.append(f"**{label}:** {v}")
    for c in _as_list(ca.get("criteria_met", [])):
        lines.append(f"- [x] {c}")
    for c in _as_list(ca.get("criteria_unmet", [])):
        lines.append(f"- [ ] {c}")
    return lines


def _report_commit_stats(commits):
    """Files and +/- lines of the run's OWN recorded commits, via ``git show --numstat``.

    Returns ``{"files": [...], "added": N, "removed": N}`` or None when nothing could
    be measured (no commits, a vanished repo, git failed). A ``sha`` that is not a
    plain hex object id is skipped and never reaches git — it comes from run.json,
    which is a file on disk, not a trusted value. Binary files count as 0 lines.
    """
    files: list = []
    added = removed = 0
    measured = False
    for c in commits or []:
        if not isinstance(c, dict):
            continue
        repo, sha = c.get("repo"), str(c.get("sha") or "")
        if not repo or not _SHA_RE.match(sha) or not Path(str(repo)).is_dir():
            continue
        # A credited commit can be SHARED with another session (its `files` is
        # filtered to this run's own files): count only those paths. An empty list
        # means none of the commit is ours. No `files` key = a legacy record → the
        # whole commit, as before.
        own = c.get("files")
        if isinstance(own, list):
            own = [str(p) for p in own if isinstance(p, str) and p.strip()]
            if not own:
                continue
        try:
            r = subprocess.run(
                ["git", "-C", str(repo), "show", "--numstat", "--format=", sha, "--",
                 *(own if isinstance(own, list) else [])],
                capture_output=True, text=True, timeout=15,
                env={**os.environ, "GIT_LITERAL_PATHSPECS": "1"},
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if r.returncode != 0:
            continue
        measured = True
        for line in r.stdout.splitlines():
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            a, d, path = cols
            added += int(a) if a.isdigit() else 0
            removed += int(d) if d.isdigit() else 0
            if path not in files:
                files.append(path)
    if not measured:
        return None
    return {"files": files, "added": added, "removed": removed}


def _report_tokens_label(usage: dict) -> str:
    """REPORT header **Tokens:** value for a ``measured_run_usage`` dict. A transcript
    measurement (``source="transcript"``) shows the per-response total and the
    settled dollar figure only when turn rows supplied one; a ``run_token_usage``
    dict shows its total plus the same coverage caveats the chat summary's
    ``_usage_label`` shows (不含当前一轮 / 含建 run 前的同一轮). A legacy dict
    without the flags renders with no caveat."""
    tokens = usage.get("tokens")
    if tokens is None:
        tokens = sum(int(usage.get(k) or 0) for k in
                     ("input_tokens", "output_tokens", "cache_creation_tokens"))
    if usage.get("source") == "transcript":
        # measured_run_usage: tokens per response; dollars only if turn rows settled some
        cost = ""
        if usage.get("cost_usd") is not None:
            cnotes = [n for n, on in (
                ("excludes the turn in flight", usage.get("cost_excludes_current_turn")),
                ("includes the pre-run part of the creating turn", usage.get("cost_includes_prerun")))
                if on]
            cost = f"; ${float(usage['cost_usd']):.2f} settled" + (f", {', '.join(cnotes)}" if cnotes else "")
        return (f"{int(tokens):,} (measured per response, "
                f"cache read {int(usage.get('cache_read_tokens') or 0):,}{cost})")
    notes = []
    if usage.get("excludes_current_turn") is True:
        notes.append("不含当前一轮 / excludes the turn in flight")
    if usage.get("includes_prerun") is True:
        notes.append("含建 run 前的同一轮 / includes the pre-run part of the creating turn")
    tail = ("; " + "; ".join(notes)) if notes else ""
    return (f"{int(tokens):,} (measured, ${float(usage.get('cost_usd') or 0):.2f}, "
            f"cache read {int(usage.get('cache_read_tokens') or 0):,}{tail})")


#: (stage, nested path) -> fields the report reads there. Two tests hold it honest:
#: every entry must exist in the writer schema (template + required + recommended),
#: and every field the read helpers below actually ``.get()`` must be listed here
#: (found by parsing their code) — so the list cannot drift from the code either.
_REPORT_READS = {
    ("test", ()): ("tests_total", "passed", "failed", "tests_new", "regressions"),
    ("review", ()): ("findings_count", "findings", "approved"),
    ("build", ("tdd",)): ("tests_generated",),
    ("deliver", ("adversarial_review",)): (
        "spawned", "profile_tier", "findings_total", "findings_fixed",
        "findings_remaining", "findings",
    ),
    ("deliver", ("completion_audit",)): (
        "all_green", "requirements_met", "requirements_total", "gaps", "unfixable_gaps",
    ),
}

#: Fields read ONLY so that reports of runs written under an older schema still
#: render. Not in the current writer schema on purpose — new runs never write them.
_REPORT_LEGACY_READS = {
    ("deliver", ("adversarial_review",)): ("verdict",),
    ("deliver", ("completion_audit",)): ("criteria_met", "criteria_unmet"),
}


def cmd_run_report(args, reg: ArtifactRegistry) -> str:
    """Generate comprehensive REPORT.md for a completed (or running) pipeline run.

    Aggregates run.json + ALL published artifacts (evaluation, design_doc,
    changeset, review, test_report, delivery) into a full retrospective report.
    Covers: evaluation rationale, design approach, TDD results, review findings,
    adversarial review, completion audit, files changed, and lessons.
    """
    from datetime import datetime, timezone

    # Register at the command entry (adversarial-review MED): this must NOT be buried in
    # the best-effort metrics try/except below — if metrics extraction throws first, the
    # project would go unregistered. Guarantee registration before any Projects/ write.
    _ensure_project_dir(args.project)

    run_file = _resolve_run_file(args.project, args.run_id)
    run_state = json.loads(run_file.read_text(encoding="utf-8"))

    stages = run_state.get("stages", [])
    profile = normalize_profile(run_state.get("profile"))  # C3: label consistency
    requirement = run_state.get("requirement", "")

    # ── Load artifact data strictly from this run ──────────────────────
    # IMPORTANT: Only load artifacts linked by this run's stage artifact_ids.
    # Never use a "latest" or by-date fallback — both cause cross-contamination
    # between runs (see the #4 NOTE below).
    art_data: dict[str, dict] = {}  # stage_name -> artifact data

    for s in stages:
        stage_name = s.get("stage", "?")
        art_id = s.get("artifact_id")
        if art_id:
            # Read contract (Action B): own-run + type enforced. expected_type from
            # the stage→type map; a stage not in the map passes type unchecked (None).
            loaded = _load_artifact_for_metrics(
                args.project, args.run_id, art_id,
                _STAGE_ARTIFACT_TYPE.get(stage_name),
            )
            if loaded:
                art_data[stage_name] = loaded.data

    # NOTE (#4): the old date-scoped fallback that reloaded each stage from a bare
    # top-level {type}-{date}.json was DELETED. It overwrote this run's artifact (loaded
    # above by run.json artifact_id) with a run-blind bare file whenever a recommended
    # field was absent (e.g. evaluate lacking scores) — and a bare file is run-blind
    # (last same-day writer wins), so a sibling run's data contaminated this report.
    # The report now trusts ONLY run.json's per-stage artifact_id (loaded via the
    # manifest). A stage genuinely without an artifact_id simply shows no data (honest)
    # rather than borrowing another run's. No bare top-level date-file reader remains.

    # ── Determine skipped stages ──────────────────────────────────────
    ALL_STAGES = ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"]
    present_stages = {s.get("stage", "?") for s in stages}  # Action B: legacy `name` fallback removed (0 live data)
    skipped_stages: dict[str, str] = {}  # stage -> reason
    for stg in ALL_STAGES:
        if stg not in present_stages:
            # Determine skip reason from profile
            if profile == "trivial" and stg in ("think", "plan", "reflect"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' omits research/planning"
            elif profile == "bugfix" and stg in ("think", "review"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' uses direct fix path"
            elif profile == "research" and stg in ("build", "review", "test", "deliver"):
                skipped_stages[stg] = f"Skipped — profile '{profile}' is research-only"
            else:
                if stg == "reflect" and profile == "full":
                    skipped_stages[stg] = "Not executed — pipeline completed before REFLECT"
                else:
                    skipped_stages[stg] = f"Skipped — not required for profile '{profile}'"

    # ── Collect decisions ─────────────────────────────────────────────
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            # Decisions come from two shapes in the wild: pipeline stages write
            # list[dict] ({classification, description, ...}), but session/
            # DailyActivity-sourced decisions are list[str]. A bare string here
            # crashes `**d` (TypeError: not a mapping). Coerce defensively — the
            # report is a best-effort renderer that must never crash on a
            # malformed-but-harmless stage record. (run_e346b8ed DoD0a)
            if not isinstance(d, dict):
                d = {"description": str(d)}
            all_decisions.append({
                "stage": s.get("stage", "?"),
                **d,
            })

    mech = sum(1 for d in all_decisions if d.get("classification") == "mechanical")
    taste = sum(1 for d in all_decisions if d.get("classification") == "taste")
    judgment = sum(1 for d in all_decisions if d.get("classification") == "judgment")

    # ── Stage map (needed by all sections for stage-json PRIMARY reads) ──
    _stage_map = {s.get("stage", "?"): s for s in stages}  # Action B: legacy `name` fallback removed

    # ── Section 1: TL;DR ──────────────────────────────────────────────
    delivery = art_data.get("deliver", {})
    title = delivery.get("title", requirement[:80] or "Pipeline Report")

    # ── Section 2: Evaluation ─────────────────────────────────────────
    # PRIMARY: stage-json (always populated per Rule 22), FALLBACK: published artifact
    _eval_stage_rec = _stage_map.get("evaluate", {})
    eval_data = art_data.get("evaluate", {})
    eval_scores = eval_data.get("scores", {})
    eval_recommendation = _eval_stage_rec.get("recommendation") or eval_data.get("recommendation", "?")
    eval_scope = _eval_stage_rec.get("scope") or eval_data.get("scope", "?")
    eval_criteria = _as_list(_eval_stage_rec.get("acceptance_criteria") or eval_data.get("acceptance_criteria", []))

    eval_table_lines = []
    for dim in ["strategic", "priority", "historical", "feasibility"]:
        score = eval_scores.get(dim)
        if score is not None:
            # score may be numeric (3.0) or qualitative ("high") — same untyped
            # eval_scores dict as roi below; format both safely (see roi guard).
            score_str = f"{score:.2f}" if isinstance(score, (int, float)) else str(score)
            eval_table_lines.append(f"| {dim.capitalize()} | {score_str} | |")
    roi = eval_scores.get("roi")
    if roi is not None:
        # roi may be numeric (3.2) or a qualitative string ("high") — format both.
        roi_str = f"{roi:.3f}" if isinstance(roi, (int, float)) else str(roi)
        eval_table_lines.append(f"| **ROI** | **{roi_str}** | **{eval_recommendation}** |")

    # ── Section 3: Design & Approach ──────────────────────────────────
    # PRIMARY: stage-json (think/plan records), FALLBACK: published artifacts
    _think_stage_rec = _stage_map.get("think", {})
    _plan_stage_rec = _stage_map.get("plan", {})
    design = art_data.get("plan", {})
    if not design.get("approach"):
        design = art_data.get("think", {})
    approach = _plan_stage_rec.get("approach_chosen") or _think_stage_rec.get("approach_chosen") or design.get("approach", "")
    boundaries = design.get("boundaries", {})
    success_criteria = _as_list(_plan_stage_rec.get("spec_summary") or design.get("success_criteria", design.get("acceptance_criteria", [])))
    files_to_change = _plan_stage_rec.get("files_planned") or design.get("files_to_change", [])
    # Think stage research findings
    think_data = art_data.get("think", {})
    key_findings = _as_list(_think_stage_rec.get("key_findings") or think_data.get("key_findings", []))
    alternatives = _as_list(_think_stage_rec.get("alternatives") or think_data.get("alternatives", []))

    # ── Section 4: Pipeline Execution ─────────────────────────────────
    # Show ALL 8 standard stages — executed ones with data, skipped with reason
    stage_lines = []
    stage_data_map = {s.get("stage", "?"): s for s in stages}  # Action B: legacy `name` fallback removed
    for stg in ALL_STAGES:
        if stg in stage_data_map:
            s = stage_data_map[stg]
            status = s.get("status", "?")
            artifact = s.get("artifact_id", "-")
            tokens = s.get("token_cost", 0)
            summary = s.get("summary", "")[:50]
            stage_lines.append(f"| {stg} | {status} | {artifact} | {tokens:,} | {summary} |")
        elif stg in skipped_stages:
            stage_lines.append(f"| {stg} | ⏭ skipped | - | 0 | {skipped_stages[stg]} |")

    # ── Extract stage-json metrics (Rule 22: stage records ARE the audit trail) ──
    # These supplement artifact data with per-stage metrics that may not be in published artifacts
    # (_stage_map already defined above for Section 2-3 reads)

    # Changeset stage is profile-aware (Kiro #6): build for full/bugfix, goal_cycle
    # for goal. Both the stage RECORD and the artifact DATA are read from it.
    _changeset_stage = _changeset_stage_for_profile(profile)

    # Gate 1 verdict (from build stage-json)
    _build_rec = _stage_map.get(_changeset_stage, {})
    gate1_verdict = _build_rec.get("gate1_verdict", "N/A")
    gate1_checks = _build_rec.get("gate1_checks", {})
    gate1_override = _build_rec.get("gate1_override", False)

    # Files changed (prefer stage-json, fallback to artifact)
    stage_files_changed = _build_rec.get("files_changed", [])

    _test_rec = _stage_map.get("test", {})

    # Total token cost (sum all stages)
    total_token_cost = sum(s.get("token_cost", 0) for s in stages)
    # E4: measured usage, the same source as the chat summary (measured_run_usage):
    # tokens per response from the run's recorded CLI transcripts, so a run whose
    # turn is still in flight is measured; the settled dollar figure from the
    # token_usage turn rows when any exist. Nothing measured → the stage sum,
    # labelled as an estimate.
    tokens_label = f"{total_token_cost:,} (估算 / estimate)"
    try:
        from core.run_delivery_summary import measured_run_usage
        _usage = measured_run_usage(run_state)
        if _usage:
            tokens_label = _report_tokens_label(_usage)
    except Exception as exc:  # noqa: BLE001 — the report must render without usage data
        print(f"run-report: measured token usage unavailable ({type(exc).__name__}: {exc})",
              file=sys.stderr)

    # ── Section 5: Test results ───────────────────────────────────────
    # From the test_report artifact — the TEST stage's own record of what ran. The
    # BUILD record's ``tdd`` is a different thing (tests WRITTEN during BUILD:
    # tests_generated/green_pass) and is shown as such, never as the suite total.
    test_data = art_data.get("test", {})
    test_metrics = _report_test_metrics(test_data)
    if not test_metrics and isinstance(_test_rec.get("tdd"), dict):
        test_metrics = _report_test_metrics(_test_rec.get("tdd"))
    tests_phrase = _report_tests_phrase(test_metrics)
    build_tdd = _build_rec.get("tdd") if isinstance(_build_rec.get("tdd"), dict) else {}

    # Profile-aware changeset (Kiro #6): a goal run's changeset lives under
    # goal_cycle with its OWN schema (cycles/tests_total/regressions/mutations_red),
    # NOT build's files_changed/lines_added — reading "build" off a goal artifact
    # silently renders 0. Section 8 below maps the fields per changeset stage.
    changeset = art_data.get(_changeset_stage, {})

    # ── Section 6: Decision Log ───────────────────────────────────────
    decision_lines = []
    for d in all_decisions:
        decision_lines.append(
            f"| {d.get('stage', '?')} | {d.get('description', '')[:60]} | "
            f"{d.get('classification', '?')} | {d.get('reasoning', '')[:50]} |"
        )

    # ── Section 7: Quality Gates ──────────────────────────────────────
    review_data = art_data.get("review", {})
    _rf = review_data.get("findings")
    review_findings = _rf if isinstance(_rf, list) else []
    # None = not recorded. A present list (even empty) is a measurement; a bare int
    # is a legacy count.
    review_findings_count = (len(_rf) if isinstance(_rf, list)
                             else _rf if _int_or_none(_rf) is not None else None)
    # The review writer records findings_count + approved (no "severity" field).
    _fc_review = _int_or_none(review_data.get("findings_count"))
    if _fc_review is not None:
        review_findings_count = _fc_review
    _approved = review_data.get("approved")
    review_status = ("approved" if _approved is True else "not approved"
                     if _approved is False else None)

    adversarial = delivery.get("adversarial_review", {})
    if not isinstance(adversarial, dict):
        adversarial = {}
    adversarial_findings = _as_list(adversarial.get("findings", []))
    adversarial_summary = _report_adversarial_summary(adversarial)
    adversarial_verdict = adversarial.get("verdict")  # legacy runs only

    completion_audit = delivery.get("completion_audit", {})
    completion_lines = _report_completion_lines(completion_audit)
    # Numeric confidence was retired for the binary push-ready verdict (deliver.md
    # "Push-Ready Gate"); no stage writes it, so the report no longer renders one.

    # ── Section 8: Files Changed ──────────────────────────────────────
    # Prefer stage-json files_changed (always populated per Rule 22), fallback to artifact.
    # Profile-aware (Kiro #6): a goal_cycle changeset has NO files_changed/lines_added —
    # its schema is cycles/tests_total/regressions/mutations_red/dod_met. Reading build's
    # field names off a goal artifact renders a misleading 0; map goal's OWN fields instead.
    _fc = stage_files_changed if stage_files_changed else changeset.get("files_changed", [])
    files_changed = _fc if isinstance(_fc, list) else []
    # None = not recorded (no list / no count); a MEASURED zero (git said 0 files)
    # stays 0 below and renders as 0, not as "not recorded".
    files_changed_count = (len(files_changed) if isinstance(_fc, list) and _fc
                           else (_fc if isinstance(_fc, int) else None))
    lines_added = _int_or_none(changeset.get("lines_added"))
    lines_removed = _int_or_none(changeset.get("lines_removed"))
    # Goal-cycle files/lines fallback (Kiro #6 + REVIEW code-quality MED): a goal
    # run's changeset lives under goal_cycle, which is in _ARTIFACTLESS_STAGES
    # ("commits incrementally") so it MAY record no artifact_id → art_data['goal_cycle']
    # empty. Source files_changed from the goal_cycle STAGE RECORD (_build_rec is
    # _stage_map[_changeset_stage]) when the artifact is absent, so the report header's
    # `Files:` count reflects a goal run's real effort instead of the hard-0 the old
    # build-only read produced. (We render through the existing header field — no new
    # section — to avoid a dead-variable summary that renders nowhere.)
    if _changeset_stage == "goal_cycle" and not files_changed:
        _gc_files = _build_rec.get("files_changed", [])
        if isinstance(_gc_files, list) and _gc_files:
            files_changed = _gc_files
            files_changed_count = len(_gc_files)
    # Git truth wins: once the run has committed, its recorded commits say exactly
    # which files changed and by how many lines. The stage lists above are the
    # agent's own (often earlier, partial) account and are only the fallback.
    commit_stats = _report_commit_stats(run_state.get("commits"))
    if commit_stats is not None:
        files_changed = commit_stats["files"]
        files_changed_count = len(files_changed)
        lines_added = commit_stats["added"]
        lines_removed = commit_stats["removed"]

    # ── Duration & timestamps ─────────────────────────────────────────
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_str = ""
    # In-flight fallback (run_fbf97252): `completed_at` is written ONLY by the
    # `--status completed` branch, which historically ran AFTER run-report — so
    # for 466 of 466 real N/A reports this block simply had no end timestamp.
    # The completion branch now regenerates the report (see cmd_run_update), so
    # a COMPLETED run gets the exact value; this fallback covers a report
    # generated mid-run, and is marked so the number is never mistaken for final.
    #
    # `updated_at` is the fallback, NOT the newest stage-artifact timestamp.
    # Measured against ground truth (completed_at - created_at) on 426-433 real
    # runs: updated_at median +0.00% error, 97.5% within +/-1%; newest stage
    # artifact median -4.34%, worst -98.98%, only 13.1% within +/-1%. The
    # tempting estimator is the strictly worse one.
    # "(in progress)" must key off the run's STATUS, not off a missing timestamp.
    # `not completed_at` is true for 214 real runs that are anything but live —
    # 98 abandoned, 54 cancelled, and 55 whose status IS 'completed' but which
    # never got the timestamp written. Labelling those "in progress" is a false
    # liveness claim about a dead run, the same class of confidently-wrong output
    # this section exists to stop rendering.
    #
    # Derives from `_TERMINAL_STATUSES` — the module's declared SSOT — so the two
    # cannot drift on the shared values. Three statuses that occur on disk are
    # added for DISPLAY only (superseded/rejected/aborted, 4 real runs): they are
    # deliberately NOT pushed into the SSOT itself, because that tuple also drives
    # `is_terminal_run`, crash-detect, supersede and the revival guard — widening
    # it changes liveness semantics well beyond a duration label (R5: scope
    # follows the task).
    #
    # `paused` is deliberately EXCLUDED: it is a REVIVAL status
    # (`_REVIVAL_STATUSES`), and `is_terminal_run`'s docstring is explicit that
    # treating a paused mid-pipeline run as terminal silently writes off a
    # genuinely resumable run. A paused run keeps its in-progress marker, which is
    # the truth — it is unfinished.
    _DISPLAY_TERMINAL = frozenset(_TERMINAL_STATUSES) | {
        "superseded", "rejected", "aborted",
    }
    _in_flight = (
        not completed
        and str(run_state.get("status", "")).strip().lower() not in _DISPLAY_TERMINAL
    )
    _end = completed or run_state.get("updated_at", "")
    if created and _end:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(_end)
            mins = round((t1 - t0).total_seconds() / 60, 1)
            duration_str = f"{mins} min (in progress)" if _in_flight else f"{mins} min"
        except (ValueError, TypeError):
            pass

    # ── Validation events ─────────────────────────────────────────────
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ══════════════════════════════════════════════════════════════════
    # BUILD REPORT
    # ══════════════════════════════════════════════════════════════════
    sections = []

    sections.append(f"""# Autonomous Pipeline Report: {title}

**Run ID:** {run_state['id']} | **Project:** {args.project} | **Profile:** {profile}
**Date:** {now} | **Duration:** {duration_str or 'N/A'} | **Tokens:** {tokens_label}
**Gate 1:** {gate1_verdict}{' (override)' if gate1_override else ''} | **Files:** {files_changed_count if files_changed_count is not None else 'not recorded'} | **Tests:** {tests_phrase or 'not recorded'}""")

    # TL;DR
    # The same three-part review content the chat completion summary renders
    # (core.run_delivery_summary.review_summary): solved / how / leftovers. Never the
    # requirement text — that is what was ASKED, not what was done.
    _review = _review_parts(run_state, delivery)
    summary_text = "\n".join(
        ([_one_line(delivery.get("summary")), ""] if _one_line(delivery.get("summary")) else [])
        + _review_report_lines(_review))
    sections.append(f"""## TL;DR
{summary_text}""")

    # 1. Requirement
    sections.append(f"""## 1. Requirement
{requirement}""")

    # 1.5 Requirement Provenance Audit (Run 3a 块1, design §4.6.1-4.6.3) — the
    # "glass box": what the AI understood + where it decided for the user. Rendered
    # mechanically from this run's evaluation artifact (same source as the real-time
    # inline emit). Only shown when the run produced a STRUCTURED requirements[] —
    # a legacy / free-text run skips it (render_audit_trail_md degrades, but we don't
    # even call it unless there's structured data, to keep old reports unchanged).
    _eval_art = art_data.get("evaluate", {}) if isinstance(art_data.get("evaluate"), dict) else {}
    # #8: gate must AGREE with the validator's gate (validate_requirements_block, which
    # requires schema_version>=REQ_SCHEMA_VERSION). A requirements[] present WITHOUT a
    # valid schema_version is a legacy / half-activated artifact — rendering the new
    # audit block for it would change an old report's format on a presence-only match
    # (the presence-vs-version drift, same root as finding #1).
    if _eval_art.get("requirements"):
        try:
            from pipeline_validator import render_audit_trail_md, REQ_SCHEMA_VERSION
            _sv = _eval_art.get("schema_version")
            # gate agrees with the validator's own (schema_version>=REQ_SCHEMA_VERSION);
            # a requirements[] without a valid version is legacy/half-activated and skips.
            if isinstance(_sv, int) and _sv >= REQ_SCHEMA_VERSION:
                # E4: the provenance anchor is the user's own words when recorded.
                sections.append(render_audit_trail_md(run_state.get("intent") or requirement, _eval_art))
        except Exception as _audit_err:  # never let the audit block break the whole report
            # a VALID structured artifact whose render/import fails degrades VISIBLY (not
            # a silent skip) — the legacy no-version case is handled by the version gate above.
            sections.append(f"## 需求溯源审计\n_(audit render skipped: {type(_audit_err).__name__})_")

    # 2. Evaluation
    eval_section = "## 2. Evaluation\n"
    if eval_table_lines:
        eval_section += "| Dimension | Score | Recommendation |\n|---|---|---|\n"
        eval_section += "\n".join(eval_table_lines)
    else:
        eval_section += f"Recommendation: **{eval_recommendation}** | Scope: {eval_scope}"
    if eval_criteria:
        eval_section += "\n\n**Acceptance Criteria:**\n"
        for i, ac in enumerate(eval_criteria[:12], 1):
            eval_section += f"{i}. {ac}\n"
    sections.append(eval_section)

    # 3. Design & Approach
    design_section = "## 3. Design & Approach\n"
    if key_findings:
        design_section += "**Research Findings (THINK):**\n"
        for kf in key_findings[:8]:
            design_section += f"- {kf}\n"
        design_section += "\n"
    if alternatives:
        design_section += "**Alternatives Evaluated:**\n"
        for alt in alternatives[:4]:
            if isinstance(alt, dict):
                rec = " ✅ (recommended)" if alt.get("recommendation") else ""
                design_section += f"- {alt.get('constraint', '?')} — effort: {alt.get('effort', '?')}{rec}\n"
            else:
                design_section += f"- {str(alt)[:80]}\n"
        design_section += "\n"
    if approach:
        design_section += f"**Chosen Approach:** {approach}\n\n"
    if success_criteria:
        design_section += "**Success Criteria:**\n"
        for sc in success_criteria[:10]:
            design_section += f"- {sc}\n"
    if boundaries and isinstance(boundaries, dict):
        always_rules = _as_list(boundaries.get("always", []))
        never_rules = _as_list(boundaries.get("never", []))
        if always_rules:
            design_section += "\n**Always:**\n"
            for r in always_rules[:5]:
                design_section += f"- {r}\n"
        if never_rules:
            design_section += "\n**Never:**\n"
            for r in never_rules[:5]:
                design_section += f"- {r}\n"
    if not approach and not success_criteria and not key_findings:
        if "think" in skipped_stages or "plan" in skipped_stages:
            design_section += f"**⏭ Skipped** — {skipped_stages.get('think', skipped_stages.get('plan', ''))}\n"
        else:
            design_section += "(No design artifact data captured for this run)\n"
    sections.append(design_section)

    # 4. Pipeline Execution
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    exec_section = f"""## 4. Pipeline Execution
| Stage | Status | Artifact | Tokens | Summary |
|-------|--------|----------|--------|---------|
{chr(10).join(stage_lines) if stage_lines else "| (no stages) | | | | |"}

**Total tokens:** {total_tokens:,} | **Stages:** {sum(1 for s in stages if s.get('status') in ('done', 'completed'))}/{len(stages)} completed"""
    if validation_blocks:
        exec_section += f" | **Validation blocks:** {validation_blocks}"

    # ── 4.1 Per-stage elapsed (derived, read-only) ────────────────────────────
    # Answers "which stage consumed the time" — the question run.json's single
    # total could not. Rendered as its OWN block, deliberately NOT as an extra
    # column on the table above: that table walks ALL_STAGES (canonical order)
    # while these rows are sorted by publish TIMESTAMP (execution order), and the
    # two disagree on 11.9% of real runs. Zipping them would silently mis-pair a
    # duration onto the wrong stage — the kind of plausible-looking wrong number
    # that is worse than no number.
    #
    # Coverage is always disclosed: this derivation resolves >=2 stages on ~71%
    # of runs, never all of them, and an undisclosed partial table reads as
    # complete (a silent cap).
    _timing_rows, _timing_cov = _stage_publish_times(args.project, run_state)
    if _timing_rows:
        # A time-of-day-only column makes a MULTI-DAY run read as out-of-order.
        # Caught on this very run's own report: it spans 2026-09-07 to 09-11, so
        # `test` published 05:44 on a later day sorted CORRECTLY yet displayed
        # above `plan` at 09:32 — which looks exactly like a broken sort. Show the
        # date only when the resolved rows are not all on one day, so a normal
        # single-day run keeps the compact column.
        _days = {(_r.get("at") or "")[:10] for _r in _timing_rows if _r.get("at")}
        _multiday = len(_days) > 1
        timing_lines = []
        for _r in _timing_rows:
            _el = _r.get("elapsed_min")
            _cyc = _r.get("cycles")
            _name = _safe_stage_name(_r["stage"])
            _at = _r.get("at")
            if not _at:
                _at_s = "-"
            elif _multiday:
                _at_s = f"{_at[5:10]} {_at[11:19]}"  # MM-DD HH:MM:SS
            else:
                _at_s = _at[11:19]
            timing_lines.append(
                f"| {_name}{f' ({_cyc} cycles)' if _cyc else ''} "
                f"| {_at_s} "
                f"| {f'{_el:.1f} min' if _el is not None else '-'} |"
            )
        # Name the slowest stage explicitly (AC1). A reader must identify the
        # bottleneck WITHOUT scanning and comparing the column by eye — that is
        # the entire point of the section: the run this work came from spent 73%
        # of 244 minutes in one stage, and that fact should read as a sentence,
        # not as an inference the reader has to perform.
        _timed = [r for r in _timing_rows if r.get("elapsed_min") is not None]
        _slowest_line = ""
        if _timed:
            _slow = max(_timed, key=lambda r: r["elapsed_min"])
            _sum = sum(r["elapsed_min"] for r in _timed)
            # A share is only meaningful across >=2 intervals. With exactly one
            # interval the answer is unconditionally "100%", which READS as a
            # bottleneck finding while being pure arithmetic — 33 real runs
            # render that shape. Suppress it rather than publish a true number
            # that misleads. The denominator is also named explicitly: it is the
            # sum of INTER-STAGE gaps, and it excludes created_at -> first
            # publish (median 3.0 min, max 86.9 min on real runs), so calling it
            # "of measured time" would overclaim what was measured.
            _share = ""
            if len(_timed) >= 2 and _sum > 0:
                _share = (
                    f" ({_slow['elapsed_min'] / _sum * 100:.0f}% of the "
                    f"{len(_timed)} measured inter-stage gaps)"
                )
            _slowest_line = (
                f"\n\nSlowest stage: `{_safe_stage_name(_slow['stage'])}` at "
                f"{_slow['elapsed_min']:.1f} min{_share}."
            )
        exec_section += f"""

### 4.1 Per-Stage Elapsed (derived from artifact publish times)
| Stage | Published | Elapsed since previous |
|-------|-----------|------------------------|
{chr(10).join(timing_lines)}{_slowest_line}

**Timing coverage:** {_timing_cov['resolved']}/{_timing_cov['total']} stages resolved, \
{len(_timed)} with an elapsed number. \
The earliest resolved stage has no predecessor, so it never carries a number — \
`resolved` is therefore always one higher than the count of durations (true on \
462/462 real runs; stating it here so the gap does not read as a bug). \
Elapsed is the gap between consecutive artifact publishes, so it includes the \
inter-stage gap (agent reasoning, gates, sub-agent spawns), not just in-stage work. \
The run's `created_at` -> first publish is NOT counted. \
A stage with no published artifact cannot be timed and shows `-`."""
    _brows = []
    for s in stages:
        b = s.get("budget") if isinstance(s.get("budget"), dict) else None
        if not b:
            continue
        sp = b.get("spawns") or {}
        by = ", ".join(f"{e}×{n}" for e, n in (sp.get("by_expert") or {}).items()) or "-"
        tr = ", ".join(f"{m}×{n}" for m, n in (b.get("test_runs") or {}).items()) or "-"
        over = "; ".join(f"{e}: " + str(r).replace("|", "\\|").replace("\n", " ")[:200]
                         for e, r in (b.get("reasons") or {}).items()) or "-"
        _brows.append(f"| {s.get('stage')} | {by} | {sp.get('unattributed', '-')} | {tr} | "
                      f"{b.get('auto_rechecks', 0)} | {over} |")
    if _brows:
        _tot_sp = sum(sum(((s.get("budget") or {}).get("spawns") or {}).get("by_expert", {}).values())
                      for s in stages if isinstance(s.get("budget"), dict))
        _tot_tr = sum(sum(((s.get("budget") or {}).get("test_runs") or {}).values())
                      for s in stages if isinstance(s.get("budget"), dict))
        _brows.append(f"| **total** | {_tot_sp} attributed | | {_tot_tr} | | |")
        exec_section += ("\n\n### 4.2 Budget (sub-agent rounds, test runs)\n"
                         "| Stage | Sub-agents by EXPERT-ID | Unattributed | Test runs | "
                         "Auto-rechecks | Over-budget reasons |\n|---|---|---|---|---|---|\n"
                         + "\n".join(_brows))
    sections.append(exec_section)

    # 5. Test results — only rows a stage actually recorded
    _rows = []
    if "tests_total" in test_metrics:
        _rows.append(("Tests run (TEST stage)", test_metrics["tests_total"]))
    if test_metrics.get("tests_reused"):
        _rows.append(("Of which reused (passed earlier this run, inputs unchanged)",
                      test_metrics["tests_reused"]))
    if "failed" in test_metrics:
        _rows.append(("Failed", test_metrics["failed"]))
    if "suite_passed" in test_metrics:
        _rows.append(("Suite passed", "yes" if test_metrics["suite_passed"] else "no"))
    if "tests_new" in test_metrics:
        _rows.append(("New tests", test_metrics["tests_new"]))
    if "regressions" in test_metrics:
        _rows.append(("Regressions", test_metrics["regressions"]))
    _gen = _report_build_tests_written(build_tdd)
    if _gen is not None:
        _rows.append(("Tests written in BUILD", _gen))
    if _rows:
        tdd_section = "## 5. Test Results\n| Metric | Value |\n|--------|-------|\n" + "\n".join(
            f"| {k} | {v} |" for k, v in _rows)
    else:
        tdd_section = "## 5. Test Results\n_Not recorded by any stage._"
    sections.append(tdd_section)

    # 6. Decision Log
    decision_section = f"""## 6. Decision Log
| Stage | Decision | Classification | Reasoning |
|-------|----------|---------------|-----------|
{chr(10).join(decision_lines) if decision_lines else "| (no decisions) | | | |"}

**Summary:** {mech} mechanical, {taste} taste, {judgment} judgment"""
    sections.append(decision_section)

    # 7. Quality Gates
    quality_section = "## 7. Quality Gates\n"
    quality_section += "| Gate | Result |\n|------|--------|\n"
    if review_data:
        _rv = (f"{review_findings_count} findings" if review_findings_count is not None
               else "findings not recorded")
        quality_section += (f"| REVIEW (code quality) | {_rv}"
                            + (f" ({review_status})" if review_status else "") + " |\n")
    else:
        quality_section += "| REVIEW (code quality) | not recorded |\n"
    quality_section += f"| TEST (suite) | {tests_phrase or 'not recorded'} |\n"

    # 7.5 Adversarial Review
    quality_section += f"\n### 7.5 Adversarial Review\n"
    if not adversarial and "deliver" not in stage_data_map:
        quality_section += f"**⏭ NOT RUN** — deliver stage was skipped (profile: {profile})\n"
    elif not adversarial or (adversarial.get("spawned") is not True and not adversarial_verdict
                             and not adversarial_findings and adversarial_summary is None):
        quality_section += "**⚠️ NOT RUN** — adversarial sub-agent was not spawned during this run.\n"
        quality_section += "This is a quality gap. Pipeline DELIVER requires adversarial review.\n"
    elif adversarial_summary or adversarial_findings:
        _head = adversarial_summary or f"{len(adversarial_findings)} findings"
        quality_section += f"**{_head}**" + (f" (verdict: {adversarial_verdict})" if adversarial_verdict else "") + "\n"
        for f in adversarial_findings[:5]:
            if isinstance(f, dict):
                quality_section += f"- [{f.get('severity', '?')}] {f.get('description', f.get('finding', ''))[:80]}\n"
            else:
                quality_section += f"- {str(f)[:80]}\n"
    else:
        # Reaching here: spawned, no counts, no findings listed. Only a PRESENT empty
        # findings list measures zero; an absent one is simply not recorded.
        _zero = isinstance(adversarial.get("findings"), list)
        quality_section += ((f"Verdict: **{adversarial_verdict}** | " if adversarial_verdict else "")
                            + "Sub-agent spawned: ✅ | "
                            + ("Findings: 0\n" if _zero else "findings not recorded\n"))

    # 7.6 Completion Audit
    quality_section += f"\n### 7.6 Completion Audit\n"
    if completion_lines:
        quality_section += "\n".join(completion_lines) + "\n"
    else:
        quality_section += "_Not recorded by any stage._\n"
    sections.append(quality_section)

    # 8. Files Changed
    files_section = "## 8. Files Changed\n"
    _lines_txt = (f"+{lines_added} / -{lines_removed} lines"
                  if lines_added is not None and lines_removed is not None else "lines not recorded")
    if files_changed:
        for f in files_changed:
            files_section += f"- `{f}`\n"
        files_section += f"\n**{_lines_txt}** | {files_changed_count} files"
    elif (files_changed_count or 0) > 0:
        files_section += f"**{files_changed_count} files** | {_lines_txt}\n"
        files_section += "(file list not captured in changeset artifact)\n"
    elif "build" in skipped_stages:
        files_section += f"**⏭ Skipped** — {skipped_stages['build']}\n"
    else:
        files_section += "(No changeset data captured for this run)\n"
    sections.append(files_section)

    # 9. Lessons — inline from REFLECT stage + calibration history
    lessons_section = "## 9. Lessons\n"
    lessons_items: list[str] = []

    # Source 1: REFLECT stage record in run.json
    reflect_stage = stage_data_map.get("reflect", {})

    # Primary: structured lessons list (new format from reflect.md step 7)
    stage_lessons = reflect_stage.get("lessons", [])
    if isinstance(stage_lessons, list):
        for item in stage_lessons:
            if isinstance(item, str) and item.strip() and len(item.strip()) > 5:
                lessons_items.append(item.strip())

    # Fallback: parse summary/notes (legacy format)
    if not lessons_items:
        reflect_summary = reflect_stage.get("summary", "") or ""
        if reflect_summary:
            if ": " in reflect_summary and "lesson" in reflect_summary.lower():
                _, _, lessons_text = reflect_summary.partition(": ")
                for item in lessons_text.split(", "):
                    item = item.strip()
                    if item and len(item) > 5:
                        lessons_items.append(item)
            elif len(reflect_summary) > 10:
                lessons_items.append(reflect_summary)

    # Source 2: calibration_history in decision-strategy.json (matched by eval artifact_id)
    eval_art_id = None
    for s in stages:
        if s.get("stage") == "evaluate":
            eval_art_id = s.get("artifact_id")
            break

    if eval_art_id:
        strategy_path = Path(reg.projects_root) / args.project / "decision-strategy.json"
        if strategy_path.is_file():
            try:
                strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
                for entry in strategy.get("calibration_history", []):
                    if entry.get("evaluation_id") == eval_art_id:
                        for lesson in entry.get("lessons", []):
                            if lesson.strip() and lesson.strip() not in lessons_items:
                                lessons_items.append(lesson.strip())
            except (json.JSONDecodeError, OSError):
                pass

    # Source 3: REFLECT stage decisions (if any contain lessons)
    for d in reflect_stage.get("decisions", []):
        # Coerce list[str] shape (see DoD0a note at the decisions-collection
        # loop) — a bare string decision must not crash d.get(). (run_e346b8ed)
        desc = (d if isinstance(d, str) else d.get("description", "")).strip()
        if desc and desc not in lessons_items:
            lessons_items.append(desc)

    if lessons_items:
        for item in lessons_items:
            lessons_section += f"- {item}\n"
    elif "reflect" in skipped_stages:
        lessons_section += f"**⏭ Skipped** — {skipped_stages['reflect']}\n"
    else:
        lessons_section += f"{_NO_LESSONS_PLACEHOLDER}\n"
    sections.append(lessons_section)

    # 10. Known Gaps
    gaps_section = "## 10. Known Gaps & Attention Flags\n"
    _flags = [f"- {c}" for c in _as_list(
        completion_audit.get("criteria_unmet", []) if isinstance(completion_audit, dict) else [])]
    if isinstance(completion_audit, dict):
        _open = _int_or_none(completion_audit.get("gaps"))
        _unfix = _int_or_none(completion_audit.get("unfixable_gaps"))
        if _open:
            _flags.append(f"- {_open} completion-audit gap(s) still open")
        if _unfix:
            _flags.append(f"- {_unfix} gap(s) could not be closed inside the pipeline — "
                          "the user decides (see the completion audit evidence)")
    if validation_blocks:
        _flags.append(f"- Validator blocked {validation_blocks} time(s) during execution")
    # Every leftover DELIVER recorded (unresolved findings, push_ready=false, forced
    # commits, attention_flags, meta-review risks): a run that recorded any of them
    # must never read "None identified" here.
    _flags += [f"- {_one_line(x)}" for x in _review.get("leftovers", [])]
    # Tests in this run's scope that are red on main too: not this run's to fix (XG
    # 2026-10-21), registered for their own fix run (core/main_red, `main-red`).
    _reds = _main_reds_in_scope(run_file.parent, run_state.get("id") or run_file.parent.name)
    if _reds:
        _flags.append(f"- {len(_reds)} test(s) in this run's scope are red on main too "
                      "(pre-existing, not this run's change; registered, see `artifact_cli.py "
                      "main-red`): " + ", ".join(r["nodeid"] for r in _reds[:5])
                      + (f" (+{len(_reds) - 5})" if len(_reds) > 5 else ""))
    gaps_section += ("\n".join(_flags) + "\n") if _flags else "None identified.\n"
    sections.append(gaps_section)

    # Footer
    sections.append(f"---\n*Generated by SwarmAI Autonomous Pipeline | {now}*")

    report = "\n\n".join(sections) + "\n"

    # Write REPORT.md.
    #
    # Skip-guard discriminates AUTO-generated reports from HAND-WRITTEN ones via
    # a generator-owned run.json flag (`report_autogenerated`), NOT by sniffing
    # the report body for a footer string (a marker-substring match is the exact
    # unfalsifiable-on-reword anti-pattern IMPROVEMENT.md warns against, 3×):
    #   - HAND-WRITTEN report (flag absent/False) + no --force → SKIP (preserve the
    #     human's work — the original data-loss guard).
    #   - AUTO-generated report (flag True) → REGENERATE even without --force. This
    #     is the real fix: DELIVER generates REPORT.md early (to pass the advance
    #     gate); REFLECT then records lessons; the mandatory COMPLETE-time
    #     `run-report` call (no --force) must pick those lessons up instead of
    #     skipping and freezing a stale, lessons-less report (empirically 5/6 of
    #     recent completed runs had an EMPTY Lessons section because of this).
    report_path = run_file.parent / "REPORT.md"
    force = getattr(args, "force", False)
    already_autogenerated = bool(run_state.get("report_autogenerated"))
    if report_path.exists() and not force and not already_autogenerated:
        print(json.dumps({
            "skipped": True,
            "reason": "REPORT.md exists and is hand-written (use --force to overwrite)",
            "report_path": str(report_path),
        }))
        # RETURN the outcome, don't only print it. The completion-time caller
        # (:2402) captures stdout into a discarded buffer to keep its own
        # single-JSON-object contract, which destroys a printed skip signal —
        # so it would report `report_refreshed: True` for a write that never
        # happened. Every existing caller ignores the return value, so this is
        # additive. "Did not raise" is not evidence that work was done.
        return "skipped"

    report_path.write_text(report, encoding="utf-8")

    # Stamp the generator-owned flag so a later no-force call (COMPLETE step)
    # knows this report is auto-owned and may be regenerated to inline late
    # reflect lessons. A human who hand-edits REPORT.md does NOT set this flag,
    # so their work stays protected. Best-effort: a flag-write failure must not
    # fail report generation (the report itself already succeeded).
    if not already_autogenerated:
        try:
            fresh_state = json.loads(run_file.read_text(encoding="utf-8"))
            fresh_state["report_autogenerated"] = True
            _write_run_atomic(run_file, fresh_state)
        except (OSError, json.JSONDecodeError):
            pass

    print(json.dumps({
        "report_path": str(report_path),
        "gate1_verdict": gate1_verdict,
        "total_tokens": total_token_cost,
        "stages": len(stages),
        "decisions": len(all_decisions),
        "files_changed": files_changed_count,
        # None = not recorded (the report says so too) — never a fabricated 0.
        "tests_passed": (test_metrics["tests_total"] - test_metrics["failed"]
                         if "tests_total" in test_metrics and "failed" in test_metrics else None),
        "tests_total": test_metrics.get("tests_total"),
        "regenerated": already_autogenerated,
    }))
    return "written"


def _record_validation_event(
    project: str, run_id: str, stage: str,
    passed: bool, errors: list[str], warnings: list[str],
    errored: list[str] | None = None,
) -> None:
    """Append a validation event to run.json.validation_events[].

    Non-critical — failures are silently ignored (metrics are best-effort).

    ``errored`` (run_55710438) names checks that could NOT run (crashed),
    distinct from content failures in ``errors``. Additive + optional so
    existing callers are unaffected.
    """
    from datetime import datetime, timezone
    errored = errored or []
    try:
        run_file = _run_dir(project, run_id) / "run.json"
        if not run_file.exists():
            return
        run_state = json.loads(run_file.read_text(encoding="utf-8"))
        events = run_state.setdefault("validation_events", [])
        events.append({
            "stage": stage,
            "passed": passed,
            "error_count": len(errors),
            "warning_count": len(warnings),
            "errored_count": len(errored),
            "errors": errors[:5],  # Cap at 5 to avoid bloat
            "errored": errored[:5],  # checks that could not run
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        _write_run_atomic(run_file, run_state)
    except (OSError, json.JSONDecodeError, ValueError):
        # ValueError added run_dd8411f6 (#2 follow-through): _run_dir now raises on a
        # malformed run_id — must stay inside this "never crash pipeline" contract.
        pass  # Best-effort — never crash pipeline for metrics


def _extract_run_metrics(project: str, run_id: str, run_state: dict) -> dict:
    """Extract comprehensive metrics from a completed pipeline run.

    Reads run.json + artifact data to produce a flat metrics dict.
    """
    from datetime import datetime, timezone

    stages = run_state.get("stages", [])
    profile = run_state.get("profile", "full")


    if isinstance(stages, dict):
        audit = run_state.get("audit_summary") if isinstance(run_state.get("audit_summary"), dict) else {}
        speed = audit.get("speed") if isinstance(audit.get("speed"), dict) else {}
        token = audit.get("token_cost") if isinstance(audit.get("token_cost"), dict) else {}
        quality = audit.get("quality") if isinstance(audit.get("quality"), dict) else {}
        autonomy = audit.get("autonomy") if isinstance(audit.get("autonomy"), dict) else {}
        completed = sum(value in ("completed", "degraded") for value in stages.values())
        return {
            "run_id": run_id, "project": project, "profile": profile,
            "status": run_state.get("status", "?"), "stages_completed": completed,
            "stages_total": len(stages),
            "duration_minutes": (round(float(speed["wall_seconds"]) / 60, 1)
                                 if isinstance(speed.get("wall_seconds"), (int, float)) else None),
            "total_tokens": int(token.get("total_tokens") or 0), "stage_tokens": {},
            "decisions": {"mechanical": 0, "taste": 0,
                          "judgment": int(autonomy.get("human_decisions") or 0),
                          "total": int(autonomy.get("human_decisions") or 0)},
            "catches": {"review_findings": int(quality.get("findings_total") or 0),
                        "review_rp_checked": 0, "adversarial_findings": 0,
                        "adversarial_high": 0, "adversarial_resolved": 0,
                        "validation_blocks": 0, "test_regressions": 0},
            "quality": {"confidence_score": 0,
                        "completion_all_green": quality.get("verdict") == "PASS",
                        "test_passed": int(quality.get("verification_passed") or 0),
                        "test_failed": max(0, int(quality.get("verification_required") or 0)
                                           - int(quality.get("verification_passed") or 0))},
            "build": {"files_changed": 0, "tests_generated": 0},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    # --- Token metrics ---
    total_tokens = sum(s.get("token_cost", 0) for s in stages)
    stage_tokens = {
        s.get("stage", "?"): s.get("token_cost", 0)
        for s in stages
    }

    # --- Decision metrics ---
    all_decisions = []
    for s in stages:
        for d in s.get("decisions", []):
            # Coerce list[str] shape (DoD0a, run_e346b8ed) — string decisions
            # have no classification; skip the count safely rather than crash.
            all_decisions.append(d if isinstance(d, dict) else {"description": str(d)})
    decision_counts = {
        "mechanical": sum(1 for d in all_decisions if d.get("classification") == "mechanical"),
        "taste": sum(1 for d in all_decisions if d.get("classification") == "taste"),
        "judgment": sum(1 for d in all_decisions if d.get("classification") == "judgment"),
        "total": len(all_decisions),
    }

    # --- Catch metrics (from artifacts) ---
    review_findings = 0
    review_rp_checked = 0
    adversarial_findings = 0
    adversarial_high = 0
    adversarial_resolved = 0
    confidence_score = 0
    completion_all_green = False
    test_passed = 0
    test_failed = 0
    build_files_changed = 0
    build_tests_generated = 0
    _deliver_artifact_ar = None  # arena-feed fallback (artifact-only adversarial_review)

    for s in stages:
        art_id = s.get("artifact_id")
        if not art_id:
            continue

        stage_name = s.get("stage", "?")
        _res = _load_artifact_for_metrics(
            project, run_id, art_id, _STAGE_ARTIFACT_TYPE.get(stage_name)
        )
        data = _res.data
        if not data:
            continue

        if stage_name == "review":
            fc = data.get("findings_count")
            findings_list = data.get("findings", [])
            review_findings = fc if fc is not None else (len(findings_list) if isinstance(findings_list, list) else 0)
            rp = data.get("runtime_patterns", {})
            review_rp_checked = rp.get("checked", 0) if isinstance(rp, dict) else 0

        elif stage_name == "deliver":
            ar = data.get("adversarial_review", {})
            if isinstance(ar, dict):
                # Arena feed fallback source: when the deliver STAGE RECORD omits
                # adversarial_review (recorded only in the artifact), reuse this
                # already-loaded artifact copy — no extra read (run_4db42c78).
                if isinstance(ar.get("findings"), list) and ar.get("findings"):
                    _deliver_artifact_ar = ar
                findings = ar.get("findings", [])
                adversarial_findings = len(findings)
                # Count HIGH+CRITICAL, case-insensitive — consistent with the
                # confidence gate's blocking severities (_blocked_findings). A
                # bare == "HIGH" under-counted CRITICAL/lowercase relative to what
                # the gate actually blocks (Gate-2 finding #3, run_7583af5f).
                adversarial_high = sum(
                    1 for f in findings
                    if isinstance(f, dict)
                    and str(f.get("severity", "")).strip().upper() in ("HIGH", "CRITICAL")
                )
                adversarial_resolved = sum(
                    1 for f in findings
                    if isinstance(f, dict) and f.get("resolved")
                )
            cs = data.get("confidence_score", {})
            if isinstance(cs, dict):
                confidence_score = cs.get("score", 0)
            elif isinstance(cs, (int, float)):
                confidence_score = cs
            ca = data.get("completion_audit", {})
            completion_all_green = ca.get("all_green", False) if isinstance(ca, dict) else False

        elif stage_name == "test":
            # Same reader as REPORT.md: ``passed`` is a boolean in the current
            # test_report schema, so it is not a count of passing tests.
            _tm = _report_test_metrics(data)
            test_failed = _tm.get("failed", 0)
            test_passed = (_tm["tests_total"] - test_failed) if "tests_total" in _tm else 0

        elif stage_name == "build":
            fc = data.get("files_changed", [])
            build_files_changed = len(fc) if isinstance(fc, list) else (fc if isinstance(fc, int) else 0)
            tdd = data.get("tdd", {})
            build_tests_generated = tdd.get("tests_generated", tdd.get("smoke_tests", 0)) if isinstance(tdd, dict) else 0

    # --- Validation events ---
    validation_events = run_state.get("validation_events", [])
    validation_blocks = sum(1 for v in validation_events if not v.get("passed"))

    # --- Duration ---
    created = run_state.get("created_at", "")
    completed = run_state.get("completed_at", "")
    duration_minutes = None
    if created and completed:
        try:
            t0 = datetime.fromisoformat(created)
            t1 = datetime.fromisoformat(completed)
            duration_minutes = round((t1 - t0).total_seconds() / 60, 1)
        except (ValueError, TypeError):
            pass

    # --- Arena feed (Gate-2 → evolution, SCOPE B, run_4db42c78) ---
    # Auto-derive an adversarial_patterns block from the deliver stage RECORD
    # (run_state), NOT the artifact — so the environment-labeled verification
    # signal is captured at the completion gate without an artifact re-read
    # (Gate-1 fix: ACTION context is sourced from run_state stages).
    #
    # ACTION (what the agent CHOSE) is kept structurally separate from LABEL
    # (what the environment VERIFIED) — the authorship trap (CLASS A) defused at
    # the data layer. This path NEVER writes corrections.jsonl / correction_tracker
    # / auto_seed (AC6): it only reads run_state and returns a metrics dict.
    #
    # v1 keys by SEVERITY, not category — persisted findings carry `severity` but
    # NOT `category` (verified: 0 of 223 adversarial_review blocks have it), so a
    # category-keyed rollup is deferred to a future run that persists category.
    adversarial_patterns = None
    _deliver_rec = next((s for s in stages if s.get("stage") == "deliver"), None)
    if _deliver_rec is not None:
        # Prefer the stage-record adversarial_review; fall back to the
        # artifact copy loaded in the deliver branch above (some runs record it
        # only in the artifact — run_4db42c78 SMOKE). No extra read either way.
        _ar = _deliver_rec.get("adversarial_review")
        if not (isinstance(_ar, dict) and _ar.get("findings")):
            _ar = _deliver_artifact_ar
        _findings = _ar.get("findings", []) if isinstance(_ar, dict) else []
        if isinstance(_findings, list) and _findings:
            def _sev(f):
                return str(f.get("severity", "")).strip().upper() if isinstance(f, dict) else ""
            _high = sum(1 for f in _findings if _sev(f) in ("HIGH", "CRITICAL"))
            _med = sum(1 for f in _findings if _sev(f) in ("MED", "MEDIUM"))
            _low = sum(1 for f in _findings if _sev(f) == "LOW")
            # `other` = findings with an unrecognized/missing severity (INFO, "",
            # non-dict). Making it explicit keeps the partition TOTAL — a consumer
            # can rely on high+med+low+other == len(findings) (Gate-2 MED: unknown
            # severities used to vanish silently, breaking that invariant).
            _other = len(_findings) - _high - _med - _low
            _resolved = sum(1 for f in _findings
                            if isinstance(f, dict) and f.get("resolved"))
            _conv = _deliver_rec.get("convergence", {})
            _conv_iters = _conv.get("iterations", 0) if isinstance(_conv, dict) else 0
            # ACTION context from run_state stage records (no artifact re-read).
            # `next(..., {})` on the FIRST deliver rec — the pipeline is a linear
            # single-deliver flow; if retry-driven multi-deliver ever lands, revisit
            # (the artifact fallback would then track the last, not this first).
            _eval_rec = next((s for s in stages if s.get("stage") == "evaluate"), {})
            _think_rec = next((s for s in stages if s.get("stage") == "think"), {})
            adversarial_patterns = {
                "by_severity": {"high": _high, "med": _med, "low": _low, "other": _other},
                "resolved": _resolved,
                "first_pass_high": _high,
                "convergence_iterations": _conv_iters,
                "action": {
                    "profile": profile,
                    "scope": _eval_rec.get("scope", ""),
                    "approach": _think_rec.get("approach_chosen", ""),
                },
                "verifier_confidence": "structured",  # never a re-judged LLM score
            }

    _metrics = {
        "run_id": run_id,
        "project": project,
        "profile": profile,
        "status": run_state.get("status", "?"),
        "stages_completed": sum(1 for s in stages if s.get("status") in ("completed", "done")),
        "stages_total": len(stages),
        "duration_minutes": duration_minutes,
        # Tokens
        "total_tokens": total_tokens,
        "stage_tokens": stage_tokens,
        # Decisions
        "decisions": decision_counts,
        # Catches
        "catches": {
            "review_findings": review_findings,
            "review_rp_checked": review_rp_checked,
            "adversarial_findings": adversarial_findings,
            "adversarial_high": adversarial_high,
            "adversarial_resolved": adversarial_resolved,
            "validation_blocks": validation_blocks,
            "test_regressions": test_failed,
        },
        # Quality
        "quality": {
            "confidence_score": confidence_score,
            "completion_all_green": completion_all_green,
            "test_passed": test_passed,
            "test_failed": test_failed,
        },
        # Build
        "build": {
            "files_changed": build_files_changed,
            "tests_generated": build_tests_generated,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Only present when Gate-2 caught findings this run (no findings → no key,
    # so a clean run produces no arena signal — label-variance protection).
    if adversarial_patterns is not None:
        _metrics["adversarial_patterns"] = adversarial_patterns
    return _metrics


_CANON_STAGE_ORDER = (
    "evaluate", "think", "plan", "build", "review", "test",
    "goal_cycle", "deliver", "reflect",
)


def _safe_stage_name(name: object) -> str:
    """Render an on-disk stage name safely into markdown.

    SINGLE AUTHORITY for this escape. A stage name is attacker-influenced
    on-disk JSON, and it reaches the report in TWO places (the §4.1 table row
    and the "Slowest stage:" prose line). Escaping only the table row left the
    prose line able to emit a newline plus a forged `| FORGED | 999 min |` row
    into the report body — the exact fabrication the table escape exists to
    prevent, one line below the comment explaining it. Two call sites enforcing
    one invariant is the drift shape R27 exists to kill, hence one function.

    A plausible-looking wrong number is worse than no number.
    """
    return str(name).replace("|", "\\|").replace("\n", " ").replace("\r", " ")[:80]


def _stage_publish_times(
    project: str, run_state: dict, id_created: dict | None = None,
) -> tuple[list[dict], dict]:
    """Derive per-stage elapsed from artifact publish timestamps (read-only).

    Answers "which stage consumed the time" WITHOUT any new capture mechanism:
    each stage record already carries an ``artifact_id``, and manifest.json
    already carries a ``created`` ISO timestamp per artifact. Joining them
    resolves >=2 stages for ~71% of real runs, retroactively — a new writer
    could not (there is no code chokepoint every stage passes through: only
    2.0% of stage records are ``auto_recorded``).

    Returns ``(rows, coverage)`` where each row is
    ``{stage, at, elapsed_min, resolved, cycles}`` and coverage is
    ``{"resolved": n, "total": n}``. ``elapsed_min`` is the gap from the
    PREVIOUS resolved row.

    Two hazards this MUST guard (both measured on real runs, both would have
    shipped a visibly wrong table):

    * **Order by TIMESTAMP, never by array index.** ``run_state["stages"]`` is
      append-order, not execution order — 66 of 554 real runs (11.9%) hold
      canonical stages out of order, because an early stub record for a late
      stage pins its array slot permanently. Walking the array yields nonsense
      pairs such as ``evaluate -> reflect``.
    * **A negative gap can never be rendered — and the timestamp sort is what
      guarantees it, not a separate clamp.** A legitimate artifact re-publish
      RE-POINTs ``stage.artifact_id`` at a NEWER id (see
      ``_append_stage_to_run``), so a later stage can carry an EARLIER
      ``created``; 34 real runs would print a negative elapsed (worst -37.4 min)
      if intervals were computed in array order. Sorting ascending by timestamp
      makes ``row - prev >= 0`` hold by construction, so an explicit ``< 0``
      clamp here would be unreachable dead code. Stated explicitly because the
      clamp is the tempting-but-redundant fix: the ORDERING is the real guard,
      and a test written against a clamp would be vacuous (it passes with the
      clamp deleted).

    ``id_created`` lets a caller share an already-parsed map: ``cmd_run_report``
    is on a path that re-reads this 2.5 MB manifest once per stage already
    (~37 ms each), and ``ui_actions`` invokes it IN-PROCESS inside the daemon,
    so this helper must not add another parse of its own.

    Never raises: an absent or corrupt manifest degrades to zero coverage.
    """
    # SHAPE-VALIDATE every on-disk value before use. "Never raises" (above) is a
    # contract, and these files are hand-editable JSON: a non-list `stages`, a
    # non-list `artifacts`, or an unhashable `id`/`artifact_id` (list/dict) each
    # raised TypeError before this guard, killing the ENTIRE report — not just
    # the timing block. isinstance-at-the-boundary is the fix; a try around the
    # consumer would hide a real schema break behind an empty table.
    _raw_stages = run_state.get("stages")
    stages = (
        [s for s in _raw_stages if isinstance(s, dict)]
        if isinstance(_raw_stages, list) else []
    )
    total = len(stages)
    if id_created is None:
        id_created = {}
        manifest_file = (
            _get_workspace() / "Projects" / project / ".artifacts" / "manifest.json"
        )
        if manifest_file.exists():
            # Narrow guard, matching _load_artifact_for_metrics: a corrupt
            # manifest means "no timestamps", never a failed report.
            try:
                _man = json.loads(manifest_file.read_text(encoding="utf-8"))
                _arts = _man.get("artifacts")
                for _entry in (_arts if isinstance(_arts, list) else []):
                    # `id` must be a str: it becomes a dict KEY below, so a
                    # list/dict id raises "unhashable type" mid-parse.
                    if isinstance(_entry, dict) and isinstance(_entry.get("id"), str):
                        id_created[_entry["id"]] = _entry.get("created")
            # UnicodeDecodeError is a ValueError, NOT an OSError — a single
            # non-UTF-8 byte in the manifest would otherwise escape this guard
            # and, because the call site is deliberately outside a try, kill the
            # ENTIRE report (verified: `run-report` exits 1, no REPORT.md). The
            # sibling `_load_artifact_for_metrics` has the same hole; matching it
            # would be copying a bug, so this guard is deliberately wider.
            except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
                id_created = {}

    def _cycles(rec: dict):
        # The goal_cycle record's cycle count is written under inconsistent keys
        # across real runs (cycles / cycles_run / cycle_summary) — read all.
        for key in ("cycles", "cycles_run", "cycle_summary"):
            val = rec.get(key)
            # `not isinstance(val, bool)`: bool subclasses int in Python, so a
            # stray `cycles: true` would render "(True cycles)" in the report.
            if isinstance(val, int) and not isinstance(val, bool):
                return val
        return None

    resolved_rows: list[dict] = []
    unresolved_rows: list[dict] = []
    for rec in stages:
        name = rec.get("stage") or "?"
        row = {"stage": name, "at": None, "elapsed_min": None,
               "resolved": False, "cycles": _cycles(rec)}
        # `isinstance(_aid, str)`: artifact_id indexes a dict, so a list/dict
        # value raised "unhashable type" and killed the whole report.
        _aid = rec.get("artifact_id")
        raw = id_created.get(_aid) if isinstance(_aid, str) and _aid else None
        parsed = None
        if raw:
            try:
                parsed = datetime.fromisoformat(raw)
                # NORMALIZE to tz-aware before the sort below. A tz-naive value
                # mixed with a tz-aware one makes `sorted` raise TypeError
                # ("can't compare offset-naive and offset-aware datetimes"), and
                # that sort is deliberately OUTSIDE a try — so the whole report
                # would fail to generate, breaking this helper's "never raises"
                # contract and AC6's "the rest of the report still generates".
                # Guarded HERE rather than by wrapping the sort: a try around the
                # sort would hide the mix and silently drop every timing row,
                # which is the same defect wearing a quieter mask. Every current
                # writer emits UTC (artifact_registry uses datetime.now(
                # timezone.utc)), so a naive value means an older/hand-edited
                # entry, and UTC is the correct reading of it.
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                parsed = None
        if parsed is not None:
            row["at"] = raw
            row["_ts"] = parsed
            resolved_rows.append(row)
        else:
            unresolved_rows.append(row)

    # Timestamp order is the ONLY trustworthy progression signal (see docstring).
    resolved_rows.sort(key=lambda r: r["_ts"])
    prev = None
    for row in resolved_rows:
        if prev is not None:
            # >= 0 by construction: rows are sorted ascending above. That sort
            # IS the negative-elapsed guard (see docstring) — no clamp needed.
            row["elapsed_min"] = round((row["_ts"] - prev).total_seconds() / 60, 1)
        prev = row["_ts"]
        row["resolved"] = True
        row.pop("_ts", None)

    # Unresolved stages keep canonical position so the table still reads in
    # pipeline order rather than dumping them at the end arbitrarily.
    def _canon(row):
        try:
            return _CANON_STAGE_ORDER.index(row["stage"])
        except ValueError:
            return len(_CANON_STAGE_ORDER)

    rows = resolved_rows + sorted(unresolved_rows, key=_canon)
    return rows, {"resolved": len(resolved_rows), "total": total}


class LoadResult:
    """3-state result of a read-contract artifact load (Action B, Kiro #7).

    Distinguishes the three outcomes the old `dict | None` return CONFLATED, so a
    REPORT can render "unavailable" instead of a misleading real-looking 0:
      - ``available``   — artifact found, owned by this run, right type, parsed.
      - ``unavailable`` — a stage DID record an artifact_id, but it does not
        resolve (missing / corrupt / wrong-run / wrong-type / path-escape).
      - ``not_run``     — the stage recorded NO artifact_id at all (art_id None/empty).

    ``__bool__`` is True ONLY for ``available`` — so every legacy ``if loaded:`` /
    ``if isinstance(art, dict)`` caller keeps working unchanged while gaining the
    3-state distinction when it reads ``.state``.
    """

    __slots__ = ("state", "data")

    def __init__(self, state: str, data: dict | None = None):
        self.state = state
        self.data = data

    def __bool__(self) -> bool:
        return self.state == "available"


def _load_artifact_for_metrics(
    project: str, run_id: str | None, artifact_id: str | None,
    expected_type: "str | set[str] | frozenset[str] | list[str] | tuple[str, ...] | None" = None,
) -> LoadResult:
    """Load artifact data by ID with the Action B read contract.

    Enforces, in order:
      - ``not_run`` when ``artifact_id`` is falsy (no artifact recorded).
      - run OWNERSHIP (Kiro #4) — CONDITIONAL: a manifest entry carrying a
        ``run_id`` must match the passed ``run_id``; a legacy entry WITHOUT a
        run_id (~2169 pre-date the write side) passes (rejecting it would lose
        real data). A mismatch → ``unavailable``.
      - artifact TYPE (Kiro #4) — when ``expected_type`` is given, ``entry.type``
        must be IN it: a scalar is one valid type, a set/collection is a closed
        allowlist of valid types (think accepts research OR alternatives). A
        scalar is normalized to a 1-element set, so single-type stages stay
        exactly as strict; a type outside the set → ``unavailable`` (never
        fail-open). ``expected_type=None`` skips the check entirely.
      - the existing path-containment TRUST BOUNDARY (run_44a06138) — a
        ``..``-escaping ``entry.file`` is refused loudly → ``unavailable``.
    Any missing/corrupt file or manifest → ``unavailable`` (never a silent 0).
    """
    if not artifact_id:
        return LoadResult("not_run")
    ws = _get_workspace()
    manifest_file = ws / "Projects" / project / ".artifacts" / "manifest.json"
    if not manifest_file.exists():
        return LoadResult("unavailable")
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return LoadResult("unavailable")
    artifacts_dir = ws / "Projects" / project / ".artifacts"
    for entry in manifest.get("artifacts", []):
        if entry.get("id") != artifact_id:
            continue
        # OWNERSHIP (#4) — conditional: enforce only when the entry HAS a run_id.
        # A legacy run_id-less entry passes (the ~2169-entry back-compat case);
        # a present-but-mismatched run_id is a cross-run reference → refuse.
        entry_run_id = entry.get("run_id")
        if entry_run_id is not None and run_id is not None and entry_run_id != run_id:
            return LoadResult("unavailable")
        # TYPE (#4) — enforce only when caller declares an expected_type.
        # MEMBERSHIP, not scalar equality: a stage may have >1 contract-blessed
        # artifact type (think → research OR alternatives, per STAGE_ROUTING's
        # produces + optional_produces). A scalar expected_type is normalized to a
        # 1-element set, so single-type stages stay exactly as strict. Still a
        # CLOSED allowlist — a type outside the set is rejected (never fail-open).
        if expected_type is not None:
            accepted = (
                expected_type
                if isinstance(expected_type, (set, frozenset, list, tuple))
                else {expected_type}
            )
            if entry.get("type") not in accepted:
                return LoadResult("unavailable")
        data_file = artifacts_dir / entry.get("file", "")
        # TRUST BOUNDARY (run_44a06138): read-side twin of
        # ArtifactRegistry._entry_to_artifact. The manifest `file` is
        # caller-influenced; a tampered `../../../secret.json` must NOT be
        # read. Both legit shapes (bare + runs/<id>/) resolve INSIDE
        # .artifacts/; only a ..-escape fails. Surface loudly, drop.
        try:
            if not data_file.resolve().is_relative_to(artifacts_dir.resolve()):
                print(json.dumps({"security": (
                    f"metrics load for artifact {artifact_id!r} in project "
                    f"{project!r} has an out-of-bounds file path "
                    f"{entry.get('file')!r} — refusing to read (manifest tampering?)."
                )}), file=sys.stderr)
                return LoadResult("unavailable")
        except (OSError, ValueError):
            return LoadResult("unavailable")  # un-resolvable path → fail closed
        if data_file.exists():
            try:
                return LoadResult("available", json.loads(data_file.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                return LoadResult("unavailable")
        return LoadResult("unavailable")  # entry matched but data file gone
    return LoadResult("unavailable")  # no entry with this id


# Stage → expected artifact type(s) (the read-contract #4 type check). Mirrors the
# STAGE_ROUTING produces(+optional_produces) contract: evaluate→evaluation,
# think→{research, alternatives} (think's produces=research + optional_produces=
# alternatives — BOTH are valid, so a set), plan→design_doc, build→changeset,
# review→review, test→test_report, deliver→delivery, goal_cycle→changeset.
# A scalar value = single valid type; a set = any member is valid (membership
# test in _load_artifact_for_metrics). Keeping think scalar silently dropped
# every type=alternatives THINK artifact from REPORT.md (#3).
_STAGE_ARTIFACT_TYPE = {
    "evaluate": "evaluation",
    "think": {"research", "alternatives"},
    "plan": "design_doc",
    "build": "changeset",
    "goal_cycle": "changeset",
    "review": "review",
    "test": "test_report",
    "deliver": "delivery",
}


def _changeset_stage_for_profile(profile: str | None) -> str:
    """The stage whose artifact holds the changeset, by profile (Kiro #6).

    full/bugfix (and the default) build via the BUILD stage; a goal run builds
    inside GOAL_CYCLE, whose changeset artifact has a DIFFERENT schema
    (cycles/tests_total/regressions/mutations_red, NOT files_changed/lines_added).
    """
    return "goal_cycle" if normalize_profile(profile) == "goal" else "build"


def cmd_run_analytics(args, reg: ArtifactRegistry) -> None:
    """Cross-run analytics: aggregate metrics across completed pipeline runs.

    Reports: catch rates per stage, token trends, confidence distribution,
    adversarial review ROI, and validation block frequency.
    """
    runs = _load_completed_runs(args.project, limit=args.limit)
    if not runs:
        print(json.dumps({
            "project": args.project,
            "message": "No completed pipeline runs found",
        }))
        return

    # Collect metrics from each run (generate if needed)
    all_metrics: list[dict] = []
    for run_state in runs:
        run_id = run_state["id"]
        # run_dd8411f6 (#2 follow-through): _run_dir now RAISES on a malformed on-disk
        # `id` (traversal-guard, run_dd8411f6). `_load_completed_runs` does NOT validate
        # the `id` field, so a single corrupt/legacy run.json would abort the WHOLE
        # analytics batch. Degrade gracefully — skip that run, keep aggregating the rest
        # (pre-guard behavior was a harmless .exists()=False fall-through; preserve it).
        try:
            metrics_file = _run_dir(args.project, run_id) / "METRICS.json"
        except ValueError:
            continue
        if metrics_file.exists():
            try:
                m = json.loads(metrics_file.read_text(encoding="utf-8"))
                all_metrics.append(m)
                continue
            except (json.JSONDecodeError, OSError):
                pass
        # Generate on-demand
        m = _extract_run_metrics(args.project, run_id, run_state)
        all_metrics.append(m)
        # Persist for next time
        try:
            # Register the project first — never mint an unregistered Projects/<name>/.
            _ensure_project_dir(args.project)
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            metrics_file.write_text(json.dumps(m, indent=2), encoding="utf-8")
        except OSError:
            pass

    if not all_metrics:
        print(json.dumps({"project": args.project, "message": "No metrics extracted"}))
        return

    n = len(all_metrics)

    # --- Token analytics ---
    total_tokens_list = [m.get("total_tokens", 0) for m in all_metrics if m.get("total_tokens", 0) > 0]
    stage_token_agg: dict[str, list[int]] = {}
    for m in all_metrics:
        for stage, cost in m.get("stage_tokens", {}).items():
            if cost > 0:
                stage_token_agg.setdefault(stage, []).append(cost)

    # --- Catch rate analytics ---
    total_review_findings = sum(m.get("catches", {}).get("review_findings", 0) for m in all_metrics)
    total_adversarial = sum(m.get("catches", {}).get("adversarial_findings", 0) for m in all_metrics)
    total_adversarial_high = sum(m.get("catches", {}).get("adversarial_high", 0) for m in all_metrics)
    total_validation_blocks = sum(m.get("catches", {}).get("validation_blocks", 0) for m in all_metrics)
    total_test_regressions = sum(m.get("catches", {}).get("test_regressions", 0) for m in all_metrics)

    runs_with_adversarial = sum(1 for m in all_metrics if m.get("catches", {}).get("adversarial_findings", 0) > 0)
    runs_with_blocks = sum(1 for m in all_metrics if m.get("catches", {}).get("validation_blocks", 0) > 0)

    # --- Confidence distribution ---
    confidence_scores = [m.get("quality", {}).get("confidence_score", 0) for m in all_metrics if m.get("quality", {}).get("confidence_score", 0) > 0]

    # --- Decision analytics ---
    total_decisions = {
        "mechanical": sum(m.get("decisions", {}).get("mechanical", 0) for m in all_metrics),
        "taste": sum(m.get("decisions", {}).get("taste", 0) for m in all_metrics),
        "judgment": sum(m.get("decisions", {}).get("judgment", 0) for m in all_metrics),
    }

    # --- Profile distribution ---
    profile_counts: dict[str, int] = {}
    for m in all_metrics:
        p = m.get("profile", "unknown")
        profile_counts[p] = profile_counts.get(p, 0) + 1

    # --- Duration analytics ---
    durations = [m["duration_minutes"] for m in all_metrics if m.get("duration_minutes")]

    def _safe_avg(lst: list) -> float:
        return round(sum(lst) / len(lst), 1) if lst else 0

    _verdicts = [_completion_verdict(r)[0] for r in runs if isinstance(r, dict)]
    analytics = {
        "project": args.project,
        "runs_analyzed": n,
        # completed ≠ verified: verified = DELIVER push_ready (_completion_verdict)
        "verification": {"verified": sum(1 for v in _verdicts if v is True),
                         "unverified": sum(1 for v in _verdicts if v is False),
                         "not_applicable": sum(1 for v in _verdicts if v is None)},
        "profiles": profile_counts,
        # Token analytics
        "tokens": {
            "avg_per_run": _safe_avg(total_tokens_list),
            "min_per_run": min(total_tokens_list) if total_tokens_list else 0,
            "max_per_run": max(total_tokens_list) if total_tokens_list else 0,
            "total_all_runs": sum(total_tokens_list),
            "per_stage_avg": {
                stage: _safe_avg(costs)
                for stage, costs in sorted(stage_token_agg.items())
            },
        },
        # Catch analytics — the core value
        "catches": {
            "review_findings_total": total_review_findings,
            "review_findings_avg": round(total_review_findings / n, 2),
            "adversarial_findings_total": total_adversarial,
            "adversarial_high_total": total_adversarial_high,
            "adversarial_hit_rate": f"{runs_with_adversarial}/{n} runs ({round(runs_with_adversarial/n*100)}%)",
            "validation_blocks_total": total_validation_blocks,
            "validation_block_rate": f"{runs_with_blocks}/{n} runs ({round(runs_with_blocks/n*100)}%)",
            "test_regressions_total": total_test_regressions,
        },
        # Quality analytics
        "quality": {
            "confidence_avg": _safe_avg(confidence_scores),
            "confidence_min": min(confidence_scores) if confidence_scores else 0,
            "confidence_max": max(confidence_scores) if confidence_scores else 0,
            "confidence_distribution": {
                "low_0_4": sum(1 for s in confidence_scores if s <= 4),
                "med_5_7": sum(1 for s in confidence_scores if 5 <= s <= 7),
                "high_8_12": sum(1 for s in confidence_scores if s >= 8),
            },
        },
        # Decision analytics
        "decisions": {
            **total_decisions,
            "total": sum(total_decisions.values()),
            "automation_rate": (
                f"{round(total_decisions['mechanical'] / sum(total_decisions.values()) * 100)}%"
                if sum(total_decisions.values()) > 0 else "N/A"
            ),
        },
        # Duration analytics
        "duration": {
            "avg_minutes": _safe_avg(durations),
            "min_minutes": min(durations) if durations else 0,
            "max_minutes": max(durations) if durations else 0,
        },
    }

    print(json.dumps(analytics, indent=2))


def cmd_bind(args, reg) -> None:
    """CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml.

    The invokable entrypoint for the DDD binding layer — replaces the old python -c
    prose recipe. Calls core.ddd_bindings.bind_project (which loops ALL bindings with
    per-binding isolation) and prints one JSON outcome per binding. A pure-DDD project
    (no bindings.yaml) prints an empty list. Exit 1 only if EVERY binding failed
    (a mixed bound/deferred/failed set still exits 0 — deferred + isolated-failure are
    expected, not a run-level error).
    """
    from core.ddd_bindings import bind_project

    try:
        outcomes = bind_project(args.project)
    except (FileNotFoundError, ValueError) as e:
        # A malformed bindings.yaml (ValueError names the bad field) is a run-level error.
        print(json.dumps({"error": f"bind failed for '{args.project}': {e}"}), file=sys.stderr)
        sys.exit(1)

    payload = [
        {"repo": o.repo, "kind": o.kind, "status": o.status, "error": o.error,
         "worktree": o.worktree, "code_intel_db": o.code_intel_db, "node_count": o.node_count}
        for o in outcomes
    ]
    print(json.dumps({"project": args.project, "bindings": payload,
                      "counts": {
                          "bound": sum(1 for o in outcomes if o.status == "bound"),
                          "deferred": sum(1 for o in outcomes if o.status == "deferred"),
                          "failed": sum(1 for o in outcomes if o.status == "failed"),
                      }}, indent=2, default=str))
    # Run-level failure ONLY when there were bindings and every one failed.
    if outcomes and all(o.status == "failed" for o in outcomes):
        sys.exit(1)


def cmd_ddd_health(args, reg) -> None:
    """5-dimensional DDD health scoring per section."""
    project_dir = _get_workspace() / "Projects" / args.project
    if not project_dir.is_dir():
        print(json.dumps({"error": f"Project '{args.project}' not found"}))
        return

    from core.ddd_health import compute_section_health

    # persist=False: this CLI just PRINTS the scores (a read/inspect command) —
    # it must not write section_health.json as a side effect; the scheduled
    # ddd_refresh job owns the snapshot. #5 fix, run_e90535ea.
    result = compute_section_health(project_dir, persist=False)
    print(json.dumps(result, indent=2, default=str))


def cmd_ddd_retire(args, reg) -> None:
    """Agent-directed RETIRE of ONE named (title, section) knowledge entry — the
    sanctioned 'out' side of the knowledge layer (the counterpart to s_persist's
    add). Archives the entry (stays FTS5-recallable) + physically strips it from
    the doc by (title, section) identity + writes a dated .bak. Default is a
    dry-run preview; pass --apply to persist.

    This exists so removals do NOT go through a raw Edit-to-delete (which skips
    the archive → lost from recall, and can destroy a same-title sibling).
    Fail-loud: no match / ambiguous duplicate → error, exit 1.
    """
    from core.ddd_entry_lifecycle import retire_entry, RetireError, MEMORY_EVERGREEN_SECTIONS

    ws = _get_workspace()
    p = Path(args.file)
    if not p.is_absolute():
        p = ws / args.file
    if not p.exists():
        print(json.dumps({"error": f"file not found: {p}"}), file=sys.stderr)
        sys.exit(1)

    content = p.read_text(encoding="utf-8")
    archive_name = args.archive_name or (
        "MEMORY-archive.md" if p.name == "MEMORY.md" else "IMPROVEMENT-archive.md"
    )
    try:
        report = retire_entry(
            content, title=args.title, section=args.section,
            project_dir=p.parent, archive_name=archive_name,
            source_path=p if args.apply else None,
            dry_run=not args.apply, force=bool(args.force),
            # MEMORY.md's evergreen sections (Open Threads, Standing Preferences,
            # etc.) need --force parity with the autonomous reclaim path.
            evergreen_sections=MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None,
        )
    except RetireError as e:
        print(json.dumps({"error": str(e), "retired": False}), file=sys.stderr)
        sys.exit(1)

    print(json.dumps({
        "retired": bool(args.apply),
        "dry_run": not args.apply,
        "candidate": report.candidates[0] if report.candidates else None,
        "archived": report.archived,
        "archive_file": archive_name,
        "hint": ("preview only — pass --apply to persist" if not args.apply
                 else f"retired to {archive_name} (dated .bak written)"),
    }))


def cmd_ddd_noise(args, reg) -> None:
    """Per-doc DDD noise gate (M0 ②): reclaimable noise_rate + PASS/FAIL verdict.

    Measures the GATE metric (raw noise MINUS keep-class) so a doc heavy in
    permanent-but-dormant knowledge (COE/principles) doesn't FAIL spuriously.
    Verdict string (PASS/FAIL) is in stdout for the golden_set canary to assert.
    Always exits 0 — the canary keys off the string, not the exit code.
    """
    from datetime import date
    from core.ddd_entry_lifecycle import (
        parse_entries, compute_reclaimable_noise, NOISE_FAIL_THRESHOLD,
        MEMORY_EVERGREEN_SECTIONS,
    )

    ws = _get_workspace()
    today = date.today()

    # Build the doc list: --doc (one file) or default scan (MEMORY + all IMPROVEMENT).
    docs: list[tuple[str, "Path", "frozenset[str] | None"]] = []
    if getattr(args, "doc", None):
        p = Path(args.doc)
        if not p.is_absolute():
            p = ws / args.doc
        ever = MEMORY_EVERGREEN_SECTIONS if p.name == "MEMORY.md" else None
        docs.append((args.doc, p, ever))
    else:
        mem = ws / ".context" / "MEMORY.md"
        if mem.exists():
            docs.append(("MEMORY.md", mem, MEMORY_EVERGREEN_SECTIONS))
        proj_dir = ws / "Projects"
        if proj_dir.is_dir():
            for imp in sorted(proj_dir.glob("*/IMPROVEMENT.md")):
                docs.append((str(imp.relative_to(ws)), imp, None))

    results = []
    worst_fail = False
    for name, path, ever in docs:
        if not path.exists():
            results.append({"doc": name, "error": "not found"})
            continue
        report = compute_reclaimable_noise(
            parse_entries(path.read_text(encoding="utf-8")), today,
            evergreen_sections=ever,
        )
        verdict = "FAIL" if report.noise_rate > NOISE_FAIL_THRESHOLD else "PASS"
        if verdict == "FAIL":
            worst_fail = True
        results.append({
            "doc": name, "total": report.total, "noisy": report.noisy,
            "noise_rate": round(report.noise_rate, 4), "verdict": verdict,
        })

    print(json.dumps({
        "threshold": NOISE_FAIL_THRESHOLD,
        "overall": "FAIL" if worst_fail else "PASS",
        "docs": results,
    }, indent=2))


# ═══════════════════════════════════════════════════════════════════════════
# Stage boundaries: `stage-start` / `stage-complete` (pipeline-speed-systematic-fix
# §4-A, F2, G1). Mechanical work moves into the tool: the model writes judgment
# fields only; derived fields, publish, record, timing and advance are computed
# here. ALL checks run BEFORE any write (fail-closed: an error writes nothing).
# The checks themselves are the existing ones — validate_artifact_data, validate()
# (via pipeline_validator.validate_stage_state on the in-memory candidate) and
# the stage-record schema — never a copy.
# ═══════════════════════════════════════════════════════════════════════════

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def _write_run_atomic(run_file: Path, data: dict) -> None:
    """Write run.json via a same-dir temp file + os.replace (no torn file)."""
    tmp = run_file.with_name(f".run.json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, run_file)


def cmd_main_red(args, reg: ArtifactRegistry) -> None:
    """Read-only: tests red on main (pre-existing in some run's scope), oldest first.
    Each needs its own bug-fixing run; a landing whose tests pass closes it."""
    from core import main_red
    reds = sorted(main_red.open_reds().values(), key=lambda r: str(r.get("first_seen") or ""))
    print(json.dumps({"open": len(reds), "registry": str(main_red.registry_path()),
                      "reds": reds}, ensure_ascii=False, indent=2))


def _main_reds_in_scope(run_dir: Path, run_id: str) -> "list[dict]":
    """Open main-red entries among this run's selected test files, for REPORT §10."""
    try:
        import pipeline_validator as pv
        from core import main_red
        reds = main_red.open_reds()
        if not reds:
            return []
        files = {f for ev in pv.load_test_evidence(run_dir) if ev.get("run_id") in (None, run_id)
                 for f in ev.get("selected_files") or []}
        return [r for n, r in sorted(reds.items()) if n.split("::")[0] in files]
    except Exception as e:  # noqa: BLE001 — a report line, never a failure
        import logging
        logging.getLogger(__name__).debug("main-red scope lookup failed: %s", e)
        return []


# ── Gate-2, convergence = deterministic (step 3a of Knowledge/Designs/2026-10-19-
# pipeline-deterministic-convergence.md): models find, tests decide ─────────────
# gate2-next rebuilds the finding ledger from the captured expert answers, the
# author's declared events (run.json gate2_v2.events) and the runner's prove evidence
# (core.gate2_replay), and prints the ONE next step. Two review rounds at most; a
# finding closes only by a valid proof; what is not proven lowers push_ready.
def _deterministic(run_state) -> bool:
    """The run uses the deterministic Gate-2 flow (pipeline_validator.
    model_verdicts_decide is the one definition of the mode)."""
    import pipeline_validator as pv
    return isinstance(run_state, dict) and not pv.model_verdicts_decide(run_state.get("convergence"))


def main() -> None:
    import functools
    parser = argparse.ArgumentParser(
        description="Artifact registry CLI for SwarmAI pipeline", allow_abbrev=False,
    )
    # No option abbreviations: the run-owner gate reads --run-id/--stage/--status exactly
    # as written, so `--run-i X` must not silently mean --run-id (pipeline-run-worktree §4.2).
    sub = parser.add_subparsers(dest="command", required=True,
                                parser_class=functools.partial(argparse.ArgumentParser,
                                                               allow_abbrev=False))

    # discover
    p_discover = sub.add_parser("discover", help="Discover artifacts by type")
    p_discover.add_argument("--project", required=True)
    p_discover.add_argument("--types", required=True, help="Comma-separated types")
    p_discover.add_argument("--full", action="store_true", help="Include full artifact data")

    # publish
    p_publish = sub.add_parser("publish", help="Publish a new artifact")
    p_publish.add_argument("--project", required=True)
    p_publish.add_argument("--type", required=True)
    p_publish.add_argument("--producer", required=True)
    p_publish.add_argument("--summary", required=True)
    p_publish.add_argument("--data", required=True, help="JSON data string")
    p_publish.add_argument("--topic", default="")
    p_publish.add_argument("--run-id", default=None, help="Pipeline run ID (stores in runs/<id>/ subdir)")
    p_publish.add_argument("--stage", default=None, help="Pipeline stage for schema validation (validates BEFORE publishing)")
    p_publish.add_argument("--quiet", action="store_true",
                           help="Emit ONLY a single-line {\"artifact_id\": ...} on success "
                                "(parse-proof for orchestrators); on validation failure emit a "
                                "SHORT single-line {\"validation_failed\":true,\"errors\":[...]} "
                                "instead of the verbose indented schema dump.")

    # schema — fetch a stage's expected artifact template without a failed publish
    p_schema = sub.add_parser("schema", help="Print a stage's expected artifact schema + template (single-line JSON)")
    p_schema.add_argument("--stage", required=True,
                          help="Pipeline stage (evaluate/think/plan/build/review/test/deliver)")

    # state
    p_state = sub.add_parser("state", help="Get pipeline state")
    p_state.add_argument("--project", required=True)

    # advance
    p_advance = sub.add_parser("advance", help="Advance pipeline state")
    p_advance.add_argument("--project", required=True)
    p_advance.add_argument("--state", required=True)
    p_advance.add_argument(
        "--run-id",
        help="Run to auto-validate before advancing. REQUIRED to enable the "
             "pre-advance validation gate; omitting it skips validation (we never "
             "guess which run to validate — run_f3975b8b).",
    )
    p_advance.add_argument(
        "--quiet", action="store_true",
        help="Emit a compact {project,pipeline_state,advanced} payload, dropping "
             "the large next_stage_doc/next_action continuation fields (mirrors "
             "publish --quiet; for programmatic callers that only confirm the move).",
    )

    # learn
    p_learn = sub.add_parser("learn", help="Record pipeline outcome for learning")
    p_learn.add_argument("--project", required=True)
    p_learn.add_argument("--evaluation-id", required=True, help="ID of evaluation artifact")
    p_learn.add_argument("--outcome", required=True, choices=["success", "partial", "failure", "cancelled"])
    p_learn.add_argument("--actual-effort", default=None, help="Actual effort (T-shirt or sessions)")
    p_learn.add_argument("--lessons", default=None, help="Semicolon-separated lessons")

    # projects
    sub.add_parser("projects", help="List all projects")


    # run-get
    p_run_get = sub.add_parser("run-get", help="Get pipeline run state")
    p_run_get.add_argument("--project", required=True)
    p_run_get.add_argument("--run-id", default=None, help="Specific run ID (omit for list)")
    p_run_get.add_argument("--brief", action="store_true",
                           help="Compact view: status, stages, current stage, files, latest test counts + failures")
    p_run_get.add_argument("--field", action="append", default=None, metavar="PATH",
                           help="Only this dot path of run.json (repeatable), e.g. stages.3.status")


    # run-surface-changes (read-only git sweep → content/knowledge/source/process buckets)
    p_run_surface = sub.add_parser("run-surface-changes", help="Git-based COMPLETE sweep: classify this run's changed files (SwarmWS + bound repos) into surface buckets")
    p_run_surface.add_argument("--project", required=False, help="(optional) informational only — the sweep spans all trees")
    p_run_surface.add_argument("--run-id", required=False, help="(optional) informational only")


    # run-history
    p_run_hist = sub.add_parser("run-history", help="Historical token costs for calibration")
    p_run_hist.add_argument("--project", required=True)
    p_run_hist.add_argument("--limit", type=int, default=10, help="Max completed runs to analyze")

    # run-budget
    p_run_bgt = sub.add_parser("run-budget", help="Check budget status for active pipeline")
    p_run_bgt.add_argument("--project", required=True)
    p_run_bgt.add_argument("--run-id", required=True, help="Pipeline run ID")

    # main-red (core/main_red.py): tests red on main, each awaiting its own fix run
    sub.add_parser("main-red", help="List tests red on main (pre-existing), read-only")

    # run-status (v3: cross-project dashboard)
    p_run_status = sub.add_parser("run-status", help="Cross-project pipeline dashboard")
    p_run_status.add_argument("--active-only", action="store_true", help="Only show running/paused")


    # run-summary (§4-E: re-render the chat delivery summary at any time)
    p_run_summary = sub.add_parser("run-summary", help="Render the chat delivery summary of a run")
    p_run_summary.add_argument("--project", required=True)
    p_run_summary.add_argument("--run-id", required=True, help="Pipeline run ID")

    # run-analytics (cross-run aggregation)
    p_run_analytics = sub.add_parser("run-analytics", help="Cross-run pipeline analytics")
    p_run_analytics.add_argument("--project", required=True)
    p_run_analytics.add_argument("--limit", type=int, default=50, help="Max runs to analyze")


    # bind (CREATE→BIND→PULL: clone + index every binding of a project's bindings.yaml)
    p_bind = sub.add_parser("bind", help="PULL: clone + code-intel every binding in a project's bindings.yaml")
    p_bind.add_argument("--project", required=True, help="Project whose bindings.yaml to PULL")

    # ddd-health
    p_ddd_health = sub.add_parser("ddd-health", help="5-dimensional DDD health scoring per section")
    p_ddd_health.add_argument("--project", required=True)

    # ddd-retire (run_186a5f15: agent-directed named-entry RETIRE — knowledge 'out' side)
    p_ddd_retire = sub.add_parser("ddd-retire", help="Retire ONE named (title,section) knowledge entry: archive + strip (dry-run unless --apply)")
    p_ddd_retire.add_argument("--file", required=True, help="Doc path (relative to workspace or absolute), e.g. .context/MEMORY.md or Projects/X/IMPROVEMENT.md")
    p_ddd_retire.add_argument("--title", required=True, help="Exact entry title (the bold text after the [type] tag)")
    p_ddd_retire.add_argument("--section", required=True, help="Exact '## Section' the entry lives under")
    p_ddd_retire.add_argument("--archive-name", dest="archive_name", default=None, help="Archive filename (default MEMORY-archive.md for MEMORY.md, else IMPROVEMENT-archive.md)")
    p_ddd_retire.add_argument("--apply", action="store_true", help="Persist the retire (default is dry-run preview)")
    p_ddd_retire.add_argument("--force", action="store_true", help="Retire even a keep-class entry (decision/model/principle/correction/COE)")

    # ddd-noise (M0 ②: per-doc reclaimable-noise gate)
    p_ddd_noise = sub.add_parser("ddd-noise", help="Per-doc DDD noise gate (reclaimable noise_rate + PASS/FAIL)")
    p_ddd_noise.add_argument("--project", default=None, help="(unused; scans MEMORY + all IMPROVEMENT by default)")
    p_ddd_noise.add_argument("--doc", default=None, help="Single doc path (relative to workspace or absolute)")

    # release-gate (run_9fec1fb1: code-enforced CI-green gate for s_swarm-release 7b)
    p_rel_gate = sub.add_parser("release-gate", help="CI-green gate: poll CI for HEAD, write marker on green (authorizes gh release create)")
    p_rel_gate.add_argument("--project", default="SwarmAI", help="Project owning the marker (default SwarmAI)")
    p_rel_gate.add_argument("--poll", action="store_true", help="One gh run list check for HEAD; green→write marker (exit 0), red→exit 1, not-done→exit 3")
    p_rel_gate.add_argument("--status", action="store_true", help="Print marker vs HEAD (does it authorize current HEAD?) without touching CI")
    p_rel_gate.add_argument("--verify", action="store_true", help="PRE-PUBLISH gate (fail-closed): authorize the publish IFF the CI-green marker attests the commit being released. exit 0=authorized, 1=blocked. All-local, no network. Call in s_swarm-release Stage 7b BEFORE `gh release create/edit`. (Replaces the release_publish_guard hook.)")
    p_rel_gate.add_argument("--clear", action="store_true", help="Delete the marker (call after publish, or to reset)")
    p_rel_gate.add_argument("--ref", default=None, help="Tag or sha of the commit BEING RELEASED (e.g. v1.26.0). Resolves <ref>^{commit}, polls CI for THAT commit, records it (+the tag) in the marker. Omit for a branch-tip release (legacy HEAD behavior).")


    from core.run_worktree import CLAIM_RESOLVES

    import pipeline_worktree as _pw
    _pw.refuse_if_worktree_copy(__file__)
    retired_run_commands = {
        "run-create", "run-update", "run-commit", "run-checkpoint", "run-resume",
        "run-report", "run-metrics", "run-cultivate", "run-observe", "stage-start",
        "stage-complete", "gate0-prompt", "gate2-recheck", "gate2-fixspec",
        "gate2-ledger", "gate2-next", "run-diff", "run-repo-add", "run-claim-main",
        "cleanup-orphans", "purge-garbage",
    }
    if len(sys.argv) > 1 and sys.argv[1] in retired_run_commands:
        print(json.dumps({
            "error": "legacy pipeline mutation command removed by Pipeline v1; use "
                     "backend/scripts/pipeline_v1.py (runs without protected contract.json "
                     "are read-only historical records)"}), file=sys.stderr)
        sys.exit(2)
    args = parser.parse_args()

    retired = (args.command == "advance" and getattr(args, "run_id", None)) \
        or (args.command == "publish" and (getattr(args, "run_id", None)
                                            or getattr(args, "stage", None)))
    if retired:
        print(json.dumps({
            "error": "legacy pipeline mutation command removed by Pipeline v1; use "
                     "backend/scripts/pipeline_v1.py (runs without protected contract.json "
                     "are read-only historical records)"
        }), file=sys.stderr)
        raise SystemExit(2)
    _pw.lock_command(args.command, getattr(args, "project", None),
                     getattr(args, "run_id", None), _run_dir)
    reg = ArtifactRegistry(_get_workspace())

    handlers = {
        "discover": cmd_discover,
        "publish": cmd_publish,
        "schema": cmd_schema,
        "learn": cmd_learn,
        "state": cmd_state,
        "advance": cmd_advance,
        "projects": cmd_projects,
        "run-get": cmd_run_get,
        "run-surface-changes": cmd_run_surface_changes,
        "run-history": cmd_run_history,
        "run-budget": cmd_run_budget,
        "main-red": cmd_main_red,
        "run-status": cmd_run_status,
        "run-summary": cmd_run_summary,
        "run-analytics": cmd_run_analytics,
        "bind": cmd_bind,
        "ddd-health": cmd_ddd_health,
        "ddd-retire": cmd_ddd_retire,
        "ddd-noise": cmd_ddd_noise,
        "release-gate": cmd_release_gate,
    }
    try:
        handlers[args.command](args, reg)
    except json.JSONDecodeError:
        # NARROW the catch below: JSONDecodeError subclasses ValueError. A corrupt
        # user-supplied --stage-json / --taste-decision / a malformed run.json must
        # KEEP its original uncaught-traceback behavior (it is a user/data error, not
        # a blocked-identifier refusal), not be mislabeled as a path-identifier reject.
        raise
    except ValueError as e:
        # run_dd8411f6 (#2, Gate-1 amendment B): a traversal run_id/project raises
        # ValueError from _validate_path_identifiers (via _run_dir / _ensure_project_dir).
        # main() has no other top-level catch, so without this a blocked-but-malicious
        # identifier would crash with a Python traceback instead of the CLI's standard
        # {"error": ...} JSON + clean non-zero exit. The traversal is still BLOCKED
        # (no path escape) either way; this just makes the refusal legible, not a crash.
        # NOTE: the `advance` command does NOT reach here — cmd_advance wraps
        # _auto_validate_before_advance in its own `except Exception` and surfaces a
        # traversal run_id as "Validator crashed" + exit 1 (still fail-closed/blocked,
        # just a different exit code + message). Every OTHER run-id-bearing command
        # (run-update/commit/checkpoint/resume/observe/report) reaches this handler.
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
