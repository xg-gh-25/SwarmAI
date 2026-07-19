"""E2E: DDD-tier skill discovery + projection through SkillManager + ProjectionLayer.

Run 2 (REGISTRY+MOUNT, run_597f4ed1). Proves the capability-package mechanism:
- a DDD's DOMAIN skill (declared in aim.json, living in Projects/<x>/skills/) is
  discovered by SkillManager as source_tier="ddd" via the registry manifest
- it is PROJECTED (applicable) — but ALLOW-LIST-GATED like user/plugin, NOT
  always-on like built-in (Gate-1 CRITICAL-1 auth fix)
- built-in SHADOWS a same-named ddd skill (precedence built-in > ddd)
- enablement skills are excluded (never become ddd tier)

Uses a temp workspace fixture — the only NON-shadowed validation this run (the
9 real s_cmhk-* still live in built-in until Run 3).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import ddd_skill_registry as reg
from core.skill_manager import SkillManager
from core.projection_layer import ProjectionLayer


def _mkskill(root: Path, name: str, body: str = "body") -> Path:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d {name}\n---\n{body}")
    return d


def _mk_ddd(ws: Path, project: str, domain: list[str]) -> None:
    pdir = ws / "Projects" / project
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "aim.json").write_text(json.dumps({"plugins": {"domain_skills": domain}}))
    for s in domain:
        _mkskill(pdir / "skills", s)


@pytest.fixture
def env(tmp_path):
    ws = tmp_path / "SwarmWS"
    builtin = tmp_path / "backend_skills"; builtin.mkdir(parents=True)
    user = tmp_path / "user_skills"; user.mkdir()
    plugin = tmp_path / "plugin_skills"; plugin.mkdir()
    sm = SkillManager(
        builtin_path=builtin, user_skills_path=user,
        plugin_skills_path=plugin, workspace_root=ws,
    )
    return ws, builtin, sm


@pytest.mark.asyncio
async def test_domain_skill_discovered_as_ddd_tier(env):
    ws, builtin, sm = env
    _mkskill(builtin, "s_persist")  # a real built-in
    _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
    reg.build_manifest(ws, builtin)

    cache = await sm.scan_all()
    assert "s_cmhk-weekly-report" in cache
    assert cache["s_cmhk-weekly-report"].source_tier == "ddd"
    assert cache["s_persist"].source_tier == "built-in"


@pytest.mark.asyncio
async def test_builtin_shadows_same_named_ddd_skill(env):
    """Precedence built-in > ddd: a name in BOTH is served by built-in."""
    ws, builtin, sm = env
    _mkskill(builtin, "s_cmhk-weekly-report")  # ALSO in built-in (pre-Run-3 reality)
    _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
    reg.build_manifest(ws, builtin)

    cache = await sm.scan_all()
    # built-in wins → this run's ddd entry is a harmless shadow (the strangler no-op)
    assert cache["s_cmhk-weekly-report"].source_tier == "built-in"


@pytest.mark.asyncio
async def test_ddd_skill_projected_when_allow_all(env, tmp_path):
    """apply: allow_all agent (SwarmAI default) projects the ddd domain skill."""
    ws, builtin, sm = env
    _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
    reg.build_manifest(ws, builtin)
    await sm.scan_all()

    proj = ProjectionLayer(sm)
    dest_ws = tmp_path / "dest_ws"
    await proj.project_skills(dest_ws, allow_all=True)
    projected = dest_ws / ".claude" / "skills" / "s_cmhk-weekly-report"
    assert projected.is_dir(), "ddd domain skill must be projected under allow_all"


@pytest.mark.asyncio
async def test_ddd_skill_NOT_projected_when_restricted_and_not_allowlisted(env, tmp_path):
    """CRITICAL-1 auth fix: a restricted agent (not allow_all, empty allow-list)
    must NOT receive a ddd domain skill — it is allow-list-gated, NOT always-on."""
    ws, builtin, sm = env
    _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
    reg.build_manifest(ws, builtin)
    await sm.scan_all()

    proj = ProjectionLayer(sm)
    dest_ws = tmp_path / "dest_ws"
    await proj.project_skills(dest_ws, allowed_skills=[], allow_all=False)
    projected = dest_ws / ".claude" / "skills" / "s_cmhk-weekly-report"
    assert not projected.exists(), "ddd domain skill must NOT leak to a restricted agent"


@pytest.mark.asyncio
async def test_ddd_skill_projected_when_explicitly_allowlisted(env, tmp_path):
    ws, builtin, sm = env
    _mk_ddd(ws, "CMHK", ["s_cmhk-weekly-report"])
    reg.build_manifest(ws, builtin)
    await sm.scan_all()

    proj = ProjectionLayer(sm)
    dest_ws = tmp_path / "dest_ws"
    await proj.project_skills(
        dest_ws, allowed_skills=["s_cmhk-weekly-report"], allow_all=False,
    )
    projected = dest_ws / ".claude" / "skills" / "s_cmhk-weekly-report"
    assert projected.is_dir(), "explicitly allow-listed ddd skill must be projected"


@pytest.mark.asyncio
async def test_empty_registry_is_noop_builtin_intact(env):
    """Production-safety: no manifest → ddd tier no-op, built-in discovery unchanged."""
    ws, builtin, sm = env
    _mkskill(builtin, "s_persist")
    # no build_manifest call → no manifest file
    cache = await sm.scan_all()
    assert "s_persist" in cache
    assert cache["s_persist"].source_tier == "built-in"
    assert not any(i.source_tier == "ddd" for i in cache.values())


@pytest.mark.asyncio
async def test_hostile_manifest_path_rejected_from_cache(env, tmp_path):
    """Gate-2 MED: a manifest path escaping the ddd roots (../ , symlink, absolute)
    must NOT enter the discovery cache — parity with _scan_tier's containment guard."""
    ws, builtin, sm = env
    (ws / ".context").mkdir(parents=True)
    # Plant a real skill OUTSIDE the roots, and a symlink inside Projects pointing out.
    outside = tmp_path / "evil"; _mkskill(outside, "s_evil")
    (ws / reg.MANIFEST_RELPATH).write_text(json.dumps({
        "version": 1,
        "skills": [
            {"skill": "s_evil", "path": str(outside / "s_evil"), "owner_ddd": "X"},
        ],
    }))
    _mkskill(builtin, "s_persist")
    cache = await sm.scan_all()
    assert "s_evil" not in cache, "out-of-root ddd path must not enter the cache"
    assert "s_persist" in cache  # discovery otherwise intact


@pytest.mark.asyncio
async def test_malformed_manifest_does_not_break_discovery(env):
    """HIGH-4: a torn/malformed manifest must NOT take down skill discovery."""
    ws, builtin, sm = env
    _mkskill(builtin, "s_persist")
    (ws / ".context").mkdir(parents=True)
    (ws / reg.MANIFEST_RELPATH).write_text("{ broken json ")
    cache = await sm.scan_all()  # must NOT raise
    assert "s_persist" in cache  # built-in discovery survives
    assert not any(i.source_tier == "ddd" for i in cache.values())
