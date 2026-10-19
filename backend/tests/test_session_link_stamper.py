"""Tests for create_session_link_stamper (security_hooks.py) and its registration.

What is tested: the PostToolUse (Bash) hook that makes the run↔session link
authoritative from the backend. A run created with SWARM_APP_SESSION_ID stripped
inside interpreter code or a script records `session_id: null`; after a Bash call
that mentions artifact_cli / run-create, the stamper writes this session's app id
into such runs (session_id_source "backend_stamp").

Methodology: real run.json files under a temp workspace (SWARM_WORKSPACE), the
real async hooks (PreToolUse companion → PostToolUse) driven with asyncio.run, and
a spy on HookRegistry.register through the real build_hooks().

Invariants:
  • an unlinked running run created during the call is stamped, every other key
    kept, written as json.dumps(indent=2);
  • a run whose id the call names is stamped even when it is older;
  • a non-empty session_id is never overwritten; an unrelated old run, a finished
    run, a malformed run.json and non-triggering commands are left alone;
  • no / invalid app session id → no-op; any internal error → {} (fail-safe);
  • build_hooks registers the PostToolUse stamper and its PreToolUse companion
    once each, Bash-scoped, without disturbing the PreToolUse gate order.
"""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from core import security_hooks as sh

SID = "app-session-7"
CREATE = "python backend/scripts/artifact_cli.py run-create --project P --requirement r"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    root = tmp_path / "ws"
    root.mkdir()
    monkeypatch.setenv("SWARM_WORKSPACE", str(root))
    monkeypatch.setattr(sh, "_ACTIVE_RUN_GLOB_ROOT", str(root))
    return root


def _iso(seconds_ago=0.0):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _seed(ws, run_id="run_aaaa1111", session_id=None, status="running",
          created_ago=0.0, mtime_ago=None, raw=None):
    d = ws / "Projects" / "P" / ".artifacts" / "runs" / run_id
    d.mkdir(parents=True)
    f = d / "run.json"
    if raw is not None:
        f.write_text(raw, encoding="utf-8")
    else:
        f.write_text(json.dumps({
            "id": run_id, "project": "P", "requirement": "r", "status": status,
            "stages": [], "created_at": _iso(created_ago), "updated_at": _iso(created_ago),
            "completed_at": None, "session_id": session_id, "intent": "r",
            "intent_source": "requirement"}, indent=2), encoding="utf-8")
    if mtime_ago is not None:
        t = time.time() - mtime_ago
        os.utime(f, (t, t))
    return f


def _run_call(command, ctx=None, tool_use_id="tu-1", before=None):
    """Drive one Bash call: companion at start, optional *before* action (the
    command's own effect), then the PostToolUse stamper."""
    hook = sh.create_session_link_stamper(ctx if ctx is not None else {"sdk_session_id": SID})
    data = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/tmp"}

    async def _go():
        assert await hook.record_start(data, tool_use_id, None) == {}
        if before is not None:
            before()
        return await hook(dict(data, tool_response={"stdout": ""}), tool_use_id, None)

    return asyncio.run(_go())


def _load(f):
    return json.loads(f.read_text(encoding="utf-8"))


class TestStamping:
    def test_run_created_during_the_call_without_session_id_is_stamped(self, ws):
        made = {}
        cmd = ("python -c 'import os,subprocess; os.environ.pop(\"SWARM_APP_SESSION_ID\");"
               " subprocess.run([\"python\",\"backend/scripts/artifact_cli.py\",\"run-create\"])'")
        assert _run_call(cmd, before=lambda: made.setdefault("f", _seed(ws))) == {}
        f = made["f"]
        data = _load(f)
        assert data["session_id"] == SID
        assert data["session_id_source"] == "backend_stamp"
        assert data["intent"] == "r" and data["stages"] == [] and data["project"] == "P"
        assert f.read_text(encoding="utf-8") == json.dumps(data, indent=2)
        assert not [p for p in os.listdir(f.parent) if p.endswith(".tmp")]

    def test_empty_string_session_id_counts_as_missing(self, ws):
        made = {}
        _run_call(CREATE, before=lambda: made.setdefault("f", _seed(ws, session_id="")))
        assert _load(made["f"])["session_id"] == SID

    def test_existing_session_id_is_never_overwritten(self, ws, caplog):
        made = {}
        _run_call(CREATE, before=lambda: made.setdefault(
            "f", _seed(ws, session_id="other-session")))
        data = _load(made["f"])
        assert data["session_id"] == "other-session"
        assert "session_id_source" not in data
        assert "linked to session other-session" in caplog.text

    def test_unrelated_old_run_is_untouched(self, ws):
        f = _seed(ws, created_ago=3600, mtime_ago=3600)
        before = f.read_text(encoding="utf-8")
        _run_call(CREATE)
        assert f.read_text(encoding="utf-8") == before

    def test_old_run_named_by_the_call_is_stamped(self, ws):
        # A script created it earlier; the session now works on it by run id.
        f = _seed(ws, run_id="run_bbbb2222", created_ago=3600, mtime_ago=3600)
        _run_call("python backend/scripts/artifact_cli.py run-update --project P "
                  "--run-id run_bbbb2222 --stage evaluate")
        assert _load(f)["session_id"] == SID

    def test_finished_run_is_untouched(self, ws):
        made = {}
        _run_call(CREATE, before=lambda: made.setdefault("f", _seed(ws, status="completed")))
        assert _load(made["f"])["session_id"] is None

    def test_malformed_run_json_is_skipped_and_others_still_stamped(self, ws):
        made = {}

        def _make():
            made["bad"] = _seed(ws, run_id="run_bad00000", raw="{ not json")
            made["list"] = _seed(ws, run_id="run_list0000", raw="[1, 2]")
            made["good"] = _seed(ws, run_id="run_good0000")

        assert _run_call(CREATE, before=_make) == {}
        assert made["bad"].read_text(encoding="utf-8") == "{ not json"
        assert made["list"].read_text(encoding="utf-8") == "[1, 2]"
        assert _load(made["good"])["session_id"] == SID

    @pytest.mark.parametrize("command", ["ls -la", "python a.py", "git status"])
    def test_command_not_mentioning_artifact_cli_does_nothing(self, ws, command):
        made = {}
        _run_call(command, before=lambda: made.setdefault("f", _seed(ws)))
        assert _load(made["f"])["session_id"] is None

    @pytest.mark.parametrize("ctx", [{}, {"sdk_session_id": ""},
                                     {"sdk_session_id": "bad id with spaces"}])
    def test_no_valid_app_session_id_is_a_no_op(self, ws, ctx):
        made = {}
        _run_call(CREATE, ctx=ctx, before=lambda: made.setdefault("f", _seed(ws)))
        assert _load(made["f"])["session_id"] is None

    def test_without_a_recorded_start_the_fallback_window_applies(self, ws):
        f = _seed(ws, created_ago=60, mtime_ago=60)
        hook = sh.create_session_link_stamper({"sdk_session_id": SID})
        data = {"tool_name": "Bash", "tool_input": {"command": CREATE}}
        assert asyncio.run(hook(data, None, None)) == {}
        assert _load(f)["session_id"] == SID

    def test_non_bash_tool_is_ignored(self, ws):
        f = _seed(ws)
        hook = sh.create_session_link_stamper({"sdk_session_id": SID})
        data = {"tool_name": "Write", "tool_input": {"command": CREATE}}
        assert asyncio.run(hook(data, "tu-9", None)) == {}
        assert _load(f)["session_id"] is None


class TestFailSafe:
    def test_scan_exception_fails_safe(self, ws, monkeypatch, caplog):
        def boom(*_a, **_k):
            raise RuntimeError("scan broke")

        monkeypatch.setattr(sh, "_stamp_unlinked_runs", boom)
        assert _run_call(CREATE) == {}
        assert "stamping skipped" in caplog.text

    def test_write_error_leaves_run_json_intact(self, ws, monkeypatch):
        made = {}

        def bad_replace(*_a, **_k):
            raise OSError("disk full")

        monkeypatch.setattr(sh.os, "replace", bad_replace)
        assert _run_call(CREATE, before=lambda: made.setdefault("f", _seed(ws))) == {}
        assert _load(made["f"])["session_id"] is None
        assert not [p for p in os.listdir(made["f"].parent) if p.endswith(".tmp")]

    def test_slow_scan_is_bounded(self, ws, monkeypatch):
        monkeypatch.setattr(sh, "_STAMP_TIMEOUT", 0.2)
        monkeypatch.setattr(sh, "_stamp_unlinked_runs", lambda *_a: time.sleep(1.0))
        start = time.monotonic()
        assert _run_call(CREATE) == {}
        assert time.monotonic() - start < 0.9

    def test_companion_state_is_bounded(self, ws):
        hook = sh.create_session_link_stamper({"sdk_session_id": SID})
        data = {"tool_name": "Bash", "tool_input": {"command": "ls"}}

        async def _go():
            for k in range(sh._STAMP_MAX_PENDING + 50):
                await hook.record_start(data, "tu-%d" % k, None)

        asyncio.run(_go())  # must not raise or grow without bound

    def test_garbage_input_returns_empty(self):
        hook = sh.create_session_link_stamper({"sdk_session_id": SID})
        assert asyncio.run(hook({"tool_name": "Bash", "tool_input": None}, None, None)) == {}
        assert asyncio.run(hook.record_start({}, "x", None)) == {}


def _record_registrations(monkeypatch, session_context):
    from core import hook_builder
    from core.permission_manager import permission_manager

    recorded = []
    original = hook_builder.HookRegistry.register

    def _spy(self, event, hook, name, matcher=None, no_timeout=False):
        for n in getattr(hook, "members", (name,)):  # a compose_gates group
            recorded.append((event, n, matcher, no_timeout))
        return original(self, event, hook, name, matcher=matcher, no_timeout=no_timeout)

    monkeypatch.setattr(hook_builder.HookRegistry, "register", _spy)
    hooks, _s, _a = asyncio.run(hook_builder.build_hooks(
        agent_config={"id": "default"}, enable_skills=False, enable_mcp=False,
        resume_session_id="test-sess", session_context=session_context,
        permission_manager=permission_manager,
    ))
    return recorded, hooks


def test_build_hooks_registers_stamper_and_companion(monkeypatch):
    recorded, hooks = _record_registrations(monkeypatch, {"sdk_session_id": "test-sess"})
    names = [r[1] for r in recorded]
    assert names.count("session_link_stamper") == 1
    assert names.count("session_link_stamp_start") == 1
    by_name = {r[1]: r for r in recorded}
    assert by_name["session_link_stamper"] == (
        "PostToolUse", "session_link_stamper", "Bash", False)
    assert by_name["session_link_stamp_start"] == (
        "PreToolUse", "session_link_stamp_start", "Bash", False)
    pre = [r[1] for r in recorded if r[0] == "PreToolUse"]
    assert pre.index("session_link_stamp_start") < pre.index("session_link_gate") \
        == pre.index("pipeline_test_gate") - 1
    post_bash = [hm for hm in hooks["PostToolUse"] if hm.matcher == "Bash"]
    assert len(post_bash) == 1


def test_build_hooks_without_session_context_skips_the_stamper(monkeypatch):
    recorded, _hooks = _record_registrations(monkeypatch, None)
    names = [r[1] for r in recorded]
    assert "session_link_stamper" not in names
    assert "session_link_stamp_start" not in names
    assert "session_link_gate" in names
