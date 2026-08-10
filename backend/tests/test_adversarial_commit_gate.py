"""Tests for create_adversarial_commit_gate — the R1 "no commit without adversarial
review first" PreToolUse Bash backstop (CLASS A skip-attempt #12, 2026-08-10).

The gate DENYs `git commit` unless an ADVERSARIAL-review sub-agent completed this
session (a session_<sid>_adv_ marker exists). Tightened in run_df2668b4: a base
SubagentStop marker (any sub-agent, incl. Explore) no longer suffices — only the
adversarial marker create_agent_tool_audit_hook writes on adversarial completion.

Invariants:
  • DENY a git commit when the session has no ADVERSARIAL marker.
  • DENY when only a base (non-adversarial, e.g. Explore) marker exists.
  • APPROVE once an adversarial marker exists.
  • APPROVE all non-commit / non-Bash commands (fail-safe).
  • FAIL-OPEN: missing session id, unreadable dir → approve (never false-block).
  • SWARM_ADVERSARIAL_GATE_FORCE=1 → approve (sanctioned explicit bypass).
  • Detector reuses the wrapper-strip / git-global-option / segment-split discipline.
"""

import asyncio
import os
from pathlib import Path

import pytest

from core import security_hooks
from core.security_hooks import (
    create_adversarial_commit_gate,
    _command_has_git_commit,
)


@pytest.fixture
def audit_dir(tmp_path, monkeypatch):
    """Point the gate's marker dir at a temp path so tests don't touch real state."""
    d = tmp_path / "pipeline_agent_audit"
    monkeypatch.setattr(security_hooks, "_AGENT_AUDIT_DIR", d)
    return d


def _run(gate, command, tool_name="Bash"):
    return asyncio.run(
        gate({"tool_name": tool_name, "tool_input": {"command": command}}, None, None)
    )


def _is_deny(result):
    return result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _mark(audit_dir: Path, session_id: str):
    """Write an ADVERSARIAL marker (what the gate now requires)."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"session_{session_id}_adv_123.marker").write_text('{"adversarial": true}')


def _mark_base(audit_dir: Path, session_id: str):
    """Write a base (non-adversarial) marker — an Explore/investigation agent ran."""
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / f"session_{session_id}_123.marker").write_text("{}")


# ── detector: what counts as a git commit ──────────────────────────────────

class TestCommitDetector:
    @pytest.mark.parametrize("cmd", [
        "git commit -m 'x'",
        "git commit",
        "git -C /repo commit -m x",
        "git -c user.name=y commit -m x",
        "env A=b git commit -m x",
        "sudo git commit --amend --no-edit",
        "git add -A && git commit -m 'done'",   # chained after a benign op
        "git status; git commit -m x",
        "cd /repo\ngit commit -m x",            # multiline
    ])
    def test_positive(self, cmd):
        assert _command_has_git_commit(cmd) is True

    @pytest.mark.parametrize("cmd", [
        "git status",
        "git add -A",
        "git push origin main",
        "git log --oneline",
        "git diff --stat",
        "echo 'git commit is the plan'",         # commit only inside a quoted string
        "python -m pytest",
        "ls -la",
    ])
    def test_negative(self, cmd):
        assert _command_has_git_commit(cmd) is False


# ── gate behavior ───────────────────────────────────────────────────────────

class TestGateDeniesWithoutEvidence:
    def test_commit_denied_when_no_marker(self, audit_dir):
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _is_deny(_run(gate, "git commit -m 'fix'")) is True

    def test_commit_approved_after_adversarial_marker(self, audit_dir):
        _mark(audit_dir, "sess-A")
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _run(gate, "git commit -m 'fix'") == {"decision": "approve"}

    def test_commit_denied_with_only_base_marker(self, audit_dir):
        # An Explore/investigation agent ran (base marker) but no adversarial
        # review — the exact hole run_df2668b4 closed. Must still DENY.
        _mark_base(audit_dir, "sess-A")
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _is_deny(_run(gate, "git commit -m 'fix'")) is True

    def test_marker_is_session_scoped(self, audit_dir):
        # A marker for a DIFFERENT session must NOT authorize this one.
        _mark(audit_dir, "other-sess")
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _is_deny(_run(gate, "git commit -m 'fix'")) is True


class TestGateFailSafe:
    def test_non_commit_git_approved(self, audit_dir):
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _run(gate, "git push origin main") == {"decision": "approve"}

    def test_non_bash_approved(self, audit_dir):
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _run(gate, "git commit -m x", tool_name="Edit") == {"decision": "approve"}

    def test_fail_open_when_no_session_id(self, audit_dir):
        # No session id → cannot scope → fail open (never block on missing identity).
        gate = create_adversarial_commit_gate({"sdk_session_id": ""})
        assert _run(gate, "git commit -m x") == {"decision": "approve"}

    def test_force_override(self, audit_dir, monkeypatch):
        monkeypatch.setenv("SWARM_ADVERSARIAL_GATE_FORCE", "1")
        gate = create_adversarial_commit_gate({"sdk_session_id": "sess-A"})
        assert _run(gate, "git commit -m x") == {"decision": "approve"}


class TestReaderWriterPathAgreement:
    """CRITICAL regression guard (adversarial finding #6): every OTHER test in this
    file monkeypatches _AGENT_AUDIT_DIR, so none can catch a reader≠writer path
    divergence — and if the READER dir (this gate) ever drifts from the WRITER dir
    (runtime_hooks.create_agent_tool_audit_hook), the gate reads an empty dir and
    DENYs EVERY commit in production while the suite stays green. Lock the two paths
    to the same location. Does NOT monkeypatch — asserts the real module constants."""

    def test_reader_dir_equals_writer_dir(self):
        from core import runtime_hooks
        assert security_hooks._AGENT_AUDIT_DIR == runtime_hooks._PIPELINE_AUDIT_DIR
