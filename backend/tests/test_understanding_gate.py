"""Understanding gate — universal diagnosis/framing-before-build for ALL work types.

Generalizes the bug-only REPRO gate (test_pipeline_validator_repro_gate.py) into a
work-type-shaped understanding gate at the EVALUATE→THINK boundary
(design: Knowledge/Designs/2026-06-26-understanding-gate-design.md).

The gate requires, for strict profiles (full/bugfix/goal), an `understanding` block
carrying an OBSERVATION-backed, non-hedged, present-tense (no solution-language)
claim about the CURRENT state of the world — before the pipeline may propose HOW
to fix/build (THINK). Three mechanical checks, none rely on agent discipline:

  M1 — solution-language scan (claim must describe the present, not the plan)
  M2 — hedge-word scan (unresolved 似乎/probably/should-be → BLOCK unless evidence resolves)
  M3 — skeptic sub-agent (BEHAVIORAL, in evaluate.md; NOT validator-enforced here)

These tests FORCE EXECUTION of the gate (STEERING #11: guard paths need a test that
runs them) — per work_type, asserting block/pass. Back-compat: the bug-class REPRO
path is preserved via the `observation_evidence` alias and keeps its own marker;
see test_pipeline_validator_repro_gate.py (must stay green).
"""

import pytest

from scripts.pipeline_validator import validate_artifact_data

UG = "Understanding gate"  # distinct marker from "REPRO gate" (bug-class only)


def _ug_errors(errors):
    return [e for e in errors if UG in e]


def _eval(work_type, claim, evidence, **overrides):
    """Build an evaluate artifact with an understanding block."""
    base = {
        "recommendation": "GO",
        "scope": "standard",
        "acceptance_criteria": ["x"],
        "understanding": {
            "work_type": work_type,
            "claim": claim,
            "evidence": evidence,
            "evidence_kind": "code-trace",
            "skeptic_verdict": "SUPPORTED",
            "alternative_considered": "the simpler framing X loses because Y",
        },
    }
    base.update(overrides)
    return base


_GOOD_CLAIM = (
    "Today the diagnosis gate fires only for bug-class work at "
    "pipeline_validator.py:528 — no understanding gate exists for feature work."
)
_GOOD_EVIDENCE = (
    "Code-trace this session: pipeline_validator.py:519-557 scope-gates the REPRO "
    "gate to scope==bugfix; evaluate.md:38 says 'BUG-CLASS ONLY'."
)


# ---------------------------------------------------------------------------
# M0 — strict profiles require the understanding block
# ---------------------------------------------------------------------------
class TestRequiresUnderstandingOnStrictProfiles:
    # adversarial HIGH: "standard" (alias for full, rank 4) + unknown profiles
    # must be STRICT (fail-closed), not fall through both sets silently.
    @pytest.mark.parametrize("profile", ["full", "bugfix", "goal", "standard", "complex", ""])
    def test_missing_block_blocks_on_strict(self, profile):
        data = {"recommendation": "GO", "scope": "standard", "acceptance_criteria": ["x"]}
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _ug_errors(errors), f"{profile}: missing understanding must block: {errors}"

    def test_empty_evidence_blocks_on_strict(self):
        data = _eval("existing-feature", _GOOD_CLAIM, "   ")
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"empty evidence must block: {errors}"

    def test_too_short_evidence_blocks_on_strict(self):
        data = _eval("existing-feature", _GOOD_CLAIM, "read code")  # <20 chars
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"sub-20-char evidence must block: {errors}"

    def test_bare_true_evidence_blocks(self):
        data = _eval("existing-feature", _GOOD_CLAIM, True)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"bare True evidence carries no info: {errors}"


# ---------------------------------------------------------------------------
# M1 — solution-language scan (claim must be present-state, not a plan)
# ---------------------------------------------------------------------------
class TestM1SolutionLanguage:
    @pytest.mark.parametrize("contraction_plan", [
        # adversarial HIGH (run_7cf9da85): two contractions must NOT pair their
        # apostrophes and delete the plan-language between them. These are REAL plans
        # that must still BLOCK after the C3 single-quote strip was bounded.
        "that's fine today; we will replace the parser and it's shipped",
        "it's stable now but we will add the retry layer and that's it",
    ])
    def test_contractions_do_not_hide_plan(self, contraction_plan):
        data = _eval("existing-feature", contraction_plan, _GOOD_EVIDENCE)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"contraction-spanning plan must still BLOCK: {contraction_plan!r} -> {errors}"

    @pytest.mark.parametrize("bad_claim", [
        "I will add a per-tab latestCompleteGen to fix the stale render.",
        "I'll refactor TabView to use the store as sole source.",
        "The fix is to make [DONE] authoritative in chat.ts.",
        "We should add a hedge-word scan to the validator.",
        "Let's add a new understanding block to the schema.",
        "Refactor to a single render source.",
        "Change the trigger from bug-only to all profiles.",
        # adversarial false-NEGATIVES the flagship [DONE] case must now catch:
        "Make [DONE] authoritative in chat.ts.",
        "Switch to a per-tab counter.",
        "Use a circuit breaker on the resume path.",
        "Persist the state to disk on every cycle.",
        "Move the early-return below the guard.",
        "Adding a latestCompleteGen field fixes it.",
        "The fix: make the sentinel authoritative.",
    ])
    def test_solution_language_in_claim_blocks(self, bad_claim):
        data = _eval("existing-feature", bad_claim, _GOOD_EVIDENCE)
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"solution-language claim must block: {bad_claim!r} -> {errors}"

    @pytest.mark.parametrize("ok_claim", [
        # 'change' as a NOUN describing present state — must NOT false-block
        "The state change is not persisted across tab switches today.",
        "The render source falls back to a stale prop when the store is momentarily empty.",
        "chat.ts:276 already treats [DONE] as authoritative; the proposed change would be a no-op.",
        # adversarial false-POSITIVES: negated present-state claims must pass M1
        "The handler does not implement the retry interface.",
        "The function does not create a new session.",
        "The module fails to add the header today.",
        "onComplete never persists the final gen.",
        # the present-tense VERB 'lets' must not collide with the suggestion "let's"
        # (run_b9452eb9: \blet'?s\b had an optional apostrophe → matched the verb).
        "No tool lets a solo builder run team-scale AI today.",
        "The OS lets users edit files without a restart.",
        # run_b9452eb9 same-class hunt (adversarial): the following present-state
        # shapes were false-positives in SIBLING M1 patterns, now fixed.
        # \bi'?ll\b → \bi'll\b: the adjective "ill-*" must not match the "I'll" plan.
        "The design is ill-suited to the present render path.",
        "Skill registration is ill-defined; the loader skips malformed entries today.",
        # "we can/could" dropped: "we can see/observe" is present-state observation.
        "We can see the spinner hang in the current build.",
        "We could observe the race only under load today.",
        # imperative ^VERB+\S tightened to require a determiner/bracket object:
        # sentence-initial noun homographs of action verbs must pass.
        "Use of the lock is inconsistent across handlers.",
        "Set operations dominate the current hot path.",
        # run_7cf9da85 C3: a claim that QUOTES code/a pattern in backticks or quotes
        # is a citation, not plan intent — the quoted span must be stripped before M1.
        # (I hit this live: an evaluate claim describing the `let's` pattern self-blocked.)
        "The M1 regex `\\blet's\\b` matches the contraction but not the verb today.",
        "The current pattern is \"add a guard\" which already exists in the validator.",
        "Today the gate treats `i'll` and `ill` identically — that is the bug.",
    ])
    def test_present_state_claim_passes_m1(self, ok_claim):
        data = _eval("existing-feature", ok_claim, _GOOD_EVIDENCE)
        errors = validate_artifact_data("evaluate", data, profile="full")
        # M1 specifically must not fire — other checks may still be clean here
        m1 = [e for e in _ug_errors(errors) if "solution-language" in e or "present" in e.lower()]
        assert m1 == [], f"present-state claim must pass M1: {ok_claim!r} -> {errors}"


# ---------------------------------------------------------------------------
# M2 — hedge-word scan (unresolved hedge → block; observation resolves it)
# ---------------------------------------------------------------------------
class TestM2HedgeScan:
    @pytest.mark.parametrize("hedge_claim", [
        "The spinner probably hangs because of a stale streamGen.",
        "似乎是 reconcile race 导致渲染回退。",
        "It should be the render-source fallback, I think.",
        "可能是 store momentarily empty 触发的。",
        "Maybe the prop fallback is the cause.",
    ])
    def test_hedged_claim_without_resolution_blocks(self, hedge_claim):
        # evidence is itself hedged/weak — does not resolve
        data = _eval("bugfix", hedge_claim, "probably the same thing, not sure yet though.")
        errors = validate_artifact_data("evaluate", data, profile="full")
        assert _ug_errors(errors), f"unresolved hedge must block: {hedge_claim!r} -> {errors}"

    def test_hedge_resolved_by_observation_passes(self):
        # A hedge in the claim is allowed when the EVIDENCE is a concrete observation.
        data = _eval(
            "bugfix",
            "The hang is likely the stale-gen early-return in onComplete.",
            "3-signal log triage: 114 idle force-clears, 2 premature-disconnect, "
            "3 stall fires; ~109 silent => stale-gen early-return (grep counts, frontend.log).",
        )
        errors = validate_artifact_data("evaluate", data, profile="full")
        m2 = [e for e in _ug_errors(errors) if "hedge" in e.lower()]
        assert m2 == [], f"hedge resolved by observation must pass M2: {errors}"


# ---------------------------------------------------------------------------
# Happy path — observation-backed understanding passes cleanly
# ---------------------------------------------------------------------------
class TestUnderstandingGatePasses:
    @pytest.mark.parametrize("profile", ["full", "bugfix", "goal"])
    def test_valid_understanding_passes(self, profile):
        data = _eval("existing-feature", _GOOD_CLAIM, _GOOD_EVIDENCE)
        # bugfix profile: also satisfy the REPRO alias requirement via the same evidence
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _ug_errors(errors) == [], f"{profile}: valid understanding must pass: {errors}"

    def test_self_dogfood_evaluation_passes(self):
        """AC10: this very run's evaluation must pass the new gate."""
        data = _eval(
            "existing-feature",
            "Today the diagnosis-before-build gate exists only for bug-class work: "
            "pipeline_validator.py:528 scope-gates REPRO to scope==bugfix and evaluate.md:38 "
            "says BUG-CLASS ONLY, so a wrong understanding of an existing feature passes uncaught.",
            "Code-trace this session: pipeline_validator.py:519-557 (REPRO gate, _is_bug), "
            "evaluate.md:38-86 (Diagnostic-Challenge Gate BUG-CLASS ONLY), validate_artifact_data "
            "invoked at publish time (artifact_cli.py:183).",
        )
        errors = validate_artifact_data("evaluate", data, profile="goal")
        assert _ug_errors(errors) == [], f"self-dogfood eval must pass: {errors}"


# ---------------------------------------------------------------------------
# Relaxed profiles — no false-block when the block is absent
# ---------------------------------------------------------------------------
class TestRelaxedProfilesNotFalseBlocked:
    @pytest.mark.parametrize("profile", ["trivial", "docs", "research"])
    def test_missing_block_not_blocked_on_relaxed(self, profile):
        data = {"recommendation": "GO", "scope": "standard", "acceptance_criteria": ["x"]}
        errors = validate_artifact_data("evaluate", data, profile=profile)
        assert _ug_errors(errors) == [], f"{profile}: missing block must NOT block: {errors}"

    def test_relaxed_still_scans_present_block(self):
        """If a relaxed profile DOES supply an understanding block, M1/M2 still scan it
        (cheap, no skeptic) — a solution-language claim is still caught."""
        data = _eval("docs", "I will rewrite the section to fix the doc.", _GOOD_EVIDENCE)
        errors = validate_artifact_data("evaluate", data, profile="docs")
        assert _ug_errors(errors), f"relaxed profile must still scan a present block: {errors}"


# ---------------------------------------------------------------------------
# Back-compat — bug-class REPRO path preserved via observation_evidence alias
# ---------------------------------------------------------------------------
class TestReproBackCompat:
    def test_observation_evidence_alias_satisfies_understanding(self):
        """A bug-class eval using the OLD observation_evidence field (no understanding
        block) must still pass the understanding gate — the alias feeds it."""
        data = {
            "recommendation": "GO",
            "scope": "bugfix",
            "acceptance_criteria": ["x"],
            "observation_evidence": (
                "ps shows pid 33855 alive+sleeping RSS 429MB; mem pressure=ok 57%; "
                "every exit -9 preceded by our own force_kill_tree (grep)."
            ),
        }
        errors = validate_artifact_data("evaluate", data, profile="bugfix")
        # Neither the REPRO marker nor the UG marker should fire — evidence present.
        assert _ug_errors(errors) == [], f"alias must satisfy UG: {errors}"
        assert [e for e in errors if "REPRO gate" in e] == [], f"alias must satisfy REPRO: {errors}"

    def test_bugfix_without_any_evidence_blocks_both_markers_ok(self):
        """A bug-class eval with NO evidence at all still blocks (either marker is fine)."""
        data = {"recommendation": "GO", "scope": "bugfix", "acceptance_criteria": ["x"]}
        errors = validate_artifact_data("evaluate", data, profile="bugfix")
        assert errors, f"bug-class with no evidence must block: {errors}"
