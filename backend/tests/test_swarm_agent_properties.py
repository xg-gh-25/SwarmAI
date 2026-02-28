"""Property-based tests for SwarmAgent (system agent) behavior.

Uses Hypothesis to verify universal properties across all valid inputs.

**Feature: swarm-agent-system-default**
"""
import pytest
import asyncio
from hypothesis import given, strategies as st, settings, assume, HealthCheck
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
                "skill_ids": [],
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


class TestSwarmAgentSystemResourceUnbindProtection:
    """Property 4: System Resource Unbind Protection.

    **Feature: swarm-agent-system-default, Property 4: System Resource Unbind Protection**
    **Validates: Requirements 4.1, 4.2**

    For any update request to SwarmAgent that would remove a system resource
    (skill or MCP) from its bindings, the system SHALL reject the request
    and the system resource SHALL remain bound.
    """

    @pytest.fixture
    def swarm_agent_with_resources(self, client: TestClient) -> dict:
        """Create a SwarmAgent with system and user resources bound."""
        from database import db

        async def setup():
            now = datetime.now().isoformat()

            # Create multiple system skills
            system_skill_ids = []
            for i in range(3):
                skill_data = {
                    "id": f"system-skill-prop-{i}",
                    "name": f"SystemSkillProp{i}",
                    "description": f"System skill {i} for property testing",
                    "version": "1.0.0",
                    "is_system": True,
                    "created_at": now,
                    "updated_at": now,
                }
                await db.skills.put(skill_data)
                system_skill_ids.append(skill_data["id"])

            # Create multiple system MCPs
            system_mcp_ids = []
            for i in range(2):
                mcp_data = {
                    "id": f"system-mcp-prop-{i}",
                    "name": f"SystemMCPProp{i}",
                    "description": f"System MCP {i} for property testing",
                    "connection_type": "stdio",
                    "config": {"command": "test", "args": []},
                    "is_system": True,
                    "created_at": now,
                    "updated_at": now,
                }
                await db.mcp_servers.put(mcp_data)
                system_mcp_ids.append(mcp_data["id"])

            # Create multiple user skills
            user_skill_ids = []
            for i in range(3):
                skill_data = {
                    "id": f"user-skill-prop-{i}",
                    "name": f"UserSkillProp{i}",
                    "description": f"User skill {i} for property testing",
                    "version": "1.0.0",
                    "is_system": False,
                    "created_at": now,
                    "updated_at": now,
                }
                await db.skills.put(skill_data)
                user_skill_ids.append(skill_data["id"])

            # Create multiple user MCPs
            user_mcp_ids = []
            for i in range(2):
                mcp_data = {
                    "id": f"user-mcp-prop-{i}",
                    "name": f"UserMCPProp{i}",
                    "description": f"User MCP {i} for property testing",
                    "connection_type": "stdio",
                    "config": {"command": "test", "args": []},
                    "is_system": False,
                    "created_at": now,
                    "updated_at": now,
                }
                await db.mcp_servers.put(mcp_data)
                user_mcp_ids.append(mcp_data["id"])

            # Create agent with all resources bound
            agent_data = {
                "id": "swarm-agent-unbind-prop-test",
                "name": "SwarmAgent",
                "description": "System agent for unbind property testing",
                "model": "claude-sonnet-4-20250514",
                "permission_mode": "default",
                "is_default": False,
                "is_system_agent": True,
                "skill_ids": system_skill_ids + user_skill_ids,
                "mcp_ids": system_mcp_ids + user_mcp_ids,
                "created_at": now,
                "updated_at": now,
            }
            await db.agents.put(agent_data)

            return {
                "agent_id": agent_data["id"],
                "system_skill_ids": system_skill_ids,
                "system_mcp_ids": system_mcp_ids,
                "user_skill_ids": user_skill_ids,
                "user_mcp_ids": user_mcp_ids,
            }

        return asyncio.get_event_loop().run_until_complete(setup())

    @given(
        excluded_indices=st.lists(
            st.integers(min_value=0, max_value=2),
            min_size=1,
            max_size=3,
            unique=True
        )
    )
    @PROPERTY_SETTINGS
    def test_skill_unbind_rejected_for_any_system_skill_subset_removal(
        self, client: TestClient, swarm_agent_with_resources: dict,
        excluded_indices: list[int]
    ):
        """All updates that remove any system skill are rejected.

        **Validates: Requirements 4.1**
        """
        agent_id = swarm_agent_with_resources["agent_id"]
        system_skill_ids = swarm_agent_with_resources["system_skill_ids"]
        user_skill_ids = swarm_agent_with_resources["user_skill_ids"]

        remaining_system_skills = [
            sid for i, sid in enumerate(system_skill_ids)
            if i not in excluded_indices
        ]
        new_skill_ids = remaining_system_skills + user_skill_ids

        response = client.put(
            f"/api/agents/{agent_id}",
            json={"skill_ids": new_skill_ids}
        )

        assert response.status_code == 400, (
            f"Expected 400 when removing system skills {excluded_indices}, "
            f"got {response.status_code}"
        )

        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system skills" in data["message"].lower()

        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        for system_skill_id in system_skill_ids:
            assert system_skill_id in agent_data["skill_ids"]

    @given(
        excluded_indices=st.lists(
            st.integers(min_value=0, max_value=1),
            min_size=1,
            max_size=2,
            unique=True
        )
    )
    @PROPERTY_SETTINGS
    def test_mcp_unbind_rejected_for_any_system_mcp_subset_removal(
        self, client: TestClient, swarm_agent_with_resources: dict,
        excluded_indices: list[int]
    ):
        """All updates that remove any system MCP are rejected.

        **Validates: Requirements 4.2**
        """
        agent_id = swarm_agent_with_resources["agent_id"]
        system_mcp_ids = swarm_agent_with_resources["system_mcp_ids"]
        user_mcp_ids = swarm_agent_with_resources["user_mcp_ids"]

        remaining_system_mcps = [
            mid for i, mid in enumerate(system_mcp_ids)
            if i not in excluded_indices
        ]
        new_mcp_ids = remaining_system_mcps + user_mcp_ids

        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": new_mcp_ids}
        )

        assert response.status_code == 400, (
            f"Expected 400 when removing system MCPs {excluded_indices}, "
            f"got {response.status_code}"
        )

        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system mcp" in data["message"].lower()

        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        for system_mcp_id in system_mcp_ids:
            assert system_mcp_id in agent_data["mcp_ids"]

    @given(
        skill_excluded_indices=st.lists(
            st.integers(min_value=0, max_value=2),
            min_size=0,
            max_size=3,
            unique=True
        ),
        mcp_excluded_indices=st.lists(
            st.integers(min_value=0, max_value=1),
            min_size=0,
            max_size=2,
            unique=True
        )
    )
    @PROPERTY_SETTINGS
    def test_combined_unbind_rejected_when_any_system_resource_removed(
        self, client: TestClient, swarm_agent_with_resources: dict,
        skill_excluded_indices: list[int], mcp_excluded_indices: list[int]
    ):
        """Updates removing any combination of system resources are rejected.

        **Validates: Requirements 4.1, 4.2**
        """
        assume(len(skill_excluded_indices) > 0 or len(mcp_excluded_indices) > 0)

        agent_id = swarm_agent_with_resources["agent_id"]
        system_skill_ids = swarm_agent_with_resources["system_skill_ids"]
        system_mcp_ids = swarm_agent_with_resources["system_mcp_ids"]
        user_skill_ids = swarm_agent_with_resources["user_skill_ids"]
        user_mcp_ids = swarm_agent_with_resources["user_mcp_ids"]

        remaining_system_skills = [
            sid for i, sid in enumerate(system_skill_ids)
            if i not in skill_excluded_indices
        ]
        remaining_system_mcps = [
            mid for i, mid in enumerate(system_mcp_ids)
            if i not in mcp_excluded_indices
        ]

        update_payload = {}
        if len(skill_excluded_indices) > 0:
            update_payload["skill_ids"] = remaining_system_skills + user_skill_ids
        if len(mcp_excluded_indices) > 0:
            update_payload["mcp_ids"] = remaining_system_mcps + user_mcp_ids

        response = client.put(
            f"/api/agents/{agent_id}",
            json=update_payload
        )

        assert response.status_code == 400, (
            f"Expected 400 when removing system resources, got {response.status_code}"
        )

        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"

        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 200
        agent_data = get_response.json()

        for system_skill_id in system_skill_ids:
            assert system_skill_id in agent_data["skill_ids"]
        for system_mcp_id in system_mcp_ids:
            assert system_mcp_id in agent_data["mcp_ids"]

    @given(
        user_skill_subset=st.lists(
            st.integers(min_value=0, max_value=2),
            min_size=0,
            max_size=3,
            unique=True
        ),
        user_mcp_subset=st.lists(
            st.integers(min_value=0, max_value=1),
            min_size=0,
            max_size=2,
            unique=True
        )
    )
    @PROPERTY_SETTINGS
    def test_user_resource_unbind_allowed_when_all_system_resources_kept(
        self, client: TestClient, swarm_agent_with_resources: dict,
        user_skill_subset: list[int], user_mcp_subset: list[int]
    ):
        """Updates that keep all system resources but modify user resources succeed.

        **Validates: Requirements 4.1, 4.2 (inverse - control test)**
        """
        from database import db

        agent_id = swarm_agent_with_resources["agent_id"]
        user_skill_ids = swarm_agent_with_resources["user_skill_ids"]
        user_mcp_ids = swarm_agent_with_resources["user_mcp_ids"]

        # Query ALL system resources from database (includes both fixture-created
        # and app-startup-created system resources)
        all_system_skills = asyncio.get_event_loop().run_until_complete(
            db.skills.list_by_system()
        )
        all_system_mcps = asyncio.get_event_loop().run_until_complete(
            db.mcp_servers.list_by_system()
        )
        all_system_skill_ids = [s["id"] for s in all_system_skills]
        all_system_mcp_ids = [m["id"] for m in all_system_mcps]

        kept_user_skills = [
            sid for i, sid in enumerate(user_skill_ids)
            if i in user_skill_subset
        ]
        kept_user_mcps = [
            mid for i, mid in enumerate(user_mcp_ids)
            if i in user_mcp_subset
        ]

        response = client.put(
            f"/api/agents/{agent_id}",
            json={
                "skill_ids": all_system_skill_ids + kept_user_skills,
                "mcp_ids": all_system_mcp_ids + kept_user_mcps,
            }
        )

        assert response.status_code == 200, (
            f"Expected 200 when keeping all system resources, got {response.status_code}"
        )

        data = response.json()

        # Verify all system resources remain bound
        for system_skill_id in all_system_skill_ids:
            assert system_skill_id in data["skill_ids"]
        for system_mcp_id in all_system_mcp_ids:
            assert system_mcp_id in data["mcp_ids"]

        # Verify kept user resources are present
        for kept_skill in kept_user_skills:
            assert kept_skill in data["skill_ids"]
        for kept_mcp in kept_user_mcps:
            assert kept_mcp in data["mcp_ids"]

    def test_empty_skill_ids_rejected_when_system_skills_exist(
        self, client: TestClient, swarm_agent_with_resources: dict
    ):
        """Updating with empty skill_ids is rejected when system skills exist.

        **Validates: Requirements 4.1**
        """
        agent_id = swarm_agent_with_resources["agent_id"]
        system_skill_ids = swarm_agent_with_resources["system_skill_ids"]

        response = client.put(
            f"/api/agents/{agent_id}",
            json={"skill_ids": []}
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system skills" in data["message"].lower()

        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        for system_skill_id in system_skill_ids:
            assert system_skill_id in agent_data["skill_ids"]

    def test_empty_mcp_ids_rejected_when_system_mcps_exist(
        self, client: TestClient, swarm_agent_with_resources: dict
    ):
        """Updating with empty mcp_ids is rejected when system MCPs exist.

        **Validates: Requirements 4.2**
        """
        agent_id = swarm_agent_with_resources["agent_id"]
        system_mcp_ids = swarm_agent_with_resources["system_mcp_ids"]

        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": []}
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system mcp" in data["message"].lower()

        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 200
        agent_data = get_response.json()
        for system_mcp_id in system_mcp_ids:
            assert system_mcp_id in agent_data["mcp_ids"]


class TestSwarmAgentInitializationIdempotence:
    """Property 5: Initialization Idempotence.

    **Feature: swarm-agent-system-default, Property 5: Initialization Idempotence**
    **Validates: Requirements 7.4**

    For any number of application restarts (calls to ensure_default_agent()),
    the system SHALL not create duplicate skill or MCP records, and SwarmAgent
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

            # Query all skills and MCPs
            all_skills = await db.skills.list()
            all_mcps = await db.mcp_servers.list()

            return all_skills, all_mcps

        all_skills, all_mcps = asyncio.get_event_loop().run_until_complete(
            run_multiple_initializations()
        )

        # Verify no duplicate skill IDs
        skill_ids = [s["id"] for s in all_skills]
        assert len(skill_ids) == len(set(skill_ids)), (
            f"Duplicate skill IDs found after {num_calls} initializations: "
            f"{[sid for sid in skill_ids if skill_ids.count(sid) > 1]}"
        )

        # Verify no duplicate MCP IDs
        mcp_ids = [m["id"] for m in all_mcps]
        assert len(mcp_ids) == len(set(mcp_ids)), (
            f"Duplicate MCP IDs found after {num_calls} initializations: "
            f"{[mid for mid in mcp_ids if mcp_ids.count(mid) > 1]}"
        )

        # Verify no duplicate skill names (system skills should be unique by name)
        system_skill_names = [s["name"] for s in all_skills if s.get("is_system")]
        assert len(system_skill_names) == len(set(system_skill_names)), (
            f"Duplicate system skill names found: "
            f"{[n for n in system_skill_names if system_skill_names.count(n) > 1]}"
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

            # Get all system resources
            system_skills = await db.skills.list_by_system()
            system_mcps = await db.mcp_servers.list_by_system()

            return swarm_agent, system_skills, system_mcps

        swarm_agent, system_skills, system_mcps = asyncio.get_event_loop().run_until_complete(
            run_and_check_bindings()
        )

        assert swarm_agent is not None, "SwarmAgent should exist after initialization"

        # Verify no duplicate bindings in skill_ids
        agent_skill_ids = swarm_agent.get("skill_ids", [])
        assert len(agent_skill_ids) == len(set(agent_skill_ids)), (
            f"Duplicate skill bindings in SwarmAgent after {num_calls} initializations: "
            f"{[sid for sid in agent_skill_ids if agent_skill_ids.count(sid) > 1]}"
        )

        # Verify no duplicate bindings in mcp_ids
        agent_mcp_ids = swarm_agent.get("mcp_ids", [])
        assert len(agent_mcp_ids) == len(set(agent_mcp_ids)), (
            f"Duplicate MCP bindings in SwarmAgent after {num_calls} initializations: "
            f"{[mid for mid in agent_mcp_ids if agent_mcp_ids.count(mid) > 1]}"
        )

        # Verify all system skills are bound exactly once
        system_skill_ids = {s["id"] for s in system_skills}
        bound_system_skills = system_skill_ids & set(agent_skill_ids)
        assert bound_system_skills == system_skill_ids, (
            f"Not all system skills are bound. Missing: {system_skill_ids - bound_system_skills}"
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
        from database import db

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

