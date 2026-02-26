"""Tests for skills API endpoints."""
import pytest
import zipfile
from fastapi.testclient import TestClient
from io import BytesIO


def _make_skill_zip(name: str = "test_skill") -> BytesIO:
    """Create a minimal valid ZIP containing a SKILL.md file."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"{name}/SKILL.md", f"# {name}\nA test skill.")
    buf.seek(0)
    return buf


class TestSkillsList:
    """Tests for GET /api/skills endpoint."""

    def test_list_skills_success(self, client: TestClient):
        """Test listing skills returns 200 and list."""
        response = client.get("/api/skills")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestSystemSkillsList:
    """Tests for GET /api/skills/system endpoint."""

    def test_list_system_skills_success(self, client: TestClient):
        """Test listing system skills returns only system skills."""
        response = client.get("/api/skills/system")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        for skill in data:
            assert skill.get("is_system", False) == True


class TestGetSkill:
    """Tests for GET /api/skills/{skill_id} endpoint."""

    def test_get_skill_not_found(self, client: TestClient, invalid_skill_id: str):
        """Test getting non-existent skill returns 404."""
        response = client.get(f"/api/skills/{invalid_skill_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SKILL_NOT_FOUND"
        assert "suggested_action" in data


class TestUploadSkill:
    """Tests for POST /api/skills/upload endpoint."""

    def test_upload_skill_success(self, client: TestClient):
        """Test uploading skill ZIP returns 201."""
        zip_content = _make_skill_zip("UploadedSkill")

        response = client.post(
            "/api/skills/upload",
            files={"file": ("test_skill.zip", zip_content, "application/zip")},
            data={"name": "UploadedSkill"}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "UploadedSkill"
        assert "id" in data

    def test_upload_skill_without_name(self, client: TestClient):
        """Test uploading skill ZIP uses filename as name."""
        zip_content = _make_skill_zip("my_skill")

        response = client.post(
            "/api/skills/upload",
            files={"file": ("my_skill.zip", zip_content, "application/zip")}
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my_skill"

    def test_upload_skill_invalid_format(self, client: TestClient):
        """Test uploading non-ZIP file returns error."""
        txt_content = BytesIO(b"not a zip file")

        response = client.post(
            "/api/skills/upload",
            files={"file": ("test.txt", txt_content, "text/plain")}
        )
        assert response.status_code in [400, 422]
        data = response.json()
        assert "code" in data


class TestGenerateSkill:
    """Tests for POST /api/skills/generate endpoint."""

    def test_generate_skill_success(self, client: TestClient):
        """Test generating skill returns 201."""
        response = client.post(
            "/api/skills/generate",
            json={"description": "A skill that sends notifications"}
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert "name" in data
        assert data["description"] == "A skill that sends notifications"

    def test_generate_skill_empty_description(self, client: TestClient):
        """Test generating skill with empty description."""
        response = client.post(
            "/api/skills/generate",
            json={"description": ""}
        )
        # Depending on validation, this might succeed or fail
        assert response.status_code in [201, 400, 422]


class TestDeleteSkill:
    """Tests for DELETE /api/skills/{skill_id} endpoint."""

    def test_delete_skill_success(self, client: TestClient):
        """Test deleting user skill returns 204."""
        # First generate a skill
        create_response = client.post(
            "/api/skills/generate",
            json={"description": "Skill to delete"}
        )
        skill_id = create_response.json()["id"]

        # Delete it
        response = client.delete(f"/api/skills/{skill_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_response = client.get(f"/api/skills/{skill_id}")
        assert get_response.status_code == 404

    def test_delete_skill_not_found(self, client: TestClient, invalid_skill_id: str):
        """Test deleting non-existent skill returns 404."""
        response = client.delete(f"/api/skills/{invalid_skill_id}")
        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "SKILL_NOT_FOUND"
