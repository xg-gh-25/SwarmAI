"""Deleting a Brain takes away what it owns outside its folder (core/brain_teardown):
its clones and graphs (moved, never unlinked), its local-roots rows, its job bodies and
their state, its name in user_context, its projected skills. What another Brain still
owns, and a user's own checkout, stay."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

_BINDINGS = """ddd_spec_version: "1.0"
brain_kind: code-repo
governed_assets:
  - kind: code-repo
    name: lib
  - kind: job
    name: gone-job
bindings:
  - repo: lib
    kind: external
    clone: "https://example.com/lib.git"
    delivery_contract:
      remote_kind: github-pr
      build_system: local-script
      branch: main
      deploy_pipeline: "none"
      refresh_policy: on-develop
      review_path: github-pr
      auto_send: manual-push
"""


@pytest.fixture
def env(tmp_path, monkeypatch, own_local_root):
    from core.code_intel import invalidate_cache
    ws = tmp_path / "SwarmWS"
    projects = ws / "Projects"
    gone = projects / "Gone"
    (gone / "4-capabilities" / "s_gone").mkdir(parents=True)
    (gone / "4-capabilities" / "s_gone" / "SKILL.md").write_text("---\nname: s_gone\n---\n")
    (gone / "bindings.yaml").write_text(_BINDINGS)
    (projects / "Keep").mkdir(parents=True)
    bindings = tmp_path / "bindings"
    (bindings / "lib").mkdir(parents=True)
    (bindings / "lib" / "unpushed.txt").write_text("work")
    for suffix in ("", "-wal"):
        (bindings / f"lib.code_intel.db{suffix}").write_text("db")
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", projects)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    checkout = tmp_path / "code" / "app"
    checkout.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    own_local_root("Gone", checkout)
    from core.code_intel.local_roots import db_path
    local_db = db_path(checkout)
    local_db.parent.mkdir(parents=True, exist_ok=True)
    local_db.write_text("db")
    # the Brain's job body and its state; another user job
    from jobs import job_manager, scheduler
    from jobs.models import JobState
    job_manager.USER_JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    job_manager._save_user_jobs([{"id": "gone-job", "name": "g"}, {"id": "other", "name": "o"}])
    st = scheduler.load_state()
    st.jobs["gone-job"] = JobState()
    st.jobs["other"] = JobState()
    scheduler.save_state(st)
    from jobs.config_io import mutate_config
    mutate_config(lambda c: c.setdefault("user_context", {}).__setitem__("projects", ["Gone", "Keep"]))
    # its domain skill: registered and projected
    (ws / ".context").mkdir(parents=True)
    (ws / ".context" / "ddd_skill_registry.json").write_text(json.dumps({"version": 1, "skills": [
        {"skill": "s_gone", "path": str(gone / "4-capabilities" / "s_gone"), "owner_ddd": "Gone"}]}))
    (ws / ".claude" / "skills" / "s_gone").mkdir(parents=True)
    invalidate_cache()
    yield {"ws": ws, "gone": gone, "bindings": bindings, "checkout": checkout, "local_db": local_db}
    invalidate_cache()


def _delete(env):
    from core import brain_teardown
    owned = brain_teardown.collect(env["gone"], env["ws"])
    trash = env["ws"] / "Projects" / ".trash" / "Gone-x"
    trash.parent.mkdir(parents=True, exist_ok=True)
    env["gone"].rename(trash)
    return trash, brain_teardown.take_away(owned, trash, env["ws"])


def test_deleting_a_brain_takes_away_what_it_owns_outside_its_folder(env):
    trash, report = _delete(env)
    assert "failed" not in report, report
    out = trash / "_outside"
    # clone and graphs moved, not unlinked (the clone keeps its unpushed work)
    assert not (env["bindings"] / "lib").exists()
    assert (out / "clones" / "lib" / "unpushed.txt").read_text() == "work"
    assert not list(env["bindings"].glob("lib.code_intel.db*"))
    assert {p.name for p in (out / "code-intel").iterdir()} >= {
        "lib.code_intel.db", "lib.code_intel.db-wal", env["local_db"].name}
    # the user's own checkout is untouched
    assert (env["checkout"] / ".git").is_dir()
    # registry, jobs, state, config, skills
    from core.code_intel.local_roots import registry_path
    assert "Gone" not in json.loads(registry_path().read_text())["projects"]
    from jobs import job_manager, scheduler
    assert [j["id"] for j in job_manager._get_user_jobs()] == ["other"]
    assert yaml.safe_load((out / "user-jobs.yaml").read_text())["jobs"][0]["id"] == "gone-job"
    assert set(scheduler.load_state().jobs) == {"other"}
    from jobs.config_io import read_config
    assert read_config()["user_context"]["projects"] == ["Keep"]
    assert not (env["ws"] / ".claude" / "skills" / "s_gone").exists()
    from core import ddd_skill_registry
    assert not any(r["skill"] == "s_gone" for r in ddd_skill_registry.read_manifest(env["ws"]))


def test_what_another_brain_still_owns_stays(env, own_local_root):
    own_local_root("Keep", env["checkout"])         # the same checkout, one shared graph
    (env["ws"] / "Projects" / "Keep" / "bindings.yaml").write_text(_BINDINGS)  # the same clone
    _delete(env)
    assert env["local_db"].exists()
    assert (env["bindings"] / "lib" / "unpushed.txt").exists()
    assert (env["bindings"] / "lib.code_intel.db").exists()


def test_one_failing_step_does_not_stop_the_others(env, monkeypatch):
    from core.code_intel import local_roots
    def _boom(project):
        raise local_roots.LocalRootError("registry unreadable")
    monkeypatch.setattr(local_roots, "forget_project", _boom)
    _, report = _delete(env)
    assert any(f.startswith("local_roots") for f in report["failed"])
    from jobs import job_manager
    assert [j["id"] for j in job_manager._get_user_jobs()] == ["other"]
    assert not (env["bindings"] / "lib").exists()


@pytest.mark.asyncio
async def test_delete_project_runs_the_teardown(env, monkeypatch):
    from core import brain_teardown
    from core.swarm_workspace_manager import swarm_workspace_manager as mgr
    seen = []
    monkeypatch.setattr(brain_teardown, "take_away",
                        lambda owned, dest, ws: seen.append((owned.name, Path(dest).parent.name)))
    monkeypatch.setattr(mgr, "_find_project_dir", lambda pid, ws: env["gone"])
    await mgr.delete_project("some-id", str(env["ws"]))
    assert seen == [("Gone", ".trash")]
