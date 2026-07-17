"""Tests for code intelligence API router (GET /api/code-intel/{project}/summary)."""

import time

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create FastAPI test client with code_intel router."""
    from fastapi import FastAPI
    from routers.code_intel import router

    app = FastAPI()
    app.include_router(router, prefix="/api/code-intel")
    return TestClient(app)


class TestCodeIntelSummary:
    """GET /api/code-intel/{project}/summary."""

    def test_returns_summary_when_db_exists(self, client):
        """AC1: endpoint returns JSON stats from existing code_intel.db."""
        mock_graph = MagicMock()
        mock_graph.get_codebase_summary.return_value = {
            "total_nodes": 9301,
            "total_edges": 17941,
            "total_files": 860,
            "entry_point_count": 5392,
            "dead_code_count": 1277,
            "languages": {"python": 8600, "typescript": 701},
            "modules": {"tests": {"function_count": 3847, "class_count": 100, "file_count": 200}},
            "top_connected": [{"name": "error", "file_path": "core/errors.py", "callers": 157}],
            "last_indexed": "2026-05-04T10:30:00",
            "module_count": 6,
        }

        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/SwarmAI/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol_count"] == 9301
        assert data["edge_count"] == 17941
        assert data["unused_exports_count"] == 1277
        assert data["unused_exports_pct"] == pytest.approx(13.7, abs=0.1)
        assert data["entry_points"] == 5392
        assert data["last_indexed_at"] == "2026-05-04T10:30:00"
        assert "python" in data["languages"]
        assert len(data["modules_top5"]) <= 5

    def test_returns_404_when_no_db(self, client):
        """AC5: graceful degradation — no code_intel.db returns 404."""
        with patch("routers.code_intel.load_project_graph", return_value=None):
            resp = client.get("/api/code-intel/NonExistent/summary")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_freshness_status_fresh(self, client):
        """Freshness: indexed within 7 days → status 'fresh'."""
        from datetime import datetime, timezone, timedelta

        recent_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        mock_graph = MagicMock()
        mock_graph.get_codebase_summary.return_value = {
            "total_nodes": 100, "total_edges": 50, "total_files": 10,
            "entry_point_count": 5, "dead_code_count": 3,
            "languages": {"python": 100}, "modules": {},
            "top_connected": [], "last_indexed": recent_time, "module_count": 1,
        }

        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/TestProject/summary")

        assert resp.status_code == 200
        assert resp.json()["freshness_status"] == "fresh"

    def test_freshness_status_stale(self, client):
        """Freshness: indexed >7 days ago → status 'stale'."""
        from datetime import datetime, timezone, timedelta

        old_time = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        mock_graph = MagicMock()
        mock_graph.get_codebase_summary.return_value = {
            "total_nodes": 100, "total_edges": 50, "total_files": 10,
            "entry_point_count": 5, "dead_code_count": 3,
            "languages": {"python": 100}, "modules": {},
            "top_connected": [], "last_indexed": old_time, "module_count": 1,
        }

        with patch("routers.code_intel.load_project_graph", return_value=mock_graph):
            resp = client.get("/api/code-intel/TestProject/summary")

        assert resp.status_code == 200
        assert resp.json()["freshness_status"] == "stale"


class TestCodeIntelReindex:
    """POST /api/code-intel/{project}/reindex."""

    def test_reindex_returns_accepted(self, client):
        """AC4: re-index triggers background task and returns 202."""
        from pathlib import Path
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True

        with patch("routers.code_intel.get_code_intel_db_path", return_value=mock_path), \
             patch("routers.code_intel._run_reindex") as mock_reindex, \
             patch("routers.code_intel._reindex_in_progress", {}):
            resp = client.post("/api/code-intel/SwarmAI/reindex")

        assert resp.status_code == 202
        assert resp.json()["status"] == "indexing"
        mock_reindex.assert_called_once_with("SwarmAI")

    def test_reindex_404_when_no_project(self, client):
        """Re-index on project without code_intel returns 404."""
        from pathlib import Path
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = False

        with patch("routers.code_intel.get_code_intel_db_path", return_value=mock_path):
            resp = client.post("/api/code-intel/NonExistent/reindex")

        assert resp.status_code == 404

    def test_reindex_returns_already_indexing_when_in_progress(self, client):
        """F3: concurrent reindex guard — returns already_indexing."""
        from pathlib import Path
        mock_path = MagicMock(spec=Path)
        mock_path.exists.return_value = True

        with patch("routers.code_intel.get_code_intel_db_path", return_value=mock_path), \
             patch("routers.code_intel._reindex_in_progress", {"SwarmAI": time.time()}):
            resp = client.post("/api/code-intel/SwarmAI/reindex")

        assert resp.status_code == 202
        assert resp.json()["status"] == "already_indexing"


class TestRunReindexRepoPathParsing:
    """run_19eecc9f (AC6): _run_reindex must resolve the repo path from the REAL
    TECH.md formats. The old inline single-format regex ('**Repo Path:**') matched
    ZERO real projects → reindex silently returned for every project. These tests
    close the coverage gap (prior reindex tests mock _run_reindex wholesale, so the
    path-parsing was never exercised)."""

    def _run_with_tech_md(self, tmp_path, tech_md_content):
        """Drive _run_reindex against a fake project dir; capture parse_repo's arg."""
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        import routers.code_intel as mod

        db_path = tmp_path / "code_intel.db"
        db_path.write_text("")  # exists() → True
        (tmp_path / "TECH.md").write_text(tech_md_content, encoding="utf-8")

        captured = {}

        def fake_parse_repo(root):
            captured["root"] = root
            return []

        # GraphStore + parse_repo are imported function-locally inside _run_reindex,
        # so patch them at their SOURCE modules (not on routers.code_intel).
        with patch.object(mod, "get_code_intel_db_path", return_value=db_path), \
             patch("core.code_intel.graph_store.GraphStore", return_value=MagicMock()), \
             patch("core.code_intel.parser.parse_repo", side_effect=fake_parse_repo), \
             patch.object(mod, "invalidate_cache"), \
             patch.dict(mod._reindex_in_progress, {}, clear=True):
            mod._run_reindex("FakeProj")
        return captured

    def test_local_bold_format_resolves(self, tmp_path):
        """'**Local:** `path`' (SwarmAI format) — parse_repo is called, not skipped."""
        repo = tmp_path / "myrepo"
        repo.mkdir()
        content = f"## Codebase Location\n\n- **Local:** `{repo}/`\n"
        captured = self._run_with_tech_md(tmp_path, content)
        assert "root" in captured, "parse_repo was NOT called — reindex silently skipped"
        # trailing slash normalized away by the caller's rstrip/resolve
        assert str(captured["root"]) == str(repo.resolve())

    def test_bare_backtick_format_resolves(self, tmp_path):
        """'## Codebase Location' + bare backtick path (ai_ready_repo format)."""
        repo = tmp_path / "airepo"
        repo.mkdir()
        content = f"## Codebase Location\n\n`{repo}`\n\n## GitHub\n"
        captured = self._run_with_tech_md(tmp_path, content)
        assert "root" in captured
        assert str(captured["root"]) == str(repo.resolve())

    def test_no_marker_skips_gracefully(self, tmp_path):
        """No path marker → warning + return, parse_repo NEVER called (unchanged)."""
        content = "## Some Heading\n\nno path here.\n"
        captured = self._run_with_tech_md(tmp_path, content)
        assert "root" not in captured, "parse_repo should not run without a repo_path"
