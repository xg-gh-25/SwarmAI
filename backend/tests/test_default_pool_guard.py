"""Tests for default_pool_guard — the PreToolUse Write/Edit gate that WARNs when
new code offloads high-risk blocking work (git/subprocess/Bedrock/clone) onto the
default asyncio ThreadPoolExecutor via ``asyncio.to_thread`` / ``run_in_executor(None,)``
instead of a dedicated ``core.executors`` class pool.

Provenance: pipeline run_d72047b0. The gate is the P7 structural fix for the
"blocking work on the default pool → /health starvation" class (COE run_b36c7880,
EVOLUTION O006/O020). It is a WARN nudge, never a block (mirrors inclusive_term_guard).

Methodology: each test drives the guard with a synthetic Write/Edit tool_input and
asserts the WARN fires (or not). The mutation-proof (test_routers_exemption_is_load_bearing)
removes the routers/ exemption and confirms the routers case starts firing — proving
the exemption is doing real work, not vacuous.
"""
import asyncio

from core.security_hooks import default_pool_guard


def _run(tool_name: str, file_path: str, text: str) -> dict:
    """Drive the guard with a synthetic Write/Edit and return its decision dict."""
    field = "content" if tool_name == "Write" else "new_string"
    input_data = {
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path, field: text},
    }
    return asyncio.run(default_pool_guard(input_data, None, None))


def _warned(decision: dict) -> bool:
    """A WARN = approve + a non-empty additionalContext mentioning the pool."""
    return (
        decision.get("decision") == "approve"
        and "executors.run_in" in (decision.get("additionalContext") or "")
    )


# ── HIT cases: high-risk callee on the default pool, outside routers/ ──────────

def test_highrisk_callee_in_core_warns():
    """to_thread wrapping a high-risk-named callee (needs_human_review — forks
    git check-ignore) in core/ → WARN."""
    d = _run(
        "Edit",
        "backend/core/foo.py",
        "verdict = await asyncio.to_thread(needs_human_review, path, 'written')",
    )
    assert _warned(d), f"expected WARN for needs_human_review offload in core/, got {d}"


def test_subprocess_run_callee_warns():
    """to_thread(subprocess.run, ...) — the callee NAME subprocess.run is itself a
    high-risk token, so this fires on the callee alone (independent of args)."""
    d = _run(
        "Edit",
        "backend/core/plugin_manager.py",
        'result = await asyncio.to_thread(subprocess.run, [SAFE, ARGS])',
    )
    assert _warned(d), f"expected WARN for to_thread(subprocess.run, ...), got {d}"


def test_highrisk_token_only_in_args_warns():
    """The REAL blind spot that hid plugin_manager's git fetch/clone: a BENIGN-named
    callee (_helper — no high-risk token in the name) with 'git' only in the ARGS
    list. This passes ONLY if the args-window scan is live — the callee name gives
    zero signal. Distinct from test_subprocess_run_callee_warns (which fires on the
    callee name). If the 200-char args window were deleted, this goes RED."""
    d = _run(
        "Edit",
        "backend/core/plugin_manager.py",
        'result = await asyncio.to_thread(_helper, ["git", "clone", url, dst])',
    )
    assert _warned(d), f"git-in-args with benign callee must warn (args-window path), got {d}"


def test_run_in_executor_none_bedrock_warns():
    """run_in_executor(None, ...) wrapping a Bedrock/embed callee → WARN."""
    d = _run(
        "Write",
        "backend/hooks/some_hook.py",
        "await loop.run_in_executor(None, self._deep_check, root, ws)",
    )
    assert _warned(d), f"expected WARN for run_in_executor(None, _deep_check), got {d}"


# ── MISS cases: legitimate / exempt ───────────────────────────────────────────

def test_routers_short_fs_read_is_exempt():
    """A routers/ HTTP handler doing a short fs read via to_thread → NO warn.
    ~100 such callsites exist and are legitimate (<1s, request-scoped)."""
    d = _run(
        "Edit",
        "backend/routers/workspace_api.py",
        "content = await asyncio.to_thread(target.read_text, encoding='utf-8')",
    )
    assert not _warned(d), f"routers/ short fs read must NOT warn, got {d}"


def test_core_to_thread_no_highrisk_callee_is_exempt():
    """A core/ to_thread wrapping a pure/cheap callee (no high-risk token) → NO warn.
    The gate targets high-risk work, not all to_thread usage."""
    d = _run(
        "Edit",
        "backend/core/foo.py",
        "data = await asyncio.to_thread(json.loads, raw_string)",
    )
    assert not _warned(d), f"cheap to_thread(json.loads) must NOT warn, got {d}"


def test_non_write_tool_is_exempt():
    """A Read (or any non-Write/Edit tool) → approve, no scan."""
    d = asyncio.run(
        default_pool_guard(
            {"tool_name": "Read", "tool_input": {"file_path": "backend/core/foo.py"}},
            None,
            None,
        )
    )
    assert d.get("decision") == "approve" and not d.get("additionalContext")


def test_malformed_input_fails_open():
    """Malformed tool_input never crashes the write path (fail-open approve)."""
    d = asyncio.run(default_pool_guard({"tool_name": "Edit", "tool_input": None}, None, None))
    assert d.get("decision") == "approve"


def test_scan_error_fails_open(monkeypatch):
    """The load-bearing guarantee: a scan EXCEPTION can never crash the write path.
    Force _scan_default_pool_offload to raise → the guard's try/except must still
    return approve (fail-open). The isinstance guards catch None/list BEFORE the
    scan, so only this test exercises the except block."""
    import core.security_hooks as m

    def _boom(_text):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr(m, "_scan_default_pool_offload", _boom)
    d = _run("Edit", "backend/core/foo.py",
             "await asyncio.to_thread(needs_human_review, p, 'written')")
    assert d.get("decision") == "approve", "scan error must fail open (approve), never crash the write"


# ── MUTATION PROOF: the routers/ exemption is load-bearing ────────────────────

def test_routers_exemption_is_load_bearing(monkeypatch):
    """TRUE mutation-proof: remove the routers/ exemption and confirm the SAME
    routers/ path flips from suppressed → warning. This proves the exemption is
    the ONLY thing suppressing it (dead-code check) — without it the gate would
    false-positive storm over the ~100 legit routers/ callsites, get ignored, and
    be useless.
    """
    import core.security_hooks as m
    highrisk = "x = await asyncio.to_thread(needs_human_review, p, 'written')"
    # Baseline: exemption live → routers/ suppressed.
    assert not _warned(_run("Edit", "backend/routers/foo.py", highrisk)), \
        "baseline: routers/ must be exempt while the exemption is live"
    # MUTATE: remove the exemption entirely.
    monkeypatch.setattr(m, "_is_routers_path", lambda fp: False)
    # The SAME routers/ path must now warn — proving the exemption was load-bearing.
    assert _warned(_run("Edit", "backend/routers/foo.py", highrisk)), \
        "removing _is_routers_path must make routers/ warn — else the exemption is dead code"
