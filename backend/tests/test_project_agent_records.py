"""What a run under a Project Agent records (design 2026-10-28-project-agent-flow §7 ⑦):
which of its checks ran (from its Bash calls, against this machine's own list), how
many times a Brain gate refused something (counted on the Hive into the turn's
outcome), and the Brain/mode/profile from the host's dispatch row — with nothing the
Hive chose reaching /runs as text.
"""
from __future__ import annotations

import asyncio

from core import swarm_out_results as sor

AGENT = {"brain": "Lib", "mode": "code-change", "revision": "r" * 64, "profile": "bugfix",
         "delivery": "bundle", "acceptance": "tests pass", "gates": ["no_git_push"],
         "validations": [{"id": "tests", "command": "pytest -q"}, {"id": "lint", "command": "ruff check ."},
                         {"id": "build", "command": "npm run build"}]}


def _bash(i: str, cmd: str, error: bool) -> list[dict]:
    return [{"role": "assistant", "content": [
        {"type": "tool_use", "id": i, "name": "Bash", "summary": f"Running: {cmd[:191]}",
         "display": {"tool": "Bash", "object": cmd, "key": "k"}}]},
        # persisted results sit in assistant rows (as the Hive's messages API returns them)
        {"role": "assistant", "content": [{"type": "tool_result", "tool_use_id": i, "is_error": error}]}]


def _messages(*bash, refusals=None) -> list[dict]:
    rows = [{"role": "user", "content": [{"type": "text", "text": "go"}]}]
    for b in bash:
        rows += b
    outcome = {"type": "turn_outcome", "ended": "completed", "at": "2026-10-29T01:00:00+00:00"}
    if refusals is not None:
        outcome["gate_refusals"] = refusals
    rows.append({"role": "assistant", "content": [{"type": "text", "text": "done"}, outcome]})
    return rows


def test_checks_come_from_the_runs_bash_calls():
    res = sor.summarize(_messages(_bash("a", "cd backend &&  pytest   -q tests/", False),
                                  _bash("b", "npm run build", False), _bash("c", "npm run build", True)),
                        agent=AGENT)
    assert res["checks"] == [{"id": "tests", "status": "ran"}, {"id": "lint", "status": "not_run"},
                             {"id": "build", "status": "errored"}]       # the last build call errored
    assert sor.summarize(_messages(), agent=None)["checks"] is None


def test_gate_refusals_add_up_across_turns_and_reach_runs_only_under_known_names():
    rows = _messages(refusals={"no_git_push": 2, "integrity": 1})
    rows += _messages(refusals={"no_git_push": 1, "<b>x</b>": 5, "evil gate": 3})
    res = sor.summarize(rows, agent=AGENT)
    assert res["gate_refusals"] == {"no_git_push": 3, "integrity": 1}
    pub = sor.public({"result": {**res, "gate_refusals": {**res["gate_refusals"], "other_gate": 4}}},
                     [], AGENT)
    assert pub["gate_refusals"] == {"integrity": 1, "no_git_push": 3}


def test_runs_carries_the_hosts_agent_and_only_the_hosts_check_ids():
    res = sor.summarize(_messages(_bash("a", "pytest -q", False)), agent=AGENT)
    res["checks"].append({"id": "made-up", "status": "ran"})
    res["checks"][1]["status"] = "passed"                          # not a status /runs knows
    pub = sor.public({"result": res}, [], AGENT)
    assert pub["project_agent"] == {"brain": "Lib", "mode": "code-change", "profile": "bugfix",
                                    "delivery": "bundle", "execution": None}   # a row from before execution
    run = sor.public({"result": res}, [], {**AGENT, "execution": "pipeline"})
    assert run["project_agent"]["execution"] == "pipeline"
    assert sor.public({"result": res}, [], {**AGENT, "execution": "made-up"})["project_agent"]["execution"] is None
    assert pub["checks"] == [{"id": "tests", "status": "ran"}, {"id": "lint", "status": "not_run"},
                             {"id": "build", "status": "not_run"}]
    old = sor.public({"result": res}, [], None)
    assert old["project_agent"] is None and old["checks"] is None and old["gate_refusals"] is None


def test_the_fetch_summarises_with_the_rows_agent(monkeypatch, tmp_path):
    seen = {}
    real = sor.summarize

    def spy(body, has_more=False, agent=None):
        seen["agent"] = agent
        return real(body, has_more, agent)
    monkeypatch.setattr(sor, "summarize", spy)
    import httpx
    import core.swarm_out as so
    monkeypatch.setattr(sor, "_dir", lambda: tmp_path)

    async def instance(ref):
        return {"id": "h", "name": "h"}
    monkeypatch.setattr(so, "_instance", instance)
    monkeypatch.setattr(so, "_client", lambda inst, timeout=30: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_messages())), base_url="http://h"))
    row = {"dispatch_id": "so_" + "a" * 12, "hive_id": "h", "session_id": "s" * 8, "project_agent": AGENT}
    asyncio.run(sor.fetch(row))
    assert seen["agent"] == AGENT


def test_a_brain_gate_refusal_is_counted_for_the_turn():
    from core import project_agent_session as pas
    ctx: dict = {}
    assert pas.refusal_label("Brain gate no_git_push refused this: no") == "no_git_push"
    assert pas.refusal_label("The Brain's gates were changed since this session started") == "integrity"
    ctx[pas.PIN_KEY] = {"brain": "Lib"}
    ctx[pas.REFUSALS_KEY] = {"no_git_push": 2}
    assert pas.take_refusals(ctx) == {"no_git_push": 2} and pas.take_refusals(ctx) == {}


def test_the_hook_counts_and_the_outcome_carries_the_refusals(monkeypatch):
    from core import project_agent_session as pas
    from core.session_router import SessionRouter
    ctx: dict = {}

    async def pin(_ctx):
        return {"brain": "Lib", "gates": ["g"]}
    monkeypatch.setattr(pas, "current_pin", pin)
    from core import executors

    async def run_in(_name, fn, *a):
        return "Brain gate g refused this: nope"
    monkeypatch.setattr(executors, "run_in", run_in)
    hook = pas.create_brain_gate_hook(ctx)
    out = asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "x"}}, "t", None))
    asyncio.run(hook({"tool_name": "Bash", "tool_input": {"command": "x"}}, "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny" and ctx[pas.REFUSALS_KEY] == {"g": 2}

    class _Unit:
        _hook_session_context = ctx

    class _Writer:
        async def record_outcome(self, block):
            self.block = block
            return True

    class _Outcome:
        def block(self, repos):
            return {"type": "turn_outcome"}

    class _Self:
        _units = {"s1": _Unit()}

        async def _task_repo_facts(self, sid):
            return None
    w = _Writer()
    block = asyncio.run(SessionRouter._write_turn_outcome(_Self(), w, "s1", _Outcome()))
    assert block["gate_refusals"] == {"g": 2} and pas.REFUSALS_KEY not in ctx
    block = asyncio.run(SessionRouter._write_turn_outcome(_Self(), w, "s1", _Outcome()))
    assert "gate_refusals" not in block


def test_only_bash_calls_count_as_running_a_check():
    write = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "w", "name": "Write", "display": {"tool": "Write", "object": "pytest -q"}},
        {"type": "tool_result", "tool_use_id": "w", "is_error": False}]}]
    res = sor.summarize(_messages(write), agent=AGENT)
    assert res["checks"][0] == {"id": "tests", "status": "not_run"}


def test_runs_rows_get_the_agent_part(monkeypatch):
    res = sor.summarize(_messages(_bash("a", "pytest -q", False)), agent=AGENT)
    monkeypatch.setattr(sor, "_read_kept", lambda ids: {i: {"result": res} for i in ids})
    monkeypatch.setattr(sor, "check_due", lambda *a: False)
    rows = [{"dispatch_id": "so_" + "b" * 12, "hive_id": "h", "session_id": "s", "repos": [],
             "project_agent": AGENT}]
    asyncio.run(sor.attach(rows, {"h": "running"}))
    assert rows[0]["result"]["project_agent"]["brain"] == "Lib"
    assert rows[0]["result"]["checks"][0] == {"id": "tests", "status": "ran"}


# ── review round 1 ───────────────────────────────────────────────────────────

def test_a_check_is_run_only_when_a_call_runs_it_not_when_it_mentions_it():
    mentions = [_bash("g", 'grep -rn "pytest -q" .', False), _bash("e", "echo 'later: npm run build'", False),
                _bash("h", "cat > s.sh <<EOF\nruff check .\nEOF", False)]
    res = sor.summarize(_messages(*mentions), agent=AGENT)
    assert [c["status"] for c in res["checks"]] == ["not_run", "not_run", "not_run"]
    no_result = [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "x", "name": "Bash", "display": {"object": "x; npm run build"}}]}]
    assert sor.summarize(_messages(no_result), agent=AGENT)["checks"][2] == {"id": "build", "status": "unknown"}
    long_id = {**AGENT, "validations": [{"id": "t" * 120, "command": "pytest -q"}]}
    res = sor.summarize(_messages(_bash("a", "pytest -q", False)), agent=long_id)
    assert sor.public({"result": res}, [], long_id)["checks"] == [{"id": "t" * 120, "status": "ran"}]


def test_none_counted_differs_from_a_hive_that_does_not_count():
    assert sor.summarize(_messages(), agent=AGENT)["gate_refusals"] is None
    res = sor.summarize(_messages(refusals={}), agent=AGENT)
    assert res["gate_refusals"] == {} and sor.public({"result": res}, [], AGENT)["gate_refusals"] == {}
    assert sor.public({"result": sor.summarize(_messages(), agent=AGENT)}, [], AGENT)["gate_refusals"] is None
    res = sor.summarize(_messages(refusals={"error:no_git_push": 1, "error:other": 2}), agent=AGENT)
    assert sor.public({"result": res}, [], AGENT)["gate_refusals"] == {"error:no_git_push": 1}


def test_checks_are_partial_when_older_turns_were_not_read():
    res = sor.summarize(_messages(), has_more=True, agent=AGENT)
    assert res["checks_partial"] and sor.public({"result": res}, [], AGENT)["checks_partial"]


def test_refusal_labels_name_a_gate_that_could_not_run():
    from core import project_agent_session as pas
    assert pas.refusal_label("Brain gate lint failed (exit 1)") == "error:lint"
    assert pas.refusal_label("Brain gate lint did not answer within 20s") == "error:lint"
    assert pas.refusal_label("Brain gate lint could not start (OSError)") == "error:lint"
    assert pas.refusal_label("Brain gate lint is not in the Brain's 3-gates/") == "integrity"
    assert pas.take_refusals({}) is None and pas.take_refusals({"sdk_session_id": "s"}) is None
    assert pas.take_refusals({pas.PIN_KEY: {"brain": "Lib"}}) == {}


def test_counts_an_outcome_could_not_write_go_to_the_next_one():
    import pytest
    from core import project_agent_session as pas
    from core.session_router import SessionRouter
    ctx = {pas.PIN_KEY: {"brain": "Lib"}, pas.REFUSALS_KEY: {"g": 2}}

    class _Unit:
        _hook_session_context = ctx

    class _Self:
        _units = {"s1": _Unit()}

        async def _task_repo_facts(self, sid):
            return None

    class _Outcome:
        def block(self, repos):
            return {"type": "turn_outcome"}

    class _Refuses:
        async def record_outcome(self, block):
            return False

    class _Raises:
        async def record_outcome(self, block):
            raise asyncio.CancelledError()
    assert asyncio.run(SessionRouter._write_turn_outcome(_Self(), _Refuses(), "s1", _Outcome())) is None
    assert ctx[pas.REFUSALS_KEY] == {"g": 2}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(SessionRouter._write_turn_outcome(_Self(), _Raises(), "s1", _Outcome()))
    assert ctx[pas.REFUSALS_KEY] == {"g": 2}


def test_the_run_is_checked_against_how_it_was_dispatched():
    """run-spec §4.2 invariant 6: declared native but a run was made, declared pipeline
    but none ran (only once the run ended), another profile or another run: differs."""
    ex = sor.execution_check
    native, piped = {**AGENT, "execution": "native"}, {**AGENT, "execution": "pipeline", "run_id": "run_0123abcd"}
    run = {"run_id": "run_0123abcd", "profile": "bugfix"}
    assert ex({}, native) == {"state": "matches", "why": None}
    assert ex({"pipeline": run}, native) == {"state": "differs", "why": "made_a_run"}
    assert ex({"ended": "completed"}, piped) == {"state": "differs", "why": "no_run"}
    assert ex({}, piped) == {"state": "not_yet", "why": None}                  # still running
    assert ex({"ended": "completed", "provisional": True}, piped)["state"] == "not_yet"
    assert ex({"pipeline": run}, piped) == {"state": "matches", "why": None}
    assert ex({"pipeline": {**run, "profile": "direct"}}, piped)["why"] == "other_profile"
    assert ex({"pipeline": {**run, "run_id": "run_ffffffff"}}, piped)["why"] == "other_run"
    assert ex({}, AGENT) is None                                               # a row from before
    # from real session blocks: a run that never reached an end status still ran
    stage = {"role": "assistant", "content": [{"type": "pipeline_stage", "runId": "run_0123abcd", "stage": "evaluate",
                                               "status": "in_progress", "profile": "bugfix"}]}
    res = sor.summarize([{"role": "user", "content": "go"}, stage], agent=piped)
    assert res["pipeline"] is None and ex({**res, "ended": "completed"}, piped) == {"state": "matches", "why": None}
    assert ex({**res, "ended": "completed"}, native)["why"] == "made_a_run"
    other = {"role": "assistant", "content": [{"type": "pipeline_summary", "runId": "run_ffffffff", "profile": "trivial",
                                               "status": "abandoned"}]}
    res = sor.summarize([{"role": "user", "content": "go"}, stage, other], agent=piped)
    assert ex({**res, "ended": "completed"}, piped)["state"] == "matches"      # its own run, not the other
    assert sor.summarize([{"role": "user", "content": "go"}, other], agent=piped)["pipeline"]["run_id"] == "run_ffffffff"
    pub = sor.public({"result": {"pipeline": {**run, "profile": "direct"}}}, [], piped)
    assert pub["execution_check"] == {"state": "differs", "why": "other_profile"}
    assert sor.public({"result": {}}, [], None)["execution_check"] is None
