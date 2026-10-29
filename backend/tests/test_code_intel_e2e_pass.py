"""The end-to-end pass (design 2026-10-29-code-intel-e2e): what a change breaks, calls
on library values, a Hive's task clone, and the operations fixes.

Real files, real git repos, real parses and a real graph wherever the behaviour lives
there; the interface reader is also checked on its own, for each kind of change.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.code_intel.interface import breaks, call_sites, delta, interfaces, names_in_code


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=T",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


# ── A1. the interface delta, one kind at a time ───────────────────────────────

_OLD = '''
from dataclasses import dataclass


@dataclass
class Report:
    """What was picked."""
    candidates: list = None
    archived: int = 0


def reclaim(content, today, *, archive_name=None, dry_run=True):
    """Old words."""
    return 1


def spread(a, *rest, **opts):
    return a


class Store:
    def save(self, x, y=1):
        return x

    @staticmethod
    def make(a):
        return a
'''

_NEW = '''
from dataclasses import dataclass


@dataclass
class Report:
    """What was picked, in new words."""
    candidates: list = None


def reclaim(content, today, *, archive_name=None):
    """New words."""
    # and a comment
    return 1


def spread(a):
    return a


class Store:
    def save(self, x, y):
        return x

    @staticmethod
    def make(a):
        # a comment only
        return a
'''


def _d(key):
    return delta(interfaces(_OLD, "python").get(key), interfaces(_NEW, "python").get(key))


def test_each_kind_of_interface_change_is_named():
    assert _d("reclaim").removed_params == ["dry_run"]
    assert _d("Store.save").now_required == ["y"]
    s = _d("spread")
    assert s.dropped_varpos and s.dropped_varkw and s.fewer_positionals == 1
    assert _d("Report").removed_fields == ["archived"]
    assert _d("Store.make").comments_only                     # a comment, no code token
    assert delta(interfaces(_OLD, "python")["reclaim"], None).removed
    assert delta(None, interfaces(_NEW, "python")["reclaim"]).interface is False


def test_a_docstring_only_change_is_comments_only():
    old = 'def f(a):\n    """One."""\n    return a\n'
    new = 'def f(a):\n    """Two, longer."""\n    return a\n'
    assert delta(interfaces(old, "python")["f"], interfaces(new, "python")["f"]).comments_only
    code = 'def f(a):\n    """Two, longer."""\n    return a + 1\n'
    assert not delta(interfaces(old, "python")["f"], interfaces(code, "python")["f"]).comments_only


def test_typescript_interfaces():
    old = ("export class S { constructor(private a: A, b?: number) {}\n"
           "  save(x: number, y = 2) { return x } }\nexport function g(a: string, ...r: any[]) {}\n")
    new = ("export class S { constructor(private a: A, b: number) {}\n"
           "  save(x: number) { return x } }\nexport function g(a: string) {}\n")
    o, n = interfaces(old, "typescript"), interfaces(new, "typescript")
    assert delta(o["S"], n["S"]).now_required == ["b"]
    assert delta(o["S.save"], n["S.save"]).removed_params == ["y"]
    assert delta(o["g"], n["g"]).dropped_varpos


# ── A2. a call site against the delta ─────────────────────────────────────────

_CALLER = ("def f(s, kw):\n"
           "    reclaim(c, t, dry_run=True)\n"       # 2: a removed keyword
           "    Store().save(1)\n"                   # 3: a new required parameter not passed
           "    Store.save(s)\n"                     # 4: through the class: not judged
           "    Store().save(*kw)\n"                 # 5: a splat: not judged
           "    spread(1, 2, 3)\n"                   # 6: more positionals than it takes now
           "    reclaim(c, t)\n")                    # 7: fine


def _why(line, name, key, cls=None):
    o, n = interfaces(_OLD, "python"), interfaces(_NEW, "python")
    out = []
    for site in call_sites(_CALLER, "python", line, name, class_name=cls):
        out += breaks(delta(o[key], n[key]), n[key], site, is_method=bool(cls))
    return out


def test_a_call_site_breaks_only_for_what_the_change_removed_or_required():
    assert _why(2, "reclaim", "reclaim") == ["passes `dry_run=`, a parameter this change removes"]
    assert _why(3, "save", "Store.save", "Store") == [
        "does not pass `y`, which this change makes required"]
    assert _why(4, "save", "Store.save", "Store") == []
    assert _why(5, "save", "Store.save", "Store") == []
    assert _why(6, "spread", "spread") == [
        "passes 3 positional arguments; it now takes at most 1"]
    assert _why(7, "reclaim", "reclaim") == []


def test_code_only_names():
    assert names_in_code("# reclaim does it\nx = 'a.b'\n", "python", "reclaim", anchors=["mod"]) is False
    assert names_in_code('def f():\n    """reclaim is used here"""\n', "python", "reclaim",
                         anchors=["mod"]) is False
    assert names_in_code("patch('pkg.mod.reclaim')\n", "python", "reclaim", anchors=["mod"]) is True
    assert names_in_code("getattr(mod, 'reclaim')\n", "python", "reclaim", anchors=["mod"]) is True
    assert names_in_code("x = mod.reclaim\n", "python", "reclaim", anchors=["mod"]) is True
    assert names_in_code("anything", "go", "reclaim", anchors=["mod"]) is None


# ── A3. the report, end to end ────────────────────────────────────────────────

_LIB = ("def reclaim(content, *, dry_run=True):\n    \"\"\"Pick.\"\"\"\n    return content\n\n\n"
        "def helper(x):\n    \"\"\"Old words.\"\"\"\n    return x\n")
_USE = ("from lib import reclaim, helper\n\n\ndef run(c):\n    return reclaim(c, dry_run=False)\n\n\n"
        "def other(c):\n    return helper(c)\n")
_EDITED = ("from lib import reclaim\n\n\ndef tool(c):\n    # edited around the call, left as it was\n"
           "    return reclaim(c, dry_run=True)\n")
_FIXED = ("from lib import reclaim\n\n\ndef fixed(c):\n    return reclaim(c, dry_run=True)\n")


@pytest.fixture
def project(tmp_path, monkeypatch, own_local_root):
    from core.code_intel import invalidate_cache
    from core.code_intel.build import ensure_graph
    ws = tmp_path / "ws"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    for name, text in {"lib.py": _LIB, "use.py": _USE, "edited.py": _EDITED, "fixed.py": _FIXED,
                       "notes.py": "# reclaim and lib, only in a comment\nX = 1\n"}.items():
        (repo / name).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    own_local_root("Demo", repo)
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    invalidate_cache()
    assert [r["action"] for r in ensure_graph(ws / "Projects" / "Demo")] == ["built"]
    yield repo
    invalidate_cache()


def _change(repo):
    (repo / "lib.py").write_text(_LIB.replace(", *, dry_run=True", "")
                                 .replace("Old words.", "New words, only a docstring."))
    # two lines added above the call (the graph's line is now two off), call unchanged
    (repo / "edited.py").write_text(_EDITED.replace(
        "    # edited around", "    c = c or ''\n    c = c.strip()\n    # edited around"))
    # the author fixed this call: it must not be named
    (repo / "fixed.py").write_text(_FIXED.replace(", dry_run=True", ""))


def test_the_report_names_the_breaks_first_and_folds_a_docstring_change(project):
    from core.code_intel.changed import render, report
    _change(project)
    rep = report(project, refresh=False)
    by = {c.id: c for c in rep.symbols}
    c = by["lib.py::reclaim"]
    assert c.delta == "parameter `dry_run` removed" and c.breaking == 2
    out = {x["file"]: x["breaks"] for x in c.outside}
    assert out["use.py"] == ["L5: passes `dry_run=`, a parameter this change removes"]
    inside = {x["file"]: x["breaks"] for x in c.inside}
    assert inside["edited.py"] == ["L8: passes `dry_run=`, a parameter this change removes"]
    assert inside["fixed.py"] == []                       # fixed in this change: not a break
    assert rep.comments_only == ["lib.py::helper"]
    assert "lib.py::helper" not in by
    assert rep.breaks == 2
    text = render(rep)
    assert text.index("will break") < text.index("lib.py::helper")
    assert "edited.py: tool (a file this change edits)" in text
    assert "notes.py" not in text                         # only a comment names it


def test_a_break_is_looked_up_however_small_the_cap(project, monkeypatch):
    import core.code_intel.changed as changed
    _change(project)
    (project / "use.py").write_text(_USE + "\n\ndef more(c):\n    return c\n")
    monkeypatch.setattr(changed, "MAX_EXAMINED", 0)
    rep = changed.report(project, refresh=False)
    assert [c.id for c in rep.symbols] == ["lib.py::reclaim"] and rep.breaks == 2


def test_a_removed_field_is_read_by_name_outside(tmp_path, monkeypatch, own_local_root):
    from core.code_intel import invalidate_cache
    from core.code_intel.build import ensure_graph
    from core.code_intel.changed import report
    ws = tmp_path / "ws"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "rep.py").write_text("class Report:\n    def __init__(self):\n        self.archived = 0\n"
                                 "        self.kept = 1\n")
    (repo / "use.py").write_text("from rep import Report\n\n\ndef count(r: Report):\n"
                                 "    return r.archived + r.kept\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    own_local_root("Demo", repo)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    invalidate_cache()
    ensure_graph(ws / "Projects" / "Demo")
    (repo / "rep.py").write_text("class Report:\n    def __init__(self):\n        self.kept = 1\n")
    rep = report(repo, refresh=False)
    (c,) = [c for c in rep.symbols if c.id == "rep.py::Report"]
    assert c.field_reads == [{"file": "use.py", "line": 5, "field": "archived"}]
    invalidate_cache()


# ── B. calls on library values get no edge; stated types reach further ────────

def _graph(tmp_path, files):
    from core.code_intel.build import rebuild
    from core.code_intel.graph_store import GraphStore
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    g = GraphStore(tmp_path / "g.db")
    rebuild(g, repo, head=None)
    return g


def _calls(g, source):
    return {r[0]: round(r[1], 2) for r in g._conn.execute(
        "SELECT target_id, confidence FROM code_edges WHERE source_id = ? AND edge_type='calls'",
        (source,))}


def test_a_call_on_a_library_value_reaches_no_repo_method(tmp_path):
    g = _graph(tmp_path, {
        "mine.py": ("class Box:\n    def extend(self, x):\n        return x\n\n"
                    "    def execute(self, q):\n        return q\n\n"
                    "    def resolve(self):\n        return 1\n"),
        "use.py": ("import sqlite3\nfrom pathlib import Path\n\n\n"
                   "def run(p: Path, names: list[str], x):\n"
                   "    cmd = []\n    cmd.extend(names)\n"
                   "    conn = sqlite3.connect(':memory:')\n    conn.execute('select 1')\n"
                   "    p.resolve()\n    Path(x).resolve()\n    Path(x).expanduser().resolve()\n"
                   "    names.extend([])\n"),
        "ui.ts": ("import { Box } from './box';\n"
                  "export function f(xs: string[]) {\n  const m = new Map();\n  m.get('a');\n"
                  "  xs.push('b');\n  JSON.parse('{}');\n}\n"),
        "box.ts": "export class Box {\n  get(k: string) { return k; }\n  push(x: string) { return x; }\n}\n",
    })
    try:
        assert _calls(g, "use.py::run") == {}, _calls(g, "use.py::run")
        assert not any(t.startswith("box.ts::") for t in _calls(g, "ui.ts::f")), _calls(g, "ui.ts::f")
    finally:
        g.close()


def test_self_attributes_and_return_annotations_are_typed(tmp_path):
    from core.code_intel.parser import TYPED_CALL_CONFIDENCE
    g = _graph(tmp_path, {
        "store.py": ("class Store:\n    def save(self, x):\n        return x\n\n\n"
                     "class Cache:\n    def save(self, x):\n        return x\n"),
        "svc.py": ("from store import Store, Cache\n\n\n"
                   "class Service:\n"
                   "    def __init__(self, cache: Cache):\n"
                   "        self.store = Store()\n        self.cache = cache\n        self._last = {}\n\n"
                   "    def run(self, x):\n"
                   "        self.store.save(x)\n        self.cache.save(x)\n        self._last.clear()\n\n\n"
                   "def make() -> Store:\n    return Store()\n\n\n"
                   "def use(x):\n    return make().save(x)\n"),
    })
    try:
        run = _calls(g, "svc.py::Service.run")
        assert run.get("store.py::Store.save") == TYPED_CALL_CONFIDENCE
        assert run.get("store.py::Cache.save") == TYPED_CALL_CONFIDENCE
        assert not any("clear" in t for t in run)
        assert _calls(g, "svc.py::use").get("store.py::Store.save") == TYPED_CALL_CONFIDENCE
    finally:
        g.close()


def test_a_name_only_call_named_like_a_builtin_method_is_kept_but_never_a_lead(tmp_path):
    from core.code_intel.impact import _callers
    from core.code_intel.parser import BUILTIN_NAME_CONFIDENCE, NAME_ONLY_CONFIDENCE
    g = _graph(tmp_path, {
        "cfg.py": "class Config:\n    def get(self, k):\n        return k\n\n    def lookup(self, k):\n        return k\n",
        "use.py": "def read(c, k):\n    c.get(k)\n    return c.lookup(k)\n",
    })
    try:
        e = _calls(g, "use.py::read")
        assert e["cfg.py::Config.get"] == BUILTIN_NAME_CONFIDENCE
        assert e["cfg.py::Config.lookup"] == NAME_ONLY_CONFIDENCE
        (c,) = _callers(g, ["cfg.py::Config.get"], 1)
        assert c.by_name and c.builtin_name                  # reached, marked, not a lead
    finally:
        g.close()


# ── C. a Hive's task clone is the Brain's repo ────────────────────────────────

@pytest.fixture
def hive(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    proj = ws / "Projects" / "P"
    proj.mkdir(parents=True)
    (proj / "bindings.yaml").write_text(
        "bindings:\n  - repo: app\n    kind: external\n    clone: https://github.com/acme/app.git\n"
        "    delivery_contract:\n      remote_kind: self-hosted-main\n      build_system: local-script\n"
        "      branch: main\n      review_path: none\n      auto_send: manual-push\n")
    clone = ws / "Hive" / "repos" / "github.com" / "acme" / "app"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    _git(clone, "remote", "add", "origin", "https://token@github.com/acme/app.git")
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    return proj, clone


def test_a_hive_task_clone_with_the_bindings_origin_is_owned(hive):
    from core.code_intel import owned_graphs, owned_repo_roots
    proj, clone = hive
    refs = owned_graphs(proj)
    assert [(r.repo, Path(r.repo_root)) for r in refs][0] == ("app", clone.resolve())
    assert "/bindings/hive/" in str(refs[0].db_path)
    assert str(clone.resolve()) in owned_repo_roots(proj)


def test_a_clone_with_another_origin_or_off_a_hive_is_not(hive, monkeypatch):
    from core.code_intel.local_roots import hive_task_clones
    proj, clone = hive
    _git(clone, "remote", "set-url", "origin", "https://github.com/evil/app.git")
    assert hive_task_clones(proj) == []
    _git(clone, "remote", "set-url", "origin", "https://github.com/acme/app.git")
    assert hive_task_clones(proj) == [("app", clone.resolve())]
    monkeypatch.setenv("SWARMAI_MODE", "desktop")
    assert hive_task_clones(proj) == []


def test_a_clone_reached_through_a_link_out_of_hive_repos_is_not(hive, tmp_path):
    from core.code_intel.local_roots import hive_task_clones
    proj, clone = hive
    outside = tmp_path / "elsewhere"
    clone.rename(outside)
    clone.symlink_to(outside)
    assert hive_task_clones(proj) == []


def test_prepare_graph_asks_for_a_build_of_a_missing_or_behind_graph(tmp_path, monkeypatch,
                                                                     own_local_root):
    import core.code_intel.build as build
    from core.code_intel import invalidate_cache
    ws = tmp_path / "ws"
    (ws / "Projects" / "P").mkdir(parents=True)
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    own_local_root("P", repo)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    asked = []
    monkeypatch.setattr(build, "request_rebuild", lambda project, ref: asked.append(ref.repo) or True)
    invalidate_cache()
    assert build.prepare_graph("P", repo) == "requested" and asked == ["app"]   # missing
    build.ensure_graph(ws / "Projects" / "P")
    assert build.prepare_graph("P", repo) == "current" and asked == ["app"]
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-qam", "next")
    assert build.prepare_graph("P", repo) == "requested" and asked == ["app", "app"]
    assert build.prepare_graph("P", tmp_path) == "none"
    invalidate_cache()


# ── D. operations ─────────────────────────────────────────────────────────────

def test_an_empty_parse_by_this_parser_stops_asking_for_a_rebuild(tmp_path, monkeypatch):
    import core.code_intel.build as build
    from core.code_intel.graph_store import GraphStore
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("def f():\n    return 1\n")
    g = GraphStore(tmp_path / "g.db")
    build.rebuild(g, repo, head=None)
    g.set_meta("parser_version", "0")                        # written by an older parser
    assert build.parser_outdated(g) and build.db_parser_outdated(tmp_path / "g.db")
    (repo / "a.py").unlink()
    build.rebuild(g, repo, head=None)                         # this parser finds nothing
    assert not build.parser_outdated(g) and not build.db_parser_outdated(tmp_path / "g.db")
    g.close()


def test_the_summary_of_a_graph_behind_its_head_asks_for_an_update(tmp_path, monkeypatch,
                                                                   own_local_root):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import core.code_intel.build as build
    import routers.code_intel as mod
    from core.code_intel import invalidate_cache
    ws = tmp_path / "ws"
    (ws / "Projects" / "P").mkdir(parents=True)
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("def f():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    own_local_root("P", repo)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    asked = []
    monkeypatch.setattr(build, "request_rebuild", lambda project, ref: asked.append(ref.repo) or True)
    invalidate_cache()
    build.ensure_graph(ws / "Projects" / "P")
    app = FastAPI()
    app.include_router(mod.router, prefix="/api/code-intel")
    client = TestClient(app)
    assert client.get("/api/code-intel/P/summary").json()["freshness_status"] == "fresh"
    assert asked == []
    (repo / "a.py").write_text("def f():\n    return 2\n")
    _git(repo, "commit", "-qam", "next")
    assert client.get("/api/code-intel/P/summary").json()["freshness_status"] == "updating"
    assert asked == ["app"]
    invalidate_cache()


def test_a_survey_diff_runs_no_configured_diff_program(tmp_path):
    from core.brain_bootstrap.repo_read import git
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    marker = tmp_path / "ran"
    tool = tmp_path / "tool.sh"
    tool.write_text(f"#!/bin/sh\ntouch {marker}\n")
    tool.chmod(0o755)
    _git(repo, "config", "diff.external", str(tool))
    (repo / "a.txt").write_text("two\n")
    out = git(repo, "diff")
    assert out is not None and "-one" in out and "+two" in out     # a real patch
    assert not marker.exists()
