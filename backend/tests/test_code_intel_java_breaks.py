"""Java: what a change does to a method's or a constructor's versions, and which
calls it breaks (core.code_intel.interface + changed).

Java has overloads and no keywords or defaults: a method is the set of its versions,
each an arity. A call breaks when it passes a number of arguments some old version
took and no version takes now — a removed overload, a parameter added to the only
version, a new constructor that hides the implicit ``Foo()``. A parameter whose type
changed is named and its callers listed, never counted as a break.
"""
from __future__ import annotations

import subprocess

import pytest

from core.code_intel.interface import Site, breaks, call_sites, delta, interfaces

_STORE = '''package com.acme.store;

/** A store. */
public class Store {
    private int hidden;
    public String name;

    public void save(String x) { }
    public void save(String x, int n) { }
    public void load(String k) { }
    public void touch() { }
    private void own() { save("a", 1); }
}
'''


def _j(text):
    return interfaces(text, "java", ".java")


def test_overloads_constructors_and_fields_are_read():
    ifs = _j(_STORE + '''
record Pair(String a, int b) { Pair(String a) { this(a, 0); } }
interface Repo { void put(String k); }
enum Color { RED(1); Color(int v) {} void m() {} }
class Inner { Inner(int a) {} Inner(String... xs) {} }
''')
    assert sorted(ifs["Store.save"].arities) == [(1, False), (2, False)]
    assert ifs["Store.save"].sigs == ["(String)", "(String, int)"]
    assert ifs["Store"].arities == [(0, False)]             # the implicit Store()
    assert ifs["Store"].fields == {"name"}                   # a private field: no reader outside
    assert ifs["Store"].methods == {"save", "load", "touch", "own"}
    assert sorted(ifs["Pair"].arities) == [(1, False), (2, False)]   # canonical + declared
    assert ifs["Repo"].arities is None and "Repo.put" in ifs
    assert ifs["Color"].arities is None and "Color.m" in ifs
    assert sorted(ifs["Inner"].arities) == [(0, True), (1, False)]


def _delta(old_src, new_src, key):
    return delta(_j(old_src).get(key), _j(new_src).get(key))


@pytest.mark.parametrize("old, new, lost", [
    ("void save(String x, int n) {}", "void save(String x) {}", ["2"]),              # removed overload
    ("void save(String x) {}", "void save(String x, int n) {}", ["1"]),              # a parameter added
    ("void save(String... xs) {}", "void save(String x) {}", ["0", "2 or more"]),    # varargs dropped
    ("void save(String x) {}", "void save(String... xs) {}", []),                    # widened
])
def test_lost_arities(old, new, lost):
    keep = "void save(String x) {}" if "int n" in old and "int n" not in new else ""
    d = _delta(f"class S {{ {keep} {old} }}", f"class S {{ {new} }}", "S.save")
    assert d.lost_arities == lost
    assert d.interface == bool(lost)


def test_an_added_overload_or_a_type_change_breaks_no_call():
    d = _delta("class S { void save(String x) {} }",
               "class S { void save(String x) {} void save(String x, int n) {} }", "S.save")
    assert not d.interface and d.signature
    d = _delta("class S { void save(String x) {} }", "class S { void save(long x) {} }", "S.save")
    assert not d.interface and d.types_changed == "(String) → (long)"
    assert "parameter types changed: (String) → (long)" in d.describe()
    assert breaks(d, None, Site(1, 1, [], False, False), is_method=True, language="java") == []


def test_a_new_constructor_hides_the_implicit_one():
    d = _delta("class S { }", "class S { S(String a) {} }", "S")
    assert d.lost_arities == ["0"]
    assert breaks(d, None, Site(3, 0, [], False, False), is_method=False, language="java") == \
        ["passes 0 arguments; no version of it takes 0 now"]


def test_a_comment_or_a_parameter_name_is_no_interface_change():
    d = _delta("class S { /** a */ void save(String x) {} }",
               "class S { /** b, longer */ void save(String x) {} }", "S.save")
    assert d.comments_only
    d = _delta("class S { void save(String x) {} }", "class S { void save(String y) {} }", "S.save")
    assert not d.interface and not d.signature


def test_java_call_sites_count_arguments():
    text = ('class A { void m(Store s) {\n  s.save("a", 1); new Store();\n'
            '  new com.acme.Store<String>(x);\n} }\n')
    assert [(s.positional, s.through_class) for s in call_sites(text, "java", 2, "save",
                                                                suffix=".java")] == [(2, False)]
    assert [s.positional for s in call_sites(text, "java", 2, "Store", suffix=".java")] == [0]
    assert [s.positional for s in call_sites(text, "java", 3, "Store", suffix=".java")] == [1]


# ── the report, end to end on a real repo and graph ──────────────────────────

_PKG = "src/main/java/com/acme/"
_API = '''package com.acme.api;

import com.acme.store.Store;

public class Api {
    private final Store store = new Store();

    public void one() { store.save("a"); }
    public void two() { store.save("a", 2); }
    public void make() { Store s = new Store(); s.touch(); }
    public void typed(Store s) { s.load("k"); }
    public void gone(Store s) { s.touch(); }
    public String nm(Store s) { return s.name; }
}
'''


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b.c", "-c", "user.name=T",
                           *args], check=True, capture_output=True, text=True).stdout.strip()


def _bound_repo(tmp_path, monkeypatch, own_local_root, files):
    from core.code_intel import invalidate_cache
    from core.code_intel.build import ensure_graph
    ws = tmp_path / "ws"
    (ws / "Projects" / "Demo").mkdir(parents=True)
    root = tmp_path / "app"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    own_local_root("Demo", root)
    monkeypatch.setattr("config.get_swarmws", lambda: ws)
    monkeypatch.setattr("jobs.paths.PROJECTS_DIR", ws / "Projects")
    monkeypatch.setattr("core.code_intel.build._sync_docs", lambda *a: None)
    invalidate_cache()
    assert [r["action"] for r in ensure_graph(ws / "Projects" / "Demo")] == ["built"]
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch, own_local_root):
    from core.code_intel import invalidate_cache
    yield _bound_repo(tmp_path, monkeypatch, own_local_root,
                      {f"{_PKG}store/Store.java": _STORE, f"{_PKG}api/Api.java": _API})
    invalidate_cache()


def test_the_report_names_each_java_call_a_change_breaks(repo):
    from core.code_intel.changed import report
    store = repo / f"{_PKG}store/Store.java"
    store.write_text(_STORE
                     .replace("    public void save(String x, int n) { }\n", "")
                     .replace("public void load(String k)", "public void load(long k)")
                     .replace("    public void touch() { }\n", "")
                     .replace("    public String name;\n", "")
                     .replace('private void own() { save("a", 1); }',
                              'private void own() { save("a"); }\n    public Store(String n) { }'))
    # a call this change adds, in a changed file: no graph edge has it yet
    (repo / f"{_PKG}api/New.java").write_text(
        "package com.acme.api;\nimport com.acme.store.Store;\n"
        "public class New { void go() { new Store(); } }\n")
    # … and one in the class's own package, which needs no import
    (repo / f"{_PKG}store/Maker.java").write_text(
        "package com.acme.store;\npublic class Maker { Store go() { return new Store(); } }\n")
    _git(repo, "add", "-A")
    rep = report(repo, refresh=False)
    by = {c.id.split("::")[-1]: c for c in rep.symbols}
    api = f"{_PKG}api/Api.java"

    save = by["Store.save"]
    assert save.delta == "no version takes 2 arguments any more"
    assert {x["symbol"]: x["breaks"] for x in save.outside if x["file"] == api} == {
        "Api.one": [], "Api.two": ["L9: passes 2 arguments; no version of it takes 2 now"]}

    ctor = by["Store"]
    assert ctor.delta == "no version takes 0 arguments any more; field `name` removed"
    out = {x["symbol"]: x["breaks"] for x in ctor.outside}
    assert out == {"Api": ["L6: passes 0 arguments; no version of it takes 0 now"],   # a field
                   "Api.make": ["L10: passes 0 arguments; no version of it takes 0 now"]}

    touch = by["Store.touch"]
    assert touch.removed and {x["symbol"] for x in touch.outside if x["breaks"]} == \
        {"Api.make", "Api.gone"}

    load = by["Store.load"]                      # a type change: listed, never a break
    assert "parameter types changed: (String) → (long)" in load.delta
    assert all(not x["breaks"] for x in load.outside) and load.outside

    assert ctor.new_breaks == [
        {"file": f"{_PKG}api/New.java", "line": 3,
         "breaks": ["L3: passes 0 arguments; no version of it takes 0 now"]},
        {"file": f"{_PKG}store/Maker.java", "line": 2,
         "breaks": ["L2: passes 0 arguments; no version of it takes 0 now"]}]
    assert ctor.field_reads == [{"file": api, "line": 13, "field": "name"}]


def test_a_constructor_change_inside_the_class_body_judges_its_calls(tmp_path, monkeypatch,
                                                                     own_local_root):
    """Python too: `__init__` losing a parameter touches only the method, yet the
    calls of the class (`Store(1, b=2)`) are the ones that break."""
    from core.code_intel import invalidate_cache
    from core.code_intel.changed import report
    root = _bound_repo(tmp_path, monkeypatch, own_local_root, {
        "lib.py": "class Store:\n    kind = 1\n\n    def __init__(self, a, b=1):\n        self.a = a\n",
        "use.py": "from lib import Store\n\n\ndef make():\n    return Store(1, b=2)\n"})
    try:
        (root / "lib.py").write_text("class Store:\n    kind = 1\n\n    def __init__(self, a):\n"
                                     "        self.a = a\n")
        rep = report(root, refresh=False)
        (c,) = [c for c in rep.symbols if c.id == "lib.py::Store"]
        assert [x["breaks"] for x in c.outside] == [
            ["L5: passes `b=`, a parameter this change removes"]]
    finally:
        invalidate_cache()


# ── never a false break: what the source does not declare ────────────────────

@pytest.mark.parametrize("old, new, key", [
    ("class C { public String toString() { return \"\"; } }", "class C { }", "C.toString"),
    ("record P(int a) { public int a() { return a; } }", "record P(int a) { }", "P.a"),
    ("enum E { X; public String toString() { return \"\"; } }", "enum E { X; }", "E.toString"),
    ("@Data class D { public int getA() { return 1; } }", "@Data class D { }", "D.getA"),
])
def test_removing_a_member_java_or_a_generator_still_provides_is_no_removal(old, new, key):
    from core.code_intel.changed import _still_reachable
    assert delta(_j(old).get(key), _j(new).get(key)).removed
    assert _still_reachable(key, _j(new), set())


def test_a_removed_method_nothing_provides_is_a_removal():
    from core.code_intel.changed import _still_reachable
    assert not _still_reachable("C.go", _j("class C { }"), set())


def test_an_inherited_version_still_takes_the_call():
    base = "class Base { void m(int a) {} }\n"
    d = _delta(base + "class Sub extends Base { void m(int a) {} void m(int a, int b) {} }",
               base + "class Sub extends Base { void m(int a, int b) {} }", "Sub.m")
    assert not d.interface                       # Base.m(int) takes m(6)
    d = _delta("class Sub extends Lib { void m(int a) {} }",
               "class Sub extends Lib { void m(int a, int b) {} }", "Sub.m")
    assert not d.interface and "not judged" in d.describe()   # Lib may declare m(int)


def test_lombok_constructors_are_not_judged():
    d = _delta("@NoArgsConstructor class F { }", "@NoArgsConstructor class F { F(String a) {} }", "F")
    assert not d.interface


def test_a_python_enum_call_is_a_lookup_not_its_init():
    old = ("from enum import Enum\nclass Planet(Enum):\n    EARTH = (5.9, 6.3)\n"
           "    def __init__(self, mass, radius):\n        self.mass = mass\n")
    new = old.replace("def __init__(self, mass, radius)", "def __init__(self, mass, radius, g)")
    d = delta(interfaces(old, "python").get("Planet"), interfaces(new, "python").get("Planet"))
    assert breaks(d, interfaces(new, "python")["Planet"], Site(9, 1, [], False, False),
                  is_method=False) == []


def test_a_new_call_of_another_foo_in_the_same_package_is_not_judged(tmp_path, monkeypatch,
                                                                      own_local_root):
    """`new other.Foo()`, a file importing another `Store`, a file declaring its own:
    none is this package's Store, so none is judged against it."""
    from core.code_intel import invalidate_cache
    from core.code_intel.changed import report
    root = _bound_repo(tmp_path, monkeypatch, own_local_root,
                       {f"{_PKG}store/Store.java": _STORE, f"{_PKG}api/Api.java": _API})
    try:
        (root / f"{_PKG}store/Store.java").write_text(
            _STORE.replace('private void own() { save("a", 1); }',
                           'private void own() { }\n    public Store(String n) { }'))
        pkg = f"{_PKG}store/"
        (root / f"{pkg}Q.java").write_text(
            "package com.acme.store;\npublic class Q { Object go() { return new other.Store(); } }\n")
        (root / f"{pkg}I.java").write_text(
            "package com.acme.store;\nimport lib.Store;\npublic class I { Object go() { return new Store(); } }\n")
        (root / f"{pkg}N.java").write_text(
            "package com.acme.store;\npublic class N { static class Store {}\n"
            "  Object go() { return new Store(); } }\n")
        (root / f"{_PKG}other/U.java").parent.mkdir(parents=True)
        (root / f"{_PKG}other/U.java").write_text(   # another package's own Store
            "package com.acme.other;\npublic class U { Object go() { return new Store(); } }\n")
        # … and one that IS ours, so the check is seen to look at all of them
        (root / f"{pkg}Ok.java").write_text(
            "package com.acme.store;\npublic class Ok { Object go() { return new Store(); } }\n")
        _git(root, "add", "-A")
        rep = report(root, refresh=False)
        (ctor,) = [c for c in rep.symbols if c.id.endswith("store/Store.java::Store")]
        assert [b["file"] for b in ctor.new_breaks] == [f"{pkg}Ok.java"]
    finally:
        invalidate_cache()
