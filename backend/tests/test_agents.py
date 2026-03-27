"""Tests for agent API endpoints."""
import pytest
from fastapi.testclient import TestClient


class TestAgentsList:
    """Tests for GET /api/agents endpoint."""

    def test_list_agents_success(self, client: TestClient):
        """Test listing agents returns 200 and list."""
        response = client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)

    def test_list_agents_includes_default(self, client: TestClient):
        """Test that default agent is included in list."""
        response = client.get("/api/agents")
        assert response.status_code == 200
        data = response.json()
        agent_ids = [agent["id"] for agent in data]
        assert "default" in agent_ids


class TestGetDefaultAgent:
    """Tests for GET /api/agents/default endpoint."""

    def test_get_default_agent_success(self, client: TestClient):
        """Test getting default agent returns 200."""
        response = client.get("/api/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "default"
        assert "name" in data

    def test_get_default_agent_has_required_fields(self, client: TestClient):
        """Test default agent response contains all required fields.

        Validates: Requirements 4.1
        """
        response = client.get("/api/agents/default")
        assert response.status_code == 200
        data = response.json()

        # Verify required fields are present
        assert data["id"] == "default"
        assert "name" in data
        assert "description" in data
        assert "model" in data
        assert "created_at" in data
        assert "updated_at" in data


class TestGetAgent:
    """Tests for GET /api/agents/{agent_id} endpoint."""

    def test_get_agent_success(self, client: TestClient):
        """Test getting an existing agent returns 200."""
        # First create an agent
        create_response = client.post(
            "/api/agents",
            json={
                "name": "Test Agent",
                "description": "Test description",
            }
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Now get it
        response = client.get(f"/api/agents/{agent_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == agent_id
        assert data["name"] == "Test Agent"

    def test_get_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test getting non-existent agent returns 404."""
        response = client.get(f"/api/agents/{invalid_agent_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"
        assert "suggested_action" in data


class TestCreateAgent:
    """Tests for POST /api/agents endpoint."""

    def test_create_agent_success(self, client: TestClient, sample_agent_data: dict):
        """Test creating agent returns 201."""
        response = client.post("/api/agents", json=sample_agent_data)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_agent_data["name"]
        assert "id" in data
        assert "created_at" in data

    def test_create_agent_minimal(self, client: TestClient):
        """Test creating agent with minimal data."""
        response = client.post(
            "/api/agents",
            json={"name": "Minimal Agent"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Minimal Agent"

    def test_create_agent_missing_name(self, client: TestClient):
        """Test creating agent without name returns 400."""
        response = client.post("/api/agents", json={})
        assert response.status_code == 400 or response.status_code == 422
        data = response.json()
        assert "code" in data


class TestUpdateAgent:
    """Tests for PUT /api/agents/{agent_id} endpoint."""

    def test_update_agent_success(self, client: TestClient):
        """Test updating agent returns 200."""
        # Create agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Original Name"}
        )
        agent_id = create_response.json()["id"]

        # Update it
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"name": "Updated Name"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"

    def test_update_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test updating non-existent agent returns 404."""
        response = client.put(
            f"/api/agents/{invalid_agent_id}",
            json={"name": "New Name"}
        )
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"

    def test_update_default_agent_name_rejected(self, client: TestClient):
        """Test updating default agent name is rejected (SwarmAgent protection).

        Validates: Requirements 1.2 - SwarmAgent name cannot be changed
        """
        # Attempt to update the default agent's name (should be rejected)
        response = client.put(
            "/api/agents/default",
            json={"name": "Updated SwarmAI", "description": "Updated description"}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system agent" in data["message"].lower()

    def test_update_default_agent_editable_properties(self, client: TestClient):
        """Test updating default agent's editable properties works (except name).

        Validates: Requirements 1.2 - SwarmAgent name is protected, but other properties can be updated
        """
        # Update editable properties (without changing name)
        response = client.put(
            "/api/agents/default",
            json={
                "description": "Custom description",
                "system_prompt": "Custom system prompt",
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "SwarmAgent"  # Name should remain unchanged
        assert data["description"] == "Custom description"
        assert data["system_prompt"] == "Custom system prompt"
        assert data["id"] == "default"


class TestDeleteAgent:
    """Tests for DELETE /api/agents/{agent_id} endpoint."""

    def test_delete_agent_success(self, client: TestClient):
        """Test deleting agent returns 204."""
        # Create agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Agent to Delete"}
        )
        agent_id = create_response.json()["id"]

        # Delete it
        response = client.delete(f"/api/agents/{agent_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 404

    def test_delete_default_agent_forbidden(self, client: TestClient):
        """Test deleting default agent returns error."""
        response = client.delete("/api/agents/default")
        assert response.status_code in [400, 403, 422]
        data = response.json()
        assert "code" in data

    def test_delete_default_agent_returns_400(self, client: TestClient):
        """Test deleting default agent returns 400 with proper error message.

        Validates: Requirements 2.1 - Delete request for default agent SHALL be rejected
        """
        response = client.delete("/api/agents/default")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "default agent" in data["message"].lower()
        assert "suggested_action" in data

    def test_delete_agent_not_found(self, client: TestClient, invalid_agent_id: str):
        """Test deleting non-existent agent returns 404."""
        response = client.delete(f"/api/agents/{invalid_agent_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "AGENT_NOT_FOUND"


class TestSwarmAgentProtections:
    """Tests for SwarmAgent (system agent) API protections.

    **Validates: Requirements 1.2, 1.3, 4.1, 4.2, 5.1, 5.2**
    """

    @pytest.fixture
    def swarm_agent_id(self, client: TestClient) -> str:
        """Create a SwarmAgent (system agent) for testing.

        Returns the agent ID of the created system agent.
        """
        # Create a system agent with is_system_agent=True
        # We need to insert directly into the database since the API doesn't allow creating system agents
        import asyncio
        from database import db
        from datetime import datetime

        async def create_system_agent():
            now = datetime.now().isoformat()
            agent_data = {
                "id": "swarm-agent-test",
                "name": "SwarmAgent",
                "description": "System agent for testing",
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

        return asyncio.run(create_system_agent())

    @pytest.fixture
    def user_skill_id(self, client: TestClient) -> str:
        """Create a user skill for testing.

        Returns the skill folder name of the created user skill.
        """
        import os
        from pathlib import Path

        # Use a skill folder name that exists in the filesystem
        # For testing, we'll use a known user skill folder name
        skill_folder = "user-test-skill"
        
        # Create the skill directory if it doesn't exist (for testing)
        skills_dir = Path.home() / ".swarm-ai" / "skills"
        skill_path = skills_dir / skill_folder
        skill_path.mkdir(parents=True, exist_ok=True)
        
        # Create a minimal SKILL.md file
        skill_md = skill_path / "SKILL.md"
        skill_md.write_text("# User Test Skill\n\nA user skill for testing.")
        
        return skill_folder

    @pytest.fixture
    def user_mcp_id(self, client: TestClient) -> str:
        """Create a user MCP server for testing.

        Returns the MCP ID of the created user MCP server.
        """
        import asyncio
        from database import db
        from datetime import datetime

        async def create_user_mcp():
            now = datetime.now().isoformat()
            mcp_data = {
                "id": "user-mcp-test",
                "name": "UserTestMCP",
                "description": "A user MCP server for testing",
                "connection_type": "stdio",
                "config": {"command": "test", "args": []},
                "is_system": False,
                "created_at": now,
                "updated_at": now,
            }
            await db.mcp_servers.put(mcp_data)
            return mcp_data["id"]

        return asyncio.run(create_user_mcp())

    @pytest.fixture
    def swarm_agent_with_system_resources(self, client: TestClient) -> dict:
        """Create a SwarmAgent with system skill and MCP bound.

        Returns a dict with agent_id, system_skill_folder, and system_mcp_id.
        The system resources are created first, then the agent is created with them bound.
        """
        import asyncio
        from database import db
        from datetime import datetime

        async def setup():
            now = datetime.now().isoformat()

            # Use a known system skill folder name (these exist in the filesystem)
            system_skill_folder = "code-simplifier"  # A known system skill

            # Create system MCP
            mcp_data = {
                "id": "system-mcp-for-agent",
                "name": "SystemMCPForAgent",
                "description": "A system MCP server for testing",
                "connection_type": "stdio",
                "config": {"command": "test", "args": []},
                "is_system": True,
                "created_at": now,
                "updated_at": now,
            }
            await db.mcp_servers.put(mcp_data)

            # Create agent with system resources bound
            agent_data = {
                "id": "swarm-agent-resources-test",
                "name": "SwarmAgent",
                "description": "System agent with resources for testing",
                "model": "claude-sonnet-4-20250514",
                "permission_mode": "default",
                "is_default": False,
                "is_system_agent": True,
                "allowed_skills": [system_skill_folder],
                "mcp_ids": [mcp_data["id"]],
                "created_at": now,
                "updated_at": now,
            }
            await db.agents.put(agent_data)

            return {
                "agent_id": agent_data["id"],
                "system_skill_folder": system_skill_folder,
                "system_mcp_id": mcp_data["id"],
            }

        return asyncio.run(setup())

    # -------------------------------------------------------------------------
    # Name Update Protection Tests
    # -------------------------------------------------------------------------

    def test_swarm_agent_name_update_rejected(self, client: TestClient, swarm_agent_id: str):
        """Test that changing SwarmAgent name is rejected.

        **Validates: Requirements 1.2**
        """
        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={"name": "CustomAgentName"}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system agent" in data["message"].lower()

    def test_swarm_agent_same_name_update_allowed(self, client: TestClient, swarm_agent_id: str):
        """Test that updating SwarmAgent with same name is allowed.

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

    def test_swarm_agent_other_properties_update_allowed(self, client: TestClient, swarm_agent_id: str):
        """Test that updating SwarmAgent's other properties is allowed.

        **Validates: Requirements 1.2**
        """
        response = client.put(
            f"/api/agents/{swarm_agent_id}",
            json={
                "description": "New description",
                "system_prompt": "New system prompt",
                "max_turns": 20,
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "New description"
        assert data["system_prompt"] == "New system prompt"
        assert data["max_turns"] == 20

    # -------------------------------------------------------------------------
    # System Skill Unbind Protection Tests
    # -------------------------------------------------------------------------

    def test_system_skill_unbind_rejected(
        self, client: TestClient, swarm_agent_with_system_resources: dict
    ):
        """Test that unbinding system skill from SwarmAgent is rejected.

        **Validates: Requirements 4.1**
        """
        agent_id = swarm_agent_with_system_resources["agent_id"]
        # Try to update with empty allowed_skills (removing the system skill)
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": []}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system skills" in data["message"].lower()

    def test_system_skill_unbind_partial_rejected(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_skill_id: str
    ):
        """Test that partially unbinding system skill is rejected.

        **Validates: Requirements 4.1**
        """
        import asyncio
        from core.skill_manager import skill_manager

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL built-in (system) skills from SkillManager
        async def get_all_builtin_skills():
            cache = await skill_manager.get_cache()
            return [folder for folder, info in cache.items() if info.source_tier == "built-in"]

        all_builtin_skill_folders = asyncio.run(get_all_builtin_skills())

        # First add a user skill while keeping ALL built-in skills
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": all_builtin_skill_folders + [user_skill_id]}
        )
        assert response.status_code == 200

        # Now try to remove one built-in skill (keeping user skill and other built-in skills)
        # This should fail because we're removing a built-in skill
        remaining_allowed_skills = all_builtin_skill_folders[1:] + [user_skill_id]  # Remove first built-in skill
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": remaining_allowed_skills}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system skills" in data["message"].lower()

    # -------------------------------------------------------------------------
    # System MCP Unbind Protection Tests
    # -------------------------------------------------------------------------

    def test_system_mcp_unbind_rejected(
        self, client: TestClient, swarm_agent_with_system_resources: dict
    ):
        """Test that unbinding system MCP from SwarmAgent is rejected.

        **Validates: Requirements 4.2**
        """
        agent_id = swarm_agent_with_system_resources["agent_id"]
        # Try to update with empty mcp_ids (removing the system MCP)
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": []}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system mcp" in data["message"].lower()

    def test_system_mcp_unbind_partial_rejected(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_mcp_id: str
    ):
        """Test that partially unbinding system MCP is rejected.

        **Validates: Requirements 4.2**
        """
        import asyncio
        from database import db

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL system MCPs from database (includes those registered at app init)
        async def get_all_system_mcps():
            return await db.mcp_servers.list_by_system()

        all_system_mcps = asyncio.run(get_all_system_mcps())
        all_system_mcp_ids = [m["id"] for m in all_system_mcps]

        # First add a user MCP while keeping ALL system MCPs
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": all_system_mcp_ids + [user_mcp_id]}
        )
        assert response.status_code == 200

        # Now try to remove only one system MCP (keeping user MCP + remaining system MCPs)
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": [user_mcp_id]}
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system mcp" in data["message"].lower()

    # -------------------------------------------------------------------------
    # User Skill Bind/Unbind Success Tests
    # -------------------------------------------------------------------------

    def test_user_skill_bind_to_swarm_agent_success(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_skill_id: str
    ):
        """Test that binding user skill to SwarmAgent succeeds.

        **Validates: Requirements 5.1**
        """
        import asyncio
        from core.skill_manager import skill_manager

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL built-in (system) skills from SkillManager
        async def get_all_builtin_skills():
            cache = await skill_manager.get_cache()
            return [folder for folder, info in cache.items() if info.source_tier == "built-in"]

        all_builtin_skill_folders = asyncio.run(get_all_builtin_skills())

        # Add user skill while keeping ALL built-in skills
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": all_builtin_skill_folders + [user_skill_id]}
        )
        assert response.status_code == 200
        data = response.json()
        # Verify all built-in skills are still bound
        for skill_folder in all_builtin_skill_folders:
            assert skill_folder in data["allowed_skills"]
        # Verify user skill is now bound
        assert user_skill_id in data["allowed_skills"]

    def test_user_skill_unbind_from_swarm_agent_success(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_skill_id: str
    ):
        """Test that unbinding user skill from SwarmAgent succeeds.

        **Validates: Requirements 5.2**
        """
        import asyncio
        from core.skill_manager import skill_manager

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL built-in (system) skills from SkillManager
        async def get_all_builtin_skills():
            cache = await skill_manager.get_cache()
            return [folder for folder, info in cache.items() if info.source_tier == "built-in"]

        all_builtin_skill_folders = asyncio.run(get_all_builtin_skills())

        # First add user skill while keeping ALL built-in skills
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": all_builtin_skill_folders + [user_skill_id]}
        )
        assert response.status_code == 200

        # Now remove only the user skill (keeping ALL built-in skills)
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"allowed_skills": all_builtin_skill_folders}
        )
        assert response.status_code == 200
        data = response.json()
        # Verify all built-in skills are still bound
        for skill_folder in all_builtin_skill_folders:
            assert skill_folder in data["allowed_skills"]
        # Verify user skill is no longer bound
        assert user_skill_id not in data["allowed_skills"]

    # -------------------------------------------------------------------------
    # User MCP Bind/Unbind Success Tests
    # -------------------------------------------------------------------------

    def test_user_mcp_bind_to_swarm_agent_success(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_mcp_id: str
    ):
        """Test that binding user MCP to SwarmAgent succeeds.

        **Validates: Requirements 5.4**
        """
        import asyncio
        from database import db

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL system MCPs from database (includes those registered at app init)
        async def get_all_system_mcps():
            return await db.mcp_servers.list_by_system()

        all_system_mcps = asyncio.run(get_all_system_mcps())
        all_system_mcp_ids = [m["id"] for m in all_system_mcps]

        # Add user MCP while keeping ALL system MCPs
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": all_system_mcp_ids + [user_mcp_id]}
        )
        assert response.status_code == 200
        data = response.json()
        for mcp_id in all_system_mcp_ids:
            assert mcp_id in data["mcp_ids"]
        assert user_mcp_id in data["mcp_ids"]

    def test_user_mcp_unbind_from_swarm_agent_success(
        self, client: TestClient, swarm_agent_with_system_resources: dict,
        user_mcp_id: str
    ):
        """Test that unbinding user MCP from SwarmAgent succeeds.

        **Validates: Requirements 5.5**
        """
        import asyncio
        from database import db

        agent_id = swarm_agent_with_system_resources["agent_id"]

        # Get ALL system MCPs from database (includes those registered at app init)
        async def get_all_system_mcps():
            return await db.mcp_servers.list_by_system()

        all_system_mcps = asyncio.run(get_all_system_mcps())
        all_system_mcp_ids = [m["id"] for m in all_system_mcps]

        # First add user MCP while keeping ALL system MCPs
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": all_system_mcp_ids + [user_mcp_id]}
        )
        assert response.status_code == 200

        # Now remove only the user MCP (keeping ALL system MCPs)
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"mcp_ids": all_system_mcp_ids}
        )
        assert response.status_code == 200
        data = response.json()
        for mcp_id in all_system_mcp_ids:
            assert mcp_id in data["mcp_ids"]
        assert user_mcp_id not in data["mcp_ids"]

    # -------------------------------------------------------------------------
    # Delete Protection Tests
    # -------------------------------------------------------------------------

    def test_swarm_agent_delete_rejected(self, client: TestClient, swarm_agent_id: str):
        """Test that deleting SwarmAgent is rejected.

        **Validates: Requirements 1.3**
        """
        response = client.delete(f"/api/agents/{swarm_agent_id}")
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "VALIDATION_FAILED"
        assert "system agent" in data["message"].lower()

    def test_swarm_agent_still_exists_after_delete_attempt(
        self, client: TestClient, swarm_agent_id: str
    ):
        """Test that SwarmAgent still exists after failed delete attempt.

        **Validates: Requirements 1.3**
        """
        # Attempt to delete
        delete_response = client.delete(f"/api/agents/{swarm_agent_id}")
        assert delete_response.status_code == 400

        # Verify agent still exists
        get_response = client.get(f"/api/agents/{swarm_agent_id}")
        assert get_response.status_code == 200
        data = get_response.json()
        assert data["id"] == swarm_agent_id
        assert data["name"] == "SwarmAgent"

    # -------------------------------------------------------------------------
    # Non-System Agent Tests (Control Group)
    # -------------------------------------------------------------------------

    def test_regular_agent_name_update_allowed(self, client: TestClient):
        """Test that regular (non-system) agent name can be changed.

        Control test to ensure protection only applies to system agents.
        """
        # Create a regular agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Regular Agent"}
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Update the name
        response = client.put(
            f"/api/agents/{agent_id}",
            json={"name": "New Name"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"

    def test_regular_agent_delete_allowed(self, client: TestClient):
        """Test that regular (non-system) agent can be deleted.

        Control test to ensure protection only applies to system agents.
        """
        # Create a regular agent
        create_response = client.post(
            "/api/agents",
            json={"name": "Agent to Delete"}
        )
        assert create_response.status_code == 201
        agent_id = create_response.json()["id"]

        # Delete it
        response = client.delete(f"/api/agents/{agent_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/agents/{agent_id}")
        assert get_response.status_code == 404
