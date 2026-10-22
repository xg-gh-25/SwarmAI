"""Tests for shell writes that bypassed governance and change tracking.

What is tested
--------------
1. Parsers (``core.file_change_classifier``): ``sed -i`` targets and interpreter
   heredoc write CANDIDATES (``python3 - <<'PY' … open(p,'w')``), incl. ``cd`` anchoring
   and the negatives (read-only scripts, ``sed -n``, non-interpreter heredocs).
2. ``governance_file_gate`` now advises on Bash commands that write a governed
   file (sed -i / redirect / heredoc patch) and stays silent otherwise.
3. The live emit: a heredoc patch surfaces ``file_changed(written)`` ONLY for the
   candidate whose mtime moved during the call (a literal it merely read is dropped).

Key invariants
--------------
- INV1: heredoc literals are candidates, never proof — the emit confirms by mtime.
- INV2: the governance gate always approves (advisory); a parse error never blocks.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from core.file_change_classifier import (
    parse_bash_heredoc_write_candidates,
    parse_bash_write_targets,
)

PATCH = (
    "cd /repo/backend && python3 - <<'PY'\n"
    "p='core/x.py'; s=open(p).read()\n"
    "cfg=open('core/settings.json').read()\n"
    "open(p,'w').write(s)\n"
    "PY"
)


# ── 1. parsers ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd,expected", [
    ("sed -i '' 's/a/b/' backend/context/AGENT.md && cat x.md", ["backend/context/AGENT.md"]),
    ("sed -i.bak -e 's/a/b/' f1.py f2.py", ["f1.py", "f2.py"]),
    ("sed --in-place 's/a/b/' f.py", ["f.py"]),
])
def test_sed_in_place_targets(cmd, expected):
    assert parse_bash_write_targets(cmd) == expected


@pytest.mark.parametrize("cmd", ["sed -n '1,20p' f.py", "sed 's/a/b/' f.py > /dev/null"])
def test_sed_without_in_place_is_not_a_write(cmd):
    assert parse_bash_write_targets(cmd) == []


def test_heredoc_candidates_are_cd_anchored():
    assert parse_bash_heredoc_write_candidates(PATCH) == [
        "/repo/backend/core/x.py", "/repo/backend/core/settings.json",
    ]


@pytest.mark.parametrize("cmd", [
    "python3 - <<'PY'\nimport json; print(json.load(open('a.json')))\nPY",  # read only
    "cat > x.py <<'EOF'\nopen('y.py','w')\nEOF",  # not an interpreter (redirect parser owns it)
    "python3 -c \"open('a.py','w')\"",  # no heredoc
    "",
])
def test_heredoc_negatives(cmd):
    assert parse_bash_heredoc_write_candidates(cmd) == []


def test_heredoc_absolute_path_and_path_write_text():
    cmd = "source .venv/bin/activate && python - <<EOF\nfrom pathlib import Path\nPath('/tmp/out.md').write_text('hi')\nEOF"
    assert parse_bash_heredoc_write_candidates(cmd) == ["/tmp/out.md"]


# ── 2. governance gate on Bash ──────────────────────────────────────────────

async def _gate(cmd):
    from core.security_hooks import create_governance_file_gate

    return await create_governance_file_gate()(
        {"tool_name": "Bash", "tool_input": {"command": cmd}}, None, None
    )


@pytest.mark.parametrize("cmd,label", [
    ("sed -i '' 's/a/b/' backend/context/SOUL.md", "CONSTITUTIONAL"),
    ("echo x >> /u/.swarm-ai/SwarmWS/.context/STEERING.md", "STATUTORY"),
    ("python3 - <<'PY'\np='backend/context/AGENT.md'\nopen(p,'w').write('x')\nPY", "CONSTITUTIONAL"),
])
async def test_gate_advises_on_bash_governance_write(cmd, label):
    r = await _gate(cmd)
    assert r["decision"] == "approve"
    assert label in r.get("additionalContext", "")


@pytest.mark.parametrize("cmd", [
    "cat backend/context/SOUL.md",
    "sed -n '1,40p' backend/context/AGENT.md",
    "sed -i '' 's/a/b/' backend/core/x.py",
    "",
])
async def test_gate_silent_on_bash_reads_and_other_files(cmd):
    r = await _gate(cmd)
    assert r == {"decision": "approve"}


async def test_gate_never_blocks_on_parser_error(monkeypatch):
    def _boom(_c):
        raise RuntimeError("parser bug")

    monkeypatch.setattr("core.file_change_classifier.parse_bash_write_targets", _boom)
    assert await _gate("sed -i '' 's/a/b/' backend/context/SOUL.md") == {"decision": "approve"}


def test_gate_registered_for_bash():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "core" / "hook_builder.py").read_text()
    assert '"governance_file_gate", matcher="Write|Edit|Bash"' in src


# ── 3. live emit: mtime confirms the candidate ──────────────────────────────

def _drive_tool_call(orch, use, on_tool_run, final):
    """Drive the orchestrator: tool_use → (the tool runs: on_tool_run()) →
    tool_result → final ResultMessage. The tool's side effect happens AFTER the
    tool_use was consumed, as in production."""
    from claude_agent_sdk import ToolResultBlock, UserMessage

    tool_id = use.content[0].id

    class _Client:
        async def query(self, prompt):
            pass

        def receive_response(self):
            async def _gen():
                yield use
                on_tool_run()
                yield UserMessage(content=[
                    ToolResultBlock(tool_use_id=tool_id, content="ok", is_error=False),
                ])
                yield final
            return _gen()

    orch._parent._client = _Client()

    async def _run():
        events = []
        async for ev in orch._read_formatted_response():
            events.append(ev)
        return events

    return asyncio.run(_run())


def test_heredoc_patch_emits_only_the_file_it_changed(tmp_path, monkeypatch):
    from claude_agent_sdk import AssistantMessage, ToolUseBlock

    from core.permission_manager import permission_manager
    from tests.test_orchestrator_surface_wiring import _patch_boundaries
    from tests.test_tool_call_leak_guard import _make_orchestrator, _make_result_message

    permission_manager.clear_session("sess-test")
    _patch_boundaries(monkeypatch, review_worthy=True, kind="source")
    monkeypatch.setattr(
        "routers.workspace_api.resolve_path_to_physical",
        lambda raw, _ws: {"relative": os.path.basename(raw), "absolute": raw},
    )
    written, read_only = tmp_path / "x.py", tmp_path / "settings.json"
    old = time.time() - 600
    for f in (written, read_only):
        f.write_text("old")
        os.utime(f, (old, old))
    cmd = (
        f"cd {tmp_path} && python3 - <<'PY'\n"
        "s=open('settings.json').read()\nopen('x.py','w').write(s)\nPY"
    )
    use = AssistantMessage(
        content=[ToolUseBlock(id="tu-hd", name="Bash", input={"command": cmd})],
        model="test-model",
    )
    try:
        events = _drive_tool_call(
            _make_orchestrator(), use, lambda: written.write_text("new"), _make_result_message(),
        )
    finally:
        permission_manager.clear_session("sess-test")
    fc = [e for e in events if e.get("type") == "file_changed" and e.get("operation") == "written"]
    assert [e["absolutePath"] for e in fc] == [str(written)], "read-only literal must be dropped"
