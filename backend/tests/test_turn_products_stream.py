"""The orchestrator inserts ``turn_product`` blocks after the call that made them
(design docs/design/2026-10-26-session-products.md §2).

Driven through the real ``_read_formatted_response`` with the leaf boundaries stubbed
(test_orchestrator_surface_wiring's harness). Properties:
- a successful Write of a product file → ``file_changed`` then ONE ``assistant``
  event carrying the ``turn_product`` block, after the tool_result;
- a ``source`` file (code) → file_changed but no product block;
- a failed call → no block;
- a listed MCP call with a successful result → a remote block with its target; the
  same call failing → none.
"""
from __future__ import annotations

import asyncio

from tests.test_orchestrator_surface_wiring import (  # noqa: F401 — autouse fixture
    _drive, _isolate_permission_queue, _make_orchestrator, _make_result_message, _patch_boundaries,
    _tool_result, _write_tool_use,
)


def _products(events):
    out = []
    for e in events:
        if e.get("type") == "assistant":
            out += [b for b in e.get("content") or [] if b.get("type") == "turn_product"]
    return out


def _types(events):
    return [e.get("type") for e in events]


def test_product_write_yields_one_block_after_its_result(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="content")
    events, raised = asyncio.run(_drive(_make_orchestrator(), [
        _write_tool_use(file_path="Knowledge/q3.md"), _tool_result(), _make_result_message()]))
    assert raised is None
    blocks = _products(events)
    assert blocks == [{"type": "turn_product", "toolUseId": "tu-w1", "product": "file",
                       "path": "Knowledge/q3.md", "absolutePath": "/tmp/ws/Knowledge/q3.md", "fileKind": "content"}]
    t = _types(events)
    pi = next(i for i, e in enumerate(events) if e in [x for x in events if _products([x])])
    assert t.index("tool_result") < t.index("file_changed") < pi


def test_code_file_is_not_a_product(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="source")
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _write_tool_use(file_path="backend/x.py"), _tool_result(), _make_result_message()]))
    assert any(e.get("type") == "file_changed" for e in events)
    assert _products(events) == []


def test_failed_call_makes_no_product(monkeypatch):
    _patch_boundaries(monkeypatch, review_worthy=True, kind="content")
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _write_tool_use(file_path="Knowledge/q3.md"), _tool_result(is_error=True), _make_result_message()]))
    assert _products(events) == []


def _mcp_use(tid="tu-m1"):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    return AssistantMessage(content=[ToolUseBlock(id=tid, name="mcp__slack-mcp__post_message",
                                                  input={"channel": "#launch", "text": "shipped"})], model="m")


def test_listed_remote_call_yields_a_remote_block(monkeypatch):
    _patch_boundaries(monkeypatch)
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _mcp_use(), _tool_result("tu-m1"), _make_result_message()]))
    assert _products(events) == [{"type": "turn_product", "toolUseId": "tu-m1", "product": "remote",
                                  "tool": "mcp__slack-mcp__post_message", "action": "Posted to Slack",
                                  "target": "#launch"}]


def test_failed_remote_call_yields_nothing(monkeypatch):
    _patch_boundaries(monkeypatch)
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _mcp_use(), _tool_result("tu-m1", is_error=True), _make_result_message()]))
    assert _products(events) == []


def test_product_blocks_are_not_resume_text():
    from core.context_injector import _TOOL_ONLY_TYPES
    assert "turn_product" in _TOOL_ONLY_TYPES
