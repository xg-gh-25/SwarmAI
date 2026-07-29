"""Tests for the DDD Brain Hub Distribute tab endpoint (Run 3).

Methodology: integration tests against a REAL temporary workspace tree (real
aim.json policy parsing + real git for source_changed_since) — the Distribute
tab projects real distribution state, so tests assert against real data, never
fabricated targets.

Key invariants:
  - AC1: GET /brains/{name}/distribution returns declared reach + output state,
    all live-computed (R30#4).
  - AC2: a DDD with NO distribution block in aim.json → distributable=false,
    declared=false, declared_targets=[] — the honest phase-1 state, never
    fabricated.
  - AC3: source_changed_since=true iff the subtree has a git commit AFTER the
    distribute output was produced.
  - AC5: the endpoint REUSES ddd_distribution_policy.validate_distribution_file
    (no hand-rolled aim.json distribution parsing).
"""

import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def dist_repo(tmp_path, monkeypatch):
    """A tmp SwarmWS with two DDDs: 'Declared' (has a distribution block) and
    'Bare' (no distribution block → not distributable)."""
    ws = tmp_path / "SwarmWS"
    for name, aim in [
        ("Declared", {"name": "Declared", "distribution": {"targets": ["open-plugin"], "visibility": "internal"}}),
        ("Bare", {"name": "Bare", "plugins": {"native_skills": []}}),  # NO distribution block
    ]:
        pd = ws / "Projects" / name
        (pd / "2-understanding").mkdir(parents=True)
        (pd / ".project.json").write_text('{"name": "%s"}\n' % name)
        (pd / "aim.json").write_text(json.dumps(aim) + "\n")
        (pd / "2-understanding" / "TECH.md").write_text("line1\nline2\nline3\n")

    _git(ws, "init", "-q")
    _git(ws, "config", "user.email", "t@t.co")
    _git(ws, "config", "user.name", "t")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-qm", "base")

    monkeypatch.setattr("routers.ddd_brain._workspace_root", lambda: ws)
    return {"ws": ws}


@pytest.fixture
def client():
    from routers.ddd_brain import router

    app = FastAPI()
    app.include_router(router, prefix="/api/ddd")
    return TestClient(app)


class TestDistribution:
    def test_declared_brain_surfaces_targets(self, client, dist_repo):
        resp = client.get("/api/ddd/brains/Declared/distribution")
        assert resp.status_code == 200
        d = resp.json()
        assert d["declared"] is True
        assert d["distributable"] is True
        assert "open-plugin" in d["declared_targets"]
        assert d["visibility"] == "internal"
        assert d["has_output"] is False          # no output produced yet
        assert d["output_path"] is None
        assert d["last_distribute_time"] is None

    def test_bare_brain_is_not_distributable_not_fabricated(self, client, dist_repo):
        """AC2: no distribution block → honest not-distributable, empty targets."""
        resp = client.get("/api/ddd/brains/Bare/distribution")
        assert resp.status_code == 200
        d = resp.json()
        assert d["distributable"] is False
        assert d["declared"] is False
        assert d["declared_targets"] == []       # NEVER fabricated

    def test_404_unknown_brain(self, client, dist_repo):
        assert client.get("/api/ddd/brains/Nope/distribution").status_code == 404

    def test_source_changed_since_true_after_commit(self, client, dist_repo):
        """AC3: a subtree commit AFTER the distribute output → source_changed_since."""
        ws = dist_repo["ws"]
        # Produce a distribute output dir, commit it (so its state is baseline).
        outdir = ws / "Projects" / "Declared" / ".artifacts" / "distribute"
        outdir.mkdir(parents=True)
        (outdir / "open-plugin").mkdir()
        (outdir / "open-plugin" / "plugin.json").write_text("{}\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "distribute output")

        # Ensure a later mtime gap, then commit a NEW subtree change.
        time.sleep(0.02)
        (ws / "Projects" / "Declared" / "2-understanding" / "TECH.md").write_text(
            "line1\nCHANGED\nline3\n"
        )
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "later knowledge change")

        d = client.get("/api/ddd/brains/Declared/distribution").json()
        assert d["has_output"] is True
        assert d["output_path"] and d["output_path"].endswith("distribute")
        assert d["source_changed_since"] is True

    def test_no_source_change_when_output_is_newest(self, client, dist_repo):
        ws = dist_repo["ws"]
        # Commit a knowledge change FIRST, then the output dir LAST → output newest.
        (ws / "Projects" / "Declared" / "2-understanding" / "TECH.md").write_text(
            "line1\nEARLY\nline3\n"
        )
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "early change")
        time.sleep(0.02)
        outdir = ws / "Projects" / "Declared" / ".artifacts" / "distribute"
        outdir.mkdir(parents=True)
        (outdir / "x.txt").write_text("out\n")
        _git(ws, "add", "-A")
        _git(ws, "commit", "-qm", "distribute output newest")

        d = client.get("/api/ddd/brains/Declared/distribution").json()
        assert d["has_output"] is True
        assert d["source_changed_since"] is False

    def test_endpoint_reuses_policy_validator(self):
        """AC5: endpoint must reuse validate_distribution_file, not hand-parse aim.json."""
        src = Path(__file__).resolve().parents[1] / "routers" / "ddd_brain.py"
        text = src.read_text()
        assert "validate_distribution_file" in text
