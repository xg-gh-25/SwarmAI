"""Code graph: call names per language, arrow functions, imports, index-time markers.

Design docs/design/2026-10-27-new-brain-initial-ddd.md §5.8 G3 (imports kept in their
own table, used to pick between same-named definitions) and G4 (four language leads,
each written as a failing test first).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore
from core.code_intel.parser import _build_file_scope_regex, parse_file, parse_repo


def _parse(tmp_path: Path, name: str, src: str):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")
    return parse_file(p, tmp_path)


def _call_targets(result) -> set[str]:
    return {e.target_id.rsplit("::", 1)[-1] for e in result.edges if e.edge_type == "calls"}


# ── G4: the method name of a call, per language ──────────────────────────────

@pytest.mark.parametrize("name, src", [
    ("a.go", "package main\nfunc helper() {}\nfunc run(s *S) {\n  s.Process()\n  helper()\n}\n"),
    ("A.java", "class A {\n  void helper() {}\n  void run(B b) {\n    b.process();\n    helper();\n  }\n}\n"),
    ("a.rs", "fn helper() {}\nfn run(s: S) {\n  s.process();\n  helper();\n}\n"),
    ("a.cpp", "void helper() {}\nvoid run(S s) {\n  s.process();\n  p->process();\n  helper();\n}\n"),
    ("a.kt", "fun helper() {}\nfun run(s: S) {\n  s.process()\n  helper()\n}\n"),
    ("a.rb", "def helper\nend\ndef run(s)\n  s.process(1)\n  helper()\nend\n"),
])
def test_method_calls_are_named_by_the_method(tmp_path, name, src) -> None:
    targets = _call_targets(_parse(tmp_path, name, src))
    assert {"process", "helper"} <= {t.lower() for t in targets}, targets
    assert "b" not in targets and "s" not in targets      # never the receiver


def test_scoped_calls_take_the_last_name(tmp_path) -> None:
    assert "f" in _call_targets(_parse(tmp_path, "a.rs", "fn r() { m::f(); }\n"))
    assert "f" in _call_targets(_parse(tmp_path, "b.cpp", "void r() { ns::f(); }\n"))


def test_python_and_ts_calls_are_unchanged(tmp_path) -> None:
    py = _parse(tmp_path, "a.py", "def helper():\n    pass\ndef run(o):\n    o.save()\n    self_obj = o\n    helper()\n")
    assert {"save", "helper"} <= _call_targets(py)
    ts = _parse(tmp_path, "a.ts", "function helper() {}\nfunction run(o) { o.save(); helper(); }\n")
    assert {"save", "helper"} <= _call_targets(ts)


# ── G4: an arrow function is named by what holds it, never by its parameter ──

def test_arrow_functions_take_the_variable_name(tmp_path) -> None:
    r = _parse(tmp_path, "a.ts",
               "export const handler = req => { helper(); };\n"
               "export const other = (a, b) => a + b;\n"
               "class K { onClick = () => { helper(); }; }\n"
               "function helper() {}\n"
               "[1].map(x => helper());\n")
    names = {n.name for n in r.nodes}
    assert {"handler", "other", "onClick", "helper"} <= names, names
    assert "req" not in names and "x" not in names and "a" not in names
    sources = {e.source_id.rsplit("::", 1)[-1] for e in r.edges
               if e.edge_type == "calls" and e.target_id.endswith("helper")}
    assert "handler" in sources and "K.onClick" in sources


# ── G4: a parenthesised Python import keeps every name ───────────────────────

def test_multiline_python_imports_keep_every_name() -> None:
    im, _ = _build_file_scope_regex(
        "from pkg.mod import (\n    alpha,\n    beta as b2,  # note\n)\nfrom x import y\n", "python")
    assert im.get("alpha") == "pkg/mod" and im.get("b2") == "pkg/mod" and im.get("y") == "x"


# ── G3: imports in their own table, used to pick between same names ──────────

def _repo(tmp_path: Path, files: dict[str, str]) -> GraphStore:
    for rel, src in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(src, encoding="utf-8")
    g = GraphStore(tmp_path / "g.db")
    g.bulk_insert(parse_repo(tmp_path), repo_root=tmp_path)
    return g


def test_an_ambiguous_call_resolves_through_the_import(tmp_path) -> None:
    g = _repo(tmp_path, {
        "pkg/a.py": "def helper():\n    pass\n",
        "pkg/b.py": "def helper():\n    pass\n",
        "app.py": "from pkg.a import helper\n\ndef run():\n    helper()\n",
    })
    callers = {c[0] for c in g.find_callers("pkg/a.py::helper", depth=1)}
    assert any(c.endswith("run") for c in callers), callers
    assert not g.find_callers("pkg/b.py::helper", depth=1)


# A file that defines nothing is not in the graph (parse_repo keeps files with symbols
# only): it has no call to disambiguate, and an import of it cannot pick between
# definitions. So both sides of each fixture define a function.
@pytest.mark.parametrize("files, src_file, dst", [
    ({"pkg/a.py": "def x():\n    pass\n", "app.py": "from pkg.a import x\n\ndef run():\n    x()\n"}, "app.py", "pkg/a.py"),
    ({"pkg/__init__.py": "", "pkg/a.py": "def x():\n    pass\n", "pkg/b.py": "from .a import x\n\ndef run():\n    x()\n"},
     "pkg/b.py", "pkg/a.py"),
    ({"src/util.ts": "export function x() {}\n", "src/app.ts": "import { x } from './util';\nexport function run() { x(); }\n"},
     "src/app.ts", "src/util.ts"),
    ({"pkg/util/u.go": "package util\nfunc Do() {}\n",
      "main.go": "package main\nimport (\n  \"fmt\"\n  \"github.com/o/r/pkg/util\"\n)\nfunc main() { util.Do() }\n"},
     "main.go", "pkg/util/"),
    ({"src/main/java/com/x/Y.java": "package com.x;\nclass Y { void m() {} }\n",
      "src/main/java/com/z/A.java": "package com.z;\nimport com.x.Y;\nclass A { void r() { new Y(); } }\n"},
     "src/main/java/com/z/A.java", "src/main/java/com/x/Y.java"),
])
def test_imports_resolve_to_repo_files(tmp_path, files, src_file, dst) -> None:
    g = _repo(tmp_path, files)
    assert dst in g.imported_files(src_file)


def test_external_imports_resolve_to_nothing(tmp_path) -> None:
    g = _repo(tmp_path, {"app.py": "import os\nfrom requests import get\n\ndef run():\n    get()\n"})
    assert g.imported_files("app.py") == set()


def test_imports_follow_incremental_updates_and_deletes(tmp_path) -> None:
    g = _repo(tmp_path, {"pkg/a.py": "def x():\n    pass\n", "pkg/c.py": "def y():\n    pass\n",
                         "app.py": "from pkg.a import x\n\ndef run():\n    x()\n"})
    assert g.imported_files("app.py") == {"pkg/a.py"}
    (tmp_path / "app.py").write_text("from pkg.c import y\n\ndef run():\n    y()\n", encoding="utf-8")
    g.incremental_update(tmp_path, ["app.py"])
    assert g.imported_files("app.py") == {"pkg/c.py"}
    g.remove_file("pkg/c.py")
    assert g.imported_files("app.py") == set()
    g.clear()
    assert g._conn.execute("SELECT COUNT(*) FROM code_imports").fetchone()[0] == 0


# ── G4: one format for when a graph was indexed ──────────────────────────────

def test_index_time_markers_have_one_format_and_every_reader_agrees(tmp_path) -> None:
    import time
    from core.code_intel.graph_store import parse_index_time
    from routers.code_intel import _compute_freshness
    g = GraphStore(tmp_path / "g.db")
    g.mark_indexed(full=False)
    stamp = g.get_meta("last_incremental_update")
    assert "T" in stamp and parse_index_time(stamp) is not None          # ISO, parseable
    assert _compute_freshness(g.get_codebase_summary()["last_indexed"]) == "fresh"
    # an older graph: an epoch float in one key, ISO in the other
    g.set_meta("last_incremental_update", str(time.time() - 60))
    g.set_meta("last_full_index", "2020-01-01T00:00:00+00:00")
    assert abs(g.indexed_at() - (time.time() - 60)) < 5               # the newer one
    assert _compute_freshness(g.get_codebase_summary()["last_indexed"]) == "fresh"
    assert parse_index_time("garbage") is None


def test_the_mtime_fallback_reads_an_iso_marker(tmp_path) -> None:
    from core.code_intel.freshness import check_freshness
    (tmp_path / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    g = GraphStore(tmp_path / "g.db")
    g.set_meta("repo_root", str(tmp_path))
    g.set_meta("last_full_index", "2099-01-01T00:00:00+00:00")   # ISO: used to raise
    assert check_freshness(g).stale is False


def test_a_bound_graph_records_when_and_at_which_commit(tmp_path, monkeypatch) -> None:
    import subprocess
    from core.ddd_bindings import _bind_locked
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "i"]):
        subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True)

    class E:
        def __init__(self, workspace_dir):
            pass

        def git_clone(self, url, dest):
            subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True, capture_output=True)
            return True

    monkeypatch.setattr("core.git_sync_engine.GitSyncEngine", E)

    class B:
        repo = "src"
        clone = str(src)

    wt, db = tmp_path / "wt" / "src", tmp_path / "wt" / "src.code_intel.db"
    wt.parent.mkdir()
    _bind_locked(B(), wt, db)
    g = GraphStore(db)
    head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True,
                          text=True).stdout.strip()
    assert g.get_meta("last_indexed_commit") == head and g.indexed_at() is not None


# ── G6: recall hits carry file:line, signature and repo; all graphs searched ──

def test_codeintel_hits_name_their_place_and_every_graph_is_searched(tmp_path, monkeypatch) -> None:
    from core import recall_multi as rm
    from core.code_intel import CodeIntelDb
    graphs = []
    for repo in ("alpha", "beta"):
        root = tmp_path / repo
        (root / "src").mkdir(parents=True)
        (root / "src" / "w.py").write_text(f"# {repo}\n\ndef widget_{repo}(x, y):\n    return x\n",
                                           encoding="utf-8")
        g = GraphStore(tmp_path / f"{repo}.db")
        g.bulk_insert(parse_repo(root), repo_root=root)
        graphs.append((CodeIntelDb(db_path=tmp_path / f"{repo}.db", repo=repo, repo_root=root), g))
    monkeypatch.setattr("core.code_intel.load_project_graphs", lambda p: graphs)
    hits = rm._project_codeintel_recall("widget", project="P")
    assert {h["repo"] for h in hits} == {"alpha", "beta"}
    a = next(h for h in hits if h["repo"] == "alpha")
    assert a["line"] == 3 and a["signature"] == "def widget_alpha(x, y):"
    out = rm._render_domain_hits("codeintel", hits)
    assert "`src/w.py:3` `def widget_alpha(x, y):` (alpha)" in out


def test_freshness_is_computed_once_per_head(tmp_path, monkeypatch) -> None:
    import subprocess
    from core.code_intel import freshness as fr
    root = tmp_path / "r"
    root.mkdir()
    (root / "a.py").write_text("def a():\n    pass\n", encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-c", "user.email=t@e", "-c", "user.name=t", "-C", str(root), *args],
                       check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "1")
    g = GraphStore(tmp_path / "g.db")
    g.bulk_insert(parse_repo(root), repo_root=root)
    first = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    g.mark_indexed(full=True, head=first)
    (root / "a.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    git("commit", "-q", "-am", "2")
    fr._DIFF_CACHE.clear()
    calls = []
    real = fr._git
    monkeypatch.setattr(fr, "_git", lambda cwd, args, timeout=5: calls.append(args[0]) or real(cwd, args, timeout))
    one = fr.check_freshness(g)
    n = len(calls)
    two = fr.check_freshness(g)
    assert one.stale and one.changed_files == ["a.py"] and two.changed_files == ["a.py"]
    assert calls[n:] == ["rev-parse"]                    # the second time: HEAD only
    two.changed_files.append("x")                        # a caller's edit stays its own
    assert fr.check_freshness(g).changed_files == ["a.py"]


def test_an_import_only_edit_refreshes_the_import_table(tmp_path, monkeypatch) -> None:
    """A changed import line with no symbol change grades COSMETIC (nodes are left
    as they are); its imports must still be refreshed and resolved."""
    import subprocess as sp
    import core.code_intel.tests.test_freshness as tf
    handler, proj, env = tf.TestGradedIncrementalE2E._init_repo(None, tmp_path, monkeypatch)
    monkeypatch.setattr("config.get_swarmws", lambda: tmp_path / ".swarm-ai" / "SwarmWS")
    (proj / "cosmetic.py").write_text("from structural import beta\n\ndef alpha():\n    return 1\n")
    sp.run(["git", "add", "."], cwd=proj, check=True, env=env)
    sp.run(["git", "commit", "-q", "-m", "import only"], cwd=proj, check=True, env=env)
    out = handler.reindex_projects(full=False)
    g = GraphStore(proj / "code_intel.db")
    rows = list(g._conn.execute("SELECT * FROM code_imports"))
    assert g.imported_files("cosmetic.py") == {"structural.py"}, (out, rows)



def test_a_go_call_resolves_through_the_imported_package(tmp_path) -> None:
    g = _repo(tmp_path, {
        "pkg/util/u.go": "package util\nfunc Do() {}\n",
        "pkg/other/o.go": "package other\nfunc Do() {}\n",
        "main.go": "package main\nimport \"github.com/o/r/pkg/util\"\nfunc main() { util.Do() }\n",
    })
    assert g.find_callers("pkg/util/u.go::Do", depth=1)
    assert not g.find_callers("pkg/other/o.go::Do", depth=1)


# ── review round 1 (correctness) ─────────────────────────────────────────────

def test_a_method_call_on_a_value_links_only_to_a_method(tmp_path) -> None:
    g = _repo(tmp_path, {
        "util.go": "package main\n\nfunc Close() {}\n\ntype F struct {\n\tx int\n}\n\nfunc (f F) Flush() {}\n",
        "main.go": "package main\n\nfunc run(f F) {\n\tf.Close()\n\tf.Flush()\n}\n",
        "lib.py": "def save():\n    pass\n",
        "repo.py": "class Repo:\n    def store(self):\n        pass\n",
        "app.py": "def run(o):\n    o.save()\n    o.store()\n",
    })
    assert not g.find_callers("util.go::Close", depth=1)        # a free function
    assert g.find_callers("util.go::F.Flush", depth=1)
    assert not g.find_callers("lib.py::save", depth=1)
    assert g.find_callers("repo.py::Repo.store", depth=1)


def test_a_call_through_a_module_still_links_to_the_function(tmp_path) -> None:
    g = _repo(tmp_path, {
        "pkg/utils.py": "def helper():\n    pass\n",
        "app.py": "from pkg import utils\n\ndef run():\n    utils.helper()\n",
    })
    assert g.find_callers("pkg/utils.py::helper", depth=1)


def test_a_value_call_never_links_to_the_same_files_function(tmp_path) -> None:
    r = _parse(tmp_path, "a.py", "def M():\n    pass\n\ndef run(x):\n    x.M()\n")
    e = next(e for e in r.edges if e.edge_type == "calls" and e.target_id.endswith("M"))
    assert e.target_id == "M" and e.confidence < 0.5


def test_incremental_import_resolution_is_scoped_and_a_full_pass_catches_up(tmp_path) -> None:
    g = _repo(tmp_path, {"app.py": "from pkg.c import y\n\ndef run():\n    y()\n",
                         "other.py": "def z():\n    pass\n"})
    assert g.imported_files("app.py") == set()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "c.py").write_text("def y():\n    pass\n", encoding="utf-8")
    g.incremental_update(tmp_path, ["pkg/c.py"])
    assert g.imported_files("app.py") == set()      # another file's row: not this batch
    g.resolve_imports()
    assert g.imported_files("app.py") == {"pkg/c.py"}


def test_full_import_resolution_scales(tmp_path) -> None:
    import time
    g = GraphStore(tmp_path / "g.db")
    files = [f"m{i}/sub/f{i}.py" for i in range(5000)]
    g._conn.executemany(
        "INSERT INTO code_nodes (id, file_path, node_type, name, line_start, line_end, language,"
        " is_export, is_entry_point, file_hash, indexed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(f"{f}::fn", f, "function", "fn", 1, 1, "python", 1, 0, "h", 0) for f in files])
    g._conn.executemany("INSERT INTO code_imports (src_file, name, module, line) VALUES (?,?,?,?)",
                        [(files[i % 5000], f"n{i}", f"ext{i}.lib", 1) for i in range(12000)])
    g._conn.commit()
    t = time.monotonic()
    g.resolve_imports()
    assert time.monotonic() - t < 3


def test_an_empty_bind_is_not_marked_indexed(tmp_path, monkeypatch) -> None:
    import subprocess
    from core.ddd_bindings import _bind_locked
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("# no code\n", encoding="utf-8")
    for args in (["init", "-q", "-b", "main"], ["add", "-A"],
                 ["-c", "user.email=t@e", "-c", "user.name=t", "commit", "-q", "-m", "i"]):
        subprocess.run(["git", "-C", str(src), *args], check=True, capture_output=True)

    class E:
        def __init__(self, workspace_dir):
            pass

        def git_clone(self, url, dest):
            subprocess.run(["git", "clone", "-q", str(src), str(dest)], check=True, capture_output=True)
            return True

    monkeypatch.setattr("core.git_sync_engine.GitSyncEngine", E)

    class B:
        repo = "src"
        clone = str(src)

    wt, db = tmp_path / "wt" / "src", tmp_path / "wt" / "src.code_intel.db"
    wt.parent.mkdir()
    _bind_locked(B(), wt, db)
    g = GraphStore(db)
    assert g.get_meta("last_indexed_commit") is None and g.indexed_at() is None


def test_mtime_freshness_keys_on_the_last_full_index(tmp_path) -> None:
    import os
    import time
    from core.code_intel.freshness import check_freshness
    f = tmp_path / "a.py"
    f.write_text("def a():\n    pass\n", encoding="utf-8")
    now = time.time()
    os.utime(f, (now - 100, now - 100))
    g = GraphStore(tmp_path / "g.db")
    g.set_meta("repo_root", str(tmp_path))
    g.set_meta("last_full_index", str(now - 1000))      # before the edit
    g.mark_indexed(full=False)                           # a later partial batch
    assert check_freshness(g).stale is True


def test_a_recreated_db_file_is_reopened(tmp_path, monkeypatch) -> None:
    import core.code_intel as ci
    from core.code_intel import CodeIntelDb
    db = tmp_path / "x.db"
    GraphStore(db).set_meta("repo_root", "/one")
    monkeypatch.setattr(ci, "code_intel_dbs", lambda d: [CodeIntelDb(db_path=db, repo="x")])
    ci._db_graph_cache.clear()
    first = ci.load_project_graphs("P")[0][1]
    assert first.get_meta("repo_root") == "/one"
    first.close()
    ci._db_graph_cache[str(db)] = (first, ci._db_graph_cache[str(db)][1])
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    GraphStore(db).set_meta("repo_root", "/two")
    assert ci.load_project_graphs("P")[0][1].get_meta("repo_root") == "/two"
    ci._db_graph_cache.clear()


def test_java_wildcard_and_static_imports(tmp_path) -> None:
    from core.code_intel.parser import scan_imports
    refs = scan_imports("import a.b.*;\nimport static a.b.C.m;\nimport a.b.D;\n", "java")
    assert [(r.name, r.module) for r in refs] == [("m", "a.b.C"), ("D", "a.b.D")]


def test_rust_generic_calls_are_named(tmp_path) -> None:
    targets = _call_targets(_parse(tmp_path, "g.rs", "fn r(v: V) { v.iter().collect::<Vec<u8>>(); }\n"))
    assert "collect" in targets and "iter" in targets


def test_a_backtick_in_a_signature_cannot_break_the_line(tmp_path) -> None:
    from core import recall_multi as rm
    (tmp_path / "a.js").write_text("function tpl(x = `y`) {}\n", encoding="utf-8")
    assert "`" not in rm._signature_line(str(tmp_path), "a.js", 1)


# ── G4: definitions named by the grammar's name field; methods typed as methods ──

def _nodes(tmp_path, name, src):
    return {n.id: n.node_type for n in _parse(tmp_path, name, src).nodes}


def test_go_types_and_methods_are_nodes(tmp_path) -> None:
    nodes = _nodes(tmp_path, "a.go", "package m\n\ntype F struct {\n\tx int\n}\n\n"
                                     "func (f *F) Flush() {}\n\nfunc helper() {}\n")
    assert nodes.get("a.go::F") == "class"
    assert nodes.get("a.go::F.Flush") == "method"
    assert nodes.get("a.go::helper") == "function"


def test_rust_impl_methods_belong_to_their_type(tmp_path) -> None:
    nodes = _nodes(tmp_path, "a.rs", "struct S;\nimpl S {\n    fn go(&self) {}\n}\n"
                                     "trait T {\n    fn t(&self);\n}\nfn free() {}\n")
    assert nodes.get("a.rs::S") == "class" and nodes.get("a.rs::S.go") == "method"
    assert nodes.get("a.rs::T") == "class" and nodes.get("a.rs::free") == "function"
    assert list(nodes).count("a.rs::S") == 1


def test_ruby_classes_hold_their_methods(tmp_path) -> None:
    nodes = _nodes(tmp_path, "a.rb", "class K\n  def m\n  end\nend\ndef free\nend\n")
    assert nodes.get("a.rb::K") == "class" and nodes.get("a.rb::K.m") == "method"
    assert nodes.get("a.rb::free") == "function"


def test_cpp_members_defined_outside_the_class_still_resolve(tmp_path) -> None:
    g = _repo(tmp_path, {
        "s.cpp": "struct S { void p(); };\nvoid S::p() {}\n",
        "m.cpp": "void run(S s) {\n  s.p();\n}\n",
    })
    assert g.find_callers("s.cpp::p", depth=1)


def test_an_unchanged_import_row_is_not_rewritten(tmp_path) -> None:
    g = _repo(tmp_path, {"pkg/a.py": "def x():\n    pass\n",
                         "app.py": "import os\nfrom pkg.a import x\n\ndef run():\n    x()\n"})
    before = g._conn.total_changes
    g.resolve_imports()
    assert g._conn.total_changes == before


def test_invalidating_a_project_closes_its_graphs(tmp_path, monkeypatch) -> None:
    import core.code_intel as ci
    from core.code_intel import CodeIntelDb
    db = tmp_path / "y.db"
    GraphStore(db).set_meta("repo_root", "/r")
    monkeypatch.setattr(ci, "code_intel_dbs", lambda d: [CodeIntelDb(db_path=db, repo="y")])
    ci._db_graph_cache.clear()
    ci.load_project_graphs("P")
    assert str(db) in ci._db_graph_cache
    ci.invalidate_cache("P")
    assert str(db) not in ci._db_graph_cache


def test_signatures_are_read_for_the_kept_hits_only(tmp_path, monkeypatch) -> None:
    from core import recall_multi as rm
    from core.code_intel import CodeIntelDb
    root = tmp_path / "r"
    root.mkdir()
    (root / "w.py").write_text("".join(f"def widget_{i}():\n    pass\n" for i in range(20)),
                               encoding="utf-8")
    g = GraphStore(tmp_path / "g.db")
    g.bulk_insert(parse_repo(root), repo_root=root)
    monkeypatch.setattr("core.code_intel.load_project_graphs",
                        lambda p: [(CodeIntelDb(db_path=tmp_path / "g.db", repo="r"), g)] * 2)
    reads = []
    real = rm._signature_line
    monkeypatch.setattr(rm, "_signature_line", lambda *a: reads.append(a) or real(*a))
    hits = rm._project_codeintel_recall("widget", project="P", limit=4)
    assert len(hits) == 4 and len(reads) == 4
