"""governed_repo_roots: the code repos a project's bindings.yaml declares (P7, S4b-C2).

One reader for "which local git repos does project P govern", used by the DDD feeds
(engine_metrics, code_change_feed, ddd_refresh, ddd_orchestrator, ddd_self_audit) instead
of each one hunting for the SwarmAI source checkout. Both bindings.yaml shapes count:
``bindings[].worktree`` and ``governed_assets[kind=code-repo].source_workspace``. A path
that is not the toplevel of a git repo on disk is left out; nothing is guessed from the
project's name or from where the daemon itself runs.
"""
import subprocess
from pathlib import Path

from core.ddd_bindings import governed_repo_roots


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path.resolve()


def test_both_binding_shapes(tmp_path):
    a = _git_repo(tmp_path / "repo-a")
    b = _git_repo(tmp_path / "repo-b")
    proj = tmp_path / "Projects" / "P"
    proj.mkdir(parents=True)
    (proj / "bindings.yaml").write_text(f"""
governed_assets:
  - kind: code-repo
    name: a
    source_workspace: {a}
  - kind: skill-set
    name: s
bindings:
  - repo: b
    kind: external
    clone: https://example.invalid/b.git
    worktree: {b}
    delivery_contract: {{}}
""")
    assert governed_repo_roots(proj) == [a, b]


def test_nothing_is_guessed(tmp_path):
    proj = tmp_path / "Projects" / "SwarmAI"
    proj.mkdir(parents=True)
    # a self-hosted asset with no path, a path that is not a repo, a missing path
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    (proj / "bindings.yaml").write_text(f"""
governed_assets:
  - kind: code-repo
    name: swarmai
  - kind: code-repo
    name: plain
    source_workspace: {not_repo}
  - kind: code-repo
    name: gone
    source_workspace: {tmp_path / 'gone'}
""")
    assert governed_repo_roots(proj) == []


def test_no_or_bad_bindings(tmp_path):
    proj = tmp_path / "P"
    proj.mkdir()
    assert governed_repo_roots(proj) == []
    (proj / "bindings.yaml").write_text("{not: [yaml")
    assert governed_repo_roots(proj) == []
