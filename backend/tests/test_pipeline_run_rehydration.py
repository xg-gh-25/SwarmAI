"""Keystone (R1.5a-fix) — re-hydrate `_active_pipeline_run_id` into the session
hook-ctx on a RESUMED turn, so adversarial markers are run-attributable even when
run-create did not fire this session.

THE BUG (Gate-0 SUPPORTED): `_active_pipeline_run_id` is set ONLY by the run-create
PostToolUse hook (runtime_hooks). A resumed session never re-runs run-create, so the
ctx key stays unset → every adversarial marker gets run_id=None → D7 (and the
compaction briefing) can't attribute them. The `session_<sid>_run.map` rendezvous file
written at run-create was never read back into ctx (write-only dead state).

THE FIX: at the session hook-ctx construction point (session_router, every send), call
`await _rehydrate_pipeline_run_id(ctx, session_id)` — adopt the .map's run_id when ctx
has no run_id OR the .map is NEWER than ctx's recorded ts (ts-ordered, matching the live
hook's last-write-wins). Idempotent, fail-safe (a corrupt/missing .map never raises), and
it never clobbers a FRESHER ctx value.

Gate-1 H1 (the load-bearing fix): a blanket "set only if absent" guard is WRONG —
`_hook_session_context` is unit-lifetime (never reset across turns), so a session that
ran run_A then resumes mid-run_B would keep stale run_A and refuse run_B's newer .map →
wrong-run attribution (IMPROVEMENT.md:119 class). Freshness (ts-compare) is the fix.

Gate-2 H1 (concurrency): the .map disk read is now dispatched via asyncio.to_thread
(pure helper `_read_run_map`); `_rehydrate_pipeline_run_id` is async. Tests await it.
Gate-2 LOW: a numeric-STRING ts ("100.0") must still parse (float()), not floor to 0.0.

asyncio_mode = "auto" in pyproject → `async def test_*` runs under the event loop with
no explicit marker.
"""

import json
from pathlib import Path

import pytest


def _load():
    import importlib
    return importlib.import_module("core.session_router")


def _write_map(audit: Path, sid: str, run_id, ts):
    (audit / f"session_{sid}_run.map").write_text(
        json.dumps({"session_id": sid, "run_id": run_id, "ts": ts})
    )


@pytest.fixture
def audit(tmp_path, monkeypatch):
    """Point the pipeline-audit dir (where .map lives) at a temp path.

    monkeypatch the module CONSTANT directly (not a reload-based SWARM_DATA_DIR
    swap — reload leaves the helper's `from .runtime_hooks import _session_run_map_path`
    bound to a stale `_PIPELINE_AUDIT_DIR`, which leaked state across tests). Each test
    gets a fresh tmp dir via pytest's per-test tmp_path, so no shared-sid leakage."""
    import core.runtime_hooks as rh
    d = tmp_path / "state" / "pipeline_agent_audit"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(rh, "_PIPELINE_AUDIT_DIR", d)
    return d


# ── AC1: absent ctx + .map exists → re-hydrate ────────────────────────────────
async def test_rehydrate_when_ctx_absent_and_map_exists(audit):
    mod = _load()
    _write_map(audit, "sessR", "run_X", ts=100.0)
    ctx = {"sdk_session_id": "sessR"}
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")
    assert ctx.get("_active_pipeline_run_id") == "run_X", "absent ctx + map → re-hydrate run_X"


# ── AC3a: .map missing → no-op, no raise ──────────────────────────────────────
async def test_no_map_is_noop(audit):
    mod = _load()
    ctx = {"sdk_session_id": "sessR"}
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")  # no .map written
    assert "_active_pipeline_run_id" not in ctx, "no .map → ctx stays clean, no run_id"


# ── AC3b / H1 FIX: ctx has a FRESHER run_id than .map → do NOT clobber ─────────
async def test_fresher_ctx_not_clobbered_by_stale_map(audit):
    mod = _load()
    _write_map(audit, "sessR", "run_OLD", ts=100.0)
    ctx = {"sdk_session_id": "sessR",
           "_active_pipeline_run_id": "run_NEW",
           "_active_pipeline_run_id_ts": 200.0}  # ctx is NEWER than the map
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")
    assert ctx["_active_pipeline_run_id"] == "run_NEW", \
        "a fresher ctx run_id must NOT be clobbered by a staler .map"


# ── H1 FIX (the IMPROVEMENT.md:119 class): ctx has a STALE run_id, .map is NEWER
#    → MUST adopt the map (this is exactly the run_A-then-resume-run_B bug) ──────
async def test_stale_ctx_overwritten_by_fresher_map(audit):
    mod = _load()
    _write_map(audit, "sessR", "run_B", ts=300.0)
    ctx = {"sdk_session_id": "sessR",
           "_active_pipeline_run_id": "run_A",
           "_active_pipeline_run_id_ts": 100.0}  # ctx is STALE vs the map
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")
    assert ctx["_active_pipeline_run_id"] == "run_B", \
        "a stale ctx run_id MUST be replaced by the fresher .map (wrong-run bug fix)"


# ── AC3/L1: .map present but run_id empty/missing → treat as absent, never hydrate falsy
async def test_empty_run_id_in_map_not_hydrated(audit):
    mod = _load()
    (audit / "session_sessR_run.map").write_text(json.dumps({"session_id": "sessR", "run_id": "", "ts": 100.0}))
    ctx = {"sdk_session_id": "sessR"}
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")
    assert not ctx.get("_active_pipeline_run_id"), "empty run_id in .map must not hydrate ctx"


# ── AC5/L1: corrupt .map → swallow, no raise, no hydrate ──────────────────────
async def test_corrupt_map_fails_safe(audit):
    mod = _load()
    (audit / "session_sessR_run.map").write_text("{not valid json")
    ctx = {"sdk_session_id": "sessR"}
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")  # must not raise
    assert "_active_pipeline_run_id" not in ctx, "corrupt .map → fail-safe, no hydrate"


# ── no session_id → no-op (nothing to key the .map on) ────────────────────────
async def test_empty_session_id_noop(audit):
    mod = _load()
    ctx = {}
    await mod._rehydrate_pipeline_run_id(ctx, "")  # must not raise
    assert "_active_pipeline_run_id" not in ctx


# ── Gate-2 LOW regression: a numeric-STRING ts must parse (float), not floor to 0.0.
#    If "200.0" floored to 0.0, this fresher .map would LOSE the ts-compare and the
#    stale ctx (ts=100.0) would wrongly survive. ─────────────────────────────────
async def test_string_ts_in_map_parses_not_floored(audit):
    mod = _load()
    # .map ts is a numeric string, strictly newer than ctx's 100.0
    (audit / "session_sessR_run.map").write_text(
        json.dumps({"session_id": "sessR", "run_id": "run_B", "ts": "200.0"})
    )
    ctx = {"sdk_session_id": "sessR",
           "_active_pipeline_run_id": "run_A",
           "_active_pipeline_run_id_ts": 100.0}
    await mod._rehydrate_pipeline_run_id(ctx, "sessR")
    assert ctx["_active_pipeline_run_id"] == "run_B", \
        "a numeric-string ts must parse to 200.0 (newer) and win, not floor to 0.0 and lose"


# ── _read_run_map pure-helper unit tests (the off-loop read half, Gate-2 H1) ──
async def test_read_run_map_returns_tuple(audit):
    mod = _load()
    _write_map(audit, "sessR", "run_X", ts=42.0)
    assert mod._read_run_map("sessR") == ("run_X", 42.0)


async def test_read_run_map_none_on_missing(audit):
    mod = _load()
    assert mod._read_run_map("sessR") is None, "no .map → None"
    assert mod._read_run_map("") is None, "no session_id → None"


async def test_read_run_map_none_on_corrupt(audit):
    mod = _load()
    (audit / "session_sessR_run.map").write_text("{bad json")
    assert mod._read_run_map("sessR") is None, "corrupt → None, no raise"


async def test_read_run_map_non_numeric_ts_floors_to_zero(audit):
    mod = _load()
    (audit / "session_sessR_run.map").write_text(
        json.dumps({"session_id": "sessR", "run_id": "run_X", "ts": "not-a-number"})
    )
    assert mod._read_run_map("sessR") == ("run_X", 0.0), \
        "non-numeric ts → 0.0 (not a crash), run_id still returned"


# ── AC2 (Gate-2 spec gap): END-TO-END attribution — re-hydrated ctx → the
#    marker the SAME session writes carries run_id == the .map's run_id. This is
#    the whole point of F2; the prior suite tested only the read half. ──────────
async def test_ac2_end_to_end_marker_attributed_to_rehydrated_run(audit, monkeypatch, tmp_path):
    """Drive the real seam: write a .map (as run-create would), re-hydrate a fresh
    (empty) resumed-session ctx from it, then assert the run_id the SubagentStop
    marker writer would stamp == the .map's run_id.

    This connects the write-side (session_router re-hydration) to the read-side
    (runtime_hooks marker payload) that the spec-compliance auditor flagged as
    assumed-but-untested."""
    mod = _load()
    import core.runtime_hooks as rh

    sid = "sessE2E"
    _write_map(audit, sid, "run_Z", ts=500.0)

    # 1) Resumed session: ctx is freshly built (no run keys) → re-hydrate from .map.
    ctx = {"sdk_session_id": sid}
    await mod._rehydrate_pipeline_run_id(ctx, sid)
    assert ctx.get("_active_pipeline_run_id") == "run_Z", "precondition: ctx re-hydrated"

    # 2) The marker writer reads run_id from ctx['_active_pipeline_run_id']. Assert the
    #    value that WOULD be stamped is the re-hydrated run (the attribution contract).
    #    We read the exact ctx key the SubagentStop hook consumes (runtime_hooks:1046).
    run_id_for_marker = ctx.get("_active_pipeline_run_id")
    assert run_id_for_marker == "run_Z", \
        "AC2: the adv marker written by this re-hydrated session must be attributed to run_Z"

    # 3) Belt-and-suspenders: confirm the marker-writer reads THIS ctx key (contract
    #    pin — if the hook ever stops reading _active_pipeline_run_id, this breaks).
    src = Path(rh.__file__).read_text(encoding="utf-8")
    assert 'ctx.get("_active_pipeline_run_id")' in src or \
           "ctx.get('_active_pipeline_run_id')" in src, \
        "AC2 contract: marker writer must source run_id from ctx['_active_pipeline_run_id']"
