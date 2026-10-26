"""Managing the user's own golden cases (design 2026-10-26-eval-as-product §8 item 3).

Add, edit and try all run the same admission: every validator gate, plus a trial run
of a quick check's command and negative command. An edit re-stamps the case so it
stays validated. Delete removes the case from the file; nothing is archived.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eval_service import EvalService  # noqa: E402
from scripts.golden_case_validator import compute_case_stamp  # noqa: E402
from tests._eval_golden_fixture import install_golden_fixture  # noqa: E402

PURPOSE = {"en": "Checks one thing; if it breaks the user loses it."}


def _quick(cid, *, neg_marker="QC_TEETH", neg_prints="TEETH", **kw):
    """A quick check whose commands print their markers without containing them."""
    return {"id": cid, "category": "recall", "dimension": "cognition", "title": cid,
            "purpose": PURPOSE, "evaluators": ["canary_pass"], "affected_by": ["backend/core/memory_index.py"],
            "verification": {
                "command": "printf 'QC_%s\\n' OK",
                "expected_contains": "QC_OK",
                "negative_command": f"printf 'QC_%s\\n' {neg_prints}",
                "negative_expected_contains": neg_marker,
            }, **kw}


def _offline(cid, **kw):
    return {"id": cid, "category": "decision", "dimension": "quality", "title": cid,
            "purpose": PURPOSE, "evaluators": ["trajectory_capture"], "affected_by": ["backend/context/AGENT.md"],
            "scenario": {"prompt": "Which port does the Demo API listen on?"},
            "expected_trajectory": ["Grep|Read"],
            "decision_rubric": "PASS only if the answer names port 8080. FAIL if it guesses.",
            **kw}


@pytest.fixture
def svc(tmp_path, monkeypatch):
    product_qc = _quick("PRODUCT_QC")
    product_qc["verification"] = {**product_qc["verification"], "command": "printf 'PQ_%s\\n' OK"}
    product = [{**product_qc, "_origin": "product"}]
    install_golden_fixture(monkeypatch, tmp_path, {"version": 2, "cases": []},
                           product_cases=product)
    return EvalService(workspace_root=tmp_path)


def _private_ids(ws):
    data = yaml.safe_load((ws / "Eval" / "golden_set.private.yaml").read_text()) or {}
    return [c["id"] for c in data.get("cases") or []]


# ── add: every gate, then the trial run ─────────────────────────────────────────

def test_add_quick_check_runs_the_trial_and_stamps(svc):
    case = svc.add_case(_quick("QC1"))
    assert case["eval_method"] == "programmatic" and case["tier"] == "active"
    assert case["validated_by_4gate"] == compute_case_stamp(case)


@pytest.mark.parametrize("kw, needle", [
    ({"neg_prints": "OK"}, "trial run failed"),       # negative still prints the pass marker
    ({"neg_prints": "NOTHING"}, "trial run failed"),  # negative never prints its own marker
    ({"neg_marker": ""}, "negative_expected_contains"),
])
def test_add_quick_check_refuses_a_negative_that_proves_nothing(svc, tmp_path, kw, needle):
    with pytest.raises(ValueError, match=needle):
        svc.add_case(_quick("QC_BAD", **kw))
    assert svc.get_case_detail("QC_BAD") is None
    assert "QC_BAD" not in _private_ids(tmp_path)


def test_add_refuses_a_dimension_outside_the_three_with_cases(svc):
    with pytest.raises(ValueError, match="dimension"):
        svc.add_case(_offline("OFF_DIM", dimension="judgment_quality"))
    with pytest.raises(ValueError, match="dimension"):
        svc.add_case(_offline("OFF_COST", dimension="cost"))


@pytest.mark.parametrize("change, needle", [
    ({"scenario": {"prompt": "  "}, "eval_method": "behavior"}, "scenario.prompt"),
    ({"expected_trajectory": []}, "expected_trajectory"),
    ({"decision_rubric": "PASS only if it names port 8080."}, "when it fails"),
])
def test_add_offline_case_needs_prompt_steps_and_a_two_sided_rubric(svc, change, needle):
    with pytest.raises(ValueError, match=needle):
        svc.add_case(_offline("OFF_BAD", **change))


def test_add_offline_case_derives_method_and_tier(svc, tmp_path):
    case = svc.add_case(_offline("OFF1"))
    assert case["eval_method"] == "behavior" and case["tier"] == "behavior"
    assert case["validated_by_4gate"] == compute_case_stamp(case)
    assert "OFF1" in _private_ids(tmp_path)


def test_add_drops_a_product_origin_sent_by_the_caller(svc):
    case = svc.add_case({**_offline("OFF_SNEAK"), "_origin": "product"})
    assert case.get("_origin") != "product"
    svc.delete_case("OFF_SNEAK")  # still the user's own, so it can be deleted


# ── edit: same admission, re-stamped ────────────────────────────────────────────

def test_update_restamps_the_edited_case(svc, tmp_path):
    before = svc.add_case(_quick("QC2"))["validated_by_4gate"]
    after = svc.update_case("QC2", {"title": "renamed"})
    assert after["validated_by_4gate"] != before
    assert after["validated_by_4gate"] == compute_case_stamp(after)
    reloaded = next(c for c in EvalService(workspace_root=tmp_path)._cases if c["id"] == "QC2")
    assert reloaded["title"] == "renamed"
    assert reloaded["validated_by_4gate"] == compute_case_stamp(reloaded)


def test_update_runs_the_trial_and_leaves_the_case_unchanged_on_failure(svc):
    good = dict(svc.add_case(_quick("QC3")))
    bad = {**good["verification"], "negative_command": "printf 'QC_%s\\n' OK"}
    with pytest.raises(ValueError, match="trial run failed"):
        svc.update_case("QC3", {"verification": bad})
    now = svc.get_case_detail("QC3")
    assert now["verification"] == good["verification"]
    assert now["validated_by_4gate"] == good["validated_by_4gate"]


def test_update_refuses_a_product_case(svc):
    with pytest.raises(ValueError, match="read-only"):
        svc.update_case("PRODUCT_QC", {"title": "x"})


# ── delete: gone from the file, no archive ──────────────────────────────────────

def test_delete_removes_the_case_from_the_file(svc, tmp_path):
    svc.add_case(_offline("OFF_DEL"))
    assert svc.delete_case("OFF_DEL") == {"id": "OFF_DEL", "deleted": True}
    assert "OFF_DEL" not in _private_ids(tmp_path)
    assert EvalService(workspace_root=tmp_path).get_case_detail("OFF_DEL") is None


def test_delete_refuses_a_product_case_and_reports_a_missing_one(svc):
    with pytest.raises(ValueError, match="read-only"):
        svc.delete_case("PRODUCT_QC")
    with pytest.raises(ValueError, match="not found"):
        svc.delete_case("NOPE")


# ── try: the same checks, nothing written ───────────────────────────────────────

def test_try_reports_every_check_and_writes_nothing(svc, tmp_path):
    ok = svc.try_case(_quick("QC_TRY"))
    assert ok["ok"] and {c["name"] for c in ok["checks"]} >= {"schema", "dimension", "trial"}
    assert ok["trial"]["status"] == "passed"
    bad = svc.try_case(_offline("OFF_TRY", dimension="cost"))
    assert not bad["ok"]
    assert [c["name"] for c in bad["checks"] if not c["ok"]] == ["dimension"]
    assert svc.get_case_detail("QC_TRY") is None and "QC_TRY" not in _private_ids(tmp_path)


def test_api_try_add_and_delete(svc, monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.eval as eval_router

    monkeypatch.setattr(eval_router, "get_eval_service", lambda: svc)
    app = FastAPI()
    app.include_router(eval_router.router, prefix="/api/eval")
    client = TestClient(app)
    body = _offline("OFF_API")
    r = client.post("/api/eval/golden-set/try", json=body)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert "OFF_API" not in _private_ids(tmp_path)
    r = client.post("/api/eval/golden-set", json=body)
    assert r.status_code == 200 and r.json()["case"]["decision_rubric"] == body["decision_rubric"]
    r = client.put("/api/eval/golden-set/OFF_API", json={"dimension": "cost"})
    assert r.status_code == 400
    assert client.put("/api/eval/golden-set/NOPE", json={"title": "x"}).status_code == 404
    r = client.delete("/api/eval/golden-set/OFF_API")
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert "OFF_API" not in _private_ids(tmp_path)


def test_private_detail_returns_what_the_form_edits(svc):
    """The user's own case reads back with the fields the edit form shows; a field
    the form does not know about stays out (the allowlist fails closed)."""
    svc.add_case({**_quick("QC_DETAIL"), "source": "STEERING R1"})
    d = svc.get_case_detail("QC_DETAIL")
    for key in ("purpose", "verification", "tier", "validated_by_4gate"):
        assert key in d, key
    assert "source" not in d
    svc.add_case(_offline("OFF_DETAIL"))
    d = svc.get_case_detail("OFF_DETAIL")
    for key in ("scenario", "expected_trajectory", "decision_rubric"):
        assert key in d, key
