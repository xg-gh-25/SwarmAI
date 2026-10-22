"""Tests Pipeline v1 sealed final report construction.

Methodology: signed runner events bound to immutable call receipts and two-repo merges.
"""
from __future__ import annotations

import copy

import pytest

from core.run_protocol import events as protocol_events
from core.run_protocol.final import (FinalError, build_repo_final, call_hash,
                                     merge_final, validate_final)

TREE = "1" * 40
BASE = "2" * 40
RUNNER = "3" * 64
PIPELINE = "4" * 64
CONTRACT_PIPELINE = "5" * 64
AUTH = protocol_events.EventAuthority(b"final-test-authority-key-00000001")
MANIFEST = {"schema": 1, "entries": [
    {"id": "lint", "paths": ["backend/**"], "cwd": ".", "argv": ["lint"], "call_type": "lint"}
]}


def _call(seq, call_type="test", result="pass", repo="repo", **extra):
    row = {"seq": seq, "repo": repo, "call_type": call_type,
           "code_tree": TREE, "argv": ["tool", str(seq)], "tool_version": "1",
           "result": result, "started_at": float(seq), "finished_at": float(seq), **extra}
    record_hash = call_hash(row)
    event, _ = protocol_events.make_event(
        writer=AUTH.writer("runner"), run_id="run_one", kind="test_call",
        at=f"2026-10-21T00:00:{seq:02d}+00:00",
        payload={"seq": seq, "call_type": call_type, "code_tree": TREE,
                 "result": result, "details": [f"record_hash:{record_hash}"]})
    row.update(record_hash=record_hash, event_id=event["event_id"],
               event_hash=protocol_events.event_digest(event), event=event)
    return row


def _selection():
    return {"files": ["backend/tests/a.py"], "nodeids": ["backend/tests/a.py::test_a"],
            "summary": {"passed": 1},
            "dependency_scope": {"files": ["backend/tests/a.py"]},
            "risk_rules": ["security"], "mode": "regression"}


def _row(calls=None, required=("lint",), repo="repo", seq0=0):
    return build_repo_final(
        repo=repo, code_tree=TREE, base=BASE,
        calls=([_call(seq0 + 1, repo=repo),
                _call(seq0 + 2, "lint", repo=repo, validation_id="lint")]
               if calls is None else calls),
        required_validation_ids=required, validation_manifest=MANIFEST,
        runner_code_hash=RUNNER, pipeline_code_hash=PIPELINE,
        contract_pipeline_code_hash=CONTRACT_PIPELINE,
        runner_argv=["pipeline_test.py", "--mode", "regression"],
        selection=_selection(), cache_keys=["6" * 64],
        main_red={"sha": "7" * 64, "generated_at": "now"},
        trace=[{"exit": "X1", "kind": "test", "covers": ["R1.1"],
                "ref": "backend/tests/a.py::test_a", "result": "pass", "seq": seq0 + 1}],
    )


def test_green_repo_and_exact_two_repo_merge():
    row_a, row_b = _row(repo="a"), _row(repo="b", seq0=2)
    assert row_a["tests"]["verdict"] == "green"
    final = merge_final("run_one", ["a", "b"], {"a": row_a, "b": row_b})
    validate_final(final, expected_repos=["a", "b"])
    assert final["repos"]["a"]["validations"] == [{"id": "lint", "verdict": "pass", "seq": 2}]
    assert final["repos"]["a"]["provenance"]["built_from"][0]["test_call_event_id"]


def test_red_incomplete_and_not_run_validation_are_distinct():
    assert _row([_call(1, result="fail")], required=())["tests"]["verdict"] == "red"
    assert _row([_call(1, result="error")], required=())["tests"]["verdict"] == "incomplete"
    row = _row([_call(1)])
    assert row["validations"][0]["verdict"] == "not_run"


def test_wrong_tree_duplicate_seq_or_unknown_trace_fails():
    bad = _call(1); bad["code_tree"] = "9" * 40
    with pytest.raises(FinalError, match="different code tree"):
        _row([bad])
    with pytest.raises(FinalError, match="unique"):
        _row([_call(1), _call(1)])
    with pytest.raises(FinalError, match="unknown runner call"):
        build_repo_final(repo="repo", code_tree=TREE, base=BASE, calls=[_call(1)],
            required_validation_ids=[], validation_manifest=MANIFEST,
            runner_code_hash=RUNNER, pipeline_code_hash=PIPELINE,
            contract_pipeline_code_hash=CONTRACT_PIPELINE,
            runner_argv=["x"], selection=_selection(), cache_keys=[], main_red={},
            trace=[{"exit":"X", "kind":"test", "covers":[], "ref":"x",
                    "result":"pass", "seq":2}])


def test_pure_document_change_allows_an_empty_green_test_final():
    row = build_repo_final(
        repo="repo", code_tree=TREE, base=BASE, calls=[], required_validation_ids=[],
        validation_manifest=MANIFEST, runner_code_hash=RUNNER,
        pipeline_code_hash=PIPELINE, contract_pipeline_code_hash=CONTRACT_PIPELINE,
        runner_argv=["runner"],
        selection={"files": [], "nodeids": [], "summary": {},
                   "dependency_scope": {}, "risk_rules": [], "mode": "validation"},
        cache_keys=[], main_red={}, trace=[], tests_required=False,
    )
    assert row["tests"]["verdict"] == "green"


def test_final_repo_set_built_from_and_record_hash_are_fail_closed():
    row = _row(repo="a")
    with pytest.raises(FinalError, match="exactly"):
        merge_final("run_one", ["a", "b"], {"a": row})
    final = merge_final("run_one", ["a"], {"a": copy.deepcopy(row)})
    final["repos"]["a"]["provenance"]["built_from"] = []
    with pytest.raises(FinalError, match="built_from"):
        validate_final(final, expected_repos=["a"])
    final = merge_final("run_one", ["a"], {"a": copy.deepcopy(row)})
    final["repos"]["a"]["provenance"]["selection"]["files"] = ["forged.py"]
    with pytest.raises(FinalError, match="record_hash"):
        validate_final(final, expected_repos=["a"])


def test_unsigned_or_unbound_call_cannot_form_green_final():
    call = _call(1)
    call["event"]["producer"] = "cli"
    with pytest.raises(FinalError, match="signed event"):
        _row([call], required=())
