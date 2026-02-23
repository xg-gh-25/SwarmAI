"""Property-based tests for policy enforcement.

**Feature: workspace-refactor, Property 17: Policy enforcement blocks execution**

Uses Hypothesis to verify that for ANY disabled skill or MCP in a workspace,
the task creation endpoint returns 409 Conflict with a policy_violations array
describing which capabilities are missing.

The policy enforcement flow:
1. Task creation request includes required_skills / required_mcps
2. Policy validation checks effective config
3. If any required capability is disabled → 409 with policy_violations

**Validates: Requirements 26.1-26.7, 34.1-34.7**
"""
import pytest
from hypothesis import given, strategies as st, settings, HealthCheck
from datetime import datetime, timezone
from uuid import uuid4

from database import db
from tests.helpers import ensure_default_workspace, create_custom_workspace


PROPERTY_SETTINGS = settings(
    max_examples=2,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

capability_name_strategy = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=3,
    max_size=20,
).filter(lambda x: x.strip())

# How many capabilities to generate per test (1-4)
num_capabilities_strategy = st.integers(min_value=1, max_value=4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_skill(name: str, is_privileged: bool = False) -> str:
    """Insert a global skill and return its ID."""
    now = datetime.now(timezone.utc).isoformat()
    skill_id = str(uuid4())
    await db.skills.put({
        "id": skill_id,
        "name": name,
        "description": f"Test skill {name}",
        "is_privileged": 1 if is_privileged else 0,
        "is_system": 0,
        "created_at": now,
        "updated_at": now,
    })
    return skill_id


async def _create_mcp_server(name: str, is_privileged: bool = False) -> str:
    """Insert a global MCP server and return its ID."""
    now = datetime.now(timezone.utc).isoformat()
    mcp_id = str(uuid4())
    await db.mcp_servers.put({
        "id": mcp_id,
        "name": name,
        "description": f"Test MCP server {name}",
        "connection_type": "stdio",
        "config": "{}",
        "is_privileged": 1 if is_privileged else 0,
        "is_system": 0,
        "is_active": 1,
        "created_at": now,
        "updated_at": now,
    })
    return mcp_id


async def _set_workspace_skill(workspace_id: str, skill_id: str, enabled: bool) -> None:
    """Create or update a workspace_skills junction row."""
    now = datetime.now(timezone.utc).isoformat()
    existing = await db.workspace_skills.get_by_workspace_and_skill(workspace_id, skill_id)
    if existing:
        await db.workspace_skills.update(existing["id"], {"enabled": 1 if enabled else 0})
    else:
        await db.workspace_skills.put({
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "skill_id": skill_id,
            "enabled": 1 if enabled else 0,
            "created_at": now,
            "updated_at": now,
        })


async def _set_workspace_mcp(workspace_id: str, mcp_server_id: str, enabled: bool) -> None:
    """Create or update a workspace_mcps junction row."""
    now = datetime.now(timezone.utc).isoformat()
    existing = await db.workspace_mcps.get_by_workspace_and_mcp(workspace_id, mcp_server_id)
    if existing:
        await db.workspace_mcps.update(existing["id"], {"enabled": 1 if enabled else 0})
    else:
        await db.workspace_mcps.put({
            "id": str(uuid4()),
            "workspace_id": workspace_id,
            "mcp_server_id": mcp_server_id,
            "enabled": 1 if enabled else 0,
            "created_at": now,
            "updated_at": now,
        })


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestPolicyEnforcementBlocksExecution:
    """Property 17: Policy enforcement blocks execution.

    For ANY disabled skill or MCP in a workspace, the task creation endpoint
    SHALL return 409 Conflict with a policy_violations array.

    **Validates: Requirements 26.1-26.7, 34.1-34.7**
    """

    @given(data=st.data())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_disabled_skill_returns_409(self, client, data: st.DataObject):
        """Any disabled skill in required_skills triggers 409.

        **Validates: Requirements 26.1, 34.2, 34.7**
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace()

        # Generate a random skill name and create it
        skill_name = data.draw(capability_name_strategy)
        skill_id = await _create_skill(skill_name)

        # Enable in SwarmWS, disable in custom workspace
        await _set_workspace_skill(swarmws_id, skill_id, True)
        await _set_workspace_skill(custom_ws_id, skill_id, False)

        resp = client.post("/api/tasks", json={
            "agent_id": "default",
            "message": f"Task requiring {skill_name}",
            "workspace_id": custom_ws_id,
            "required_skills": [skill_id],
        })

        assert resp.status_code == 409, (
            f"Expected 409 for disabled skill '{skill_name}', got {resp.status_code}"
        )
        body = resp.json()
        assert body["code"] == "POLICY_VIOLATION"
        assert len(body["policy_violations"]) >= 1

        # The violation must reference our disabled skill
        violation_ids = [v["entity_id"] for v in body["policy_violations"]]
        assert skill_id in violation_ids, (
            f"Disabled skill {skill_id} must appear in policy_violations"
        )

    @given(data=st.data())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_disabled_mcp_returns_409(self, client, data: st.DataObject):
        """Any disabled MCP in required_mcps triggers 409.

        **Validates: Requirements 26.2, 34.2, 34.7**
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace()

        mcp_name = data.draw(capability_name_strategy)
        mcp_id = await _create_mcp_server(mcp_name)

        # Enable in SwarmWS, disable in custom workspace
        await _set_workspace_mcp(swarmws_id, mcp_id, True)
        await _set_workspace_mcp(custom_ws_id, mcp_id, False)

        resp = client.post("/api/tasks", json={
            "agent_id": "default",
            "message": f"Task requiring {mcp_name}",
            "workspace_id": custom_ws_id,
            "required_mcps": [mcp_id],
        })

        assert resp.status_code == 409, (
            f"Expected 409 for disabled MCP '{mcp_name}', got {resp.status_code}"
        )
        body = resp.json()
        assert body["code"] == "POLICY_VIOLATION"
        assert len(body["policy_violations"]) >= 1

        violation_ids = [v["entity_id"] for v in body["policy_violations"]]
        assert mcp_id in violation_ids, (
            f"Disabled MCP {mcp_id} must appear in policy_violations"
        )

    @given(data=st.data())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_409_response_contains_required_fields(self, client, data: st.DataObject):
        """409 response has code, message, policy_violations, suggested_action.

        **Validates: Requirements 26.3, 26.7, 34.3, 34.7**
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace()

        skill_name = data.draw(capability_name_strategy)
        skill_id = await _create_skill(skill_name)

        await _set_workspace_skill(swarmws_id, skill_id, True)
        await _set_workspace_skill(custom_ws_id, skill_id, False)

        resp = client.post("/api/tasks", json={
            "agent_id": "default",
            "message": "Policy check",
            "workspace_id": custom_ws_id,
            "required_skills": [skill_id],
        })

        assert resp.status_code == 409
        body = resp.json()

        # Required top-level fields (Req 34.7)
        assert "code" in body, "Response must include 'code' field"
        assert "message" in body, "Response must include 'message' field"
        assert "policy_violations" in body, "Response must include 'policy_violations' array"
        assert "suggested_action" in body, "Response must include 'suggested_action' field"

        # Each violation must have entity_type, entity_id, message, suggestedAction
        for violation in body["policy_violations"]:
            assert "entity_type" in violation, "Violation must include 'entity_type'"
            assert "entity_id" in violation, "Violation must include 'entity_id'"
            assert "message" in violation, "Violation must include 'message'"
            assert "suggestedAction" in violation, "Violation must include 'suggestedAction'"

    @given(data=st.data())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_mixed_disabled_skills_and_mcps_all_reported(self, client, data: st.DataObject):
        """When both skills and MCPs are disabled, all violations are reported.

        **Validates: Requirements 26.1, 26.2, 26.3, 34.7**
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace()

        # Create and disable a skill
        skill_name = data.draw(capability_name_strategy)
        skill_id = await _create_skill(skill_name)
        await _set_workspace_skill(swarmws_id, skill_id, True)
        await _set_workspace_skill(custom_ws_id, skill_id, False)

        # Create and disable an MCP
        mcp_name = data.draw(capability_name_strategy)
        mcp_id = await _create_mcp_server(mcp_name)
        await _set_workspace_mcp(swarmws_id, mcp_id, True)
        await _set_workspace_mcp(custom_ws_id, mcp_id, False)

        resp = client.post("/api/tasks", json={
            "agent_id": "default",
            "message": "Task needing both",
            "workspace_id": custom_ws_id,
            "required_skills": [skill_id],
            "required_mcps": [mcp_id],
        })

        assert resp.status_code == 409
        body = resp.json()
        violation_ids = {v["entity_id"] for v in body["policy_violations"]}

        assert skill_id in violation_ids, (
            f"Disabled skill must appear in violations"
        )
        assert mcp_id in violation_ids, (
            f"Disabled MCP must appear in violations"
        )
        assert len(body["policy_violations"]) >= 2, (
            "Both skill and MCP violations must be reported"
        )

    @given(data=st.data())
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_enabled_capabilities_do_not_trigger_409(self, client, data: st.DataObject):
        """Enabled capabilities pass policy validation (no 409).

        **Validates: Requirements 26.5, 34.1**
        """
        swarmws_id = await ensure_default_workspace()
        custom_ws_id = await create_custom_workspace()

        skill_name = data.draw(capability_name_strategy)
        skill_id = await _create_skill(skill_name)

        # Enable in BOTH workspaces — should pass policy check
        await _set_workspace_skill(swarmws_id, skill_id, True)
        await _set_workspace_skill(custom_ws_id, skill_id, True)

        resp = client.post("/api/tasks", json={
            "agent_id": "default",
            "message": f"Task with enabled {skill_name}",
            "workspace_id": custom_ws_id,
            "required_skills": [skill_id],
        })

        # Should NOT be 409 — the capability is enabled
        assert resp.status_code != 409, (
            f"Enabled skill '{skill_name}' should not trigger policy violation"
        )
