"""What a change does to a symbol's interface, and which call sites it breaks.

The callers report (``core.code_intel.changed``) used to say only *who* calls a
changed function. A reviewer then had to open every caller to find the one that
breaks. This module reads the base and the new version of each changed file and
answers the question the reviewer has:

- ``interfaces(text, language)``: each function's parameters (name, kind, default),
  each class's fields, methods and constructor parameters, and a hash of each
  symbol's code tokens without comments or a Python docstring.
- ``delta(old, new)``: removed; removed parameters; parameters that became required;
  ``*args`` / ``**kwargs`` dropped; fields removed; changed only in comments.
- ``call_sites(text, language, line, name)`` + ``breaks(delta, new, site)``: at a
  call the graph found, the arguments passed, and whether the change breaks them —
  a keyword naming a removed parameter, more positionals than the new signature
  takes, a new required parameter not passed. A call with a ``*`` / ``**`` splat or a
  spread is never judged, and nor is a method called through its class
  (``Cls.m(obj, …)``); only what the change itself removed or required is reported,
  so a call that was already wrong is not blamed on it.

Python, TypeScript and JavaScript (tree-sitter, the parser's grammars). For any other
language ``interfaces`` returns nothing and the report falls back to which lines a
change touched. Nothing is executed; a file that does not parse gives no interface.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

LANGS = frozenset({"python", "typescript", "javascript"})

_COMMENT_TYPES = frozenset({"comment", "line_comment", "block_comment", "html_comment"})
_PY_FUNC = "function_definition"
_PY_CLASS = "class_definition"
_TS_FUNCS = frozenset({"function_declaration", "method_definition", "generator_function_declaration"})
_TS_CLASSES = frozenset({"class_declaration", "class", "abstract_class_declaration"})


@dataclass
class Param:
    name: str
    kind: str            # pos | kw (keyword-only) | varpos (*args, ...rest) | varkw (**kw)
    default: bool        # has a default (or TS optional ``x?``)


@dataclass
class Iface:
    kind: str                                  # function | method | class
    line: int
    params: list[Param] | None = None          # functions, methods; a class: its constructor
    fields: set[str] = field(default_factory=set)
    methods: set[str] = field(default_factory=set)
    static: bool = False                       # staticmethod / classmethod / TS static
    code_hash: str = ""
    bases: list[str] = field(default_factory=list)   # a class's base names (as written)


@dataclass
class Delta:
    removed: bool = False
    removed_params: list[str] = field(default_factory=list)
    now_required: list[str] = field(default_factory=list)
    dropped_varpos: bool = False
    dropped_varkw: bool = False
    fewer_positionals: int | None = None       # the new positional capacity, when smaller
    removed_fields: list[str] = field(default_factory=list)
    comments_only: bool = False
    changed: bool = False                      # any code token changed
    signature: bool = False                    # the parameter list changed at all

    @property
    def interface(self) -> bool:
        """The interface a caller sees changed in a way that can break it."""
        return bool(self.removed or self.removed_params or self.now_required
                    or self.dropped_varpos or self.dropped_varkw
                    or self.fewer_positionals is not None or self.removed_fields)

    def describe(self) -> str:
        parts = []
        if self.removed:
            return "removed"
        params = [p for p in self.removed_params if p not in self.removed_fields]
        if params:
            parts.append("parameter " + ", ".join(f"`{p}`" for p in params) + " removed")
        if self.now_required:
            parts.append(", ".join(f"`{p}`" for p in self.now_required) + " now required")
        if self.dropped_varpos:
            parts.append("no longer takes extra positional arguments")
        if self.dropped_varkw:
            parts.append("no longer takes extra keyword arguments")
        if self.fewer_positionals is not None and not params and not self.removed_fields:
            parts.append(f"takes at most {self.fewer_positionals} positional arguments")
        if self.removed_fields:
            parts.append("field " + ", ".join(f"`{f}`" for f in self.removed_fields) + " removed")
        if not parts and self.signature:
            parts.append("signature changed; existing calls still fit it")
        return "; ".join(parts)


# ── reading one file ───────────────────────────────────────────────────────────

def _parse(text: str, language: str, suffix: str = ""):
    from .parser import _get_cached_parser, _tree_sitter_live, grammar_for
    grammar = grammar_for(suffix) if suffix else language
    if grammar is None or not _tree_sitter_live(grammar):
        return None
    parser = _get_cached_parser(grammar)
    if parser is None:
        return None
    try:
        return parser.parse(text.encode("utf-8"))
    except Exception:  # noqa: BLE001 -- an unparseable file has no interface
        return None


def _t(node) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


def _code_hash(node, language: str) -> str:
    """A hash of *node*'s code tokens: comments and a Python docstring left out."""
    skip: set[int] = set()
    if language == "python":
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in (_PY_FUNC, _PY_CLASS):
                body = n.child_by_field_name("body")
                first = body.named_children[0] if body is not None and body.named_children else None
                if first is not None and (first.type == "string" or (
                        first.type == "expression_statement" and first.named_child_count == 1
                        and first.named_children[0].type == "string")):
                    skip.add(first.start_byte)       # the docstring (bare or wrapped)
            stack.extend(n.children)
    h = hashlib.sha1()
    stack = [node]
    while stack:
        n = stack.pop()
        if n.type in _COMMENT_TYPES or (n.start_byte in skip and n.type in ("expression_statement",
                                                                           "string")):
            continue
        if n.child_count == 0:
            h.update(n.text or b"")
            h.update(b"\x00")
        else:
            stack.extend(reversed(n.children))
    return h.hexdigest()


def _py_params(params_node, *, drop_first: bool) -> list[Param]:
    out: list[Param] = []
    kw_only = False
    for p in (params_node.children if params_node is not None else []):
        t = p.type
        if t in ("(", ")", ","):
            continue
        if t == "keyword_separator" or _t(p) == "*":
            kw_only = True
            continue
        if t == "positional_separator" or _t(p) == "/":
            continue
        if t == "list_splat_pattern":
            out.append(Param(_t(p).lstrip("*"), "varpos", True))
            kw_only = True
            continue
        if t == "dictionary_splat_pattern":
            out.append(Param(_t(p).lstrip("*"), "varkw", True))
            continue
        if t == "identifier":
            name, default = _t(p), False
        elif t == "typed_parameter":
            ident = next((c for c in p.children if c.type in ("identifier", "list_splat_pattern",
                                                               "dictionary_splat_pattern")), None)
            if ident is not None and ident.type == "list_splat_pattern":
                out.append(Param(_t(ident).lstrip("*"), "varpos", True))
                kw_only = True
                continue
            if ident is not None and ident.type == "dictionary_splat_pattern":
                out.append(Param(_t(ident).lstrip("*"), "varkw", True))
                continue
            name, default = _t(ident), False
        elif t in ("default_parameter", "typed_default_parameter"):
            name, default = _t(p.child_by_field_name("name")), True
        else:
            continue
        out.append(Param(name, "kw" if kw_only else "pos", default))
    if drop_first and out and out[0].kind == "pos":
        out = out[1:]
    return out


def _ts_params(params_node) -> list[Param]:
    out: list[Param] = []
    for p in (params_node.children if params_node is not None else []):
        if p.type not in ("required_parameter", "optional_parameter", "identifier",
                          "assignment_pattern", "rest_pattern"):
            continue
        pat = p.child_by_field_name("pattern") or p
        if pat.type == "rest_pattern" or p.type == "rest_pattern":
            out.append(Param(_t(pat).lstrip("."), "varpos", True))
            continue
        name = _t(pat) if pat.type == "identifier" else ""
        default = p.type == "optional_parameter" or p.child_by_field_name("value") is not None \
            or p.type == "assignment_pattern"
        out.append(Param(name, "pos", default))
    return out


def _decorators(node) -> list[str]:
    parent = node.parent
    if parent is None or parent.type != "decorated_definition":
        return []
    return [_t(c).lstrip("@").strip() for c in parent.children if c.type == "decorator"]


def interfaces(text: str, language: str, suffix: str = "") -> dict[str, Iface]:
    """``{"f": …, "Cls": …, "Cls.m": …}`` for *text*; {} for another language or a
    file that does not parse."""
    if language not in LANGS:
        return {}
    tree = _parse(text, language, suffix)
    if tree is None:
        return {}
    out: dict[str, Iface] = {}
    if language == "python":
        _py_scan(tree.root_node, None, out)
    else:
        _ts_scan(tree.root_node, None, out)
    return out


_PY_COMPOUND = frozenset({"if_statement", "elif_clause", "else_clause", "try_statement",
                          "except_clause", "finally_clause", "with_statement", "block"})


def _deco_names(item) -> list[str]:
    return [d.split("(")[0].split(".")[-1] for d in _decorators(item)]


def _py_scan(node, cls: str | None, out: dict[str, Iface]) -> None:
    """Functions, classes and methods under *node*: at module level also inside
    ``if`` / ``try`` / ``with`` (a definition there binds at module level). The last
    definition of a name is the one that binds (so ``@overload`` stubs, which come
    before the implementation, are replaced by it)."""
    for child in node.children:
        if cls is None and child.type in _PY_COMPOUND:
            _py_scan(child, None, out)
            continue
        item = child.child_by_field_name("definition") if child.type == "decorated_definition" else child
        if item is None:
            continue
        if item.type == _PY_FUNC:
            name = _t(item.child_by_field_name("name"))
            decos = _deco_names(item)
            static = "staticmethod" in decos
            classm = "classmethod" in decos
            key = f"{cls}.{name}" if cls else name
            out[key] = Iface(
                kind="method" if cls else "function", line=item.start_point[0] + 1,
                params=_py_params(item.child_by_field_name("parameters"),
                                  drop_first=bool(cls) and not static),
                static=static or classm, code_hash=_code_hash(item, "python"))
        elif item.type == _PY_CLASS:
            name = _t(item.child_by_field_name("name"))
            supers = item.child_by_field_name("superclasses")
            iface = Iface(kind="class", line=item.start_point[0] + 1,
                          code_hash=_code_hash(item, "python"),
                          bases=[_t(c).split(".")[-1] for c in (supers.named_children if supers else [])
                                 if c.type in ("identifier", "attribute")])
            decos = _decorators(item)
            dataclass = any(d.split("(")[0].split(".")[-1] == "dataclass" for d in decos)
            kw_only = any("kw_only=True" in d.replace(" ", "") for d in decos)
            body = item.child_by_field_name("body")
            dc_params: list[Param] = []
            for stmt in (body.children if body is not None else []):
                inner = stmt.named_children[0] if stmt.type == "expression_statement" and \
                    stmt.named_child_count == 1 else stmt
                if inner.type == "assignment":
                    left = inner.child_by_field_name("left")
                    ann = inner.child_by_field_name("type")
                    if left is not None and left.type == "identifier":
                        if ann is not None and _t(ann).split(".")[-1] == "KW_ONLY":
                            kw_only = True             # `_: KW_ONLY`: the rest are keyword-only
                            continue
                        iface.fields.add(_t(left))
                        if dataclass and ann is not None and "ClassVar" not in _t(ann):
                            dc_params.append(Param(_t(left), "kw" if kw_only else "pos",
                                                   inner.child_by_field_name("right") is not None))
                deco = stmt.child_by_field_name("definition") if stmt.type == "decorated_definition" else stmt
                if deco is not None and deco.type == _PY_FUNC:
                    mname = _t(deco.child_by_field_name("name"))
                    iface.methods.add(mname)
                    _self_attrs(deco, iface.fields)
                    if mname == "__init__":
                        iface.params = _py_params(deco.child_by_field_name("parameters"),
                                                  drop_first=True)
            if iface.params is None and dataclass and not iface.bases:
                # a dataclass with bases also takes its bases' fields: not judged
                iface.params = dc_params
            out[name] = iface
            if body is not None:
                _py_scan(body, name, out)


def bound_names(text: str, language: str, suffix: str = "") -> set[str]:
    """Every name the module binds at top level: definitions (inside ``if`` / ``try``
    too), imports (``from b import f`` keeps ``f`` importable from here) and
    assignments. A symbol gone from its definitions but still bound here was moved,
    not removed, for a caller that imports it from this module."""
    if language not in LANGS:
        return set()
    tree = _parse(text, language, suffix)
    if tree is None:
        return set()
    out: set[str] = set()

    def last(node) -> str:
        return _t(node).split(".")[-1].strip()

    stack = list(tree.root_node.children)
    while stack:
        n = stack.pop()
        t = n.type
        if t in _PY_COMPOUND or t in ("export_statement", "expression_statement",
                                      "lexical_declaration", "variable_declaration",
                                      "import_clause", "named_imports", "export_clause"):
            stack.extend(n.children)
            continue
        if t == "decorated_definition":
            n = n.child_by_field_name("definition") or n
            t = n.type
        if t in (_PY_FUNC, _PY_CLASS) or t in _TS_FUNCS or t in _TS_CLASSES:
            out.add(_t(n.child_by_field_name("name")))
        elif t == "import_from_statement":               # from m import a, b as c
            module = n.child_by_field_name("module_name")
            for c in n.named_children:
                if module is not None and c.start_byte == module.start_byte:
                    continue
                if c.type == "aliased_import":
                    out.add(_t(c.child_by_field_name("alias")))
                elif c.type == "dotted_name":
                    out.add(last(c))
        elif t == "import_statement" and language == "python":   # import a.b [as c]
            for c in n.named_children:
                if c.type == "aliased_import":
                    out.add(_t(c.child_by_field_name("alias")))
                elif c.type == "dotted_name":
                    out.add(_t(c).split(".")[0])
        elif t == "import_statement":                    # TS/JS import … from
            stack.extend(n.children)
        elif t in ("import_specifier", "export_specifier"):
            out.add(_t(n.child_by_field_name("alias") or n.child_by_field_name("name")))
        elif t == "namespace_import":
            out.update(_t(c) for c in n.named_children if c.type == "identifier")
        elif t == "identifier" and n.parent is not None and n.parent.type == "import_clause":
            out.add(_t(n))                               # import X from …
        elif t in ("assignment", "variable_declarator"):
            left = n.child_by_field_name("left") or n.child_by_field_name("name")
            if left is not None and left.type == "identifier":
                out.add(_t(left))
    out.discard("")
    return out


def _self_attrs(method, fields: set[str]) -> None:
    params = method.child_by_field_name("parameters")
    first = next((c for c in (params.children if params is not None else [])
                  if c.type == "identifier"), None)
    me = _t(first) if first is not None else "self"
    stack = [method.child_by_field_name("body")]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type in (_PY_FUNC, _PY_CLASS, "lambda"):
            continue
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            targets = [left] if left is not None and left.type == "attribute" else (
                [c for c in left.named_children if c.type == "attribute"]
                if left is not None and left.type in ("pattern_list", "tuple_pattern",
                                                       "list_pattern") else [])
            for t in targets:                    # self.a = … / self.a, self.b = …
                obj = t.child_by_field_name("object")
                if obj is not None and _t(obj) == me:
                    fields.add(_t(t.child_by_field_name("attribute")))
        stack.extend(n.children)


def _ts_scan(node, cls: str | None, out: dict[str, Iface]) -> None:
    for child in node.children:
        item = child
        if item.type in ("export_statement",):
            decl = item.child_by_field_name("declaration")
            if decl is None:
                continue
            item = decl
        if item.type in _TS_FUNCS:
            name = _t(item.child_by_field_name("name"))
            if not name:
                continue
            static = any(_t(c) == "static" for c in item.children)
            key = f"{cls}.{name}" if cls and item.type == "method_definition" else name
            # an overload signature has no body: the implementation is the one that binds
            if item.child_by_field_name("body") is None and key in out:
                continue
            out[key] = Iface(kind="method" if cls else "function",
                             line=item.start_point[0] + 1,
                             params=_ts_params(item.child_by_field_name("parameters")),
                             static=static, code_hash=_code_hash(item, "typescript"))
        elif item.type in ("lexical_declaration", "variable_declaration") and cls is None:
            for d in item.named_children:
                if d.type != "variable_declarator":
                    continue
                value = d.child_by_field_name("value")
                name = _t(d.child_by_field_name("name"))
                if value is not None and value.type in ("arrow_function", "function_expression", "function"):
                    params = value.child_by_field_name("parameters")
                    if params is None and value.child_by_field_name("parameter") is not None:
                        params_list = [Param(_t(value.child_by_field_name("parameter")), "pos", False)]
                    else:
                        params_list = _ts_params(params)
                    out.setdefault(name, Iface(kind="function", line=d.start_point[0] + 1,
                                               params=params_list,
                                               code_hash=_code_hash(d, "typescript")))
        elif item.type in _TS_CLASSES:
            name = _t(item.child_by_field_name("name"))
            if not name:
                continue
            heritage = next((c for c in item.children if c.type == "class_heritage"), None)
            iface = Iface(kind="class", line=item.start_point[0] + 1,
                          code_hash=_code_hash(item, "typescript"),
                          bases=[_t(c) for c in _ts_heritage(heritage)])
            body = item.child_by_field_name("body")
            for m in (body.children if body is not None else []):
                if m.type in ("public_field_definition", "field_definition"):
                    iface.fields.add(_t(m.child_by_field_name("name") or m.child_by_field_name("property")))
                elif m.type == "method_definition":
                    mname = _t(m.child_by_field_name("name"))
                    iface.methods.add(mname)
                    params = m.child_by_field_name("parameters")
                    if mname == "constructor":
                        iface.params = _ts_params(params)
                        for p in (params.children if params is not None else []):
                            if any(c.type == "accessibility_modifier" for c in p.children):
                                pat = p.child_by_field_name("pattern")
                                if pat is not None:
                                    iface.fields.add(_t(pat))
                        _this_attrs(m, iface.fields)
            out.setdefault(name, iface)
            if body is not None:
                _ts_scan(body, name, out)


def _ts_heritage(node) -> list:
    out, stack = [], [node] if node is not None else []
    while stack:
        n = stack.pop()
        if n.type in ("identifier", "type_identifier") and n.parent is not None and \
                n.parent.type in ("extends_clause", "class_heritage"):
            out.append(n)
        stack.extend(n.children)
    return out


def _this_attrs(method, fields: set[str]) -> None:
    stack = [method.child_by_field_name("body")]
    while stack:
        n = stack.pop()
        if n is None:
            continue
        if n.type in ("function_expression", "function", "class", "class_declaration"):
            continue
        if n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "member_expression" and \
                    (left.child_by_field_name("object") or left).type == "this":
                fields.add(_t(left.child_by_field_name("property")))
        stack.extend(n.children)


# ── comparing two versions ─────────────────────────────────────────────────────

def _positional_capacity(params: list[Param]) -> int | None:
    """How many positionals a call may pass; None when unbounded (``*args``)."""
    if any(p.kind == "varpos" for p in params):
        return None
    return sum(1 for p in params if p.kind == "pos")


def delta(old: Iface | None, new: Iface | None) -> Delta:
    d = Delta()
    if old is None:
        return d                                   # new: nothing outside can call it yet
    if new is None:
        d.removed = d.changed = True
        return d
    d.changed = old.code_hash != new.code_hash
    if old.params is not None and new.params is not None:
        d.signature = [(p.name, p.kind, p.default) for p in old.params] != \
            [(p.name, p.kind, p.default) for p in new.params]
        new_names = {p.name: p for p in new.params}
        old_names = {p.name: p for p in old.params}
        new_varkw = any(p.kind == "varkw" for p in new.params)
        for p in old.params:
            if p.kind in ("varpos", "varkw") or not p.name:
                continue
            if p.name not in new_names and not new_varkw:
                d.removed_params.append(p.name)
        for p in new.params:
            if p.kind in ("varpos", "varkw") or not p.name or p.default:
                continue
            was = old_names.get(p.name)
            if was is None or was.default:
                d.now_required.append(p.name)
        d.dropped_varpos = any(p.kind == "varpos" for p in old.params) and \
            not any(p.kind == "varpos" for p in new.params)
        d.dropped_varkw = any(p.kind == "varkw" for p in old.params) and not new_varkw
        before, after = _positional_capacity(old.params), _positional_capacity(new.params)
        if after is not None and (before is None or after < before):
            d.fewer_positionals = after
    if old.kind == "class" and new.kind == "class":
        d.removed_fields = sorted(f for f in old.fields - new.fields
                                  if f and f not in new.methods)
    d.comments_only = not d.changed and not d.interface
    return d


# ── a call site ────────────────────────────────────────────────────────────────

@dataclass
class Site:
    line: int
    positional: int
    keywords: list[str]
    splat: bool                 # *args / **kw / ...spread: not judged
    through_class: bool         # Cls.m(...) on the class itself


_CALLS = frozenset({"call", "call_expression", "new_expression"})


def call_sites(text: str, language: str, line: int, name: str, *, suffix: str = "",
               class_name: str | None = None, tree=None) -> list[Site]:
    """The calls of *name* on *line* of *text* (the graph's call-site line)."""
    if language not in LANGS:
        return []
    tree = tree or _parse(text, language, suffix)
    if tree is None:
        return []
    out: list[Site] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.start_point[0] + 1 > line or n.end_point[0] + 1 < line:
            continue
        if n.type in _CALLS and n.start_point[0] + 1 == line:
            fn = n.child_by_field_name("function") or n.child_by_field_name("constructor")
            callee = fn
            receiver = None
            if fn is not None and fn.type in ("attribute", "member_expression"):
                receiver = fn.child_by_field_name("object")
                callee = fn.child_by_field_name("attribute") or fn.child_by_field_name("property")
            if callee is not None and _t(callee) == name:
                args = n.child_by_field_name("arguments")
                pos, kws, splat = 0, [], False
                if args is not None and args.type == "generator_expression":
                    pos = 1                        # f(x for x in y): one argument
                elif args is not None and args.type not in ("argument_list", "arguments"):
                    splat = True                   # a tagged template, …: not judged
                for a in (args.named_children if args is not None and
                          args.type in ("argument_list", "arguments") else []):
                    if a.type == "keyword_argument":
                        kws.append(_t(a.child_by_field_name("name")))
                    elif a.type in ("list_splat", "dictionary_splat", "spread_element"):
                        splat = True
                    elif a.type not in _COMMENT_TYPES:
                        pos += 1
                # Cls.m(obj, …) through any class (the owner, a subclass, an alias):
                # self is passed by hand, so the count is off by one
                through = receiver is not None and receiver.type == "identifier" and (
                    (class_name is not None and _t(receiver) == class_name)
                    or _t(receiver)[:1].isupper())
                out.append(Site(line=line, positional=pos, keywords=kws, splat=splat,
                                through_class=through))
        stack.extend(n.children)
    return out


def breaks(d: Delta, new: Iface | None, site: Site, *, is_method: bool,
           language: str = "python") -> list[str]:
    """Why *site* breaks under *d* — only for what the change removed or required. In
    JavaScript a missing or extra argument never fails, so only a removal counts."""
    if d.removed:
        return ["calls it, and this change removes it"]
    if new is None or new.params is None or site.splat or language == "javascript":
        return []
    if is_method and site.through_class and not new.static:
        return []                                  # Cls.m(obj, …): self passed by hand
    reasons = []
    for kw in site.keywords:
        if kw in d.removed_params:
            reasons.append(f"passes `{kw}=`, a parameter this change removes")
        elif d.dropped_varkw and kw not in {p.name for p in new.params}:
            reasons.append(f"passes `{kw}=`, which it no longer accepts")
    cap = _positional_capacity(new.params)
    if cap is not None and site.positional > cap and (
            d.fewer_positionals is not None or d.dropped_varpos):
        reasons.append(f"passes {site.positional} positional arguments; it now takes at most {cap}")
    pos_params = [p for p in new.params if p.kind == "pos"]
    for i, p in enumerate(pos_params):
        if p.name in d.now_required and i >= site.positional and p.name not in site.keywords:
            reasons.append(f"does not pass `{p.name}`, which this change makes required")
    for p in new.params:
        if p.kind == "kw" and p.name in d.now_required and p.name not in site.keywords:
            reasons.append(f"does not pass `{p.name}=`, which this change makes required")
    return reasons


def reads_attribute(text: str, language: str, attr: str, *, suffix: str = "", tree=None) -> list[int]:
    """Lines of *text* that read ``.attr`` in code (not in a comment or a string)."""
    if language not in LANGS:
        return []
    tree = tree or _parse(text, language, suffix)
    if tree is None:
        return []
    lines: list[int] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in ("attribute", "member_expression"):
            a = n.child_by_field_name("attribute") or n.child_by_field_name("property")
            if a is not None and _t(a) == attr:
                lines.append(n.start_point[0] + 1)
        stack.extend(n.children)
    return sorted(set(lines))


def names_in_code(text: str, language: str, name: str, *, anchors: list[str],
                  suffix: str = "", tree=None) -> bool | None:
    """Whether *text* names *name* in code: an identifier or member token, a string
    that is exactly the name (``getattr(mod, "name")``, a registry key), or one that is
    a dotted path ending in it with an anchor (``"pkg.mod.name"``, a mock).
    None when the file cannot be read this way (another language, no parse)."""
    if language not in LANGS:
        return None
    tree = tree or _parse(text, language, suffix)
    if tree is None:
        return None
    import re
    path_re = re.compile(r"[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+")
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        if n.type in _COMMENT_TYPES:
            continue
        if n.type in ("string_content", "string_fragment"):
            s = _t(n).strip()
            if s == name:                     # getattr(mod, "name"), a registry key
                return True
            if path_re.fullmatch(s) and s.endswith("." + name) and \
                    any(a in s.split(".") for a in anchors):
                return True                   # patch("pkg.mod.name")
            continue
        if n.child_count == 0 and n.type in ("identifier", "property_identifier",
                                              "type_identifier", "field_identifier",
                                              "shorthand_property_identifier") and _t(n) == name:
            return True
        stack.extend(n.children)
    return False
