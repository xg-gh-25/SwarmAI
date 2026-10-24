"""`.swarmai/pipeline.toml` (core/repo_pipeline_config.py) and the landing settings it drives.

Design docs/design/2026-10-24-swarmai-usable-by-anyone.md §3.3.6, P7. What is tested:
- parsing: defaults, valid fields, any invalid field drops the whole file to defaults;
- load_at reads the committed file at a revision, not the working tree;
- landing_settings: author "repo" = the repo's own git user, "swarm" = Swarm; the trailer
  is the declared line (none by default); a repo with no git user and author "repo" is a
  LandingError naming the fix;
- commit_repos lands a repo with no declaration as the repo's own user with no trailer,
  and a repo declaring swarm + trailer as Swarm with that trailer.
- the SwarmAI repo's own file declares Swarm, its trailer and the node_modules link.
"""
import subprocess
from pathlib import Path

import pytest

from core import repo_pipeline_config as rpc
from core.run_protocol import landing


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _repo(tmp_path, toml=None, user=True):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    if user:
        _git(repo, "config", "user.name", "Dev One")
        _git(repo, "config", "user.email", "dev@example.com")
    (repo / "f.py").write_text("old\n")
    if toml is not None:
        (repo / ".swarmai").mkdir()
        (repo / ".swarmai" / "pipeline.toml").write_text(toml)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=x", "-c", "user.email=x@x", "commit", "-q", "-m", "init")
    return repo


def test_defaults_and_valid_fields():
    assert rpc.parse("") == rpc.DEFAULT
    cfg = rpc.parse('[landing]\nauthor = "swarm"\ntrailer = "Co-Authored-By: X <x@x>"\n'
                    '[worktree]\nlink = ["desktop/node_modules"]\n')
    assert cfg == rpc.RepoPipelineConfig("swarm", "Co-Authored-By: X <x@x>",
                                         ("desktop/node_modules",))


@pytest.mark.parametrize("text", [
    "not toml [", '[landing]\nauthor = "me"\n', '[landing]\ntrailer = 3\n',
    '[worktree]\nlink = ["../etc"]\n', '[worktree]\nlink = ["/abs"]\n',
    '[worktree]\nlink = [".git/hooks"]\n', 'landing = 1\n',
])
def test_any_invalid_field_means_defaults(text):
    assert rpc.parse(text) == rpc.DEFAULT


def test_load_at_reads_the_committed_file(tmp_path):
    repo = _repo(tmp_path, '[landing]\nauthor = "swarm"\n')
    (repo / ".swarmai" / "pipeline.toml").write_text('[landing]\nauthor = "repo"\n')
    assert rpc.load_at(str(repo), "HEAD").author == "swarm"      # working tree ignored
    assert rpc.load_at(str(tmp_path), "HEAD") == rpc.DEFAULT     # not a repo


def test_landing_settings(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = _repo(tmp_path / "a")
    assert landing.landing_settings(str(plain), "HEAD") == \
        {"author": "Dev One <dev@example.com>", "trailer": ""}
    declared = _repo(tmp_path / "b", '[landing]\nauthor = "swarm"\ntrailer = "T: 1"\n')
    assert landing.landing_settings(str(declared), "HEAD") == \
        {"author": "Swarm <swarm@swarmai.dev>", "trailer": "T: 1"}


def test_repo_author_without_a_git_user_names_the_fix(tmp_path, monkeypatch):
    repo = _repo(tmp_path, user=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for k in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
              "EMAIL"):
        monkeypatch.delenv(k, raising=False)
    try:
        ident = landing.repo_ident(str(repo))
    except landing.LandingError as exc:
        assert 'author = "swarm"' in str(exc)
    else:   # git may synthesize user@host on some machines; then it must still be an ident
        assert ident.endswith(">") and "<" in ident


def _land_one(tmp_path, repo):
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "f.py").write_text("new\n")
    _git(repo, "add", "f.py")
    tree = _git(repo, "write-tree")
    _git(repo, "reset", "--hard", "-q", base)
    commits = landing.commit_repos(run_id="run_cfg", integration_roots={"r": str(repo)},
                                   bases={"r": base}, bindings={"r": {"delivery_tree": tree}},
                                   message="feat: x", progress_path=tmp_path / "p.json")
    return _git(repo, "log", "-1", "--format=%an <%ae>|%cn <%ce>|%B", commits["r"])


def test_an_undeclared_repo_lands_as_its_own_user_without_a_trailer(tmp_path):
    out = _land_one(tmp_path, _repo(tmp_path))
    assert out.startswith("Dev One <dev@example.com>|Dev One <dev@example.com>|feat: x")
    assert "Co-Authored-By" not in out


def test_a_declared_repo_lands_as_swarm_with_its_trailer(tmp_path):
    repo = _repo(tmp_path, '[landing]\nauthor = "swarm"\n'
                           'trailer = "Co-Authored-By: Swarm <swarm@swarmai.dev>"\n')
    out = _land_one(tmp_path, repo)
    assert out.startswith("Swarm <swarm@swarmai.dev>|Swarm <swarm@swarmai.dev>|feat: x")
    assert out.rstrip().endswith("Co-Authored-By: Swarm <swarm@swarmai.dev>")


def test_the_swarmai_repo_declares_its_own_conventions():
    root = Path(__file__).resolve().parents[2]
    cfg = rpc.parse((root / ".swarmai" / "pipeline.toml").read_text())
    assert cfg.author == "swarm"
    assert cfg.trailer == "Co-Authored-By: Swarm <swarm@swarmai.dev>"
    assert cfg.links == ("desktop/node_modules",)
