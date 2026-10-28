"""Who calls this (core.code_intel.impact): callers of a file or a symbol, across files
and across a project's graphs, with the graph's freshness and blind spots named.

Driven with real git repos indexed by the real parser into real GraphStores.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True,
                          capture_output=True, text=True).stdout


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD").strip()


_FILES = {
    "core/store.py": "def save(x):\n    return _write(x)\n\ndef _write(x):\n    return x\n",
    "api/routes.py": "from core.store import save\n\ndef post(x):\n    return save(x)\n",
    "api/app.py": "from api.routes import post\n\ndef handle(x):\n    return post(x)\n",
    "web/view.ts": "export function render(){ return 1 }\n",
    "web/page.ts": "import { render } from './view';\nexport class Page { show(){ return render() } }\n",
}

_BINDING = ("  - repo: {name}\n    kind: external\n    clone: \"https://example.com/{name}.git\"\n"
            "    delivery_contract:\n      remote_kind: github-pr\n      branch: main\n"
            "      review_path: github-pr\n      auto_send: manual-push\n")


@pytest.fixture
def project(tmp_path, monkeypatch):
    from core.code_intel import invalidate_cache
    from core.code_intel.build import rebuild
    bindings_root = tmp_path / "bindings"
    monkeypatch.setattr("core.ddd_bindings._default_bindings_root", lambda: bindings_root)
    projects = tmp_path / "Projects"
    (projects / "Demo").mkdir(parents=True)
    (projects / "Demo" / "bindings.yaml").write_text(
        "bindings:\n" + _BINDING.format(name="app") + _BINDING.format(name="other"),
        encoding="utf-8")
    repos = {}
    for name, files in (("app", _FILES), ("other", {"core/store.py": "def save(x):\n    return x\n",
                                                    "use.py": "from core.store import save\n\n"
                                                              "def go():\n    return save(1)\n"})):
        clone = bindings_root / name
        clone.mkdir(parents=True)
        _git(clone, "init", "-q", "-b", "main")
        _git(clone, "config", "user.email", "t@example.com")
        _git(clone, "config", "user.name", "t")
        head = _commit(clone, files, "init")
        g = GraphStore(bindings_root / f"{name}.code_intel.db")
        rebuild(g, clone, head=head)
        g.close()
        repos[name] = clone
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", projects)
    invalidate_cache()
    yield repos
    invalidate_cache()


def _by_repo(results):
    return {r.repo: r for r in results}


def test_a_symbol_has_its_direct_and_indirect_callers_across_files(project):
    from core.code_intel.impact import impact
    res = _by_repo(impact("Demo", "core/store.py::save", repo="app"))["app"]
    got = {(c.file, c.symbol, c.depth) for c in res.callers}
    assert ("api/routes.py", "post", 1) in got
    assert ("api/app.py", "handle", 2) in got
    assert not any(c.depth > 2 for c in res.callers)


def test_depth_bounds_the_walk(project):
    from core.code_intel.impact import impact
    res = _by_repo(impact("Demo", "core/store.py::save", repo="app", depth=1))["app"]
    assert {c.file for c in res.callers} == {"api/routes.py"}


def test_a_file_target_leaves_out_its_own_internal_calls(project):
    from core.code_intel.impact import impact
    res = _by_repo(impact("Demo", "core/store.py", repo="app"))["app"]
    assert res.target_kind == "file"
    files = {c.file for c in res.callers}
    assert "core/store.py" not in files          # save → _write is internal
    assert "api/routes.py" in files


def test_typescript_methods_are_callers_too(project):
    from core.code_intel.impact import impact
    res = _by_repo(impact("Demo", "render", repo="app"))["app"]
    assert ("web/page.ts", "Page.show", 1) in {(c.file, c.symbol, c.depth) for c in res.callers}


def test_every_graph_is_searched_and_each_answers_for_its_own_repo(project):
    from core.code_intel.impact import impact
    res = _by_repo(impact("Demo", "core/store.py::save"))
    assert set(res) == {"app", "other"}
    assert {c.file for c in res["other"].callers} == {"use.py"}
    assert "use.py" not in {c.file for c in res["app"].callers}


def test_a_short_name_resolves_to_its_symbol(project):
    from core.code_intel import impact as mod
    res = _by_repo(mod.impact("Demo", "save", repo="app"))["app"]
    assert res.seeds == ["core/store.py::save"]
    assert "Callers of symbol save" in mod.render("Demo", "save", [res])


def test_a_name_with_many_matches_lists_some_and_asks_to_qualify(project):
    from core.code_intel import impact as mod
    from core.code_intel.build import rebuild
    files = {f"m{i:02d}.py": "def run():\n    return 1\n" for i in range(12)}
    head = _commit(project["other"], files, "many")
    from core.code_intel import project_graph
    rebuild(project_graph("Demo", "other")[1], project["other"], head=head)
    res = _by_repo(mod.impact("Demo", "run", repo="other"))["other"]
    assert len(res.seeds) == mod._MAX_SYMBOL_MATCHES and res.more_matches == 2
    assert "(+2 more: give file::name)" in mod.render("Demo", "run", [res])


def test_a_stale_graph_says_so_and_names_changed_files_in_the_answer(project):
    from core.code_intel.impact import impact, render
    _commit(project["app"], {"api/routes.py": "from core.store import save\n\n"
                                              "def post(x):\n    return save(x) + 0\n"}, "edit")
    res = _by_repo(impact("Demo", "core/store.py::save", repo="app"))["app"]
    assert res.commits_behind == 1 and res.changed_since_index == ["api/routes.py"]
    text = render("Demo", "core/store.py::save", [res])
    assert "1 commits behind HEAD" in text and "in this answer: api/routes.py" in text


def test_no_graph_and_no_target_are_errors(project, capsys):
    from core.code_intel.impact import main
    assert main(["save", "--project", "Nope"]) == 1
    assert main(["no_such_symbol", "--project", "Demo"]) == 1
    assert main(["save", "--project", "Demo", "--repo", "nope"]) == 1


def test_the_cli_text_and_json(project, capsys):
    from core.code_intel.impact import main
    assert main(["core/store.py::save", "--project", "Demo", "--repo", "app"]) == 0
    out = capsys.readouterr().out
    assert "depth 1 (direct): 1 callers in 1 files" in out
    assert "api/routes.py: post (L3)" in out
    assert "Cross-check with grep" in out
    assert main(["core/store.py::save", "--project", "Demo", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {r["repo"] for r in data["results"]} == {"app", "other"}


def test_like_wildcards_in_a_name_are_literal(project):
    """``_`` and ``%`` are LIKE wildcards: a name must not match by pattern."""
    from core.code_intel.impact import impact
    assert impact("Demo", "s_ve", repo="app") == []
    assert impact("Demo", "%", repo="app") == []
