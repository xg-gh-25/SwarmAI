"""core.code_intel.changed: the callers of the code a change touches, for a reviewer.

Real repos, a real graph and real diffs (uncommitted, committed, in a linked work tree).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_STORE = ('class Store:\n    def save(self, x):\n        return x\n\n'
          '    def load(self, k):\n        return k\n\n\n'
          'def save_all(rows):\n    return [Store().save(r) for r in rows]\n')
_API = 'from store import save_all\n\n\ndef post(rows):\n    return save_all(rows)\n'
_JOBS = ('import threading\nfrom store import save_all\n\n\n'
         'def start(rows):\n    threading.Thread(target=save_all, args=(rows,)).start()\n')
_TEST = ('from unittest import mock\n\n\ndef test_x():\n'
         '    with mock.patch("store.save_all"):\n        pass\n')


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@e", "-c", "user.name=t",
                           *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def project(tmp_path, monkeypatch, own_local_root):
    from core.code_intel import invalidate_cache
    from core.code_intel.build import ensure_graph
    ws = tmp_path / "ws"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    for name, text in {"store.py": _STORE, "api.py": _API, "jobs.py": _JOBS,
                       "test_store.py": _TEST}.items():
        (repo / name).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    own_local_root("Demo", repo)
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    invalidate_cache()
    assert [r["action"] for r in ensure_graph(ws / "Projects" / "Demo")] == ["built"]
    yield repo
    invalidate_cache()


def _by_id(rep):
    return {c.id: c for c in rep.symbols}


def _touched(rep):
    """Every touched symbol id: those shown in full and the quiet ones."""
    return sorted([c.id for c in rep.symbols] + rep.quiet)


def test_a_signature_change_lists_its_callers_and_the_files_that_name_it(project):
    from core.code_intel.changed import render, report
    (project / "store.py").write_text(_STORE.replace("def save_all(rows):", "def save_all(rows, *, strict):"))
    rep = report(project)
    c = _by_id(rep)["store.py::save_all"]
    assert c.signature
    assert [x["file"] for x in c.outside] == ["api.py"]
    # passed as a value (thread target) and mocked by its dotted path: no call the graph saw
    assert c.mentions == ["jobs.py", "test_store.py"]
    text = render(rep)
    assert "`store.py::save_all` (signature or first lines changed)" in text
    assert "api.py: post" in text and "jobs.py, test_store.py" in text


def test_a_method_body_change_names_the_method_not_its_class(project):
    from core.code_intel.changed import report
    (project / "store.py").write_text(_STORE.replace("        return k\n", "        return k * 2\n"))
    assert _touched(report(project)) == ["store.py::Store.load"]


def test_an_insertion_after_a_function_does_not_touch_it(project):
    from core.code_intel.changed import report
    (project / "api.py").write_text(_API + "\n\ndef extra():\n    return 1\n")
    assert _touched(report(project)) == []


def test_a_committed_change_is_found_with_a_base(project):
    from core.code_intel.changed import report
    (project / "store.py").write_text(_STORE.replace("return x\n", "return x or None\n"))
    _git(project, "commit", "-qam", "save")
    assert _touched(report(project)) == []                  # nothing uncommitted
    rep = report(project, base="HEAD~1")
    assert _touched(rep) == ["store.py::Store.save"]
    assert rep.quiet == ["store.py::Store.save"]            # called only from its own file


def test_a_linked_work_tree_uses_the_graph_of_its_checkout(project, tmp_path):
    from core.code_intel.changed import report
    wt = tmp_path / "wt"
    _git(project, "worktree", "add", "-q", "-b", "work", str(wt))
    (wt / "store.py").write_text(_STORE.replace("def save_all(rows):", "def save_all(items):"))
    rep = report(wt)
    assert rep.project == "Demo"
    assert [x["file"] for x in _by_id(rep)["store.py::save_all"].outside] == ["api.py"]


def test_a_checkout_without_a_graph_says_so(tmp_path, monkeypatch):
    from core.code_intel.changed import render, report
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", tmp_path / "none")
    repo = tmp_path / "bare"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("def a():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "i")
    (repo / "a.py").write_text("def a(x):\n    return x\n")
    text = render(report(repo))
    assert "no code graph" in text and "1 files changed" in text


def test_the_cli_prints_the_block(project, capsys):
    from core.code_intel.changed import main
    (project / "store.py").write_text(_STORE.replace("def save_all(rows):", "def save_all(rows, x):"))
    assert main(["--repo", str(project)]) == 0
    assert capsys.readouterr().out.startswith("## Callers of the code this change touches")


def test_a_caller_the_change_also_edits_is_listed_apart(project):
    """A caller in a file the change touches was (presumably) updated with it; the
    callers outside the change are the ones nobody looked at."""
    from core.code_intel.changed import report
    (project / "store.py").write_text(_STORE.replace("def save_all(rows):", "def save_all(rows, x):"))
    (project / "api.py").write_text(_API.replace("save_all(rows)\n", "save_all(rows, 1)\n"))
    c = _by_id(report(project))["store.py::save_all"]
    assert c.outside == [] and [x["file"] for x in c.inside] == ["api.py"]


def _noisy(project):
    """Another module with its own `save_all`, a doc naming ours, and a file named
    after ours without the module: none of them refers to store.save_all."""
    (project / "other.py").write_text("def save_all(x):\n    return x\n")
    (project / "notes.md").write_text("store.save_all is the bulk writer\n")
    (project / "cli.py").write_text("SAVE = 'save_all'\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "noise")


def test_a_name_counts_only_in_code_that_also_names_its_module(project):
    from core.code_intel.changed import report
    _noisy(project)
    (project / "store.py").write_text(_STORE.replace("def save_all(rows):", "def save_all(rows, x):"))
    c = _by_id(report(project))["store.py::save_all"]
    assert c.mentions == ["jobs.py", "test_store.py"]


def test_what_is_called_from_outside_comes_first_and_the_rest_is_one_line(project):
    from core.code_intel.changed import render, report
    (project / "store.py").write_text(
        _STORE.replace("        return k\n", "        return k * 2\n")
              .replace("def save_all(rows):", "def save_all(rows, x):"))
    rep = report(project)
    assert [c.id for c in rep.symbols] == ["store.py::save_all"]
    assert rep.quiet == ["store.py::Store.load"]
    assert "1 more changed symbols have no caller and no mention" in render(rep)


def test_symbols_of_a_new_file_are_counted_not_listed(project):
    from core.code_intel.changed import render, report
    (project / "fresh.py").write_text("def brand_new():\n    return 1\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "fresh")
    from core.code_intel import load_project_graphs
    from core.code_intel.build import ensure_fresh
    (ref, g), = load_project_graphs("Demo")
    ensure_fresh(project.parent / "ws" / "Projects" / "Demo", ref, g)
    rep = report(project, base="HEAD~1")
    assert rep.new == 1 and _touched(rep) == []
    assert "1 symbols are in files new in this change" in render(rep)


def test_a_method_caller_linked_by_name_alone_is_left_out(project):
    """`conn.save(...)` on some other object reaches `Store.save` by its name; a file
    that names neither `Store` nor the module is not counted as its caller."""
    from core.code_intel import load_project_graphs
    from core.code_intel.build import ensure_fresh
    from core.code_intel.changed import report
    (project / "db.py").write_text("def flush(conn):\n    return conn.save(1)\n")
    (project / "use.py").write_text("from store import Store\n\n\ndef go():\n    return Store().save(2)\n")
    _git(project, "add", "-A")
    _git(project, "commit", "-q", "-m", "callers")
    (ref, g), = load_project_graphs("Demo")
    ensure_fresh(project.parent / "ws" / "Projects" / "Demo", ref, g)
    (project / "store.py").write_text(_STORE.replace("    def save(self, x):", "    def save(self, x, y=0):"))
    rep = report(project)
    c = _by_id(rep)["store.py::Store.save"]
    files = [x["file"] for x in c.outside]
    assert "use.py" in files and "db.py" not in files
