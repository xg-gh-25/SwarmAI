"""Shared test fixtures for code_intel — test-isolation hygiene.

Why this exists (run_423bb21d): several code_intel tests legitimately mutate
PROCESS-GLOBAL parser state to exercise real paths —
`test_parser.py::TestTreeSitterLiveAST` clears `_ts_live_cache` and populates the
thread-local parser cache to drive the REAL tree-sitter construction; other tests
`monkeypatch` `_tree_sitter_live`. monkeypatch reverts its own attribute swaps, but
the MODULE-LEVEL caches (`_ts_live_cache` dict, `_parser_cache_tls.cache`) persist
across tests in the same process.

That leak is invisible until two independently-authored test files land in the same
run: the graded-incremental freshness E2E
(`test_freshness.py::TestGradedIncrementalE2E`) re-parses a repo and grades a
comment-only edit as SKIP — but a perturbed liveness cache left by an earlier
parser test flips the grade to PARTIAL_UPDATE, so freshness fails ONLY when it runs
after the parser tests (proven by file-order bisection: each file passes alone;
`test_parser.py`+`test_freshness.py` together fails; the failure survives with the
value-ref feature fully disabled → it is a pre-existing isolation defect, not a
feature regression).

Fix (additive, touches no existing test): an autouse fixture resets the two
process-global parser caches around every test in this directory, so state a test
mutates cannot bleed into the next. Cheap (dict clear + attr delete), and it makes
the ordering-dependent red deterministically green for all code_intel tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_parser_process_globals():
    """Reset module-level parser caches before AND after each test.

    Both the tree-sitter liveness cache and the thread-local parser cache are
    process-global; a test that clears/populates them must not leak into the next
    (see module docstring). Reset on both edges so a test starts clean regardless
    of who ran before it, and leaves nothing behind for who runs after.
    """
    try:
        import core.code_intel.parser as P
    except Exception:
        # If the parser module can't import, there's nothing to reset — let the
        # test itself surface the import error.
        yield
        return

    def _reset():
        cache = getattr(P, "_ts_live_cache", None)
        if isinstance(cache, dict):
            cache.clear()
        tls = getattr(P, "_parser_cache_tls", None)
        if tls is not None and hasattr(tls, "cache"):
            try:
                del tls.cache
            except AttributeError:
                pass

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _isolate_local_roots(tmp_path_factory, monkeypatch):
    """Keep the local-root registry (``<app data>/bindings/local_roots.json``, the
    ownership declaration) off the live store: every test gets its own app-data dir."""
    monkeypatch.setenv("SWARM_DATA_DIR", str(tmp_path_factory.mktemp("app_data")))


@pytest.fixture
def own_local_root():
    """``own(project, path)``: make *path* a confirmed local root of *project* — what a
    person's Confirm in Brain Hub records — without the git-top checks."""
    return _own_local_root


def _own_local_root(project: str, path) -> None:
    import json
    import os
    from core.code_intel.local_roots import registry_path
    reg = registry_path()
    reg.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(reg.read_text()) if reg.exists() else {"version": 1, "projects": {}}
    rows = data["projects"].setdefault(project, [])
    real = os.path.realpath(str(path))
    if not any(r["path"] == real for r in rows):
        rows.append({"path": real, "state": "confirmed", "added_at": "t", "source": "test"})
    reg.write_text(json.dumps(data))
