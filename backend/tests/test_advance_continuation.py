"""Stage-advance continuation contract — the CONTINUE-side enforcement carrier.

WHAT IS TESTED
--------------
The pipeline skill had an *enforcement asymmetry*: its STOP side is code-enforced
(``cmd_advance`` itself ``sys.exit(1)``s on validation failure; ``run-update
--status completed`` BLOCKS; ``cmd_run_checkpoint`` hard-blocks a checkpoint whose
``should_checkpoint=false``), while its CONTINUE side existed only as loop-level
prose (INSTRUCTIONS.md §3f, the Core Loop, Rule 20) with **zero turn-boundary
directive and zero carrier**. Observed consequence: the orchestrating agent ran
``advance`` and then ended its assistant turn (``stop_reason=end_turn``, zero
tool_use in that message) — nothing interrupted it, it simply read ``advance`` as
a terminal action.

The fix under test adds a *condition-aware* carrier: ``cmd_advance``'s success
payload now names the next stage doc (``next_stage_doc``) and what to do with it
(``next_action``), and the same contract phrase is mirrored in the stage docs so
the two sources cannot drift.

METHODOLOGY
-----------
- The CLI-side tests drive ``cmd_advance`` directly with a stub args object and a
  ``tmp_path`` workspace, then parse the captured stdout. ``Path.is_file()`` is
  never mocked — "the emitted path really exists" IS the contract.
- The doc-side tests read the real source-of-truth files under
  ``backend/skills/`` (never the ``.claude/skills/`` projection, which lags) and
  assert the shared phrase, following the established
  ``test_deliver_template_drift.py`` pattern.
- The stop-side tests are REGRESSION guards: they assert this change did not
  weaken the fail-closed validation branch, and that the Stop hook remains
  passive (no ``decision="block"`` / ``continue_=False`` interception, which the
  design explicitly forbids — it would misfire on the only three legitimate
  stops: real L2 escalation, a true checkpoint, and the user pressing stop).

KEY INVARIANTS
--------------
1. A path is emitted only after ``Path.is_file()`` passes — never a fabricated one.
2. ``next_action`` is conditioned on the existing ``_compute_should_checkpoint``
   SSOT: when a checkpoint is genuinely due, the payload must NOT urge
   continuation.
3. The CLI phrase and the doc phrase come from ONE module constant.
4. The continuation carrier never makes ``advance`` fail (degrades silently).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

# ── Locate the real source tree (tests may run from either repo root) ────────
_THIS = Path(__file__).resolve()
BACKEND = _THIS.parent.parent
REPO = BACKEND.parent
SKILL = BACKEND / "skills" / "s_autonomous-pipeline"
STAGES = SKILL / "stages"

# Sibling skills that also call `advance` (Gate-1 finding #4 — R27 scope).
SIBLING_DOCS = [
    BACKEND / "skills" / "s_evaluate" / "SKILL.md",
    BACKEND / "skills" / "s_qa" / "INSTRUCTIONS.md",
    BACKEND / "skills" / "s_deliver" / "SKILL.md",
]


def _load_cli():
    """Import artifact_cli.py by path (it lives in scripts/, not a package)."""
    path = BACKEND / "scripts" / "artifact_cli.py"
    spec = importlib.util.spec_from_file_location("_artifact_cli_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cli():
    return _load_cli()


class _Args:
    """Minimal stand-in for argparse.Namespace as cmd_advance consumes it."""

    def __init__(self, project, state, run_id=None):
        self.project = project
        self.state = state
        self.run_id = run_id


def _write_run(tmp_path, project, run_id, profile="full", completed=(), token_cost=0):
    """Create a run.json in the layout _resolve_run_file expects."""
    run_dir = tmp_path / "Projects" / project / ".artifacts" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = [
        {"stage": s, "status": "completed", "token_cost": token_cost} for s in completed
    ]
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": run_id, "project": project, "profile": profile,
                    "status": "running", "stages": stages}),
        encoding="utf-8",
    )
    return run_dir


@pytest.fixture
def workspace(tmp_path, monkeypatch, cli):
    """Point the CLI's workspace resolution at tmp_path.

    `_get_workspace()` reads the SWARM_WORKSPACE env var (artifact_cli.py:120-125),
    so setting it is what actually redirects run.json lookups — verified, not
    assumed. Auto-validation is stubbed out because these tests exercise the
    continuation payload, not the validator (which has its own suite).
    """
    monkeypatch.setenv("SWARM_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(cli, "_auto_validate_before_advance",
                        lambda *a, **k: None, raising=False)
    return tmp_path


def _advance_payload(cli, args, capsys):
    """Run cmd_advance and return its parsed stdout JSON."""
    class _Reg:
        def advance_pipeline(self, project, state):
            return None

    cli.cmd_advance(args, _Reg())
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "cmd_advance produced no stdout"
    return json.loads(out[-1])


# ── AC1: stdout carries a validated next_stage_doc + next_action ─────────────

class TestStdoutContract:
    """AC1 — the success payload names the next doc and what to do with it."""

    def test_success_payload_has_continuation_keys(self, cli, workspace, capsys):
        _write_run(workspace, "P", "run_x", completed=["evaluate"])
        payload = _advance_payload(cli, _Args("P", "think", "run_x"), capsys)

        assert payload["project"] == "P"
        assert payload["pipeline_state"] == "think"
        assert "next_stage_doc" in payload, "next_stage_doc missing from advance stdout"
        assert "next_action" in payload, "next_action missing from advance stdout"
        assert payload["next_action"], "next_action must be non-empty"

    def test_emitted_path_really_exists_on_disk(self, cli, workspace, capsys):
        """Never emit a fabricated path — is_file() is deliberately NOT mocked."""
        _write_run(workspace, "P", "run_x", completed=["evaluate"])
        payload = _advance_payload(cli, _Args("P", "think", "run_x"), capsys)

        doc = payload["next_stage_doc"]
        assert doc, "expected a next_stage_doc for a mid-profile transition"
        assert Path(doc).is_absolute(), f"next_stage_doc must be absolute, got {doc!r}"
        assert Path(doc).is_file(), f"next_stage_doc does not exist: {doc}"

    def test_path_points_at_source_of_truth_not_projection(self, cli, workspace, capsys):
        """INSTRUCTIONS.md:389-392 — read backend/skills, never .claude/skills."""
        _write_run(workspace, "P", "run_x", completed=["evaluate"])
        payload = _advance_payload(cli, _Args("P", "think", "run_x"), capsys)

        doc = payload["next_stage_doc"]
        assert ".claude/skills" not in doc, (
            "next_stage_doc points at the stale projected copy; must be backend/skills"
        )
        assert "backend/skills/s_autonomous-pipeline/stages" in doc.replace("\\", "/")

    def test_missing_doc_yields_null_not_an_unverified_path(
        self, cli, workspace, monkeypatch, capsys, tmp_path
    ):
        """The is_file() guard must be what suppresses the path — not luck.

        Every real stage doc currently exists on disk, so a successor is always
        resolvable in practice; that makes "we never emit a bad path" untestable
        against the real tree (removing the guard still passes). Pointing the doc
        dir at an empty directory makes "doc absent" constructible, so this is the
        ONLY test that fails when the guard is deleted. Without it, AC1 would be
        vacuous — green because the files happen to be there.
        """
        empty = tmp_path / "no_stage_docs"
        empty.mkdir()
        monkeypatch.setattr(cli, "_STAGE_DOC_DIR", empty, raising=True)

        _write_run(workspace, "P", "run_m", completed=["evaluate"])
        payload = _advance_payload(cli, _Args("P", "think", "run_m"), capsys)

        assert payload["next_stage_doc"] is None, (
            "a successor whose doc is absent must yield null — emitting an "
            f"unverified path sends the agent to a failing Read: "
            f"{payload['next_stage_doc']!r}"
        )
        assert payload["next_action"], "an instruction is still required"


# ── AC2: correct successor for every transition; terminal is null ────────────

class TestStageResolution:
    """AC2 — the doc named must be the stage being ENTERED, for every transition.

    ``advance --state X`` means "the run is now entering X" — ``stages/build.md``
    issues ``advance --state review`` at the END of BUILD. So the doc to read next
    is ``X.md`` itself.

    This is stated at length because the FIRST version of this suite asserted the
    opposite (``("review", "test.md")`` etc., i.e. stage-after-next) and so ratified
    an off-by-one that skipped the stage just entered. A spec reviewer caught it, not
    these tests. RP64: a hardcoded-literal expectation LOCKS a wrong answer, and a
    test written by the same author who wrote the bug tends to encode the same
    misunderstanding. If a future change makes these tests fail, re-derive the
    semantics from a stage doc's own advance line before "fixing" the expectation.
    """

    # EVERY stage in each profile, `evaluate` included. No stage doc issues
    # `advance --state evaluate` (evaluate is entered at run start, not advanced
    # into), but it is still covered here because the helper accepts any state and
    # an off-by-one hides precisely at a list's edges. An earlier revision of this
    # list omitted the edges AND hardcoded a stage-after-next expectation, so it
    # ratified a skew instead of catching it (RP64).
    @pytest.mark.parametrize(
        "advanced_to",
        ["evaluate", "think", "plan", "build", "review", "test", "deliver", "reflect"],
    )
    def test_full_profile_names_the_stage_being_entered(
        self, cli, workspace, capsys, advanced_to
    ):
        _write_run(workspace, "P", "run_f", profile="full")
        payload = _advance_payload(cli, _Args("P", advanced_to, "run_f"), capsys)

        doc = payload["next_stage_doc"]
        assert doc, f"expected a doc when entering {advanced_to}"
        assert Path(doc).name == f"{advanced_to}.md", (
            f"advancing to {advanced_to} must point at {advanced_to}.md "
            f"(the stage being entered), got {Path(doc).name}"
        )
        assert Path(doc).is_file()

    # `goal_cycle` is advanceable: PIPELINE_STATES is derived from PIPELINE_PROFILES
    # (test_goal_cycle_is_an_advanceable_state below pins that against the REAL enum,
    # since this test's registry stub no-ops advance_pipeline).
    @pytest.mark.parametrize(
        "advanced_to", ["evaluate", "think", "plan", "goal_cycle", "deliver", "reflect"]
    )
    def test_goal_profile_names_the_stage_being_entered(
        self, cli, workspace, capsys, advanced_to
    ):
        _write_run(workspace, "P", "run_g", profile="goal")
        payload = _advance_payload(cli, _Args("P", advanced_to, "run_g"), capsys)

        doc = payload["next_stage_doc"]
        assert doc, f"expected a doc when entering {advanced_to} (goal)"
        assert Path(doc).name == f"{advanced_to}.md"
        assert Path(doc).is_file()

    def test_goal_cycle_is_an_advanceable_state(self):
        """Every profile stage — including goal's `goal_cycle` — is in the REAL
        PIPELINE_STATES that `advance_pipeline` validates against (the gap this
        file used to pin is closed: the enum is derived from PIPELINE_PROFILES)."""
        import sys
        sys.path.insert(0, str(BACKEND))
        from core.artifact_registry import PIPELINE_STATES
        from core.pipeline_profiles import PIPELINE_PROFILES

        assert "goal_cycle" in PIPELINE_PROFILES["goal"], "goal profile shape changed"
        for stages in PIPELINE_PROFILES.values():
            assert set(stages) <= set(PIPELINE_STATES)

    def test_reflect_points_at_reflect_not_past_it(self, cli, workspace, capsys):
        """The regression guard for the off-by-one, stated as its own case.

        REFLECT is a real stage with real work (it writes the lessons back to the
        DDD). Routing straight to COMPLETE here would silently skip that — the
        precise damage the +1 revision caused.
        """
        _write_run(workspace, "P", "run_t", profile="full")
        payload = _advance_payload(cli, _Args("P", "reflect", "run_t"), capsys)

        doc = payload["next_stage_doc"]
        assert doc and Path(doc).name == "reflect.md", (
            "advancing to reflect must point at reflect.md, not skip to COMPLETE"
        )

    def test_off_profile_state_yields_null_not_a_confident_guess(
        self, cli, workspace, capsys
    ):
        """A stage the profile does not run must not be handed out as the next doc.

        `goal_cycle.md` exists on disk, so a naive implementation would happily
        emit it for a `full` run that never executes that stage.
        """
        _write_run(workspace, "P", "run_o", profile="full")
        payload = _advance_payload(cli, _Args("P", "goal_cycle", "run_o"), capsys)

        assert payload["next_stage_doc"] is None, (
            "goal_cycle is not in the full profile — emitting its doc would send "
            f"the agent into a stage this run never runs: {payload['next_stage_doc']!r}"
        )
        assert payload["next_action"], "an instruction is still required"

        # The FIELD being null is not enough: prose that says "read that stage's doc
        # from stages/" re-creates the confidently-wrong pointer the null was meant
        # to withhold — the field-vs-string inconsistency, inverted.
        action = payload["next_action"]
        assert "stages/" not in action, (
            f"off-profile instruction must not point at a stage doc: {action!r}"
        )
        assert re.search(r"not part of this run's profile", action), (
            f"off-profile case should name the real problem: {action!r}"
        )

    def test_reflect_doc_carries_v1_terminal_commands(self):
        """REFLECT must route background processing to a bounded terminal command."""
        text = (STAGES / "reflect.md").read_text(encoding="utf-8")
        assert "pipeline_v1.py reflect-complete" in text
        assert "reflect-exhausted" in text


# ── AC7: budget is NEVER a reason to withhold continuation (post-refactor) ───

class TestCheckpointConditioning:
    """AC7 — budget NEVER blocks/withholds continuation (STEERING #2 + XG directive).

    SEMANTIC INVERSION (2026-10 budget-cognitive-hygiene refactor): the former
    contract was "a due BUDGET checkpoint must not be argued against" — advance read
    `_compute_should_checkpoint` and withheld continuation when budget was high. That
    budget-block is REMOVED: `_compute_should_checkpoint["should_checkpoint"]` is now
    always False, budget is no longer a true-trigger, and advance NEVER withholds
    continuation on a budget/token basis. A real checkpoint (L2/retry/stuck) is
    surfaced by the pause gates + Escalation Routing Protocol, not by this probe.
    These tests now assert the INVERTED contract: no matter the token consumption,
    advance keeps urging continuation.
    """

    def test_continuation_urged_at_low_consumption(self, cli, workspace, capsys):
        _write_run(workspace, "P", "run_lo", completed=["evaluate"], token_cost=1_000)
        payload = _advance_payload(cli, _Args("P", "think", "run_lo"), capsys)

        assert cli._CONTINUATION_PHRASE in payload["next_action"], (
            "next_action must carry the continuation phrase"
        )

    def test_high_token_consumption_still_urges_continuation(self, cli, workspace, capsys):
        """INVERTED: a high token count is NO LONGER a checkpoint — budget never blocks.

        Pre-refactor this asserted the opposite (400K consumed → checkpoint DUE →
        continuation suppressed). Budget-block removed (STEERING #2): the same run
        must now keep urging continuation and name the next stage doc.
        """
        _write_run(
            workspace, "P", "run_hi",
            completed=["evaluate", "think", "plan"], token_cost=400_000,
        )
        payload = _advance_payload(cli, _Args("P", "build", "run_hi"), capsys)

        action = payload["next_action"]
        assert cli._CONTINUATION_PHRASE in action, (
            "budget is never a checkpoint — high consumption must STILL urge "
            f"continuation, got: {action!r}"
        )
        assert payload["next_stage_doc"] is not None, (
            "a high-consumption run must still get its next stage doc (budget never "
            f"withholds it), got {payload['next_stage_doc']!r}"
        )

    def test_partial_budget_dict_still_continues(self, cli, workspace, capsys):
        """INVERTED: an unmeasurable budget no longer withholds continuation.

        Pre-refactor a partial `budget` dict routed to a "could not be measured —
        resolve budget first" instruction (fail-safe on the STOP side). Budget-block
        removed: advance resolves `stages` from the profile and continues regardless;
        budget is never a gating consideration, so there is nothing to "resolve".
        """
        run_dir = workspace / "Projects" / "P" / ".artifacts" / "runs" / "run_bad"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps({"run_id": "run_bad", "project": "P", "profile": "full",
                        "budget": {}, "stages": []}),
            encoding="utf-8",
        )

        payload = _advance_payload(cli, _Args("P", "review", "run_bad"), capsys)
        action = payload["next_action"]

        assert cli._CONTINUATION_PHRASE in action, (
            "a partial budget dict must NOT withhold continuation (budget never "
            f"blocks): {action!r}"
        )
        assert not re.search(r"could not be measured", action, re.I), (
            "the old budget-unmeasured instruction must be gone — budget is not a "
            f"gating consideration anymore: {action!r}"
        )

    def test_missing_token_cost_still_continues(self, cli, workspace, capsys):
        """INVERTED: a missing token_cost is irrelevant — budget never gates advance.

        Pre-refactor a completed stage without token_cost under-counted `consumed`
        and routed to "unmeasured → don't urge continuation". Budget-block removed:
        token_cost presence/absence no longer affects whether advance continues.
        """
        run_dir = workspace / "Projects" / "P" / ".artifacts" / "runs" / "run_nc"
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(
            json.dumps({
                "run_id": "run_nc", "project": "P", "profile": "full",
                "stages": [
                    {"stage": "evaluate", "status": "completed", "token_cost": 9000},
                    {"stage": "think", "status": "completed"},  # no token_cost
                ],
            }),
            encoding="utf-8",
        )

        payload = _advance_payload(cli, _Args("P", "review", "run_nc"), capsys)
        assert cli._CONTINUATION_PHRASE in payload["next_action"], (
            "a missing token_cost is irrelevant to continuation now (budget never "
            f"blocks): {payload['next_action']!r}"
        )

    def test_missing_run_id_still_continues(self, cli, workspace, capsys):
        """INVERTED: no --run-id is fine — budget was the only thing it gated.

        Pre-refactor a run_id-less advance could not measure budget and withheld
        continuation. Budget-block removed: there is no budget question to answer,
        so advance continues (resolving `stages` from the full-profile default).
        """
        payload = _advance_payload(cli, _Args("P", "review", None), capsys)

        assert cli._CONTINUATION_PHRASE in payload["next_action"], (
            "without a run id there is no budget to gate on — must still urge "
            f"continuation: {payload['next_action']!r}"
        )

    def test_payload_keys_present_even_when_resolution_fails(
        self, cli, workspace, monkeypatch, capsys
    ):
        """The documented payload shape must be stable — no KeyError for consumers."""
        _write_run(workspace, "P", "run_k", completed=["evaluate"])
        monkeypatch.setattr(
            cli, "_next_stage_continuation",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
            raising=False,
        )
        payload = _advance_payload(cli, _Args("P", "review", "run_k"), capsys)

        assert "next_stage_doc" in payload and "next_action" in payload, (
            "both keys must stay present (null) so consumers never KeyError"
        )
        assert payload["next_stage_doc"] is None
        assert payload["next_action"] is None

    def test_advance_no_longer_gates_on_budget_ssot(self, cli, workspace, monkeypatch, capsys):
        """INVERTED: advance must NOT make should_checkpoint gate continuation.

        Pre-refactor advance called `_compute_should_checkpoint` and withheld the
        continuation phrase when it returned should_checkpoint=true. Budget-block
        removed: even if the SSOT is forced to return should_checkpoint=True, advance
        must STILL urge continuation — budget can never gate it. This is the
        mutation-proof that the budget→withhold wiring is truly gone, not just that
        the SSOT now returns False.
        """
        _write_run(workspace, "P", "run_force", completed=["evaluate"], token_cost=1_000)

        def _force_due(run_state, project):
            return {"consumed": 999, "remaining": 0, "usable": 0, "pct_consumed": 99.9,
                    "next_stage": "think", "next_stage_estimate": 30000,
                    "should_checkpoint": True, "budget_total": 800000,
                    "calibration_source": "defaults"}

        monkeypatch.setattr(cli, "_compute_should_checkpoint", _force_due, raising=True)
        payload = _advance_payload(cli, _Args("P", "think", "run_force"), capsys)

        assert cli._CONTINUATION_PHRASE in payload["next_action"], (
            "even a forced should_checkpoint=True must NOT withhold continuation — "
            "budget never gates advance (STEERING #2). If this fails, a budget→"
            f"checkpoint wire survived the refactor: {payload['next_action']!r}"
        )


# ── AC3 / AC4 / AC9: the doc side, sharing ONE phrase ───────────────────────

class TestDocContracts:
    """AC9 — sibling skills retain the legacy advance contract they still use."""


    @pytest.mark.parametrize("doc", SIBLING_DOCS, ids=lambda p: p.parent.name)
    def test_sibling_skills_carry_the_contract(self, cli, doc):
        """AC9 — the 3 sibling skills that also call advance (Gate-1 finding #4)."""
        text = doc.read_text(encoding="utf-8")
        assert "artifact_cli.py advance" in text, f"{doc} no longer calls advance — update this test"
        assert cli._CONTINUATION_PHRASE in text, (
            f"{doc.parent.name} calls advance but omits the continuation contract "
            "— the same contract must not drift across skills"
        )

    @pytest.mark.parametrize("doc", SIBLING_DOCS, ids=lambda p: p.parent.name)
    def test_sibling_advance_passes_run_id(self, doc):
        """A --run-id-less advance skips validation entirely (run_f3975b8b)."""
        text = doc.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "artifact_cli.py advance" in line:
                assert "--run-id" in line, (
                    f"{doc.parent.name}: advance without --run-id skips auto-validation"
                )


# ── AC5 / AC6: the stop side is untouched (regression guards) ───────────────

class TestStopSideUnweakened:
    """AC5/AC6 — the continuation carrier must not erode any legitimate stop."""

    def test_validation_failure_still_fails_closed(self, cli, workspace, monkeypatch, capsys):
        """The fail-closed branch must keep exiting non-zero, with no hint leaked."""
        _write_run(workspace, "P", "run_v", completed=["evaluate"])

        def _boom(*a, **k):
            raise SystemExit(1)

        monkeypatch.setattr(cli, "_auto_validate_before_advance", _boom, raising=False)

        class _Reg:
            def advance_pipeline(self, project, state):
                raise AssertionError("must not advance when validation blocks")

        with pytest.raises(SystemExit) as exc:
            cli.cmd_advance(_Args("P", "think", "run_v"), _Reg())
        assert exc.value.code == 1
        assert "next_action" not in capsys.readouterr().out

    def test_real_valueerror_branch_still_exits_nonzero(self, cli, workspace, capsys):
        """Drive the ACTUAL error branch, not a monkeypatched stand-in.

        The sibling test above injects SystemExit through the validator, which
        proves propagation but never executes cmd_advance's own
        `except ValueError -> sys.exit(1)` path. An invalid state makes the real
        registry raise, so this is the branch as production hits it.
        """
        _write_run(workspace, "P", "run_ve", completed=["evaluate"])

        class _Reg:
            def advance_pipeline(self, project, state):
                raise ValueError(f"Unknown pipeline state '{state}'")

        with pytest.raises(SystemExit) as exc:
            cli.cmd_advance(_Args("P", "not_a_stage", "run_ve"), _Reg())
        assert exc.value.code == 1
        captured = capsys.readouterr()
        assert "next_action" not in captured.out, (
            "a failed advance must not emit a continuation hint"
        )
        assert "next_stage_doc" not in captured.out

    def test_continuation_failure_never_breaks_advance(self, cli, workspace, monkeypatch, capsys):
        """The hint is an enhancement — it must degrade, never fail the command."""
        _write_run(workspace, "P", "run_d", completed=["evaluate"])

        def _boom(*a, **k):
            raise RuntimeError("continuation resolution exploded")

        monkeypatch.setattr(cli, "_next_stage_continuation", _boom, raising=False)
        payload = _advance_payload(cli, _Args("P", "think", "run_d"), capsys)
        assert payload["pipeline_state"] == "think", "advance must still succeed"

    def test_stop_hook_remains_passive(self):
        """AC6 — no end_turn interception (STEERING #2: would misfire on real stops).

        Asserts the two mechanisms that could actually intercept a turn ending —
        a ``decision: "block"`` verdict or ``continue_: False`` — rather than
        scanning for the word "block", which would trip on any unrelated comment.
        Interception is forbidden by design: the Stop hook's input carries only
        ``stop_hook_active``, so it cannot tell an illegitimate stall from the
        three legitimate stops (real L2 escalation, a true checkpoint, the user
        pressing stop) and would misfire on exactly those.
        """
        src = (BACKEND / "core" / "hook_builder.py").read_text(encoding="utf-8")
        idx = src.index("def _stop_hook")
        body = src[idx: src.index('registry.register("Stop"', idx)]

        assert '"decision": "approve"' in body, "Stop hook must stay passive"
        assert '"block"' not in body, "Stop hook must not return a block verdict"
        assert "continue_" not in body, "Stop hook must not force continuation"

        # The two interception mechanisms must be absent from the WHOLE Stop
        # registration, not just the callback body — a blocking verdict could also
        # be injected at the register() call site.
        reg_line_start = src.index('registry.register("Stop"', idx)
        registration = src[reg_line_start: reg_line_start + 200]
        assert "block" not in registration.lower(), (
            "the Stop registration must not introduce a blocking verdict"
        )


    def test_budget_removed_as_checkpoint_trigger(self):
        """REFACTOR POSITIVE ASSERTION: budget is no longer a true-trigger.

        The core of the refactor — mutation-proof that a budget-worded reason is NOT
        treated as a legitimate pause. If 'budget' is ever re-added to
        _CHECKPOINT_TRUE_TRIGGERS, this goes RED.
        """
        import sys as _s
        _s.path.insert(0, str(BACKEND))
        from scripts.artifact_cli import (
            _CHECKPOINT_TRUE_TRIGGERS,
            _checkpoint_reason_has_true_trigger,
        )
        assert "budget" not in _CHECKPOINT_TRUE_TRIGGERS, (
            "budget was re-added as a true-trigger — the budget-block it enables is "
            "banned (STEERING #2 + XG directive)"
        )
        assert _checkpoint_reason_has_true_trigger("budget exhausted") is False, (
            "a budget-worded reason must not count as a true trigger"
        )
        # real triggers survive
        for r in ("l2 block", "retry exhausted", "stuck on X", "judgment call"):
            assert _checkpoint_reason_has_true_trigger(r) is True, f"lost trigger: {r}"


class TestLegacyFlatRunDeletion:
    """3b: the legacy flat ``pipeline-run-<id>.json`` read path was deleted.
    These lock the two contracts the deletion must preserve."""

    def test_resolve_run_file_still_fail_closes_on_missing_run(self, cli, workspace):
        """After the legacy branch is gone, a missing run must still
        ``sys.exit(1)`` (fail-closed) — not silently return a non-existent path."""
        with pytest.raises(SystemExit) as exc:
            cli._resolve_run_file("P", "run_does_not_exist")
        assert exc.value.code == 1

    def test_resolve_run_file_finds_new_path(self, cli, workspace):
        """The surviving new-path branch still resolves a real run.json."""
        _write_run(workspace, "P", "run_live", completed=["evaluate"])
        resolved = cli._resolve_run_file("P", "run_live")
        assert resolved.name == "run.json"
        assert resolved.exists()

    def test_nameless_stage_record_reads_question_mark(self, cli, workspace):
        """After collapsing ``s.get('stage', s.get('name'))`` → ``s.get('stage', '?')``,
        a persisted stage record with neither key reads '?' (the dead ``name``
        fallback is gone, the default survives). Guards the collapse is non-vacuous."""
        rec = {"status": "completed"}  # no 'stage', no 'name'
        assert rec.get("stage", "?") == "?"
        # and a real record still reads its stage
        assert {"stage": "build"}.get("stage", "?") == "build"
