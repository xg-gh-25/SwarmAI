"""Deleting a Brain takes away what it owns outside its folder (core/brain_teardown):
its clones and graphs (moved, never unlinked), its local-roots rows, its job bodies and
their state, its name in user_context; its projected skills go with the registry
refresh. What another Brain still owns, what cannot be shown to be its own, and a
user's own checkout, stay."""
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
    (bindings / "lib" / ".git").mkdir(parents=True)
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
    job_manager._save_user_jobs([
        {"id": "gone-job", "name": "g", "config": {"command": "python Projects/Gone/4-capabilities/x.py"}},
        {"id": "other", "name": "o"}])
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
    owned = brain_teardown.collect(env["gone"])
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

    async def _refresh(ws):
        seen.append(("refresh", Path(ws)))
    monkeypatch.setattr(brain_teardown, "refresh_registries", _refresh)
    monkeypatch.setattr(mgr, "_find_project_dir", lambda pid, ws: env["gone"])
    await mgr.delete_project("some-id", str(env["ws"]))
    assert seen == [("Gone", ".trash"), ("refresh", env["ws"])]


@pytest.mark.asyncio
async def test_the_registry_refresh_drops_the_brains_projected_skill(env, monkeypatch):
    """Skills are not moved by name: the refresh rebuilds the manifest from Projects/
    and re-projects, and the projection drops the copy whose source is gone."""
    from core import brain_teardown, ddd_skill_registry
    from core.skill_manager import skill_manager
    monkeypatch.setattr(skill_manager, "workspace_root", skill_manager.workspace_root)
    seen = []

    async def _scan():
        seen.append("scan")

    async def _cache():
        return {}                      # no skill in any tier any more
    monkeypatch.setattr(skill_manager, "scan_all", _scan)
    monkeypatch.setattr(skill_manager, "get_cache", _cache)
    _delete(env)
    await brain_teardown.refresh_registries(env["ws"])
    assert not any(r["skill"] == "s_gone" for r in ddd_skill_registry.read_manifest(env["ws"]))
    assert not (env["ws"] / ".claude" / "skills" / "s_gone").exists()
    assert seen == ["scan"]


def test_a_brain_whose_ownership_cannot_be_read_keeps_everything_shared(env):
    """Another Brain's bindings.yaml does not parse: it may own the clone, the graph
    or the job, so none of them moves ("cannot tell" is never "nobody owns it")."""
    (env["ws"] / "Projects" / "Keep" / "bindings.yaml").write_text("bindings: [unclosed\n")
    _, report = _delete(env)
    assert any(f.startswith("ownership") for f in report["failed"])
    assert (env["bindings"] / "lib" / "unpushed.txt").exists()
    assert (env["bindings"] / "lib.code_intel.db").exists() and env["local_db"].exists()
    from jobs import job_manager
    assert {j["id"] for j in job_manager._get_user_jobs()} == {"gone-job", "other"}


def test_names_the_bindings_root_keeps_are_never_a_clone(env):
    """A binding naming the root's own entries (local-root graphs, the registry) is
    refused, so deleting its Brain cannot move them."""
    from core.code_intel.local_roots import registry_path
    reg = registry_path()
    root = env["bindings"]
    (root / "local").mkdir(exist_ok=True)
    (root / "local" / "x.code_intel.db").write_text("db")
    (root / "local_roots.json").write_text(reg.read_text())
    (root / "hive" / ".git").mkdir(parents=True)
    contract = _BINDINGS.split("    delivery_contract:\n", 1)[1]
    text = _BINDINGS + "".join(
        f"  - repo: r{i}\n    kind: external\n    worktree: {wt}\n"
        f"    clone: \"https://example.com/r{i}.git\"\n    delivery_contract:\n{contract}"
        for i, wt in enumerate(("local", "local_roots.json", "hive")))
    (env["gone"] / "bindings.yaml").write_text(text)
    from core.ddd_bindings import load_bindings
    assert len(load_bindings(env["gone"] / "bindings.yaml").bindings) == 4
    _delete(env)
    assert (root / "local" / "x.code_intel.db").exists()
    assert (root / "local_roots.json").exists() and (root / "hive" / ".git").is_dir()


def test_a_job_another_brain_or_the_system_declares_stays(env):
    from jobs import job_manager, scheduler
    from jobs.models import JobState
    system_id = sorted(job_manager._get_all_system_job_ids())[0]
    (env["ws"] / "Projects" / "Keep" / "bindings.yaml").write_text(
        'ddd_spec_version: "1.0"\ngoverned_assets:\n  - kind: job\n    name: other\n')
    text = _BINDINGS.replace("    name: gone-job\n", "    name: gone-job\n  - kind: job\n"
                             f"    name: other\n  - kind: job\n    name: {system_id}\n"
                             "  - kind: job\n    name: phantom\n")
    (env["gone"] / "bindings.yaml").write_text(text)
    st = scheduler.load_state()
    st.jobs[system_id] = JobState()
    st.jobs["phantom"] = JobState()     # declared, no body in user-jobs.yaml
    scheduler.save_state(st)
    _, report = _delete(env)
    assert report["jobs"] == ["gone-job"]
    assert [j["id"] for j in job_manager._get_user_jobs()] == ["other"]
    assert set(scheduler.load_state().jobs) == {"other", system_id, "phantom"}


def test_a_shared_local_root_on_an_unmounted_disk_keeps_its_graph(env, own_local_root):
    """The other Brain's root is not readable right now (disk unmounted): it still
    owns the shared graph."""
    import shutil
    own_local_root("Keep", env["checkout"])
    shutil.rmtree(env["checkout"])
    _delete(env)
    assert env["local_db"].exists()


def test_a_moved_db_keeps_its_sidecars_paired_on_a_name_clash(env):
    from core import brain_teardown
    owned = brain_teardown.collect(env["gone"])
    trash = env["ws"] / "Projects" / ".trash" / "Gone-x"
    (trash / "_outside" / "code-intel").mkdir(parents=True)
    (trash / "_outside" / "code-intel" / "lib.code_intel.db").write_text("older")
    for p in env["gone"].iterdir():
        p.rename(trash / p.name)
    brain_teardown.take_away(owned, trash, env["ws"])
    names = {p.name for p in (trash / "_outside" / "code-intel").iterdir()}
    assert {"lib.code_intel.db.1", "lib.code_intel.db.1-wal"} <= names
    assert "lib.code_intel.db-wal" not in names


def test_a_clone_being_bound_stays(env):
    from core.ddd_bindings import _bind_lock
    with _bind_lock(env["bindings"] / "lib", "lib"):
        _, report = _delete(env)
    assert (env["bindings"] / "lib" / "unpushed.txt").exists()
    assert any("bind of lib is running" in f for f in report["failed"])


def test_its_own_local_root_graph_moves_even_with_the_disk_unmounted(env):
    import shutil
    shutil.rmtree(env["checkout"])
    trash, _ = _delete(env)
    assert not env["local_db"].exists()
    assert (trash / "_outside" / "code-intel" / env["local_db"].name).exists()


def test_a_cached_graph_store_of_a_moved_db_is_closed(env):
    from core import code_intel as ci
    closed = []

    class _G:
        _db_path = "x"

        def close(self):
            closed.append(True)
    db = env["bindings"] / "lib.code_intel.db"
    ci._db_graph_cache[str(db)] = (_G(), (0, 0))
    _delete(env)
    assert closed == [True] and str(db) not in ci._db_graph_cache


def test_a_user_job_sharing_the_id_but_not_the_folder_stays(env):
    from jobs import job_manager
    job_manager._save_user_jobs([{"id": "gone-job", "name": "mine", "config": {"command": "echo hi"}},
                                 {"id": "other", "name": "o"}])
    _, report = _delete(env)
    assert report["jobs"] == []
    assert {j["id"] for j in job_manager._get_user_jobs()} == {"gone-job", "other"}


def test_an_unreadable_local_roots_registry_keeps_everything(env):
    from core.code_intel.local_roots import registry_path
    registry_path().write_text("{not json")
    _, report = _delete(env)
    assert any(f.startswith("ownership") for f in report["failed"])
    assert (env["bindings"] / "lib" / "unpushed.txt").exists() and env["local_db"].exists()
    from jobs import job_manager
    assert {j["id"] for j in job_manager._get_user_jobs()} == {"gone-job", "other"}


def test_another_brain_failing_any_other_way_keeps_everything(env, monkeypatch):
    from core import brain_teardown
    real = brain_teardown._ownership

    def _own(p, reg):
        if p.name == "Keep":
            raise RuntimeError("symlink loop")
        return real(p, reg)
    monkeypatch.setattr(brain_teardown, "_ownership", _own)
    _, report = _delete(env)
    assert any("symlink loop" in f for f in report["failed"])
    assert (env["bindings"] / "lib" / "unpushed.txt").exists()


@pytest.mark.parametrize("name", ["local", "Local", "HIVE", "local_roots.json",
                                  "Local_Roots.json.lock", "x.Code_Intel.DB", "x.BIND.LOCK"])
def test_a_name_the_bindings_root_keeps_is_never_a_worktree(tmp_path, name):
    from core.ddd_bindings import binding_worktree, load_bindings
    f = tmp_path / "bindings.yaml"
    f.write_text(_BINDINGS.replace("  - repo: lib\n", f"  - repo: lib\n    worktree: {name}\n"))
    b = load_bindings(f).bindings[0]
    with pytest.raises(ValueError, match="reserved"):
        binding_worktree(b, tmp_path / "root")
