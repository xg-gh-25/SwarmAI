"""templates — the smallest valid example of every document a run asks the agent for
(design docs/design/2026-11-01-pipeline-delivery-friction.md D7).

The shapes are enforced by code (``schemas``, ``validation_report``, ``reflect``, the
engine's review checks); a stage card names the rules, not an example, so a first
submission was often refused. ``pipeline-v1 template <kind>`` prints one of these: the
document with ``<PLACEHOLDER>`` tokens where the run's own values go, and what each
token is and where to find it. ``tests/test_pipeline_v1_templates.py`` sends every
template, its tokens filled with a real run's values, through the real entry point:
a schema change that a template no longer meets turns that test red.

Public: ``KINDS``, ``template(kind)``, ``fill(value, values)``.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Mapping

PLACEHOLDERS = {
    "<FILE>": "a file the change edits, from the repository root",
    "<SYMBOL>": "the function / class / key in that file the task changes",
    "<TEST_FILE>": "a test file of the repository, from its root",
    "<TEST_REF>": "a test the plan relies on: <test file>::<test name>",
    "<REPO>": "the repo name the run was created with (--repo <name>=<path>)",
    "<COMMAND>": "the exact command you ran, as you would type it again",
    "<ANCHOR>": "an evidence anchor of this run: `pipeline-v1 events` lists them",
    "<FINDING_ID>": "a finding id (CF-xxxxxxxx): `pipeline-v1 findings` lists them",
    "<PROJECT>": "the run's project name (--project)",
}

_AC = {"id": "R1.1", "pattern": "event", "condition": "<SYMBOL> is called with valid input",
       "response": "it returns the documented result", "negative": False, "universal": False}

_TASK = {"id": "T1", "file": "<FILE>", "symbol": "<SYMBOL>",
         "change": "what this task changes in <SYMBOL>, in one sentence",
         "requirements": ["R1.1"], "depends_on": [], "creates_test_files": []}

_EXITS = [{"id": "X1", "kind": "test", "ref": "<TEST_REF>", "covers": ["R1.1"]}]

_TEMPLATES: dict[str, dict[str, Any]] = {
    # EVALUATE (full / bugfix): stage-complete --artifact -
    "requirements": {
        "requirements": [{"id": "R1", "story": "As <who>, I want <what>, so that <why>.",
                          "acceptance": [_AC]}],
        "analysis": [{"kind": "missing_edge", "refs": ["R1.1"],
                      "note": "an edge case the request did not name, and how it is handled",
                      "resolution": "rewritten"}],
        "clarifications": [],
        "understanding": {"claim": "what the change is, in one sentence",
                          "readings": ["the one reading of the request this run takes"],
                          "evidence": ["<FILE>"], "root_cause": None},
        "knowledge_refs": [],
    },
    # PLAN (full / bugfix): stage-complete --artifact -
    "design_spec": {
        "alternatives": [{"what": "the approach chosen"}, {"what": "an approach not taken"}],
        "chosen": "the approach chosen",
        "design": {"user_workflow": "how a user meets the change",
                   "architecture": "where it lives and what it calls",
                   "flow": "call -> validate; invalid -> error; valid -> result",
                   "states": [{"name": "ready", "created_by": "what creates it",
                               "removed_by": "what removes it"}]},
        "properties": [{"id": "P1", "statement": "what holds after the change",
                        "validates": ["R1.1"], "test": "<TEST_REF>", "kind": "example"}],
        "failure_modes": [{"id": "F1", "scenario": "bad input", "expected": "an error",
                           "test": "<TEST_REF>"}],
        "tasks": [_TASK],
        "exit_criteria": _EXITS,
        "consumers": [{"id": "K1", "contract": "<SYMBOL>", "pattern": "<SYMBOL>",
                       "paths": ["<FILE>"], "readers": ["<TEST_FILE>"]}],
        "security_boundaries": [],
    },
    # PLAN (trivial): stage-complete --artifact -
    "design_spec-lite": {
        "tasks": [_TASK], "exit_criteria": _EXITS,
        "no_contract_change_reason": "why no caller sees a different contract",
    },
    # BUILD / TEST: stage-complete --artifact -; DELIVER / REVIEW: report-tests --artifact -
    "validation_report": {"reports": [{
        "repo": "<REPO>",
        "commands": [
            {"command": "<COMMAND>", "cwd": ".", "result": "pass"},
            {"command": "<COMMAND>", "cwd": ".", "result": "pre_existing",
             "reason": "fails the same way on the run's base (pipeline-v1 base-tree)"}],
        "tests_changed": ["<TEST_FILE>"],
        "rationale": "why these commands show the change is right"}]},
    # DELIVER: reflect-prepare --artifact -
    "reflect": {
        "delivered": [{"kind": "event", "when": "2026-01-01", "scope": "project:<PROJECT>",
                       "title": "what the run delivered", "what": "the change, in a sentence",
                       "outcome": "what is different for its users", "evidence": ["<ANCHOR>"]}],
        "decisions": [{"kind": "cognition", "type": "decision", "scope": "project:<PROJECT>",
                       "title": "a choice this run made", "what": "what was chosen",
                       "why": "why it was chosen", "rejected_alternatives": "none",
                       "evidence": ["<ANCHOR>"]}],
        "corrections": [], "practices": [], "gaps": [],
    },
    # REVIEW: review-address --artifact -
    "review_address": {"pass": 1, "items": [
        {"finding_id": "<FINDING_ID>", "hunk": "the fix in a line, or why there is no change"}]},
}

KINDS = tuple(_TEMPLATES)


def template(kind: str) -> dict[str, Any]:
    """``{"kind", "document", "placeholders"}`` for *kind*; ValueError for another."""
    if kind not in _TEMPLATES:
        raise ValueError(f"no template {kind!r}; one of: {', '.join(KINDS)}")
    document = copy.deepcopy(_TEMPLATES[kind])
    text = json.dumps(document)
    used = {token: why for token, why in PLACEHOLDERS.items() if token in text}
    return {"kind": kind, "document": document, "placeholders": used}


def fill(value: Any, values: Mapping[str, str]) -> Any:
    """*value* with every placeholder token replaced by ``values[token]``."""
    text = json.dumps(value)
    for token, real in values.items():
        text = text.replace(token, json.dumps(real)[1:-1])
    return json.loads(text)
