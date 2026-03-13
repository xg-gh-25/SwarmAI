"""Bug condition exploration tests for MCP tool label misclassification (Bug 1).

What is being tested:
- ``summarize_tool_use`` and ``get_tool_category`` from ``core.tool_summarizer``
- Verifies that MCP tool names (``mcp__ServerName__tool_name`` format) produce
  clean, meaningful labels instead of raw full MCP names with ``mcp__`` prefix.

Testing methodology: Unit tests + property-based testing with Hypothesis

Key properties / invariants being verified:
- MCP tool labels must NOT contain the raw ``mcp__`` prefix
- ``get_tool_category`` must return a meaningful category (not ``"fallback"``)
  for MCP tools with recognizable suffixes like ``email_search``
- No MCP name should unexpectedly match ``_WEB_SEARCH_NAMES``

**Validates: Requirements 1.1, 1.2**

CRITICAL: These tests are EXPECTED TO FAIL on unfixed code — failure confirms
the bugs exist. Do NOT fix the code when tests fail.
"""

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

from core.tool_summarizer import (
    summarize_tool_use,
    get_tool_category,
    _WEB_SEARCH_NAMES,
    _TOOL_SEARCH_NAMES,
    _SKILL_NAMES,
)

PROPERTY_SETTINGS = settings(
    max_examples=50,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# ---------------------------------------------------------------------------
# Hypothesis Strategies
# ---------------------------------------------------------------------------

_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz0123456789"
)

# Generate random MCP server names (e.g., "github", "aws_outlook_mcp")
_server_name = st.text(alphabet=_safe_chars, min_size=2, max_size=20)

# Generate random MCP tool names (e.g., "create_issue", "email_search")
_tool_name = st.text(alphabet=_safe_chars, min_size=2, max_size=20)

# Generate random input dict keys/values
_input_key = st.sampled_from(["query", "title", "path", "command", "url", "name", "data"])
_input_value = st.text(min_size=1, max_size=50)
_input_dict = st.dictionaries(_input_key, _input_value, min_size=0, max_size=3)


# ---------------------------------------------------------------------------
# Unit Tests — MCP Tool Label Misclassification
# ---------------------------------------------------------------------------

class TestMCPToolLabelBug:
    """Tests that demonstrate Bug 1: MCP tool labels contain raw mcp__ prefix."""

    def test_github_create_issue_no_raw_prefix(self):
        """summarize_tool_use for mcp__GitHub__create_issue should NOT
        contain the raw 'mcp__' prefix in the label."""
        label = summarize_tool_use("mcp__GitHub__create_issue", {"title": "Fix bug"})
        assert "mcp__" not in label, (
            f"Label contains raw mcp__ prefix: {label!r}"
        )

    def test_outlook_email_search_contains_extracted_name(self):
        """summarize_tool_use for mcp__aws_outlook_mcp__email_search should
        contain the extracted tool name 'email_search', not the full raw
        MCP name."""
        label = summarize_tool_use(
            "mcp__aws_outlook_mcp__email_search",
            {"query": "meeting notes"},
        )
        # The label should contain the extracted tool name segment
        assert "email_search" in label.lower(), (
            f"Label does not contain extracted tool name 'email_search': {label!r}"
        )
        # And should NOT contain the raw mcp__ prefix
        assert "mcp__" not in label, (
            f"Label contains raw mcp__ prefix: {label!r}"
        )

    def test_get_tool_category_mcp_search_tool(self):
        """get_tool_category for mcp__aws_outlook_mcp__email_search should
        return 'search', not 'fallback'."""
        category = get_tool_category("mcp__aws_outlook_mcp__email_search")
        assert category == "search", (
            f"Expected category 'search', got {category!r}"
        )

    def test_mcp_names_do_not_match_web_search_names(self):
        """Root cause investigation: verify that MCP tool names in
        mcp__ServerName__tool_name format do NOT accidentally match
        _WEB_SEARCH_NAMES via exact set membership.

        This confirms the root cause — MCP names are too long to match
        any category set, so they always fall to the fallback branch."""
        mcp_names = [
            "mcp__github__create_issue",
            "mcp__aws_outlook_mcp__email_search",
            "mcp__slack__post_message",
            "mcp__jira__search_issues",
        ]
        for mcp_name in mcp_names:
            lower = mcp_name.lower()
            assert lower not in _WEB_SEARCH_NAMES, (
                f"MCP name {mcp_name!r} unexpectedly matches "
                f"_WEB_SEARCH_NAMES: {_WEB_SEARCH_NAMES}"
            )


# ---------------------------------------------------------------------------
# Property-Based Test — MCP Tool Labels Never Contain Raw Prefix
# ---------------------------------------------------------------------------

class TestMCPToolLabelProperty:
    """Property-based test: for random MCP names mcp__{server}__{tool}
    with random input dicts, the label must never contain raw mcp__ prefix.

    **Validates: Requirements 1.1, 1.2**
    """

    @given(
        server=_server_name,
        tool=_tool_name,
        input_data=_input_dict,
    )
    @PROPERTY_SETTINGS
    def test_mcp_label_never_contains_raw_prefix(
        self, server: str, tool: str, input_data: dict
    ):
        """For any MCP tool name mcp__{server}__{tool}, the summarized
        label must never contain the raw 'mcp__' prefix."""
        mcp_name = f"mcp__{server}__{tool}"
        label = summarize_tool_use(mcp_name, input_data)
        assert "mcp__" not in label, (
            f"Label for {mcp_name!r} contains raw mcp__ prefix: {label!r}"
        )


# ===================================================================
# PRESERVATION TESTS — Existing SDK Tool Labels (Task 2)
# ===================================================================
#
# These tests verify existing behavior that MUST be preserved after
# the Bug 1 fix.  They MUST PASS on unfixed code.
#
# **Validates: Requirements 3.1, 3.2**
# ===================================================================

from core.tool_summarizer import (
    _BASH_NAMES,
    _READ_NAMES,
    _WRITE_NAMES,
    _SEARCH_NAMES,
    _WEB_FETCH_NAMES,
    _WEB_SEARCH_NAMES,
    _TOOL_SEARCH_NAMES,
    _SKILL_NAMES,
    _LIST_DIR_NAMES,
    _TODOWRITE_NAMES,
)


# ---------------------------------------------------------------------------
# Unit Tests — Preservation of SDK Tool Labels
# ---------------------------------------------------------------------------

class TestSDKToolLabelPreservation:
    """Verify that existing SDK tool labels remain unchanged after fixes.

    These tests lock down the current behavior of ``summarize_tool_use``
    for all built-in SDK tool categories.  They MUST PASS on unfixed code.

    **Validates: Requirements 3.1, 3.2**
    """

    def test_bash_label(self):
        """Bash tool produces 'Running: {command}' label."""
        label = summarize_tool_use("bash", {"command": "npm test"})
        assert label == "Running: npm test"

    def test_read_label(self):
        """Read tool produces 'Reading {path}' label."""
        label = summarize_tool_use("read", {"path": "src/app.ts"})
        assert label == "Reading src/app.ts"

    def test_websearch_label(self):
        """WebSearch tool produces 'Searching web for {query}' label."""
        label = summarize_tool_use("websearch", {"query": "python docs"})
        assert label == "Searching web for python docs"

    def test_unknown_tool_fallback(self):
        """Unknown tool with no input fields produces 'Using {name}'."""
        label = summarize_tool_use("unknown_tool", {})
        assert label == "Using unknown_tool"

    def test_write_label(self):
        """Write tool produces 'Writing to {path}' label."""
        label = summarize_tool_use("write", {"path": "config.json"})
        assert label == "Writing to config.json"

    def test_grep_label(self):
        """Grep/search tool produces 'Searching for {pattern}' label."""
        label = summarize_tool_use("grep", {"pattern": "TODO"})
        assert label == "Searching for TODO"

    def test_webfetch_label(self):
        """WebFetch tool produces 'Fetching {url}' label."""
        label = summarize_tool_use("webfetch", {"url": "https://example.com"})
        assert label == "Fetching https://example.com"

    def test_listdirectory_label(self):
        """ListDirectory tool produces 'Listing {path}' label."""
        label = summarize_tool_use("listdirectory", {"path": "src/"})
        assert label == "Listing src/"

    def test_todowrite_label(self):
        """TodoWrite tool produces 'Writing {n} todos' label."""
        label = summarize_tool_use("todowrite", {"todos": [1, 2, 3]})
        assert label == "Writing 3 todos"

    def test_bash_no_command_fallback(self):
        """Bash tool with no command falls back to 'Using {name}'."""
        label = summarize_tool_use("bash", {})
        assert label == "Using bash"

    def test_read_no_path_fallback(self):
        """Read tool with no path falls back to 'Using {name}'."""
        label = summarize_tool_use("read", {})
        assert label == "Using read"

    def test_toolsearch_strips_select_prefix(self):
        """ToolSearch tool strips 'select:' prefix and formats tool list."""
        label = summarize_tool_use("ToolSearch", {"query": "select:Bash,Read,Grep"})
        assert label == "Loading tools: Bash, Read, Grep"
        assert get_tool_category("ToolSearch") == "tool_search"

    def test_toolsearch_no_select_prefix(self):
        """ToolSearch with non-select query passes through unchanged."""
        label = summarize_tool_use("ToolSearch", {"query": "+slack send"})
        assert label == "Loading tools: +slack send"

    def test_toolsearch_empty_query(self):
        """ToolSearch with no query falls back to 'Using {name}'."""
        label = summarize_tool_use("ToolSearch", {})
        assert label == "Using ToolSearch"

    def test_toolsearch_select_single_tool(self):
        """ToolSearch with single tool in select: prefix."""
        label = summarize_tool_use("ToolSearch", {"query": "select:Skill"})
        assert label == "Loading tools: Skill"

    def test_skill_with_skill_name(self):
        """Skill tool with skill_name field shows the skill name."""
        label = summarize_tool_use("Skill", {"skill_name": "save-memory"})
        assert label == "Using skill: save-memory"
        assert get_tool_category("Skill") == "skill"

    def test_skill_with_skillname_field(self):
        """Skill tool with skillName field shows the skill name."""
        label = summarize_tool_use("Skill", {"skillName": "save-context"})
        assert label == "Using skill: save-context"

    def test_skill_no_name_fallback(self):
        """Skill tool with no name fields falls back to 'Using Skill'."""
        label = summarize_tool_use("Skill", {})
        assert label == "Using Skill"

    def test_skill_name_field_ignored_when_generic(self):
        """Skill tool ignores 'name' field when it equals 'Skill' (generic)."""
        label = summarize_tool_use("Skill", {"name": "Skill"})
        assert label == "Using Skill"

    def test_skill_name_field_used_when_specific(self):
        """Skill tool uses 'name' field when it's a specific skill name."""
        label = summarize_tool_use("Skill", {"name": "memory-distill"})
        assert label == "Using skill: memory-distill"


# ---------------------------------------------------------------------------
# Property-Based Tests — SDK Tool Label Preservation
# ---------------------------------------------------------------------------

# Build a combined set of all known SDK tool names for PBT strategies
_ALL_SDK_NAMES: list[str] = sorted(
    _BASH_NAMES
    | _READ_NAMES
    | _WRITE_NAMES
    | _SEARCH_NAMES
    | _WEB_FETCH_NAMES
    | _WEB_SEARCH_NAMES
    | _TOOL_SEARCH_NAMES
    | _SKILL_NAMES
    | _LIST_DIR_NAMES
    | _TODOWRITE_NAMES
)

# Map each category set to its expected label prefix/format
_CATEGORY_LABEL_PATTERNS: dict[str, tuple[set[str], str]] = {
    "bash": (_BASH_NAMES, "Running: "),
    "read": (_READ_NAMES, "Reading "),
    "write": (_WRITE_NAMES, "Writing to "),
    "search": (_SEARCH_NAMES, "Searching for "),
    "web_fetch": (_WEB_FETCH_NAMES, "Fetching "),
    "web_search": (_WEB_SEARCH_NAMES, "Searching web for "),
    "list_dir": (_LIST_DIR_NAMES, "Listing "),
}


class TestSDKToolLabelPreservationPBT:
    """Property-based tests verifying SDK tool labels are unchanged.

    For all SDK tool names from known category sets, the fixed function
    must produce the same category-specific label format as the original.

    **Validates: Requirements 3.1, 3.2**
    """

    @given(
        tool_name=st.sampled_from(_ALL_SDK_NAMES),
        context_value=st.text(min_size=1, max_size=40),
    )
    @PROPERTY_SETTINGS
    def test_sdk_tools_produce_category_specific_labels(
        self, tool_name: str, context_value: str,
    ):
        """For any SDK tool name from known category sets, assert the
        function produces the expected category-specific label prefix.

        **Validates: Requirements 3.1, 3.2**
        """
        lower = tool_name.lower()

        # Determine which category this tool belongs to and build
        # the appropriate input dict with the right field name
        if lower in _BASH_NAMES:
            input_data = {"command": context_value}
            expected_prefix = "Running: "
        elif lower in _READ_NAMES:
            input_data = {"path": context_value}
            expected_prefix = "Reading "
        elif lower in _WRITE_NAMES:
            input_data = {"path": context_value}
            expected_prefix = "Writing to "
        elif lower in _SEARCH_NAMES:
            input_data = {"pattern": context_value}
            expected_prefix = "Searching for "
        elif lower in _WEB_FETCH_NAMES:
            input_data = {"url": context_value}
            expected_prefix = "Fetching "
        elif lower in _WEB_SEARCH_NAMES:
            input_data = {"query": context_value}
            expected_prefix = "Searching web for "
        elif lower in _TOOL_SEARCH_NAMES:
            # ToolSearch strips "select:" prefix, so test with raw query
            input_data = {"query": context_value}
            expected_prefix = "Loading tools: "
        elif lower in _SKILL_NAMES:
            input_data = {"skill_name": context_value}
            expected_prefix = "Using skill: "
        elif lower in _LIST_DIR_NAMES:
            input_data = {"path": context_value}
            expected_prefix = "Listing "
        elif lower in _TODOWRITE_NAMES:
            # TodoWrite uses a list, not a string context
            input_data = {"todos": [1]}
            expected_prefix = "Writing 1 todos"
            label = summarize_tool_use(tool_name, input_data)
            assert label == expected_prefix, (
                f"TodoWrite label mismatch: {label!r} != {expected_prefix!r}"
            )
            return
        else:
            pytest.skip(f"Tool {tool_name!r} not in any known category")
            return

        label = summarize_tool_use(tool_name, input_data)
        assert label.startswith(expected_prefix), (
            f"Label for {tool_name!r} should start with "
            f"{expected_prefix!r}, got {label!r}"
        )

    @given(
        tool_name=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyz",
            min_size=3,
            max_size=15,
        ).filter(lambda n: n not in _ALL_SDK_NAMES and "__" not in n),
    )
    @PROPERTY_SETTINGS
    def test_unknown_tools_no_input_produce_using_fallback(
        self, tool_name: str,
    ):
        """For tools with no recognizable input fields, assert
        'Using {name}' fallback is preserved.

        **Validates: Requirements 3.2**
        """
        label = summarize_tool_use(tool_name, {})
        assert label == f"Using {tool_name}", (
            f"Fallback label mismatch for {tool_name!r}: {label!r}"
        )
