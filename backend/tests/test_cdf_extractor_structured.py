"""CDF source extractor through jobs.bedrock.structured() (extract_llm, design §5.4 r16.14;
2026-10-29-cdf-launch §1 A1).

What is tested:
- ``unit_schema`` has one list per allowed kind, each item exactly that kind's
  contracts_v2 fields (minus ``kind`` and ``relation_hint``), required as contracts_v2
  requires, and passes structured()'s schema check. No item property is a field-less
  object: one made the model send the whole answer as a string (3/3 real S2 calls).
- ``units_from_answer`` gives back each unit with its kind and without nulls.
- ``BedrockExtractor.extract`` asks through structured() with the tool name, schema and
  its own budget, and records usage.
- A truncated / missing / invalid reply is UntypedOutput.
"""
from __future__ import annotations

import pytest

from core.ingestion import contracts_v2
from core.ingestion.prompts import UntypedOutput
from core.ingestion.sources import extract_llm
from jobs.bedrock import StructuredOutputError, _check_schema

KIND_SETS = [("reference",), ("cognition",), ("cognition", "event", "gap", "reference"),
             ("cognition", "gap"), ("event",)]


def _items(kinds):
    schema = extract_llm.unit_schema(kinds)
    return {k: schema["properties"][f"{k}_units"]["items"] for k in kinds}


@pytest.mark.parametrize("kinds", KIND_SETS)
def test_schema_has_one_list_per_kind_with_its_own_fields(kinds) -> None:
    schema = extract_llm.unit_schema(kinds)
    _check_schema(schema)
    assert set(schema["properties"]) == {f"{k}_units" for k in kinds} == set(schema["required"])
    defs = extract_llm._UNIT_DEFS()
    for kind, item in _items(kinds).items():
        # every field but the ones no source extracts by default (one list: _NOT_EXTRACTED)
        assert set(item["properties"]) == set(defs[kind]["properties"]) - extract_llm._NOT_EXTRACTED
        assert set(item["required"]) == set(defs[kind]["required"]) - {"kind"}
        assert not [n for n, p in item["properties"].items()
                    if p["type"] == "object" and not p.get("properties")]


def test_overrides_are_extracted_only_where_a_source_asks_for_them() -> None:
    """Only the after-session review may declare an override (brain-driven §2.1)."""
    plain = extract_llm.unit_schema(("cognition",))
    review = extract_llm.unit_schema(("cognition",), extra=("overrides",))
    _check_schema(review)
    assert "overrides" not in plain["properties"]["cognition_units"]["items"]["properties"]
    assert review["properties"]["cognition_units"]["items"]["properties"]["overrides"]["type"] == "array"


def test_units_from_answer_restores_the_kind_and_drops_nulls() -> None:
    answer = {"cognition_units": [{"title": "t", "how": None}], "reference_units": [{"title": "r"}],
              "event_units": None}
    assert extract_llm.units_from_answer(answer, ("cognition", "event", "reference")) == [
        {"kind": "cognition", "title": "t"}, {"kind": "reference", "title": "r"}]


def test_scope_rule_reaches_the_model() -> None:
    """_valid_scope is a validator the JSON schema cannot carry; its rule is in words."""
    for kinds in KIND_SETS:
        for item in _items(kinds).values():
            if "scope" in item["properties"]:
                assert contracts_v2.SCOPE_RULE in item["properties"]["scope"]["description"]
    for value in ("user", "agent", "world", "project:SwarmAI"):
        assert contracts_v2._valid_scope(value) and value.split(":")[0] in contracts_v2.SCOPE_RULE
    assert not contracts_v2._valid_scope("project")


@pytest.mark.parametrize("kinds", KIND_SETS)
def test_validator_rules_reach_the_model(kinds) -> None:
    schema = extract_llm.unit_schema(kinds)
    for kind in kinds:
        text = schema["properties"][f"{kind}_units"]["description"]
        assert all(rule in text for rule in contracts_v2.UNIT_RULES[kind])
        others = set(contracts_v2.UNIT_RULES) - {kind}
        assert not any(rule in text for other in others for rule in contracts_v2.UNIT_RULES[other]
                       if rule not in contracts_v2.UNIT_RULES[kind])


def test_schema_refuses_unknown_kinds() -> None:
    with pytest.raises(ValueError):
        extract_llm.unit_schema(("opinion",))


def _good_cognition(anchor="event:e1"):
    return {"type": "decision", "scope": "world",
            "title": "Use one write path", "what": "Every CDF write goes through the worker.",
            "why": "Two writers raced on MEMORY.md.", "rejected_alternatives": "none",
            "evidence": [anchor], "how": None}


def test_extract_asks_through_structured_and_strips(monkeypatch) -> None:
    calls = {}
    recorded = []

    def fake(prompt, **kw):
        calls.update(kw)
        kw["usage"].update(input_tokens=10, output_tokens=5)
        return {"cognition_units": [_good_cognition()], "reference_units": []}

    monkeypatch.setattr(extract_llm, "structured", fake)
    monkeypatch.setattr(extract_llm.llm_usage, "record", recorded.append)
    result = extract_llm.BedrockExtractor(("cognition", "reference")).extract("p")
    assert calls["name"] == extract_llm.TOOL_NAME
    assert calls["schema"] == extract_llm.unit_schema(("cognition", "reference"))
    assert calls["max_tokens"] == 16000 and calls["read_timeout"] == 300
    assert result.value == [{"kind": "cognition", **{k: v for k, v in _good_cognition().items()
                                                     if k != "how"}}]
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
                        lambda prompt, **kw: {"cognition_units": [_good_cognition()]})
    result = extract_llm.extract_units("S2", "data", ("event:e1",),
                                       allowed_kinds=("cognition", "reference"))
    assert [u.kind for u in result.units] == ["cognition"] and not result.invalid_units
    assert isinstance(result.units[0], contracts_v2.CognitionUnit)


@pytest.mark.parametrize("kinds", KIND_SETS)
def test_enums_are_words_so_one_bad_value_loses_one_unit(kinds) -> None:
    """structured() refuses the whole reply over one off-list value; the schema states
    the values, SOURCE_UNIT refuses only the unit that breaks them."""
    for item in _items(kinds).values():
        for name, prop in item["properties"].items():
            assert "enum" not in prop and "enum" not in (prop.get("items") or {}), name
    defs = extract_llm._UNIT_DEFS()
    if "reference" in kinds:
        assert "principle" in _items(kinds)["reference"]["properties"]["ontology_types"]["description"]


def test_an_off_list_value_invalidates_only_its_unit(monkeypatch) -> None:
    bad = {**_good_cognition(), "type": "design"}
    monkeypatch.setattr(extract_llm, "structured",
                        lambda prompt, **kw: {"cognition_units": [_good_cognition(), bad]})
    result = extract_llm.extract_units("S2", "data", ("event:e1",), allowed_kinds=("cognition",))
    assert len(result.units) == 1 and len(result.invalid_units) == 1
