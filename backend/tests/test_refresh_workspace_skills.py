"""refresh_workspace_skills projects the skills of a Brain installed or deleted after boot.

The bug: the refresh re-scanned the tiers with ``scan_all()``, which returns a fresh
dict but leaves ``SkillManager``'s cache as it was; ``project_skills`` then read the
cache (``get_cache``), still valid from boot, so a Brain synced to a Hive later never
got its domain skills into ``.claude/skills`` (the session reported "Unknown skill"),
and a deleted Brain's skills were never removed.
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from core import projection_layer
from core.skill_manager import SkillManager


def _skill(dir_: Path, name: str) -> None:
    d = dir_ / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill {name}\n---\n# {name}\n")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    builtin = tmp_path / "builtin"
    _skill(builtin, "s_core")
    workspace = tmp_path / "SwarmWS"
    (workspace / "Projects").mkdir(parents=True)
    sm = SkillManager(builtin_path=builtin, user_skills_path=tmp_path / "user",
                      plugin_skills_path=tmp_path / "plugin", workspace_root=workspace)
    monkeypatch.setattr("core.skill_manager.skill_manager", sm)
    monkeypatch.setattr(projection_layer, "_refresh_lock", None)
    # boot: the cache is filled before any Brain exists
    asyncio.run(projection_layer.refresh_workspace_skills(workspace, why="boot"))
    assert (workspace / ".claude" / "skills" / "s_core").is_dir()
    return workspace


def test_a_brain_installed_after_boot_gets_its_skills_projected(ws):
    _skill(ws / "Projects" / "Late" / "4-capabilities", "s_late-review")
    asyncio.run(projection_layer.refresh_workspace_skills(ws, why="hive brain install"))
    assert (ws / ".claude" / "skills" / "s_late-review" / "SKILL.md").is_file()


def test_a_deleted_brain_loses_its_projected_skills(ws):
    _skill(ws / "Projects" / "Gone" / "4-capabilities", "s_gone-review")
    asyncio.run(projection_layer.refresh_workspace_skills(ws, why="hive brain install"))
    assert (ws / ".claude" / "skills" / "s_gone-review").exists()
    shutil.rmtree(ws / "Projects" / "Gone")
    asyncio.run(projection_layer.refresh_workspace_skills(ws, why="a Brain delete"))
    assert not (ws / ".claude" / "skills" / "s_gone-review").exists()
