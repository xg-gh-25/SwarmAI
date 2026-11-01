"""A run's own agent lands it with run-land (or records landed-as) without asking the
person, like a native commit; deleting a snapshot and accepting a finding still ask.
And a file written from a heredoc is not asked about for the text it holds.

Questions: does a one-command run-land of a landing request pass with no prompt and
leave the agent's authorization for the approved phase; do the other actions and a
composed line still ask; is a cat/tee heredoc's body data, while a heredoc a shell or
interpreter reads, or a line that also runs a shell, is still judged?"""
from __future__ import annotations

import asyncio

import pytest

from core import security_hooks as sh
from core.worktree_write_gate import v1_command
from tests.test_project_agent_session import _PM

LAND = v1_command("run-land --project P --run-id run_aaaaaaaa --request land_abcdef0123456789 "
                  "--message 'fix: x\n\nCo-Authored-By: Swarm <swarm@swarmai.dev>'")


def _gate(pm):
    return sh.create_dangerous_command_gate({"sdk_session_id": "s"}, "s", pm, True)


def _run(gate, command, tool_use_id="t1", **extra):
    return asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp",
                             **extra}, tool_use_id, None))


@pytest.mark.parametrize("action", ["land", "land_skip_tests", "confirm_landed"])
def test_the_runs_agent_lands_it_without_asking(monkeypatch, action):
    monkeypatch.setattr(sh, "_v1_request_action", lambda c: action)
    pm = _PM(decision="deny")
    assert _run(_gate(pm), LAND) == {"decision": "approve"}
    assert pm.asked == []
    assert sh.consume_land_approval("t1", LAND) == "host"       # the agent's, not the person's


@pytest.mark.parametrize("action", ["discard_snapshot", "accept_finding", None])
def test_deleting_a_snapshot_or_accepting_a_finding_still_asks(monkeypatch, action):
    monkeypatch.setattr(sh, "_v1_request_action", lambda c: action)
    pm = _PM(decision="approve")
    _run(_gate(pm), LAND)
    assert len(pm.asked) == 1


def test_a_sub_agent_does_not_land_the_run(monkeypatch):
    monkeypatch.setattr(sh, "_v1_request_action", lambda c: "land")
    pm = _PM(decision="approve")
    _run(_gate(pm), LAND, agent_id="sub-1")
    assert len(pm.asked) == 1


def test_a_landing_is_the_agents_on_a_dispatched_task_and_with_approvals_off(monkeypatch):
    from core.project_agent_session import set_pin
    monkeypatch.setattr(sh, "_v1_request_action", lambda c: "land")
    ctx = {"sdk_session_id": "s"}
    set_pin(ctx, {"brain": "Lib", "mode": "code-change", "revision": "r", "profile": None,
                  "reserved": [], "gates": [], "gates_digest": "d"})
    pm = _PM(decision="deny")
    assert _run(sh.create_dangerous_command_gate(ctx, "s", pm, True), LAND) == {"decision": "approve"}
    assert _run(sh.create_dangerous_command_gate({"sdk_session_id": "s"}, "s", _PM(), False), LAND) \
        == {"decision": "approve"}
    assert pm.asked == []


def test_a_composed_line_is_not_one_landing(monkeypatch):
    from types import SimpleNamespace

    from core import pipeline_human
    from core.run_protocol import landing
    monkeypatch.setattr(pipeline_human, "load_engine", lambda run_id: SimpleNamespace(store=None))
    monkeypatch.setattr(landing, "request_event", lambda store, rid: {"request": {"action": "land"}})
    assert sh._v1_request_action(LAND) == "land"                     # the request is readable
    assert sh._v1_request_action(LAND + " && echo done") is None
    assert sh._v1_request_action(f"echo $(date) && {LAND}") is None
    assert sh._v1_request_action(LAND + " --request discard_0123456789abcdef") is None   # two ids


DATA = "cat > /tmp/t.py <<'EOF'\ncases = ('git push --force origin x', 'rm -rf ~/')\nEOF\npython3 /tmp/t.py"


def test_a_file_written_from_a_heredoc_is_not_asked_about_its_text():
    pm = _PM(decision="deny")
    assert _run(_gate(pm), DATA) == {"decision": "approve"} and pm.asked == []
    pm = _PM(decision="deny")
    _run(_gate(pm), "tee notes.md <<'EOF'\ngit push --force origin main\nEOF")
    assert pm.asked == []


@pytest.mark.parametrize("command", [
    "cat > n.md <<EOF\n$(git push --force origin main)\nEOF",          # unquoted: expanded
    "cat > n.md <<EOF\n`git push --force origin main`\nEOF",
    "cat > n.md <<\\EOF\nplain\nEOF\n./run.sh",
])
def test_a_body_the_shell_expands_or_may_run_is_left_for_the_rules(command):
    """The danger rules read these exactly as before this change (what they catch in
    them is theirs)."""
    assert sh._command_as_run(command) == command


@pytest.mark.parametrize("command", [
    "bash <<'EOF'\ngit push --force origin x\nEOF",                      # a shell reads it
    "ssh build-host <<'EOF'\ngit push --force origin x\nEOF",          # it runs there
    "cat <<'EOF' | /bin/sh\ngit push --force origin x\nEOF",           # piped to a shell
    "cat <<'EOF' | python3 -\nimport os\nEOF\ngit push --force origin x",
    "cat > x.sh <<'EOF'\ngit push --force origin x\nEOF\n/bin/zsh x.sh",
    "cat > x.sh <<'EOF'\ngit push --force origin x\nEOF\n$SHELL x.sh",
    "cat > x.sh <<'EOF'\ngit push --force origin x\nEOF\nchmod +x x.sh && ./x.sh",
    "cat <<'EOF' > >(bash)\ngit push --force origin x\nEOF",
    "x=$(cat <<'EOF'\ngit push --force origin x\nEOF\n); $x",
    "cat > x.sh <<'EOF'\ngit push --force origin x\nEOF\nbash x.sh",       # and runs it
    "cat > x.sh <<'EOF'\ngit push --force origin x\nEOF\n. x.sh",
    "cat > x.py <<'EOF'\nok\nEOF\ngit push --force origin x",             # outside the body
])
def test_what_a_heredoc_runs_is_still_judged(command):
    pm = _PM(decision="deny")
    _run(_gate(pm), command)
    assert len(pm.asked) == 1, command
