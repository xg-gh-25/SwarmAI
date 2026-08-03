"""Tests for StreamingOrchestrator._build_file_change_events (Cycle 3, run_e626e121).

The unified backend file-change emit: raw written path(s) → enriched SSE event(s)
{path, absolutePath, relevance, operation}, resolved once (cached), bookkeeping
dropped, and — since run_6ebe2d09 (Layer 1) — unresolvable written paths are
DROPPED (not fail-open-emitted with path==raw), because a just-written file always
exists on disk. Mocks the resolver boundary so the test is hermetic (no real walk).
"""
import asyncio


import core.streaming_orchestrator as so


def _orch():
    # _build_file_change_events only touches `self` for nothing — a bare instance
    # via __new__ avoids the full SessionUnit wiring.
    return so.StreamingOrchestrator.__new__(so.StreamingOrchestrator)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_deliverable_event_carries_resolved_absolute(monkeypatch):
    monkeypatch.setattr(
        "routers.workspace_api.resolve_path_to_physical",
        lambda raw, ws: {"relative": "Projects/SwarmAI/report.html",
                         "absolute": "/Users/gawan/repo/report.html"},
    )
    orch = _orch()
    cache: dict = {}
    events = _run(orch._build_file_change_events(["report.html"], cache))
    assert len(events) == 1
    ev = events[0]
    assert ev["type"] == "file_changed"
    assert ev["path"] == "Projects/SwarmAI/report.html"       # ws-relative display
    assert ev["absolutePath"] == "/Users/gawan/repo/report.html"  # physical (copy)
    assert ev["relevance"] == "deliverable"
    assert ev["operation"] == "written"


def test_bookkeeping_path_dropped(monkeypatch):
    # A .artifacts write must never surface — classifier drops it BEFORE resolution.
    called = {"n": 0}

    def _resolver(raw, ws):
        called["n"] += 1
        return {"relative": raw, "absolute": "/abs/" + raw}

    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical", _resolver)
    orch = _orch()
    events = _run(orch._build_file_change_events(
        ["Projects/SwarmAI/.artifacts/x.json"], {}))
    assert events == []
    assert called["n"] == 0  # bookkeeping never even pays resolution (perf)


def test_unresolvable_written_path_is_dropped(monkeypatch):
    # Layer 1 (run_6ebe2d09): a WRITTEN path that fails to resolve is NOT a real
    # file — a just-written deliverable always exists on disk, so resolve=None means
    # the "path" is garbage (e.g. `L4(top-right)` / `bottom` mis-parsed from a Bash
    # `>` operator). Emitting it produced a broken Canvas row → "Resource not found".
    # The old fail-open behavior (emit with path==raw) is REVERSED: drop the event.
    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical",
                        lambda raw, ws: None)
    orch = _orch()
    events = _run(orch._build_file_change_events(["L4(top-right)", "bottom", "ghost.html"], {}))
    assert events == []  # unresolvable written paths never reach the Canvas


def test_resolution_cached_per_turn(monkeypatch):
    calls = {"n": 0}

    def _resolver(raw, ws):
        calls["n"] += 1
        return {"relative": raw, "absolute": "/abs/" + raw}

    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical", _resolver)
    orch = _orch()
    cache: dict = {}
    _run(orch._build_file_change_events(["a.html"], cache))
    _run(orch._build_file_change_events(["a.html"], cache))  # same path, same turn
    assert calls["n"] == 1  # resolved once, second hit served from cache


def test_multiple_targets_from_one_bash(monkeypatch):
    monkeypatch.setattr("routers.workspace_api.resolve_path_to_physical",
                        lambda raw, ws: {"relative": raw, "absolute": "/abs/" + raw})
    orch = _orch()
    events = _run(orch._build_file_change_events(["a.md", "b.md"], {}))
    assert [e["path"] for e in events] == ["a.md", "b.md"]
    assert all(e["relevance"] == "deliverable" for e in events)


# ── run_0520a394: the agent's OWN Edit/Write must emit file_changed even though
#    its tool_result arrives in a UserMessage (Anthropic protocol), not the
#    AssistantMessage branch. These drive the REAL _read_formatted_response
#    generator end-to-end (AssistantMessage Edit ToolUse → UserMessage matching
#    ToolResult) — NOT the _build_file_change_events pure fn — so they cover the
#    exact wiring the prior RP45 tests missed. ─────────────────────────────────

import sys
from unittest.mock import MagicMock, patch


class _MRev:  # ── mock SDK message/block types (isinstance targets) ──
    class ResultMessage: pass
    class AssistantMessage: pass
    class SystemMessage: pass
    class TextBlock: pass
    class ToolUseBlock: pass
    class ToolResultBlock: pass
    class UserMessage: pass
    class StreamEvent: pass
    class ThinkingBlock: pass


def _patch_sdk():
    return patch.dict(sys.modules, {
        "claude_agent_sdk": MagicMock(**{
            "ResultMessage": _MRev.ResultMessage, "AssistantMessage": _MRev.AssistantMessage,
            "SystemMessage": _MRev.SystemMessage, "TextBlock": _MRev.TextBlock,
            "ToolUseBlock": _MRev.ToolUseBlock, "ToolResultBlock": _MRev.ToolResultBlock,
            "UserMessage": _MRev.UserMessage,
        }),
        "claude_agent_sdk.types": MagicMock(**{
            "StreamEvent": _MRev.StreamEvent, "ThinkingBlock": _MRev.ThinkingBlock,
        }),
    })


def _tool_use(tool_id, name, file_path):
    b = _MRev.ToolUseBlock()
    b.id = tool_id
    b.name = name
    b.input = {"file_path": file_path}
    return b


def _tool_result(tool_use_id, *, is_error=False):
    b = _MRev.ToolResultBlock()
    b.tool_use_id = tool_use_id
    b.content = "ok"
    b.is_error = is_error
    return b


def _assistant(blocks):
    m = _MRev.AssistantMessage()
    m.content = blocks
    m.model = "claude-opus-4-8"
    return m


def _user(content):
    m = _MRev.UserMessage()
    m.content = content
    return m


def _make_unit():
    from core.session_unit import SessionState, SessionUnit
    unit = SessionUnit(session_id="test-usermsg-fc", agent_id="default")
    unit._model_name = "claude-opus-4-8"
    unit._transition(SessionState.IDLE)
    unit._transition(SessionState.STREAMING)
    unit._content_emitted = True  # suppress zombie/empty-result guard on instant mock
    return unit


def _drive(unit, messages, monkeypatch):
    """Run the REAL _read_formatted_response over a mock SDK message stream,
    collect yielded events. Resolver monkeypatched (hermetic — no workspace walk)."""
    monkeypatch.setattr(
        "routers.workspace_api.resolve_path_to_physical",
        lambda raw, ws: {"relative": raw, "absolute": "/abs/" + raw},
    )

    async def _mock_response():
        for m in messages:
            yield m

    mock_client = MagicMock()
    mock_client.receive_response = MagicMock(return_value=_mock_response())
    unit._client = mock_client

    async def _collect():
        mock_pm = MagicMock()
        mock_pm.get_session_queue = MagicMock(return_value=asyncio.Queue())
        with patch("core.permission_manager.permission_manager", mock_pm):
            out = []
            async for ev in unit._read_formatted_response():
                out.append(ev)
            return out

    return _run(_collect())


def test_parent_edit_result_via_usermessage_emits_file_changed(monkeypatch):
    """AC1: Edit ToolUse (AssistantMessage) → matching ToolResult (UserMessage)
    → a file_changed event IS emitted (the bug: it wasn't, because the emit
    lived only in the AssistantMessage branch)."""
    with _patch_sdk():
        unit = _make_unit()
        result = _MRev.ResultMessage()
        result.is_error = False; result.subtype = None; result.result = ""
        result.error = ""; result.session_id = None; result.usage = None
        result.duration_ms = 1; result.total_cost_usd = 0.0; result.num_turns = 1
        msgs = [
            _assistant([_tool_use("tu_edit_1", "Edit", "Knowledge/x.md")]),
            _user([_tool_result("tu_edit_1")]),  # protocol: result in UserMessage
            result,
        ]
        events = _drive(unit, msgs, monkeypatch)
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert len(fc) == 1, f"expected 1 file_changed, got {[e.get('type') for e in events]}"
    assert fc[0]["operation"] == "written"
    assert fc[0]["path"] == "Knowledge/x.md"


def test_subagent_result_via_usermessage_does_not_emit(monkeypatch):
    """AC2: a UserMessage ToolResult whose id was NEVER a parent Edit ToolUse
    (i.e. a sub-agent Agent result — never in _pending_file_changes) emits NO
    file_changed."""
    with _patch_sdk():
        unit = _make_unit()
        result = _MRev.ResultMessage()
        result.is_error = False; result.subtype = None; result.result = ""
        result.error = ""; result.session_id = None; result.usage = None
        result.duration_ms = 1; result.total_cost_usd = 0.0; result.num_turns = 1
        msgs = [_user([_tool_result("tu_agent_99")]), result]  # no preceding Edit ToolUse
        events = _drive(unit, msgs, monkeypatch)
    assert [e for e in events if e.get("type") == "file_changed"] == []


def test_parent_edit_is_error_via_usermessage_does_not_emit(monkeypatch):
    """AC3: a FAILED Edit (is_error) must not surface, even though its id is in
    _pending_file_changes."""
    with _patch_sdk():
        unit = _make_unit()
        result = _MRev.ResultMessage()
        result.is_error = False; result.subtype = None; result.result = ""
        result.error = ""; result.session_id = None; result.usage = None
        result.duration_ms = 1; result.total_cost_usd = 0.0; result.num_turns = 1
        msgs = [
            _assistant([_tool_use("tu_edit_2", "Write", "Knowledge/y.md")]),
            _user([_tool_result("tu_edit_2", is_error=True)]),
            result,
        ]
        events = _drive(unit, msgs, monkeypatch)
    assert [e for e in events if e.get("type") == "file_changed"] == []
