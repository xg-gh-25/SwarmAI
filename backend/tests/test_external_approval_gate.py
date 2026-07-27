"""Tests for external_approval_gate — routes non-Bash EXTERNAL tool calls through
the PermissionManager approval flow (Gate-1 #1, generalizing C041 beyond Bash).

WHAT IS TESTED
--------------
Before this, a NON-Bash tool with off-machine side effects (an MCP tool that sends
email / posts to Slack / mutates a CRM) passed with ZERO approval gating: the
dangerous_command_gate is Bash-scoped and the SDK can_use_tool file handler allows
all non-file tools (and is None under global_user_mode). external_approval_gate is a
PreToolUse hook (fires regardless of global_user_mode) that:
  - approves Bash unconditionally (dangerous_command_gate owns Bash — no double-gate)
  - approves any non-EXTERNAL tool (READ/WRITE_LOCAL) — no prompt
  - routes EXTERNAL tools through PermissionManager enqueue → wait → decision
  - handles tool_use_id=None defensively (approve + skip tracking — Gate-1 B4)
"""

from __future__ import annotations

import asyncio

import pytest

from core.security_hooks import create_external_approval_gate


class _FakePM:
    """Records enqueue calls and returns a preset decision, mimicking PermissionManager's
    surface the gate uses. Not a MagicMock (spec discipline)."""

    def __init__(self, decision="approve"):
        self.enqueued = []
        self.stored = []
        self._decision = decision
        self.removed = []

    def is_command_approved(self, session_key, command):
        return False

    def is_resolved(self, session_id, tool_call_id):
        return False

    def store_pending_request(self, req):
        self.stored.append(req)

    async def enqueue_permission_request(self, session_id, req):
        self.enqueued.append((session_id, req))

    async def wait_for_permission_decision(self, request_id):
        return self._decision

    def remove_pending_request(self, request_id):
        self.removed.append(request_id)

    def approve_command(self, session_key, command):
        pass


def _invoke(gate, tool_name, tool_input=None, tool_use_id="toolu_1"):
    return asyncio.run(
        gate(
            {"tool_name": tool_name, "tool_input": tool_input or {}},
            tool_use_id,
            None,
        )
    )


def _is_approve(result):
    return result.get("decision") == "approve"


def _is_deny(result):
    hso = result.get("hookSpecificOutput", {})
    return hso.get("permissionDecision") == "deny"


class TestExternalIsGated:
    def test_external_mcp_tool_is_enqueued_and_approved(self):
        pm = _FakePM(decision="approve")
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "mcp__slack-mcp__post_message", {"text": "hi"})
        assert _is_approve(result)
        assert len(pm.enqueued) == 1, "EXTERNAL tool must be routed to approval"
        assert pm.enqueued[0][1]["toolName"] == "mcp__slack-mcp__post_message"

    def test_external_mcp_tool_denied(self):
        pm = _FakePM(decision="deny")
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "mcp__aws-outlook-mcp__email_send", {})
        assert _is_deny(result)
        assert len(pm.enqueued) == 1


class TestNonExternalPassesFreely:
    def test_read_mcp_tool_not_enqueued(self):
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "mcp__aws-sentral-mcp__search_accounts", {})
        assert _is_approve(result)
        assert pm.enqueued == [], "READ tool must NOT prompt"

    def test_local_write_mcp_tool_not_enqueued(self):
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "mcp__aws-outlook-mcp__email_draft", {})
        assert _is_approve(result)
        assert pm.enqueued == []

    def test_builtin_read_not_enqueued(self):
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm)
        assert _is_approve(_invoke(gate, "Read", {"file_path": "/x"}))
        assert pm.enqueued == []


class TestBashNotDoubleGated:
    def test_bash_approved_without_enqueue(self):
        # Bash is owned by dangerous_command_gate — this gate must skip it entirely
        # (no double-prompt), even though a `gh repo edit` Bash cmd is EXTERNAL-ish.
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "Bash", {"command": "gh repo edit --visibility private"})
        assert _is_approve(result)
        assert pm.enqueued == [], "Bash must be left to dangerous_command_gate"


class TestDefensiveToolUseId:
    def test_none_tool_use_id_approves_without_tracking(self):
        # Gate-1 B4: no tool_use_id → cannot correlate a durable approval; approve
        # and skip tracking (mirror ask_question_gate) rather than block.
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm)
        result = _invoke(gate, "mcp__slack-mcp__post_message", {}, tool_use_id=None)
        assert _is_approve(result)
        assert pm.enqueued == []


class TestHumanApprovalDisabled:
    def test_disabled_autodenies_external(self):
        pm = _FakePM()
        gate = create_external_approval_gate({}, "sess1", pm, enable_human_approval=False)
        result = _invoke(gate, "mcp__slack-mcp__post_message", {})
        assert _is_deny(result)
        assert pm.enqueued == []
