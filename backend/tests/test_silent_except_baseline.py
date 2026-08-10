"""Structural contract test: the SILENT-SWALLOW broad-except CLASS must not GROW.

WHY THIS EXISTS (the structural-closure tier for the silent-swallow broad-except CLASS,
promotes governance candidate GC19):
A ``except Exception:`` (or bare ``except:`` / ``except BaseException:``) whose handler
body neither LOGS (``logger.*`` / ``log.*`` / ``print``) nor RE-RAISES silently swallows
the error — a real failure renders as a truthful-looking success. The archetype is
``ddd_brain._pending_count``'s ``except Exception: return 0`` feeding the BrainHub
attention badge: an authority failure showed as "✓ nothing needs you" (a lying badge,
reported across 6 self-audit rounds). This class was GROWING (a self-audit measured the
per-file count rising round over round) with only prose (GC19) to stop it — prose has
been demonstrably bypassed (SOUL P7: 3+ occurrences → build a gate OUTSIDE the agent).
This test is that gate: a NEW silent-swallow handler in an in-scope file pushes that
file's count above its committed baseline → this test goes RED → the non-DB pytest CI
step fails → the change cannot merge green. Sibling of ``test_router_async_blocking.py``
(the async-blocking-I/O class) — same AST-source-scan move, different class.

THE SANCTIONED PATTERN (NOT a violation): give the handler teeth —
``except Exception as e: logger.warning("...: %s", e); return 0`` (log THEN degrade) or a
``raise`` (conditional or unconditional). That is the entire point of the gate — GC19's
thesis is "observability, not silence". A ``# noqa`` escape hatch is DELIBERATELY ABSENT:
it is trivially bypassable (a whitelist-trap, the C041 family) — the only sanctioned
escape is to add a real log line, or (for a legitimate mass change) to bump the committed
baseline on the record via ``--update-baseline``.

DISCRIMINATOR (Gate-1 verified, run_9af622ee — deliberately UNDER-matches per PIT110):
A handler is SILENT iff its ``except`` clause is BROAD (``except Exception`` /
``except BaseException`` / bare ``except:``) AND its body, anywhere, contains NONE of:
  - a call to ``<x>.error/.warning/.exception/.warn/.critical/.info/.debug`` (standard
    ``logger.*`` / ``log.*`` / ``self.logger.*`` shape) or a bare ``print(...)`` /
    ``traceback.print_exc()`` / ``traceback.print_exception()``,
  - a ``raise`` statement (bare, ``raise X``, OR conditional ``if c: raise`` — a
    conditional re-raise is NOT silent; it propagates on that branch).
This UNDER-matches on purpose (PIT110, the async-scanner's stated philosophy): a handler
that logs through a CUSTOM helper (``_log_err(e)``) or signals via ``notify(...)`` /
``sys.exit()`` is counted as silent (false-positive). That is acceptable because the
BASELINE snapshots the current tree — every existing false-positive is already absorbed,
so a false-positive only ever affects a genuinely-NEW handler, where "add a real log
line" is the correct fix anyway. We do NOT chase every logging shape (over-matching would
let real silent swallows through by making the discriminator fragile).

RATCHET (Gate-1 SSA-acknowledged trade-off): the baseline is a per-file COUNT, not a
line-level identity. A net-zero churn (delete one silent handler, add a different one in
the same file) passes. This is ACCEPTED: the gate's job is to stop the class GROWING, not
to forbid refactoring within a file. Line-level identity was rejected as git-brittle
(line numbers shift on every edit). ``--update-baseline`` regenerates the snapshot when a
legitimate mass change (a cleanup that REDUCES counts, or an intentional add) lands.

SCOPE: ``backend/{routers,core,channels}`` — where the class lives and where the
self-audit measured it growing (routers via ddd_brain, core via attention_authority,
channels via slack/gateway/streaming). ``tests/`` is excluded (test code legitimately
swallows). Full-tree AST parse measured at ~1.4s (223 files / ~117K LOC) — no CI concern.
"""

from __future__ import annotations

import ast
import json
import pathlib

# ---------------------------------------------------------------------------
# Scope + baseline location.
# ---------------------------------------------------------------------------
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
SCOPE_DIRS = ("routers", "core", "channels")
BASELINE_PATH = pathlib.Path(__file__).resolve().parent / "silent_except_baseline.json"

# Logging method names that count as "the handler communicated the error".
_LOG_METHODS = frozenset({
    "error", "warning", "exception", "warn", "critical", "info", "debug",
})
# Bare-name / dotted calls that also count as communicating the error.
_LOG_BARE_NAMES = frozenset({"print"})
_TRACEBACK_PRINTERS = frozenset({"print_exc", "print_exception"})

# Error-event BUILDERS: yielding one of these is the third communication form, and
# arguably the strongest — it puts the failure (with its traceback in ``detail``) on the
# operator's SCREEN, not merely in a log file. Modelled as an explicit allowlist of
# builder names, deliberately NOT "any yield counts": a blanket yield rule would let
# `yield None` / `yield {}` launder a genuinely silent handler. Measured blast radius
# when introduced: exactly 2 handlers (retry_manager's two spawn-failure paths), both
# `yield _build_error_event(code="SPAWN_FAILED", detail=spawn_tb, ...)`.
_ERROR_EVENT_BUILDERS = frozenset({"_build_error_event", "build_error_event"})


def _handler_communicates(handler: ast.ExceptHandler) -> bool:
    """True if the handler body LOGS, RE-RAISES, or SURFACES an error event in code that
    RUNS when the handler fires.

    Logs  = ``<x>.error/.warning/.exception/.warn/.critical/.info/.debug(...)`` (the
            standard ``logger.*`` / ``log.*`` / ``self.logger.*`` shape),
            ``traceback.print_exc()`` / ``print_exception()``, or a bare ``print(...)``.
    Raise = any ``raise`` statement (bare / ``raise X`` / conditional ``if c: raise`` —
            a conditional re-raise propagates on that branch, so the handler is NOT silent).
    Yield = ``yield <builder>(...)`` for a builder in ``_ERROR_EVENT_BUILDERS``. An async
            generator cannot log-and-return its way out of a streaming turn; its way to
            report is to yield an error event down the stream, which reaches the USER.
            Restricted to named builders on purpose — see that constant.

    ⚠️ Nested-scope guard (Gate-2 correctness, run_9af622ee): we walk the handler's own
    statements but do NOT descend into a nested ``def``/``async def``/``lambda``/``class``
    DEFINED inside the handler — code inside a nested definition does NOT run when the
    handler fires (it's only defined, not called), so a logger/raise buried in an
    unused nested function would be a FALSE "communicates". Only communication in the
    handler's directly-executed body counts. (A nested def that is also CALLED here still
    counts via the Call node for the call itself, not its body.)
    """
    def _scan(nodes) -> bool:
        for node in nodes:
            # A nested definition: its BODY doesn't run on handler fire — skip the body,
            # but still scan the node's non-body parts (decorators/defaults/bases can call).
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                # decorators + default-arg expressions + base classes DO execute at def time,
                # but that is def-time, not handler-fire relevant; conservatively skip the
                # whole nested definition (under-match per PIT110 — a nested def that logs is
                # a rare shape, and the baseline absorbs any existing instance).
                continue
            if isinstance(node, ast.Raise):
                return True
            # `yield <error-event builder>(...)` — surfaces to the user. Matched at the
            # Yield node (not the bare Call) so that merely CONSTRUCTING an event and
            # dropping it on the floor does not count as having communicated it.
            if isinstance(node, (ast.Yield, ast.YieldFrom)):
                v = node.value
                if (isinstance(v, ast.Call) and isinstance(v.func, ast.Name)
                        and v.func.id in _ERROR_EVENT_BUILDERS):
                    return True
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    if fn.attr in _LOG_METHODS or fn.attr in _TRACEBACK_PRINTERS:
                        return True
                elif isinstance(fn, ast.Name):
                    if fn.id in _LOG_BARE_NAMES:
                        return True
            # recurse into this node's direct children (but the nested-def guard above
            # prevents descending into function/class bodies)
            if _scan(ast.iter_child_nodes(node)):
                return True
        return False

    return _scan(ast.iter_child_nodes(handler))


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """True if the except clause is broad: bare ``except:``, ``except Exception``,
    or ``except BaseException`` (single name or in a tuple)."""
    t = handler.type
    if t is None:  # bare except:
        return True
    names: list[str] = []
    if isinstance(t, ast.Name):
        names = [t.id]
    elif isinstance(t, ast.Tuple):
        names = [e.id for e in t.elts if isinstance(e, ast.Name)]
    return any(n in ("Exception", "BaseException") for n in names)


def _find_silent_swallows(source: str, filename: str = "<src>") -> list[tuple[str, int]]:
    """Return (filename, lineno) for every SILENT-SWALLOW broad-except handler:
    a broad ``except`` whose body neither logs nor re-raises."""
    tree = ast.parse(source, filename=filename)
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _is_broad_except(node):
            continue
        if _handler_communicates(node):
            continue
        out.append((filename, node.lineno))
    return out


def _scan_scope() -> dict[str, int]:
    """Per-file silent-swallow counts across the in-scope dirs.

    Keyed by the path RELATIVE to backend/ (e.g. ``routers/ddd_brain.py``) so the
    baseline is stable regardless of the absolute checkout location. Files with zero
    silent swallows are OMITTED (a new/clean file is implicit 0)."""
    counts: dict[str, int] = {}
    for sub in SCOPE_DIRS:
        base = BACKEND_DIR / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "/tests/" in path.as_posix() or path.name.startswith("test_"):
                continue
            try:
                v = _find_silent_swallows(path.read_text(encoding="utf-8"), filename=path.name)
            except (OSError, SyntaxError, ValueError, UnicodeError):
                continue
            if v:
                counts[path.relative_to(BACKEND_DIR).as_posix()] = len(v)
    return counts


def _load_baseline() -> dict[str, int]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.exists() else {}


# ---------------------------------------------------------------------------
# AC1 — the real gate: no file may EXCEED its committed baseline (ratchet).
# A brand-new silent swallow in any in-scope file (or any silent swallow in a
# file not in the baseline) makes this RED.
# ---------------------------------------------------------------------------
def test_no_new_silent_swallow_broad_except():
    current = _scan_scope()
    baseline = _load_baseline()
    regressions = []
    for rel, n in current.items():
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(f"  {rel}: {n} silent-swallow broad-except (baseline {allowed})")
    if regressions:
        raise AssertionError(
            f"{len(regressions)} file(s) EXCEED their silent-swallow baseline — a broad "
            f"`except Exception:` that neither logs nor re-raises was added. Give it teeth "
            f"(`except Exception as e: logger.warning('...: %s', e); ...` or a `raise`), NOT "
            f"a `# noqa`. If this is a legitimate mass change, regenerate the baseline via "
            f"`python -m tests.test_silent_except_baseline --update-baseline`:\n"
            + "\n".join(regressions)
        )


# ---------------------------------------------------------------------------
# AC3 — baseline honesty: the committed baseline must MATCH the live scan at
# snapshot time (documents the current sites; absorbs nothing silently). If this
# fails, someone changed the tree without regenerating the baseline — regenerate.
# (This is the twin of AC1: AC1 blocks GROWTH; AC3 blocks a stale/drifted baseline
# that under-counts, which would silently permit new swallows.)
# ---------------------------------------------------------------------------
def test_baseline_matches_current_scan():
    current = _scan_scope()
    baseline = _load_baseline()
    # AC3 direction that matters: baseline must not UNDER-count vs current (that would
    # be AC1's job too) NOR OVER-count relative to what's really there (a stale, inflated
    # baseline that would let a real regression hide under slack). Require exact equality.
    assert current == baseline, (
        "silent_except_baseline.json is OUT OF SYNC with the live scan. Regenerate it "
        "(`python -m tests.test_silent_except_baseline --update-baseline`) and commit — "
        "the baseline must be an HONEST snapshot, not a loose ceiling.\n"
        f"  only-in-current (new/increased): "
        f"{ {k: current[k] for k in current if current.get(k) != baseline.get(k)} }\n"
        f"  only-in-baseline (removed/decreased): "
        f"{ {k: baseline[k] for k in baseline if baseline.get(k) != current.get(k)} }"
    )


# ---------------------------------------------------------------------------
# AC2 — NON-VACUITY (mutation proof): the scanner must CATCH a deliberate silent
# swallow. A source-scan that matches nothing proves nothing (verification-theater).
# ---------------------------------------------------------------------------
def test_scanner_catches_injected_silent_swallow():
    bad = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"       # broad
        "        return 0\n"            # no log, no raise → SILENT
    )
    v = _find_silent_swallows(bad, filename="<injected>")
    assert len(v) == 1, f"scanner FAILED to catch an injected silent-swallow broad-except: {v}"

    bare = (
        "def g():\n"
        "    try:\n"
        "        risky()\n"
        "    except:\n"                 # bare except:
        "        pass\n"                # SILENT
    )
    assert len(_find_silent_swallows(bare, "<bare>")) == 1, "scanner missed a bare `except: pass`"

    base_exc = (
        "def h():\n"
        "    try:\n"
        "        risky()\n"
        "    except BaseException:\n"
        "        continue\n"
    )
    assert len(_find_silent_swallows(base_exc, "<base>")) == 1, "scanner missed `except BaseException: continue`"


# ---------------------------------------------------------------------------
# AC2 — NO FALSE POSITIVE on handlers that DO communicate the error. A logged /
# re-raising / conditionally-raising handler must be CLEAN.
# ---------------------------------------------------------------------------
def test_scanner_passes_communicating_handlers():
    good = (
        "import logging, traceback\n"
        "logger = logging.getLogger(__name__)\n"
        "\n"
        "def logged():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as e:\n"
        "        logger.warning('failed: %s', e)\n"   # logs → clean
        "        return 0\n"
        "\n"
        "def reraised():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        raise\n"                              # re-raises → clean
        "\n"
        "def conditionally_raised():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as e:\n"
        "        if fatal(e):\n"
        "            raise\n"                          # conditional raise → NOT silent
        "        return None\n"
        "\n"
        "def typed_only():\n"
        "    try:\n"
        "        risky()\n"
        "    except (OSError, ValueError):\n"          # NOT broad → not our class
        "        return 0\n"
        "\n"
        "def traceback_printed():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        traceback.print_exc()\n"              # prints → clean
        "        return 0\n"
        "\n"
        "def printed():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        print('boom')\n"                      # bare print → clean
        "        return 0\n"
        "\n"
        "async def surfaced():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as e:\n"
        "        yield _build_error_event(code='X', detail=str(e))\n"  # surfaces → clean
        "        return\n"
    )
    v = _find_silent_swallows(good, filename="<communicating>")
    assert v == [], f"scanner FALSE-POSITIVED on a communicating handler: {v}"


def test_yield_allowlist_is_narrow():
    """The error-event yield rule must not degenerate into "any yield counts".

    An allowlist's only real risk is being too loose, so pin the boundary: yielding a
    NAMED error-event builder communicates, but yielding a bare value, a dict, or some
    other function's result does NOT — otherwise `yield None` would launder every
    silent handler in an async generator. Also pins that merely CONSTRUCTING an event
    and dropping it is not communicating.
    """
    src = (
        "async def bare_yield():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        yield None\n"                          # SILENT
        "\n"
        "async def dict_yield():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        yield {'_abort': True}\n"              # SILENT (no reason carried)
        "\n"
        "async def other_call_yield():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        yield make_payload(1)\n"               # SILENT (not an error builder)
        "\n"
        "async def built_but_dropped():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as e:\n"
        "        ev = _build_error_event(code='X', detail=str(e))\n"  # SILENT: never sent
        "        return\n"
    )
    lines = [ln for _f, ln in _find_silent_swallows(src, filename="<narrow>")]
    assert len(lines) == 4, (
        "the yield allowlist LEAKED — one of yield None / yield {...} / "
        f"yield other_call() / build-without-yield was treated as communicating: {lines}"
    )


def test_scanner_ignores_logging_in_unused_nested_def():
    """Gate-2 correctness (run_9af622ee): a handler that DEFINES a nested function
    which logs — but never CALLS it — is still SILENT (the nested body doesn't run on
    handler fire). ast.walk would false-NEGATIVE here; the nested-scope guard fixes it."""
    nested = (
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        def _unused():\n"
        "            logger.warning('never called')\n"   # inside a nested def → does NOT run
        "        return 0\n"                              # the handler itself is SILENT
    )
    v = _find_silent_swallows(nested, filename="<nested>")
    assert len(v) == 1, (
        "a logger call inside an UNUSED nested def must NOT count as the handler "
        f"communicating — the handler is silent. Got: {v}"
    )


# ---------------------------------------------------------------------------
# AC4 — bare `except:` is BANNED outright, with no baseline and no ratchet.
#
# It is strictly worse than `except Exception:` because it also catches
# KeyboardInterrupt and SystemExit, so a shutdown signal arriving inside the try
# block gets absorbed and the process refuses to die. Unlike the silent-swallow
# count there is no debt to work down here — the codebase was already at exactly 2
# (both JSON parses in routers/plugins.py) when this gate was written, both fixed in
# the same commit. Zero is therefore the permanent floor, which is why this is a
# hard assertion rather than another entry in the baseline.
#
# Scope is DELIBERATELY WIDER than SCOPE_DIRS: the silent-swallow baseline is scoped
# to routers/core/channels because that is where the debt was inventoried, but a bare
# except is a bug anywhere, and there is no reason to let one land in jobs/ or hooks/.
# ---------------------------------------------------------------------------
_BARE_EXCEPT_SCOPE = ("routers", "core", "channels", "jobs", "hooks", "utils",
                      "database", "skills", "middleware")


def _find_bare_excepts() -> list[str]:
    out: list[str] = []
    for sub in _BARE_EXCEPT_SCOPE:
        base = BACKEND_DIR / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "/tests/" in path.as_posix() or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError, UnicodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    out.append(f"{path.relative_to(BACKEND_DIR).as_posix()}:{node.lineno}")
    return out


def test_no_bare_except_anywhere():
    found = _find_bare_excepts()
    assert found == [], (
        f"{len(found)} bare `except:` found — it also catches KeyboardInterrupt and "
        f"SystemExit, so a shutdown signal raised inside the try block is swallowed and "
        f"the process will not exit. Name the exceptions you actually expect "
        f"(`except (json.JSONDecodeError, TypeError):`), or use `except Exception as e:` "
        f"with a log if it truly must be broad:\n  " + "\n  ".join(found)
    )


def test_bare_except_scanner_is_not_vacuous():
    """The gate above asserts an empty list, which a broken scanner also satisfies.

    Pin that the detector actually fires — and that it does NOT fire on the narrowed
    form the fix uses, otherwise the gate would just be banning all error handling.
    """
    bare = "def f():\n    try:\n        risky()\n    except:\n        return []\n"
    narrowed = ("import json\n"
                "def f():\n    try:\n        risky()\n"
                "    except (json.JSONDecodeError, TypeError):\n        return []\n")
    broad_named = "def f():\n    try:\n        risky()\n    except Exception:\n        return []\n"

    def bare_lines(src: str) -> list[int]:
        return [n.lineno for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.ExceptHandler) and n.type is None]

    assert bare_lines(bare) == [4], "detector missed a bare except:"
    assert bare_lines(narrowed) == [], "detector false-positived on a narrowed except"
    assert bare_lines(broad_named) == [], (
        "detector false-positived on `except Exception:` — that is the silent-swallow "
        "gate's business, not this one's"
    )


# ---------------------------------------------------------------------------
# --update-baseline regenerator (the ONLY sanctioned way to move the baseline).
#   python -m tests.test_silent_except_baseline --update-baseline
# Run from backend/ with the venv active.
# ---------------------------------------------------------------------------
def _update_baseline() -> None:
    counts = _scan_scope()
    ordered = {k: counts[k] for k in sorted(counts)}
    BASELINE_PATH.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    total = sum(ordered.values())
    print(f"wrote {BASELINE_PATH} — {len(ordered)} files, {total} silent-swallow broad-except total")


if __name__ == "__main__":
    import sys

    if "--update-baseline" in sys.argv:
        _update_baseline()
    else:
        cur = _scan_scope()
        print(json.dumps({k: cur[k] for k in sorted(cur)}, indent=2))
        print(f"TOTAL: {sum(cur.values())} silent-swallow broad-except across {len(cur)} files")
