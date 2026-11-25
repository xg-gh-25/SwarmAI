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

SCOPE — TWO TIERS (run_a1f4c2d8):
  * STRICT_DIRS = ``backend/routers/`` + ``backend/channels/`` — ZERO tolerance. Both are
    clean, so one new violation is RED. ``channels/`` was folded in by this run, which
    also FIXED the site this docstring used to name as out-of-scope
    (``channels/gateway.py`` ``_stage_file_to_workspace`` — ``write_bytes`` of a whole
    Slack attachment on the loop).
  * BASELINED_DIRS = ``backend/core/`` — a per-file COUNT ratchet in
    ``async_blocking_baseline.json`` (46 findings across 7 files at freeze time). core/
    is a multi-run cleanup in colder user-initiated paths, so it is FROZEN rather than
    left unscanned: a NEW blocking call there is RED immediately, and the existing 46
    get cleaned in later batches without blocking anything. Putting core/ in the strict
    tier today would turn the gate RED with no fix in the changeset — the exact
    anti-pattern this file warns about for sqlite3.connect.
Nothing is silently out of scope any more: every directory that can host this class is
either at zero or frozen with its number on the record.
"""

from __future__ import annotations

import ast
import json
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
    # NOTE: subprocess run/check_output/check_call are NOT here — they live in
    # _BLOCKING_DOTTED (module-qualified). A bare `.run()` attr match false-positives
    # on every unrelated `<obj>.run()` (found when widening to channels/:
    # gateway.py:1825 `heartbeat_mgr.run()` inside asyncio.create_task — an async
    # coroutine, the OPPOSITE of blocking). routers/ happened to have no such call, so
    # the loose match survived undetected. Verified across routers/ + channels/: every
    # real subprocess call is dotted `subprocess.run(...)` (no `from subprocess import
    # run`), so requiring the qualifier loses ZERO coverage and kills the FP class.
    # shutil recursive tree ops (Gate-1: rmtree/move are used in async handlers)
    "rmtree", "move", "copytree",
    # file locking
    "flock", "lockf",
    # urllib blocking fetch
    "urlopen",
})

# Bare-name calls (``open(...)``):
#
# Also lists the KNOWN-BLOCKING sync recall entrypoints. These are not FS primitives
# but user-defined sync functions that internally do a heavy BM25 tokenize + a
# multi-source file walk — calling one DIRECTLY on the loop stalls it exactly like a
# read_text (measured 7–13× /health stall while library_search was inline). The AST
# gate is primitive-name based, so it was BLIND to "call a helper that blocks
# inside": recall_library_hits() sat un-offloaded in library_api.library_search while
# every sibling handler used to_thread, and the gate still reported the router clean —
# the exact "half-migrated, gate green" hole the report flagged. Denylisting these
# names by identity closes it: a bare recall_*/recall_all call in an async handler is
# now RED, so the offload can never silently regress. (They are module-level sync defs
# in core.recall_multi; there is no same-named async method in scope to false-positive.)
_BLOCKING_NAMES = frozenset({
    "open",
    "recall_all", "recall_library_hits", "recall_multi",
})

# Dotted calls needing a module qualifier so we don't confuse them with harmless
# same-named methods (``time.sleep`` blocks; ``asyncio.sleep`` does NOT;
# ``os.walk`` blocks; a local ``.walk`` might not).
_BLOCKING_DOTTED = frozenset({
    ("time", "sleep"),
    ("os", "walk"),
    ("os", "system"),
    ("os", "remove"),
    ("os", "rename"),
    # subprocess blocking variants — qualifier REQUIRED (see the _BLOCKING_ATTRS note).
    # Popen is deliberately absent (it returns immediately; only .wait/.communicate block).
    ("subprocess", "run"),
    ("subprocess", "check_output"),
    ("subprocess", "check_call"),
    # Synchronous SQLite session (run_a1f4c2d8 — see the CLOSED GAP note below). Dotted
    # so a same-named `.connect()` on an async client/socket object is not flagged.
    # aiosqlite is NOT here: it is the async driver, awaiting it is the correct pattern.
    ("sqlite3", "connect"),
})

# CLOSED GAP (run_a1f4c2d8) — sqlite3.connect is now DENYLISTED, above.
#   It was held out because a full sync SQLite session is a DISTINCT blocking class (a
#   session lifecycle, not one-shot FS I/O) and the only two occurrences —
#   library_api.py list_mounts / register_mount (the latter also ran judge_mount_kind's
#   rglob AND index_code_mount, a whole tree-sitter graph build) — needed the entire
#   try/conn/finally restructured, not a one-line wrap. Denylisting it before that fix
#   would have turned the gate RED with no fix in the same changeset.
#   Both are now sync `_read` / `_work` helpers dispatched via asyncio.to_thread, so the
#   primitive is denylisted and the class is closed rather than documented.
#   Verified before flipping: those were the ONLY two sqlite3.connect calls in
#   routers/ + channels/, so this adds coverage without a baseline.

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
ROUTERS_DIR = _BACKEND / "routers"

# ── TWO TIERS (run_a1f4c2d8) ────────────────────────────────────────────────
# STRICT_DIRS  — zero tolerance. Already clean, so a single new violation is RED.
# BASELINED_DIRS — a per-file COUNT ratchet (core/), because it holds 46 findings in
#   colder user-initiated paths that are a multi-run cleanup. Adding core/ to the strict
#   tier would turn the gate RED with no fix in the changeset — the exact anti-pattern
#   this file warns about for sqlite3.connect. Freezing it instead means:
#     * a NEW blocking call in core/ is RED immediately (the class stops growing), and
#     * the remaining 46 get cleaned in later batches without blocking anything.
#   Same shape as tests/silent_except_baseline.json, deliberately — one ratchet pattern
#   in this codebase, not two.
#
# WHY FREEZE INSTEAD OF FINISHING THE CLEANUP: batch 2 measured the returns. Of
# swarm_workspace_manager's 6 reported findings, 3 were FALSE POSITIVES (already
# dispatched via anyio) — a third of the batch was phantom, and hand-restructuring the
# real ones churned correct code. The remaining files are cold paths (plugin install,
# one-time legacy cleanup) where a hand refactor carries more regression risk than the
# loop-blocking it removes. A gate that makes new violations impossible is worth more
# than 46 hand edits, and it is what this repo has repeatedly found to be the only
# durable lever (SOUL P7: prose has been bypassed; build a gate outside the agent).
STRICT_DIRS = (ROUTERS_DIR, _BACKEND / "channels")
BASELINED_DIRS = (_BACKEND / "core",)
BASELINE_PATH = pathlib.Path(__file__).with_name("async_blocking_baseline.json")

# Scanned scope. ``channels/`` joined in run_a1f4c2d8 (the sibling gate
# test_silent_except_baseline.py already scanned routers+core+channels, so the narrow
# routers-only scope no longer had a justification). Measured blast radius at the time
# of widening: channels/ = 2 findings — one REAL (gateway.py _stage_file_to_workspace's
# attachment write_bytes, FIXED in this run) and one FALSE POSITIVE (the bare `.run()`
# attr match, fixed by moving subprocess.* to _BLOCKING_DOTTED). So widening cost one
# real fix + one scanner-precision fix, not a baseline.
#
# ``core/`` stays OUT — named honestly, not silently dropped: the same scanner finds
# **64** findings there (plugin_manager 7, projection_layer 6, runtime_hooks 6,
# prompt_builder 5, swarm_workspace_manager 6, skill_manager 3, ui_actions 2, ...).
# That is a multi-run cleanup, and adding core/ here now would turn the gate RED with
# no fix in this changeset — the exact anti-pattern this file warns about. Same
# out-of-scope-but-named discipline as the sqlite3.connect class above.
SCOPE_DIRS = (ROUTERS_DIR, _BACKEND / "channels")


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


# Off-loop dispatchers: a call lexically inside one of these calls' arguments runs on a
# worker thread, not on the loop (see the long note in _find_violations).
# `executors.run_nav` / `run_nav_action` are the Left Nav pools' spelling of run_in; they
# were missing, so `run_nav(lambda: p.read_text())` would have been a false positive.
_DISPATCH_ATTRS = frozenset({
    "to_thread", "run_sync", "run_in", "run_in_executor", "run_nav", "run_nav_action",
})


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """child -> parent map (ast has no parent pointers)."""
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node
    return parent


def _ancestors(parent: dict[ast.AST, ast.AST], node: ast.AST):
    cur = parent.get(node)
    while cur is not None:
        yield cur
        cur = parent.get(cur)


def _in_dispatch(parent: dict[ast.AST, ast.AST], node: ast.AST) -> bool:
    """True when ``node`` sits lexically inside the arguments of an off-loop dispatch."""
    return any(
        isinstance(a, ast.Call)
        and isinstance(a.func, ast.Attribute)
        and a.func.attr in _DISPATCH_ATTRS
        for a in _ancestors(parent, node)
    )


def _find_violations(source: str, filename: str = "<src>") -> list[tuple[str, int, str, str]]:
    """Return (filename, lineno, primitive, enclosing_async_fn) for every blocking
    primitive called DIRECTLY in an ``async def`` body (not in a sync helper, not
    lexically inside ``asyncio.to_thread(...)``).
    """
    tree = ast.parse(source, filename=filename)
    parent = _parent_map(tree)

    def ancestors(node: ast.AST):
        return _ancestors(parent, node)

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

        # Lexically inside a thread-dispatch call → sanctioned (the lambda form).
        # Covers BOTH off-loop dispatchers used in this codebase:
        #   asyncio.to_thread(lambda: p.read_text())
        #   anyio.to_thread.run_sync(lambda: p.write_text(...))   <- run_a1f4c2d8
        # The anyio spelling was MISSING and produced a wave of FALSE POSITIVES in
        # core/: swarm_workspace_manager alone contributed 6 "findings" that were all
        # ALREADY correctly dispatched via anyio (routers/artifacts.py never tripped it
        # because it passes a bare helper — `run_sync(_helper)` is an ast.Attribute with
        # no Call node — so the gap stayed hidden while the scan was routers-only).
        # Matching the ATTRIBUTE NAME (`to_thread` / `run_sync`) rather than the full
        # dotted path keeps this robust to aliasing (`from anyio import to_thread`), at
        # the cost of exempting an unrelated `x.run_sync(...)` — acceptable under the
        # file's under-match principle, and no such call exists in scope.
        # `executors.run_in(pool, fn)` / `loop.run_in_executor(pool, fn)` are the
        # same off-loop dispatch onto a NAMED pool (run_d72047b0 routed high-risk
        # work there on purpose). Missing them made correctly-dispatched calls
        # count as blocking: plugin_manager's three hang-guarded git calls moved
        # INTO run_in and the ratchet went red for code that had just been fixed.
        # The set itself is module-level (_DISPATCH_ATTRS) so the helper scan below
        # exempts exactly the same dispatchers.
        if _in_dispatch(parent, node):
            continue

        violations.append((filename, node.lineno, primitive, enclosing_fn.name))

    return violations


def _find_helper_violations(source: str, filename: str = "<src>") -> list[tuple[str, int, str, str]]:
    """Blocking calls an ``async def`` makes THROUGH a sync helper of the same module.

    ``_find_violations`` sees only a primitive written directly in the async body. A
    handler that calls ``_build_card(d)`` — a sync helper that walks a folder — blocks
    the loop just the same, and that scan stayed green on it: ``pollinate_assets`` did
    its whole folder walk on the loop while this file listed pollinate.py as clean.

    Rule: an async def that CALLS (``name(...)``, not passes ``name``) a sync ``def`` of
    the same module, outside an off-loop dispatch, is a violation when that helper — or
    a helper it calls, transitively — makes a blocking call outside a dispatch. Returns
    (filename, lineno of the call in the async def, "helper()->...->primitive",
    async fn). The sanctioned shapes stay clean: ``await run_nav(helper)`` passes the
    name (an ``ast.Name``, no call), and ``to_thread(lambda: helper())`` is inside a
    dispatch.

    Deliberately under-matches (same principle as the denylists above):
    * only bare-name calls to module-level or nested ``def``s — not methods
      (``self._x()``), not imported functions (the recall_* names are denylisted by
      identity instead);
    * a helper passed as a callback (``sorted(xs, key=helper)``) is not followed: the
      receiver may run it on the loop or in a pool (``BackgroundTasks.add_task``), and
      this scanner cannot tell which.
    """
    tree = ast.parse(source, filename=filename)
    parent = _parent_map(tree)

    def enclosing_fn(node: ast.AST):
        return next(
            (a for a in _ancestors(parent, node)
             if isinstance(a, (ast.AsyncFunctionDef, ast.FunctionDef))),
            None,
        )

    helpers: dict[str, list[ast.FunctionDef]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and not isinstance(parent.get(node), ast.ClassDef):
            helpers.setdefault(node.name, []).append(node)

    direct: dict[ast.FunctionDef, str] = {}        # helper -> first blocking primitive
    calls: dict[ast.FunctionDef, list[str]] = {}   # helper -> helper names it calls
    async_calls: list[tuple[ast.Call, ast.AsyncFunctionDef, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = enclosing_fn(node)
        if fn is None or _in_dispatch(parent, node):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) and node.func.id in helpers else None
        if isinstance(fn, ast.FunctionDef):
            prim = _blocking_call_name(node)
            if prim is not None:
                direct.setdefault(fn, prim)
            if name is not None:
                calls.setdefault(fn, []).append(name)
        elif name is not None:
            async_calls.append((node, fn, name))

    # Which helpers reach a blocking call: a fixed point over the call graph. Start
    # from the helpers that block directly; each pass marks a helper that calls a
    # marked one. It stops when a pass marks nothing, after at most one pass per
    # helper — no recursion, so cycles and shared callees need no special case and the
    # cost stays polynomial whatever the graph's shape (a memoized walk was exponential
    # once a recursive helper sat below shared helpers).
    blocks: dict[ast.FunctionDef, str] = dict(direct)
    changed = True
    while changed:
        changed = False
        for fn, names in calls.items():
            if fn in blocks:
                continue
            for name in names:
                hit = next((d for d in helpers[name] if d in blocks), None)
                if hit is not None:
                    blocks[fn] = f"{name}()->{blocks[hit]}"
                    changed = True
                    break

    out: list[tuple[str, int, str, str]] = []
    for node, fn, name in async_calls:
        hit = next((d for d in helpers[name] if d in blocks), None)
        if hit is not None:
            out.append((filename, node.lineno, f"{name}()->{blocks[hit]}", fn.name))
    return out


def _scan_dirs(scopes, finder=None) -> list[tuple[str, int, str, str]]:
    """Scan every ``*.py`` under each scope for violations.

    ``rglob`` (not ``glob``) so a package subdir cannot hide a violation —
    ``channels/adapters/`` and ``core/code_intel/`` are real subpackages. Filenames are
    reported dir-qualified (``channels/gateway.py``) so a finding is unambiguous when
    two scoped dirs hold a same-named module, AND so the baseline keys below are stable.
    """
    out: list[tuple[str, int, str, str]] = []
    for scope in scopes:
        if not scope.is_dir():
            continue
        for path in sorted(scope.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            label = f"{scope.name}/{path.relative_to(scope).as_posix()}"
            out.extend((finder or _find_violations)(path.read_text(encoding="utf-8"), filename=label))
    return out


def _scan_routers() -> list[tuple[str, int, str, str]]:
    """The STRICT tier (routers + channels) — kept under its historic name so the
    existing regression guard below reads unchanged."""
    return _scan_dirs(STRICT_DIRS)


def _scan_strict_helpers() -> list[tuple[str, int, str, str]]:
    """Helper-level findings (``_find_helper_violations``) in the STRICT tier."""
    return _scan_dirs(STRICT_DIRS, finder=_find_helper_violations)


def _scan_baselined() -> dict[str, int]:
    """Everything the ratchet freezes, as {dir-qualified file: finding count}:
      * core/            — DIRECT findings (``_find_violations``);
      * routers/ channels/ — HELPER-level findings (``_find_helper_violations``). Their
        direct findings stay at zero tolerance in test_no_blocking_io_in_async_handlers.
    The keys never collide (each is prefixed by its own scope dir), so one baseline file
    holds both. core/ helper-level findings are not counted yet: core/ is already a
    frozen cleanup backlog, and counting them would only make that backlog bigger
    without a fix. Measure them with ``_scan_dirs(BASELINED_DIRS, _find_helper_violations)``.
    """
    counts: dict[str, int] = {}
    for f, _ln, _prim, _fn in _scan_dirs(BASELINED_DIRS) + _scan_strict_helpers():
        counts[f] = counts.get(f, 0) + 1
    return counts


def _load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# AC1 — the real gate: zero blocking I/O directly on the loop in routers/ + channels/.
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
# AC8 (run_a1f4c2d8) — core/ RATCHET: the class must not GROW.
#
# core/ is not zero yet (46 findings in colder user-initiated paths — a multi-run
# cleanup), so it is frozen per-file rather than left unscanned. A NEW blocking call in
# core/ pushes its file above the committed baseline → RED → the change cannot merge
# green. Cleanup batches lower the numbers; nothing raises them silently.
#
# Ratchet is a per-file COUNT, not line identity (same accepted trade-off as
# silent_except_baseline.json): line numbers shift on every edit, so identity would be
# git-brittle. A net-zero churn within one file passes — the job is to stop GROWTH, not
# to forbid refactoring.
#
# The same ratchet also freezes HELPER-level findings in routers/ + channels/ (a handler
# that calls a same-module sync helper which blocks — _find_helper_violations). When the
# helper scan was added, other routers already had such calls (mcp, workspace,
# workspace_api, library_api); they are recorded per file in the baseline, so a NEW one
# anywhere in routers/ or channels/ is RED while the existing ones get fixed separately.
#
# There is deliberately NO per-call escape hatch (no `# noqa`): a bypassable annotation
# is the C041 whitelist trap. The sanctioned moves are (a) dispatch the call off-loop,
# or (b) regenerate the baseline ON THE RECORD via
#     python -m tests.test_router_async_blocking --update-baseline
# ---------------------------------------------------------------------------
def test_core_blocking_io_does_not_exceed_baseline():
    baseline = _load_baseline()
    current = _scan_baselined()
    regressions = {
        f: (current[f], baseline.get(f, 0))
        for f in current
        if current[f] > baseline.get(f, 0)
    }
    if regressions:
        detail = "\n".join(
            f"  {f}: {cur} blocking call(s) in an async body (baseline {base})"
            for f, (cur, base) in sorted(regressions.items())
        )
        raise AssertionError(
            "blocking I/O was ADDED to an async body in core/ — it stalls the event "
            "loop for every request and every chat tab's SSE stream. Move it into a "
            "sync helper and `await asyncio.to_thread(helper)` (or "
            "`anyio.to_thread.run_sync`), NOT a `# noqa`. If this is a legitimate mass "
            "change, regenerate the baseline via `python -m "
            "tests.test_router_async_blocking --update-baseline`:\n" + detail
        )


def test_core_baseline_is_an_honest_snapshot():
    """The baseline must MATCH the live scan — not merely bound it.

    A loose ceiling rots: a file cleaned from 7 to 2 would keep a stale 7 of headroom,
    silently re-admitting 5 new violations later. So a REDUCTION fails here too and must
    be recorded with --update-baseline. Same stance (and same friction) as
    silent_except_baseline.json — an honest snapshot, not a high-water mark.
    """
    baseline = _load_baseline()
    current = _scan_baselined()
    assert baseline, (
        f"{BASELINE_PATH.name} is missing or empty — regenerate it with "
        f"`python -m tests.test_router_async_blocking --update-baseline`"
    )
    only_current = {f: c for f, c in current.items() if baseline.get(f, 0) != c}
    only_baseline = {f: c for f, c in baseline.items() if current.get(f, 0) != c}
    assert current == baseline, (
        f"{BASELINE_PATH.name} is OUT OF SYNC with the live scan. Regenerate it "
        f"(`python -m tests.test_router_async_blocking --update-baseline`) and commit — "
        f"the baseline must be an HONEST snapshot, not a loose ceiling.\n"
        f"  live-differs (new/increased): {only_current}\n"
        f"  baseline-differs (removed/decreased): {only_baseline}"
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
def test_scanner_catches_bare_sync_recall_call():
    """Non-vacuity for the recall-entrypoint denylist: a bare recall_library_hits()
    (or recall_all()) directly in an async handler must be flagged. This is the class
    the AST gate was blind to — a heavy sync helper called on the loop — and the exact
    site (library_api.library_search) that was half-migrated while the gate stayed green."""
    bad = (
        "import asyncio\n"
        "async def library_search(q, scope):\n"
        "    from core.recall_multi import recall_library_hits\n"
        "    return recall_library_hits(q, scope)\n"  # blocking sync helper, on the loop
    )
    v = _find_violations(bad, filename="<recall>")
    assert any(prim == "recall_library_hits" and fn == "library_search"
               for _, _, prim, fn in v), (
        "scanner FAILED to flag a bare sync recall_library_hits() on the loop — the "
        "half-migration hole is still open"
    )
    # And the sanctioned offload form must NOT be flagged.
    good = (
        "import asyncio\n"
        "async def library_search(q, scope):\n"
        "    from core.recall_multi import recall_library_hits\n"
        "    return await asyncio.to_thread(recall_library_hits, q, scope)\n"
    )
    assert _find_violations(good, filename="<recall_ok>") == [], (
        "the to_thread-offloaded recall call must be clean"
    )


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
        "\n"
        "async def via_anyio_lambda():\n"
        "    import anyio\n"
        "    p = Path('/tmp/x')\n"
        # anyio is the OTHER dispatcher this codebase uses; the lambda form must be
        # exempt exactly like asyncio's (run_a1f4c2d8 — it was NOT, and produced 3
        # phantom findings in core/swarm_workspace_manager alone)
        "    return await anyio.to_thread.run_sync(lambda: p.write_text('data'))\n"
        "\n"
        "async def via_anyio_bare():\n"
        "    import anyio\n"
        "    def _work():\n"
        "        return Path('/tmp/x').read_text()\n"
        "    return await anyio.to_thread.run_sync(_work)\n"
    )
    v = _find_violations(good, filename="<sanctioned>")
    assert v == [], f"scanner FALSE-POSITIVED on the sanctioned to_thread pattern: {v}"


def test_scanner_catches_blocking_inside_helper():
    """Non-vacuity for the helper scan: the old GET /pollinate/assets shape — an async
    handler calling a sync helper that (through another helper) walks a folder — must be
    flagged, naming the chain. A direct-only scan reported this handler clean."""
    bad = (
        "from pathlib import Path\n"
        "def _list_dirs(root):\n"
        "    return [d for d in root.iterdir() if d.is_dir()]\n"
        "def _read_meta(d):\n"
        "    return (d / 'run.json').read_text()\n"
        "def _build_card(d):\n"
        "    return {'meta': _read_meta(d)}\n"
        "async def assets():\n"
        "    return [_build_card(d) for d in _list_dirs(Path('/tmp'))]\n"
    )
    v = _find_helper_violations(bad, filename="<helper>")
    found = {(prim, fn) for _, _, prim, fn in v}
    assert ("_list_dirs()->iterdir", "assets") in found, v
    assert ("_build_card()->_read_meta()->read_text", "assets") in found, v
    # The direct scan alone is blind to it — that is the hole this closes.
    assert _find_violations(bad, filename="<helper>") == []


def test_helper_scan_passes_dispatched_helpers():
    """The sanctioned shapes stay clean: the helper passed by NAME to a dispatcher (no
    call on the loop), called inside a dispatch lambda, a helper that does nothing
    blocking, a method call, and recursion between helpers that never block."""
    good = (
        "import asyncio\n"
        "from core import executors\n"
        "from pathlib import Path\n"
        "def _walk():\n"
        "    return list(Path('/tmp').iterdir())\n"
        "def _pure(x):\n"
        "    return x + 1\n"
        "def _ping(n):\n"
        "    return _pong(n - 1) if n else 0\n"
        "def _pong(n):\n"
        "    return _ping(n)\n"
        "class Store:\n"
        "    def load(self):\n"
        "        return Path('/tmp/x').read_text()\n"
        "async def via_run_nav():\n"
        "    return await executors.run_nav(_walk)\n"
        "async def via_lambda():\n"
        "    return await asyncio.to_thread(lambda: _walk())\n"
        "async def via_run_nav_lambda():\n"
        "    return await executors.run_nav(lambda: _walk())\n"
        "async def pure_and_recursive(store):\n"
        "    store.load()\n"  # a method, not followed (under-match by design)
        "    return _pure(1) + _ping(3)\n"
    )
    assert _find_helper_violations(good, filename="<ok>") == []
    assert _find_violations(good, filename="<ok>") == []


def test_helper_scan_follows_a_blocking_cycle():
    """Mutual recursion must not hide a blocking call reached inside the cycle."""
    src = (
        "from pathlib import Path\n"
        "def _a(n):\n"
        "    return _b(n)\n"
        "def _b(n):\n"
        "    if n:\n"
        "        return _a(n - 1)\n"
        "    return Path('/tmp/x').read_bytes()\n"
        "async def handler():\n"
        "    return _a(3)\n"
    )
    v = _find_helper_violations(src, filename="<cycle>")
    assert [(p, fn) for _, _, p, fn in v] == [("_a()->_b()->read_bytes", "handler")]


def test_helper_scan_does_not_explode_on_shared_helpers():
    """A helper reached through many paths costs the same as one reached once. A
    40-level chain where each helper calls the next two has about 10^8 paths; a scan that
    walked paths would never finish (the --timeout catches it). Checked twice: without
    a loop, and with the last helper calling itself (the recursive-walker shape, which
    defeated a memoized walk)."""
    import time
    depth = 40
    lines = ["from pathlib import Path"]
    for i in range(depth):
        lines.append(f"def _h{i}():")
        lines.append(f"    return _h{i + 1}() + _h{i + 2}()")
    lines += [f"def _h{depth}():", "    return 0", f"def _h{depth + 1}():", "    return 1"]
    lines += ["async def handler():", "    return _h0()"]
    acyclic = "\n".join(lines) + "\n"
    recursive = acyclic.replace(f"def _h{depth}():\n    return 0",
                                f"def _h{depth}(n=0):\n    return _h{depth}(n - 1) if n else 0")
    assert recursive != acyclic
    for clean in (acyclic, recursive):
        t0 = time.monotonic()
        assert _find_helper_violations(clean, filename="<wide>") == []
        assert time.monotonic() - t0 < 5.0
        # ...and a blocking call at the far end of the same graph is still found.
        blocking = clean.replace(f"def _h{depth + 1}():\n    return 1",
                                 f"def _h{depth + 1}():\n    return Path('/tmp/x').read_text()")
        t0 = time.monotonic()
        v = _find_helper_violations(blocking, filename="<wide>")
        assert time.monotonic() - t0 < 5.0
        assert len(v) == 1 and v[0][2].endswith("read_text") and v[0][3] == "handler"


# ---------------------------------------------------------------------------
# AC7 (run_a1f4c2d8) — the anyio exemption must not become a BLANKET pass.
#
# Widening the dispatch-call exemption from {to_thread} to {to_thread, run_sync} risks
# over-matching: if it were implemented as "anywhere under a Call", a blocking call
# merely NEAR a dispatch would go unflagged. Assert the exemption is LEXICAL — inside the
# dispatch call's arguments — by checking a violation SIBLING to a dispatch is still
# caught. Without this, the FP fix could silently hollow out the gate.
# ---------------------------------------------------------------------------
def test_scanner_passes_named_pool_dispatch():
    """executors.run_in / loop.run_in_executor with a lambda are off-loop."""
    src = (
        "async def via_run_in():\n"
        "    return await executors.run_in('subprocess', lambda: subprocess.run(['git']))\n"
        "async def via_run_in_executor(loop):\n"
        "    return await loop.run_in_executor(None, lambda: p.read_text())\n"
    )
    assert _find_violations(src) == []
    # ...and the SAME call outside the dispatcher is still a violation.
    bare = "async def raw():\n    return subprocess.run(['git'])\n"
    assert [v[2] for v in _find_violations(bare)] == ["subprocess.run"]


def test_anyio_exemption_does_not_blanket_pass_the_handler():
    mixed = (
        "import anyio\n"
        "from pathlib import Path\n"
        "async def handler():\n"
        "    p = Path('/tmp/x')\n"
        "    await anyio.to_thread.run_sync(lambda: p.write_text('ok'))\n"  # exempt
        "    return p.read_text()\n"  # NOT exempt — sibling statement, still on the loop
    )
    v = _find_violations(mixed, filename="<mixed>")
    prims = {prim for _, _, prim, _ in v}
    assert "read_text" in prims, (
        "a blocking call SIBLING to an anyio dispatch was not flagged — the exemption "
        "leaked from 'inside the dispatch arguments' to 'anywhere in the handler', "
        "which would hollow out the whole gate"
    )
    assert "write_text" not in prims, (
        f"the anyio lambda form is still false-positiving: {v}"
    )


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
    # "Clean" counts helper-level calls too (_find_helper_violations): pollinate.py sat
    # in this list while GET /assets walked its folder on the loop through _build_card
    # and _list_run_dirs — a direct-only scan could not see it.
    already_fixed = ["eval.py", "jobs.py", "memory.py",
                     "pollinate.py", "tasks.py"]
    dirty = []
    for name in already_fixed:
        path = ROUTERS_DIR / name
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        v = _find_violations(src, filename=name) + _find_helper_violations(src, filename=name)
        if v:
            dirty.append((name, v))
    assert not dirty, f"a previously-fixed router regressed: {dirty}"


# ---------------------------------------------------------------------------
# AC5 (run_a1f4c2d8) — the WIDENED scope must not be VACUOUS.
#
# Widening SCOPE_DIRS is only meaningful if the scan actually reaches those files. A
# path typo / wrong glob would make the gate silently scan nothing and still pass —
# the "guard that never executes" failure this repo has now hit 6 times (a lint rule
# that isn't in CI, a reconciliation endpoint that 500'd, `self._pid`). So assert the
# scan REACHES channels/, not just that it returns [].
# ---------------------------------------------------------------------------
def test_scope_actually_reaches_channels():
    scoped = {p.name for p in SCOPE_DIRS}
    assert "channels" in scoped, "channels/ dropped out of SCOPE_DIRS"

    channels_dir = _BACKEND / "channels"
    assert channels_dir.is_dir(), "channels/ missing — SCOPE_DIRS points at nothing"

    # The scanner must PARSE these files (not skip them): inject a violation into a
    # real scoped file's source and confirm _find_violations flags it. This proves the
    # reach without depending on channels/ containing a violation (it must not).
    real = (channels_dir / "gateway.py").read_text(encoding="utf-8")
    injected = real + (
        "\n\nasync def _ac5_probe():\n"
        "    from pathlib import Path\n"
        "    return Path('/tmp/x').read_bytes()\n"
    )
    v = _find_violations(injected, filename="channels/gateway.py")
    assert any(fn == "_ac5_probe" for _, _, _, fn in v), (
        "scanner cannot flag a violation in a channels/ source — the widened scope "
        "is vacuous"
    )


# ---------------------------------------------------------------------------
# AC6 (run_a1f4c2d8) — subprocess precision: still CATCHES the real thing, no longer
# FALSE-POSITIVES on an unrelated `<obj>.run()`.
#
# Moving run/check_output/check_call from _BLOCKING_ATTRS to _BLOCKING_DOTTED is a
# LOOSENING of the matcher, so it needs both halves proven: the catch must survive
# (else the tightening silently created a hole) and the FP must be gone (the reason
# for the change). Without the first half this would be a coverage regression dressed
# up as a precision fix.
# ---------------------------------------------------------------------------
def test_subprocess_match_requires_module_qualifier():
    still_caught = (
        "import subprocess\n"
        "async def handler():\n"
        "    return subprocess.run(['git', 'status'])\n"
    )
    v = _find_violations(still_caught, filename="<subproc>")
    assert any(prim == "subprocess.run" and fn == "handler" for _, _, prim, fn in v), (
        "tightening subprocess to a dotted match LOST coverage — a real "
        "subprocess.run on the loop is no longer flagged"
    )

    no_longer_fp = (
        "import asyncio\n"
        "async def handler(mgr, app):\n"
        "    task = asyncio.create_task(mgr.run())\n"   # a coroutine, NOT blocking
        "    app.run()\n"                              # unrelated .run() method
        "    return task\n"
    )
    v = _find_violations(no_longer_fp, filename="<fp>")
    assert v == [], (
        f"bare `.run()` still false-positives — that is what blocked widening to "
        f"channels/ (gateway.py heartbeat_mgr.run()): {v}"
    )


# ---------------------------------------------------------------------------
# AC9 (run_a1f4c2d8) — the RATCHET itself must have teeth, and must not be VACUOUS.
#
# A baseline gate has two silent failure modes, and both look green:
#   1. the scan reaches nothing (a bad scope path) → every count is 0 → any real
#      violation is "within baseline" forever;
#   2. the comparison is written so a regression cannot fail it.
# Six guards in this repo shipped without ever executing, so assert both directly
# instead of trusting that the two tests above would have caught a regression.
# ---------------------------------------------------------------------------
def test_baselined_scan_actually_reaches_core():
    scoped = {p.name for p in BASELINED_DIRS}
    assert "core" in scoped, "core/ dropped out of BASELINED_DIRS"
    assert (_BACKEND / "core").is_dir(), "BASELINED_DIRS points at nothing"

    counts = _scan_baselined()
    assert counts, (
        "the core/ scan returned NO files at all. Either the scope path is wrong or "
        "_find_violations stopped matching — either way the ratchet is inert and every "
        "future violation would pass as 'within baseline'."
    )
    assert any(k.startswith("core/") for k in counts), (
        "the baselined scan holds no core/ file — the core/ ratchet is inert"
    )
    # Keys must be dir-qualified, or a baseline written today would not match tomorrow's
    # scan of a same-named module in another scope.
    prefixes = tuple(f"{d.name}/" for d in (*BASELINED_DIRS, *STRICT_DIRS))
    assert all(k.startswith(prefixes) for k in counts), (
        f"baseline keys lost their dir qualifier: {sorted(counts)[:3]}"
    )


def test_ratchet_flags_an_increase_over_baseline():
    """Drive the comparison with a synthetic +1 and assert it is a regression.

    Mirrors test_core_blocking_io_does_not_exceed_baseline's logic against a known-bad
    input, so a future edit that makes the real check unable to fail is caught here.
    """
    baseline = {"core/example.py": 1}
    current = {"core/example.py": 2}
    regressions = {
        f: (current[f], baseline.get(f, 0))
        for f in current
        if current[f] > baseline.get(f, 0)
    }
    assert regressions == {"core/example.py": (2, 1)}

    # A brand-new file (absent from the baseline) must also count as a regression —
    # otherwise a whole new module could arrive full of blocking calls unnoticed.
    regressions_new = {
        f: (v, baseline.get(f, 0))
        for f, v in {"core/brand_new.py": 1}.items()
        if v > baseline.get(f, 0)
    }
    assert regressions_new == {"core/brand_new.py": (1, 0)}

    # And a DECREASE must not be reported as a regression (cleanup is not a failure of
    # the growth check — it fails the separate honesty check, which demands a rewrite).
    assert not {
        f: v for f, v in {"core/example.py": 0}.items() if v > baseline.get(f, 0)
    }


# ---------------------------------------------------------------------------
# --update-baseline regenerator (the ONLY sanctioned way to move the baseline).
#   python -m tests.test_router_async_blocking --update-baseline
# ---------------------------------------------------------------------------
def _update_baseline() -> None:
    counts = _scan_baselined()
    ordered = {k: counts[k] for k in sorted(counts)}
    BASELINE_PATH.write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(ordered.values())
    print(f"wrote {BASELINE_PATH} — {len(ordered)} files, {total} frozen finding(s) "
          f"(core/ direct + routers/channels via helpers)")


if __name__ == "__main__":
    import sys
    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        strict = _scan_routers()
        print(f"STRICT (routers+channels): {len(strict)} violation(s)")
        for f, ln, prim, fn in strict:
            print(f"  {f}:{ln}  {prim}()  in async {fn}")
        for f, ln, prim, fn in _scan_strict_helpers():
            print(f"  helper  {f}:{ln}  {prim}  in async {fn}")
        cur = _scan_baselined()
        print(json.dumps({k: cur[k] for k in sorted(cur)}, indent=2))
        print(f"BASELINED: {sum(cur.values())} across {len(cur)} files")
