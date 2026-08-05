"""Tests for Layer-2 Canvas fs-watcher (author-agnostic live-surfacing).

WHAT IS TESTED
--------------
The Layer-2 workspace filesystem watcher surfaces file writes that Layer-1's
per-tool SSE emit CANNOT see — writes by sub-agents / CLI subprocesses / hooks,
which the Claude SDK filters out as sidechain messages (verified:
claude_agent_sdk/types.py:1600 "tool-use sidechain messages are filtered out").

Two units under test:
  1. surface_injection  — per-session injection queue registry + the
     attribution resolver (sole non-channel STREAMING session AND that session
     has an in-flight tool — prevents simultaneous ambiguity + in-flight gate).
  2. WorkspaceSurfaceWatcher — the watchfiles-based watcher that classifies a
     batch via needs_human_review_batch (off-loop) and publishes file_changed
     events to the sole streaming session.

KEY INVARIANTS (map to PLAN ACs)
  AC1 — a content write, with exactly ONE session streaming, reaches THAT
        session's queue as a file_changed(operation=written) event.
  AC2 — with 2+ sessions streaming, publish routes to NONE (no wall-clock /
        time-window attribution — prevents the simultaneous-candidate ambiguity
        of the run_4de279ca cross-tab bleed; a narrow ~2s time-shift residual
        remains, see surface_injection module docstring — NOT "impossible").
  AC4 — noise (Knowledge/, .artifacts/, gitignored) is filtered by
        needs_human_review → not surfaced; a large batch does not flood.
  AC5 — event shape matches Layer-1 ({path, absolutePath, kind, operation}) so
        the frontend's path-keyed Map dedups a Layer-1/Layer-2 double-emit.

METHODOLOGY: mock ONLY the boundaries — the SessionRouter (unit states) and the
watchfiles batch. surface_injection logic + needs_human_review verdict run for
real (needs_human_review is itself fail-safe).
"""

import asyncio
from types import SimpleNamespace

import pytest

from core import surface_injection
from core.session_unit import SessionState


# ── Test doubles ────────────────────────────────────────────────────────
def _unit(session_id: str, state: SessionState, is_channel: bool = False,
          open_tools: bool = True):
    """A minimal SessionUnit stand-in exposing only what the resolver reads.

    open_tools defaults True: the in-flight gate (run_bfbbe0fd) requires the
    sole-streaming unit to have a tool open to surface LIVE, so the default
    keeps the pre-gate sole-streaming tests meaningful (a streaming session
    mid-tool-run). Set open_tools=False to exercise the gate's drop path.
    """
    return SimpleNamespace(
        session_id=session_id,
        state=state,
        is_channel_session=is_channel,
        has_open_tools=lambda: open_tools,
    )


class _FakeRouter:
    def __init__(self, units):
        self._units = units

    def list_units(self):
        return list(self._units)


@pytest.fixture(autouse=True)
def _clear_registry():
    """Each test starts with an empty injection registry."""
    surface_injection._reset_for_test()
    yield
    surface_injection._reset_for_test()


# ── AC2: sole-streaming attribution (prevents simultaneous ambiguity) ────
def test_sole_streaming_returns_single_streaming_session():
    router = _FakeRouter([
        _unit("s1", SessionState.STREAMING),
        _unit("s2", SessionState.IDLE),
        _unit("s3", SessionState.COLD),
    ])
    assert surface_injection.resolve_sole_streaming_session(router) == "s1"


def test_sole_streaming_none_when_two_stream():
    """AC2: 2+ streaming → NONE (never guess between tabs)."""
    router = _FakeRouter([
        _unit("s1", SessionState.STREAMING),
        _unit("s2", SessionState.STREAMING),
    ])
    assert surface_injection.resolve_sole_streaming_session(router) is None


def test_sole_streaming_none_when_zero_stream():
    router = _FakeRouter([_unit("s1", SessionState.IDLE)])
    assert surface_injection.resolve_sole_streaming_session(router) is None


def test_sole_streaming_ignores_channel_sessions():
    """A channel (Slack) session streaming must not count — desktop Canvas only."""
    router = _FakeRouter([
        _unit("s1", SessionState.STREAMING),
        _unit("chan", SessionState.STREAMING, is_channel=True),
    ])
    # Exactly one NON-channel streaming session → attribute to it.
    assert surface_injection.resolve_sole_streaming_session(router) == "s1"


# ── AC1: publish routes to the sole streaming session's queue ────────────
def test_publish_reaches_sole_streaming_queue():
    router = _FakeRouter([_unit("s1", SessionState.STREAMING)])
    q = surface_injection.register("s1")
    event = {"type": "file_changed", "path": "Knowledge/x.md",
             "absolutePath": "/ws/Knowledge/x.md", "kind": "knowledge",
             "operation": "written"}
    n = surface_injection.publish_file_event(event, router=router)
    assert n == 1
    assert q.get_nowait() == event


def test_publish_drops_when_no_registered_queue():
    """Sole session resolved but it has no open SSE queue → drop (0 delivered)."""
    router = _FakeRouter([_unit("s1", SessionState.STREAMING)])
    # no register()
    n = surface_injection.publish_file_event({"type": "file_changed"}, router=router)
    assert n == 0


def test_publish_drops_when_two_stream():
    """AC2 end-to-end: 2 streaming + both registered → NOTHING delivered."""
    router = _FakeRouter([
        _unit("s1", SessionState.STREAMING),
        _unit("s2", SessionState.STREAMING),
    ])
    q1 = surface_injection.register("s1")
    q2 = surface_injection.register("s2")
    n = surface_injection.publish_file_event({"type": "file_changed"}, router=router)
    assert n == 0
    assert q1.empty() and q2.empty()


# ── AC lifecycle: register/unregister isolation (no leak) ─────────────────
def test_unregister_removes_queue():
    surface_injection.register("s1")
    surface_injection.unregister("s1")
    router = _FakeRouter([_unit("s1", SessionState.STREAMING)])
    n = surface_injection.publish_file_event({"type": "file_changed"}, router=router)
    assert n == 0  # queue gone → nothing delivered, no KeyError


def test_drain_nowait_returns_all_pending():
    q = surface_injection.register("s1")
    q.put_nowait({"i": 1})
    q.put_nowait({"i": 2})
    drained = surface_injection.drain_nowait("s1")
    assert drained == [{"i": 1}, {"i": 2}]
    assert surface_injection.drain_nowait("s1") == []  # empty after drain


# ── AC4: watcher filters noise, builds correct events for content ─────────
@pytest.mark.asyncio
async def test_watcher_publishes_only_reviewworthy_content(tmp_path, monkeypatch):
    """AC4 + AC1: a batch with a content file + a gitignored/noise file →
    only the review-worthy content file is published as a file_changed event.
    """
    import subprocess

    from core import workspace_surface_watcher as wsw

    ws = tmp_path
    # SwarmWS is ALWAYS a git repo in production; needs_human_review_batch runs
    # `git check-ignore` and fails CLOSED outside a repo. Mirror prod reality.
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    (ws / "Reports").mkdir()
    content_file = ws / "Reports" / "out.md"
    content_file.write_text("# report")
    noise_file = ws / ".artifacts" / "runs"
    noise_file.mkdir(parents=True)
    noise = noise_file / "state.json"
    noise.write_text("{}")

    published = []

    def _fake_publish(event, router=None):
        published.append(event)
        return 1

    monkeypatch.setattr(wsw.surface_injection, "publish_file_event", _fake_publish)

    watcher = wsw.WorkspaceSurfaceWatcher(ws)
    # Drive one batch directly (bypass awatch): both files "changed".
    await watcher._handle_batch([str(content_file), str(noise)])

    paths = [e["path"] for e in published]
    # Report .md is a surfaceable knowledge/content doc; .artifacts/ is noise.
    assert any("out.md" in p for p in paths), f"content not surfaced: {paths}"
    assert not any("state.json" in p for p in paths), f"noise leaked: {paths}"
    # AC5: shape matches Layer-1.
    for e in published:
        assert set(e) >= {"type", "path", "absolutePath", "kind", "operation"}
        assert e["type"] == "file_changed"
        assert e["operation"] == "written"


# ═══════════════════════════════════════════════════════════════════════
# run_bfbbe0fd hardening — 5 fixes (#1 real-router, #3 final-drain,
# #4 separator, #5 in-flight-gate) + public accessor
# ═══════════════════════════════════════════════════════════════════════

# ── AC5 (fix5): in-flight-tool gate ──────────────────────────────────────
def test_inflight_gate_drops_when_no_open_tool():
    """A sole streaming session with NO open tool → NOT surfaced (a
    background-job write during an idle-tool chat is not mis-attributed)."""
    router = _FakeRouter([_unit("s1", SessionState.STREAMING, open_tools=False)])
    assert surface_injection.resolve_sole_streaming_session(router) is None


def test_inflight_gate_surfaces_when_tool_open():
    """A sole streaming session WITH an open tool (sub-agent/Bash/Write mid-run)
    → surfaced. This is the primary case (parent Agent-tool open across the
    whole sub-agent run)."""
    router = _FakeRouter([_unit("s1", SessionState.STREAMING, open_tools=True)])
    assert surface_injection.resolve_sole_streaming_session(router) == "s1"


def test_inflight_gate_publish_drops_idle_tool_session():
    """End-to-end: sole streaming but idle-tool → publish delivers to 0 queues."""
    router = _FakeRouter([_unit("s1", SessionState.STREAMING, open_tools=False)])
    surface_injection.register("s1")
    n = surface_injection.publish_file_event({"type": "file_changed"}, router=router)
    assert n == 0


# ── AC1 (fix1): REAL router — no _FakeRouter, drive the live singleton chain ──
def test_real_router_resolve_and_publish(monkeypatch):
    """Drive the REAL session_registry.session_router + a REAL SessionUnit —
    NOT _FakeRouter. Closes the RP45 gap: if the singleton lookup /
    list_units() / has_open_tools() contract regresses, THIS test goes red
    while the _FakeRouter tests stay green."""
    from core import session_registry
    from core.session_router import SessionRouter
    from core.session_unit import SessionState as RealState
    from core.session_unit import SessionUnit

    # A REAL SessionUnit (not _FakeRouter) — exercises the real .state /
    # .is_channel_session / .has_open_tools() / _open_tool_uses contract that
    # resolve_sole_streaming_session depends on. A real SessionRouter holds it
    # (prompt_builder is unused by list_units, so a stub is fine — the point is
    # the REAL list_units() + REAL SessionUnit, not the spawn machinery).
    router = SessionRouter(prompt_builder=SimpleNamespace())
    unit = SessionUnit("real-sess", "agent-x")
    unit.state = RealState.STREAMING
    unit.is_channel_session = False
    unit._open_tool_uses["tool-1"] = (0.0, "Agent")  # a tool is open (in-flight)
    router._units["real-sess"] = unit

    # publish_file_event with router=None → resolves via the real singleton.
    monkeypatch.setattr(session_registry, "session_router", router)
    q = surface_injection.register("real-sess")
    ev = {"type": "file_changed", "path": "Reports/x.md",
          "absolutePath": "/ws/Reports/x.md", "kind": "content",
          "operation": "written"}
    n = surface_injection.publish_file_event(ev)  # router=None → live singleton
    assert n == 1
    assert q.get_nowait() == ev

    # And the public accessor the gate depends on actually exists + works.
    assert unit.has_open_tools() is True
    unit._open_tool_uses.clear()
    assert unit.has_open_tools() is False


# ── AC4 (fix4): path separator normalization ─────────────────────────────
@pytest.mark.asyncio
async def test_watcher_normalizes_path_separators(tmp_path, monkeypatch):
    """Emitted event['path'] must use forward slashes on all platforms so the
    frontend path-key Map dedups a Layer-1/Layer-2 double-emit (Windows)."""
    import subprocess

    from core import workspace_surface_watcher as wsw

    ws = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
    sub = ws / "Reports"
    sub.mkdir()
    f = sub / "out.md"
    f.write_text("# r")

    published = []
    monkeypatch.setattr(wsw.surface_injection, "publish_file_event",
                        lambda ev, router=None: published.append(ev) or 1)

    watcher = wsw.WorkspaceSurfaceWatcher(ws)
    await watcher._handle_batch([str(f)])
    assert published, "content file should surface"
    for e in published:
        assert "\\" not in e["path"], f"path must be normalized: {e['path']!r}"


# ── AC3 (fix3): final drain delivers a post-last-message enqueue ─────────
@pytest.mark.asyncio
async def test_final_drain_delivers_trailing_event_through_real_generator():
    """AC3, driven through the REAL chat_stream message_generator (not
    drain_nowait in isolation — RP47). run_conversation yields ONE message, and
    a watcher event is enqueued AFTER that (i.e. after the last in-loop drain,
    simulating an end-of-turn write). The chat.py FINAL DRAIN (after the
    async-for, before the excepts) must surface it in the response body.

    Mutation: delete the final-drain block in chat.py → the trailing event is
    dropped by the finally's unregister → this test goes RED. (The prior version
    only exercised drain_nowait() in isolation and would stay GREEN on that
    regression — the exact test-theater this run exists to eliminate.)
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    import routers.chat as chat_mod

    SID = "final-drain-sess"
    TRAILING = {"type": "file_changed", "path": "Reports/late.md",
                "absolutePath": "/ws/Reports/late.md", "kind": "content",
                "operation": "written"}

    async def fake_run_conversation(**kwargs):
        # One normal message → the in-loop drain runs (queue still empty here).
        yield {"type": "text", "sessionId": SID, "content": "hi"}
        # A watcher write lands AFTER the last message, BEFORE the loop ends.
        surface_injection._QUEUES[SID].put_nowait(TRAILING)
        # loop ends normally → chat.py final drain must catch TRAILING.

    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={
        "agent_id": "default", "message": "hi", "session_id": SID,
        "enable_skills": False, "enable_mcp": False,
    })
    mock_router = MagicMock()
    mock_router.run_conversation = fake_run_conversation
    mock_router.get_unit = MagicMock(return_value=None)

    seen = []
    with patch.object(chat_mod, "_get_router", return_value=mock_router), \
         patch.object(chat_mod, "agent_exists", new_callable=AsyncMock, return_value=True):
        resp = await chat_mod.chat_stream(mock_request)
        async for chunk in resp.body_iterator:
            seen.append(chunk)

    body = "".join(c if isinstance(c, str) else c.decode() for c in seen)
    assert "Reports/late.md" in body, (
        "the trailing (post-last-message) watcher event must be surfaced by the "
        f"chat.py final drain; body did not contain it: {body!r}")
