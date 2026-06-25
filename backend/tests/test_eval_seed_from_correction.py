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
from unittest.mock import patch, MagicMock

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.eval_service import EvalService
import core.eval_hooks as eval_hooks
from scripts import eval_runner


@pytest.fixture
def eval_workspace(tmp_path):
    project_dir = tmp_path / "Projects" / "SwarmAI"
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
        # eval_runner skips a case with no turns/assertions (eval_runner.py:628).
        # A seeded case MUST carry both or it is dead-on-arrival.
        case = svc.auto_seed_case("C040", "correction body here", "CLASS_B")
        turns = case.get("scenario", {}).get("turns", [])
        assert turns and turns[0].get("input"), "seeded case has no usable scenario turn"
        assert case.get("assertions"), "seeded case has no assertions → runner skips it"

    def test_seeded_case_evaluator_is_recognized(self, svc):
        case = svc.auto_seed_case("C041", "text", "CLASS_A")
        evs = set(case.get("evaluators", []))
        known = eval_runner.PROGRAMMATIC_EVALUATORS | eval_runner.LLM_EVALUATORS
        assert evs, "seeded case has no evaluator"
        assert evs & known, f"seeded evaluators {evs} are none of the recognized {known}"


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

    def test_seeded_case_reaches_judge_not_skipped(self, svc):
        # The core replayability proof: a goal_success case routes to the LLM
        # judge. We STUB Bedrock (no real call) and assert the judge path is
        # REACHED — i.e. the case is NOT skipped as malformed. If the seeded
        # scenario were empty, eval_llm_judge returns status=skipped BEFORE the
        # client is ever touched.
        case = svc.auto_seed_case("C200", "do not repeat the silent-fallback pattern", "CLASS_B")

        fake_client = MagicMock()
        fake_client.converse.return_value = {
            "output": {"message": {"content": [{"text":
                '{"verdict":"passed","assertion_results":[],"confidence":0.9,"notes":"ok"}'}]}}
        }
        with patch("core.llm_optimizer._get_bedrock_client", return_value=fake_client):
            result = eval_runner.eval_llm_judge(case, "goal_success")

        assert result.get("status") != "skipped", (
            f"seeded case was SKIPPED (not replayable): {result.get('notes')}"
        )
        # Proof the judge path was actually exercised, not short-circuited.
        assert fake_client.converse.called, "judge prompt never sent — case not replayable"

    def test_malformed_case_would_be_skipped(self, svc):
        # Negative control: a case WITHOUT a scenario IS skipped — proving the
        # above assertion is meaningful (the seeded case clears a real bar).
        bad_case = {"id": "GS_BAD", "evaluators": ["goal_success"], "title": "x",
                    "scenario": {"turns": []}, "assertions": []}
        result = eval_runner.eval_llm_judge(bad_case, "goal_success")
        assert result["status"] == "skipped"
