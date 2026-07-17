"""Tests for the verify-native tree_sitter FUNCTIONAL probe (run_494094ec).

Why this exists: verify_build's tree_sitter capability check used to route through
the verify-import endpoint, which does only ``__import__(module)``. That passes even
when the AST path is non-functional (the get_parser old-ABI bug fixed in run_2e46f2af:
``import tree_sitter`` succeeded while ``.parse(bytes)`` raised). A bare-import gate
therefore gives FALSE CONFIDENCE — it cannot detect a broken AST. The verify-native
endpoint is the functional-probe pattern (load + call + assert a real result, as it
already does for sqlite_vec). This test drives the tree_sitter branch of that endpoint.

Testing methodology: FastAPI TestClient against /api/system/verify-native with
SWARMAI_VERIFY_BUILD=1, exactly mirroring the endpoint's real dispatch. No mock of
tree-sitter — the whole point is to exercise the REAL parse.
"""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SWARMAI_VERIFY_BUILD", "1")
    import backend.main as main  # heavy import; scoped to this fixture
    return TestClient(main.app)


class TestVerifyNativeTreeSitter:
    def test_tree_sitter_parse_probe_is_functional(self, client):
        """The tree_sitter/parse probe must ACTUALLY parse (not just import).

        RED before the branch exists: verify-native returns
        {"loadable": false, "detail": "unknown native extension: tree_sitter/parse"}.
        GREEN after: it constructs a real Parser, parses bytes, and confirms an AST
        node — the functional signal a bare __import__ cannot give.
        """
        resp = client.get("/api/system/verify-native", params={"path": "tree_sitter/parse"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["loadable"] is True, (
            f"tree_sitter/parse must be a FUNCTIONAL probe (real parse → AST node), "
            f"not import-only. Got: {data}")
        # detail should evidence a real parse (e.g. the root node type 'module')
        assert "module" in data.get("detail", "").lower(), (
            f"probe detail must evidence a real AST parse (root type 'module'), got: {data}")

    def test_sqlite_vec_native_probe_still_works(self, client):
        """Regression: the pre-existing sqlite_vec verify-native branch is unaffected."""
        resp = client.get("/api/system/verify-native", params={"path": "sqlite_vec/vec0"})
        assert resp.status_code == 200
        # loadable may be True or False depending on the sqlite build, but it must
        # still dispatch to the sqlite_vec branch (NOT "unknown native extension").
        data = resp.json()
        assert "unknown native extension" not in data.get("detail", ""), (
            f"sqlite_vec branch must still be reachable, got: {data}")

    def test_unknown_native_still_rejected(self, client):
        """A genuinely unknown native path is still rejected (dispatch integrity)."""
        resp = client.get("/api/system/verify-native", params={"path": "not_a_real_ext/xyz"})
        data = resp.json()
        assert data["loadable"] is False
        assert "unknown native extension" in data.get("detail", "")
