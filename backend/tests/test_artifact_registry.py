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

import pytest

from core.artifact_registry import (
    PIPELINE_STATES,
    ArtifactRegistry,
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


class TestManifestConcurrencyLock:
    """Regression: publish/supersede/advance_pipeline serialize their
    manifest read-modify-write under an exclusive cross-process flock, so a
    concurrent publisher cannot drop another's just-appended entry (the
    lost-update race that orphaned data files from the manifest).

    These use the OS-lock-hold shape (see test_eval_crud): hold the sidecar
    lock from the main thread, run the mutation in a background thread, and
    assert it BLOCKS until release. This goes RED if the flock is reverted
    (the mutation would complete immediately, not block).
    """

    def test_publish_blocks_while_lock_held(self, workspace, registry):
        import threading
        from utils.file_lock import flock_exclusive, flock_unlock

        project = "LockApp"
        (workspace / "Projects" / project).mkdir()
        # First publish creates .artifacts/ + manifest so the sidecar exists.
        registry.publish(
            project, "research", data={"x": 1},
            producer="p", summary="seed",
        )

        lock_path = registry._lock_path(project)
        holder_fd = open(lock_path, "w")
        flock_exclusive(holder_fd)  # main thread holds the exclusive lock

        done = threading.Event()

        def run_publish():
            registry.publish(
                project, "review", data={"y": 2},
                producer="p", summary="blocked-until-release",
            )
            done.set()

        t = threading.Thread(target=run_publish, daemon=True)
        t.start()

        # While we hold the lock, publish's manifest append must NOT complete.
        assert not done.wait(timeout=0.8), (
            "publish completed while another process held the exclusive "
            "manifest lock — it is not acquiring an OS-level lock (race open)"
        )

        flock_unlock(holder_fd)
        holder_fd.close()
        assert done.wait(timeout=5.0), "publish did not finish after release (deadlock?)"

        # Both entries survive in the manifest (no lost update).
        manifest = registry._read_manifest(project)
        summaries = {e["summary"] for e in manifest["artifacts"]}
        assert {"seed", "blocked-until-release"} <= summaries

    def test_interleaved_publishes_all_survive(self, workspace, registry):
        """A serialized burst of publishes all land — none is lost."""
        project = "BurstApp"
        (workspace / "Projects" / project).mkdir()
        ids = [
            registry.publish(
                project, "research", data={"n": i},
                producer="p", summary=f"entry-{i}",
            )
            for i in range(10)
        ]
        manifest = registry._read_manifest(project)
        got = {e["id"] for e in manifest["artifacts"]}
        assert set(ids) == got
        assert len(manifest["artifacts"]) == 10

    def test_lock_released_on_mutator_exception(self, workspace, registry):
        """If the mutation raises, the lock is released (finally) so the next
        acquire does not deadlock."""
        from utils.file_lock import flock_exclusive_nb, flock_unlock

        project = "ErrApp"
        (workspace / "Projects" / project).mkdir()
        registry.publish(
            project, "research", data={"x": 1}, producer="p", summary="seed",
        )

        def boom(_manifest):
            raise ValueError("mutator failed")

        with pytest.raises(ValueError, match="mutator failed"):
            registry._mutate_manifest(project, boom)

        # Lock must be free now — a non-blocking acquire succeeds.
        fd = open(registry._lock_path(project), "w")
        try:
            flock_exclusive_nb(fd)  # raises if still held
            flock_unlock(fd)
        finally:
            fd.close()

    def test_supersede_and_advance_still_work_under_lock(self, workspace, registry):
        """Locked path preserves supersede + advance_pipeline behavior."""
        project = "MixApp"
        (workspace / "Projects" / project).mkdir()
        old = registry.publish(
            project, "research", data={"x": 1}, producer="p", summary="old",
        )
        new = registry.publish(
            project, "research", data={"x": 2}, producer="p", summary="new",
        )
        registry.supersede(project, old, new)
        registry.advance_pipeline(project, "build")

        manifest = registry._read_manifest(project)
        by_id = {e["id"]: e for e in manifest["artifacts"]}
        assert by_id[old]["superseded_by"] == new
        assert by_id[new]["superseded_by"] is None
        assert manifest["pipeline_state"] == "build"


class TestRunScopedFilenameCollision:
    """Regression (run_fc95d24c / DoD0b): two same-type same-day publishes into
    ONE run must NOT overwrite each other on disk.

    Before the fix, bare_filename was f"{type}-{date}{topic}.json" with no
    artifact_id, so two run-scoped publishes of the same type on the same day
    produced identical filenames — the 2nd overwrote the 1st on disk while the
    manifest kept two distinct ids, so id1's entry silently resolved to id2's
    data (BUILD lost to DELIVER; think lost to plan — both hit this session).
    Fix: run-scoped filenames append the artifact_id. Top-level names unchanged.

    Mutation: revert the artifact_id append in publish() → this test goes RED
    (1 file on disk, id1 resolves to id2's data).
    """

    def test_run_scoped_same_type_same_day_no_collision(self, workspace, registry):
        project = "CollisionApp"
        (workspace / "Projects" / project).mkdir()

        id1 = registry.publish(project, "changeset", {"who": "BUILD"},
                               producer="build", summary="build stage", run_id="run_x")
        id2 = registry.publish(project, "changeset", {"who": "DELIVER"},
                               producer="deliver", summary="deliver stage", run_id="run_x")

        assert id1 != id2
        run_dir = workspace / "Projects" / project / ".artifacts" / "runs" / "run_x"
        files = sorted(run_dir.glob("*.json"))
        assert len(files) == 2, "two same-type publishes must produce two distinct files"

        # Each artifact_id must resolve to ITS OWN data (not the other's).
        a1 = registry.get_artifact(project, id1)
        a2 = registry.get_artifact(project, id2)
        assert a1 is not None and a2 is not None
        assert a1.data["who"] == "BUILD", "id1 must resolve to BUILD (not overwritten)"
        assert a2.data["who"] == "DELIVER"

    def test_top_level_filename_scheme_unchanged(self, workspace, registry):
        """Top-level (no run_id) filenames must stay byte-identical to the old
        scheme so the by-name readers (_load_artifact_by_date etc.) keep working."""
        project = "TopLevelApp"
        (workspace / "Projects" / project).mkdir()

        registry.publish(project, "research", {"k": "v"},
                         producer="t", summary="s")  # no run_id → top-level
        artifacts_dir = workspace / "Projects" / project / ".artifacts"
        research_files = [f.name for f in artifacts_dir.iterdir()
                          if f.name.startswith("research-")]
        assert len(research_files) == 1
        # Exact old scheme: research-YYYYMMDD.json — NO artifact_id suffix.
        import re
        assert re.fullmatch(r"research-\d{8}\.json", research_files[0]), \
            f"top-level name must be unchanged, got {research_files[0]}"
