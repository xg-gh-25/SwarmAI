"""The ``ingest submit --wait`` hint for a unit kept out of a file read every turn names
the cause that fired (desktop_main.reroute_hint).

Question these tests answer: when the isolated user check refuses a quoted unit, does the
hint say so, instead of claiming the quote was not found word for word (which sends the
agent to re-submit the same quote and write a duplicate)?
"""
import json

import desktop_main

DEST = {"store": "COMMON_DDD", "relative_path": "Knowledge/Library/agent-self-knowledge.md",
        "rerouted_from": "EVOLUTION"}


def _decision(vector):
    return json.dumps({"decision": "write", "version_vector": vector})


def test_user_check_refusal_is_named_with_its_reason():
    hint = desktop_main.reroute_hint(
        DEST, _decision({"basis": "inferred",
                         "user_check": {"holds": False, "reason": "The message is only approval."}}),
        {"user_quote": "你自己优化下"})
    assert "user check" in hint
    assert "The message is only approval." in hint
    assert "not found word for word" not in hint
    assert "earlier turn" in hint


def test_external_basis_is_named():
    hint = desktop_main.reroute_hint(DEST, _decision({"basis": "external"}),
                                     {"user_quote": "x y"})
    assert "external content" in hint
    assert "word for word" not in hint


def test_missing_quote_is_named():
    hint = desktop_main.reroute_hint(DEST, _decision({"basis": "inferred"}), {"title": "t"})
    assert "No user_quote was given" in hint


def test_quote_not_found_keeps_the_verbatim_hint():
    hint = desktop_main.reroute_hint(DEST, _decision({"basis": "inferred"}),
                                     {"user_quote": "never said"})
    assert "not found word for word" in hint
    assert "Knowledge/Library/agent-self-knowledge.md" in hint and "EVOLUTION" in hint


def test_unreadable_decision_falls_back_without_raising():
    hint = desktop_main.reroute_hint(DEST, "{not json", {"user_quote": "q q"})
    assert "not found word for word" in hint
