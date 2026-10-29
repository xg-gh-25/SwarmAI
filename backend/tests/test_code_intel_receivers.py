"""Receiver types and value references in the code graph (core.code_intel.receivers).

A call on a value whose class the source states links to that class's method (and up
its bases), not to every method of the same name; one whose type the source does not
state is still linked by name but marked so; a function handed on as a value or
patched by its dotted path has a `references` edge. Real files, real parses, a real
graph, end to end through `build.rebuild` and the store's resolution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore
from core.code_intel.parser import (NAME_ONLY_CONFIDENCE, PATH_REFERENCE_CONFIDENCE,
                                    TYPED_CALL_CONFIDENCE)

_STORE = '''
class Base:
    def flush(self):
        return 1


class Store(Base):
    def save(self, x):
        return self.flush()

    def load(self, k):
        return self.db.load(k)


class Cache:
    def save(self, x):
        return x


def save_all(rows):
    return [Store().save(r) for r in rows]
'''


def _graph(tmp_path: Path, files: dict[str, str]) -> GraphStore:
    from core.code_intel.build import rebuild
    repo = tmp_path / "repo"
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    g = GraphStore(tmp_path / "g.db")
    rebuild(g, repo, head=None)
    return g


def _edges(g: GraphStore, source: str) -> dict[tuple[str, str], float]:
    return {(r[0], r[1]): round(r[2], 2) for r in g._conn.execute(
        "SELECT target_id, edge_type, confidence FROM code_edges WHERE source_id = ?",
        (source,))}


@pytest.fixture
def py(tmp_path):
    g = _graph(tmp_path, {
        "store.py": _STORE,
        "api.py": (
            "import threading\nfrom store import Store, Cache, save_all\n\n\n"
            "def post(s: Store, c: 'Cache', rows):\n"
            "    s.save(1)\n    c.save(2)\n    x = Store()\n    x.load(3)\n"
            "    Store.load(None, 4)\n"
            "    threading.Thread(target=save_all).start()\n"
            "    handlers = {'save': save_all}\n"
            "    return [Store.load]\n\n\n"
            "def mixed(flag):\n"
            "    y = Store() if flag else None\n    y = Cache()\n    y = Store()\n    y.save(5)\n\n\n"
            "def untyped(conn):\n    return conn.save(6)\n\n\n"
            "def shadow(save_all):\n    return run(save_all)\n\n\n"
            "def run(f):\n    return f\n"),
        "pkg/__init__.py": "",
        "pkg/mod.py": "def tool():\n    return 1\n",
        "other.py": "def tool():\n    return 2\n\n\ndef getcwd():\n    return 0\n",
        "test_api.py": (
            "from unittest import mock\nfrom store import Store\n\n\n"
            "def test_x(monkeypatch):\n"
            "    with mock.patch('pkg.mod.tool'):\n        pass\n"
            "    mock.patch.object(Store, 'save')\n"
            "    mock.patch('store.Store.load')\n"
            "    monkeypatch.setattr('os.getcwd', lambda: 1)\n\n\n"
            "def test_y(monkeypatch):\n    import pkg.mod as m\n"
            "    monkeypatch.setattr(m, 'tool', lambda: 2)\n"
            "    monkeypatch.setattr(Store, 'load', None)\n"
            "    setattr(m, 'tool', None)\n"),
    })
    yield g
    g.close()


def test_a_call_on_a_typed_receiver_links_to_that_class(py):
    e = _edges(py, "api.py::post")
    assert e[("store.py::Store.save", "calls")] == TYPED_CALL_CONFIDENCE      # s: Store
    assert e[("store.py::Cache.save", "calls")] == TYPED_CALL_CONFIDENCE      # c: 'Cache'
    assert e[("store.py::Store.load", "calls")] == TYPED_CALL_CONFIDENCE      # x = Store()
    calls_to = {t for (t, k) in e if k == "calls"}
    assert "store.py::Cache.load" not in calls_to


def test_self_and_a_constructed_receiver_resolve_inside_the_class(py):
    assert _edges(py, "store.py::Store.save")[("store.py::Base.flush", "calls")] == \
        TYPED_CALL_CONFIDENCE                                              # up the bases
    assert _edges(py, "store.py::save_all")[("store.py::Store.save", "calls")] == \
        TYPED_CALL_CONFIDENCE                                              # Store().save()


def test_a_chain_on_self_is_not_a_call_on_self(py):
    """`self.db.load(k)` calls `load` on whatever `self.db` is, not Store.load."""
    e = _edges(py, "store.py::Store.load")
    assert ("store.py::Store.load", "calls") not in e


def test_an_untyped_receiver_is_linked_by_name_and_marked(py):
    e = _edges(py, "api.py::untyped")
    marked = {t: c for (t, k), c in e.items() if k == "calls"}
    # two methods named save: ambiguous by name, left unresolved, never a stated link
    assert all(c <= NAME_ONLY_CONFIDENCE + 0.06 for c in marked.values()), marked


def test_a_name_bound_to_two_classes_is_not_typed(py):
    e = _edges(py, "api.py::mixed")
    for cls in ("Store", "Cache"):
        assert e.get((f"store.py::{cls}.save", "calls")) != TYPED_CALL_CONFIDENCE, e


def test_functions_named_as_values_are_references(py):
    e = _edges(py, "api.py::post")
    assert e[("store.py::save_all", "references")] == PATH_REFERENCE_CONFIDENCE  # imported
    assert e[("store.py::Store.load", "references")] == TYPED_CALL_CONFIDENCE   # [Store.load]


def test_a_parameter_shadows_a_module_name(py):
    assert ("store.py::save_all", "references") not in _edges(py, "api.py::shadow")


def test_class_bases_are_extends_not_references(py):
    assert ("store.py::Base", "references") not in _edges(py, "store.py::<module>")
    assert _edges(py, "store.py::Store")[("store.py::Base", "extends")] == 1.0


def test_a_patch_names_its_target_by_path_only(py):
    e = _edges(py, "test_api.py::test_x")
    refs = {t for (t, k) in e if k == "references"}
    assert "pkg/mod.py::tool" in refs and "other.py::tool" not in refs
    assert "store.py::Store.save" in refs                    # patch.object(Store, "save")
    assert "store.py::Store.load" in refs                    # patch("store.Store.load")
    assert "other.py::getcwd" not in refs                    # os.getcwd is not ours


def test_a_setattr_on_a_module_or_class_names_its_member(py):
    refs = {t for (t, k) in _edges(py, "test_api.py::test_y") if k == "references"}
    assert {"pkg/mod.py::tool", "store.py::Store.load"} <= refs
    assert "other.py::tool" not in refs


def test_typescript_receivers_and_callbacks(tmp_path):
    g = _graph(tmp_path, {
        "store.ts": ("export class Store {\n  save(x: number) { return this.flush(); }\n"
                     "  flush() { return 1; }\n}\nexport class Cache {\n  save(x: number) { return x; }\n}\n"),
        "api.ts": ("import { Store } from './store';\n"
                   "function done() { return 1; }\n"
                   "export function post(s: Store) {\n  s.save(1);\n  const x = new Store();\n"
                   "  x.flush();\n  setTimeout(done, 1);\n  return { done };\n}\n"),
    })
    try:
        e = _edges(g, "api.ts::post")
        assert e[("store.ts::Store.save", "calls")] == TYPED_CALL_CONFIDENCE
        assert e[("store.ts::Store.flush", "calls")] == TYPED_CALL_CONFIDENCE
        assert ("store.ts::Cache.save", "calls") not in e
        assert e[("api.ts::done", "references")] == 1.0
        assert _edges(g, "store.ts::Store.save")[("store.ts::Store.flush", "calls")] == \
            TYPED_CALL_CONFIDENCE
    finally:
        g.close()


def test_impact_lists_name_only_callers_apart_and_does_not_follow_them(tmp_path):
    from core.code_intel.impact import _callers
    g = _graph(tmp_path, {
        "store.py": "class Store:\n    def save(self, x):\n        return x\n",
        "db.py": "def run(conn):\n    return conn.save(1)\n\n\ndef outer():\n    return run(None)\n",
        "api.py": ("from store import Store\n\n\ndef post(s: Store):\n    return s.save(1)\n\n\n"
                   "def top():\n    return post(None)\n"),
    })
    try:
        callers = {(c.file, c.symbol): c for c in _callers(g, ["store.py::Store.save"], 2)}
        assert not callers[("api.py", "post")].by_name and callers[("api.py", "post")].depth == 1
        assert callers[("api.py", "top")].depth == 2                    # followed: stated
        assert callers[("db.py", "run")].by_name                        # a guess …
        assert ("db.py", "outer") not in callers                        # … not followed
    finally:
        g.close()


def test_a_call_through_a_module_alias_matches_that_module_only(tmp_path):
    g = _graph(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/mod.py": "def tool():\n    return 1\n",
        "other.py": "def tool():\n    return 2\n\n\ndef getcwd():\n    return 0\n",
        "use.py": ("import os\nimport pkg.mod as m\nfrom pkg import mod as mm\n\n\n"
                   "def go():\n    os.getcwd()\n    m.tool()\n    return mm.tool()\n"),
    })
    try:
        targets = {t for (t, k) in _edges(g, "use.py::go") if k == "calls"}
        assert "pkg/mod.py::tool" in targets
        assert "other.py::tool" not in targets and "other.py::getcwd" not in targets
    finally:
        g.close()



# ── review round 1 (correctness) ─────────────────────────────────────────────

def _typed_targets(g, source):
    return {t for (t, k), c in _edges(g, source).items() if k == "calls" and c == TYPED_CALL_CONFIDENCE}


def test_a_rebinding_of_unknown_type_untypes_the_name(tmp_path):
    g = _graph(tmp_path, {
        "store.py": "class Store:\n    def save(self):\n        return 1\n",
        "use.py": (
            "from store import Store\n\n\n"
            "def rebound(make):\n    s = Store()\n    s = make()\n    return s.save()\n\n\n"
            "def caught():\n    s = Store()\n    try:\n        pass\n"
            "    except Exception as s:\n        return s.save()\n\n\n"
            "def opened():\n    s = Store()\n    with open('x') as s:\n        return s.save()\n\n\n"
            "def param(s: Store):\n    s = s.child()\n    return s.save()\n\n\n"
            "def comp(xs):\n    s = Store()\n    return [s.save() for s in xs]\n\n\n"
            "def outer(make):\n    s = Store()\n\n    def inner():\n        s = make()\n"
            "        return s.save()\n    return inner\n\n\n"
            "def still_typed():\n    s = Store()\n    return s.save()\n"),
    })
    try:
        for fn in ("rebound", "caught", "opened", "param", "comp", "outer.inner"):
            assert "store.py::Store.save" not in _typed_targets(g, f"use.py::{fn}"), fn
        assert "store.py::Store.save" in _typed_targets(g, "use.py::still_typed")
    finally:
        g.close()


def test_typescript_containers_function_this_and_destructuring(tmp_path):
    g = _graph(tmp_path, {
        "repo.ts": "export class Repo {\n  find(x: number) { return x; }\n}\n",
        "svc.ts": ("import { Repo } from './repo';\n"
                   "function save() { return 1; }\n"
                   "export class Svc {\n  save() { return 1; }\n"
                   "  run(repos: Repo[], deps: any) {\n    repos.find(1);\n"
                   "    [1].forEach(function () { this.save(); });\n"
                   "    const { save } = deps;\n    return go(save);\n  }\n}\n"
                   "function go(f: any) { return f; }\n"),
    })
    try:
        e = _edges(g, "svc.ts::Svc.run")
        assert ("repo.ts::Repo.find", "calls") not in e or e[("repo.ts::Repo.find", "calls")] != TYPED_CALL_CONFIDENCE
        assert e.get(("svc.ts::Svc.save", "calls")) != TYPED_CALL_CONFIDENCE
        assert ("svc.ts::save", "references") not in e
    finally:
        g.close()


def test_a_module_handed_on_as_a_value_reaches_no_same_named_function(tmp_path):
    g = _graph(tmp_path, {
        "core/__init__.py": "",
        "core/store.py": "def keep():\n    return 1\n",
        "core/other.py": "def store():\n    return 2\n",
        "use.py": ("import importlib\nfrom core import store\nimport core.store as cs\n\n\n"
                   "def again():\n    importlib.reload(store)\n    return run(cs)\n\n\n"
                   "def run(m):\n    return m\n"),
    })
    try:
        refs = {t for (t, k) in _edges(g, "use.py::again") if k == "references"}
        assert "core/other.py::store" not in refs
    finally:
        g.close()


def test_a_reference_to_a_member_its_class_lacks_is_dropped(tmp_path):
    g = _graph(tmp_path, {
        "store.py": "class Store:\n    def save(self):\n        return 1\n",
        "elsewhere.py": "class Cache:\n    def load(self):\n        return 2\n",
        "test_s.py": ("from unittest import mock\nfrom store import Store\n\n\n"
                      "def test_x():\n    mock.patch.object(Store, 'load')\n"),
    })
    try:
        assert ("elsewhere.py::Cache.load", "references") not in _edges(g, "test_s.py::test_x")
    finally:
        g.close()


def test_a_failed_resolution_keeps_the_unresolved_edges(tmp_path, monkeypatch):
    """Unresolved is not orphaned: when resolution raises, the cleanup is skipped,
    so the cross-file edges wait for the next run instead of being deleted."""
    import core.code_intel.parser as parser
    from core.code_intel.parser import parse_repo
    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "a.py").write_text("from b import f\n\n\ndef g():\n    return f()\n")
    (repo / "b.py").write_text("def f():\n    return 1\n")
    g = GraphStore(tmp_path / "g.db")
    try:
        for pr in parse_repo(repo):
            g.upsert_nodes(pr.nodes)
            g.upsert_edges(pr.edges)
        g._conn.commit()
        before = g._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
        monkeypatch.setattr(parser, "resolve_bare_targets",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        g.resolve_and_prune()
        assert g._conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0] == before
        assert not g._conn.in_transaction
    finally:
        g.close()


@pytest.mark.parametrize("line", [7, None])
def test_two_spellings_resolving_to_one_edge_are_merged(tmp_path, line):
    from core.code_intel.parser import MEMBER_CALL_CONFIDENCE, QUALIFIED_SEPARATOR
    g = GraphStore(tmp_path / "g.db")
    try:
        g.upsert_nodes([
            {"id": "store.py::Store", "file_path": "store.py", "node_type": "class",
             "name": "Store", "line_start": 1, "line_end": 3, "language": "python"},
            {"id": "store.py::Store.save", "file_path": "store.py", "node_type": "method",
             "name": "save", "line_start": 2, "line_end": 3, "language": "python"},
            {"id": "use.py::go", "file_path": "use.py", "node_type": "function",
             "name": "go", "line_start": 1, "line_end": 9, "language": "python"}])
        g.upsert_edges([
            {"source_id": "use.py::go", "target_id": "store::Store.save", "edge_type": "calls",
             "confidence": TYPED_CALL_CONFIDENCE, "line_number": line},
            {"source_id": "use.py::go", "target_id": "save", "edge_type": "calls",
             "confidence": MEMBER_CALL_CONFIDENCE, "line_number": line}])
        g._conn.commit()
        g.resolve_bare_targets(QUALIFIED_SEPARATOR)
        rows = g._conn.execute("SELECT target_id, confidence FROM code_edges").fetchall()
        assert rows == [("store.py::Store.save", TYPED_CALL_CONFIDENCE)]
    finally:
        g.close()


def test_an_aliased_import_is_recorded_under_its_alias():
    from core.code_intel.parser import scan_imports
    refs = scan_imports("import pkg.mod as m, json\nimport a.b\n", "python")
    assert [(r.name, r.module, r.whole_module) for r in refs] == [
        ("m", "pkg.mod", True), ("json", "json", True), ("b", "a.b", True)]


def test_a_relative_import_with_no_module_path_guesses_nothing(tmp_path):
    g = _graph(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from . import helper\n\n\ndef go():\n    return run(helper)\n\n\ndef run(f):\n    return f\n",
        "other.py": "def helper():\n    return 1\n",
    })
    try:
        assert ("other.py::helper", "references") not in _edges(g, "pkg/a.py::go")
    finally:
        g.close()


def _call_lines(g, source: str, target_suffix: str) -> set[int]:
    return {r[0] for r in g._conn.execute(
        "SELECT line_number FROM code_edges WHERE source_id = ? AND edge_type = 'calls' "
        "AND target_id LIKE ?", (source, "%" + target_suffix))}


def test_an_element_taken_out_of_a_container_keeps_its_link(tmp_path):
    """`d: dict[str, Store]` is a builtin container (EXTERNAL), but what `d.get(k)`,
    `xs.pop()`, `max(xs, …)` return is an element, which may be the repo's: a call on
    it is linked by name (as before receivers were typed), never dropped. A method
    whose result is the builtin's own (`k.strip()`, `p.resolve()`) still gets none."""
    g = _graph(tmp_path, {
        "store.py": _STORE,
        "api.py": (
            "from collections import deque\nfrom pathlib import Path\nfrom queue import Queue\n"
            "from store import Store\n\n\n"
            "def element(d: dict[str, Store], q: deque, xs: list[Store], jobs: Queue, k):\n"
            "    d.get(k).load(1)\n"                    # L8
            "    d.setdefault(k, Store()).load(2)\n"    # L9
            "    xs.pop().load(3)\n"                     # L10
            "    q.popleft().load(4)\n"                  # L11
            "    max(xs, key=id).load(5)\n"              # L12
            "    min(xs, key=id).load(6)\n"              # L13
            "    next(iter(xs)).load(7)\n"               # L14
            "    xs[0].load(8)\n"                        # L15
            "    jobs.get_nowait().load(9)\n\n\n"        # L16
            "def outside(k: str, p: Path):\n"
            "    k.strip().load(1)\n"
            "    p.resolve().load(2)\n"
            "    ', '.join([]).load(3)\n"),
    })
    try:
        assert _call_lines(g, "api.py::element", "Store.load") == set(range(8, 17))
        assert _call_lines(g, "api.py::outside", "Store.load") == set()
    finally:
        g.close()


def test_typescript_container_elements_keep_their_link(tmp_path):
    g = _graph(tmp_path, {
        "store.ts": ("export class Store {\n  save(x: number) { return this.flush(); }\n"
                     "  flush() { return 1; }\n}\n"),
        "api.ts": ("import { Store } from './store';\n"
                   "export function el(m: Map<string, Store>, xs: Store[]) {\n"
                   "  m.get('k')!.flush();\n  xs.find((s) => true)!.flush();\n"
                   "  xs.pop()!.flush();\n  xs.at(0)!.flush();\n}\n"
                   "export function out(s: string) {\n  s.trim().flush();\n}\n"),
    })
    try:
        assert _call_lines(g, "api.ts::el", "Store.flush") == {3, 4, 5, 6}
        assert _call_lines(g, "api.ts::out", "Store.flush") == set()
    finally:
        g.close()
