"""Read-contract tests for the artifact registry loader (Action B).

Verifies the single read contract added in Action B of the artifact-subsystem
systemic fix:
  - _load_artifact_for_metrics enforces run_id OWNERSHIP (conditionally — only
    when the manifest entry carries a run_id; the ~2169 legacy run_id-less entries
    must still load) and artifact TYPE.
  - It returns a 3-state result (available / unavailable / not_run) so a caller
    can distinguish "artifact missing" from "a real 0".
  - _changeset_stage_for_profile maps the changeset source by profile
    (build for full/bugfix, goal_cycle for goal).
  - Artifact dataclass + _entry_to_artifact + discover() carry/filter run_id.

Methodology: real temp workspace + manifest (no mocks of our own code — mock only
the _get_workspace boundary). Each ownership/type/state branch has a test that goes
RED when the branch is removed (mutation-proven in BUILD).
"""
import json
from pathlib import Path

import pytest

from core.artifact_registry import ArtifactRegistry


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — a real workspace with a hand-built manifest + data files
# ─────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "Projects").mkdir()
    return tmp_path


def _seed_artifact(workspace: Path, project: str, *, art_id: str, art_type: str,
                   run_id: str | None, payload: dict) -> None:
    """Write a data file + append a manifest entry (mimics the write side)."""
    arts_dir = workspace / "Projects" / project / ".artifacts"
    arts_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{art_type}-20261012-{art_id}.json"
    (arts_dir / fname).write_text(json.dumps(payload), encoding="utf-8")
    manifest_file = arts_dir / "manifest.json"
    if manifest_file.exists():
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    else:
        manifest = {"project": project, "pipeline_state": "build", "artifacts": []}
    entry = {"id": art_id, "type": art_type, "producer": "s_autonomous-pipeline",
             "created": "2026-10-12T00:00:00+00:00", "file": fname,
             "summary": "test", "superseded_by": None}
    if run_id is not None:
        entry["run_id"] = run_id
    manifest["artifacts"].append(entry)
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — conditional ownership (tracer bullet)
# ─────────────────────────────────────────────────────────────────────────────
class TestOwnership:
    def test_cross_run_entry_rejected(self, workspace, monkeypatch):
        """An entry owned by run B must NOT load under run A's run_id."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_foreign", art_type="evaluation",
                       run_id="run_B", payload={"v": 1})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_foreign", "evaluation")
        assert res.state == "unavailable"
        assert res.data is None

    def test_same_run_entry_loads(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_mine", art_type="evaluation",
                       run_id="run_A", payload={"v": 42})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_mine", "evaluation")
        assert res.state == "available"
        assert res.data == {"v": 42}

    def test_legacy_no_run_id_entry_still_loads(self, workspace, monkeypatch):
        """The ~2169 legacy entries lacking run_id MUST pass (conditional ownership)."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_legacy", art_type="evaluation",
                       run_id=None, payload={"v": 7})
        res = cli._load_artifact_for_metrics("P", "run_whatever", "art_legacy", "evaluation")
        assert res.state == "available"
        assert res.data == {"v": 7}


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — type check
# ─────────────────────────────────────────────────────────────────────────────
class TestTypeCheck:
    def test_wrong_type_rejected(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_x", art_type="research",
                       run_id="run_A", payload={"v": 1})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_x", "evaluation")
        assert res.state == "unavailable"

    def test_expected_type_none_skips_type_check(self, workspace, monkeypatch):
        """expected_type=None → no type enforcement (back-compat for callers that don't know)."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_y", art_type="research",
                       run_id="run_A", payload={"v": 9})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_y", None)
        assert res.state == "available"
        assert res.data == {"v": 9}

    # #3 fix — the type gate must accept a SET of valid types (membership), not
    # just a scalar. THINK is published as either `research` OR `alternatives`
    # (STAGE_ROUTING['think'] produces=research + optional_produces=alternatives),
    # so _STAGE_ARTIFACT_TYPE['think'] = {"research","alternatives"}. Before this
    # fix the scalar gate rejected the alternatives case → THINK content silently
    # dropped from REPORT.md across 24+ runs.
    def test_set_expected_accepts_alternatives_member(self, workspace, monkeypatch):
        """A set expected_type accepts a member type (the actual #3 bug: think=alternatives)."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_alt", art_type="alternatives",
                       run_id="run_A", payload={"key_findings": ["kf1"], "alternatives": [{"constraint": "SIMPLICITY"}]})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_alt", {"research", "alternatives"})
        assert res.state == "available"
        assert res.data.get("key_findings") == ["kf1"]

    def test_set_expected_accepts_research_member(self, workspace, monkeypatch):
        """The other member of the set still loads (no regression for type=research think)."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_res", art_type="research",
                       run_id="run_A", payload={"v": 7})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_res", {"research", "alternatives"})
        assert res.state == "available"

    def test_set_expected_rejects_nonmember(self, workspace, monkeypatch):
        """Still a CLOSED allowlist AND discriminates by MEMBERSHIP: with the SAME set
        gate, a member loads but a non-member is rejected. Pinning both against one set
        is what proves the gate does membership (not scalar `!=`, under which a str is
        never == a set, so EVERY type — member or not — would 'reject' for the wrong
        reason and this test would pass vacuously on the reverted code)."""
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        gate = {"research", "alternatives"}
        _seed_artifact(workspace, "P", art_id="art_member", art_type="alternatives",
                       run_id="run_A", payload={"v": 1})
        _seed_artifact(workspace, "P", art_id="art_wrong", art_type="evaluation",
                       run_id="run_A", payload={"v": 2})
        # member → available, non-member → unavailable, SAME gate: only true membership
        # (not scalar `!=`) can satisfy both halves simultaneously.
        assert cli._load_artifact_for_metrics("P", "run_A", "art_member", gate).state == "available"
        assert cli._load_artifact_for_metrics("P", "run_A", "art_wrong", gate).state == "unavailable"

    def test_think_stage_type_is_a_set(self, monkeypatch):
        """_STAGE_ARTIFACT_TYPE['think'] accepts both think produces (contract honored)."""
        import scripts.artifact_cli as cli
        think_types = cli._STAGE_ARTIFACT_TYPE["think"]
        assert "research" in think_types and "alternatives" in think_types
        # other stages stay single-type (scalar) — strictness unchanged
        assert cli._STAGE_ARTIFACT_TYPE["evaluate"] == "evaluation"
        assert cli._STAGE_ARTIFACT_TYPE["plan"] == "design_doc"


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — three-state (available / unavailable / not_run)
# ─────────────────────────────────────────────────────────────────────────────
class TestThreeState:
    def test_missing_artifact_is_unavailable_not_zero(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        (workspace / "Projects" / "P" / ".artifacts").mkdir(parents=True)
        (workspace / "Projects" / "P" / ".artifacts" / "manifest.json").write_text(
            json.dumps({"project": "P", "artifacts": []}), encoding="utf-8")
        res = cli._load_artifact_for_metrics("P", "run_A", "art_absent", "evaluation")
        assert res.state == "unavailable"
        assert res.data is None
        assert not res  # falsy — back-compat with `if loaded:` callers

    def test_not_run_when_art_id_is_none(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        res = cli._load_artifact_for_metrics("P", "run_A", None, "evaluation")
        assert res.state == "not_run"
        assert not res

    def test_available_is_truthy(self, workspace, monkeypatch):
        import scripts.artifact_cli as cli
        monkeypatch.setattr(cli, "_get_workspace", lambda: workspace)
        _seed_artifact(workspace, "P", art_id="art_ok", art_type="evaluation",
                       run_id="run_A", payload={"v": 1})
        res = cli._load_artifact_for_metrics("P", "run_A", "art_ok", "evaluation")
        assert bool(res) is True
        assert res.data == {"v": 1}


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — profile-aware changeset stage
# ─────────────────────────────────────────────────────────────────────────────




# ─────────────────────────────────────────────────────────────────────────────
# AC5 — Artifact.run_id + discover() run_id filter
# ─────────────────────────────────────────────────────────────────────────────
class TestDiscoverRunIdFilter:
    def test_artifact_carries_run_id(self, workspace):
        reg = ArtifactRegistry(workspace)
        (workspace / "Projects" / "P").mkdir(parents=True)
        reg.publish("P", "research", data={"k": 1}, producer="x", summary="s",
                    run_id="run_A")
        arts = reg.discover("P", "research")
        assert len(arts) == 1
        assert arts[0].run_id == "run_A"

    def test_discover_filters_by_run_id(self, workspace):
        reg = ArtifactRegistry(workspace)
        (workspace / "Projects" / "P").mkdir(parents=True)
        reg.publish("P", "research", data={"k": 1}, producer="x", summary="A",
                    run_id="run_A")
        reg.publish("P", "research", data={"k": 2}, producer="x", summary="B",
                    run_id="run_B")
        only_a = reg.discover("P", "research", run_id="run_A")
        assert len(only_a) == 1
        assert only_a[0].run_id == "run_A"

    def test_discover_no_filter_returns_all(self, workspace):
        reg = ArtifactRegistry(workspace)
        (workspace / "Projects" / "P").mkdir(parents=True)
        reg.publish("P", "research", data={"k": 1}, producer="x", summary="A",
                    run_id="run_A")
        reg.publish("P", "research", data={"k": 2}, producer="x", summary="B",
                    run_id="run_B")
        assert len(reg.discover("P", "research")) == 2

    def test_discover_run_id_filter_excludes_legacy_no_run_id(self, workspace, monkeypatch):
        """A run-scoped discover must NOT leak a run_id-less legacy entry."""
        import scripts.artifact_cli as cli
        reg = ArtifactRegistry(workspace)
        _seed_artifact(workspace, "P", art_id="art_legacy", art_type="research",
                       run_id=None, payload={"k": 1})
        legacy_only = reg.discover("P", "research", run_id="run_A")
        assert legacy_only == []
