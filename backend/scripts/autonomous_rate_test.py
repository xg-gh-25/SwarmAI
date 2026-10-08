"""Tests for autonomous-rate observability (AIDLC design §6).

Covers the four pure/mechanism pieces of the autonomous-rate feature:
  - _classify_intervention_kind: checkpoint reason -> intervention kind
    (reuses artifact_cli._CHECKPOINT_TRUE_TRIGGERS; no new taxonomy)
  - _infer_codefix_from_commits: committed files NOT in files_touched => codefix
    (files_touched=None => NO inference — honest lower-bound, design §4.2)
  - compute_autonomous_rate: delivered(=completed & >=1 commit) denominator;
    autonomous = 0 judgment-class + 0 codefix interventions; per-kind breakdown
  - _append_intervention: lazy-init of run_state['interventions']

Methodology: pure functions tested directly (no mocking — in-process). Rate
computation uses tmp-filesystem run.json fixtures. These assert BEHAVIOR
(classification correctness, denominator/numerator math), not internal shape.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import artifact_cli as ac  # noqa: E402
import autonomous_rate as ar  # noqa: E402


# ───────────────────────── _classify_intervention_kind ──────────────────────
# The mapping (design §4.2, Gate-1-corrected to REAL trigger tokens only):
#   judgment                         -> "judgment"  (human judgment decision)
#   l2 / block / gate_spawn_blocked  -> "unblock"    (human cleared a block)
#   external                         -> "approval"   (external approval gate)
#   budget/exhaust/retry/crash/stuck/error/escalat/mutation/re-baseline/git revert
#                                    -> None         (systemic, NOT a human intervention)
class TestClassifyInterventionKind:
    def test_judgment_reason_maps_to_judgment(self):
        assert ac._classify_intervention_kind("L2 judgment decision on public API") == "judgment"

    def test_l2_block_maps_to_unblock(self):
        assert ac._classify_intervention_kind("L2 BLOCK pending user decision") in ("unblock", "judgment")

    def test_plain_block_maps_to_unblock(self):
        # whole-word 'block' trigger, no 'judgment'/'l2' -> unblock.
        # Mirrors _checkpoint_reason_has_true_trigger's whole-word semantics:
        # 'blocked'/'roadblock' deliberately do NOT trigger (and so classify to
        # None) — the classifier must stay consistent with the pause vocabulary.
        assert ac._classify_intervention_kind("block: design question for a human") == "unblock"

    def test_inflected_blocked_is_none_consistent_with_trigger(self):
        # 'blocked' is NOT a whole-word 'block' match → None, EXACTLY as the
        # checkpoint guard treats it (it would not trigger a pause either).
        assert ac._checkpoint_reason_has_true_trigger("blocked on X") is False
        assert ac._classify_intervention_kind("blocked on X") is None

    def test_gate_spawn_blocked_maps_to_unblock(self):
        assert ac._classify_intervention_kind("gate_spawn_blocked after retry") == "unblock"

    def test_external_maps_to_approval(self):
        assert ac._classify_intervention_kind("external approval needed") == "approval"

    def test_budget_is_systemic_none(self):
        assert ac._classify_intervention_kind("budget exhausted, checkpointing") is None

    def test_crash_is_systemic_none(self):
        assert ac._classify_intervention_kind("session_crash_auto_detected") is None

    def test_retry_is_systemic_none(self):
        assert ac._classify_intervention_kind("retry exhausted") is None

    def test_no_trigger_returns_none(self):
        assert ac._classify_intervention_kind("some unrecognized reason") is None

    def test_empty_reason_returns_none(self):
        assert ac._classify_intervention_kind("") is None
        assert ac._classify_intervention_kind(None) is None

    def test_judgment_precedence_over_block(self):
        # A reason with BOTH judgment and block words -> judgment wins (most specific/human)
        assert ac._classify_intervention_kind("judgment call: this block needs a human") == "judgment"


# ───────────────────────── _append_intervention (lazy-init) ─────────────────
class TestAppendIntervention:
    def test_lazy_init_creates_list(self):
        rs = {"id": "run_x"}  # no interventions key
        ac._append_intervention(rs, stage="build", kind="codefix", avoidable=True, note="n")
        assert rs["interventions"] == [
            {"stage": "build", "kind": "codefix", "avoidable": True, "note": "n"}
        ]

    def test_appends_to_existing(self):
        rs = {"interventions": [{"stage": "a", "kind": "judgment", "avoidable": False, "note": ""}]}
        ac._append_intervention(rs, stage="deliver", kind="codefix", avoidable=True, note="x")
        assert len(rs["interventions"]) == 2
        assert rs["interventions"][1]["kind"] == "codefix"

    def test_note_truncated(self):
        rs = {}
        long_note = "z" * 500
        ac._append_intervention(rs, stage="build", kind="unblock", avoidable=False, note=long_note)
        assert len(rs["interventions"][0]["note"]) <= 200


# ───────────────────────── _infer_codefix_from_commits ──────────────────────
# design §4.2: committed files NOT in files_touched => human codefix (avoidable).
# files_touched=None (legacy/unrecorded) => NO inference (honest lower-bound).
class TestInferCodefixFromCommits:
    def test_committed_file_absent_from_files_touched_is_codefix(self):
        commits = [{"repo": "/r", "sha": "abc", "files": ["a.py", "b.py"]}]
        files_touched = ["a.py"]
        extra = ac._infer_codefix_from_commits(commits, files_touched)
        assert extra == ["b.py"]

    def test_all_committed_tracked_no_codefix(self):
        commits = [{"repo": "/r", "sha": "abc", "files": ["a.py"]}]
        assert ac._infer_codefix_from_commits(commits, ["a.py", "b.py"]) == []

    def test_files_touched_none_no_inference(self):
        # The honest lower-bound: cannot determine -> infer nothing (never a FALSE codefix)
        commits = [{"repo": "/r", "sha": "abc", "files": ["a.py", "b.py"]}]
        assert ac._infer_codefix_from_commits(commits, None) == []

    def test_empty_commits_no_codefix(self):
        assert ac._infer_codefix_from_commits([], ["a.py"]) == []

    def test_files_touched_empty_list_means_everything_untracked(self):
        # [] is DIFFERENT from None: [] = "recorded, zero files" -> everything committed is extra.
        # (cmd_run_commit refuses empty files_touched upstream, but the pure fn must be well-defined.)
        commits = [{"repo": "/r", "sha": "abc", "files": ["a.py"]}]
        assert ac._infer_codefix_from_commits(commits, []) == ["a.py"]

    def test_absolute_files_touched_normalized_no_false_codefix(self):
        # Gate-2 F1 regression: files_touched is ABSOLUTE, committed[].files is
        # repo-relative. WITHOUT a normalizer the raw compare marks the file a
        # (false) codefix. WITH the injected normalizer (maps abs -> repo-rel),
        # the same file is recognized as tracked -> no codefix.
        commits = [{"repo": "/repo", "sha": "abc", "files": ["backend/foo.py"]}]
        files_touched = ["/repo/backend/foo.py"]  # absolute, as BUILD records it

        # raw (no normalizer) WOULD false-positive — proves the bug exists w/o fix:
        assert ac._infer_codefix_from_commits(commits, files_touched) == ["backend/foo.py"]

        # with a normalizer that maps the absolute path to its repo-relative form,
        # the file is tracked -> NO false codefix:
        def fake_norm(repo, files):
            # emulate `git ls-files --full-name`: strip the repo prefix
            return {f[len(repo) + 1:] if f.startswith(repo + "/") else f for f in files}
        assert ac._infer_codefix_from_commits(
            commits, files_touched, normalizer=fake_norm) == []

    def test_normalizer_only_affects_matching_repo(self):
        # A genuinely-untracked file still surfaces as codefix even with normalizer.
        commits = [{"repo": "/repo", "sha": "abc", "files": ["backend/foo.py", "backend/human.py"]}]
        files_touched = ["/repo/backend/foo.py"]  # human.py NOT recorded

        def fake_norm(repo, files):
            return {f[len(repo) + 1:] if f.startswith(repo + "/") else f for f in files}
        assert ac._infer_codefix_from_commits(
            commits, files_touched, normalizer=fake_norm) == ["backend/human.py"]


# ───────────────────────── compute_autonomous_rate ──────────────────────────
def _write_run(runs_root: Path, run_id: str, *, status: str, commits: int,
               interventions=None):
    d = runs_root / run_id
    d.mkdir(parents=True, exist_ok=True)
    run = {
        "id": run_id,
        "status": status,
        "commits": [{"repo": "/r", "sha": f"{run_id}_{i}", "files": ["x.py"]}
                    for i in range(commits)],
    }
    if interventions is not None:
        run["interventions"] = interventions
    (d / "run.json").write_text(json.dumps(run), encoding="utf-8")


class TestComputeAutonomousRate:
    def test_delivered_denominator_excludes_abandoned_and_zero_commit(self, tmp_path):
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1, interventions=[])        # delivered, autonomous
        _write_run(runs, "run_b", status="abandoned", commits=0)                           # excluded (not delivered)
        _write_run(runs, "run_c", status="completed", commits=0)                           # excluded (no commit)
        _write_run(runs, "run_d", status="running", commits=1)                             # excluded (not completed)
        res = ar.compute_autonomous_rate(runs)
        assert res["delivered"] == 1
        assert res["autonomous"] == 1
        assert res["rate"] == 1.0

    def test_codefix_run_is_not_autonomous(self, tmp_path):
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1, interventions=[])
        _write_run(runs, "run_b", status="completed", commits=1,
                   interventions=[{"stage": "build", "kind": "codefix", "avoidable": True, "note": ""}])
        res = ar.compute_autonomous_rate(runs)
        assert res["delivered"] == 2
        assert res["autonomous"] == 1
        assert res["rate"] == 0.5

    def test_judgment_run_is_not_autonomous(self, tmp_path):
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1,
                   interventions=[{"stage": "plan", "kind": "judgment", "avoidable": False, "note": ""}])
        res = ar.compute_autonomous_rate(runs)
        assert res["delivered"] == 1
        assert res["autonomous"] == 0
        assert res["rate"] == 0.0

    def test_unblock_approval_do_not_disqualify(self, tmp_path):
        # Only judgment-class + codefix disqualify (design §4). unblock/approval
        # are human touches but per the AIDLC definition clarification, only
        # JUDGMENT-class interventions (and codefix) subtract autonomy.
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1,
                   interventions=[{"stage": "build", "kind": "unblock", "avoidable": False, "note": ""}])
        res = ar.compute_autonomous_rate(runs)
        assert res["autonomous"] == 1

    def test_by_kind_breakdown(self, tmp_path):
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1,
                   interventions=[{"stage": "build", "kind": "codefix", "avoidable": True, "note": ""},
                                  {"stage": "plan", "kind": "judgment", "avoidable": False, "note": ""}])
        res = ar.compute_autonomous_rate(runs)
        assert res["by_kind"]["codefix"] == 1
        assert res["by_kind"]["judgment"] == 1
        assert res["by_avoidable"]["avoidable"] == 1
        assert res["by_avoidable"]["unavoidable"] == 1

    def test_no_interventions_field_treated_as_empty(self, tmp_path):
        # Legacy runs have no interventions field -> treated as autonomous
        # (BUT measurement_floor_note must warn these are lower-bound).
        runs = tmp_path / "runs"
        _write_run(runs, "run_a", status="completed", commits=1, interventions=None)
        res = ar.compute_autonomous_rate(runs)
        assert res["autonomous"] == 1
        assert "measurement_floor" in res

    def test_empty_runs_dir_rate_is_none_not_crash(self, tmp_path):
        runs = tmp_path / "runs"
        runs.mkdir()
        res = ar.compute_autonomous_rate(runs)
        assert res["delivered"] == 0
        assert res["rate"] is None  # 0/0 is undefined, not 0.0
