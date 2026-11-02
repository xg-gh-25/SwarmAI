"""The Swarm Out dispatch card and the autonomous dispatch (design
docs/design/2026-10-30-swarm-out-dispatch-card.md).

Questions: (1) does pick_hive choose deterministically and refuse when none can take a
task; (2) is a gone-out card built from this machine's dispatch log row only (a printed
row the log does not hold is no card), with every field the card shows; (3) is a refused
dispatch a failure card that says nothing was sent; (4) does a real Bash dispatch call
through the orchestrator yield one persisted card after its tool_result, also when the
call failed; (5) is the card non-conversational, and does its text reach a channel
reply; (6) are non-owner channel sessions refused by the CLI and by the Bash handler.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from core import swarm_out, swarm_out_card as card

ROW = {"dispatch_id": "so_b150abc3fd86", "hive_id": "h1", "hive": "explorer", "session_id": "0a7e6d08-f227",
       "title": "SwarmAI weekly report", "origin": "swarm-out", "sent_at": "2026-10-30T05:25:50Z",
       "repos": [{"url": "https://github.com/xg-gh-25/SwarmAI.git", "ref": "main",
                  "dir": "Hive/repos/github.com/xg-gh-25/SwarmAI", "sha": "a680d1a0" + "0" * 32}],
       "project_agent": {"brain": "SwarmAI", "mode": "research", "revision": "fcdbf4dc" + "0" * 56,
                         "profile": "research", "delivery": "bundle", "acceptance": "a report exists",
                         "execution": "pipeline", "execution_why": "a report from the week's commits"},
       "brief": "Read the week's commits and write what changed.", "hive_reason": "idle",
       "brain_sync": "synced", "repo_notes": [{"name": "swarmai", "url": "https://github.com/xg-gh-25/SwarmAI.git", "dirty": 3}]}
CMD = ('cd ~/.swarm-ai/SwarmWS && "$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch '
       '--title "SwarmAI weekly report" --brief "x" --brain SwarmAI --task-file /tmp/t.md --acceptance a --revision r')


def H(name, status="running", reachable=True, saturated=False, streaming=0, mem=40.0, capable=True):
    return {"name": name, "status": status, "reachable": reachable, "saturated": saturated,
            "streaming": streaming, "memory_percent": mem, "capable": capable}


def test_a_hive_holding_a_run_that_waits_on_the_person_is_not_idle():
    """design agent-owns-its-runs §2.8: two runs went to demo while explorer was free,
    because a run waiting on the person streams nothing."""
    both_quiet = [H("demo", streaming=0, mem=10), H("explorer", streaming=0, mem=10)]
    assert swarm_out.pick_hive(both_quiet) == ("demo", "idle")
    assert swarm_out.pick_hive(both_quiet, {"demo": 1}) == ("explorer", "idle")
    assert swarm_out.pick_hive(both_quiet, {"demo": 2, "explorer": 1}) == ("explorer", "least_busy:1")


def test_open_runs_count_only_unsettled_runs_under_a_project_agent(tmp_path, monkeypatch):
    from core import swarm_out_settle
    rows = [{"dispatch_id": f"so_{i:012x}", "hive": h, **({"project_agent": {}} if pa else {})}
            for i, (h, pa) in enumerate([("demo", True), ("demo", True), ("demo", False), ("explorer", True)])]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: rows)
    monkeypatch.setattr(swarm_out, "_state_dir", lambda: tmp_path)
    swarm_out_settle._update(rows[1]["dispatch_id"], lambda rec: rec.update(settled={"outcome": "passed"}))
    swarm_out_settle._update(rows[3]["dispatch_id"], lambda rec: rec.update(abandoned_at="x"))
    assert swarm_out.open_runs_by_hive() == {"demo": 1}
    # only a tie-breaker: a log it cannot read, or a row without an id, never fails a dispatch
    monkeypatch.setattr(swarm_out, "_read_log", lambda: (_ for _ in ()).throw(OSError("unreadable")))
    assert swarm_out.open_runs_by_hive() == {}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [{"dispatch_id": 7, "hive": "demo", "project_agent": {}}])
    assert swarm_out.open_runs_by_hive() == {}


def test_pick_hive_is_deterministic_and_refuses_when_none_can_take_it():
    assert swarm_out.pick_hive([H("b", streaming=1), H("a", streaming=1, mem=20), H("c", streaming=2)]) \
        == ("a", "least_busy:1")
    assert swarm_out.pick_hive([H("b", streaming=0, mem=10), H("a", streaming=0, mem=10)])[0] == "a"   # name breaks a tie
    assert swarm_out.pick_hive([H("x"), H("y", saturated=True)]) == ("x", "only_free")
    assert swarm_out.pick_hive([H("x"), H("z", streaming=3)]) == ("x", "idle")
    for none in ([], [H("s", status="stopped")], [H("u", reachable=False)], [H("f", saturated=True)],
                 [H("n", streaming=None)]):
        with pytest.raises(swarm_out.SwarmOutError, match="no running Hive"):
            swarm_out.pick_hive(none)
    # zero-touch Z6: a Hive whose version lacks a required capability is never picked,
    # even when it is the idle one; with none capable, the agent is told to update one
    assert swarm_out.pick_hive([H("old", capable=False), H("new", streaming=2)]) == ("new", "only_free")
    for none in ([H("old", capable=False)], [H("unknown", capable=None)]):
        with pytest.raises(swarm_out.SwarmOutError, match="update one"):
            swarm_out.pick_hive(none)


def test_a_card_comes_from_the_dispatch_log_never_from_the_printed_row(monkeypatch):
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [ROW])
    printed = json.dumps({**ROW, "hive": "somewhere-else", "title": "lies"})
    b = card.card(CMD, printed)
    assert b["type"] == "swarm_out_dispatch" and b["ok"] is True
    assert (b["hive"], b["title"], b["brain"], b["mode"], b["profile"]) == (
        "explorer", "SwarmAI weekly report", "SwarmAI", "research", "research")
    assert b["repos"] == [{"name": "swarmai", "ref": "main", "sha": ROW["repos"][0]["sha"], "dirty": 3, "access": None}]
    ro = card.from_row({**ROW, "repos": [{**ROW["repos"][0], "access": "read"}]})
    assert ro["repos"][0]["access"] == "read" and ", read-only" in ro["text"]
    assert (b["sessionId"], b["hiveReason"], b["brainSync"], b["delivery"]) == ("0a7e6d08-f227", "idle", "synced", "bundle")
    assert "3 uncommitted files not sent" in b["text"] and "so_b150abc3fd86" in b["text"]
    assert "• Hive: explorer (idle)" in b["text"]
    assert (b["execution"], b["executionWhy"]) == ("pipeline", "a report from the week's commits")
    assert "• Runs: pipeline research (a report from the week's commits)" in b["text"]
    native = card.from_row({**ROW, "project_agent": {**ROW["project_agent"], "execution": "native", "profile": None}})
    assert "• Runs: native" in native["text"]
    older = card.from_row({**ROW, "project_agent": {**ROW["project_agent"], "execution": "x"}})
    assert older["execution"] is None and "• Runs:" not in older["text"]   # a row from before the choice
    assert [card.hive_reason_text(c) for c in ("named", None, "only_free", "least_busy:2", "odd")] == [
        None, None, "the only one free", "the least busy (2 running)", "odd"]
    # an id the log does not hold is no card at all
    assert card.card(CMD, json.dumps({**ROW, "dispatch_id": "so_" + "f" * 12})) is None
    assert card.card(CMD, json.dumps({"runs": []})) is None
    assert card.card(CMD, "not json")["maybeSent"] is True   # no answer: it may have gone out


def test_the_card_is_this_calls_dispatch_as_the_cli_prints_it(monkeypatch):
    """What the CLI really prints (indent=2, maybe more lines after it); only a row sent
    after the call was seen; an echoed older id is no card of this call."""
    import time
    from datetime import datetime, timezone
    now = time.time()
    fresh = {**ROW, "sent_at": datetime.fromtimestamp(now, timezone.utc).isoformat()}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [ROW, {**fresh, "dispatch_id": "so_" + "a" * 12}])
    printed = json.dumps({**fresh, "dispatch_id": "so_" + "a" * 12}, indent=2) + "\nexit=0\n"
    assert card.card(CMD, printed, since=now - 1)["dispatchId"] == "so_" + "a" * 12
    assert card.card(CMD, "Exit code 0\n" + printed, since=now - 1)["ok"] is True
    # an earlier dispatch's id, printed again by this call: not this call's dispatch
    assert card.card(CMD, json.dumps(ROW, indent=2), since=now) is None
    # a refusal followed by an echoed older id: the refusal is what the CLI said
    replay = 'Exit code 1\n{"ok": false, "detail": "a draft"}\n' + json.dumps({"dispatch_id": ROW["dispatch_id"]})
    assert card.card(CMD, replay, since=now)["ok"] is False
    # no answer at all (stopped at the Bash timeout, sent to the background): not sure
    m = card.card(CMD, "Command timed out after 2m 0.0s", since=now)
    assert (m["ok"], m["maybeSent"]) == (False, True)
    # a wrong command line ran nothing
    assert card.card(CMD, "usage: swarm_out dispatch [-h] ...\nerror: the following arguments are required", since=now) is None


def test_channel_text_is_whole_only_in_a_dm_and_carries_no_markup(monkeypatch):
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [ROW])
    b = card.card(CMD, json.dumps(ROW))
    assert card.channel_text(b, direct=True) == b["text"] and "Done when" in b["text"]
    group = card.channel_text(b, direct=False)
    assert group.splitlines() == [b["text"].splitlines()[0], b["text"].splitlines()[-1]]
    f = card.failure("<!channel> <https://evil|re-auth> & more", "t", "B", None)
    assert "<" not in f["text"] and ">" not in f["text"] and "‹!channel›" in f["text"]
    assert card.channel_text(f, direct=False).splitlines()[1].startswith("‹!channel›")


def test_a_refused_dispatch_is_a_failure_card(monkeypatch):
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [])
    b = card.card(CMD, '{"ok": false, "status": 400, "detail": "SwarmAI cannot be sent to a Hive: the definition was changed"}')
    assert (b["ok"], b["brain"], b["title"], b["dispatchId"]) == (False, "SwarmAI", "SwarmAI weekly report", None)
    assert "definition was changed" in b["error"] and "Nothing was sent" in b["text"]
    assert card.watched(CMD) and not card.watched('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out spec --brain X')
    # no answer in time: it may be running there, never "nothing was sent"
    m = card.card(CMD, '{"ok": false, "maybe_sent": true, "detail": "the task may be running there"}')
    assert (m["ok"], m["maybeSent"]) == (False, True)
    assert "Nothing was sent" not in m["text"] and "may be running on the Hive" in m["text"]
    assert b["maybeSent"] is False


# ── through the orchestrator ──

def _bash(i, c):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock
    return AssistantMessage(content=[ToolUseBlock(id=i, name="Bash", input={"command": c})], model="m")


def _res(i, text, is_error=False, carrier="user"):
    """The tool_result, in a UserMessage or (the other path the SDK takes) an AssistantMessage."""
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, UserMessage
    blk = ToolResultBlock(tool_use_id=i, content=text, is_error=is_error)
    return UserMessage(content=[blk]) if carrier == "user" else AssistantMessage(content=[blk], model="m")


def _cards(events):
    return [b for e in events if e.get("type") == "assistant"
            for b in e.get("content") or [] if isinstance(b, dict) and b.get("type") == "swarm_out_dispatch"]


@pytest.fixture
def _isolated_queue():
    from core.permission_manager import permission_manager
    permission_manager.clear_session("sess-test")
    yield
    permission_manager.clear_session("sess-test")


@pytest.mark.usefixtures("_isolated_queue")
@pytest.mark.parametrize("carrier", ["user", "assistant"])
@pytest.mark.parametrize("is_error,output,ok", [
    (False, json.dumps(ROW), True),
    (True, '{"ok": false, "detail": "no running Hive can take a task now"}', False)])
def test_a_dispatch_call_yields_one_persisted_card_after_its_result(monkeypatch, is_error, output, ok, carrier):
    from datetime import datetime, timezone
    from tests.test_tool_call_leak_guard import _drive, _make_orchestrator, _make_result_message
    now = {**ROW, "sent_at": datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [now])
    printed = ("Exit code 1\n" + output) if is_error else json.dumps(now, indent=2)
    events, raised = asyncio.run(_drive(_make_orchestrator(), [
        _bash("tu1", CMD), _res("tu1", printed, is_error, carrier), _make_result_message()]))
    assert raised is None
    (b,) = _cards(events)
    assert b["ok"] is ok
    # after the tool_result, before the turn's result
    types = [e.get("type") for e in events]
    at = next(i for i, e in enumerate(events) if e.get("type") == "assistant" and _cards([e]))
    # the call's own result event (a UserMessage result is a tool_result; one inside an
    # AssistantMessage comes out in the assistant event that carries it)
    res = next(i for i, e in enumerate(events) if e.get("type") == "tool_result"
               or (e.get("type") == "assistant" and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                                         for c in e.get("content") or [])))
    assert res < at < types.index("result")
    # an older dispatch's row printed by this call: the orchestrator passes when the call
    # was seen, so it is no card
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [ROW])
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _bash("tu3", CMD), _res("tu3", json.dumps(ROW, indent=2), False, carrier), _make_result_message()]))
    assert _cards(events) == []
    # another command yields none
    events, _ = asyncio.run(_drive(_make_orchestrator(), [
        _bash("tu2", "git status"), _res("tu2", json.dumps(ROW)), _make_result_message()]))
    assert _cards(events) == []


def test_the_card_is_not_conversation():
    """The resume context leaves the card out (its channel text: test_channel_gateway)."""
    from core import context_injector
    assert "swarm_out_dispatch" in context_injector._TOOL_ONLY_TYPES


# ── who may dispatch ──

def test_a_non_owner_channel_session_is_refused(monkeypatch, capsys, tmp_path):
    from core.knowledge_cli import RESTRICTED_ENV
    from core.security_hooks import create_file_access_permission_handler, owner_command_denial
    from scripts import swarm_out as cli
    monkeypatch.setenv(RESTRICTED_ENV, "1")
    for argv in (["runs"], ["hives"], ["dispatch", "--title", "t", "--brain", "B", "--task", "x", "--execution", "native",
                                       "--returns", "answer", "--acceptance", "a", "--revision", "r"]):
        assert cli.main(argv) == 1 and "only available to the owner" in capsys.readouterr().out
    for c in ('"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --brain B',
              "curl -s http://127.0.0.1:18321/api/swarm-out/dispatch -d @x",
              "python -m scripts.swa''rm_out runs"):
        assert owner_command_denial(c) == "Swarm Out is only available to the owner"
    assert owner_command_denial("git status") is None
    channel = create_file_access_permission_handler([str(tmp_path)], [], deny_knowledge=True)
    res = asyncio.run(channel("Bash", {"command": '"$SWARM_BACKEND_BIN" py -m scripts.swarm_out runs'}, {}))
    assert res["behavior"] == "deny" and "owner" in res["message"]
    owner = create_file_access_permission_handler([str(tmp_path)])
    assert asyncio.run(owner("Bash", {"command": '"$SWARM_BACKEND_BIN" py -m scripts.swarm_out runs'}, {}))["behavior"] == "allow"


def test_a_dispatch_whose_output_was_redirected_is_shown_from_this_sessions_log_row(monkeypatch):
    """A dispatch call that printed nothing (its output went to a file) but went out:
    the card comes from the log row this session dispatched after the call started, as
    if the id had been printed. Not this session's, sent before the call, or more than
    one candidate: still "not sure" (a second dispatch would run the task twice)."""
    import time
    from datetime import datetime, timezone
    now = time.time()
    at = datetime.fromtimestamp(now, timezone.utc).isoformat()
    mine = {**ROW, "dispatch_id": "so_" + "b" * 12, "sent_at": at, "dispatched_from": "sess-a"}
    redirected = CMD + " > /tmp/dispatch.json 2>&1"
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [ROW, mine])
    b = card.card(redirected, "", since=now - 1, session_id="sess-a")
    assert (b["ok"], b["dispatchId"], b["hive"]) == (True, "so_" + "b" * 12, "explorer")
    # another session's dispatch, or none named: not this call's
    for sid in ("sess-b", None):
        m = card.card(redirected, "", since=now - 1, session_id=sid)
        assert (m["ok"], m["maybeSent"]) == (False, True)
    # sent before this call started: an earlier dispatch of this session
    m = card.card(redirected, "", since=now + 60, session_id="sess-a")
    assert (m["ok"], m["maybeSent"]) == (False, True)
    # a row whose title is not this call's --title
    other = {**mine, "title": "another task"}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [other])
    assert card.card(redirected, "", since=now - 1, session_id="sess-a")["maybeSent"] is True
    # two rows that both fit: which is this call's is not known
    twin = {**mine, "dispatch_id": "so_" + "c" * 12}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [mine, twin])
    assert card.card(redirected, "", since=now - 1, session_id="sess-a")["maybeSent"] is True


def test_a_loop_of_dispatches_shows_each_task_it_sent(monkeypatch):
    """A call whose --title the shell expanded (a loop over Brains) and whose output was
    not the CLI's JSON may have sent several tasks: each row this session dispatched
    after it started is a card, in the order sent. A literal title keeps the one-row
    rule; nothing sent stays "not sure"."""
    import time
    from datetime import datetime, timezone
    now = time.time()
    at = lambda dt: datetime.fromtimestamp(now + dt, timezone.utc).isoformat()  # noqa: E731
    first = {**ROW, "dispatch_id": "so_" + "1" * 12, "title": "check A", "sent_at": at(1), "dispatched_from": "sess-a"}
    second = {**ROW, "dispatch_id": "so_" + "2" * 12, "title": "check B", "sent_at": at(2), "dispatched_from": "sess-a"}
    before = {**ROW, "dispatch_id": "so_" + "3" * 12, "sent_at": at(-60), "dispatched_from": "sess-a"}
    other = {**ROW, "dispatch_id": "so_" + "4" * 12, "sent_at": at(1), "dispatched_from": "sess-b"}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [second, before, other, first])
    loop = ('for n in A B; do "$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --brain $n '
            '--title "check $n" --task x --acceptance y --execution native --returns answer '
            '--revision r | python3 -c "print(1)"; done')
    got = card.cards(loop, "A so_111111111111 demo\nB so_222222222222 demo\n", since=now, session_id="sess-a")
    assert [(b["ok"], b["dispatchId"], b["title"]) for b in got] == [
        (True, first["dispatch_id"], "check A"), (True, second["dispatch_id"], "check B")]
    # a literal title with two rows that fit: still not sure which is this call's
    literal = CMD + " > /tmp/dispatch.json 2>&1"
    twin = [{**first, "title": ROW["title"]}, {**second, "title": ROW["title"]}]
    monkeypatch.setattr(swarm_out, "_read_log", lambda: twin)
    (b,) = card.cards(literal, "", since=now, session_id="sess-a")
    assert (b["ok"], b["maybeSent"]) == (False, True)
    # the loop sent nothing this session can see: the "not sure" card
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [before, other])
    (b,) = card.cards(loop, "", since=now, session_id="sess-a")
    assert (b["ok"], b["maybeSent"]) == (False, True)


@pytest.mark.usefixtures("_isolated_queue")
def test_the_orchestrator_shows_a_redirected_dispatch_from_its_own_sessions_row(monkeypatch):
    from datetime import datetime, timezone
    from tests.test_tool_call_leak_guard import _drive, _make_orchestrator, _make_result_message
    row = {**ROW, "sent_at": datetime.now(timezone.utc).isoformat(), "dispatched_from": "sess-test"}
    monkeypatch.setattr(swarm_out, "_read_log", lambda: [row])
    cmd = CMD + " > /tmp/dispatch.json 2>&1"
    events, raised = asyncio.run(_drive(_make_orchestrator(), [
        _bash("tu1", cmd), _res("tu1", ""), _make_result_message()]))
    assert raised is None
    (b,) = _cards(events)
    assert (b["ok"], b["dispatchId"]) == (True, ROW["dispatch_id"])


def test_a_dispatch_command_inside_a_heredoc_body_is_not_a_dispatch():
    """A Bash call that writes a file whose text holds a dispatch command (a script, a
    note) dispatches nothing: no card. The heredoc's own line, and a real dispatch after
    the body ends, still count."""
    line = '"$SWARM_BACKEND_BIN" py -m scripts.swarm_out dispatch --title t --brain B'
    assert not card.watched(f"cat > /tmp/cmd.txt <<'EOF'\n{line}\nEOF")
    assert not card.watched(f'cat > /tmp/cmd.txt <<-"END"\n\t{line}\n\tEND\npython3 x.py')
    assert card.watched(f"cat > /tmp/t.md <<'EOF'\ntask text\nEOF\n{line}")
    assert card.watched(f"{line} <<'EOF'\nignored\nEOF")
    # a here-string is not a heredoc: the rest of the command is still read
    assert card.watched(f"cat <<<word\n{line}")
