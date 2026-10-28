"""CDF source extractor through jobs.bedrock.structured() (extract_llm, design §5.4 r16.14).

What is tested:
- ``flat_unit_schema`` is derived from contracts_v2 (every field of every allowed kind,
  nothing else), passes structured()'s schema check, and requires only what every
  allowed kind requires.
- ``BedrockExtractor.extract`` asks through structured() with the tool name, schema and
  its own budget, strips other kinds' fields and nulls, and records usage.
- A truncated / missing / invalid reply is UntypedOutput, and the attempt is failed.
- Through ``extract_units`` a unit carrying another kind's field still validates.
"""
from __future__ import annotations

import pytest

from core.ingestion import contracts_v2
from core.ingestion.prompts import UntypedOutput
from core.ingestion.sources import extract_llm
from jobs.bedrock import StructuredOutputError, _check_schema

KIND_SETS = [("reference",), ("cognition",), ("cognition", "event", "gap", "reference"),
             ("cognition", "gap"), ("event",)]


def _unit_schema(kinds):
    return extract_llm.flat_unit_schema(kinds)["properties"]["units"]["items"]


@pytest.mark.parametrize("kinds", KIND_SETS)
def test_flat_schema_is_derived_and_accepted(kinds) -> None:
    schema = extract_llm.flat_unit_schema(kinds)
    _check_schema(schema)
    unit = schema["properties"]["units"]["items"]
    defs = extract_llm._UNIT_DEFS()
    expected = set().union(*(set(defs[k]["properties"]) for k in kinds))
    assert set(unit["properties"]) == expected
    assert unit["properties"]["kind"]["enum"] == list(kinds)
    always = set.intersection(*(set(defs[k]["required"]) for k in kinds))
    assert set(unit["required"]) == always | {"kind"}


def test_scope_rule_reaches_the_model() -> None:
    """_valid_scope is a validator the JSON schema cannot carry; its rule is in words."""
    for kinds in KIND_SETS:
        assert contracts_v2.SCOPE_RULE in _unit_schema(kinds)["properties"]["scope"]["description"]
    for value in ("user", "agent", "world", "project:SwarmAI"):
        assert contracts_v2._valid_scope(value) and value.split(":")[0] in contracts_v2.SCOPE_RULE
    assert not contracts_v2._valid_scope("project")


@pytest.mark.parametrize("kinds", KIND_SETS)
def test_validator_rules_reach_the_model(kinds) -> None:
    text = _unit_schema(kinds)["properties"]["kind"]["description"]
    for kind in kinds:
        assert all(rule in text for rule in contracts_v2.UNIT_RULES[kind])
    others = set(contracts_v2.UNIT_RULES) - set(kinds)
    assert not any(rule in text for kind in others for rule in contracts_v2.UNIT_RULES[kind])


def test_flat_schema_refuses_unknown_kinds() -> None:
    with pytest.raises(ValueError):
        extract_llm.flat_unit_schema(("opinion",))


def _good_cognition(anchor="event:e1"):
    return {"kind": "cognition", "type": "decision", "scope": "world",
            "title": "Use one write path", "what": "Every CDF write goes through the worker.",
            "why": "Two writers raced on MEMORY.md.", "rejected_alternatives": "none",
            "evidence": [anchor], "how": None, "body": "belongs to reference units"}


def test_extract_asks_through_structured_and_strips(monkeypatch) -> None:
    calls = {}
    recorded = []

    def fake(prompt, **kw):
        calls.update(kw)
        kw["usage"].update(input_tokens=10, output_tokens=5)
        return {"units": [_good_cognition()]}

    monkeypatch.setattr(extract_llm, "structured", fake)
    monkeypatch.setattr(extract_llm.llm_usage, "record", recorded.append)
    result = extract_llm.BedrockExtractor(("cognition", "reference")).extract("p")
    assert calls["name"] == extract_llm.TOOL_NAME
    assert calls["schema"] == extract_llm.flat_unit_schema(("cognition", "reference"))
    assert calls["max_tokens"] == 16000 and calls["read_timeout"] == 300
    assert result.value == [{k: v for k, v in _good_cognition().items()
                             if k not in ("how", "body")}]
    assert recorded == [{"input_tokens": 10, "output_tokens": 5}]


@pytest.mark.parametrize("kind", StructuredOutputError.KINDS)
def test_bad_reply_is_untyped(monkeypatch, kind) -> None:
    def fake(prompt, **kw):
        raise StructuredOutputError(kind, raw="cut", detail="d")

    monkeypatch.setattr(extract_llm, "structured", fake)
    with pytest.raises(UntypedOutput) as info:
        extract_llm.BedrockExtractor(("reference",)).extract("p")
    assert kind in str(info.value) and info.value.raw == "cut"


def test_extract_units_default_extractor_keeps_a_valid_unit(monkeypatch) -> None:
    monkeypatch.setattr(extract_llm, "structured",
                        lambda prompt, **kw: {"units": [_good_cognition()]})
    result = extract_llm.extract_units("S2", "data", ("event:e1",),
                                       allowed_kinds=("cognition", "reference"))
    assert [u.kind for u in result.units] == ["cognition"] and not result.invalid_units
    assert isinstance(result.units[0], contracts_v2.CognitionUnit)
