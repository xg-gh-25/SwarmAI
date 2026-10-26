"""Case admission (design 2026-10-26-eval-as-product §3.2): every new case says what
it protects (purpose), and the golden set stays at or below its total cap."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.eval_service import EvalService  # noqa: E402
from core.golden_set_store import TOTAL_CAP  # noqa: E402
from scripts.golden_case_validator import gate_purpose, validate_case  # noqa: E402
from tests._eval_golden_fixture import install_golden_fixture  # noqa: E402

PURPOSE = {"en": "Checks one thing; if it breaks the user loses it.", "zh": "检查一件事；坏了用户会受影响。"}


def _case(cid, **kw):
    return {"id": cid, "category": "decision", "dimension": "judgment_quality",
            "evaluators": ["goal_success"], "affected_by": [], "eval_method": "llm",
            "title": cid, "purpose": PURPOSE, **kw}


@pytest.mark.parametrize("purpose, both, ok", [
    (None, False, False),
    ("a string", False, False),
    ({}, False, False),
    ({"en": "  "}, False, False),
    ({"en": "Protects X."}, False, True),
    ({"zh": "保护 X。"}, False, True),
    ({"en": "Protects X."}, True, False),
    (PURPOSE, True, True),
    ({"en": "x" * 201, "zh": "y"}, False, False),
])
def test_gate_purpose(purpose, both, ok):
    case = {"id": "C"} if purpose is None else {"id": "C", "purpose": purpose}
    assert gate_purpose(case, require_both=both)[0] is ok


def test_validator_needs_purpose_for_new_cases_only():
    no_purpose = {k: v for k, v in _case("N", assertions=["x"]).items() if k != "purpose"}
    assert validate_case(no_purpose, [], for_public=False)[1]["purpose"][0] is False
    assert "purpose" not in validate_case(no_purpose, [], for_public=False, grandfathered=True)[1]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    return tmp_path


def test_add_case_requires_purpose(ws, monkeypatch):
    install_golden_fixture(monkeypatch, ws, {"version": 2, "cases": []})
    svc = EvalService(workspace_root=ws)
    with pytest.raises(ValueError, match="purpose"):
        svc.add_case({k: v for k, v in _case("NOPURPOSE").items() if k != "purpose"})
    assert svc.add_case(_case("WITHPURPOSE"))["id"] == "WITHPURPOSE"


def test_add_case_stops_at_the_total_cap(ws, monkeypatch):
    full = [_case(f"C{i:03d}") for i in range(TOTAL_CAP - 1)]
    archived = [_case("OLD", tier="archived")]
    install_golden_fixture(monkeypatch, ws, {"version": 2, "cases": full + archived},
                           product_cases=[_case("PRODUCT1")])
    svc = EvalService(workspace_root=ws)  # product 1 + private 79 live = 80
    with pytest.raises(ValueError, match="Retire a case"):
        svc.add_case(_case("ONE_TOO_MANY"))
    svc.delete_case("C000")  # archiving one frees a slot
    assert svc.add_case(_case("FITS"))["id"] == "FITS"


def test_api_create_passes_purpose_through(ws, monkeypatch):
    """The request model must carry purpose, or every case added from the UI is rejected."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.eval as eval_router

    install_golden_fixture(monkeypatch, ws, {"version": 2, "cases": []})
    svc = EvalService(workspace_root=ws)
    monkeypatch.setattr(eval_router, "get_eval_service", lambda: svc)
    app = FastAPI()
    app.include_router(eval_router.router, prefix="/api/eval")
    client = TestClient(app)
    body = {k: v for k, v in _case("UI_CASE").items() if k not in ("eval_method",)}
    assert client.post("/api/eval/golden-set", json=body).status_code == 200
    assert next(c for c in svc._cases if c["id"] == "UI_CASE")["purpose"] == PURPOSE
    body.pop("purpose")
    body["id"] = "UI_NOPURPOSE"
    assert client.post("/api/eval/golden-set", json=body).status_code == 400
