"""Tests for filesystem-based skills API endpoints.

Tests the skills API defined in ``routers/skills.py``:

- ``GET  /api/skills``                     — List all skills (no content)
- ``POST /api/skills``                     — Create a user skill
- ``POST /api/skills/rescan``              — Invalidate cache, return fresh list
- ``GET  /api/skills/{folder_name}``       — Get single skill with content
- ``PUT  /api/skills/{folder_name}``       — Update a user skill
- ``DELETE /api/skills/{folder_name}``     — Delete a user skill

Testing methodology: unit tests via FastAPI TestClient.

Key invariants:
- GET /api/skills returns 200 with a list
- GET /api/skills/{folder_name} returns 404 for non-existent skills
- POST /api/skills returns 201 for valid skill creation
- DELETE /api/skills/{folder_name} returns 204 on success, 404 for missing
"""
import pytest
from fastapi.testclient import TestClient


class TestSkillsList:
    """Tests for GET /api/skills endpoint."""

    def test_list_skills_success(self, client: TestClient):
        """Test listing skills returns 200 and list."""
        response = client.get("/api/skills")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestSkillsRescan:
    """Tests for POST /api/skills/rescan endpoint."""

    def test_rescan_skills_success(self, client: TestClient):
        """Test rescanning skills returns 200 and list."""
        response = client.post("/api/skills/rescan")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestGetSkill:
    """Tests for GET /api/skills/{folder_name} endpoint."""

    def test_get_skill_not_found(self, client: TestClient):
        """Test getting non-existent skill returns 404."""
        response = client.get("/api/skills/nonexistent-skill-xyz-999")
        assert response.status_code == 404

    def test_get_skill_found(self, client: TestClient):
        """Test getting an existing skill returns 200 with content."""
        # List skills first to find one that exists
        list_resp = client.get("/api/skills")
        skills = list_resp.json()
        if not skills:
            pytest.skip("No skills available to test")

        folder_name = skills[0]["folder_name"]
        response = client.get(f"/api/skills/{folder_name}")
        assert response.status_code == 200
        data = response.json()
        assert data["folder_name"] == folder_name
        assert "content" in data


class TestCreateSkill:
    """Tests for POST /api/skills endpoint."""

    def test_create_skill_missing_fields(self, client: TestClient):
        """Test creating skill without required fields returns error."""
        response = client.post("/api/skills", json={})
        assert response.status_code in [400, 422]


class TestDeleteSkill:
    """Tests for DELETE /api/skills/{folder_name} endpoint."""

    def test_delete_skill_not_found(self, client: TestClient):
        """Test deleting non-existent skill returns 404."""
        response = client.delete("/api/skills/nonexistent-skill-xyz-999")
        assert response.status_code == 404
