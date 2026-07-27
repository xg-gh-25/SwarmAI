"""Tests for the declarative tool risk taxonomy (core/tool_risk.py).

WHAT IS TESTED
--------------
`classify(tool_name, metadata=None) -> RiskClass` — the single source of truth for
a tool's intrinsic side-effect class, ported from andrewyng/openworker's risk.py and
adapted to SwarmAI's tool namespace (built-in tools + `mcp__Server__tool` MCP names).

Four classes:
  READ         no side effects        (Read/Glob/Grep, WebFetch/WebSearch, MCP reads)
  WRITE_LOCAL  mutates the workspace   (Write/Edit)
  EXEC         runs commands          (Bash)
  EXTERNAL     off-machine side effect (MCP send/post/reply/upload + CRM update/delete,
                                        git/gh external ops) → routed to approval

KEY DESIGN RULES (Gate-1 B3 fix — fail-OPEN, not fail-closed):
  - MCP read verbs (search|read|get|list|fetch) are READ, never EXTERNAL (PIT56: a
    prompt-storm on every search_ call trains blind-approve — worse than a missed gate).
  - LOCAL-write MCP tools (email_draft, create_account_summary, sift_insights_create)
    are NOT EXTERNAL — they mutate local/workspace state, not off-machine state.
  - Unknown MCP tools default to READ (fail-OPEN): a missed gate is recoverable; a
    false-positive prompt-storm is not.
  - classify() is PURE and contains NO gate carve-outs and imports NOTHING from
    security_hooks (one-direction rule: classify decides CLASS, gates decide ACTION).
"""

from __future__ import annotations

import pytest

from core.tool_risk import RiskClass, classify


class TestClassifyBuiltins:
    def test_read_tools(self):
        assert classify("Read") is RiskClass.READ
        assert classify("Glob") is RiskClass.READ
        assert classify("Grep") is RiskClass.READ

    def test_web_read_tools_are_read(self):
        # WebFetch/WebSearch retrieve, they do not mutate off-machine state.
        assert classify("WebFetch") is RiskClass.READ
        assert classify("WebSearch") is RiskClass.READ

    def test_write_local(self):
        assert classify("Write") is RiskClass.WRITE_LOCAL
        assert classify("Edit") is RiskClass.WRITE_LOCAL

    def test_exec(self):
        assert classify("Bash") is RiskClass.EXEC

    def test_agent_and_skill_are_not_external(self):
        # Spawning a sub-agent / invoking a skill is in-process orchestration,
        # not an off-machine side effect — must NOT prompt.
        assert classify("Task") is RiskClass.READ
        assert classify("Skill") is RiskClass.READ
        assert classify("TodoWrite") is RiskClass.READ


class TestClassifyMcpReads:
    """MCP read verbs must be READ — the PIT56 false-positive guard."""

    @pytest.mark.parametrize(
        "name",
        [
            "mcp__aws-sentral-mcp__search_accounts",
            "mcp__aws-sentral-mcp__fetch_account_details",
            "mcp__aws-sentral-mcp__get_opportunity_details",
            "mcp__aws-sentral-mcp__list_territories",
            "mcp__aws-outlook-mcp__email_read",
            "mcp__slack-mcp__get_messages",
            "mcp__hs-kmine-mcp__highspot_search",
        ],
    )
    def test_mcp_read_verbs_are_read(self, name):
        assert classify(name) is RiskClass.READ


class TestClassifyMcpExternal:
    """Genuinely off-machine MCP write verbs must be EXTERNAL."""

    @pytest.mark.parametrize(
        "name",
        [
            "mcp__slack-mcp__post_message",
            "mcp__slack-mcp__bulk_post_message",
            "mcp__aws-outlook-mcp__email_send",
            "mcp__aws-outlook-mcp__email_reply",
            "mcp__aws-outlook-mcp__email_forward",
            "mcp__aws-sentral-mcp__update_opportunity",  # CRM off-machine mutation
            "mcp__slack-mcp__upload_file",
        ],
    )
    def test_mcp_offmachine_verbs_are_external(self, name):
        assert classify(name) is RiskClass.EXTERNAL


class TestClassifyLocalWriteNotExternal:
    """Gate-1 B3: LOCAL-write MCP tools are NOT EXTERNAL (no off-machine effect)."""

    @pytest.mark.parametrize(
        "name",
        [
            "mcp__aws-outlook-mcp__email_draft",  # composes a draft, does not send
            "mcp__aws-sentral-mcp__create_account_summary",  # local analysis artifact
            "mcp__aws-sentral-mcp__sift_insights_create",  # local insight record
        ],
    )
    def test_local_write_mcp_not_external(self, name):
        # These must not trigger an approval prompt — they don't leave the machine.
        assert classify(name) is not RiskClass.EXTERNAL


class TestClassifyUnknownFailOpen:
    """Gate-1 B3: unknown MCP tools default to READ (fail-OPEN)."""

    def test_unknown_mcp_tool_is_read(self):
        assert classify("mcp__some-new-mcp__frobnicate_widget") is RiskClass.READ

    def test_unknown_builtin_is_read(self):
        assert classify("SomeFutureBuiltinTool") is RiskClass.READ


class TestClassifyGitExternal:
    """git/gh external ops carried as Bash are EXEC (dangerous_command_gate owns them);
    but classify() must still recognize the EXTERNAL verb family for non-Bash callers."""

    def test_bash_is_exec_not_external(self):
        # Bash is always EXEC — the hardened dangerous_command_gate owns it,
        # external_approval_gate must skip Bash (no double-gate).
        assert classify("Bash") is RiskClass.EXEC


class TestOneDirectionInvariant:
    """classify() must not import security_hooks (one-direction: CLASS not ACTION)."""

    def test_tool_risk_does_not_import_security_hooks(self):
        import core.tool_risk as tr
        import inspect

        src = inspect.getsource(tr)
        # Check actual IMPORT statements, not prose mentions in docstrings/comments.
        import_lines = [
            ln for ln in src.splitlines()
            if ln.strip().startswith(("import ", "from "))
        ]
        joined_imports = "\n".join(import_lines)
        assert "security_hooks" not in joined_imports, (
            "tool_risk must not import security_hooks (one-direction rule)"
        )
        # No gate logic copied in: it must not import fnmatch (the glob engine the
        # dangerous_command_gate uses), nor carry the actual dangerous glob literal.
        assert "fnmatch" not in joined_imports
        assert 'rm -rf /' not in src  # the real dangerous glob, not the word "rm"
