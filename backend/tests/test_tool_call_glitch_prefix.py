"""Tests for the tool-call glitch PREFIX shape (second shape of the leak glitch).

What is tested
--------------
1. ``detect_glitch_prefix`` / ``strip_glitch_prefix`` on real observed samples and
   on English / code / user-ish negatives.
2. ``leak_transcript.clean_transcript_leaks`` also removes the prefix word from
   assistant text blocks (and leaves user messages alone).
3. Orchestrator: the word is stripped from the yielded ``assistant`` event; at
   the next tool-result boundary with nothing open, the restart marker is raised
   with ``kind=prefix``; gated like the leak restart; if the turn ends first, the
   transcript is flagged for cleaning before the next send().
4. ``RetryManager._handle_leak_poison_restart(kind="prefix")`` sends the glitch
   continuation prompt (the tool call ran — it must not say it failed).
5. ``SessionUnit._clean_poisoned_transcript`` kills a warm process BEFORE
   cleaning and keeps the --resume identity.

Key invariants
--------------
- INV1: only the glitch word goes; the rest of the line and all other text stay.
- INV2: the restart never fires while a tool is open, after a user Stop, outside
  the primary stream, or without an SDK session id.
- INV3: the restart and the deferred clean are one-shot.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core.leak_transcript import clean_transcript_leaks
from core.session_utils import (
    LEAK_RESTART_KIND_PREFIX,
    LEAK_RESTART_MARKER,
    detect_glitch_prefix,
    strip_glitch_prefix,
)
from tests.test_tool_call_leak_guard import (
    _drive,
    _make_orchestrator,
    _make_result_message,
)
from tests.test_tool_call_leak_restart import (
    SID,
    _asst,
    _collect,
    _opts,
    _texts,
    _unit_with_boundary_mocks,
    _write_transcript,
)

# ── 1. detector / strip ─────────────────────────────────────────────────────

POSITIVE = [
    ("count 显示 2 paused,都是无关历史", "显示 2 paused,都是无关历史"),
    ("core：第 0 节把整文件级", "第 0 节把整文件级"),
    ("core GREEN change 3:加 import", "GREEN change 3:加 import"),
    ("core SMOKE:", "SMOKE:"),
    ("core", ""),
    ("先 stage。\n\ncall", "先 stage。"),
    ("跑测试。\n\ncore 跑 registry 测试。\n\ncore", "跑测试。\n\n跑 registry 测试。"),
]
NEGATIVE = [
    "count the files first",
    "Count 一下",
    "discount 显示",
    "the core 模块",
    "core/ 目录下",
    "`core 跑` is inline code",
    "```\ncore 跑\n```",
]


@pytest.mark.parametrize("text,expected", POSITIVE)
def test_strip_removes_only_the_glitch_word(text, expected):
    assert detect_glitch_prefix(text)
    assert strip_glitch_prefix(text) == expected
    assert strip_glitch_prefix(expected) == expected  # idempotent


@pytest.mark.parametrize("text", NEGATIVE)
def test_negatives_untouched(text):
    assert not detect_glitch_prefix(text)
    assert strip_glitch_prefix(text) == text


# ── 2. transcript cleaner ───────────────────────────────────────────────────

def test_clean_transcript_strips_prefix_in_assistant_only(tmp_path, monkeypatch):
    user = {"type": "user", "uuid": "u1",
            "message": {"role": "user", "content": [{"type": "text", "text": "core 帮我看下"}]}}
    p = _write_transcript(tmp_path, monkeypatch, [
        _asst("先跑测试。\n\ncore 跑 registry 测试。", "m1"),
        _asst("core", "m2"),
        user,
    ])
    assert clean_transcript_leaks(p) == 2
    assert _texts(p) == [
        ("assistant", "先跑测试。\n\n跑 registry 测试。"),
        ("assistant", "(retrying)"),  # a bare word becomes the non-empty filler
        ("user", "core 帮我看下"),
    ]


# ── 3. orchestrator ─────────────────────────────────────────────────────────

def _glitch_call(tool_id="toolu_g1", text="先查状态。\n\ncore 查状态。"):
    from claude_agent_sdk import AssistantMessage, TextBlock, ToolUseBlock

    return AssistantMessage(content=[
        TextBlock(text=text),
        ToolUseBlock(id=tool_id, name="Bash", input={"command": "ls"}),
    ], model="test-model")


def _tool_result(tool_id="toolu_g1"):
    from claude_agent_sdk import ToolResultBlock, UserMessage

    return UserMessage(content=[
        ToolResultBlock(tool_use_id=tool_id, content="ok", is_error=False),
    ])


def _orch(eligible=True):
    orch = _make_orchestrator()
    orch._parent._streaming_start_time = 0.0
    orch._parent._leak_restart_eligible = eligible
    return orch


def _shown(events):
    return [b["text"] for ev in events if ev.get("type") == "assistant"
            for b in ev["content"] if b.get("type") == "text"]


def test_prefix_stripped_and_restart_at_tool_result_boundary():
    orch = _orch()
    events, raised = asyncio.run(_drive(
        orch, [_glitch_call(), _tool_result(), _make_result_message()],
    ))
    assert _shown(events) == ["先查状态。\n\n查状态。"]
    assert raised is not None
    assert LEAK_RESTART_MARKER in str(raised)
    assert LEAK_RESTART_KIND_PREFIX in str(raised)
    assert orch._parent._leak_restart_eligible is False  # one-shot
    assert not [ev for ev in events if ev.get("type") == "result"]


@pytest.mark.parametrize("setup", ["not_eligible", "user_stopped", "other_tool_open",
                                   "no_sid", "question_pending"])
def test_restart_gated_and_deferred_instead(setup):
    orch = _orch()
    p = orch._parent
    if setup == "not_eligible":
        p._leak_restart_eligible = False
    elif setup == "user_stopped":
        p._user_stopped_current_turn = True
    elif setup == "other_tool_open":
        p._open_tool_uses = {"toolu_other": (0.0, "Bash")}
    elif setup == "no_sid":
        p._sdk_session_id = None
    elif setup == "question_pending":
        p._pending_tool_use_id = "toolu_ask"
    events, raised = asyncio.run(_drive(
        orch, [_glitch_call(), _tool_result(), _make_result_message()],
    ))
    assert raised is None
    assert _shown(events) == ["先查状态。\n\n查状态。"]
    assert getattr(p, "_transcript_poisoned", False) is True


def test_glitch_in_final_answer_flags_transcript():
    from tests.test_tool_call_leak_guard import _make_assistant_message

    orch = _orch()
    events, raised = asyncio.run(_drive(
        orch, [_make_assistant_message("完成了。\n\ncore"), _make_result_message()],
    ))
    assert raised is None
    assert _shown(events) == ["完成了。"]
    assert orch._parent._transcript_poisoned is True


def test_subagent_text_is_not_the_main_reply_and_does_not_arm():
    # A sub-agent's text (parent_tool_use_id set) is not shown as the main
    # reply at all (2026-10-18, sub-agent steps nest under their Agent step),
    # so its glitch word can neither show nor arm the main-thread restart.
    from claude_agent_sdk import AssistantMessage, TextBlock

    orch = _orch()
    sub = AssistantMessage(content=[TextBlock(text="core 跑子任务")], model="m",
                           parent_tool_use_id="toolu_agent")
    events, raised = asyncio.run(_drive(orch, [sub, _make_result_message()]))
    assert raised is None
    assert _shown(events) == []
    assert getattr(orch._parent, "_transcript_poisoned", False) is False


def test_clean_text_neither_restarts_nor_flags():
    orch = _orch()
    events, raised = asyncio.run(_drive(
        orch, [_glitch_call(text="先查状态。"), _tool_result(), _make_result_message()],
    ))
    assert raised is None
    assert getattr(orch._parent, "_transcript_poisoned", False) is False


# ── 4. restart handler with kind=prefix ─────────────────────────────────────

async def test_prefix_restart_sends_continue_prompt_and_cleans(tmp_path, monkeypatch):
    from core.streaming_orchestrator import StreamingOrchestrator

    p = _write_transcript(tmp_path, monkeypatch, [_asst("core 查状态。")])
    calls = []
    u = _unit_with_boundary_mocks(calls, stream_events=[{"type": "assistant", "content": []}])
    u._transcript_poisoned = True
    events = await _collect(
        u._retry_manager._handle_leak_poison_restart(_opts(), None, kind="prefix")
    )
    assert calls[0] == ("crash", False)
    assert calls[1] == ("spawn", SID)
    assert calls[2] == ("query", StreamingOrchestrator._GLITCH_CONTINUATION_PROMPT)
    assert "not executed" not in StreamingOrchestrator._GLITCH_CONTINUATION_PROMPT
    assert not detect_glitch_prefix(StreamingOrchestrator._GLITCH_CONTINUATION_PROMPT)
    assert events[-1] == {"_recovered": True}
    assert u._transcript_poisoned is False
    assert _texts(p) == [("assistant", "查状态。")]


# ── 5. deferred clean before the next send() ────────────────────────────────

async def test_clean_poisoned_transcript_kills_first_then_cleans(tmp_path, monkeypatch):
    from core.session_unit import SessionState

    p = _write_transcript(tmp_path, monkeypatch, [_asst("core 查状态。")])
    calls = []
    u = _unit_with_boundary_mocks(calls)
    u.state = SessionState.IDLE
    u._transcript_poisoned = True

    async def _checkpoint(trigger, **k):
        calls.append(("checkpoint", trigger))

    u._arm_recovery_checkpoint = _checkpoint
    order = []
    real_clean = clean_transcript_leaks

    def _spy_clean(path):
        order.append([c[0] for c in calls])
        return real_clean(path)

    monkeypatch.setattr("core.leak_transcript.clean_transcript_leaks", _spy_clean)

    await u._clean_poisoned_transcript()

    assert order == [["checkpoint", "crash"]], "kill BEFORE the file is cleaned"
    assert ("crash", False) in calls, "identity kept for --resume"
    assert u._sdk_session_id == SID
    assert u._transcript_poisoned is False
    assert _texts(p) == [("assistant", "查状态。")]


async def test_clean_poisoned_transcript_without_identity_is_noop(tmp_path, monkeypatch):
    calls = []
    u = _unit_with_boundary_mocks(calls)
    u._sdk_session_id = None
    u._transcript_poisoned = True
    await u._clean_poisoned_transcript()
    assert calls == []
    assert u._transcript_poisoned is False


def test_restart_marker_with_kind_is_not_retriable():
    from core.session_utils import _is_retriable_error

    assert not _is_retriable_error(
        f"{LEAK_RESTART_MARKER} {LEAK_RESTART_KIND_PREFIX} (session_id=x)"
    )
