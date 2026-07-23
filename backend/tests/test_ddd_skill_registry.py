"""Tests for the DDD Skill Registry engine (run_597f4ed1, Run 2 REGISTRY+MOUNT).

Validates the capability-package discovery mechanism:
- build_manifest resolves each mounted DDD's DOMAIN skills (aim.json domain_skills)
- enablement skills (native_skills / s_ddd-*/ s_repo-to-ddd) are EXCLUDED
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
        _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report", "s_ddd-persist", "s_repo-to-ddd"])
        records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        assert names == {"s_cmhk-weekly-report"}  # enablement excluded

    def test_native_skills_never_registered(self, tmp_path):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-x"], native_skills=["s_ddd-manager", "s_repo-to-ddd"])
        records = reg.build_manifest(ws, builtin)
        assert {r["skill"] for r in records} == {"s_cmhk-x"}

    def test_folder_is_authoritative_undeclared_skill_discovered(self, tmp_path):
        """FOLDER-AS-SOURCE: a domain skill DIR present in 4-capabilities/ is
        discovered even if it is NOT listed in aim.json domain_skills. The folder
        is the source of truth — the declared list is no longer the gate (it is
        only a fail-loud cross-check, see test_declared_but_absent_warns)."""
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        # aim.json declares only ONE, but TWO domain skill dirs exist on disk.
        pdir = _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
        _mkskill(pdir / "skills", "s_cmhk-undeclared")  # on disk, NOT declared
        records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        # Folder-authoritative: BOTH are discovered (old declared-list would miss the 2nd).
        assert names == {"s_cmhk-weekly-report", "s_cmhk-undeclared"}
        assert all(r["class"] == "domain" for r in records)

    def test_declared_but_absent_warns_not_silent(self, tmp_path, caplog):
        """AC3 (mid-migration safety): a skill DECLARED in aim.json but ABSENT from
        the folder must be logged fail-LOUD (warning), not silently dropped. It is
        NOT added to the manifest (the folder is authoritative), but its absence is
        surfaced so a half-migrated DDD is visible, not invisible."""
        import logging
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        # Declares a ghost skill that has NO dir on disk.
        pdir = _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
        aim = json.loads((pdir / "aim.json").read_text())
        aim["plugins"]["domain_skills"].append("s_cmhk-ghost")
        (pdir / "aim.json").write_text(json.dumps(aim))
        with caplog.at_level(logging.WARNING):
            records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        assert names == {"s_cmhk-weekly-report"}     # ghost NOT added (folder authoritative)
        assert "s_cmhk-ghost" in caplog.text          # but its absence is LOUD
        assert "declared" in caplog.text.lower()

    def test_dir_without_skill_md_excluded(self, tmp_path):
        """A dir in 4-capabilities/ WITHOUT a SKILL.md (e.g. _shared) is not a skill
        and must be excluded by the folder scan."""
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        pdir = _mk_ddd(ws, "CMHK", ["s_cmhk-a"])
        (pdir / "skills" / "_shared").mkdir(parents=True)  # no SKILL.md
        (pdir / "skills" / "_shared" / "helper.py").write_text("x = 1")
        records = reg.build_manifest(ws, builtin)
        assert {r["skill"] for r in records} == {"s_cmhk-a"}  # _shared excluded

    def test_symlink_skill_dir_is_rejected(self, tmp_path):
        """Gate-2 security (path-escape): a symlink in 4-capabilities/ pointing at a
        skill-shaped dir OUTSIDE the DDD must NOT be registered — else the packager
        (which shares this primitive) could copy host files outside the package."""
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        pdir = _mk_ddd(ws, "CMHK", ["s_cmhk-a"])
        # Plant a skill-shaped dir OUTSIDE the DDD, then symlink into it from caps.
        outside = tmp_path / "outside" / "s_evil"
        outside.mkdir(parents=True)
        (outside / "SKILL.md").write_text("---\nname: s_evil\n---\nescape")
        (pdir / "skills" / "s_evil").symlink_to(outside, target_is_directory=True)
        records = reg.build_manifest(ws, builtin)
        names = {r["skill"] for r in records}
        # The symlinked escape skill is rejected; the real one survives.
        assert names == {"s_cmhk-a"}
        assert "s_evil" not in names

    def test_per_ddd_scan_error_does_not_wipe_other_ddds(self, tmp_path, monkeypatch):
        """AC4 (per-DDD fail-soft): if ONE DDD's 4-capabilities/ dir raises OSError
        on scan, that DDD is skipped (logged), but OTHER DDDs' skills still resolve
        — a per-project read blip must not wipe the whole manifest."""
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-a"])
        _mk_ddd(ws, "IVTHub", ["s_ivt-b"])
        real_iterdir = Path.iterdir

        def _selective_boom(self):
            # Only CMHK's capabilities dir explodes; Projects/ + IVTHub are fine.
            if self.name in ("skills", "4-capabilities") and "CMHK" in str(self):
                raise OSError("transient: CMHK caps dir unreadable")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _selective_boom)
        records = reg.build_manifest(ws, builtin)
        # IVTHub survives; CMHK is skipped (not a crash, not a full wipe).
        assert {r["skill"] for r in records} == {"s_ivt-b"}

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


class TestEmptyOverwriteGuard:
    """A zero-result scan must NOT wipe a good manifest (run_669e29f6 skeptic
    finding). build_manifest's fail-soft guaranteed 'never raises', NOT 'never
    destroys the cache' — a transient Projects/ read error would overwrite a good
    manifest with []. The guard keeps the existing cache on the 'was non-empty,
    now suddenly 0' transition, while still writing empty for a legit 0-domain
    workspace (nothing existed to protect)."""

    def test_transient_empty_scan_does_not_wipe_good_manifest(self, tmp_path, monkeypatch):
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "CMHK", ["s_cmhk-a", "s_cmhk-b"])
        # 1) Build a GOOD, non-empty manifest.
        reg.build_manifest(ws, builtin)
        assert {r["skill"] for r in reg.read_manifest(ws)} == {"s_cmhk-a", "s_cmhk-b"}

        # 2) Simulate a transient Projects/ read failure: iterdir raises OSError,
        #    so the scan resolves ZERO records.
        real_iterdir = Path.iterdir

        def _boom(self):
            if self.name == "Projects":
                raise OSError("transient: Projects/ momentarily unreadable")
            return real_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", _boom)
        result = reg.build_manifest(ws, builtin)

        # 3) The guard must have kept the good cache — NOT overwritten it with [].
        assert {r["skill"] for r in reg.read_manifest(ws)} == {"s_cmhk-a", "s_cmhk-b"}
        # build_manifest returns the preserved records too (not []).
        assert {r["skill"] for r in result} == {"s_cmhk-a", "s_cmhk-b"}

    def test_legit_zero_domain_workspace_still_writes_empty(self, tmp_path):
        # A workspace whose DDDs declare NO domain skills: nothing pre-exists to
        # protect, so the guard must NOT fire — an empty manifest is the correct,
        # truthful result (read_manifest returns [] beforehand → guard skipped).
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        _mk_ddd(ws, "PhysicalAI", [])  # 0-asset pure-knowledge brain
        result = reg.build_manifest(ws, builtin)
        assert result == []
        # The manifest file was written (exists) and reads back as empty.
        assert reg.read_manifest(ws) == []

    def test_legit_removal_of_all_skills_updates_manifest(self, tmp_path):
        # THE SEMANTIC GAP (Gate-2 finding, run_669e29f6): a DDD that HAD domain
        # skills legitimately drops them all. Projects/ is fully readable — this is
        # a REAL 0, not a transient error — so the manifest MUST update to remove the
        # stale skill. The guard must NOT fire here (it gates on scan_failure, not on
        # records==0). A guard gated on `records==0` alone would keep the stale skill
        # FOREVER (self-perpetuating staleness).
        #
        # FOLDER-AS-SOURCE: "removing a skill" now means DELETING THE DIR (the folder
        # is authoritative), NOT editing aim.json. (Under the old declared-list
        # semantics this test emptied domain_skills; that no longer removes anything
        # because the dir is the source of truth.)
        import shutil
        ws = tmp_path / "SwarmWS"
        builtin = tmp_path / "b"; builtin.mkdir(parents=True)
        pdir = _mk_ddd(ws, "CMHK", ["s_cmhk-a"])
        reg.build_manifest(ws, builtin)
        assert {r["skill"] for r in reg.read_manifest(ws)} == {"s_cmhk-a"}  # non-empty

        # Operator removes the skill by DELETING its dir; Projects/ still fully readable.
        shutil.rmtree(pdir / "skills" / "s_cmhk-a")
        (pdir / "aim.json").write_text(json.dumps(
            {"name": "CMHK", "plugins": {"native_skills": [], "domain_skills": []}}
        ))
        result = reg.build_manifest(ws, builtin)

        # The manifest MUST reflect the removal — NOT keep the stale skill.
        assert result == []
        assert reg.read_manifest(ws) == []
