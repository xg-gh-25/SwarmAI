"""Per-stage timing derivation + REPORT.md duration correctness (run_fbf97252).

WHAT IS TESTED
--------------
The pipeline could not answer "which stage consumed the time". Three defects,
each with a test class here:

1. ``_try_generate_metrics`` did a bare ``write_text`` on METRICS.json, so
   completing a run DESTROYED whatever ``run-observe`` had accumulated
   (``stage_timing``/``think_depth``/``profile_decision``). Measured before the
   fix: 481 completed runs with no ``stage_timing`` vs 3 with, and every run
   still carrying ``wall_minutes`` was cancelled/abandoned/paused — i.e. it had
   never reached the destructive write. → ``TestMetricsMerge``.
2. ``REPORT.md`` rendered ``Duration: N/A`` for *every* run, because
   ``cmd_run_report`` requires ``completed_at`` while ``completed_at`` is only
   written by the ``--status completed`` branch that INSTRUCTIONS.md ran
   *afterwards* (measured: 466 of 466 N/A reports were generated before
   ``completed_at`` existed). Fixed by regenerating the report inside the
   completion branch, plus an ``updated_at`` fallback for genuinely in-flight
   runs. → ``TestCompletionRegeneratesReport`` / ``TestInFlightDurationFallback``.
3. No surface showed per-stage elapsed. Derived read-only from the artifact
   ``created`` timestamps already in manifest.json. → ``TestStagePublishTimes``
   / ``TestTimingSectionRendering``.

METHODOLOGY / KEY PROPERTIES
----------------------------
Fixtures are deliberately shaped to have TEETH against the specific tautology
traps a Gate-1 review identified:

* The in-flight duration fixture OMITS ``completed_at`` — a fixture that *sets*
  it passes against the unmodified code and proves nothing.
* Coverage is asserted with a MIXED-cause fixture (missing artifact_id, id absent
  from the manifest, unparseable timestamp, one good stage) so a hardcoded
  ``len(stages)`` denominator cannot pass.
* Ordering is asserted with the slowest stage placed LAST in ``stages[]`` but
  FIRST by timestamp — an array-order implementation fails. ``stages[]`` is
  append-order and is out of canonical order in 11.9% of real runs.
* Negative intervals are asserted as *unknown*, never rendered. 34 real runs
  would otherwise print a negative elapsed (worst -37.4 min) because a
  re-published artifact legitimately re-points ``artifact_id`` to a newer id.
* The malformed-manifest test writes genuinely invalid bytes rather than mocking
  a raise, so the real ``json`` parse path executes.
* ``TestTwoAttributeArgs`` constructs exactly ``SimpleNamespace(project, run_id)``
  — mirroring ``ui_actions.py``'s in-process daemon call, which passes only those
  two attributes. It is the only guard against a production-only AttributeError.
"""
from __future__ import annotations

import contextlib
import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

BASE = datetime(2026, 9, 7, 10, 0, 0, tzinfo=timezone.utc)


def _iso(minutes: float) -> str:
    return (BASE + timedelta(minutes=minutes)).isoformat()


def _write_manifest(ws, project: str, entries: list[dict]) -> None:
    """Write .artifacts/manifest.json with the real production shape."""
    art_dir = ws / "Projects" / project / ".artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    (art_dir / "manifest.json").write_text(
        json.dumps({
            "project": project,
            "pipeline_state": "deliver",
            "updated_at": _iso(0),
            "artifacts": entries,
        }, indent=2),
        encoding="utf-8",
    )


def _write_run(ws, project: str, run_id: str, run_data: dict):
    run_dir = ws / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps(run_data, indent=2), encoding="utf-8")
    return run_dir


def _artifact(art_id: str, created: str) -> dict:
    return {
        "id": art_id,
        "created": created,
        "file": f"{art_id}.json",
        "producer": "s_autonomous-pipeline",
        "summary": "x",
        "type": "evaluation",
        "superseded_by": None,
    }


def _stage(name: str, art_id: str | None = None, **extra) -> dict:
    rec = {"stage": name, "status": "completed", "stage_doc_consumed": True}
    if art_id is not None:
        rec["artifact_id"] = art_id
    rec.update(extra)
    return rec


# ── 1. METRICS.json merge (AC8) ──────────────────────────────────────────────

class TestMetricsMerge:
    """Completing a run must NOT destroy previously-observed telemetry."""

    def test_existing_stage_timing_survives_completion(self, tmp_path, monkeypatch):
        """stage_timing recorded by run-observe survives _try_generate_metrics.

        RED before the fix: _try_generate_metrics built a fresh dict and
        write_text'd it, so stage_timing vanished the moment a run completed.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _try_generate_metrics, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_merge01"
        run_data = {
            "id": run_id, "project": project, "profile": "full",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            "stages": [_stage("evaluate", "art_a")], "taste_decisions": [],
        }
        run_dir = _write_run(tmp_path, project, run_id, run_data)
        _write_manifest(tmp_path, project, [_artifact("art_a", _iso(5))])

        # Pre-existing observe telemetry (what cmd_run_observe writes).
        (run_dir / "METRICS.json").write_text(json.dumps({
            "stage_timing": {"build": {"start": _iso(10), "end": _iso(25),
                                       "wall_minutes": 15.0}},
            "think_depth": {"alternatives_count": 3},
        }, indent=2), encoding="utf-8")

        reg = ArtifactRegistry(_get_workspace())
        _try_generate_metrics(project, run_id, run_data, reg)

        after = json.loads((run_dir / "METRICS.json").read_text())
        assert "stage_timing" in after, "completion destroyed stage_timing"
        assert after["stage_timing"]["build"]["wall_minutes"] == 15.0
        assert after["think_depth"]["alternatives_count"] == 3
        # Freshly extracted keys must still be written.
        assert after.get("run_id") == run_id

    def test_fresh_keys_win_over_stale(self, tmp_path, monkeypatch):
        """A key present in BOTH is taken from the fresh extraction, not the file."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _try_generate_metrics, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_merge02"
        run_data = {
            "id": run_id, "project": project, "profile": "full",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            "stages": [_stage("evaluate", "art_a")], "taste_decisions": [],
        }
        run_dir = _write_run(tmp_path, project, run_id, run_data)
        _write_manifest(tmp_path, project, [_artifact("art_a", _iso(5))])
        (run_dir / "METRICS.json").write_text(
            json.dumps({"run_id": "STALE_VALUE", "stage_timing": {}}, indent=2),
            encoding="utf-8")

        reg = ArtifactRegistry(_get_workspace())
        _try_generate_metrics(project, run_id, run_data, reg)

        after = json.loads((run_dir / "METRICS.json").read_text())
        assert after["run_id"] == run_id, "stale file value must not win"

    def test_in_flight_duration_uses_updated_at(self, tmp_path, monkeypatch):
        """completed_at ABSENT -> duration derived from updated_at, marked in-progress.

        The fixture deliberately OMITS completed_at. A fixture that sets it would
        pass against the unmodified code (the `created and completed` branch
        already handles that case) and would therefore have no teeth.

        updated_at was chosen over "newest stage artifact timestamp" on measured
        accuracy against ground truth on 426-433 real runs:
          updated_at            median  +0.00% err, 97.5% within +/-1%
          newest stage artifact median  -4.34% err, worst -98.98%, 13.1% within +/-1%
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_inflight"
        run_data = {
            "id": run_id, "project": project, "profile": "full",
            "requirement": "in-flight run", "status": "running",
            "created_at": _iso(0),
            "updated_at": _iso(90),        # 90 minutes of real elapsed
            # completed_at deliberately ABSENT — this is the state at the moment
            # run-report is invoked (466 of 466 real N/A reports looked like this)
            "stages": [_stage("evaluate", "art_a"), _stage("think", "art_b")],
            "taste_decisions": [],
        }
        _write_run(tmp_path, project, run_id, run_data)
        _write_manifest(tmp_path, project, [
            _artifact("art_a", _iso(10)), _artifact("art_b", _iso(30)),
        ])

        reg = ArtifactRegistry(_get_workspace())
        cmd_run_report(SimpleNamespace(project=project, run_id=run_id), reg)

        text = (tmp_path / "Projects" / project / ".artifacts" / "runs"
                / run_id / "REPORT.md").read_text()
        assert "Duration:** N/A" not in text, "in-flight run still rendered N/A"
        assert "90.0 min" in text, f"expected updated_at-derived 90.0 min: {text[:400]}"
        assert "in progress" in text.lower(), "in-flight value must be marked"

    def test_corrupt_existing_metrics_does_not_block(self, tmp_path, monkeypatch):
        """Malformed METRICS.json degrades to a fresh write, never raises.

        Real bytes, not a mocked raise, so the actual json parse path runs.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _try_generate_metrics, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_merge03"
        run_data = {
            "id": run_id, "project": project, "profile": "full",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            "stages": [_stage("evaluate", "art_a")], "taste_decisions": [],
        }
        run_dir = _write_run(tmp_path, project, run_id, run_data)
        _write_manifest(tmp_path, project, [_artifact("art_a", _iso(5))])
        (run_dir / "METRICS.json").write_text("{invalid json", encoding="utf-8")

        reg = ArtifactRegistry(_get_workspace())
        _try_generate_metrics(project, run_id, run_data, reg)  # must not raise

        after = json.loads((run_dir / "METRICS.json").read_text())
        assert after.get("run_id") == run_id


# ── 2. per-stage timing derivation (AC1/AC3/AC10) ────────────────────────────

class TestStagePublishTimes:
    """Derive per-stage elapsed from manifest `created` timestamps.

    Two data hazards drive these tests, both measured on real runs:

    * ``run_state["stages"]`` is APPEND order, not execution order — 11.9% of
      real runs (66/554) hold canonical stages out of order, because an early
      stub record for a late stage pins its array position permanently. Walking
      the array would compute an ``evaluate -> reflect`` interval and label it
      pipeline progression.
    * A legitimate artifact re-publish RE-POINTs ``stage.artifact_id`` at a NEWER
      id, so a later stage can carry an EARLIER ``created``. 34 real runs would
      render a negative elapsed (worst -37.4 min).
    """

    def test_rows_ordered_by_timestamp_not_array_position(self, tmp_path, monkeypatch):
        """Slowest stage is LAST in stages[] but FIRST by timestamp.

        An array-order implementation fails this: it would pair the wrong
        neighbours and mis-name the slowest stage.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        project = "P"
        # stages[] deliberately scrambled: 'deliver' sits before 'think'/'plan'
        run_state = {"stages": [
            _stage("evaluate", "art_e"),
            _stage("deliver", "art_d"),
            _stage("think", "art_t"),
            _stage("plan", "art_p"),
        ]}
        _write_manifest(tmp_path, project, [
            _artifact("art_e", _iso(0)),
            _artifact("art_t", _iso(5)),
            _artifact("art_p", _iso(45)),   # 40 min gap -> the slowest interval
            _artifact("art_d", _iso(50)),
        ])

        rows, coverage = _stage_publish_times(project, run_state)

        assert [r["stage"] for r in rows] == ["evaluate", "think", "plan", "deliver"], \
            "rows must be ordered by resolved timestamp, not array index"
        assert coverage == {"resolved": 4, "total": 4}
        by = {r["stage"]: r["elapsed_min"] for r in rows}
        assert by["evaluate"] is None, "first row has no predecessor"
        assert by["think"] == pytest.approx(5.0)
        assert by["plan"] == pytest.approx(40.0)
        assert by["deliver"] == pytest.approx(5.0)
        slowest = max((r for r in rows if r["elapsed_min"] is not None),
                     key=lambda r: r["elapsed_min"])
        assert slowest["stage"] == "plan"

    def test_out_of_order_publish_never_yields_negative_elapsed(self, tmp_path, monkeypatch):
        """A later stage carrying an EARLIER timestamp must not produce a negative.

        Modelled on real run_84cb2ea3: build published 02:38:24, review 02:17:05
        (review's artifact predates build's by 21 min after a build re-publish).
        34 real runs have this shape.

        TEETH NOTE: asserting only ``elapsed_min >= 0 or None`` would be VACUOUS —
        an explicit ``< 0`` clamp is unreachable once rows are timestamp-sorted, so
        such a test passes with the clamp deleted. What actually prevents the
        negative is the ORDERING, so this test pins the ordering-derived facts:
        the row order follows timestamps (review before build, opposite to the
        stages[] array) and the interval carries the positive magnitude. Deleting
        the sort flips both.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        project = "P"
        # Array order says build-then-review; timestamps say the opposite.
        run_state = {"stages": [_stage("build", "art_b"), _stage("review", "art_r")]}
        _write_manifest(tmp_path, project, [
            _artifact("art_b", _iso(38)),
            _artifact("art_r", _iso(17)),   # 21 min BEFORE build
        ])

        rows, _ = _stage_publish_times(project, run_state)

        assert [r["stage"] for r in rows] == ["review", "build"], \
            "rows must follow timestamps, not the stages[] array"
        by = {r["stage"]: r["elapsed_min"] for r in rows}
        assert by["review"] is None, "earliest row has no predecessor"
        assert by["build"] == pytest.approx(21.0), \
            "interval must be the positive magnitude, computed in timestamp order"
        assert all(r["elapsed_min"] is None or r["elapsed_min"] >= 0 for r in rows)

    def test_mixed_cause_coverage(self, tmp_path, monkeypatch):
        """Three DIFFERENT failure causes + one good stage -> resolved 1/4.

        A single-cause fixture would let a hardcoded len(stages) denominator or a
        stubbed resolved=True pass. Causes here: no artifact_id / id absent from
        the manifest / unparseable `created`.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        project = "P"
        run_state = {"stages": [
            _stage("evaluate", "art_ok"),
            _stage("think"),                       # cause 1: no artifact_id
            _stage("plan", "art_missing"),         # cause 2: id not in manifest
            _stage("build", "art_bad"),            # cause 3: unparseable created
        ]}
        _write_manifest(tmp_path, project, [
            _artifact("art_ok", _iso(0)),
            _artifact("art_bad", "not-a-timestamp"),
        ])

        rows, coverage = _stage_publish_times(project, run_state)

        assert coverage == {"resolved": 1, "total": 4}, f"got {coverage}"
        resolved = {r["stage"] for r in rows if r["resolved"]}
        assert resolved == {"evaluate"}
        unresolved = {r["stage"] for r in rows if not r["resolved"]}
        assert unresolved == {"think", "plan", "build"}

    def test_corrupt_and_missing_manifest_yield_empty_not_exception(self, tmp_path, monkeypatch):
        """Real malformed bytes (not a mocked raise) -> empty rows, no exception."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        project = "P"
        run_state = {"stages": [_stage("evaluate", "art_a")]}

        # (a) manifest absent entirely
        rows, coverage = _stage_publish_times(project, run_state)
        assert rows == [] or coverage["resolved"] == 0
        assert coverage["total"] == 1

        # (b) manifest present but genuinely invalid JSON
        art_dir = tmp_path / "Projects" / project / ".artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "manifest.json").write_text("{invalid json", encoding="utf-8")
        rows, coverage = _stage_publish_times(project, run_state)
        assert coverage["resolved"] == 0

    def test_goal_cycle_aggregate_is_disclosed(self, tmp_path, monkeypatch):
        """A goal_cycle row must surface its cycle count.

        goal_cycle collapses N BUILD+TEST iterations into ONE stage record
        (median publish-delta 22.0 min across 54 real runs), so a bare interval
        would read as one stage's duration. The real schema is inconsistent —
        `cycles`, `cycles_run` and `cycle_summary` all occur — so all are read.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        project = "P"
        for key in ("cycles", "cycles_run"):
            run_state = {"stages": [
                _stage("plan", "art_p"),
                _stage("goal_cycle", "art_g", **{key: 5}),
            ]}
            _write_manifest(tmp_path, project, [
                _artifact("art_p", _iso(0)), _artifact("art_g", _iso(22)),
            ])
            rows, _ = _stage_publish_times(project, run_state)
            gc = next(r for r in rows if r["stage"] == "goal_cycle")
            assert gc.get("cycles") == 5, f"cycle count not surfaced for key={key}"


class TestTwoAttributeArgs:
    """cmd_run_report must work with exactly SimpleNamespace(project, run_id).

    ui_actions.py direct-IMPORTS cmd_run_report and calls it in-process inside
    the frozen PyInstaller daemon with ONLY those two attributes (a subprocess
    would silently no-op there, since sys.executable is python-backend). Any bare
    `args.X` added to cmd_run_report raises AttributeError in production only.
    The neighbouring test module's own _Args carries 3 attributes, which would
    MASK this — hence an explicit 2-attribute guard.
    """

    def test_report_generates_with_two_attribute_args(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_twoattr"
        _write_run(tmp_path, project, run_id, {
            "id": run_id, "project": project, "profile": "full",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(30), "completed_at": _iso(30),
            "stages": [_stage("evaluate", "art_a")], "taste_decisions": [],
        })
        _write_manifest(tmp_path, project, [_artifact("art_a", _iso(5))])

        reg = ArtifactRegistry(_get_workspace())
        # EXACTLY two attributes — mirrors ui_actions.py's in-daemon call.
        cmd_run_report(SimpleNamespace(project=project, run_id=run_id), reg)

        report = (tmp_path / "Projects" / project / ".artifacts" / "runs"
                  / run_id / "REPORT.md")
        assert report.exists() and report.stat().st_size >= 500


# ── 4. completion regenerates the report (AC2 root fix) ──────────────────────

class TestCompletionRegeneratesReport:
    """`run-update --status completed` must itself refresh REPORT.md.

    The Duration:N/A defect is an ORDERING bug, not a missing field: the report
    is rendered while `completed_at` is still None, because INSTRUCTIONS ran
    run-report BEFORE --status completed. Fixing only the prose ordering leaves
    every future agent (and every already-shipped skill copy) able to reproduce
    it, so the completion branch regenerates mechanically.

    TEETH: the fixture's pre-existing REPORT.md carries `Duration:** N/A` and is
    marked `report_autogenerated` (the DELIVER-time early report, which is what
    satisfies the >=500-byte completion gate). A no-op implementation leaves that
    N/A in place, so the assertion fails. The hand-written case asserts the
    OPPOSITE direction, so a naive unconditional overwrite also fails.
    """

    def _seed(self, ws, project, run_id, *, autogenerated: bool):
        # `research` profile = the SHORTEST complete stage set (evaluate/think/
        # reflect, pipeline_profiles.py:19). The behaviour under test — the
        # completion branch refreshing REPORT.md — is profile-AGNOSTIC, so the
        # fixture uses the profile whose completion gates are cheapest to satisfy
        # honestly. Faking a `full` run's 8 stages would test the gate, not the fix.
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "r", "status": "in_progress",
            "created_at": _iso(0), "updated_at": _iso(10),
            "report_autogenerated": autogenerated,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=20),
                dict(
                    _stage("reflect", "art_c", token_cost=5),
                    lessons=[
                        "The completion branch must refresh REPORT.md because the "
                        "report is rendered before completed_at exists."
                    ],
                ),
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, project, [
            _artifact("art_a", _iso(5)),
            _artifact("art_b", _iso(40)),
            _artifact("art_c", _iso(55)),
        ])
        # The DELIVER-time early report: >=500 bytes so the completion gate
        # passes, and stamped N/A because completed_at did not exist yet.
        (run_dir / "REPORT.md").write_text(
            "# Pipeline Report\n\n**Duration:** N/A\n\n" + ("filler line\n" * 80),
            encoding="utf-8",
        )
        return run_dir

    def test_completed_branch_refreshes_stale_na_duration(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_update, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_regen"
        run_dir = self._seed(tmp_path, project, run_id, autogenerated=True)
        assert "N/A" in (run_dir / "REPORT.md").read_text(encoding="utf-8")

        cmd_run_update(
            SimpleNamespace(
                project=project, run_id=run_id, status="completed",
                stage_json=None, profile=None, requirement=None,
                ddd_checksums=None, taste_decision=None,
                files_touched=None, reason=None,
            ),
            ArtifactRegistry(_get_workspace()),
        )

        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "**Duration:** N/A" not in body, (
            "completion did not regenerate the report; the pre-completion N/A "
            f"duration survived:\n{body[:300]}"
        )
        assert "min" in body

    def test_hand_written_report_is_never_clobbered(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_update, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_handwritten"
        run_dir = self._seed(tmp_path, project, run_id, autogenerated=False)
        marker = "HUMAN-AUTHORED CONTENT DO NOT LOSE"
        (run_dir / "REPORT.md").write_text(
            f"# Report\n\n{marker}\n\n" + ("filler line\n" * 80), encoding="utf-8"
        )

        cmd_run_update(
            SimpleNamespace(
                project=project, run_id=run_id, status="completed",
                stage_json=None, profile=None, requirement=None,
                ddd_checksums=None, taste_decision=None,
                files_touched=None, reason=None,
            ),
            ArtifactRegistry(_get_workspace()),
        )

        assert marker in (run_dir / "REPORT.md").read_text(encoding="utf-8"), (
            "completion overwrote a hand-written report (data loss)"
        )

    def test_regeneration_failure_does_not_fail_completion(self, tmp_path, monkeypatch):
        """Report refresh is best-effort: the run must still complete.

        Uses a REAL failure source rather than a mocked raise, so the actual
        write path executes. The report is left a VALID >=500-byte file (so the
        pre-existing REPORT.md size gate still passes — a directory would be
        stat'd at 64 bytes and blocked there, testing the wrong thing) but is
        made read-only, so the regeneration's write_text raises PermissionError.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_update, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_regenfail"
        run_dir = self._seed(tmp_path, project, run_id, autogenerated=True)
        report = run_dir / "REPORT.md"
        report.chmod(0o444)  # write_text will raise PermissionError
        assert report.stat().st_size >= 500  # size gate must still pass

        cmd_run_update(
            SimpleNamespace(
                project=project, run_id=run_id, status="completed",
                stage_json=None, profile=None, requirement=None,
                ddd_checksums=None, taste_decision=None,
                files_touched=None, reason=None,
            ),
            ArtifactRegistry(_get_workspace()),
        )

        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert state["status"] == "completed"
        assert state.get("completed_at"), "completed_at must persist despite report failure"


# ── 5. the derived timing must actually be RENDERED (AC1/AC3 the read side) ───

class TestTimingSectionRendering:
    """`_stage_publish_times` output must reach REPORT.md.

    The pre-existing `stage_timing` mechanism failed exactly here: it computed
    `avg_minutes`/`median_minutes` and the renderer printed only Avg Tokens, so
    a correct calculation was dropped on the floor. Deriving without rendering
    repeats that defect, so this class asserts the RENDERED artifact, never the
    helper's return value.

    TEETH:
    * The fixture's slowest stage is LAST in `stages[]` but FIRST by timestamp,
      so an array-order renderer prints the wrong pairing.
    * One stage is deliberately unresolvable (no artifact_id), so a renderer
      that silently drops it — rather than disclosing partial coverage — fails
      the coverage assertion. A silent cap reads as "covered everything".
    """

    def _seed(self, ws, project, run_id):
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "measure per-stage time", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(90), "completed_at": _iso(90),
            "report_autogenerated": True,
            "stages": [
                # think is listed SECOND but published LAST (t=70) — an
                # array-order implementation would pair it against evaluate.
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_c", token_cost=30),
                # reflect has NO artifact_id -> unresolvable, must be disclosed.
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, project, [
            _artifact("art_a", _iso(5)),
            _artifact("art_c", _iso(70)),
        ])
        return run_dir

    def test_report_renders_per_stage_elapsed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_render"
        run_dir = self._seed(tmp_path, project, run_id)

        cmd_run_report(
            SimpleNamespace(project=project, run_id=run_id),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        # think published at t=70, previous resolved row is evaluate at t=5 -> 65.0
        assert "65.0" in body, (
            "per-stage elapsed was derived but never rendered (the exact defect "
            f"of the old stage_timing path):\n{body[:1500]}"
        )

    def test_partial_coverage_is_disclosed_never_silently_dropped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_render_cov"
        run_dir = self._seed(tmp_path, project, run_id)

        cmd_run_report(
            SimpleNamespace(project=project, run_id=run_id),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        # 2 of 3 stages resolvable; the report must SAY so rather than imply
        # full coverage by omission.
        assert "2/3" in body or "2 of 3" in body, (
            "partial timing coverage was not disclosed; a silent cap reads as "
            f"full coverage:\n{body[:1500]}"
        )


# ── 6. Gate-caught gaps: named bottleneck, cycles annotation, tz mixing ──────

class TestRenderedBottleneckAndCycles:
    """The RENDERED report must name the slowest stage and disclose cycles.

    Both were caught by the spec-compliance gate as "derived but never
    rendered" — the exact defect the old `stage_timing` path died of (it
    computed avg/median minutes and the renderer printed only Avg Tokens).
    Asserting the helper's return value does NOT catch it: a mutation deleting
    the cycles annotation from the renderer's f-string left the whole suite
    green. These tests read the REPORT.md bytes.
    """

    def _seed(self, ws, project, run_id, *, cycles: int | None = None):
        goal_rec = _stage("goal_cycle", "art_c", token_cost=30)
        if cycles is not None:
            goal_rec["cycles"] = cycles
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "goal",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(200), "completed_at": _iso(200),
            "report_autogenerated": True,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=10),
                goal_rec,
            ],
            "taste_decisions": [],
        })
        # goal_cycle is by far the slowest: 5 -> 20 (15 min) -> 180 (160 min).
        _write_manifest(ws, project, [
            _artifact("art_a", _iso(5)),
            _artifact("art_b", _iso(20)),
            _artifact("art_c", _iso(180)),
        ])
        return run_dir

    def _render(self, ws, project, run_id):
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace
        cmd_run_report(
            SimpleNamespace(project=project, run_id=run_id),
            ArtifactRegistry(_get_workspace()),
        )
        return (ws / "Projects" / project / ".artifacts" / "runs" / run_id
                / "REPORT.md").read_text(encoding="utf-8")

    def test_slowest_stage_is_named_not_merely_tabulated(self, tmp_path, monkeypatch):
        """AC1: a reader must not have to compare the column by eye."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        project, run_id = "P", "run_slow"
        self._seed(tmp_path, project, run_id, cycles=4)
        body = self._render(tmp_path, project, run_id)

        assert "goal_cycle" in body
        # The bottleneck must be stated in prose, naming the stage AND its value.
        import re
        m = re.search(r"Slowest stage:.*", body)
        assert m, f"the slowest stage is never named in prose:\n{body[:1200]}"
        line = m.group(0)
        assert "goal_cycle" in line, f"named the wrong stage: {line!r}"
        assert "160.0" in line, f"named the wrong duration: {line!r}"

    def test_slowest_line_uses_no_bold_field_token(self, tmp_path, monkeypatch):
        """It must not add a `**Xxx:**` token — that shape is parsed elsewhere.

        proactive_intelligence._extract_report_field scrapes REPORT.md for
        `**Field:**` headers; a new one competes with the real header fields.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        project, run_id = "P", "run_slow_tok"
        self._seed(tmp_path, project, run_id)
        body = self._render(tmp_path, project, run_id)
        assert "**Slowest" not in body, "introduced a competing **Field:** token"

    def test_goal_cycle_cycles_are_rendered_not_just_derived(self, tmp_path, monkeypatch):
        """AC4: the RENDERED row must disclose that the interval spans N cycles."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        project, run_id = "P", "run_cyc"
        self._seed(tmp_path, project, run_id, cycles=4)
        body = self._render(tmp_path, project, run_id)
        assert "4 cycles" in body, (
            f"cycle count derived but never rendered:\n{body[:1200]}"
        )

    def test_no_cycles_key_renders_no_annotation(self, tmp_path, monkeypatch):
        """The annotation must be conditional, not a hardcoded string."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        project, run_id = "P", "run_nocyc"
        self._seed(tmp_path, project, run_id, cycles=None)
        body = self._render(tmp_path, project, run_id)
        assert "cycles)" not in body, "rendered a cycles annotation with no cycles data"

    def test_mixed_naive_and_aware_timestamps_do_not_kill_the_report(
        self, tmp_path, monkeypatch
    ):
        """A tz-naive `created` beside a tz-aware one must not raise.

        The timestamp sort is deliberately OUTSIDE any try (it is the
        negative-elapsed guard), so an unnormalised naive value made
        `sorted` raise TypeError and NO report was written at all —
        breaking both the helper's "never raises" contract and AC6's
        "the rest of the report still generates".
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        project, run_id = "P", "run_tzmix"
        _write_run(tmp_path, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "r", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            "report_autogenerated": True,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=10),
            ],
            "taste_decisions": [],
        })
        _write_manifest(tmp_path, project, [
            _artifact("art_a", "2026-09-07T10:05:00"),          # NAIVE
            _artifact("art_b", _iso(20)),                        # AWARE
        ])
        body = self._render(tmp_path, project, run_id)  # must not raise
        assert "4.1 Per-Stage Elapsed" in body
        assert "15.0" in body, (
            f"the naive timestamp was not normalised to UTC:\n{body[:1200]}"
        )


# ── 7. Gate-2 caught: honest skip reporting + shape-hostile inputs ────────────
#
# These cover two defects an adversarial review found that the earlier tests
# could not see:
#   1. `cmd_run_update` reported `report_refreshed: True` even when the nested
#      `cmd_run_report` SKIPPED (hand-written report). The skip signal was a
#      stdout print, and the wrapper redirects stdout to a discarded buffer —
#      so "did not raise" was being read as "did refresh". A caller parsing the
#      envelope got a false positive about a write that never happened.
#   2. `_stage_publish_times` promised "never raises" but four on-disk shapes
#      raised TypeError, and because the call site is OUTSIDE a try, the whole
#      REPORT.md failed to generate — not just the timing block.

class TestSkipIsReportedHonestly:
    """A skipped regeneration must NOT be reported as a refresh."""

    def test_skip_reports_report_skipped_not_refreshed(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_update, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_skiphonest"
        run_dir = TestCompletionRegeneratesReport()._seed(
            tmp_path, project, run_id, autogenerated=False
        )
        (run_dir / "REPORT.md").write_text(
            "# Hand-written\n" + ("filler line\n" * 80), encoding="utf-8"
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cmd_run_update(
                SimpleNamespace(
                    project=project, run_id=run_id, status="completed",
                    stage_json=None, profile=None, requirement=None,
                    ddd_checksums=None, taste_decision=None,
                    files_touched=None, reason=None,
                ),
                ArtifactRegistry(_get_workspace()),
            )

        envelope = json.loads(buf.getvalue().strip().splitlines()[-1])
        assert envelope.get("report_refreshed") is not True, (
            "a SKIPPED regeneration was reported as report_refreshed=True — a "
            f"caller would believe a write happened that did not: {envelope}"
        )
        assert envelope.get("report_skipped") is True, (
            f"the skip must be surfaced explicitly, got: {envelope}"
        )


class TestShapeHostileInputsNeverRaise:
    """`_stage_publish_times` docstring promises "never raises" — enforce it.

    Each shape below raised TypeError before the isinstance guards, and the
    helper's call site in cmd_run_report is deliberately outside a try, so the
    raise killed the ENTIRE report.
    """

    @pytest.mark.parametrize("run_state,id_created,label", [
        ({"stages": 5}, {"a1": "2026-01-01T00:00:00+00:00"}, "stages not a list"),
        ({"stages": [{"stage": "s", "artifact_id": ["a1"]}]},
         {"a1": "2026-01-01T00:00:00+00:00"}, "artifact_id unhashable"),
        ({"stages": [{"stage": "s", "artifact_id": {"k": "v"}}]},
         {"a1": "2026-01-01T00:00:00+00:00"}, "artifact_id is a dict"),
    ])
    def test_hostile_shape_degrades_never_raises(
        self, tmp_path, monkeypatch, run_state, id_created, label
    ):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        rows, cov = _stage_publish_times("P", run_state, id_created=id_created)
        assert isinstance(rows, list), f"{label}: expected graceful degradation"
        assert cov.get("resolved") == 0, (
            f"{label}: a hostile shape must resolve nothing, got {cov}"
        )

    @pytest.mark.parametrize("artifacts,label", [
        (7, "artifacts not a list"),
        ([{"id": ["x"], "created": "2026-01-01T00:00:00+00:00"}], "entry id unhashable"),
    ])
    def test_hostile_manifest_degrades_never_raises(
        self, tmp_path, monkeypatch, artifacts, label
    ):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        art_dir = tmp_path / "Projects" / "P" / ".artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        (art_dir / "manifest.json").write_text(
            json.dumps({"artifacts": artifacts}), encoding="utf-8"
        )

        rows, cov = _stage_publish_times(
            "P", {"stages": [{"stage": "s", "artifact_id": "a1"}]}
        )
        assert cov.get("resolved") == 0, f"{label}: expected zero coverage, got {cov}"

    def test_pipe_in_stage_name_cannot_forge_table_rows(self, tmp_path, monkeypatch):
        """A stage name with `|` must not fabricate extra timing rows."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        project, run_id = "P", "run_pipe"
        evil = "build | x |\n| FORGED | 00:00:00 | 999 min "
        run_dir = _write_run(tmp_path, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "pipe injection", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(90), "completed_at": _iso(90),
            "report_autogenerated": True,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage(evil, "art_b", token_cost=20),
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
            ],
            "taste_decisions": [],
        })
        _write_manifest(tmp_path, project, [
            _artifact("art_a", _iso(5)),
            _artifact("art_b", _iso(40)),
        ])
        cmd_run_report(
            SimpleNamespace(project=project, run_id=run_id),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        # UNCONDITIONAL assertions. The earlier form was
        #   assert "FORGED" not in body or "\\|" in body
        # whose right disjunct is satisfied by ANY escaped pipe anywhere in the
        # report — including the correctly-escaped row — so the left disjunct
        # never bound. Proven by mutation: deleting only `.replace("\n", " ")`
        # (keeping the pipe escape) still passed all 27 tests. Assert the two
        # things that actually matter instead:
        #   1. no forged row may ever START a line (that needs a real newline)
        #   2. every pipe from the name is escaped, so no cell boundary is forged
        # STRUCTURAL invariant: the injected name occupies exactly ONE line.
        # Escaping `|` while leaving the newline still mangles the table into a
        # broken second row, and an exact-string check for "| FORGED |" cannot
        # see that (the escaped form reads "\| FORGED \|").
        # Assert the row is INTACT, not merely that a substring is absent.
        # Counting lines containing "FORGED" is NOT a discriminator: when the
        # newline splits the row, "FORGED" simply moves to the second fragment
        # and the count is unchanged (verified by mutation). The invariant that
        # actually breaks is CONTIGUITY — the timing row must begin with the
        # stage name and carry its timestamp + duration on that SAME line.
        timing_block = body[body.find("4.1"):]
        row = next(
            (l for l in timing_block.splitlines() if l.startswith("| build")),
            None,
        )
        assert row is not None, (
            "the injected stage's timing row does not start with its name — a "
            f"newline split the row:\n{timing_block[:600]}"
        )
        assert "10:" in row and "min" in row, (
            f"the row lost its timestamp/duration to a line split:\n{row!r}"
        )
        assert "| FORGED |" not in body, (
            "an unescaped pipe in a stage name forged a fabricated timing cell:\n"
            f"{timing_block[:600]}"
        )
        # And the sanitized name must still be PRESENT (escaped, not dropped) —
        # otherwise a fix that silently deletes the row would pass the above.
        assert "build" in body, "the stage row vanished instead of being escaped"


# ── 8. Gate-2 caught: the escape's second call site + honest denominators ─────
#
# Gate 2 (fresh-context adversarial) found three things the earlier tests could
# not see, all verified against source before fixing:
#   1. UnicodeDecodeError is a ValueError, NOT an OSError — one non-UTF-8 byte in
#      manifest.json escaped the guard and killed the ENTIRE report (exit 1, no
#      REPORT.md), breaking the "never raises" contract.
#   2. The `|`/newline escape protected the TABLE row but the "Slowest stage:"
#      PROSE line interpolated the raw name one line below the comment
#      explaining why that is dangerous — so a name could still forge a row.
#   3. With exactly one measured interval the share is unconditionally "100% of
#      measured time", which READS as a bottleneck finding while being pure
#      arithmetic (23 real runs are that shape).

class TestNeverRaisesOnUndecodableManifest:
    """A non-UTF-8 byte in manifest.json must degrade, not kill the report."""

    def test_undecodable_manifest_degrades_to_zero_coverage(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _stage_publish_times

        art_dir = tmp_path / "Projects" / "P" / ".artifacts"
        art_dir.mkdir(parents=True, exist_ok=True)
        # Valid JSON structure, one undecodable byte inside a string value.
        (art_dir / "manifest.json").write_bytes(
            b'{"artifacts":[{"id":"a1","created":"2026-01-01T00:00:00+00:00","n":"'
            + b"\xff" + b'"}]}'
        )

        rows, cov = _stage_publish_times(
            "P", {"stages": [{"stage": "s", "artifact_id": "a1"}]}
        )
        assert cov.get("resolved") == 0, (
            "an undecodable manifest must degrade to zero coverage, not raise — "
            "the call site is outside a try, so a raise kills the whole report"
        )
        assert len(rows) == 1, "the stage row must still be listed as unresolved"


class TestSlowestLineEscapesAndHonestShare:
    """The prose line shares the table's escape and never prints a bogus 100%."""

    def _seed(self, ws, project, run_id, stage_names_and_times):
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "prose escape", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(90), "completed_at": _iso(90),
            "report_autogenerated": True,
            "stages": [
                _stage(n, f"art_{i}", token_cost=10)
                for i, (n, _) in enumerate(stage_names_and_times)
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, project, [
            _artifact(f"art_{i}", t)
            for i, (_, t) in enumerate(stage_names_and_times)
        ])
        return run_dir

    def test_slowest_prose_line_escapes_pipe_and_newline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        evil = "goal_cycle | y |\n| PROSEFORGED | 00:00:00 | 999 min "
        run_dir = self._seed(tmp_path, "P", "run_prose", [
            ("evaluate", _iso(5)),
            (evil, _iso(60)),        # biggest gap -> becomes the SLOWEST stage
            ("reflect", _iso(65)),
        ])
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_prose"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        assert "Slowest stage:" in body, "the slowest-stage line must render"
        # Assert the STRUCTURAL invariant, not one spelling of the payload.
        # Escaping `|` alone is NOT enough: with the newline intact the name
        # still splits across lines and mangles the table (verified — the
        # escaped-but-multiline form rendered a broken second row). So the real
        # invariant is that the injected name occupies exactly ONE line, in BOTH
        # the table row and the prose line. Matching `"| PROSEFORGED |"` only
        # catches the unescaped variant and silently passes the newline half.
        # CONTIGUITY, not substring-absence (a line count is not a
        # discriminator: a newline just moves the payload to the next fragment).
        # The prose line must carry the name AND its duration on ONE line.
        prose = next(
            (l for l in body.splitlines() if l.startswith("Slowest stage:")),
            None,
        )
        assert prose is not None, "the Slowest stage line must render"
        assert "min" in prose, (
            "the Slowest stage line was split by a newline in the stage name, so "
            f"its duration fell onto another line:\n{prose!r}"
        )
        assert "| PROSEFORGED |" not in body, (
            "an unescaped pipe reached a render site and forged a table cell"
        )

    def test_single_interval_suppresses_meaningless_100_percent(self, tmp_path, monkeypatch):
        """One measured gap => the share is trivially 100%; it must be omitted."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        # Exactly TWO resolved artifacts => exactly ONE interval.
        run_dir = self._seed(tmp_path, "P", "run_onegap", [
            ("evaluate", _iso(5)),
            ("reflect", _iso(45)),
        ])
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_onegap"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        assert "Slowest stage:" in body, "the slowest stage must still be named"
        assert "100%" not in body, (
            "with a single measured interval the share is unconditionally 100% — "
            f"a number that reads as a finding but is pure arithmetic:\n"
            f"{body[body.find('4.1'):][:500]}"
        )

    def test_multi_interval_share_names_its_denominator(self, tmp_path, monkeypatch):
        """A real share must state WHAT it is a share of, not 'measured time'."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed(tmp_path, "P", "run_multigap", [
            ("evaluate", _iso(5)),
            ("think", _iso(15)),
            ("reflect", _iso(85)),   # dominant gap
        ])
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_multigap"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        # Pin the EXACT sentence, value included. Asserting only the phrase
        # "inter-stage gaps" lets ANY share value through — right or wrong —
        # which is precisely the "plausible-looking wrong number is worse than
        # no number" defect the production comment names. Verified vacuous:
        # mutating *100 -> *1000 and the count -> 1 both stayed GREEN.
        # gaps here are think-evaluate = 10.0 and reflect-think = 70.0;
        # sum 80.0, slowest 70.0 -> 88%.
        assert "(88% of the 2 measured inter-stage gaps)" in body, (
            "the share must render the correct value AND name its denominator "
            "(it excludes created_at -> first publish, so it is NOT 'measured "
            f"time'):\n{body[body.find('4.1'):][:500]}"
        )
        assert "70.0 min" in body, f"the dominant gap must render:\n{body[body.find('4.1'):][:400]}"

    def test_coverage_discloses_the_unnumbered_first_row(self, tmp_path, monkeypatch):
        """`resolved` always exceeds the duration count by 1 — say so."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed(tmp_path, "P", "run_cov1", [
            ("evaluate", _iso(5)),
            ("think", _iso(15)),
            ("reflect", _iso(40)),
        ])
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_cov1"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        # Pin the COUNTS, not just the prose. Asserting the phrase alone left the
        # numbers unprotected — verified vacuous: mutating the count to 999 and
        # to 0 both stayed GREEN, while the docstring claimed to check "BOTH the
        # resolved count and the number of rows that carry a duration".
        # 3 stages all resolve; the earliest has no predecessor -> 2 durations.
        assert "3/3 stages resolved, 2 with an elapsed number." in body, (
            "coverage must report BOTH the resolved count and the number of rows "
            f"that actually carry a duration:\n{body[body.find('Timing coverage'):][:400]}"
        )
        assert "no predecessor" in body, (
            "the disclosure must explain WHY resolved > durations, else the gap "
            "reads as a bug"
        )


# ── 9. Adversarial pass 3: the guards whose WRITERS were never exercised ──────
#
# A third adversarial pass ran 48 mutations against this file and proved 6 tests
# vacuous. The common shape: the FIXTURE pre-set the condition, so the production
# code that establishes it was never reached. Most load-bearing was the
# `report_autogenerated` stamp — the SOLE writer of the flag that protects a
# hand-written REPORT.md from being overwritten. Deleting the stamp block
# entirely kept all 32 tests green, i.e. the data-loss guard's only writer was
# unprotected. These tests exercise the WRITERS, not just the pre-seeded state.

class TestGuardWritersAreExercised:

    def _seed_unflagged(self, ws, project, run_id):
        """A run with NO report_autogenerated flag — the stamp must create it."""
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "stamp writer", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            # deliberately NO "report_autogenerated"
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=10),
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, project, [
            _artifact("art_a", _iso(5)), _artifact("art_b", _iso(30)),
        ])
        return run_dir

    def test_first_generation_stamps_report_autogenerated(self, tmp_path, monkeypatch):
        """The stamp is the ONLY writer of the hand-written-report guard flag."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed_unflagged(tmp_path, "P", "run_stamp")
        before = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert "report_autogenerated" not in before, "fixture must start unflagged"

        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_stamp"),
            ArtifactRegistry(_get_workspace()),
        )

        after = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert after.get("report_autogenerated") is True, (
            "generating a report did not stamp report_autogenerated — that flag is "
            "the SOLE authority telling a later no-force call the report is "
            "auto-owned. Without it, every generated report looks hand-written and "
            f"can never be refreshed with late lessons: {after.keys()}"
        )
        # The stamp must not damage the rest of the state.
        assert after.get("id") == "run_stamp" and len(after.get("stages", [])) == 3

    def test_stamp_makes_the_next_no_force_call_regenerate(self, tmp_path, monkeypatch):
        """End-to-end consequence: stamp -> a second call refreshes, not skips."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed_unflagged(tmp_path, "P", "run_stamp2")
        reg = ArtifactRegistry(_get_workspace())
        cmd_run_report(SimpleNamespace(project="P", run_id="run_stamp2"), reg)
        # Second call, no --force: must REGENERATE (outcome "written"), not skip.
        outcome = cmd_run_report(SimpleNamespace(project="P", run_id="run_stamp2"), reg)
        assert outcome == "written", (
            "a second no-force call skipped, so late REFLECT lessons could never "
            f"reach the report (the stamp did not take effect): {outcome!r}"
        )

    def test_completed_run_duration_is_a_number_not_in_progress(self, tmp_path, monkeypatch):
        """The `else` half of the in-flight ternary was unprotected.

        Only the in-progress branch was asserted anywhere, so a mutation making
        EVERY run render "(in progress)" stayed green — a completed report could
        claim it was still running forever.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed_unflagged(tmp_path, "P", "run_donedur")
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_donedur"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")

        assert "60.0 min" in body, (
            "a COMPLETED run must render its real duration (created_at -> "
            f"completed_at = 60 min):\n{body[:600]}"
        )
        assert "in progress" not in body, (
            f"a completed run claimed to still be in progress:\n{body[:600]}"
        )

    def test_unresolved_rows_follow_canonical_stage_order(self, tmp_path, monkeypatch):
        """Two unresolved stages, appended out of order, must render canonically.

        Every other fixture has <=1 unresolved stage, so the _canon sort was
        never exercised (deleting it stayed green).
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = _write_run(tmp_path, "P", "run_canon", {
            "id": "run_canon", "project": "P", "profile": "research",
            "requirement": "canon order", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(60), "completed_at": _iso(60),
            "report_autogenerated": True,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                # BOTH unresolved (no artifact_id), appended REVERSED vs canonical
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
                _stage("plan", None, token_cost=5),
            ],
            "taste_decisions": [],
        })
        _write_manifest(tmp_path, "P", [_artifact("art_a", _iso(5))])
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_canon"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        block = body[body.find("4.1"):]
        order = [
            l.split("|")[1].strip()
            for l in block.splitlines()
            if l.startswith("| ") and not l.startswith("| Stage")
        ]
        assert order.index("plan") < order.index("reflect"), (
            "unresolved rows must render in CANONICAL stage order, not the order "
            f"they happen to sit in stages[]: {order}"
        )

    def test_absurdly_long_stage_name_is_truncated(self, tmp_path, monkeypatch):
        """The [:80] bound was unprotected — a 500-char name must not blow the row."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import _safe_stage_name

        rendered = _safe_stage_name("z" * 500)
        assert len(rendered) <= 80, (
            f"an unbounded stage name reached the report ({len(rendered)} chars) — "
            "one on-disk value can then dominate the whole table"
        )


# ── 10. Meta-review: the derived gaps must FEED the aggregate, not just render ─
#
# The meta-review found the sharper half of the AC8 defect: the analytics minutes
# column reads `stage_timing`, which only exists when an agent remembers to call
# `run-observe stage_start`/`stage_end`. Measured on the real corpus: 34 of 659
# METRICS.json carry it, and per-stage wall_minutes counts are evaluate=8,
# think=1, plan=1 — so with INSUFFICIENT_N=3 the new column would render a number
# for exactly ONE stage, forever, while the per-run derivation computed the same
# durations and threw them away into markdown.
#
# So completion PERSISTS the derived gaps (`derived_stage_gaps`) and the
# aggregator prefers them: one channel, fed by a code path every run passes,
# retroactive over every run that has artifacts.

class TestDerivedGapsArePersisted:

    def _seed(self, ws, project, run_id):
        run_dir = _write_run(ws, project, run_id, {
            "id": run_id, "project": project, "profile": "research",
            "requirement": "persist gaps", "status": "completed",
            "created_at": _iso(0), "updated_at": _iso(90), "completed_at": _iso(90),
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=20),
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, project, [
            _artifact("art_a", _iso(5)),
            _artifact("art_b", _iso(35)),   # 30.0 min gap
        ])
        return run_dir

    def test_completion_persists_derived_gaps(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import (
            _try_generate_metrics, ArtifactRegistry, _get_workspace,
        )

        run_dir = self._seed(tmp_path, "P", "run_persist")
        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        _try_generate_metrics("P", "run_persist", run_state,
                              ArtifactRegistry(_get_workspace()))

        m = json.loads((run_dir / "METRICS.json").read_text(encoding="utf-8"))
        gaps = m.get("derived_stage_gaps")
        assert gaps, (
            "the per-stage gaps were derived and then thrown away into markdown; "
            "nothing persisted them, so the cross-run aggregate stays empty "
            f"forever: {list(m.keys())}"
        )
        assert gaps["think"]["wall_minutes"] == 30.0, (
            f"the derived gap must persist its real value: {gaps}"
        )
        # The unresolvable stage must NOT be invented with a 0.
        assert "reflect" not in gaps, (
            f"a stage with no timestamp must be absent, never persisted as 0: {gaps}"
        )

    def test_persisted_gaps_do_not_clobber_observe_telemetry(self, tmp_path, monkeypatch):
        """Both channels coexist — persisting must not undo the merge fix."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import (
            _try_generate_metrics, ArtifactRegistry, _get_workspace,
        )

        run_dir = self._seed(tmp_path, "P", "run_persist2")
        (run_dir / "METRICS.json").write_text(json.dumps({
            "stage_timing": {"build": {"wall_minutes": 15.0}},
            "think_depth": {"alternatives_count": 3},
        }), encoding="utf-8")

        run_state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        _try_generate_metrics("P", "run_persist2", run_state,
                              ArtifactRegistry(_get_workspace()))

        m = json.loads((run_dir / "METRICS.json").read_text(encoding="utf-8"))
        assert m["stage_timing"]["build"]["wall_minutes"] == 15.0, "observe data lost"
        assert m["think_depth"]["alternatives_count"] == 3, "observe data lost"
        assert m.get("derived_stage_gaps", {}).get("think", {}).get("wall_minutes") == 30.0


class TestAggregatorPrefersDerivedGaps:
    """analyze_stage_efficiency must read the fed channel, not only the unfed one."""

    def test_derived_gaps_feed_the_minutes_columns(self):
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "scripts"))
        import pipeline_analytics as pa

        # THREE runs, so INSUFFICIENT_N=3 is satisfied and a number must render.
        corpus = [
            {"run_id": f"R{i}", "project": "P", "profile": "research",
             "status": "completed", "stage_tokens": {"think": 1000},
             "derived_stage_gaps": {"think": {"wall_minutes": 30.0}}}
            for i in range(3)
        ]
        eff = pa.analyze_stage_efficiency(corpus)
        assert eff["stages"]["think"].get("avg_minutes") == 30.0, (
            "the aggregator ignored derived_stage_gaps, so the minutes columns stay "
            f"empty even though every run now carries the data: {eff['stages']['think']}"
        )
        assert eff["stages"]["think"]["duration_sample_count"] == 3

    def test_observe_stage_timing_still_wins_when_present(self):
        """observe's explicit measurement is more precise — it must not be lost."""
        import sys
        from pathlib import Path as _P
        sys.path.insert(0, str(_P(__file__).resolve().parent.parent / "scripts"))
        import pipeline_analytics as pa

        corpus = [
            {"run_id": f"R{i}", "project": "P", "profile": "research",
             "status": "completed", "stage_tokens": {"build": 1000},
             "stage_timing": {"build": {"wall_minutes": 12.0}},
             "derived_stage_gaps": {"build": {"wall_minutes": 99.0}}}
            for i in range(3)
        ]
        eff = pa.analyze_stage_efficiency(corpus)
        assert eff["stages"]["build"]["avg_minutes"] == 12.0, (
            "the derived gap (which includes inter-stage time) overwrote observe's "
            f"precise in-stage measurement: {eff['stages']['build']}"
        )


class TestDeadRunsAreNotLabelledInProgress:
    """`(in progress)` must key off STATUS, not off a missing timestamp.

    `not completed_at` is true for 218 real runs that are anything but live:
    98 abandoned, 54 cancelled, 4 paused, and 55 whose status IS 'completed' but
    which never got the timestamp written. Labelling those "in progress" is a
    false liveness claim about a dead run.

    The fixture deliberately OMITS completed_at — a fixture that sets it never
    reaches the status branch at all (verified: the mutation reverting this fix
    stayed green against such a fixture).
    """

    def _seed(self, ws, run_id, status):
        run_dir = _write_run(ws, "P", run_id, {
            "id": run_id, "project": "P", "profile": "research",
            "requirement": "liveness label", "status": status,
            "created_at": _iso(0), "updated_at": _iso(45),
            # NO completed_at — the exact real-corpus shape
            "report_autogenerated": True,
            "stages": [
                _stage("evaluate", "art_a", token_cost=10),
                _stage("think", "art_b", token_cost=10),
                dict(_stage("reflect", None, token_cost=5), lessons=["x" * 40]),
            ],
            "taste_decisions": [],
        })
        _write_manifest(ws, "P", [
            _artifact("art_a", _iso(5)), _artifact("art_b", _iso(30)),
        ])
        return run_dir

    # `paused` is intentionally ABSENT — see test_paused_run_stays_in_progress.
    @pytest.mark.parametrize("status", ["abandoned", "cancelled", "failed",
                                        "completed", "superseded", "rejected",
                                        "aborted"])
    def test_terminal_status_never_claims_in_progress(self, tmp_path, monkeypatch, status):
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed(tmp_path, f"run_dead_{status}", status)
        cmd_run_report(
            SimpleNamespace(project="P", run_id=f"run_dead_{status}"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "in progress" not in body, (
            f"a run with status={status!r} was labelled in-progress purely because "
            f"completed_at was absent — a false liveness claim:\n{body[:400]}"
        )
        assert "45.0 min" in body, (
            f"the duration must still render via the updated_at fallback:\n{body[:400]}"
        )

    def test_paused_run_stays_in_progress(self, tmp_path, monkeypatch):
        """`paused` is a REVIVAL status, not a terminal one — it must stay live.

        `_REVIVAL_STATUSES` lists paused alongside running, and
        `is_terminal_run`'s docstring is explicit that treating a paused
        mid-pipeline run as terminal silently writes off a genuinely resumable
        run. A paused run is unfinished, so "in progress" is the TRUE label.
        This test exists to stop a future "complete the terminal set" edit from
        quietly reclassifying it.
        """
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed(tmp_path, "run_paused", "paused")
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_paused"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "in progress" in body, (
            "a PAUSED run is resumable, not finished — stripping its in-progress "
            f"marker writes off work that can still continue:\n{body[:400]}"
        )

    def test_genuinely_running_run_still_says_in_progress(self, tmp_path, monkeypatch):
        """The in-flight label must survive for a run that IS live."""
        monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
        from scripts.artifact_cli import cmd_run_report, ArtifactRegistry, _get_workspace

        run_dir = self._seed(tmp_path, "run_live", "running")
        cmd_run_report(
            SimpleNamespace(project="P", run_id="run_live"),
            ArtifactRegistry(_get_workspace()),
        )
        body = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        assert "in progress" in body, (
            f"a genuinely running run lost its in-progress marker:\n{body[:400]}"
        )
