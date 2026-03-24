"""Tests for the Artifact Registry -- filesystem-only typed skill output chaining.

Tests cover:
- L0 behavior (no project -> empty results, no errors)
- Publishing artifacts (creates files, updates manifest)
- Discovery (filters by type, skips superseded)
- Pipeline state management
- Edge cases (corrupt manifest, missing data file, invalid types)
- Superseding artifacts
- Project listing
"""

import json
import os
import shutil
import tempfile

import pytest

from core.artifact_registry import (
    ARTIFACT_TYPES,
    PIPELINE_STATES,
    Artifact,
    ArtifactRegistry,
    ProjectPipelineStatus,
    _slugify,
)


@pytest.fixture
def workspace(tmp_path):
    """Create a workspace with Projects/ directory."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    return tmp_path


@pytest.fixture
def registry(workspace):
    """Create a registry for the test workspace."""
    return ArtifactRegistry(workspace)


@pytest.fixture
def project_with_artifacts(workspace, registry):
    """Create a project and publish some artifacts."""
    project = "TestApp"
    (workspace / "Projects" / project).mkdir()

    # Publish a research artifact
    rid = registry.publish(
        project, "research",
        data={"summary": "Found 3 patterns", "key_findings": ["a", "b"]},
        producer="s_deep-research",
        summary="Payment retry research",
        topic="payment-retry",
    )

    # Publish a design doc
    did = registry.publish(
        project, "design_doc",
        data={"title": "Payment Retry", "decisions": [], "acceptance_criteria": ["AC1"]},
        producer="s_narrative-writing",
        summary="Payment retry design",
        topic="payment-retry",
    )

    return project, rid, did


# ─────────────────────────────────────────────────────────────────────────────
# L0 behavior: no project, no errors
# ─────────────────────────────────────────────────────────────────────────────


class TestL0NoProject:
    """L0: everything returns empty/None when no project is given."""

    def test_discover_none_project(self, registry):
        assert registry.discover(None, "research") == []

    def test_discover_empty_project(self, registry):
        assert registry.discover("", "research") == []

    def test_discover_nonexistent_project(self, registry):
        assert registry.discover("DoesNotExist", "research") == []

    def test_get_pipeline_state_none(self, registry):
        assert registry.get_pipeline_state(None) is None

    def test_get_pipeline_state_nonexistent(self, registry):
        assert registry.get_pipeline_state("DoesNotExist") is None

    def test_get_artifact_nonexistent_project(self, registry):
        assert registry.get_artifact("DoesNotExist", "art_123") is None

    def test_discover_no_types(self, registry):
        assert registry.discover("SomeProject") == []


# ─────────────────────────────────────────────────────────────────────────────
# Publishing
# ─────────────────────────────────────────────────────────────────────────────


class TestPublish:

    def test_publish_creates_artifact_file(self, workspace, registry):
        project = "MyApp"
        (workspace / "Projects" / project).mkdir()

        aid = registry.publish(
            project, "research",
            data={"key": "value"},
            producer="test",
            summary="Test artifact",
            topic="test-topic",
        )

        assert aid.startswith("art_")
        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        assert artifacts_dir.is_dir()

        # Data file exists
        data_files = [f for f in artifacts_dir.iterdir() if f.name.startswith("research-")]
        assert len(data_files) == 1
        content = json.loads(data_files[0].read_text())
        assert content["key"] == "value"

    def test_publish_updates_manifest(self, workspace, registry):
        project = "MyApp"
        (workspace / "Projects" / project).mkdir()

        registry.publish(
            project, "research",
            data={}, producer="test", summary="First",
        )
        registry.publish(
            project, "design_doc",
            data={}, producer="test", summary="Second",
        )

        manifest_path = workspace / "Projects" / project / ".artifacts" / "manifest.json"
        manifest = json.loads(manifest_path.read_text())

        assert len(manifest["artifacts"]) == 2
        assert manifest["artifacts"][0]["type"] == "research"
        assert manifest["artifacts"][1]["type"] == "design_doc"
        assert manifest["project"] == project

    def test_publish_auto_creates_artifacts_dir(self, workspace, registry):
        project = "FreshApp"
        (workspace / "Projects" / project).mkdir()

        registry.publish(
            project, "research", data={}, producer="test", summary="Auto-create test",
        )

        assert (workspace / "Projects" / project / ".artifacts").is_dir()
        assert (workspace / "Projects" / project / ".artifacts" / "manifest.json").is_file()

    def test_publish_invalid_type_raises(self, workspace, registry):
        project = "MyApp"
        (workspace / "Projects" / project).mkdir()

        with pytest.raises(ValueError, match="Unknown artifact type"):
            registry.publish(
                project, "invalid_type",
                data={}, producer="test", summary="Bad type",
            )

    def test_publish_nonexistent_project_raises(self, registry):
        with pytest.raises(FileNotFoundError):
            registry.publish(
                "GhostProject", "research",
                data={}, producer="test", summary="No project",
            )

    def test_publish_with_topic_in_filename(self, workspace, registry):
        project = "MyApp"
        (workspace / "Projects" / project).mkdir()

        registry.publish(
            project, "research",
            data={}, producer="test", summary="Topic test",
            topic="Payment Retry Patterns",
        )

        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        files = [f.name for f in artifacts_dir.iterdir() if f.suffix == ".json" and f.name != "manifest.json"]
        assert len(files) == 1
        assert "payment-retry-patterns" in files[0]


# ─────────────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────────────


class TestDiscover:

    def test_discover_by_type(self, project_with_artifacts, registry):
        project, rid, did = project_with_artifacts

        research = registry.discover(project, "research")
        assert len(research) == 1
        assert research[0].type == "research"
        assert research[0].id == rid

    def test_discover_multiple_types(self, project_with_artifacts, registry):
        project, rid, did = project_with_artifacts

        results = registry.discover(project, "research", "design_doc")
        assert len(results) == 2
        types = {a.type for a in results}
        assert types == {"research", "design_doc"}

    def test_discover_skips_superseded(self, project_with_artifacts, registry):
        project, rid, did = project_with_artifacts

        # Supersede the research artifact
        registry.supersede(project, rid, did)

        research = registry.discover(project, "research")
        assert len(research) == 0

    def test_discover_loads_data(self, project_with_artifacts, registry):
        project, rid, did = project_with_artifacts

        research = registry.discover(project, "research")
        assert research[0].data["summary"] == "Found 3 patterns"
        assert research[0].data["key_findings"] == ["a", "b"]

    def test_discover_type_not_present(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts
        assert registry.discover(project, "test_report") == []


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline state
# ─────────────────────────────────────────────────────────────────────────────


class TestPipelineState:

    def test_get_state_after_publish(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts
        # Default state from first publish
        state = registry.get_pipeline_state(project)
        assert state == "think"

    def test_advance_pipeline(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts

        registry.advance_pipeline(project, "build")
        assert registry.get_pipeline_state(project) == "build"

        registry.advance_pipeline(project, "test")
        assert registry.get_pipeline_state(project) == "test"

    def test_advance_invalid_state_raises(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts

        with pytest.raises(ValueError, match="Unknown pipeline state"):
            registry.advance_pipeline(project, "invalid_state")

    def test_advance_creates_manifest_if_missing(self, workspace, registry):
        project = "EmptyProject"
        (workspace / "Projects" / project).mkdir()

        registry.advance_pipeline(project, "plan")
        assert registry.get_pipeline_state(project) == "plan"

    def test_all_pipeline_states_valid(self, workspace, registry):
        project = "StateTest"
        (workspace / "Projects" / project).mkdir()

        for state in PIPELINE_STATES:
            registry.advance_pipeline(project, state)
            assert registry.get_pipeline_state(project) == state


# ─────────────────────────────────────────────────────────────────────────────
# Get artifact by ID
# ─────────────────────────────────────────────────────────────────────────────


class TestGetArtifact:

    def test_get_existing(self, project_with_artifacts, registry):
        project, rid, _ = project_with_artifacts

        artifact = registry.get_artifact(project, rid)
        assert artifact is not None
        assert artifact.id == rid
        assert artifact.type == "research"
        assert artifact.data["summary"] == "Found 3 patterns"

    def test_get_nonexistent_id(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts
        assert registry.get_artifact(project, "art_nonexistent") is None


# ─────────────────────────────────────────────────────────────────────────────
# Supersede
# ─────────────────────────────────────────────────────────────────────────────


class TestSupersede:

    def test_supersede_marks_old(self, project_with_artifacts, registry):
        project, rid, did = project_with_artifacts

        registry.supersede(project, rid, did)

        old = registry.get_artifact(project, rid)
        assert old is not None
        assert old.superseded_by == did
        assert not old.is_active

    def test_supersede_nonexistent_project_is_noop(self, registry):
        # Should not raise
        registry.supersede("GhostProject", "art_1", "art_2")


# ─────────────────────────────────────────────────────────────────────────────
# Project listing
# ─────────────────────────────────────────────────────────────────────────────


class TestListProjects:

    def test_list_with_artifacts(self, project_with_artifacts, registry):
        project, _, _ = project_with_artifacts

        statuses = registry.list_projects()
        assert len(statuses) >= 1

        status = next(s for s in statuses if s.project == project)
        assert status.artifact_count == 2
        assert status.active_artifact_count == 2
        assert status.pipeline_state == "think"
        assert status.latest_artifact == "design_doc"

    def test_list_project_without_artifacts(self, workspace, registry):
        (workspace / "Projects" / "EmptyApp").mkdir()

        statuses = registry.list_projects()
        empty = next(s for s in statuses if s.project == "EmptyApp")
        assert empty.artifact_count == 0
        assert empty.pipeline_state == "-"

    def test_list_empty_workspace(self, registry):
        # Projects/ exists but is empty
        assert registry.list_projects() == []

    def test_list_no_projects_dir(self, tmp_path):
        reg = ArtifactRegistry(tmp_path)
        assert reg.list_projects() == []


# ─────────────────────────────────────────────────────────────────────────────
# Edge cases
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_corrupt_manifest_returns_none(self, workspace, registry):
        project = "CorruptApp"
        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "manifest.json").write_text("not json", encoding="utf-8")

        assert registry.discover(project, "research") == []
        assert registry.get_pipeline_state(project) is None

    def test_missing_data_file_still_returns_artifact(self, workspace, registry):
        project = "MissingData"
        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        manifest = {
            "project": project,
            "pipeline_state": "think",
            "updated_at": "2026-01-01T00:00:00Z",
            "artifacts": [{
                "id": "art_abc123",
                "type": "research",
                "producer": "test",
                "created": "2026-01-01T00:00:00Z",
                "file": "research-20260101-missing.json",
                "summary": "Data file deleted",
                "superseded_by": None,
            }],
        }
        (artifacts_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        results = registry.discover(project, "research")
        assert len(results) == 1
        assert results[0].data == {}  # Empty data, but artifact still returned

    def test_malformed_artifact_entry_skipped(self, workspace, registry):
        project = "MalformedApp"
        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        artifacts_dir.mkdir(parents=True)

        manifest = {
            "project": project,
            "pipeline_state": "think",
            "updated_at": "2026-01-01T00:00:00Z",
            "artifacts": [
                {"id": "art_good", "type": "research", "file": "r.json",
                 "summary": "Good", "superseded_by": None},
                {"bad": "entry"},  # Missing required fields
            ],
        }
        (artifacts_dir / "manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8",
        )

        results = registry.discover(project, "research")
        assert len(results) == 1  # Bad entry skipped, good one returned


# ─────────────────────────────────────────────────────────────────────────────
# Slugify helper
# ─────────────────────────────────────────────────────────────────────────────


class TestSlugify:

    def test_basic(self):
        assert _slugify("Payment Retry") == "payment-retry"

    def test_special_chars(self):
        assert _slugify("API v2.0 (beta)") == "api-v2-0-beta"

    def test_max_length(self):
        result = _slugify("a" * 100, max_len=10)
        assert len(result) <= 10

    def test_empty(self):
        assert _slugify("") == ""

    def test_unicode(self):
        result = _slugify("DDD调研")
        assert "ddd" in result
