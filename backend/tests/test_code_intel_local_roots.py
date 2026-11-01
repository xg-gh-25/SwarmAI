"""Local roots: the one ownership declaration for a user's own checkout.

A directory declared on this machine (``bind --local``) is owned at once and its graph
built by a child; Brain Hub lists it with a Remove (design 2026-10-29-code-intel-auto
D1). Nothing a document says grants ownership: a TECH.md path counts once, in the
install-time migration; a bindings.yaml worktree path counts for nothing.
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


def test_a_bindings_worktree_outside_the_root_owns_nothing(env):
    from core.code_intel import owned_graphs, owned_repo_roots
    (env["project"] / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n    clone: \"https://example.com/app.git\"\n"
        f"    worktree: {env['repo']}\n    delivery_contract:\n      remote_kind: github-pr\n"
        "      branch: main\n      review_path: github-pr\n      auto_send: manual-push\n",
        encoding="utf-8")
    assert owned_repo_roots(env["project"]) == []
    assert owned_graphs(env["project"]) == []


def test_a_declared_root_gets_a_graph_keyed_by_its_path(env):
    from core.code_intel import code_intel_dbs, owned_graphs, project_graph
    from core.code_intel.build import ensure_graph
    from core.code_intel.local_roots import db_path, declare
    declare("Demo", env["repo"])
    (ref,) = owned_graphs(env["project"])
    assert ref.repo == "app" and ref.db_path == db_path(env["repo"])
    assert code_intel_dbs(env["project"]) == []                # not built yet
    rows = ensure_graph(env["project"])
    assert [r["action"] for r in rows] == ["built"]
    assert [n["id"] for n in project_graph("Demo")[1].get_nodes_by_file("m.py")] == ["m.py::f"]
    assert [r["action"] for r in ensure_graph(env["project"])] == ["exists"]


def test_an_unreadable_registry_owns_nothing_and_is_not_overwritten(env):
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import LocalRootError, declare, registry_path
    registry_path().write_text("{not json", encoding="utf-8")
    assert owned_repo_roots(env["project"]) == []
    with pytest.raises(LocalRootError):
        declare("Demo", env["repo"])
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


def test_a_declared_root_replaced_by_a_symlink_is_not_followed(env):
    import shutil
    from core.code_intel import owned_repo_roots
    from core.code_intel.local_roots import declare
    declare("Demo", env["repo"])
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


def test_bind_local_files_the_entry_under_the_projects_own_name(env, capsys, monkeypatch):
    import scripts.artifact_cli as cli
    from core.code_intel.local_roots import entries
    monkeypatch.setattr("core.code_intel.build.spawn_build", lambda *a, **k: None)
    cli._bind_local("demo", str(env["repo"]))
    assert [e["path"] for e in entries("Demo")] == [str(env["repo"].resolve())]
    assert entries("demo") == []
    with pytest.raises(SystemExit):
        cli._bind_local("NoSuchBrain", str(env["repo"]))


def test_bind_local_records_the_checkout_as_a_binding_bound_in_place(env, capsys, monkeypatch):
    """zero-touch R3.3: the rest of the Brain (Project Agent, a Hive task, P6) finds the
    checkout in bindings.yaml; recording it twice adds nothing; a bind never clones over it."""
    import yaml
    import scripts.artifact_cli as cli
    from core.ddd_bindings import bind_project
    from core.project_agent import repo_roots
    monkeypatch.setattr("core.code_intel.build.spawn_build", lambda *a, **k: None)
    cli._bind_local("Demo", str(env["repo"]))
    cli._bind_local("Demo", str(env["repo"]))
    rows = yaml.safe_load((env["project"] / "bindings.yaml").read_text())["bindings"]
    assert [(r["repo"], r["worktree"]) for r in rows] == [("app", str(env["repo"].resolve()))]
    assert repo_roots(env["project"]) == {"app": env["repo"].resolve()}
    out = bind_project("Demo")
    assert [(o.repo, o.status, o.worktree) for o in out] == [("app", "bound", str(env["repo"].resolve()))]
    assert (env["repo"] / "README.md").exists() or any(env["repo"].iterdir())   # untouched


def test_the_desktop_sidecar_is_a_local_mode(monkeypatch):
    from core.code_intel.local_roots import local_mode
    for mode, ok in (("", True), ("daemon", True), ("subprocess", True), ("hive", False)):
        monkeypatch.setenv("SWARMAI_MODE", mode)
        assert local_mode() is ok, mode
