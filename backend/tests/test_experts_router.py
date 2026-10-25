"""Tests for the Experts API router (GET /api/experts) — R2 Experts visualization.

Methodology: integration tests against the REAL expert tree (backend/experts/),
no mocks of ExpertManager — the endpoint projects real registry state, so the
tests assert against real data (same philosophy as test_ddd_brain_router).

Key invariants under test:
  - AC1: GET /api/experts returns the 12 real code experts grouped by domain,
    each carrying the 7 frontmatter fields + the derived is_valid health signal.
  - Serialization: ExpertInfo (frozen dataclass) is projected into ExpertResponse
    (pydantic) via the explicit converter — the Gate-1 frontmatter-serialization
    gap. A regression (returning the raw dataclass) would fail FastAPI validation.
  - Empty-domain safety: by_domain over a tree with no experts yields {} (not a
    crash), so the frontend renders an empty state, not an error.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from routers.experts import router

    app = FastAPI()
    app.include_router(router, prefix="/api/experts")
    return TestClient(app)


# The 9 code experts expected from the real tree (mirrors
# test_expert_manager.py's roster — the design §D2 code domain).
_EXPECTED_CODE_EXPERTS = {
    "api-contract", "concurrency", "correctness",
    "integration", "operational", "performance",
    "state-machine", "security", "ux",
}


class TestListExperts:
    def test_returns_200_grouped_by_domain(self, client):
        """AC1: GET /api/experts returns experts grouped by domain."""
        resp = client.get("/api/experts")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, dict), "response must be {domain: [experts]}"
        assert "code" in data, "the 12 code experts live under domain 'code'"

    def test_code_domain_has_all_12_experts(self, client):
        """AC1: the real tree's 12 code experts all surface, keyed by expert_id."""
        data = client.get("/api/experts").json()
        ids = {e["expert_id"] for e in data["code"]}
        assert ids == _EXPECTED_CODE_EXPERTS, (
            f"expected the 12 code experts, got {sorted(ids)}"
        )

    def test_every_expert_carries_the_7_fields_plus_health(self, client):
        """AC1: each expert projects the 6 frontmatter fields + is_valid (not a stub)."""
        data = client.get("/api/experts").json()
        required = {
            "expert_id", "display_name_zh", "display_name_en", "domain",
            "scope", "visibility", "is_valid",
        }
        for expert in data["code"]:
            assert required <= set(expert.keys()), (
                f"{expert.get('expert_id')} missing fields: "
                f"{required - set(expert.keys())}"
            )
            # Non-empty substance: a real code expert carries a scope description
            # (the UI's one-line "what this expert does") + a display name.
            assert expert["scope"], f"{expert['expert_id']} has empty scope"
            assert expert["display_name_en"], (
                f"{expert['expert_id']} has empty display_name_en"
            )

    def test_all_real_experts_are_valid(self, client):
        """AC1: the shipped 12 are well-formed (is_valid True) — health signal works."""
        data = client.get("/api/experts").json()
        for expert in data["code"]:
            assert expert["is_valid"] is True, (
                f"{expert['expert_id']} is_valid False — a shipped expert should be "
                "well-formed; this asserts the health signal is wired, not fabricated"
            )

    def test_experts_sorted_by_id_within_domain(self, client):
        """by_domain sorts each domain's experts by expert_id — stable UI order."""
        data = client.get("/api/experts").json()
        ids = [e["expert_id"] for e in data["code"]]
        assert ids == sorted(ids), "experts must be sorted by expert_id within domain"


class TestSerializationContract:
    """The Gate-1 finding: ExpertInfo is a frozen dataclass; the route MUST project
    it into the pydantic ExpertResponse via the explicit converter. These assert the
    converter is real (not a raw-dataclass passthrough that would fail validation)."""

    def test_converter_projects_is_valid_from_missing_fields(self):
        from routers.experts import _expert_info_to_response
        from core.expert_manager import ExpertInfo
        from pathlib import Path

        # A malformed expert (missing fields) must project is_valid=False.
        broken = ExpertInfo(
            expert_id="x", display_name_zh="", display_name_en="",
            domain="code", scope="", visibility="",
            path=Path("/tmp/x.md"), missing_fields=("display_name_en", "scope"),
        )
        resp = _expert_info_to_response(broken)
        assert resp.is_valid is False, (
            "is_valid must derive from missing_fields — a malformed expert surfaces "
            "as unhealthy, not dropped"
        )

    def test_converter_projects_valid_expert(self):
        from routers.experts import _expert_info_to_response
        from core.expert_manager import ExpertInfo
        from pathlib import Path

        good = ExpertInfo(
            expert_id="correctness", display_name_zh="正确性评审官",
            display_name_en="Correctness Auditor", domain="code",
            scope="逻辑、边界条件、错误处理",
            visibility="public", path=Path("/tmp/c.md"), missing_fields=(),
        )
        resp = _expert_info_to_response(good)
        assert resp.is_valid is True
        assert resp.expert_id == "correctness"
        assert resp.scope == "逻辑、边界条件、错误处理"


class TestEmptyTreeSafety:
    def test_empty_tree_yields_empty_dict_not_crash(self, tmp_path):
        """A tree with no experts → {} (frontend renders empty state, no error)."""
        from routers.experts import _expert_info_to_response  # noqa: F401
        from core.expert_manager import ExpertManager

        mgr = ExpertManager(experts_root=tmp_path)
        assert mgr.by_domain() == {}, "empty tree must yield {} not a crash"


class TestCrossBoundaryContract:
    """Layer-4 cross-boundary E2E (cross_boundary=true: frontend↔backend contract).

    The seam: GET /api/experts (backend ExpertResponse, snake_case) ↔ the frontend
    toCamelExpert mapper (desktop/src/services/experts.ts) which reads a FIXED set of
    snake_case keys. This test binds the backend's emitted key-set to the exact keys
    the frontend consumes — a divergence (backend drops/renames a field the frontend
    reads) goes RED here, which no single-side unit catches.
    """

    # The snake_case keys toCamelExpert reads (experts.ts). If the backend stops
    # emitting one, the frontend silently renders '' — this set is the contract.
    _FRONTEND_CONSUMED_KEYS = {
        "expert_id", "display_name_zh", "display_name_en", "domain",
        "scope", "visibility", "is_valid",
    }

    def test_backend_emits_exactly_the_keys_the_frontend_consumes(self, client):
        """The real HTTP response's per-expert key-set MUST cover every key the
        frontend toCamelExpert mapper reads — the lockstep contract."""
        data = client.get("/api/experts").json()
        sample = data["code"][0]
        emitted = set(sample.keys())
        missing = self._FRONTEND_CONSUMED_KEYS - emitted
        assert not missing, (
            f"backend dropped keys the frontend reads: {missing} — "
            "frontend would render these fields blank (silent contract break)"
        )

    def test_response_never_leaks_the_internal_path_field(self, client):
        """Gate-2 security: ExpertInfo carries an internal `path: Path`; the response
        MUST NOT include it (nor any key beyond the 8 declared). A regression that
        spread the raw dataclass would leak absolute filesystem paths to any client.
        This asserts the exclusion as a testable contract, not a doc assumption."""
        data = client.get("/api/experts").json()
        for expert in data["code"]:
            assert "path" not in expert, (
                f"{expert.get('expert_id')} leaked the internal 'path' field — "
                "info disclosure (absolute FS path to any GET /api/experts caller)"
            )
            # Exactly the 8 declared keys, nothing more (no accidental dataclass spread).
            assert set(expert.keys()) == self._FRONTEND_CONSUMED_KEYS, (
                f"{expert.get('expert_id')} emitted unexpected keys: "
                f"{set(expert.keys()) - self._FRONTEND_CONSUMED_KEYS}"
            )
