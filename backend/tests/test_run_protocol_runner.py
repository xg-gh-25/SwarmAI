"""Tests Pipeline v1 runner call/final persistence.

Methodology: temporary git repository and sealed run evidence; every call uses a small
local subprocess and the production runner event authority.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from core import evidence_seal
from core.run_protocol import events as protocol_events
from core.run_protocol.runner import RunnerError, V1Runner, is_v1_run, read_contract


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def _rig(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("SWARM_DATA_DIR", str(data))
    repo = tmp_path / "repo"; repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "backend").mkdir(); (repo / "backend" / "a.py").write_text("x=1\n")
    manifest = {"schema": 1, "entries": [
        {"id": "check", "paths": ["backend/**/*.py"], "cwd": ".",
         "argv": ["python", "-c", "print('ok')"], "call_type": "lint"}]}
    (repo / "validations.json").write_text(json.dumps(manifest))
    _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    run_id = "run_one"
    audit = data / "state" / "pipeline_agent_audit" / "runs" / run_id
    audit.mkdir(parents=True)
    key = b"runner-test-authority-key-0000001"
    (audit / "authority.key").write_bytes(key)
    authority = protocol_events.EventAuthority(key)
    contract = {"run_id": run_id, "protocol_version": 1, "expected_repos": ["repo"],
                "event_authority_id": authority.authority_id,
                "pipeline_code_hash": "a" * 64}
    (audit / "contract.json").write_text(json.dumps(contract))
    # the runner writes through the run's lifecycle-validated store, so the log
    # starts with its genesis event as every real run's does
    from core.run_protocol.store import AuditStore
    AuditStore(data / "state" / "pipeline_agent_audit", run_id, authority).append_new(
        writer=authority.writer("cli"), kind="run_create", at="2026-10-23T00:00:00Z",
        payload={"run_id": run_id, "profile": "direct", "decision_cap": 0})
    run_dir = tmp_path / "run"; run_dir.mkdir()
    evidence_seal.enable(run_id)
    runner = V1Runner(run_dir=run_dir, repo_root=repo, repo_name="repo", run_id=run_id,
                      base=base, code_tree=tree, contract=contract, python="python3")
    return runner, data, run_dir


def _selection():
    return {"files": ["backend/tests/test_a.py"],
            "nodeids": ["backend/tests/test_a.py::test_a"],
            "summary": {"passed": 1},
            "dependency_scope": {"files": ["backend/tests/test_a.py"]},
            "risk_rules": [], "rules_version": "1", "mode": "regression",
            "risk_status": "within_cap"}


def test_contract_presence_is_authoritative(tmp_path, monkeypatch):
    _runner, data, _ = _rig(tmp_path, monkeypatch)
    assert is_v1_run(data, "run_one")
    assert read_contract(data, "run_one")["expected_repos"] == ["repo"]
    assert not is_v1_run(data, "run_other")


def test_validation_call_and_final_are_sealed_and_event_bound(tmp_path, monkeypatch):
    runner, _data, run_dir = _rig(tmp_path, monkeypatch)
    required = runner.required_validations(["backend/a.py"])
    call = runner.run_validation(required[0])
    assert call["result"] == "pass" and call["validation_id"] == "check"
    test_call = runner.record_call(call_type="test",
        argv=["python3", "-c", "print('ok')"], tool_version="pytest",
        target="backend/tests/test_a.py::test_a")
    assert test_call["event"]["producer"] == "runner"
    assert test_call["event"]["payload"]["result"] == "pass"
    final = runner.finalize(
        changed_paths=["backend/a.py"], runner_code_hash="f" * 64,
        pipeline_code_hash="b" * 64,
        runner_argv=["pipeline_test.py", "--mode", "regression"],
        selection=_selection(), cache_keys=[],
        main_red={"sha": "e" * 64, "generated_at": "now"},
        trace=[{"exit":"X1", "kind":"test", "covers":["R1.1"],
                "ref":"backend/tests/test_a.py::test_a", "result":"pass",
                "seq":test_call["seq"]}],
    )
    assert final["tests"]["verdict"] == "green"
    assert final["validations"][0]["verdict"] == "pass"
    assert final["provenance"]["selection"]["nodeids"] == ["backend/tests/test_a.py::test_a"]
    assert len(final["provenance"]["record_hash"]) == 64
    path = run_dir / "evidence" / "tests" / "final-repo.json"
    assert evidence_seal.read_checked(path)[1] is True


def test_public_record_call_rejects_caller_verdict(tmp_path, monkeypatch):
    runner, _data, _run_dir = _rig(tmp_path, monkeypatch)
    with pytest.raises(TypeError):
        runner.record_call(call_type="test", argv=["true"], tool_version="x", result="pass")


def test_pipeline_receipt_must_be_runner_sealed_to_pass(tmp_path, monkeypatch):
    runner, _data, _run_dir = _rig(tmp_path, monkeypatch)
    call = runner.record_pipeline_call(argv=["pipeline_test.py"], tool_version="1",
                                       target="verify", output={"ok": True}, exit_code=0)
    assert call["result"] == "error"
    with pytest.raises(RunnerError, match="outside"):
        runner.record_pipeline_call(argv=["pipeline_test.py"], tool_version="1",
            target="verify", output={"evidence": str(tmp_path / "forged.json")}, exit_code=0)


def test_prove_receipt_emits_correlated_runner_events(tmp_path, monkeypatch):
    # this test is about the runner's call/proof correlation; the finding ledger
    # (which refuses a proof of an unknown finding) is covered by the findings and
    # real-E2E tests, so its fold is left out of this synthetic run's store
    from core.run_protocol import engine
    monkeypatch.setattr(engine, "_FINDING_KINDS", engine._FINDING_KINDS - {"proof"})
    runner, _data, run_dir = _rig(tmp_path, monkeypatch)
    receipt = run_dir / "evidence" / "proofs" / "prove.json"
    receipt.parent.mkdir(parents=True)
    nodeid = "backend/tests/test_a.py::test_a"
    receipt.write_text(json.dumps({
        "schema": 1, "run_id": "run_one", "round": "R1",
        "overlay_tree": "9" * 40,
        "proofs": [{"nodeid": nodeid,
                    "old": ["assert_fail", "assert_fail"],
                    "new": "pass", "verdict": "valid"}],
    }))
    result = runner.record_proof_result(
        output={"evidence": str(receipt)}, exit_code=0,
        bindings={"CF-1234abcd": nodeid})
    assert len(result["proof_events"]) == 1
    events = runner._store.read_events()
    calls = [event for event in events if event["kind"] == "test_call"]
    assert [event["payload"]["snapshot"] for event in calls] == ["old", "old", "new"]
    assert [event["payload"]["result"] for event in calls] == [
        "assert_fail", "assert_fail", "pass"]
    proof = result["proof_events"][0]
    assert proof["producer"] == "runner"
    assert proof["payload"]["test_call_event_ids"] == [event["event_id"] for event in calls]
