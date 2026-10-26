"""Tests for eval_runner.load_merged_golden_set: the product set + the workspace's
private set, through core.golden_set_store.load_merged (the one reader).

The runner only READS the golden set (write_run targets EvalHistory), so these
tests cover merging and validation, not writes.
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_runner import load_merged_golden_set  # noqa: E402


def _case(cid: str, affected: list[str]) -> dict:
    return {
        "id": cid,
        "evaluators": ["file_contains"],
        "affected_by": affected,
        "category": "compliance",
    }


def _write(path: Path, cases: list[dict], version: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"version": version, "cases": cases}))


def _layout(tmp_path):
    product = tmp_path / "product.yaml"
    ws = tmp_path / "ws"
    private = ws / "Eval" / "golden_set.private.yaml"
    return product, ws, private


def test_merges_product_and_private(tmp_path):
    product, ws, private = _layout(tmp_path)
    _write(product, [_case("PROD001", ["backend/x.py"])])
    _write(private, [_case("PRIV001", ["STEERING.R1"])])

    data = load_merged_golden_set(ws, product_path=product)
    assert {c["id"]: c["_origin"] for c in data["cases"]} == {
        "PROD001": "product", "PRIV001": "private"}


def test_private_absent_loads_product_only(tmp_path):
    product, ws, _ = _layout(tmp_path)
    _write(product, [_case("PROD001", ["backend/x.py"])])
    data = load_merged_golden_set(ws, product_path=product)
    assert {c["id"] for c in data["cases"]} == {"PROD001"}


def test_private_reusing_a_product_id_is_skipped(tmp_path):
    product, ws, private = _layout(tmp_path)
    _write(product, [_case("DUP", ["backend/x.py"])])
    _write(private, [_case("DUP", ["STEERING.R1"])])
    data = load_merged_golden_set(ws, product_path=product)
    assert [c["_origin"] for c in data["cases"]] == ["product"]
    assert data["shadowed_private_ids"] == ["DUP"]


def test_repeated_id_inside_one_file_fails_loud(tmp_path):
    product, ws, private = _layout(tmp_path)
    _write(product, [_case("PROD001", ["backend/x.py"])])
    _write(private, [_case("X", []), _case("X", [])])
    with pytest.raises(ValueError, match="repeated"):
        load_merged_golden_set(ws, product_path=product)


def test_missing_product_set_fails_loud(tmp_path):
    product, ws, _ = _layout(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_merged_golden_set(ws, product_path=product)


def test_schema_validation_still_applies_to_private(tmp_path):
    product, ws, private = _layout(tmp_path)
    _write(product, [_case("PROD001", ["backend/x.py"])])
    # private case missing affected_by — must still trip the schema assert
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text(yaml.dump({"version": 2, "cases": [{"id": "PRIV001", "evaluators": ["file_contains"]}]}))
    with pytest.raises(AssertionError, match="affected_by"):
        load_merged_golden_set(ws, product_path=product)
