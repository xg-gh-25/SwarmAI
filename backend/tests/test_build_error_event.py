"""Unit tests for _build_error_event error sanitization helper.

Tests the ``_build_error_event`` function from ``session_utils.py`` which
conditionally includes traceback detail based on ``settings.debug``.

Key invariants verified:
- Production mode (debug=False): no tracebacks, file paths, or line numbers in detail
- Debug mode (debug=True): full traceback preserved in detail
- Safe detail strings are always preserved
- ``suggested_action`` is included when provided

Requirements: 9.1, 9.2
"""
from unittest.mock import patch

import pytest

from core.session_utils import _build_error_event


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/Users/user/backend/core/agent_manager.py", line 42, in _execute\n'
    "    result = await client.query(msg)\n"
    '  File "/home/user/.venv/lib/python3.12/site-packages/claude_sdk/client.py", line 100, in query\n'
    '    raise ConnectionError("timeout")\n'
    "ConnectionError: timeout"
)


# ---------------------------------------------------------------------------
# Production mode tests (debug=False) — Requirement 9.1
# ---------------------------------------------------------------------------

class TestProductionMode:
    """Error events in production mode must not leak internal details."""


    @patch("core.session_utils.settings")
    def test_traceback_stripped(self, mock_settings):
        """Tracebacks must not appear in detail when debug=False."""
        mock_settings.debug = False
        event = _build_error_event(
            code="TEST", message="something broke", detail=SAMPLE_TRACEBACK
        )
        detail = event.get("detail", "")
        assert "Traceback" not in detail
        assert 'File "' not in detail
        assert '.py", line' not in detail

    @patch("core.session_utils.settings")
    def test_exception_class_preserved(self, mock_settings):
        """The exception class name should survive sanitization."""
        mock_settings.debug = False
        event = _build_error_event(
            code="TEST", message="broke", detail=SAMPLE_TRACEBACK
        )
        detail = event.get("detail", "")
        assert "ConnectionError: timeout" in detail

    @patch("core.session_utils.settings")
    def test_no_detail_when_only_traceback(self, mock_settings):
        """If detail is purely traceback lines, detail key may be absent."""
        mock_settings.debug = False
        pure_tb = (
            "Traceback (most recent call last):\n"
            '  File "/app/main.py", line 1, in <module>\n'
            "    ^^^^^^^^^^^^^^\n"
        )
        event = _build_error_event(code="T", message="m", detail=pure_tb)
        # All lines stripped → detail should be absent or empty
        assert "detail" not in event or event["detail"] == ""

    @patch("core.session_utils.settings")
    def test_safe_detail_preserved(self, mock_settings):
        """Non-traceback detail strings pass through unchanged."""
        mock_settings.debug = False
        event = _build_error_event(
            code="NET", message="fail", detail="Connection refused"
        )
        assert event["detail"] == "Connection refused"


# ---------------------------------------------------------------------------
# Debug mode tests (debug=True) — Requirement 9.2
# ---------------------------------------------------------------------------

class TestDebugMode:
    """In debug mode, full traceback detail must be preserved."""

    @patch("core.session_utils.settings")
    def test_full_traceback_included(self, mock_settings):
        """Full traceback must appear in detail when debug=True."""
        mock_settings.debug = True
        event = _build_error_event(
            code="TEST", message="broke", detail=SAMPLE_TRACEBACK
        )
        assert event["detail"] == SAMPLE_TRACEBACK

    @patch("core.session_utils.settings")
    def test_file_paths_included(self, mock_settings):
        """File paths and line numbers must be present in debug mode."""
        mock_settings.debug = True
        event = _build_error_event(
            code="TEST", message="broke", detail=SAMPLE_TRACEBACK
        )
        assert 'File "' in event["detail"]
        assert '.py", line' in event["detail"]


# ---------------------------------------------------------------------------
# Common behavior tests (both modes)
# ---------------------------------------------------------------------------

class TestCommonBehavior:
    """Behavior that applies regardless of debug mode."""

    def test_basic_event_structure(self):
        """Event must always have type, code, and error fields."""
        event = _build_error_event(code="MY_CODE", message="my message")
        assert event["type"] == "error"
        assert event["code"] == "MY_CODE"
        assert event["error"] == "my message"

    def test_no_detail_when_none(self):
        """detail key should be absent when no detail provided."""
        event = _build_error_event(code="X", message="y")
        assert "detail" not in event

    def test_suggested_action_included(self):
        """suggested_action must be included when provided."""
        event = _build_error_event(
            code="CRED", message="expired",
            suggested_action="Run: ada credentials update"
        )
        assert event["suggested_action"] == "Run: ada credentials update"

    def test_suggested_action_absent_when_none(self):
        """suggested_action key should be absent when not provided."""
        event = _build_error_event(code="X", message="y")
        assert "suggested_action" not in event
