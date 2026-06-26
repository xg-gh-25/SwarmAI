"""Tests for eval golden-set public/private split + origin-tagged dual-file write.

Decouple v3 (run_69b1c644): golden_set.yaml (public, tracked) +
golden_set.private.yaml (gitignored, instance cases) are merged at load, each
case tagged with _origin, and written back to its OWN file — so a private case
NEVER leaks into the tracked public file (Gate-1 CRITICAL).

Properties under test:
- merge-load tags origin + merges both files (private optional)
- split-write routes each case back to its origin file (no cross-file leak)
- cross-file id collision fails loud (no silent shadow)
- _origin is internal — never serialized to disk
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.eval_service import EvalService  # noqa: E402


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
def split_workspace(tmp_path):
    """Workspace with BOTH public and private golden-set files."""
    proj = tmp_path / "Eval"
    proj.mkdir(parents=True)
    (proj / "EvalHistory").mkdir()
    # public: a code-affected case
    (proj / "golden_set.yaml").write_text(
        yaml.dump({"version": 2, "cases": [_mk_case("PUB001", ["backend/scripts/eval_runner.py"])]})
    )
    # private: an instance-DDD case
    (proj / "golden_set.private.yaml").write_text(
        yaml.dump({"version": 2, "cases": [_mk_case("PRIV001", ["STEERING.R1"])]})
    )
    return tmp_path, proj


def test_merge_load_tags_origin_and_merges_both(split_workspace):
    tmp_path, _ = split_workspace
    svc = EvalService(workspace_root=tmp_path)
    by_id = {c["id"]: c for c in svc._cases}
    assert "PUB001" in by_id and "PRIV001" in by_id, "both files must merge"
    assert by_id["PUB001"]["_origin"] == "public"
    assert by_id["PRIV001"]["_origin"] == "private"


def test_private_absent_loads_public_only(tmp_path):
    proj = tmp_path / "Eval"
    proj.mkdir(parents=True)
    (proj / "EvalHistory").mkdir()
    (proj / "golden_set.yaml").write_text(
        yaml.dump({"version": 2, "cases": [_mk_case("PUB001", ["backend/x.py"])]})
    )
    # no private file — simulates someone cloning the public repo
    svc = EvalService(workspace_root=tmp_path)
    assert [c["id"] for c in svc._cases] == ["PUB001"]


def test_split_write_no_private_leak_into_public(split_workspace):
    """THE Gate-1 CRITICAL: persisting must NOT write private cases into the
    tracked public file."""
    tmp_path, proj = split_workspace
    svc = EvalService(workspace_root=tmp_path)
    # mutate a public case + trigger a persist (mimics add/auto-seed/promote)
    svc._cases[0]["title"] = "touched"
    svc._persist_golden_set()

    public_disk = yaml.safe_load((proj / "golden_set.yaml").read_text())
    private_disk = yaml.safe_load((proj / "golden_set.private.yaml").read_text())
    pub_ids = {c["id"] for c in public_disk.get("cases", [])}
    priv_ids = {c["id"] for c in private_disk.get("cases", [])}

    assert "PRIV001" not in pub_ids, "PRIVATE CASE LEAKED INTO PUBLIC FILE"
    assert pub_ids == {"PUB001"}
    assert priv_ids == {"PRIV001"}


def test_origin_never_serialized(split_workspace):
    tmp_path, proj = split_workspace
    svc = EvalService(workspace_root=tmp_path)
    svc._persist_golden_set()
    for fname in ("golden_set.yaml", "golden_set.private.yaml"):
        raw = (proj / fname).read_text()
        assert "_origin" not in raw, f"{fname} leaked internal _origin tag"


def test_cross_file_id_collision_fails_loud(tmp_path):
    proj = tmp_path / "Eval"
    proj.mkdir(parents=True)
    (proj / "EvalHistory").mkdir()
    (proj / "golden_set.yaml").write_text(
        yaml.dump({"version": 2, "cases": [_mk_case("DUP", ["backend/x.py"])]})
    )
    (proj / "golden_set.private.yaml").write_text(
        yaml.dump({"version": 2, "cases": [_mk_case("DUP", ["STEERING.R1"])]})
    )
    with pytest.raises(ValueError, match="collision"):
        EvalService(workspace_root=tmp_path)
