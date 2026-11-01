"""The agent confirms its own Project Agent; the person decides only what widens it.

Design docs/design/2026-11-01-project-agent-owned-by-agent.md §8 (V1-V7):
core/project_agent_floor, project_agent_review.agent_confirm, task_spec.confirm_latest,
core/project_agent_card and its ask_question_gate branch, the routes, the gate on the
person's confirm route.
"""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import project_agent_card as pac, project_agent_floor as fl
from core.project_agent import confirmation, revision, source_check_current
from core.project_agent_review import agent_confirm, confirm

ALL4 = ["open_pr", "publish_release", "push", "send_external_message"]
DOC = {
    "schema": 2, "status": "draft",
    "identity": {"summary": "lib", "source": "PRODUCT.md"},
    "intake": {"handles": ["a", "b", "c"], "repos": []},
    "confidence": "research first",
    "watches": [{"area": "x", "why": "y", "source": "PRODUCT.md"}, {"area": "z", "why": "w", "source": "PRODUCT.md"}],
    "gates": {"enforce": ["no_git_push"]},
    "reserved_for_human": ALL4,
    "placements": ["chat-tab", "hive"],
    "modes": [
        {"id": "research", "when": "a question", "skills": ["s_a"]},
        {"id": "release", "when": "build it", "placements": ["chat-tab"],
         "reserved_for_human": ALL4, "gates": {"enforce": ["no_git_push"]}},
    ],
    "default_mode": "research",
}
SID = "sess-abc123"


def _brain(root: Path, doc: dict | None = None, name: str = "Lib") -> Path:
    b = root / "Projects" / name
    (b / "2-understanding").mkdir(parents=True)
    (b / "2-understanding" / "PRODUCT.md").write_text("# PRODUCT\n")
    (b / "4-capabilities" / "s_a").mkdir(parents=True)
    (b / "4-capabilities" / "s_a" / "SKILL.md").write_text("---\nname: s_a\n---\n")
    (b / "3-gates").mkdir()
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(0)\n")
    (b / "aim.json").write_text(json.dumps({"name": name, "aliases": []}))
    (b / ".project.json").write_text("{}")
    _put(b, doc or DOC)
    return b


def _put(b: Path, doc: dict) -> None:
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))


def _doc(b: Path) -> dict:
    return yaml.safe_load((b / "project-agent.yaml").read_text())


def _person(b: Path):
    return confirm(b, seen_revision=revision(b), reviewer="tester")


def _agent(b: Path, why="the definition is current"):
    return agent_confirm(b, session=SID, why=why)


def _without(lst, x):
    return [i for i in lst if i != x]


# ── the floor, a set comparison ─────────────────────────────────────────────

def test_nothing_widens_against_itself_and_each_kind_of_widening_is_named():
    a = fl.authority(DOC)
    assert fl.widened(a, a) == []
    d = copy.deepcopy(DOC)
    d["modes"][1]["reserved_for_human"] = _without(ALL4, "push")
    assert fl.widened(fl.authority(d), a) == ["mode release no longer keeps push for you"]
    d = copy.deepcopy(DOC)
    d["gates"]["enforce"] = []
    d["modes"][1]["gates"]["enforce"] = []
    assert set(fl.widened(fl.authority(d), a)) == {
        "the shared part no longer enforces gate no_git_push", "mode research no longer enforces gate no_git_push",
        "mode release no longer enforces gate no_git_push"}
    d = copy.deepcopy(DOC)
    d["modes"][1]["delivery"] = "branch-pr"
    assert "pushes to GitHub" in fl.widened(fl.authority(d), a)[0]
    floor = fl.authority(d)
    d["modes"][1]["pr_remote"] = "origin"
    assert "from origin instead of fork" in fl.widened(fl.authority(d), floor)[0]
    # a mode the floor does not know is held to the shared floor and to a bundle
    d = copy.deepcopy(DOC)
    d["modes"].append({"id": "ship", "when": "ship it", "reserved_for_human": [], "delivery": "branch-pr"})
    w = fl.widened(fl.authority(d), a)
    assert any(x == "mode ship no longer keeps push for you" for x in w) and any("ship delivers" in x for x in w)


def test_narrowing_is_never_widening():
    a = fl.authority({**DOC, "reserved_for_human": ["push"],
                      "modes": [{"id": "research", "when": "q"}]})
    assert fl.widened(fl.authority(DOC), a) == []


# ── the agent's confirmation ────────────────────────────────────────────────

def test_the_agent_confirms_a_draft_that_keeps_the_product_floor(tmp_path):
    b = _brain(tmp_path)
    r = _agent(b)
    assert r["status"] == "confirmed" and r["changed"] == ["first confirmation"]
    doc = _doc(b)
    assert doc["status"] == "reviewed" and doc["reviewed_by"] == fl.AGENT
    assert confirmation(doc["status"], source_check_current(b)) == "reviewed"
    rec = fl.load(b)
    assert rec["floor"] == fl.PRODUCT_FLOOR and rec["set_by"] is None          # V1
    assert rec["agent"][-1]["session"] == SID and rec["agent"][-1]["revision"] == revision(b)


def test_the_agent_does_not_confirm_a_draft_that_gives_up_a_reserved_action(tmp_path):
    d = copy.deepcopy(DOC)
    d["reserved_for_human"] = _without(ALL4, "push")
    d["modes"][1]["reserved_for_human"] = _without(ALL4, "push")
    b = _brain(tmp_path, d)
    before = (b / "project-agent.yaml").read_bytes()
    r = _agent(b)
    assert r["status"] == "widened" and "the shared part no longer keeps push for you" in r["widened"]   # V7
    assert (b / "project-agent.yaml").read_bytes() == before and source_check_current(b) is None


def test_a_changed_skill_is_confirmed_by_the_agent_and_the_floor_stays_the_persons(tmp_path):
    b = _brain(tmp_path)
    _person(b)
    floor = fl.load(b)
    assert floor["set_by"] == "tester"
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")
    assert confirmation(_doc(b)["status"], source_check_current(b)) == "changed"
    r = _agent(b)
    assert r["status"] == "confirmed" and r["changed"] == ["a skill or a gate it uses"]            # V3
    after = fl.load(b)
    assert after["floor"] == floor["floor"] and after["set_by"] == "tester"                        # V1
    assert _agent(b)["status"] == "already"


def test_the_agent_holds_to_the_persons_floor_not_the_product_default(tmp_path):
    b = _brain(tmp_path)
    _person(b)
    d = _doc(b)
    d["modes"][1]["reserved_for_human"] = _without(ALL4, "push")    # still valid: the shared part keeps it
    d["reserved_for_human"] = _without(ALL4, "push")
    _put(b, d)
    r = _agent(b)
    assert r["status"] == "widened" and r["floorSetBy"] == "tester"
    # the person may give up an action: a later agent confirmation then holds to that
    _person(b)
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")
    assert _agent(b)["status"] == "confirmed"


def test_a_person_confirmed_definition_from_before_the_floor_seeds_it(tmp_path):
    d = copy.deepcopy(DOC)
    d["reserved_for_human"] = ["push"]
    d["modes"][1]["reserved_for_human"] = ["push"]
    b = _brain(tmp_path, d)
    _person(b)
    (b / fl.FLOOR_FILE).unlink()                                    # confirmed before floors existed
    assert _agent(b)["status"] == "already"                         # the spec before a dispatch reads it...
    assert (b / fl.FLOOR_FILE).is_file()                            # ...and keeps the person's floor
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")
    assert _agent(b)["status"] == "confirmed"
    rec = fl.load(b)
    assert rec["set_by"] == "tester" and rec["floor"]["reserved"]["*"] == ["push"]


def test_a_definition_hand_marked_as_the_persons_does_not_seed_the_floor(tmp_path):
    d = copy.deepcopy(DOC)
    d.update(status="reviewed", reviewed_by="tester", reserved_for_human=["push"])
    d["modes"][1]["reserved_for_human"] = ["push"]
    b = _brain(tmp_path, d)                                          # no source check: not a confirmation
    assert fl.load(b)["floor"] == fl.PRODUCT_FLOOR and not (b / fl.FLOOR_FILE).exists()
    assert _agent(b)["status"] == "widened"


def test_a_source_added_to_the_repository_since_is_seen_without_the_person(tmp_path):
    d = copy.deepcopy(DOC)
    d["modes"][0]["setup"] = [{"command": "make", "source": "ci.yml"}]
    b = _brain(tmp_path, d)
    confirm(b, seen_revision=revision(b), reviewer="tester")       # the person confirmed it with a missing source
    assert source_check_current(b)["ok"] is False
    assert _agent(b)["status"] == "problems"
    (b / "ci.yml").write_text("x\n")                                 # the agent adds it
    assert _agent(b)["status"] == "already" and source_check_current(b)["ok"] is True


def test_a_gate_the_person_confirmed_cannot_be_made_to_pass_everything(tmp_path):
    b = _brain(tmp_path)
    _person(b)
    (b / "3-gates" / "no_git_push.py").write_text("import sys\nsys.exit(0)  # nothing checked\n")
    r = _agent(b)
    assert r["status"] == "widened" and any("gate script" in w for w in r["widened"])


def test_a_mode_that_pushes_may_not_push_somewhere_new_and_a_removed_mode_changes_nothing():
    d = copy.deepcopy(DOC)
    d["modes"][1].update(delivery="branch-pr", repos=["a"])
    floor = fl.authority(d)
    d["modes"][1]["repos"] = ["a", "b"]
    assert fl.widened(fl.authority(d), floor) == ["mode release pushes to b as well"]
    d = copy.deepcopy(DOC)
    d["modes"] = d["modes"][:1]
    assert fl.widened(fl.authority(d), fl.authority(DOC)) == []


@pytest.mark.parametrize("break_it,word", [
    (lambda d: d["modes"][0].update(when=""), "when is empty"),
    (lambda d: d["modes"][0].update(setup=[{"command": "make", "source": "ci.yml", "repo": "x"}]),
     "source ci.yml not found"),
])
def test_a_definition_with_problems_is_not_confirmed(tmp_path, break_it, word):
    d = copy.deepcopy(DOC)
    break_it(d)
    b = _brain(tmp_path, d)
    before = (b / "project-agent.yaml").read_bytes()
    r = _agent(b)
    assert r["status"] == "problems" and any(word in p for p in r["problems"])                     # V4
    assert (b / "project-agent.yaml").read_bytes() == before and source_check_current(b) is None


def test_the_agent_says_why(tmp_path):
    b = _brain(tmp_path)
    assert _agent(b, why="  ")["status"] == "problems"
    assert agent_confirm(tmp_path / "Projects" / "Nope", session=None, why="w")["status"] == "none"


def test_a_persons_confirmation_moves_the_floor_and_clears_the_agent_log(tmp_path):
    b = _brain(tmp_path)
    _agent(b)
    assert fl.load(b)["agent"]
    d = _doc(b)
    d["confidence"] = "ask first"
    _put(b, d)
    _person(b)
    rec = fl.load(b)
    assert rec["set_by"] == "tester" and rec["agent"] == []


# ── before a dispatch ───────────────────────────────────────────────────────

def test_the_spec_confirms_the_latest_before_it_reads_the_revision(tmp_path):
    from core import task_spec
    b = _brain(tmp_path)
    _person(b)
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")
    r = task_spec.confirm_latest(tmp_path, "Lib", SID)
    assert r["status"] == "confirmed"
    spec = task_spec.build(tmp_path, "Lib", "t", "a", None, "native", "w", "hive", returns="answer")
    spec["project_agent"] = r
    assert spec["revision"] == r["revision"] == revision(b)
    assert not [x for x in task_spec.admit(spec) if "confirm" in x]


def test_a_spec_that_widens_says_what_and_how_to_ask(tmp_path):
    from core import task_spec
    d = copy.deepcopy(DOC)
    d["reserved_for_human"] = _without(ALL4, "push")
    d["modes"][1]["reserved_for_human"] = _without(ALL4, "push")
    _brain(tmp_path, d)
    r = task_spec.confirm_latest(tmp_path, "Lib", SID)
    spec = task_spec.build(tmp_path, "Lib", "t", "a", None, "native", "w", "hive", returns="answer")
    spec["project_agent"] = r
    why = next(x for x in task_spec.admit(spec) if "draft, not confirmed" in x)
    assert "no longer keeps push" in why and "project_agent card" in why


# ── the card: only the person's pick confirms as the person ─────────────────

@pytest.fixture
def ws(tmp_path, monkeypatch):
    import config
    from core import project_registry
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path)
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path / "data")
    d = copy.deepcopy(DOC)
    d["reserved_for_human"] = _without(ALL4, "push")
    d["modes"][1]["reserved_for_human"] = _without(ALL4, "push")
    return _brain(tmp_path, d)


def _forward(questions, answers, session=SID):
    return asyncio.run(pac.forward(session, questions, answers))


def test_the_card_lists_what_widened_and_the_persons_confirm_moves_the_floor(ws):
    qs = pac.card("Lib", SID)["questions"]
    assert qs[0]["question"].startswith(f"[Project Agent · Lib · {revision(ws)[:8]}]")
    assert "no longer keeps push" in qs[0]["question"] and "the product default" in qs[0]["question"]
    out = _forward(qs, {qs[0]["question"]: pac.CONFIRM})
    assert out["status"] == "confirmed"                                                            # V5
    assert _doc(ws)["reviewed_by"] not in (None, fl.AGENT)
    assert fl.load(ws)["floor"]["reserved"]["*"] == _without(ALL4, "push")
    assert pac.status("Lib")["status"] == "confirmed"


@pytest.mark.parametrize("how", ["other session", "edited", "free text", "changed since", "two questions"])
def test_what_is_not_the_persons_pick_on_the_built_card_confirms_nothing(ws, how):
    qs = pac.card("Lib", SID)["questions"]
    session, answers = SID, {qs[0]["question"]: pac.CONFIRM}
    if how == "other session":
        session = "sess-other"
    elif how == "edited":
        qs = copy.deepcopy(qs)
        qs[0]["options"][0]["description"] = "the agent's words"
    elif how == "free text":
        answers = {qs[0]["question"]: "sure, go ahead"}
    elif how == "changed since":
        d = _doc(ws)
        d["confidence"] = "changed"
        _put(ws, d)
    else:
        qs = qs + [{"question": "another", "options": []}]
    before = (ws / "project-agent.yaml").read_bytes()
    out = _forward(qs, answers, session)
    assert out["status"] == "not_confirmed"
    assert (ws / "project-agent.yaml").read_bytes() == before and fl.load(ws)["set_by"] is None


def test_a_confirmed_card_is_not_confirmed_twice(ws):
    qs = pac.card("Lib", SID)["questions"]
    assert _forward(qs, {qs[0]["question"]: pac.CONFIRM})["status"] == "confirmed"
    assert _forward(qs, {qs[0]["question"]: pac.CONFIRM})["reason"] == "this card was already confirmed"
    assert pac.status("Lib")["status"] == "confirmed"


def test_keep_them_for_me_confirms_nothing_and_says_so(ws):
    qs = pac.card("Lib", SID)["questions"]
    assert _forward(qs, {qs[0]["question"]: pac.KEEP})["status"] == "kept"
    assert _doc(ws)["status"] == "draft" and pac.status("Lib")["status"] == "kept"


def test_no_card_when_there_is_nothing_to_ask_or_no_session(ws):
    with pytest.raises(pac.CardError, match="session"):
        pac.card("Lib", None)
    d = _doc(ws)
    d["reserved_for_human"] = ALL4
    d["modes"][1]["reserved_for_human"] = ALL4
    _put(ws, d)
    with pytest.raises(pac.CardError, match="nothing to ask"):
        pac.card("Lib", SID)                                       # the agent confirmed it instead
    assert _doc(ws)["reviewed_by"] == fl.AGENT
    assert _forward([{"question": "plain", "options": []}], {"plain": "x"}) is None


def test_the_ask_gate_confirms_only_on_a_persons_answer(ws, monkeypatch):
    from core import security_hooks
    from core.ask_question_manager import AskQuestionManager
    qs = pac.card("Lib", SID)["questions"]
    pick = {qs[0]["question"]: pac.CONFIRM}
    monkeypatch.setattr("core.permission_manager.permission_manager.publish_prompt", lambda *a, **k: None)

    def gate(answered_by):
        mgr = AskQuestionManager()
        hook = security_hooks.create_ask_question_gate(SID, {"sdk_session_id": SID}, mgr)

        async def go():
            task = asyncio.ensure_future(hook({"tool_name": "AskUserQuestion",
                                               "tool_input": {"questions": qs}}, "tu-1", None))
            await asyncio.sleep(0)
            mgr.set_answer("tu-1", pick, answered_by=answered_by)
            return await task
        return asyncio.run(go())
    gate("auto")
    assert _doc(ws)["status"] == "draft", "an unattended pick is not the person's"
    out = gate("person")
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert _doc(ws)["status"] == "reviewed" and fl.load(ws)["set_by"] not in (None, fl.AGENT)


# ── the routes and the gate ─────────────────────────────────────────────────

def test_the_agent_routes_need_no_app_header_and_the_persons_route_still_does(tmp_path, monkeypatch):
    import config
    import routers.ddd_brain as rb
    monkeypatch.setattr(rb, "_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(rb, "_projects_root", lambda: tmp_path / "Projects")
    monkeypatch.setattr(config, "get_app_data_dir", lambda: tmp_path / "data")
    b = _brain(tmp_path)
    app = FastAPI()
    app.include_router(rb.router, prefix="/api/ddd")
    c = TestClient(app)
    r = c.post("/api/ddd/brains/Lib/project-agent/agent-confirm", json={"session": SID, "why": "current"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert c.post("/api/ddd/brains/Lib/project-agent/agent-card", json={"session": SID}).status_code == 409
    assert c.post("/api/ddd/brains/Lib/project-agent/confirm", json={"revision": revision(b)}).status_code == 415   # V6
    assert c.post("/api/ddd/brains/Nope/project-agent/agent-confirm", json={"why": "w"}).status_code == 404
    monkeypatch.setenv("SWARMAI_MODE", "hive")
    assert c.post("/api/ddd/brains/Lib/project-agent/agent-confirm", json={"why": "w"}).status_code == 403


_H = "-H 'X-Swarm-User-Action: 1'"


@pytest.mark.parametrize("cmd", [
    f"curl -s -X POST {_H} http://127.0.0.1:18321/api/ddd/brains/Lib/project-agent/confirm -d '{{}}'",
    f"curl -s -X POST {_H} http://127.0.0.1:18321/api/ddd/runner-agent/confirm -d '{{}}'",
    f'curl -s -X POST {_H} "http://127.0.0.1:18321/api/ddd/brains/"$B"/project-agent/confirm" -d x',
    f'curl -s -X POST {_H} "http://127.0.0.1:18321/api/ddd/brains/$(echo Lib)/project-agent/confirm" -d x',
    f"curl -s -X POST {_H} http://127.0.0.1:18321/api/ddd/brains/Lib/project-agent%2Fconfirm -d x",
    f"curl -s -X POST {_H} http://127.0.0.1:18321/api/ddd/brains/Lib/./project-agent/confirm -d x",
])
def test_a_command_never_sends_the_persons_confirmation(cmd):
    from core import run_owner_gate
    deny = run_owner_gate._user_only_deny(cmd)
    assert deny is not None and "scripts.project_agent" in deny["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_agents_own_routes_are_not_refused():
    from core import run_owner_gate
    for tail in ("agent-confirm", "agent-card"):
        assert run_owner_gate._user_only_deny(
            f"curl -s -X POST http://127.0.0.1:18321/api/ddd/brains/Lib/project-agent/{tail} -d x") is None


def test_the_gate_exports_the_session_before_a_project_agent_line():
    from core import run_owner_gate
    gate = run_owner_gate.create_run_owner_gate({"sdk_session_id": SID})
    cmd = '"$SWARM_BACKEND_BIN" py -m scripts.project_agent confirm --brain Lib --why w'
    out = asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": cmd}}, "t1", None))
    assert out["hookSpecificOutput"]["updatedInput"]["command"].startswith(f"export SWARM_APP_SESSION_ID={SID}")
    # only a plain run of it: a line that merely names the module is not allowed by this gate
    other = asyncio.run(gate({"tool_name": "Bash", "tool_input": {
        "command": "rm -rf build; echo scripts.project_agent"}}, "t2", None))
    assert (other.get("hookSpecificOutput") or {}).get("permissionDecision") != "allow"


def test_the_spec_route_confirms_the_latest_and_says_how_it_went(tmp_path, monkeypatch):
    from core import project_registry
    from routers.swarm_out import router
    monkeypatch.setattr(project_registry, "get_swarmws", lambda: tmp_path)
    monkeypatch.delenv("SWARMAI_MODE", raising=False)
    b = _brain(tmp_path)
    _person(b)
    (b / "4-capabilities" / "s_a" / "run.sh").write_text("echo\n")      # changed since the person's confirm
    app = FastAPI()
    app.include_router(router, prefix="/api/swarm-out")
    r = TestClient(app).post("/api/swarm-out/spec", json={
        "brain": "Lib", "task": "t", "acceptance": "a", "execution": "native", "returns": "answer", "session": SID})
    assert r.status_code == 200
    body = r.json()
    assert body["spec"]["project_agent"]["status"] == "confirmed"
    assert body["spec"]["revision"] == revision(b)
    assert not [x for x in body["refusals"] if "confirm" in x]
