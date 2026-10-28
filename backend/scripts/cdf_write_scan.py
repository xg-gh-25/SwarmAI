"""Static scan for direct writes to CDF-governed files (CDF §8 stage-B gate).

A module that names a governed location (``MARKER``) is a marker module. A **hit** is
a function in a marker module that writes a file: directly (a sink below), or by
calling a **helper writer**, a function in a non-marker module that writes, directly
or through another helper (calls resolved across modules through imports,
``self`` / ``cls`` and class names). A writer in a marker module is a hit itself, so
the chain stops there. The sanctioned writers (``EXEMPT``: the CDF writer,
materializer and governed editor) are never hits and never helpers. The
caller decides whether a hit is a real governed write or a documented exemption
(``tests/cdf_materializer_allowlist.txt``).

Sinks: ``open`` / ``io.open`` / ``codecs.open`` / ``os.fdopen`` / ``Path.open`` with a
write mode, or a mode the scanner cannot read (fail closed); ``os.open`` with a write
flag; ``write_text`` / ``write_bytes`` / ``touch`` / ``unlink``; ``os.remove`` /
``os.unlink`` / ``os.truncate`` / ``os.replace`` / ``os.rename``; ``Path.rename``;
``Path.replace`` (one argument, so ``str.replace`` is not one); ``shutil.move`` /
``copy*`` / ``rmtree``.

Key public symbols: ``MARKER``, ``EXEMPT``, ``ROOTS``, ``sink_kind``,
``scan_tree``, ``Hit``.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

MARKER = re.compile(
    r"MEMORY\.md|EVOLUTION\.md|STEERING\.md|USER\.md|TOOLS\.md|TIMELINE\.md|"
    r"PRODUCT\.md|TECH\.md|IMPROVEMENT\.md|PROJECT\.md|"
    r"ddd_path|ddd_write_path|Knowledge/"
)
EXEMPT = frozenset({
    "core/ingestion/materializer.py",
    "core/ingestion/governed_edit.py",
    "core/ingestion/writer.py",  # the single CDF writer (journaled plans + materializer)
})
# Every production source under backend/: whole top-level packages and modules.
ROOTS = ("apps", "channels", "core", "database", "hooks", "jobs", "mcp_servers",
         "middleware", "routers", "scripts", "skills", "templates", "utils")
TOP_LEVEL = ("config.py", "config_settings.py", "desktop_main.py", "main.py",
             "model_registry.py")
_SKIP_PARTS = {"tests", "__pycache__", ".venv", "node_modules", "upstream"}

_OPEN_FUNCS = {"open", "io.open", "codecs.open", "os.fdopen"}
_WRITE_FLAGS = {"O_WRONLY", "O_RDWR", "O_CREAT", "O_APPEND", "O_TRUNC"}
_LEAF_SINKS = {"write_text", "write_bytes", "touch", "unlink", "rmtree"}
_OS_SINKS = {"os.remove", "os.unlink", "os.truncate", "os.replace", "os.rename"}


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _mode_is_write(call: ast.Call, position: int) -> bool:
    """True when the mode writes, or cannot be read (fail closed); absent = read."""
    values = list(call.args[position:position + 1])
    values += [k.value for k in call.keywords if k.arg == "mode"]
    if not values:
        return False
    for value in values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if any(flag in value.value for flag in "wax+"):
                return True
        else:
            return True
    return False


def sink_kind(call: ast.Call) -> str | None:
    """The sink a call is, or None."""
    name = _name(call.func)
    leaf = name.rsplit(".", 1)[-1]
    if name == "os.open":  # before Path.open: same leaf, flags not a mode
        flags = call.args[1] if len(call.args) > 1 else None
        names = {n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", "")
                 for n in ast.walk(flags)} if flags is not None else set()
        return "os.open" if (names & _WRITE_FLAGS or not (names & {"O_RDONLY"})) else None
    if name in _OPEN_FUNCS:
        return "open" if _mode_is_write(call, 1) else None
    if leaf == "open" and isinstance(call.func, ast.Attribute):  # Path.open(mode)
        return "open" if _mode_is_write(call, 0) else None
    if name in _OS_SINKS:
        return name
    if leaf in _LEAF_SINKS:
        return leaf
    if name.startswith("shutil.") and (leaf == "move" or leaf.startswith("copy")):
        return name
    if leaf == "rename":
        return "rename"
    if leaf == "replace" and isinstance(call.func, ast.Attribute) \
            and len(call.args) == 1 and not call.keywords:
        return "Path.replace"
    return None


@dataclass
class _Func:
    module: str
    qual: str
    rel: str
    sinks: list[str] = field(default_factory=list)
    calls: list[tuple[str, str]] = field(default_factory=list)  # (module, qual) targets


@dataclass(frozen=True)
class Hit:
    """One writer function in a module that names a governed location."""

    key: str            # "<relpath>:<qualname>"
    via: str            # the sink kind, or "via <module>:<qual>" for a helper write


def _module_name(rel: str) -> str:
    mod = rel[:-3].replace("/", ".")
    return mod[:-9] if mod.endswith(".__init__") else mod


def _resolve_relative(module: str, level: int, target: str | None, is_pkg: bool) -> str:
    parts = module.split(".")
    base = parts if is_pkg else parts[:-1]
    base = base[: len(base) - (level - 1)] if level > 1 else base
    return ".".join([*base, target] if target else base)


class _Collector(ast.NodeVisitor):
    def __init__(self, module: str, rel: str, is_pkg: bool) -> None:
        self.module, self.rel, self.is_pkg = module, rel, is_pkg
        self.funcs: dict[str, _Func] = {}
        self.aliases: dict[str, str] = {}      # local name -> dotted target
        self.stack: list[str] = []
        self.class_stack: list[str] = []
        self.raw_calls: list[tuple[str, str, ast.Call]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for a in node.names:
            if a.asname:
                self.aliases[a.asname] = a.name
            else:
                top = a.name.split(".")[0]
                self.aliases.setdefault(top, top)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = (_resolve_relative(self.module, node.level, node.module, self.is_pkg)
                if node.level else (node.module or ""))
        for a in node.names:
            if a.name != "*":
                self.aliases[a.asname or a.name] = f"{base}.{a.name}" if base else a.name
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.stack.append(node.name)
        self.class_stack.append(".".join(self.stack))
        self.generic_visit(node)
        self.class_stack.pop()
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        qual = ".".join(self.stack)
        self.funcs[qual] = _Func(self.module, qual, self.rel)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if self.stack and ".".join(self.stack) in self.funcs:
            qual = ".".join(self.stack)
            kind = sink_kind(node)
            if kind:
                self.funcs[qual].sinks.append(kind)
            self.raw_calls.append((qual, self.class_stack[-1] if self.class_stack else "",
                                   node))
        self.generic_visit(node)


def _candidates(collector: _Collector, owner_class: str, call: ast.Call) -> list[str]:
    """Dotted names the call may target, most specific first."""
    name = _name(call.func)
    if not name:
        return []
    head, _, rest = name.partition(".")
    out: list[str] = []
    if head in ("self", "cls") and rest and owner_class:
        out.append(f"{collector.module}:{owner_class}.{rest}")
    if head in collector.aliases:
        target = collector.aliases[head] + (f".{rest}" if rest else "")
        out.append(target)
    out.append(f"{collector.module}:{name}")
    return out


def scan_tree(backend: Path) -> list[Hit]:
    """All hits on the production surface under *backend*, sorted by key."""
    files: list[Path] = [backend / t for t in TOP_LEVEL if (backend / t).is_file()]
    for root in ROOTS:
        base = backend / root
        if base.exists():
            files += [p for p in base.rglob("*.py")
                      if not (_SKIP_PARTS & set(p.relative_to(backend).parts[:-1]))
                      and not p.name.startswith("test_")
                      and not p.name.endswith("_test.py")]
    collectors: list[_Collector] = []
    marker_modules: set[str] = set()
    for path in files:
        rel = path.relative_to(backend).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError):
            continue
        c = _Collector(_module_name(rel), rel, path.name == "__init__.py")
        c.visit(tree)
        collectors.append(c)
        if MARKER.search(source):
            marker_modules.add(c.module)

    funcs: dict[str, _Func] = {}
    for c in collectors:
        for qual, f in c.funcs.items():
            funcs[f"{c.module}:{qual}"] = f
    modules = {c.module for c in collectors}

    def lookup(dotted: str) -> str | None:
        if ":" in dotted:
            return dotted if dotted in funcs else None
        parts = dotted.split(".")
        for i in range(len(parts) - 1, 0, -1):
            mod, qual = ".".join(parts[:i]), ".".join(parts[i:])
            if mod in modules:
                key = f"{mod}:{qual}"
                return key if key in funcs else None
        return None

    for c in collectors:
        for qual, owner, call in c.raw_calls:
            for cand in _candidates(c, owner, call):
                key = lookup(cand)
                if key and key != f"{c.module}:{qual}":
                    funcs[f"{c.module}:{qual}"].calls.append(tuple(key.split(":", 1)))
                    break

    # Direct sinks everywhere; then helper writers: a function in a module that does
    # NOT name a governed location but writes (directly or through another such
    # helper). A writer inside a marker module is already a hit itself, so the chain
    # stops there: its callers are not hits on its account.
    via: dict[str, str] = {}
    for key, f in funcs.items():
        if f.sinks and f.rel not in EXEMPT:
            via[key] = sorted(set(f.sinks))[0]
    helper = {k for k in via if funcs[k].module not in marker_modules}
    changed = True
    while changed:
        changed = False
        for key, f in funcs.items():
            if key in helper or f.rel in EXEMPT or f.module in marker_modules:
                continue
            if any(f"{m}:{q}" in helper for m, q in f.calls):
                helper.add(key)
                via.setdefault(key, "helper")
                changed = True
    for key, f in funcs.items():
        if key in via or f.rel in EXEMPT or f.module not in marker_modules:
            continue
        target = next((f"{m}:{q}" for m, q in f.calls if f"{m}:{q}" in helper), None)
        if target:
            via[key] = f"via {target}"

    hits = [Hit(f"{f.rel}:{f.qual}", via[key]) for key, f in funcs.items()
            if key in via and f.module in marker_modules and f.rel not in EXEMPT]
    return sorted(hits, key=lambda h: h.key)
