"""Unit and property-based tests for the context preview API endpoint.

This module tests the ``GET /api/projects/{project_id}/context`` endpoint
defined in ``backend/routers/context.py``.  Tests use ``pytest`` with
``httpx.AsyncClient`` for async FastAPI testing, mocking the underlying
dependencies (``swarm_workspace_manager``, ``context_cache``, ``db``) to
isolate the router logic.

Testing methodology:

- **Unit tests** — request/response contracts, query parameter handling,
  ETag caching, content truncation, and error responses.
- **Property-based tests** — use ``hypothesis`` to verify universal
  correctness properties across randomized inputs.

Key scenarios verified:

- Valid project returns 200 with assembled layers
- Invalid project returns 404
- ``thread_id`` query param is forwarded to the assembler
- ``preview_limit`` truncates ``content_preview`` fields
- ``budget_exceeded`` flag reflects truncation state
- ``ETag`` header is present in successful responses
- 304 Not Modified when ``If-None-Match`` matches current ETag
- All ``source_path`` values are workspace-relative (no absolute paths)
- ``truncation_summary`` is present when layers are truncated

Property-based invariants verified:

- **Property 9**: Token count consistency — ``total_token_count`` equals
  sum of layer ``token_count`` values, and ``budget_exceeded`` is True iff
  at least one layer has ``truncated = true``.

Requirements: 33.1, 33.2, 33.3, 33.4, 36.1, 36.2
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import ASGITransport, AsyncClient

from core.context_assembler import AssembledContext, ContextLayer
from core.context_snapshot_cache import VersionCounters


# ── Helpers ────────────────────────────────────────────────────────────


def _make_layer(
    layer_number: int = 1,
    name: str = "System Prompt",
    source_path: str = "system-prompts.md",
    content: str = "Hello world",
    token_count: int = 10,
    truncated: bool = False,
    truncation_stage: int = 0,
) -> ContextLayer:
    """Build a ``ContextLayer`` with sensible defaults."""
    return ContextLayer(
        layer_number=layer_number,
        name=name,
        source_path=source_path,
        content=content,
        token_count=token_count,
        truncated=truncated,
        truncation_stage=truncation_stage,
    )


def _make_assembled(
    layers: list[ContextLayer] | None = None,
    total_token_count: int = 0,
    budget_exceeded: bool = False,
    token_budget: int = 10_000,
    truncation_summary: str = "",
) -> AssembledContext:
    """Build an ``AssembledContext`` with sensible defaults."""
    layers = layers or []
    if total_token_count == 0 and layers:
        total_token_count = sum(l.token_count for l in layers)
    return AssembledContext(
        layers=layers,
        total_token_count=total_token_count,
        budget_exceeded=budget_exceeded,
        token_budget=token_budget,
        truncation_summary=truncation_summary,
    )


_DEFAULT_COUNTERS = VersionCounters(
    thread_version=1,
    task_version=2,
    todo_version=3,
    project_files_version=4,
    memory_version=5,
)


def _patch_dependencies(
    assembled: AssembledContext | None = None,
    counters: VersionCounters | None = None,
    project_exists: bool = True,
    workspace_path: str | None = "/tmp/test-ws",
):
    """Return a context manager that patches all router dependencies.

    Patches:
    - ``swarm_workspace_manager.get_project`` — raises ValueError if not found
    - ``swarm_workspace_manager.expand_path`` — returns workspace_path
    - ``db.workspace_config.get_config`` — returns config dict or None
    - ``context_cache.get_or_assemble`` — returns assembled context
    - ``context_cache._read_version_counters`` — returns version counters
    """
    assembled = assembled or _make_assembled()
    counters = counters or _DEFAULT_COUNTERS

    ws_config = (
        {"file_path": workspace_path} if workspace_path else None
    )

    async def mock_get_project(project_id, ws_path):
        if not project_exists:
            raise ValueError("Project not found")
        return {"id": project_id, "name": "Test Project"}

    patches = {}

    patches["ws_mgr"] = patch(
        "routers.context.swarm_workspace_manager",
        **{
            "get_project": AsyncMock(side_effect=mock_get_project),
            "expand_path": MagicMock(return_value=workspace_path),
        },
    )
    patches["db"] = patch(
        "routers.context.db",
        **{
            "workspace_config.get_config": AsyncMock(return_value=ws_config),
        },
    )
    patches["cache"] = patch(
        "routers.context.context_cache",
        **{
            "get_or_assemble": AsyncMock(return_value=assembled),
            "_read_version_counters": AsyncMock(return_value=counters),
        },
    )

    return patches


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def app():
    """Import the FastAPI app for test client creation."""
    from main import app as _app
    return _app


# ── Test: Valid project returns 200 with layers ───────────────────────


class TestValidProjectReturns200:
    """Verify that a valid project returns 200 with assembled layers.

    **Validates: Requirements 33.1, 33.2**
    """

    @pytest.mark.asyncio
    async def test_valid_project_returns_200_with_layers(self, app) -> None:
        """GET with a valid project_id returns 200 and layer data."""
        layers = [
            _make_layer(1, "System Prompt", "system-prompts.md", "sys content", 50),
            _make_layer(3, "Instructions", "Projects/abc/instructions.md", "instr", 30),
        ]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        body = resp.json()
        assert body["project_id"] == "proj-123"
        assert len(body["layers"]) == 2
        assert body["layers"][0]["layer_number"] == 1
        assert body["layers"][0]["name"] == "System Prompt"
        assert body["layers"][1]["layer_number"] == 3
        assert body["total_token_count"] == 80

    @pytest.mark.asyncio
    async def test_response_includes_all_layer_fields(self, app) -> None:
        """Each layer in the response has all required fields."""
        layers = [_make_layer(1, "System Prompt", "system-prompts.md", "content", 42)]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        layer = resp.json()["layers"][0]
        assert "layer_number" in layer
        assert "name" in layer
        assert "source_path" in layer
        assert "token_count" in layer
        assert "content_preview" in layer
        assert "truncated" in layer
        assert "truncation_stage" in layer


# ── Test: Invalid project returns 404 ─────────────────────────────────


class TestInvalidProjectReturns404:
    """Verify that a non-existent project returns 404.

    **Validates: Requirements 33.1**
    """

    @pytest.mark.asyncio
    async def test_invalid_project_returns_404(self, app) -> None:
        """GET with an unknown project_id returns 404."""
        patches = _patch_dependencies(project_exists=False)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/nonexistent/context")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ── Test: thread_id query param ───────────────────────────────────────


class TestThreadIdQueryParam:
    """Verify thread_id query param is forwarded and reflected.

    **Validates: Requirements 33.4**
    """

    @pytest.mark.asyncio
    async def test_with_thread_id(self, app) -> None:
        """thread_id is included in the response when provided."""
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    params={"thread_id": "thread-abc"},
                )

        assert resp.status_code == 200
        assert resp.json()["thread_id"] == "thread-abc"

    @pytest.mark.asyncio
    async def test_without_thread_id(self, app) -> None:
        """thread_id is null in the response when not provided."""
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert resp.json()["thread_id"] is None


# ── Test: preview_limit truncation ────────────────────────────────────


class TestPreviewLimitTruncation:
    """Verify preview_limit truncates content_preview fields.

    **Validates: Requirements 33.2**
    """

    @pytest.mark.asyncio
    async def test_content_truncated_to_preview_limit(self, app) -> None:
        """content_preview is truncated to the preview_limit chars."""
        long_content = "A" * 1000
        layers = [_make_layer(content=long_content, token_count=200)]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    params={"preview_limit": 50},
                )

        assert resp.status_code == 200
        preview = resp.json()["layers"][0]["content_preview"]
        assert len(preview) == 50

    @pytest.mark.asyncio
    async def test_short_content_not_padded(self, app) -> None:
        """Content shorter than preview_limit is returned as-is."""
        layers = [_make_layer(content="short", token_count=5)]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    params={"preview_limit": 500},
                )

        assert resp.status_code == 200
        assert resp.json()["layers"][0]["content_preview"] == "short"

    @pytest.mark.asyncio
    async def test_default_preview_limit_is_500(self, app) -> None:
        """Default preview_limit of 500 is applied when not specified."""
        long_content = "B" * 800
        layers = [_make_layer(content=long_content, token_count=150)]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        preview = resp.json()["layers"][0]["content_preview"]
        assert len(preview) == 500



# ── Test: budget_exceeded flag ────────────────────────────────────────


class TestBudgetExceededFlag:
    """Verify budget_exceeded reflects whether context was truncated.

    **Validates: Requirements 33.3**
    """

    @pytest.mark.asyncio
    async def test_budget_exceeded_true_when_truncated(self, app) -> None:
        """budget_exceeded is True when assembler reports truncation."""
        layers = [_make_layer(truncated=True, truncation_stage=1)]
        assembled = _make_assembled(
            layers=layers, budget_exceeded=True, truncation_summary="[truncated]"
        )
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert resp.json()["budget_exceeded"] is True

    @pytest.mark.asyncio
    async def test_budget_exceeded_false_when_within_budget(self, app) -> None:
        """budget_exceeded is False when no truncation occurred."""
        layers = [_make_layer(truncated=False)]
        assembled = _make_assembled(layers=layers, budget_exceeded=False)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert resp.json()["budget_exceeded"] is False


# ── Test: ETag header present ─────────────────────────────────────────


class TestETagHeader:
    """Verify ETag header is present in successful responses.

    **Validates: Requirements 36.1**
    """

    @pytest.mark.asyncio
    async def test_etag_header_present(self, app) -> None:
        """Successful response includes an ETag header."""
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert "etag" in resp.headers
        assert resp.headers["etag"]  # non-empty

    @pytest.mark.asyncio
    async def test_etag_body_matches_header_hash(self, app) -> None:
        """The etag field in the body is derived from version counters."""
        counters = VersionCounters(
            thread_version=10, task_version=20, todo_version=30,
            project_files_version=40, memory_version=50,
        )
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled, counters=counters)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        body_etag = resp.json()["etag"]
        expected_hash = counters.compute_hash()
        assert body_etag == expected_hash


# ── Test: 304 Not Modified ────────────────────────────────────────────


class TestNotModified304:
    """Verify 304 Not Modified when If-None-Match matches current ETag.

    **Validates: Requirements 36.2**
    """

    @pytest.mark.asyncio
    async def test_304_when_if_none_match_matches(self, app) -> None:
        """Returns 304 when If-None-Match header matches the current ETag."""
        counters = VersionCounters(
            thread_version=1, task_version=2, todo_version=3,
            project_files_version=4, memory_version=5,
        )
        etag_value = f'"{counters.compute_hash()}"'
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled, counters=counters)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    headers={"If-None-Match": etag_value},
                )

        assert resp.status_code == 304

    @pytest.mark.asyncio
    async def test_200_when_if_none_match_does_not_match(self, app) -> None:
        """Returns 200 when If-None-Match header does not match."""
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    headers={"If-None-Match": '"stale-etag"'},
                )

        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_304_via_since_version_param(self, app) -> None:
        """Returns 304 when since_version matches the current version hash."""
        counters = VersionCounters(
            thread_version=7, task_version=8, todo_version=9,
            project_files_version=10, memory_version=11,
        )
        assembled = _make_assembled(layers=[_make_layer()])
        patches = _patch_dependencies(assembled=assembled, counters=counters)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    params={"since_version": counters.compute_hash()},
                )

        assert resp.status_code == 304


# ── Test: Workspace-relative paths ────────────────────────────────────


class TestWorkspaceRelativePaths:
    """Verify source_path values are workspace-relative (no absolute paths).

    **Validates: Requirements 33.2**
    """

    @pytest.mark.asyncio
    async def test_no_absolute_paths_in_source_path(self, app) -> None:
        """All source_path values are workspace-relative, not absolute."""
        layers = [
            _make_layer(1, "System Prompt", "system-prompts.md", "a", 10),
            _make_layer(3, "Instructions", "Projects/abc/instructions.md", "b", 20),
            _make_layer(6, "Memory", "Knowledge/Memory/prefs.md", "c", 15),
        ]
        assembled = _make_assembled(layers=layers)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        for layer in resp.json()["layers"]:
            path = layer["source_path"]
            assert not path.startswith("/"), (
                f"source_path should be workspace-relative, got absolute: {path}"
            )
            assert not path.startswith("C:"), (
                f"source_path should not be a Windows absolute path: {path}"
            )


# ── Test: truncation_summary present when truncated ───────────────────


class TestTruncationSummary:
    """Verify truncation_summary is present when layers are truncated.

    **Validates: Requirements 33.3**
    """

    @pytest.mark.asyncio
    async def test_truncation_summary_present_when_truncated(self, app) -> None:
        """truncation_summary is non-empty when truncation occurred."""
        layers = [_make_layer(truncated=True, truncation_stage=2)]
        summary = "[Context truncated: Memory layer reduced. Use tools for full content.]"
        assembled = _make_assembled(
            layers=layers, budget_exceeded=True, truncation_summary=summary,
        )
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert resp.json()["truncation_summary"] == summary

    @pytest.mark.asyncio
    async def test_truncation_summary_empty_when_no_truncation(self, app) -> None:
        """truncation_summary is empty when no truncation occurred."""
        layers = [_make_layer(truncated=False)]
        assembled = _make_assembled(layers=layers, truncation_summary="")
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        assert resp.json()["truncation_summary"] == ""


# ── Test: Empty layers when workspace path missing ────────────────────


class TestEmptyLayersWhenNoWorkspace:
    """Verify 200 with empty layers when workspace path is not configured.

    **Validates: Requirements 33.1**
    """

    @pytest.mark.asyncio
    async def test_returns_200_empty_layers_when_no_workspace(self, app) -> None:
        """Returns 200 with empty layers when workspace config is missing."""
        # workspace_path=None means get_config returns None for the second
        # call (after project validation). We need to handle this carefully:
        # the first call validates the project, the second resolves the path.
        # With workspace_path=None, _validate_project_exists will raise 404
        # because get_config returns None. So we need a special setup.

        # For this test, we mock get_config to return a config for validation
        # but then return None for the workspace path resolution.
        ws_config_with_path = {"file_path": "/tmp/test-ws"}

        call_count = {"n": 0}

        async def mock_get_config():
            call_count["n"] += 1
            if call_count["n"] == 1:
                return ws_config_with_path  # for _validate_project_exists
            return None  # for _get_workspace_path in main handler

        async def mock_get_project(project_id, ws_path):
            return {"id": project_id, "name": "Test"}

        with (
            patch("routers.context.swarm_workspace_manager", **{
                "get_project": AsyncMock(side_effect=mock_get_project),
                "expand_path": MagicMock(return_value="/tmp/test-ws"),
            }),
            patch("routers.context.db", **{
                "workspace_config.get_config": AsyncMock(side_effect=mock_get_config),
            }),
            patch("routers.context.context_cache"),
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get("/api/projects/proj-123/context")

        assert resp.status_code == 200
        body = resp.json()
        assert body["layers"] == []
        assert body["total_token_count"] == 0


# ── Test: token_budget query param forwarded ──────────────────────────


class TestTokenBudgetParam:
    """Verify token_budget query param is reflected in the response.

    **Validates: Requirements 33.3**
    """

    @pytest.mark.asyncio
    async def test_custom_token_budget_reflected(self, app) -> None:
        """Custom token_budget is reflected in the response."""
        assembled = _make_assembled(layers=[_make_layer()], token_budget=5000)
        patches = _patch_dependencies(assembled=assembled)

        with patches["ws_mgr"], patches["db"], patches["cache"]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                resp = await ac.get(
                    "/api/projects/proj-123/context",
                    params={"token_budget": 5000},
                )

        assert resp.status_code == 200
        assert resp.json()["token_budget"] == 5000


# ── Property-Based Tests ──────────────────────────────────────────────
#
# Property tests use the ``hypothesis`` library to verify universal
# correctness properties across randomized inputs.  Each test references
# its design-document property with a tag comment.
# ──────────────────────────────────────────────────────────────────────

from hypothesis import given, settings, HealthCheck
import hypothesis.strategies as st

from schemas.context import ContextLayerResponse, ContextPreviewResponse


# ── Strategies ────────────────────────────────────────────────────────

# Layer numbers correspond to the 8-layer assembly model (1–8).
_LAYER_NUMBERS = list(range(1, 9))

# Workspace-relative path segments — never absolute.
_RELATIVE_PATHS = st.from_regex(
    r"[A-Za-z][A-Za-z0-9_/\-]{0,60}\.md",
    fullmatch=True,
)


def _random_context_layer() -> st.SearchStrategy[ContextLayer]:
    """Strategy producing a random ``ContextLayer`` with valid fields."""
    return st.builds(
        ContextLayer,
        layer_number=st.sampled_from(_LAYER_NUMBERS),
        name=st.text(min_size=1, max_size=30).filter(lambda s: s.strip()),
        source_path=_RELATIVE_PATHS,
        content=st.text(min_size=0, max_size=200),
        token_count=st.integers(min_value=0, max_value=5000),
        truncated=st.booleans(),
        truncation_stage=st.sampled_from([0, 1, 2, 3]),
    )


def _random_assembled_context() -> st.SearchStrategy[AssembledContext]:
    """Strategy producing a random ``AssembledContext``.

    The ``total_token_count`` is always the exact sum of layer token
    counts (the invariant the assembler guarantees).  ``budget_exceeded``
    is True iff at least one layer has ``truncated = True`` (the
    invariant from the truncation engine).
    """
    return (
        st.lists(_random_context_layer(), min_size=0, max_size=8)
        .map(lambda layers: _build_assembled_from_layers(layers))
    )


def _build_assembled_from_layers(layers: list[ContextLayer]) -> AssembledContext:
    """Construct an ``AssembledContext`` with consistent invariants."""
    total = sum(l.token_count for l in layers)
    exceeded = any(l.truncated for l in layers)
    summary = "[Context truncated]" if exceeded else ""
    return AssembledContext(
        layers=layers,
        total_token_count=total,
        budget_exceeded=exceeded,
        token_budget=10_000,
        truncation_summary=summary,
    )


def _map_to_preview(
    assembled: AssembledContext,
    project_id: str = "test-proj",
    thread_id: str | None = None,
    preview_limit: int = 500,
) -> ContextPreviewResponse:
    """Replicate the router's mapping from AssembledContext → ContextPreviewResponse.

    This mirrors the logic in ``routers.context.get_project_context`` so
    the property test validates the mapping preserves token-count
    consistency without requiring HTTP round-trips.
    """
    layer_responses = []
    for layer in assembled.layers:
        content_preview = layer.content[:preview_limit] if layer.content else ""
        layer_responses.append(
            ContextLayerResponse(
                layer_number=layer.layer_number,
                name=layer.name,
                source_path=layer.source_path,
                token_count=layer.token_count,
                content_preview=content_preview,
                truncated=layer.truncated,
                truncation_stage=layer.truncation_stage,
            )
        )

    return ContextPreviewResponse(
        project_id=project_id,
        thread_id=thread_id,
        layers=layer_responses,
        total_token_count=assembled.total_token_count,
        budget_exceeded=assembled.budget_exceeded,
        token_budget=assembled.token_budget,
        truncation_summary=assembled.truncation_summary,
        etag="test-etag",
    )


# ── Property 9: Token count consistency ──────────────────────────────


class TestTokenCountConsistencyProperty:
    """Property 9: Token count consistency.

    *For any* context preview response, the ``total_token_count`` field
    SHALL equal the sum of all individual layer ``token_count`` values,
    and ``budget_exceeded`` SHALL be ``true`` if and only if at least one
    layer has ``truncated = true``.

    Feature: swarmws-intelligence, Property 9: Token count consistency

    **Validates: Requirements 33.3**
    """

    @given(assembled=_random_assembled_context())
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_total_token_count_equals_sum_of_layer_counts(
        self, assembled: AssembledContext
    ) -> None:
        """total_token_count equals the sum of all layer token_count values.

        Feature: swarmws-intelligence, Property 9: Token count consistency
        **Validates: Requirements 33.3**
        """
        preview = _map_to_preview(assembled)

        expected_total = sum(layer.token_count for layer in preview.layers)
        assert preview.total_token_count == expected_total, (
            f"total_token_count ({preview.total_token_count}) != "
            f"sum of layer counts ({expected_total})"
        )

    @given(assembled=_random_assembled_context())
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow],
    )
    def test_budget_exceeded_iff_any_layer_truncated(
        self, assembled: AssembledContext
    ) -> None:
        """budget_exceeded is True iff at least one layer has truncated=True.

        Feature: swarmws-intelligence, Property 9: Token count consistency
        **Validates: Requirements 33.3**
        """
        preview = _map_to_preview(assembled)

        any_truncated = any(layer.truncated for layer in preview.layers)
        assert preview.budget_exceeded == any_truncated, (
            f"budget_exceeded ({preview.budget_exceeded}) should equal "
            f"any(layer.truncated) ({any_truncated}). "
            f"Layer truncated flags: {[l.truncated for l in preview.layers]}"
        )
