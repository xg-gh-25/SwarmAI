"""Who a call or a reference points at, when the source says it: receiver types and
value references (Python, TypeScript, JavaScript).

The call graph used to link ``x.save()`` to every method named ``save`` (it could not
see what ``x`` is), so ``conn.execute()`` counted as a caller of each ``execute`` in the
repo; and a function handed on as a value (``Thread(target=save_all)``, a registry, a
test's ``patch("pkg.mod.save_all")``) had no edge at all, so a contract change missed
those sites. This module reads what the source states, nothing more:

- **Receiver types**, per function scope (``scope_of``): ``self`` / ``this``; a
  parameter with an annotation (``s: Store``, ``s?: Store``); a local assigned from a
  constructor (``x = Store()``, ``const x = new Store()``) or annotated (``x: Store``);
  a class named directly (``Store.make()``). A call on such a receiver targets
  ``Store.save`` (``typed_target``). A name bound to two classes is dropped.
- **Value references** (``value_reference``): a function or class name, or
  ``self.method`` / ``this.method``, in a value position — an argument, a keyword
  value, the right side of an assignment, a list/tuple/dict/array element, a return
  value, a decorator. A name bound locally (a parameter, an assignment) is not a
  reference to the module's definition of the same name.
- **Patch strings** (``patch_reference``, Python): ``patch("a.b.c")``,
  ``monkeypatch.setattr("a.b.c", …)`` and ``patch.object(Cls, "m")`` name a symbol
  by its dotted path.

What it does not do: infer a type the source does not state (a return value, an
attribute's type, a container element). Such calls keep the name-only link, marked by
its confidence (``parser.NAME_ONLY_CONFIDENCE`` once resolved), so a reader can tell a
link the source states from a guess.
"""
from __future__ import annotations

import re

TYPED_LANGS = frozenset({"python", "typescript", "javascript"})

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*$")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$")
_FUNC_TYPES = frozenset({"function_definition", "function_declaration", "method_definition",
                         "arrow_function", "function_expression", "lambda", "function"})
_CLASS_TYPES = frozenset({"class_definition", "class_declaration", "class"})
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


def _type_name(type_node) -> str | None:
    """The class an annotation names: ``Store``, ``"Store"``, ``mod.Store`` (last
    part), ``Store | None`` / ``Optional[Store]`` (the one class); None otherwise."""
    if type_node is None:
        return None
    found: list[str] = []
    stack = [type_node]
    while stack:
        n = stack.pop()
        if n.type in ("array_type", "generic_type", "tuple_type", "function_type"):
            return None                       # Repo[], Promise<Repo>: not a Repo
        if n.type == "subscript":             # list[Store] is not a Store; Optional[X] is
            head = n.child_by_field_name("value")
            if _text(head).split(".")[-1] != "Optional":
                return None
            stack.extend(c for c in n.children if c is not head)
            continue
        if n.type in ("identifier", "type_identifier"):
            found.append(_text(n))
        elif n.type == "string_content" or n.type == "string_fragment":
            t = _text(n).split(".")[-1]
            if _IDENT.match(t):
                found.append(t)
        elif n.type == "attribute":           # mod.Store → Store
            attr = n.child_by_field_name("attribute")
            if attr is not None:
                found.append(_text(attr))
            continue
        stack.extend(reversed(n.children))
    names = [f for f in found if f not in ("None", "Optional", "null", "undefined", "typing")]
    return names[0] if len(set(names)) == 1 else None


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
    """What a function's own code binds: names to classes (where the source says)
    and every locally bound name; ``this_ok`` is False inside a TS/JS ``function``
    expression, whose ``this`` is not the enclosing class's."""

    def __init__(self, types: dict[str, str] | None = None, bound: set[str] | None = None,
                 this_ok: bool = True):
        self.types = dict(types or {})
        self.bound = set(bound or ())
        self.this_ok = this_ok

    def child(self) -> "Scope":
        return Scope(self.types, self.bound, self.this_ok)


# Python lambda / TS-JS function expressions: a scope of their own the walker does not
# get as a definition (they are not graph symbols).
NESTED_SCOPE_TYPES = frozenset({"lambda", "function_expression", "function"})
_PATTERN_LEAVES = ("identifier", "shorthand_property_identifier_pattern")


def scope_of(func_node, language: str, classes: set[str], outer: Scope | None) -> Scope:
    """The scope of *func_node*: its parameters and the names its body binds (not
    those of nested functions or classes), on top of *outer* (a closure sees it).

    A name has a class only when EVERY binding of it in this scope gives that same
    class (``x = Store()``, ``s: Store``); any other binding — a call whose result
    the source does not type, ``except … as x``, ``with … as x``, a loop or
    comprehension variable, a destructured name — leaves it untyped here, and so
    hides an outer scope's type for it too. Order is not used: a confident wrong
    edge is worse than a link by name."""
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
            bind(name, _type_name(p.child_by_field_name("type")))
        elif p.type in ("required_parameter", "optional_parameter"):
            pattern = p.child_by_field_name("pattern")
            ann = p.child_by_field_name("type")
            if pattern is not None and pattern.type == "identifier":
                bind(_text(pattern), _type_name(ann))
            else:
                bind_leaves(pattern)
        else:
            bind_leaves(p)                    # *args, **kw, destructured params

    body = func_node.child_by_field_name("body")
    stack = [body] if body is not None else []
    while stack:
        n = stack.pop()
        if n.type in _FUNC_TYPES or n.type in _CLASS_TYPES:
            continue                          # a nested scope binds its own names
        if n.type == "assignment":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                cls = _type_name(n.child_by_field_name("type")) or \
                    _constructed_class(n.child_by_field_name("right"), classes)
                bind(_text(left), cls)
            else:                             # a, b = …: bound, type unknown
                bind_leaves(left)
        elif n.type == "augmented_assignment":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                bind(_text(left), None)
        elif n.type == "variable_declarator":
            name = n.child_by_field_name("name")
            if name is not None and name.type == "identifier":
                cls = _type_name(n.child_by_field_name("type")) or \
                    _constructed_class(n.child_by_field_name("value"), classes)
                bind(_text(name), cls)
            else:
                bind_leaves(name)             # const { save } = deps
        elif n.type == "assignment_expression":
            left = n.child_by_field_name("left")
            if left is not None and left.type == "identifier":
                bind(_text(left), _constructed_class(n.child_by_field_name("right"), classes))
        elif n.type in ("for_statement", "for_in_statement", "for_in_clause",
                        "named_expression"):
            bind_leaves(n.child_by_field_name("left") or n.child_by_field_name("name"))
        elif n.type == "as_pattern":          # except E as x / with f() as x
            bind_leaves(n.child_by_field_name("alias"))
        stack.extend(n.children)
    for name, classes_seen in seen.items():
        if len(classes_seen) == 1 and None not in classes_seen:
            scope.types[name] = next(iter(classes_seen))
        else:
            scope.types.pop(name, None)
    return scope


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


def receiver_class(receiver: str, scope: Scope | None, classes: set[str],
                   import_map: dict, enclosing_class: str | None) -> str | None:
    """The class a single-name receiver is, when the source says it."""
    if receiver in _SELF:
        if receiver == "this" and scope is not None and not scope.this_ok:
            return None                       # a function expression's own `this`
        return enclosing_class
    if scope is not None and receiver in scope.types:
        return scope.types[receiver]
    if scope is not None and receiver in scope.bound:
        return None                           # a local of unknown type shadows a class
    if receiver in classes:
        return receiver                       # Store.make()
    if receiver in import_map and receiver[:1].isupper():
        return receiver                       # an imported class: Store.make()
    return None


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
