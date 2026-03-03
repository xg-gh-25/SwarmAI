"""Property-based tests for SwarmAgent (system agent) behavior.

Uses Hypothesis to verify universal properties across all valid inputs.

**Feature: swarm-agent-system-default**
"""
import pytest
import asyncio
from hypothesis import given, strategies as st, settings, HealthCheck
from fastapi.testclient import TestClient
from datetime import datetime


# Suppress function-scoped fixture warning since we're testing updates to
# the same system agent across iterations (which is the intended behavior)
PROPERTY_SETTINGS = settings(
    max_examples=100,
    suppress_health_check=[HealthCheck.function_scoped_fixture]
)


# Strategy for generating valid agent names
# Agent names can contain letters, numbers, spaces, and common punctuation
# but must be non-empty and different from "SwarmAgent"
valid_name_strategy = st.text(
    alphabet=st.characters(
        whitelist_categories=('L', 'N', 'P', 'S'),
        whitelist_characters=' -_'
    ),
    min_size=1,
    max_size=100
).filter(lambda x: x.strip() and x.strip() != "SwarmAgent")


class TestSwarmAgentNameUpdateProtection:
    """Property 1: Name Update Protection.

    **Feature: swarm-agent-system-default, Property 1: Name Update Protection**
    **Validates: Requirements 1.2**

    For any update request to SwarmAgent containing a name change,
    the system SHALL reject the request and the agent name SHALL
    remain "SwarmAgent".
    """

    @pytest.fixture
    def swarm_agent_id(self, client: TestClient) -> str:
        """Create a SwarmAgent (system agent) for testing."""
        from database import db

        async def create_system_agent():
            now = datetime.now().isoformat()
            agent_data = {
                "id": "swarm-agent-property-test",
                "name": "SwarmAgent",
                "description": "System agent for property testing",
                "model": "claude-sonnet-4-20250514",
                "permission_mode": "default",
                "is_default": False,
                "is_system_agent": True,
                "allowed_skills": [],
                "mcp_ids": [],
                "created_at": now,
                "updated_at": now,
            }
            await db.agents.put(agent_data)
            return agent_data["id"]

        return asyncio.get_event_loop().run_until_complete(create_system_agent())

    @given(name=valid_name_strategy)
    @PROPERTY_SETTINGS
    def test_name_update_rejected_for_any_valid_name(
        self, client: TestClient, swarm_agent_id: str, name: str
    ):
        """All name updates to SwarmAgent are rejected.

        **Validates: Requirements 1.2**
        """
        from hypothesis import assume
        assume(name.strip())
        assume(name.strip() != "SwarmAgent")

        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={"name": name}
        )

        assert response.status_code == 400, (
            f"Expected 400 for name '{name}', got {response.status_code}"
        )

        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system agent" in data["message"].lower()

        get_response = client.get(f"/api/agents/{swarm_agent_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "SwarmAgent"

    @given(name=valid_name_strategy)
    @PROPERTY_SETTINGS
    def test_name_update_rejected_preserves_other_properties(
        self, client: TestClient, swarm_agent_id: str, name: str
    ):
        """Rejected name updates do not affect other agent properties.

        **Validates: Requirements 1.2**
        """
        from hypothesis import assume
        assume(name.strip())
        assume(name.strip() != "SwarmAgent")

        get_response = client.get(f"/api/agents/{swarm_agent_id}")
        assert get_response.status_code == 200
        original_data = get_response.json()

        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={"name": name}
        )
        assert response.status_code == 400

        get_response_after = client.get(f"/api/agents/{swarm_agent_id}")
        assert get_response_after.status_code == 200
        after_data = get_response_after.json()

        assert after_data["name"] == original_data["name"]
        assert after_data["description"] == original_data["description"]
        assert after_data["is_system_agent"] == original_data["is_system_agent"]
        assert after_data["id"] == original_data["id"]

    @given(
        name=valid_name_strategy,
        description=st.text(min_size=0, max_size=200)
    )
    @PROPERTY_SETTINGS
    def test_name_update_rejected_even_with_valid_other_fields(
        self, client: TestClient, swarm_agent_id: str, name: str, description: str
    ):
        """Name updates are rejected even when combined with valid field updates.

        **Validates: Requirements 1.2**
        """
        from hypothesis import assume
        assume(name.strip())
        assume(name.strip() != "SwarmAgent")

        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={"name": name, "description": description}
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system agent" in data["message"].lower()

        get_response = client.get(f"/api/agents/{swarm_agent_id}")
        assert get_response.status_code == 200
        assert get_response.json()["name"] == "SwarmAgent"

    def test_same_name_update_allowed(self, client: TestClient, swarm_agent_id: str):
        """Updating SwarmAgent with the same name is allowed.

        **Validates: Requirements 1.2**
        """
        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={"name": "SwarmAgent", "description": "Updated description"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "SwarmAgent"
        assert data["description"] == "Updated description"


class TestSwarmAgentInitializationIdempotence:
    """Property 5: Initialization Idempotence.

    **Feature: swarm-agent-system-default, Property 5: Initialization Idempotence**
    **Validates: Requirements 7.4**

    For any number of application restarts (calls to ensure_default_agent()),
    the system SHALL not create duplicate MCP records, and SwarmAgent
    SHALL have exactly one binding per system resource.
    """

    @given(
        num_calls=st.integers(min_value=2, max_value=10)
    )
    @PROPERTY_SETTINGS
    def test_no_duplicate_resources_after_multiple_initializations(
        self, client: TestClient, num_calls: int
    ):
        """Multiple ensure_default_agent() calls do not create duplicate resources.

        **Validates: Requirements 7.4**
        """
        from core.agent_manager import ensure_default_agent
        from database import db

        async def run_multiple_initializations():
            # Run ensure_default_agent() multiple times
            for _ in range(num_calls):
                await ensure_default_agent()

            # Query all MCPs
            all_mcps = await db.mcp_servers.list()

            return all_mcps

        all_mcps = asyncio.get_event_loop().run_until_complete(
            run_multiple_initializations()
        )

        # Verify no duplicate MCP IDs
        mcp_ids = [m["id"] for m in all_mcps]
        assert len(mcp_ids) == len(set(mcp_ids)), (
            f"Duplicate MCP IDs found after {num_calls} initializations: "
            f"{[mid for mid in mcp_ids if mcp_ids.count(mid) > 1]}"
        )

        # Verify no duplicate MCP names (system MCPs should be unique by name)
        system_mcp_names = [m["name"] for m in all_mcps if m.get("is_system")]
        assert len(system_mcp_names) == len(set(system_mcp_names)), (
            f"Duplicate system MCP names found: "
            f"{[n for n in system_mcp_names if system_mcp_names.count(n) > 1]}"
        )

    @given(
        num_calls=st.integers(min_value=2, max_value=10)
    )
    @PROPERTY_SETTINGS
    def test_swarm_agent_has_exactly_one_binding_per_system_resource(
        self, client: TestClient, num_calls: int
    ):
        """SwarmAgent has exactly one binding per system resource after multiple inits.

        **Validates: Requirements 7.4**
        """
        from core.agent_manager import ensure_default_agent, DEFAULT_AGENT_ID
        from database import db

        async def run_and_check_bindings():
            # Run ensure_default_agent() multiple times
            for _ in range(num_calls):
                await ensure_default_agent()

            # Get SwarmAgent
            swarm_agent = await db.agents.get(DEFAULT_AGENT_ID)

            # Get all system MCPs
            system_mcps = await db.mcp_servers.list_by_system()

            return swarm_agent, system_mcps

        swarm_agent, system_mcps = asyncio.get_event_loop().run_until_complete(
            run_and_check_bindings()
        )

        assert swarm_agent is not None, "SwarmAgent should exist after initialization"

        # Verify no duplicate bindings in mcp_ids
        agent_mcp_ids = swarm_agent.get("mcp_ids", [])
        assert len(agent_mcp_ids) == len(set(agent_mcp_ids)), (
            f"Duplicate MCP bindings in SwarmAgent after {num_calls} initializations: "
            f"{[mid for mid in agent_mcp_ids if agent_mcp_ids.count(mid) > 1]}"
        )

        # Verify all system MCPs are bound exactly once
        system_mcp_ids = {m["id"] for m in system_mcps}
        bound_system_mcps = system_mcp_ids & set(agent_mcp_ids)
        assert bound_system_mcps == system_mcp_ids, (
            f"Not all system MCPs are bound. Missing: {system_mcp_ids - bound_system_mcps}"
        )

    @given(
        num_calls=st.integers(min_value=2, max_value=10)
    )
    @PROPERTY_SETTINGS
    def test_swarm_agent_properties_stable_across_initializations(
        self, client: TestClient, num_calls: int
    ):
        """SwarmAgent core properties remain stable across multiple initializations.

        **Validates: Requirements 7.4**
        """
        from core.agent_manager import ensure_default_agent, DEFAULT_AGENT_ID, SWARM_AGENT_NAME

        async def run_and_check_stability():
            results = []
            for _ in range(num_calls):
                agent = await ensure_default_agent()
                results.append({
                    "id": agent.get("id"),
                    "name": agent.get("name"),
                    "is_system_agent": agent.get("is_system_agent"),
                    "is_default": agent.get("is_default"),
                })
            return results

        results = asyncio.get_event_loop().run_until_complete(run_and_check_stability())

        # Verify all results have consistent core properties
        # Note: SQLite stores booleans as integers (0/1), so we use bool() for comparison
        for i, result in enumerate(results):
            assert result["id"] == DEFAULT_AGENT_ID, (
                f"Agent ID changed on call {i+1}: expected {DEFAULT_AGENT_ID}, got {result['id']}"
            )
            assert result["name"] == SWARM_AGENT_NAME, (
                f"Agent name changed on call {i+1}: expected {SWARM_AGENT_NAME}, got {result['name']}"
            )
            assert bool(result["is_system_agent"]) is True, (
                f"is_system_agent changed on call {i+1}: expected True, got {result['is_system_agent']}"
            )
            assert bool(result["is_default"]) is True, (
                f"is_default changed on call {i+1}: expected True, got {result['is_default']}"
            )

    def test_single_swarm_agent_exists_after_multiple_initializations(
        self, client: TestClient
    ):
        """Only one SwarmAgent exists after multiple initializations.

        **Validates: Requirements 7.4**
        """
        from core.agent_manager import ensure_default_agent, SWARM_AGENT_NAME
        from database import db

        async def run_and_count_agents():
            # Run ensure_default_agent() 5 times
            for _ in range(5):
                await ensure_default_agent()

            # Get all agents
            all_agents = await db.agents.list()
            return all_agents

        all_agents = asyncio.get_event_loop().run_until_complete(run_and_count_agents())

        # Count agents named "SwarmAgent"
        swarm_agents = [a for a in all_agents if a.get("name") == SWARM_AGENT_NAME]
        assert len(swarm_agents) == 1, (
            f"Expected exactly 1 SwarmAgent, found {len(swarm_agents)}"
        )

        # Count system agents
        system_agents = [a for a in all_agents if a.get("is_system_agent")]
        assert len(system_agents) == 1, (
            f"Expected exactly 1 system agent, found {len(system_agents)}"
        )
