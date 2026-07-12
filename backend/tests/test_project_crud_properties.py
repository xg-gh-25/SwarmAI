"""Property-based tests for project CRUD operations.

Tests the ``SwarmWorkspaceManager`` project lifecycle methods using
Hypothesis to verify universal correctness properties across randomised
valid inputs.

Key properties verified:

- ``test_project_creation_produces_complete_scaffold``
    — Property 1: Every valid project name produces a complete directory
      scaffold with all template items and correct ``.project.json`` defaults.
- ``test_project_crud_round_trip``
    — Property 5: For any set of created projects, create→get(id) returns
      matching metadata, create→list includes it, get_by_name returns the
      same, and delete→get raises ValueError.

**Feature: swarmws-projects**
"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from hypothesis import given, settings, HealthCheck, strategies as st

from core.swarm_workspace_manager import SwarmWorkspaceManager
from core.project_schema_migrations import CURRENT_SCHEMA_VERSION
from tests.helpers import PROPERTY_SETTINGS



# ---------------------------------------------------------------------------
# Hypothesis settings
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

valid_project_names = st.from_regex(
    r"[a-zA-Z0-9][a-zA-Z0-9 _.\-]{0,99}", fullmatch=True
).filter(lambda n: n.strip() == n)

# ---------------------------------------------------------------------------
# Expected template items
# ---------------------------------------------------------------------------

EXPECTED_FILES = {".project.json"}
EXPECTED_DIRS: set[str] = set()

REQUIRED_METADATA_FIELDS = {
    "id", "name", "description", "created_at", "updated_at",
    "status", "tags", "priority", "schema_version", "version",
    "update_history",
}


# ---------------------------------------------------------------------------
# Property Tests
# ---------------------------------------------------------------------------


class TestProjectCreationScaffold:
    """Property 1: Project creation produces complete metadata and template.

    # Feature: swarmws-projects, Property 1: Project creation produces complete metadata and template

    *For any* valid project name, ``create_project()`` should produce a
    directory containing all Standard Project Template items and a
    ``.project.json`` with all required fields, ``version=1``,
    ``schema_version="1.0.0"``, and exactly one ``created`` history entry.

    **Validates: Requirements 4.2, 4.3, 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3, 32.1**
    """

    @given(name=valid_project_names)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_creation_produces_complete_scaffold(
        self,
        tmp_path: Path,
        name: str,
    ):
        """Every valid project name produces a complete scaffold with correct defaults.

        **Validates: Requirements 4.2, 4.3, 5.1, 5.5, 18.1, 27.1, 27.2, 27.3, 31.3, 32.1**
        """
        # Use a unique workspace dir per Hypothesis example to avoid collisions
        workspace_dir = tmp_path / str(uuid4())
        workspace_dir.mkdir(parents=True, exist_ok=True)
        projects_dir = workspace_dir / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        manager = SwarmWorkspaceManager()
        result = await manager.create_project(
            project_name=name,
            workspace_path=str(workspace_dir),
        )

        project_dir = projects_dir / name

        # --- Verify directory exists ---
        assert project_dir.exists(), f"Project directory '{name}' was not created"
        assert project_dir.is_dir(), f"Project path '{name}' is not a directory"

        # --- Verify all expected files exist ---
        for expected_file in EXPECTED_FILES:
            file_path = project_dir / expected_file
            assert file_path.exists(), (
                f"Expected file '{expected_file}' missing from project scaffold"
            )
            assert file_path.is_file(), (
                f"Expected '{expected_file}' to be a file, not a directory"
            )

        # --- Verify all expected directories exist ---
        for expected_dir in EXPECTED_DIRS:
            dir_path = project_dir / expected_dir
            assert dir_path.exists(), (
                f"Expected directory '{expected_dir}' missing from project scaffold"
            )
            assert dir_path.is_dir(), (
                f"Expected '{expected_dir}' to be a directory, not a file"
            )

        # --- Verify .project.json content ---
        metadata_path = project_dir / ".project.json"
        raw = metadata_path.read_text()
        metadata = json.loads(raw)

        # All required fields present
        for field in REQUIRED_METADATA_FIELDS:
            assert field in metadata, (
                f"Required field '{field}' missing from .project.json"
            )

        # Field value checks
        assert metadata["name"] == name
        assert metadata["description"] == ""
        assert metadata["status"] == "active"
        assert metadata["tags"] == []
        assert metadata["priority"] is None
        assert metadata["version"] == 1
        assert metadata["schema_version"] == CURRENT_SCHEMA_VERSION
        # DDD six-section spec version stamp (§3.7 anti-drift): a provisioned project
        # must record which DDD spec version it was scaffolded under, so propagated
        # DDDs are version-traceable. Sourced from the DDD_SPEC_VERSION module constant.
        from core.swarm_workspace_manager import DDD_SPEC_VERSION
        assert metadata["ddd_spec_version"] == DDD_SPEC_VERSION

        # UUID is a non-empty string
        assert isinstance(metadata["id"], str) and len(metadata["id"]) > 0

        # Timestamps are non-empty strings
        assert isinstance(metadata["created_at"], str) and len(metadata["created_at"]) > 0
        assert isinstance(metadata["updated_at"], str) and len(metadata["updated_at"]) > 0

        # --- Verify update_history ---
        history = metadata["update_history"]
        assert isinstance(history, list)
        assert len(history) == 1, (
            f"Expected exactly 1 history entry, got {len(history)}"
        )

        entry = history[0]
        assert entry["version"] == 1
        assert entry["action"] == "created"
        assert entry["changes"] == {}
        assert entry["source"] == "user"
        assert isinstance(entry["timestamp"], str) and len(entry["timestamp"]) > 0

        # --- Verify return value matches file content ---
        assert result["id"] == metadata["id"]
        assert result["name"] == metadata["name"]
        assert result["version"] == metadata["version"]
        assert result["schema_version"] == metadata["schema_version"]



class TestProjectCRUDRoundTrip:
    """Property 5: Project CRUD Round-Trip.

    # Feature: swarmws-projects, Property 5: Project create-then-read round trip

    *For any* valid project name, the full CRUD lifecycle should be
    consistent: create→get(id) returns matching metadata,
    create→list includes the project, get_by_name returns the same
    metadata, and delete→get(id) raises ValueError.

    **Validates: Requirements 4.6, 18.3, 18.4, 18.6, 18.9, 31.6**
    """

    @given(name=valid_project_names)
    @PROPERTY_SETTINGS
    @pytest.mark.asyncio
    async def test_project_crud_round_trip(
        self,
        tmp_path: Path,
        name: str,
    ):
        """Create→get→list→get_by_name→delete→get round trip is consistent.

        **Validates: Requirements 4.6, 18.3, 18.4, 18.6, 18.9, 31.6**
        """
        # Use a unique workspace dir per Hypothesis example to avoid collisions
        workspace_dir = tmp_path / str(uuid4())
        workspace_dir.mkdir(parents=True, exist_ok=True)
        projects_dir = workspace_dir / "Projects"
        projects_dir.mkdir(parents=True, exist_ok=True)

        ws = str(workspace_dir)
        manager = SwarmWorkspaceManager()

        # ── CREATE ───────────────────────────────────────────────────
        created = await manager.create_project(
            project_name=name,
            workspace_path=ws,
        )
        project_id = created["id"]

        # ── GET by id ────────────────────────────────────────────────
        fetched = await manager.get_project(project_id, workspace_path=ws)

        assert fetched["id"] == project_id
        assert fetched["name"] == name
        assert fetched["version"] == created["version"]
        assert fetched["schema_version"] == created["schema_version"]
        assert fetched["status"] == created["status"]
        assert fetched["tags"] == created["tags"]
        assert fetched["description"] == created["description"]
        assert fetched["priority"] == created["priority"]
        assert fetched["created_at"] == created["created_at"]

        # ── LIST includes the project ────────────────────────────────
        all_projects = await manager.list_projects(workspace_path=ws)
        listed_ids = [p["id"] for p in all_projects]
        assert project_id in listed_ids, (
            f"Created project {project_id} not found in list_projects"
        )

        # Find the matching entry and verify metadata consistency
        listed = next(p for p in all_projects if p["id"] == project_id)
        assert listed["name"] == name
        assert listed["version"] == created["version"]
        assert listed["schema_version"] == created["schema_version"]

        # ── GET by name ──────────────────────────────────────────────
        by_name = await manager.get_project_by_name(name, workspace_path=ws)

        assert by_name["id"] == project_id
        assert by_name["name"] == name
        assert by_name["version"] == created["version"]
        assert by_name["created_at"] == created["created_at"]

        # ── DELETE ───────────────────────────────────────────────────
        deleted = await manager.delete_project(project_id, workspace_path=ws)
        assert deleted is True

        # ── GET after delete raises ValueError ───────────────────────
        with pytest.raises(ValueError):
            await manager.get_project(project_id, workspace_path=ws)


# ---------------------------------------------------------------------------
# Six-section canonical DDD structure (DDD-agent-brain spec §3.6) — physical
# scaffold. Fixed project name (NOT Hypothesis) to keep the path-assertion
# oracle unambiguous (Gate-0 E: a generated name equal to a section dir would
# make "is there a skills/ dir" ambiguous).
# ---------------------------------------------------------------------------

# ① IDENTITY manifests + ③④⑥ section skeleton that provisioning must scaffold.
# ② (4 docs + Knowledge/) is covered by the scaffold test above; ⑤ bindings is
# provisioned by BIND, not CREATE — deliberately absent here.
EXPECTED_SIX_SECTION_FILES = {
    "aim.json",                       # ①
    "AGENTS.md",                      # ①
    ".crux_template.md",              # ①
    "gates/README.md",                # ③
    "gates/context/includes/README.md",  # ③ denylist-data home
    "skills/README.md",               # ④
    "agents/README.md",               # ④
    "agent-sops/README.md",           # ④
    "REFRESHER.md",                   # ⑥ (marker: activates on BIND, no-op for no-repo)
}


class TestSixSectionScaffold:
    """The canonical six-section DDD structure is physically materialized at
    CREATE (option A, XG decision 2026-07-12): the SKELETON (dirs + purpose
    READMEs) is concrete so SwarmAI-maintenance follows the standard and AIM
    export is low-variance. Section CONTENT still accretes."""

    @pytest.mark.asyncio
    async def test_create_scaffolds_six_section_skeleton(self, tmp_path: Path):
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="SixSecProj", workspace_path=str(ws))
        pdir = ws / "Projects" / "SixSecProj"

        for rel in EXPECTED_SIX_SECTION_FILES:
            p = pdir / rel
            assert p.exists(), f"six-section scaffold missing: {rel}"
            assert p.is_file(), f"{rel} should be a file"
            body = p.read_text(encoding="utf-8")
            assert body.strip(), f"{rel} must not be empty (legible standard, not cargo-cult)"

        # Every scaffolded README must state its purpose (AC5: not a bare empty dir).
        for readme_rel in [r for r in EXPECTED_SIX_SECTION_FILES if r.endswith("README.md")]:
            body = (pdir / readme_rel).read_text(encoding="utf-8").lower()
            assert ("belongs" in body or "accrete" in body or "purpose" in body), (
                f"{readme_rel} must explain the section's purpose / what belongs / accretion rule"
            )

        # {project_name} templating actually applied (not a literal placeholder).
        agents_md = (pdir / "AGENTS.md").read_text(encoding="utf-8")
        assert "{project_name}" not in agents_md, "template placeholder left unfilled"
        assert "SixSecProj" in agents_md, "project name not templated into AGENTS.md"

        # ① aim.json must be VALID JSON after templating (Gate-2 CRITICAL: a
        # {{}}-vs-.replace brace mismatch shipped a manifest with literal braces
        # that failed json.loads). Parse it + assert the templated name landed.
        aim = json.loads((pdir / "aim.json").read_text(encoding="utf-8"))
        assert aim["name"] == "SixSecProj"
        assert aim["ddd_spec_version"] == "1.0"

    @pytest.mark.asyncio
    async def test_provision_is_idempotent(self, tmp_path: Path):
        """Re-provisioning writes nothing new (AC3) — never clobbers hand-authored content."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="IdemProj", workspace_path=str(ws))
        # second provision pass over the now-populated project → zero new files
        created_second = await manager.provision_project_ddd("IdemProj", workspace_path=str(ws))
        assert created_second == [], f"re-provision must be a no-op, got: {created_second}"

    @pytest.mark.asyncio
    async def test_refresher_marks_bind_activation(self, tmp_path: Path):
        """⑥ REFRESHER.md is a shape-neutral marker: it must state it activates on
        BIND and is a no-op for a no-repo project (Gate-0/Gate-1 F reconciliation —
        the skeleton is concrete but the semantics honor GOVERN-a-physical-repo)."""
        ws = tmp_path / "ws"
        (ws / "Projects").mkdir(parents=True)
        manager = SwarmWorkspaceManager()
        await manager.create_project(project_name="RefProj", workspace_path=str(ws))
        body = (ws / "Projects" / "RefProj" / "REFRESHER.md").read_text(encoding="utf-8").lower()
        assert "bind" in body, "REFRESHER must explain it activates when a repo is bound (⑤)"
        assert ("no-op" in body or "no repo" in body or "not-yet-built" in body), (
            "REFRESHER must state it's a no-op without a bound repo"
        )
