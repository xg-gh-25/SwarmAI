"""Where a Brain's repository lives, and binding one from a URL (fixing-spec
docs/design/2026-11-02-new-brain-setup-fixing-spec.md G1).

- a URL is normalised (no scheme -> https) and refused with credentials or a scheme
  git would run code for, before anything is cloned or written;
- ``bind --url`` clones into the repository workspace and reads it in place: the
  binding's worktree is that checkout, the Project Agent's placement says
  ``repos_dir:<name>``; the same repository already there is reused, a different one
  moves to ``<owner>-<name>``;
- ``bind --relocate`` moves a clean bind-managed mirror into the workspace and refuses
  one that holds unpushed work, moving nothing;
- ``describe`` gives an agent the checkout and where to write code.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

_GIT_ID = ["-c", "user.email=t@e", "-c", "user.name=t"]


def _bare_with_commit(path: Path, branch: str = "main") -> str:
    src = path.parent / (path.name + "-src")
    subprocess.run(["git", "init", "-q", "-b", branch, str(src)], check=True)
    (src / "m.py").write_text("def f():\n    return 1\n")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(src), *_GIT_ID, "commit", "-q", "-m", "init"], check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(path)], check=True)
    return f"file://{path}"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from core.code_intel import invalidate_cache
    home = tmp_path / "home"
    ws = home / ".swarm-ai" / "SwarmWS"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    bindings = home / ".swarm-ai" / "bindings"
    bindings.mkdir(parents=True)
    repos = tmp_path / "Repos"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.repo_placement.repos_dir", lambda: repos)
    monkeypatch.setattr("core.code_intel.build.spawn_build", lambda project, **k: None)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    invalidate_cache()
    yield {"tmp": tmp_path, "ws": ws, "bindings": bindings, "repos": repos,
           "project": ws / "Projects" / "Demo"}
    invalidate_cache()


def _run_cli(argv):
    import sys
    import scripts.artifact_cli as cli
    old = sys.argv
    sys.argv = ["artifact_cli", "bind", "--project", "Demo", *argv]
    try:
        cli.main()
    finally:
        sys.argv = old


def _bind(argv, capsys):
    with pytest.raises(SystemExit) as e:
        _run_cli(argv)
    return e.value.code, capsys.readouterr()


def _bind_ok(argv, capsys):
    _run_cli(argv)
    return capsys.readouterr()


# ── URLs ─────────────────────────────────────────────────────────────────────

def test_a_url_without_a_scheme_becomes_https():
    from core.repo_placement import normalize_url
    assert normalize_url("github.com/acme/app") == "https://github.com/acme/app.git"
    assert normalize_url("https://github.com/acme/app.git") == "https://github.com/acme/app.git"
    assert normalize_url("git@github.com:acme/app.git") == "git@github.com:acme/app.git"


@pytest.mark.parametrize("url, why", [
    ("https://alice:tok@github.com/acme/app.git", "credentials"),
    ("https://tok@github.com/acme/app.git", "credentials"),
    ("git://github.com/acme/app.git", "not allowed"),
    ("ext::sh -c touch% /tmp/x", "not allowed"),
])
def test_a_url_git_should_not_clone_is_refused(url, why):
    from core.repo_placement import PlacementError, normalize_url
    with pytest.raises(PlacementError, match=why):
        normalize_url(url)


def test_a_refused_url_clones_and_writes_nothing(env, capsys, monkeypatch):
    called = []
    monkeypatch.setattr("core.git_sync_engine.GitSyncEngine.git_clone",
                        lambda self, *a: called.append(a) or True)
    code, out = _bind(["--url", "https://u:p@github.com/acme/app.git"], capsys)
    assert code == 1 and "credentials" in out.err
    assert called == [] and not (env["project"] / "bindings.yaml").exists()


# ── bind --url ───────────────────────────────────────────────────────────────

def test_bind_url_clones_into_the_workspace_and_owns_it_in_place(env, capsys):
    from core.project_agent import repo_roots
    from core.repo_placement import placement
    url = _bare_with_commit(env["tmp"] / "remotes" / "app.git", branch="trunk")
    out = _bind_ok(["--url", url], capsys)
    checkout = env["repos"] / "app"
    assert (checkout / "m.py").is_file()
    assert json.loads(out.out)["local_root"]["path"] == str(checkout.resolve())
    assert repo_roots(env["project"]) == {"app": checkout.resolve()}
    assert placement(env["project"])["app"] == {"remote": url, "branch": "trunk",
                                                 "checkout": "repos_dir:app"}
    assert not (env["bindings"] / "app").exists()          # no managed mirror


def test_the_same_repository_already_in_the_workspace_is_reused(env, capsys, monkeypatch):
    url = _bare_with_commit(env["tmp"] / "remotes" / "app.git")
    _bind_ok(["--url", url], capsys)
    monkeypatch.setattr("core.git_sync_engine.GitSyncEngine.git_clone",
                        lambda self, *a: pytest.fail("cloned again"))
    _bind_ok(["--url", url], capsys)
    import yaml
    doc = yaml.safe_load((env["project"] / "bindings.yaml").read_text())
    assert [b["repo"] for b in doc["bindings"]] == ["app"]


def test_another_repository_with_the_same_name_goes_to_owner_name(env, capsys):
    first = _bare_with_commit(env["tmp"] / "one" / "app.git")
    other = _bare_with_commit(env["tmp"] / "two" / "app.git")
    _bind_ok(["--url", first], capsys)
    from core.repo_placement import clone_into_workspace
    root, cloned = clone_into_workspace(other)
    assert cloned and root == env["repos"] / "two-app"


# ── bind --relocate ──────────────────────────────────────────────────────────

def _mirror_binding(env, url: str) -> Path:
    mirror = env["bindings"] / "app"
    subprocess.run(["git", "clone", "-q", url, str(mirror)], check=True)
    (env["project"] / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n"
        f"    clone: \"{url}\"\n    worktree: null\n    delivery_contract:\n"
        "      remote_kind: self-hosted-main\n      branch: main\n"
        "      review_path: s_expert-review\n      auto_send: manual-push\n")
    return mirror


def test_relocate_moves_a_clean_mirror_into_the_workspace(env, capsys):
    from core.project_agent import repo_roots
    from core.repo_placement import placement
    mirror = _mirror_binding(env, _bare_with_commit(env["tmp"] / "remotes" / "app.git"))
    assert placement(env["project"])["app"]["checkout"] == "mirror"
    _bind_ok(["--relocate", "app"], capsys)
    target = (env["repos"] / "app").resolve()
    assert not mirror.exists() and (target / "m.py").is_file()
    assert repo_roots(env["project"]) == {"app": target}
    assert placement(env["project"])["app"]["checkout"] == "repos_dir:app"
    import yaml
    b = yaml.safe_load((env["project"] / "bindings.yaml").read_text())["bindings"]
    assert len(b) == 1 and b[0]["delivery_contract"]["review_path"] == "s_expert-review"


def test_relocate_refuses_a_mirror_with_unpushed_work_and_moves_nothing(env, capsys):
    mirror = _mirror_binding(env, _bare_with_commit(env["tmp"] / "remotes" / "app.git"))
    (mirror / "wip.py").write_text("x = 1\n")
    code, out = _bind(["--relocate", "app"], capsys)
    assert code == 1 and "nothing was moved" in out.err
    assert (mirror / "wip.py").is_file() and not (env["repos"] / "app").exists()


# ── what an agent is told ────────────────────────────────────────────────────

def test_describe_names_the_checkout_and_where_to_write_code(env, capsys):
    from core.repo_placement import describe
    mirror = _mirror_binding(env, _bare_with_commit(env["tmp"] / "remotes" / "app.git"))
    assert "--relocate app" in describe(env["project"])[0]
    _bind_ok(["--relocate", "app"], capsys)
    line = describe(env["project"])[0]
    assert str((env["repos"] / "app").resolve()) in line and "session_worktree create" in line
    assert not mirror.exists()
