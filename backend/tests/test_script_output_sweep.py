"""Post-Bash sweep for files a script wrote (core.script_output_sweep + orchestrator).

What is tested:
- sweep_root: an absolute leading `cd` wins over the session cwd; relative / `~` /
  `$VAR` / `..` cds are not guessed at; `git …` commands are never swept; `/` and
  the home directory are refused
- recent_outputs on a real temp tree: only deliverable extensions with mtime inside
  the call (1s slack); hidden and NOISE_SEGMENTS dirs pruned; depth bound; a bulk
  change (> MAX_HITS) reports nothing; a missing root is [] not an error
- the orchestrator: a successful Bash result routes the swept paths through
  _build_file_write_events with modified_since = the call's start (both result
  paths); a FAILED result sweeps nothing
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core import script_output_sweep as sos


@pytest.mark.parametrize("command,cwd,expected", [
    ("python make_deck.py", "/tmp", "/tmp"),
    ("cd /tmp && python make_deck.py", "/usr", "/tmp"),
    ("cd '/tmp' ; node build.js", None, "/tmp"),
    ("cd out && python x.py", "/tmp", None),
    ("cd ~/x && python x.py", "/tmp", None),
    ("cd $HOME && python x.py", "/tmp", None),
    ("cd /tmp/../etc && python x.py", "/tmp", None),
    ("git pull", "/tmp", None),
    ("cd /tmp && git checkout main", None, None),
    ("GIT_PAGER=cat git log", "/tmp", None),
    ("python x.py", None, None),
    ("python x.py", "relative/dir", None),
    ("", "/tmp", None),
])
def test_sweep_root(command, cwd, expected):
    got = sos.sweep_root(command, cwd)
    assert got == (str(Path(expected).resolve()) if expected else None)


def test_sweep_root_refuses_root_and_home():
    assert sos.sweep_root("python x.py", "/") is None
    assert sos.sweep_root("python x.py", str(Path.home())) is None
    assert sos.sweep_root("python x.py", "/definitely/not/here") is None


def _touch(p: Path, age: float = 0.0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))
    return p


@pytest.fixture
def _tmp_not_noise(monkeypatch):
    """The OS temp dir is itself a canvas noise root; trees under tmp_path stand in
    for a real workspace here."""
    monkeypatch.setattr(sos, "is_noise_path", lambda p: False)


def test_recent_outputs_applies_the_noise_check(tmp_path):
    since = time.time()
    _touch(tmp_path / "out" / "deck.pptx")
    assert sos.recent_outputs(str(tmp_path), since) == []  # real check: tmp is noise


@pytest.mark.usefixtures("_tmp_not_noise")
def test_recent_outputs_finds_what_the_call_wrote(tmp_path):
    since = time.time()
    new = _touch(tmp_path / "out" / "deck.pptx")
    _touch(tmp_path / "report.md")
    _touch(tmp_path / "old.md", age=30)            # before the call
    _touch(tmp_path / "make.py")                   # not a deliverable extension
    _touch(tmp_path / ".hidden" / "x.md")          # hidden dir pruned
    _touch(tmp_path / "node_modules" / "p.md")     # noise segment pruned
    _touch(tmp_path / "a" / "b" / "c" / "d" / "deep.md")  # beyond MAX_DEPTH
    got = sos.recent_outputs(str(tmp_path), since)
    assert got == sorted([str(new), str(tmp_path / "report.md")])


@pytest.mark.usefixtures("_tmp_not_noise")
def test_recent_outputs_bulk_change_reports_nothing(tmp_path):
    since = time.time()
    for i in range(sos.MAX_HITS + 1):
        _touch(tmp_path / f"f{i}.md")
    assert sos.recent_outputs(str(tmp_path), since) == []
    assert sos.recent_outputs(str(tmp_path / "missing"), since) == []


# ── orchestrator wiring ──────────────────────────────────────────────────────

@pytest.fixture
def _isolated_queue():
    from core.permission_manager import permission_manager
    permission_manager.remove_session_queue("sess-test")
    yield
    permission_manager.remove_session_queue("sess-test")


def _drive_bash(tmp_path, monkeypatch, *, is_error=False, in_assistant=False):
    """Drive the real orchestrator through one Bash call that 'runs a script' in
    tmp_path (the file is created before the result arrives); the write gate is
    recorded instead of classifying."""
    from claude_agent_sdk import AssistantMessage, ToolResultBlock, ToolUseBlock, UserMessage
    from tests.test_pipeline_stage_events import _drive_stream
    from tests.test_tool_call_leak_guard import _make_orchestrator, _make_result_message
    orch = _make_orchestrator()
    orch._parent._cwd = str(tmp_path)
    calls = []

    async def _gate(self, paths, cache, modified_since=None):
        calls.append((list(paths), modified_since))
        return [{"type": "file_changed", "path": p, "absolutePath": p, "kind": "content", "operation": "written"}
                for p in paths]

    from core.streaming_orchestrator import StreamingOrchestrator
    monkeypatch.setattr(StreamingOrchestrator, "_build_file_write_events", _gate)
    use = AssistantMessage(content=[ToolUseBlock(id="tb1", name="Bash", input={"command": "python make_deck.py"})],
                           model="test-model")
    t0 = time.time()
    deck = _touch(tmp_path / "out" / "deck.pptx")
    res_block = ToolResultBlock(tool_use_id="tb1", content="done", is_error=is_error)
    result = (AssistantMessage(content=[res_block], model="test-model") if in_assistant
              else UserMessage(content=[res_block]))
    events, raised = _drive_stream(orch, [use, result, _make_result_message()])
    assert raised is None
    return events, calls, deck, t0


@pytest.mark.usefixtures("_isolated_queue", "_tmp_not_noise")
@pytest.mark.parametrize("in_assistant", [False, True])
def test_script_output_becomes_file_changed(tmp_path, monkeypatch, in_assistant):
    events, calls, deck, t0 = _drive_bash(tmp_path, monkeypatch, in_assistant=in_assistant)
    swept = [c for c in calls if str(deck) in c[0]]
    assert len(swept) == 1
    assert swept[0][1] is not None and abs(swept[0][1] - t0) < 5  # modified_since = call start
    assert any(e.get("type") == "file_changed" and e.get("absolutePath") == str(deck) for e in events)


@pytest.mark.usefixtures("_isolated_queue", "_tmp_not_noise")
def test_failed_bash_sweeps_nothing(tmp_path, monkeypatch):
    events, calls, deck, _ = _drive_bash(tmp_path, monkeypatch, is_error=True)
    assert calls == []
    assert not any(e.get("type") == "file_changed" for e in events)
