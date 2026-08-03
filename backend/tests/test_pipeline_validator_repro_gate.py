"""REPRO gate — diagnosis-before-build for bug-class evaluations (run_688b6487).

A bug-fix evaluation MUST carry OBSERVATION evidence (ps / log-signal counts /
live gauges / repro), not an inferred-from-reading-code root cause. This session
twice shipped a confident-but-wrong root cause to BUILD; both were framing errors
an evidence requirement at EVALUATE would have caught.

These tests FORCE EXECUTION of the gate (mock the artifact, assert the
block/pass), per STEERING #11 (recovery/guard paths need a test that runs them).
"""


from scripts.pipeline_validator import validate_artifact_data


def _bug_eval(**overrides):
    base = {
        "recommendation": "GO",
        "scope": "bugfix",
        "acceptance_criteria": ["x"],
    }
    base.update(overrides)
    return base


class TestReproGateBlocks:
    """Bug-class evaluation WITHOUT observation_evidence → BLOCK."""

    def test_bugfix_without_observation_evidence_blocks(self):
        errors = validate_artifact_data("evaluate", _bug_eval(), profile="bugfix")
        repro = [e for e in errors if "REPRO gate" in e or "observation_evidence" in e]
        assert len(repro) >= 1, f"Expected REPRO block, got: {errors}"

    def test_bug_class_marker_without_evidence_blocks(self):
        # Even when scope is not literally 'bugfix', an explicit bug_class marker trips it.
        errors = validate_artifact_data(
            "evaluate", _bug_eval(scope="standard", bug_class=True), profile="full",
        )
        repro = [e for e in errors if "REPRO gate" in e]
        assert len(repro) >= 1, f"Expected REPRO block via bug_class, got: {errors}"

    def test_empty_string_evidence_blocks(self):
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence="   "), profile="bugfix",
        )
        repro = [e for e in errors if "REPRO gate" in e]
        assert len(repro) >= 1, f"whitespace evidence must not satisfy: {errors}"

    def test_too_short_evidence_blocks(self):
        # A token-length floor prevents a vacuous "ok" from satisfying the gate.
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence="ps ok"), profile="bugfix",
        )
        repro = [e for e in errors if "REPRO gate" in e]
        assert len(repro) >= 1, f"sub-20-char evidence must not satisfy: {errors}"


class TestReproGatePasses:
    """Bug-class evaluation WITH real observation evidence → no REPRO error."""

    def test_bugfix_with_observation_evidence_passes(self):
        errors = validate_artifact_data("evaluate", _bug_eval(
            observation_evidence=(
                "3-signal log triage: 114 idle force-clears, only 2 premature-disconnect "
                "+ 3 stall-timer fires; ~109 silent => stale-gen early-return in onComplete "
                "(grep counts, frontend.log)."
            ),
        ), profile="bugfix")
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"valid evidence must pass REPRO gate, got: {errors}"

    def test_structured_evidence_object_passes(self):
        # Non-string evidence (e.g. a dict of observations) is accepted as present.
        errors = validate_artifact_data("evaluate", _bug_eval(
            observation_evidence={"ps": "pid 33855 alive sleeping RSS 429MB",
                                  "gauge": "mem pressure=ok 57%"},
        ), profile="bugfix")
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"structured evidence must pass, got: {errors}"


class TestReproGateDoesNotFalseBlock:
    """DoD3b: the gate must NOT fire for non-bug work (goal/standard/research)."""

    def test_goal_eval_not_blocked(self):
        errors = validate_artifact_data("evaluate", {
            "recommendation": "GO", "scope": "goal", "acceptance_criteria": ["x"],
        }, profile="goal")
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"goal scope must not trip REPRO gate, got: {errors}"

    def test_standard_feature_eval_not_blocked(self):
        errors = validate_artifact_data("evaluate", {
            "recommendation": "GO", "scope": "standard", "acceptance_criteria": ["x"],
        }, profile="full")
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"standard scope must not trip REPRO gate, got: {errors}"

    def test_research_eval_not_blocked(self):
        errors = validate_artifact_data("evaluate", {
            "recommendation": "GO", "scope": "research-only", "acceptance_criteria": ["x"],
        }, profile="research")
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"research scope must not trip REPRO gate, got: {errors}"

    def test_docs_profile_with_bugfix_scope_not_blocked(self):
        """Relaxed profile (docs) + scope=bugfix → REPRO relaxed, matching the
        depth-check relaxation pattern (adversarial LOW)."""
        errors = validate_artifact_data(
            "evaluate", _bug_eval(), profile="docs",
        )
        repro = [e for e in errors if "REPRO gate" in e]
        assert repro == [], f"docs profile must relax REPRO gate, got: {errors}"


class TestReproGateEvidenceTypes:
    """Pin the exact accept/reject of non-string evidence types (adversarial MED)."""

    def test_bare_true_blocks(self):
        # observation_evidence:true carries zero information → must NOT satisfy.
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence=True), profile="bugfix",
        )
        assert [e for e in errors if "REPRO gate" in e], "bare True must block"

    def test_empty_list_blocks(self):
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence=[]), profile="bugfix",
        )
        assert [e for e in errors if "REPRO gate" in e], "empty list must block"

    def test_empty_dict_blocks(self):
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence={}), profile="bugfix",
        )
        assert [e for e in errors if "REPRO gate" in e], "empty dict must block"

    def test_nonempty_list_passes(self):
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence=["ps: pid 33855 alive"]),
            profile="bugfix",
        )
        assert [e for e in errors if "REPRO gate" in e] == [], "non-empty list must pass"

    def test_twenty_char_string_passes_by_design(self):
        """CONSCIOUS DECISION (not a gap): the length floor is anti-LAZINESS, not
        anti-fabrication. A 20-char garbage string passes the validator; the
        diagnostic-challenge sub-agent (evaluate.md) is the fabrication backstop.
        Pinned so a future tightening is a deliberate choice, not an accident."""
        errors = validate_artifact_data(
            "evaluate", _bug_eval(observation_evidence="a" * 20), profile="bugfix",
        )
        assert [e for e in errors if "REPRO gate" in e] == [], (
            "20-char string passes by design (length floor only)"
        )
