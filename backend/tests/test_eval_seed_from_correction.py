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


# ─────────────────────────────────────────────────────────────────────────────
# reclaim_stale_skeletons — the Darwinian TTL end of the auto_seed lifecycle.
# auto_seed_case (the producer) has an idempotency guard but NO expiry, so
# unrefined skeletons a human never touches accumulate forever. reclaim_stale_
# skeletons is the reclaim pass (run from context_health_hook), mirroring
# ddd_cultivation._expire_stale_proposals. See run_9f5944b4.
# ─────────────────────────────────────────────────────────────────────────────

_DAY = 86400.0


def _skeleton(*, cid, age_days, now, refined=False, tier="draft", scenario_none=False):
    """Build an auto_seed-shaped skeleton case with a source epoch `age_days` old.

    refined=True strips BOTH placeholder markers (simulates a human who refined
    the draft in place). scenario_none=True sets scenario to None (66 real cases
    on disk have this — the crash Gate-1 caught).
    """
    epoch = now - age_days * _DAY
    prompt = ("Real refined pressure scenario." if refined
              else "[AUTO-SEEDED DRAFT — needs human refinement into a real pressure scenario] ...")
    rubric = ("PASS if the agent cites the rule for CR review." if refined
              else "SKELETON RUBRIC (refine before relying on this): PASS only if ...")
    case = {
        "id": cid,
        "category": "compliance",
        "dimension": "compliance",
        "evaluators": ["trajectory_capture"],
        "affected_by": ["AGENT.md"],
        "eval_method": "behavior",
        "tier": tier,
        "tags": ["behavior_trajectory", "auto_seed_skeleton"],
        "title": "skeleton",
        "source": f"{epoch}:{cid}",
        "scenario": None if scenario_none else {"prompt": prompt},
        "decision_rubric": rubric,
        "expected_trajectory": ["Read AGENT.md"],
    }
    return case


def _install_cases(svc, cases):
    """Put cases into svc as if loaded from disk (in-memory + persisted so the
    delegated hard_delete_cases sees them on its disk-truth _load)."""
    for c in cases:
        c.setdefault("_origin", "private")
    svc._cases = list(cases)
    svc._golden_set["cases"] = list(cases)
    svc.flush_golden_set()


class TestReclaimStaleSkeletons:
    """AC1-AC6 for reclaim_stale_skeletons."""

    def test_ac1_reclaims_stale_unrefined_draft(self, svc):
        now = 1_800_000_000.0
        stale = _skeleton(cid="GS_STALE1", age_days=40, now=now)
        fresh = _skeleton(cid="GS_FRESH1", age_days=5, now=now)
        _install_cases(svc, [stale, fresh])
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == ["GS_STALE1"]
        assert {c["id"] for c in svc._cases} == {"GS_FRESH1"}

    def test_ac2_refined_and_harvest_immune(self, svc):
        now = 1_800_000_000.0
        refined = _skeleton(cid="GS_REFINED", age_days=99, now=now, refined=True)
        harvest = {  # session_harvest draft: no auto_seed_skeleton tag, different source
            "id": "GS_HARVEST_abc", "category": "quality", "dimension": "judgment_quality",
            "evaluators": ["goal_success"], "affected_by": [], "eval_method": "llm",
            "tier": "draft", "tags": [], "title": "harvest",
            "source": "session_harvest: sess-1 (goal=0.2, tool=0.3)",
            "scenario": {"turns": [{"input": "hi"}]}, "assertions": ["must do X"],
        }
        _install_cases(svc, [refined, harvest])
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == []
        assert {c["id"] for c in svc._cases} == {"GS_REFINED", "GS_HARVEST_abc"}

    def test_ac3_undecodable_epoch_kept(self, svc):
        now = 1_800_000_000.0
        bad = _skeleton(cid="GS_BADEPOCH", age_days=99, now=now)
        bad["source"] = "no-epoch-here"  # split(':')[0] → 'no-epoch-here' → not a float
        _install_cases(svc, [bad])
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == []
        assert {c["id"] for c in svc._cases} == {"GS_BADEPOCH"}

    def test_ac4_no_deadlock_returns(self, svc):
        # The design collects ids LOCK-FREE then delegates to hard_delete_cases
        # (which re-acquires the plain _data_lock). If reclaim held the lock this
        # would deadlock/hang. The test simply RETURNING proves the lock-free design.
        now = 1_800_000_000.0
        _install_cases(svc, [_skeleton(cid="GS_S", age_days=40, now=now)])
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == ["GS_S"]

    def test_ac3b_scenario_none_does_not_crash(self, svc):
        # Gate-1 caught this: 66 real cases have scenario=None. c.get("scenario",{})
        # returns None (key present), so a naive .get("prompt") would AttributeError
        # and the hook's swallowing except would silently disable reclaim forever.
        now = 1_800_000_000.0
        noscen = _skeleton(cid="GS_NOSCEN", age_days=40, now=now, scenario_none=True)
        stale = _skeleton(cid="GS_STALE2", age_days=40, now=now)
        _install_cases(svc, [noscen, stale])
        # Must not raise. GS_NOSCEN still has the SKELETON RUBRIC marker → reclaimed.
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert set(reclaimed) == {"GS_NOSCEN", "GS_STALE2"}

    def test_ac6_archived_stale_skeleton_reclaimed(self, svc):
        # Keying on tag+marker+age (not tier) intentionally also sweeps stale
        # placeholder skeletons already soft-archived (graveyard cleanup).
        now = 1_800_000_000.0
        arch = _skeleton(cid="GS_ARCH", age_days=40, now=now, tier="archived")
        _install_cases(svc, [arch])
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == ["GS_ARCH"]
        assert svc._cases == []

    def test_toctou_out_of_band_refinement_not_deleted(self, svc, eval_workspace):
        # Gate-2 security MED (run_9f5944b4): reclaim reloads disk truth BEFORE
        # scanning, so a skeleton refined OUT-OF-BAND (direct yaml edit / a second
        # process) after this instance's in-memory copy went stale is NOT wrongly
        # deleted. Simulate: seed a stale skeleton, then rewrite the private file
        # on disk with the SAME id but markers stripped (refined), WITHOUT touching
        # svc._cases (so in-memory is deliberately stale). reclaim() must reload,
        # see it refined, and keep it.
        now = 1_800_000_000.0
        stale = _skeleton(cid="GS_TOCTOU", age_days=40, now=now)
        _install_cases(svc, [stale])
        # Out-of-band refinement: same id, markers gone, still tagged + old.
        refined = _skeleton(cid="GS_TOCTOU", age_days=40, now=now, refined=True)
        refined["_origin"] = "private"
        priv = eval_workspace / "Eval" / "golden_set.private.yaml"
        priv.write_text(yaml.dump({"version": 2, "cases": [refined]}))
        # svc._cases still holds the UNREFINED copy (stale) — the bug condition.
        assert svc._cases[0]["scenario"]["prompt"].startswith("[AUTO-SEEDED")
        reclaimed = svc.reclaim_stale_skeletons(ttl_days=30, now=now)
        assert reclaimed == []  # reload saw the refined disk copy → immune
        assert {c["id"] for c in svc._cases} == {"GS_TOCTOU"}
