"""The "Save to Memory" extraction asks through jobs.bedrock.structured() (design
docs/design/2026-10-28-structured-model-output.md): an unusable reply is reported as
such, never as "Nothing new to save"; an empty answer is a real "nothing to save".

Mock boundaries: the bedrock-runtime client (jobs.bedrock.build_timeout_client) and
the message store (the ``database`` module).
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import patch

import pytest

from core import memory_extractor as me


class _Client:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def converse(self, **kw):
        self.calls.append(kw)
        return self.reply


def _tool(answer, stop="tool_use"):
    return {"stopReason": stop, "output": {"message": {"content": [
        {"toolUse": {"toolUseId": "t", "name": "save", "input": answer}}]}}}


EMPTY = {k: [] for k in me._SECTIONS}


@pytest.fixture
def run(tmp_path, monkeypatch):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} about the plan"}
            for i in range(max(me.MIN_MESSAGES_FOR_EXTRACTION, 4))]

    class _Msgs:
        async def list_by_session(self, _sid):
            return msgs
    monkeypatch.setitem(sys.modules, "database", types.SimpleNamespace(db=types.SimpleNamespace(messages=_Msgs())))
    (tmp_path / ".context").mkdir()
    (tmp_path / ".context" / "MEMORY.md").write_text("# Memory\n")

    async def _run_in(_pool, fn, *a):
        return fn(*a)

    def _go(reply, error=None):
        client = _Client(reply)
        boundary = (patch("jobs.bedrock.build_timeout_client", side_effect=error) if error
                    else patch("jobs.bedrock.build_timeout_client", return_value=client))
        with boundary, \
             patch("core.executors.run_in", _run_in):
            res = asyncio.run(me.extract_and_save("s1", workspace_path=tmp_path,
                                                  config={"aws_region": "us-west-2",
                                                          "memory_extraction_model": "m-x"}))
        return res, client
    return _go


@pytest.mark.parametrize("reply, kind", [
    (_tool({}, stop="max_tokens"), "truncated"),
    ({"stopReason": "end_turn", "output": {"message": {"content": [{"text": "{}"}]}}}, "missing"),
    (_tool({**EMPTY, "lessons_learned": "not a list"}), "invalid"),
])
def test_an_unusable_reply_is_not_nothing_to_save(run, reply, kind):
    res, _ = run(reply)
    assert res.error == f"LLM extraction reply {kind}" and res.total_saved == 0


def test_an_empty_answer_is_nothing_to_save(run):
    res, client = run(_tool({**EMPTY, "open_threads": ["  "]}))
    assert res.error == "Nothing new to save"
    (kw,) = client.calls
    assert kw["modelId"] == "m-x" and kw["toolConfig"]["toolChoice"] == {"tool": {"name": "save"}}


def test_a_bedrock_error_is_reported_as_a_failure(run):
    res, _ = run(None, error=RuntimeError("bedrock down"))
    assert res.error == "LLM extraction failed: RuntimeError"


def test_sections_drop_blank_entries():
    with patch("jobs.bedrock.build_timeout_client",
               return_value=_Client(_tool({**EMPTY, "key_decisions": ["use X", " "]}))):
        got = me._extract_sections("p", {})
    assert got == {**EMPTY, "key_decisions": ["use X"]}
