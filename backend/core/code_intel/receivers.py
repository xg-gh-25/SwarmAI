"""Who a call or a reference points at, when the source says it: receiver types and
value references (Python, TypeScript, JavaScript, Java).

The call graph used to link ``x.save()`` to every method named ``save`` (it could not
see what ``x`` is), so ``conn.execute()`` counted as a caller of each ``execute`` in the
repo; and a function handed on as a value (``Thread(target=save_all)``, a registry, a
test's ``patch("pkg.mod.save_all")``) had no edge at all, so a contract change missed
those sites. This module reads what the source states, nothing more:

- **Receiver types**, per function scope (``scope_of``) and per file (``FileTypes``):
  ``self`` / ``this``; a parameter with an annotation (``s: Store``, ``s?: Store``); a
  local whose every binding gives one type (``x = Store()``, ``x: Store``); a class
  named directly (``Store.make()``); ``self.x`` / ``this.x`` by the class's attributes
  (``self.x = Store()``, ``self.x = db`` with ``db: Database``, a class-level or TS
  property annotation, a TS constructor parameter property); a call by its same-file
  return annotation (``make() -> Store``). A call on such a receiver targets
  ``Store.save`` (``class_target``). A name bound to two classes is dropped.
- **Java** reads its declarations instead: a parameter's, a local's (``var x = new
  Store()`` by its value), a field's declared type (``this.x`` and the bare ``x``; a
  constructor's assignment cannot change it), a for-each / ``instanceof`` / catch /
  try-with-resources variable, a cast, a static call through a class name (a class of
  the same package needs no import), a same-file method's return type. A type names
  a class only when it is imported from the repo or the repo has its file (so a type
  variable ``<T>`` is none). An import is outside the repo when no file of the repo
  holds its class (the JDK, a library, a model generated into another package);
  ``java.lang`` names (``String``, ``System``) are outside without one. A method reference (``this::save``,
  ``Store::save``, ``store::save``) is a reference.
- **Values outside the repo** (``EXTERNAL``): a literal, a comprehension, a builtin
  (``list()``, ``open()``), anything imported from a module that is not the repo's
  (the stdlib, a package), a builtin or outside annotation (``str``, ``list[X]``,
  ``Path``, ``string``, ``T[]``, ``Map<…>``), a TS global (``new Map()``,
  ``JSON.parse``) — and any member or call on one (``Path(x).resolve()``). A call on
  such a value gets no edge: no repo method can be its target.
- **Value references** (``value_reference``): a function or class name, or
  ``self.method`` / ``this.method``, in a value position — an argument, a keyword
  value, the right side of an assignment, a list/tuple/dict/array element, a return
  value, a decorator. A name bound locally (a parameter, an assignment) is not a
  reference to the module's definition of the same name.
- **Patch strings** (``patch_reference``, Python): ``patch("a.b.c")``,
  ``monkeypatch.setattr("a.b.c", …)`` and ``patch.object(Cls, "m")`` name a symbol
  by its dotted path.

What it does not do: infer a type the source does not state, a return type from
another file, or a container's element type. Such calls keep the name-only link,
marked by its confidence (``parser.NAME_ONLY_CONFIDENCE`` once resolved; a name that is
also a builtin type's method, ``data.get()``, gets ``BUILTIN_NAME_CONFIDENCE`` and is
kept for reachability only), so a reader can tell a link the source states from a
guess.
"""
from __future__ import annotations

import re

TYPED_LANGS = frozenset({"python", "typescript", "javascript", "java"})

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_FUNC_TYPES = frozenset({"method_declaration", "constructor_declaration", "lambda_expression",
                         "function_definition", "function_declaration", "method_definition",
                         "arrow_function", "function_expression", "lambda", "function"})
_CLASS_TYPES = frozenset({"class_definition", "class_declaration", "class",
                          "interface_declaration", "enum_declaration", "record_declaration"})
_SELF = frozenset({"self", "this"})


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _is(parent, field: str, node) -> bool:
    child = parent.child_by_field_name(field)
    return child is not None and child.start_byte == node.start_byte and child.end_byte == node.end_byte


def class_names(root) -> set[str]:
    """Every class this file defines (any depth)."""
    out: set[str] = set()
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type in _CLASS_TYPES:
            name = n.child_by_field_name("name")
            if name is not None and _IDENT.match(_text(name)):
                out.add(_text(name))
        stack.extend(n.children)
    return out


def known_classes(classes: set[str], import_map: dict) -> set[str]:
    """This file's classes plus the imported names written as classes (``Store``)."""
    return classes | {n for n in import_map if n[:1].isupper() and _IDENT.match(n)}


# ── what a value is: a repo class, EXTERNAL, or unknown (None) ────────────────
#
# EXTERNAL: a value no repo method can be called on — a literal, a builtin, an object
# of a module outside the repo. A member call on it gets no edge at all (before, every
# ``cmd.extend()`` / ``conn.execute()`` / ``p.resolve()`` was linked by name to the
# repo's own ``extend`` / ``execute`` / ``resolve``; most name-only edges were these).
EXTERNAL = "<external>"
_NOT_JAVA = object()          # FileTypes._java_value: "not a Java-only shape"

_PY_BUILTIN_TYPES = frozenset({
    "str", "int", "float", "bool", "bytes", "bytearray", "list", "dict", "set",
    "frozenset", "tuple", "object", "complex", "memoryview", "range", "type",
    "List", "Dict", "Set", "FrozenSet", "Tuple", "Sequence", "MutableSequence",
    "Mapping", "MutableMapping", "Iterable", "Iterator", "Generator", "Callable",
    "Literal", "Deque", "DefaultDict", "OrderedDict", "Counter", "AbstractSet",
    "Awaitable", "Coroutine", "AsyncIterator", "AsyncIterable", "Pattern", "Match",
})
_PY_BUILTIN_CALLS = frozenset({
    "str", "int", "float", "bool", "bytes", "bytearray", "list", "dict", "set",
    "frozenset", "tuple", "open", "sorted", "reversed", "enumerate", "zip", "map",
    "filter", "range", "iter", "repr", "format", "len", "sum", "min", "max", "abs",
    "round", "vars", "dir", "chr", "ord", "hex", "bin", "oct", "hash", "id",
})
_TS_UNKNOWN_TYPES = frozenset({"any", "unknown"})
_TS_GLOBALS = frozenset({
    "Array", "Map", "Set", "WeakMap", "WeakSet", "WeakRef", "Promise", "Date", "RegExp",
    "Error", "TypeError", "RangeError", "URL", "URLSearchParams", "Record", "Partial",
    "Readonly", "ReadonlyArray", "ReadonlyMap", "ReadonlySet", "Uint8Array", "Int32Array",
    "Float32Array", "ArrayBuffer", "DataView", "Blob", "File", "FileReader", "FormData",
    "Headers", "Response", "Request", "AbortController", "AbortSignal", "HTMLElement",
    "HTMLInputElement", "HTMLDivElement", "Element", "Event", "KeyboardEvent",
    "MouseEvent", "CustomEvent", "EventTarget", "Document", "Window", "Node", "NodeList",
    "JSON", "Math", "Object", "String", "Number", "Boolean", "Symbol", "BigInt", "Intl",
    "Reflect", "Proxy", "console", "window", "document", "navigator", "localStorage",
    "sessionStorage", "location", "history", "performance", "crypto", "process",
    "Buffer", "EventSource", "WebSocket", "TextEncoder", "TextDecoder", "Worker",
    "ResizeObserver", "MutationObserver", "IntersectionObserver", "Iterable",
    "Iterator", "Generator", "AsyncIterable", "Function", "Awaited", "ReturnType",
    "Parameters", "Pick", "Omit", "Exclude", "Extract", "Required", "NonNullable",
})
_LITERALS = frozenset({
    # python
    "string", "concatenated_string", "integer", "float", "list", "dictionary", "set",
    "tuple", "list_comprehension", "dictionary_comprehension", "set_comprehension",
    "generator_expression", "true", "false",
    # ts / js
    "template_string", "number", "array", "object", "regex", "arrow_function",
})
_NULLS = frozenset({"none", "null", "undefined"})
# Java: java.lang needs no import; the primitives; what a value of them is.
_JAVA_GLOBALS = frozenset({
    "String", "Object", "Integer", "Long", "Double", "Float", "Short", "Byte", "Boolean",
    "Character", "Number", "Math", "StrictMath", "System", "StringBuilder", "StringBuffer",
    "Thread", "Runnable", "Exception", "RuntimeException", "Error", "Throwable",
    "IllegalArgumentException", "IllegalStateException", "NullPointerException",
    "UnsupportedOperationException", "Iterable", "Class", "Enum", "Record", "Void",
    "CharSequence", "Comparable", "AutoCloseable", "Process", "ProcessBuilder", "Runtime",
})
_JAVA_LITERALS = frozenset({
    "string_literal", "text_block", "decimal_integer_literal", "hex_integer_literal",
    "octal_integer_literal", "binary_integer_literal", "decimal_floating_point_literal",
    "hex_floating_point_literal", "character_literal", "class_literal",
    "array_creation_expression", "array_initializer",
})
# Outside functions whose result has their argument's type (not EXTERNAL).
_PASSTHROUGH = frozenset({"cast", "copy", "deepcopy", "replace"})


# Methods of a JDK value whose result is a JDK value whatever the element type (a
# string, a number, a boolean, a stream, a container, an Optional); a method missing
# here (get, orElse, next, getValue, reduce …) may return a repo object.
_JAVA_JDK_RESULTS = frozenset({
    "toString", "trim", "strip", "substring", "toLowerCase", "toUpperCase", "length",
    "size", "isEmpty", "isBlank", "equals", "hashCode", "contains", "containsKey",
    "containsValue", "startsWith", "endsWith", "matches", "split", "format", "formatted",
    "replace", "replaceAll", "concat", "repeat", "chars", "getBytes", "charAt",
    "indexOf", "lastIndexOf", "compareTo", "stream", "parallelStream", "keySet",
    "values", "entrySet", "iterator", "toList", "collect", "count", "sum", "anyMatch",
    "allMatch", "noneMatch", "isPresent", "getClass", "name", "ordinal", "append",
    "toArray", "subList", "filter", "map", "flatMap", "sorted", "distinct", "limit",
    "skip", "boxed", "mapToObj", "mapToInt", "mapToLong", "findFirst", "findAny",
    "getMessage", "valueOf", "intValue", "longValue", "doubleValue", "booleanValue",
})
_JAVA_BUILTIN_METHODS = frozenset({
    "get", "put", "add", "remove", "size", "isEmpty", "contains", "containsKey", "equals",
    "hashCode", "toString", "stream", "map", "filter", "collect", "forEach", "append",
    "length", "substring", "trim", "strip", "split", "format", "valueOf", "orElse",
    "orElseGet", "orElseThrow", "isPresent", "ifPresent", "of", "ofNullable", "getClass",
    "close", "iterator", "next", "hasNext", "keySet", "values", "entrySet", "getKey",
    "getValue", "set", "clear", "addAll", "startsWith", "endsWith", "toLowerCase",
    "toUpperCase", "replace", "matches", "charAt", "indexOf", "compareTo", "build",
    "builder", "flatMap", "reduce", "sorted", "distinct", "limit", "toList", "anyMatch",
    "allMatch", "noneMatch", "findFirst", "findAny", "count", "putIfAbsent",
    "computeIfAbsent", "getOrDefault", "info", "debug", "warn", "error", "trace",
})
_TS_BUILTIN_METHODS = frozenset({
    "get", "set", "has", "delete", "clear", "add", "push", "pop", "shift", "unshift",
    "slice", "splice", "map", "filter", "reduce", "reduceRight", "forEach", "find",
    "findIndex", "findLast", "findLastIndex", "includes", "indexOf", "lastIndexOf", "join",
    "concat", "sort", "reverse", "some", "every", "keys", "values", "entries", "flat",
    "flatMap", "fill", "at", "trim", "trimStart", "trimEnd", "split", "replace",
    "replaceAll", "match", "matchAll", "startsWith", "endsWith", "toLowerCase",
    "toUpperCase", "padStart", "padEnd", "charAt", "charCodeAt", "substring", "substr",
    "repeat", "localeCompare", "normalize", "search", "then", "catch", "finally",
    "toString", "toFixed", "valueOf", "toISOString", "toJSON", "getTime", "test", "exec",
    "assign", "freeze", "apply", "call", "bind", "addEventListener",
    "removeEventListener", "dispatchEvent", "querySelector", "querySelectorAll",
    "getAttribute", "setAttribute", "removeAttribute", "appendChild", "removeChild",
    "contains", "focus", "blur", "click", "scrollIntoView", "json", "text", "abort",
    "toLocaleString", "toLocaleDateString", "toLocaleTimeString", "getItem", "setItem",
    "removeItem", "write", "close", "send", "next", "return", "throw", "log", "warn",
    "error", "info", "debug",
})


def builtin_method_names(language: str) -> frozenset[str]:
    """The public method names of the language's builtin value types: Python's read from
    the types themselves (str, bytes, list, dict, set, tuple, numbers, files, paths,
    sqlite connections and cursors), TS/JS's from the standard prototypes."""
    global _PY_BUILTIN_METHODS
    if language in ("typescript", "javascript"):
        return _TS_BUILTIN_METHODS
    if language == "java":
        return _JAVA_BUILTIN_METHODS
    if language != "python":
        return frozenset()
    if _PY_BUILTIN_METHODS is None:
        import io
        import pathlib
        import sqlite3
        names: set[str] = set()
        for typ in (str, bytes, bytearray, list, dict, set, frozenset, tuple, int, float,
                    io.TextIOWrapper, io.BytesIO, pathlib.Path, sqlite3.Connection,
                    sqlite3.Cursor):
            names |= {n for n in dir(typ) if not n.startswith("_")}
        _PY_BUILTIN_METHODS = frozenset(names)
    return _PY_BUILTIN_METHODS


_PY_BUILTIN_METHODS: frozenset[str] | None = None


def repo_module_names(repo_root) -> frozenset[str]:
    """Every directory name and source-file stem in the repo: a module whose top-level
    name is none of these is outside the repo. Read once per process and root: every
    parse runs in a short-lived process (a build child, a CLI's inline update), never in
    the daemon, so a module added later is seen by the next one."""
    return _repo_walk(repo_root)[0]


def repo_source_paths(repo_root) -> dict[str, tuple[str, ...]]:
    """Every source file of the repo, by stem: its paths relative to the root, without
    the suffix (``Store`` → ``("src/main/java/com/acme/store/Store",)``). A Java class
    is the repo's when a file of that path exists. Read with repo_module_names, once per
    process and root."""
    return _repo_walk(repo_root)[1]


_repo_walk_cache: dict[str, tuple[frozenset[str], dict[str, tuple[str, ...]]]] = {}


def _repo_walk(repo_root) -> tuple[frozenset[str], dict[str, tuple[str, ...]]]:
    import os
    from pathlib import Path
    if repo_root is None:
        return frozenset(), {}
    key = str(repo_root)
    hit = _repo_walk_cache.get(key)
    if hit is not None:
        return hit
    from .parser import LANGUAGE_MAP, _should_skip_dir
    names: set[str] = set()
    by_stem: dict[str, list[str]] = {}
    root = Path(repo_root)
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not _should_skip_dir(d)]
            rel = Path(dirpath).relative_to(root)
            for part in rel.parts:
                names.add(part)
            for f in filenames:
                p = Path(f)
                if p.suffix in LANGUAGE_MAP:
                    names.add(p.stem)
                    by_stem.setdefault(p.stem, []).append((rel / p.stem).as_posix())
    except OSError:
        pass
    out = (frozenset(names), {k: tuple(v) for k, v in by_stem.items()})
    _repo_walk_cache[key] = out
    return out


def _java_packages(root) -> list[str]:
    """This file's package (``com/acme/api``, "" for none), then each package it
    imports with ``.*`` — where a name with no import of its own may come from."""
    own, wild = "", []
    for c in root.children:
        if c.type == "package_declaration":
            name = next((k for k in c.named_children if k.type in
                         ("scoped_identifier", "identifier")), None)
            own = _text(name).replace(".", "/") if name is not None else ""
        elif c.type == "import_declaration" and any(k.type == "asterisk" for k in c.children) \
                and not any(k.type == "static" or _text(k) == "static" for k in c.children):
            name = next((k for k in c.named_children if k.type in
                         ("scoped_identifier", "identifier")), None)
            if name is not None:
                wild.append(_text(name).replace(".", "/"))
    return [own, *wild]


def _java_external(name: str, module: str, sources: dict[str, tuple[str, ...]]) -> bool:
    """A Java import outside the repo: a class no file of the repo holds (the JDK,
    ``com.amazon.coral.Activity``, a model class generated into another package).
    The import map holds the package (``a/b`` of ``import a.b.C``), or the class for a
    static or nested import (``a/b/C`` of ``import static a.b.C.m`` /
    ``import a.b.C.Inner``): the class's file is ``a/b/C``."""
    segs = [s for s in module.split("/") if s]
    first_cap = next((i for i, s in enumerate(segs) if s[:1].isupper()), None)
    path = "/".join(segs[:first_cap + 1] if first_cap is not None else segs + [name])
    stem = path.rsplit("/", 1)[-1]
    return not any(p == path or p.endswith("/" + path) for p in sources.get(stem, ()))


def is_external_module(module: str, language: str, repo_names: frozenset[str]) -> bool:
    """Whether an import's module lies outside the repo: a Python stdlib module, or one
    whose top-level name is no directory or source file of the repo; a TS/JS bare
    specifier whose first segment is none of them. A relative import never is."""
    import sys
    if not module:
        return False
    if language == "python":
        if module.startswith((".", "/")):
            return False
        top = module.replace("/", ".").split(".")[0]
        # an absolute import of a stdlib name is the stdlib (the repo's own module of
        # that name would have to sit on sys.path's root, and repo_names holds names at
        # any depth: a `types.py` deep in the tree is not what `import types` reads)
        if top in getattr(sys, "stdlib_module_names", ()):
            return True
        return top not in repo_names
    if language in ("typescript", "javascript"):
        if module.startswith((".", "/", "@/", "~", "#")):
            return False
        first = module.split("/")[0]
        return first.lstrip("@") not in repo_names and first not in repo_names
    return False


class FileTypes:
    """What one file's source states about types, for the receiver of a call: its
    classes, the imported names, which imports are outside the repo, the type of each
    class attribute (``self.x``), and the return annotation of each function."""

    def __init__(self, root, language: str, import_map: dict, top_names: set[str],
                 modules: dict[str, str] | None = None, repo_root=None):
        self.language = language
        self.import_map = dict(import_map)
        self.top_names = set(top_names)
        self.modules = dict(modules or {})
        self.classes = class_names(root)
        self.sources: dict[str, tuple[str, ...]] = {}
        self.java_pkgs: list[str] = []            # this file's package, then `.*` imports
        self.known = known_classes(self.classes, import_map)
        names = repo_module_names(repo_root)
        if language == "java":
            self.sources = repo_source_paths(repo_root)
            self.java_pkgs = _java_packages(root)
            self.ext_names = {n for n, m in import_map.items()
                              if _java_external(n, m, self.sources)}
        else:
            self.ext_names = {n for n, m in import_map.items()
                              if is_external_module(m, language, names)}
        self.ext_modules = {n for n, m in self.modules.items()
                            if is_external_module(m, language, names)}
        self.attrs: dict[str, dict[str, str]] = {}
        self.bases: dict[str, list[str]] = {}
        self.returns: dict[str, str] = {}
        self._scan(root)

    # an annotation ───────────────────────────────────────────────────────────
    def ann_type(self, type_node) -> str | None:
        """The type an annotation names: a class, EXTERNAL (a builtin, a container, an
        outside type), or None. ``Optional[X]`` / ``X | None`` / ``X | null`` is X."""
        if type_node is None:
            return None
        n = type_node
        while n.type in ("type", "type_annotation", "parenthesized_type") and n.named_child_count == 1:
            n = n.named_children[0]
        t = n.type
        if t == "scoped_type_identifier":          # Outer.Inner / a.b.C
            parts = _text(n).split(".")
            if self.language == "java" and len(parts) > 1 and parts[0][:1].islower():
                pkg = "/".join(p for p in parts[:-1] if p[:1].islower())
                outer = next((p for p in parts if p[:1].isupper()), parts[-1])
                return EXTERNAL if _java_external(outer, pkg, self.sources) else parts[-1]
            return self._named(parts[-1])
        if t == "predefined_type":
            return None if _text(n) in _TS_UNKNOWN_TYPES else EXTERNAL
        if t in ("array_type", "tuple_type", "function_type", "template_literal_type",
                 "object_type", "readonly_type"):
            return EXTERNAL
        if t == "literal_type":
            return None if _text(n) in _NULLS else EXTERNAL
        if t in ("union_type", "binary_operator"):
            parts = [c for c in (n.named_children if t == "union_type" else
                                 (n.child_by_field_name("left"), n.child_by_field_name("right")))
                     if c is not None and _text(c) not in _NULLS and _text(c) != "None"]
            types = {self.ann_type(c) for c in parts}
            return types.pop() if len(types) == 1 else None
        if t in ("generic_type", "subscript"):
            head_node = n.child_by_field_name("name") or n.child_by_field_name("value") or (
                n.named_children[0] if n.named_children else None)
            head = _text(head_node).split(".")[-1]
            args = [c for c in n.named_children if c is not head_node]
            if args and args[0].type in ("type_parameter", "type_arguments"):
                args = list(args[0].named_children)
            if self.language == "java":           # List<Store>, Optional<Store>: java.util's
                return self._java_class(head)
            if head in ("Optional", "Union"):
                types = {self.ann_type(c) for c in args if _text(c) not in ("None",)}
                return types.pop() if len(types) == 1 else None
            if head in ("Any", "TypeVar", "Annotated", "ClassVar", "Final"):
                return self.ann_type(args[0]) if head in ("ClassVar", "Final") and args else None
            if head in self.classes or (head in self.import_map and head not in self.ext_names):
                return head                        # a repo generic: Node[int], Response<T>
            builtin = (_PY_BUILTIN_TYPES if self.language == "python" else
                       _JAVA_GLOBALS if self.language == "java" else _TS_GLOBALS)
            if head in builtin or head in self.ext_names:
                return EXTERNAL
            return None
        if t in ("string", "string_fragment", "string_content"):
            inner = [c for c in n.children if c.type in ("string_content", "string_fragment")]
            return self._named(_text(inner[0] if inner else n).split(".")[-1])
        if t in ("attribute", "nested_type_identifier"):          # mod.Store / ns.Store
            obj = n.child_by_field_name("object") or n.child_by_field_name("module")
            attr = n.child_by_field_name("attribute") or n.child_by_field_name("name")
            if obj is not None and _text(obj).split(".")[0] in (self.ext_modules | self.ext_names):
                return EXTERNAL
            return self._named(_text(attr)) if attr is not None else None
        if t in ("identifier", "type_identifier"):
            return None if _text(n) == "var" else self._named(_text(n))
        return None

    def _named(self, name: str) -> str | None:
        if not name or not _IDENT.match(name):
            return None
        if name in self.classes:
            return name
        if name in self.ext_names:
            return EXTERNAL
        if self.language == "python" and name in _PY_BUILTIN_TYPES and name not in self.import_map:
            return EXTERNAL
        if self.language == "java":
            return self._java_class(name)
        if self.language != "python" and name in _TS_GLOBALS and name not in self.import_map \
                and name not in self.top_names:
            return EXTERNAL
        if name in ("Any", "None", "Optional", "typing"):
            return None
        return name                                    # a class the store resolves by name

    # a value ─────────────────────────────────────────────────────────────────
    def value_type(self, node, scope: "Scope | None", enclosing_class: str | None,
                   _depth: int = 0) -> str | None:
        """The type of an expression, when the source states it (see EXTERNAL)."""
        if node is None or _depth > 24:
            return None
        t = node.type
        if t in _LITERALS or (self.language == "java" and t in _JAVA_LITERALS):
            return EXTERNAL
        if self.language == "java":
            got = self._java_value(node, scope, enclosing_class, _depth)
            if got is not _NOT_JAVA:
                return got
        if t in ("parenthesized_expression", "non_null_expression"):
            inner = node.named_children
            return self.value_type(inner[0], scope, enclosing_class, _depth + 1) if inner else None
        if t == "as_expression":
            return self.ann_type(node.named_children[-1]) if node.named_children else None
        if t == "binary_operator":
            ops = (node.child_by_field_name("left"), node.child_by_field_name("right"))
            if any(o is not None and o.type in ("string", "concatenated_string") for o in ops):
                return EXTERNAL                              # "…" % x, "…" + x
            return None
        if t in ("identifier", "this", "self"):
            return self._name_type(_text(node), scope, enclosing_class)
        if t in ("attribute", "member_expression"):
            obj = node.child_by_field_name("object")
            attr = node.child_by_field_name("attribute") or node.child_by_field_name("property")
            if obj is None or attr is None:
                return None
            if obj.type == "identifier" and _text(obj) in self.ext_modules and \
                    not (scope is not None and _text(obj) in scope.bound):
                return EXTERNAL                              # os.environ, sqlite3.Row
            owner = self.value_type(obj, scope, enclosing_class, _depth + 1)
            if owner == EXTERNAL:
                return EXTERNAL
            if owner is None or (obj.type == "identifier" and _text(obj) == owner):
                return None                                  # Store.x: the class, not one
            return self.attr_type(owner, _text(attr))
        if t in ("call", "call_expression"):
            fn = node.child_by_field_name("function")
            if fn is None:
                return None
            if fn.type == "identifier":
                name = _text(fn)
                if scope is not None and name in scope.bound:
                    return None
                if name in self.classes or (name in self.known and name not in self.ext_names):
                    return name                              # Store(...)
                if name in self.ext_names:
                    if name in _PASSTHROUGH:
                        return self._passthrough(name, node, scope, enclosing_class, _depth)
                    return EXTERNAL                          # Path(...), defaultdict()
                if self.language == "python" and name in _PY_BUILTIN_CALLS and \
                        name not in self.top_names and name not in self.import_map:
                    return EXTERNAL
                if self.language != "python" and name in _TS_GLOBALS and \
                        name not in self.top_names and name not in self.import_map:
                    return EXTERNAL                          # String(x), Number(x)
                return self.returns.get(name)
            if fn.type in ("attribute", "member_expression"):
                obj = fn.child_by_field_name("object")
                attr = fn.child_by_field_name("attribute") or fn.child_by_field_name("property")
                if obj is None or attr is None:
                    return None
                if obj.type == "identifier" and _text(obj) in self.ext_modules and \
                        not (scope is not None and _text(obj) in scope.bound):
                    if _text(attr) in _PASSTHROUGH:          # typing.cast, copy.copy
                        return self._passthrough(_text(attr), node, scope, enclosing_class, _depth)
                    return EXTERNAL                          # sqlite3.connect()
                owner = self.value_type(obj, scope, enclosing_class, _depth + 1)
                if owner == EXTERNAL:
                    return EXTERNAL                          # Path(x).resolve()
                if owner is not None:
                    return self.returns.get(f"{owner}.{_text(attr)}")
            return None
        if t == "new_expression":
            ctor = node.child_by_field_name("constructor")
            name = _text(ctor) if ctor is not None and ctor.type == "identifier" else ""
            if not name:
                return None
            if name in self.classes or (name in self.known and name not in self.ext_names):
                return name
            if name in self.ext_names or (name in _TS_GLOBALS and name not in self.top_names
                                          and name not in self.import_map):
                return EXTERNAL
            return None
        return None

    def _passthrough(self, name: str, call, scope, enclosing_class, depth) -> str | None:
        """An outside function that returns a value of its argument's type: ``cast(T,
        x)`` is a T, ``copy(x)`` / ``deepcopy(x)`` / ``replace(x, …)`` are x's type."""
        args = call.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if first is None:
            return None
        if name == "cast":
            return self.ann_type(first) or (self._named(_text(first).split(".")[-1])
                                            if first.type in ("identifier", "attribute") else None)
        return self.value_type(first, scope, enclosing_class, depth + 1)

    def _java_class(self, name: str) -> str | None:
        """What a Java type name is, by the language's own order: a class of this
        file; an import (EXTERNAL when no file of the repo holds it); a class of this
        package, or of a package imported with ``.*``, whose file the repo has; a
        ``java.lang`` name (EXTERNAL); else None — a type variable (``<T>``), or a
        class a ``.*`` import of a library may bring."""
        if name in self.classes:
            return name
        if name in self.import_map:                # (a static import names a member)
            if name in self.ext_names:
                return EXTERNAL
            return name if name[:1].isupper() else None
        if name[:1].isupper() and any(
                p == f"{pkg}/{name}" or p.endswith(f"/{pkg}/{name}") if pkg else p == name
                or p.endswith("/" + name)
                for p in self.sources.get(name, ()) for pkg in self.java_pkgs):
            return name
        if name in _JAVA_GLOBALS:
            return EXTERNAL
        return None

    def _java_value(self, node, scope, enclosing_class, depth):
        """A Java expression's type, or _NOT_JAVA for the shared cases."""
        t = node.type
        if t == "object_creation_expression":
            return self.ann_type(node.child_by_field_name("type"))
        if t == "cast_expression":
            return self.ann_type(node.child_by_field_name("type"))
        if t == "field_access":
            obj, fld = node.child_by_field_name("object"), node.child_by_field_name("field")
            if obj is None or fld is None:
                return None
            owner = self.value_type(obj, scope, enclosing_class, depth + 1)
            if owner == EXTERNAL:
                return EXTERNAL
            if owner is None:
                return None
            return self.attr_type(owner, _text(fld))
        if t == "method_invocation":
            obj, name = node.child_by_field_name("object"), _text(node.child_by_field_name("name"))
            if obj is None:                         # make(): this class's, or a static import
                if enclosing_class:
                    got = self._method_return(enclosing_class, name)
                    if got is not None:
                        return got
                return self.returns.get(name)
            owner = self.value_type(obj, scope, enclosing_class, depth + 1)
            if owner == EXTERNAL:
                # "a".trim(), list.stream(): a JDK value; list.get(0), opt.get(): the
                # element, which may be the repo's (linked by name)
                return EXTERNAL if name in _JAVA_JDK_RESULTS else None
            return self._method_return(owner, name) if owner is not None else None
        if t == "ternary_expression" or t == "lambda_expression":
            return None
        return _NOT_JAVA

    def _method_return(self, cls: str, name: str, _seen: frozenset = frozenset()) -> str | None:
        """The return type of ``cls.name``, here or in a base defined in this file."""
        if cls in _seen:
            return None
        got = self.returns.get(f"{cls}.{name}")
        if got is not None:
            return got
        for base in self.bases.get(cls, []):
            found = self._method_return(base, name, _seen | {cls})
            if found is not None:
                return found
        return None

    def _name_type(self, name: str, scope: "Scope | None", enclosing_class: str | None):
        if name in _SELF:
            if name == "this" and scope is not None and not scope.this_ok:
                return None
            return enclosing_class
        if scope is not None and name in scope.block_locals and enclosing_class and \
                self._has_field(enclosing_class, name):
            return None                             # the block's local, or the field
        if scope is not None and name in scope.types:
            return scope.types[name]
        if scope is not None and name in scope.bound:
            return None
        if self.language == "java":
            if enclosing_class:                     # a field, without `this.`
                got = self.attr_type(enclosing_class, name)
                if got is not None:
                    return got
            return self._java_class(name)          # Repo.find(), System.out, UUID
        if name in self.ext_modules or name in self.ext_names:
            return EXTERNAL
        if name in self.classes:
            return name
        if name in self.import_map and name[:1].isupper():
            return name
        if self.language != "python" and name in _TS_GLOBALS and name not in self.top_names \
                and name not in self.import_map:
            return EXTERNAL                                  # JSON.parse, console.log
        return None

    def _has_field(self, cls: str, attr: str, _seen: frozenset = frozenset()) -> bool:
        if cls in _seen:
            return False
        if attr in self.attrs.get(cls, {}):
            return True
        return any(self._has_field(b, attr, _seen | {cls}) for b in self.bases.get(cls, []))

    def attr_type(self, cls: str, attr: str, _seen: frozenset = frozenset()) -> str | None:
        """The type of ``cls``'s attribute *attr*, here or in a base defined in this file."""
        if cls in _seen:
            return None
        got = self.attrs.get(cls, {}).get(attr)
        if got is not None:
            return got or None
        for base in self.bases.get(cls, []):
            found = self.attr_type(base, attr, _seen | {cls})
            if found is not None:
                return found
        return None

    # the per-file scan ───────────────────────────────────────────────────────
    def _scan(self, root) -> None:
        stack = [(root, None)]
        while stack:
            n, cls = stack.pop()
            if n.type in _CLASS_TYPES:
                name = _text(n.child_by_field_name("name"))
                if _IDENT.match(name or ""):
                    self._scan_class(n, name)
                    stack.extend((c, name) for c in n.children)
                    continue
            if n.type in ("function_definition", "function_declaration", "method_definition",
                          "method_declaration", "constructor_declaration"):
                name = _text(n.child_by_field_name("name"))
                ret = self.ann_type(n.child_by_field_name("return_type") or (
                    n.child_by_field_name("type") if n.type == "method_declaration" else None))
                if ret and _IDENT.match(name or ""):
                    self.returns[f"{cls}.{name}" if cls else name] = ret
                continue                                  # a nested def is not a top one
            stack.extend((c, cls) for c in n.children)

    def _scan_class(self, node, cls: str) -> None:
        from .parser import _extract_base_names
        self.bases[cls] = [b for b in _extract_base_names(node, self.language) if b != cls]
        seen: dict[str, set] = {}

        def bind(attr: str, typ) -> None:
            if attr and _IDENT.match(attr):
                seen.setdefault(attr, set()).add(typ)

        if node.type == "record_declaration":           # java: record P(Store s) → P.s
            for p in (node.child_by_field_name("parameters") or node).named_children:
                if p.type == "formal_parameter":
                    bind(_text(p.child_by_field_name("name")),
                         self.ann_type(p.child_by_field_name("type")))
        body = node.child_by_field_name("body")
        for child in (body.children if body is not None else []):
            item = child
            if item.type == "expression_statement" and item.named_child_count == 1:
                item = item.named_children[0]
            if item.type == "decorated_definition":
                item = item.child_by_field_name("definition") or item
            if item.type == "assignment":                 # python class-level x: T = v
                left = item.child_by_field_name("left")
                if left is not None and left.type == "identifier":
                    right = item.child_by_field_name("right")
                    if right is not None and right.type in ("call",) and \
                            _text(right.child_by_field_name("function")).endswith("field"):
                        right = None                       # dataclass field(...)
                    typ = self.ann_type(item.child_by_field_name("type")) or (
                        self.value_type(right, None, cls) if right is not None
                        and right.type not in _NULLS else None)
                    if right is not None and right.type in _NULLS and \
                            item.child_by_field_name("type") is None:
                        continue
                    bind(_text(left), typ)
            elif item.type in ("public_field_definition", "field_definition"):
                name = _text(item.child_by_field_name("name") or item.child_by_field_name("property"))
                value = item.child_by_field_name("value")
                typ = self.ann_type(item.child_by_field_name("type")) or (
                    self.value_type(value, None, cls) if value is not None else None)
                if value is not None and value.type in _NULLS and typ is None:
                    continue
                bind(name, typ)
            elif item.type == "field_declaration":       # java: private Store store = …;
                typ = self.ann_type(item.child_by_field_name("type"))
                for d in item.children:
                    if d.type == "variable_declarator":
                        bind(_text(d.child_by_field_name("name")), typ)
            elif item.type in ("function_definition", "method_definition"):
                self._scan_method(item, cls, bind)
        self.attrs[cls] = {a: (next(iter(ts)) if len(ts) == 1 and None not in ts else "")
                           for a, ts in seen.items()}

    def _scan_method(self, method, cls: str, bind) -> None:
        scope = scope_of(method, self.language, self, None)
        name = _text(method.child_by_field_name("name"))
        params = method.child_by_field_name("parameters")
        if name == "constructor" and params is not None:   # TS parameter properties
            for p in params.children:
                if p.type in ("required_parameter", "optional_parameter") and any(
                        c.type == "accessibility_modifier" or _text(c) == "readonly"
                        for c in p.children):
                    pat = p.child_by_field_name("pattern")
                    if pat is not None and pat.type == "identifier":
                        bind(_text(pat), self.ann_type(p.child_by_field_name("type")))
        me = "self"
        if self.language == "python" and params is not None:
            first = next((c for c in params.children if c.type == "identifier"), None)
            me = _text(first) if first is not None else "self"
        body = method.child_by_field_name("body")
        stack = [body] if body is not None else []
        while stack:
            n = stack.pop()
            if n.type in _FUNC_TYPES or n.type in _CLASS_TYPES:
                continue
            if n.type in ("assignment", "assignment_expression"):
                left = n.child_by_field_name("left")
                if left is not None and left.type in ("attribute", "member_expression",
                                                      "field_access"):
                    obj = left.child_by_field_name("object")
                    attr = (left.child_by_field_name("attribute") or left.child_by_field_name("property")
                            or left.child_by_field_name("field"))
                    if obj is not None and attr is not None and (
                            (obj.type == "identifier" and _text(obj) == me) or obj.type == "this"):
                        right = n.child_by_field_name("right")
                        if right is not None and right.type in _NULLS and \
                                n.child_by_field_name("type") is None:
                            pass                        # self.x = None: no type either way
                        else:
                            bind(_text(attr), self.ann_type(n.child_by_field_name("type"))
                                 or self.value_type(right, scope, cls))
            stack.extend(n.children)


def _constructed_class(value, classes: set[str]) -> str | None:
    """``Store(...)`` / ``new Store(...)`` → Store, when Store is a known class."""
    if value is None:
        return None
    if value.type == "call":
        fn = value.child_by_field_name("function")
    elif value.type == "new_expression":
        fn = value.child_by_field_name("constructor")
    else:
        return None
    name = _text(fn) if fn is not None and fn.type == "identifier" else ""
    return name if name in classes else None


class Scope:
    """What a function's own code binds: names to types (where the source says) and
    every locally bound name; ``this_ok`` is False inside a TS/JS ``function``
    expression, whose ``this`` is not the enclosing class's."""

    def __init__(self, types: dict[str, str] | None = None, bound: set[str] | None = None,
                 this_ok: bool = True, block_locals: set[str] | None = None):
        self.types = dict(types or {})
        self.bound = set(bound or ())
        self.this_ok = this_ok
        # java: names declared in a nested block / a statement (for, catch, if …);
        # outside it, the name may be a field (the scope does not see blocks)
        self.block_locals = set(block_locals or ())

    def child(self) -> "Scope":
        return Scope(self.types, self.bound, self.this_ok, self.block_locals)


# Python lambda / TS-JS function expressions: a scope of their own the walker does not
# get as a definition (they are not graph symbols).
NESTED_SCOPE_TYPES = frozenset({"lambda", "function_expression", "function",
                                "lambda_expression", "constructor_declaration"})
_PATTERN_LEAVES = ("identifier", "shorthand_property_identifier_pattern")


def scope_of(func_node, language: str, ft: "FileTypes", outer: Scope | None,
             enclosing_class: str | None = None) -> Scope:
    """The scope of *func_node*: its parameters and the names its body binds (not
    those of nested functions or classes), on top of *outer* (a closure sees it).

    A name has a type only when EVERY binding of it in this scope gives that same
    type (``x = Store()``, ``s: Store``, ``cmd = []`` → EXTERNAL); any other binding —
    a call whose result the source does not type, ``except … as x``, ``with … as x``,
    a loop or comprehension variable, a destructured name — leaves it untyped here,
    and so hides an outer scope's type for it too. A binding to None / null is
    neither (``x = None`` then ``x = Store()`` is a Store). Order is not used: a
    confident wrong edge is worse than a link by name."""
    scope = outer.child() if outer is not None else Scope()
    if language in ("typescript", "javascript") and func_node.type in (
            "function_expression", "function", "function_declaration"):
        scope.this_ok = False
    seen: dict[str, set[str | None]] = {}

    def bind(name: str, cls: str | None) -> None:
        if not name or not _IDENT.match(name):
            return
        scope.bound.add(name)
        seen.setdefault(name, set()).add(cls)

    def bind_leaves(target) -> None:
        for kind in _PATTERN_LEAVES:
            for leaf in _leaves(target, kind) if target is not None else []:
                bind(leaf, None)

    # java: `var x = store` / `this.store` / `make()` read the class's fields and
    # methods (python's `self` is a parameter, typed by the walker)
    owner = enclosing_class if language == "java" else None

    def value(v):
        return None if v is None else ft.value_type(v, scope, owner)

    params = func_node.child_by_field_name("parameters") or \
        func_node.child_by_field_name("parameter")
    plist = ([params] if params is not None and params.type == "identifier"
             else (params.children if params is not None else []))
    for p in plist:
        if p.type == "identifier":
            bind(_text(p), None)
        elif p.type in ("typed_parameter", "typed_default_parameter", "default_parameter"):
            ident = next((c for c in p.children if c.type == "identifier"), None)
            name = _text(p.child_by_field_name("name") or ident)
            bind(name, ft.ann_type(p.child_by_field_name("type")))
        elif p.type in ("formal_parameter", "spread_parameter"):      # java
            ident = p.child_by_field_name("name") or next(
                (c for c in p.named_children if c.type == "variable_declarator"), None)
            if ident is not None and ident.type == "variable_declarator":
                ident = ident.child_by_field_name("name")
            typ = ft.ann_type(p.child_by_field_name("type") or next(
                (c for c in p.named_children if c.type.endswith("type") or
                 c.type == "type_identifier"), None))
            bind(_text(ident), EXTERNAL if p.type == "spread_parameter" else typ)
        elif p.type in ("required_parameter", "optional_parameter"):
            pattern = p.child_by_field_name("pattern")
            ann = p.child_by_field_name("type")
            if pattern is not None and pattern.type == "identifier":
                bind(_text(pattern), ft.ann_type(ann))
            else:
                bind_leaves(pattern)
        else:
            bind_leaves(p)                    # *args, **kw, destructured params

    body = func_node.child_by_field_name("body")
    stack = [body] if body is not None else []
    pending: list[tuple[str, object, object]] = []
    while stack:
        n = stack.pop()
        if n.type in _FUNC_TYPES or n.type in _CLASS_TYPES:
            continue                          # a nested scope binds its own names
        if language == "java":
            if n.type in ("local_variable_declaration", "resource"):
                ann = n.child_by_field_name("type")
                decls = ([n] if n.type == "resource" else
                         [d for d in n.named_children if d.type == "variable_declarator"])
                nested = n.type == "resource" or n.parent != body
                for d in decls:
                    name = d.child_by_field_name("name")
                    if name is not None and name.type == "identifier":
                        pending.append((_text(name), ann, d.child_by_field_name("value")))
                        if nested:
                            scope.block_locals.add(_text(name))
                    v = d.child_by_field_name("value")
                    if v is not None:
                        stack.append(v)
                continue
            if n.type in ("enhanced_for_statement", "instanceof_expression"):
                name = n.child_by_field_name("name")
                if name is not None and name.type == "identifier":
                    ann = n.child_by_field_name("type") or n.child_by_field_name("right")
                    bind(_text(name), ft.ann_type(ann))
                    scope.block_locals.add(_text(name))
            elif n.type == "catch_formal_parameter":
                types = [c for c in (n.named_children[0].named_children
                                     if n.named_children and n.named_children[0].type == "catch_type"
                                     else [])]
                bind(_text(n.child_by_field_name("name")),
                     ft.ann_type(types[0]) if len(types) == 1 else None)
                scope.block_locals.add(_text(n.child_by_field_name("name")))
            # an assignment never declares in java, and cannot change a declared type
            stack.extend(n.children)
            continue
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                pending.append((_text(left), n.child_by_field_name("type"),
                                n.child_by_field_name("right")))
            elif left is not None and left.type not in ("attribute", "subscript"):
                bind_leaves(left)             # a, b = …: bound, type unknown
        elif n.type == "augmented_assignment":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                bind(_text(left), None)
        elif n.type == "variable_declarator":
            name = n.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                pending.append((_text(name), n.child_by_field_name("type"),
                                n.child_by_field_name("value")))
            else:
                bind_leaves(name)             # const { save } = deps
        elif n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                pending.append((_text(left), None, n.child_by_field_name("right")))
        elif n.type in ("for_statement", "for_in_statement", "for_in_clause",
                        "named_expression"):
            bind_leaves(n.child_by_field_name("left") or n.child_by_field_name("name"))
        elif n.type == "as_pattern":          # except E as x / with f() as x
            bind_leaves(n.child_by_field_name("alias"))
        stack.extend(n.children)
    # every local is bound before any value is typed: `x = y` where y is a local reads
    # the local, never a module name
    for name, _ann, _v in pending:
        scope.bound.add(name)
    for name, ann, v in pending:
        if v is not None and v.type in _NULLS and ann is None:
            scope.bound.add(name)
            continue                          # x = None: neither a type nor untyped
        bind(name, ft.ann_type(ann) or value(v))
    for name, classes_seen in seen.items():
        if len(classes_seen) == 1 and None not in classes_seen:
            scope.types[name] = next(iter(classes_seen))
        else:
            scope.types.pop(name, None)
    return scope


def method_reference_target(node, ft: "FileTypes", scope: "Scope | None",
                            enclosing_class: str | None) -> tuple[str, str] | None:
    """``(class, method)`` a Java method reference names: ``this::save`` /
    ``Store::save`` / ``store::save`` (by the value's type); None when the class is
    unknown or outside the repo, or for ``X::new``."""
    kids = node.named_children
    if len(kids) != 2 or kids[1].type != "identifier":
        return None
    left, member = kids[0], _text(kids[1])
    if left.type in ("type_identifier", "scoped_type_identifier", "generic_type"):
        cls = ft.ann_type(left)
    else:
        cls = ft.value_type(left, scope, enclosing_class)
    if not cls or cls == EXTERNAL:
        return None
    return cls, member


def _leaves(node, kind: str) -> list[str]:
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if n.type == kind:
            out.append(_text(n))
        stack.extend(n.children)
    return out


def direct_receiver(call_node, classes: set[str] | None = None) -> str | None:
    """The receiver of a member call when it is ONE name (``x`` of ``x.save()``,
    ``self``/``this``) or a constructor of a known class (``Store().save()`` →
    ``Store``); None for a plain call or a chain (``self.db.save()``)."""
    fn = call_node.child_by_field_name("function")
    if fn is None or fn.type not in ("attribute", "member_expression"):
        return None
    recv = fn.child_by_field_name("object")
    if recv is None:
        return None
    if recv.type in ("this", "self"):
        return _text(recv)
    if recv.type == "identifier":
        return _text(recv)
    built = _constructed_class(recv, classes or set())
    return built


def receiver_type(call_node, ft: "FileTypes", scope: Scope | None,
                  enclosing_class: str | None) -> str | None:
    """The type of a member call's receiver: a class, EXTERNAL, or None (unknown, or
    not a member call). ``self.db.save()`` is typed by ``self.db``'s attribute type,
    ``Path(x).resolve()`` and ``conn.execute()`` with ``conn = sqlite3.connect()``
    are EXTERNAL."""
    if call_node.type == "method_invocation":        # java: obj.m(), Cls.m(), this.m()
        recv = call_node.child_by_field_name("object")
        if recv is None or recv.type == "super":
            return None
        return ft.value_type(recv, scope, enclosing_class)
    fn = call_node.child_by_field_name("function")
    if fn is None or fn.type not in ("attribute", "member_expression"):
        return None
    recv = fn.child_by_field_name("object")
    if recv is None:
        return None
    if recv.type == "identifier":
        name = _text(recv)
        if name in ft.modules and name not in ft.ext_modules and \
                not (scope is not None and name in scope.bound):
            return None                       # an internal module alias: the caller's
    return ft.value_type(recv, scope, enclosing_class)


def class_target(cls: str, member: str, path: str, classes: set[str], import_map: dict,
                 sep: str, qualify) -> str:
    """The target id of ``cls.member``: this file's class, an imported one (module
    qualified, resolved by the store), or the bare ``Cls.member``."""
    if cls in classes:
        return qualify(member, path, cls)
    if cls in import_map:
        return f"{import_map[cls]}{sep}{cls}.{member}"
    return f"{cls}.{member}"


_VALUE_PARENTS = frozenset({
    "argument_list", "arguments", "list", "tuple", "set", "array", "return_statement",
    "expression_list", "decorator", "parenthesized_expression",
})


def in_value_position(node) -> bool:
    """Whether *node* is used as a value: an argument, a keyword value, an assigned
    value, a container element, a returned value, a decorator."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type in ("argument_list", "class_heritage", "extends_clause") and \
            parent.parent is not None and parent.parent.type in _CLASS_TYPES | {"class_heritage"}:
        return False                          # a class's bases (extends edges hold them)
    if parent.type in _VALUE_PARENTS:
        return True
    if parent.type == "keyword_argument":
        return _is(parent, "value", node)
    if parent.type in ("assignment", "augmented_assignment", "assignment_expression"):
        return _is(parent, "right", node)
    if parent.type == "variable_declarator":
        return _is(parent, "value", node)
    if parent.type == "pair":
        return _is(parent, "value", node)
    return False


def value_reference(node, *, path: str, scope: Scope | None, classes: set[str],
                    import_map: dict, top_names: set[str], enclosing_class: str | None,
                    sep: str, qualify) -> str | None:
    """The target id a value-position *node* references, or None."""
    if node.type in ("identifier", "shorthand_property_identifier"):
        if node.type == "identifier" and not in_value_position(node):
            return None
        name = _text(node)
        if scope is not None and name in scope.bound:
            return None
        if name in top_names:
            return qualify(name, path, None)
        if name in import_map:
            # matched by its module path only (the caller marks it so): a name that
            # is a module (`reload(store)`) must not reach a same-named function
            return f"{import_map[name]}{sep}{name}"
        return None
    if node.type in ("attribute", "member_expression") and in_value_position(node):
        recv = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute") or node.child_by_field_name("property")
        if recv is None or attr is None:
            return None
        if recv.type in ("this", "self") or _text(recv) in _SELF:
            if not enclosing_class:
                return None
            return qualify(_text(attr), path, enclosing_class)
        if recv.type == "identifier" and _text(recv) in known_classes(classes, import_map):
            if scope is not None and _text(recv) in scope.bound:
                return None
            return class_target(_text(recv), _text(attr), path, classes, import_map, sep, qualify)
    return None


def module_aliases(content: str) -> dict[str, str]:
    """``import a.b as m`` / ``from a import b`` (a module) → name → ``a/b``: the
    module a name stands for, for ``setattr(m, "f", …)`` patches."""
    from .parser import scan_imports
    out: dict[str, str] = {}
    for ref in scan_imports(content, "python"):
        if ref.whole_module:
            # `import a.b` binds `a`, not `b`: only a plain or aliased import names it
            if "." not in ref.module or ref.name != ref.module.rsplit(".", 1)[-1]:
                out[ref.name] = ref.module.replace(".", "/")
    # `from a import b` / `from a import b as c`: b may be a module (a/b); a lowercase
    # name only (a class is typed instead). scan_imports keeps the alias, not b.
    from .parser import _PY_FROM_RE
    for m in _PY_FROM_RE.finditer(content):
        if m.group(1).startswith("."):
            continue
        body = re.sub(r"#[^\n]*", "", m.group(2)).strip().strip("()")
        for part in body.split(","):
            orig, _, alias = (x.strip() for x in part.strip().partition(" as "))
            name = alias or orig
            if orig.isidentifier() and name.isidentifier() and name[:1].islower():
                out.setdefault(name, f"{m.group(1)}.{orig}".replace(".", "/"))
    return out


def patch_reference(call_node, call_name: str | None, *, path: str, classes: set[str],
                    import_map: dict, sep: str, qualify,
                    modules: dict[str, str] | None = None) -> str | None:
    """The symbol a Python test patch names, or None: ``patch("a.b.f")``,
    ``monkeypatch.setattr("a.b.f", …)``, ``patch.object(Cls, "m")``,
    ``monkeypatch.setattr(mod, "f", …)`` / ``patch.object(mod, "f")``."""
    args = call_node.child_by_field_name("arguments")
    if args is None or call_name not in ("patch", "setattr", "object"):
        return None
    positional = [a for a in args.children if a.type not in ("(", ")", ",", "keyword_argument")]
    fn = call_node.child_by_field_name("function")
    two_arg = (call_name == "object" and _text(fn).endswith("patch.object")) or (
        call_name == "setattr" and fn is not None and fn.type == "attribute")
    if two_arg and len(positional) >= 2 and positional[0].type == "identifier":
        owner, member = _text(positional[0]), _string(positional[1])
        if not member or not _IDENT.match(member):
            return None
        if owner in classes or (owner in import_map and owner[:1].isupper()):
            return class_target(owner, member, path, classes, import_map, sep, qualify)
        if modules and owner in modules:
            return f"{modules[owner]}{sep}{member}"
        return None
    if call_name == "object":
        return None
    dotted = _string(positional[0]) if positional else None
    if not dotted or not _DOTTED.match(dotted):
        return None
    parts = dotted.split(".")
    if parts[-2][:1].isupper():               # pkg.mod.Cls.method
        return f"{'/'.join(parts[:-2])}{sep}{parts[-2]}.{parts[-1]}"
    return f"{'/'.join(parts[:-1])}{sep}{parts[-1]}"


def _string(node) -> str | None:
    if node is None or node.type != "string":
        return None
    content = [c for c in node.children if c.type in ("string_content", "string_fragment")]
    return _text(content[0]) if len(content) == 1 else None
