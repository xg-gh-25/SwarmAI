"""The reach of a scope is part of its one definition (design 2026-10-31-memory-chain-audit M1).

Questions:
- does the one scope definition say that agent/world are read in every session and that a
  fact about one tool or subject, however reliable, is project:<subject>?
- does every door that tells an extractor what a scope means carry that definition (the
  review prompt, the unit schema, S1's refusal), so no source learns another meaning?
- does the review prompt's placement block say the same, and does the persist skill no
  longer offer "world" as the default scope?
"""
from __future__ import annotations

from pathlib import Path

from core.ingestion import contracts_v2


def test_the_scope_definition_carries_the_reach():
    rule = contracts_v2.SCOPE_RULE
    assert "read in every future session" in rule
    assert "however reliable" in rule and "project:<that project or subject>" in rule
    for scope in ("user", "agent", "world", "project:"):
        assert scope in rule


def test_every_door_shows_the_one_definition():
    from core.ingestion.sources import extract_llm, s1_user, s7_review
    schema = extract_llm.unit_schema(("cognition",), extra=())
    descriptions = []

    def walk(node):
        if isinstance(node, dict):
            scope = (node.get("properties") or {}).get("scope")
            if isinstance(scope, dict) and "description" in scope:
                descriptions.append(scope["description"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schema)
    assert descriptions and all(contracts_v2.SCOPE_RULE in d for d in descriptions)
    prompt = s7_review.build_prompt(("cognition",), {"user_messages": 1, "tool_calls": 0, "files_written": []},
                                    "(index)", "(record)", projects=["SwarmAI"], general=[])
    assert contracts_v2.SCOPE_RULE in prompt
    assert "SCOPE_RULE" in Path(s1_user.__file__).read_text(encoding="utf-8")


def test_the_review_says_where_a_narrow_fact_goes():
    from core.ingestion.sources import s7_review
    where = " ".join(s7_review._WHERE.split()).casefold()
    assert "a fact about one tool, library, api or subject" in where
    assert "however reliable" in where


def test_the_persist_skill_does_not_default_to_world():
    skill = (Path(__file__).resolve().parents[1] / "skills/s_persist/SKILL.md").read_text(encoding="utf-8")
    assert '"scope":"world"' not in skill
    assert "Reach decides the scope" in skill
