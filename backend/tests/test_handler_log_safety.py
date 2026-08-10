"""Gate: a log line added to an except handler must not itself blow up.

WHY THIS EXISTS
The silent-except baseline gate (test_silent_except_baseline.py) pushes every broad
handler toward logging its exception. That pressure creates a specific, nasty failure
mode: the log call references a name that is not actually usable at handler-fire time.

Both variants are invisible to everything else in the pipeline:
  * ``ast.parse`` accepts them (they are syntactically perfect),
  * import smoke tests never execute them (handler bodies only run on failure),
  * unit tests rarely force the failure path,
so the first execution is a real production incident — where the handler raises
NameError/UnboundLocalError *on top of* the original exception, replacing a silent
degrade with a hard crash. That is strictly worse than the silence the log replaced,
and it is the one way this whole cleanup could do net harm.

TWO CHECKS
1. NameError — a name loaded in the log call that is bound nowhere in scope
   (a typo, or a parameter of a different function).
2. UnboundLocalError — a name whose only binding is INSIDE the corresponding ``try``
   body. Static scope analysis says "bound in this function", but if the exception
   fires before that line runs, the name does not exist yet:

       try:
           conn = connect()        # <-- raises here
           rows = conn.fetch()     # rows never bound
       except Exception as exc:
           logger.warning("failed after %s rows: %s", rows, exc)   # UnboundLocalError

BOTH ARE HARD ASSERTIONS, NO BASELINE. Measured at introduction: 0 and 0 across the
whole backend, so zero is a floor to hold rather than debt to work down.

REFINEMENTS THAT MATTER (each was a false positive first, and a gate that cries wolf
gets ignored, so they are load-bearing):
  * A log call is attributed to its INNERMOST enclosing handler. Handlers nest — a
    function defined inside a module-level import-fallback ``except`` block has its
    own handlers — and attributing a call to every ancestor evaluates it against
    scopes it does not live in.
  * A name the HANDLER ITSELF assigns before use is safe, even if the try body also
    assigned it (``except Exception as e: error_msg = f"...{e}"; logger.error(error_msg)``
    is correct, and initialization_manager.reset_to_defaults does exactly that).
  * Assignments textually BEFORE the try, and function parameters, are safe.
"""

import ast
import builtins
import pathlib

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]

# Wider than the silent-swallow baseline's SCOPE_DIRS on purpose: that baseline is
# scoped to where the debt was inventoried, but a crashing log line is a bug anywhere.
SCOPE_DIRS = ("routers", "core", "channels", "jobs", "hooks", "utils", "database",
              "middleware", "services")

_LOG_METHODS = frozenset({
    "error", "warning", "exception", "warn", "critical", "info", "debug",
})
_BUILTINS = frozenset(dir(builtins))


def _iter_files():
    for sub in SCOPE_DIRS:
        base = BACKEND_DIR / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            posix = path.as_posix()
            if "__pycache__" in path.parts or "/tests/" in posix:
                continue
            if path.name.startswith("test_"):
                continue
            yield path


def _parse(path):
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError, UnicodeError):
        return None


def _parent_map(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _stored_names(nodes) -> set[str]:
    """Names bound by these nodes (assignment / import / walrus / for / with-as)."""
    out: set[str] = set()
    for node in nodes:
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                out.add(n.id)
            elif isinstance(n, (ast.Import, ast.ImportFrom)):
                for alias in n.names:
                    out.add((alias.asname or alias.name).split(".")[0])
    return out


def _scope_bindings(fn) -> set[str]:
    """Every name a function scope binds: params, assignments, nested defs, imports."""
    out: set[str] = set()
    args = getattr(fn, "args", None)
    if args is not None:
        for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
            out.add(arg.arg)
        if args.vararg:
            out.add(args.vararg.arg)
        if args.kwarg:
            out.add(args.kwarg.arg)
    for n in ast.walk(fn):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            out.add(n.id)
        elif isinstance(n, ast.arg):
            out.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for alias in n.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            out.add(n.name)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            out.update(n.names)
    return out


def _module_bindings(tree) -> set[str]:
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Try, ast.If)):
            out |= _stored_names([node])
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
    return out


def _log_calls(node):
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr in _LOG_METHODS):
            yield call


def _innermost_handler(call, parents):
    """The handler a log call actually LIVES in (not every ancestor handler)."""
    cur = call
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.ExceptHandler):
            return cur
        if isinstance(cur, ast.Module):
            return None
    return None


def _loaded_names(call):
    for n in ast.walk(call):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
            yield n


def find_nameerror_risks(tree, rel: str) -> list[str]:
    """Log calls in handlers referencing a name bound nowhere in scope."""
    parents = _parent_map(tree)
    module_names = _module_bindings(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        calls = [c for c in _log_calls(node)
                 if _innermost_handler(c, parents) is node]
        if not calls:
            continue
        scope = set(module_names) | _BUILTINS
        cur = node
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope |= _scope_bindings(cur)
            if isinstance(cur, ast.Module):
                break
        if node.name:
            scope.add(node.name)
        for call in calls:
            for name in _loaded_names(call):
                if name.id not in scope:
                    out.append(f"{rel}:{name.lineno} {name.id!r} is not bound in scope")
    return out


def find_unbound_risks(tree, rel: str) -> list[str]:
    """Log calls referencing a name whose only binding is inside the try body."""
    parents = _parent_map(tree)
    module_names = _module_bindings(tree)
    out: list[str] = []
    for tryn in ast.walk(tree):
        if not isinstance(tryn, ast.Try):
            continue
        try_stores = _stored_names(tryn.body)
        if not try_stores:
            continue

        # Safe: module globals, builtins, enclosing params, and anything assigned
        # textually BEFORE the try (it has already run when the handler fires).
        safe = set(module_names) | _BUILTINS
        cur = tryn
        while cur in parents:
            cur = parents[cur]
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = cur.args
                for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
                    safe.add(arg.arg)
                if args.vararg:
                    safe.add(args.vararg.arg)
                if args.kwarg:
                    safe.add(args.kwarg.arg)
                safe |= _stored_names(
                    [s for s in cur.body if getattr(s, "lineno", 0) < tryn.lineno])
            if isinstance(cur, ast.Module):
                break

        for handler in tryn.handlers:
            # A name the handler itself assigns before logging is safe.
            handler_safe = safe | _stored_names(handler.body)
            if handler.name:
                handler_safe.add(handler.name)
            for call in _log_calls(handler):
                if _innermost_handler(call, parents) is not handler:
                    continue
                for name in _loaded_names(call):
                    if name.id in try_stores and name.id not in handler_safe:
                        out.append(
                            f"{rel}:{name.lineno} {name.id!r} is assigned only inside "
                            f"the try body (line {tryn.lineno}) — may be unbound when "
                            f"the handler fires")
    return out


def _scan(finder) -> list[str]:
    findings: list[str] = []
    for path in _iter_files():
        tree = _parse(path)
        if tree is None:
            continue
        findings += finder(tree, path.relative_to(BACKEND_DIR).as_posix())
    return findings


def test_no_nameerror_in_handler_logs():
    found = _scan(find_nameerror_risks)
    assert found == [], (
        f"{len(found)} log call(s) inside an except handler reference an unbound name. "
        f"This raises NameError when the handler fires — turning a silent degrade into "
        f"a crash, and only in production, because handler bodies never run during "
        f"import or a passing test:\n  " + "\n  ".join(sorted(set(found)))
    )


def test_no_unboundlocal_in_handler_logs():
    found = _scan(find_unbound_risks)
    assert found == [], (
        f"{len(found)} log call(s) reference a variable that may not be bound yet when "
        f"the handler fires (it is assigned inside the try, possibly after the line that "
        f"raised). Log something already available — the exception, a parameter, or a "
        f"value assigned before the try:\n  " + "\n  ".join(sorted(set(found)))
    )


# ---------------------------------------------------------------------------
# Non-vacuity. Both gates above assert an empty list, which a detector that never
# fires also satisfies — the exact trap that made two CI-wiring assertions in this
# codebase pass while checking nothing. These pin that each detector fires on the bug
# it targets AND stays quiet on the correct forms, so the gates cannot be silently
# neutered by a refactor.
# ---------------------------------------------------------------------------
def _findings(src: str, finder) -> list[str]:
    return finder(ast.parse(src), "<src>")


def test_nameerror_detector_fires_and_is_specific():
    caught = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def f(path):\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        logger.warning('%s %s', pth, exc)\n"   # typo: pth
    )
    assert len(_findings(caught, find_nameerror_risks)) == 1

    ok = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "MODULE_CONST = 1\n"
        "def f(path, *args, **kw):\n"
        "    local = 2\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        logger.warning('%s %s %s %s %s', path, local, MODULE_CONST, len(args), exc)\n"
    )
    assert _findings(ok, find_nameerror_risks) == [], (
        "detector false-positived on params / locals / module consts / builtins / "
        "the `as` name"
    )


def test_nameerror_detector_attributes_to_innermost_handler():
    """A function defined inside an import-fallback handler has its own handlers.

    Its log call must be judged against ITS scope, not the outer handler's, or every
    such call reports phantom unbound names (ddd_packager._read_domain_skills is the
    real instance that exposed this).
    """
    nested = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    from somewhere import thing\n"
        "except Exception:\n"
        "    def fallback(aim_path):\n"
        "        try:\n"
        "            load(aim_path)\n"
        "        except Exception as exc:\n"
        "            logger.warning('%s %s', aim_path, exc)\n"
        "            return []\n"
    )
    assert _findings(nested, find_nameerror_risks) == [], (
        "log call was judged against an ANCESTOR handler's scope, so `aim_path` and "
        "`exc` looked unbound despite being a parameter and the handler's own `as` name"
    )


def test_unbound_detector_fires_and_is_specific():
    caught = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def f():\n"
        "    try:\n"
        "        conn = connect()\n"
        "        rows = conn.fetch()\n"
        "    except Exception as exc:\n"
        "        logger.warning('after %s rows: %s', rows, exc)\n"
    )
    assert len(_findings(caught, find_unbound_risks)) == 1

    # Correct forms that must NOT be flagged.
    before_try = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def f():\n"
        "    label = 'x'\n"
        "    try:\n"
        "        label = compute()\n"
        "    except Exception as exc:\n"
        "        logger.warning('%s: %s', label, exc)\n"
    )
    assert _findings(before_try, find_unbound_risks) == [], (
        "flagged a name that was also assigned BEFORE the try, so it is always bound"
    )

    rebound_in_handler = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def f():\n"
        "    try:\n"
        "        msg = build()\n"
        "    except Exception as exc:\n"
        "        msg = f'failed: {exc}'\n"
        "        logger.error(msg)\n"
    )
    assert _findings(rebound_in_handler, find_unbound_risks) == [], (
        "flagged a name the handler REASSIGNS before logging — the try-body binding is "
        "irrelevant there (initialization_manager.reset_to_defaults does exactly this)"
    )
