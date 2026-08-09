"""Structural contract test: NO blocking filesystem/subprocess I/O directly in an
``async def`` route handler in ``backend/routers/``.

WHY THIS EXISTS (the structural-closure tier for the async-blocking-I/O CLASS):
A FastAPI ``async def`` handler runs ON the event loop. A blocking primitive
(``path.read_text()``, ``dir.iterdir()``, ``shutil.rmtree()``, ``subprocess.run()``,
``flock``, ...) called DIRECTLY in that body blocks the WHOLE loop until it returns —
every other request/stream stalls behind it. run_a65f2d6c (community ④) and
run_b2d3ece0 (7-router audit) fixed such calls one endpoint at a time via
``asyncio.to_thread`` — but that was per-ENDPOINT coverage with no guard: nothing
stopped a FUTURE new ``async def`` from re-introducing the bug. This test is that
guard. A new blocking-async handler makes this test RED → fails CI (the non-DB pytest
step runs it) → the PR cannot merge green. That is what "close the class structurally"
means — the exact move run_72a39300's ESLint ``no-restricted-syntax`` rule made for the
frontend URL double-``/api``-prefix class.

THE SANCTIONED PATTERN (NOT a violation): define a plain sync ``def`` helper that does
the blocking work, then ``await asyncio.to_thread(helper)``. The blocking call's NEAREST
enclosing function is then the SYNC helper, so it is correctly NOT flagged. This is the
run_b2d3ece0 fix shape — the scanner must pass it (AC4).

DISCRIMINATOR (Gate-1 verified, run_6ea3cb12):
- Flag a blocking call IFF its NEAREST enclosing ``FunctionDef`` is an
  ``AsyncFunctionDef`` (NOT "any ancestor is async" — that would false-positive on
  every sync helper defined inside an async handler).
- EXCEPT when the call is lexically inside an ``asyncio.to_thread(...)`` argument —
  load-bearing ONLY for the ``to_thread(lambda: path.read_text())`` form, because the
  bare-attribute form ``to_thread(path.read_text)`` is an ``ast.Attribute`` (no call,
  ast never sees a Call node) and needs no guard.

SCOPE: ``backend/routers/`` only — where the class lives and is measurable. Known
same-symptom site ONE LEVEL UP, deliberately out of scope (named, not silently
widened, per Gate-1): ``backend/channels/gateway.py`` ``_stage_file_to_workspace``
(``target.write_bytes`` on the loop). Widening the scan to ``channels/`` / ``core/``
services is a follow-up run, not this one.
"""

from __future__ import annotations

import ast
import pathlib

# ---------------------------------------------------------------------------
# Blocking-primitive denylist.
#
# Deliberately UNDER-matches FP-prone primitives (PIT110): cheap metadata ops
# (Path.exists / stat / is_dir / mkdir) are NOT listed — a bare stat is not the
# loop-stalling class we hit, and listing them would false-positive on the
# guard clauses every handler has. The core family below is EXACTLY the set
# observed in the 16 real violations + the run_b2d3ece0 fixes.
# ---------------------------------------------------------------------------

# Method / attribute names (matched on ``x.<attr>()``):
_BLOCKING_ATTRS = frozenset({
    # pathlib.Path filesystem I/O
    "rglob", "glob", "iterdir",
    "read_text", "read_bytes", "write_text", "write_bytes",
    # pathlib.Path single-file mutations (Gate-2 run_6ea3cb12: unlink/rename/touch
    # block the loop just like write_text — a delete/rename/create sibling of the
    # read/write family). NOTE: `.rename`/`.replace` are common non-FS method names
    # (dict has neither; DataFrame.rename does) — a rare FP is acceptable per the
    # under-match principle, and none exists in routers/ today.
    "unlink", "rename", "touch",
    # subprocess (blocking variants only — Popen is non-blocking-ish, os.system is rare)
    "run", "check_output", "check_call",
    # shutil recursive tree ops (Gate-1: rmtree/move are used in async handlers)
    "rmtree", "move", "copytree",
    # file locking
    "flock", "lockf",
    # urllib blocking fetch
    "urlopen",
})

# Bare-name calls (``open(...)``):
_BLOCKING_NAMES = frozenset({"open"})

# Dotted calls needing a module qualifier so we don't confuse them with harmless
# same-named methods (``time.sleep`` blocks; ``asyncio.sleep`` does NOT;
# ``os.walk`` blocks; a local ``.walk`` might not).
_BLOCKING_DOTTED = frozenset({
    ("time", "sleep"),
    ("os", "walk"),
    ("os", "system"),
    ("os", "remove"),
    ("os", "rename"),
})

# KNOWN GAP (deliberately NOT in the denylist — named, not silently dropped):
#   sqlite3.connect + a full sync SQLite session is a DISTINCT blocking class (DB
#   session lifecycle, not one-shot FS I/O). It blocks the loop in library_api.py
#   list_mounts / register_mount (the latter also runs index_code_mount, a code-index
#   walk). Fixing those correctly means restructuring the whole try/conn/finally, not
#   a one-line wrap — so it is a scoped FOLLOW-UP run, not this one. Adding
#   sqlite3.connect here now would turn the gate RED with no fix in this changeset.
#   Same out-of-scope-but-named discipline as channels/gateway.py:_stage_file_to_workspace.

ROUTERS_DIR = pathlib.Path(__file__).resolve().parents[1] / "routers"


def _blocking_call_name(call: ast.Call) -> str | None:
    """Return a human label if this Call is a blocking primitive, else None."""
    fn = call.func
    if isinstance(fn, ast.Attribute):
        # module-qualified dotted call, e.g. time.sleep / os.walk
        if isinstance(fn.value, ast.Name) and (fn.value.id, fn.attr) in _BLOCKING_DOTTED:
            return f"{fn.value.id}.{fn.attr}"
        if fn.attr in _BLOCKING_ATTRS:
            return fn.attr
    elif isinstance(fn, ast.Name):
        if fn.id in _BLOCKING_NAMES:
            return fn.id
    return None


def _find_violations(source: str, filename: str = "<src>") -> list[tuple[str, int, str, str]]:
    """Return (filename, lineno, primitive, enclosing_async_fn) for every blocking
    primitive called DIRECTLY in an ``async def`` body (not in a sync helper, not
    lexically inside ``asyncio.to_thread(...)``).
    """
    tree = ast.parse(source, filename=filename)

    # child -> parent map (ast has no parent pointers)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def ancestors(node: ast.AST):
        cur = parent.get(node)
        while cur is not None:
            yield cur
            cur = parent.get(cur)

    violations: list[tuple[str, int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        primitive = _blocking_call_name(node)
        if primitive is None:
            continue

        anc = list(ancestors(node))

        # NEAREST enclosing function decides (NOT "any ancestor is async").
        enclosing_fn = next(
            (a for a in anc if isinstance(a, (ast.AsyncFunctionDef, ast.FunctionDef))),
            None,
        )
        if enclosing_fn is None:
            continue  # module-level / class-body — not a handler
        if not isinstance(enclosing_fn, ast.AsyncFunctionDef):
            continue  # inside a SYNC helper → sanctioned (may be to_thread-dispatched)

        # Lexically inside asyncio.to_thread(...) → sanctioned (the lambda form).
        in_to_thread = any(
            isinstance(a, ast.Call)
            and isinstance(a.func, ast.Attribute)
            and a.func.attr == "to_thread"
            for a in anc
        )
        if in_to_thread:
            continue

        violations.append((filename, node.lineno, primitive, enclosing_fn.name))

    return violations


def _scan_routers() -> list[tuple[str, int, str, str]]:
    """Scan every ``backend/routers/*.py`` file for violations."""
    out: list[tuple[str, int, str, str]] = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        out.extend(_find_violations(path.read_text(encoding="utf-8"), filename=path.name))
    return out


# ---------------------------------------------------------------------------
# AC1 — the real gate: zero blocking I/O directly on the loop in any router.
# ---------------------------------------------------------------------------
def test_no_blocking_io_in_async_handlers():
    violations = _scan_routers()
    if violations:
        detail = "\n".join(
            f"  {f}:{ln}  {prim}()  in async def {fn}()" for f, ln, prim, fn in violations
        )
        raise AssertionError(
            f"{len(violations)} blocking I/O call(s) directly in async route handler bodies "
            f"(they stall the event loop — wrap in a sync helper dispatched via "
            f"`await asyncio.to_thread(...)`, the run_b2d3ece0 pattern):\n{detail}"
        )


# ---------------------------------------------------------------------------
# AC2 — NON-VACUITY (mutation proof): the scanner must CATCH a deliberate violation.
# A source-scan that matches nothing proves nothing (verification-theater — the
# lesson persisted 2026-08-09). This feeds the scanner a known-bad handler and
# asserts it is flagged.
# ---------------------------------------------------------------------------
def test_scanner_catches_injected_violation():
    bad = (
        "import asyncio\n"
        "from pathlib import Path\n"
        "async def handler():\n"
        "    return Path('/tmp/x').read_text()\n"  # blocking, directly on the loop
    )
    v = _find_violations(bad, filename="<injected>")
    assert any(prim == "read_text" and fn == "handler" for _, _, prim, fn in v), (
        "scanner FAILED to catch a deliberate blocking read_text() in an async handler "
        "— it has no teeth"
    )


# ---------------------------------------------------------------------------
# AC4 — no FALSE POSITIVE on the sanctioned patterns (the run_b2d3ece0 shape).
# Both the sync-helper form and the bare-attribute to_thread form must be CLEAN.
# ---------------------------------------------------------------------------
def test_scanner_passes_sanctioned_to_thread_patterns():
    good = (
        "import asyncio\n"
        "from pathlib import Path\n"
        "\n"
        "async def via_sync_helper():\n"
        "    def _work():\n"
        "        return Path('/tmp/x').read_text()\n"  # nearest fn is sync _work → OK
        "    return await asyncio.to_thread(_work)\n"
        "\n"
        "async def via_bare_attr():\n"
        "    p = Path('/tmp/x')\n"
        "    return await asyncio.to_thread(p.write_text, 'data')\n"  # write_text is an Attribute, not a Call
        "\n"
        "async def via_lambda():\n"
        "    p = Path('/tmp/x')\n"
        "    return await asyncio.to_thread(lambda: p.read_text())\n"  # inside to_thread(...) → OK
    )
    v = _find_violations(good, filename="<sanctioned>")
    assert v == [], f"scanner FALSE-POSITIVED on the sanctioned to_thread pattern: {v}"


# ---------------------------------------------------------------------------
# AC4 (regression guard): the routers run_b2d3ece0 already fixed must stay clean.
# If any of these regress, this test localizes it before the tree-wide test.
# ---------------------------------------------------------------------------
def test_previously_fixed_routers_stay_clean():
    # The routers run_b2d3ece0 / community ④ fully cleaned FOR THE DENYLISTED
    # PRIMITIVES. NOTE two exclusions:
    #  - workspace.py: run_b2d3ece0 fixed only its read_file/upload/write_file paths;
    #    its browse_filesystem/list_files iterdir walks are fixed in THIS run.
    #  - library_api.py: NOT here — Gate-2 (run_6ea3cb12) found it still does
    #    sqlite3.connect on the loop (a known out-of-scope class, see _BLOCKING_DOTTED
    #    note). It is clean for the FS family but not loop-block-free, so it does not
    #    belong in a "fully clean" guard.
    already_fixed = ["eval.py", "jobs.py", "memory.py",
                     "pollinate.py", "tasks.py", "community_api.py"]
    dirty = []
    for name in already_fixed:
        path = ROUTERS_DIR / name
        if not path.exists():
            continue
        v = _find_violations(path.read_text(encoding="utf-8"), filename=name)
        if v:
            dirty.append((name, v))
    assert not dirty, f"a previously-fixed router regressed: {dirty}"
