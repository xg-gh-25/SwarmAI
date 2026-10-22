"""Integration: the pipeline CLI's dir-minting commands self-register the project.

Root fix for the "pseudo-DDD" class (run_be30a0c6): a Projects/<name>/ minted by
run-create / release-gate / run-report / run-analytics must carry a .project.json,
else Brain Hub (_list_project_dirs) cannot see it. These tests exercise the single
choke _ensure_project_dir and assert the Layer-4 seam: writer (CLI) -> reader
(_list_project_dirs) sees the project.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent  # backend/
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))


@pytest.fixture
def cli(tmp_path, monkeypatch):
    """artifact_cli with _get_workspace pinned to a scratch workspace."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path))
    import artifact_cli
    importlib.reload(artifact_cli)
    # pin the workspace resolver to tmp so no real ~/.swarm-ai is touched
    monkeypatch.setattr(artifact_cli, "_get_workspace", lambda: tmp_path)
    return artifact_cli


def test_ensure_project_dir_creates_and_registers(cli, tmp_path):
    pdir = cli._ensure_project_dir("FreshProj")
    assert pdir == tmp_path / "Projects" / "FreshProj"
    assert pdir.is_dir()
    meta = pdir / ".project.json"
    assert meta.exists(), "minting a project dir must register it (.project.json)"
    assert json.loads(meta.read_text())["name"] == "FreshProj"


def test_ensure_project_dir_idempotent(cli, tmp_path):
    cli._ensure_project_dir("FreshProj")
    meta = tmp_path / "Projects" / "FreshProj" / ".project.json"
    before = meta.read_text()
    cli._ensure_project_dir("FreshProj")  # 2nd mint
    assert meta.read_text() == before, "re-minting must not rewrite existing metadata"


def test_brain_hub_sees_a_registered_project(cli, tmp_path, monkeypatch):
    """Layer-4: after the CLI mints+registers a project, Brain Hub's _list_project_dirs
    (the reader) includes it. This is the writer->reader seam the fix exists to close."""
    cli._ensure_project_dir("VisibleProj")

    # point the Brain Hub reader at the same scratch workspace
    from routers import ddd_brain
    monkeypatch.setattr(ddd_brain, "_projects_root", lambda: tmp_path / "Projects")
    names = [d.name for d in ddd_brain._list_project_dirs()]
    assert "VisibleProj" in names, "a registered project MUST be visible to Brain Hub"


def test_rejects_path_traversal_project_name(cli, tmp_path):
    """SECURITY: a project name with a path-escape segment must be rejected, not
    mkdir'd outside Projects/ (adversarial-review HIGH)."""
    for bad in ["../escape", "../../etc", "a/b", "..", "/abs"]:
        with pytest.raises(ValueError):
            cli._ensure_project_dir(bad)
    # nothing escaped the workspace
    assert not (tmp_path.parent / "escape").exists()
    assert not (tmp_path / ".." / "escape").resolve().exists()


def test_pseudo_ddd_is_reproduced_without_registration(cli, tmp_path, monkeypatch):
    """MUTATION-PROOF: if the birth point skips registration (raw mkdir, the OLD bug),
    the project is a pseudo-DDD invisible to Brain Hub. This guards that the fix has teeth."""
    # simulate the pre-fix behavior: mkdir the subtree WITHOUT _ensure_project_dir
    raw = tmp_path / "Projects" / "PseudoProj" / ".artifacts" / "runs" / "run_x"
    raw.mkdir(parents=True, exist_ok=True)

    from routers import ddd_brain
    monkeypatch.setattr(ddd_brain, "_projects_root", lambda: tmp_path / "Projects")
    names = [d.name for d in ddd_brain._list_project_dirs()]
    assert "PseudoProj" not in names, (
        "a dir minted without .project.json must be INVISIBLE — this is the bug the "
        "fix closes; if this ever appears, registration is not enforced at the birth point"
    )
