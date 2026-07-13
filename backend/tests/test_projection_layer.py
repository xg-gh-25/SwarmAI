"""Tests for ProjectionLayer skip-when-fresh (run_bf4cb46e).

The bug: project_skills rmtree+copytree'd ALL ~89 skills on EVERY boot
(projection_layer.py:139-161), a multi-minute filesystem churn that timed
out the TestClient(app) fixture (>40s; 221s in isolation) and slowed every
production daemon boot. Fix: skip a skill whose destination is already
identical to the source (same relative-file-set AND dest max file-mtime >=
source max file-mtime). copytree uses copy2 which PRESERVES source mtime, so
an EDITED file makes dest older → re-copies; a DELETED source file changes
the path-set → re-copies. Only a byte-for-byte-fresh skill is skipped.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from core.projection_layer import ProjectionLayer, _is_skill_fresh


def _mkskill(root: Path, name: str, files: dict[str, str]) -> Path:
    """Create a fake skill dir with the given {relpath: content}."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return d


class TestIsSkillFresh:
    """_is_skill_fresh(src, dst): True only when dst is byte-current with src."""

    def test_missing_dest_is_not_fresh(self, tmp_path):
        src = _mkskill(tmp_path / "src", "s_x", {"SKILL.md": "a"})
        dst = tmp_path / "dst" / "s_x"  # does not exist
        assert _is_skill_fresh(src, dst) is False

    def test_identical_copy_is_fresh(self, tmp_path):
        src = _mkskill(tmp_path / "src", "s_x", {"SKILL.md": "a", "sub/b.py": "x"})
        dst = tmp_path / "dst" / "s_x"
        import shutil
        shutil.copytree(src, dst)  # copy2 preserves mtime
        assert _is_skill_fresh(src, dst) is True

    def test_edited_source_file_is_not_fresh(self, tmp_path):
        src = _mkskill(tmp_path / "src", "s_x", {"SKILL.md": "a"})
        dst = tmp_path / "dst" / "s_x"
        import shutil
        shutil.copytree(src, dst)
        # Edit the source AFTER copy → source mtime advances past dest.
        time.sleep(0.01)
        (src / "SKILL.md").write_text("EDITED")
        assert _is_skill_fresh(src, dst) is False

    def test_deleted_source_file_is_not_fresh(self, tmp_path):
        # dst has an extra file the src no longer has → path-sets differ → stale.
        src = _mkskill(tmp_path / "src", "s_x", {"SKILL.md": "a"})
        dst = tmp_path / "dst" / "s_x"
        import shutil
        shutil.copytree(src, dst)
        (dst / "orphan.py").write_text("leftover")  # simulate a since-deleted source file
        assert _is_skill_fresh(src, dst) is False

    def test_added_source_file_is_not_fresh(self, tmp_path):
        src = _mkskill(tmp_path / "src", "s_x", {"SKILL.md": "a"})
        dst = tmp_path / "dst" / "s_x"
        import shutil
        shutil.copytree(src, dst)
        (src / "new.py").write_text("added")  # source gained a file
        assert _is_skill_fresh(src, dst) is False


# --- Bytecode-exclusion regression (run_6eaee58a, root-fix chat-brain-check Q3.2) ---
# project_skills() shutil.copytree'd source skills verbatim, with no ignore=, so a
# source skill's __pycache__/*.pyc landed in .claude/skills/ — the exact stray-.pyc
# that chat-brain-check Q3.2 flags. These tests drive the REAL project_skills() (not a
# raw copytree) so removing the ignore= makes them RED (Gate-1 W5: no test-theater).


@dataclass
class _FakeSkillInfo:
    """Minimal stand-in for core.skill_manager.SkillInfo (only fields project_skills reads)."""
    folder_name: str
    path: Path
    source_tier: str = "built-in"
    platform: str = "all"


class _FakeSkillManager:
    """Async get_cache() + tier-root attrs so _validate_skill_source passes."""

    def __init__(self, builtin_path: Path, cache: dict[str, _FakeSkillInfo]) -> None:
        self.builtin_path = builtin_path
        self.user_skills_path = builtin_path / "_user_none"
        self.plugin_skills_path = builtin_path / "_plugin_none"
        self._cache = cache

    async def get_cache(self) -> dict[str, _FakeSkillInfo]:
        return self._cache


def _projected_bytecode(skills_dir: Path) -> list[Path]:
    """All .pyc/.pyo files + __pycache__ dirs under a projected skills dir."""
    hits = list(skills_dir.rglob("*.pyc")) + list(skills_dir.rglob("*.pyo"))
    hits += [p for p in skills_dir.rglob("__pycache__") if p.is_dir()]
    return hits


class TestProjectionExcludesBytecode:
    """project_skills() must NOT copy __pycache__/*.pyc into .claude/skills/."""

    @pytest.mark.asyncio
    async def test_projected_skill_has_no_bytecode_but_keeps_source(self, tmp_path):
        # A source skill (under builtin_path so validation passes) with a real
        # source file AND a __pycache__/*.pyc that MUST NOT be projected.
        builtin = tmp_path / "builtin"
        src = _mkskill(builtin, "s_probe", {
            "SKILL.md": "# probe",
            "scripts/mod.py": "x = 1\n",
            "scripts/__pycache__/mod.cpython-312.pyc": "\x00\x00fake-bytecode",
        })
        mgr = _FakeSkillManager(builtin, {"s_probe": _FakeSkillInfo("s_probe", src)})
        ws = tmp_path / "ws"

        await ProjectionLayer(mgr).project_skills(ws, allow_all=True)

        dst = ws / ".claude" / "skills" / "s_probe"
        # AC2: real source files still copied intact.
        assert (dst / "SKILL.md").exists(), "SKILL.md must be projected"
        assert (dst / "scripts" / "mod.py").exists(), "source .py must be projected"
        # AC1: zero bytecode leaked (RED if ignore= is removed from copytree).
        assert _projected_bytecode(dst) == [], (
            f"bytecode leaked into projected skill: {_projected_bytecode(dst)}"
        )

    @pytest.mark.asyncio
    async def test_shared_projection_excludes_bytecode(self, tmp_path):
        # AC3: the _shared/ copytree site (projection_layer.py ~:271) must also
        # exclude bytecode. Drive the REAL project_skills(), then assert against
        # the ACTUAL _shared source projection_layer resolves. LOW-6: no silent
        # `if exists` green — if the _shared source is genuinely absent in this
        # env, pytest.skip with a visible reason (never a silent pass).
        import core.projection_layer as pl

        builtin = tmp_path / "builtin"
        src = _mkskill(builtin, "s_only", {"SKILL.md": "# s"})
        mgr = _FakeSkillManager(builtin, {"s_only": _FakeSkillInfo("s_only", src)})
        ws = tmp_path / "ws"

        await ProjectionLayer(mgr).project_skills(ws, allow_all=True)

        # Resolve _shared the same way project_skills does (module-relative source).
        shared_source = Path(pl.__file__).resolve().parent.parent / "skills" / "_shared"
        shared_dst = ws / ".claude" / "skills" / "_shared"
        if not shared_source.is_dir():
            pytest.skip(f"_shared source absent in this env ({shared_source}); "
                        "wiring covered by the skill-loop test above")
        # _shared source exists → it MUST have been projected AND be bytecode-free.
        assert shared_dst.exists(), "_shared source exists but was not projected"
        assert _projected_bytecode(shared_dst) == [], (
            f"bytecode leaked into projected _shared: {_projected_bytecode(shared_dst)}"
        )

    @pytest.mark.asyncio
    async def test_stale_projected_bytecode_is_self_healed(self, tmp_path):
        # MED-8 (run_6eaee58a): a dst projected BEFORE the ignore existed carries
        # stale __pycache__/*.pyc that the file-set fingerprint can't see (it skips
        # __pycache__). Simulate that pre-fix install, then re-project from an
        # UNCHANGED source: _is_skill_fresh must treat the bytecode-bearing dst as
        # NOT fresh → rmtree + clean re-copy purges it. RED if the _dst_has_bytecode
        # self-heal guard is removed (skip-when-fresh would keep the stale .pyc).
        builtin = tmp_path / "builtin"
        src = _mkskill(builtin, "s_stale", {
            "SKILL.md": "# stale",
            "scripts/mod.py": "y = 2\n",
        })
        mgr = _FakeSkillManager(builtin, {"s_stale": _FakeSkillInfo("s_stale", src)})
        ws = tmp_path / "ws"
        dst = ws / ".claude" / "skills" / "s_stale"

        # First projection (clean — no bytecode in source), then INJECT a stale
        # .pyc into the projected dst as a pre-fix boot would have left behind.
        await ProjectionLayer(mgr).project_skills(ws, allow_all=True)
        stale = dst / "scripts" / "__pycache__" / "mod.cpython-312.pyc"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("\x00\x00stale-bytecode")
        assert _projected_bytecode(dst), "precondition: stale bytecode injected"

        # Re-project from the UNCHANGED source. Without self-heal, skip-when-fresh
        # would leave the stale .pyc; with it, the dst is purged.
        await ProjectionLayer(mgr).project_skills(ws, allow_all=True)

        assert (dst / "scripts" / "mod.py").exists(), "source must survive re-copy"
        assert _projected_bytecode(dst) == [], (
            f"stale bytecode NOT self-healed: {_projected_bytecode(dst)}"
        )

    def test_dst_has_bytecode_scans_pycache_but_prunes_node_modules(self, tmp_path):
        # MED meta-review (run_6eaee58a): the bytecode hunter must (a) DETECT a
        # .pyc inside __pycache__ (that's where .pyc lives — pruning it would make
        # self-heal a no-op), and (b) NOT descend into node_modules (perf: a skill
        # like s_pollinate ships a huge node_modules that must not be walked every
        # boot). This locks both halves of the walker's skip policy.
        from core.projection_layer import _dst_has_bytecode

        # (a) .pyc inside __pycache__ IS detected.
        d1 = _mkskill(tmp_path, "d1", {"m.py": "x", "__pycache__/m.cpython-312.pyc": "\x00"})
        assert _dst_has_bytecode(d1) is True, "must detect .pyc inside __pycache__"

        # (b) a .pyc buried under node_modules is NOT counted (dir is pruned).
        d2 = _mkskill(tmp_path, "d2", {"m.py": "x", "node_modules/pkg/x.pyc": "\x00"})
        assert _dst_has_bytecode(d2) is False, "must NOT walk into node_modules"

        # (c) a clean dir has no bytecode.
        d3 = _mkskill(tmp_path, "d3", {"m.py": "x", "SKILL.md": "# s"})
        assert _dst_has_bytecode(d3) is False, "clean dir has no bytecode"


class TestUntrustedCopyIgnore:
    """make_untrusted_copy_ignore(root): drop symlinks whose realpath ESCAPES the
    source root (host-file exfil via an untrusted plugin), keep internal symlinks
    + regular files, and still exclude bytecode (COPY_IGNORE composition).
    run_0e5f1969 — pre-existing plugin_manager copytree(symlinks=False) deref leak.
    """

    def _copy(self, tmp_path, build):
        """Build a src via `build(src)`, copytree it through the untrusted ignore,
        return the dst Path."""
        import shutil
        from core.projection_layer import make_untrusted_copy_ignore
        src = tmp_path / "plugin"
        src.mkdir()
        build(src)
        dst = tmp_path / "projected"
        shutil.copytree(str(src), str(dst), ignore=make_untrusted_copy_ignore(src))
        return dst

    def test_escaping_symlink_is_not_dereferenced_into_dst(self, tmp_path):
        # An untrusted plugin ships a symlink escaping to a host secret. The fix
        # must NOT copy the target's content into the discoverable tree.
        # RED without the escape-drop: copytree(symlinks=False) derefs it → dst/leak
        # becomes a real file containing SECRET.
        secret = tmp_path / "outside_secret.txt"
        secret.write_text("SENSITIVE-KEY-MATERIAL")

        def build(src):
            (src / "SKILL.md").write_text("# ok")
            os.symlink(str(secret), str(src / "leak"))  # escapes the plugin dir

        dst = self._copy(tmp_path, build)
        assert (dst / "SKILL.md").exists(), "regular files must still copy"
        leak = dst / "leak"
        # Neither present as a deref'd file NOR as a live symlink to the secret.
        assert not leak.exists(), (
            "escaping symlink was projected (exfil): "
            f"exists={leak.exists()} is_symlink={leak.is_symlink()}"
        )

    def test_internal_symlink_is_preserved(self, tmp_path):
        # W2 positive case: an intra-plugin symlink (target inside the source root)
        # must be KEPT — blocks an over-broad "drop ALL symlinks" mutation.
        def build(src):
            (src / "real.py").write_text("x = 1\n")
            os.symlink("real.py", str(src / "alias.py"))  # relative, internal

        dst = self._copy(tmp_path, build)
        assert (dst / "real.py").exists(), "internal target must copy"
        alias = dst / "alias.py"
        assert alias.exists(), "internal symlink must be preserved (not dropped)"

    def test_bytecode_still_excluded_via_composition(self, tmp_path):
        # The untrusted ignore must ALSO honor COPY_IGNORE (bytecode) — a plugin
        # has both concerns at once.
        def build(src):
            (src / "SKILL.md").write_text("# ok")
            pyc = src / "scripts" / "__pycache__" / "m.cpython-312.pyc"
            pyc.parent.mkdir(parents=True)
            pyc.write_text("\x00\x00fake")

        dst = self._copy(tmp_path, build)
        assert (dst / "SKILL.md").exists()
        assert _projected_bytecode(dst) == [], (
            f"bytecode leaked despite COPY_IGNORE composition: {_projected_bytecode(dst)}"
        )

    def test_nested_escaping_symlink_is_dropped(self, tmp_path):
        # copytree recurses + calls ignore per-dir → an escaping symlink nested
        # several levels deep must also be dropped (not just top-level).
        secret = tmp_path / "deep_secret.txt"
        secret.write_text("DEEP-SECRET")

        def build(src):
            deep = src / "a" / "b" / "c"
            deep.mkdir(parents=True)
            (deep / "keep.py").write_text("k")
            os.symlink(str(secret), str(deep / "leak_deep"))

        dst = self._copy(tmp_path, build)
        assert (dst / "a" / "b" / "c" / "keep.py").exists()
        assert not (dst / "a" / "b" / "c" / "leak_deep").exists(), (
            "nested escaping symlink was projected (exfil)"
        )
