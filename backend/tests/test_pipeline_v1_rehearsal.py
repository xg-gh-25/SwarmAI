"""Pre-cutover rehearsal: direct twice and every other profile once.

Each code profile uses a real isolated worktree, validated documents, sealed test final,
delivery-tree commit and completed five-dimension audit with no BLOCK.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.run_protocol import git_tree
from core.run_protocol.engine import RunEngine
from core.run_protocol.hook_bridge import ReviewHookBridge
from core.run_protocol.runner import V1Runner


def _git(repo, *args):
    return subprocess.run(["git","-C",str(repo),*args],check=True,stdout=subprocess.PIPE,text=True).stdout.strip()


def _repo(root):
    repo=root/"repo"; repo.mkdir(); _git(repo,"init","-q"); _git(repo,"config","user.email","a@b.c"); _git(repo,"config","user.name","T")
    (repo/"a.py").write_text("value=1\n"); (repo/"tests").mkdir(); (repo/"tests/test_x.py").write_text("def test_value(): pass\n")
    (repo/"validations.json").write_text(json.dumps({"schema":1,"entries":[{"id":"none","paths":["docs/**"],"cwd":".","argv":["true"],"call_type":"lint"}]}))
    _git(repo,"add","."); _git(repo,"commit","-qm","base"); return repo


def _ac(identifier="R1.1"):
    return {"id":identifier,"pattern":"event","condition":"call runs","response":"return value","negative":False,"universal":False}


def _requirements(profile):
    value={"requirements":[{"id":"R1","story":"As a user","acceptance":[_ac()]}],
           "analysis":[{"kind":"missing_edge","refs":["R1.1"],"note":"empty","resolution":"rewritten"}],
           "clarifications":[{"qid":"Q1","question":"Which?","answer":"existing","answered_by":"evidence"}],
           "understanding":{"claim":"change value","readings":["one"],"evidence":["repo"],"root_cause":None},
           "prior_knowledge":[]}
    if profile=="bugfix": value["bugfix"]={"current":[_ac("C1")],"expected":[_ac("E1")],"unchanged":[_ac("U1")]}
    return value


def _design(profile):
    lite={"tasks":[{"id":"T1","file":"a.py","symbol":"value","change":"set 2","requirements":["R1.1"],"depends_on":[],"creates_test_files":[]}],
          "exit_criteria":[{"id":"X1","kind":"test","ref":"tests/test_x.py::test_value","covers":["R1.1"]}],
          "no_contract_change_reason":"private value"}
    if profile in {"direct","trivial"}: return lite
    value={"alternatives":[{"what":"set"}],"chosen":"set","design":{"user_workflow":"call","architecture":"module","flow":"call -> validate; invalid -> error; valid -> value","states":[{"name":"ready","created_by":"load","removed_by":"exit"}]},
           "properties":[{"id":"P1","statement":"value exists","validates":["R1.1"],"test":"tests/test_x.py::test_value","kind":"example"}],
           "failure_modes":[{"id":"F1","scenario":"missing","expected":"error","test":"tests/test_x.py::test_value"}],
           "tasks":lite["tasks"],"exit_criteria":lite["exit_criteria"],
           "consumers":[{"id":"K1","contract":"value","pattern":"value","paths":["a.py"],"readers":["tests/test_x.py"]}],"security_boundaries":[]}
    if profile=="bugfix": value["exit_criteria"].append({"id":"X2","kind":"test","ref":"tests/test_x.py::test_value","covers":["U1"]})
    return value


def _runner(engine, wt, target, profile):
    runner = V1Runner(
        run_dir=engine.run_dir, repo_root=wt, repo_name="repo",
        run_id=engine.run_id, base=_git(wt, "rev-parse", "HEAD"),
        code_tree=git_tree.write_tree(
            str(wt), (f".aidlc/deliverables/{engine.run_id}/",), {}),
        contract=engine.contract, python="python3")
    call = runner.record_call(
        call_type="test", argv=["python3", "-c", "pass"],
        tool_version="python3", target="tests/test_x.py::test_value")
    covers = ["R1.1"] + (["U1"] if profile == "bugfix" else [])
    selection = {
        "files": ["tests/test_x.py"],
        "nodeids": ["tests/test_x.py::test_value"],
        "summary": {"selected": 1, "mode": target},
        "dependency_scope": {"tests/test_x.py::test_value": ["a.py"]},
        "test_dependencies": {"tests/test_x.py::test_value": ["a.py"]},
        "risk_rules": [], "mode": target, "risk_status": "within_cap",
    }
    runner.finalize(
        changed_paths=["a.py"], runner_code_hash="f" * 64,
        pipeline_code_hash=engine.contract["pipeline_code_hash"],
        runner_argv=["pipeline_test.py", "--mode", target],
        selection=selection, cache_keys=[],
        main_red={"sha": "e" * 64, "generated_at": "now"},
        trace=[{"exit": "X1", "kind": "test", "covers": covers,
                "ref": "tests/test_x.py::test_value", "result": "pass",
                "seq": call["seq"]}],
        node_results={"tests/test_x.py::test_value": {"result": "pass", "seq": call["seq"]}},
        tdd_required=["tests/test_x.py"])


def _run_code(case, profile, run_id, monkeypatch):
    data,ws=case/"data",case/"ws"; (ws/"Projects/P").mkdir(parents=True); repo=_repo(case)
    monkeypatch.setenv("SWARM_DATA_DIR",str(data)); monkeypatch.setenv("SWARM_WORKTREE_ROOT",str(case/"wts")); monkeypatch.setenv("SWARM_WORKTREE_LEDGER",str(case/"ledger"))
    engine=RunEngine.create(data_dir=data,workspace=ws,project="P",profile=profile,session_id="s",repos={"repo":str(repo)},run_id=run_id,requirement="rehearsal change")
    if profile=="direct":
        wt=Path(engine.stage_start()["repos"]["repo"]); (wt/"a.py").write_text("value=2\n")  # direct: no PLAN
    else:
        requirements = _requirements(profile)
        if profile == "bugfix":
            gate = engine.gate0_start(requirements)
            bridge = ReviewHookBridge(engine.store, "s")
            bound = bridge.track_tool_use("gate0-tool", gate["handout"]["prompt"])
            assert bound
            bridge.record_answer(
                bound, "VERDICT: SUPPORTED\nEvidence confirms the bug framing.",
                completed_at="2026-10-22T00:00:00+00:00")
            bridge.record_usage(
                {"input_tokens": 100, "output_tokens": 20, "responses": 1},
                completed_at="2026-10-22T00:00:01+00:00")
        assert engine.stage_complete(requirements)["ok"]
        if profile in {"full","bugfix","goal"}: assert engine.stage_complete()["ok"]  # THINK
        wt=Path(engine.stage_start()["repos"]["repo"]); (wt/"a.py").write_text("value=2\n")
        assert engine.stage_complete(_design(profile))["ok"]
    _runner(engine,wt,"tdd",profile); assert engine.stage_complete()["ok"]
    if profile=="goal": pass
    assert engine.review_start()["reviewers"]==[]; assert engine.stage_complete()["ok"]
    _runner(engine,wt,"regression",profile); assert engine.stage_complete()["ok"]
    engine.normal_land(message=f"feat: {profile}\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>"); engine.reflect_complete()
    audit=json.loads((engine.run_dir/"run_audit.json").read_text())
    assert all(row["verdict"]!="BLOCK" for row in audit["summary"].values()),audit["summary"]


@pytest.mark.parametrize("case_name,profile",[("direct-a","direct"),("direct-b","direct"),("full","full"),("bugfix","bugfix"),("trivial","trivial"),("goal","goal")])
def test_code_profile_rehearsal(case_name,profile,tmp_path,monkeypatch):
    case=tmp_path/case_name; case.mkdir(); _run_code(case,profile,"run_"+case_name.replace("-","_"),monkeypatch)


def test_research_rehearsal(tmp_path,monkeypatch):
    case=tmp_path/"research"; data,ws=case/"data",case/"ws"; (ws/"Projects/P").mkdir(parents=True)
    monkeypatch.setenv("SWARM_DATA_DIR",str(data)); engine=RunEngine.create(data_dir=data,workspace=ws,project="P",profile="research",session_id="s",repos={},run_id="run_research")
    assert engine.stage_complete(_requirements("research"))["ok"]; assert engine.stage_complete()["ok"]; assert engine.stage_complete()["ok"]
    engine.reflect_complete(); audit=json.loads((engine.run_dir/"run_audit.json").read_text()); assert all(row["verdict"]!="BLOCK" for row in audit["summary"].values())
