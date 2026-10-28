"""jobs.bedrock.structured() and text(): the one entry for a model's answer
(design docs/design/2026-10-28-structured-model-output.md §5.1).

Mock boundary: the boto client (get_client / build_timeout_client). The functions
under test and converse_with_retry run for real.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from jobs import bedrock as b  # noqa: E402

VERDICT = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["passed", "failed"]},
        "confidence": {"type": "number"},
        "notes": {"type": "string"},
    },
    "required": ["reasoning", "verdict", "confidence", "notes"],
}


class _Client:
    def __init__(self, resp):
        self.resp = resp
        self.calls: list[dict] = []

    def converse(self, **kw):
        self.calls.append(kw)
        return self.resp


def _tool_reply(inp, name="answer", stop="tool_use"):
    return {"stopReason": stop, "usage": {"inputTokens": 10, "outputTokens": 5},
            "output": {"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": name, "input": inp}}]}}}


def _text_reply(text, stop="end_turn"):
    return {"stopReason": stop, "usage": {"inputTokens": 10, "outputTokens": 5},
            "output": {"message": {"content": [{"text": text}]}}}


@pytest.fixture
def client(monkeypatch):
    holder = {}

    def _get(**_kw):
        return holder["c"]
    monkeypatch.setattr(b, "get_client", _get)
    monkeypatch.setattr(b, "build_timeout_client", _get)

    def _set(resp):
        holder["c"] = _Client(resp)
        return holder["c"]
    return _set


GOOD = {"reasoning": "checked each", "verdict": "failed", "confidence": "0.95", "notes": "4/4"}


def test_an_answer_comes_back_checked_and_typed(client) -> None:
    c = client(_tool_reply(GOOD))
    got = b.structured("judge this", schema=VERDICT, model_id="m", max_tokens=2000,
                       system="be precise", read_timeout=30)
    assert got == {**GOOD, "confidence": 0.95}
    (kw,) = c.calls
    assert kw["toolConfig"]["toolChoice"] == {"tool": {"name": "answer"}}
    (tool,) = kw["toolConfig"]["tools"]
    assert tool["toolSpec"]["inputSchema"]["json"] is VERDICT
    assert kw["system"] == [{"text": "be precise"}]
    assert kw["inferenceConfig"] == {"maxTokens": 2000, "temperature": 0.0}


def test_a_reply_cut_at_max_tokens_is_truncated_not_broken(client) -> None:
    client(_tool_reply({}, stop="max_tokens"))
    with pytest.raises(b.StructuredOutputError) as e:
        b.structured("p", schema=VERDICT, model_id="m", max_tokens=400)
    assert e.value.kind == "truncated" and "max_tokens=400" in str(e.value)


@pytest.mark.parametrize("resp", [
    _text_reply('{"verdict": "passed"}'),            # answered in text instead
    _tool_reply(GOOD, name="something_else"),
    {"stopReason": "tool_use", "output": {"message": {"content": [{"toolUse": {"name": "answer", "input": "x"}}]}}},
    {},
])
def test_no_call_to_the_answer_tool_is_missing(client, resp) -> None:
    client(resp)
    with pytest.raises(b.StructuredOutputError) as e:
        b.structured("p", schema=VERDICT, model_id="m", max_tokens=400)
    assert e.value.kind == "missing"


@pytest.mark.parametrize("inp, where", [
    ({**GOOD, "verdict": "maybe"}, "$.verdict"),
    ({k: v for k, v in GOOD.items() if k != "notes"}, "missing ['notes']"),
    ({**GOOD, "confidence": "high"}, "$.confidence"),
    ({**GOOD, "notes": 3}, "$.notes"),
])
def test_an_answer_off_its_schema_is_invalid(client, inp, where) -> None:
    client(_tool_reply(inp))
    with pytest.raises(b.StructuredOutputError) as e:
        b.structured("p", schema=VERDICT, model_id="m", max_tokens=400)
    assert e.value.kind == "invalid" and where in e.value.detail
    assert '"verdict"' in e.value.raw          # the reply is kept for diagnosis


def test_arrays_and_nested_objects_are_checked(client) -> None:
    schema = {"type": "object", "required": ["items"], "properties": {
        "items": {"type": "array", "items": {"type": "object", "required": ["n"],
                                             "properties": {"n": {"type": "integer"}}}}}}
    client(_tool_reply({"items": [{"n": "2"}, {"n": 3}]}))
    assert b.structured("p", schema=schema, model_id="m", max_tokens=9) == {"items": [{"n": 2}, {"n": 3}]}
    client(_tool_reply({"items": [{"n": 1}, {}]}))
    with pytest.raises(b.StructuredOutputError, match=r"\$\.items\[1\]"):
        b.structured("p", schema=schema, model_id="m", max_tokens=9)


@pytest.mark.parametrize("schema", [
    {"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}},
    {"type": "object", "additionalProperties": False},
    {"type": "array", "items": {"type": "string"}},
    {"type": "thing"},
])
def test_a_schema_outside_the_supported_subset_is_refused_before_any_call(client, schema) -> None:
    c = client(_tool_reply({}))
    with pytest.raises(ValueError):
        b.structured("p", schema=schema, model_id="m", max_tokens=9)
    assert c.calls == []


def test_text_returns_the_whole_reply(client) -> None:
    c = client({"stopReason": "end_turn", "output": {"message": {"content": [{"text": "a "}, {"text": "b"}]}}})
    assert b.text("p", model_id="m", max_tokens=50) == "a b"
    (kw,) = c.calls
    assert "toolConfig" not in kw and "system" not in kw


def test_text_cut_at_max_tokens_is_never_returned_as_complete(client) -> None:
    client(_text_reply("half a rule that", stop="max_tokens"))
    with pytest.raises(b.StructuredOutputError) as e:
        b.text("p", model_id="m", max_tokens=400)
    assert e.value.kind == "truncated" and e.value.raw == "half a rule that"


def test_an_empty_text_reply_is_missing(client) -> None:
    client(_text_reply("  "))
    with pytest.raises(b.StructuredOutputError) as e:
        b.text("p", model_id="m", max_tokens=400)
    assert e.value.kind == "missing"


def test_usage_is_recorded_only_when_asked(client) -> None:
    client(_text_reply("ok"))
    with patch.object(b, "_record_usage_sync") as rec:
        b.text("p", model_id="m", max_tokens=5)
        assert rec.call_count == 0
        b.text("p", model_id="m", max_tokens=5, record_usage=True)
        rec.assert_called_once_with(input_tokens=10, output_tokens=5, model="m")


def test_an_auth_error_is_retried_once_through_the_same_entry(monkeypatch) -> None:
    calls = []

    class _Flaky:
        def converse(self, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise Exception("Error when retrieving credentials: token expired")
            return _text_reply("ok")
    monkeypatch.setattr(b, "build_timeout_client", lambda **_kw: _Flaky())
    assert b.text("p", model_id="m", max_tokens=5, read_timeout=30) == "ok"
    assert len(calls) == 2


def test_the_error_kinds_are_closed() -> None:
    with pytest.raises(ValueError):
        b.StructuredOutputError("weird")
    assert len(b.StructuredOutputError("invalid", "x" * 5000).raw) == b._RAW_KEEP


@pytest.mark.parametrize("bad", ["NaN", "nan", "Infinity", "-inf", "1e400", float("nan"), float("inf"), "0x10", "1_0"])
def test_a_number_must_be_finite_decimal(client, bad) -> None:
    # A NaN score would read as a perfect one (min(1.0, nan) is 1.0).
    client(_tool_reply({**GOOD, "confidence": bad}))
    with pytest.raises(b.StructuredOutputError) as e:
        b.structured("p", schema=VERDICT, model_id="m", max_tokens=9)
    assert e.value.kind == "invalid" and "$.confidence" in e.value.detail


@pytest.mark.parametrize("given, want", [("7", 7), (" 7 ", 7), (7.0, 7), (7, 7)])
def test_an_integer_may_be_written_as_text_or_a_whole_float(client, given, want) -> None:
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    client(_tool_reply({"n": given}))
    got = b.structured("p", schema=schema, model_id="m", max_tokens=9)["n"]
    assert got == want and type(got) is int


@pytest.mark.parametrize("given", ["7.5", "1_000", 7.5, True])
def test_a_non_integer_is_refused(client, given) -> None:
    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}
    client(_tool_reply({"n": given}))
    with pytest.raises(b.StructuredOutputError):
        b.structured("p", schema=schema, model_id="m", max_tokens=9)


@pytest.mark.parametrize("schema", [
    {"type": ["string", "null"]},
    {"type": "object", "required": "ab"},
    {"type": "object", "properties": [{"type": "string"}]},
    {"type": "string", "enum": "ab"},
])
def test_a_malformed_schema_is_a_value_error(schema) -> None:
    with pytest.raises(ValueError):
        b._check_schema(schema)


def test_a_model_that_rejects_a_forced_choice_is_told_to_use_the_tool(client) -> None:
    # Measured 2026-10-28: claude-opus-5-5 400s on toolChoice "tool"; it answers through
    # the tool under "auto" (after its thinking or some text, both ignored here).
    c = client({"stopReason": "tool_use", "output": {"message": {"content": [
        {"reasoningContent": {"reasoningText": {"text": "thinking"}}},
        {"text": "Let me check."},
        {"toolUse": {"toolUseId": "t", "name": "answer", "input": GOOD}}]}}})
    got = b.structured("p", schema=VERDICT, model_id="us.anthropic.claude-opus-5-5",
                       max_tokens=9, system="be precise")
    assert got["verdict"] == "failed"
    (kw,) = c.calls
    assert kw["toolConfig"]["toolChoice"] == {"auto": {}}
    assert kw["system"] == [{"text": "be precise\n\nAnswer only by calling the answer tool."}]
    assert "temperature" not in kw["inferenceConfig"]   # opus 4.7+ reject one


@pytest.mark.parametrize("model_id", ["eu.anthropic.claude-opus-5-5", "apac.anthropic.claude-opus-5-5"])
def test_any_geography_of_a_model_gets_its_capabilities(client, model_id) -> None:
    # A deployment's bedrock_model_map may name any geography; the checks read the full ID.
    c = client(_tool_reply(GOOD))
    b.structured("p", schema=VERDICT, model_id=model_id, max_tokens=9, temperature=0.2)
    (kw,) = c.calls
    assert kw["toolConfig"]["toolChoice"] == {"auto": {}}
    assert "temperature" not in kw["inferenceConfig"]


def test_a_model_that_accepts_temperature_gets_it(client) -> None:
    c = client(_tool_reply(GOOD))
    b.structured("p", schema=VERDICT, model_id="us.anthropic.claude-sonnet-4-6", max_tokens=9,
                 temperature=0.2)
    assert c.calls[0]["inferenceConfig"]["temperature"] == 0.2
    assert c.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "answer"}}


@pytest.mark.parametrize("model, forced", [
    ("claude-opus-5-5", False), ("us.anthropic.claude-opus-5-5", False),
    ("claude-opus-5", True), ("claude-opus-4-8", True), ("claude-sonnet-4-6", True), (None, True),
])
def test_forced_tool_choice_follows_the_measured_table(model, forced) -> None:
    from model_registry import supports_forced_tool_choice
    assert supports_forced_tool_choice(model) is forced
