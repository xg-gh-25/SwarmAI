"""Integration tests for the turn-end surface-sweep WIRING in the streaming
orchestrator (run_a18d69f5 #3 — the guard paths shipped untested in run_4de279ca).

These drive the REAL `_read_formatted_response` generator against a scripted SDK
message stream and FORCE each orchestration guard path — the meta-lesson (GC17/R28,
5th+ recurrence): every guard/recovery branch needs a test that ENTERS it.

Guard paths covered:
  (a) a turn that issued a tool_use → the turn-end sweep RUNS (surfaces its delta)
  (b) a PURE-conversation turn (zero tool_use) → the sweep is SKIPPED (#1 dirty-flag)
  (c) the sweep raising/timing-out → the `result` event still yields (fail-safe)
  (d) the capped-summary row is emitted when the sweep reports dropped>0

CRITICAL patch target (Gate-1 finding 5): `_read_formatted_response` does a LOCAL
`from core.run_surface_changes import sweep_turn_delta` on every call, so a spy MUST
patch `core.run_surface_changes.sweep_turn_delta` (the source module), NOT the
orchestrator module — patching the latter no-ops and the test passes vacuously.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from core.session_unit import SessionState

# Reuse the proven harness (fake SDK client + parent stub) from the leak-guard suite.
from tests.test_tool_call_leak_guard import (
    _drive,
    _make_orchestrator,
    _make_result_message,
)


def _tool_use_message(tool_name: str = "Write", tool_input: dict | None = None):
    """An AssistantMessage carrying a single ToolUseBlock — marks the turn as
    'wrote' (any tool_use, per the #1 dirty-flag) so the sweep runs."""
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    return AssistantMessage(
        content=[ToolUseBlock(id="tu-1", name=tool_name, input=tool_input or {})],
        model="test-model",
    )


def _text_message(text: str = "hi"):
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model="test-model")


def _baseline_patch(monkeypatch, snapshot_calls: list):
    """Stub porcelain_snapshot (turn-start baseline) so the test needs no real git.
    Records each call so we can assert the baseline is ALWAYS captured."""
    def _snap(_root, _deadline=None):
        snapshot_calls.append(1)
        return set()
    monkeypatch.setattr("core.run_surface_changes.porcelain_snapshot", _snap)
    monkeypatch.setattr("core.project_registry.get_swarmws", lambda: "/tmp/ws")


def _run(coro_gen_events):
    return asyncio.get_event_loop().run_until_complete(coro_gen_events)


# ── (a) a tool_use turn RUNS the sweep ───────────────────────────────────────

def test_tool_use_turn_runs_sweep_and_surfaces_delta(monkeypatch):
    sweep_calls = []
    snap_calls = []
    _baseline_patch(monkeypatch, snap_calls)

    def _fake_sweep(_root, _baseline, _budget=None):
        sweep_calls.append(1)
        return ([{
            "type": "file_changed", "path": "Knowledge/Designs/x.md",
            "absolutePath": "/tmp/ws/Knowledge/Designs/x.md",
            "kind": "content", "operation": "written",
        }], 0)
    monkeypatch.setattr("core.run_surface_changes.sweep_turn_delta", _fake_sweep)

    orch = _make_orchestrator()
    events, raised = asyncio.get_event_loop().run_until_complete(
        _drive(orch, [_tool_use_message(), _make_result_message()])
    )
    assert raised is None
    assert sweep_calls == [1], "a tool_use turn MUST run the turn-end sweep"
    fc = [e for e in events if e.get("type") == "file_changed"]
    assert any(e["path"] == "Knowledge/Designs/x.md" for e in fc), "delta not surfaced"
    # the file_changed must precede the result (before the `result` yield — OT01)
    types_seq = [e["type"] for e in events]
    assert types_seq.index("file_changed") < types_seq.index("result")


# ── (b) a zero-tool turn SKIPS the sweep (#1 dirty-flag) ─────────────────────

def test_pure_conversation_turn_skips_sweep(monkeypatch):
    sweep_calls = []
    snap_calls = []
    _baseline_patch(monkeypatch, snap_calls)
    monkeypatch.setattr(
        "core.run_surface_changes.sweep_turn_delta",
        lambda *a, **k: sweep_calls.append(1) or ([], 0),
    )

    orch = _make_orchestrator()
    events, raised = asyncio.get_event_loop().run_until_complete(
        _drive(orch, [_text_message("just chatting"), _make_result_message()])
    )
    assert raised is None
    assert sweep_calls == [], "a zero-tool-use turn MUST skip the turn-end sweep"
    # baseline is STILL captured unconditionally (can't be conditioned on a flag
    # not yet known at turn-start — Gate-1 finding 3)
    assert snap_calls == [1], "baseline must be captured even on a zero-tool turn"
    assert any(e.get("type") == "result" for e in events)


# ── (c) sweep failure → result still yields (fail-safe) ──────────────────────

def test_sweep_failure_does_not_block_result(monkeypatch):
    snap_calls = []
    _baseline_patch(monkeypatch, snap_calls)

    def _boom(_root, _baseline, _budget=None):
        raise RuntimeError("git wedged")
    monkeypatch.setattr("core.run_surface_changes.sweep_turn_delta", _boom)

    orch = _make_orchestrator()
    events, raised = asyncio.get_event_loop().run_until_complete(
        _drive(orch, [_tool_use_message(), _make_result_message()])
    )
    assert raised is None, "a sweep exception must NOT propagate out of the turn"
    assert any(e.get("type") == "result" for e in events), "result must still yield"
    assert not [e for e in events if e.get("type") == "file_changed"]


# ── (d) capped-summary row emitted when dropped>0 ────────────────────────────

def test_capped_summary_row_emitted(monkeypatch):
    snap_calls = []
    _baseline_patch(monkeypatch, snap_calls)
    monkeypatch.setattr(
        "core.run_surface_changes.sweep_turn_delta",
        lambda *a, **k: ([], 7),  # zero events, 7 dropped by the cap
    )

    orch = _make_orchestrator()
    events, raised = asyncio.get_event_loop().run_until_complete(
        _drive(orch, [_tool_use_message(), _make_result_message()])
    )
    assert raised is None
    summary = [e for e in events if e.get("operation") == "summary"]
    assert len(summary) == 1, "a capped sweep must emit exactly one summary row"
    assert "+7 more" in summary[0]["path"]
