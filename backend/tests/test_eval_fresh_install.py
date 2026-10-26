"""Eval works on a fresh install (design docs/design/2026-10-26-eval-as-product.md §3.1).

A fresh install has an empty workspace: no Eval/ folder, no project, no source
checkout path, no private cases. The product golden set ships with the backend, so
eval must load it and every product case must pass, with the BVT negatives executed.
Product cases run with a temporary data dir, workspace and HOME, so a run can never
touch the user's real data.
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.golden_set_store import (  # noqa: E402
    PRIVATE_ORIGIN, PRODUCT_ORIGIN, load_merged, product_golden_set_path,
)
from scripts import eval_runner as er  # noqa: E402


@pytest.fixture
def fresh_ws(tmp_path, monkeypatch):
    ws = tmp_path / "SwarmWS"
    ws.mkdir()
    monkeypatch.setenv("SWARMWS", str(ws))
    monkeypatch.setenv("SWARM_WORKSPACE", str(ws))
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path / "data"))
    # The daemon exports this; a terminal or test run starts without it.
    monkeypatch.delenv("SWARM_BACKEND_BIN", raising=False)
    return ws


def test_fresh_workspace_loads_the_product_set(fresh_ws):
    assert not (fresh_ws / "Eval").exists()
    assert er._find_workspace_root() == fresh_ws
    gs = er.load_merged_golden_set(fresh_ws)
    assert gs["cases"], "a fresh install must have product cases"
    assert {c["_origin"] for c in gs["cases"]} == {PRODUCT_ORIGIN}


def test_every_product_case_passes_on_a_fresh_install(fresh_ws):
    gs = er.load_merged_golden_set(fresh_ws)
    result = er.run_eval(gs, "fresh-install-test", None, fresh_ws, verify_teeth=True)
    by_id = {r["id"]: r for r in result["cases"]}
    bad = {cid: (r["status"], (r.get("notes") or "")[:160])
           for cid, r in by_id.items() if r["status"] != "passed"}
    assert not bad, bad
    assert set(by_id) == {c["id"] for c in gs["cases"]}


def test_a_run_never_writes_outside_its_temp_dirs(fresh_ws, tmp_path):
    case = {"id": "P", "_origin": PRODUCT_ORIGIN}
    out = er._run_case_command(
        'echo "$SWARM_DATA_DIR|$HOME|$SWARMWS|$SWARM_BACKEND_BIN|$PWD|$SWARM_WORKSPACE"',
        case, fresh_ws, 20).stdout.strip()
    data, home, ws, binp, cwd, ws2 = out.split("|")
    assert ws2 == ws, "SWARM_WORKSPACE (read first by get_swarmws) must match"
    real_home = str(Path.home())
    for p in (data, home, ws, cwd):
        assert p and not p.startswith(real_home + "/.swarm-ai"), p
        assert p != str(fresh_ws)
    assert binp, "SWARM_BACKEND_BIN must be set for the case command"

    private = {"id": "Q", "_origin": PRIVATE_ORIGIN}
    out = er._run_case_command('echo "$SWARMWS|$PWD"', private, fresh_ws, 20).stdout.strip()
    ws, cwd = out.split("|")
    assert ws == str(fresh_ws), "a private case checks the user's own workspace"
    assert cwd != str(fresh_ws), "cwd is never the workspace (py -m would import from it)"


def _write_private(ws: Path, cases: list[dict]) -> Path:
    p = ws / "Eval" / "golden_set.private.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump({"version": 2, "cases": cases}), encoding="utf-8")
    return p


def test_private_case_with_a_product_id_is_skipped_not_fatal(fresh_ws):
    product_id = load_merged(fresh_ws)["cases"][0]["id"]
    _write_private(fresh_ws, [
        {"id": product_id, "evaluators": ["canary_pass"], "affected_by": []},
        {"id": "MY_CASE", "evaluators": ["canary_pass"], "affected_by": []},
    ])
    gs = load_merged(fresh_ws)
    assert gs["shadowed_private_ids"] == [product_id]
    origins = {c["id"]: c["_origin"] for c in gs["cases"]}
    assert origins[product_id] == PRODUCT_ORIGIN
    assert origins["MY_CASE"] == PRIVATE_ORIGIN


def test_service_keeps_product_cases_read_only(fresh_ws):
    from core.eval_service import EvalService

    product_file = product_golden_set_path()
    before = product_file.read_bytes()
    svc = EvalService(workspace_root=fresh_ws)
    product_id = next(c["id"] for c in svc._cases if c["_origin"] == PRODUCT_ORIGIN)

    with pytest.raises(ValueError, match="read-only"):
        svc.update_case(product_id, {"title": "changed"})
    with pytest.raises(ValueError, match="read-only"):
        svc.delete_case(product_id)
    out = svc.hard_delete_cases([product_id])
    assert (out["refused"], out["deleted"]) == ([product_id], [])
    assert any(c["id"] == product_id for c in svc._cases)

    svc.add_case({"id": "MY_LLM_CASE", "category": "decision", "dimension": "judgment_quality",
                  "evaluators": ["goal_success"], "affected_by": [], "eval_method": "llm",
                  "title": "mine"})
    private = yaml.safe_load((fresh_ws / "Eval" / "golden_set.private.yaml").read_text())
    assert [c["id"] for c in private["cases"]] == ["MY_LLM_CASE"]
    assert "_origin" not in private["cases"][0]
    assert product_file.read_bytes() == before


def test_promotion_never_changes_a_product_case(fresh_ws, monkeypatch):
    from core.eval_service import EvalService

    svc = EvalService(workspace_root=fresh_ws)
    product = next(c for c in svc._cases if c["_origin"] == PRODUCT_ORIGIN)
    product["tier"] = "active"  # a promotable tier, so only the product guard can stop it
    monkeypatch.setattr(svc, "_count_consecutive_passes", lambda cid: 99)
    assert product["id"] not in svc.promote_stable_cases()
    assert product["tier"] == "active"


def test_service_without_a_workspace_writes_nothing(tmp_path, monkeypatch):
    from core.eval_service import EvalService

    monkeypatch.setenv("HOME", str(tmp_path))
    svc = EvalService(workspace_root=None)
    with pytest.raises(RuntimeError, match="no workspace"):
        svc.add_case({"id": "X", "category": "decision", "dimension": "judgment_quality",
                      "evaluators": ["goal_success"], "affected_by": [], "eval_method": "llm"})
    assert not any(tmp_path.iterdir()), "the no-workspace service created files"
