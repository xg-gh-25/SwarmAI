"""Tests for the DDD Skill Registry engine (run_597f4ed1, Run 2 REGISTRY+MOUNT).

Validates the capability-package discovery mechanism:
- build_manifest resolves each mounted DDD's DOMAIN skills (aim.json domain_skills)
- enablement skills (native_skills / s_ddd-*/ s_ai-ready-repo) are EXCLUDED
- fail-soft: missing OR malformed manifest/aim.json → [] (never raises — the
  read path is inside SkillManager.scan_all, the choke point for ALL discovery)
- atomic write (no torn read)

These use a temp workspace fixture (a fake DDD in tmp Projects/) — the only
NON-shadowed validation this run, since the 9 real s_cmhk-* still live in
backend/skills/ (built-in shadows them until Run 3).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import ddd_skill_registry as reg


def _mkskill(root: Path, name: str) -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test {name}\n---\nbody")
    return d


def _mk_ddd(workspace: Path, project: str, domain_skills: list[str],
            native_skills: list[str] | None = None) -> Path:
    """Create a DDD project dir with aim.json + its domain skills' dirs in the package."""
    pdir = workspace / "Projects" / project
    pdir.mkdir(parents=True, exist_ok=True)
    aim = {
        "name": project,
        "plugins": {
            "native_skills": native_skills or [],
            "domain_skills": domain_skills,
        },
    }
    (pdir / "aim.json").write_text(json.dumps(aim))
    for s in domain_skills:
        _mkskill(pdir / "skills", s)  # skill dir lives IN the package
    return pdir


class TestBuildManifest:
    def test_discovers_domain_skills(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "backend_skills"
        builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report", "s_cmhk-account-360"])
        records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        assert names == {"s_cmhk-weekly-report", "s_cmhk-account-360"}
        assert all(r["class"] == "domain" for r in records)
        assert all(r["owner_ddd"] == "CMHK" for r in records)

    def test_excludes_enablement_skills(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        # enablement listed (wrongly) in domain_skills must be dropped
        _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report", "s_ddd-persist", "s_ai-ready-repo"])
        records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        assert names == {"s_cmhk-weekly-report"}  # enablement excluded

    def test_native_skills_never_registered(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-x"], native_skills=["s_ddd-manager", "s_ai-ready-repo"])
        records = reg.build_manifest(ws, builtin)
        assert {r["skill"] for r in records} == {"s_cmhk-x"}

    def test_resolves_builtin_when_not_in_package(self, tmp_path):
        """Strangler: domain skill still in built-in (pre-Run-3) is resolved there."""
        ws = tmp_path / "SwarmWS"
        (ws / "Projects" / "CMHK").mkdir(parents=True)
        (ws / "Projects" / "CMHK" / "aim.json").write_text(
            json.dumps({"plugins": {"domain_skills": ["s_cmhk-y"]}})
        )
        builtin = tmp_path / "b"
        _mkskill(builtin, "s_cmhk-y")  # lives in built-in, NOT the package
        records = reg.build_manifest(ws, builtin)
        assert len(records) == 1
        assert Path(records[0]["path"]) == builtin / "s_cmhk-y"

    def test_declared_but_absent_skill_skipped(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / "Projects" / "CMHK").mkdir(parents=True)
        (ws / "Projects" / "CMHK" / "aim.json").write_text(
            json.dumps({"plugins": {"domain_skills": ["s_cmhk-ghost"]}})
        )
        builtin = tmp_path / "b"; builtin.mkdir()
        records = reg.build_manifest(ws, builtin)
        assert records == []  # declared but no dir anywhere → skipped, no crash

    def test_no_projects_dir_is_empty_not_crash(self, tmp_path):
        ws = tmp_path / "SwarmWS"; ws.mkdir()
        builtin = tmp_path / "b"; builtin.mkdir()
        assert reg.build_manifest(ws, builtin) == []

    def test_atomic_write_produces_readable_manifest(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
        reg.build_manifest(ws, builtin)
        manifest = ws / reg.MANIFEST_RELPATH
        assert manifest.is_file()
        data = json.loads(manifest.read_text())
        assert data["version"] == 1
        assert data["skills"][0]["skill"] == "s_cmhk-weekly-report"


class TestReadManifestFailSoft:
    """read_manifest is called inside scan_all — it MUST NEVER raise."""

    def test_missing_manifest_returns_empty(self, tmp_path):
        assert reg.read_manifest(tmp_path / "SwarmWS") == []

    def test_malformed_json_returns_empty_never_raises(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / ".context").mkdir(parents=True)
        (ws / reg.MANIFEST_RELPATH).write_text("{ this is not valid json ")
        # Must NOT raise — malformed == missing == empty (protects all 29 callers)
        assert reg.read_manifest(ws) == []

    def test_non_dict_manifest_returns_empty(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / ".context").mkdir(parents=True)
        (ws / reg.MANIFEST_RELPATH).write_text("[1, 2, 3]")
        assert reg.read_manifest(ws) == []

    def test_malformed_records_filtered(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / ".context").mkdir(parents=True)
        (ws / reg.MANIFEST_RELPATH).write_text(json.dumps({
            "version": 1,
            "skills": [
                {"skill": "s_ok", "path": "/x", "owner_ddd": "D"},
                {"skill": "s_bad"},                # missing path/owner → dropped
                "not a dict",                      # dropped
                {"path": "/y", "owner_ddd": "D"},  # missing skill → dropped
            ],
        }))
        out = reg.read_manifest(ws)
        assert len(out) == 1
        assert out[0]["skill"] == "s_ok"

    def test_roundtrip_build_then_read(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-a", "s_cmhk-b"])
        reg.build_manifest(ws, builtin)
        out = reg.read_manifest(ws)
        assert {r["skill"] for r in out} == {"s_cmhk-a", "s_cmhk-b"}
