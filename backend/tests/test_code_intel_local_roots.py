"""Local roots: the one ownership declaration for a user's own checkout.

An agent may propose a directory; only a person's Confirm (a Brain Hub endpoint) makes
it owned, and only an owned repo is parsed into a Brain. Nothing a document says
grants ownership any more: a TECH.md path is only a pending suggestion, a bindings.yaml
worktree path counts for nothing (design 2026-10-29-code-intel-unification §0.3).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@e", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], check=True)
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    from core.code_intel import invalidate_cache
    home = tmp_path / "home"
    ws = home / ".swarm-ai" / "SwarmWS"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    bindings = home / ".swarm-ai" / "bindings"
    bindings.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    repo = _git_repo(home / "code" / "app")
    invalidate_cache()
    yield {"home": home, "ws": ws, "bindings": bindings, "repo": repo,
           "project": ws / "Projects" / "Demo"}
    invalidate_cache()


def test_refused_paths(env):
    """Each guard on its own: home, SwarmWS, its parent and the bindings root are git
    repos here, so the git-top check alone cannot be what refuses them (a dotfiles
    checkout makes $HOME a git repo)."""
    from core.code_intel.local_roots import LocalRootError, validate
    for top in (env["home"], env["ws"].parent, env["ws"], env["bindings"]):
        subprocess.run(["git", "init", "-q", str(top)], check=True)
    sub = env["repo"] / "pkg"
    sub.mkdir()
    inside_ws = _git_repo(env["ws"] / "Projects" / "Demo" / "vendored")
    in_bindings = _git_repo(env["bindings"] / "clone")
    for bad, why in ((env["home"], "home directory"), (env["ws"], "SwarmWS or a directory"),
                     (env["ws"].parent, "SwarmWS or a directory"), (inside_ws, "inside SwarmWS"),
                     (in_bindings, "bindings directory"), (env["bindings"], "bindings directory"),
                     (sub, "not the top"), (env["home"] / "missing", "no such directory")):
        with pytest.raises(LocalRootError, match=why):
            validate(bad)
    assert validate(env["repo"]) == str(env["repo"].resolve())


def test_a_proposed_root_owns_nothing_until_a_person_confirms_it(env):
    from core.code_intel import owned_repo_roots, repo_root_is_owned
    from core.code_intel.local_roots import confirm, propose
    entry = propose("Demo", env["repo"])
    assert entry["state"] == "pending"
    assert owned_repo_roots(env["project"]) == []
    assert not repo_root_is_owned(env["project"], str(env["repo"]))
    confirm("Demo", env["repo"])
    assert owned_repo_roots(env["project"]) == [str(env["repo"].resolve())]
    assert repo_root_is_owned(env["project"], str(env["repo"]))


def test_a_tech_md_path_is_only_a_suggestion_and_a_removed_one_stays_removed(env):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import entries, remove, suggest_from_tech_md
    (env["project"] / "TECH.md").write_text(f"# TECH\n\n**Local:** `{env['repo']}`\n",
                                            encoding="utf-8")
    assert owned_repo_roots(env["project"]) == []          # the doc grants nothing
    assert suggest_from_tech_md(env["project"])["state"] == "pending"
    assert [e["source"] for e in entries("Demo")] == ["tech-md"]
    assert remove("Demo", env["repo"])
    assert suggest_from_tech_md(env["project"]) is None     # not suggested again
    assert entries("Demo") == []


def test_a_bindings_worktree_outside_the_root_owns_nothing(env):
    from core.code_intel import owned_graphs, owned_repo_roots
    (env["project"] / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n    clone: \"https://example.com/app.git\"\n"
        f"    worktree: {env['repo']}\n    delivery_contract:\n      remote_kind: github-pr\n"
        "      branch: main\n      review_path: github-pr\n      auto_send: manual-push\n",
        encoding="utf-8")
    assert owned_repo_roots(env["project"]) == []
    assert owned_graphs(env["project"]) == []


def test_a_confirmed_root_gets_a_graph_keyed_by_its_path(env):
    from core.code_intel import code_intel_dbs, owned_graphs, project_graph
    from core.code_intel.build import ensure_graph
    from core.code_intel.local_roots import confirm, db_path, propose
    propose("Demo", env["repo"])
    confirm("Demo", env["repo"])
    (ref,) = owned_graphs(env["project"])
    assert ref.repo == "app" and ref.db_path == db_path(env["repo"])
    assert code_intel_dbs(env["project"]) == []                # not built yet
    rows = ensure_graph(env["project"])
    assert [r["action"] for r in rows] == ["built"]
    assert [n["id"] for n in project_graph("Demo")[1].get_nodes_by_file("m.py")] == ["m.py::f"]
    assert [r["action"] for r in ensure_graph(env["project"])] == ["exists"]


def test_bind_local_only_proposes(env, capsys):
    import scripts.artifact_cli as cli
    from core.code_intel import owned_repo_roots
    cli._bind_local("Demo", str(env["repo"]))
    out = json.loads(capsys.readouterr().out)
    assert out["local_root"]["state"] == "pending"
    assert owned_repo_roots(env["project"]) == []
    with pytest.raises(SystemExit):
        cli._bind_local("Demo", str(env["home"]))


def test_confirm_endpoint_owns_the_root_and_starts_the_build_child(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.ddd_brain as mod
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import propose
    started = []
    monkeypatch.setattr("core.code_intel.build.spawn_build",
                        lambda project, repo=None, full=False: started.append((project, repo)))
    monkeypatch.setattr(mod, "_resolve_brain_dir", lambda name: env["project"] if name == "Demo" else None)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/ddd")
    client = TestClient(app)
    propose("Demo", env["repo"])
    rows = client.get("/api/ddd/brains/Demo/local-roots").json()["localRoots"]
    assert [r["state"] for r in rows] == ["pending"]
    r = client.post("/api/ddd/brains/Demo/local-roots/confirm", json={"path": str(env["repo"])})
    assert r.status_code == 200
    assert owned_repo_roots(env["project"]) == [str(env["repo"].resolve())]
    assert started == [("Demo", "app")]
    bad = client.post("/api/ddd/brains/Demo/local-roots/confirm", json={"path": str(env["home"])})
    assert bad.status_code == 400
    gone = client.post("/api/ddd/brains/Demo/local-roots/remove", json={"path": str(env["repo"])})
    assert gone.status_code == 200 and owned_repo_roots(env["project"]) == []


def test_an_unreadable_registry_owns_nothing_and_is_not_overwritten(env):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import LocalRootError, propose, registry_path
    registry_path().write_text("{not json", encoding="utf-8")
    assert owned_repo_roots(env["project"]) == []
    with pytest.raises(LocalRootError):
        propose("Demo", env["repo"])
    assert registry_path().read_text(encoding="utf-8") == "{not json"


def test_a_symlink_or_another_case_is_checked_as_the_directory_it_is(env):
    from core.code_intel.local_roots import LocalRootError, validate
    link = env["home"] / "ws-link"
    link.symlink_to(env["ws"], target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(env["ws"])], check=True)
    with pytest.raises(LocalRootError):
        validate(link)
    repo_link = env["home"] / "app-link"
    repo_link.symlink_to(env["repo"], target_is_directory=True)
    assert validate(repo_link) == str(env["repo"].resolve())
    swapped = Path(str(env["repo"])[:-3] + "APP")
    if swapped.exists():                        # a case-insensitive disk (macOS default)
        assert validate(swapped) == str(env["repo"].resolve())


def test_a_confirmed_root_replaced_by_a_symlink_is_not_followed(env):
    import shutil
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import confirm, propose
    propose("Demo", env["repo"])
    confirm("Demo", env["repo"])
    shutil.rmtree(env["repo"])
    other = _git_repo(env["home"] / "elsewhere")
    env["repo"].symlink_to(other, target_is_directory=True)
    assert owned_repo_roots(env["project"]) == []


def test_a_hand_edited_relative_entry_owns_nothing(env):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import registry_path
    registry_path().write_text(json.dumps({"version": 1, "projects": {"Demo": [
        {"path": ".", "state": "confirmed"}, {"path": str(env["ws"]), "state": "confirmed"}]}}))
    assert owned_repo_roots(env["project"]) == []


def _client(env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import routers.ddd_brain as mod
    monkeypatch.setattr(mod, "_resolve_brain_dir", lambda name: env["project"] if name == "Demo" else None)
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/ddd")
    return TestClient(app)


def test_confirm_refuses_a_path_never_proposed_and_refuses_on_a_hive(env, monkeypatch):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import propose
    monkeypatch.setattr("core.code_intel.build.spawn_build",
                        lambda *a, **k: pytest.fail("a build started"))
    client = _client(env, monkeypatch)
    r = client.post("/api/ddd/brains/Demo/local-roots/confirm", json={"path": str(env["repo"])})
    assert r.status_code == 400 and "not proposed" in r.json()["detail"]
    propose("Demo", env["repo"])
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    r = client.post("/api/ddd/brains/Demo/local-roots/confirm", json={"path": str(env["repo"])})
    assert r.status_code == 403
    assert owned_repo_roots(env["project"]) == []


def test_bind_local_files_the_entry_under_the_projects_own_name(env, capsys):
    import scripts.artifact_cli as cli
    from core.code_intel.local_roots import entries
    cli._bind_local("demo", str(env["repo"]))
    assert [e["path"] for e in entries("Demo")] == [str(env["repo"].resolve())]
    assert entries("demo") == []
    with pytest.raises(SystemExit):
        cli._bind_local("NoSuchBrain", str(env["repo"]))
