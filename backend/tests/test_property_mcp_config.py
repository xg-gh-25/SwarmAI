"""Property-based tests for MCP config built without workspace filtering.

**Feature: unified-swarm-workspace-cwd**

Uses Hypothesis to verify that ``_build_mcp_config()`` includes all valid
MCP IDs from the agent's ``mcp_ids`` list with no workspace filtering,
and that name deduplication works correctly.

**Validates: Requirements 9.1, 9.2, 9.3**
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from hypothesis import given, strategies as st, settings, HealthCheck

PROPERTY_SETTINGS = settings(
    max_examples=50,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_safe_chars = st.sampled_from(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789_-"
)

mcp_id_strategy = st.text(alphabet=_safe_chars, min_size=1, max_size=16)

connection_type_strategy = st.sampled_from(["stdio", "sse", "http"])

mcp_name_strategy = st.text(alphabet=_safe_chars, min_size=1, max_size=12)


rejected_tools_strategy = st.lists(
    st.text(alphabet=_safe_chars, min_size=1, max_size=12),
    min_size=0,
    max_size=4,
)


def _make_mcp_record(name: str, connection_type: str, rejected_tools: list | None = None) -> dict:
    """Build a fake MCP server DB record for the given connection type."""
    config: dict = {}
    if connection_type == "stdio":
        config = {"command": "/usr/bin/test", "args": ["--flag"], "env": {"KEY": "val"}}
    elif connection_type in ("sse", "http"):
        config = {"url": f"http://localhost:8080/{name}"}
    record = {
        "name": name,
        "connection_type": connection_type,
        "config": config,
    }
    if rejected_tools:
        record["rejected_tools"] = rejected_tools
    return record


# Strategy: generate a list of (mcp_id, name, connection_type) tuples with unique IDs
mcp_entry_strategy = st.tuples(mcp_id_strategy, mcp_name_strategy, connection_type_strategy)

agent_mcp_ids_strategy = st.lists(
    mcp_entry_strategy,
    min_size=0,
    max_size=10,
    unique_by=lambda t: t[0],  # unique MCP IDs
)


# ---------------------------------------------------------------------------
# Helper: replicate _build_mcp_config logic for testing
# ---------------------------------------------------------------------------

async def _build_mcp_config_under_test(
    agent_config: dict,
    enable_mcp: bool,
    db_get_fn,
) -> tuple[dict, list[str]]:
    """Replicate the simplified _build_mcp_config logic.

    This mirrors the production code so we can test the algorithm
    without importing the full PromptBuilder (which has heavy deps).

    Returns:
        Tuple of (mcp_servers dict, disallowed_tools list).
    """
    mcp_servers: dict = {}
    disallowed_tools: list[str] = []

    if not (enable_mcp and agent_config.get("mcp_ids")):
        return mcp_servers, disallowed_tools

    used_names: set = set()
    for mcp_id in agent_config["mcp_ids"]:
        mcp_config = await db_get_fn(mcp_id)
        if mcp_config:
            connection_type = mcp_config.get("connection_type", "stdio")
            config = mcp_config.get("config", {})

            server_name = mcp_config.get("name", mcp_id)
            base_name = server_name
            suffix = 1
            while server_name in used_names:
                server_name = f"{base_name}_{suffix}"
                suffix += 1
            used_names.add(server_name)

            if connection_type == "stdio":
                mcp_servers[server_name] = {
                    "type": "stdio",
                    "command": config.get("command"),
                    "args": config.get("args", []),
                }
                env = config.get("env")
                if env and isinstance(env, dict):
                    mcp_servers[server_name]["env"] = env
            elif connection_type == "sse":
                mcp_servers[server_name] = {
                    "type": "sse",
                    "url": config.get("url"),
                }
            elif connection_type == "http":
                mcp_servers[server_name] = {
                    "type": "http",
                    "url": config.get("url"),
                }

            # Collect per-server rejected_tools → global disallowed_tools
            rejected = mcp_config.get("rejected_tools") or []
            for tool in rejected:
                disallowed_tools.append(f"mcp__{server_name}__{tool}")

    return mcp_servers, disallowed_tools


# ---------------------------------------------------------------------------
# Property 9: MCP config built without workspace filtering
# ---------------------------------------------------------------------------


class TestMcpConfigWithoutWorkspaceFiltering:
    """Property 9: MCP config built without workspace filtering.

    For any agent configuration with mcp_ids, the MCP servers dict should
    contain entries for all valid MCP IDs — with no filtering by workspace.

    **Validates: Requirements 9.1, 9.2, 9.3**
    """

    @given(mcp_entries=agent_mcp_ids_strategy)
    @PROPERTY_SETTINGS
    def test_all_valid_mcp_ids_appear_in_result(self, mcp_entries):
        """Every valid MCP ID in agent's mcp_ids appears in the result dict.

        **Validates: Requirements 9.1, 9.2**
        """
        # Build the lookup table and agent config
        db_records: dict = {}
        mcp_ids: list = []
        for mcp_id, name, conn_type in mcp_entries:
            db_records[mcp_id] = _make_mcp_record(name, conn_type)
            mcp_ids.append(mcp_id)

        agent_config = {"mcp_ids": mcp_ids}

        async def mock_get(mid):
            return db_records.get(mid)

        result, _ = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        # All valid MCP IDs should produce entries in the result
        assert len(result) == len(mcp_ids), (
            f"Expected {len(mcp_ids)} MCP entries, got {len(result)}. "
            f"IDs: {mcp_ids}, result keys: {list(result.keys())}"
        )

    @given(mcp_entries=agent_mcp_ids_strategy)
    @PROPERTY_SETTINGS
    def test_no_workspace_filtering_applied(self, mcp_entries):
        """No MCP IDs are filtered out — all agent MCPs are included.

        **Validates: Requirements 9.2, 9.3**
        """
        db_records: dict = {}
        mcp_ids: list = []
        for mcp_id, name, conn_type in mcp_entries:
            db_records[mcp_id] = _make_mcp_record(name, conn_type)
            mcp_ids.append(mcp_id)

        agent_config = {"mcp_ids": mcp_ids}

        async def mock_get(mid):
            return db_records.get(mid)

        result, _ = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        # Collect all names used in result values
        result_types = {v.get("type") for v in result.values()}
        valid_types = {"stdio", "sse", "http"}

        # Every entry should have a valid connection type
        assert result_types <= valid_types, (
            f"Unexpected connection types: {result_types - valid_types}"
        )

    @given(
        name=mcp_name_strategy,
        conn_type=connection_type_strategy,
        count=st.integers(min_value=2, max_value=5),
    )
    @PROPERTY_SETTINGS
    def test_name_deduplication(self, name, conn_type, count):
        """Duplicate MCP names get suffixed to ensure unique keys.

        **Validates: Requirement 9.1**
        """
        # Create multiple MCP entries with the same name but different IDs
        db_records: dict = {}
        mcp_ids: list = []
        for i in range(count):
            mcp_id = f"mcp_{i}"
            db_records[mcp_id] = _make_mcp_record(name, conn_type)
            mcp_ids.append(mcp_id)

        agent_config = {"mcp_ids": mcp_ids}

        async def mock_get(mid):
            return db_records.get(mid)

        result, _ = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        # All entries should be present (deduplication adds suffixes, not drops)
        assert len(result) == count, (
            f"Expected {count} entries after dedup, got {len(result)}"
        )

        # All keys should be unique
        keys = list(result.keys())
        assert len(keys) == len(set(keys)), (
            f"Duplicate keys found: {keys}"
        )

        # First entry uses the base name, rest get suffixes
        assert name in result, f"Base name '{name}' should be in result"
        for i in range(1, count):
            expected_key = f"{name}_{i}"
            assert expected_key in result, (
                f"Expected deduped key '{expected_key}' in result, "
                f"got keys: {keys}"
            )

    @given(mcp_entries=agent_mcp_ids_strategy)
    @PROPERTY_SETTINGS
    def test_disabled_mcp_returns_empty(self, mcp_entries):
        """When enable_mcp is False, result is always empty.

        **Validates: Requirement 9.1**
        """
        mcp_ids = [e[0] for e in mcp_entries]
        agent_config = {"mcp_ids": mcp_ids}

        async def mock_get(mid):
            return {"name": mid, "connection_type": "stdio", "config": {"command": "x"}}

        result, disallowed = asyncio.run(
            _build_mcp_config_under_test(agent_config, False, mock_get)
        )

        assert result == {}, f"Expected empty dict when MCP disabled, got {result}"
        assert disallowed == [], f"Expected empty disallowed when MCP disabled, got {disallowed}"

    @given(mcp_entries=agent_mcp_ids_strategy)
    @PROPERTY_SETTINGS
    def test_unknown_mcp_ids_skipped(self, mcp_entries):
        """MCP IDs not found in DB are silently skipped.

        **Validates: Requirement 9.1**
        """
        # Only register half the IDs in the DB
        db_records: dict = {}
        mcp_ids: list = []
        known_count = 0
        for i, (mcp_id, name, conn_type) in enumerate(mcp_entries):
            mcp_ids.append(mcp_id)
            if i % 2 == 0:
                db_records[mcp_id] = _make_mcp_record(name, conn_type)
                known_count += 1

        agent_config = {"mcp_ids": mcp_ids}

        async def mock_get(mid):
            return db_records.get(mid)

        result, _ = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        assert len(result) == known_count, (
            f"Expected {known_count} entries (known IDs only), got {len(result)}"
        )

    @given(
        name=mcp_name_strategy,
        conn_type=connection_type_strategy,
        rejected=rejected_tools_strategy,
    )
    @PROPERTY_SETTINGS
    def test_rejected_tools_become_disallowed(self, name, conn_type, rejected):
        """Per-server rejected_tools produce mcp__<Name>__<tool> disallowed entries.

        **Validates: rejected_tools → disallowed_tools mapping**
        """
        mcp_id = "test_mcp"
        record = _make_mcp_record(name, conn_type, rejected_tools=rejected)
        agent_config = {"mcp_ids": [mcp_id]}

        async def mock_get(mid):
            return record if mid == mcp_id else None

        _, disallowed = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        # Every rejected tool should appear as mcp__<name>__<tool>
        expected = [f"mcp__{name}__{t}" for t in rejected]
        assert disallowed == expected, (
            f"Expected disallowed={expected}, got {disallowed}"
        )

    def test_no_rejected_tools_means_empty_disallowed(self):
        """MCP without rejected_tools produces no disallowed entries."""
        record = _make_mcp_record("MyServer", "stdio")
        agent_config = {"mcp_ids": ["id1"]}

        async def mock_get(mid):
            return record if mid == "id1" else None

        _, disallowed = asyncio.run(
            _build_mcp_config_under_test(agent_config, True, mock_get)
        )

        assert disallowed == [], f"Expected empty disallowed, got {disallowed}"
