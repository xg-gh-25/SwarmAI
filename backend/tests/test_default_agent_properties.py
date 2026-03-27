"""Property-based tests for default agent behavior.

Uses Hypothesis to verify universal properties across all valid inputs.
"""
import pytest
from hypothesis import given, strategies as st, settings, assume, HealthCheck
from fastapi.testclient import TestClient
from tests.helpers import PROPERTY_SETTINGS



# Suppress function-scoped fixture warning since we're testing updates to
# the same default agent across iterations (which is the intended behavior)



# Strategies for generating valid editable field values
name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S'), whitelist_characters=' '),
    min_size=1,
    max_size=100
).filter(lambda x: x.strip())  # Ensure non-empty after strip

description_strategy = st.one_of(
    st.none(),
    st.text(min_size=0, max_size=500)
)

system_prompt_strategy = st.one_of(
    st.none(),
    st.text(min_size=0, max_size=2000)
)

# Generate valid skill/mcp IDs (alphanumeric with hyphens)
id_strategy = st.text(
    alphabet=st.characters(whitelist_categories=('L', 'N'), whitelist_characters='-_'),
    min_size=1,
    max_size=50
).filter(lambda x: x and not x.startswith('-') and not x.startswith('_'))

allowed_skills_strategy = st.lists(id_strategy, min_size=0, max_size=5, unique=True)
mcp_ids_strategy = st.lists(id_strategy, min_size=0, max_size=3, unique=True)


class TestDefaultAgentUpdatePreservation:
    """Property 1: Default Agent Update Preservation.

    **Validates: Requirements 2.2**

    For any valid update to the default agent's editable properties
    (name, description, system_prompt, allowed_skills, mcp_ids), the update
    SHALL be applied and the `is_default` flag SHALL remain `true`.
    """

    @given(name=name_strategy)
    @PROPERTY_SETTINGS
    def test_name_update_preserves_is_default(self, client: TestClient, name: str):
        """Updating name preserves is_default flag (for non-system agents).

        **Validates: Requirements 2.2**
        
        Note: The default agent (SwarmAgent) is now also a system agent,
        so name updates are blocked. This test verifies that:
        - For system agents: name updates are rejected with 400
        - The is_default flag is preserved regardless
        """
        # Skip empty names after strip
        assume(name.strip())

        # First check if the default agent is a system agent
        get_response = client.get("/api/agents/default")
        assert get_response.status_code == 200
        is_system = get_response.json().get("is_system_agent", False)

        response = client.put(
            "/api/agents/default",
            json={"name": name}
        )
        
        if is_system:
            # System agents cannot have their name changed
            assert response.status_code == 400
            # Verify the agent still exists and is_default is preserved
            verify_response = client.get("/api/agents/default")
            assert verify_response.status_code == 200
            assert verify_response.json()["is_default"] is True
        else:
            assert response.status_code == 200
            data = response.json()
            # Verify update was applied
            assert data["name"] == name
            # Verify is_default remains true
            assert data["is_default"] is True
            # Verify it's still the default agent
            assert data["id"] == "default"

    @given(description=description_strategy)
    @PROPERTY_SETTINGS
    def test_description_update_preserves_is_default(self, client: TestClient, description: str | None):
        """Updating description preserves is_default flag.

        **Validates: Requirements 2.2**
        """
        response = client.put(
            "/api/agents/default",
            json={"description": description}
        )
        assert response.status_code == 200
        data = response.json()

        # Verify update was applied
        assert data["description"] == description
        # Verify is_default remains true
        assert data["is_default"] is True
        assert data["id"] == "default"

    @given(system_prompt=system_prompt_strategy)
    @PROPERTY_SETTINGS
    def test_system_prompt_update_preserves_is_default(self, client: TestClient, system_prompt: str | None):
        """Updating system_prompt preserves is_default flag.

        **Validates: Requirements 2.2**
        """
        response = client.put(
            "/api/agents/default",
            json={"system_prompt": system_prompt}
        )
        assert response.status_code == 200
        data = response.json()

        # Verify update was applied
        assert data["system_prompt"] == system_prompt
        # Verify is_default remains true
        assert data["is_default"] is True
        assert data["id"] == "default"

    @given(mcp_ids=mcp_ids_strategy)
    @PROPERTY_SETTINGS
    def test_mcp_ids_update_preserves_is_default(self, client: TestClient, mcp_ids: list[str]):
        """Updating mcp_ids preserves is_default flag.

        **Validates: Requirements 2.2**
        """
        import asyncio
        from database import db

        # First check if the default agent is a system agent
        get_response = client.get("/api/agents/default")
        assert get_response.status_code == 200
        is_system = get_response.json().get("is_system_agent", False)

        # For system agents, we must include all system MCPs in the update
        if is_system:
            async def get_system_mcp_ids():
                system_mcps = await db.mcp_servers.list_by_system()
                return [m["id"] for m in system_mcps]
            system_mcp_ids = asyncio.run(get_system_mcp_ids())
            # Merge system MCPs with generated ones (dedup)
            effective_mcp_ids = list(set(system_mcp_ids + mcp_ids))
        else:
            effective_mcp_ids = mcp_ids

        response = client.put(
            "/api/agents/default",
            json={"mcp_ids": effective_mcp_ids}
        )
        assert response.status_code == 200
        data = response.json()

        # Verify update was applied
        assert set(data["mcp_ids"]) == set(effective_mcp_ids)
        # Verify is_default remains true
        assert data["is_default"] is True
        assert data["id"] == "default"

    @given(
        name=name_strategy,
        description=description_strategy,
        system_prompt=system_prompt_strategy,
        mcp_ids=mcp_ids_strategy
    )
    @PROPERTY_SETTINGS
    def test_combined_updates_preserve_is_default(
        self,
        client: TestClient,
        name: str,
        description: str | None,
        system_prompt: str | None,
        mcp_ids: list[str]
    ):
        """Updating multiple editable fields at once preserves is_default flag.

        **Validates: Requirements 2.2**

        This tests the combined case where multiple editable properties
        are updated in a single request.
        
        Note: For system agents, name updates are excluded from the payload
        and system MCPs must be preserved in mcp_ids.
        """
        import asyncio
        from database import db

        # Skip empty names after strip
        assume(name.strip())

        # First check if the default agent is a system agent
        get_response = client.get("/api/agents/default")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        is_system = agent_data.get("is_system_agent", False)

        # For system agents, ensure system MCPs are included
        if is_system:
            async def get_system_mcp_ids():
                system_mcps = await db.mcp_servers.list_by_system()
                return [m["id"] for m in system_mcps]
            system_mcp_ids = asyncio.run(get_system_mcp_ids())
            effective_mcp_ids = list(set(system_mcp_ids + mcp_ids))
        else:
            effective_mcp_ids = mcp_ids

        # Build update payload - exclude name for system agents
        update_payload = {
            "description": description,
            "system_prompt": system_prompt,
            "mcp_ids": effective_mcp_ids,
        }
        
        if not is_system:
            update_payload["name"] = name

        response = client.put(
            "/api/agents/default",
            json=update_payload
        )
        assert response.status_code == 200
        data = response.json()

        # Verify updates were applied (name only if not system agent)
        if not is_system:
            assert data["name"] == name
        assert data["description"] == description
        assert data["system_prompt"] == system_prompt
        assert set(data["mcp_ids"]) == set(effective_mcp_ids)

        # Verify is_default remains true (the core property)
        assert data["is_default"] is True
        assert data["id"] == "default"

    @given(
        name=name_strategy,
        description=description_strategy
    )
    @PROPERTY_SETTINGS
    def test_sequential_updates_preserve_is_default(
        self,
        client: TestClient,
        name: str,
        description: str | None
    ):
        """Sequential updates to different fields preserve is_default flag.

        **Validates: Requirements 2.2**

        This tests that is_default remains true even after multiple
        sequential update operations.
        
        Note: For system agents, name updates are skipped.
        """
        assume(name.strip())

        # First check if the default agent is a system agent
        get_response = client.get("/api/agents/default")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        is_system = agent_data.get("is_system_agent", False)
        original_name = agent_data.get("name")

        # First update: name (only for non-system agents)
        if not is_system:
            response1 = client.put(
                "/api/agents/default",
                json={"name": name}
            )
            assert response1.status_code == 200
            assert response1.json()["is_default"] is True

        # Second update: description
        response2 = client.put(
            "/api/agents/default",
            json={"description": description}
        )
        assert response2.status_code == 200
        data = response2.json()

        # Verify updates are reflected
        if not is_system:
            assert data["name"] == name
        else:
            assert data["name"] == original_name  # Name unchanged for system agents
        assert data["description"] == description
        # Verify is_default still true after sequential updates
        assert data["is_default"] is True
        assert data["id"] == "default"
