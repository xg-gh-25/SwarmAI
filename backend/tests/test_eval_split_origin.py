"""Tests for the product/private golden-set split (design 2026-10-26-eval-as-product §3.1).

The product set ships with the backend and is read-only; the user's own cases
live in <workspace>/Eval/golden_set.private.yaml. Both are merged at load through
core.golden_set_store.load_merged, each case tagged with an in-memory _origin.

Properties under test:
- merge-load tags origin + merges both files (private optional)
- persisting writes ONLY the private file; the product file is never written
- a private case reusing a product id is skipped (product wins), not fatal
- _origin is internal — never serialized to disk
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.eval_service import EvalService  # noqa: E402
from tests._eval_golden_fixture import install_golden_fixture  # noqa: E402


def _mk_case(cid: str, affected: list[str]) -> dict:
    return {
        "id": cid,
        "category": "compliance",
        "dimension": "compliance",
        "level": "session",
        "title": f"case {cid}",
        "source": "test",
        "eval_method": "programmatic",
        "affected_by": affected,
        "evaluators": ["file_contains"],
        "scenario": {"turns": [{"input": "x"}]},
        "verification": {"file": "t.py", "grep": "x"},
    }


@pytest.fixture
def split_workspace(tmp_path, monkeypatch):
    """A product set with one case and a private set with one case."""
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    product, private = install_golden_fixture(
        monkeypatch, tmp_path,
        {"version": 2, "cases": [_mk_case("PRIV001", ["STEERING.R1"])]},
        product_cases=[_mk_case("PROD001", ["backend/scripts/eval_runner.py"])],
    )
    return tmp_path, product, private


def test_merge_load_tags_origin_and_merges_both(split_workspace):
    tmp_path, _, _ = split_workspace
    svc = EvalService(workspace_root=tmp_path)
    by_id = {c["id"]: c for c in svc._cases}
    assert by_id["PROD001"]["_origin"] == "product"
    assert by_id["PRIV001"]["_origin"] == "private"


def test_private_absent_loads_product_only(split_workspace):
    tmp_path, _, private = split_workspace
    private.unlink()
    svc = EvalService(workspace_root=tmp_path)
    assert [c["id"] for c in svc._cases] == ["PROD001"]


def test_persist_never_writes_the_product_file(split_workspace):
    tmp_path, product, private = split_workspace
    before = product.read_bytes()
    svc = EvalService(workspace_root=tmp_path)
    for c in svc._cases:  # even an in-memory change to a product case stays in memory
        c["title"] = "touched"
    svc._persist_golden_set()

    assert product.read_bytes() == before, "product set was written"
    priv_ids = {c["id"] for c in yaml.safe_load(private.read_text())["cases"]}
    assert priv_ids == {"PRIV001"}, "a product case leaked into the private file"


def test_origin_never_serialized(split_workspace):
    tmp_path, _, private = split_workspace
    svc = EvalService(workspace_root=tmp_path)
    svc._persist_golden_set()
    assert "_origin" not in private.read_text()


def test_private_reusing_a_product_id_is_skipped(tmp_path, monkeypatch):
    (tmp_path / "Eval" / "EvalHistory").mkdir(parents=True)
    install_golden_fixture(
        monkeypatch, tmp_path,
        {"version": 2, "cases": [_mk_case("DUP", ["STEERING.R1"]), _mk_case("MINE", [])]},
        product_cases=[_mk_case("DUP", ["backend/x.py"])],
    )
    svc = EvalService(workspace_root=tmp_path)
    by_id = {c["id"]: c for c in svc._cases}
    assert by_id["DUP"]["_origin"] == "product"
    assert by_id["DUP"]["affected_by"] == ["backend/x.py"]
    assert "MINE" in by_id
    assert svc.shadowed_private_ids == ["DUP"]
