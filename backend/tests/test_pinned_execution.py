"""How a dispatched task runs reaches the Hive's pin, and the Hive acts on it (design
docs/design/2026-10-30-run-spec.md §4.2, invariants 1, 2 and 3).

Questions: (1) does the execution the Host chose go through ``_pin_request`` and the
Hive's ``make_pin`` into the pin unchanged, with the work order's writable clones as the
run's roots; (2) does a pipeline pin's session start with its run already created (the
profile, the requirement and the owner the pin names), a native one with none, and does
a failed create stop the session before its first message; (3) is ``pipeline-v1 create``
refused in a session whose execution is pinned, while ``status`` and an unpinned session
are untouched; (4) does the project section name the run, and does admission refuse a
pipeline under no Brain or a ``direct`` task too long for its requirement.
"""
from __future__ import annotations

import asyncio

import pytest

from core import project_agent_session as pas, swarm_out
from tests.test_project_agent_session import PIN, _bash, _ctx, _router, _run, _ws
from tests.test_task_spec import ws  # noqa: F401 — the fixture

RUN = "run_0123abcd"


def _agent(**kw):
    return {"brain": "Lib", "mode": "release", "revision": "r", "profile": "bugfix", "execution": "pipeline",
            "run_id": RUN, **kw}


def test_the_hosts_choice_reaches_the_pin_unchanged(tmp_path):
    ws = _ws(tmp_path)
    req = swarm_out._pin_request(_agent(), "fix the signer")
    assert req["execution"] == {"how": "pipeline", "profile": "bugfix", "run_id": RUN, "requirement": "fix the signer"}
    pin = pas.make_pin(ws, "Lib", "release", None, req["execution"], {"lib": "/w/lib"})
    assert pin["execution"] == {"how": "pipeline", "profile": "bugfix", "run_id": RUN,
                                "requirement": "fix the signer", "repos": {"lib": "/w/lib"}}
    native = swarm_out._pin_request(_agent(execution="native", profile=None, run_id=None))
    assert pas.make_pin(ws, "Lib", "release", None, native["execution"])["execution"] == {"how": "native"}
    assert "execution" not in pas.make_pin(ws, "Lib", "release")         # a dispatch from before
    long = swarm_out._pin_request(_agent(), "x" * 5000)
    assert long["execution"]["requirement"] is None                       # the run writes its own
    for bad in ({"how": "both"}, {"how": "pipeline", "profile": "nope", "run_id": RUN},
                {"how": "pipeline", "profile": "full", "run_id": "../x"}):
        with pytest.raises(pas.PinError):
            pas.make_pin(ws, "Lib", "release", None, bad)
    assert pas._valid_pin({**PIN, "execution": {"how": "x"}}).get("broken")
    assert pas._valid_pin({**PIN, "execution": {"how": "native"}}) == {**PIN, "execution": {"how": "native"}}


def test_a_pipeline_pin_creates_its_run_owned_by_the_session(tmp_path, monkeypatch):
    import core.project_registry as preg
    from core.run_protocol import cli_commands
    ws = _ws(tmp_path)
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    monkeypatch.setattr(cli_commands, "_workspace", lambda: ws)
    pin = pas.make_pin(ws, "Lib", "release", None,
                       {"how": "pipeline", "profile": "research", "run_id": RUN, "requirement": "why is it slow"})
    assert pas.create_pinned_run("sess-0001", pin) == RUN
    from config import get_app_data_dir
    from core.run_protocol.engine import RunEngine
    engine = RunEngine.load(data_dir=get_app_data_dir(), workspace=ws, project="Lib", run_id=RUN)
    assert (engine.contract["profile"], engine.contract["session_id"], engine.contract["requirement"]) == \
        ("research", "sess-0001", "why is it slow")
    assert pas.create_pinned_run("sess-0002", pas.make_pin(ws, "Lib", "release", None, {"how": "native"})) is None
    assert pas.create_pinned_run("sess-0003", pas.make_pin(ws, "Lib", "release")) is None
    with pytest.raises(Exception):
        pas.create_pinned_run("sess-0004", pin)                           # the id is taken: no second run


@pytest.mark.asyncio
async def test_the_router_creates_the_run_before_the_first_message(monkeypatch):
    order = []

    def create(sid, pin):
        order.append(("create", sid, pin["execution"]["run_id"]))
        return RUN
    monkeypatch.setattr(pas, "create_pinned_run", create)

    def send(**kw):
        order.append(("send",))

        async def gen():
            yield {"type": "result", "sessionId": "s-run"}
        return gen()
    pin = {**PIN, "execution": {"how": "pipeline", "profile": "bugfix", "run_id": RUN, "requirement": None, "repos": {}}}
    await _run(_router(), "s-run", pin, send)
    assert order == [("create", "s-run", RUN), ("send",)]

    def fail(sid, pin):
        raise RuntimeError("no")
    monkeypatch.setattr(pas, "create_pinned_run", fail)
    ran = []

    def send2(**kw):
        ran.append(1)

        async def gen():
            yield {"type": "result"}
        return gen()
    _u, out = await _run(_router(), "s-run2", pin, send2)
    assert any(e.get("code") == "PROJECT_AGENT_NOT_RECORDED" for e in out) and not ran


def test_create_is_refused_where_the_execution_is_pinned(monkeypatch):
    from core.security_hooks import create_dangerous_command_gate
    from unittest.mock import MagicMock
    create = '"$SWARM_BACKEND_BIN" pipeline-v1 create --project Lib --profile direct --requirement x'
    status = f'"$SWARM_BACKEND_BIN" pipeline-v1 status --project Lib --run-id {RUN}'
    for ex in ({"how": "pipeline", "profile": "bugfix", "run_id": RUN}, {"how": "native"}):
        gate = create_dangerous_command_gate(_ctx({**PIN, "execution": ex}), "k", MagicMock())
        out = _bash(gate, create)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "decided when the task was dispatched" in out["hookSpecificOutput"]["permissionDecisionReason"]
        assert _bash(gate, status) == {"decision": "approve"}
        assert _bash(gate, 'grep -n "pipeline-v1 create" SKILL.md') == {"decision": "approve"}
        assert _bash(gate, "echo pipeline-v1 create") == {"decision": "approve"}   # named, not run
    unpinned = create_dangerous_command_gate(_ctx(PIN), "k", MagicMock())
    assert _bash(unpinned, create) == {"decision": "approve"}


def test_the_project_section_names_the_run_and_admission_checks_the_pipeline(ws):
    from core import task_spec as ts
    from tests.test_task_spec import _brain, _build
    _brain(ws)
    spec = _build(ws, "Lib", "x", "the signer test passes", execution="pipeline:bugfix")
    text = ts.segment(spec, RUN)
    assert f"pipeline run `{RUN}`" in text and f"--run-id {RUN}" in text and "do not create another" in text
    assert "--run-id" not in ts.segment(_build(ws, "Lib", "x", "y"), None)          # native names no run
    direct = _build(ws, "Lib", "x" * 4100, "y", execution="pipeline:direct")
    assert any("at most" in r for r in ts.admit(direct))
    general = _build(ws, "General", "x", "y", execution="pipeline:research", returns="answer")
    assert any("belongs to a Brain's project" in r for r in ts.admit(general))


@pytest.mark.asyncio
async def test_the_hives_chat_pins_the_execution_on_the_orders_writable_clones(async_client, tmp_path, monkeypatch):
    from core import hive_workspace as hw
    from core.project_agent import revision
    from routers import chat as chat_router
    import core.project_registry as preg
    ws = _ws(tmp_path)
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    seen = {}

    class _Router:
        async def run_conversation(self, **kw):
            seen.update(kw)
            yield {"type": "session_start", "sessionId": "s-ex"}
            yield {"type": "result", "sessionId": "s-ex"}
    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())

    async def record(sid, repos):
        return None
    monkeypatch.setattr(chat_router, "_record_task_repos", record)
    wid = "so_" + "d" * 12
    order = {"work_id": wid, "created_at": "x", "prepared_at": 1.0, "session_id": None, "outputs": f"Outputs/{wid}",
             "repos": [{"name": "lib", "url": "https://github.com/acme/lib.git", "ref": "main", "sha": "a" * 40,
                        "access": "write", "dir": hw.repo_rel(wid, "lib"), "branch": hw.branch(wid)},
                       {"name": "docs", "url": "https://github.com/acme/docs.git", "ref": "main", "sha": "b" * 40,
                        "access": "read", "dir": hw.repo_rel(wid, "docs"), "branch": None}]}
    hw._write_order(order)
    ex = {"how": "pipeline", "profile": "bugfix", "run_id": RUN, "requirement": "fix it"}
    r = await async_client.post("/api/chat/stream", json={
        "agent_id": "default", "message": "hi", "work_id": wid,
        "project_agent": {"brain": "Lib", "mode": "release", "revision": revision(ws / "Projects" / "Lib"),
                          "execution": ex}})
    assert r.status_code == 200
    assert seen["project_agent"]["execution"] == {**ex, "repos": {"lib": str(ws / hw.repo_rel(wid, "lib"))}}
