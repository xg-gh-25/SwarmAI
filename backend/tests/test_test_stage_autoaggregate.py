"""Tests for _maybe_autoaggregate_test_artifact — the test-stage auto-aggregate
that mirrors the deliver auto-aggregate, closing the cross_boundary_e2e gate
asymmetry (a test stage's inline cross_boundary_e2e recorded via --stage-json was
invisible to the completion gate, which reads only published artifacts).

Methodology: drive the pure helper directly with a mock registry. Covers:
- AC1: inline cross_boundary_e2e on a completed test stage → artifact published + id backfilled
- AC2: non-regression — an existing artifact_id is never overwritten
- AC3: non-regression — no cross_boundary_e2e (or run falsey) → no publish (never fabricates E2E)
- AC4: profile gating (only full/bugfix) + schema-valid shape (passed + layers.ac_driven)
Mutation notes inline: removing the `artifact_id` guard breaks AC2; removing the
cross_boundary_e2e check breaks AC3.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from artifact_cli import _maybe_autoaggregate_test_artifact


class _MockReg:
    """Records publish() calls and returns a deterministic artifact id."""

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def publish(self, *, project, artifact_type, data, producer, summary,
                topic="", run_id=None):
        if self.fail:
            raise RuntimeError("simulated publish failure")
        self.calls.append({
            "project": project, "artifact_type": artifact_type,
            "data": data, "producer": producer, "summary": summary,
            "topic": topic, "run_id": run_id,
        })
        return "art_mock_test"


def _run_state_with_test(stage_overrides=None):
    base = {
        "stage": "test",
        "status": "completed",
        "cross_boundary_e2e": {"run": True, "test_file": "t.py::x", "mutation": "revert -> RED"},
    }
    if stage_overrides is not None:
        base.update(stage_overrides)
    return {"stages": [{"stage": "evaluate", "status": "completed"}, base]}


# ── AC1: happy path — inline E2E promoted to artifact ────────────────────────
def test_ac1_promotes_inline_e2e_to_artifact():
    rs = _run_state_with_test()
    reg = _MockReg()
    art_id = _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
    assert art_id == "art_mock_test"
    # backfilled onto the test stage record
    test_rec = next(s for s in rs["stages"] if s.get("stage") == "test")
    assert test_rec["artifact_id"] == "art_mock_test"
    # published as a test_report carrying the E2E + a schema-valid shape (AC4)
    assert len(reg.calls) == 1
    pub = reg.calls[0]
    assert pub["artifact_type"] == "test_report"
    assert pub["run_id"] == "run_x"
    assert pub["summary"]  # non-empty summary passed (real signature requires it)
    assert pub["topic"] == ""  # auto-aggregate must not set a topic
    assert pub["data"]["cross_boundary_e2e"]["run"] is True
    assert pub["data"]["auto_aggregated"] is True  # provenance flag
    # ac_driven inner structure (test-schema required nested field), not just presence
    assert pub["data"]["layers"]["ac_driven"]["run"] is True


def test_ac1_passed_field_honored_not_defaulted():
    # The `passed` field must reflect the stage record, not a hardcoded True. Three
    # cases prove the default is only a fallback (mutation: flip default → case C RED
    # if flipped to False; cases A/B guard against hardcoding either constant).
    for given, expect in [({"passed": True}, True), ({"passed": False}, False)]:
        rs = _run_state_with_test(given)
        reg = _MockReg()
        _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
        assert reg.calls[0]["data"]["passed"] is expect
    # missing → defaults True
    rs = _run_state_with_test()
    reg = _MockReg()
    _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
    assert reg.calls[0]["data"]["passed"] is True


def test_ac1_works_for_full_profile_too():
    rs = _run_state_with_test()
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "full", reg, "P", "run_x") == "art_mock_test"
    assert len(reg.calls) == 1  # actually published, not just returned
    assert reg.calls[0]["artifact_type"] == "test_report"


# ── AC2: non-regression — never overwrite an existing artifact_id ────────────
def test_ac2_existing_artifact_id_not_overwritten():
    # MUTATION: if the `test_rec.get("artifact_id")` guard is removed, this RED.
    rs = _run_state_with_test({"artifact_id": "art_real_published"})
    reg = _MockReg()
    art_id = _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
    assert art_id is None
    assert reg.calls == []  # no publish
    test_rec = next(s for s in rs["stages"] if s.get("stage") == "test")
    assert test_rec["artifact_id"] == "art_real_published"  # untouched


# ── AC3: non-regression — never fabricate E2E ────────────────────────────────
def test_ac3_no_cross_boundary_e2e_no_publish():
    # MUTATION: if the cross_boundary_e2e presence check is removed, this RED.
    rs = _run_state_with_test({"cross_boundary_e2e": None})
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") is None
    assert reg.calls == []


def test_ac3_e2e_run_falsey_no_publish():
    rs = _run_state_with_test({"cross_boundary_e2e": {"run": False}})
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") is None
    assert reg.calls == []


def test_ac3_test_stage_not_completed_no_publish():
    rs = _run_state_with_test({"status": "in_progress"})
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") is None
    assert reg.calls == []


def test_no_test_stage_returns_none():
    # MUTATION: removing the `not test_rec` guard would crash on None.get() here.
    rs = {"stages": [{"stage": "evaluate", "status": "completed"}]}
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") is None
    assert reg.calls == []


def test_status_done_also_triggers():
    # The code allows status in ("completed", "done"); "done" must also fire.
    # MUTATION: `in ("completed","done")` → `== "completed"` makes this RED.
    rs = _run_state_with_test({"status": "done"})
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") == "art_mock_test"
    assert len(reg.calls) == 1


def test_stage_matched_by_name_field_fallback():
    # The lookup is s.get("stage", s.get("name","")) — a record keyed by `name` must match.
    rs = {"stages": [{"name": "test", "status": "completed",
                      "layers": {"ac_driven": {"run": True, "pass": 1}},
                      "cross_boundary_e2e": {"run": True}}]}
    reg = _MockReg()
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") == "art_mock_test"


# ── AC4: profile gating ──────────────────────────────────────────────────────
def test_ac4_non_strict_profile_no_publish():
    for prof in ("trivial", "docs", "research", "goal"):
        rs = _run_state_with_test()
        reg = _MockReg()
        assert _maybe_autoaggregate_test_artifact(rs, prof, reg, "P", "run_x") is None
        assert reg.calls == []


# ── Robustness: publish failure degrades, never crashes ──────────────────────
def test_publish_failure_degrades_to_none():
    rs = _run_state_with_test()
    reg = _MockReg(fail=True)
    # must not raise
    assert _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x") is None
    test_rec = next(s for s in rs["stages"] if s.get("stage") == "test")
    assert "artifact_id" not in test_rec  # not backfilled on failure


def test_missing_layers_synthesizes_schema_valid_shape():
    # A test stage with E2E but no layers → helper must still emit layers.ac_driven.
    rs = _run_state_with_test({"tests_new": 5})
    reg = _MockReg()
    _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
    layers = reg.calls[0]["data"]["layers"]
    assert "ac_driven" in layers and layers["ac_driven"]["run"] is True


def test_non_dict_layers_replaced_with_schema_valid_shape():
    # MUTATION guard: if the `not isinstance(_layers, dict)` branch is removed, a
    # non-dict layers (e.g. a stray int/str) would flow into the artifact and the
    # completion gate would publish a schema-INVALID test_report (layers.ac_driven
    # missing). The helper must always emit a dict with ac_driven.
    for bad_layers in (0, "n/a", [1, 2], True):
        rs = _run_state_with_test({"layers": bad_layers})
        reg = _MockReg()
        _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
        layers = reg.calls[0]["data"]["layers"]
        assert isinstance(layers, dict) and "ac_driven" in layers, (
            f"non-dict layers {bad_layers!r} not normalized: {layers!r}"
        )
        assert layers["ac_driven"]["run"] is True


def test_layers_dict_without_ac_driven_replaced():
    # layers is a dict but missing the required ac_driven nested key → must be replaced.
    rs = _run_state_with_test({"layers": {"import_smoke": {"run": True}}})
    reg = _MockReg()
    _maybe_autoaggregate_test_artifact(rs, "bugfix", reg, "P", "run_x")
    layers = reg.calls[0]["data"]["layers"]
    assert "ac_driven" in layers  # schema-required nested field synthesized
