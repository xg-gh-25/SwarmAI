"""A session under a Project Agent: reserved actions and Brain gates (design
docs/design/2026-10-28-project-agent-flow.md §8, §11 "执行").

Questions these tests answer: which commands count as each reserved action (and
which do not); in a Project Agent session is a reserved action asked every time and
never answered from the approval cache, while a session without one is unchanged;
does an MCP tool whose name says nothing get asked when external messages are
reserved; is the pin made from the definition (default mode, the mode's own lists)
and refused for a missing mode, a changed revision or a bad name; is a pin read back
from the session record after a restart, and a broken record refuses Bash / Write /
Edit; do Brain gates refuse on exit 2 with the reason, refuse on any other failure, a
timeout (killing the whole process group) or a changed 3-gates/ (including a new
json.py), run from the bytes that were hashed, without AWS variables, and never run
in a session without a Project Agent.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest
import yaml

from core import project_agent_session as pas
from core.security_hooks import (_is_reserved_action, create_dangerous_command_gate,
                                 create_external_approval_gate)

ALL = ["push", "open_pr", "publish_release", "send_external_message"]


# ── the predicate ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("command,action", [
    ("git push", "push"),
    ("git push origin main", "push"),
    ("git -C repo push", "push"),
    ("GIT_TRACE=1 git push", "push"),
    ("git status && git push", "push"),
    ("git status\ngit push", "push"),
    ("/usr/bin/git push", "push"),
    ("git send-pack origin main", "push"),
    ("git -c alias.p=push p", "push"),
    ("git config alias.p push", "push"),
    ("git config --global alias.up 'push -f'", "push"),
    ("gh pr create --fill", "open_pr"),
    ("gh pr merge 3", "open_pr"),
    ("gh pr ready 3", "open_pr"),
    ("gh api repos/o/r/pulls -f title=x", "open_pr"),
    ("gh api -X POST repos/o/r/pulls", "open_pr"),
    ("gh api --method=PATCH repos/o/r/pulls/3", "open_pr"),
    ("gh release create v1", "publish_release"),
    ("gh release upload v1 a.tgz", "publish_release"),
    ("gh release edit v1 --draft=false", "publish_release"),
    ("gh api -XPATCH repos/o/r/releases/1", "publish_release"),
    ("npm publish", "publish_release"),
    ("pnpm publish --access public", "publish_release"),
    ("yarn npm publish", "publish_release"),
    ("uv publish", "publish_release"),
    ("poetry publish --build", "publish_release"),
    ("hatch publish", "publish_release"),
    ("twine upload dist/*", "publish_release"),
    ("cargo publish", "publish_release"),
    ("gem push x.gem", "publish_release"),
    ("docker push img:1", "publish_release"),
    ("echo 'unclosed && git push", "push"),          # does not tokenise: the raw text decides
])
def test_reserved_actions(command, action):
    assert _is_reserved_action(command, ALL) == action


@pytest.mark.parametrize("command", [
    "git status", "git log --grep push", "echo git push", "git commit -m 'push it'",
    "gh pr view 3", "gh pr list", "gh api repos/o/r/pulls", "gh api repos/o/r/releases",
    "gh api -X GET repos/o/r/pulls -f state=open",
    "gh release view v1", "npm install", "npm run publish-docs", "cargo build",
    "docker pull img", "uv sync", "git config user.name x",
])
def test_not_reserved_actions(command):
    assert _is_reserved_action(command, ALL) is None


def test_only_the_reserved_ones_count():
    assert _is_reserved_action("git push", ["open_pr"]) is None
    assert _is_reserved_action("npm publish && git push", ["push"]) == "push"
    assert _is_reserved_action("git push", []) is None


# ── the gates' use of the pin ────────────────────────────────────────────────

class _PM:
    def __init__(self, decision="approve", cached=False):
        self.asked = []
        self.decision = decision
        self.cached = cached
        self.approved = []

    def is_command_approved(self, session_key, command):
        return self.cached

    def is_resolved(self, session_id, tool_call_id):
        return False

    def store_pending_request(self, req):
        pass

    async def request_decision(self, session_id, req, timeout=None):
        self.asked.append(req)
        return self.decision

    def remove_pending_request(self, request_id):
        pass

    def approve_command(self, session_key, command):
        self.approved.append(command)


PIN = {"brain": "Lib", "mode": "code-change", "revision": "r", "profile": "bugfix",
       "reserved": ALL, "gates": [], "gates_digest": "d"}


def _ctx(pin):
    ctx = {"sdk_session_id": "s-pa"}
    pas.set_pin(ctx, pin)
    return ctx


def _bash(gate, command, tool_use_id="t1"):
    return asyncio.run(gate({"tool_name": "Bash", "tool_input": {"command": command}}, tool_use_id, None))


def test_a_reserved_action_is_asked_every_time_even_when_approved_before():
    pm = _PM(decision="approve", cached=True)
    gate = create_dangerous_command_gate(_ctx(PIN), "s-pa", pm, True)
    assert _bash(gate, "git push origin main") == {"decision": "approve"}
    assert _bash(gate, "git push origin main") == {"decision": "approve"}
    assert len(pm.asked) == 2 and "Project Agent reserves 'push'" in pm.asked[0]["reason"]
    assert pm.approved == []                                    # never stored for reuse


def test_a_denied_reserved_action_is_refused():
    gate = create_dangerous_command_gate(_ctx(PIN), "s-pa", _PM(decision="deny"), True)
    out = _bash(gate, "gh pr create --fill")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_without_a_project_agent_nothing_changes():
    pm = _PM()
    gate = create_dangerous_command_gate(_ctx(None), "s-x", pm, True)
    assert _bash(gate, "git push origin main") == {"decision": "approve"} and pm.asked == []
    # a session whose pin reserves nothing: the same
    gate = create_dangerous_command_gate(_ctx({**PIN, "reserved": []}), "s-pa", pm, True)
    assert _bash(gate, "npm publish") == {"decision": "approve"} and pm.asked == []


def test_an_unknown_mcp_tool_is_asked_when_external_messages_are_reserved(monkeypatch):
    import core.tool_risk as tr
    monkeypatch.setattr(tr, "user_overrides", lambda name: None)
    name = "mcp__crm__zap_record"                               # no verb the taxonomy knows
    pm = _PM()
    gate = create_external_approval_gate(_ctx(PIN), "s-pa", pm, True)
    asyncio.run(gate({"tool_name": name, "tool_input": {}}, "t1", None))
    assert len(pm.asked) == 1
    asyncio.run(gate({"tool_name": "mcp__crm__get_record", "tool_input": {}}, "t2", None))
    assert len(pm.asked) == 1                                   # a read verb is still a read
    pm2 = _PM()
    plain = create_external_approval_gate(_ctx(None), "s-x", pm2, True)
    asyncio.run(plain({"tool_name": name, "tool_input": {}}, "t3", None))
    assert pm2.asked == []
    monkeypatch.setattr(tr, "user_overrides", lambda n: tr.RiskClass.READ if n == name else None)
    gate = create_external_approval_gate(_ctx(PIN), "s-pa", pm2, True)
    asyncio.run(gate({"tool_name": name, "tool_input": {}}, "t4", None))
    assert pm2.asked == []                                      # the user's own classification wins


# ── the pin ──────────────────────────────────────────────────────────────────

DEF = {
    "schema": 2, "status": "reviewed",
    "gates": {"enforce": ["block_rm"]},
    "reserved_for_human": ["push"],
    "placements": ["chat-tab"], "landing": {"approval": "human"},
    "modes": [
        {"id": "research", "when": "a question", "profile": "research"},
        {"id": "release", "when": "publish", "profile": "direct",
         "reserved_for_human": ["push", "publish_release"], "gates": {"enforce": ["block_rm", "slow"]}},
    ],
    "default_mode": "research",
}
BLOCK_RM = """import json, sys
e = json.load(sys.stdin)
cmd = (e.get("tool_input") or {}).get("command") or ""
if e.get("hook_event_name") != "PreToolUse":
    sys.exit(3)
if "rm -rf" in cmd:
    print("no rm -rf here", file=sys.stderr)
    sys.exit(2)
sys.exit(0)
"""


def _ws(tmp_path: Path, doc: dict | None = None, gates: dict[str, str] | None = None) -> Path:
    ws = tmp_path / "ws"
    b = ws / "Projects" / "Lib"
    (b / "3-gates").mkdir(parents=True)
    for name, body in (gates or {"block_rm": BLOCK_RM, "slow": "import time\ntime.sleep(60)\n"}).items():
        (b / "3-gates" / f"{name}.py").write_text(body)
    (b / "project-agent.yaml").write_text(yaml.safe_dump(doc or DEF))
    return ws


def test_a_pin_is_made_from_the_definition(tmp_path):
    ws = _ws(tmp_path)
    pin = pas.make_pin(ws, "Lib")
    assert pin["mode"] == "research" and pin["reserved"] == ["push"] and pin["gates"] == ["block_rm"]
    rel = pas.make_pin(ws, "Lib", "release")
    assert rel["reserved"] == ["push", "publish_release"] and rel["gates"] == ["block_rm", "slow"]
    assert rel["revision"] and len(rel["gates_digest"]) == 64
    for args in (("Lib", "nope"), ("Lib", None, "stale"), ("../etc",), ("Nope",)):
        with pytest.raises(pas.PinError):
            pas.make_pin(ws, *args)
    (ws / "Projects" / "Lib" / "project-agent.yaml").write_text(yaml.safe_dump({**DEF, "default_mode": "x"}))
    with pytest.raises(pas.PinError):
        pas.make_pin(ws, "Lib")                                  # a definition with problems


def test_a_pin_is_read_back_from_the_session_record(monkeypatch):
    rows = {"s1": {"metadata": {"project_agent": PIN}}, "s2": {"metadata": {"project_agent": {"brain": "x"}}},
            "s3": {"metadata": {}}, "s4": {"metadata": {"project_agent": {**PIN, "revision": ""}}}}

    class _Sessions:
        async def get(self, sid):
            return rows.get(sid)

    import types
    import database
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Sessions()))
    assert asyncio.run(pas.current_pin({"sdk_session_id": "s1"})) == PIN
    assert "broken" in asyncio.run(pas.current_pin({"sdk_session_id": "s2"}))
    assert "broken" in asyncio.run(pas.current_pin({"sdk_session_id": "s4"}))
    ctx = {"sdk_session_id": "s3"}
    assert asyncio.run(pas.current_pin(ctx)) is None and ctx[pas.PIN_KEY] is None

    class _Down:
        async def get(self, sid):
            raise RuntimeError("db down")
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Down()))
    ctx = {"sdk_session_id": "s1"}
    assert "broken" in asyncio.run(pas.current_pin(ctx)) and pas.PIN_KEY not in ctx   # refused, asked again


# ── Brain gates ──────────────────────────────────────────────────────────────

def _input(command="ls"):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}


def test_a_gate_refuses_with_its_reason_and_lets_the_rest_through(tmp_path):
    ws = _ws(tmp_path)
    pin = pas.make_pin(ws, "Lib")
    assert pas.run_gates(ws, pin, _input("ls -la")) is None
    reason = pas.run_gates(ws, pin, _input("rm -rf /"))
    assert "Brain gate block_rm refused this: no rm -rf here" in reason


def test_a_failing_or_slow_gate_refuses(tmp_path):
    ws = _ws(tmp_path, gates={"block_rm": "import sys\nsys.exit(1)\n", "slow": "import time\ntime.sleep(60)\n"})
    pin = pas.make_pin(ws, "Lib")
    assert "failed (exit 1)" in pas.run_gates(ws, pin, _input())
    ws2 = _ws(tmp_path / "two")
    rel = pas.make_pin(ws2, "Lib", "release")
    t0 = time.monotonic()
    assert "did not answer" in pas.run_gates(ws2, rel, _input())
    assert time.monotonic() - t0 < pas.GATE_TIMEOUT_S + 5


def test_a_timed_out_gate_is_killed_with_its_children(tmp_path):
    marker = tmp_path / "child-alive"
    spawn = f"""import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", "import time; time.sleep(8); open({str(marker)!r}, 'w').write('x')"])
time.sleep(60)
"""
    ws = _ws(tmp_path, gates={"block_rm": BLOCK_RM, "slow": spawn})
    rel = pas.make_pin(ws, "Lib", "release")
    assert "did not answer" in pas.run_gates(ws, rel, _input())
    time.sleep(pas.GATE_TIMEOUT_S + 1)
    assert not marker.exists()                                  # the whole process group went


def test_a_changed_gates_directory_refuses(tmp_path):
    ws = _ws(tmp_path)
    pin = pas.make_pin(ws, "Lib")
    (ws / "Projects" / "Lib" / "3-gates" / "json.py").write_text("raise SystemExit(0)\n")
    assert "were changed" in pas.run_gates(ws, pin, _input("rm -rf /"))
    (ws / "Projects" / "Lib" / "3-gates" / "json.py").unlink()
    (ws / "Projects" / "Lib" / "3-gates" / "__pycache__").mkdir()
    (ws / "Projects" / "Lib" / "3-gates" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    assert "refused this" in pas.run_gates(ws, pin, _input("rm -rf /"))    # caches are not content


def test_a_stray_json_py_in_the_brain_cannot_replace_the_standard_library(tmp_path):
    ws = _ws(tmp_path)
    (ws / "Projects" / "Lib" / "3-gates" / "json.py").write_text("raise SystemExit(0)\n")
    pin = pas.make_pin(ws, "Lib")                               # pinned with it present
    assert "refused this" in pas.run_gates(ws, pin, _input("rm -rf /"))


def test_the_gate_runs_the_bytes_that_were_hashed(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    pin = pas.make_pin(ws, "Lib")
    real = pas.gates_snapshot
    gate_file = ws / "Projects" / "Lib" / "3-gates" / "block_rm.py"

    def swap_after_hashing(d):
        out = real(d)
        gate_file.write_text("import sys\nsys.exit(0)\n")       # replaced after it was read
        return out
    monkeypatch.setattr(pas, "gates_snapshot", swap_after_hashing)
    assert "refused this" in pas.run_gates(ws, pin, _input("rm -rf /"))


def test_a_gate_runs_in_its_own_empty_directory(tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    probe = ("import os, sys\nr = os.path.realpath\nhere = r(os.path.dirname(os.path.abspath(__file__)))\n"
             "ok = r(os.getcwd()) == here and r(os.environ.get('HOME', '')) == here and os.listdir('.') == [os.path.basename(__file__)]\n"
             "sys.exit(0 if ok else 2)\n")
    ws = _ws(tmp_path, gates={"block_rm": probe, "slow": "pass\n"})
    assert pas.run_gates(ws, pas.make_pin(ws, "Lib"), _input()) is None


def test_a_gate_sees_no_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA_TEST")
    monkeypatch.setenv("GH_TOKEN", "ghp_test")
    leak = "import os, sys\nbad = [k for k in os.environ if k.startswith(('AWS', 'GH_', 'ANTHROPIC'))]\n" \
           "print(bad, file=sys.stderr)\nsys.exit(2 if bad else 0)\n"
    ws = _ws(tmp_path, gates={"block_rm": leak, "slow": "pass\n"})
    assert pas.run_gates(ws, pas.make_pin(ws, "Lib"), _input()) is None


def test_the_hook_runs_gates_only_in_a_project_agent_session(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    import core.project_registry as preg
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    pin = pas.make_pin(ws, "Lib")
    hook = pas.create_brain_gate_hook(_ctx(pin))
    out = asyncio.run(hook(_input("rm -rf /"), "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert asyncio.run(hook({**_input("rm -rf /"), "tool_name": "Read"}, "t", None)) == {}
    plain = pas.create_brain_gate_hook(_ctx(None))
    assert asyncio.run(plain(_input("rm -rf /"), "t", None)) == {}
    broken = pas.create_brain_gate_hook(_ctx({"broken": "the record is incomplete"}))
    out = asyncio.run(broken({"tool_name": "Write", "tool_input": {"file_path": "x"}}, "t", None))
    assert "cannot run its gates" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_every_session_registers_the_hook(tmp_path, monkeypatch):
    from core.hook_builder import build_hooks
    from core.permission_manager import permission_manager
    import core.project_registry as preg
    ws = _ws(tmp_path)
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    ctx = _ctx(pas.make_pin(ws, "Lib"))
    hooks, _s, _a = asyncio.run(build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="s-pa", session_context=ctx, permission_manager=permission_manager))
    group = next(hm for hm in hooks["PreToolUse"] if hm.matcher == "Bash|Write|Edit|MultiEdit|NotebookEdit")
    assert len(group.hooks) == 1 and group.timeout                 # its own group, not the 5 s chain
    out = asyncio.run(group.hooks[0](_input("rm -rf /"), "t", None))
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── a chat request that names a Project Agent ────────────────────────────────

@pytest.mark.asyncio
async def test_a_chat_request_is_pinned_before_the_turn(async_client, tmp_path, monkeypatch):
    from routers import chat as chat_router
    import core.project_registry as preg
    ws = _ws(tmp_path)
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)
    seen = {}

    class _Router:
        async def run_conversation(self, **kw):
            seen.update(kw)
            yield {"type": "session_start", "sessionId": "s-stub"}
            yield {"type": "result", "sessionId": "s-stub"}

    monkeypatch.setattr(chat_router, "_get_router", lambda: _Router())
    from core.project_agent import revision
    rev = revision(ws / "Projects" / "Lib")
    # a pin that names no revision would skip the host-vs-Hive comparison: refused
    r = await async_client.post("/api/chat/stream", json={
        "agent_id": "default", "message": "hi", "project_agent": {"brain": "Lib", "mode": "release"}})
    assert r.status_code in (400, 422) and not seen
    r = await async_client.post("/api/chat/stream", json={
        "agent_id": "default", "message": "hi",
        "project_agent": {"brain": "Lib", "mode": "release", "revision": rev}})
    assert r.status_code == 200
    assert seen["project_agent"]["mode"] == "release" and seen["project_agent"]["gates"] == ["block_rm", "slow"]
    seen.clear()
    r = await async_client.post("/api/chat/stream", json={
        "agent_id": "default", "message": "hi", "project_agent": {"brain": "Lib", "mode": "nope", "revision": rev}})
    assert r.status_code in (400, 422) and not seen
    r = await async_client.post("/api/chat/stream", json={"agent_id": "default", "message": "hi"})
    assert r.status_code == 200 and seen["project_agent"] is None


# ── review round 1 fixes ─────────────────────────────────────────────────────

@pytest.mark.parametrize("command,action", [
    ("pnpm -r publish", "publish_release"),
    ("pnpm --filter x publish", "publish_release"),
    ("npm --workspace a publish", "publish_release"),
    ("cargo +nightly publish", "publish_release"),
    ("docker --context x push img", "publish_release"),
    ("docker image push img", "publish_release"),
    ("uv --directory x publish", "publish_release"),
    ("poetry -C x publish", "publish_release"),
    ("gh api -H 'Accept: application/vnd.github+json' -X POST repos/o/r/pulls -f title=t", "open_pr"),
    ("gh api repos/o/r/pulls -ftitle=t -fhead=h -fbase=main", "open_pr"),
    ("gh api repos/o/r/pulls --raw-field=title=t", "open_pr"),
    ("gh api graphql -f query='mutation { mergePullRequest(input: {}) { clientMutationId } }'", "open_pr"),
    ("gh pr --repo o/r create --fill", "open_pr"),
    ("gh release -R o/r create v1", "publish_release"),
    ("git --git-dir .git push origin main", "push"),
    ("git --work-tree . push", "push"),
    ("git --config-env alias.p=X p", "push"),
    ("GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=alias.p GIT_CONFIG_VALUE_0=push git p", "push"),
    ("exec git push", "push"),
    ("xargs -n1 git push", "push"),
    ("sudo -u me git push", "push"),
    ("timeout 5 npm publish", "publish_release"),
    ("nice -n 10 git push", "push"),
    ("git subtree push --prefix d origin b", "push"),
])
def test_reserved_actions_past_options_and_wrappers(command, action):
    assert _is_reserved_action(command, ALL) == action


@pytest.mark.parametrize("command", [
    "npm run publish", "npm run-script publish", "pnpm exec publish", "cargo run -- publish",
    "docker run img push", "gh api -H 'Accept: x/y' repos/o/r/pulls", "gh api graphql -f query='{ viewer { login } }'",
    "gh api -X GET repos/o/r/releases", "git --git-dir .git log", "sudo -u me ls", "timeout 5 sleep 1",
])
def test_not_reserved_past_options_and_wrappers(command):
    assert _is_reserved_action(command, ALL) is None


def test_an_unreadable_session_record_refuses_gated_tools(monkeypatch, tmp_path):
    import types
    import database

    class _Down:
        async def get(self, sid):
            raise RuntimeError("database is locked")
    monkeypatch.setattr(database, "db", types.SimpleNamespace(sessions=_Down()))
    ctx = {"sdk_session_id": "s-locked"}
    hook = pas.create_brain_gate_hook(ctx)
    out = asyncio.run(hook({"tool_name": "Write", "tool_input": {"file_path": "x"}}, "t", None))
    assert "cannot be read right now" in out["hookSpecificOutput"]["permissionDecisionReason"]
    assert pas.PIN_KEY not in ctx                                 # asked again next call


def test_a_gate_that_cannot_run_refuses(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    import core.project_registry as preg
    monkeypatch.setattr(preg, "get_swarmws", lambda: ws)

    def boom(*a):
        raise OSError("No space left on device")
    monkeypatch.setattr(pas, "_run_one", boom)
    hook = pas.create_brain_gate_hook(_ctx(pas.make_pin(ws, "Lib")))
    out = asyncio.run(hook(_input("ls"), "t", None))
    assert "could not run (OSError)" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_detached_grandchild_does_not_keep_the_call_waiting(tmp_path):
    gate = ("import os, sys, time\n"
            "if os.fork() == 0:\n    os.setsid()\n    time.sleep(30)\n    os._exit(0)\n"
            "time.sleep(60)\n")
    ws = _ws(tmp_path, gates={"block_rm": BLOCK_RM, "slow": gate})
    rel = pas.make_pin(ws, "Lib", "release")
    t0 = time.monotonic()
    assert "did not answer" in pas.run_gates(ws, rel, _input())
    assert time.monotonic() - t0 < pas.GATE_TIMEOUT_S + 3


def test_a_gate_reading_no_stdin_with_a_large_call_still_answers(tmp_path):
    ws = _ws(tmp_path, gates={"block_rm": "import sys\nsys.exit(0)\n", "slow": "pass\n"})
    pin = pas.make_pin(ws, "Lib")
    assert pas.run_gates(ws, pin, _input("x" * 300_000)) is None


def test_gates_changing_while_pinning_are_refused(tmp_path, monkeypatch):
    ws = _ws(tmp_path)
    real = pas.revision
    gate = ws / "Projects" / "Lib" / "3-gates" / "block_rm.py"

    def changing(d):
        out = real(d)
        gate.write_text("import sys\nsys.exit(0)\n")
        return out
    monkeypatch.setattr(pas, "revision", changing)
    with pytest.raises(pas.PinError, match="changed while"):
        pas.make_pin(ws, "Lib")


def test_an_unknown_mcp_tool_is_asked_under_a_broken_pin(monkeypatch):
    import core.tool_risk as tr
    monkeypatch.setattr(tr, "user_overrides", lambda name: None)
    pm = _PM()
    gate = create_external_approval_gate(_ctx({"broken": "x"}), "s-pa", pm, True)
    asyncio.run(gate({"tool_name": "mcp__crm__zap_record", "tool_input": {}}, "t1", None))
    assert len(pm.asked) == 1


def test_record_pin_keeps_other_metadata_and_says_when_it_failed():
    from database import db

    async def go():
        await db.sessions.put({"id": "s-meta", "agent_id": "default", "title": "t",
                               "created_at": "2026-10-29T00:00:00", "last_accessed": "2026-10-29T00:00:00",
                               "metadata": {"task_repos": [{"url": "https://github.com/o/r"}]}})
        ok = await pas.record_pin("s-meta", PIN)
        row = await db.sessions.get("s-meta")
        missing = await pas.record_pin("s-none", PIN)
        return ok, row, missing
    ok, row, missing = asyncio.run(go())
    assert ok and not missing
    assert row["metadata"]["task_repos"] and row["metadata"]["project_agent"] == PIN


# ── the router: pinned before the first tool call, and on the record ─────────

def _router():
    from unittest.mock import AsyncMock, MagicMock
    from core.session_router import SessionRouter
    pb = MagicMock()
    pb.build_options = AsyncMock(return_value=MagicMock(model="test", system_prompt="test"))
    return SessionRouter(prompt_builder=pb, config=MagicMock())


async def _run(router, sid, pin, send):
    from unittest.mock import patch
    from core.session_unit import SessionState
    unit = router.get_or_create_unit(sid, "default")
    unit._client = object()
    if unit.state != SessionState.IDLE:
        unit._transition(SessionState.IDLE)
    out = []
    with patch("core.session_unit.SessionUnit.send", side_effect=send):
        async for ev in router.run_conversation(agent_id="default", user_message="hi", session_id=sid,
                                                client_id=f"c-{sid}", project_agent=pin):
            out.append(ev)
    return unit, out


@pytest.mark.asyncio
async def test_the_router_pins_the_session_before_the_turn_runs():
    from database import db
    seen = {}

    def send(**kw):
        async def gen():
            unit = router.get_unit("s-pin")
            seen["pin"] = await pas.current_pin(unit._hook_session_context)
            yield {"type": "session_start", "sessionId": "s-pin"}
            yield {"type": "result", "sessionId": "s-pin"}
        return gen()
    router = _router()
    await _run(router, "s-pin", PIN, send)
    assert seen["pin"] == PIN
    assert (await db.sessions.get("s-pin"))["metadata"]["project_agent"] == PIN


@pytest.mark.asyncio
async def test_a_queued_first_send_is_already_pinned_on_its_record():
    from database import db
    from core.exceptions import SessionBusyError

    def busy(**_k):
        async def gen():
            raise SessionBusyError(detail="actively streaming")
            yield  # pragma: no cover
        return gen()
    await _run(_router(), "s-queued", PIN, busy)
    assert (await db.sessions.get("s-queued"))["metadata"]["project_agent"] == PIN


@pytest.mark.asyncio
async def test_a_pin_that_cannot_be_recorded_stops_the_session(monkeypatch):
    async def fail(sid, pin):
        return False
    monkeypatch.setattr(pas, "record_pin", fail)
    ran = []

    def send(**kw):
        ran.append(1)

        async def gen():
            yield {"type": "result"}
        return gen()
    _unit, out = await _run(_router(), "s-unrec", PIN, send)
    assert any(e.get("code") == "PROJECT_AGENT_NOT_RECORDED" for e in out) and not ran
