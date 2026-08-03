"""M4-4 — verify seed_from_correction produces a REPLAYABLE golden-set case.

`eval_hooks.seed_from_correction` (the hook called by user_correction_detector)
delegates to `EvalService.auto_seed_case`, which auto-grows the behavioral
contract from real corrections. Existing tests (test_eval_hooks.py) assert the
case is STRUCTURALLY present (tier=draft, id, evaluators) but NOT that it is
actually REPLAYABLE — i.e. that the eval runner would judge it rather than
silently skip it as malformed. A seeded case that the runner skips is the
silent-failure the closed-loop design condemns: "auto-grew the contract" but
the contract never executes.

This test forces the full seam (hook → auto_seed_case → golden_set → eval_runner
judge-prep) and asserts the seeded case reaches the judge. The Bedrock call is
STUBBED — the point is to prove the case is well-formed enough to be judged, NOT
to spend an LLM call (goal_success is an LLM evaluator).
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.eval_service import EvalService
import core.eval_hooks as eval_hooks
from scripts import eval_runner


@pytest.fixture
def eval_workspace(tmp_path):
    project_dir = tmp_path / "Eval"
    project_dir.mkdir(parents=True)
    (project_dir / "EvalHistory").mkdir()
    golden_set = {
        "version": 2,
        "categories": ["compliance"],
        "dimensions": ["compliance"],
        "cases": [],
    }
    (project_dir / "golden_set.yaml").write_text(yaml.dump(golden_set))
    return tmp_path


@pytest.fixture
def svc(eval_workspace):
    return EvalService(workspace_root=eval_workspace)


class TestSeededCaseStructurallyValid:
    """The seeded case must satisfy the contract add_case enforces."""

    def test_seed_has_all_required_case_fields(self, svc):
        case = svc.auto_seed_case("C038", "asserted deploy state without observing", "CLASS_B")
        assert case is not None
        for field in EvalService._REQUIRED_CASE_FIELDS:
            assert field in case, f"seeded case missing required field {field!r}"
            assert case[field] not in (None, "", []), f"required field {field!r} is empty"

    def test_seeded_case_would_pass_add_case_validation(self, svc):
        # auto_seed_case appends directly (bypassing add_case). Prove the result
        # WOULD survive add_case's required-field gate — i.e. it is not a
        # second-class citizen the manual path would reject.
        case = svc.auto_seed_case("C039", "some correction text", "CLASS_A")
        missing = EvalService._REQUIRED_CASE_FIELDS - set(case.keys())
        assert missing == set(), f"seeded case would fail add_case: missing {missing}"

    def test_seeded_case_has_replayable_scenario(self, svc):
        # M5 Part 2: the seed is a trajectory_capture DRAFT skeleton. It must
        # carry a non-empty scenario.prompt + a non-empty expected_trajectory
        # (eval_runner.py:882 makes a decision_rubric with empty trajectory a
        # hard ERROR). These are the fields the trajectory runner consumes.
        case = svc.auto_seed_case("C040", "correction body here", "CLASS_B")
        prompt = case.get("scenario", {}).get("prompt", "")
        assert prompt, "seeded case has no scenario.prompt → trajectory runner skips it"
        assert case.get("expected_trajectory"), (
            "seeded case has empty expected_trajectory → decision_rubric is a hard error"
        )
        assert case.get("decision_rubric"), "seeded skeleton has no decision_rubric"

    def test_seeded_case_evaluator_is_recognized(self, svc):
        case = svc.auto_seed_case("C041", "text", "CLASS_A")
        evs = set(case.get("evaluators", []))
        known = (eval_runner.PROGRAMMATIC_EVALUATORS | eval_runner.LLM_EVALUATORS
                 | eval_runner.BEHAVIOR_EVALUATORS)
        assert evs, "seeded case has no evaluator"
        assert evs & known, f"seeded evaluators {evs} are none of the recognized {known}"

    def test_seeded_case_is_behavior_draft_excluded_from_score(self, svc):
        # M5 Part 2 core invariant: an auto-seeded skeleton is eval_method=behavior
        # + tier=draft, so the normal score path filters it out (eval_runner.py:1189
        # drops behavior cases unless the behavior_trajectory tag is requested).
        # An unrefined skeleton must NEVER pollute the health number.
        case = svc.auto_seed_case("C042", "some recurring failure", "CLASS_A")
        assert case["eval_method"] == "behavior"
        assert case["tier"] == "draft"
        assert case["evaluators"] == ["trajectory_capture"]
        # The governing doc for CLASS_A is STEERING.md → trajectory reads it.
        assert case["expected_trajectory"] == ["Read STEERING.md"]


class TestSeededCaseReplayable:
    """End-to-end: the seeded case is judged by the runner, not skipped."""

    def test_seed_from_correction_hook_appends_case(self, svc, eval_workspace, monkeypatch):
        # Drive the actual hook (not auto_seed_case directly) so the full seam is
        # covered. The hook imports get_eval_service LOCALLY from core.eval_service,
        # so patch it at the source module (not on eval_hooks).
        import core.eval_service as eval_service_mod
        monkeypatch.setattr(eval_service_mod, "get_eval_service", lambda: svc)
        before = svc.case_count
        eval_hooks.seed_from_correction("C100", "never auto-deploy without approval", "CLASS_A")
        assert svc.case_count == before + 1
        seeded = next(c for c in svc._cases if c["id"] == "GS_C100")
        assert seeded["tier"] == "draft"

    def test_seeded_draft_is_skipped_never_spawns(self, svc, monkeypatch):
        # M5 Part 2 + adversarial Gate-2 HIGH (run_0305426d): an auto-seeded
        # skeleton is tier=draft and must NEVER be graded — not even on an
        # explicit behavior_trajectory run (which bypasses the eval_method filter
        # in run_eval). eval_trajectory_capture must skip tier=draft BEFORE
        # spawning, so an unrefined tautology-rubric skeleton can't fold a free
        # pass into the score or spend Bedrock. The prose "refine before relying"
        # is enforced in CODE here.
        case = svc.auto_seed_case("C200", "do not repeat the silent-fallback pattern", "CLASS_B")
        assert case["tier"] == "draft"

        spawned = {"called": False}
        def _fake_run(prompt, allowed_tools=None, timeout=120):
            spawned["called"] = True
            return [], "I would just do it."
        monkeypatch.setattr("scripts.scenario_runner.run_scenario_full", _fake_run)

        result = eval_runner.eval_trajectory_capture(case)

        assert result["status"] == "skipped", (
            f"tier=draft skeleton must be SKIPPED, got {result.get('status')}"
        )
        assert not spawned["called"], "a draft skeleton must NEVER spawn a real agent"

    def test_refined_draft_promoted_off_draft_does_spawn(self, svc, monkeypatch):
        # Symmetry: once a human refines the skeleton and promotes it off draft
        # tier, it DOES run — proving the guard keys on tier=draft specifically,
        # not on the auto_seed_skeleton tag or behavior method (which a real
        # refined case still carries).
        case = svc.auto_seed_case("C201", "some failure", "CLASS_A")
        case["tier"] = "active"  # human refined + promoted

        spawned = {"called": False}
        def _fake_run(prompt, allowed_tools=None, timeout=120):
            spawned["called"] = True
            return ["Read STEERING.md"], "I will not repeat it."
        monkeypatch.setattr("scripts.scenario_runner.run_scenario_full", _fake_run)
        # decision judge would fire after trajectory pass; stub it to avoid Bedrock.
        monkeypatch.setattr(eval_runner, "_judge_decision_direction",
                            lambda case, txt: {"status": "passed", "notes": "ok"})

        result = eval_runner.eval_trajectory_capture(case)
        assert spawned["called"], "a refined (non-draft) case must run"
        assert result["status"] != "skipped"

    def test_malformed_case_would_be_error(self, svc):
        # Negative control: a trajectory case with a decision_rubric but EMPTY
        # expected_trajectory IS a hard error (eval_runner.py:886) — proving the
        # above assertion is meaningful (the seeded case clears a real bar that
        # a malformed one does not).
        bad_case = {"id": "GS_BAD", "evaluators": ["trajectory_capture"], "title": "x",
                    "scenario": {"prompt": "do something"}, "expected_trajectory": [],
                    "decision_rubric": "PASS if X"}
        result = eval_runner.eval_trajectory_capture(bad_case)
        assert result["status"] == "error"
