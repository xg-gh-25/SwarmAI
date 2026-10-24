"""code_change_feed reads every project's bound repos (P7, S4b-C2).

- _governed_repos lists (project, repo) from each Projects/<P>/bindings.yaml; a project
  with no declared repo contributes nothing, and no SwarmAI checkout is looked for.
- a bound repo's last commit is re-indexed into THAT project's graph, and the
  last-analysed commit is tracked per (project, repo).
"""
import subprocess
from unittest.mock import patch

from hooks.code_change_feed import CodeChangeFeed


def _repo(path):
    path.mkdir(parents=True)
    g = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)
    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (path / "pkg").mkdir()
    for n in ("a.py", "b.py", "c.py"):
        (path / "pkg" / n).write_text("def f(): pass\n")
    g("add", "-A")
    g("commit", "-qm", "add pkg")
    return path.resolve()


def test_each_project_feeds_its_own_repo(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".context").mkdir(parents=True)
    repo = _repo(tmp_path / "anyrepo")
    other = ws / "Projects" / "Other"
    other.mkdir(parents=True)
    (other / "bindings.yaml").write_text(
        f"governed_assets:\n  - kind: code-repo\n    name: anyrepo\n    source_workspace: {repo}\n")
    (ws / "Projects" / "SwarmAI").mkdir()          # no bindings: contributes nothing

    feed = CodeChangeFeed()
    assert feed._governed_repos(str(ws)) == [("Other", repo)]

    seen = []
    with patch("hooks.code_change_feed._reindex_changed_files",
               side_effect=lambda changes, root, project: seen.append((root, project)) or 0):
        feed._analyze_repos(str(ws))
    assert seen == [(str(repo), "Other")]
    state = ws / ".context" / ".code_change_feed" / "Other__anyrepo.txt"
    assert len(state.read_text().strip()) == 40

    seen.clear()
    with patch("hooks.code_change_feed._reindex_changed_files",
               side_effect=lambda changes, root, project: seen.append((root, project)) or 0):
        feed._analyze_repos(str(ws))
    assert seen == []                                  # same commit is not re-processed
