"""Unit tests for the /health endpoint's ``pending_hook_tasks`` field.

This module verifies that the ``/health`` endpoint correctly exposes the
number of in-flight background hook tasks via the ``pending_hook_tasks``
field.  The field reads from ``session_registry.hook_executor.pending_count``
and returns 0 when the executor is ``None``.

Testing methodology: unit tests using FastAPI's ``TestClient`` with
``unittest.mock.patch`` to control the ``_startup_complete`` flag and
direct attribute assignment on ``session_registry`` to swap the executor.

Key scenarios tested:

- ``pending_hook_tasks`` is present in a healthy response
- ``pending_hook_tasks`` is 0 when no hooks are running
- ``pending_hook_tasks`` reflects actual pending count
- ``pending_hook_tasks`` is 0 when hook_executor is None
- ``pending_hook_tasks`` is absent from initializing response

**Validates: Requirements 7.3**
"""

import contextlib
import os
import subprocess
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_client():
    """Create a synchronous TestClient for endpoint testing."""
    from main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@contextlib.contextmanager
def _patch_hook_executor(executor):
    """Temporarily swap session_registry.hook_executor."""
    from core import session_registry
    original = session_registry.hook_executor
    session_registry.hook_executor = executor
    try:
        yield
    finally:
        session_registry.hook_executor = original


class TestHealthEndpointPendingHookTasks:
    """Tests for the pending_hook_tasks field in /health response.

    **Validates: Requirements 7.3**
    """

    def test_healthy_response_includes_pending_hook_tasks(self, test_client):
        """Verify ``pending_hook_tasks`` field is present when healthy."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 0

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "healthy"
        assert "pending_hook_tasks" in body

    def test_pending_hook_tasks_is_zero_when_idle(self, test_client):
        """Verify field is 0 when no hooks are running."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 0

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 0

    def test_pending_hook_tasks_reflects_in_flight_count(self, test_client):
        """Verify field reflects actual pending count when hooks are in flight."""
        mock_executor = MagicMock()
        mock_executor.pending_count = 3

        with patch("main._startup_complete", True), \
             _patch_hook_executor(mock_executor):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 3

    def test_pending_hook_tasks_zero_when_executor_is_none(self, test_client):
        """Verify field is 0 when hook_executor is None."""
        with patch("main._startup_complete", True), \
             _patch_hook_executor(None):
            resp = test_client.get("/health")

        assert resp.json()["pending_hook_tasks"] == 0

    def test_initializing_response_has_no_pending_hook_tasks(self, test_client):
        """Verify initializing response does NOT include pending_hook_tasks."""
        with patch("main._startup_complete", False):
            resp = test_client.get("/health")

        body = resp.json()
        assert body["status"] == "initializing"
        assert "pending_hook_tasks" not in body


class TestHealthEndpointVersionObservability:
    """Tests for runtime SDK + CLI version reporting in /health.

    The endpoint must expose the REAL claude-agent-sdk version and the bundled
    CLI version (cached at boot), ending the 'which version is running' guessing.
    Frozen-env-safe: no importlib.metadata / dist-info dependency.
    """

    def test_healthy_response_includes_real_sdk_version(self, test_client):
        """sdk_version must be the real claude_agent_sdk.__version__, not the
        static 'claude-agent-sdk' string."""
        import claude_agent_sdk
        expected = getattr(claude_agent_sdk, "__version__", None)
        with patch("main._startup_complete", True):
            resp = test_client.get("/health")
        body = resp.json()
        assert "sdk_version" in body
        assert body["sdk_version"] != "claude-agent-sdk"  # not the static label
        if expected:
            assert body["sdk_version"] == expected

    def test_sdk_version_fallback_to_unknown(self):
        """_resolve_sdk_version returns 'unknown' (never raises) when both the
        __version__ attribute and the env var are absent."""
        import main
        import claude_agent_sdk
        had_attr = hasattr(claude_agent_sdk, "__version__")
        saved = getattr(claude_agent_sdk, "__version__", None)
        env_saved = os.environ.pop("CLAUDE_AGENT_SDK_VERSION", None)
        try:
            if had_attr:
                del claude_agent_sdk.__version__
            assert main._resolve_sdk_version() == "unknown"
        finally:
            if had_attr:
                claude_agent_sdk.__version__ = saved
            if env_saved is not None:
                os.environ["CLAUDE_AGENT_SDK_VERSION"] = env_saved

    def test_healthy_response_includes_cli_version(self, test_client):
        """cli_version must be present in the healthy response (the cached boot
        value), not recomputed per request."""
        with patch("main._startup_complete", True), \
             patch("main._cli_version", "2.1.150"):
            resp = test_client.get("/health")
        body = resp.json()
        assert body.get("cli_version") == "2.1.150"

    def test_cli_version_resolver_falls_back_on_failure(self):
        """_resolve_cli_version returns 'unknown' (never raises) when the
        subprocess fails / times out / binary is missing."""
        import main
        with patch("main.subprocess.run", side_effect=FileNotFoundError("no claude")):
            assert main._resolve_cli_version() == "unknown"
        with patch("main.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 2)):
            assert main._resolve_cli_version() == "unknown"

    def test_cli_version_nonzero_returncode_is_unknown(self):
        """A non-zero CLI exit must yield 'unknown', not the first token of
        whatever diagnostic text it printed to stdout (adversarial LOW conf 7).
        The bundled CLI exists in the test venv, so is_file() is real; we only
        mock the subprocess result to simulate a failing exit code."""
        import main
        from unittest.mock import MagicMock
        fake = MagicMock()
        fake.returncode = 1
        fake.stdout = "ERROR: license check failed"
        with patch("main.subprocess.run", return_value=fake):
            assert main._resolve_cli_version() == "unknown"

    def test_initializing_response_still_has_version_and_sdk(self, test_client):
        """No regression: initializing branch keeps version + sdk fields."""
        with patch("main._startup_complete", False):
            resp = test_client.get("/health")
        body = resp.json()
        assert body["status"] == "initializing"
        assert "version" in body
        assert "sdk" in body


class TestConsecutiveFailureLogLevel:
    """A consecutive-failure 'timeout' is graceful-degrade → WARNING, never
    CRITICAL. A real fault (exception text) still escalates to CRITICAL.

    Regression guard for the context_health false-alarm: the hook ran out of
    its wall-clock budget (slow Bedrock embed) and deferred DELTA work to the
    next session — self-healing, not a fault — yet the executor logged a
    CRITICAL 'investigate immediately' on every run. timeout reason must be
    WARNING; only deterministic faults (corruption/missing-dep/config) stay
    CRITICAL.
    """

    def _executor(self):
        from core.session_hooks import (
            BackgroundHookExecutor,
            SessionLifecycleHookManager,
        )
        return BackgroundHookExecutor(SessionLifecycleHookManager())

    def test_consecutive_timeouts_warn_not_critical(self, caplog):
        import logging
        ex = self._executor()
        with caplog.at_level(logging.WARNING, logger="core.session_hooks"):
            for _ in range(3):  # >= CONSECUTIVE_FAILURE_ALERT_THRESHOLD
                # Mirrors the real TimeoutError call sites (session_hooks.py
                # :530/:585): error_msg="timeout" AND the explicit is_timeout
                # flag — the flag, not the string, is what downgrades to WARNING.
                ex._record_hook_result(
                    "context_health", False, "timeout", is_timeout=True
                )

        criticals = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not criticals, "timeout must NOT escalate to CRITICAL"
        assert any("timed out" in r.getMessage() for r in warnings)

    def test_consecutive_real_faults_still_critical(self, caplog):
        import logging
        ex = self._executor()
        with caplog.at_level(logging.WARNING, logger="core.session_hooks"):
            for _ in range(3):
                ex._record_hook_result(
                    "evolution_maintenance", False, "zlib decompression error"
                )

        criticals = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert criticals, "a real (non-timeout) fault must stay CRITICAL"
        assert any("NOT transient" in r.getMessage() for r in criticals)

    def test_fault_whose_text_contains_timeout_still_critical(self, caplog):
        """Finding 2 guard: a deterministic fault whose ERROR TEXT merely
        contains the word 'timeout' (e.g. a botocore ReadTimeoutError from a
        MISconfigured endpoint, a SQLite lock-wait) must STILL escalate. The
        timeout/fault split keys on the explicit is_timeout flag set only by
        the TimeoutError handler — never on the error string."""
        import logging
        ex = self._executor()
        with caplog.at_level(logging.WARNING, logger="core.session_hooks"):
            for _ in range(3):
                # is_timeout defaults False — this is the str(exc) fault path,
                # even though the message says "Read timeout on endpoint URL".
                ex._record_hook_result(
                    "knowledge_sync", False,
                    'Read timeout on endpoint URL: "https://bedrock..."',
                )

        criticals = [r for r in caplog.records if r.levelno >= logging.CRITICAL]
        assert criticals, "a fault is CRITICAL even if its text says 'timeout'"
        assert any("NOT transient" in r.getMessage() for r in criticals)

    def test_success_resets_consecutive_counter(self, caplog):
        import logging
        ex = self._executor()
        with caplog.at_level(logging.WARNING, logger="core.session_hooks"):
            ex._record_hook_result("h", False, "zlib error")
            ex._record_hook_result("h", False, "zlib error")
            ex._record_hook_result("h", True)  # reset
            ex._record_hook_result("h", False, "zlib error")  # only 1 in a row
        # 1 consecutive < threshold(3) → no CRITICAL emitted
        assert not [r for r in caplog.records if r.levelno >= logging.CRITICAL]
