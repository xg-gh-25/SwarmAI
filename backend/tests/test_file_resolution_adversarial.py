"""Adversarial tests for workspace file resolution (Stage 3/4).

Tests cover:
- Stage 3: bare filename search within Projects/ (symlinked repos)
- Stage 4: bare filename search within workspace root (Knowledge/, .context/, etc.)
- Non-deterministic ordering edge cases
- Path traversal via bare filenames
- Depth cap enforcement
- Excluded directory pruning
- Symlink boundary enforcement
- Priority: Projects/ searched before workspace root
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def workspace(tmp_path):
    """Create a mock workspace with standard structure."""
    # Standard workspace directories
    (tmp_path / "Knowledge" / "Notes").mkdir(parents=True)
    (tmp_path / "Knowledge" / "DailyActivity").mkdir(parents=True)
    (tmp_path / ".context").mkdir()
    (tmp_path / "Services").mkdir()
    (tmp_path / "Projects").mkdir()
    return tmp_path


@pytest.fixture
def app_with_workspace(workspace):
    """Create a FastAPI test app patched to use our temp workspace."""
    from main import app

    async def _mock_workspace_path():
        return str(workspace)

    with patch("routers.workspace_api._get_workspace_path", new=_mock_workspace_path):
        yield app, workspace


# ---------------------------------------------------------------------------
# Stage 3: Bare filename in Projects/ (symlinked repos)
# ---------------------------------------------------------------------------

class TestStage3BareFilenameInProjects:
    """Stage 3 searches inside Projects/ subdirectories for bare filenames."""

    @pytest.mark.asyncio
    async def test_finds_file_in_project(self, app_with_workspace):
        """Basic: finds a file inside a project directory."""
        app, ws = app_with_workspace
        project = ws / "Projects" / "MyProject"
        project.mkdir(parents=True)
        (project / "src").mkdir()
        (project / "src" / "handler.py").write_text("# handler")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspace/file/resolve", params={"path": "handler.py"})
            assert resp.status_code == 200
            data = resp.json()
            assert "Projects/MyProject" in data["resolved_path"]
            assert data["resolved_path"].endswith("handler.py")

    @pytest.mark.asyncio
    async def test_project_priority_over_workspace_root(self, app_with_workspace):
        """Stage 3 (Projects/) is searched BEFORE Stage 4 (workspace root)."""
        app, ws = app_with_workspace
        project = ws / "Projects" / "Alpha"
        project.mkdir(parents=True)
        (project / "config.json").write_text("{}")

        # Same file in workspace root
        (ws / "Knowledge" / "config.json").write_text("{}")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspace/file/resolve", params={"path": "config.json"})
            assert resp.status_code == 200
            # Should find in Projects/ first (Stage 3 before Stage 4)
            assert "Projects/Alpha" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_projects_searched_in_sorted_order(self, app_with_workspace):
        """Projects are iterated in sorted() order — deterministic."""
        app, ws = app_with_workspace

        # Create projects in reverse-alpha order on disk
        for name in ["Zulu", "Alpha", "Mike"]:
            p = ws / "Projects" / name
            p.mkdir(parents=True)
            (p / "shared.txt").write_text(f"from {name}")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspace/file/resolve", params={"path": "shared.txt"})
            assert resp.status_code == 200
            # sorted() means Alpha wins
            assert "Projects/Alpha" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_depth_cap_enforced(self, app_with_workspace):
        """Files deeper than _MAX_DEPTH (8) are not found."""
        app, ws = app_with_workspace
        project = ws / "Projects" / "Deep"
        project.mkdir(parents=True)

        # Create file at depth 9 (beyond the cap of 8)
        deep_path = project
        for i in range(9):
            deep_path = deep_path / f"level{i}"
        deep_path.mkdir(parents=True)
        (deep_path / "buried.txt").write_text("too deep")

        # Also create one at depth 7 (within cap)
        shallow = project / "a" / "b" / "c"
        shallow.mkdir(parents=True)
        (shallow / "shallow.txt").write_text("reachable")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Buried file should NOT be found
            resp = await client.get("/api/workspace/file/resolve", params={"path": "buried.txt"})
            assert resp.status_code == 404

            # Shallow file should be found
            resp = await client.get("/api/workspace/file/resolve", params={"path": "shallow.txt"})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_excluded_dirs_pruned(self, app_with_workspace):
        """Files inside node_modules, .git, etc. are NOT found."""
        app, ws = app_with_workspace
        project = ws / "Projects" / "NodeApp"
        project.mkdir(parents=True)

        # File inside node_modules — should be excluded
        nm = project / "node_modules" / "lodash"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")

        # File inside .git — should be excluded
        git = project / ".git" / "objects"
        git.mkdir(parents=True)
        (git / "pack.idx").write_text("")

        # File inside __pycache__ — should be excluded
        cache = project / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "module.cpython-312.pyc").write_text("")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for filename in ["index.js", "pack.idx", "module.cpython-312.pyc"]:
                resp = await client.get("/api/workspace/file/resolve", params={"path": filename})
                # These should NOT resolve (excluded dirs)
                assert resp.status_code == 404, f"{filename} should not resolve from excluded dir"


# ---------------------------------------------------------------------------
# Stage 4: Bare filename in workspace root
# ---------------------------------------------------------------------------

class TestStage4BareFilenameInWorkspaceRoot:
    """Stage 4 searches the workspace root, excluding Projects/ (already searched)."""

    @pytest.mark.asyncio
    async def test_finds_file_in_knowledge(self, app_with_workspace):
        """Finds a file in Knowledge/ when not in Projects/."""
        app, ws = app_with_workspace
        (ws / "Knowledge" / "Notes" / "2026-06-07-foo.md").write_text("# note")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "2026-06-07-foo.md"}
            )
            assert resp.status_code == 200
            assert "Knowledge/Notes/2026-06-07-foo.md" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_finds_file_in_context(self, app_with_workspace):
        """Finds a file in .context/ directory."""
        app, ws = app_with_workspace
        (ws / ".context" / "MEMORY.md").write_text("# memory")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "MEMORY.md"}
            )
            assert resp.status_code == 200
            assert ".context/MEMORY.md" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_stage4_excludes_projects(self, app_with_workspace):
        """Stage 4 skips Projects/ (already searched in Stage 3)."""
        app, ws = app_with_workspace
        # File ONLY in Projects/ — Stage 3 finds it
        project = ws / "Projects" / "Solo"
        project.mkdir(parents=True)
        (project / "only_here.txt").write_text("solo")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "only_here.txt"}
            )
            # Should still be found (by Stage 3, not 4)
            assert resp.status_code == 200
            assert "Projects/Solo" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_stage4_depth_cap(self, app_with_workspace):
        """Stage 4 also respects _MAX_DEPTH = 8."""
        app, ws = app_with_workspace
        deep = ws / "Knowledge"
        for i in range(9):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        (deep / "deep_root.txt").write_text("too deep")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "deep_root.txt"}
            )
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Non-determinism: Stage 4 os.walk ordering
# ---------------------------------------------------------------------------

class TestStage4DeterministicOrder:
    """Stage 4 os.walk dirs are sorted — same-name files resolve deterministically."""

    @pytest.mark.asyncio
    async def test_same_filename_multiple_dirs_resolves_alphabetically_first(self, app_with_workspace):
        """When the same filename exists in multiple Stage 4 dirs,
        the alphabetically-first directory wins (sorted os.walk)."""
        app, ws = app_with_workspace
        (ws / "Knowledge" / "README.md").write_text("# knowledge readme")
        (ws / "Services" / "README.md").write_text("# services readme")
        (ws / ".context" / "README.md").write_text("# context readme")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "README.md"}
            )
            assert resp.status_code == 200
            resolved = resp.json()["resolved_path"]
            # Workspace root itself is checked first (depth 0), then sorted subdirs.
            # ".context" sorts before "Knowledge" and "Services" (dot comes first),
            # but the workspace root is visited first by os.walk — if not found there,
            # subdirs are visited in sorted order: .context < Knowledge < Services.
            # README.md is in subdirs (not root), so .context wins.
            assert ".context/README.md" == resolved

    @pytest.mark.asyncio
    async def test_deterministic_across_runs(self, app_with_workspace):
        """Multiple calls return the same result — no randomness."""
        app, ws = app_with_workspace
        (ws / "Knowledge" / "target.md").write_text("a")
        (ws / "Services" / "target.md").write_text("b")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            results = []
            for _ in range(5):
                resp = await client.get(
                    "/api/workspace/file/resolve", params={"path": "target.md"}
                )
                assert resp.status_code == 200
                results.append(resp.json()["resolved_path"])

            # All 5 calls must return the same path
            assert len(set(results)) == 1


# ---------------------------------------------------------------------------
# Security: Path traversal via bare filename
# ---------------------------------------------------------------------------

class TestPathTraversalSecurity:
    """Ensure bare filename search can't be abused for traversal."""

    @pytest.mark.asyncio
    async def test_dotdot_in_path_rejected(self, app_with_workspace):
        """Paths with .. components are rejected before reaching Stage 3/4."""
        app, ws = app_with_workspace

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "../../../etc/passwd"}
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_slash_in_filename_skips_bare_search(self, app_with_workspace):
        """Filenames with / do NOT enter Stage 3/4 bare-filename search."""
        app, ws = app_with_workspace
        # Create the file at the relative path — should use Stage 1/2, not 3/4
        (ws / "Knowledge" / "Notes").mkdir(parents=True, exist_ok=True)
        (ws / "Knowledge" / "Notes" / "test.md").write_text("content")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve",
                params={"path": "Knowledge/Notes/test.md"},
            )
            # This goes through Stage 1 (direct lookup), not Stage 3/4
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_filename_with_special_chars(self, app_with_workspace):
        """Filenames with special characters don't cause injection."""
        app, ws = app_with_workspace
        # These shouldn't crash the resolution
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for evil_name in [
                "file; rm -rf /",
                "$(whoami).txt",
                "file`id`.txt",
                "*",
                "?",
                "[evil]",
            ]:
                resp = await client.get(
                    "/api/workspace/file/resolve", params={"path": evil_name}
                )
                # Should either 404 (not found) or 400 (bad path) — never 500
                assert resp.status_code in (400, 404), f"Unexpected {resp.status_code} for {evil_name!r}"

    @pytest.mark.asyncio
    async def test_null_byte_in_filename(self, app_with_workspace):
        """Null bytes in path should not cause crashes (security: null byte injection)."""
        app, ws = app_with_workspace

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "file\x00.txt"}
            )
            # Must reject gracefully — null bytes are a classic injection vector
            assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Symlink behavior in bare filename search
# ---------------------------------------------------------------------------

class TestSymlinkInBareSearch:
    """How Stage 3 handles symlinked project directories."""

    @pytest.mark.asyncio
    async def test_finds_file_through_project_symlink(self, app_with_workspace):
        """Stage 3 resolves symlinks in Projects/ to search the real repo."""
        app, ws = app_with_workspace

        # Create a "real" repo outside workspace
        real_repo = ws.parent / "real_swarmai"
        real_repo.mkdir()
        (real_repo / "backend").mkdir()
        (real_repo / "backend" / "main.py").write_text("# main")

        # Symlink from Projects/
        project_link = ws / "Projects" / "SwarmAI"
        project_link.symlink_to(real_repo)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "main.py"}
            )
            assert resp.status_code == 200
            assert "Projects/SwarmAI" in resp.json()["resolved_path"]

    @pytest.mark.asyncio
    async def test_symlink_inside_project_not_followed(self, app_with_workspace):
        """os.walk(followlinks=False) by default — symlinks inside the
        project tree are NOT followed during Stage 3 walk."""
        app, ws = app_with_workspace

        project = ws / "Projects" / "TestProj"
        project.mkdir(parents=True)

        # Create a symlink inside the project pointing elsewhere
        secret_dir = ws.parent / "secrets"
        secret_dir.mkdir()
        (secret_dir / "api_key.txt").write_text("sk-123456")

        escape_link = project / "data"
        escape_link.symlink_to(secret_dir)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "api_key.txt"}
            )
            # os.walk with followlinks=False should NOT traverse into the symlink
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Miscellaneous edge cases for file resolution."""

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self, app_with_workspace):
        """Empty string path should fail gracefully."""
        app, ws = app_with_workspace

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": ""}
            )
            # FastAPI should reject empty (min_length or the endpoint logic)
            assert resp.status_code in (400, 404, 422)

    @pytest.mark.asyncio
    async def test_very_long_filename(self, app_with_workspace):
        """Extremely long filenames should not crash (max_length=1024 on param)."""
        app, ws = app_with_workspace

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            # Exceeds max_length=1024 — rejected by validation middleware (400 or 422)
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "a" * 1025}
            )
            assert resp.status_code in (400, 422)

    @pytest.mark.asyncio
    async def test_hidden_file_found(self, app_with_workspace):
        """Hidden files (dotfiles) should be findable."""
        app, ws = app_with_workspace
        (ws / ".context" / ".hidden_config").write_text("secret")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": ".hidden_config"}
            )
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_file_not_found_returns_404(self, app_with_workspace):
        """Non-existent filename returns clean 404."""
        app, ws = app_with_workspace

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve",
                params={"path": "absolutely_nonexistent_file_xyz.txt"},
            )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_directory_name_not_resolved_as_file(self, app_with_workspace):
        """A bare name that matches a directory (not file) should 404."""
        app, ws = app_with_workspace
        (ws / "Knowledge" / "Notes" / "mydir").mkdir(parents=True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                "/api/workspace/file/resolve", params={"path": "mydir"}
            )
            # "mydir" is a directory, not a file — should not resolve
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_concurrent_resolution_safe(self, app_with_workspace):
        """Multiple concurrent resolution requests don't interfere."""
        app, ws = app_with_workspace

        # Create distinct files
        for i in range(5):
            (ws / "Knowledge" / f"file_{i}.md").write_text(f"content {i}")

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            tasks = [
                client.get("/api/workspace/file/resolve", params={"path": f"file_{i}.md"})
                for i in range(5)
            ]
            responses = await asyncio.gather(*tasks)

            for i, resp in enumerate(responses):
                assert resp.status_code == 200
                assert f"file_{i}.md" in resp.json()["resolved_path"]
