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

import time
from pathlib import Path

import pytest

from core.projection_layer import _is_skill_fresh


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
