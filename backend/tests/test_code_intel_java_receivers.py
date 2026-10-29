"""Java receiver types in the code graph (core.code_intel.receivers).

A Java call on a value whose type the source states links to that class's method:
a typed parameter or local, ``var x = new Foo()``, a declared field (``this.x`` and
the bare field name), a static call through a class name, a same-file method's
return type, a cast, a for-each / instanceof / try-with-resources variable, a method
reference. A JDK or outside-library value (``String``, ``List``, an imported
``software.amazon…`` type) gets no edge at all. Real files, real parses, a real
graph, end to end through ``build.rebuild`` and the store's resolution.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.code_intel.graph_store import GraphStore
from core.code_intel.parser import TYPED_CALL_CONFIDENCE

_PKG = "src/main/java/com/acme/"

_STORE = '''package com.acme.store;

public class Store {
    public void save(String x) { flush(); }
    public void flush() {}
    public Row find(String k) { return new Row(); }
    public static Store open() { return new Store(); }
    public Row get() { return null; }
}
'''
_ROW = '''package com.acme.store;

public class Row {
    public void touch() {}
    public void save(String x) {}
    public String format(String f) { return f; }
    public boolean equals(Object o) { return false; }
}
'''
_CACHE = '''package com.acme.store;

public class Cache {
    public void save(String x) {}
    public void flush() {}
    public Row find(String k) { return null; }
}
'''
_HELPER = '''package com.acme.api;

public class Helper {
    public static void assist() {}
}
'''
_PAIR = '''package com.acme.store;

public record Pair(Store left) {
    public static Pair of() { return new Pair(new Store()); }
}
'''
_API = '''package com.acme.api;

import com.acme.store.Store;
import com.acme.store.Row;
import com.acme.store.Cache;
import java.util.List;
import java.util.Map;
import java.util.function.Supplier;
import java.util.Optional;
import java.util.UUID;
import com.acme.store.Pair;
import software.amazon.awssdk.services.s3.S3Client;

public class Api<T> {
    private final Store store;
    private S3Client s3;
    private List<String> names;

    public Api(Store store) { this.store = store; }

    public void byParam(Store s) { s.save("a"); }
    public void byVar() { var s = new Store(); s.flush(); }
    public void byLocal() { Store s = Store.open(); s.save("b"); }
    public void byThisField() { this.store.save("c"); }
    public void byBareField() { store.flush(); }
    public void byStatic() { Store.open(); }
    public void byReturn() { store.find("k").touch(); }
    public void byOwnReturn() { make().flush(); }
    public void byCast(Object o) { ((Store) o).save("d"); }
    public void byForEach(List<Store> all) { for (Store s : all) { s.flush(); } }
    public void byInstanceof(Object o) { if (o instanceof Store s) { s.save("e"); } }
    public void byResource() { try (Store s = Store.open()) { s.flush(); } }
    public void byReference(List<String> xs) { xs.forEach(store::save); xs.forEach(this::own); }
    public void own(String x) {}
    public void bySamePackage() { Helper.assist(); }
    public void byRecord() { Pair.of(); }
    public void byReassign(Supplier<Store> sup) { Store s = new Store(); s = sup.get(); s.save("h"); }
    public Store store() { return store; }
    private Optional<Store> opt;
    public void byOptional() { opt.get(); opt.map(x -> x).isPresent(); }
    public void byElement(List<Store> xs, Map<String, Store> m) { xs.get(0).save("i"); m.get("k").touch(); }
    public void byBlock(boolean b) { if (b) { Cache store = new Cache(); store.flush(); } store.save("j"); }
    public void byVarField() { var x = this.store; x.save("k"); var y = make(); y.flush(); }
    public void byQualified(com.acme.store.Store s, software.amazon.Lib l) { s.save("l"); l.touch(); }
    public void byUuid() { UUID.randomUUID().toString(); }
    public Store current() { return store; }
    public void external(String k, Map<String, Store> m) {
        k.trim().length();
        names.add(k);
        s3.close();
        System.out.println(k);
        String.valueOf(1).isEmpty();
        String.format("%s", k);
        k.trim().format("x");
        s3.touch();
        byte[] data = new byte[0];
        data.equals(k);
    }
    public void generic(T value) { value.save("f"); }
    public void lambda(List<Store> xs) { xs.forEach(x -> x.save("g")); }
    private Store make() { return new Store(); }
}
'''


@pytest.fixture
def g(tmp_path: Path):
    from core.code_intel.build import rebuild
    repo = tmp_path / "repo"
    files = {
        f"{_PKG}store/Store.java": _STORE, f"{_PKG}store/Row.java": _ROW,
        f"{_PKG}store/Cache.java": _CACHE, f"{_PKG}api/Api.java": _API,
        f"{_PKG}api/Helper.java": _HELPER, f"{_PKG}store/Pair.java": _PAIR,
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    store = GraphStore(tmp_path / "g.db")
    rebuild(store, repo, head=None)
    yield store
    store.close()


def _edges(g: GraphStore, method: str) -> dict[tuple[str, str], float]:
    src = f"{_PKG}api/Api.java::Api.{method}"
    return {(r[0], r[1]): round(r[2], 2) for r in g._conn.execute(
        "SELECT target_id, edge_type, confidence FROM code_edges WHERE source_id = ?", (src,))}


def _typed(g, method, kind="calls"):
    return {t.split("::")[-1] for (t, k), c in _edges(g, method).items()
            if k == kind and c == TYPED_CALL_CONFIDENCE}


@pytest.mark.parametrize("method, want", [
    ("byParam", {"Store.save"}),
    ("byVar", {"Store.flush"}),
    ("byLocal", {"Store.open", "Store.save"}),
    ("byThisField", {"Store.save"}),
    ("byBareField", {"Store.flush"}),
    ("byStatic", {"Store.open"}),
    ("byReturn", {"Store.find"}),
    ("byOwnReturn", {"Store.flush"}),
    ("byCast", {"Store.save"}),
    ("byForEach", {"Store.flush"}),
    ("byInstanceof", {"Store.save"}),
    ("byResource", {"Store.open", "Store.flush"}),
    ("bySamePackage", {"Helper.assist"}),       # no import needed, the repo has its file
    ("byRecord", {"Pair.of"}),                  # a record is a class
    ("byReassign", {"Store.save"}),             # an assignment keeps the declared type
    ("byVarField", {"Store.save", "Store.flush"}),  # var x = this.store / make()
    ("byQualified", {"Store.save"}),            # com.acme.store.Store, a repo file
])
def test_a_call_on_a_typed_java_value_links_to_that_class_only(g, method, want):
    e = _edges(g, method)
    assert _typed(g, method) >= want, e
    # never the same-named method of another class, never by name
    for t, kind in e:
        assert not t.endswith(("Cache.save", "Cache.flush", "Cache.find", "Row.save")), e
        if kind == "calls":
            assert e[(t, kind)] == TYPED_CALL_CONFIDENCE or "::" in t, e


def test_a_return_type_declared_in_another_file_is_not_known(g):
    """Return types are read from the calling file only (as in Python / TS): the
    result of another file's method is linked by name, never typed or dropped."""
    e = _edges(g, "byReturn")
    assert e[(f"{_PKG}store/Row.java::Row.touch", "calls")] < TYPED_CALL_CONFIDENCE


def test_a_method_reference_is_a_typed_reference(g):
    assert _typed(g, "byReference", "references") == {"Store.save", "Api.own"}


def test_jdk_and_outside_library_values_get_no_edge(g):
    """`String.format`, `k.trim().format()`, `s3.touch()`, `data.equals()`: a repo
    method of each name exists (Row.format, Row.touch, Row.equals), none is called."""
    assert {t for (t, k) in _edges(g, "external") if k == "calls"} == set()


def test_a_jdk_container_is_not_its_element(g):
    """`Optional<Store>` is java.util's: `opt.get()` is no call of a Store method. An
    element taken out of a JDK container (`xs.get(0)`, `m.get(k)`) may be the repo's,
    so a call on it keeps its link by name (it had one before Java was typed)."""
    assert _typed(g, "byOptional") == set()
    assert {t for (t, k) in _edges(g, "byOptional") if k == "calls"} == set()
    e = _edges(g, "byElement")
    assert e.get((f"{_PKG}store/Row.java::Row.touch", "calls")) == 0.4, e   # by name
    assert not _typed(g, "byElement"), e


def test_a_local_of_a_nested_block_does_not_type_the_field_of_its_name(g):
    """`if (b) { Cache store = …; }` then `store.save()`: the second is the field's
    Store — the scope does not see blocks, so the name is left untyped, never Cache."""
    e = _edges(g, "byBlock")
    assert not any(t.endswith("Cache.save") for t, _k in e), e
    assert all(c < TYPED_CALL_CONFIDENCE for (t, k), c in e.items() if t.endswith(".save")), e


def test_imported_jdk_and_library_types_are_outside(g):
    """`UUID.randomUUID()` (an imported ALL_CAPS class), a fully qualified library
    type (`software.amazon.Lib l; l.touch()`): no edge."""
    assert {t for (t, k) in _edges(g, "byUuid") if k == "calls"} == set()
    assert not any(t.endswith("Row.touch") for t, _k in _edges(g, "byQualified"))


def test_a_wildcard_import_does_not_make_another_packages_file_the_class(tmp_path):
    """`import com.lib.model.*;` and a repo file `com/acme/other/Request.java`: the
    `Request` here may be the library's; it is no typed link to the repo's. A class
    of the same package needs no import."""
    from core.code_intel.build import rebuild
    repo = tmp_path / "repo"
    files = {
        f"{_PKG}other/Request.java": ("package com.acme.other;\npublic class Request {\n"
                                      "  public String getId() { return null; }\n}\n"),
        f"{_PKG}api/Use.java": ("package com.acme.api;\nimport com.lib.model.*;\n"
                                "public class Use {\n  public void go(Request r) { r.getId(); }\n}\n"),
        f"{_PKG}same/Thing.java": "package com.acme.same;\npublic class Thing { public void act() {} }\n",
        f"{_PKG}same/User.java": ("package com.acme.same;\npublic class User {\n"
                                  "  public void go(Thing t) { t.act(); }\n}\n"),
        f"{_PKG}api/Wild.java": ("package com.acme.api;\nimport com.acme.other.*;\n"
                                 "public class Wild {\n  public void go(Request r) { r.getId(); }\n}\n"),
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    store = GraphStore(tmp_path / "g.db")
    try:
        rebuild(store, repo, head=None)
        rows = dict(store._conn.execute(
            "SELECT source_id, confidence FROM code_edges WHERE edge_type = 'calls'").fetchall())
        assert rows.get(f"{_PKG}api/Use.java::Use.go", 0) < TYPED_CALL_CONFIDENCE, rows
        assert rows[f"{_PKG}same/User.java::User.go"] == TYPED_CALL_CONFIDENCE, rows
        # a `.*` import of the repo's own package: its file is the class
        assert rows[f"{_PKG}api/Wild.java::Wild.go"] == TYPED_CALL_CONFIDENCE, rows
    finally:
        store.close()


def test_a_field_named_like_a_method_is_not_a_reference_to_it(g):
    assert {t for (t, k) in _edges(g, "current") if k == "references"} == set()


def test_a_type_variable_and_a_lambda_parameter_stay_untyped(tmp_path):
    """`T value` and `x -> x.save()` say nothing of the class: a call by name (which
    the store leaves unresolved here, three classes having a `save`)."""
    from core.code_intel.parser import MEMBER_CALL_CONFIDENCE, parse_file
    f = tmp_path / "Api.java"
    f.write_text(_API, encoding="utf-8")
    edges = parse_file(f, tmp_path).edges
    for method in ("generic", "lambda"):
        got = {(e.target_id, e.confidence) for e in edges
               if e.source_id == f"Api.java::Api.{method}" and e.edge_type == "calls"}
        assert got == {("save", MEMBER_CALL_CONFIDENCE)}, got


def test_a_field_declared_with_an_interface_type_keeps_it(tmp_path):
    from core.code_intel.receivers import FileTypes
    from core.code_intel.parser import _get_cached_parser
    src = (b"class Store {}\nclass Impl extends Store {}\n"
           b"class A { private Store store; A() { this.store = new Impl(); }\n"
           b"  void m() {} }\n")
    root = _get_cached_parser("java").parse(src).root_node
    ft = FileTypes(root, "java", {}, set(), None, None)
    assert ft.attrs["A"]["store"] == "Store"


def test_a_call_through_an_interface_is_a_caller_of_its_implementation(tmp_path):
    """`Repo repo; repo.save()` is typed by the interface; the change of
    `RepoImpl.save` still lists it (a call on the method it overrides), and Java's
    `implements` / `extends` are extends edges."""
    from core.code_intel.build import rebuild
    from core.code_intel.impact import _callers
    repo = tmp_path / "repo"
    files = {
        f"{_PKG}store/Repo.java": "package com.acme.store;\npublic interface Repo { void save(String x); }\n",
        f"{_PKG}store/RepoImpl.java": ("package com.acme.store;\npublic class RepoImpl implements Repo {\n"
                                       "  public void save(String x) {}\n  public void other() {}\n}\n"),
        f"{_PKG}api/Use.java": ("package com.acme.api;\nimport com.acme.store.Repo;\n"
                                "public class Use {\n  private final Repo repo;\n"
                                "  Use(Repo r) { this.repo = r; }\n  public void go() { repo.save(\"a\"); }\n}\n"),
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    g = GraphStore(tmp_path / "g.db")
    try:
        rebuild(g, repo, head=None)
        impl = f"{_PKG}store/RepoImpl.java::RepoImpl"
        assert g._conn.execute("SELECT target_id FROM code_edges WHERE source_id = ? AND "
                               "edge_type = 'extends'", (impl,)).fetchall() == \
            [(f"{_PKG}store/Repo.java::Repo",)]
        got = {(c.symbol, c.how, c.by_name) for c in _callers(g, [f"{impl}.save"], 1)}
        assert got == {("Use.go", "base", False)}, got
        assert _callers(g, [f"{impl}.other"], 1) == []
    finally:
        g.close()
