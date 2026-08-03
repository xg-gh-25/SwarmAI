"""Tests for change_risk_score module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.code_intel.change_risk_score import (
    RISK_WEIGHTS,
    SECURITY_KEYWORDS,
    _bucket,
    _score_caller_count,
    _score_module_crossing,
    _score_module_spread,
    _score_security_surface,
    _score_test_gap,
    score_change,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_graph_store():
    gs = MagicMock()
    # find_callers returns list[tuple[caller_id, hop]]
    gs.find_callers.return_value = []
    # get_nodes_by_file returns list[dict]
    gs.get_nodes_by_file.return_value = []
    return gs


# ---------------------------------------------------------------------------
# Risk buckets
# ---------------------------------------------------------------------------

class TestBucket:
    def test_low(self):
        assert _bucket(0.0) == "LOW"
        assert _bucket(0.29) == "LOW"

    def test_medium(self):
        assert _bucket(0.3) == "LOW"       # boundary: <= 0.3 is LOW
        assert _bucket(0.31) == "MEDIUM"
        assert _bucket(0.6) == "MEDIUM"

    def test_high(self):
        assert _bucket(0.61) == "HIGH"
        assert _bucket(0.8) == "HIGH"

    def test_critical(self):
        assert _bucket(0.81) == "CRITICAL"
        assert _bucket(1.0) == "CRITICAL"


# ---------------------------------------------------------------------------
# Individual dimensions
# ---------------------------------------------------------------------------

class TestModuleSpread:
    def test_single_module(self):
        s = _score_module_spread(["backend/core/auth.py", "backend/core/user.py"])
        assert s.raw == 0.0   # 1 module

    def test_two_modules(self):
        s = _score_module_spread(["backend/core/auth.py", "backend/api/views.py"])
        assert s.raw == pytest.approx(0.25)   # (2-1)/4 = 0.25

    def test_five_modules(self):
        files = [f"mod{i}/sub/f.py" for i in range(5)]
        s = _score_module_spread(files)
        assert s.raw == pytest.approx(1.0)


class TestTestGap:
    def test_all_tested(self, mock_graph_store):
        # find_callers returns tuples with "test" in the caller_id
        mock_graph_store.find_callers.return_value = [
            ("tests/test_x.py:test_something", 1)
        ]
        s = _score_test_gap(["n1", "n2"], mock_graph_store)
        assert s.raw == 0.0

    def test_none_tested(self, mock_graph_store):
        mock_graph_store.find_callers.return_value = []
        s = _score_test_gap(["n1", "n2"], mock_graph_store)
        assert s.raw == 1.0

    def test_half_tested(self, mock_graph_store):
        call_count = 0
        def _find_callers(nid, depth=1):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 0:
                return [("tests/test_x.py:test_it", 1)]
            return []
        mock_graph_store.find_callers.side_effect = _find_callers
        s = _score_test_gap(["n1", "n2"], mock_graph_store)
        assert s.raw == pytest.approx(0.5)

    def test_empty_nodes(self, mock_graph_store):
        s = _score_test_gap([], mock_graph_store)
        assert s.raw == 0.0


class TestCallerCount:
    def test_no_callers(self, mock_graph_store):
        mock_graph_store.find_callers.return_value = []
        s = _score_caller_count(["n1"], mock_graph_store)
        assert s.raw == 0.0

    def test_many_callers(self, mock_graph_store):
        mock_graph_store.find_callers.return_value = [(f"c{i}", 1) for i in range(15)]
        s = _score_caller_count(["n1"], mock_graph_store)
        assert s.raw == 1.0   # capped at 1.0


class TestSecuritySurface:
    def test_no_keywords(self, mock_graph_store):
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "frontend/page.py:render_page", "name": "render_page",
             "file_path": "frontend/page.py"}
        ]
        s = _score_security_surface(
            ["frontend/page.py"], ["frontend/page.py:render_page"], mock_graph_store
        )
        assert s.raw == 0.0

    def test_auth_keyword_in_path(self, mock_graph_store):
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "backend/auth/login.py:foo", "name": "foo",
             "file_path": "backend/auth/login.py"}
        ]
        s = _score_security_surface(
            ["backend/auth/login.py"], ["backend/auth/login.py:foo"], mock_graph_store
        )
        assert s.raw > 0.0
        assert "auth" in s.detail

    def test_multiple_keywords(self, mock_graph_store):
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "backend/crypto/token.py:hash_password", "name": "hash_password",
             "file_path": "backend/crypto/token.py"}
        ]
        s = _score_security_surface(
            ["backend/crypto/token.py"],
            ["backend/crypto/token.py:hash_password"],
            mock_graph_store,
        )
        # "crypto", "token" from path; "hash", "password" from name
        assert s.raw == 1.0  # 4 keywords >= 3 -> capped at 1.0


class TestModuleCrossing:
    def test_all_internal(self, mock_graph_store):
        # Node and its callers are in the same module
        mock_graph_store.get_nodes_by_file.side_effect = lambda fp: {
            "backend/core/a.py": [
                {"id": "backend/core/a.py:fn", "name": "fn", "file_path": "backend/core/a.py"}
            ],
            "backend/core/b.py": [
                {"id": "backend/core/b.py:caller", "name": "caller",
                 "file_path": "backend/core/b.py"}
            ],
        }.get(fp, [])
        mock_graph_store.find_callers.return_value = [
            ("backend/core/b.py:caller", 1)
        ]
        s = _score_module_crossing(["backend/core/a.py:fn"], mock_graph_store)
        assert s.raw == 0.0

    def test_all_external(self, mock_graph_store):
        mock_graph_store.get_nodes_by_file.side_effect = lambda fp: {
            "backend/core/a.py": [
                {"id": "backend/core/a.py:fn", "name": "fn", "file_path": "backend/core/a.py"}
            ],
            "frontend/ui/b.py": [
                {"id": "frontend/ui/b.py:caller", "name": "caller",
                 "file_path": "frontend/ui/b.py"}
            ],
        }.get(fp, [])
        mock_graph_store.find_callers.return_value = [
            ("frontend/ui/b.py:caller", 1)
        ]
        s = _score_module_crossing(["backend/core/a.py:fn"], mock_graph_store)
        assert s.raw == 1.0


# ---------------------------------------------------------------------------
# Weight sanity
# ---------------------------------------------------------------------------

class TestWeights:
    def test_weights_sum_to_one(self):
        total = sum(RISK_WEIGHTS.values())
        assert total == pytest.approx(1.0)

    def test_security_keywords_present(self):
        assert "auth" in SECURITY_KEYWORDS
        assert "sql" in SECURITY_KEYWORDS
        assert "eval" in SECURITY_KEYWORDS


# ---------------------------------------------------------------------------
# Integration: score_change
# ---------------------------------------------------------------------------

class TestScoreChange:
    @patch("core.code_intel.change_risk_score.subprocess.run")
    def test_low_risk_change(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        mock_graph_store.find_callers.return_value = [
            ("tests/test_x.py:test_it", 1)
        ]
        mock_graph_store.get_nodes_by_file.return_value = [
            {"id": "a/b/c.py:helper", "name": "helper", "file_path": "a/b/c.py"}
        ]

        result = score_change(
            mock_graph_store,
            tmp_path,
            changed_files=["backend/core/utils.py"],
            changed_node_ids=["a/b/c.py:helper"],
        )
        assert result.risk_level == "LOW"
        assert len(result.dimensions) == 6

    @patch("core.code_intel.change_risk_score.subprocess.run")
    def test_result_formatting(self, mock_run, mock_graph_store, tmp_path):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = score_change(
            mock_graph_store, tmp_path,
            changed_files=["a/b.py"],
            changed_node_ids=[],
        )
        assert "Risk:" in result.to_minimal_context()
        assert "Risk:" in result.to_full_context()
