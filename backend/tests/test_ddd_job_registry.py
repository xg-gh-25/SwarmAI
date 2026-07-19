"""Tests for the DDD Job Registry (build_manifest_jobs).

Covers: discovery of kind:job assets from bindings.yaml, depends_on_skill
resolution, the PER-PROJECT empty-overwrite guard (stronger than the skill
registry's all-or-nothing guard), genuine-removal, heterogeneous-shape fail-soft,
and an E2E against the real workspace's GitHub_Community jobs.

Temp-workspace fixtures (fake DDDs in tmp Projects/) — the only I/O is a tmp dir.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from core import ddd_job_registry as reg


def _mk_ddd_with_jobs(workspace: Path, project: str, jobs: list[dict]) -> Path:
    """Create a DDD project dir with a bindings.yaml declaring kind:job assets."""
    pdir = workspace / "Projects" / project
    pdir.mkdir(parents=True, exist_ok=True)
    assets = [{"kind": "job", **j} for j in jobs]
    (pdir / "bindings.yaml").write_text(yaml.safe_dump({"governed_assets": assets}))
    # aim.json presence isn't required by the job registry, but keep it realistic.
    (pdir / "aim.json").write_text(json.dumps({"name": project, "plugins": {}}))
    return pdir


def _seed_skill_manifest(workspace: Path, skills: list[str]) -> None:
    """Write a minimal skill-registry manifest so depends_on_skill can resolve."""
    p = workspace / ".context" / "ddd_skill_registry.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    recs = [{"skill": s, "class": "domain", "owner_ddd": "X", "path": f"/x/{s}"} for s in skills]
    p.write_text(json.dumps({"version": 1, "skills": recs}))


class TestBuildManifestJobs:
    def test_discovers_job_assets(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GitHub_Community", [
            {"name": "gh-morning", "schedule": "0 8 * * 1-5", "type": "agent_task",
             "enabled": True, "depends_on_skill": "s_github_community"},
            {"name": "gh-evening", "schedule": "0 9 * * 1-5", "type": "agent_task",
             "enabled": True, "depends_on_skill": "s_github_community"},
        ])
        recs = reg.build_manifest_jobs(ws)
        assert {r["job_id"] for r in recs} == {"gh-morning", "gh-evening"}
        assert all(r["owner_ddd"] == "GitHub_Community" for r in recs)
        # name → job_id mapping (bindings.yaml uses `name`)
        assert all("name" not in r for r in recs)

    def test_depends_on_skill_resolved(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        _seed_skill_manifest(ws, ["s_github_community"])   # this one resolves
        _mk_ddd_with_jobs(ws, "GH", [
            {"name": "j-ok", "depends_on_skill": "s_github_community"},
            {"name": "j-dangling", "depends_on_skill": "s_nonexistent"},
        ])
        recs = {r["job_id"]: r for r in reg.build_manifest_jobs(ws)}
        assert recs["j-ok"]["depends_on_skill_resolved"] is True
        # dangling job is SURFACED (resolved=False), not silently dropped
        assert recs["j-dangling"]["depends_on_skill_resolved"] is False
        assert "j-dangling" in recs

    def test_no_bindings_is_empty_not_crash(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        (ws / "Projects" / "NoBindings").mkdir(parents=True)
        assert reg.build_manifest_jobs(ws) == []

    def test_heterogeneous_shape_failsoft(self, tmp_path):
        # A project using a bare `jobs: []` (CMHK-style), NOT governed_assets — must
        # yield no job records for it, never crash.
        ws = tmp_path / "SwarmWS"
        pdir = ws / "Projects" / "CMHK"
        pdir.mkdir(parents=True)
        (pdir / "bindings.yaml").write_text(yaml.safe_dump({"jobs": []}))
        _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-1", "depends_on_skill": "s_x"}])
        recs = reg.build_manifest_jobs(ws)
        assert {r["job_id"] for r in recs} == {"gh-1"}  # CMHK contributes nothing

    def test_roundtrip_read_manifest(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-1", "depends_on_skill": "s_x"}])
        reg.build_manifest_jobs(ws)
        out = reg.read_manifest(ws)
        assert {r["job_id"] for r in out} == {"gh-1"}

    def test_no_dependency_resolved_is_None_not_False(self, tmp_path):
        # A job with NO depends_on_skill must get resolved=None (not dangling), so a
        # consumer testing `resolved is False` for dangling doesn't false-alarm on it.
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-nodep"}])  # no depends_on_skill
        rec = reg.build_manifest_jobs(ws)[0]
        assert rec["depends_on_skill"] is None
        assert rec["depends_on_skill_resolved"] is None  # NOT False

    def test_undecodable_bindings_yaml_does_not_crash(self, tmp_path):
        # A readable-but-invalid-UTF-8 bindings.yaml raises UnicodeDecodeError
        # (a ValueError, NOT OSError). It must NOT crash build_manifest_jobs (never-
        # raises contract) NOR be preserved as transient — it's a genuine unparseable
        # source → parse-to-0. (Gate-2 finding, run_5ec6b7ad.)
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GOOD", [{"name": "good-1", "depends_on_skill": "s_x"}])
        bad = ws / "Projects" / "BAD"
        bad.mkdir(parents=True)
        (bad / "bindings.yaml").write_bytes(b"\xff\xfe governed_assets: bad \x80\x81")
        # Must not raise; BAD contributes nothing, GOOD is unaffected.
        recs = reg.build_manifest_jobs(ws)
        assert {r["job_id"] for r in recs} == {"good-1"}


class TestPerProjectGuard:
    """The stronger-than-skill-registry guard: ONE project's transiently-unreadable
    bindings.yaml must NOT wipe that DDD's jobs, while OTHER projects rebuild
    normally, AND a genuine removal still updates."""

    def test_per_project_transient_failure_preserves_that_ddds_jobs(self, tmp_path, monkeypatch):
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-1", "depends_on_skill": "s_x"}])
        _mk_ddd_with_jobs(ws, "OTHER", [{"name": "ot-1", "depends_on_skill": "s_y"}])
        # 1) Good manifest with BOTH projects' jobs.
        reg.build_manifest_jobs(ws)
        assert {r["job_id"] for r in reg.read_manifest(ws)} == {"gh-1", "ot-1"}

        # 2) GH's bindings.yaml becomes transiently unreadable; OTHER reads fine.
        gh_bindings = (ws / "Projects" / "GH" / "bindings.yaml").resolve()
        real_read_text = Path.read_text

        def _boom(self, *a, **k):
            if self.resolve() == gh_bindings:
                raise OSError("transient: GH bindings.yaml unreadable")
            return real_read_text(self, *a, **k)

        # Mutate OTHER so a FRESH rebuild differs from a PRESERVED-from-prior slice
        # (adds ot-2). If the guard wrongly preserved OTHER too, ot-2 would be
        # absent — this makes the test non-vacuous (Gate-2 finding, run_5ec6b7ad).
        _mk_ddd_with_jobs(ws, "OTHER", [
            {"name": "ot-1", "depends_on_skill": "s_y"},
            {"name": "ot-2", "depends_on_skill": "s_z"},
        ])

        monkeypatch.setattr(Path, "read_text", _boom)
        reg.build_manifest_jobs(ws)

        # 3) GH's job PRESERVED (not wiped, file unreadable); OTHER REBUILT FRESH
        #    (ot-2 present proves it was re-read, not preserved-from-prior).
        got = {r["job_id"] for r in reg.read_manifest(ws)}
        assert got == {"gh-1", "ot-1", "ot-2"}, f"per-project guard failed: {got}"

    def test_genuine_removal_updates_manifest(self, tmp_path):
        # bindings.yaml readable but the job asset removed → REAL removal, must
        # update (NOT preserved by the guard — the guard only fires on OSError).
        ws = tmp_path / "SwarmWS"
        pdir = _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-1", "depends_on_skill": "s_x"}])
        reg.build_manifest_jobs(ws)
        assert {r["job_id"] for r in reg.read_manifest(ws)} == {"gh-1"}

        # Operator removes the job asset (file fully readable, just no jobs now).
        (pdir / "bindings.yaml").write_text(yaml.safe_dump({"governed_assets": []}))
        reg.build_manifest_jobs(ws)
        assert reg.read_manifest(ws) == []   # updated, NOT stale-preserved

    def test_brand_new_project_transient_failure_preserves_nothing(self, tmp_path, monkeypatch):
        # A project that FAILS its very first read has no prior slice → preserve
        # nothing (no crash, no phantom entries).
        ws = tmp_path / "SwarmWS"
        _mk_ddd_with_jobs(ws, "GH", [{"name": "gh-1"}])
        (ws / ".context").mkdir(parents=True, exist_ok=True)  # empty manifest area
        new_bindings = (ws / "Projects" / "GH" / "bindings.yaml").resolve()
        real = Path.read_text
        monkeypatch.setattr(Path, "read_text",
                            lambda self, *a, **k: (_ for _ in ()).throw(OSError("boom"))
                            if self.resolve() == new_bindings else real(self, *a, **k))
        recs = reg.build_manifest_jobs(ws)
        assert recs == []  # nothing to preserve, no crash
