"""Tests for eval_runner.load_golden_set merging public + private golden sets.

Decouple v3 (run_69b1c644): load_golden_set merges golden_set.yaml (public) with
a sibling golden_set.private.yaml (private, optional) so private instance cases
actually RUN in the eval runner — while the runner only ever READS (write_run
targets EvalHistory, not golden_set, so there is no leak risk here).
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_runner import load_golden_set  # noqa: E402


def _case(cid: str, affected: list[str]) -> dict:
    return {
        "id": cid,
        "evaluators": ["file_contains"],
        "affected_by": affected,
        "category": "compliance",
    }


def _write(path: Path, cases: list[dict], version: int = 2) -> None:
    path.write_text(yaml.dump({"version": version, "cases": cases}))


def test_merges_public_and_private(tmp_path):
    pub = tmp_path / "golden_set.yaml"
    priv = tmp_path / "golden_set.private.yaml"
    _write(pub, [_case("PUB001", ["backend/x.py"])])
    _write(priv, [_case("PRIV001", ["STEERING.R1"])])

    data = load_golden_set(pub)
    ids = {c["id"] for c in data["cases"]}
    assert ids == {"PUB001", "PRIV001"}


def test_private_absent_loads_public_only(tmp_path):
    pub = tmp_path / "golden_set.yaml"
    _write(pub, [_case("PUB001", ["backend/x.py"])])
    # no private sibling — clone-safe
    data = load_golden_set(pub)
    assert {c["id"] for c in data["cases"]} == {"PUB001"}


def test_collision_across_files_fails_loud(tmp_path):
    pub = tmp_path / "golden_set.yaml"
    priv = tmp_path / "golden_set.private.yaml"
    _write(pub, [_case("DUP", ["backend/x.py"])])
    _write(priv, [_case("DUP", ["STEERING.R1"])])
    with pytest.raises((AssertionError, ValueError), match="(?i)collision|duplicate"):
        load_golden_set(pub)


def test_schema_validation_still_applies_to_private(tmp_path):
    pub = tmp_path / "golden_set.yaml"
    priv = tmp_path / "golden_set.private.yaml"
    _write(pub, [_case("PUB001", ["backend/x.py"])])
    # private case missing affected_by — must still trip the schema assert
    priv.write_text(yaml.dump({"version": 2, "cases": [{"id": "PRIV001", "evaluators": ["file_contains"]}]}))
    with pytest.raises(AssertionError, match="affected_by"):
        load_golden_set(pub)
