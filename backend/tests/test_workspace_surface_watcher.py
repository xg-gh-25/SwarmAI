"""Tests for Layer-2 Canvas fs-watcher (author-agnostic live-surfacing).

WHAT IS TESTED
--------------
The Layer-2 workspace filesystem watcher surfaces file writes that Layer-1's
per-tool SSE emit CANNOT see — writes by sub-agents / CLI subprocesses / hooks,
which the Claude SDK filters out as sidechain messages (verified:
claude_agent_sdk/types.py:1600 "tool-use sidechain messages are filtered out").

Two units under test:
  1. surface_injection  — per-session injection queue registry + the
     sole-streaming-session attribution resolver (the bleed-proof gate).
  2. WorkspaceSurfaceWatcher — the watchfiles-based watcher that classifies a
     batch via needs_human_review_batch (off-loop) and publishes file_changed
     events to the sole streaming session.

KEY INVARIANTS (map to PLAN ACs)
  AC1 — a content write, with exactly ONE session streaming, reaches THAT
        session's queue as a file_changed(operation=written) event.
  AC2 — with 2+ sessions streaming, publish routes to NONE (no wall-clock /
        time-window attribution — the run_4de279ca cross-tab bleed made
        structurally impossible, not heuristically avoided).
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
def _unit(session_id: str, state: SessionState, is_channel: bool = False):
    """A minimal SessionUnit stand-in exposing only what the resolver reads."""
    return SimpleNamespace(
        session_id=session_id,
        state=state,
        is_channel_session=is_channel,
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


# ── AC2: sole-streaming attribution (the bleed-proof gate) ───────────────
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
